"""PROTOTYPE — throwaway. Not the implementation. Do not merge to dev.

Answers ticket #26: does the live gauge tell the truth, and is it legible
at 250x122?

Sibling to usage_prototype.py (#18, branch prototype/usage-reader), which
covers the DAILY rows. This one covers the LIVE gauge only.

Reads, on this machine, right now:
  ~/.local/state/zeropi-display/rate-limits.json  (claude-hud snapshot, #27)
  ~/.claude/sessions/<pid>.json                   (live session registry)
  ~/.claude/projects/<enc-cwd>/<sessionId>.jsonl  (for context size)

Deliberately does NOT read ~/.claude.json (#22: never maintained while
Claude Code runs) or ~/.claude/.credentials.json (auth material).

Run:
    python3 desktop/gauge_prototype.py             # one shot
    python3 desktop/gauge_prototype.py --watch     # poll every 30s, #25's loop
    python3 desktop/gauge_prototype.py --render out.png
    python3 desktop/gauge_prototype.py --boundary  # window arithmetic

--render needs pillow; nothing else has dependencies.

Unpolished on purpose: no tests, no error handling, no abstractions.
"""

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SNAPSHOT = Path.home() / ".local/state/zeropi-display/rate-limits.json"
SESSIONS = Path.home() / ".claude/sessions"
PROJECTS = Path.home() / ".claude/projects"

SINGLE_WRITE_BUDGET = 514   # MTU 517 minus 3 bytes ATT overhead (#23)
POLL_SECONDS = 30           # #25: matches claude-hud's own write throttle
STALE_SECONDS = 300         # #24/#25: the Pi dims past this

PANEL_W, PANEL_H = 250, 122

# Context windows, #31 (docs/research/context-window-table.md). Prefix match:
# logged model ids may be date-suffixed. NOT 200K for the models that matter.
CONTEXT_WINDOW = {
    "claude-opus-5":    1_000_000,
    "claude-sonnet-5":  1_000_000,
    "claude-haiku-4-5":   200_000,
}


def window_for(model):
    for prefix, size in CONTEXT_WINDOW.items():
        if model and model.startswith(prefix):
            return size, True
    return None, False


def encode(path):
    """#18's project-dir encoding: every non-alnum becomes a dash."""
    return re.sub(r"[^a-zA-Z0-9]", "-", path)


# --- the three reads ------------------------------------------------------

def read_snapshot():
    """claude-hud's rate-limit snapshot. Absent is a real state, not zero."""
    if not SNAPSHOT.exists():
        return None
    return json.loads(SNAPSHOT.read_text())


def active_session():
    """#24's rule: the session with the most recent updatedAt.

    Note .key files live alongside the .json ones — glob narrowly.
    """
    sessions = []
    for f in SESSIONS.glob("*.json"):
        try:
            sessions.append(json.loads(f.read_text()))
        except Exception:
            pass
    if not sessions:
        return None, []
    sessions.sort(key=lambda s: s.get("updatedAt", 0), reverse=True)
    return sessions[0], sessions


def context_for(session):
    """Latest assistant entry's input + cache_creation + cache_read (#24).

    Guards against sidechain (sub-agent) entries: if one is last in the file,
    the naive rule reports the SUB-AGENT's context, not the session's.
    """
    if not session:
        return None
    jsonl = PROJECTS / encode(session["cwd"]) / f"{session['sessionId']}.jsonl"
    if not jsonl.exists():
        return None
    last = None
    skipped_sidechain = 0
    for line in jsonl.open(errors="ignore"):
        if '"type":"assistant"' not in line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("type") != "assistant":
            continue
        if r.get("isSidechain"):
            skipped_sidechain += 1
            continue
        last = r
    if not last:
        return None
    u = last.get("message", {}).get("usage", {}) or {}
    tokens = (u.get("input_tokens", 0)
              + u.get("cache_creation_input_tokens", 0)
              + u.get("cache_read_input_tokens", 0))
    model = last.get("message", {}).get("model")
    size, known = window_for(model)
    return {
        "tokens": tokens,
        "model": model,
        "window": size,
        "window_known": known,
        "pct": round(100 * tokens / size) if size else None,
        "at": last.get("timestamp"),
        "skipped_sidechain": skipped_sidechain,
    }


# --- the gauge ------------------------------------------------------------

def iso_to_dt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def gauge_state():
    snap = read_snapshot()
    sess, all_sessions = active_session()
    ctx = context_for(sess)
    now = datetime.now(timezone.utc)

    age = None
    if snap and snap.get("updated_at"):
        age = (now - iso_to_dt(snap["updated_at"])).total_seconds()

    return {
        "now": now,
        "snapshot": snap,
        "snapshot_age_s": age,
        "stale": (age is not None and age > STALE_SECONDS),
        "session": sess,
        "session_count": len(all_sessions),
        "context": ctx,
    }


def gauge_payload(st):
    """The Gauge Payload (#19's name). Field naming is a proposal, not settled.

    generated_at is claude-hud's updated_at, NOT our clock — #24 says the Pi
    compares that against its own clock for the staleness dim.
    """
    snap = st["snapshot"] or {}
    ctx = st["context"] or {}
    fh = snap.get("five_hour") or {}
    sd = snap.get("seven_day") or {}
    return {
        "kind": "gauge",
        "desktop_id": "prototype-desktop-id",
        "generated_at": snap.get("updated_at"),
        "five_hour": {"pct": fh.get("used_percentage"),
                      "resets_at": fh.get("resets_at")},
        "seven_day": {"pct": sd.get("used_percentage"),
                      "resets_at": sd.get("resets_at")},
        "context": {"pct": ctx.get("pct"),
                    "tokens": ctx.get("tokens"),
                    "model": ctx.get("model")},
    }


def push_integers(st):
    """#25's trigger: the values whose CHANGE is worth a push."""
    snap = st["snapshot"] or {}
    ctx = st["context"] or {}
    fh = snap.get("five_hour") or {}
    sd = snap.get("seven_day") or {}
    return (fh.get("used_percentage"), sd.get("used_percentage"),
            ctx.get("pct"), fh.get("resets_at"), sd.get("resets_at"))


def fmt_delta(seconds):
    if seconds is None:
        return "?"
    seconds = int(seconds)
    sign = "-" if seconds < 0 else ""
    seconds = abs(seconds)
    h, m = divmod(seconds // 60, 60)
    return f"{sign}{h}h{m:02d}m" if h else f"{sign}{m}m"


# --- output ---------------------------------------------------------------

def print_state(st):
    snap = st["snapshot"]
    print("=" * 68)
    print(f"  LIVE GAUGE  @ {st['now'].isoformat(timespec='seconds')}")
    print("=" * 68)

    if not snap:
        print("\n  snapshot: ABSENT — no data yet (#24's explicit null state)")
    else:
        fh, sd = snap.get("five_hour") or {}, snap.get("seven_day") or {}
        flag = "  ** STALE **" if st["stale"] else ""
        print(f"\n  snapshot written {st['snapshot_age_s']:.0f}s ago{flag}")
        print(f"    (that is a WRITE time, not a fetch time — #27)")
        for name, w in (("5h ", fh), ("7d ", sd)):
            pct, ra = w.get("used_percentage"), w.get("resets_at")
            left = (iso_to_dt(ra) - st["now"]).total_seconds() if ra else None
            bar = "#" * int((pct or 0) / 5) + "." * (20 - int((pct or 0) / 5))
            print(f"    {name} {str(pct).rjust(3)}%  [{bar}]  "
                  f"resets in {fmt_delta(left)}  ({ra})")

    sess, ctx = st["session"], st["context"]
    print(f"\n  sessions registered: {st['session_count']}")
    if not sess:
        print("    none — gauge renders blank (#24)")
    else:
        age = (st["now"].timestamp() * 1000 - sess.get("updatedAt", 0)) / 1000
        print(f"    active: pid {sess['pid']}  status={sess.get('status')}  "
              f"updatedAt {age:.0f}s ago")
        print(f"            cwd  {sess.get('cwd')}")
    if ctx:
        known = "" if ctx["window_known"] else "  ** MODEL NOT IN TABLE **"
        print(f"\n  context: {ctx['tokens']:,} tokens = {ctx['pct']}% of "
              f"{ctx['window']:,} ({ctx['model']}){known}")
        if ctx["skipped_sidechain"]:
            print(f"    skipped {ctx['skipped_sidechain']} sidechain entries")

    payload = gauge_payload(st)
    blob = json.dumps(payload, separators=(",", ":"))
    n = len(blob.encode())
    ok = "OK" if n <= SINGLE_WRITE_BUDGET else "OVER BUDGET"
    print(f"\n  Gauge Payload: {n} bytes / {SINGLE_WRITE_BUDGET} budget  [{ok}]")
    print(f"    {blob}")
    print()


def watch(interval=POLL_SECONDS):
    """#25's loop: poll, push only when an integer changes."""
    print(f"watching, polling every {interval}s — Ctrl-C to stop")
    print("(#25: a push happens only when one of the tracked integers moves)\n")
    last = None
    pushes = started = 0
    t0 = time.time()
    while True:
        st = gauge_state()
        cur = push_integers(st)
        started += 1
        stamp = datetime.now().strftime("%H:%M:%S")
        if last is None:
            print(f"[{stamp}] baseline    5h={cur[0]}% 7d={cur[1]}% ctx={cur[2]}%")
        elif cur != last:
            pushes += 1
            changed = [n for n, a, b in zip(
                ("5h", "7d", "ctx", "5h.resets", "7d.resets"), last, cur) if a != b]
            print(f"[{stamp}] PUSH #{pushes}   5h={cur[0]}% 7d={cur[1]}% "
                  f"ctx={cur[2]}%   changed: {', '.join(changed)}")
        else:
            mins = (time.time() - t0) / 60
            print(f"[{stamp}] no change   5h={cur[0]}% 7d={cur[1]}% ctx={cur[2]}%"
                  f"   ({started} polls / {pushes} pushes in {mins:.0f}m)")
        last = cur
        time.sleep(interval)


def boundary():
    """The rollover: where an off-by-one in a window rule would show up.

    #22 settled that we never model the window — we read resets_at. This
    prints the arithmetic anyway, to show there IS no local rule to get wrong.
    """
    st = gauge_state()
    snap = st["snapshot"] or {}
    now = st["now"]
    print("=" * 68)
    print("  WINDOW BOUNDARY")
    print("=" * 68)
    print("\n  #22: the boundary is READ, never modelled. Nothing below is")
    print("  computed from local activity — it is all server-supplied.\n")
    for name, w in (("five_hour", snap.get("five_hour") or {}),
                    ("seven_day", snap.get("seven_day") or {})):
        ra = w.get("resets_at")
        if not ra:
            print(f"  {name}: resets_at is null")
            continue
        dt = iso_to_dt(ra)
        left = (dt - now).total_seconds()
        print(f"  {name}:")
        print(f"    resets_at      {ra}")
        print(f"    local time     {dt.astimezone().isoformat(timespec='seconds')}")
        print(f"    time remaining {fmt_delta(left)}  ({left:.0f}s)")
        print(f"    on clock hour? {'yes' if dt.minute == 0 and dt.second == 0 else 'NO'}")
        print(f"    redraws before reset at a {STALE_SECONDS}s floor: "
              f"{int(left // STALE_SECONDS)}")
        print()
    print("  Simulated rollover — what the Pi must render as reset passes:")
    for offset in (600, 60, 1, -1, -60):
        state = "post-reset (pct should have dropped to ~0)" if offset < 0 \
            else "pre-reset"
        print(f"    T{offset:+5}s  countdown '{fmt_delta(offset)}'  {state}")
    print("\n  ^ the Pi computes that countdown from resets_at and ITS OWN")
    print("    clock, between pushes (#25 decision 4 / ADR-0008). Nothing")
    print("    establishes that clock today — that is #37.\n")


def _draw_common(d, font, st, y0):
    """7d + context + footer, shared by every variant. Returns nothing."""
    snap = st["snapshot"] or {}
    ctx = st["context"] or {}
    sd = snap.get("seven_day") or {}

    def bar(x, y, w, h, pct):
        d.rectangle([x, y, x + w, y + h], outline=0)
        fill = int((w - 2) * (pct or 0) / 100)
        if fill:
            d.rectangle([x + 1, y + 1, x + 1 + fill, y + h - 1], fill=0)

    resets_sd = iso_to_dt(sd.get("resets_at"))
    sd_left = (resets_sd - st["now"]).total_seconds() if resets_sd else None
    d.text((3, y0), "7D", font=font(11, True), fill=0)
    d.text((25, y0 - 1), f"{sd.get('used_percentage')}%", font=font(13, True), fill=0)
    d.text((58, y0 + 1), f"resets {fmt_delta(sd_left)}", font=font(10), fill=0)
    bar(3, y0 + 16, 244, 8, sd.get("used_percentage"))

    # Context: absolute, NOT a bar. Measured 0/42 sessions ever passed 900k
    # against a 1,000,000 window, so a percentage bar is a permanent stub.
    y1 = y0 + 29
    tok = (ctx.get("tokens") or 0) / 1000
    win = (ctx.get("window") or 0) / 1_000_000
    d.text((3, y1), "CTX", font=font(11, True), fill=0)
    d.text((30, y1 - 1), f"{tok:.0f}k", font=font(13, True), fill=0)
    d.text((72, y1 + 1), f"of {win:.1f}M  ({ctx.get('pct')}%)", font=font(10), fill=0)

    foot = "STALE" if st["stale"] else f"upd {int(st['snapshot_age_s'] or 0)}s"
    d.text((3, 110), foot, font=font(10), fill=0)
    d.text((160, 110), (ctx.get("model") or "")[:16], font=font(10), fill=0)


def render(path, variant="a", st=None):
    """The layout at exactly 250x122, 1-bit, as the panel would show it.

    Three variants, because #26 asks whether a percentage is even the right
    framing versus time-until-reset. Look at them, do not reason about them.
    """
    from PIL import Image, ImageDraw, ImageFont

    if st is None:
        st = gauge_state()
    snap = st["snapshot"] or {}
    fh = snap.get("five_hour") or {}
    pct = fh.get("used_percentage")
    left = (iso_to_dt(fh.get("resets_at")) - st["now"]).total_seconds() \
        if fh.get("resets_at") else None

    def font(size, bold=False):
        name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
        return ImageFont.truetype(f"/usr/share/fonts/truetype/dejavu/{name}", size)

    img = Image.new("1", (PANEL_W, PANEL_H), 1)
    d = ImageDraw.Draw(img)

    def bar(x, y, w, h, p):
        d.rectangle([x, y, x + w, y + h], outline=0)
        fill = int((w - 2) * (p or 0) / 100)
        if fill:
            d.rectangle([x + 1, y + 1, x + 1 + fill, y + h - 1], fill=0)

    if variant == "a":       # gauge-led: the percentage is the headline
        d.text((3, 2), "5H", font=font(13, True), fill=0)
        d.text((28, -5), f"{pct}%", font=font(34, True), fill=0)
        d.text((246, 8), f"resets {fmt_delta(left)}", font=font(12),
               fill=0, anchor="ra")
        bar(3, 36, 244, 12, pct)
        _draw_common(d, font, st, 54)

    elif variant == "b":     # time-led: the countdown is the headline
        d.text((3, 2), "5H RESETS IN", font=font(11, True), fill=0)
        d.text((3, -4), "", font=font(11), fill=0)
        d.text((124, 8), fmt_delta(left), font=font(30, True),
               fill=0, anchor="ma")
        d.text((246, 6), f"{pct}%", font=font(15, True), fill=0, anchor="ra")
        bar(3, 36, 244, 12, pct)
        _draw_common(d, font, st, 54)

    else:                    # c — split: both large, side by side
        d.text((3, 1), "5H", font=font(10, True), fill=0)
        d.text((3, 8), f"{pct}%", font=font(28, True), fill=0)
        d.line([(122, 3), (122, 40)], fill=0)
        d.text((132, 1), "RESETS IN", font=font(10, True), fill=0)
        d.text((132, 8), fmt_delta(left), font=font(24, True), fill=0)
        bar(3, 40, 244, 10, pct)
        _draw_common(d, font, st, 56)

    img.save(path)
    img.resize((PANEL_W * 3, PANEL_H * 3), Image.NEAREST).save(
        path.replace(".png", "-3x.png"))
    print(f"wrote {path} (+3x)  variant={variant}")


def null_states(prefix):
    """#24 insisted three states be DISTINGUISHABLE. Look at them.

    no-data  : snapshot absent (before claude-hud's first render)
    no-sess  : snapshot present, zero live sessions -> gauge blank
    stale    : snapshot present but older than the 300s mark
    """
    base = gauge_state()
    cases = {
        "no-data": {**base, "snapshot": None, "snapshot_age_s": None,
                    "stale": False, "context": None, "session": None,
                    "session_count": 0},
        "no-sess": {**base, "session": None, "session_count": 0,
                    "context": None},
        "stale": {**base, "stale": True, "snapshot_age_s": 4200},
    }
    for name, st in cases.items():
        render(f"{prefix}-{name}.png", "c", st)


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print_state(gauge_state())
    elif args[0] == "--watch":
        watch(int(args[1]) if len(args) > 1 else POLL_SECONDS)
    elif args[0] == "--boundary":
        boundary()
    elif args[0] == "--nulls":
        null_states(args[1] if len(args) > 1 else "nulls")
    elif args[0] == "--render":
        render(args[1] if len(args) > 1 else "gauge.png",
               args[2] if len(args) > 2 else "a")
    else:
        print(__doc__)
