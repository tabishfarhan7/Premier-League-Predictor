# PitchIQ API Documentation

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `PITCHIQ_LLM` | `ollama:llama3.2` | The LLM to use (e.g., `openai:gpt-4o-mini`). |
| `PITCHIQ_API_KEY` | (None) | If set, require `X-API-Key` on `/api/v1/*` endpoints. |
| `PITCHIQ_CORS_ORIGINS` | `http://localhost:5173,...` | Comma-separated list of allowed origins. |
| `PITCHIQ_RATE_LIMIT_PER_MIN` | `30` | Rate limit per IP address for chat endpoints. |
| `PITCHIQ_MAX_CONCURRENT_CHATS`| `2` | Max simultaneous LLM calls (prevents local lockups). |
| `PITCHIQ_CHAT_TIMEOUT_S` | `90` | Hard timeout (in seconds) for SSE chat streaming. |
| `PITCHIQ_DB` | `pitchiq.db` | Path to the SQLite database. |
| `OPENAI_API_KEY` | (None) | Required if using an OpenAI model. |

## Error Format

All 4xx and 5xx responses (except `/ready` 503) share this structure:

```json
{
  "error": {
    "code": "invalid_input",
    "message": "Human readable message explaining what went wrong.",
    "request_id": "a1b2c3d4e5f6..."
  }
}
```

**Common Error Codes:**
- `invalid_input` (422): Validation error (e.g., unknown team name).
- `unauthorized` (401): Missing or invalid `X-API-Key`.
- `rate_limit_exceeded` (429): Too many requests (returns `Retry-After` header).
- `busy` (429): Concurrency limit reached.
- `model_not_ready` (503): The database or trained model file is missing.
- `llm_unavailable` (503): The agent or LLM crashed/timed out.
- `internal_error` (500): Unexpected backend crash.

---

## Diagnostics

### GET `/health`
Simple liveness check. Always returns 200.

**Response:**
```json
{
  "status": "ok"
}
```

### GET `/ready`
Readiness check. Verifies the database, the trained model, the vector DB, and the LLM. Returns 200 if the database and model exist. Returns 503 otherwise.

**Response:**
```json
{
  "ready": true,
  "dependencies": {
    "database": { "ok": true },
    "model": { "ok": true },
    "qdrant": { "ok": true },
    "llm": { "ok": false, "message": "Ollama not reachable" }
  }
}
```

---

## Chat Endpoints

### POST `/api/v1/chat`
Blocking question answering.

**Request:**
```json
{
  "question": "Who wins Arsenal vs Chelsea?",
  "thread_id": "optional-conversation-id"
}
```

**Response:**
```json
{
  "request_id": "...",
  "thread_id": "...",
  "answer": "Arsenal is favoured...",
  "tool_calls": [
    {
      "tool": "predict_match_tool",
      "args": { "home_team": "Arsenal", "away_team": "Chelsea" },
      "ok": true,
      "elapsed_ms": 310
    }
  ],
  "sources": [],
  "prediction": null,
  "usage": { "llm_calls": 1, "input_tokens": 512, "output_tokens": 128 },
  "elapsed_s": 2.5,
  "revisions": 0
}
```

**Curl Example:**
```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"question":"Who wins Arsenal vs Chelsea?"}'
```

### POST `/api/v1/chat/stream`
Server-Sent Events streaming version of the chat endpoint.

**Request:** Same as `/chat`.

**SSE Events:**

| Event | Payload | Notes |
|---|---|---|
| `start` | `{request_id, thread_id}` | Sent immediately. |
| `tool_call` | `{id, name, args}` | Agent invoked a tool. |
| `tool_result` | `{id, name, ok, elapsed_ms, summary}` | Tool finished. Artifacts are truncated to `summary`. |
| `revision` | `{detail}` | Grounding check triggered a rewrite. |
| `answer` | `{text, sources, prediction, usage, elapsed_s}` | Final response. (Never sent if `error` fires). |
| `error` | `{code, message}` | Backend failed or timed out. |
| `done` | `{}` | Always sent last to terminate the stream. |

**Curl Example:**
```bash
curl -N -X POST http://localhost:8000/api/v1/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"question":"Who wins Arsenal vs Chelsea?"}'
```

---

## Data Endpoints

### POST `/api/v1/predict`
Predict a match outcome using XGBoost (no LLM).

**Request:**
```json
{
  "home_team": "Arsenal",
  "away_team": "Chelsea",
  "match_date": "2026-10-25"
}
```

**Response:**
```json
{
  "home": "Arsenal",
  "away": "Chelsea",
  "match_date": "2026-10-25",
  "probabilities": {
    "home_win": 0.45,
    "draw": 0.25,
    "away_win": 0.30
  },
  "most_likely": "home_win",
  "why": [
    {
      "factor": "home team's Elo rating",
      "value": 1950.5,
      "pushes": "towards",
      "strength": 0.15
    }
  ],
  "context": {
    "elo_home": 1950.5,
    "elo_away": 1820.0,
    "home_points_per_game_last5": 2.2,
    "away_points_per_game_last5": 1.4,
    "home_rest_days": 7,
    "away_rest_days": 4
  },
  "warnings": [],
  "model": {
    "trained_through": "2026-05-19",
    "test_log_loss": 0.98,
    "note": "..."
  }
}
```

### GET `/api/v1/teams`
List active teams. Pass `?all=true` for all historical teams.

### GET `/api/v1/teams/{team}/form?matches=5`
Recent form for a team.

### GET `/api/v1/teams/{team}/availability`
Live FPL availability (injuries, suspensions).

### GET `/api/v1/fixtures?team=Arsenal&limit=10`
Upcoming match schedule.

### GET `/api/v1/model`
Model card metrics.
