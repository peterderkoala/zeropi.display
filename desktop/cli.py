"""Desktop: `cli.py` (spec §9) — the command-line renderer of the
Management Surface.

`python desktop/cli.py <command>`. **Not** a `zeropi` console script (§9.1) —
packaging is deliberately deferred to when the web UI brings a service that
must be installed anyway. `push.py`'s own CLI stays exactly as it is.

Five commands beyond `status`/`config`, all imperative and all refused
outright rather than queued when the Pi is Unreachable (ADR-0011): `pair`,
`push`, `redraw`, `wipe`, `restart`. The actions themselves live in
`operations.py` (web-ui spec §4), shared with the web UI; this module only
turns their results into text and §9.6's exit codes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from typing import Any, Callable, Optional

import config
import operations
import push
import usage
import verdict
from operations import PI_PROJECTED_SETTING, SERVICE_NAME, TIER3, Outcome, Refused

EXIT_CODE = {Outcome.OK: 0, Outcome.FAULT: 1, Outcome.CANT_TELL: 2, Outcome.REFUSED: 3}

# Kept under their old names: the tests drive them directly.
_service_info = operations.service_info


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


def _err(*lines: str) -> None:
    for line in lines:
        print(line, file=sys.stderr)


def _print_refusal(r: Refused) -> int:
    """§4.2's refusals, in the CLI's own words (byte-identical to #86's)."""
    if r.kind == "pair_only":
        _err(f"✗ {operations.PI_ADDRESS_KEY} is written only by  python desktop/cli.py pair.", "Nothing was written.")
    elif r.kind == "not_a_setting":
        _err(
            f"✗ {r.message}",
            "",
            f"    {r.rationale}",
            "    It is not stored as Configuration at all — it is a constant in the code, shown read-only.",
            "",
            "    See  python desktop/cli.py config  (Tier 3).",
        )
    elif r.kind == "out_of_range":
        _err(f"✗ Refused. {r.message}")
        if r.rationale:
            _err("", f"    {r.rationale}")
        _err("", "    Nothing was written.")
    elif r.kind == "not_paired":
        _err(f"✗ {r.message}", "Nothing was queued. Pair this Desktop with a Pi first.")
    else:  # unknown_key, confirm_mismatch
        _err(f"✗ {r.message}")
    return EXIT_CODE[r.outcome]


def _print_unreachable_refusal(u: operations.Unreachable, *, action: str = "queued") -> None:
    if u.reason is verdict.Reach.BUSY:
        _err(
            f"✗ Refused — the link is busy ({u.detail}).",
            f"Not {action}. Retry in a moment — a Batch takes about two minutes.",
        )
    else:
        _err(f"✗ Refused — the Pi is unreachable ({u.detail}).", f"Not {action}. Re-run when the Pi is back.")


def _print_batch(batch: push.BatchResult) -> None:
    print(f"Batch: {batch.sent} sent, {batch.failed} failed.")


# ---------------------------------------------------------------------------
# §9.2 `status`
# ---------------------------------------------------------------------------


# §8.2's three severities, rendered — kept local to the CLI (verdict.py's
# own GLYPHS dict is keyed by §8.1's four *states*, a different vocabulary).
SEVERITY_GLYPH = {
    verdict.Severity.OK: "✓",
    verdict.Severity.FAIL: "✗",
    verdict.Severity.NOTE: "·",
}


def _ago(seconds: Optional[float]) -> str:
    """`_dur` as a point in the past: "4m 7s ago", or plain "never" -- not
    "never ago" (found on hardware by #87)."""
    return "never" if seconds is None else f"{_dur(seconds)} ago"


def _desktop_lines(facts: verdict.DesktopFacts, service_info: operations.ServiceInfo) -> list[str]:
    uptime = f", up {_dur(service_info.uptime_s)}" if service_info.uptime_s is not None else ""
    return [
        f"  Desktop   {SERVICE_NAME} {service_info.state}{uptime}",
        f"            last Batch {_ago(facts.seconds_since_last_push)}, {facts.readings} Readings sent",
    ]


def _render_status_full(r: operations.StatusResult, service_info: operations.ServiceInfo) -> str:
    v, facts, status = r.verdict, r.facts, r.status
    lines = ["", f"  {v.glyph}  {v.headline}", ""]

    if v.state is verdict.State.NOT_PAIRED:
        lines += [f"     {v.advice}", ""]
        return "\n".join(lines)

    if v.state is verdict.State.CANT_TELL:
        if r.error_detail:
            lines += [f"     {r.error_detail}."]
        if r.reach is verdict.Reach.ABSENT:
            lines.append("     Normal when it is powered off or out of range. Not a fault.")
        lines.append("")
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


def _render_status_brief(r: operations.StatusResult) -> str:
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


async def cmd_status(cfg: config.Configuration, args: argparse.Namespace, *, lock_wait_s: float = push.CLI_LOCK_WAIT_S) -> int:
    r = await operations.status(cfg, lock_wait_s=lock_wait_s)
    if args.json:
        print(json.dumps(operations.status_json(r, _service_info()), indent=2))
    elif args.brief:
        print(_render_status_brief(r))
    else:
        print(_render_status_full(r, _service_info()))
    return EXIT_CODE[r.outcome]


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
    try:
        got = operations.config_get(conn, key)
    except Refused as r:
        return _print_refusal(r)
    if got.tier3 is not None:
        print(f"{key} = {got.value}  ({got.tier3.fixed_by}, read-only — not a Configuration key)")
    else:
        print(f"{key} = {got.value}")
    return 0


async def _config_set(conn, cfg: config.Configuration, key: str, raw_value: str) -> int:
    try:
        value = operations.write_config(conn, key, raw_value)
    except Refused as r:
        if r.kind == "unknown_key":
            _err(f"✗ {r.message} Nothing was written.")
            return EXIT_CODE[r.outcome]
        return _print_refusal(r)

    if operations.is_pi_push(cfg, key):
        result = await operations.push_setting(cfg, value)
        if result.error is not None:
            print(f"Set {key} = {value}.")
            print(f"Could not push it to the Pi yet — {result.error}.")
            print("It will apply on the next successful connection (§7.2).")
        else:
            print(f"Set {key} = {value}. Pushed to the Pi — it applies immediately (§6.1).")
        return EXIT_CODE[result.outcome]

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
    r = await operations.pair(cfg, cfg_path, lock_wait_s=lock_wait_s)
    if r.address is None:
        _err(
            f"✗ Refused — the Pi is unreachable ({r.unreachable.detail}).",
            "Not paired. Pairing needs the Pi powered on and in range.",
        )
    elif r.unreachable is not None:
        _print_unreachable_refusal(r.unreachable, action="pushed")
    else:
        if r.batch.wiped:
            print("⚠ The Pi wiped its Readings — it was coupled to a different Desktop (ADR-0006).")
            print("Re-pushing this Desktop's archive.")
        _print_batch(r.batch)
    return EXIT_CODE[r.outcome]


async def cmd_push(cfg: config.Configuration, args: argparse.Namespace, *, lock_wait_s: float = push.CLI_LOCK_WAIT_S) -> int:
    try:
        r = await operations.push_batch(cfg, lock_wait_s=lock_wait_s)
    except Refused as refusal:
        return _print_refusal(refusal)
    if r.unreachable is not None:
        _print_unreachable_refusal(r.unreachable)
        return EXIT_CODE[r.outcome]

    _print_batch(r.batch)
    # Best-effort (§7.2): re-assertion riding this same connection may have
    # failed independently of the Batch. `write_settings` already prints its
    # own failure line (`batch.settings_ok` is the page's copy, web-ui §4.5),
    # so this stays phrased as "attempted", not "done".
    print(f"Settings re-assertion attempted: {PI_PROJECTED_SETTING} = {r.idle_keepalive_s}.")
    return EXIT_CODE[r.outcome]


def _print_command_failure(c: operations.CommandResult) -> int:
    """A Command with no ok Ack: Unreachable, or an error/missing Ack."""
    if c.unreachable is not None:
        _print_unreachable_refusal(c.unreachable)
    else:
        _err(f"✗ Refused — {c.reason}.")
    return EXIT_CODE[c.outcome]


async def cmd_redraw(cfg: config.Configuration, args: argparse.Namespace, *, lock_wait_s: float = push.CLI_LOCK_WAIT_S) -> int:
    try:
        r = await operations.redraw(cfg, lock_wait_s=lock_wait_s)
    except Refused as refusal:
        return _print_refusal(refusal)
    if r.outcome is not Outcome.OK:
        return _print_command_failure(r)

    if r.ack.get("drawn"):
        print("Drawn.")
        return 0

    remaining = r.ack.get("floor_remaining_s")
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
    try:
        operations.require_paired(cfg)
        short = operations.short_id(cfg.pi_address)
        print(f"This deletes all {operations.readings_sent(cfg)} Readings on the Pi, then re-pushes them from here.")
        print("The Pi will show the empty frame until the first Batch lands.")
        typed = confirm_fn(f"Type the Pi's short id to confirm [{short}]: ")
        r = await operations.wipe(cfg, typed, lock_wait_s=lock_wait_s)
    except Refused as refusal:
        return _print_refusal(refusal)

    if r.command.outcome is not Outcome.OK:
        return _print_command_failure(r.command)
    if r.repush_busy is not None:
        _err(
            f"Wiped, but could not re-push yet — {r.repush_busy}.",
            "Re-run  python desktop/cli.py push  once the link is free.",
        )
    else:
        _print_batch(r.repush)
    return EXIT_CODE[r.outcome]


def cmd_restart(run_fn: Callable[..., Any] = subprocess.run) -> int:
    r = asyncio.run(operations.restart(run_fn=run_fn))
    if r.error is not None:
        print(f"✗ Failed to restart {r.service}: {r.error}", file=sys.stderr)
    else:
        print(f"Restarted {r.service}.")
    return EXIT_CODE[r.outcome]


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
    web_p = sub.add_parser("web", help="Serve the web UI on 127.0.0.1 (web-ui spec §5.1).")
    web_p.add_argument("--port", type=int, default=8737, help="Port to bind (default 8737); no fallback if taken.")

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
    if args.command == "web":
        import web  # lazily: nothing outside the web module imports fastapi (web-ui §3.1)

        return web.serve(port=args.port, cfg_path=cfg_path, cli_store_path=args.store)

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
