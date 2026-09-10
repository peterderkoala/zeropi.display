"""Desktop: the Claude Code usage reader and store (spec §4).

Parses the Claude Code JSONL logs under ``~/.claude/projects/<project-key>/**/*.jsonl``
(project key directories, recursed so sub-agent logs at
``<project-key>/<session-uuid>/subagents/*.jsonl`` attribute to the right
project), dedups and prices the usage entries found there, and maintains the
Desktop store — the SQLite archive of record at
``~/.local/share/zeropi-display/usage-archive.db`` by default. Readings (one
``(date, project_key, model)`` group) are aggregated from the store, never
re-derived from the logs (ADR-0005).

No BLE, no I/O beyond files: importable and testable with no radio and no
``~/.claude`` present (spec §1, §11.1). Every function that walks the real
filesystem takes its root path as an argument, defaulting to the real path.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Iterator

# ---------------------------------------------------------------------------
# §4.2 Pricing
# ---------------------------------------------------------------------------

# USD per million tokens. See docs/research/pricing-table.md.
PRICING: dict[str, dict[str, float]] = {
    "claude-opus-5":    {"in": 5.00, "out": 25.00, "w5m": 6.25, "w1h": 10.00, "read": 0.50},
    "claude-sonnet-5":  {"in": 2.00, "out": 10.00, "w5m": 2.50, "w1h":  4.00, "read": 0.20},
    "claude-haiku-4-5": {"in": 1.00, "out":  5.00, "w5m": 1.25, "w1h":  2.00, "read": 0.10},
}
WEB_SEARCH_USD_PER_REQUEST = 0.01     # $10 per 1,000 searches
FREE_MODELS = {"<synthetic>"}         # no API call happened: free, and *known*

DEFAULT_STORE_PATH = Path("~/.local/share/zeropi-display/usage-archive.db").expanduser()
DEFAULT_PROJECTS_ROOT = Path("~/.claude/projects").expanduser()

STORE_USER_VERSION = 1

# The seven calendar days a Batch covers, ending today, inclusive (§4.6).
WINDOW_DAYS = 7


def price_for(model: str | None) -> tuple[dict[str, float] | None, bool]:
    """Returns (rates_or_None, known). known=False drives cost_complete."""
    if model in FREE_MODELS:
        return None, True
    for prefix, rates in PRICING.items():
        if model and model.startswith(prefix):
            return rates, True
    return None, False


def normalise_model(model: str | None) -> str | None:
    """The model id normalised to the matched pricing-table prefix (§4.6).

    A date-suffixed id (``claude-haiku-4-5-20251001``) is stored as the
    matched prefix (``claude-haiku-4-5``). The raw id is returned unchanged
    when no prefix matches (including free models like ``<synthetic>``).
    """
    if model is None:
        return None
    for prefix in PRICING:
        if model.startswith(prefix):
            return prefix
    return model


def compute_cost(usage: dict[str, Any], model: str | None) -> tuple[float, bool]:
    """Cost in USD for one usage entry, and whether the model was known.

    Rules (spec §4.2):
    - prefix-matched pricing table, `<synthetic>` free and known
    - cache-write TTL split priced separately (never the summed
      `cache_creation_input_tokens`)
    - `thinking_tokens` is already inside `output_tokens` and is never added
    - web_search_requests billed flat per request; web_fetch is free
    - service_tier is ignored
    - an unmatched model id still counts tokens elsewhere; cost is 0.0 and
      the caller is told the model was unknown so it can set
      `cost_complete = False`
    """
    rates, known = price_for(model)
    if rates is None:
        return 0.0, known

    input_tokens = usage.get("input_tokens") or 0
    output_tokens = usage.get("output_tokens") or 0
    cache_creation = usage.get("cache_creation") or {}
    write_5m = cache_creation.get("ephemeral_5m_input_tokens") or 0
    write_1h = cache_creation.get("ephemeral_1h_input_tokens") or 0
    cache_read = usage.get("cache_read_input_tokens") or 0

    cost = (
        input_tokens * rates["in"]
        + output_tokens * rates["out"]
        + write_5m * rates["w5m"]
        + write_1h * rates["w1h"]
        + cache_read * rates["read"]
    ) / 1_000_000.0

    server_tool_use = usage.get("server_tool_use") or {}
    web_search_requests = server_tool_use.get("web_search_requests") or 0
    cost += web_search_requests * WEB_SEARCH_USD_PER_REQUEST

    return cost, known


# ---------------------------------------------------------------------------
# §4.3 Project identity and label derivation
# ---------------------------------------------------------------------------

def encode(path: str) -> str:
    """The lossy but verifiable Project Key encoding of an absolute path."""
    return re.sub(r"[^a-zA-Z0-9]", "-", path)


WORKTREE_MARKER = "/.claude/worktrees/"


@dataclass
class ProjectLabel:
    label: str
    rule: str          # "R1" | "R2" | "R3" | "R4"
    verified: bool


def derive_project_label(project_key: str, cwds: Iterable[str]) -> ProjectLabel:
    """The Project Label cascade R1-R4 (spec §4.3).

    ``cwds`` is every ``cwd`` seen anywhere under the project-key directory,
    including subagents/. Exposed for `--dry-run` only — never stored, never
    pushed (§4.3, §13).
    """
    cwds = list(cwds)

    # R1: any cwd encodes to the directory name directly.
    for cwd in cwds:
        if encode(cwd) == project_key:
            return ProjectLabel(label=Path(cwd).name, rule="R1", verified=True)

    # R2: truncate at a /.claude/worktrees/ marker.
    for cwd in cwds:
        idx = cwd.find(WORKTREE_MARKER)
        if idx == -1:
            continue
        root = cwd[:idx]
        if encode(root) == project_key:
            return ProjectLabel(label=Path(root).name, rule="R2", verified=True)

    # R3: walk each cwd's ancestors.
    for cwd in cwds:
        p = Path(cwd)
        for ancestor in [p, *p.parents]:
            if encode(str(ancestor)) == project_key:
                return ProjectLabel(label=ancestor.name, rule="R3", verified=True)

    # R4: strip the leading '-' and take the segment after the last '-'.
    stripped = project_key[1:] if project_key.startswith("-") else project_key
    label = stripped.rsplit("-", 1)[-1] if stripped else project_key
    return ProjectLabel(label=label, rule="R4", verified=False)


# ---------------------------------------------------------------------------
# §4.4 Date bucketing
# ---------------------------------------------------------------------------

def local_date(timestamp: str) -> str | None:
    """Local date on the Desktop, from an entry's ISO-8601 UTC `timestamp`.

    Returns None on an unparseable timestamp rather than raising — the
    reader must never hard-fail on a corpus it does not control (§4.4
    entries with no usable timestamp are skipped, not crashed on).
    """
    try:
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone().date().isoformat()
    except (ValueError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# §4.1 Selection, dedup key and winner rank
# ---------------------------------------------------------------------------

@dataclass
class UsageEntry:
    """One winning usage entry, ready for store ingest."""

    request_id: str
    message_id: str
    session_id: str | None
    project_key: str
    cwd: str | None
    local_date: str | None
    model: str
    input_tokens: int
    output_tokens: int
    cache_write_5m_tokens: int
    cache_write_1h_tokens: int
    cache_read_tokens: int
    web_search_requests: int
    cost_usd: float
    cost_complete: bool
    rank_sidechain: int   # 1 = not sidechain (preferred), 0 = sidechain
    rank_tokens: int
    rank_speed: int       # 1 = speed is non-null, 0 = null
    source_file: str
    source_end_offset: int


def _rank_key(raw: dict[str, Any]) -> tuple[int, int, int]:
    """The 3-step winner comparison (§4.1), larger is better."""
    usage = raw["message"]["usage"]
    rank_sidechain = 0 if raw.get("isSidechain") else 1
    rank_tokens = (
        (usage.get("input_tokens") or 0)
        + (usage.get("output_tokens") or 0)
        + (usage.get("cache_creation_input_tokens") or 0)
        + (usage.get("cache_read_input_tokens") or 0)
    )
    rank_speed = 1 if usage.get("speed") is not None else 0
    return (rank_sidechain, rank_tokens, rank_speed)


def iter_jsonl_lines(path: Path) -> Iterator[tuple[int, int, dict[str, Any]]]:
    """Yields (start_offset, end_offset, parsed_object) for each JSON line.

    Offsets are byte offsets into the file, used for the store's high-water
    mark (§4.5). Blank lines are skipped. A line that fails to parse as JSON
    is skipped rather than raising — the reader must never hard-fail on a
    corpus it does not control.
    """
    with path.open("rb") as fh:
        offset = 0
        for raw_line in fh:
            start = offset
            offset += len(raw_line)
            end = offset
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            yield start, end, obj


def read_usage_entries(
    project_key: str,
    project_dir: Path,
    min_offsets: dict[str, int] | None = None,
) -> list[UsageEntry]:
    """All winning usage entries for one project directory (§4.1).

    ``min_offsets`` maps an absolute source-file path to the byte offset
    already ingested (the store's high-water mark, §4.5) — lines ending at
    or before that offset are skipped. When omitted, every line is read.

    Selection: `type == "assistant"` entries carrying `message.usage`.
    Dedup key `(requestId, message.id)`; unkeyed entries are dropped.
    Sidechain entries are included. Within a duplicate group only the
    highest-ranking entry survives.
    """
    min_offsets = min_offsets or {}

    # candidates[key] -> (rank_key, UsageEntry)
    candidates: dict[tuple[str, str], tuple[tuple[int, int, int], UsageEntry]] = {}

    for jsonl_path in sorted(project_dir.glob("**/*.jsonl")):
        source_file = str(jsonl_path.resolve())
        floor = min_offsets.get(source_file, -1)
        for start, end, raw in iter_jsonl_lines(jsonl_path):
            if end <= floor:
                continue
            if raw.get("type") != "assistant":
                continue
            message = raw.get("message") or {}
            usage = message.get("usage")
            if not usage:
                continue

            request_id = raw.get("requestId")
            message_id = message.get("id")
            if not request_id or not message_id:
                continue

            # §4.4: entries with no (or unparseable) timestamp are skipped.
            timestamp = raw.get("timestamp")
            entry_date = local_date(timestamp) if timestamp else None
            if entry_date is None:
                continue

            # A usage entry with no model id has nothing to price or store
            # against the schema's NOT NULL `model` column — skip it rather
            # than crash the whole ingest on one bad line.
            model = message.get("model")
            if not model:
                continue

            key = (request_id, message_id)
            rank = _rank_key(raw)
            existing = candidates.get(key)
            if existing is not None and existing[0] >= rank:
                continue

            cost_usd, known = compute_cost(usage, model)
            normalised_model = normalise_model(model)

            cache_creation = usage.get("cache_creation") or {}
            server_tool_use = usage.get("server_tool_use") or {}

            entry = UsageEntry(
                request_id=request_id,
                message_id=message_id,
                session_id=raw.get("sessionId"),
                project_key=project_key,
                cwd=raw.get("cwd"),
                local_date=entry_date,
                model=normalised_model,
                input_tokens=usage.get("input_tokens") or 0,
                output_tokens=usage.get("output_tokens") or 0,
                cache_write_5m_tokens=cache_creation.get("ephemeral_5m_input_tokens") or 0,
                cache_write_1h_tokens=cache_creation.get("ephemeral_1h_input_tokens") or 0,
                cache_read_tokens=usage.get("cache_read_input_tokens") or 0,
                web_search_requests=server_tool_use.get("web_search_requests") or 0,
                cost_usd=cost_usd,
                cost_complete=known,
                rank_sidechain=rank[0],
                rank_tokens=rank[1],
                rank_speed=rank[2],
                source_file=source_file,
                source_end_offset=end,
            )
            candidates[key] = (rank, entry)

    return [entry for _rank, entry in candidates.values()]


def collect_cwds(project_dir: Path) -> list[str]:
    """Every `cwd` seen anywhere under a project directory (§4.3)."""
    cwds: list[str] = []
    for jsonl_path in sorted(project_dir.glob("**/*.jsonl")):
        for _start, _end, raw in iter_jsonl_lines(jsonl_path):
            cwd = raw.get("cwd")
            if cwd:
                cwds.append(cwd)
    return cwds


def discover_projects(root: Path = DEFAULT_PROJECTS_ROOT) -> dict[str, Path]:
    """Project Key -> project directory, for every project under `root`."""
    if not root.is_dir():
        return {}
    return {p.name: p for p in sorted(root.iterdir()) if p.is_dir()}


# ---------------------------------------------------------------------------
# §4.5 The Desktop store
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE entries (
    request_id             TEXT    NOT NULL,
    message_id             TEXT    NOT NULL,
    session_id              TEXT,
    project_key            TEXT    NOT NULL,
    local_date             TEXT    NOT NULL,
    model                  TEXT    NOT NULL,
    input_tokens           INTEGER NOT NULL,
    output_tokens          INTEGER NOT NULL,
    cache_write_5m_tokens  INTEGER NOT NULL,
    cache_write_1h_tokens  INTEGER NOT NULL,
    cache_read_tokens      INTEGER NOT NULL,
    web_search_requests    INTEGER NOT NULL DEFAULT 0,
    cost_usd               REAL    NOT NULL,
    cost_complete          INTEGER NOT NULL,
    rank_sidechain         INTEGER NOT NULL,
    rank_tokens            INTEGER NOT NULL,
    rank_speed             INTEGER NOT NULL,
    source_file            TEXT    NOT NULL,
    source_end_offset      INTEGER NOT NULL,
    pushed_at              TEXT,
    PRIMARY KEY (request_id, message_id)
);

CREATE INDEX entries_reading ON entries (local_date, project_key, model);
CREATE INDEX entries_source  ON entries (source_file, source_end_offset);
"""


class StoreVersionError(RuntimeError):
    """Raised when the store's `user_version` does not match what this code
    expects. The store is the archive of record: on a mismatch it refuses to
    run rather than dropping data (§4.5, the opposite of the Pi's gate)."""


def resolve_store_path(cli_path: str | None = None) -> Path:
    """Location resolution order (§4.5): --store flag, then env, then default."""
    if cli_path:
        return Path(cli_path).expanduser()
    env_path = os.environ.get("ZEROPI_USAGE_STORE")
    if env_path:
        return Path(env_path).expanduser()
    return DEFAULT_STORE_PATH


def open_store(path: Path) -> sqlite3.Connection:
    """Opens (creating if needed) the Desktop store at `path`.

    Raises StoreVersionError if an existing file's `user_version` does not
    match `STORE_USER_VERSION` — the store never drops and recreates.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row

    current_version = conn.execute("PRAGMA user_version").fetchone()[0]

    if is_new or current_version == 0:
        conn.executescript(SCHEMA_SQL)
        conn.execute(f"PRAGMA user_version = {STORE_USER_VERSION}")
        conn.commit()
        return conn

    if current_version != STORE_USER_VERSION:
        conn.close()
        raise StoreVersionError(
            f"usage store at {path} has user_version={current_version}, "
            f"expected {STORE_USER_VERSION}. Refusing to run — the store is "
            "the archive of record and is never dropped automatically. "
            "Restore from a backup or write a migration."
        )

    return conn


def high_water_marks(conn: sqlite3.Connection) -> dict[str, int]:
    """Per-source-file `MAX(source_end_offset)` (§4.5's resume point)."""
    rows = conn.execute(
        "SELECT source_file, MAX(source_end_offset) AS max_offset "
        "FROM entries GROUP BY source_file"
    ).fetchall()
    return {row["source_file"]: row["max_offset"] for row in rows}


_UPSERT_SQL = """
INSERT INTO entries (
    request_id, message_id, session_id, project_key, local_date,
    model, input_tokens, output_tokens, cache_write_5m_tokens,
    cache_write_1h_tokens, cache_read_tokens, web_search_requests,
    cost_usd, cost_complete, rank_sidechain, rank_tokens, rank_speed,
    source_file, source_end_offset, pushed_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
ON CONFLICT (request_id, message_id) DO UPDATE SET
    session_id = excluded.session_id,
    project_key = excluded.project_key,
    local_date = excluded.local_date,
    model = excluded.model,
    input_tokens = excluded.input_tokens,
    output_tokens = excluded.output_tokens,
    cache_write_5m_tokens = excluded.cache_write_5m_tokens,
    cache_write_1h_tokens = excluded.cache_write_1h_tokens,
    cache_read_tokens = excluded.cache_read_tokens,
    web_search_requests = excluded.web_search_requests,
    cost_usd = excluded.cost_usd,
    cost_complete = excluded.cost_complete,
    rank_sidechain = excluded.rank_sidechain,
    rank_tokens = excluded.rank_tokens,
    rank_speed = excluded.rank_speed,
    source_file = excluded.source_file,
    source_end_offset = excluded.source_end_offset,
    pushed_at = CASE
        WHEN entries.input_tokens != excluded.input_tokens
          OR entries.output_tokens != excluded.output_tokens
          OR entries.cache_write_5m_tokens != excluded.cache_write_5m_tokens
          OR entries.cache_write_1h_tokens != excluded.cache_write_1h_tokens
          OR entries.cache_read_tokens != excluded.cache_read_tokens
          OR entries.web_search_requests != excluded.web_search_requests
          OR entries.cost_usd != excluded.cost_usd
          OR entries.cost_complete != excluded.cost_complete
        THEN NULL
        ELSE entries.pushed_at
    END
WHERE (excluded.rank_sidechain, excluded.rank_tokens, excluded.rank_speed)
    > (entries.rank_sidechain, entries.rank_tokens, entries.rank_speed)
"""


def ingest_entries(conn: sqlite3.Connection, entries: Iterable[UsageEntry]) -> None:
    """Idempotent upsert on (request_id, message_id) (§4.5).

    A single INSERT ... ON CONFLICT DO UPDATE per entry: replaces the stored
    row only if the incoming entry outranks it (the WHERE guard on the
    DO UPDATE clause — SQLite skips the update entirely, leaving the
    existing row including its `pushed_at` untouched, whenever the incoming
    entry does not outrank the stored one; this also covers a brand-new
    row, which trivially "outranks" nothing stored). If the replacement
    changes any token/cost/cost_complete value, `pushed_at` is reset to
    NULL (the Reading is now stale on the Pi). If the winner is unchanged,
    `pushed_at` is left alone.
    """
    rows = [
        (
            entry.request_id, entry.message_id, entry.session_id,
            entry.project_key, entry.local_date, entry.model,
            entry.input_tokens, entry.output_tokens,
            entry.cache_write_5m_tokens, entry.cache_write_1h_tokens,
            entry.cache_read_tokens, entry.web_search_requests,
            entry.cost_usd, int(entry.cost_complete),
            entry.rank_sidechain, entry.rank_tokens, entry.rank_speed,
            entry.source_file, entry.source_end_offset,
        )
        for entry in entries
    ]
    if not rows:
        return
    conn.executemany(_UPSERT_SQL, rows)
    conn.commit()


def ingest_projects_root(
    conn: sqlite3.Connection,
    root: Path = DEFAULT_PROJECTS_ROOT,
) -> None:
    """Ingests every project under `root` into the store, resuming from each
    source file's high-water mark (§4.5)."""
    offsets = high_water_marks(conn)
    for project_key, project_dir in discover_projects(root).items():
        entries = read_usage_entries(project_key, project_dir, min_offsets=offsets)
        ingest_entries(conn, entries)


def clear_pushed_marks(conn: sqlite3.Connection) -> None:
    """`--resend-all`: clear every `pushed_at` so the whole Window is pushed."""
    conn.execute("UPDATE entries SET pushed_at = NULL")
    conn.commit()


def mark_pushed(conn: sqlite3.Connection, date_: str, project_key: str, model: str, pushed_at: str) -> None:
    """Marks every entry in one (date, project_key, model) group as pushed."""
    conn.execute(
        "UPDATE entries SET pushed_at = ? WHERE local_date = ? AND project_key = ? AND model = ?",
        (pushed_at, date_, project_key, model),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# §4.6 Aggregating Readings
# ---------------------------------------------------------------------------

@dataclass
class Reading:
    date: str
    project_key: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    cost_usd: float
    session_count: int
    cost_complete: bool
    pending: bool

    def is_all_zero(self) -> bool:
        return (
            self.input_tokens == 0
            and self.output_tokens == 0
            and self.cache_creation_tokens == 0
            and self.cache_read_tokens == 0
        )


def window_dates(today: date | None = None, days: int = WINDOW_DAYS) -> list[str]:
    """The seven calendar days a Batch covers, ending today, inclusive (§4.6)."""
    today = today or date.today()
    return [(today - timedelta(days=offset)).isoformat() for offset in range(days - 1, -1, -1)]


def aggregate_readings(
    conn: sqlite3.Connection,
    window: Iterable[str] | None = None,
) -> list[Reading]:
    """Readings aggregated from the store, grouped by (date, project_key, model).

    `window` is the set of local_date strings to include; defaults to the
    seven-calendar-day Window ending today (§4.6). All-zero rows are dropped.
    Ordered newest date first (ADR-0003).
    """
    window = list(window) if window is not None else window_dates()
    if not window:
        return []

    placeholders = ",".join("?" for _ in window)
    rows = conn.execute(
        f"""
        SELECT local_date, project_key, model,
               SUM(input_tokens) AS input_tokens,
               SUM(output_tokens) AS output_tokens,
               SUM(cache_write_5m_tokens + cache_write_1h_tokens) AS cache_creation_tokens,
               SUM(cache_read_tokens) AS cache_read_tokens,
               SUM(cost_usd) AS cost_usd,
               COUNT(DISTINCT session_id) AS session_count,
               MIN(cost_complete) AS cost_complete,
               SUM(CASE WHEN pushed_at IS NULL THEN 1 ELSE 0 END) AS unpushed_count
        FROM entries
        WHERE local_date IN ({placeholders})
        GROUP BY local_date, project_key, model
        ORDER BY local_date DESC
        """,
        window,
    ).fetchall()

    readings = []
    for row in rows:
        reading = Reading(
            date=row["local_date"],
            project_key=row["project_key"],
            model=row["model"],
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            cache_creation_tokens=row["cache_creation_tokens"],
            cache_read_tokens=row["cache_read_tokens"],
            cost_usd=round(row["cost_usd"], 4),
            session_count=row["session_count"],
            cost_complete=bool(row["cost_complete"]),
            pending=row["unpushed_count"] > 0,
        )
        if reading.is_all_zero():
            continue
        readings.append(reading)

    return readings


def pending_readings(conn: sqlite3.Connection, window: Iterable[str] | None = None) -> list[Reading]:
    """Readings within the Window that are pending (§4.6)."""
    return [r for r in aggregate_readings(conn, window) if r.pending]


# ---------------------------------------------------------------------------
# §6.1 Wire format
# ---------------------------------------------------------------------------

def reading_to_daily_payload(
    reading: Reading,
    desktop_id: str,
    batch_size: int,
    batch_index: int,
) -> dict[str, Any]:
    """The Daily Payload shape for one Reading (spec §6.1)."""
    return {
        "kind": "daily",
        "desktop_id": desktop_id,
        "batch_size": batch_size,
        "batch_index": batch_index,
        "date": reading.date,
        "project": reading.project_key,
        "model": reading.model,
        "input_tokens": reading.input_tokens,
        "output_tokens": reading.output_tokens,
        "cache_creation_tokens": reading.cache_creation_tokens,
        "cache_read_tokens": reading.cache_read_tokens,
        "cost_usd": reading.cost_usd,
        "session_count": reading.session_count,
        "cost_complete": reading.cost_complete,
    }
