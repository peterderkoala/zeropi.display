"""The Gauge reader (spec §5).

Reads claude-hud's rate-limit snapshot and the live session registry on the
Desktop, and turns them into a Gauge Payload (spec §6.2, minus `kind` and
`desktop_id`, which `push.py` attaches). No BLE, no other I/O — importable
and fully testable without a radio (spec §1).

Everything time-shaped is converted to a duration relative to `now` before
it leaves this module (spec §5.3, ADR-0009): nothing here hands back an
instant for a caller to interpret later against a *different* `now`.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

# --- Constants -------------------------------------------------------------

DEFAULT_RATE_LIMITS_PATH = Path.home() / ".local/state/zeropi-display/rate-limits.json"
DEFAULT_SESSIONS_DIR = Path.home() / ".claude/sessions"
DEFAULT_PROJECTS_ROOT = Path.home() / ".claude/projects"

# A Gauge snapshot this old at push time would arrive already expired on the
# Pi (spec §5.3, §8.4 — 300s is one redraw floor). Refuse to push it.
STALE_THRESHOLD_S = 300

# docs/research/context-window-table.md (branch research/context-window-table),
# reproduced at spec §5.2. Prefix-matched, same pattern as the pricing table.
CONTEXT_WINDOW = {
    "claude-opus-5": 1_000_000,
    "claude-sonnet-5": 1_000_000,
    "claude-haiku-4-5": 200_000,
}


def encode(path: str) -> str:
    """The Project Key encoding (spec §4.3), needed here to resolve the
    active session's own JSONL file under ~/.claude/projects/."""
    return re.sub(r"[^a-zA-Z0-9]", "-", path)


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _proc_pid_alive(pid: int) -> bool:
    return Path(f"/proc/{pid}").exists()


# --- §5.1 The rate-limit snapshot ------------------------------------------


def read_snapshot(path: Optional[Path] = None) -> Optional[dict]:
    """Reads claude-hud's rate-limit snapshot.

    Returns the parsed dict, or `None` if the file does not exist — spec
    §5.1's "never configured" row: this is the "no Gauge" case, and it is
    never reported as zero. A file that *exists* but carries null fields
    (closed Claude Code, headless-only, or both windows null) is read and
    returned normally; that is a different case from an absent file, and is
    handled by `compute_windows` producing null `pct`/`resets_in_s` values
    rather than by this function.
    """
    path = path or DEFAULT_RATE_LIMITS_PATH
    try:
        text = path.read_text()
    except OSError:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # A corrupt or mid-write snapshot (the writer's atomic temp+rename
        # should prevent this, but do not let a torn read crash the push
        # loop) degrades the same way an absent file does: "no Gauge".
        return None


def _window_from(window: dict, now: datetime) -> dict:
    pct = window.get("used_percentage")
    resets_at = window.get("resets_at")
    resets_in_s = None
    if resets_at is not None:
        resets_in_s = max(0, round((_parse_iso(resets_at) - now).total_seconds()))
    return {"pct": pct, "resets_in_s": resets_in_s}


def compute_windows(snapshot: dict, now: datetime) -> tuple[int, dict, dict]:
    """Converts a raw snapshot into (snapshot_age_s, five_hour, seven_day),
    all durations relative to `now` (spec §5.3). `pct: null` survives
    untouched — it is a real, observed state (spec §6.2), never coerced to
    zero.
    """
    updated_at = _parse_iso(snapshot["updated_at"])
    snapshot_age_s = max(0, round((now - updated_at).total_seconds()))
    five_hour = _window_from(snapshot["five_hour"], now)
    seven_day = _window_from(snapshot["seven_day"], now)
    return snapshot_age_s, five_hour, seven_day


# --- §5.2 The active session and the context size ---------------------------


def find_active_session(
    sessions_dir: Optional[Path] = None,
    pid_alive: Optional[Callable[[int], bool]] = None,
) -> Optional[dict]:
    """Picks the active session from the live session registry.

    Liveness is `/proc/<pid>` only — never `updatedAt`, which is a
    status-transition timestamp, not a heartbeat (spec §5.2). `pid_alive` is
    injectable so tests can simulate a live pid without a real matching
    process; it defaults to a real `/proc` check.

    Filters to `kind == "interactive"`; among survivors, the active session
    is the one with the most recent `updatedAt`. Returns `None` if no
    interactive, live session exists.
    """
    sessions_dir = sessions_dir or DEFAULT_SESSIONS_DIR
    pid_alive = pid_alive or _proc_pid_alive

    candidates = []
    for entry_path in sorted(sessions_dir.glob("*.json")):
        if entry_path.name.startswith("."):
            continue
        try:
            data = json.loads(entry_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("kind") != "interactive":
            continue
        pid = data.get("pid")
        if pid is None or not pid_alive(pid):
            continue
        candidates.append(data)

    if not candidates:
        return None
    candidates.sort(key=lambda d: d.get("updatedAt") or "", reverse=True)
    return candidates[0]


def context_pct(model: Optional[str], tokens: int) -> Optional[int]:
    """Percentage of `tokens` against the hardcoded context-window table,
    prefix-matched. `None` for an unrecognised model — `tokens` is still
    reported by the caller regardless.
    """
    if not model:
        return None
    for prefix, window in CONTEXT_WINDOW.items():
        if model.startswith(prefix):
            return round(100 * tokens / window)
    return None


def read_context_size(
    session: Optional[dict], projects_root: Optional[Path] = None
) -> Optional[dict]:
    """Computes the active session's context size (spec §5.2).

    `session` is a registry entry as returned by `find_active_session` (or
    `None`, meaning no live session — this returns `None` too). Reads the
    session's own JSONL under ~/.claude/projects/<encode(cwd)>/<sessionId>.jsonl
    and takes the LATEST NON-SIDECHAIN `type: "assistant"` entry — the
    opposite rule from usage.py's dedup, which counts sidechain entries.
    """
    if session is None:
        return None
    cwd = session.get("cwd")
    session_id = session.get("sessionId")
    if not cwd or not session_id:
        return None

    projects_root = projects_root or DEFAULT_PROJECTS_ROOT
    jsonl_path = projects_root / encode(cwd) / f"{session_id}.jsonl"
    try:
        text = jsonl_path.read_text()
    except OSError:
        return None

    latest = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("type") != "assistant":
            continue
        if entry.get("isSidechain"):
            continue
        usage = (entry.get("message") or {}).get("usage")
        if not usage:
            continue
        latest = entry

    if latest is None:
        return None

    usage = latest["message"]["usage"]
    tokens = (
        usage.get("input_tokens", 0)
        + usage.get("cache_creation_input_tokens", 0)
        + usage.get("cache_read_input_tokens", 0)
    )
    model = latest["message"].get("model")
    return {"tokens": tokens, "pct": context_pct(model, tokens), "model": model}


# --- Top-level: build a Gauge Payload ---------------------------------------


def build_gauge_payload(
    now: Optional[datetime] = None,
    rate_limits_path: Optional[Path] = None,
    sessions_dir: Optional[Path] = None,
    projects_root: Optional[Path] = None,
    pid_alive: Optional[Callable[[int], bool]] = None,
) -> Optional[dict]:
    """Builds a Gauge Payload (spec §6.2, minus `kind`/`desktop_id`).

    Returns `None` in exactly two cases, and the caller (`push.py`) must
    treat both alike: skip the push, there is nothing to send this cycle.

    1. No snapshot file at all (spec §5.1's "no Gauge" — never reported as
       a zero-percentage Payload).
    2. A snapshot that is already `>= STALE_THRESHOLD_S` old at push time
       (spec §5.3) — pushing it would arrive already expired on the Pi, so
       the push is skipped; the next change pushes a better value.

    A snapshot that exists and is fresh enough, but has null fields inside
    it (closed Claude Code, headless-only, or both windows null), is NOT one
    of these two cases — it produces a real Payload with `pct: null`, which
    the Pi renders as its own distinct "NO USAGE DATA" state (spec §9.2).
    """
    now = now or datetime.now(timezone.utc)

    snapshot = read_snapshot(rate_limits_path)
    if snapshot is None:
        return None

    snapshot_age_s, five_hour, seven_day = compute_windows(snapshot, now)
    if snapshot_age_s >= STALE_THRESHOLD_S:
        return None

    session = find_active_session(sessions_dir, pid_alive)
    context = read_context_size(session, projects_root)

    return {
        "snapshot_age_s": snapshot_age_s,
        "five_hour": five_hour,
        "seven_day": seven_day,
        "context": context,
    }
