"""pi/render.py: frame builders and the panel driver (docs/spec-eink-rendering.md).

Imported by receive.py, not merged into it: the GPIO-claiming import needs
exactly one door (spec §7), and this module's DB-only functions must stay
importable with no bluezero, no panel and no SPI, same as receive.py itself.

Only the Historic View's data layer (spec §6, ticket #62) lives here so far.
Frame builders (§5, ticket #60) and the panel context manager / worker (§7,
§8, ticket #61) land in this same module from separate tickets.
"""

import sqlite3
from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class HistoricRow:
    date: str
    total_cost: float
    incomplete: bool


def historic_rows(conn: sqlite3.Connection, limit: int = 5) -> List[HistoricRow]:
    """The `limit` most recent Active Days, newest first (spec §6).

    An Active Day is any date with at least one `readings` row -- not
    `cost > 0` (an unpriced model yields real tokens at $0, and the Desktop
    already drops all-zero rows, so a row existing means usage happened).
    A day is incomplete iff *any* Reading that day has `cost_complete = 0`.
    """
    rows = conn.execute(
        """
        SELECT date, SUM(cost_usd), MIN(cost_complete)
        FROM readings
        GROUP BY date
        ORDER BY date DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        HistoricRow(date=date, total_cost=total_cost, incomplete=(min_complete == 0))
        for date, total_cost, min_complete in rows
    ]


def historic_average(conn: sqlite3.Connection) -> Optional[float]:
    """The mean daily total across *every* Active Day the Pi holds -- not
    just the rows `historic_rows` returns (spec §6: pairing a since-date
    with an average over a different window is quietly wrong).

    `None` when there are no Active Days at all.
    """
    row = conn.execute(
        """
        SELECT AVG(daily_total) FROM (
            SELECT SUM(cost_usd) AS daily_total
            FROM readings
            GROUP BY date
        )
        """
    ).fetchone()
    return row[0] if row and row[0] is not None else None


def coverage_start(conn: sqlite3.Connection) -> Optional[str]:
    """`meta['coverage_start']`, verbatim (spec §6). `None` if never set."""
    row = conn.execute(
        "SELECT value FROM meta WHERE key = 'coverage_start'"
    ).fetchone()
    return row[0] if row else None
