# Readings are persisted on the Pi, not the Desktop

Although the Desktop owns the real data sources and the Pi is otherwise a
dumb receiver, each Reading is saved to a SQLite database on the Pi rather
than the Desktop.

This is because the e-ink display's planned long-term-graph and
usage-average features run on the Pi itself and need local access to
history independent of whether the Desktop is currently connected. Storing
Readings on the Desktop instead would make the display's own features
depend on a live BLE session to backfill data, which defeats the point of
the Pi holding its own display state.

## Considered Options

- **Persist on the Desktop**: natural given the Desktop already owns the
  real data sources, and avoids duplicating storage. Rejected because the
  e-ink display needs its history available locally, without depending on
  the Desktop being present at render/graph time.
