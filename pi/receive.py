"""Pi (BLE peripheral): dumb receiver for the Payload GATT service.

Advertises the GATT service, accepts a single-write Payload (Daily or
Gauge shape), and returns a JSON Ack over the notify characteristic. A
Daily Payload is persisted as a Reading in SQLite; a Gauge Payload is
held in memory only and never written to SQLite. See
../docs/spec-usage-pipeline.md §6 and §8 for the design this implements,
and ../CONTEXT.md for the vocabulary.

The BLE stack (`bluezero`) is only available on the Pi itself. Every
function/class in this module that does not need it is importable and
usable on a plain dev machine with no `bluezero` installed — the BLE
imports live inside the functions/classes that actually touch the radio
(`main()`, `ReceiveState.send_ack`), not at module scope. See
tests/test_receive_importable.py.
"""

import json
import sqlite3
import time
from typing import Callable, Optional

# Aliased: this module's own render(view) function -- the well-known seam
# RedrawGate calls -- would otherwise shadow the module of the same name.
import render as render_module

SERVICE_UUID = "abbac370-5a95-490d-a1fc-921c1c95300d"
WRITE_CHARACTERISTIC_UUID = "014ca0e2-c76c-4443-a755-e5a1ad25368d"
NOTIFY_CHARACTERISTIC_UUID = "08c89458-52f1-47eb-ab58-f7f7995d8efb"

# The store's configurable path (spec §4.5) is the deliberate exception in
# this codebase; the Pi's DB_PATH stays a hardcoded constant, owned by
# pi/install-pi.sh (spec §4.4) -- unlike GAUGE_EXPIRY_S/REDRAW_FLOOR_S
# (Tier 3, spec §4.3) and IDLE_KEEPALIVE_S (a Setting as of spec §6, applied
# live -- see RedrawGate below), which are not "every constant in this file"
# any more.
DB_PATH = "/opt/zeropi-display/data.db"

SCHEMA_VERSION = 1

# The redraw floor (spec §8.5): the Pi's hard gate on physical panel
# updates, enforced regardless of what the Desktop's own throttle does.
REDRAW_FLOOR_S = 300

# Gauge Age expiry (spec §8.4/§8.9, ADR-0009/0010): one redraw floor.
# Must stay comfortably above the Desktop's GAUGE_THROTTLE_S
# (desktop/service.py), roughly expiry >= 2 x throttle, or a replacement
# Gauge arrives after the on-screen one has already expired
# (docs/spec-eink-rendering.md §9).
GAUGE_EXPIRY_S = 300

# Idle keep-alive full refresh (spec §8.5, ADR-0007/0010): the code
# default, run before the Pi has ever been told a Setting (spec §6.3). Not
# Configuration -- the same line drawn for a Tier 3 constant.
IDLE_KEEPALIVE_S = 24 * 60 * 60

# Settings persist in `meta` under this prefix (spec §6.2) -- load-bearing,
# because `setting.*` keys now share a table with `desktop_id` and
# `coverage_start`.
SETTING_KEY_PREFIX = "setting."

DAILY_REQUIRED_FIELDS = (
    "date",
    "project",
    "model",
    "input_tokens",
    "output_tokens",
    "cache_creation_tokens",
    "cache_read_tokens",
    "cost_usd",
    "session_count",
    "cost_complete",
    "batch_size",
    "batch_index",
)

GAUGE_REQUIRED_FIELDS = ("snapshot_age_s", "five_hour", "seven_day")


# ---------------------------------------------------------------------------
# Schema (spec §8.1) — self-healing, version-gated. init_db() is the sole
# owner of the schema; a fresh Pi and an already-provisioned one take the
# identical path, since both read PRAGMA user_version = 0 on mismatch.
# ---------------------------------------------------------------------------


_CREATE_READINGS_SQL = """
    CREATE TABLE readings (
        date                    TEXT    NOT NULL,
        project                 TEXT    NOT NULL,
        model                   TEXT    NOT NULL,
        input_tokens            INTEGER NOT NULL,
        output_tokens           INTEGER NOT NULL,
        cache_creation_tokens   INTEGER NOT NULL,
        cache_read_tokens       INTEGER NOT NULL,
        cost_usd                REAL    NOT NULL,
        session_count           INTEGER NOT NULL,
        cost_complete           INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY (date, project, model)
    )
"""

_CREATE_META_SQL = """
    CREATE TABLE meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
"""


def init_db(db_path: str = DB_PATH) -> None:
    conn = sqlite3.connect(db_path)
    try:
        current_version = conn.execute("PRAGMA user_version").fetchone()[0]
        if current_version != SCHEMA_VERSION:
            conn.execute("DROP TABLE IF EXISTS readings")
            conn.execute("DROP TABLE IF EXISTS meta")
            conn.execute(_CREATE_READINGS_SQL)
            conn.execute(_CREATE_META_SQL)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.commit()
    finally:
        conn.close()


def _wipe_readings(conn: sqlite3.Connection) -> None:
    """Drop and recreate `readings`, delete the stored Coverage Start, and
    clear every Setting back to its code default (spec §6.3).

    Used by the Desktop Id wipe (spec §8.3). Does not touch
    `meta['desktop_id']` — the caller decides what to do with that.

    Settings are the *previous* Desktop's policy (spec §6.3): leaving them
    would have a re-coupled Pi run a stranger's keepalive until the new
    Desktop happened to push its own, with nothing prompting it to.
    """
    conn.execute("DROP TABLE IF EXISTS readings")
    conn.execute(_CREATE_READINGS_SQL)
    conn.execute("DELETE FROM meta WHERE key = 'coverage_start'")
    conn.execute("DELETE FROM meta WHERE key LIKE ?", (SETTING_KEY_PREFIX + "%",))


# ---------------------------------------------------------------------------
# Desktop Id wipe (spec §8.3)
# ---------------------------------------------------------------------------


def _get_meta(conn: sqlite3.Connection, key: str):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


# ---------------------------------------------------------------------------
# Settings (spec §6) -- the Pi's configuration seam. Real work, not a rename:
# before this the Pi had no seam at all, and `IDLE_KEEPALIVE_S` was read as a
# module global from inside a method (RedrawGate, below).
# ---------------------------------------------------------------------------

# The only Setting today (spec §4.2's `pi.idle_keepalive_s`). Adding a
# second Setting means adding a row here, not inventing a new mechanism.
_SETTING_COERCERS: dict[str, Callable[[object], object]] = {
    "idle_keepalive_s": int,
}


class UnknownSettingError(ValueError):
    """Raised by `apply_settings` for a key not in `_SETTING_COERCERS`.
    Ticket 3's `settings`-kind dispatch turns this into an Ack `reason`
    (spec §5.1); this module raises it before anything is written."""


class SettingValueError(ValueError):
    """Raised by `apply_settings` when a known Setting's value fails to
    coerce (e.g. `idle_keepalive_s: "abc"`) — before anything is written,
    same as `UnknownSettingError`."""


def get_setting(conn: sqlite3.Connection, name: str, default):
    """The current value of Setting `name` — from `meta`, coerced, or
    `default` if it has never been set (spec §6.3's code default)."""
    raw = _get_meta(conn, SETTING_KEY_PREFIX + name)
    if raw is None:
        return default
    return _SETTING_COERCERS[name](raw)


def apply_settings(conn: sqlite3.Connection, settings: dict) -> None:
    """Validates and persists every key in `settings` (spec §6.2).

    Rejects on the first unrecognised key or uncoercible value, before
    anything is written — the same all-or-nothing discipline as the
    Desktop's Configuration store (desktop/config.py's
    `write_config_value`): every value is coerced up front, into a
    separate dict, so a later key's bad value can never leave an earlier
    key's write sitting in the transaction. Does not commit; the caller
    owns the transaction (spec §6.1's live-apply and §6.2's persistence are
    the same write).
    """
    coerced = {}
    for name, value in settings.items():
        if name not in _SETTING_COERCERS:
            raise UnknownSettingError(f"{name!r} is not a known Setting")
        try:
            coerced[name] = _SETTING_COERCERS[name](value)
        except (TypeError, ValueError) as exc:
            raise SettingValueError(f"{name}={value!r} is not a valid value: {exc}") from exc
    for name, value in coerced.items():
        _set_meta(conn, SETTING_KEY_PREFIX + name, str(value))


def check_desktop_id(conn: sqlite3.Connection, desktop_id: str) -> bool:
    """Run the Desktop Id wipe check (spec §8.3). Returns True iff this
    Payload triggered a wipe (so its Ack, and only its Ack, gets
    `wiped: true`).

    Must run AFTER payload validation — a malformed Payload never
    destroys Readings.
    """
    stored_id = _get_meta(conn, "desktop_id")
    if stored_id is None:
        # Absent stored id -> adopt, no wipe (the DROP would be a no-op).
        _set_meta(conn, "desktop_id", desktop_id)
        return False
    if stored_id == desktop_id:
        return False
    # Stored id differs -> drop and recreate readings, delete
    # coverage_start, store the new id, flag this Ack as the wipe Ack.
    _wipe_readings(conn)
    _set_meta(conn, "desktop_id", desktop_id)
    return True


# ---------------------------------------------------------------------------
# Upsert + Coverage Start (spec §8.2)
# ---------------------------------------------------------------------------


def upsert_reading(conn: sqlite3.Connection, payload: dict) -> None:
    """Upsert one Reading and advance Coverage Start, in the caller's
    transaction. Last-write-wins on conflict.

    Coverage Start = MIN(current, incoming date), treating an absent
    current value as +infinity. Deliberately NOT derived as
    `SELECT MIN(date) FROM readings` (spec §8.2 explicitly rejects this).
    """
    conn.execute(
        """
        INSERT INTO readings (
            date, project, model,
            input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens,
            cost_usd, session_count, cost_complete
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(date, project, model) DO UPDATE SET
            input_tokens = excluded.input_tokens,
            output_tokens = excluded.output_tokens,
            cache_creation_tokens = excluded.cache_creation_tokens,
            cache_read_tokens = excluded.cache_read_tokens,
            cost_usd = excluded.cost_usd,
            session_count = excluded.session_count,
            cost_complete = excluded.cost_complete
        """,
        (
            payload["date"],
            payload["project"],
            payload["model"],
            payload["input_tokens"],
            payload["output_tokens"],
            payload["cache_creation_tokens"],
            payload["cache_read_tokens"],
            payload["cost_usd"],
            payload["session_count"],
            1 if payload["cost_complete"] else 0,
        ),
    )
    current = _get_meta(conn, "coverage_start")
    incoming = payload["date"]
    if current is None or incoming < current:
        _set_meta(conn, "coverage_start", incoming)


# ---------------------------------------------------------------------------
# Payload validation (spec §6.4)
# ---------------------------------------------------------------------------


class PayloadError(ValueError):
    """Raised by parse_payload() on any validation failure. `.kind` carries
    the Payload's `kind` field when it parsed far enough to have one, else
    None (so the Ack can omit `kind` per spec §6.3)."""

    def __init__(self, reason: str, kind=None):
        super().__init__(reason)
        self.reason = reason
        self.kind = kind


def _check_type(value, expected_types) -> bool:
    if expected_types is bool:
        return isinstance(value, bool)
    if expected_types is int:
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_types is float:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_types is str:
        return isinstance(value, str)
    return True


_DAILY_FIELD_TYPES = {
    "date": str,
    "project": str,
    "model": str,
    "input_tokens": int,
    "output_tokens": int,
    "cache_creation_tokens": int,
    "cache_read_tokens": int,
    "cost_usd": float,
    "session_count": int,
    "cost_complete": bool,
    "batch_size": int,
    "batch_index": int,
}


def parse_payload(raw_value) -> dict:
    """Validate and return a Payload dict, per spec §6.4. Raises
    PayloadError on any failure; persists nothing itself.
    """
    try:
        text = bytes(raw_value).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PayloadError(f"invalid payload: {exc}") from exc

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PayloadError(f"invalid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise PayloadError(f"expected a JSON object, got {type(payload).__name__}")

    kind = payload.get("kind")
    if kind not in ("daily", "gauge"):
        raise PayloadError(f"unknown kind: {kind!r}")

    if "desktop_id" not in payload or not isinstance(payload["desktop_id"], str):
        raise PayloadError("missing field(s): desktop_id", kind=kind)

    if kind == "daily":
        missing = [f for f in DAILY_REQUIRED_FIELDS if f not in payload]
        if missing:
            raise PayloadError(f"missing field(s): {', '.join(missing)}", kind=kind)
        bad_type = [
            f for f in DAILY_REQUIRED_FIELDS if not _check_type(payload[f], _DAILY_FIELD_TYPES[f])
        ]
        if bad_type:
            raise PayloadError(f"wrong type for field(s): {', '.join(bad_type)}", kind=kind)
    else:  # gauge
        missing = [f for f in GAUGE_REQUIRED_FIELDS if f not in payload]
        if missing:
            raise PayloadError(f"missing field(s): {', '.join(missing)}", kind=kind)
        if not _check_type(payload["snapshot_age_s"], int):
            raise PayloadError("wrong type for field(s): snapshot_age_s", kind=kind)
        for window_key in ("five_hour", "seven_day"):
            window = payload[window_key]
            if not isinstance(window, dict) or "pct" not in window or "resets_in_s" not in window:
                raise PayloadError(
                    f"missing field(s): {window_key}.pct or {window_key}.resets_in_s",
                    kind=kind,
                )

    return payload


# ---------------------------------------------------------------------------
# Ack (spec §6.3)
# ---------------------------------------------------------------------------


def build_ack(status: str, kind=None, date=None, project=None, model=None,
              drawn: bool = False, wiped: bool = False, reason: str = None) -> dict:
    ack = {"status": status}
    if kind is not None:
        ack["kind"] = kind
    if date is not None:
        ack["date"] = date
    if project is not None:
        ack["project"] = project
    if model is not None:
        ack["model"] = model
    # `drawn` means "the redraw floor accepted this for drawing", NOT
    # "pixels moved" -- the refresh runs on a worker thread and outlives
    # the Ack (spec §8). Its only consumer is a human reading a log to see
    # whether the floor coalesced; it is not a promise the panel updated.
    ack["drawn"] = drawn
    ack["wiped"] = wiped
    if reason is not None:
        ack["reason"] = reason
    return ack


# ---------------------------------------------------------------------------
# The Gauge state machine (spec §8.4) — held in memory only, never written
# to SQLite. Everything time-shaped runs on time.monotonic().
# ---------------------------------------------------------------------------


class GaugeState:
    def __init__(self):
        self.five_hour = None
        self.seven_day = None
        self.context = None
        self.arrival_mark = None
        self._snapshot_age_s = None

    def update(self, payload: dict, now: float = None) -> None:
        now = time.monotonic() if now is None else now
        self.five_hour = payload["five_hour"]
        self.seven_day = payload["seven_day"]
        self.context = payload.get("context")
        self._snapshot_age_s = payload["snapshot_age_s"]
        self.arrival_mark = now

    def is_live(self) -> bool:
        return self.arrival_mark is not None

    def gauge_age_s(self, now: float = None) -> float:
        now = time.monotonic() if now is None else now
        return self._snapshot_age_s + (now - self.arrival_mark)

    def is_expired(self, now: float = None) -> bool:
        if not self.is_live():
            return True
        return self.gauge_age_s(now) >= GAUGE_EXPIRY_S

    def _countdown(self, resets_in_s, now: float = None):
        if resets_in_s is None:
            return None
        now = time.monotonic() if now is None else now
        return max(0, round(resets_in_s - (now - self.arrival_mark)))

    def view(self, now: float = None) -> dict:
        """Build the Gauge frame's view data (for render()) at `now`."""
        now = time.monotonic() if now is None else now
        return {
            "five_hour": {
                "pct": self.five_hour["pct"],
                "resets_in_s": self._countdown(self.five_hour["resets_in_s"], now),
            },
            "seven_day": {
                "pct": self.seven_day["pct"],
                "resets_in_s": self._countdown(self.seven_day["resets_in_s"], now),
            },
            "context": self.context,
            "gauge_age_s": self.gauge_age_s(now),
        }


# ---------------------------------------------------------------------------
# The redraw floor (spec §8.5) — a hard gate the Pi enforces itself.
# ---------------------------------------------------------------------------


class RedrawGate:
    """Tracks when the panel last physically moved and decides whether a
    new redraw is allowed right now, or must be coalesced (spec §8.5).

    `historic_pending` tracks ONLY whether there is unshown Historic View
    data (a Reading upsert while a live Gauge was on screen) — it must not
    be touched by a Gauge-frame draw, or a live Gauge's countdown
    animating on the floor would silently clear a Historic redraw that was
    never actually drawn.
    """

    def __init__(self, idle_keepalive_s: Optional[Callable[[], float]] = None):
        self.last_drawn_at = None  # monotonic seconds, None = never drawn
        self.historic_pending = False
        # A looked-up value (spec §6.1), not a value captured once at
        # construction -- called fresh on every check, so a Setting written
        # mid-run is picked up on the very next redraw check with nothing
        # cached to go stale. Defaults to the code default (spec §6.3),
        # which is what every existing bare `RedrawGate()` call gets.
        self._idle_keepalive_s = idle_keepalive_s or (lambda: IDLE_KEEPALIVE_S)

    def mark_historic_pending(self) -> None:
        self.historic_pending = True

    def _floor_elapsed(self, now: float) -> bool:
        return self.last_drawn_at is None or (now - self.last_drawn_at) >= REDRAW_FLOOR_S

    def _idle_elapsed(self, now: float) -> bool:
        return self.last_drawn_at is not None and (now - self.last_drawn_at) >= self._idle_keepalive_s()

    def _draw(self, view: dict, now: float) -> None:
        render(view)
        self.last_drawn_at = now

    def try_draw_gauge(self, view: dict, now: float = None) -> bool:
        """Attempt to draw a live Gauge frame right now — on arrival of a
        Gauge Payload, or the countdown animating on the Pi's own clock.
        Returns True if drawn, False if coalesced by the floor. Does NOT
        touch `historic_pending`.
        """
        now = time.monotonic() if now is None else now
        if self._floor_elapsed(now):
            self._draw(view, now)
            return True
        return False

    def try_draw_historic_now(self, view: dict, now: float = None) -> bool:
        """A Reading just arrived and no live Gauge is showing: draw the
        Historic View now if the floor allows, else coalesce (mark
        pending for the next allowed moment).
        """
        now = time.monotonic() if now is None else now
        if self._floor_elapsed(now):
            self._draw(view, now)
            self.historic_pending = False
            return True
        self.historic_pending = True
        return False

    def try_draw_historic_if_due(self, view: dict, now: float = None) -> bool:
        """The Pi's-own-clock path for the Historic View: only actually
        draws when there is a reason to — a pending Reading update, or the
        24h idle keep-alive is due. Otherwise a Historic redraw would fire
        every floor interval even with nothing new to show.
        """
        now = time.monotonic() if now is None else now
        if not self._floor_elapsed(now):
            return False
        if self.historic_pending or self._idle_elapsed(now):
            self._draw(view, now)
            self.historic_pending = False
            return True
        return False


# ---------------------------------------------------------------------------
# render() — the hand-off to the render worker (spec §7, §8). ReceiveState's
# `_panel_worker` is set once by main(); stays None under test and before
# main() has run, and render() is then a safe no-op -- receive.py and its DB
# functions must stay usable with no panel, no SPI and no bluezero (§7).
# ---------------------------------------------------------------------------


def _build_frame(view: dict):
    """Turn a RedrawGate view into a concrete frame (spec §5). The Historic
    View's data comes straight from the readings table via render.py's data
    layer -- computed on demand at redraw, no cache (spec §6).
    """
    if view.get("historic"):
        conn = sqlite3.connect(ReceiveState.db_path)
        try:
            rows = render_module.historic_rows(conn)
            if not rows:
                return render_module.empty_frame()
            return render_module.historic_frame(
                rows, render_module.coverage_start(conn), render_module.historic_average(conn)
            )
        finally:
            conn.close()

    five_hour = view["five_hour"]
    seven_day = view["seven_day"]
    # Either window's pct can independently be null (parse_payload allows
    # it on each field separately) -- checking only five_hour would draw a
    # literal "None%" for seven_day if they ever diverge.
    if five_hour["pct"] is None or seven_day["pct"] is None:
        return render_module.no_usage_data_frame()
    return render_module.gauge_frame(
        five_hour["pct"], five_hour["resets_in_s"], seven_day["pct"], seven_day["resets_in_s"]
    )


def render(view: dict) -> None:
    """Hand off to the render worker (spec §8). Returns immediately
    regardless of whether the panel is present, healthy, or even
    configured -- the refresh runs on its own thread and outlives this
    call, same as the Ack it precedes.

    `_build_frame` runs here, inline, not on the worker thread -- it is
    PIL drawing, not the slow panel I/O the worker exists to keep off the
    write handler. But it still runs on the caller's thread (the BLE event
    loop, or main() at startup), so a failure in it (a missing font, spec
    §3; a corrupt DB read) must not escape and crash the link: receiving,
    persisting and Acking never depend on the display (spec §8).

    ⚠ A build failure on a Historic redraw is NOT re-queued: `try_draw_*`
    already reports this redraw as accepted and clears `historic_pending`
    right after calling this (RedrawGate's own contract, out of scope to
    change here). A failed build therefore self-heals only on the next
    Reading, or the 24h idle keep-alive -- not immediately. In practice
    this path is deterministic (a missing font, present or not; a DB file
    already proven writable moments earlier in the same request) rather
    than transient, so this is an accepted, bounded gap, not a silent one:
    it is logged every time it happens.
    """
    if ReceiveState._panel_worker is None:
        return
    try:
        image = _build_frame(view)
    except Exception as exc:
        print(f"Frame build failed, panel unaffected: {exc}")
        return
    ReceiveState._panel_worker.submit(image)
    # The one line every verification run has grepped for since §8.6's stub
    # (docs/usage-pipeline-verification.md) -- keep emitting it here now
    # that it means a frame was actually accepted and handed to the worker.
    print(f"render: {view}")


def _draw_startup_frame() -> None:
    """The panel's first frame after process start (spec §9, gap check
    §14.4): the Historic View, drawn unconditionally -- `last_drawn_at` is
    still None so the floor trivially allows it. Without this, a reboot
    leaves whatever the panel held before the power cut on screen,
    arbitrarily stale and undetectable, since `monotonic()` reset with it.

    Named rather than inlined in main(): a future boot splash is then a
    one-line change here, not a rewrite of main() or the redraw floor.
    """
    ReceiveState.redraw_gate.try_draw_historic_now({"historic": True})


# ---------------------------------------------------------------------------
# BLE plumbing. Everything below this line touches bluezero, so the import
# lives here rather than at module scope (see tests/test_receive_importable.py).
# ---------------------------------------------------------------------------


def _live_idle_keepalive_s() -> float:
    """The looked-up value `RedrawGate._idle_elapsed` reads in production
    (spec §6.1) -- opened fresh on every call, against `ReceiveState.db_path`
    at call time, rather than cached: a Setting written mid-run must be
    picked up on the very next redraw check with nothing to go stale."""
    conn = sqlite3.connect(ReceiveState.db_path)
    try:
        return get_setting(conn, "idle_keepalive_s", IDLE_KEEPALIVE_S)
    finally:
        conn.close()


class ReceiveState:
    ack_characteristic = None
    gauge = GaugeState()
    redraw_gate = RedrawGate(idle_keepalive_s=_live_idle_keepalive_s)
    db_path = DB_PATH
    # Tracks whether the Gauge was live-and-unexpired as of the last tick,
    # so an expiry transition (with no new Payload arriving) can force a
    # fallback redraw to the Historic View (spec §9.2, ADR-0010) instead of
    # leaving a stale Gauge frame on the panel indefinitely.
    _gauge_was_shown = False
    # A render_module.PanelWorker, set once by main(); stays None under test
    # and before main() has run, and render() is then a safe no-op.
    _panel_worker = None

    @classmethod
    def on_connect(cls, ble_device) -> None:
        print(f"Connected to {ble_device.address}")

    @classmethod
    def on_disconnect(cls, adapter_address: str, device_address: str) -> None:
        print(f"Disconnected from {device_address}")

    @classmethod
    def on_ack_notify(cls, notifying: bool, characteristic) -> None:
        cls.ack_characteristic = characteristic if notifying else None

    @classmethod
    def send_ack(cls, ack: dict) -> None:
        if cls.ack_characteristic is None:
            return
        characteristic = cls.ack_characteristic

        from bluezero import async_tools

        def _notify() -> bool:
            characteristic.set_value(list(json.dumps(ack).encode("utf-8")))
            return False

        # Deferred: notifying from inside the write's own D-Bus call
        # confuses BlueZ's ATT state machine (the pending write reply
        # races the notification), so send it on the next event-loop
        # iteration instead.
        async_tools.add_timer_ms(0, _notify)

    @classmethod
    def periodic_tick(cls) -> bool:
        """Fired on a recurring timer (spec §8.5's own-clock redraw): the
        countdown moves every minute even with no usage change, and the
        24h idle keep-alive rides the same check. Returns True to keep the
        bluezero timer repeating.

        Also the only place an expiry transition is ever observed with no
        new Payload arriving: if the Gauge was showing last tick and has
        now expired, force a Historic fallback redraw (spec §9.2,
        ADR-0010) — otherwise a Desktop that simply stops pushing would
        leave a stale Gauge frame on the panel forever.

        Also where the render worker's watchdog gets polled (spec §8): a
        stuck ReadBusy needs no thread of its own, since this timer
        already runs on the BLE process's own clock once a minute.
        """
        if cls._panel_worker is not None:
            cls._panel_worker.check_watchdog()

        gauge_showing = cls.gauge.is_live() and not cls.gauge.is_expired()
        if gauge_showing:
            cls.redraw_gate.try_draw_gauge(cls.gauge.view())
        else:
            if cls._gauge_was_shown:
                cls.redraw_gate.mark_historic_pending()
            cls.redraw_gate.try_draw_historic_if_due({"historic": True})
        cls._gauge_was_shown = gauge_showing
        return True

    @classmethod
    def on_write(cls, value: list, options: dict) -> None:
        try:
            payload = parse_payload(value)
        except PayloadError as exc:
            print(f"Rejected malformed payload: {exc.reason}")
            cls.send_ack(build_ack("error", kind=exc.kind, reason=exc.reason))
            return

        kind = payload["kind"]
        conn = sqlite3.connect(cls.db_path)
        try:
            wiped = check_desktop_id(conn, payload["desktop_id"])

            if kind == "daily":
                upsert_reading(conn, payload)
                conn.commit()
                # batch_size/batch_index are consumed for logging only
                # (spec §6.1) — this is what makes an incomplete Batch
                # recognisable on the Pi without an extra round trip.
                print(
                    f"Reading upserted: {payload['date']} {payload['project']} "
                    f"{payload['model']} (batch {payload['batch_index'] + 1}/"
                    f"{payload['batch_size']})"
                )
                cls.redraw_gate.mark_historic_pending()
                drawn = False
                live_gauge_showing = cls.gauge.is_live() and not cls.gauge.is_expired()
                if not live_gauge_showing:
                    drawn = cls.redraw_gate.try_draw_historic_now({"historic": True})
                cls.send_ack(
                    build_ack(
                        "ok",
                        kind="daily",
                        date=payload["date"],
                        project=payload["project"],
                        model=payload["model"],
                        drawn=drawn,
                        wiped=wiped,
                    )
                )
            else:  # gauge
                conn.commit()
                cls.gauge.update(payload)
                drawn = cls.redraw_gate.try_draw_gauge(cls.gauge.view())
                cls.send_ack(build_ack("ok", kind="gauge", drawn=drawn, wiped=wiped))
        except sqlite3.Error as exc:
            conn.rollback()
            print(f"Failed to persist reading: {exc}")
            # Correlation fields are echoed whenever they parsed (spec
            # §6.3), even on an error Ack — a Daily Payload always has
            # date/project/model by the time it could reach a DB error.
            cls.send_ack(
                build_ack(
                    "error",
                    kind=kind,
                    date=payload.get("date") if kind == "daily" else None,
                    project=payload.get("project") if kind == "daily" else None,
                    model=payload.get("model") if kind == "daily" else None,
                    reason=f"db write failed: {exc}",
                )
            )
        finally:
            conn.close()


def main(adapter_address: str) -> None:
    from bluezero import adapter  # noqa: F401 — imported for parity/typing only
    from bluezero import async_tools
    from bluezero import peripheral

    init_db()
    ReceiveState.db_path = DB_PATH
    ReceiveState._panel_worker = render_module.PanelWorker()
    _draw_startup_frame()

    ble_receiver = peripheral.Peripheral(adapter_address, local_name="zeropi-display")
    ble_receiver.add_service(srv_id=1, uuid=SERVICE_UUID, primary=True)
    ble_receiver.add_characteristic(
        srv_id=1,
        chr_id=1,
        uuid=WRITE_CHARACTERISTIC_UUID,
        value=[],
        notifying=False,
        flags=["write"],
        write_callback=ReceiveState.on_write,
        read_callback=None,
        notify_callback=None,
    )
    ble_receiver.add_characteristic(
        srv_id=1,
        chr_id=2,
        uuid=NOTIFY_CHARACTERISTIC_UUID,
        value=[],
        notifying=False,
        flags=["notify"],
        notify_callback=ReceiveState.on_ack_notify,
        read_callback=None,
        write_callback=None,
    )

    ble_receiver.on_connect = ReceiveState.on_connect
    ble_receiver.on_disconnect = ReceiveState.on_disconnect

    # The Pi's own-clock redraw (spec §8.5): checked once a minute so the
    # countdown animates even with no Payload arriving, and so the 24h
    # idle keep-alive fires.
    async_tools.add_timer_ms(60_000, ReceiveState.periodic_tick)

    ble_receiver.publish()


if __name__ == "__main__":
    from bluezero import adapter as _adapter

    main(list(_adapter.Adapter.available())[0].address)
