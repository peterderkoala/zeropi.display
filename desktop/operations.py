"""Desktop: the Management Surface's operations (web-ui spec §4).

Everything `cli.py` used to do inside its `cmd_*` functions, minus the
printing and the exit code: each operation takes its inputs as arguments
and returns a structured result, so `cli.py` and the web layer are two
renderers over one set of actions (web-ui spec §1: "a skin, not a second
surface").

- A refusal decided before any BLE raises `Refused` (§4.2). Nothing has been
  written or sent when it does; the web layer answers the request with it
  and creates no Operation.
- An Unreachable Pi is not a refusal: it is part of the result
  (`Unreachable`), because the Operation did run.
- Every result carries an `outcome` named after management-surface §9.6's
  exit-code categories. The CLI maps it to its number; the web never sees one.
- Progress lines go through `progress(kind, text)` (§4.4). The default,
  `push.print_progress`, prints exactly what the CLI always printed.

Nothing here prints, except through `progress` and the lines `push.py`
itself prints outside §4.4's closed set of kinds.
"""

from __future__ import annotations

import enum
import json
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import config
import push
import usage
import verdict

SERVICE_NAME = "zeropi-push"

# §4.2's one Setting: the only Tier 2 key projected onto the Pi. Unlike
# every other Configuration key, changing it does not need a Desktop
# restart to take effect (§6.1: the Pi applies a Setting live) -- so
# `config set` pushes it over BLE immediately instead of reporting
# "pending restart".
PI_PROJECTED_SETTING = "pi.idle_keepalive_s"

# §4.6: written only by `pair`, and refused everywhere else so the wire's
# only path to a stored Pi address is the one that actually verified it
# answers.
PI_ADDRESS_KEY = "pi.address"


# ---------------------------------------------------------------------------
# §4.3 Tier 3 — verified invariants, mirrored for display only.
#
# Never imported from `pi/receive.py` (the Pi is a different deployment,
# and importing it would pull in `bluezero`); mirrored here by hand, like
# `config.py`'s and `verdict.py`'s own Tier 3 mirrors. `config set` against
# one of these names is refused as "not a setting" (§9.3) rather than
# "unknown key" -- the two refusal shapes are deliberately different.
#
# ⚠ These are strings, not live references, because this module must import
# cleanly on a Desktop-only install that never ships pi/receive.py or
# pi/render.py. `test_cli.py::test_tier3_mirrors_match_the_pis_real_constants`
# is what stands between an amendment on the Pi and a silently stale value
# here -- for every entry it can reach (the numeric ones); "SERVICE_UUID + 2
# characteristics" is a live `push.SERVICE_UUID` reference below, and
# "historic pitch" has no single named constant on the Pi to assert against.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Tier3Entry:
    value: str
    fixed_by: str
    why: str


TIER3: dict[str, Tier3Entry] = {
    "REDRAW_FLOOR_S": Tier3Entry(
        str(config.REDRAW_FLOOR_S), "ADR-0008",
        "the panel is rated for one update per 180s; 300s is the operating "
        "point established on hardware",
    ),
    "GAUGE_EXPIRY_S": Tier3Entry(
        str(config.GAUGE_EXPIRY_S), "ADR-0009/ADR-0010",
        "amended 2026-09-09 on measurement (#55)",
    ),
    "MAX_PAYLOAD_BYTES": Tier3Entry(
        str(push.MAX_PAYLOAD_BYTES), "ADR-0001",
        "re-confirmed by bisection: 512 is Acked, 513 raises "
        "INVALID_ATTRIBUTE_VALUE_LENGTH (#67) -- not the MTU",
    ),
    "MAX_ACK_BYTES": Tier3Entry(
        "min(512, ATT_MTU - 5) = 512", "#73/#78",
        "measured on the wire; an over-budget notification is truncated "
        "silently, twice, before it reaches the Desktop",
    ),
    "SERVICE_UUID + 2 characteristics": Tier3Entry(
        push.SERVICE_UUID, "hardware fact",
        "must match pi/receive.py's own copy exactly; changing one end "
        "silently breaks discovery",
    ),
    "text floor": Tier3Entry(
        "13px", "spec-eink-rendering.md §4",
        "re-confirmed with text rasterised on the Pi itself; NOT monotone "
        "-- it re-collides at 14px",
    ),
    "PANEL_W, PANEL_H": Tier3Entry(
        "250, 122", "hardware fact", "the Waveshare V4 panel's fixed resolution",
    ),
    "WATCHDOG_TIMEOUT_S": Tier3Entry(
        "30.0", "spec-eink-rendering.md §8",
        "stops the Pi caring about a stuck render thread; it cannot recover it",
    ),
    "FONT_DIR": Tier3Entry(
        "/usr/share/fonts/truetype/dejavu (fonts-dejavu-core)", "#66",
        "the 13px text floor was verified on glass with DejaVu specifically",
    ),
    "historic pitch": Tier3Entry(
        "5 rows @ 20px", "render.py",
        "floors usage.window_days's minimum of 5",
    ),
}

# §4.2's "Why those bounds" column, condensed -- shown on a refused Tier 2
# write so the ceiling/floor reads as derived, not arbitrary (§9.3).
TIER2_RATIONALE: dict[str, str] = {
    "service.poll_interval_s": (
        "Floor: the loop re-reads a file claude-hud rewrites at its own "
        "cadence -- under 5s is pure I/O. Ceiling: GAUGE_EXPIRY_S / 2 -- "
        "polling slower than half the expiry lets a Gauge change go "
        "unnoticed until the on-screen Gauge has already expired."
    ),
    "service.gauge_throttle_s": (
        "Ceiling is GAUGE_EXPIRY_S / 2 -- derived from a verified invariant "
        "(ADR-0008, amended by #55), not typed in here. A replacement Gauge "
        "must land well before expiry, or expiry stops meaning \"the Desktop "
        "is gone\" and starts firing in normal operation. Floor: below 30s "
        "the BLE work per change dominates for zero panel benefit."
    ),
    "gauge.stale_threshold_s": (
        "Ceiling is exactly GAUGE_EXPIRY_S -- a snapshot older than expiry "
        "arrives on the Pi already expired, so pushing it is guaranteed waste."
    ),
    "batch.catchup_threshold_s": (
        "Floor one hour -- below a normal daily cadence, catchup trips "
        "constantly. Ceiling one week: usage.window_days caps what a Batch "
        "can cover, so catchup beyond the Window cannot recover anything."
    ),
    "usage.window_days": (
        "Floor is panel geometry: the Historic View draws five rows at "
        "20px pitch, so a Window under 5 starves it. Ceiling: ADR-0003 "
        "sends one write per Reading, so each extra day is extra BLE work "
        "for rows the panel never draws."
    ),
    "push.scan_timeout_s": (
        "Ceiling: a scan longer than a minute outlives the cadence it "
        "feeds. Floor: 1s is below observed scan/connect time and will "
        "simply always fail."
    ),
    "push.ack_timeout_s": (
        "Same bounds as push.scan_timeout_s -- a Gauge push that fails is "
        "dropped silently with no retry, so a long timeout costs a stalled "
        "loop rather than a retry storm."
    ),
    "pi.idle_keepalive_s": (
        "Floor one hour: ADR-0007 is full-refresh-only, so every keepalive "
        "is a full panel refresh against a panel rated for one update per "
        "180s. Ceiling one week: past that the \"still alive\" reassurance "
        "the keepalive exists to give is gone."
    ),
}


# ---------------------------------------------------------------------------
# Outcomes, refusals, Unreachable
# ---------------------------------------------------------------------------


class Outcome(enum.Enum):
    """Management-surface §9.6's exit-code categories, by name (web-ui §6.6)."""

    OK = "ok"  # 0
    FAULT = "fault"  # 1: the Verdict is *not working*, or a row/Command failed
    CANT_TELL = "cant_tell"  # 2: either Unreachable case, or *not paired*
    REFUSED = "refused"  # 3


class Refused(Exception):
    """A refusal decided before any BLE (web-ui §4.2): nothing was written
    and nothing was sent. `message` is the human sentence; `rationale` is
    the "why" the CLI indents beneath it, when there is one."""

    def __init__(self, kind: str, message: str, rationale: Optional[str] = None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.rationale = rationale

    @property
    def outcome(self) -> Outcome:
        # §9.6: *not paired* is can't tell, every other refusal is 3.
        return Outcome.CANT_TELL if self.kind == "not_paired" else Outcome.REFUSED


@dataclass(frozen=True)
class Unreachable:
    """The Pi could not be asked (§7.1): `reason` is "absent" or "busy"."""

    reason: str
    detail: str


def _unreachable(exc: Exception) -> Unreachable:
    return Unreachable("busy" if isinstance(exc, push.BleLinkBusy) else "absent", str(exc))


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------


def short_id(address: str) -> str:
    """The Pi's "short id" (§9.4's wipe confirmation) -- the last three
    octets of its BLE address, lowercase, no separators. Short enough to
    type, long enough that a slip does not silently match a different Pi.
    Not the first three: those are the manufacturer prefix (B8:27:EB on every
    Pi of this generation), so they cannot tell two Pis apart (#101).
    """
    return address.replace(":", "").lower()[-6:]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def desktop_facts(cfg: config.Configuration, summary: usage.PushedSummary, now: Optional[datetime] = None) -> verdict.DesktopFacts:
    """Gathers verdict.py's `DesktopFacts` (§8.3) -- verdict.py itself takes
    no I/O, so this is the caller-side half #86 owns."""
    now = now or _now_utc()
    seconds_since_last_push = None
    if summary.last_pushed_at is not None:
        try:
            last = datetime.fromisoformat(summary.last_pushed_at)
        except ValueError:
            last = None
        if last is not None:
            seconds_since_last_push = (now - last).total_seconds()
    return verdict.DesktopFacts(
        pi_address=cfg.pi_address,
        readings=summary.readings,
        coverage_start=summary.coverage_start,
        idle_keepalive_s=cfg.pi_idle_keepalive_s,
        seconds_since_last_push=seconds_since_last_push,
    )


def readings_sent(cfg: config.Configuration) -> int:
    """How many Readings this Desktop has pushed -- what a `wipe` deletes."""
    store = usage.open_store(cfg.paths_store)
    try:
        return usage.pushed_summary(store).readings
    finally:
        store.close()


@dataclass(frozen=True)
class ServiceInfo:
    """The Desktop identity block's service half (§9.2's "Desktop" line).
    Cosmetic only -- nothing in the Verdict depends on it."""

    state: str  # "running" | "not running" | "unknown"
    uptime_s: Optional[float]


def service_info(
    service_name: str = SERVICE_NAME,
    run_fn: Callable[..., Any] = subprocess.run,
) -> ServiceInfo:
    try:
        result = run_fn(
            ["systemctl", "--user", "show", service_name, "-p", "ActiveState", "-p", "ActiveEnterTimestamp"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ServiceInfo(state="unknown", uptime_s=None)
    if result.returncode != 0:
        return ServiceInfo(state="unknown", uptime_s=None)

    fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    active_state = fields.get("ActiveState", "")
    if active_state == "active":
        state = "running"
    elif active_state:
        state = "not running"
    else:
        state = "unknown"

    uptime_s = None
    timestamp = fields.get("ActiveEnterTimestamp", "")
    if timestamp and timestamp != "n/a":
        try:
            entered = datetime.strptime(timestamp, "%a %Y-%m-%d %H:%M:%S %Z")
            uptime_s = (datetime.now() - entered).total_seconds()
        except ValueError:
            uptime_s = None
    return ServiceInfo(state=state, uptime_s=uptime_s)


def restart_service(service_name: str = SERVICE_NAME, run_fn: Callable[..., Any] = subprocess.run) -> tuple[bool, str]:
    try:
        result = run_fn(
            ["systemctl", "--user", "restart", service_name],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if result.returncode != 0:
        return False, result.stderr.strip() or f"exit code {result.returncode}"
    return True, ""


def require_paired(cfg: config.Configuration) -> None:
    """The `not_paired` refusal shared by every Pi action that needs a
    stored `pi.address` (§4.6)."""
    if cfg.pi_address is None:
        raise Refused("not_paired", "Not paired — no Pi address is recorded.")


# ---------------------------------------------------------------------------
# §7's seams, called directly rather than re-derived (per the handoff note
# on #84/#85: "the CLI-initiated settings write already exists").
# ---------------------------------------------------------------------------


async def send_command(
    verb: str,
    desktop_id_: str,
    pi_address: str,
    cfg: config.Configuration,
    *,
    lock_wait_s: float = push.CLI_LOCK_WAIT_S,
    round_trips: Optional[list[float]] = None,
    before_send: Optional[Callable[[], None]] = None,
    progress: push.Progress = push.print_progress,
) -> Optional[dict]:
    """Sends one Command Payload (spec §5.2) over one BLE connection,
    re-asserting Settings at its head like every other connection (§7.2).
    Returns the Ack dict, or raises (`BleLinkBusy`, or a scan/connect
    failure) exactly like `push.run_batch_pass` does.

    `round_trips`, if given, receives the Command's own write-and-Ack time —
    not the scan and connect before it, which on real hardware are 5-10 s of
    the total and would make §9.2's "replied in" read as a slow Pi (#87).
    `before_send` runs while the BLE lock is held, just before the write.
    """
    settings = push.settings_from_config(cfg)

    async def _run(send_one: push.SendOne) -> Optional[dict]:
        if before_send is not None:
            before_send()
        start = time.monotonic()
        ack = await send_one(push.build_command_payload(verb, desktop_id_))
        if round_trips is not None:
            round_trips.append(time.monotonic() - start)
        return ack

    return await push._with_ble_connection(
        _run,
        settings_payload=push.build_settings_payload(settings, desktop_id_),
        pi_address=pi_address,
        lock_wait_s=lock_wait_s,
        progress=progress,
    )


# ---------------------------------------------------------------------------
# §9.2 `status`
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StatusResult:
    verdict: verdict.Verdict
    facts: verdict.DesktopFacts
    status: Optional[dict]
    round_trip_s: Optional[float]
    error_detail: Optional[str]
    reach: verdict.Reach = verdict.Reach.REACHABLE

    @property
    def outcome(self) -> Outcome:
        if self.verdict.state is verdict.State.OK:
            return Outcome.OK
        if self.verdict.state is verdict.State.NOT_WORKING:
            return Outcome.FAULT
        return Outcome.CANT_TELL  # CANT_TELL or NOT_PAIRED — §9.6: split from 1 on purpose


async def status(
    cfg: config.Configuration,
    *,
    lock_wait_s: float = push.CLI_LOCK_WAIT_S,
    progress: push.Progress = push.print_progress,
) -> StatusResult:
    def read_facts() -> verdict.DesktopFacts:
        store = usage.open_store(cfg.paths_store)
        try:
            summary = usage.pushed_summary(store)
        finally:
            store.close()
        return desktop_facts(cfg, summary)

    if cfg.pi_address is None:
        facts = read_facts()
        v = verdict.build_verdict(facts, None, verdict.Reach.REACHABLE)
        return StatusResult(v, facts, None, None, None)

    did = push.desktop_id()
    reach = verdict.Reach.REACHABLE
    status_ack: Optional[dict] = None
    round_trip_s: Optional[float] = None
    error_detail: Optional[str] = None
    round_trips: list[float] = []
    # ⚠ The Desktop's side is read while the BLE lock is held, never before
    # waiting for it: a Batch holding the link is still setting its push
    # marks, so a snapshot taken then undercounts against the Pi's
    # post-Batch reply and reads as a false "not working" (found by #87).
    held_facts: list[verdict.DesktopFacts] = []
    try:
        status_ack = await send_command(
            "status",
            did,
            cfg.pi_address,
            cfg,
            lock_wait_s=lock_wait_s,
            round_trips=round_trips,
            before_send=lambda: held_facts.append(read_facts()),
            progress=progress,
        )
    except push.BleLinkBusy as exc:
        reach = verdict.Reach.BUSY
        error_detail = str(exc)
    except sqlite3.Error:
        # The Desktop's own store, read under the lock above -- this
        # Desktop's fault, never evidence that the Pi is absent.
        raise
    except Exception as exc:  # noqa: BLE001 - scan/connect failure means absent (§7.1)
        reach = verdict.Reach.ABSENT
        error_detail = str(exc)
    else:
        round_trip_s = round_trips[0] if round_trips else None
        if status_ack is None:
            # Connected, but no Ack came back: nothing was learned (§8.1).
            reach = verdict.Reach.ABSENT
            error_detail = "no Ack from the Pi"
        # ⚠ An error Ack is passed through, not turned into ABSENT: the Pi
        # answered, so the Verdict must say *not working*, not *unreachable*
        # (found on hardware by #87). That includes §11 trap 6's truncated
        # reply, which push.py reports as `malformed ack from Pi: …`.

    facts = held_facts[0] if held_facts else read_facts()
    v = verdict.build_verdict(facts, status_ack, reach)
    return StatusResult(v, facts, status_ack, round_trip_s, error_detail, reach)


def status_json(r: StatusResult, desktop_id_: str, service: ServiceInfo) -> dict:
    """The one builder of management-surface §9.5's `status` JSON (web-ui
    §4.3), used by `cli.py status --json` and the web layer alike."""
    v, facts, status_ = r.verdict, r.facts, r.status
    return {
        "verdict": {"ok": v.ok, "state": v.state.value, "headline": v.headline},
        "reachable": v.reachable,
        "desktop": {
            "desktop_id": desktop_id_,
            "service": service.state,
            "readings_sent": facts.readings,
            "coverage_start": facts.coverage_start,
        },
        "pi": status_,
        "checks": [
            {"name": c.name, "severity": c.severity.value, "ok": c.ok, "detail": c.detail}
            for c in v.checks
        ],
    }


# ---------------------------------------------------------------------------
# §9.3 `config`
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConfigValue:
    key: str
    value: Any
    tier3: Optional[Tier3Entry] = None  # set for a Tier 3 name: read-only, not Configuration


def config_get(conn, key: str) -> ConfigValue:
    if key in TIER3:
        return ConfigValue(key, TIER3[key].value, TIER3[key])
    key_spec = config.KEYS.get(key)
    if key_spec is None:
        raise Refused("unknown_key", f"{key!r} is not a known Configuration key.")
    return ConfigValue(key, config.read_config(conn).get(key, key_spec.default))


def _parse_value(key: config.Key, raw: str) -> Any:
    """A string from the CLI or the page -> the typed Python value
    `write_config_value` expects. `Key.serialize`/`validate` still do the
    real type- and range-checking (spec §3.5); this only turns text into the
    right Python type so those checks see a value of the right shape.
    """
    if key.kind == "json":
        return json.loads(raw)
    if key.kind == "real":
        return float(raw)
    if key.kind == "int":
        return int(raw)
    return raw  # "path" and "address": Key.serialize normalises the string


def write_config(conn, key: str, raw_value: str) -> Any:
    """`config set`'s write: validates `raw_value` against §4 and stores it,
    or raises `Refused` having written nothing. Returns the typed value.
    No BLE: pushing the one Setting is `push_setting`'s job."""
    if key == PI_ADDRESS_KEY:
        # §4.6: written only by `pair`, so the wire's only path to a stored
        # address is the one that actually verified the Pi answers there.
        raise Refused("pair_only", f"{PI_ADDRESS_KEY} is written only by pair.")

    if key in TIER3:
        entry = TIER3[key]
        raise Refused(
            "not_a_setting",
            f"{key} is not a setting.",
            f"It is a verified invariant fixed by {entry.fixed_by}: {entry.why}.",
        )

    key_spec = config.KEYS.get(key)
    if key_spec is None:
        raise Refused("unknown_key", f"{key!r} is not a known Configuration key. Nothing was written.")

    try:
        value = _parse_value(key_spec, raw_value)
        config.write_config_value(conn, key, value)
    except (ValueError, config.ConfigValueError) as exc:
        raise Refused("out_of_range", str(exc), TIER2_RATIONALE.get(key)) from exc
    return value


def is_pi_push(cfg: config.Configuration, key: str) -> bool:
    """Whether writing `key` is followed by sending it to the Pi (`→ Pi`)."""
    return key == PI_PROJECTED_SETTING and cfg.pi_address is not None


@dataclass(frozen=True)
class SettingPushResult:
    key: str
    value: Any
    error: Optional[str]  # None: pushed, applies immediately (§6.1)

    @property
    def outcome(self) -> Outcome:
        # Already written; a failed push applies on the next connection (§7.2).
        return Outcome.OK


async def push_setting(
    cfg: config.Configuration, value: Any, *, progress: push.Progress = push.print_progress
) -> SettingPushResult:
    did = push.desktop_id()
    settings_payload = push.build_settings_payload({"idle_keepalive_s": value}, did)
    try:
        await push._with_ble_connection(
            None,
            settings_payload=settings_payload,
            settings_required=True,
            pi_address=cfg.pi_address,
            progress=progress,
        )
    except Exception as exc:  # noqa: BLE001 - best-effort immediate push
        return SettingPushResult(PI_PROJECTED_SETTING, value, str(exc))
    return SettingPushResult(PI_PROJECTED_SETTING, value, None)


# ---------------------------------------------------------------------------
# §9.4 The actions
# ---------------------------------------------------------------------------


def _batch_outcome(batch: Optional[push.BatchResult], unreachable: Optional[Unreachable]) -> Outcome:
    if unreachable is not None:
        return Outcome.CANT_TELL
    return Outcome.OK if batch.ok else Outcome.FAULT


@dataclass(frozen=True)
class PairResult:
    desktop_id: str
    address: Optional[str]  # None: the scan found no Pi, nothing was written
    batch: Optional[push.BatchResult]
    unreachable: Optional[Unreachable]  # absent: the scan; busy: the Batch phase

    @property
    def outcome(self) -> Outcome:
        return _batch_outcome(self.batch, self.unreachable)


async def pair(
    cfg: config.Configuration,
    cfg_path,
    *,
    lock_wait_s: float = push.CLI_LOCK_WAIT_S,
    progress: push.Progress = push.print_progress,
) -> PairResult:
    did = push.desktop_id()
    # ⚠ This scan is deliberately separate from the one `run_batch_pass`
    # performs below via `_with_ble_connection` -- pairing has to discover
    # the address *before* it can be recorded, so a second, address-filtered
    # scan for the archive push is the cost of that ordering. It is usually
    # fast (the same device is still advertising seconds later), but it is a
    # real second scan, not free; worth collapsing into one connection if
    # `run_batch_pass`'s seam is ever restructured to accept an
    # already-open connection.
    progress("scanning", f"Scanning (up to {push.SCAN_TIMEOUT_SECONDS:.0f}s)…")
    try:
        device = await push.find_pi()
    except Exception as exc:  # noqa: BLE001 - no candidate Pi found
        return PairResult(did, None, None, Unreachable("absent", str(exc)))

    conn = config.open_config_store(cfg_path)
    try:
        config.write_config_value(conn, PI_ADDRESS_KEY, device.address)
    finally:
        conn.close()
    progress("found", f"Found {device.name or device.address} ({device.address})")
    progress("phase", f"Coupled. This Desktop is {did[:8]}…")

    # Re-send the Window so a fresh Pi (which adopts this Desktop Id without
    # a wipe) gets it -- but only the Window: marks outside it are Readings a
    # re-paired Pi still holds and no Batch can re-send.
    store = usage.open_store(cfg.paths_store)
    try:
        usage.clear_pushed_marks(store, usage.window_dates())
    finally:
        store.close()

    try:
        batch = await push.run_batch_pass(
            str(cfg.paths_store),
            cfg.paths_projects_root,
            settings=push.settings_from_config(cfg),
            pi_address=device.address,
            lock_wait_s=lock_wait_s,
            progress=progress,
        )
    except push.BleLinkBusy as exc:
        return PairResult(did, device.address, None, _unreachable(exc))
    return PairResult(did, device.address, batch, None)


@dataclass(frozen=True)
class PushResult:
    batch: Optional[push.BatchResult]
    unreachable: Optional[Unreachable]
    # The Setting re-asserted at the head of the connection (§7.2).
    idle_keepalive_s: int

    @property
    def outcome(self) -> Outcome:
        return _batch_outcome(self.batch, self.unreachable)


async def push_batch(
    cfg: config.Configuration,
    *,
    lock_wait_s: float = push.CLI_LOCK_WAIT_S,
    progress: push.Progress = push.print_progress,
) -> PushResult:
    require_paired(cfg)
    try:
        batch = await push.run_batch_pass(
            str(cfg.paths_store),
            cfg.paths_projects_root,
            settings=push.settings_from_config(cfg),
            pi_address=cfg.pi_address,
            lock_wait_s=lock_wait_s,
            progress=progress,
        )
    except Exception as exc:  # noqa: BLE001 - busy, or no reply from the paired Pi
        return PushResult(None, _unreachable(exc), cfg.pi_idle_keepalive_s)
    return PushResult(batch, None, cfg.pi_idle_keepalive_s)


@dataclass(frozen=True)
class CommandResult:
    """A human-typed Command (`redraw`/`wipe`): the Ack, or why there is none.

    §9.6: a Command refused because the Pi is Unreachable -- busy or absent
    -- is 3, not the Verdict's *can't tell* 2. Nothing is queued; the human
    is the only retrier (ADR-0011).
    """

    ack: Optional[dict]
    unreachable: Optional[Unreachable]

    @property
    def ok(self) -> bool:
        return self.ack is not None and self.ack.get("status") == "ok"

    @property
    def reason(self) -> str:
        """Why an Ack was not ok -- "no Ack" when none came back."""
        return (self.ack or {}).get("reason", "no Ack")

    @property
    def outcome(self) -> Outcome:
        if self.unreachable is not None:
            return Outcome.REFUSED
        return Outcome.OK if self.ok else Outcome.FAULT


async def _send_user_command(
    verb: str, cfg: config.Configuration, lock_wait_s: float, progress: push.Progress
) -> CommandResult:
    try:
        ack = await send_command(
            verb, push.desktop_id(), cfg.pi_address, cfg, lock_wait_s=lock_wait_s, progress=progress
        )
    except Exception as exc:  # noqa: BLE001 - busy, or no reply from the paired Pi
        return CommandResult(None, _unreachable(exc))
    return CommandResult(ack, None)


async def redraw(
    cfg: config.Configuration,
    *,
    lock_wait_s: float = push.CLI_LOCK_WAIT_S,
    progress: push.Progress = push.print_progress,
) -> CommandResult:
    """The Ack's `drawn` says whether the Pi drew now; if not, its
    `floor_remaining_s` is how long ADR-0008's floor holds the draw."""
    require_paired(cfg)
    return await _send_user_command("redraw", cfg, lock_wait_s, progress)


@dataclass(frozen=True)
class WipeResult:
    command: CommandResult
    repush: Optional[push.BatchResult] = None  # None: the re-push did not run
    repush_busy: Optional[str] = None  # set when the link was busy for the re-push

    @property
    def outcome(self) -> Outcome:
        if self.command.outcome is not Outcome.OK:
            return self.command.outcome
        if self.repush_busy is not None:
            return Outcome.CANT_TELL
        return Outcome.OK if self.repush.ok else Outcome.FAULT


async def wipe(
    cfg: config.Configuration,
    confirmation: str,
    *,
    lock_wait_s: float = push.CLI_LOCK_WAIT_S,
    progress: push.Progress = push.print_progress,
) -> WipeResult:
    """`confirmation` is the short id the human typed (§9.4); a mismatch
    refuses before anything is sent."""
    require_paired(cfg)
    if confirmation.strip() != short_id(cfg.pi_address):
        raise Refused("confirm_mismatch", "Confirmation did not match. Nothing was sent.")

    command = await _send_user_command("wipe", cfg, lock_wait_s, progress)
    if command.outcome is not Outcome.OK:
        return WipeResult(command)

    progress("phase", "Wiped. Re-pushing the archive.")
    # The wipe itself already succeeded, so a busy link here only affects
    # when the re-push happens, not whether the wipe did.
    store = usage.open_store(cfg.paths_store)
    try:
        usage.clear_pushed_marks(store)
    finally:
        store.close()
    try:
        repush = await push.run_batch_pass(
            str(cfg.paths_store),
            cfg.paths_projects_root,
            settings=push.settings_from_config(cfg),
            pi_address=cfg.pi_address,
            lock_wait_s=lock_wait_s,
            progress=progress,
        )
    except push.BleLinkBusy as exc:
        return WipeResult(command, repush_busy=str(exc))
    return WipeResult(command, repush)
