"""Desktop: the Verdict (spec §8).

`(desktop facts, pi status dict or None) → Verdict`. **No BLE, no clock, no
I/O** — this is where the design lives, and §10.3 requires it be testable
without either machine. Everything time-shaped arrives already reduced to a
number by the caller: the Pi reports durations because it has no wall clock
(ADR-0009), and this module compares them without consulting one either.

Four states, three severities, one precedence order. Two of those are load
bearing in a way that reads as detail until you see the failure they prevent:

- ⚠ **Unreachable is its own state, never "not working"** (§8.1). A Pi that
  is powered off is not broken, and a surface that says it is trains people
  to ignore the surface. `Verdict.ok` is therefore `True`/`False`/**`None`**
  — a consumer reading it as a plain boolean is the bug the shape prevents.
- ⚠ **`NOTE` is not a nicety** (§8.2). Without it, a Pi that rebooted four
  minutes ago and is drawing its startup frame perfectly renders as
  `✗ Not working — the Pi restarted since the last push`. **`Restarted` is a
  comparison, not a fault.**

Rendering — the layout, the `--json` envelope, the six evidence rows —
belongs to `cli.py` (#86). This module produces the facts rendering
formats, in `checks`, which is always the six entries of §8.3 in §8.4's
precedence order: **fixed, not filtered** (§9.2, §9.5).

Two pieces of text live here anyway, deliberately rather than by accident:
the **glyphs** (§8.1 defines them as part of the state model, one per
state) and the **headlines** (§8.5 specifies both Unreachable ones
verbatim, and §8.4's whole point is that the headline *names the story* a
particular check tells — which only this module knows). Everything about
how they are arranged on a screen is still #86's.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

import config

# §8.3's Panel bound is one keepalive plus one floor, and the floor is a
# Tier 3 invariant mirrored (not imported — the Pi and the Desktop are
# different deployments) in `config.py`. Re-exported here so a reader of this
# module can see the number the bound is derived from without chasing it.
REDRAW_FLOOR_S = config.REDRAW_FLOOR_S

# §8.3's Image check compares the Pi's `schema_version` against "the
# Desktop's expectation" — this is it. Outside the Tier system (§4.4:
# migration machinery, not a tunable value), and, like `config.py`'s two
# mirrors, kept in step with `pi/receive.py:SCHEMA_VERSION` **by hand**,
# because neither deployment installs the other's code. `test_verdict.py`
# asserts the three mirrors still match, which is the only thing standing
# between an amendment there and a Verdict that quietly compares against a
# stale number here.
EXPECTED_PI_SCHEMA_VERSION = 1


class State(str, Enum):
    """§8.1's four states. ⚠ Collapsing `CANT_TELL` into `NOT_WORKING` is the
    failure this state model exists to prevent."""

    OK = "ok"
    NOT_WORKING = "not_working"
    CANT_TELL = "cant_tell"
    NOT_PAIRED = "not_paired"


class Severity(str, Enum):
    """§8.2's three severities. `NOTE` is shown, but never headlines."""

    OK = "ok"
    FAIL = "fail"
    NOTE = "note"


class Reach(str, Enum):
    """What the BLE lock and the scan between them established (§7.1).

    `BUSY` and `ABSENT` are both Unreachable and both yield `CANT_TELL`, but
    they get **different headlines** (§8.5): in the busy case this Desktop is
    itself the thing occupying the link, and telling someone "the Pi is
    unreachable" when they are looking at it is how a surface loses trust.
    """

    REACHABLE = "reachable"
    ABSENT = "absent"
    BUSY = "busy"


GLYPHS = {
    State.OK: "✓",
    State.NOT_WORKING: "✗",
    State.CANT_TELL: "?",
    State.NOT_PAIRED: "–",
}

# §8.4's precedence: headline the most explanatory failure, in this fixed
# order. ⚠ Counting checks produces a worse answer than naming the story --
# the first render of a diverged Pi said "2 checks failed", when the truth is
# one fact seen twice.
CHECK_ORDER = ("coupled", "image", "panel", "readings", "coverage", "restarted")


@dataclass(frozen=True)
class DesktopFacts:
    """The half of every comparison only the Desktop holds (§8.3).

    Gathering these is the caller's job (#86): `readings` and
    `coverage_start` come from the usage archive, `schema_version` and
    `idle_keepalive_s` from Configuration and the mirrored constants, and
    `seconds_since_last_push` from `entries.pushed_at`. Keeping them a plain
    frozen record is what keeps this module free of I/O.
    """

    pi_address: Optional[str]
    readings: int
    coverage_start: Optional[str]
    idle_keepalive_s: int
    # None means "this Desktop has never pushed", so `Restarted` has nothing
    # to compare against — and cannot claim the Pi did not restart either.
    seconds_since_last_push: Optional[float]
    # The Desktop's expectation for §8.3's Image check. Defaulted to the
    # mirrored constant so the drift guard in `test_verdict.py` protects
    # something a real caller actually compares against.
    schema_version: int = EXPECTED_PI_SCHEMA_VERSION


@dataclass(frozen=True)
class Check:
    """One of §8.3's six comparisons. `ok` is `None` when nothing was
    learned, mirroring `Verdict.ok` for the same reason.

    `detail` is the evidence row's text (§9.2's right-hand column, and
    §9.5's `checks[].detail`); `story` is the same fact phrased to headline,
    so §8.4 can *name the story* rather than count failures. They differ
    because an evidence row sits under a label and a headline does not.
    """

    name: str
    severity: Severity
    ok: Optional[bool]
    detail: str
    story: str = ""


@dataclass(frozen=True)
class Verdict:
    state: State
    ok: Optional[bool]
    glyph: str
    headline: str
    advice: Optional[str]
    reachable: bool
    checks: tuple[Check, ...]


# ---------------------------------------------------------------------------
# §8.5 The two Unreachable headlines
# ---------------------------------------------------------------------------

# ⚠ One glossary term, two headlines. Both state "Nothing was queued"
# (ADR-0011), because a human who has just been refused will otherwise assume
# something is pending.
UNREACHABLE_HEADLINES = {
    Reach.ABSENT: (
        "Can't tell — the Pi is unreachable.",
        "Nothing was queued. Re-run when the Pi is back.",
    ),
    Reach.BUSY: (
        "Can't tell yet — the link is busy.",
        "Nothing was queued. Retry in a moment — a Batch takes about two minutes.",
    ),
}

NOT_PAIRED_HEADLINE = "Not paired — no Pi address is recorded."
NOT_PAIRED_ADVICE = "Nothing was queued. Pair this Desktop with a Pi first."

UNLEARNED_DETAIL = "not checked — nothing was learned"


def build_verdict(
    facts: DesktopFacts,
    status: Optional[dict],
    reach: Reach = Reach.REACHABLE,
) -> Verdict:
    """The whole of §8, as one pure function.

    `status` is §5.4's status reply as the Pi sent it, or `None` when no
    reply arrived. `reach` is what §7.1's lock established: *held past the
    wait, scan not attempted* is `BUSY`; *acquired, scan timed out* is
    `ABSENT`.
    """
    if facts.pi_address is None:
        # §4.6: `pi.address` being null **is** the not-paired state. It is
        # checked before reachability because "no Pi answered" is not news
        # about a Desktop that was never told which Pi to talk to.
        return _unlearned(State.NOT_PAIRED, NOT_PAIRED_HEADLINE, NOT_PAIRED_ADVICE)

    if reach is not Reach.REACHABLE or status is None:
        # A reachable Pi that sent no reply is `absent` by the same rule as
        # a scan that timed out: nothing was learned, and nothing is queued.
        headline, advice = UNREACHABLE_HEADLINES[
            reach if reach in UNREACHABLE_HEADLINES else Reach.ABSENT
        ]
        return _unlearned(State.CANT_TELL, headline, advice)

    checks = _run_checks(facts, status)
    by_name = {check.name: check for check in checks}

    # §8.4: the same fault seen twice makes the surface less informative, not
    # more. A lost Batch fails Readings *and* Coverage; when Readings has
    # already failed, Coverage is evidence, not a second story.
    readings_failed = by_name["readings"].severity is Severity.FAIL
    coverage_failed = by_name["coverage"].severity is Severity.FAIL
    if readings_failed and coverage_failed:
        checks = tuple(
            _demote(check) if check.name == "coverage" else check for check in checks
        )

    headline = _headline(checks)
    state = State.NOT_WORKING if headline is not None else State.OK
    return Verdict(
        state=state,
        ok=state is State.OK,
        glyph=GLYPHS[state],
        headline=headline or "Working.",
        advice=None,
        reachable=True,
        checks=checks,
    )


def _unlearned(state: State, headline: str, advice: str) -> Verdict:
    """A Verdict where no comparison could be made.

    ⚠ The six checks are still present (§9.5: "fixed, not filtered … even
    when `reachable` is false, with `ok: null`"). They carry `NOTE` because
    `OK` would claim a comparison that never happened and `FAIL` would be the
    very collapse §8.1 forbids.
    """
    return Verdict(
        state=state,
        ok=None,
        glyph=GLYPHS[state],
        headline=headline,
        advice=advice,
        reachable=False,
        checks=tuple(
            Check(name=name, severity=Severity.NOTE, ok=None, detail=UNLEARNED_DETAIL)
            for name in CHECK_ORDER
        ),
    )


def _demote(check: Check) -> Check:
    return Check(
        name=check.name,
        severity=Severity.NOTE,
        ok=check.ok,
        detail=check.detail,
        story=check.story,
    )


def _headline(checks: tuple[Check, ...]) -> Optional[str]:
    """The most explanatory failure, in §8.4's fixed order — or `None` when
    nothing failed. Only a `FAIL` headlines; a `NOTE` never does."""
    for check in checks:
        if check.severity is Severity.FAIL:
            return f"Not working — {check.story}."
    return None


# ---------------------------------------------------------------------------
# §8.3 The six comparisons
# ---------------------------------------------------------------------------


def _run_checks(facts: DesktopFacts, status: dict) -> tuple[Check, ...]:
    built = {
        "coupled": _coupled(status),
        "image": _image(facts, status),
        "panel": _panel(facts, status),
        "readings": _readings(facts, status),
        "coverage": _coverage(facts, status),
        "restarted": _restarted(facts, status),
    }
    return tuple(built[name] for name in CHECK_ORDER)


def _check(name: str, ok: bool, detail: str, story: str, *, note: bool = False) -> Check:
    """`note=True` is §8.2's third severity: a comparison that did not pass
    but is not a fault. Only `Restarted` uses it at construction; Coverage
    reaches it by demotion (§8.4)."""
    severity = Severity.OK if ok else (Severity.NOTE if note else Severity.FAIL)
    return Check(name=name, severity=severity, ok=ok, detail=detail, story=story)


_MISSING = object()


def _field(status: dict, name: str) -> Any:
    """Every read of the reply goes through here.

    ⚠ §5.4 defines all of these fields as always present, so an absent one
    means the reply is not the reply this Verdict was written against — a
    fault, uniformly, rather than a silently benign default. `Coupled`
    reading a missing `wiped` as "not wiped" was exactly that bug.
    """
    return status.get(name, _MISSING)


def _missing(name: str, label: str, *, note: bool = False) -> Check:
    return _check(
        name,
        False,
        f"no {label} in the reply",
        f"the Pi's reply carried no {label}",
        note=note,
    )


def _number(value: Any) -> Optional[float]:
    """A duration or count from the wire, or `None` if it is not a number.

    The reply is parsed JSON from another machine, so a field can be the
    wrong *type* as easily as absent; comparing a string against a bound
    raises `TypeError` deep inside a check. `bool` is excluded because
    `True` is an `int` in Python and a boolean uptime is not a duration.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _coupled(status: dict) -> Check:
    """A reply arrived and `wiped` is false (§8.3)."""
    wiped = _field(status, "wiped")
    if wiped is _MISSING:
        return _missing("coupled", "wipe flag")
    if wiped:
        return _check(
            "coupled",
            False,
            "replied, but reported a wipe",
            "the Pi wiped its Readings, so it was coupled to another Desktop",
        )
    return _check("coupled", True, "replied, not wiped", "coupled")


def _image(facts: DesktopFacts, status: dict) -> Check:
    """`schema_version` vs the Desktop's expectation (§8.3) — catches a Pi
    running an older image."""
    value = _field(status, "schema_version")
    if value is _MISSING:
        return _missing("image", "schema version")
    if value != facts.schema_version:
        return _check(
            "image",
            False,
            f"schema {value} (expected {facts.schema_version})",
            f"the Pi is running schema {value}, not {facts.schema_version}",
        )
    return _check("image", True, f"schema {value}", "the image matches")


def _panel(facts: DesktopFacts, status: dict) -> Check:
    """`panel == "ok"` **and** `since_redraw_s <= idle_keepalive_s +
    REDRAW_FLOOR_S` (§8.3).

    ⚠ The bound is **derived, not guessed**: at rest the Historic View
    redraws only when pending or when the keepalive is due, so anything
    beyond one keepalive plus one floor is genuinely wrong.
    """
    health = _field(status, "panel")
    if health is _MISSING:
        return _missing("panel", "panel health")
    if health != "ok":
        return _check("panel", False, f"panel {health}", f"the Pi's panel is {health}")

    since = _field(status, "since_redraw_s")
    if since is _MISSING:
        return _missing("panel", "since_redraw_s")
    frame = status.get("frame", "?")
    bound = facts.idle_keepalive_s + REDRAW_FLOOR_S
    if since is None:
        # Panel Health `ok` with no draw ever recorded contradicts itself:
        # §5.4 reports `panel: "never"` for a Pi that has attempted nothing.
        # The rule as written cannot be satisfied — null is not `<= bound` —
        # so this fails rather than being quietly excused.
        return _check(
            "panel",
            False,
            "panel ok, but nothing has ever been drawn",
            "the Pi reports a healthy panel it has never drawn on",
        )
    seconds = _number(since)
    if seconds is None:
        return _check(
            "panel",
            False,
            f"since_redraw_s is {since!r}, not a duration",
            "the Pi reported an unreadable time since its last draw",
        )
    if seconds > bound:
        return _check(
            "panel",
            False,
            f"{frame} frame, {int(seconds)}s since the last draw (bound {bound}s)",
            f"the panel has not redrawn in {int(seconds)}s",
        )
    return _check("panel", True, f"{frame} frame, drew {int(seconds)}s ago", "the panel is drawing")


def _readings(facts: DesktopFacts, status: dict) -> Check:
    """`readings` vs the Desktop's count of pushed Readings (§8.3)."""
    value = _field(status, "readings")
    if value is _MISSING:
        return _missing("readings", "Reading count")
    if value == facts.readings:
        return _check("readings", True, str(value), "the Readings agree")
    count = _number(value)
    if count is None:
        return _check(
            "readings",
            False,
            f"readings is {value!r}, not a count",
            "the Pi reported an unreadable Reading count",
        )
    difference = facts.readings - int(count)
    if difference > 0:
        return _check(
            "readings",
            False,
            f"{value}  ({difference} missing)",
            f"the Pi is missing {difference} Readings",
        )
    return _check(
        "readings",
        False,
        f"{value}  ({-difference} unexpected)",
        f"the Pi holds {-difference} Readings this Desktop did not send",
    )


def _coverage(facts: DesktopFacts, status: dict) -> Check:
    """`coverage_start` vs `MIN(local_date)` over pushed entries (§8.3)."""
    value = _field(status, "coverage_start")
    if value is _MISSING:
        return _missing("coverage", "coverage start")
    if value == facts.coverage_start:
        return _check("coverage", True, str(value), "the coverage agrees")

    expected = facts.coverage_start
    story = f"the Pi's coverage starts at {value}, not {expected}"
    if value is None:
        return _check(
            "coverage",
            False,
            f"none  (expected {expected})",
            f"the Pi holds no coverage start, expected {expected}",
        )
    # "short" is the §9.2 evidence row's word for a Pi whose history begins
    # later than the Desktop's -- the shape a lost Batch leaves behind.
    short = expected is not None and value > expected
    detail = f"{value}  (short)" if short else f"{value}  (expected {expected})"
    return _check("coverage", False, detail, story)


def _restarted(facts: DesktopFacts, status: dict) -> Check:
    """`uptime_s` less than the elapsed time since the Desktop's last
    successful push (§8.3).

    ⚠ **Severity `NOTE`, never `FAIL`.** `uptime_s` is the process's, not the
    machine's: the question that matters is *"did `receive.py` restart"*, and
    the honest answer to it is a note, not a fault.
    """
    uptime = _field(status, "uptime_s")
    if uptime is _MISSING:
        return _missing("restarted", "uptime", note=True)
    seconds = _number(uptime)
    if seconds is None:
        return _check(
            "restarted",
            False,
            f"uptime_s is {uptime!r}, not a duration",
            "the Pi reported an unreadable uptime",
            note=True,
        )
    if facts.seconds_since_last_push is None:
        # ⚠ Not `OK`: this Desktop has never pushed, so there is nothing to
        # compare against, and claiming the Pi did not restart would assert a
        # comparison that never happened -- the same rule `_unlearned` states.
        return Check(
            name="restarted",
            severity=Severity.NOTE,
            ok=None,
            detail=f"up {int(seconds)}s, with no push to compare against",
            story="nothing has been pushed from this Desktop yet",
        )
    gap = int(facts.seconds_since_last_push)
    if seconds < facts.seconds_since_last_push:
        return _check(
            "restarted",
            False,
            f"up {int(seconds)}s, less than the {gap}s since the last push",
            "the Pi restarted since the last push",
            note=True,
        )
    return _check("restarted", True, f"up {int(seconds)}s", "the Pi has not restarted")
