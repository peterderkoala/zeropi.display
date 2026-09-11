"""Desktop: the Configuration store and the Tier schema (spec §3, §4).

A dedicated SQLite file at ``~/.config/zeropi-display/config.db`` — separate
from the usage archive (``usage.py``'s store) so a corrupt or version-gated
archive never takes Configuration down with it (spec §3.1). Key/value rows,
exactly like the Pi's ``meta`` table: the storage layer stays dumb and this
module's schema (``KEYS``) is the single authority on each key's type, range
and Tier.

``resolve()`` is the one function anything outside this module should call.
It resolves the full precedence chain — CLI flag, then environment
variable, then Configuration, then compiled default (spec §3.2) — exactly
once, into a frozen ``Configuration`` object. Callers (an entry point's
``main()``) are expected to call it once at startup and thread the result
into the parameters that already exist for it (spec §3.3); nothing in this
module reads Configuration lazily or caches it at import time.

No BLE, no panel, no ``~/.claude``: importable and fully testable against a
``tmp_path`` scratch file (spec §10.1).
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import gauge
import usage

CONFIG_USER_VERSION = 1

DEFAULT_CONFIG_DIR = Path("~/.config/zeropi-display").expanduser()
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "config.db"

# §3.4 — location only. Generation, rotation and revocation are out of
# scope (spec §12); nothing in this module reads or writes this file.
TOKEN_PATH = DEFAULT_CONFIG_DIR / "token"
TOKEN_MODE = 0o600

# Tier 3 invariants this schema's bounds are *derived from* (spec §4.3).
# Mirrored here, not imported: the Desktop and the Pi are different
# deployments (neither installs the other's code), so these two constants
# are kept in sync by hand, against `pi/receive.py`. A Tier 3 amendment
# there (as #55 made to GAUGE_EXPIRY_S) must be re-applied here too, or the
# derived bounds below silently go stale — exactly what §10.2's tests exist
# to catch.
REDRAW_FLOOR_S = 300  # pi/receive.py:REDRAW_FLOOR_S — ADR-0008
GAUGE_EXPIRY_S = 300  # pi/receive.py:GAUGE_EXPIRY_S — ADR-0009/0010, amended by #55

SCHEMA_SQL = """
CREATE TABLE config (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

_ADDRESS_RE = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")


class ConfigVersionError(RuntimeError):
    """Raised when the config store is missing/corrupt/version-mismatched
    in a way that means it must not be trusted (spec §3.5: fail closed)."""


class ConfigValueError(ValueError):
    """Raised when a value is the wrong type or out of range for its key
    (spec §3.5) — on write, before anything is stored; on read, for a
    *known* key whose stored value no longer parses or validates, which is
    a corrupt-store condition, not an unknown-key one (spec §3.6)."""


class UnknownKeyError(ValueError):
    """Raised on a write to a key not in ``KEYS`` (spec §3.6 — rejected on
    write, unlike an unknown key already in the store, which is tolerated
    on read)."""


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _check_range(name: str, value: float, lo: Optional[float], hi: Optional[float]) -> None:
    if lo is not None and value < lo:
        raise ConfigValueError(f"{name}={value!r} is below the floor {lo!r}")
    if hi is not None and value > hi:
        raise ConfigValueError(f"{name}={value!r} is above the ceiling {hi!r}")


# ---------------------------------------------------------------------------
# §4 The constant inventory — one Key per row, in table form.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Key:
    tier: int  # 1 or 2 — never 3 (§4.3: a Tier 3 invariant is never stored)
    kind: str  # "path" | "json" | "real" | "int" | "address"
    default: Any
    lo: Optional[float] = None
    hi: Optional[float] = None
    json_shape: Optional[type] = None  # dict or list, for kind == "json"

    def parse(self, raw: str) -> Any:
        """TEXT from the store -> a typed, range-checked Python value."""
        if self.kind == "path":
            if not raw:
                raise ConfigValueError("path value must not be empty")
            return Path(raw).expanduser()
        if self.kind == "json":
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ConfigValueError(f"invalid JSON: {exc}") from exc
            self._check_json_shape(value)
            return value
        if self.kind == "real":
            try:
                value = float(raw)
            except ValueError as exc:
                raise ConfigValueError(f"{raw!r} is not a real number") from exc
            _check_range("value", value, self.lo, self.hi)
            return value
        if self.kind == "int":
            try:
                value = int(raw)
            except ValueError as exc:
                raise ConfigValueError(f"{raw!r} is not an integer") from exc
            _check_range("value", value, self.lo, self.hi)
            return value
        if self.kind == "address":
            if raw == "":
                return None
            if not _ADDRESS_RE.match(raw):
                raise ConfigValueError(f"{raw!r} is not a colon-separated six-octet address")
            return raw
        raise AssertionError(f"unhandled kind {self.kind!r}")

    def _check_json_shape(self, value: Any) -> None:
        if self.json_shape is not None and not isinstance(value, self.json_shape):
            raise ConfigValueError(f"expected a JSON {self.json_shape.__name__}, got {type(value).__name__}")

    def validate(self, value: Any) -> None:
        """Type- and range-checks an already-typed Python value (spec
        §3.5 — used before a write, and equally by `parse` above after a
        read, so both paths reject the same things)."""
        if self.kind == "path":
            if not isinstance(value, (str, Path)) or not str(value):
                raise ConfigValueError("path value must be a non-empty string or Path")
        elif self.kind == "json":
            self._check_json_shape(value)
        elif self.kind == "real":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ConfigValueError(f"expected a real number, got {type(value).__name__}")
            _check_range("value", float(value), self.lo, self.hi)
        elif self.kind == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ConfigValueError(f"expected an integer, got {type(value).__name__}")
            _check_range("value", value, self.lo, self.hi)
        elif self.kind == "address":
            if value is not None and (not isinstance(value, str) or not _ADDRESS_RE.match(value)):
                raise ConfigValueError(f"{value!r} is not a colon-separated six-octet address, or null")
        else:
            raise AssertionError(f"unhandled kind {self.kind!r}")

    def serialize(self, value: Any) -> str:
        """A validated Python value -> TEXT for storage."""
        self.validate(value)
        if self.kind == "path":
            return str(Path(value).expanduser())
        if self.kind == "json":
            return json.dumps(value)
        if self.kind == "real":
            return repr(float(value))
        if self.kind == "int":
            return str(int(value))
        if self.kind == "address":
            return "" if value is None else value
        raise AssertionError(f"unhandled kind {self.kind!r}")


# --- §4.1 Tier 1 — deployment facts (10 keys, freely editable) -------------

KEYS: dict[str, Key] = {
    "paths.projects_root": Key(1, "path", usage.DEFAULT_PROJECTS_ROOT),
    "paths.store": Key(1, "path", usage.DEFAULT_STORE_PATH),
    "paths.rate_limits": Key(1, "path", gauge.DEFAULT_RATE_LIMITS_PATH),
    "paths.sessions_dir": Key(1, "path", gauge.DEFAULT_SESSIONS_DIR),
    "pricing.overlay": Key(1, "json", {}, json_shape=dict),
    "pricing.free_models": Key(1, "json", [], json_shape=list),
    "pricing.web_search_usd_per_request": Key(1, "real", 0.01, lo=0.0),
    "context_window.overlay": Key(1, "json", {}, json_shape=dict),
    "batch.scheduled_hour": Key(1, "int", 4, lo=0, hi=23),
    # §4.6 — the 18th key. Written only by `cli.py pair` (ticket 6); this
    # ticket only needs it to exist and validate.
    "pi.address": Key(1, "address", None),

    # --- §4.2 Tier 2 -- policy, with guardrails (8 keys) -------------------
    #
    # Ceilings/floors are expressions over the Tier 3 mirrors above, not
    # hand-copied literals (spec §4, §10.2) -- a `GAUGE_EXPIRY_S` amendment
    # here moves these automatically.
    "service.poll_interval_s": Key(2, "real", 30.0, lo=5.0, hi=GAUGE_EXPIRY_S / 2),
    "service.gauge_throttle_s": Key(2, "real", 120.0, lo=30.0, hi=GAUGE_EXPIRY_S / 2),
    "gauge.stale_threshold_s": Key(2, "int", 300, lo=60, hi=GAUGE_EXPIRY_S),
    "batch.catchup_threshold_s": Key(2, "int", 86400, lo=3600, hi=604800),
    "usage.window_days": Key(2, "int", usage.WINDOW_DAYS, lo=5, hi=30),
    "push.scan_timeout_s": Key(2, "real", 10.0, lo=1.0, hi=60.0),
    "push.ack_timeout_s": Key(2, "real", 10.0, lo=1.0, hi=60.0),
    "pi.idle_keepalive_s": Key(2, "int", 86400, lo=3600, hi=604800),
}

assert all(key.tier in (1, 2) for key in KEYS.values()), "Tier 3 is never a Configuration key (§4.3)"


# ---------------------------------------------------------------------------
# §3.1 The store itself
# ---------------------------------------------------------------------------


def open_config_store(path: Path) -> sqlite3.Connection:
    """Opens (creating if needed) the config store at `path`, WAL journal
    mode. Mirrors `usage.open_store`'s shape and its version-gate discipline
    (spec §3.1, §3.5): a missing file is created with an empty table (which
    reads back as every key's default, spec §3.5's "not an error"); a file
    that exists but is not a readable SQLite database, or whose
    `user_version` does not match, fails closed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row

    try:
        conn.execute("PRAGMA journal_mode=WAL")
        current_version = conn.execute("PRAGMA user_version").fetchone()[0]
    except sqlite3.DatabaseError as exc:
        conn.close()
        raise ConfigVersionError(f"config store at {path} is not a valid SQLite database: {exc}") from exc

    if is_new or current_version == 0:
        conn.executescript(SCHEMA_SQL)
        conn.execute(f"PRAGMA user_version = {CONFIG_USER_VERSION}")
        conn.commit()
        return conn

    if current_version != CONFIG_USER_VERSION:
        conn.close()
        raise ConfigVersionError(
            f"config store at {path} has user_version={current_version}, "
            f"expected {CONFIG_USER_VERSION}. Refusing to run."
        )

    return conn


def read_config(conn: sqlite3.Connection) -> dict[str, Any]:
    """Every known key currently held in the store, parsed and range-checked
    (spec §3.6). A row whose key is not in `KEYS` is warned about and
    skipped — tolerated, because a future version retiring a setting must
    not turn a routine upgrade into a hard failure on data a previous,
    equally legitimate version wrote itself."""
    values: dict[str, Any] = {}
    for row in conn.execute("SELECT key, value FROM config"):
        key_spec = KEYS.get(row["key"])
        if key_spec is None:
            warnings.warn(
                f"config: ignoring unknown key {row['key']!r} (from a newer or reverted version?)",
                stacklevel=2,
            )
            continue
        values[row["key"]] = key_spec.parse(row["value"])
    return values


def write_config_value(conn: sqlite3.Connection, name: str, value: Any) -> None:
    """Validates and stores one key (spec §3.5, §3.6). Rejected on an
    unknown key, or a value that fails to coerce or falls outside its
    range — in every rejection case, nothing is written."""
    key_spec = KEYS.get(name)
    if key_spec is None:
        raise UnknownKeyError(f"{name!r} is not a known Configuration key")
    text = key_spec.serialize(value)  # raises ConfigValueError; nothing written yet
    conn.execute(
        "INSERT INTO config (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (name, text, _now_iso()),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# §3.2 Precedence, and §3.3 resolving once into a frozen object
# ---------------------------------------------------------------------------


def resolve_config_path(cli_config_path: Optional[str] = None, env: Optional[dict] = None) -> Path:
    """The one bootstrap exception (spec §3.2): the config store's own
    location cannot live inside the config store."""
    env = os.environ if env is None else env
    if cli_config_path:
        return Path(cli_config_path).expanduser()
    env_path = env.get("ZEROPI_CONFIG")
    if env_path:
        return Path(env_path).expanduser()
    return DEFAULT_CONFIG_PATH


@dataclass(frozen=True)
class Configuration:
    """Every Tier 1/2 value, resolved once (spec §3.3). Attribute names are
    each key's dotted name with `.` replaced by `_`."""

    paths_projects_root: Path
    paths_store: Path
    paths_rate_limits: Path
    paths_sessions_dir: Path
    pricing_overlay: dict
    pricing_free_models: list
    pricing_web_search_usd_per_request: float
    context_window_overlay: dict
    batch_scheduled_hour: int
    pi_address: Optional[str]
    service_poll_interval_s: float
    service_gauge_throttle_s: float
    gauge_stale_threshold_s: int
    batch_catchup_threshold_s: int
    usage_window_days: int
    push_scan_timeout_s: float
    push_ack_timeout_s: float
    pi_idle_keepalive_s: int


def resolve(
    *,
    cli_config_path: Optional[str] = None,
    cli_store_path: Optional[str] = None,
    env: Optional[dict] = None,
) -> Configuration:
    """Resolves the full precedence chain once (spec §3.2, §3.3): CLI flag,
    then environment variable, then Configuration, then compiled default.

    Only `paths.store` currently has a CLI-flag/env layer above Configuration
    (the pre-existing `--store` / `ZEROPI_USAGE_STORE` escape, spec §3.2) —
    there is deliberately no general per-key environment mechanism.

    Call this once, at an entry point's `main()`, never lazily at a use site
    (spec §3.3): every 246-test-suite-safe module that reads Configuration
    values takes them as parameters instead of resolving this itself.
    """
    env = os.environ if env is None else env

    conn = open_config_store(resolve_config_path(cli_config_path, env))
    try:
        stored = read_config(conn)
    finally:
        conn.close()

    values: dict[str, Any] = {name: stored.get(name, key.default) for name, key in KEYS.items()}

    if cli_store_path:
        values["paths.store"] = Path(cli_store_path).expanduser()
    elif env.get("ZEROPI_USAGE_STORE"):
        values["paths.store"] = Path(env["ZEROPI_USAGE_STORE"]).expanduser()

    return Configuration(**{name.replace(".", "_"): value for name, value in values.items()})
