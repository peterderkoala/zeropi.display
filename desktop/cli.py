"""Desktop: `cli.py` (spec §9) — the front end wiring tickets 1-5 together.

`python desktop/cli.py <command>`. **Not** a `zeropi` console script (§9.1) —
packaging is deliberately deferred to when the web UI brings a service that
must be installed anyway. `push.py`'s own CLI stays exactly as it is; this
module calls `push.run_batch_pass` / `push.run_gauge_push` / the BLE seams
as functions, never by shelling out.

Five commands beyond `status`/`config`, all imperative and all refused
outright rather than queued when the Pi is Unreachable (ADR-0011): `pair`,
`push`, `redraw`, `wipe`, `restart`. Every one of them is integration over
the seams #81-#85 already built — this module adds no new domain logic of
its own beyond rendering the Verdict (§8) and validating `config set`
against the Tier schema (§4).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
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
# ⚠ These are strings, not live references, because cli.py must import
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
# Small shared helpers
# ---------------------------------------------------------------------------


def _dur(seconds: Optional[float]) -> str:
    """A short human duration, or "never" for `None` -- the same shorthand
    used throughout status rendering (§9.2's `up 3d 4h`, `drew 2m 23s ago`)."""
    if seconds is None:
        return "never"
    seconds = max(0, int(seconds))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _short_id(address: str) -> str:
    """The Pi's "short id" (§9.4's wipe confirmation) -- the first three
    octets of its BLE address, lowercase, no separators. Short enough to
    type, long enough that a slip does not silently match a different Pi.
    """
    return address.replace(":", "").lower()[:6]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _desktop_facts(cfg: config.Configuration, summary: usage.PushedSummary, now: Optional[datetime] = None) -> verdict.DesktopFacts:
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


@dataclass(frozen=True)
class ServiceInfo:
    """The Desktop identity block's service half (§9.2's "Desktop" line).
    Cosmetic only -- nothing in the Verdict depends on it."""

    state: str  # "running" | "not running" | "unknown"
    uptime_s: Optional[float]


def _service_info(
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


def _restart_service(service_name: str = SERVICE_NAME, run_fn: Callable[..., Any] = subprocess.run) -> tuple[bool, str]:
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


# ---------------------------------------------------------------------------
# §7's seams, called directly rather than re-derived (per the handoff note
# on #84/#85: "the CLI-initiated settings write already exists").
# ---------------------------------------------------------------------------


async def _send_command(
    verb: str,
    desktop_id_: str,
    pi_address: str,
    cfg: config.Configuration,
    *,
    lock_wait_s: float = push.CLI_LOCK_WAIT_S,
    round_trips: Optional[list[float]] = None,
    before_send: Optional[Callable[[], None]] = None,
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
    )


def _print_unreachable_refusal(exc: Exception, *, busy: bool, action: str = "queued") -> None:
    if busy:
        print(f"✗ Refused — the link is busy ({exc}).", file=sys.stderr)
        print(
            f"Not {action}. Retry in a moment — a Batch takes about two minutes.",
            file=sys.stderr,
        )
    else:
        print(f"✗ Refused — the Pi is unreachable ({exc}).", file=sys.stderr)
        print(f"Not {action}. Re-run when the Pi is back.", file=sys.stderr)


# ---------------------------------------------------------------------------
# §9.2 `status`
# ---------------------------------------------------------------------------


def _exit_code_for(v: verdict.Verdict) -> int:
    if v.state is verdict.State.OK:
        return 0
    if v.state is verdict.State.NOT_WORKING:
        return 1
    return 2  # CANT_TELL or NOT_PAIRED — §9.6: split from 1 on purpose


# §8.2's three severities, rendered — kept local to the CLI (verdict.py's
# own GLYPHS dict is keyed by §8.1's four *states*, a different vocabulary).
SEVERITY_GLYPH = {
    verdict.Severity.OK: "✓",
    verdict.Severity.FAIL: "✗",
    verdict.Severity.NOTE: "·",
}


@dataclass(frozen=True)
class StatusResult:
    verdict: verdict.Verdict
    facts: verdict.DesktopFacts
    status: Optional[dict]
    round_trip_s: Optional[float]
    error_detail: Optional[str]


async def _status_verdict(cfg: config.Configuration, *, lock_wait_s: float = push.CLI_LOCK_WAIT_S) -> StatusResult:
    def read_facts() -> verdict.DesktopFacts:
        store = usage.open_store(cfg.paths_store)
        try:
            summary = usage.pushed_summary(store)
        finally:
            store.close()
        return _desktop_facts(cfg, summary)

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
        status_ack = await _send_command(
            "status",
            did,
            cfg.pi_address,
            cfg,
            lock_wait_s=lock_wait_s,
            round_trips=round_trips,
            before_send=lambda: held_facts.append(read_facts()),
        )
    except push.BleLinkBusy as exc:
        reach = verdict.Reach.BUSY
        error_detail = str(exc)
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
    return StatusResult(v, facts, status_ack, round_trip_s, error_detail)


def _ago(seconds: Optional[float]) -> str:
    """`_dur` as a point in the past: "4m 7s ago", or plain "never" -- not
    "never ago" (found on hardware by #87)."""
    return "never" if seconds is None else f"{_dur(seconds)} ago"


def _desktop_lines(facts: verdict.DesktopFacts, service_info: ServiceInfo) -> list[str]:
    uptime = f", up {_dur(service_info.uptime_s)}" if service_info.uptime_s is not None else ""
    return [
        f"  Desktop   {SERVICE_NAME} {service_info.state}{uptime}",
        f"            last Batch {_ago(facts.seconds_since_last_push)}, {facts.readings} Readings sent",
    ]


def _render_status_full(r: StatusResult, service_info: ServiceInfo) -> str:
    v, facts, status = r.verdict, r.facts, r.status
    lines = ["", f"  {v.glyph}  {v.headline}", ""]

    if v.state is verdict.State.NOT_PAIRED:
        lines += [f"     {v.advice}", ""]
        return "\n".join(lines)

    if v.state is verdict.State.CANT_TELL:
        if r.error_detail:
            lines += [f"     {r.error_detail}.", ""]
        lines += _desktop_lines(facts, service_info) + [
            f"  Pi        last seen {_ago(facts.seconds_since_last_push)}",
            f"            holding {facts.readings} Readings then",
            "",
            f"  {v.advice}",
            "",
        ]
        return "\n".join(lines)

    if v.advice:
        # Only the error-reply Verdict reaches here with advice: the Pi
        # answered, but with nothing the six checks could compare.
        lines += [f"     {v.advice}", ""]
    lines += _desktop_lines(facts, service_info)
    if status is not None:
        rt = f"{r.round_trip_s:.1f}s" if r.round_trip_s is not None else "?"
        uptime = f", up {_dur(status['uptime_s'])}" if "uptime_s" in status else ""
        lines.append(f"  Pi        replied in {rt}{uptime}")
    lines.append("")
    for check in v.checks:
        lines.append(f"  {check.name.capitalize():<10} {SEVERITY_GLYPH[check.severity]}  {check.detail}")
    lines.append("")
    return "\n".join(lines)


def _render_status_brief(r: StatusResult) -> str:
    v, facts, status = r.verdict, r.facts, r.status
    if v.state is verdict.State.OK and status is not None:
        lines = [
            f"  {v.glyph} Working. Pi up {_dur(status.get('uptime_s'))}, "
            f"{status.get('frame')} frame drawn {_dur(status.get('since_redraw_s'))} ago,",
            f"    {facts.readings} Readings agreed from {facts.coverage_start}.",
        ]
        for check in v.checks:
            if check.severity is verdict.Severity.NOTE:
                lines.append(f"    · {check.name}: {check.detail}")
        return "\n".join(lines)

    lines = [f"  {v.glyph} {v.headline}"]
    for check in v.checks:
        if check.severity is verdict.Severity.FAIL:
            lines.append(f"    {check.name}: {check.detail}")
    if v.advice:
        lines.append(f"    {v.advice}")
    return "\n".join(lines)


def _render_status_json(r: StatusResult, desktop_id_: str, service_info: ServiceInfo) -> dict:
    v, facts, status = r.verdict, r.facts, r.status
    return {
        "verdict": {"ok": v.ok, "state": v.state.value, "headline": v.headline},
        "reachable": v.reachable,
        "desktop": {
            "desktop_id": desktop_id_,
            "service": service_info.state,
            "readings_sent": facts.readings,
            "coverage_start": facts.coverage_start,
        },
        "pi": status,
        "checks": [
            {"name": c.name, "severity": c.severity.value, "ok": c.ok, "detail": c.detail}
            for c in v.checks
        ],
    }


async def cmd_status(cfg: config.Configuration, args: argparse.Namespace, *, lock_wait_s: float = push.CLI_LOCK_WAIT_S) -> int:
    r = await _status_verdict(cfg, lock_wait_s=lock_wait_s)
    if args.json:
        did = "" if cfg.pi_address is None else push.desktop_id()
        print(json.dumps(_render_status_json(r, did, _service_info()), indent=2))
    elif args.brief:
        print(_render_status_brief(r))
    else:
        print(_render_status_full(r, _service_info()))
    return _exit_code_for(r.verdict)


# ---------------------------------------------------------------------------
# §9.3 `config`
# ---------------------------------------------------------------------------


def _render_config_listing(conn) -> str:
    values = config.read_config(conn)
    lines = ["", "  Tier 1 — deployment facts (freely editable)", ""]
    for name, key in config.KEYS.items():
        if key.tier != 1:
            continue
        value = values.get(name, key.default)
        lines.append(f"    {name:<38}{value}")

    lines += ["", "  Tier 2 — policy (editable within a validated range)", ""]
    for name, key in config.KEYS.items():
        if key.tier != 2:
            continue
        value = values.get(name, key.default)
        bound = ""
        if key.lo is not None or key.hi is not None:
            bound = f"{key.lo} .. {key.hi}"
        marker = "  → Pi" if name == PI_PROJECTED_SETTING else ""
        lines.append(f"    {name:<38}{str(value):<12} {bound}{marker}")

    lines += ["", "  Tier 3 — verified invariants. NOT settings; shown so you can see them.", ""]
    for name, entry in TIER3.items():
        lines.append(f"    {name:<38}{entry.value:<22} {entry.fixed_by}")
        lines.append(f"    {'':<38}{entry.why}")

    lines += ["", "  Changing a Tier 1/2 key needs a restart:  python desktop/cli.py restart", ""]
    return "\n".join(lines)


def _config_get(conn, key: str) -> int:
    if key in TIER3:
        entry = TIER3[key]
        print(f"{key} = {entry.value}  ({entry.fixed_by}, read-only — not a Configuration key)")
        return 0
    key_spec = config.KEYS.get(key)
    if key_spec is None:
        print(f"✗ {key!r} is not a known Configuration key.", file=sys.stderr)
        return 3
    values = config.read_config(conn)
    value = values.get(key, key_spec.default)
    print(f"{key} = {value}")
    return 0


def _parse_cli_value(key: config.Key, raw: str) -> Any:
    """A CLI string argument -> the typed Python value `write_config_value`
    expects. `Key.serialize`/`validate` still do the real type- and
    range-checking (spec §3.5); this only turns text into the right Python
    type so those checks see a value of the right shape.
    """
    if key.kind == "json":
        return json.loads(raw)
    if key.kind == "real":
        return float(raw)
    if key.kind == "int":
        return int(raw)
    return raw  # "path" and "address": Key.serialize normalises the string


async def _config_set(conn, cfg: config.Configuration, key: str, raw_value: str) -> int:
    if key == PI_ADDRESS_KEY:
        # §4.6: written only by `pair`, so the wire's only path to a stored
        # address is the one that actually verified the Pi answers there.
        print(f"✗ {PI_ADDRESS_KEY} is written only by  python desktop/cli.py pair.", file=sys.stderr)
        print("Nothing was written.", file=sys.stderr)
        return 3

    if key in TIER3:
        entry = TIER3[key]
        print(f"✗ {key} is not a setting.", file=sys.stderr)
        print("", file=sys.stderr)
        print(f"    It is a verified invariant fixed by {entry.fixed_by}: {entry.why}.", file=sys.stderr)
        print(
            "    It is not stored as Configuration at all — it is a constant in the "
            "code, shown read-only.",
            file=sys.stderr,
        )
        print("", file=sys.stderr)
        print("    See  python desktop/cli.py config  (Tier 3).", file=sys.stderr)
        return 3

    key_spec = config.KEYS.get(key)
    if key_spec is None:
        print(f"✗ {key!r} is not a known Configuration key. Nothing was written.", file=sys.stderr)
        return 3

    try:
        value = _parse_cli_value(key_spec, raw_value)
        config.write_config_value(conn, key, value)
    except (ValueError, config.ConfigValueError) as exc:
        print(f"✗ Refused. {exc}", file=sys.stderr)
        rationale = TIER2_RATIONALE.get(key)
        if rationale:
            print("", file=sys.stderr)
            print(f"    {rationale}", file=sys.stderr)
        print("", file=sys.stderr)
        print("    Nothing was written.", file=sys.stderr)
        return 3

    if key == PI_PROJECTED_SETTING and cfg.pi_address is not None:
        did = push.desktop_id()
        settings_payload = push.build_settings_payload({"idle_keepalive_s": value}, did)
        try:
            await push._with_ble_connection(
                None,
                settings_payload=settings_payload,
                settings_required=True,
                pi_address=cfg.pi_address,
            )
        except Exception as exc:  # noqa: BLE001 - best-effort immediate push
            print(f"Set {key} = {value}.")
            print(f"Could not push it to the Pi yet — {exc}.")
            print("It will apply on the next successful connection (§7.2).")
            return 0
        print(f"Set {key} = {value}. Pushed to the Pi — it applies immediately (§6.1).")
        return 0

    print(f"Set {key} = {value}.")
    print("Pending restart. Run  python desktop/cli.py restart  to apply.")
    return 0


async def cmd_config(cfg: config.Configuration, cfg_path, args: argparse.Namespace) -> int:
    conn = config.open_config_store(cfg_path)
    try:
        action = getattr(args, "config_action", None)
        if action == "get":
            return _config_get(conn, args.key)
        if action == "set":
            return await _config_set(conn, cfg, args.key, args.value)
        print(_render_config_listing(conn))
        return 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# §9.4 The action commands
# ---------------------------------------------------------------------------


async def cmd_pair(
    cfg: config.Configuration,
    cfg_path,
    args: argparse.Namespace,
    *,
    lock_wait_s: float = push.CLI_LOCK_WAIT_S,
) -> int:
    did = push.desktop_id()
    # ⚠ This scan is deliberately separate from the one `run_batch_pass`
    # performs below via `_with_ble_connection` -- pairing has to discover
    # the address *before* it can be recorded, so a second, address-filtered
    # scan for the archive push is the cost of that ordering. It is usually
    # fast (the same device is still advertising seconds later), but it is a
    # real second scan, not free; worth collapsing into one connection if
    # `run_batch_pass`'s seam is ever restructured to accept an
    # already-open connection.
    print(f"Scanning (up to {push.SCAN_TIMEOUT_SECONDS:.0f}s)…")
    try:
        device = await push.find_pi()
    except Exception as exc:  # noqa: BLE001 - no candidate Pi found
        print(f"✗ Refused — the Pi is unreachable ({exc}).", file=sys.stderr)
        print("Not paired. Pairing needs the Pi powered on and in range.", file=sys.stderr)
        return 2

    conn = config.open_config_store(cfg_path)
    try:
        config.write_config_value(conn, PI_ADDRESS_KEY, device.address)
    finally:
        conn.close()
    print(f"Found {device.name or device.address} ({device.address})")
    print(f"Coupled. This Desktop is {did[:8]}…")

    # Re-send the Window so a fresh Pi (which adopts this Desktop Id without
    # a wipe) gets it -- but only the Window: marks outside it are Readings a
    # re-paired Pi still holds and no Batch can re-send.
    store = usage.open_store(cfg.paths_store)
    try:
        usage.clear_pushed_marks(store, usage.window_dates())
    finally:
        store.close()

    try:
        result = await push.run_batch_pass(
            str(cfg.paths_store),
            cfg.paths_projects_root,
            settings=push.settings_from_config(cfg),
            pi_address=device.address,
            lock_wait_s=lock_wait_s,
        )
    except push.BleLinkBusy as exc:
        _print_unreachable_refusal(exc, busy=True, action="pushed")
        return 2

    if result.wiped:
        print("⚠ The Pi wiped its Readings — it was coupled to a different Desktop (ADR-0006).")
        print("Re-pushing this Desktop's archive.")
    print(f"Batch: {result.sent} sent, {result.failed} failed.")
    return 0 if result.ok else 1


def _require_paired(cfg: config.Configuration) -> Optional[int]:
    """The "Not paired" refusal shared by every action command that needs a
    stored `pi.address` (§4.6) -- `None` means it is fine to proceed."""
    if cfg.pi_address is not None:
        return None
    print("✗ Not paired — no Pi address is recorded.", file=sys.stderr)
    print("Nothing was queued. Pair this Desktop with a Pi first.", file=sys.stderr)
    return 2


def _ack_refusal(ack: Optional[dict]) -> Optional[int]:
    """The refusal shared by every command that sends a Command Payload and
    gets back either no Ack or an error one -- `None` means the Ack was ok."""
    if ack is not None and ack.get("status") == "ok":
        return None
    reason = (ack or {}).get("reason", "no Ack")
    print(f"✗ Refused — {reason}.", file=sys.stderr)
    return 1


async def _repush_after_wipe(cfg: config.Configuration, lock_wait_s: float) -> int:
    """Re-pushes the whole archive after a successful `wipe` Ack. A busy
    link here is reported, not left to crash the CLI with an unhandled
    exception — the wipe itself already succeeded, so this only affects
    when the re-push happens, not whether the wipe did."""
    store = usage.open_store(cfg.paths_store)
    try:
        usage.clear_pushed_marks(store)
    finally:
        store.close()
    try:
        result = await push.run_batch_pass(
            str(cfg.paths_store),
            cfg.paths_projects_root,
            settings=push.settings_from_config(cfg),
            pi_address=cfg.pi_address,
            lock_wait_s=lock_wait_s,
        )
    except push.BleLinkBusy as exc:
        print(f"Wiped, but could not re-push yet — {exc}.", file=sys.stderr)
        print("Re-run  python desktop/cli.py push  once the link is free.", file=sys.stderr)
        return 2
    print(f"Batch: {result.sent} sent, {result.failed} failed.")
    return 0 if result.ok else 1


async def cmd_push(cfg: config.Configuration, args: argparse.Namespace, *, lock_wait_s: float = push.CLI_LOCK_WAIT_S) -> int:
    if (code := _require_paired(cfg)) is not None:
        return code
    try:
        result = await push.run_batch_pass(
            str(cfg.paths_store),
            cfg.paths_projects_root,
            settings=push.settings_from_config(cfg),
            pi_address=cfg.pi_address,
            lock_wait_s=lock_wait_s,
        )
    except push.BleLinkBusy as exc:
        _print_unreachable_refusal(exc, busy=True)
        return 2
    except Exception as exc:  # noqa: BLE001 - no reply from the paired Pi
        _print_unreachable_refusal(exc, busy=False)
        return 2

    print(f"Batch: {result.sent} sent, {result.failed} failed.")
    # Best-effort (§7.2): re-assertion riding this same connection may have
    # failed independently of the Batch, and `BatchResult` carries no field
    # for that -- `write_settings` already prints its own failure line when
    # it happens, so this is deliberately phrased as "attempted", not "done".
    print(f"Settings re-assertion attempted: {PI_PROJECTED_SETTING} = {cfg.pi_idle_keepalive_s}.")
    return 0 if result.ok else 1


async def cmd_redraw(cfg: config.Configuration, args: argparse.Namespace, *, lock_wait_s: float = push.CLI_LOCK_WAIT_S) -> int:
    if (code := _require_paired(cfg)) is not None:
        return code

    did = push.desktop_id()
    try:
        ack = await _send_command("redraw", did, cfg.pi_address, cfg, lock_wait_s=lock_wait_s)
    except push.BleLinkBusy as exc:
        _print_unreachable_refusal(exc, busy=True)
        return 2
    except Exception as exc:  # noqa: BLE001 - no reply from the paired Pi
        _print_unreachable_refusal(exc, busy=False)
        return 2

    if (code := _ack_refusal(ack)) is not None:
        return code

    if ack.get("drawn"):
        print("Drawn.")
        return 0

    remaining = ack.get("floor_remaining_s")
    print(f"Queued on the Pi. It will draw in {_dur(remaining)}.")
    print(
        f"ADR-0008 gates every draw at {config.REDRAW_FLOOR_S}s, so the Pi holds this "
        "rather than overriding the floor. Nothing further to do."
    )
    return 0


async def cmd_wipe(
    cfg: config.Configuration,
    args: argparse.Namespace,
    confirm_fn: Callable[[str], str] = input,
    *,
    lock_wait_s: float = push.CLI_LOCK_WAIT_S,
) -> int:
    if (code := _require_paired(cfg)) is not None:
        return code

    short = _short_id(cfg.pi_address)
    store = usage.open_store(cfg.paths_store)
    try:
        summary = usage.pushed_summary(store)
    finally:
        store.close()

    print(f"This deletes all {summary.readings} Readings on the Pi, then re-pushes them from here.")
    print("The Pi will show the empty frame until the first Batch lands.")
    typed = confirm_fn(f"Type the Pi's short id to confirm [{short}]: ")
    if typed.strip() != short:
        print("✗ Confirmation did not match. Nothing was sent.", file=sys.stderr)
        return 3

    did = push.desktop_id()
    try:
        ack = await _send_command("wipe", did, cfg.pi_address, cfg, lock_wait_s=lock_wait_s)
    except push.BleLinkBusy as exc:
        _print_unreachable_refusal(exc, busy=True)
        return 2
    except Exception as exc:  # noqa: BLE001 - no reply from the paired Pi
        _print_unreachable_refusal(exc, busy=False)
        return 2

    if (code := _ack_refusal(ack)) is not None:
        return code

    print("Wiped. Re-pushing the archive.")
    return await _repush_after_wipe(cfg, lock_wait_s)


def cmd_restart(run_fn: Callable[..., Any] = subprocess.run) -> int:
    ok, detail = _restart_service(run_fn=run_fn)
    if not ok:
        print(f"✗ Failed to restart {SERVICE_NAME}: {detail}", file=sys.stderr)
        return 3
    print(f"Restarted {SERVICE_NAME}.")
    return 0


# ---------------------------------------------------------------------------
# Argument parsing and dispatch
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python desktop/cli.py", description=__doc__)
    parser.add_argument("--config", metavar="PATH", help="Override the Configuration store location (spec §3.2).")
    parser.add_argument("--store", metavar="PATH", help="Override the usage archive store location (spec §4.5).")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON (status only, spec §9.5).")

    sub = parser.add_subparsers(dest="command", required=True)

    status_p = sub.add_parser("status", help="Is it working? (spec §9.2)")
    status_p.add_argument("--brief", action="store_true", help="One line when fine, detail only for what is wrong.")

    config_p = sub.add_parser("config", help="List or change Configuration (spec §9.3).")
    config_sub = config_p.add_subparsers(dest="config_action")
    get_p = config_sub.add_parser("get", help="Print one key's current value.")
    get_p.add_argument("key")
    set_p = config_sub.add_parser("set", help="Write one key, validated against §4.")
    set_p.add_argument("key")
    set_p.add_argument("value")

    sub.add_parser("pair", help="Scan, record pi.address, and push the archive (spec §9.4).")
    push_p = sub.add_parser("push", help="Run a Batch over one connection now (spec §9.4).")
    push_p.add_argument("--now", action="store_true", help="No effect — push always runs immediately.")
    sub.add_parser("redraw", help="Send the redraw verb (spec §9.4).")
    sub.add_parser("wipe", help="Send the wipe verb, after typed confirmation (spec §9.4).")
    sub.add_parser("restart", help=f"systemctl --user restart {SERVICE_NAME} (spec §9.4).")

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        cfg = config.resolve(cli_config_path=args.config, cli_store_path=args.store)
    except config.ConfigVersionError as exc:
        print(f"Refusing to run: {exc}", file=sys.stderr)
        return 1

    cfg_path = config.resolve_config_path(args.config)

    if args.command == "restart":
        return cmd_restart()

    dispatch: dict[str, Callable[[], Any]] = {
        "config": lambda: cmd_config(cfg, cfg_path, args),
        "status": lambda: cmd_status(cfg, args),
        "pair": lambda: cmd_pair(cfg, cfg_path, args),
        "push": lambda: cmd_push(cfg, args),
        "redraw": lambda: cmd_redraw(cfg, args),
        "wipe": lambda: cmd_wipe(cfg, args),
    }
    try:
        # The usage archive store (spec §4.5) is opened lazily, inside each
        # command -- unlike the Configuration store above, which every
        # command needs before it can do anything. A version mismatch there
        # surfaces here, the way push.py's own `main()` catches it around
        # `_async_main` rather than around `config.resolve`.
        return asyncio.run(dispatch[args.command]())
    except usage.StoreVersionError as exc:
        print(f"Refusing to run: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
