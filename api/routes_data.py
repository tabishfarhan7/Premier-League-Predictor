"""PitchIQ API – data endpoints (no LLM involved).

GET  /api/v1/teams
GET  /api/v1/teams/{team}/form
GET  /api/v1/teams/{team}/availability
GET  /api/v1/fixtures
GET  /api/v1/model
POST /api/v1/predict
"""
from __future__ import annotations

import logging
import math
import uuid
from typing import Any, Optional

import anyio
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

import predictor as predictor_mod   # module-level import for monkeypatching
import teams as teams_mod
import fpl_sync as fpl_mod
from api.schemas import (
    AvailabilityResponse,
    FixtureItem,
    FixturesResponse,
    FormMatch,
    FormResponse,
    FormSummary,
    ModelCardResponse,
    PlayerAvailability,
    PredictRequest,
    PredictionContext,
    PredictionModel,
    PredictionProbabilities,
    PredictionResponse,
    TeamItem,
    TeamsResponse,
    WhyFactor,
)
from api.security import error_response

log = logging.getLogger("pitchiq.api.data")

router = APIRouter()

_CACHE_HEADERS = {"Cache-Control": "public, max-age=60"}


def _sanitise(value: Any) -> Any:
    """Recursively replace NaN/Inf with None so JSON stays valid."""
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, dict):
        return {k: _sanitise(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitise(v) for v in value]
    return value


def _normalise_prediction(raw: dict) -> dict:
    """Move the dynamic 'why_<outcome>' key to a stable 'why' key."""
    most = raw.get("most_likely", "")
    dynamic_key = f"why_{most}"
    why = raw.pop(dynamic_key, []) if dynamic_key in raw else []
    # Also remove any other why_ keys (shouldn't exist but be safe)
    for k in list(raw.keys()):
        if k.startswith("why_"):
            raw.pop(k)
    raw["why"] = why
    return raw


# --------------------------------------------------------------------------- #
# POST /predict
# --------------------------------------------------------------------------- #
@router.post(
    "/predict",
    response_model=PredictionResponse,
    summary="Predict a match outcome (no LLM)",
    description=(
        "Run the XGBoost model directly. Returns calibrated home/draw/away "
        "probabilities with feature explanations. The dynamic 'why_<outcome>' key "
        "is normalised to a stable 'why' list."
    ),
    tags=["Prediction"],
    responses={
        200: {"description": "Prediction result"},
        422: {"description": "Unknown team or invalid input"},
        503: {"description": "Model or database files missing"},
    },
)
async def predict_endpoint(body: PredictRequest, request: Request) -> PredictionResponse:
    request_id: str = getattr(request.state, "request_id", uuid.uuid4().hex)

    try:
        raw = await anyio.to_thread.run_sync(
            lambda: predictor_mod.predict_match(
                body.home_team, body.away_team, body.match_date
            ),
            cancellable=False,
        )
    except ValueError as exc:
        return error_response(422, "invalid_input", str(exc), request_id)  # type: ignore[return-value]
    except FileNotFoundError as exc:
        log.error("predict: model/db missing: %s", exc)
        return error_response(503, "model_not_ready", "Model or database not found.", request_id)  # type: ignore[return-value]
    except Exception as exc:  # noqa: BLE001
        log.exception("predict: unexpected error request_id=%s", request_id)
        return error_response(500, "internal_error", "An unexpected error occurred.", request_id)  # type: ignore[return-value]

    raw = _sanitise(raw)
    raw = _normalise_prediction(raw)

    prob = raw.get("probabilities", {})
    ctx = raw.get("context", {})
    mdl = raw.get("model", {})

    return PredictionResponse(
        home=raw["home"],
        away=raw["away"],
        match_date=raw["match_date"],
        probabilities=PredictionProbabilities(**prob),
        most_likely=raw["most_likely"],
        why=[WhyFactor(**w) for w in raw.get("why", [])],
        context=PredictionContext(**ctx),
        warnings=raw.get("warnings", []),
        model=PredictionModel(**mdl),
    )


# --------------------------------------------------------------------------- #
# GET /teams
# --------------------------------------------------------------------------- #
@router.get(
    "/teams",
    response_model=TeamsResponse,
    summary="List Premier League clubs",
    description=(
        "Returns the clubs active in the current season (highest season_idx). "
        "Pass ?all=true to include every club ever in the database."
    ),
    tags=["Teams"],
)
async def teams_endpoint(
    all: bool = Query(default=False, description="Include all historical clubs"),
    request: Request = None,  # type: ignore[assignment]
) -> TeamsResponse:
    request_id: str = getattr(request.state, "request_id", uuid.uuid4().hex) if request else ""

    try:
        df = await anyio.to_thread.run_sync(
            lambda: predictor_mod.load_matches(),
            cancellable=False,
        )
    except FileNotFoundError as exc:
        log.error("teams: db missing: %s", exc)
        return error_response(503, "model_not_ready", "Database not found.", request_id)  # type: ignore[return-value]
    except Exception as exc:  # noqa: BLE001
        log.exception("teams: unexpected error")
        return error_response(500, "internal_error", "An unexpected error occurred.", request_id)  # type: ignore[return-value]

    if all:
        club_names = sorted(set(df["home_team"]) | set(df["away_team"]))
    else:
        current_idx = df["season_idx"].max()
        current = df[df["season_idx"] == current_idx]
        club_names = sorted(set(current["home_team"]) | set(current["away_team"]))

    items = []
    for name in club_names:
        aliases = teams_mod.TEAMS.get(name, [])
        items.append(TeamItem(name=name, aliases=aliases))

    resp = JSONResponse(
        content={"teams": [t.model_dump() for t in items]},
        headers=_CACHE_HEADERS,
    )
    return resp  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# GET /teams/{team}/form
# --------------------------------------------------------------------------- #
@router.get(
    "/teams/{team}/form",
    response_model=FormResponse,
    summary="Recent form for a team",
    description="Last N completed matches with result, score and a summary.",
    tags=["Teams"],
)
async def team_form_endpoint(
    team: str,
    matches: int = Query(default=5, ge=1, le=10, description="Number of matches (1-10)"),
    request: Request = None,  # type: ignore[assignment]
) -> FormResponse:
    request_id: str = getattr(request.state, "request_id", uuid.uuid4().hex) if request else ""

    if len(team) > 60:
        return error_response(422, "invalid_input", "Team name too long (max 60 chars).", request_id)  # type: ignore[return-value]

    # Resolve canonical name
    canon = teams_mod.canonical(team)
    if canon is None:
        try:
            teams_mod.require_canonical(team)
        except ValueError as exc:
            return error_response(422, "invalid_input", str(exc), request_id)  # type: ignore[return-value]

    canon = canon or team

    try:
        df = await anyio.to_thread.run_sync(
            lambda: predictor_mod.load_matches(),
            cancellable=False,
        )
    except FileNotFoundError as exc:
        log.error("form: db missing: %s", exc)
        return error_response(503, "model_not_ready", "Database not found.", request_id)  # type: ignore[return-value]
    except Exception as exc:  # noqa: BLE001
        log.exception("form: unexpected error")
        return error_response(500, "internal_error", "An unexpected error occurred.", request_id)  # type: ignore[return-value]

    # Find played matches for this team
    played = df[df["home_goals"].notna() & df["away_goals"].notna()]
    home_mask = played["home_team"] == canon
    away_mask = played["away_team"] == canon
    team_matches = played[home_mask | away_mask].sort_values("match_date", ascending=False).head(matches)

    form_list: list[FormMatch] = []
    wins = draws = losses = goals_for = goals_against = 0

    for row in team_matches.itertuples():
        is_home = row.home_team == canon
        venue = "H" if is_home else "A"
        opponent = row.away_team if is_home else row.home_team
        gf = int(row.home_goals) if is_home else int(row.away_goals)
        ga = int(row.away_goals) if is_home else int(row.home_goals)
        score = f"{gf}-{ga}"
        if gf > ga:
            result_char = "W"
            wins += 1
        elif gf == ga:
            result_char = "D"
            draws += 1
        else:
            result_char = "L"
            losses += 1
        goals_for += gf
        goals_against += ga
        form_list.append(FormMatch(
            date=str(row.match_date.date()),
            venue=venue,
            opponent=opponent,
            score=score,
            result=result_char,
        ))

    summary = FormSummary(
        wins=wins,
        draws=draws,
        losses=losses,
        points=wins * 3 + draws,
        goals_for=goals_for,
        goals_against=goals_against,
    )

    resp = JSONResponse(
        content=FormResponse(team=canon, matches=matches, form=form_list, summary=summary).model_dump(),
        headers=_CACHE_HEADERS,
    )
    return resp  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# GET /teams/{team}/availability
# --------------------------------------------------------------------------- #
@router.get(
    "/teams/{team}/availability",
    response_model=AvailabilityResponse,
    summary="Injured, doubtful or suspended players (FPL)",
    description="Live FPL data. Degrades gracefully if FPL is unavailable. NaN values are converted to null.",
    tags=["Teams"],
)
async def team_availability_endpoint(
    team: str,
    request: Request = None,  # type: ignore[assignment]
) -> AvailabilityResponse:
    request_id: str = getattr(request.state, "request_id", uuid.uuid4().hex) if request else ""

    if len(team) > 60:
        return error_response(422, "invalid_input", "Team name too long.", request_id)  # type: ignore[return-value]

    canon = teams_mod.canonical(team)
    if canon is None:
        try:
            teams_mod.require_canonical(team)
        except ValueError as exc:
            return error_response(422, "invalid_input", str(exc), request_id)  # type: ignore[return-value]
    canon = canon or team

    try:
        def _fetch():
            bootstrap = fpl_mod.fetch_bootstrap()
            df = fpl_mod.players_frame(bootstrap)
            team_df = df[df["team"] == canon]
            flagged = team_df[team_df["status"].isin(["i", "d", "s"])].sort_values(["status", "name"])
            players = []
            for p in flagged.itertuples():
                cnr = p.chance_next_round
                # NaN check
                if cnr is not None and isinstance(cnr, float) and math.isnan(cnr):
                    cnr = None
                elif cnr is not None:
                    cnr = int(cnr)
                label = {"i": "injured", "d": "doubtful", "s": "suspended"}.get(p.status, p.status)
                news_val = p.news if p.news else None
                players.append(PlayerAvailability(
                    name=p.name,
                    position=p.position,
                    status=label,
                    news=news_val,
                    chance_next_round=cnr,
                ))
            return players

        players = await anyio.to_thread.run_sync(_fetch, cancellable=False)
    except Exception as exc:  # noqa: BLE001
        log.warning("availability: FPL fetch failed for %s: %s", canon, exc)
        # Degrade gracefully – return empty list with a note
        resp = JSONResponse(
            content={"team": canon, "players": []},
            headers=_CACHE_HEADERS,
        )
        return resp  # type: ignore[return-value]

    resp = JSONResponse(
        content=AvailabilityResponse(team=canon, players=players).model_dump(),
        headers=_CACHE_HEADERS,
    )
    return resp  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# GET /fixtures
# --------------------------------------------------------------------------- #
@router.get(
    "/fixtures",
    response_model=FixturesResponse,
    summary="Upcoming Premier League fixtures",
    description="Live FPL data. Degrades gracefully if FPL is unavailable.",
    tags=["Fixtures"],
)
async def fixtures_endpoint(
    team: Optional[str] = Query(default=None, description="Filter to one team (max 60 chars)"),
    limit: int = Query(default=10, ge=1, le=20, description="Max fixtures to return (1-20)"),
    request: Request = None,  # type: ignore[assignment]
) -> FixturesResponse:
    request_id: str = getattr(request.state, "request_id", uuid.uuid4().hex) if request else ""

    if team and len(team) > 60:
        return error_response(422, "invalid_input", "Team name too long.", request_id)  # type: ignore[return-value]

    canon_team: Optional[str] = None
    if team:
        canon_team = teams_mod.canonical(team)
        if canon_team is None:
            try:
                teams_mod.require_canonical(team)
            except ValueError as exc:
                return error_response(422, "invalid_input", str(exc), request_id)  # type: ignore[return-value]
        canon_team = canon_team or team

    try:
        def _fetch():
            bootstrap = fpl_mod.fetch_bootstrap()
            raw_fixtures = fpl_mod.fetch_fixtures()
            games = fpl_mod.upcoming_fixtures(bootstrap, raw_fixtures, limit=limit, team=canon_team)
            items = []
            for g in games:
                kickoff = g["kickoff_utc"]
                items.append(FixtureItem(
                    kickoff_utc=kickoff.isoformat(),
                    home=g["home"],
                    away=g["away"],
                    gameweek=g.get("gameweek"),
                    match_date=kickoff.strftime("%Y-%m-%d"),
                ))
            return items

        items = await anyio.to_thread.run_sync(_fetch, cancellable=False)
    except Exception as exc:  # noqa: BLE001
        log.warning("fixtures: FPL fetch failed: %s", exc)
        items = []

    resp = JSONResponse(
        content=FixturesResponse(fixtures=items).model_dump(),
        headers=_CACHE_HEADERS,
    )
    return resp  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# GET /model
# --------------------------------------------------------------------------- #
@router.get(
    "/model",
    response_model=ModelCardResponse,
    summary="Model card – how good is the XGBoost predictor?",
    description=(
        "Returns training metadata, honest test-set metrics across multiple "
        "baseline comparisons, calibration error, and top-8 feature importances."
    ),
    tags=["Model"],
)
async def model_card_endpoint(
    request: Request = None,  # type: ignore[assignment]
) -> ModelCardResponse:
    request_id: str = getattr(request.state, "request_id", uuid.uuid4().hex) if request else ""

    try:
        _, meta = await anyio.to_thread.run_sync(
            lambda: predictor_mod.load_model(),
            cancellable=False,
        )
    except FileNotFoundError as exc:
        log.error("model card: model missing: %s", exc)
        return error_response(503, "model_not_ready", "Model not found. Run predictor.py train first.", request_id)  # type: ignore[return-value]
    except Exception as exc:  # noqa: BLE001
        log.exception("model card: unexpected error")
        return error_response(500, "internal_error", "An unexpected error occurred.", request_id)  # type: ignore[return-value]

    importance: dict[str, float] = meta.get("feature_importance", {})
    top8 = dict(list(importance.items())[:8])

    resp = JSONResponse(
        content=ModelCardResponse(
            trained_through=meta["trained_through"],
            n_matches=meta["n_matches"],
            test_matches_scored=meta["test_matches_scored"],
            metrics_on_test=meta.get("metrics_on_test", {}),
            calibration_ece=meta.get("calibration_ece", 0.0),
            top_feature_importance=top8,
        ).model_dump(),
        headers=_CACHE_HEADERS,
    )
    return resp  # type: ignore[return-value]
