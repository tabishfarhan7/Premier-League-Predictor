"""PitchIQ ETL pipeline: Premier League results -> validated SQLite database.

Extract   download one CSV per season from football-data.co.uk
Transform clean, rename, type-cast and VALIDATE every row
Load      idempotent upsert into SQLite (safe to run as often as you like)

Usage:
    python etl.py                  # last 10 seasons (past seasons are cached)
    python etl.py --seasons 5      # last 5 seasons
    python etl.py --current-only   # only the running season (fast weekly refresh)
    python etl.py --force          # re-download everything

Exit code is 0 on success and 1 if ANY season failed, so cron / CI can tell.
"""
from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
from datetime import date, datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:  # pandera >= 0.24 moved the pandas API here
    import pandera.pandas as pa
except ImportError:  # older versions
    import pandera as pa
from pandera.typing import Series

# --------------------------------------------------------------------------- #
# Configuration. Paths hang off this file's folder, NOT the current directory,
# because cron starts jobs from an unpredictable working directory.
# --------------------------------------------------------------------------- #
BASE_DIR = Path(os.getenv("PITCHIQ_HOME", Path(__file__).resolve().parent))
RAW_DIR = BASE_DIR / "data" / "raw"      # bronze: untouched downloads
CLEAN_DIR = BASE_DIR / "data" / "clean"  # silver: validated CSVs
LOG_DIR = BASE_DIR / "logs"
DB_PATH = Path(os.getenv("PITCHIQ_DB", BASE_DIR / "pitchiq.db"))

URL_TEMPLATE = "https://www.football-data.co.uk/mmz4281/{code}/E0.csv"
SEASON_ROLLOVER_MONTH = 8  # new Premier League season starts in August

# Original CSV column -> readable name. Readable names matter later: the
# Text-to-SQL model writes better queries against `home_goals` than `FTHG`.
# (Also, `AS` is a reserved SQL word, so the raw name would break queries.)
COLUMN_MAP = {
    "Date": "match_date",
    "HomeTeam": "home_team",
    "AwayTeam": "away_team",
    "FTHG": "home_goals",
    "FTAG": "away_goals",
    "FTR": "result",
    "HS": "home_shots",
    "AS": "away_shots",
    "HST": "home_shots_on_target",
    "AST": "away_shots_on_target",
    "HF": "home_fouls",
    "AF": "away_fouls",
    "HC": "home_corners",
    "AC": "away_corners",
    "HY": "home_yellows",
    "AY": "away_yellows",
    "HR": "home_reds",
    "AR": "away_reds",
    "B365H": "odds_home",   # Bet365 decimal odds: the benchmark for our model
    "B365D": "odds_draw",
    "B365A": "odds_away",
}
REQUIRED_RAW = ["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR"]
INT_STATS = [
    "home_shots", "away_shots", "home_shots_on_target", "away_shots_on_target",
    "home_fouls", "away_fouls", "home_corners", "away_corners",
    "home_yellows", "away_yellows", "home_reds", "away_reds",
]
ODDS = ["odds_home", "odds_draw", "odds_away"]
DB_COLUMNS = (
    ["match_id", "season", "match_date", "home_team", "away_team",
     "home_goals", "away_goals", "result"] + INT_STATS + ODDS
)

log = logging.getLogger("pitchiq.etl")


class DataValidationError(Exception):
    """Raised when downloaded data breaks our rules. Better to stop than to store bad data."""


# --------------------------------------------------------------------------- #
# The gatekeeper: every row must satisfy this before it can touch the database.
# Think of it as a Zod schema for a whole table.
# --------------------------------------------------------------------------- #
class MatchSchema(pa.DataFrameModel):
    match_id: Series[str] = pa.Field(unique=True)
    season: Series[str]
    match_date: Series[pa.DateTime]
    home_team: Series[str] = pa.Field(str_length={"min_value": 2})
    away_team: Series[str] = pa.Field(str_length={"min_value": 2})
    home_goals: Series[int] = pa.Field(ge=0, le=20)
    away_goals: Series[int] = pa.Field(ge=0, le=20)
    result: Series[str] = pa.Field(isin=["H", "D", "A"])
    # Optional extras: may be missing in some seasons, but never negative.
    home_shots: Series[pd.Int64Dtype] = pa.Field(ge=0, nullable=True)
    away_shots: Series[pd.Int64Dtype] = pa.Field(ge=0, nullable=True)
    home_shots_on_target: Series[pd.Int64Dtype] = pa.Field(ge=0, nullable=True)
    away_shots_on_target: Series[pd.Int64Dtype] = pa.Field(ge=0, nullable=True)
    home_fouls: Series[pd.Int64Dtype] = pa.Field(ge=0, nullable=True)
    away_fouls: Series[pd.Int64Dtype] = pa.Field(ge=0, nullable=True)
    home_corners: Series[pd.Int64Dtype] = pa.Field(ge=0, nullable=True)
    away_corners: Series[pd.Int64Dtype] = pa.Field(ge=0, nullable=True)
    home_yellows: Series[pd.Int64Dtype] = pa.Field(ge=0, nullable=True)
    away_yellows: Series[pd.Int64Dtype] = pa.Field(ge=0, nullable=True)
    home_reds: Series[pd.Int64Dtype] = pa.Field(ge=0, nullable=True)
    away_reds: Series[pd.Int64Dtype] = pa.Field(ge=0, nullable=True)
    # Decimal odds below 1.0 are impossible.
    odds_home: Series[float] = pa.Field(ge=1.0, nullable=True)
    odds_draw: Series[float] = pa.Field(ge=1.0, nullable=True)
    odds_away: Series[float] = pa.Field(ge=1.0, nullable=True)

    class Config:
        coerce = True   # convert types first, then check
        strict = False  # ignore extra columns

    @pa.dataframe_check
    def result_matches_score(cls, df: pd.DataFrame) -> pd.Series:
        """The H/D/A label must agree with the actual score."""
        expected = (df["home_goals"] - df["away_goals"]).map(
            lambda d: "H" if d > 0 else ("A" if d < 0 else "D")
        )
        return expected == df["result"]

    @pa.dataframe_check
    def teams_differ(cls, df: pd.DataFrame) -> pd.Series:
        return df["home_team"] != df["away_team"]


# --------------------------------------------------------------------------- #
# Seasons
# --------------------------------------------------------------------------- #
def current_season_start_year(today: date | None = None) -> int:
    today = today or date.today()
    return today.year if today.month >= SEASON_ROLLOVER_MONTH else today.year - 1


def season_code(start_year: int) -> str:
    """2023 -> '2324', 2026 -> '2627' (the format football-data.co.uk uses)."""
    return f"{start_year % 100:02d}{(start_year + 1) % 100:02d}"


def season_codes(n: int, today: date | None = None) -> list[str]:
    end = current_season_start_year(today)
    return [season_code(y) for y in range(end - n + 1, end + 1)]


# --------------------------------------------------------------------------- #
# EXTRACT
# --------------------------------------------------------------------------- #
def make_session() -> requests.Session:
    """HTTP session that retries temporary failures with growing delays."""
    retry = Retry(total=3, backoff_factor=1.0,
                  status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=("GET",))
    s = requests.Session()
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers["User-Agent"] = "PitchIQ-ETL/1.0 (learning project)"
    return s


def extract(code: str, session: requests.Session, *, is_current: bool,
            force: bool = False) -> Path:
    """Download one season's CSV into data/raw/. Finished seasons never change,
    so they are downloaded once and then reused from disk."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / f"E0_{code}.csv"
    if path.exists() and not force and not is_current:
        log.info("[%s] using cached raw file", code)
        return path

    url = URL_TEMPLATE.format(code=code)
    log.info("[%s] downloading %s", code, url)
    resp = session.get(url, timeout=(5, 30))   # (connect, read) seconds
    resp.raise_for_status()                    # 404/500 -> exception, not a junk file

    # A site error page can come back as HTTP 200. Real files start with "Div".
    if not resp.content.lstrip(b"\xef\xbb\xbf").startswith(b"Div"):
        raise DataValidationError(f"{url} did not return a CSV (got an error page?)")

    tmp = path.with_suffix(".tmp")             # write fully, then swap in: no half files
    tmp.write_bytes(resp.content)
    tmp.replace(path)
    return path


# --------------------------------------------------------------------------- #
# TRANSFORM
# --------------------------------------------------------------------------- #
def read_raw(path: Path) -> pd.DataFrame:
    """Read a CSV; older files are not UTF-8, so fall back to latin-1."""
    try:
        return pd.read_csv(path, encoding="utf-8-sig")
    except UnicodeDecodeError:
        return pd.read_csv(path, encoding="latin-1")


def transform(raw: pd.DataFrame, code: str) -> pd.DataFrame:
    missing = [c for c in REQUIRED_RAW if c not in raw.columns]
    if missing:
        raise DataValidationError(f"[{code}] source is missing columns: {missing}")

    df = raw.copy()
    for col in COLUMN_MAP:                      # optional stats absent in a season
        if col not in df.columns:
            df[col] = pd.NA
    df = df[list(COLUMN_MAP)].rename(columns=COLUMN_MAP)

    # Blank trailing rows are common in these CSVs; drop rows without core fields.
    core = ["match_date", "home_team", "away_team", "home_goals", "away_goals", "result"]
    before = len(df)
    df = df.dropna(subset=core).copy()
    if before - len(df):
        log.warning("[%s] dropped %d incomplete rows", code, before - len(df))
    if df.empty:
        raise DataValidationError(f"[{code}] no usable rows after cleaning")

    # Old files use DD/MM/YY, new ones DD/MM/YYYY: 'mixed' handles both.
    df["match_date"] = pd.to_datetime(df["match_date"], dayfirst=True, format="mixed")
    for col in ("home_team", "away_team", "result"):
        df[col] = df[col].astype(str).str.strip()
    df["result"] = df["result"].str.upper()
    df["season"] = code
    df["match_id"] = (df["match_date"].dt.strftime("%Y-%m-%d") + "_"
                      + df["home_team"] + "_" + df["away_team"])

    # Numeric columns: bad values become NaN, then validation decides.
    for col in ["home_goals", "away_goals", *INT_STATS]:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
    for col in ODDS:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
    df = df.dropna(subset=["home_goals", "away_goals"]).copy()

    try:
        df = MatchSchema.validate(df, lazy=True)   # lazy = report ALL problems at once
    except pa.errors.SchemaErrors as exc:
        sample = exc.failure_cases.head(10).to_string(index=False)
        raise DataValidationError(f"[{code}] validation failed:\n{sample}") from exc

    df["match_date"] = df["match_date"].dt.strftime("%Y-%m-%d")   # store as ISO text
    return df[DB_COLUMNS].reset_index(drop=True)


# --------------------------------------------------------------------------- #
# LOAD
# --------------------------------------------------------------------------- #
# Table DDL only — indexes are created AFTER column migration in init_db()
TABLE_SQL = """
CREATE TABLE IF NOT EXISTS matches (
    match_id TEXT PRIMARY KEY,
    season TEXT NOT NULL,
    match_date TEXT NOT NULL,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    home_goals INTEGER NOT NULL CHECK (home_goals >= 0),
    away_goals INTEGER NOT NULL CHECK (away_goals >= 0),
    result TEXT NOT NULL CHECK (result IN ('H','D','A')),
    home_shots INTEGER, away_shots INTEGER,
    home_shots_on_target INTEGER, away_shots_on_target INTEGER,
    home_fouls INTEGER, away_fouls INTEGER,
    home_corners INTEGER, away_corners INTEGER,
    home_yellows INTEGER, away_yellows INTEGER,
    home_reds INTEGER, away_reds INTEGER,
    odds_home REAL, odds_draw REAL, odds_away REAL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS etl_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    seasons TEXT NOT NULL,
    rows_processed INTEGER NOT NULL,
    status TEXT NOT NULL,
    error TEXT
);
"""

# Indexes referencing matches columns — run only after migration ensures columns exist
INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_matches_date ON matches(match_date);
CREATE INDEX IF NOT EXISTS idx_matches_home ON matches(home_team);
CREATE INDEX IF NOT EXISTS idx_matches_away ON matches(away_team);
"""


def _py(value):
    """sqlite3 cannot store pandas' NA or numpy numbers; convert to plain Python."""
    if pd.isna(value):
        return None
    return value.item() if hasattr(value, "item") else value


def init_db(conn: sqlite3.Connection, db_path: Path | None = None) -> sqlite3.Connection:
    """Initialise (or migrate) the database. Returns the connection to use
    (may be a brand-new connection if the old DB was wiped due to incompatible schema)."""
    # Step 1: create tables (no indexes yet — they may reference columns not yet added)
    conn.executescript(TABLE_SQL)

    # Step 2: sanity-check that the PRIMARY KEY column exists.
    # ALTER TABLE cannot add a PRIMARY KEY, so if match_id is missing the DB schema
    # is fundamentally incompatible and must be recreated from scratch.
    pk_cols = {row[1] for row in conn.execute("PRAGMA table_info(matches)")}
    if "match_id" not in pk_cols:
        log.warning(
            "DB schema is incompatible (missing PRIMARY KEY 'match_id'). "
            "Wiping %s and starting fresh — all data will be re-downloaded.", db_path
        )
        conn.close()
        if db_path and db_path.exists():
            db_path.unlink()
        conn = sqlite3.connect(db_path)
        conn.executescript(TABLE_SQL)

    # Step 3: migrate — add any additive columns missing from the existing table
    existing = {row[1] for row in conn.execute("PRAGMA table_info(matches)")}
    schema_cols = {
        "match_date": "TEXT NOT NULL DEFAULT ''",
        "home_team": "TEXT NOT NULL DEFAULT ''",
        "away_team": "TEXT NOT NULL DEFAULT ''",
        "home_goals": "INTEGER",
        "away_goals": "INTEGER",
        "result": "TEXT",
        "home_shots": "INTEGER",
        "away_shots": "INTEGER",
        "home_shots_on_target": "INTEGER",
        "away_shots_on_target": "INTEGER",
        "home_fouls": "INTEGER",
        "away_fouls": "INTEGER",
        "home_corners": "INTEGER",
        "away_corners": "INTEGER",
        "home_yellows": "INTEGER",
        "away_yellows": "INTEGER",
        "home_reds": "INTEGER",
        "away_reds": "INTEGER",
        "odds_home": "REAL",
        "odds_draw": "REAL",
        "odds_away": "REAL",
        "updated_at": "TEXT NOT NULL DEFAULT ''",
    }
    for col, col_def in schema_cols.items():
        if col not in existing:
            log.info("DB migration: adding missing column '%s' to matches", col)
            conn.execute(f"ALTER TABLE matches ADD COLUMN {col} {col_def}")

    # Step 4: now that all columns exist, create indexes safely
    conn.executescript(INDEX_SQL)
    return conn


def load_sql(df: pd.DataFrame, conn: sqlite3.Connection) -> tuple[int, int]:
    """Upsert rows. Returns (inserted, updated). Running twice changes nothing."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cols = DB_COLUMNS + ["updated_at"]
    updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "match_id")
    sql = (f"INSERT INTO matches ({', '.join(cols)}) "
           f"VALUES ({', '.join(':' + c for c in cols)}) "
           f"ON CONFLICT(match_id) DO UPDATE SET {updates}")
    records = [{**{k: _py(v) for k, v in row.items()}, "updated_at": now}
               for row in df.to_dict("records")]

    before = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    with conn:                      # one transaction: all rows commit or none do
        conn.executemany(sql, records)
    after = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    inserted = after - before
    return inserted, len(records) - inserted


def record_run(conn, started, seasons, rows, status, error=None) -> None:
    with conn:
        conn.execute(
            "INSERT INTO etl_runs (started_at, finished_at, seasons, rows_processed, status, error)"
            " VALUES (?,?,?,?,?,?)",
            (started, datetime.now(timezone.utc).isoformat(timespec="seconds"),
             ",".join(seasons), rows, status, error),
        )


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s")
    file_h = RotatingFileHandler(LOG_DIR / "etl.log", maxBytes=1_000_000, backupCount=3)
    console = logging.StreamHandler()
    for h in (file_h, console):
        h.setFormatter(fmt)
    log.setLevel(logging.INFO)
    log.handlers = [file_h, console]


def run(codes: list[str], current: str, force: bool = False,
        db_path: Path = DB_PATH) -> int:
    """Process each season independently. One bad season does not block the rest,
    but the final exit code still reports the failure."""
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    session = make_session()
    conn = sqlite3.connect(db_path)
    conn = init_db(conn, db_path=db_path)

    total, failures = 0, []
    for code in codes:
        try:
            raw_path = extract(code, session, is_current=(code == current), force=force)
            clean = transform(read_raw(raw_path), code)
            clean.to_csv(CLEAN_DIR / f"E0_{code}.csv", index=False)
            ins, upd = load_sql(clean, conn)
            total += len(clean)
            log.info("[%s] OK: %d rows (%d new, %d updated)", code, len(clean), ins, upd)
        except Exception as exc:    # noqa: BLE001 - we log it, then report via exit code
            failures.append(code)
            log.error("[%s] FAILED: %s: %s", code, type(exc).__name__, exc)

    status = "failed" if failures else "success"
    record_run(conn, started, codes, total, status,
               f"failed seasons: {failures}" if failures else None)
    conn.close()

    if failures:
        log.error("Pipeline finished WITH FAILURES in seasons %s", failures)
        return 1
    log.info("Pipeline finished successfully: %d rows processed", total)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PitchIQ Premier League ETL")
    parser.add_argument("--seasons", type=int, default=10, help="how many recent seasons (default 10)")
    parser.add_argument("--current-only", action="store_true", help="only the running season")
    parser.add_argument("--force", action="store_true", help="re-download cached seasons")
    args = parser.parse_args(argv)

    setup_logging()
    codes = season_codes(1 if args.current_only else args.seasons)
    return run(codes, current=season_code(current_season_start_year()), force=args.force)


if __name__ == "__main__":
    sys.exit(main())