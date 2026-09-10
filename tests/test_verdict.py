"""Tests for desktop/verdict.py (#85), §8 of docs/spec-management-surface.md.

§10.3: the Verdict is `(desktop facts, pi status dict or None) → Verdict`,
with no BLE, no clock and no I/O — so this whole file is fixtures, and the
prototype's seven scenarios (`ok`, `unreachable`, `busy`, `never-paired`,
`diverged`, `stuck`, `restarted`) are the fixture list.
"""

from __future__ import annotations

import pytest

import config
import receive
import verdict as verdict_module
from verdict import CHECK_ORDER, DesktopFacts, Reach, Severity, State, build_verdict


# ---------------------------------------------------------------------------
# The seven scenarios
# ---------------------------------------------------------------------------


def _facts(**overrides) -> DesktopFacts:
    """A Desktop that agrees with `_status()` in every respect."""
    base = dict(
        pi_address="AA:BB:CC:DD:EE:FF",
        readings=312,
        coverage_start="2026-08-01",
        schema_version=1,
        idle_keepalive_s=86400,
        seconds_since_last_push=300.0,
    )
    base.update(overrides)
    return DesktopFacts(**base)


def _status(**overrides) -> dict:
    """§5.4's status reply, as the Pi sends it."""
    base = {
        "status": "ok",
        "kind": "command",
        "verb": "status",
        "drawn": False,
        "wiped": False,
        "frame": "historic",
        "since_redraw_s": 143,
        "panel": "ok",
        "readings": 312,
        "coverage_start": "2026-08-01",
        "uptime_s": 110000,
        "schema_version": 1,
    }
    base.update(overrides)
    return base


SCENARIOS = {
    "ok": (_facts(), _status(), Reach.REACHABLE),
    "unreachable": (_facts(), None, Reach.ABSENT),
    "busy": (_facts(), None, Reach.BUSY),
    "never-paired": (_facts(pi_address=None), None, Reach.ABSENT),
    "diverged": (
        _facts(),
        _status(readings=265, coverage_start="2026-08-08"),
        Reach.REACHABLE,
    ),
    "stuck": (_facts(), _status(panel="stuck"), Reach.REACHABLE),
    "restarted": (
        _facts(seconds_since_last_push=3600.0),
        _status(uptime_s=240),
        Reach.REACHABLE,
    ),
}

EXPECTED_STATE = {
    "ok": State.OK,
    "unreachable": State.CANT_TELL,
    "busy": State.CANT_TELL,
    "never-paired": State.NOT_PAIRED,
    "diverged": State.NOT_WORKING,
    "stuck": State.NOT_WORKING,
    "restarted": State.OK,
}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_every_scenario_has_a_state_a_headline_and_six_checks(name):
    facts, status, reach = SCENARIOS[name]

    got = build_verdict(facts, status, reach)

    assert got.state is EXPECTED_STATE[name]
    assert got.headline and got.headline[-1] == "."
    # ⚠ §9.5: fixed, not filtered — six entries, in §8.4's precedence order,
    # present even when nothing was learned.
    assert [check.name for check in got.checks] == list(CHECK_ORDER)
    assert len(got.checks) == 6


# ---------------------------------------------------------------------------
# §8.1 Four states — the Unreachable collapse this model exists to prevent
# ---------------------------------------------------------------------------


def test_unreachable_is_never_not_working():
    for reach in (Reach.ABSENT, Reach.BUSY):
        got = build_verdict(_facts(), None, reach)
        assert got.state is State.CANT_TELL
        assert got.glyph == "?"
        # ⚠ Nullable on purpose (§9.5): a consumer reading `ok` as a plain
        # boolean is the bug this shape prevents.
        assert got.ok is None
        assert got.reachable is False


def test_unreachable_and_busy_yield_different_headlines():
    absent = build_verdict(_facts(), None, Reach.ABSENT)
    busy = build_verdict(_facts(), None, Reach.BUSY)

    assert absent.state is busy.state is State.CANT_TELL
    assert absent.headline != busy.headline
    assert "unreachable" in absent.headline.lower()
    assert "busy" in busy.headline.lower()
    # ADR-0011: a human who has just been refused will otherwise assume
    # something is pending.
    assert "nothing was queued" in absent.advice.lower()
    assert "nothing was queued" in busy.advice.lower()


def test_a_null_pi_address_is_not_paired_not_unreachable():
    got = build_verdict(_facts(pi_address=None), None, Reach.ABSENT)

    assert got.state is State.NOT_PAIRED
    assert got.glyph == "–"
    assert got.ok is None


def test_unlearned_checks_are_present_but_claim_nothing():
    got = build_verdict(_facts(), None, Reach.ABSENT)

    assert all(check.ok is None for check in got.checks)
    assert all(check.severity is Severity.NOTE for check in got.checks)


# ---------------------------------------------------------------------------
# §8.2 Three severities — `restarted` is the one this exists for
# ---------------------------------------------------------------------------


def test_restarted_yields_working_with_one_note():
    """⚠ §8.2: a Pi that rebooted four minutes ago and is drawing its startup
    frame perfectly must not render as `✗ Not working`."""
    facts, status, reach = SCENARIOS["restarted"]

    got = build_verdict(facts, status, reach)

    assert got.state is State.OK
    assert got.ok is True
    assert got.headline == "Working."
    notes = [check for check in got.checks if check.severity is Severity.NOTE]
    assert [check.name for check in notes] == ["restarted"]
    assert not any(check.severity is Severity.FAIL for check in got.checks)


def test_restarted_is_never_a_fail_however_stale():
    got = build_verdict(
        _facts(seconds_since_last_push=86400.0), _status(uptime_s=1), Reach.REACHABLE
    )

    restarted = {check.name: check for check in got.checks}["restarted"]
    assert restarted.severity is Severity.NOTE
    assert restarted.ok is False
    assert got.state is State.OK


def test_a_pi_up_longer_than_the_gap_since_the_last_push_did_not_restart():
    got = build_verdict(_facts(seconds_since_last_push=300.0), _status(), Reach.REACHABLE)

    restarted = {check.name: check for check in got.checks}["restarted"]
    assert restarted.ok is True
    assert restarted.severity is Severity.OK


# ---------------------------------------------------------------------------
# §8.3 The six comparisons
# ---------------------------------------------------------------------------


def test_coupled_fails_when_the_reply_reports_a_wipe():
    got = build_verdict(_facts(), _status(wiped=True), Reach.REACHABLE)

    coupled = {check.name: check for check in got.checks}["coupled"]
    assert coupled.severity is Severity.FAIL
    assert got.state is State.NOT_WORKING
    assert "wipe" in got.headline.lower()


def test_image_fails_on_a_schema_mismatch():
    got = build_verdict(_facts(schema_version=2), _status(schema_version=1), Reach.REACHABLE)

    image = {check.name: check for check in got.checks}["image"]
    assert image.severity is Severity.FAIL
    assert "schema" in got.headline.lower()


@pytest.mark.parametrize("panel", ["unavailable", "stuck", "never"])
def test_panel_health_other_than_ok_fails(panel):
    got = build_verdict(_facts(), _status(panel=panel), Reach.REACHABLE)

    check = {c.name: c for c in got.checks}["panel"]
    assert check.severity is Severity.FAIL
    assert panel in check.detail


def test_panel_bound_is_one_keepalive_plus_one_floor():
    """§8.3: the bound is derived, not guessed — at rest the Historic View
    redraws only when pending or when the keepalive is due."""
    bound = 86400 + verdict_module.REDRAW_FLOOR_S

    at_bound = build_verdict(_facts(), _status(since_redraw_s=bound), Reach.REACHABLE)
    past_bound = build_verdict(_facts(), _status(since_redraw_s=bound + 1), Reach.REACHABLE)

    assert {c.name: c for c in at_bound.checks}["panel"].ok is True
    assert {c.name: c for c in past_bound.checks}["panel"].ok is False
    assert past_bound.state is State.NOT_WORKING


def test_a_pi_that_has_never_drawn_reports_a_null_since_redraw():
    got = build_verdict(_facts(), _status(since_redraw_s=None, panel="never"), Reach.REACHABLE)

    assert {c.name: c for c in got.checks}["panel"].ok is False


# ---------------------------------------------------------------------------
# §8.4 Precedence, and the Readings/Coverage coupling
# ---------------------------------------------------------------------------


def test_a_diverged_pi_headlines_readings_and_demotes_coverage():
    """⚠ §8.4: counting checks produces a worse answer than naming the story.
    A lost Batch shows up as Readings *and* Coverage — one fault, seen twice."""
    facts, status, reach = SCENARIOS["diverged"]

    got = build_verdict(facts, status, reach)
    checks = {check.name: check for check in got.checks}

    assert got.state is State.NOT_WORKING
    assert "47" in got.headline and "reading" in got.headline.lower()
    assert checks["readings"].severity is Severity.FAIL
    assert checks["coverage"].severity is Severity.NOTE
    assert checks["coverage"].ok is False
    assert "short" in checks["coverage"].detail


def test_coverage_still_fails_on_its_own_when_readings_agree():
    got = build_verdict(_facts(), _status(coverage_start="2026-08-08"), Reach.REACHABLE)
    checks = {check.name: check for check in got.checks}

    assert checks["readings"].severity is Severity.OK
    assert checks["coverage"].severity is Severity.FAIL
    assert "coverage" in got.headline.lower()


def test_the_headline_follows_the_fixed_precedence_order():
    """Coupled > Image > Panel > Readings > Coverage > Restarted."""
    every_fault = build_verdict(
        _facts(schema_version=2, seconds_since_last_push=86400.0),
        _status(wiped=True, panel="stuck", readings=1, coverage_start="2026-09-01", uptime_s=1),
        Reach.REACHABLE,
    )
    assert "wipe" in every_fault.headline.lower()

    without_coupled = build_verdict(
        _facts(schema_version=2, seconds_since_last_push=86400.0),
        _status(panel="stuck", readings=1, coverage_start="2026-09-01", uptime_s=1),
        Reach.REACHABLE,
    )
    assert "schema" in without_coupled.headline.lower()

    without_image = build_verdict(
        _facts(seconds_since_last_push=86400.0),
        _status(panel="stuck", readings=1, coverage_start="2026-09-01", uptime_s=1),
        Reach.REACHABLE,
    )
    assert "panel" in without_image.headline.lower()


def test_a_working_pi_headlines_nothing_but_working():
    got = build_verdict(*SCENARIOS["ok"])

    assert got.headline == "Working."
    assert got.glyph == "✓"
    assert got.ok is True
    assert all(check.severity is Severity.OK for check in got.checks)


# ---------------------------------------------------------------------------
# A malformed reply is a fault, not a crash
# ---------------------------------------------------------------------------


def test_a_reply_missing_a_field_fails_that_check_rather_than_raising():
    status = _status()
    del status["readings"]

    got = build_verdict(_facts(), status, Reach.REACHABLE)

    readings = {check.name: check for check in got.checks}["readings"]
    assert readings.ok is False
    assert readings.severity is Severity.FAIL


# ---------------------------------------------------------------------------
# The mirrored constants both the Panel and Image checks are derived from
# ---------------------------------------------------------------------------


def test_the_desktops_mirrors_still_match_the_pis_constants():
    """⚠ The Desktop and the Pi are different deployments — neither installs
    the other's code — so `REDRAW_FLOOR_S`, `GAUGE_EXPIRY_S` and the expected
    `SCHEMA_VERSION` are kept in step by hand. This is the test that catches
    an amendment on one end that was never re-applied to the other; without
    it, §8.3's Panel bound and Image check silently compare against stale
    numbers.
    """
    assert verdict_module.REDRAW_FLOOR_S == receive.REDRAW_FLOOR_S
    assert config.REDRAW_FLOOR_S == receive.REDRAW_FLOOR_S
    assert config.GAUGE_EXPIRY_S == receive.GAUGE_EXPIRY_S
    assert verdict_module.EXPECTED_PI_SCHEMA_VERSION == receive.SCHEMA_VERSION


# ---------------------------------------------------------------------------
# The reply is parsed JSON from another machine — absent or wrong-typed
# fields are faults, uniformly, not silently benign defaults.
# ---------------------------------------------------------------------------


def test_a_reply_with_no_wipe_flag_does_not_pass_as_coupled():
    """§8.3 Coupled is "a reply arrived **and** `wiped` is false" — reading a
    missing flag as "not wiped" is the one check that would silently pass."""
    status = _status()
    del status["wiped"]

    got = build_verdict(_facts(), status, Reach.REACHABLE)

    coupled = {check.name: check for check in got.checks}["coupled"]
    assert coupled.ok is False
    assert coupled.severity is Severity.FAIL
    assert got.state is State.NOT_WORKING


@pytest.mark.parametrize(
    "field,name",
    [("since_redraw_s", "panel"), ("uptime_s", "restarted"), ("readings", "readings")],
)
def test_a_wrong_typed_duration_or_count_fails_its_check_rather_than_raising(field, name):
    got = build_verdict(_facts(), _status(**{field: "soon"}), Reach.REACHABLE)

    check = {c.name: c for c in got.checks}[name]
    assert check.ok is False


def test_a_healthy_panel_that_has_never_drawn_is_a_contradiction_and_fails():
    """§5.4 reports `panel: "never"` for a Pi that has attempted nothing, so
    `panel: "ok"` with a null `since_redraw_s` cannot satisfy §8.3's rule."""
    got = build_verdict(_facts(), _status(since_redraw_s=None), Reach.REACHABLE)

    panel = {check.name: check for check in got.checks}["panel"]
    assert panel.ok is False
    assert panel.severity is Severity.FAIL


def test_a_desktop_that_has_never_pushed_claims_nothing_about_a_restart():
    """⚠ `OK` here would assert a comparison that never happened — the same
    rule the unreachable path states."""
    got = build_verdict(_facts(seconds_since_last_push=None), _status(), Reach.REACHABLE)

    restarted = {check.name: check for check in got.checks}["restarted"]
    assert restarted.ok is None
    assert restarted.severity is Severity.NOTE
    # A note never headlines, so the Verdict is still `working`.
    assert got.state is State.OK


def test_the_expected_schema_version_is_what_a_caller_gets_by_default():
    """The mirror guard is only worth having if a real caller compares
    against the constant it guards."""
    facts = DesktopFacts(
        pi_address="AA:BB:CC:DD:EE:FF",
        readings=312,
        coverage_start="2026-08-01",
        idle_keepalive_s=86400,
        seconds_since_last_push=300.0,
    )
    assert facts.schema_version == verdict_module.EXPECTED_PI_SCHEMA_VERSION

    got = build_verdict(facts, _status(schema_version=receive.SCHEMA_VERSION), Reach.REACHABLE)
    assert {c.name: c for c in got.checks}["image"].ok is True
