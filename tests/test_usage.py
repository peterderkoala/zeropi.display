"""Tests for desktop/usage.py (#42), against the synthetic fixture built by
#45 (tests/fixtures/README.md documents the 14 cases referenced below by
number).

Covers spec §11.3's assertions for this unit: pricing as a pure unit test,
dedup keep-first-vs-keep-winner, wire-format size, and store
idempotency/winner-clears-pushed_at/equal-winner-leaves-pushed_at-alone/
Reading-pending-iff-any-entry-unmarked.
"""

import json
import shutil
import sys
from pathlib import Path

import pytest

import usage

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "claude_projects"
MAIN_PROJECT_KEY = "-home-tester-code-zeropi-fixture"
MAIN_PROJECT_DIR = FIXTURES_DIR / MAIN_PROJECT_KEY
ALLZERO_PROJECT_KEY = "-home-tester-code-zeropi-allzero"
ALLZERO_PROJECT_DIR = FIXTURES_DIR / ALLZERO_PROJECT_KEY
MYPROJ_PROJECT_KEY = "-home-tester-code-myproj"
MYPROJ_PROJECT_DIR = FIXTURES_DIR / MYPROJ_PROJECT_KEY
APPEND_PROJECT_KEY = "-home-tester-code-zeropi-append"
APPEND_PROJECT_DIR = FIXTURES_DIR / APPEND_PROJECT_KEY


# ---------------------------------------------------------------------------
# §4.2 Pricing — pure unit tests
# ---------------------------------------------------------------------------


def test_price_for_prefix_matches_date_suffixed_model():
    # Case 5: claude-haiku-4-5-20251001 must prefix-match claude-haiku-4-5.
    rates, known = usage.price_for("claude-haiku-4-5-20251001")
    assert known is True
    assert rates == usage.PRICING["claude-haiku-4-5"]


def test_price_for_unknown_model_returns_no_rates_and_unknown():
    rates, known = usage.price_for("claude-unknown-9")
    assert rates is None
    assert known is False


def test_price_for_synthetic_is_free_and_known():
    rates, known = usage.price_for("<synthetic>")
    assert rates is None
    assert known is True


def test_compute_cost_prices_mixed_ttl_cache_writes_separately():
    # Case 2: ephemeral_5m (1000) and ephemeral_1h (2000) tokens, priced at
    # different rates. Must NOT flat-rate against the summed
    # cache_creation_input_tokens (3000).
    usage_obj = {
        "input_tokens": 500,
        "output_tokens": 300,
        "cache_creation_input_tokens": 3000,
        "cache_read_input_tokens": 1200,
        "cache_creation": {"ephemeral_5m_input_tokens": 1000, "ephemeral_1h_input_tokens": 2000},
    }
    cost, known = usage.compute_cost(usage_obj, "claude-sonnet-5")
    assert known is True
    rates = usage.PRICING["claude-sonnet-5"]
    expected = (
        500 * rates["in"]
        + 300 * rates["out"]
        + 1000 * rates["w5m"]
        + 2000 * rates["w1h"]
        + 1200 * rates["read"]
    ) / 1_000_000.0
    assert cost == pytest.approx(expected)

    # A flat-rate implementation (pricing the summed 3000 at a single rate)
    # would produce a different number — assert we are not doing that.
    flat_wrong = (
        500 * rates["in"] + 300 * rates["out"] + 3000 * rates["w5m"] + 1200 * rates["read"]
    ) / 1_000_000.0
    assert cost != pytest.approx(flat_wrong)


def test_compute_cost_defaults_absent_output_tokens_details_to_zero():
    # Case 3: output_tokens_details absent entirely — must not raise, and
    # cost must equal the case with an explicit zero.
    usage_obj = {
        "input_tokens": 250,
        "output_tokens": 90,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0},
    }
    cost, known = usage.compute_cost(usage_obj, "claude-opus-5")
    assert known is True
    rates = usage.PRICING["claude-opus-5"]
    expected = (250 * rates["in"] + 90 * rates["out"]) / 1_000_000.0
    assert cost == pytest.approx(expected)


def test_compute_cost_does_not_add_thinking_tokens():
    # Case 4: thinking_tokens present and non-zero must not change cost —
    # it is already inside output_tokens.
    usage_obj = {
        "input_tokens": 400,
        "output_tokens": 200,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0},
        "output_tokens_details": {"thinking_tokens": 150},
    }
    cost, _known = usage.compute_cost(usage_obj, "claude-opus-5")
    rates = usage.PRICING["claude-opus-5"]
    expected = (400 * rates["in"] + 200 * rates["out"]) / 1_000_000.0
    assert cost == pytest.approx(expected)


def test_compute_cost_unknown_model_counts_no_cost_but_is_unknown():
    # Case 6.
    usage_obj = {"input_tokens": 80, "output_tokens": 20}
    cost, known = usage.compute_cost(usage_obj, "claude-unknown-9")
    assert cost == 0.0
    assert known is False


def test_compute_cost_adds_web_search_requests():
    usage_obj = {
        "input_tokens": 0,
        "output_tokens": 0,
        "server_tool_use": {"web_search_requests": 3, "web_fetch_requests": 100},
    }
    cost, _known = usage.compute_cost(usage_obj, "claude-opus-5")
    assert cost == pytest.approx(3 * usage.WEB_SEARCH_USD_PER_REQUEST)


def test_normalise_model_matches_prefix_and_leaves_unknown_raw():
    assert usage.normalise_model("claude-haiku-4-5-20251001") == "claude-haiku-4-5"
    assert usage.normalise_model("claude-unknown-9") == "claude-unknown-9"
    assert usage.normalise_model("<synthetic>") == "<synthetic>"


# ---------------------------------------------------------------------------
# §4.3 Project identity and label derivation
# ---------------------------------------------------------------------------


def test_encode_matches_fixture_directory_names():
    assert usage.encode("/home/tester/code/zeropi-fixture") == MAIN_PROJECT_KEY
    assert usage.encode("/home/tester/code/myproj") == MYPROJ_PROJECT_KEY


def test_project_label_r1_basename_of_matching_cwd():
    cwds = usage.collect_cwds(MAIN_PROJECT_DIR)
    label = usage.derive_project_label(MAIN_PROJECT_KEY, cwds)
    assert label.rule == "R1"
    assert label.label == "zeropi-fixture"
    assert label.verified is True


def test_project_label_r2_worktree_truncation():
    # Case 12: only cwd is a worktree path; R1 fails, R2 must succeed.
    cwds = usage.collect_cwds(MYPROJ_PROJECT_DIR)
    label = usage.derive_project_label(MYPROJ_PROJECT_KEY, cwds)
    assert label.rule == "R2"
    assert label.label == "myproj"
    assert label.verified is True


def test_project_label_r4_is_lossy_and_unverified():
    label = usage.derive_project_label("-messagebroker-demo", ["/nowhere/matching"])
    assert label.rule == "R4"
    assert label.label == "demo"
    assert label.verified is False


# ---------------------------------------------------------------------------
# §4.4 Date bucketing
# ---------------------------------------------------------------------------


def test_local_date_buckets_either_side_of_midnight(monkeypatch):
    # Case 10: America/New_York (UTC-4 in September) local midnight is
    # 04:00Z. req-midnight-before (02:00Z) and req-midnight-after (05:00Z)
    # must bucket to different local dates under that zone.
    monkeypatch.setenv("TZ", "America/New_York")
    import time

    time.tzset()
    try:
        assert usage.local_date("2026-09-02T02:00:00.000Z") == "2026-09-01"
        assert usage.local_date("2026-09-02T05:00:00.000Z") == "2026-09-02"
    finally:
        time.tzset()


# ---------------------------------------------------------------------------
# §4.1 Reader: selection, dedup, winner rank
# ---------------------------------------------------------------------------


def _entries_by_request_id(entries):
    return {e.request_id: e for e in entries}


def test_reader_selects_only_assistant_entries_with_usage():
    entries = usage.read_usage_entries(MAIN_PROJECT_KEY, MAIN_PROJECT_DIR)
    # The leading `type: "user"` line must never appear.
    assert all(e.request_id != "" for e in entries)
    by_id = _entries_by_request_id(entries)
    assert "req-dup1" in by_id


def _base_entry():
    return {
        "type": "assistant",
        "sessionId": "sess-x",
        "requestId": "req-x",
        "cwd": "/home/tester/code/zeropi-fixture",
        "isSidechain": False,
        "message": {
            "id": "msg-x",
            "model": "claude-opus-5",
            "role": "assistant",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
    }


def test_reader_skips_entry_with_no_timestamp_instead_of_crashing(tmp_path):
    entry = _base_entry()  # no "timestamp" key at all
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()
    (proj_dir / "session.jsonl").write_text(json.dumps(entry) + "\n")

    entries = usage.read_usage_entries("proj", proj_dir)
    assert entries == []


def test_reader_skips_entry_with_unparseable_timestamp_instead_of_crashing(tmp_path):
    entry = _base_entry()
    entry["timestamp"] = "not-a-timestamp"
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()
    (proj_dir / "session.jsonl").write_text(json.dumps(entry) + "\n")

    entries = usage.read_usage_entries("proj", proj_dir)
    assert entries == []


def test_reader_skips_entry_with_missing_model_instead_of_crashing(tmp_path):
    entry = _base_entry()
    entry["timestamp"] = "2026-09-01T10:00:00.000Z"
    del entry["message"]["model"]
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()
    (proj_dir / "session.jsonl").write_text(json.dumps(entry) + "\n")

    entries = usage.read_usage_entries("proj", proj_dir)
    assert entries == []


def test_local_date_returns_none_on_unparseable_timestamp():
    assert usage.local_date("not-a-timestamp") is None


def test_reader_keeps_winner_not_first_copy():
    # Case 1: keep-winner must select the 114-output-token final copy, not
    # the first (5) or second (40) provisional copy.
    entries = _entries_by_request_id(usage.read_usage_entries(MAIN_PROJECT_KEY, MAIN_PROJECT_DIR))
    assert entries["req-dup1"].output_tokens == 114


def test_keep_first_vs_keep_winner_differ_on_case_1(tmp_path):
    # spec §11.3: dedup keep-first and keep-winner must produce measurably
    # different totals on fixture case 1. We only implement keep-winner in
    # usage.py; this test demonstrates that a naive keep-first over the same
    # raw lines would have produced a different (lower) total, proving the
    # winner-rank logic is actually doing something on this fixture.
    path = MAIN_PROJECT_DIR / "session-main.jsonl"
    raw_lines = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    dup_lines = [l for l in raw_lines if l.get("requestId") == "req-dup1"]
    assert len(dup_lines) == 3

    keep_first_output = dup_lines[0]["message"]["usage"]["output_tokens"]
    entries = _entries_by_request_id(usage.read_usage_entries(MAIN_PROJECT_KEY, MAIN_PROJECT_DIR))
    keep_winner_output = entries["req-dup1"].output_tokens

    assert keep_first_output == 5
    assert keep_winner_output == 114
    assert keep_first_output != keep_winner_output


def test_reader_reads_totals_from_top_level_usage_not_iterations():
    # Case 1 lines also carry a `usage.iterations` object restating
    # different totals per line; the winner's stored tokens must come from
    # the top-level fields (114), matching the final line's top-level usage.
    entries = _entries_by_request_id(usage.read_usage_entries(MAIN_PROJECT_KEY, MAIN_PROJECT_DIR))
    assert entries["req-dup1"].output_tokens == 114
    assert entries["req-dup1"].input_tokens == 100


def test_reader_drops_unkeyed_entries_keeps_synthetic():
    # Case 7: the requestId-less line must be dropped; the <synthetic>
    # all-zero entry (which does have requestId/message.id) is kept, free,
    # and cost_complete stays true.
    entries = _entries_by_request_id(usage.read_usage_entries(MAIN_PROJECT_KEY, MAIN_PROJECT_DIR))
    assert "req-synth1" in entries
    assert entries["req-synth1"].cost_usd == 0.0
    assert entries["req-synth1"].cost_complete is True
    assert not any(e.message_id == "msg-norequest" for e in entries.values())


def test_reader_includes_sidechain_entries():
    # Case 8 (reader half): isSidechain entries are counted, not filtered.
    entries = _entries_by_request_id(usage.read_usage_entries(MAIN_PROJECT_KEY, MAIN_PROJECT_DIR))
    assert "req-sidechain1" in entries
    assert entries["req-sidechain1"].output_tokens == 999


def test_reader_counts_nested_subagent_jsonl():
    # sess-mainA/subagents/sub1.jsonl must attribute to the same project via
    # the recursive glob.
    entries = _entries_by_request_id(usage.read_usage_entries(MAIN_PROJECT_KEY, MAIN_PROJECT_DIR))
    assert "req-subagent1" in entries
    assert entries["req-subagent1"].model == "claude-haiku-4-5"


def test_reader_normalises_date_suffixed_model():
    entries = _entries_by_request_id(usage.read_usage_entries(MAIN_PROJECT_KEY, MAIN_PROJECT_DIR))
    assert entries["req-datesuffix"].model == "claude-haiku-4-5"


def test_reader_unknown_model_counts_tokens_zero_cost_incomplete():
    entries = _entries_by_request_id(usage.read_usage_entries(MAIN_PROJECT_KEY, MAIN_PROJECT_DIR))
    entry = entries["req-unknownmodel"]
    assert entry.input_tokens == 80
    assert entry.output_tokens == 20
    assert entry.cost_usd == 0.0
    assert entry.cost_complete is False


def test_reader_buckets_local_date_per_entry(monkeypatch):
    import time

    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    try:
        entries = _entries_by_request_id(usage.read_usage_entries(MAIN_PROJECT_KEY, MAIN_PROJECT_DIR))
        assert entries["req-midnight-before"].local_date == "2026-09-01"
        assert entries["req-midnight-after"].local_date == "2026-09-02"
    finally:
        time.tzset()


# ---------------------------------------------------------------------------
# §11.3 Wire format
# ---------------------------------------------------------------------------


def test_daily_payload_serialises_under_514_bytes_for_maximal_row():
    reading = usage.Reading(
        date="2026-09-05",
        project_key="-home-someone-very-long-project-path-name-for-testing-worst-case",
        model="claude-opus-5",
        input_tokens=999999999,
        output_tokens=999999999,
        cache_creation_tokens=999999999,
        cache_read_tokens=999999999,
        cost_usd=999999.9999,
        session_count=999999,
        cost_complete=False,
        pending=True,
    )
    payload = usage.reading_to_daily_payload(
        reading, desktop_id="9f2c1ab34d5e6f70", batch_size=20, batch_index=19
    )
    encoded = json.dumps(payload).encode("utf-8")
    assert len(encoded) < 514


def test_daily_payload_typical_row_serialises_well_under_514_bytes():
    reading = usage.Reading(
        date="2026-09-05",
        project_key="-home-ryzen-git-zeropi-display",
        model="claude-opus-5",
        input_tokens=12345,
        output_tokens=6789,
        cache_creation_tokens=201775,
        cache_read_tokens=8727247,
        cost_usd=33.5412,
        session_count=4,
        cost_complete=True,
        pending=True,
    )
    payload = usage.reading_to_daily_payload(
        reading, desktop_id="9f2c1ab34d5e6f70", batch_size=3, batch_index=0
    )
    encoded = json.dumps(payload).encode("utf-8")
    assert len(encoded) < 514


# ---------------------------------------------------------------------------
# §4.5/§4.6 The store: idempotency, pushed_at rules, pending Readings
# ---------------------------------------------------------------------------


def test_store_open_creates_schema_and_sets_user_version(tmp_path):
    store_path = tmp_path / "store.db"
    conn = usage.open_store(store_path)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == usage.STORE_USER_VERSION
    conn.close()


def test_store_refuses_to_run_on_version_mismatch(tmp_path):
    store_path = tmp_path / "store.db"
    conn = usage.open_store(store_path)
    conn.execute("PRAGMA user_version = 99")
    conn.commit()
    conn.close()

    with pytest.raises(usage.StoreVersionError):
        usage.open_store(store_path)


def test_ingest_is_idempotent(tmp_path):
    store_path = tmp_path / "store.db"
    conn = usage.open_store(store_path)
    entries = usage.read_usage_entries(MAIN_PROJECT_KEY, MAIN_PROJECT_DIR)
    usage.ingest_entries(conn, entries)
    count_after_first = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]

    usage.ingest_entries(conn, entries)
    count_after_second = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]

    assert count_after_first == count_after_second
    conn.close()


def test_ingest_better_winner_clears_pushed_at(tmp_path):
    store_path = tmp_path / "store.db"
    conn = usage.open_store(store_path)

    loser = usage.UsageEntry(
        request_id="req-x", message_id="msg-x", session_id="s1",
        project_key="proj", cwd=None, local_date="2026-09-01", model="claude-opus-5",
        input_tokens=100, output_tokens=5, cache_write_5m_tokens=0,
        cache_write_1h_tokens=0, cache_read_tokens=0, web_search_requests=0,
        cost_usd=0.001, cost_complete=True,
        rank_sidechain=1, rank_tokens=105, rank_speed=0,
        source_file="/fake/a.jsonl", source_end_offset=100,
    )
    usage.ingest_entries(conn, [loser])
    usage.mark_pushed(conn, "2026-09-01", "proj", "claude-opus-5", "2026-09-05T00:00:00Z")

    row = conn.execute("SELECT pushed_at FROM entries WHERE request_id='req-x'").fetchone()
    assert row["pushed_at"] is not None

    winner = usage.UsageEntry(
        request_id="req-x", message_id="msg-x", session_id="s1",
        project_key="proj", cwd=None, local_date="2026-09-01", model="claude-opus-5",
        input_tokens=100, output_tokens=114, cache_write_5m_tokens=0,
        cache_write_1h_tokens=0, cache_read_tokens=0, web_search_requests=0,
        cost_usd=0.003, cost_complete=True,
        rank_sidechain=1, rank_tokens=214, rank_speed=1,
        source_file="/fake/a.jsonl", source_end_offset=200,
    )
    usage.ingest_entries(conn, [winner])

    row = conn.execute("SELECT output_tokens, pushed_at FROM entries WHERE request_id='req-x'").fetchone()
    assert row["output_tokens"] == 114
    assert row["pushed_at"] is None
    conn.close()


def test_ingest_equal_winner_leaves_pushed_at_alone(tmp_path):
    store_path = tmp_path / "store.db"
    conn = usage.open_store(store_path)

    entry = usage.UsageEntry(
        request_id="req-y", message_id="msg-y", session_id="s1",
        project_key="proj", cwd=None, local_date="2026-09-01", model="claude-opus-5",
        input_tokens=100, output_tokens=114, cache_write_5m_tokens=0,
        cache_write_1h_tokens=0, cache_read_tokens=0, web_search_requests=0,
        cost_usd=0.003, cost_complete=True,
        rank_sidechain=1, rank_tokens=214, rank_speed=1,
        source_file="/fake/a.jsonl", source_end_offset=200,
    )
    usage.ingest_entries(conn, [entry])
    usage.mark_pushed(conn, "2026-09-01", "proj", "claude-opus-5", "2026-09-05T00:00:00Z")

    # Re-ingest an identical (equal-rank) entry.
    usage.ingest_entries(conn, [entry])

    row = conn.execute("SELECT pushed_at FROM entries WHERE request_id='req-y'").fetchone()
    assert row["pushed_at"] is not None
    conn.close()


def test_ingest_case14_reread_does_not_overwrite_stronger_stored_winner(tmp_path):
    # Case 14: a line re-read on a later run, sharing a key with an
    # already-ingested stronger winner, must not overwrite it and must not
    # clear pushed_at.
    store_path = tmp_path / "store.db"
    conn = usage.open_store(store_path)

    main_entries = usage.read_usage_entries(MAIN_PROJECT_KEY, MAIN_PROJECT_DIR)
    usage.ingest_entries(conn, main_entries)
    usage.mark_pushed(conn, "2026-09-01", MAIN_PROJECT_KEY, "claude-opus-5", "2026-09-05T00:00:00Z")

    before = conn.execute(
        "SELECT output_tokens, pushed_at FROM entries WHERE request_id='req-dup1'"
    ).fetchone()
    assert before["output_tokens"] == 114
    assert before["pushed_at"] is not None

    # Read only the reread file in isolation, in its own directory, to force
    # the store-time comparison — reading the whole project directory at
    # once would already pick the winner before ingest ever sees the loser.
    reread_only_dir = tmp_path / "reread_only"
    reread_only_dir.mkdir()
    shutil.copyfile(
        MAIN_PROJECT_DIR / "session-case14-reread.jsonl",
        reread_only_dir / "session-case14-reread.jsonl",
    )
    single_file_entries = usage.read_usage_entries(MAIN_PROJECT_KEY, reread_only_dir)
    assert len(single_file_entries) == 1
    assert single_file_entries[0].output_tokens == 5

    usage.ingest_entries(conn, single_file_entries)

    after = conn.execute(
        "SELECT output_tokens, pushed_at FROM entries WHERE request_id='req-dup1'"
    ).fetchone()
    assert after["output_tokens"] == 114
    assert after["pushed_at"] is not None
    conn.close()


def test_high_water_mark_resumes_appended_file(tmp_path):
    # Case 13: a session file appended to between two ingest runs. Resume
    # must pick up only the appended line, not re-process or skip the first.
    live_dir = tmp_path / "claude_projects" / APPEND_PROJECT_KEY
    live_dir.mkdir(parents=True)
    live_file = live_dir / "session-live.jsonl"

    stage1 = APPEND_PROJECT_DIR / "session-case13-stage1.jsonl"
    stage2 = APPEND_PROJECT_DIR / "session-case13-stage2.jsonl"

    shutil.copyfile(stage1, live_file)

    store_path = tmp_path / "store.db"
    conn = usage.open_store(store_path)

    offsets = usage.high_water_marks(conn)
    entries_pass1 = usage.read_usage_entries(APPEND_PROJECT_KEY, live_dir, min_offsets=offsets)
    assert {e.request_id for e in entries_pass1} == {"req-append1"}
    usage.ingest_entries(conn, entries_pass1)

    shutil.copyfile(stage2, live_file)

    offsets = usage.high_water_marks(conn)
    entries_pass2 = usage.read_usage_entries(APPEND_PROJECT_KEY, live_dir, min_offsets=offsets)
    assert {e.request_id for e in entries_pass2} == {"req-append2"}
    usage.ingest_entries(conn, entries_pass2)

    all_ids = {
        row["request_id"]
        for row in conn.execute("SELECT request_id FROM entries").fetchall()
    }
    assert all_ids == {"req-append1", "req-append2"}
    conn.close()


def test_second_session_gives_distinct_session_count(tmp_path):
    # Case 9: two sessions on the same (date, project, model).
    store_path = tmp_path / "store.db"
    conn = usage.open_store(store_path)
    entries = usage.read_usage_entries(MAIN_PROJECT_KEY, MAIN_PROJECT_DIR)
    usage.ingest_entries(conn, entries)

    readings = usage.aggregate_readings(conn, window=["2026-09-01"])
    opus_2026_09_01 = [
        r for r in readings if r.project_key == MAIN_PROJECT_KEY and r.model == "claude-opus-5"
    ]
    assert len(opus_2026_09_01) == 1
    assert opus_2026_09_01[0].session_count == 2
    conn.close()


def test_all_zero_reading_is_dropped(tmp_path):
    # Case 11.
    store_path = tmp_path / "store.db"
    conn = usage.open_store(store_path)
    entries = usage.read_usage_entries(ALLZERO_PROJECT_KEY, ALLZERO_PROJECT_DIR)
    usage.ingest_entries(conn, entries)

    readings = usage.aggregate_readings(conn, window=["2026-09-03"])
    assert readings == []
    conn.close()


def test_reading_pending_iff_any_entry_unmarked(tmp_path):
    store_path = tmp_path / "store.db"
    conn = usage.open_store(store_path)
    entries = usage.read_usage_entries(MAIN_PROJECT_KEY, MAIN_PROJECT_DIR)
    usage.ingest_entries(conn, entries)

    readings = usage.aggregate_readings(conn, window=["2026-09-01"])
    opus = next(r for r in readings if r.model == "claude-opus-5" and r.project_key == MAIN_PROJECT_KEY)
    assert opus.pending is True  # nothing marked pushed yet

    usage.mark_pushed(conn, "2026-09-01", MAIN_PROJECT_KEY, "claude-opus-5", "2026-09-05T00:00:00Z")
    readings = usage.aggregate_readings(conn, window=["2026-09-01"])
    opus = next(r for r in readings if r.model == "claude-opus-5" and r.project_key == MAIN_PROJECT_KEY)
    assert opus.pending is False

    # A late-arriving entry for the same group re-opens it (simulated here
    # by ingesting one more, unmarked, entry into that same group).
    late_entry = usage.UsageEntry(
        request_id="req-late", message_id="msg-late", session_id="sess-mainA",
        project_key=MAIN_PROJECT_KEY, cwd=None, local_date="2026-09-01", model="claude-opus-5",
        input_tokens=1, output_tokens=1, cache_write_5m_tokens=0,
        cache_write_1h_tokens=0, cache_read_tokens=0, web_search_requests=0,
        cost_usd=0.0001, cost_complete=True,
        rank_sidechain=1, rank_tokens=2, rank_speed=1,
        source_file="/fake/late.jsonl", source_end_offset=1,
    )
    usage.ingest_entries(conn, [late_entry])
    readings = usage.aggregate_readings(conn, window=["2026-09-01"])
    opus = next(r for r in readings if r.model == "claude-opus-5" and r.project_key == MAIN_PROJECT_KEY)
    assert opus.pending is True
    conn.close()


def test_pending_readings_filters_to_pending_only(tmp_path):
    store_path = tmp_path / "store.db"
    conn = usage.open_store(store_path)
    entries = usage.read_usage_entries(MAIN_PROJECT_KEY, MAIN_PROJECT_DIR)
    usage.ingest_entries(conn, entries)

    all_readings = usage.aggregate_readings(conn, window=["2026-09-01", "2026-09-02"])
    for r in all_readings:
        usage.mark_pushed(conn, r.date, r.project_key, r.model, "2026-09-05T00:00:00Z")

    assert usage.pending_readings(conn, window=["2026-09-01", "2026-09-02"]) == []


def test_window_dates_covers_seven_days_ending_today():
    import datetime as dt

    today = dt.date(2026, 9, 10)
    days = usage.window_dates(today=today)
    assert len(days) == 7
    assert days[-1] == "2026-09-10"
    assert days[0] == "2026-09-04"


def test_readings_ordered_newest_date_first(tmp_path):
    store_path = tmp_path / "store.db"
    conn = usage.open_store(store_path)
    entries = usage.read_usage_entries(MAIN_PROJECT_KEY, MAIN_PROJECT_DIR)
    usage.ingest_entries(conn, entries)

    readings = usage.aggregate_readings(conn, window=["2026-09-01", "2026-09-02"])
    dates = [r.date for r in readings]
    assert dates == sorted(dates, reverse=True)
