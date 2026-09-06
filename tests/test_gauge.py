"""Tests for desktop/gauge.py (spec §5, ticket #43).

Runs with no BLE, no Pi, no real ~/.claude present, and no real /proc pid
liveness — every path gauge.py reads is taken as an argument, and session
liveness goes through an injectable `pid_alive` callable (spec §11.1, and
the ticket's testability seam for §5.2's liveness check).
"""

from datetime import datetime, timezone
from pathlib import Path

import gauge

FIXTURES = Path(__file__).parent / "fixtures" / "gauge"
RATE_LIMITS = FIXTURES / "rate_limits"
SESSIONS = FIXTURES / "sessions"
PROJECTS = FIXTURES / "projects"

# The fixtures' snapshots are all written as of this instant.
NOW_FRESH = datetime(2026, 9, 5, 9, 0, 0, tzinfo=timezone.utc)


def always_alive(pid):
    return True


def alive_except(*dead_pids):
    def check(pid):
        return pid not in dead_pids

    return check


# --- §5.1 rate-limit snapshot ------------------------------------------------


def test_absent_snapshot_file_yields_no_gauge_never_zero():
    missing = RATE_LIMITS / "does-not-exist.json"
    assert gauge.read_snapshot(missing) is None
    assert gauge.build_gauge_payload(now=NOW_FRESH, rate_limits_path=missing) is None


def test_corrupt_snapshot_file_degrades_like_absent_never_crashes(tmp_path):
    corrupt = tmp_path / "rate-limits.json"
    corrupt.write_text("{not valid json")
    assert gauge.read_snapshot(corrupt) is None
    assert gauge.build_gauge_payload(now=NOW_FRESH, rate_limits_path=corrupt) is None


def test_valid_snapshot_percentages_and_countdowns():
    snapshot = gauge.read_snapshot(RATE_LIMITS / "valid.json")
    snapshot_age_s, five_hour, seven_day = gauge.compute_windows(snapshot, NOW_FRESH)

    assert snapshot_age_s == 22  # 08:59:38 -> 09:00:00
    assert five_hour["pct"] == 26
    assert five_hour["resets_in_s"] == 9600  # 11:40:00 - 09:00:00
    assert seven_day["pct"] == 18
    assert seven_day["resets_in_s"] > 0


def test_null_used_percentage_survives_as_null_never_zero():
    snapshot = gauge.read_snapshot(RATE_LIMITS / "null_windows.json")
    _, five_hour, seven_day = gauge.compute_windows(snapshot, NOW_FRESH)

    assert five_hour["pct"] is None
    assert five_hour["resets_in_s"] is None
    assert seven_day["pct"] is None
    assert seven_day["resets_in_s"] is None
    # Explicitly not zero.
    assert five_hour["pct"] != 0
    assert seven_day["pct"] != 0


def test_countdown_clamps_at_zero_for_a_past_resets_at():
    snapshot = gauge.read_snapshot(RATE_LIMITS / "valid.json")
    # 11:40:00 resets_at, well before this "now".
    far_future = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)
    _, five_hour, _ = gauge.compute_windows(snapshot, far_future)
    assert five_hour["resets_in_s"] == 0


def test_snapshot_age_clamps_at_zero_when_updated_at_is_in_the_future():
    snapshot = gauge.read_snapshot(RATE_LIMITS / "valid.json")
    earlier = datetime(2026, 9, 5, 8, 0, 0, tzinfo=timezone.utc)
    snapshot_age_s, _, _ = gauge.compute_windows(snapshot, earlier)
    assert snapshot_age_s == 0


def test_stale_snapshot_is_refused():
    # valid.json's updated_at is 08:59:38Z; 300s later is 09:04:38Z.
    stale_now = datetime(2026, 9, 5, 9, 5, 0, tzinfo=timezone.utc)
    payload = gauge.build_gauge_payload(
        now=stale_now,
        rate_limits_path=RATE_LIMITS / "valid.json",
        sessions_dir=SESSIONS,
        projects_root=PROJECTS,
        pid_alive=always_alive,
    )
    assert payload is None


def test_fresh_snapshot_just_under_the_threshold_is_pushed():
    just_fresh_now = datetime(2026, 9, 5, 9, 4, 37, tzinfo=timezone.utc)
    payload = gauge.build_gauge_payload(
        now=just_fresh_now,
        rate_limits_path=RATE_LIMITS / "valid.json",
        sessions_dir=SESSIONS,
        projects_root=PROJECTS,
        pid_alive=always_alive,
    )
    assert payload is not None
    assert payload["snapshot_age_s"] < 300


# --- §5.2 active session and context size -----------------------------------


def test_active_session_is_most_recent_interactive_live_session():
    session = gauge.find_active_session(SESSIONS, pid_alive=always_alive)
    assert session is not None
    assert session["sessionId"] == "sess-gaugeA"  # 40002, newest interactive


def test_headless_session_excluded_even_if_most_recently_updated():
    # 40003 (headless) has the freshest updatedAt of any fixture file.
    session = gauge.find_active_session(SESSIONS, pid_alive=always_alive)
    assert session["sessionId"] != "sess-headless"


def test_dead_pid_excluded_by_liveness_check():
    # With 40002 reported dead, 40001 (older, but alive+interactive) wins.
    session = gauge.find_active_session(SESSIONS, pid_alive=alive_except(40002, 40003, 40004))
    assert session["sessionId"] == "sess-older"


def test_no_live_session_yields_none():
    session = gauge.find_active_session(SESSIONS, pid_alive=lambda pid: False)
    assert session is None


def test_no_live_session_means_context_is_null():
    assert gauge.read_context_size(None, PROJECTS) is None


def test_context_size_skips_sidechain_entries():
    session = gauge.find_active_session(SESSIONS, pid_alive=always_alive)
    context = gauge.read_context_size(session, PROJECTS)
    assert context is not None
    # 200 input + 50 cache_creation + 50 cache_read = 300, NOT the
    # chronologically-later sidechain entry's 900.
    assert context["tokens"] == 300
    assert context["model"] == "claude-sonnet-5"
    assert context["pct"] == 0  # 300 / 1_000_000, rounded


def test_context_pct_unknown_model_is_null_but_tokens_still_reported():
    pct = gauge.context_pct("claude-unknown-9", 12345)
    assert pct is None


def test_context_pct_known_model_prefix_matched():
    # Date-suffixed id, same prefix-match pattern as the pricing table.
    pct = gauge.context_pct("claude-haiku-4-5-20251001", 100_000)
    assert pct == 50


# --- End-to-end build_gauge_payload -----------------------------------------


def test_build_gauge_payload_full_shape():
    payload = gauge.build_gauge_payload(
        now=NOW_FRESH,
        rate_limits_path=RATE_LIMITS / "valid.json",
        sessions_dir=SESSIONS,
        projects_root=PROJECTS,
        pid_alive=always_alive,
    )
    assert payload is not None
    assert set(payload.keys()) == {"snapshot_age_s", "five_hour", "seven_day", "context"}
    assert payload["five_hour"] == {"pct": 26, "resets_in_s": 9600}
    assert payload["context"]["tokens"] == 300


def test_build_gauge_payload_no_live_session_has_null_context():
    payload = gauge.build_gauge_payload(
        now=NOW_FRESH,
        rate_limits_path=RATE_LIMITS / "valid.json",
        sessions_dir=SESSIONS,
        projects_root=PROJECTS,
        pid_alive=lambda pid: False,
    )
    assert payload is not None
    assert payload["context"] is None
