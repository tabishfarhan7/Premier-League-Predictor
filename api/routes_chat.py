"""PitchIQ API – chat endpoints.

POST /api/v1/chat          – blocking, returns ChatResponse
POST /api/v1/chat/stream   – Server-Sent Events

IMPORTANT: Both endpoints import the backend as a MODULE and call agent.ask /
agent.stream_agent as module-level names so monkeypatching works in tests.

All blocking backend calls run in a worker thread via anyio.to_thread.run_sync
(which uses the default thread pool). The event loop is never blocked.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Callable

import anyio
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

import agent  # imported as a module so tests can monkeypatch agent.ask etc.
from api.schemas import (
    ChatRequest,
    ChatResponse,
    SourceRecord,
    ToolCallRecord,
    UsageInfo,
)
from api.security import RateLimiter, error_response, get_client_ip
from api.settings import settings
from api.streaming import sse_generator_with_sources

log = logging.getLogger("pitchiq.api.chat")

router = APIRouter()

# Shared rate limiter instance (one per process – that's fine, we run 1 worker)
_rate_limiter = RateLimiter(settings.rate_limit_per_min)

# Asyncio semaphore limiting simultaneous LLM calls
_chat_semaphore: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    """Lazily create the semaphore on first call (must be in the event loop)."""
    global _chat_semaphore
    if _chat_semaphore is None:
        _chat_semaphore = asyncio.Semaphore(settings.max_concurrent_chats)
    return _chat_semaphore


def _check_rate_limit(request: Request) -> tuple[bool, int]:
    ip = get_client_ip(request)
    return _rate_limiter.is_allowed(ip)


# --------------------------------------------------------------------------- #
# POST /chat
# --------------------------------------------------------------------------- #
@router.post(
    "/chat",
    response_model=ChatResponse,
    summary="Ask the PitchIQ analyst a question",
    description=(
        "Send a Premier League question. The agent uses XGBoost predictions, "
        "news search, and live FPL data to answer. Conversation memory is keyed "
        "on thread_id; pass the same thread_id for follow-up questions."
    ),
    tags=["Chat"],
    responses={
        200: {"description": "Successful answer"},
        429: {"description": "Rate limit or concurrency cap exceeded"},
        502: {"description": "LLM returned an error"},
        503: {"description": "LLM unavailable"},
        422: {"description": "Validation error"},
    },
)
async def chat_endpoint(body: ChatRequest, request: Request) -> ChatResponse:
    request_id: str = getattr(request.state, "request_id", uuid.uuid4().hex)

    # Rate limit check
    allowed, retry_after = _check_rate_limit(request)
    if not allowed:
        from fastapi import HTTPException
        from fastapi.responses import JSONResponse
        return JSONResponse(  # type: ignore[return-value]
            status_code=429,
            content={
                "error": {
                    "code": "rate_limit_exceeded",
                    "message": "Too many requests. Please slow down.",
                    "request_id": request_id,
                }
            },
            headers={"Retry-After": str(retry_after), "X-Request-ID": request_id},
        )

    sem = _get_semaphore()
    if not sem._value:  # type: ignore[attr-defined]
        return error_response(  # type: ignore[return-value]
            429, "busy",
            "All analyst slots are busy. Please try again in a moment.",
            request_id,
        )

    thread_id = body.thread_id or uuid.uuid4().hex

    log.info(
        "chat request_id=%s thread_id=%s question_len=%d",
        request_id, thread_id, len(body.question),
    )

    async with sem:
        try:
            result = await anyio.to_thread.run_sync(
                lambda: agent.ask(body.question, thread_id),
                cancellable=False,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("agent.ask failed unexpectedly request_id=%s", request_id)
            return error_response(  # type: ignore[return-value]
                500, "internal_error",
                "An unexpected error occurred.",
                request_id,
            )

    if result.error:
        return error_response(  # type: ignore[return-value]
            503, "llm_unavailable",
            result.error,
            request_id,
        )

    tool_calls = [
        ToolCallRecord(
            tool=tc.get("tool", ""),
            args=tc.get("args", {}),
            ok=bool(tc.get("ok", True)),
            elapsed_ms=tc.get("elapsed_ms"),
        )
        for tc in (result.tool_calls or [])
    ]
    sources = [
        SourceRecord(
            source=s.get("source"),
            url=s.get("url"),
            date=s.get("date"),
            score=s.get("score"),
        )
        for s in (result.sources or [])
    ]
    usage_dict = result.usage or {}
    usage = UsageInfo(
        llm_calls=usage_dict.get("llm_calls", 0),
        input_tokens=usage_dict.get("input_tokens", 0),
        output_tokens=usage_dict.get("output_tokens", 0),
    )

    return ChatResponse(
        request_id=request_id,
        thread_id=result.thread_id or thread_id,
        answer=result.answer,
        tool_calls=tool_calls,
        sources=sources,
        prediction=result.prediction,
        usage=usage,
        elapsed_s=result.elapsed_s,
        revisions=result.revisions,
    )


# --------------------------------------------------------------------------- #
# POST /chat/stream
# --------------------------------------------------------------------------- #
@router.post(
    "/chat/stream",
    summary="Stream the PitchIQ analyst response as Server-Sent Events",
    description=(
        "Same as /chat but returns a text/event-stream. Events in order: "
        "start → tool_call* → tool_result* → revision* → answer (or error) → done."
    ),
    tags=["Chat"],
    responses={
        200: {"content": {"text/event-stream": {}}},
        429: {"description": "Rate limit or concurrency cap exceeded"},
    },
)
async def chat_stream_endpoint(body: ChatRequest, request: Request) -> StreamingResponse:
    request_id: str = getattr(request.state, "request_id", uuid.uuid4().hex)

    # Rate limit check
    allowed, retry_after = _check_rate_limit(request)
    if not allowed:
        from fastapi.responses import JSONResponse
        return JSONResponse(  # type: ignore[return-value]
            status_code=429,
            content={
                "error": {
                    "code": "rate_limit_exceeded",
                    "message": "Too many requests. Please slow down.",
                    "request_id": request_id,
                }
            },
            headers={"Retry-After": str(retry_after), "X-Request-ID": request_id},
        )

    sem = _get_semaphore()
    if not sem._value:  # type: ignore[attr-defined]
        from fastapi.responses import JSONResponse
        return JSONResponse(  # type: ignore[return-value]
            status_code=429,
            content={
                "error": {
                    "code": "busy",
                    "message": "All analyst slots are busy. Please try again in a moment.",
                    "request_id": request_id,
                }
            },
            headers={"X-Request-ID": request_id},
        )

    thread_id = body.thread_id or uuid.uuid4().hex

    log.info(
        "chat/stream request_id=%s thread_id=%s question_len=%d",
        request_id, thread_id, len(body.question),
    )

    question = body.question  # capture for closure

    async def _acquire_and_stream():
        """Wrap the generator so the semaphore is held for the duration of streaming."""
        async with sem:
            gen_factory: Callable = lambda: agent.stream_agent(question, thread_id)
            async for chunk in sse_generator_with_sources(
                gen_factory=gen_factory,
                request=request,
                request_id=request_id,
                thread_id=thread_id,
                timeout_s=settings.chat_timeout_s,
            ):
                yield chunk

    return StreamingResponse(
        _acquire_and_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Request-ID": request_id,
        },
    )
