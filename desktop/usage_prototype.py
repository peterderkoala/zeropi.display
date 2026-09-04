"""PROTOTYPE — throwaway. Not the implementation. Do not merge to dev.

Answers ticket #18: do the computed usage numbers look right, and does the
row shape survive contact with real data?

Reads the real Claude Code logs under ~/.claude/projects, applies the dedup
rule from #15 and the pricing table from #14, and prints the rows a push
would send at (date, project, model) grain.

Run:  python3 desktop/usage_prototype.py

Everything here is deliberately unpolished: no tests, no error handling, no
abstractions. The spec (#20) is the deliverable; this only tells us what to
put in it.
"""

import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECTS_DIR = Path.home() / ".claude" / "projects"
WINDOW_DAYS = 7
SINGLE_WRITE_BUDGET = 514  # MTU 517 minus 3 bytes ATT overhead

# --- pricing, USD per million tokens (#14, docs/research/pricing-table.md) ---
# Keys are matched by PREFIX: logged ids may be date-suffixed.
PRICING = {
    "claude-opus-5":   {"in": 5.00, "out": 25.00, "w5m": 6.25, "w1h": 10.00, "read": 0.50},
    "claude-sonnet-5": {"in": 2.00, "out": 10.00, "w5m": 2.50, "w1h": 4.00,  "read": 0.20},
    "claude-haiku-4-5": {"in": 1.00, "out": 5.00, "w5m": 1.25, "w1h": 2.00,  "read": 0.10},
}
WEB_SEARCH_USD = 0.01
FREE_MODELS = {"<synthetic>"}  # no API call happened; genuinely free, not unknown


def price_for(model):
    """Prefix match. Returns (rates, known) — known=False drives cost_complete."""
    if model in FREE_MODELS:
        return None, True
    for prefix, rates in PRICING.items():
        if model and model.startswith(prefix):
            return rates, True
    return None, False


# --- project attribution cascade R1-R4 (#15 §8) ---------------------------

def encode(path):
    return re.sub(r"[^a-zA-Z0-9]", "-", path)


def project_label(dirname, cwds):
    for cwd in cwds:
        if encode(cwd) == dirname:
            return os.path.basename(cwd), "R1", True
    for cwd in cwds:
        if "/.claude/worktrees/" in cwd:
            root = cwd.split("/.claude/worktrees/")[0]
            if encode(root) == dirname:
                return os.path.basename(root), "R2", True
    for cwd in cwds:
        p = Path(cwd)
        for anc in p.parents:
            if encode(str(anc)) == dirname:
                return anc.name, "R3", True
    return dirname.lstrip("-").split("-")[-1], "R4", False  # lossy; unverified


# --- read + dedup ---------------------------------------------------------

def rank(entry):
    """Winner selection within a duplicate group (#15 §1). Higher is better."""
    u = entry["usage"]
    total = (u.get("input_tokens", 0) + u.get("output_tokens", 0)
             + u.get("cache_creation_input_tokens", 0)
             + u.get("cache_read_input_tokens", 0))
    return (0 if entry.get("isSidechain") else 1,
            total,
            1 if u.get("speed") is not None else 0)


def load():
    """Returns (winners, first_seen, cwds_by_project, stats)."""
    groups = defaultdict(list)       # (requestId, message.id) -> [entry]
    first_seen = {}                  # naive keep-first, for the delta
    cwds = defaultdict(set)
    stats = Counter()

    for project_dir in sorted(PROJECTS_DIR.iterdir()):
        if not project_dir.is_dir():
            continue
        for path in project_dir.rglob("*.jsonl"):
            for line in path.read_text(errors="replace").splitlines():
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    stats["unparseable_lines"] += 1
                    continue
                if d.get("cwd"):
                    cwds[project_dir.name].add(d["cwd"])
                if d.get("type") != "assistant":
                    continue
                msg = d.get("message") or {}
                usage = msg.get("usage")
                if not usage:
                    continue
                stats["usage_entries"] += 1
                key = (d.get("requestId"), msg.get("id"))
                if not key[0] or not key[1]:
                    stats["dropped_unkeyed"] += 1
                    continue
                entry = {
                    "project": project_dir.name,
                    "timestamp": d.get("timestamp"),
                    "sessionId": d.get("sessionId"),
                    "isSidechain": d.get("isSidechain"),
                    "model": msg.get("model"),
                    "usage": usage,
                }
                groups[key].append(entry)
                first_seen.setdefault(key, entry)

    winners = {k: max(v, key=rank) for k, v in groups.items()}
    stats["distinct_keys"] = len(groups)
    stats["duplicate_groups"] = sum(1 for v in groups.values() if len(v) > 1)
    return winners, first_seen, cwds, stats


# --- costing + aggregation ------------------------------------------------

def split_cache_write(usage):
    cc = usage.get("cache_creation") or {}
    return (cc.get("ephemeral_5m_input_tokens", 0),
            cc.get("ephemeral_1h_input_tokens", 0))


def cost_of(usage, model):
    rates, known = price_for(model)
    if rates is None:
        return 0.0, known
    w5m, w1h = split_cache_write(usage)
    searches = (usage.get("server_tool_use") or {}).get("web_search_requests", 0) or 0
    usd = (usage.get("input_tokens", 0) * rates["in"]
           + usage.get("output_tokens", 0) * rates["out"]
           + w5m * rates["w5m"]
           + w1h * rates["w1h"]
           + usage.get("cache_read_input_tokens", 0) * rates["read"]) / 1_000_000
    return usd + searches * WEB_SEARCH_USD, known


def local_date(ts):
    """Bucket by LOCAL date on the Desktop (map decision)."""
    return (datetime.fromisoformat(ts.replace("Z", "+00:00"))
            .astimezone().date().isoformat())


def aggregate(entries, labels):
    rows = defaultdict(lambda: {
        "input_tokens": 0, "output_tokens": 0,
        "cache_write_5m": 0, "cache_write_1h": 0, "cache_read_tokens": 0,
        "thinking_tokens": 0, "cost_usd": 0.0, "cost_complete": True,
        "sessions": set(),
    })
    for e in entries:
        if not e["timestamp"]:
            continue
        u = e["usage"]
        key = (local_date(e["timestamp"]), labels[e["project"]][0], e["model"])
        r = rows[key]
        w5m, w1h = split_cache_write(u)
        r["input_tokens"] += u.get("input_tokens", 0)
        r["output_tokens"] += u.get("output_tokens", 0)
        r["cache_write_5m"] += w5m
        r["cache_write_1h"] += w1h
        r["cache_read_tokens"] += u.get("cache_read_input_tokens", 0)
        r["thinking_tokens"] += (u.get("output_tokens_details") or {}).get("thinking_tokens", 0)
        usd, known = cost_of(u, e["model"])
        r["cost_usd"] += usd
        r["cost_complete"] &= known
        r["sessions"].add(e["sessionId"])
    return rows


def wire_row(key, r):
    """The row as it would go on the wire (cache-write summed, per map)."""
    date, project, model = key
    return {
        "date": date, "project": project, "model": model,
        "input_tokens": r["input_tokens"],
        "output_tokens": r["output_tokens"],
        "cache_creation_tokens": r["cache_write_5m"] + r["cache_write_1h"],
        "cache_read_tokens": r["cache_read_tokens"],
        "cost_usd": round(r["cost_usd"], 4),
        "sessions": len(r["sessions"]),
        "cost_complete": r["cost_complete"],
    }


def main():
    t0 = time.time()
    winners, first_seen, cwds, stats = load()
    read_secs = time.time() - t0

    labels = {d: project_label(d, sorted(cwds[d])) for d in cwds}

    print("=" * 78)
    print("PROTOTYPE — usage reader against real logs (ticket #18)")
    print("=" * 78)
    print(f"\nRead {stats['usage_entries']:,} usage entries in {read_secs:.2f}s")
    print(f"  distinct keys      : {stats['distinct_keys']:,}")
    print(f"  duplicate groups   : {stats['duplicate_groups']:,}")
    print(f"  dropped (unkeyed)  : {stats['dropped_unkeyed']}")
    print(f"  unparseable lines  : {stats['unparseable_lines']}")

    print("\nProject labels (cascade R1-R4):")
    for d, (label, rule, verified) in sorted(labels.items()):
        flag = "" if verified else "   <-- UNVERIFIED"
        print(f"  {d:38s} -> {label:22s} [{rule}]{flag}")

    # --- the dedup delta this prototype exists to prove -------------------
    ranked_rows = aggregate(winners.values(), labels)
    naive_rows = aggregate(first_seen.values(), labels)

    def totals(rows):
        out = sum(r["output_tokens"] for r in rows.values())
        cost = sum(r["cost_usd"] for r in rows.values())
        toks = sum(r["input_tokens"] + r["output_tokens"] + r["cache_write_5m"]
                   + r["cache_write_1h"] + r["cache_read_tokens"]
                   for r in rows.values())
        return toks, out, cost

    rt, ro, rc = totals(ranked_rows)
    nt, no, nc = totals(naive_rows)
    print("\n" + "-" * 78)
    print("DEDUP DELTA — ranked winner selection vs naive keep-first")
    print("-" * 78)
    print(f"  {'':22s} {'total tokens':>18s} {'output tokens':>16s} {'cost USD':>12s}")
    print(f"  {'ranked (recommended)':22s} {rt:>18,} {ro:>16,} {rc:>12,.2f}")
    print(f"  {'naive keep-first':22s} {nt:>18,} {no:>16,} {nc:>12,.2f}")
    lost = (1 - no / ro) * 100 if ro else 0
    print(f"  {'naive loses':22s} {rt-nt:>18,} {ro-no:>16,} {rc-nc:>12,.2f}"
          f"   ({lost:.1f}% of output)")

    # --- flat-rate cache-write error (the #14 trap) ----------------------
    flat_hi = flat_lo = true_cost = 0.0
    for e in winners.values():
        rates, known = price_for(e["model"])
        if rates is None:
            continue
        u = e["usage"]
        w5m, w1h = split_cache_write(u)
        base = (u.get("input_tokens", 0) * rates["in"]
                + u.get("output_tokens", 0) * rates["out"]
                + u.get("cache_read_input_tokens", 0) * rates["read"]) / 1e6
        true_cost += base + (w5m * rates["w5m"] + w1h * rates["w1h"]) / 1e6
        flat_hi += base + ((w5m + w1h) * rates["w1h"]) / 1e6
        flat_lo += base + ((w5m + w1h) * rates["w5m"]) / 1e6
    print("\n" + "-" * 78)
    print("CACHE-WRITE TTL SPLIT — cost if flat-rated (the #14 trap)")
    print("-" * 78)
    print(f"  true split      : ${true_cost:,.2f}")
    print(f"  all-1h flat     : ${flat_hi:,.2f}  ({(flat_hi/true_cost-1)*100:+.2f}%)")
    print(f"  all-5m flat     : ${flat_lo:,.2f}  ({(flat_lo/true_cost-1)*100:+.2f}%)")

    # --- the actual push: 7 CALENDAR days, skip empty and all-zero rows ---
    # #18 Q1: the window is 7 *calendar* days, not 7 active days. Active-days
    # spans 27 calendar days on this corpus and makes "last 7 days" a lie.
    all_dates = sorted({k[0] for k in ranked_rows})
    today = datetime.now().astimezone().date()
    window = {(today - timedelta(days=i)).isoformat() for i in range(WINDOW_DAYS)}
    push = {k: v for k, v in ranked_rows.items() if k[0] in window}
    # #18 Q3: a row with no tokens and no cost has nothing to display.
    dropped_zero = [k for k, v in push.items() if not any(
        (v["input_tokens"], v["output_tokens"], v["cache_write_5m"],
         v["cache_write_1h"], v["cache_read_tokens"]))]
    for k in dropped_zero:
        del push[k]

    print("\n" + "=" * 78)
    print(f"THE PUSH — rolling {WINDOW_DAYS} CALENDAR days, empty days skipped")
    print("=" * 78)
    print(f"Window: {min(window)} .. {max(window)}   "
          f"({len(all_dates)} active days exist in total)")
    print(f"All-zero rows dropped: {len(dropped_zero)} {dropped_zero}")

    sizes = []
    print(f"\n{'bytes':>6s}  row")
    for key in sorted(push):
        row = wire_row(key, push[key])
        body = json.dumps(row, separators=(",", ":"))
        sizes.append(len(body))
        over = "  <-- OVER BUDGET" if len(body) > SINGLE_WRITE_BUDGET else ""
        print(f"{len(body):>6d}  {body}{over}")

    print("\n" + "-" * 78)
    print("MEASUREMENTS")
    print("-" * 78)
    print(f"  rows per push (= BLE round trips) : {len(push)}")
    print(f"  largest row                       : {max(sizes)} bytes "
          f"(budget {SINGLE_WRITE_BUDGET})")
    print(f"  smallest / mean                   : {min(sizes)} / {sum(sizes)//len(sizes)} bytes")
    print(f"  total bytes if batched            : {sum(sizes):,} "
          f"(would need {-(-sum(sizes)//SINGLE_WRITE_BUDGET)} writes even chunked)")

    # worst-case row: longest label, saturated counters
    worst = json.dumps({
        "date": "2026-09-05",
        "project": max((l[0] for l in labels.values()), key=len),
        "model": "claude-haiku-4-5-20251001",
        "input_tokens": 999_999_999, "output_tokens": 999_999_999,
        "cache_creation_tokens": 999_999_999, "cache_read_tokens": 999_999_999,
        "cost_usd": 99999.9999, "sessions": 999, "cost_complete": False,
    }, separators=(",", ":"))
    print(f"  synthetic worst case              : {len(worst)} bytes")

    # --- headline legibility --------------------------------------------
    # #18 Q2: cost is the headline; the token sum is demoted and abbreviated,
    # because all-four-classes-summed is an 8-9 digit wall dominated by cache
    # reads and swings an order of magnitude day to day.
    print("\n" + "-" * 78)
    print("HEADLINE — cost leads, tokens demoted (abbreviated)")
    print("-" * 78)

    def abbrev(n):
        for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
            if n >= div:
                return f"{n/div:.1f}{suf}"
        return str(n)

    by_day = defaultdict(lambda: {"tok": 0, "cost": 0.0, "complete": True})
    for (date, _, _), r in push.items():
        b = by_day[date]
        b["tok"] += (r["input_tokens"] + r["output_tokens"] + r["cache_write_5m"]
                     + r["cache_write_1h"] + r["cache_read_tokens"])
        b["cost"] += r["cost_usd"]
        b["complete"] &= r["cost_complete"]
    for date in sorted(by_day):
        b = by_day[date]
        print(f"  {date}   ${b['cost']:>8,.2f}   {abbrev(b['tok']):>7s} tokens"
              f"   cost_complete={b['complete']}")

    incomplete = [k for k, v in push.items() if not v["cost_complete"]]
    print(f"\n  rows with cost_complete=False : {len(incomplete)}")
    for k in incomplete:
        print(f"    {k}")

    models = Counter(e["model"] for e in winners.values())
    print(f"\n  models seen (deduped): {dict(models)}")


if __name__ == "__main__":
    sys.exit(main())
