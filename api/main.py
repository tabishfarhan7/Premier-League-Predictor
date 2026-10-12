"""PitchIQ API main application entrypoint.

Run with:
    uvicorn api.main:app --port 8000

Features:
- Global exception handlers for uniform error shapes
- Lifespan background warmup (Qdrant, model, agent)
- Health and readiness endpoints
- OpenAPI configured
"""
from __future__ import annotations

import asyncio
import logging
import threading
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

import agent
import predictor
import rag_indexer
from api.routes_chat import router as chat_router
from api.routes_data import router as data_router
from api.schemas import DependencyStatus, HealthResponse, ReadyResponse
from api.security import RequestIdMiddleware, error_response, make_api_key_dependency
from api.settings import settings

log = logging.getLogger("pitchiq.api.main")


# Background warmup state
class AppState:
    warmup_done: bool = False
    model_ok: bool = False
    db_ok: bool = False
    qdrant_ok: bool = False
    warmup_error: str | None = None


_state = AppState()


def _warmup_worker() -> None:
    """Warm up heavy dependencies in the background so startup is fast and robust."""
    try:
        # Check DB
        try:
            predictor.load_matches()
            _state.db_ok = True
        except FileNotFoundError as exc:
            log.warning("Warmup: database missing: %s", exc)

        # Load model
        try:
            predictor.load_model()
            _state.model_ok = True
        except FileNotFoundError as exc:
            log.warning("Warmup: model missing: %s", exc)

        # Warm up Qdrant & Embeddings
        try:
            rag_indexer.get_embedding_model()
            _state.qdrant_ok = True
        except Exception as exc:
            log.warning("Warmup: RAG error: %s", exc)

        # Warm up agent (loads LLM client, but doesn't actually hit the remote yet)
        try:
            agent.get_agent()
        except Exception as exc:
            log.warning("Warmup: Agent init error: %s", exc)

        log.info("Background warmup complete.")
    except Exception as exc:
        _state.warmup_error = str(exc)
        log.exception("Warmup crashed")
    finally:
        _state.warmup_done = True


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    t = threading.Thread(target=_warmup_worker, daemon=True)
    t.start()
    yield
    # Shutdown
    pass


# API Key Dependency
api_key_dep = make_api_key_dependency(settings.api_key)


app = FastAPI(
    title="PitchIQ API",
    description="Premier League match predictions and news analyst.",
    version="1.0.0",
    lifespan=lifespan,
    dependencies=[Depends(api_key_dep)],
)


# Middlewares
app.add_middleware(RequestIdMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*", "X-API-Key"],
    expose_headers=["X-Request-ID", "Retry-After"],
)


# Exception Handlers
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> Any:
    request_id = getattr(request.state, "request_id", "")
    return error_response(422, "invalid_input", str(exc), request_id)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> Any:
    request_id = getattr(request.state, "request_id", "")
    log.exception("Unhandled exception request_id=%s", request_id)
    return error_response(500, "internal_error", "An unexpected error occurred.", request_id)


# Endpoints
@app.get("/health", response_model=HealthResponse, tags=["Diagnostics"])
async def health_check() -> Any:
    return {"status": "ok"}


@app.get("/ready", response_model=ReadyResponse, tags=["Diagnostics"])
async def ready_check() -> Any:
    deps: dict[str, DependencyStatus] = {}

    deps["database"] = DependencyStatus(ok=_state.db_ok)
    if not _state.db_ok:
        deps["database"].message = "pitchiq.db not found"

    deps["model"] = DependencyStatus(ok=_state.model_ok)
    if not _state.model_ok:
        deps["model"].message = "pitchiq_xgb.json not found"

    deps["qdrant"] = DependencyStatus(ok=_state.qdrant_ok)
    if not _state.qdrant_ok:
        deps["qdrant"].message = "Qdrant or embeddings failed to load"

    # Check LLM dynamically
    llm_ok = False
    llm_msg = None
    if settings.llm_spec.startswith("openai:"):
        if settings.openai_api_key:
            llm_ok = True
        else:
            llm_msg = "OPENAI_API_KEY missing"
    else:
        # Default to ollama check
        import httpx
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{settings.ollama_host}/api/tags", timeout=2.0)
                resp.raise_for_status()
                llm_ok = True
        except Exception as exc:
            llm_msg = f"Ollama not reachable: {exc}"

    deps["llm"] = DependencyStatus(ok=llm_ok, message=llm_msg)

    # 200 if db and model are OK, otherwise 503
    ready = _state.db_ok and _state.model_ok
    status_code = 200 if ready else 503

    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=status_code,
        content=ReadyResponse(ready=ready, dependencies=deps).model_dump(),
    )


# Include Routers
app.include_router(chat_router, prefix="/api/v1")
app.include_router(data_router, prefix="/api/v1")
