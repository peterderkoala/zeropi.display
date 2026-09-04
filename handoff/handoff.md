# Handoff — zeropi.display

## Where things stand

**Milestone 1 (BLE prototype) works on real hardware.** The Desktop pushes a
Payload over BLE, the Pi parses it, persists a Reading to SQLite, and returns
an Ack — verified 18/18 on the happy path, plus all four malformed-Payload
cases and reconnect-after-restart. Full write-up:
`docs/e2e-verification.md`.

The catch: it works because of **system config applied by hand over SSH**,
which exists nowhere executable. That is what the current map addresses.

- Design/concept: `pi-eink-ble-concept.md` (repo root) — settled BLE service
  shape, Payload/Ack format, SQLite schema, UUIDs, deployment path.
- Domain glossary: `CONTEXT.md` (Desktop, Pi, Payload, Reading, Ack,
  One-liner)
- ADRs: `docs/adr/0001-single-write-payload-no-chunking.md`,
  `docs/adr/0002-readings-persisted-on-pi.md`
- Agent-skill config: `docs/agents/issue-tracker.md`, `docs/agents/domain.md`

## Maps

Two maps are open. #13 is where the work is; #7 has one ticket left.

### Current: [Real Claude Code usage read, pushed, and stored in SQLite (#13)](https://github.com/peterderkoala/zeropi.display/issues/13)

Charted 2026-09-04. Destination is a **spec** —
`docs/spec-usage-pipeline.md` plus the `CONTEXT.md`/ADR changes it needs —
precise enough that a Sonnet 5 session implements the real-usage pipeline
without reopening a decision. Implementation is a separate map.

**Read the map body first.** It carries ~20 decisions settled during
charting and the measured facts behind them; nothing on this map should
re-derive them.

Frontier — open, unblocked, unclaimed:

1. [Settle the multi-row transport protocol (#16)](https://github.com/peterderkoala/zeropi.display/issues/16)
   — `wayfinder:grilling`. Connection lifetime, ack discipline, partial
   failure, ordering.
2. [Settle who creates the new SQLite schema on the Pi (#17)](https://github.com/peterderkoala/zeropi.display/issues/17)
   — `wayfinder:grilling`. Touches map #7's open #11; don't invalidate it.
3. [Prototype: the usage reader against real logs (#18)](https://github.com/peterderkoala/zeropi.display/issues/18)
   — `wayfinder:prototype`. Unblocked now that both research tickets closed.

Blocked: [Record the vocabulary and ADRs (#19)](https://github.com/peterderkoala/zeropi.display/issues/19)
(← #16, #17), [Write the implementation spec (#20)](https://github.com/peterderkoala/zeropi.display/issues/20)
(← #16, #17, #18, #19).

Closed: [#14 pricing](https://github.com/peterderkoala/zeropi.display/issues/14)
(`docs/research/pricing-table.md`, branch `research/pricing-table`) and
[#15 dedup](https://github.com/peterderkoala/zeropi.display/issues/15)
(`docs/research/dedup-rules.md`, branch `research/dedup-rules`). Both
branches are unmerged research; the findings are summarised in the map body.

### Also open: [Target installation reproducible from scratch (#7)](https://github.com/peterderkoala/zeropi.display/issues/7)

Get a stock Pi to an unattended, reboot-surviving install with one
repeatable provisioning path. The map body carries the full inventory of
hand-applied Pi state — read it before touching the Pi.

**Only [#11](https://github.com/peterderkoala/zeropi.display/issues/11)
remains open** (verify provisioning from scratch); #8, #9 and #10 are
closed. Note that map #13's ticket #17 may change what #11 has to verify —
settle #17 before running #11.

### Closed: [Milestone 1 BLE prototype (#1)](https://github.com/peterderkoala/zeropi.display/issues/1)

Destination reached; all five tickets (#2–#6) and the map itself are
closed. The round trip is verified on real hardware — see
`docs/e2e-verification.md` and the closing comments on #6 and #1.

## Hard-won facts — do not relearn these

- **The Pi Zero 2W's BLE hardware is fine.** An earlier session suspected a
  chip/firmware limit behind "coin-flip" reliability. It was `bluetoothd`
  segfaulting in its MIDI plugin on every incoming LE connection. Do not
  design chunking or retry logic around a presumed hardware limitation.
- **`DisablePlugins` in `/etc/bluetooth/main.conf` does nothing** — not a
  valid BlueZ 5.82 key. Plugin exclusion is a `bluetoothd` command-line
  option; see the drop-in described in #7.
- **`receive.py` does not survive a `bluetoothd` restart** (ticket #9). When
  the Desktop says "no device advertising service …", check
  `bluetoothctl show | grep ActiveInstances` on the Pi and
  `journalctl -u bluetooth` for a crash before suspecting the radio.
- `push.py`'s `finally: stop_notify(...)` masks the real exception when a
  connect fails, reporting "Service Discovery has not been performed yet"
  over the top of the actual error. Tracked as
  [#12](https://github.com/peterderkoala/zeropi.display/issues/12); until
  it is fixed, delete that `finally` block by hand when diagnosing a BLE
  failure.

Usage-log facts, from map #13's research (full detail in the map body and
in `docs/research/`):

- **A naive dedup of the JSONL logs loses 26.2% of all output tokens.**
  Duplication is streaming content-block fan-out, and the early copies carry
  a *provisional* usage snapshot. Keying on `(requestId, message.id)` is only
  half the rule — you must also pick a winner within each group.
- **Cache-write tokens are two billed classes, not one.**
  `cache_creation_input_tokens` is the sum of `ephemeral_5m` and
  `ephemeral_1h`, which price differently. Costing off the sum is wrong by
  ~5% on a real session.
- **Transcript-derived cost is ~92.8% of `cost-state`, and that is correct.**
  The transcript does not contain every call the accumulator saw. Do not
  chase the gap.

## Environment notes

- Dev Pi: `192.168.4.108`, creds in `infrastructure.md` (gitignored).
  `sshpass` is installed in this dev environment for non-interactive SSH;
  the sudo password is the same as the SSH password.
- Pi: Debian 13 (trixie), Python 3.13.5, aarch64, BlueZ `5.82-1.1+rpt2`,
  `python3-dbus` `1.4.0-1`, `bluezero` `0.9.1` in `~pi/.local`.
- Desktop: `bleak` 3.0.2 in a local `.venv/` (gitignored, not committed) —
  `uv venv .venv && uv pip install -r desktop/requirements.txt`.
- Labels `wayfinder:map`, `wayfinder:task`, `wayfinder:grilling`,
  `wayfinder:research`, `wayfinder:prototype` exist. `gh` CLI is
  authenticated as `peterderkoala`.
- Claude Code usage logs live at `~/.claude/projects/**/*.jsonl` — 124 files,
  ~70MB, 3 projects as of 2026-09-04. They contain prompts and file
  contents: **never commit them or excerpts of them.** Test fixtures must be
  synthetic or scrubbed.

## Suggested skills for the next session

- **`mattpocock-skills:wayfinder`** with map #13 — take #16, #17 or #18 from
  the frontier, resolve one, record, advance.
- **`mattpocock-skills:grilling`** for #16 and #17, which are genuine open
  decisions; **`mattpocock-skills:prototype`** for #18.
- **`mattpocock-skills:domain-modeling`** for #19, which rewrites the
  Payload/Reading vocabulary and supersedes ADR 0001.

## If you run subagents, isolate them

Two research subagents were run in parallel from the same working tree on
2026-09-04 and their git operations collided — one agent's commit landed on
the other's branch. No damage (`dev` and `main` were untouched) and it was
repaired with a fast-forward, but `research/dedup-rules` still carries the
pricing commit as a result. **Give parallel agents their own worktrees.**
