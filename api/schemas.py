"""PitchIQ API – Pydantic v2 schemas.

Every request body, response model, and SSE event payload is defined here so
the frontend contract is fully typed and /docs shows realistic examples.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


# --------------------------------------------------------------------------- #
# Shared / re-used fragments
# --------------------------------------------------------------------------- #
class ErrorDetail(BaseModel):
    """Single error object embedded in every error response."""

    code: str = Field(
        description="snake_case error code",
        examples=["invalid_input"],
    )
    message: str = Field(
        description="Human-readable explanation safe to show to end-users",
        examples=["Unknown team 'Nonexistent'. Did you mean: Arsenal?"],
    )
    request_id: str = Field(
        description="Echo of X-Request-ID header for log correlation",
        examples=["a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"],
    )


class ErrorResponse(BaseModel):
    """Envelope for every 4xx / 5xx response body."""

    error: ErrorDetail


# --------------------------------------------------------------------------- #
# Chat
# --------------------------------------------------------------------------- #
class ChatRequest(BaseModel):
    """Body for POST /api/v1/chat and POST /api/v1/chat/stream."""

    question: str = Field(
        description="The analyst question (1-500 characters after stripping whitespace)",
        examples=["Who wins Arsenal vs Chelsea this weekend?"],
    )
    thread_id: Optional[str] = Field(
        default=None,
        description=(
            "Conversation identifier (^[A-Za-z0-9_-]{8,64}$). "
            "Pass null/omit to start a new conversation; the server will generate one."
        ),
        examples=["conv-abc123XY"],
    )

    @field_validator("question", mode="before")
    @classmethod
    def strip_and_validate_question(cls, v: Any) -> str:
        if not isinstance(v, str):
            raise ValueError("question must be a string")
        v = v.strip()
        if not v:
            raise ValueError("question must not be empty")
        if len(v) > 500:
            raise ValueError("question must be at most 500 characters")
        return v

    @field_validator("thread_id", mode="before")
    @classmethod
    def validate_thread_id(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        if not isinstance(v, str):
            raise ValueError("thread_id must be a string or null")
        import re
        if not re.fullmatch(r"[A-Za-z0-9_\-]{8,64}", v):
            raise ValueError(
                "thread_id must match ^[A-Za-z0-9_-]{8,64}$"
            )
        return v


class ToolCallRecord(BaseModel):
    """One tool invocation recorded in the chat response."""

    tool: str = Field(examples=["predict_match_tool"])
    args: dict[str, Any] = Field(default_factory=dict, examples=[{"home_team": "Arsenal", "away_team": "Chelsea"}])
    ok: bool = Field(examples=[True])
    elapsed_ms: Optional[int] = Field(default=None, examples=[312])


class SourceRecord(BaseModel):
    """One news article cited in the answer."""

    source: Optional[str] = Field(default=None, examples=["BBC Sport"])
    url: Optional[str] = Field(default=None, examples=["https://www.bbc.co.uk/sport/football/..."])
    date: Optional[str] = Field(default=None, examples=["2026-10-01T09:00:00+00:00"])
    score: Optional[float] = Field(default=None, examples=[0.87])


class UsageInfo(BaseModel):
    """LLM token usage for the request."""

    llm_calls: int = Field(default=0, examples=[2])
    input_tokens: int = Field(default=0, examples=[1024])
    output_tokens: int = Field(default=0, examples=[256])


class ChatResponse(BaseModel):
    """Response body for POST /api/v1/chat."""

    request_id: str = Field(examples=["a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"])
    thread_id: str = Field(examples=["conv-abc123XY"])
    answer: str = Field(examples=["Arsenal is slightly favoured with a 45% home-win probability…"])
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    sources: list[SourceRecord] = Field(default_factory=list)
    prediction: Optional[dict[str, Any]] = Field(
        default=None,
        description="Raw predictor output when predict_match_tool was called",
    )
    usage: UsageInfo = Field(default_factory=UsageInfo)
    elapsed_s: float = Field(examples=[4.2])
    revisions: int = Field(default=0, description="Grounding-check correction rounds", examples=[0])


# --------------------------------------------------------------------------- #
# Predict
# --------------------------------------------------------------------------- #
class PredictRequest(BaseModel):
    """Body for POST /api/v1/predict."""

    home_team: str = Field(
        description="Home team name (max 60 chars)",
        examples=["Arsenal"],
    )
    away_team: str = Field(
        description="Away team name (max 60 chars)",
        examples=["Chelsea"],
    )
    match_date: Optional[str] = Field(
        default=None,
        description="Kickoff date in YYYY-MM-DD format",
        examples=["2026-10-25"],
    )

    @field_validator("home_team", "away_team", mode="before")
    @classmethod
    def validate_team_name(cls, v: Any) -> str:
        if not isinstance(v, str):
            raise ValueError("team name must be a string")
        v = v.strip()
        if not v:
            raise ValueError("team name must not be empty")
        if len(v) > 60:
            raise ValueError("team name must be at most 60 characters")
        return v

    @field_validator("match_date", mode="before")
    @classmethod
    def validate_date(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        if not isinstance(v, str):
            raise ValueError("match_date must be a string or null")
        v = v.strip()
        if not v:
            return None
        import datetime
        try:
            datetime.date.fromisoformat(v)
        except ValueError:
            raise ValueError("match_date must be a real date in YYYY-MM-DD format")
        return v


class WhyFactor(BaseModel):
    """One feature contribution explaining the prediction."""

    factor: str = Field(examples=["home team's Elo rating"])
    value: Optional[float] = Field(default=None, examples=[1523.0])
    pushes: str = Field(examples=["towards"])
    strength: float = Field(examples=[0.142])


class PredictionContext(BaseModel):
    elo_home: Optional[float] = Field(default=None, examples=[1523])
    elo_away: Optional[float] = Field(default=None, examples=[1487])
    home_points_per_game_last5: Optional[float] = Field(default=None, examples=[2.2])
    away_points_per_game_last5: Optional[float] = Field(default=None, examples=[1.6])
    home_rest_days: Optional[int] = Field(default=None, examples=[7])
    away_rest_days: Optional[int] = Field(default=None, examples=[4])


class PredictionProbabilities(BaseModel):
    home_win: float = Field(examples=[0.4512])
    draw: float = Field(examples=[0.2873])
    away_win: float = Field(examples=[0.2615])


class PredictionModel(BaseModel):
    trained_through: str = Field(examples=["2026-05-19"])
    test_log_loss: float = Field(examples=[0.9874])
    note: str = Field(examples=["Calibrated probabilities from team ratings and recent form only."])


class PredictionResponse(BaseModel):
    """Response body for POST /api/v1/predict."""

    home: str = Field(examples=["Arsenal"])
    away: str = Field(examples=["Chelsea"])
    match_date: str = Field(examples=["2026-10-25"])
    probabilities: PredictionProbabilities
    most_likely: str = Field(examples=["home_win"])
    why: list[WhyFactor] = Field(
        description="Top factors for the most-likely outcome (normalised from dynamic 'why_<outcome>' key)",
    )
    context: PredictionContext
    warnings: list[str] = Field(default_factory=list)
    model: PredictionModel


# --------------------------------------------------------------------------- #
# Teams
# --------------------------------------------------------------------------- #
class TeamItem(BaseModel):
    name: str = Field(examples=["Arsenal"])
    aliases: list[str] = Field(examples=[["gunners"]])


class TeamsResponse(BaseModel):
    teams: list[TeamItem]


class FormMatch(BaseModel):
    date: str = Field(examples=["2026-10-01"])
    venue: str = Field(description="H for home, A for away", examples=["H"])
    opponent: str = Field(examples=["Chelsea"])
    score: str = Field(examples=["2-1"])
    result: str = Field(description="W, D or L", examples=["W"])


class FormSummary(BaseModel):
    wins: int = Field(examples=[3])
    draws: int = Field(examples=[1])
    losses: int = Field(examples=[1])
    points: int = Field(examples=[10])
    goals_for: int = Field(examples=[7])
    goals_against: int = Field(examples=[3])


class FormResponse(BaseModel):
    team: str = Field(examples=["Arsenal"])
    matches: int = Field(description="Number of matches requested", examples=[5])
    form: list[FormMatch]
    summary: FormSummary


# --------------------------------------------------------------------------- #
# Availability (FPL)
# --------------------------------------------------------------------------- #
class PlayerAvailability(BaseModel):
    name: str = Field(examples=["Saliba"])
    position: str = Field(examples=["DEF"])
    status: str = Field(examples=["injured"])
    news: Optional[str] = Field(default=None, examples=["Hamstring injury, 75% chance next round"])
    chance_next_round: Optional[int] = Field(default=None, examples=[75])


class AvailabilityResponse(BaseModel):
    team: str = Field(examples=["Arsenal"])
    players: list[PlayerAvailability]


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
class FixtureItem(BaseModel):
    kickoff_utc: str = Field(description="ISO 8601 UTC datetime", examples=["2026-10-25T15:00:00+00:00"])
    home: str = Field(examples=["Arsenal"])
    away: str = Field(examples=["Chelsea"])
    gameweek: Optional[int] = Field(default=None, examples=[9])
    match_date: str = Field(description="YYYY-MM-DD local date", examples=["2026-10-25"])


class FixturesResponse(BaseModel):
    fixtures: list[FixtureItem]


# --------------------------------------------------------------------------- #
# Model card
# --------------------------------------------------------------------------- #
class ModelCardResponse(BaseModel):
    trained_through: str = Field(examples=["2026-05-19"])
    n_matches: int = Field(examples=[3800])
    test_matches_scored: int = Field(examples=[760])
    metrics_on_test: dict[str, Any] = Field(
        description="Per-model metrics dict: accuracy, log_loss, brier, rps",
    )
    calibration_ece: float = Field(examples=[0.032])
    top_feature_importance: dict[str, float] = Field(
        description="Top 8 features by importance score",
    )


# --------------------------------------------------------------------------- #
# Health / Ready
# --------------------------------------------------------------------------- #
class HealthResponse(BaseModel):
    status: str = Field(examples=["ok"])


class DependencyStatus(BaseModel):
    ok: bool
    message: Optional[str] = None


class ReadyResponse(BaseModel):
    ready: bool
    dependencies: dict[str, DependencyStatus]
