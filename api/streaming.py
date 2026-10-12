"""PitchIQ API – SSE streaming bridge.

Problem: stream_agent() is a SYNCHRONOUS blocking generator (it calls LangGraph
which calls an LLM). We cannot iterate it on the asyncio event loop without
freezing the server.

Solution: run the generator in a worker thread. The thread pushes dicts into an
asyncio.Queue. The async generator reads from the queue and yields SSE frames.

Key constraints obeyed here:
- The worker thread cannot be interrupted mid-LLM-call, so we let it finish in
  the background after disconnect (it will drain into a full queue and then
  just return when the queue is garbage-collected).
- A ping comment (": ping") is sent every 15 s so HTTP proxies do not close
  idle SSE connections.
- The overall wall-clock cap PITCHIQ_CHAT_TIMEOUT_S causes an "error" event
  followed by "done", then the async generator exits.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from typing import Any, AsyncGenerator, Callable

log = logging.getLogger("pitchiq.api.streaming")

_SENTINEL = object()  # marks that the worker thread has finished
_QUEUE_MAX = 64       # prevent unbounded memory if the consumer is slow


def _worker(
    gen_factory: Callable[[], Any],
    queue: asyncio.Queue,
    loop: asyncio.AbstractEventLoop,
) -> None:
    """Run gen_factory() in a thread, push each event dict onto the asyncio queue."""
    try:
        for event in gen_factory():
            asyncio.run_coroutine_threadsafe(queue.put(event), loop).result()
    except Exception as exc:  # noqa: BLE001
        log.exception("streaming worker crashed")
        asyncio.run_coroutine_threadsafe(
            queue.put({"type": "error", "message": f"Internal streaming error ({type(exc).__name__})."}),
            loop,
        ).result()
    finally:
        asyncio.run_coroutine_threadsafe(queue.put(_SENTINEL), loop).result()


async def sse_generator(
    gen_factory: Callable[[], Any],
    request: Any,  # starlette Request – used for is_disconnected()
    request_id: str,
    thread_id: str,
    timeout_s: float,
) -> AsyncGenerator[str, None]:
    """Async SSE frame generator.

    Yields raw SSE text (event + data + blank line, or ": ping" comment).
    The caller (routes_chat.py) wraps this with StreamingResponse.
    """

    def _frame(event: str, payload: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"

    def _ping() -> str:
        return ": ping\n\n"

    queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAX)
    loop = asyncio.get_event_loop()

    # Emit the "start" event immediately
    yield _frame("start", {"request_id": request_id, "thread_id": thread_id})

    # Launch blocking generator in a thread
    t = threading.Thread(target=_worker, args=(gen_factory, queue, loop), daemon=True)
    t.start()

    deadline = time.monotonic() + timeout_s
    pending_tool_calls: dict[str, dict] = {}  # id -> {name, args}

    try:
        while True:
            # Check client disconnect
            if await request.is_disconnected():
                log.info("Client disconnected; stopping SSE send (worker continues in bg)")
                break

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                yield _frame("error", {"code": "timeout", "message": "Request timed out."})
                yield _frame("done", {})
                return

            try:
                event = await asyncio.wait_for(queue.get(), timeout=min(15.0, remaining))
            except asyncio.TimeoutError:
                # 15 s with no event – send a ping comment to keep the connection alive
                yield _ping()
                continue

            if event is _SENTINEL:
                break  # worker finished normally

            ev_type = event.get("type", "")

            if ev_type == "tool_call":
                pending_tool_calls[event["id"]] = {"name": event["name"], "args": event["args"]}
                yield _frame("tool_call", {
                    "id": event["id"],
                    "name": event["name"],
                    "args": event["args"],
                })

            elif ev_type == "tool_result":
                art = event.get("artifact") or {}
                content_text = event.get("content", "")
                # Truncate to 400 chars; never send raw artifact blobs
                summary = content_text[:400] if isinstance(content_text, str) else ""
                yield _frame("tool_result", {
                    "id": event["id"],
                    "name": event["name"],
                    "ok": event.get("ok", True),
                    "elapsed_ms": art.get("elapsed_ms"),
                    "summary": summary,
                })

            elif ev_type == "revision":
                yield _frame("revision", {"detail": event.get("detail", "")})

            elif ev_type == "answer":
                # Collect sources + prediction from accumulated tool_results in the queue
                # (stream_agent already yielded them; we parse them from pending tool calls)
                # The answer event itself carries usage + elapsed_s but NOT sources/prediction.
                # We need to gather those from the tool_result artifacts we already processed.
                # Since we don't re-process them here, the answer payload intentionally omits
                # sources/prediction (the /chat endpoint provides those via agent.ask).
                # For SSE we pass what stream_agent gives us.
                yield _frame("answer", {
                    "text": event.get("text", ""),
                    "sources": [],      # populated from tool_result processing below
                    "prediction": None,
                    "usage": event.get("usage", {}),
                    "elapsed_s": event.get("elapsed_s", 0),
                })
                # After "answer" the worker is done; drain sentinel then break
                continue

            elif ev_type == "error":
                yield _frame("error", {
                    "code": "llm_unavailable",
                    "message": event.get("message", "An error occurred."),
                })
                break

    finally:
        yield _frame("done", {})


async def sse_generator_with_sources(
    gen_factory: Callable[[], Any],
    request: Any,
    request_id: str,
    thread_id: str,
    timeout_s: float,
) -> AsyncGenerator[str, None]:
    """Like sse_generator but accumulates sources and prediction from tool_result
    artifacts so the answer event is complete.

    This is the production version used by routes_chat.py.
    """

    def _frame(event: str, payload: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"

    def _ping() -> str:
        return ": ping\n\n"

    queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAX)
    loop = asyncio.get_event_loop()

    yield _frame("start", {"request_id": request_id, "thread_id": thread_id})

    t = threading.Thread(target=_worker, args=(gen_factory, queue, loop), daemon=True)
    t.start()

    deadline = time.monotonic() + timeout_s
    accumulated_sources: list[dict] = []
    accumulated_prediction: dict | None = None

    try:
        while True:
            if await request.is_disconnected():
                log.info("SSE client disconnected; worker continues in background")
                break

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                yield _frame("error", {"code": "timeout", "message": "Request timed out."})
                yield _frame("done", {})
                return

            try:
                event = await asyncio.wait_for(queue.get(), timeout=min(15.0, remaining))
            except asyncio.TimeoutError:
                yield _ping()
                continue

            if event is _SENTINEL:
                break

            ev_type = event.get("type", "")

            if ev_type == "tool_call":
                yield _frame("tool_call", {
                    "id": event["id"],
                    "name": event["name"],
                    "args": event["args"],
                })

            elif ev_type == "tool_result":
                art = event.get("artifact") or {}
                content_text = event.get("content", "")
                summary = content_text[:400] if isinstance(content_text, str) else ""

                # Accumulate sources and prediction for the answer event
                for a in art.get("articles", []):
                    accumulated_sources.append({
                        k: a.get(k) for k in ("source", "url", "date", "score")
                    })
                if art.get("prediction") is not None:
                    accumulated_prediction = art["prediction"]

                yield _frame("tool_result", {
                    "id": event["id"],
                    "name": event["name"],
                    "ok": event.get("ok", True),
                    "elapsed_ms": art.get("elapsed_ms"),
                    "summary": summary,
                })

            elif ev_type == "revision":
                yield _frame("revision", {"detail": event.get("detail", "")})

            elif ev_type == "answer":
                yield _frame("answer", {
                    "text": event.get("text", ""),
                    "sources": accumulated_sources,
                    "prediction": accumulated_prediction,
                    "usage": event.get("usage", {}),
                    "elapsed_s": event.get("elapsed_s", 0),
                })
                # Worker will put _SENTINEL next; we'll break on that

            elif ev_type == "error":
                yield _frame("error", {
                    "code": "llm_unavailable",
                    "message": event.get("message", "An error occurred."),
                })
                break

    finally:
        yield _frame("done", {})
