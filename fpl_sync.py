"""Fantasy Premier League data: who is injured, who plays where, what is the schedule.

FPL's public web API is UNOFFICIAL, so it can change without notice. Everything that
talks to it lives in this one file: if it breaks, this is the only place to fix.

    python fpl_sync.py          # save today's player availability into pitchiq.db

Tables written:
    fpl_players    the CURRENT picture (replaced each run)
    fpl_teams      FPL team ids -> our canonical names
    fpl_snapshots  one dated row per player per day, kept FOREVER. After a season this
                   becomes real training data ("was this player fit on match day?").
"""
import logging
import os
import sqlite3
import sys
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

from teams import canonical

BASE_DIR = Path(os.getenv("PITCHIQ_HOME", Path(__file__).resolve().parent))
DB_PATH = Path(os.getenv("PITCHIQ_DB", BASE_DIR / "pitchiq.db"))   # same rule as etl.py / predictor.py

BOOTSTRAP_URL = "https://fantasy.premierleague.com/api/bootstrap-static/"
FIXTURES_URL = "https://fantasy.premierleague.com/api/fixtures/"
HEADERS = {"User-Agent": "PitchIQ/1.0 (learning project)"}
POSITIONS = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
STATUS_LABELS = {"a": "available", "d": "doubtful", "i": "injured", "s": "suspended",
                 "u": "unavailable", "n": "not in squad"}

log = logging.getLogger("pitchiq.fpl")
_CACHE: dict[str, tuple[float, object]] = {}   # url -> (fetched_at, json)


# --------------------------------------------------------------------------- #
# Fetching (timeouts, error codes, a short cache so one chat message = one request)
# --------------------------------------------------------------------------- #
def _get_json(url: str, ttl: int = 600):
    hit = _CACHE.get(url)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    resp = requests.get(url, headers=HEADERS, timeout=(5, 20))
    resp.raise_for_status()                 # 403/500 -> a clear error, not garbage data
    data = resp.json()
    _CACHE[url] = (time.time(), data)
    return data


def fetch_bootstrap(ttl: int = 600) -> dict:
    return _get_json(BOOTSTRAP_URL, ttl)


def fetch_fixtures(ttl: int = 600) -> list:
    return _get_json(FIXTURES_URL, ttl)


# --------------------------------------------------------------------------- #
# Shaping (pure functions: easy to test without the internet)
# --------------------------------------------------------------------------- #
def team_table(bootstrap: dict) -> pd.DataFrame:
    rows = [{"id": t["id"], "fpl_name": t["name"], "name": canonical(t["name"]) or t["name"],
             "strength": t.get("strength")} for t in bootstrap["teams"]]
    return pd.DataFrame(rows)


def players_frame(bootstrap: dict) -> pd.DataFrame:
    """One row per player, with OUR team names and the injury news FPL publishes."""
    teams = team_table(bootstrap).set_index("id")["name"].to_dict()
    rows = []
    for p in bootstrap["elements"]:
        rows.append({
            "player_id": p["id"], "team_id": p["team"], "team": teams.get(p["team"], f"Team {p['team']}"),
            "name": p.get("web_name") or f"{p.get('first_name', '')} {p.get('second_name', '')}".strip(),
            "position": POSITIONS.get(p.get("element_type"), "?"),
            "status": p.get("status", "a"), "news": (p.get("news") or "").strip(),
            "news_added": p.get("news_added"),
            "chance_next_round": p.get("chance_of_playing_next_round"),
            "form": pd.to_numeric(p.get("form"), errors="coerce"),
            "total_points": p.get("total_points", 0), "minutes": p.get("minutes", 0),
        })
    df = pd.DataFrame(rows)
    df["form"] = df["form"].fillna(0.0)
    return df


def upcoming_fixtures(bootstrap: dict, fixtures: list, *, now: Optional[datetime] = None,
                      limit: int = 10, team: Optional[str] = None) -> list[dict]:
    """Next scheduled matches, soonest first. Skips games with no kickoff time yet
    (FPL lists postponed / not-yet-scheduled games with kickoff_time = null)."""
    now = now or datetime.now(timezone.utc)
    names = team_table(bootstrap).set_index("id")["name"].to_dict()
    out = []
    for f in fixtures:
        if f.get("finished") or not f.get("kickoff_time"):
            continue
        kickoff = datetime.strptime(f["kickoff_time"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if kickoff < now - timedelta(hours=3):          # already played, result not processed yet
            continue
        home, away = names.get(f["team_h"], f"Team {f['team_h']}"), names.get(f["team_a"], f"Team {f['team_a']}")
        if team and team not in (home, away):
            continue
        out.append({"kickoff_utc": kickoff, "home": home, "away": away, "gameweek": f.get("event")})
    out.sort(key=lambda m: m["kickoff_utc"])
    return out[:limit]


# --------------------------------------------------------------------------- #
# Saving
# --------------------------------------------------------------------------- #
SNAPSHOT_SQL = """
CREATE TABLE IF NOT EXISTS fpl_snapshots (
    snapshot_date TEXT NOT NULL, player_id INTEGER NOT NULL, team TEXT NOT NULL, name TEXT NOT NULL,
    position TEXT, status TEXT, news TEXT, chance_next_round INTEGER,
    form REAL, total_points INTEGER, minutes INTEGER,
    PRIMARY KEY (snapshot_date, player_id)
)"""


def sync_fpl_data(db_path: Path = DB_PATH, bootstrap: Optional[dict] = None) -> dict:
    bootstrap = bootstrap or fetch_bootstrap(ttl=0)
    players, teams = players_frame(bootstrap), team_table(bootstrap)
    today = datetime.now(timezone.utc).date().isoformat()

    snap_cols = ["player_id", "team", "name", "position", "status", "news", "chance_next_round",
                 "form", "total_points", "minutes"]
    snap = players[snap_cols].copy()
    snap.insert(0, "snapshot_date", today)
    snap = snap.astype(object).where(snap.notna(), None)

    with closing(sqlite3.connect(db_path)) as conn:
        players.to_sql("fpl_players", conn, if_exists="replace", index=False)
        teams.to_sql("fpl_teams", conn, if_exists="replace", index=False)
        conn.execute(SNAPSHOT_SQL)
        with conn:   # INSERT OR REPLACE = running twice in one day never duplicates
            conn.executemany(
                f"INSERT OR REPLACE INTO fpl_snapshots VALUES ({','.join('?' * (len(snap_cols) + 1))})",
                snap.itertuples(index=False, name=None))
    flagged = int((players["status"] != "a").sum())
    log.info("Saved %d players (%d flagged as not fully fit) to %s", len(players), flagged, db_path)
    return {"players": len(players), "flagged": flagged, "snapshot_date": today}


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    try:
        sync_fpl_data()
        return 0
    except (requests.RequestException, KeyError, sqlite3.Error) as exc:
        log.error("FPL sync failed: %s: %s", type(exc).__name__, exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())