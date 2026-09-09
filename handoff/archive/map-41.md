# Map #41 — Implement the usage pipeline

**Charted 2026-09-06, closed 2026-09-09. Destination reached: the pipeline is hardware-verified (`docs/usage-pipeline-verification.md`).** Issue: https://github.com/peterderkoala/zeropi.display/issues/41

> Archived from `handoff/handoff.md` on 2026-09-09, verbatim. This is a
> **record of a finished effort**, not live guidance: facts here were true when
> written and some have since been superseded. The live handoff, the specs and
> the ADRs are authoritative. Kept because the *reasoning* behind decisions —
> and the bugs found on the way — is not recoverable from the code.

---

**#41 is CLOSED (2026-09-09)** — destination reached, all seven children
resolved. It was charted 2026-09-06 against #13's finished spec. Destination: `docs/spec-usage-pipeline.md` implemented, tested, and
verified end-to-end on real hardware. **Execution-mode** — its Notes
override "plan, don't do" since the spec's own gap check (§13) already
closed every decision; the seven child tickets are build-and-verify slices,
not decisions to grill. Full ticket bodies and blocking edges live on the
map itself — don't re-derive them here, read
[the map](https://github.com/peterderkoala/zeropi.display/issues/41).

**Update, same day: the four-ticket frontier is closed.** #42, #43, #44 and
#45 were each run as a parallel worktree-isolated subagent and merged into
`dev` sequentially (#45 first, since #42/#43/#44 all wanted its pytest
harness for TDD despite the ticket's own "no inter-dependencies" framing —
a real practical dependency the map didn't surface). All four landed clean,
each closed its own ticket after a `/code-review` pass caught and fixed real
bugs (see each ticket's closing comment for specifics — a shared Gauge/Historic
dirty flag in #44, a `NOT NULL` crash on bad timestamps in #42, a corrupt-JSON
crash in #43, a wrong fixture-README claim in #45). Full suite after all four
merges: **118 passed, 0 skipped.** `desktop/usage.py`, `desktop/gauge.py` and
the rewritten `pi/receive.py` all now exist and are independently unit-tested
against the synthetic fixture — none of them touch BLE.

**Unblocked: [Rewrite desktop/push.py — the transport (#46)](https://github.com/peterderkoala/zeropi.display/issues/46)**
(was blocked by #42, #43, #44 — all closed). Chained behind it:
[Build the resident systemd service (#47)](https://github.com/peterderkoala/zeropi.display/issues/47)
(blocked by #46) → [Verify the pipeline end-to-end on real hardware (#48)](https://github.com/peterderkoala/zeropi.display/issues/48)
(blocked by #47 and #45, both now satisfied). These three are chained, not
parallelizable — #48 in particular wants the dev Pi, check the Environment
notes below before touching it.

**Update, same day: #46 is closed too.** `desktop/push.py` is rewritten per
spec §7 against `usage.py`/`gauge.py` (kept the existing BLE mechanics —
scan-by-service-UUID, one held connection, `_acquire_mtu`, no `finally:
stop_notify` — §10 traps #2/#4 untouched). The BLE-calling code is a thin
shell around a dependency-injected `send_one` callable, so the Batch loop,
the Gauge push, wipe handling and CLI dispatch are unit-tested with a fake
radio (30 new tests, 153 total). Two functions are the seam #47's resident
service should import directly rather than subprocessing into the CLI:
`run_batch_pass(store_path=None)` and `run_gauge_push(store_path=None)` —
both return without ever touching BLE if there's nothing to send.

⚠ **A `/code-review` pass caught a real spec violation before this landed**:
the first draft only checked `wiped: true` on the Daily-batch Ack path, so a
wipe signalled on a Gauge Ack (entirely plausible — a resident service's 30 s
Gauge poll fires far more often than the 04:00 Batch) would have been
silently dropped, reproducing exactly the "Pi sits permanently empty" bug
§7.2 exists to prevent (see #36's hand-off facts below). Fixed: a wiped Gauge
Ack now clears every `pushed_at` and runs one extra Batch pass in the same
invocation, same as the Daily path. Review also caught `--resend-all
--gauge-only` silently clearing marks without ever re-Batching them — now
rejected by `parse_args`. **If you write another Ack-consuming code path,
check `wiped` on it regardless of Payload kind — this is the second time the
"of either kind" clause in §7.2 has almost been missed.**

**Verified `--dry-run` against the maintainer's real logs (§11.4's
acceptance check)**: correct Project Labels via R1 for both the main project
and its worktree, all Payload sizes well under the 514-byte budget, and a
real (non-null) Gauge state. Also exercised `--batch-only`/`--gauge-only`
against the **actual dev Pi** — it answered the scan, connected, and
negotiated the MTU fine (confirming the BLE mechanics carried over
untouched), but rejected every Payload with `missing field(s): usage_tokens,
oneliner` — **that Pi is still running the old milestone-1 `receive.py`, not
the #44 rewrite on `dev`**, so it hasn't been re-provisioned yet. Not a
`push.py` bug; flagging it here so #48 (hardware verification) or whoever
next touches the dev Pi knows to re-run `install-pi.sh` (or otherwise deploy
the rewritten `receive.py`) before expecting a real round trip. `push.py`'s
own behavior was correct throughout: continue-past-failure, no row marked
pushed, non-zero exit on a wholly-failed Batch, Gauge failure dropped
silently with exit 0.

**Update, same day: #47 is closed too.** `desktop/service.py` is the
resident `systemd --user` loop from spec §7.5, built directly against #46's
seam (`push.run_batch_pass`/`push.run_gauge_push` — no subprocessing). Two
pure, clock-injected classes carry the decision logic: `GaugeGate` (the
300s-throttled, coalescing Gauge-push trigger off `DisplayedGaugeState` —
`five_hour`/`seven_day` `used_percentage` value-or-null-ness plus
`resets_at`; the context percentage is deliberately excluded per §13
judgment call #5) and `BatchScheduler` (04:00-local plus a >24h-stale
startup catch-up). `run_forever` itself takes injectable clocks/IO, so the
wired loop — not just the two pure classes — is driven directly in tests
with no real sleeping. Ships `desktop/zeropi-push.service` (unit file only,
per the ticket; #34 still owns installing it). 30 new tests, **181 total.**

⚠ **`/code-review` caught a real wiring bug**: `batch_in_progress` was reset
in a `finally` immediately after the Batch's own `await`, before
`gate.observe()` ran — so "the Gauge waits for an in-flight Batch" never
actually held within one tick, and a Gauge push could open a second BLE
connection back-to-back with the Batch's. Fixed by resetting the flag only
at the top of each tick (so it survives through that tick's Gauge check and
only clears for the *next* one); added a regression test verified to fail
against the reverted buggy code.

⚠ **Flagged, not fixed (out of #47's scope)**: `install-desktop.sh`'s
standalone mode (the common non-maintainer-Desktop path) currently deploys
only `push.py` into `~/.local/share/zeropi-display/` — not `gauge.py`,
`usage.py`, or now `service.py`. The shipped unit file's `ExecStart`
targets that standalone layout by convention, but it won't actually run
there until this is fixed. Whoever next touches #34 or provisions a
standalone Desktop for real should know this.

**Update, 2026-09-09: #48 is closed, and with it the map.** The pipeline is
verified end-to-end on real hardware — see `docs/usage-pipeline-verification.md`
and the summary at the top of this file. Headline numbers: a 10-Reading Batch is
**10 sent, 0 failed** at MTU 517 with ~2 s for the ten sequential Ack round
trips; Payloads run **347–390 bytes** against the 514 budget; the redraw floor
coalesced a second Gauge Payload sent 0.08 s after the first (`drawn: true` then
`drawn: false`, one `render:` line); and the **wipe/hand-back recovery works on a
Gauge Ack specifically** — the exact path #46's review caught missing. The
resident service's startup catch-up fired 8 s after start and pushed all ten
Readings with no operator involvement.

⚠ **Before you next re-provision the Pi**, note that the run had to fix the Pi's
unit file (`31bb8e7`) and the Desktop's (`94665e3`) for `PYTHONUNBUFFERED`; the Pi
is currently on `31bb8e7`, so it is one commit behind `dev`'s tip in its VERSION
stamp but functionally current (`94665e3` and `a45d964` touch only the Desktop
unit and docs).

**Historic note, superseded — #48's own instructions for running the service**: to
run this loop from a repo clone with `.venv` set up: `.venv/bin/python desktop/service.py`
(`--store PATH` to override the store). To exercise the actual systemd
unit, copy `desktop/zeropi-push.service` to `~/.config/systemd/user/`,
rewrite `ExecStart` to the in-place layout (`<repo>/.venv/bin/python3
<repo>/desktop/service.py`), then the usual `daemon-reload` / `enable --now`
/ `journalctl -f` dance. The loop's first tick always attempts a startup
Batch catch-up, so a Batch pass (and a BLE connection attempt) should show
up in the log immediately.

⚠ **If you're running #42/#43/#44/#45 as parallel sessions or subagents,
give each its own worktree** — trap 12 in the spec (§10) records a prior
collision from sharing one working tree across parallel agents. Also: #44
(the Pi rewrite) and #48 (hardware verification) will want the dev Pi —
check the Environment notes below before touching it, since it's shared.
⚠ **Every one of #42/#43/#44's spawned worktrees came up on a stale base**
(the repo's bare initial commit, not `dev`'s tip) rather than `dev` as
requested — each agent had to `git reset --hard`/fast-forward onto `dev`
itself before starting. Confirm a fresh worktree is actually on `dev` before
handing it real work; don't assume the isolation tooling got the base right.

**#13 is closed** (2026-09-06). All 20 child tickets resolved and the spec —
`docs/spec-usage-pipeline.md` — is on `dev` (`d925bb0`, extended by `e93d80d`).
#41 is the implementation map opened against it.

**#7 is closed** (2026-09-06). Its destination — both ends of the link
reproducible from scratch through one documented `curl ... | bash -s --
<role>` command — was fully reached, and every child ticket resolved: #8-#11
(the original Pi BLE provisioning), #33-#35 (the curl-delivery redraw), and
#39-#40 (e-ink panel provisioning, added mid-map). **#40 (2026-09-06) closed
the last gap**: `install-pi.sh`'s e-ink panel steps (SPI persistence, the
four apt packages, deployment) had never actually executed, since #35's
hardware run predated the branch merge that added them. Teardown +
documented one-liner + reboot all passed with no manual steps, and the panel
glass was finally looked at by a human (border and all eight alternating
blocks clean). Write-up: `docs/eink-driver-verification.md`'s "Provisioning
verification" section.

One loose end, deliberately left as fog rather than a ticket: **whether this
ex-pwnagotchi HAT wires `PWR_PIN` on BCM 18** is still unconfirmed (open
since #23, not settled by #39 or #40 — no multimeter/LED on hand either
time). It is a hardware-characterization question, not a provisioning one,
so it belongs to whichever effort first drives the panel for real, not to
#7.

#39 also moved one of #7's scope lines, so read it before assuming the old
boundary: **e-ink *driver provisioning* is now in scope for #7**
(`install-pi.sh` owns SPI and the panel's apt stack, on the same "no
hand-applied system state" logic as the rest of the map), while **e-ink
*rendering* is still out** and wants its own map when it starts. The #8
decision's "no e-ink HAT provisioning yet" clause is struck through in the
map body rather than deleted.
