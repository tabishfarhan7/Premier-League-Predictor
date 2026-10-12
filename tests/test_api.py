"""Tests for the PitchIQ API.

Uses FastAPI TestClient and monkeypatches the backend modules so we never
hit Ollama, Qdrant, or the real database during tests.
"""
import asyncio
import json
import math
from unittest.mock import MagicMock

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient

# Patch the environment BEFORE importing the app
import os
os.environ["PITCHIQ_API_KEY"] = "test-secret"
os.environ["PITCHIQ_RATE_LIMIT_PER_MIN"] = "100"  # high enough so regular tests pass
os.environ["PITCHIQ_MAX_CONCURRENT_CHATS"] = "2"

from api.main import app, _state
import agent
import predictor
import fpl_sync
import teams

client = TestClient(app)

# --------------------------------------------------------------------------- #
# Fixtures for monkeypatching
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def mock_backend(monkeypatch):
    """Monkeypatch all backend interactions."""
    # agent.ask
    mock_ask = MagicMock()
    mock_ask.return_value = agent.AgentResult(
        answer="Arsenal wins 2-0.", thread_id="test-thread-id", elapsed_s=1.2,
        tool_calls=[], sources=[], prediction=None, usage={}
    )
    monkeypatch.setattr(agent, "ask", mock_ask)

    # agent.stream_agent
    def fake_stream(question, thread_id=None):
        yield {"type": "start", "request_id": "req-1", "thread_id": "test-thread"}
        yield {"type": "tool_call", "id": "t1", "name": "predict", "args": {}}
        yield {"type": "tool_result", "id": "t1", "name": "predict", "ok": True, "content": "Tool ok", "artifact": {}}
        yield {"type": "answer", "text": "Arsenal wins", "usage": {}, "elapsed_s": 0.5, "thread_id": "test-thread"}
    mock_stream = MagicMock(side_effect=fake_stream)
    monkeypatch.setattr(agent, "stream_agent", mock_stream)

    # predictor
    mock_predict = MagicMock()
    mock_predict.return_value = {
        "home": "Arsenal", "away": "Chelsea", "match_date": "2026-10-25",
        "probabilities": {"home_win": 0.6, "draw": 0.2, "away_win": 0.2},
        "most_likely": "home_win",
        "why_home_win": [{"factor": "elo_diff", "value": 100, "pushes": "towards", "strength": 0.5}],
        "context": {"elo_home": 1500, "elo_away": 1400},
        "warnings": [],
        "model": {"trained_through": "2026-05-19", "test_log_loss": 0.9, "note": "ok"}
    }
    monkeypatch.setattr(predictor, "predict_match", mock_predict)

    mock_load_matches = MagicMock()
    mock_load_matches.return_value = pd.DataFrame([
        {"match_id": 1, "match_date": pd.Timestamp("2026-10-01"), "home_team": "Arsenal", "away_team": "Chelsea", "home_goals": 2, "away_goals": 1, "result": "H", "season": "2627", "season_idx": 1}
    ])
    monkeypatch.setattr(predictor, "load_matches", mock_load_matches)

    mock_load_model = MagicMock()
    mock_load_model.return_value = (None, {
        "trained_through": "2026-05-19", "n_matches": 10, "test_matches_scored": 5,
        "metrics_on_test": {}, "calibration_ece": 0.05, "feature_importance": {"elo": 0.9}
    })
    monkeypatch.setattr(predictor, "load_model", mock_load_model)

    # fpl_sync
    monkeypatch.setattr(fpl_sync, "fetch_bootstrap", lambda: {"elements": [], "teams": [{"id": 1, "name": "Arsenal", "short_name": "ARS"}]})
    monkeypatch.setattr(fpl_sync, "fetch_fixtures", lambda: [])
    monkeypatch.setattr(fpl_sync, "upcoming_fixtures", lambda *a, **kw: [{"kickoff_utc": pd.Timestamp("2026-10-25T15:00Z", tz="UTC"), "home": "Arsenal", "away": "Chelsea", "gameweek": 9}])
    
    # fake availability with NaN
    mock_players_frame = MagicMock()
    df = pd.DataFrame([
        {"name": "Saka", "team": "Arsenal", "position": "MID", "status": "i", "news": "Knee", "chance_next_round": float("nan")}
    ])
    mock_players_frame.return_value = df
    monkeypatch.setattr(fpl_sync, "players_frame", mock_players_frame)


@pytest.fixture
def auth_headers():
    return {"X-API-Key": "test-secret"}


# --------------------------------------------------------------------------- #
# Diagnostics
# --------------------------------------------------------------------------- #
def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_ready_ok():
    _state.db_ok = True
    _state.model_ok = True
    resp = client.get("/ready")
    assert resp.status_code == 200
    assert resp.json()["ready"] is True


def test_ready_503():
    _state.model_ok = False
    resp = client.get("/ready")
    assert resp.status_code == 503
    assert resp.json()["ready"] is False
    assert "pitchiq_xgb.json" in resp.json()["dependencies"]["model"]["message"]


# --------------------------------------------------------------------------- #
# Security (API Key, CORS, Rate Limit)
# --------------------------------------------------------------------------- #
def test_api_key_missing():
    resp = client.post("/api/v1/chat", json={"question": "Hi"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_cors_preflight():
    resp = client.options(
        "/api/v1/chat",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"}
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_rate_limit():
    from api.routes_chat import _rate_limiter
    _rate_limiter.rpm = 2  # Temp lower for test
    ip = "1.2.3.4"
    h = {"X-API-Key": "test-secret", "X-Forwarded-For": ip}
    r1 = client.post("/api/v1/chat", json={"question": "1"}, headers=h)
    r2 = client.post("/api/v1/chat", json={"question": "2"}, headers=h)
    assert r1.status_code == 200
    assert r2.status_code == 200

    r3 = client.post("/api/v1/chat", json={"question": "3"}, headers=h)
    assert r3.status_code == 429
    assert r3.json()["error"]["code"] == "rate_limit_exceeded"
    assert "retry-after" in r3.headers
    _rate_limiter.rpm = 100  # restore


@pytest.mark.asyncio
async def test_concurrency_limit(auth_headers, monkeypatch):
    import api.routes_chat
    api.routes_chat._chat_semaphore = asyncio.Semaphore(1)

    # Slow down agent.ask
    async def slow_ask(*args):
        await asyncio.sleep(0.5)
        return agent.AgentResult(answer="Done")
    
    # We must patch the threaded run sync to not run in a thread for this pure async test
    import anyio
    monkeypatch.setattr(anyio.to_thread, "run_sync", slow_ask)

    async with AsyncClient(app=app, base_url="http://test") as ac:
        reqs = [
            ac.post("/api/v1/chat", json={"question": "Hi"}, headers=auth_headers),
            ac.post("/api/v1/chat", json={"question": "Hi"}, headers=auth_headers)
        ]
        res = await asyncio.gather(*reqs)
        
    statuses = [r.status_code for r in res]
    assert 200 in statuses
    assert 429 in statuses
    r429 = [r for r in res if r.status_code == 429][0]
    assert r429.json()["error"]["code"] == "busy"
    
    api.routes_chat._chat_semaphore = None


# --------------------------------------------------------------------------- #
# Chat Endpoint
# --------------------------------------------------------------------------- #
def test_chat_success(auth_headers):
    resp = client.post("/api/v1/chat", json={"question": "Who wins?"}, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["answer"] == "Arsenal wins 2-0."
    assert "thread_id" in data
    assert "request_id" in data


def test_chat_validation_error(auth_headers):
    resp = client.post("/api/v1/chat", json={"question": ""}, headers=auth_headers)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_input"

    resp2 = client.post("/api/v1/chat", json={"question": "a", "thread_id": "short"}, headers=auth_headers)
    assert resp2.status_code == 422


def test_chat_llm_error(auth_headers, monkeypatch):
    mock_ask = MagicMock(return_value=agent.AgentResult(error="Ollama unreachable"))
    monkeypatch.setattr(agent, "ask", mock_ask)
    resp = client.post("/api/v1/chat", json={"question": "Hi"}, headers=auth_headers)
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "llm_unavailable"


# --------------------------------------------------------------------------- #
# Streaming Endpoint
# --------------------------------------------------------------------------- #
def test_streaming_success(auth_headers):
    resp = client.post("/api/v1/chat/stream", json={"question": "Hi"}, headers=auth_headers)
    assert resp.status_code == 200
    lines = [L for L in resp.text.split("\n") if L.startswith("event: ")]
    events = [L.split("event: ")[1] for L in lines]
    assert events == ["start", "tool_call", "tool_result", "answer", "done"]


def test_streaming_heartbeat(auth_headers, monkeypatch):
    def slow_stream(*args):
        yield {"type": "start", "request_id": "r", "thread_id": "t"}
        import time
        time.sleep(0.3)
        yield {"type": "answer", "text": "Hi", "usage": {}}

    monkeypatch.setattr(agent, "stream_agent", slow_stream)
    monkeypatch.setattr("api.streaming.asyncio.wait_for", MagicMock(side_effect=asyncio.TimeoutError()))
    
    resp = client.post("/api/v1/chat/stream", json={"question": "Hi"}, headers=auth_headers)
    assert ": ping" in resp.text


# --------------------------------------------------------------------------- #
# Data Endpoints
# --------------------------------------------------------------------------- #
def test_predict_success_and_normalisation(auth_headers):
    resp = client.post("/api/v1/predict", json={"home_team": "Arsenal", "away_team": "Chelsea"}, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["most_likely"] == "home_win"
    # Normalised "why" key exists and dynamic "why_home_win" is gone
    assert "why" in data
    assert "why_home_win" not in data
    assert len(data["why"]) == 1


def test_predict_unknown_team(auth_headers, monkeypatch):
    def fake_predict(*a, **kw):
        raise ValueError("Unknown team 'xyz'. Did you mean: Arsenal?")
    monkeypatch.setattr(predictor, "predict_match", fake_predict)
    resp = client.post("/api/v1/predict", json={"home_team": "xyz", "away_team": "Chelsea"}, headers=auth_headers)
    assert resp.status_code == 422
    assert "Did you mean: Arsenal?" in resp.json()["error"]["message"]


def test_teams_endpoint(auth_headers):
    resp = client.get("/api/v1/teams", headers=auth_headers)
    assert resp.status_code == 200
    assert len(resp.json()["teams"]) > 0


def test_fixtures_endpoint(auth_headers):
    resp = client.get("/api/v1/fixtures", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["fixtures"][0]["home"] == "Arsenal"


def test_form_endpoint(auth_headers):
    resp = client.get("/api/v1/teams/Arsenal/form?matches=5", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"]["wins"] == 1


def test_availability_nan_to_null(auth_headers):
    resp = client.get("/api/v1/teams/Arsenal/availability", headers=auth_headers)
    assert resp.status_code == 200
    players = resp.json()["players"]
    assert len(players) == 1
    # NaN must be correctly serialized to None (null in JSON)
    assert players[0]["chance_next_round"] is None
