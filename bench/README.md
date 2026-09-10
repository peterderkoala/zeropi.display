# bench

By-hand hardware probes. Not tests, not part of any install — each one exists
because a number in a spec or an ADR needed a machine to confirm it.

- `notify-budget-probe.py` — measures the Pi→Desktop notification budget by
  bisection (#78, confirming #73). Run from the repo root with the Desktop
  venv: `.venv/bin/python bench/notify-budget-probe.py`.

  Safe against a **live** `zeropi-display`: every Payload it sends has an
  unrecognised `kind`, which `receive.py` rejects before any DB write or panel
  draw. It does **not** collide with the panel the way `pi/epd-selftest.py`
  does. Back up `data.db` anyway.

- `btmon-78-excerpts.txt` — the ATT records from that run: the MTU exchange
  settling at 517, and the `0x23` notifications whose value length caps at
  `0x0200`.

Findings live in `docs/research/notify-direction-budget.md` §6.
