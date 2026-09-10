#!/usr/bin/env python3
"""PROTOTYPE — throwaway. Ticket #76 on map #70. Do not import this, do not
extend it, do not merge it to `dev`. It lives on `prototype/cli` and is a
primary source for the spec, nothing else.

It answers one question: **what does a human type, and what comes back?** —
and in particular #76's own framing, how to show *one* answer to "is it
working?" with #74's six comparisons behind it.

No dependencies, no BLE, no SQLite. Every number below is fake but shaped
like the real thing: the seven status fields and six verdict comparisons are
#74's, the 17 Configuration keys and 10 Tier 3 invariants are #72's, the two
verbs are #75's, and the refusal behaviour is #79/ADR-0011's.

    python3 desktop/prototype-cli.py                      # the whole tour
    python3 desktop/prototype-cli.py status --variant B
    python3 desktop/prototype-cli.py status --scenario stuck --variant A
    python3 desktop/prototype-cli.py status --json

Scenarios: ok unreachable busy never-paired diverged stuck restarted
Variants (status only): A evidence  B ledger  C exceptions
"""

from __future__ import annotations

import argparse
import json
import sys

OK, BAD, UNKNOWN, NONE = "ok", "bad", "unknown", "none"
# The third severity, settled by the maintainer on this prototype: a fact
# worth seeing that is NOT a fault and never drives the headline verdict.
# A Pi that rebooted four minutes ago and is drawing fine is not "broken".
NOTE = "note"

# --------------------------------------------------------------------------
# Fake state. One dict per scenario: what the Desktop knows + what the Pi said.
# --------------------------------------------------------------------------

DESKTOP = {
    "desktop_id": "9f2c1ab34d5e6f70",
    "service": "running",
    "service_uptime_s": 273_600,
    "last_batch": "09:04 today",
    "last_push_elapsed_s": 14_820,
    "sent_readings": 312,
    "min_pushed_date": "2026-08-01",
    "schema_expected": 1,
    "idle_keepalive_s": 86_400,
    "redraw_floor_s": 300,
}

SCENARIOS = {
    "ok": {
        "reach": OK,
        "pi": {"frame": "historic", "since_redraw_s": 143, "panel": "ok",
               "readings": 312, "coverage_start": "2026-08-01",
               "uptime_s": 110_000, "schema_version": 1, "wiped": False},
    },
    "unreachable": {
        "reach": UNKNOWN,
        "reach_detail": "no Pi advertising 6e400001-… within 10.0s",
        "last_seen": "09:04 today (4h 7m ago)",
        "last_seen_readings": 312,
        "pi": None,
    },
    "busy": {
        "reach": UNKNOWN,
        "reach_detail": "the push service is mid-Batch and holds the link",
        "busy": True,
        "last_seen": "09:04 today (4h 7m ago)",
        "last_seen_readings": 312,
        "pi": None,
    },
    "never-paired": {"reach": NONE, "pi": None},
    "diverged": {
        "reach": OK,
        "pi": {"frame": "historic", "since_redraw_s": 201, "panel": "ok",
               "readings": 265, "coverage_start": "2026-08-08",
               "uptime_s": 96_000, "schema_version": 1, "wiped": False},
    },
    "stuck": {
        "reach": OK,
        "pi": {"frame": "gauge", "since_redraw_s": 15_120, "panel": "stuck",
               "readings": 312, "coverage_start": "2026-08-01",
               "uptime_s": 110_000, "schema_version": 1, "wiped": False},
    },
    "restarted": {
        "reach": OK,
        "pi": {"frame": "startup", "since_redraw_s": 22, "panel": "ok",
               "readings": 312, "coverage_start": "2026-08-01",
               "uptime_s": 240, "schema_version": 1, "wiped": False},
    },
}


def dur(s: int) -> str:
    if s is None:
        return "never"
    d, r = divmod(int(s), 86_400)
    h, r = divmod(r, 3600)
    m, sec = divmod(r, 60)
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {sec}s"
    return f"{sec}s"


# --------------------------------------------------------------------------
# The verdict — #74's six comparisons, computed on the Desktop.
# --------------------------------------------------------------------------

def verdict(sc: dict) -> tuple[str, str, list[tuple[str, str, str, str]]]:
    """Returns (state, headline, rows). rows = (name, mark, desktop, pi/detail)."""
    if sc["reach"] == NONE:
        return NONE, "Not paired.", []
    if sc["reach"] == UNKNOWN:
        # Both cases are Unreachable, but they must NOT share a headline:
        # "the Pi is unreachable" reads as a fault when the Pi is in fact
        # right there and this Desktop is the thing occupying the link.
        if sc.get("busy"):
            return UNKNOWN, "Can't tell yet — the link is busy.", []
        return UNKNOWN, "Can't tell — the Pi is unreachable.", []

    pi, d = sc["pi"], DESKTOP
    rows = []

    rows.append(("Coupled", OK if not pi["wiped"] else BAD,
                 d["desktop_id"][:8] + "…", "replied, not wiped"))

    same = pi["readings"] == d["sent_readings"]
    rows.append(("Readings", OK if same else BAD, str(d["sent_readings"]),
                 str(pi["readings"]) + ("" if same else
                     f"  ({d['sent_readings'] - pi['readings']} missing)")))

    # Coverage is only its own fault when Readings agree. A short coverage
    # alongside missing Readings is the SAME fact (a lost Batch), so it
    # demotes to a note rather than headlining a second time.
    cov = pi["coverage_start"] == d["min_pushed_date"]
    cov_mark = OK if cov else (NOTE if not same else BAD)
    rows.append(("Coverage", cov_mark, d["min_pushed_date"],
                 pi["coverage_start"] + ("" if cov else "  (short)")))

    bound = d["idle_keepalive_s"] + d["redraw_floor_s"]
    alive = pi["panel"] == "ok" and pi["since_redraw_s"] <= bound
    if pi["panel"] != "ok":
        detail = {
            "stuck": f'watchdog fired; last accepted draw {dur(pi["since_redraw_s"])} ago',
            "unavailable": "a render raised — nothing is reaching the glass",
            "never": "nothing drawn since the Pi started",
        }[pi["panel"]]
    elif not alive:
        detail = f'last draw {dur(pi["since_redraw_s"])} ago, over {dur(bound)}'
    else:
        detail = f'{pi["frame"]} frame, drew {dur(pi["since_redraw_s"])} ago'
    rows.append(("Panel", OK if alive else BAD, "—", detail))

    # A restart is a fact, not a fault: #74 lists it as a comparison, which
    # is not the same as calling it broken.
    fresh = pi["uptime_s"] < d["last_push_elapsed_s"]
    rows.append(("Restarted", NOTE if fresh else OK,
                 f'last push {dur(d["last_push_elapsed_s"])} ago',
                 f'up {dur(pi["uptime_s"])}' + ("  (restarted since)" if fresh else "")))

    img = pi["schema_version"] == d["schema_expected"]
    rows.append(("Image", OK if img else BAD, f'schema {d["schema_expected"]}',
                 f'schema {pi["schema_version"]}'))

    # Only a BAD row can change the headline. Notes are shown, never counted.
    bad = [r for r in rows if r[1] == BAD]
    notes = [r for r in rows if r[1] == NOTE]
    if not bad:
        tail = f" ({len(notes)} note{'s' if len(notes) > 1 else ''})" if notes else ""
        return OK, f"Working.{tail}", rows

    # Name the story, do not count the checks. "2 checks failed" is a worse
    # answer than "the Pi is missing 47 Readings" — and Readings+Coverage
    # failing together is ONE fact (a lost Batch), not two. So: fixed
    # precedence, headline the most explanatory failure, and only mention a
    # count when the rest are genuinely a separate story.
    order = ["Coupled", "Image", "Panel", "Readings", "Coverage", "Restarted"]
    primary = min((r[0] for r in bad), key=order.index)
    why = {"Panel": "the panel is stuck",
           "Readings": f'the Pi is missing {d["sent_readings"] - pi["readings"]} Readings',
           "Coverage": "the Pi's coverage is short",
           "Restarted": "the Pi restarted since the last push",
           "Image": "the Pi is running an older image",
           "Coupled": "the Pi is coupled to a different Desktop"}[primary]
    related = {"Readings", "Coverage"}
    rest = [r[0] for r in bad if r[0] != primary and
            not ({primary, r[0]} <= related)]
    tail = f" (+{len(rest)} more)" if rest else ""
    return BAD, f"Not working — {why}.{tail}", rows


MARK = {OK: "✓", BAD: "✗", UNKNOWN: "?", NONE: "–", NOTE: "·"}


def unreachable_why(sc: dict) -> list[str]:
    out = ["     " + sc["reach_detail"] + "."]
    if sc.get("busy"):
        out += ["     The Pi is there; this Desktop is already talking to it.",
                "     Retry in a moment — a Batch takes about two minutes."]
    else:
        out += ["     Normal when it is powered off or out of range. Not a fault."]
    return out


def unreachable_lastseen(sc: dict) -> list[str]:
    return [f'  Pi        last seen {sc["last_seen"]}',
            f'            holding {sc["last_seen_readings"]} Readings then']


# --------------------------------------------------------------------------
# VARIANT A — verdict headline, then all six comparisons as evidence.
# --------------------------------------------------------------------------

def status_a(sc: dict) -> str:
    state, headline, rows = verdict(sc)
    L = ["", f"  {MARK[state]}  {headline}", ""]

    if state == NONE:
        return "\n".join(L + [
            "     This Desktop has never been coupled to a Pi.",
            "     Run  zeropi pair  with the Pi powered on and in range.", ""])

    if state == UNKNOWN:
        L += unreachable_why(sc) + [""]
        L += [f'  Desktop   zeropi-push {DESKTOP["service"]}, up {dur(DESKTOP["service_uptime_s"])}',
              f'            last Batch {DESKTOP["last_batch"]}, {DESKTOP["sent_readings"]} Readings sent']
        L += unreachable_lastseen(sc)
        back = "in a moment" if sc.get("busy") else "when the Pi is back"
        L += ["", f"  Nothing was queued. Re-run {back}.", ""]
        return "\n".join(L)

    L += [f'  Desktop   zeropi-push {DESKTOP["service"]}, up {dur(DESKTOP["service_uptime_s"])}',
          f'            last Batch {DESKTOP["last_batch"]}, {DESKTOP["sent_readings"]} Readings sent']

    pi = sc["pi"]
    L += [f'  Pi        replied in 1.2s, up {dur(pi["uptime_s"])}', ""]
    for name, mark, _left, detail in rows:
        L.append(f"  {name:<10} {MARK[mark]}  {detail}")
    L.append("")
    return "\n".join(L)


# --------------------------------------------------------------------------
# VARIANT B — a two-column ledger: what I sent vs what it holds.
# --------------------------------------------------------------------------

def status_b(sc: dict) -> str:
    state, headline, rows = verdict(sc)
    L = ["", f'  zeropi status{" " * 34}{MARK[state]} {headline}', ""]

    if state == NONE:
        return "\n".join(L + ["     Never paired. Run  zeropi pair  to couple this Desktop.", ""])
    if state == UNKNOWN:
        return "\n".join(L + unreachable_why(sc) + [""] + unreachable_lastseen(sc) +
                         ["", "  Nothing was queued. Re-run "
                         + ("in a moment." if sc.get("busy") else "when the Pi is back."), ""])

    L += [f'  {"":<12}{"Desktop":<24}{"Pi":<32}', f'  {"-" * 70}']
    for name, mark, left, right in rows:
        L.append(f"  {name.lower():<12}{left:<24}{right:<32}{MARK[mark]}")
    L.append("")
    return "\n".join(L)


# --------------------------------------------------------------------------
# VARIANT C — one line when fine; detail only for what is wrong.
# --------------------------------------------------------------------------

def status_c(sc: dict) -> str:
    state, headline, rows = verdict(sc)
    if state == NONE:
        return "\n  – Never paired. Run  zeropi pair.\n"
    if state == UNKNOWN:
        tail = "retry in a moment" if sc.get("busy") else "not a fault; nothing queued"
        return (f'\n  ? Can\'t tell — {sc["reach_detail"]}.\n'
                f'    Last seen {sc["last_seen"]} holding '
                f'{sc["last_seen_readings"]} Readings; {tail}.\n')

    pi = sc["pi"]
    if state == OK:
        out = (f'\n  ✓ Working. Pi up {dur(pi["uptime_s"])}, {pi["frame"]} frame drawn '
               f'{dur(pi["since_redraw_s"])} ago,\n'
               f'    {pi["readings"]} Readings agreed from {pi["coverage_start"]}.\n')
        for n, m, _l, d_ in rows:
            if m == NOTE:
                out += f'    · {n.lower()}: {d_}\n'
        return out

    L = ["", f"  {MARK[BAD]} {headline}", ""]
    for name, mark, _left, detail in rows:
        if mark == BAD:
            L.append(f"    {name.lower()}: {detail}")
    if state == BAD and any(r[0] == "Panel" and r[1] == BAD for r in rows):
        L += ["", "    BLE is serving, Acks succeed and Readings persist.",
              "    Only the glass is frozen."]
    good = sum(1 for r in rows if r[1] == OK)
    L += ["", f"    Everything else checks out ({good} of {len(rows)}).", ""]
    return "\n".join(L)


def status_json(sc: dict) -> str:
    state, headline, rows = verdict(sc)
    return json.dumps({
        "verdict": {"ok": None if state in (UNKNOWN, NONE) else state == OK,
                    "state": state, "headline": headline},
        "reachable": sc["reach"] == OK,
        "desktop": DESKTOP,
        "pi": sc["pi"],
        "checks": [{"name": n.lower(), "severity": m,
                    "ok": None if m == NOTE else m == OK,
                    "detail": d} for n, m, _l, d in rows],
    }, indent=2)


# --------------------------------------------------------------------------
# config — #72's inventory, three Tiers, Tier 3 shown rather than hidden.
# --------------------------------------------------------------------------

TIER1 = [
    ("paths.projects_root", "~/.claude/projects"),
    ("paths.store", "~/.local/share/zeropi-display/usage-archive.db"),
    ("paths.rate_limits", "~/.local/state/zeropi-display/rate-limits.json"),
    ("paths.sessions_dir", "~/.claude/sessions"),
    ("pricing.overlay", "{}"),
    ("pricing.free_models", "[]"),
    ("pricing.web_search_usd_per_request", "0.01"),
    ("context_window.overlay", "{}"),
    ("batch.scheduled_hour", "4"),
]
TIER2 = [
    ("service.poll_interval_s", "30.0", "5.0 .. 150.0"),
    ("service.gauge_throttle_s", "120.0", "30.0 .. 150.0"),
    ("gauge.stale_threshold_s", "300", "60 .. 300"),
    ("batch.catchup_threshold_s", "86400", "3600 .. 604800"),
    ("usage.window_days", "7", "5 .. 30"),
    ("push.scan_timeout_s", "10.0", "1.0 .. 60.0"),
    ("push.ack_timeout_s", "10.0", "1.0 .. 60.0"),
    ("pi.idle_keepalive_s", "86400", "3600 .. 604800   → Pi"),
]
TIER3 = [
    ("REDRAW_FLOOR_S", "300", "ADR-0008", "panel rated for one update per 180 s"),
    ("GAUGE_EXPIRY_S", "300", "ADR-0010", "amended 2026-09-09 on measurement (#55)"),
    ("MAX_PAYLOAD_BYTES", "512", "ADR-0001", "re-confirmed by bisection (#67)"),
    ("notify budget", "min(512, ATT_MTU-5)", "#73/#78", "measured on the wire; no named constant today"),
    ("SERVICE_UUID  +2", "—", "—", "must match both ends; changing one breaks discovery"),
    ("text floor", "13 px", "spec §4", "⚠ not monotone — re-collides at 14 px"),
    ("PANEL_W, PANEL_H", "250, 122", "hardware", ""),
    ("WATCHDOG_TIMEOUT_S", "30.0", "spec §8", "stops us caring about the thread; cannot recover it"),
    ("FONT_DIR", "DejaVu", "#66", "the 13 px floor was verified with DejaVu specifically"),
    ("historic pitch", "5 rows @ 20 px", "render.py", "floors usage.window_days"),
]


def cmd_config() -> str:
    L = ["", "  Tier 1 — deployment facts (freely editable)", ""]
    for k, v in TIER1:
        L.append(f"    {k:<38}{v}")
    L += ["", "  Tier 2 — policy (editable within a validated range)", ""]
    for k, v, r in TIER2:
        L.append(f"    {k:<38}{v:<12}{r}")
    L += ["", "  Tier 3 — verified invariants. NOT settings; shown so you can see them.", ""]
    for k, v, adr, why in TIER3:
        L.append(f"    {k:<38}{v:<22}{adr}")
        if why:
            L.append(f'    {"":<38}{why}')
    L += ["", "  One Tier 2 key is projected to the Pi as a Setting (→ Pi).",
          "  Changing any key needs a restart:  zeropi restart", ""]
    return "\n".join(L)


def cmd_config_set_bad_range() -> str:
    return """
  $ zeropi config set service.gauge_throttle_s 600

  ✗ Refused. 600.0 is outside 30.0 .. 150.0.

    The ceiling is GAUGE_EXPIRY_S / 2 — derived from a verified invariant
    (ADR-0008, amended by #55), not typed in here. A replacement Gauge must
    land well before expiry, or expiry stops meaning "the Desktop is gone"
    and starts firing in normal operation.

    Nothing was written.
"""


def cmd_config_set_tier3() -> str:
    return """
  $ zeropi config set REDRAW_FLOOR_S 120

  ✗ REDRAW_FLOOR_S is not a setting.

    It is a verified invariant fixed by ADR-0008: the panel is rated for one
    update per 180 s and 300 s is the operating point established on hardware.
    Lowering it here would invalidate that run, so it is not stored as
    Configuration at all — it is a constant in the code, shown read-only.

    See  zeropi config  (Tier 3).
"""


# --------------------------------------------------------------------------
# verbs — #75's two, plus #74's status verb. #79/ADR-0011 governs refusal.
# --------------------------------------------------------------------------

CMD_SAMPLES = {
"redraw (queued)": """
  $ zeropi redraw

  Queued on the Pi. It will draw in 3m 22s.

    ADR-0008 gates every draw at 300 s and the last one was 98 s ago, so the
    Pi holds this rather than overriding the floor. Nothing further to do.
""",
"redraw (unreachable)": """
  $ zeropi redraw

  ✗ Refused — the Pi is unreachable (no Pi advertising within 10.0s).

    Not queued, here or anywhere. A redraw delivered later is not what you
    asked for: the panel will have redrawn on its own clock by then.
    Re-run when the Pi is back.
""",
"wipe (confirm)": """
  $ zeropi wipe

  This deletes all 312 Readings on the Pi, then re-pushes them from here.
  The Pi will show the empty frame until the first Batch lands.

  Type the Pi's short id to confirm [e45f01]:
""",
"wipe (unreachable)": """
  $ zeropi wipe

  ✗ Refused — the Pi is unreachable (no Pi advertising within 10.0s).

    Not queued. A wipe is the one destructive verb, and a queued one fires at
    whatever Pi answers next — the reason this Pi is unreachable may be the
    reason not to wipe it (powered down for a hand-off, or already coupled to
    another Desktop). Re-run with the Pi in range. [ADR-0011]
""",
"pair": """
  $ zeropi pair

  Scanning… found e4:5f:01:9c:2d:aa  (zeropi-display)
  Coupled. This Desktop is 9f2c1ab3…

  ⚠ The Pi wiped its 84 Readings — it was coupled to a different Desktop
    (ADR-0006). Re-pushing this Desktop's archive.

  Batch: 312 Readings … 312 sent, 0 failed.  Panel redrawn.
""",
"push --now": """
  $ zeropi push --now

  Batch: 12 pending Readings over one connection.
    12 sent, 0 failed. Coverage now 2026-08-01 .. 2026-09-10.
  Settings re-asserted: pi.idle_keepalive_s = 86400.
  Panel: historic frame accepted.
""",
"failure: over-budget Ack": """
  $ zeropi status

  ✗ The Pi's reply was truncated at 512 bytes.

    This is NOT malformed JSON, which is what the underlying error says
    (`malformed ack from Pi: … column 513`). BlueZ clips an oversized
    notification and returns success, so the reply arrives short and silent.
    The Pi is almost certainly fine. [#78]
""",
"failure: Gauge expired on arrival": """
  $ zeropi push --now

  Gauge not shown: the snapshot was already 312 s old when it reached the Pi
  (expiry 300 s), so the panel kept the Historic View.

    This is claude-hud's snapshot being stale, not a slow link — Gauge Age
    counts the snapshot's own age too, not just time since arrival. [#66, #74]
""",
}


def main() -> None:
    p = argparse.ArgumentParser(description="PROTOTYPE CLI for #76 — fake data.")
    p.add_argument("command", nargs="?", default="tour",
                   choices=["tour", "status", "config", "verbs"])
    p.add_argument("--scenario", default="ok", choices=list(SCENARIOS))
    p.add_argument("--variant", default="A", choices=["A", "B", "C"])
    p.add_argument("--json", action="store_true")
    p.add_argument("--brief", action="store_true",
                   help="variant C: one line when fine, detail only when not")
    a = p.parse_args()

    render = {"A": status_a, "B": status_b, "C": status_c}["C" if a.brief else a.variant]

    if a.command == "status":
        print(status_json(SCENARIOS[a.scenario]) if a.json else render(SCENARIOS[a.scenario]))
        return
    if a.command == "config":
        print(cmd_config()); print(cmd_config_set_bad_range()); print(cmd_config_set_tier3())
        return
    if a.command == "verbs":
        for title, body in CMD_SAMPLES.items():
            print(f"  ── {title} " + "─" * (66 - len(title)))
            print(body)
        return

    for variant in ("A", "B", "C"):
        r = {"A": status_a, "B": status_b, "C": status_c}[variant]
        print("=" * 74)
        print(f"  VARIANT {variant}")
        print("=" * 74)
        for name in ("ok", "stuck", "diverged", "unreachable", "never-paired"):
            print(f"\n  ── scenario: {name} " + "─" * (56 - len(name)))
            print(r(SCENARIOS[name]))


if __name__ == "__main__":
    sys.exit(main())
