"""Tests for pi/render.py's Historic View data layer (spec §6, ticket #62).

Pure SQLite against a scratch file -- no PIL, no driver, no clock. `pi` is on
pythonpath via pytest.ini, and `receive.init_db`/`upsert_reading` build the
fixture data through the same path production writes go through.
"""

import sqlite3

import pytest

import receive
import render


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "data.db")


def daily_payload(**overrides):
    payload = {
        "kind": "daily",
        "desktop_id": "desktop-a",
        "batch_size": 1,
        "batch_index": 0,
        "date": "2026-09-05",
        "project": "-home-ryzen-git-zeropi-display",
        "model": "claude-opus-5",
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_creation_tokens": 10,
        "cache_read_tokens": 5,
        "cost_usd": 1.2345,
        "session_count": 1,
        "cost_complete": True,
    }
    payload.update(overrides)
    return payload


def _conn(db_path):
    receive.init_db(db_path)
    return sqlite3.connect(db_path)


# ---------------------------------------------------------------------------
# historic_rows
# ---------------------------------------------------------------------------


def test_historic_rows_returns_five_most_recent_newest_first(db_path):
    conn = _conn(db_path)
    try:
        for i, date in enumerate(
            ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04",
             "2026-09-05", "2026-09-06", "2026-09-07"]
        ):
            receive.upsert_reading(conn, daily_payload(date=date, cost_usd=float(i)))
        conn.commit()

        rows = render.historic_rows(conn)

        assert [r.date for r in rows] == [
            "2026-09-07", "2026-09-06", "2026-09-05", "2026-09-04", "2026-09-03",
        ]
    finally:
        conn.close()


def test_historic_rows_sums_cost_across_projects_and_models_for_one_date(db_path):
    conn = _conn(db_path)
    try:
        receive.upsert_reading(conn, daily_payload(project="proj-a", model="claude-opus-5", cost_usd=1.0))
        receive.upsert_reading(conn, daily_payload(project="proj-b", model="claude-sonnet-5", cost_usd=2.5))
        conn.commit()

        rows = render.historic_rows(conn)

        assert len(rows) == 1
        assert rows[0].total_cost == pytest.approx(3.5)
    finally:
        conn.close()


def test_historic_rows_fewer_than_five_active_days_returns_them_all(db_path):
    conn = _conn(db_path)
    try:
        receive.upsert_reading(conn, daily_payload(date="2026-09-01"))
        receive.upsert_reading(conn, daily_payload(date="2026-09-02", model="claude-sonnet-5"))
        conn.commit()

        rows = render.historic_rows(conn)

        assert [r.date for r in rows] == ["2026-09-02", "2026-09-01"]
    finally:
        conn.close()


def test_historic_rows_no_active_days_returns_empty(db_path):
    conn = _conn(db_path)
    try:
        assert render.historic_rows(conn) == []
    finally:
        conn.close()


def test_historic_rows_incomplete_iff_any_reading_that_day_is_incomplete(db_path):
    conn = _conn(db_path)
    try:
        # Both readings on this date complete -> the day is complete.
        receive.upsert_reading(conn, daily_payload(date="2026-09-01", project="proj-a", cost_complete=True))
        receive.upsert_reading(conn, daily_payload(date="2026-09-01", project="proj-b", cost_complete=True))
        # One incomplete reading on this date -> the whole day is incomplete.
        receive.upsert_reading(conn, daily_payload(date="2026-09-02", project="proj-a", cost_complete=True))
        receive.upsert_reading(conn, daily_payload(date="2026-09-02", project="proj-b", cost_complete=False))
        conn.commit()

        rows = {r.date: r for r in render.historic_rows(conn)}

        assert rows["2026-09-01"].incomplete is False
        assert rows["2026-09-02"].incomplete is True
    finally:
        conn.close()


def test_historic_rows_active_day_is_any_row_not_cost_greater_than_zero(db_path):
    # An unpriced model yields real tokens at $0 (spec §6) -- the day must
    # still count as active, not be treated as though nothing happened.
    conn = _conn(db_path)
    try:
        receive.upsert_reading(conn, daily_payload(date="2026-09-01", cost_usd=0.0))
        conn.commit()

        rows = render.historic_rows(conn)

        assert len(rows) == 1
        assert rows[0].date == "2026-09-01"
        assert rows[0].total_cost == 0.0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# historic_average
# ---------------------------------------------------------------------------


def test_historic_average_is_over_every_active_day_not_just_the_five_shown(db_path):
    conn = _conn(db_path)
    try:
        # Seven Active Days, each costing 1.0 -> average must still be 1.0
        # even though historic_rows only shows the most recent five.
        for date in ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04",
                     "2026-09-05", "2026-09-06", "2026-09-07"]:
            receive.upsert_reading(conn, daily_payload(date=date, cost_usd=1.0))
        conn.commit()

        assert render.historic_average(conn) == pytest.approx(1.0)
    finally:
        conn.close()


def test_historic_average_weights_by_day_not_by_row(db_path):
    conn = _conn(db_path)
    try:
        # Day 1: two rows summing to 4.0. Day 2: one row of 2.0.
        # Average of DAILY TOTALS is (4.0 + 2.0) / 2 = 3.0, not a per-row mean.
        receive.upsert_reading(conn, daily_payload(date="2026-09-01", project="proj-a", cost_usd=1.0))
        receive.upsert_reading(conn, daily_payload(date="2026-09-01", project="proj-b", cost_usd=3.0))
        receive.upsert_reading(conn, daily_payload(date="2026-09-02", cost_usd=2.0))
        conn.commit()

        assert render.historic_average(conn) == pytest.approx(3.0)
    finally:
        conn.close()


def test_historic_average_with_no_active_days_is_none(db_path):
    conn = _conn(db_path)
    try:
        assert render.historic_average(conn) is None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# coverage_start
# ---------------------------------------------------------------------------


def test_coverage_start_returns_meta_value_verbatim(db_path):
    conn = _conn(db_path)
    try:
        receive.upsert_reading(conn, daily_payload(date="2026-09-05"))
        conn.commit()

        assert render.coverage_start(conn) == "2026-09-05"
    finally:
        conn.close()


def test_coverage_start_absent_is_none(db_path):
    conn = _conn(db_path)
    try:
        assert render.coverage_start(conn) is None
    finally:
        conn.close()
