# Management-surface E2E verification (2026-09-11)

Hardware verification of `docs/spec-management-surface.md` — ticket
[#87](https://github.com/peterderkoala/zeropi.display/issues/87), the last
child of [map #80](https://github.com/peterderkoala/zeropi.display/issues/80),
and the run §10.6 says the milestone is not done without. Everything below
ran against the real dev Pi over real BLE, driven through `desktop/cli.py`
rather than hand-crafted Payloads; nothing here is a unit test or a fixture.

**Result: every §10.6 scenario passes on hardware — after five defects the
run found were fixed during it.** All five are on the Desktop side; **the
Pi-side code (#82, #83) needed no change.** Three of the five turned *"the Pi
is fine"* into ✗ or *"the Pi is there"* into ?, which is exactly the failure
the Verdict's state model exists to prevent — and none was visible to the
465-test suite, because each needs two machines, a clock that moves, or a
Pi on an older image.

## Environment

| | |
|---|---|
| Pi | `192.168.4.108`, Pi Zero 2W, Debian 13 (trixie), Python 3.13.5, BlueZ `5.82-1.1+rpt2` |
| Pi software | `/opt/zeropi-display` at `10876a5` (`dev`), provisioned by the documented curl one-liner during the run |
| Desktop | Pop!_OS 24.04, Python 3.12.3, `bleak` 3.0.2 in the repo's `.venv`; no `zeropi-push` unit installed |
| Store | a **scratch copy** of `~/.local/share/zeropi-display/usage-archive.db` (`--store`), so the maintainer's real archive was never written — its mtime is still 2026-09-09 |
| Configuration | a scratch `--config` store, empty at the start |
| Suite | `462 passed, 3 failed` at the start (see §0); **`474 passed`** at the end |

## 0. Before the Pi: three failing tests were date rot

The handoff flagged three `test_push.py` failures as "worth a look before
#87". They were **not a product defect**: the fixtures seed the store with
2026-09-04/05, and `pending_readings` keeps only the seven-day Window ending
`date.today()` — so on 2026-09-11 the 09-04 row silently fell out of the
Batch. Fixed by pinning `usage`'s idea of today at 2026-09-05 (`a11f7b7`;
now `conftest.pin_today`, applied to `test_push.py` and `test_cli.py`,
which carries the same fixed-date store fixtures and would have rotted the
next day).

⚠ Other test files also use fixed 2026-09 dates and pass today. They were
not audited for the same rot.

## 1. A Desktop ahead of its Pi (§5.1) — and defect 1

The dev Pi was still on a 2026-09-09 image (`556e201`), from before #82/#83.
That was an opportunity rather than an obstacle: §5.1 says a Desktop
upgraded ahead of its Pi must fail **loudly**. Ran the CLI against it first.

- `status` with no `pi.address` → `– Not paired`, **exit 2**. ✓
- `pair` → address recorded, then `Settings re-assertion failed
  (continuing): unknown kind: 'settings'` and **`Batch: 13 sent, 0 failed`,
  exit 0**. ✓ §7.2's best-effort re-assertion, on hardware: a rejected
  Settings write did not fail the Batch it rode with.
- `status` → **defect 1**:

```
  ?  Can't tell — the Pi is unreachable.

     unknown kind: 'command'.

  Desktop   zeropi-push not running, up never
            last Batch 2s ago, 13 Readings sent
  Pi        last seen 2s ago
  …
  Nothing was queued. Re-run when the Pi is back.
```

The Pi answered. `cli.py` turned **any** error Ack into `Reach.ABSENT`, so a
Pi that is sitting right there and replying was told to the human as
*unreachable — re-run when it is back*, exit 2 (the steady state a cron
wrapper must not page on). It is the §8.5 trap exactly — *"the Pi is
unreachable"*, then *"the Pi is there"* one line later. The same branch
swallowed §11 trap 6's truncated reply, which `push.py` reports as
`malformed ack from Pi: …` — under a comment claiming to *name* it. **A
safeguard that looks present and is inert**, the pattern the handoff warns
about for #85.

**Fixed (`d2f23e1`)**: an error Ack reaches `build_verdict`, which renders it
as **not working** (exit 1, `ok: false`, `reachable: true`), reason in the
headline, the six checks present but unlearned. Re-run against the same old
image:

```
  ✗  Not working — the Pi answered status with an error: unknown kind: 'command'.

     The Pi is in range — this is not the Unreachable case. An unknown kind or
     verb means it runs an older image than this Desktop: reinstall it.
```

(As first committed the advice covered both causes in one line; review
split it so a `malformed ack` reply gets its own — *cut at the 512-byte
notify budget* — as §11 trap 6 requires the CLI to name it.)

Also fixed there: `last Batch never ago` and `up never` in the identity
block. Then the Pi was re-provisioned through the documented one-liner, to
`10876a5`, with `data.db` backed up first (12 Readings, md5 `8c5fb6dc…`).

## 2. A real `status` reply, cross-checked — and defects 2 and 3

The first `status` against the new image headlined **defect 2**:

```
  ✗  Not working — the Pi holds 2 Readings this Desktop did not send.
  Readings   ✗  15  (2 unexpected)
```

The Pi was right: it held two 2026-09-04 Readings. The Desktop was wrong
about itself. `pair` calls `clear_pushed_marks`, which cleared **every**
`pushed_at` — but a Batch only ever re-sends the Window, and 09-04 has left
it. Before #85 that was harmless (a mark outside the Window was never read
again); **#85's Readings/Coverage checks made those marks load-bearing**, so
now a plain re-`pair` with the *same* Pi reads as *not working*,
**permanently**, until a `wipe`. This is not an edge case: it is what every
existing install does the first time it adopts the CLI.

**Fixed (`c840eeb`)**: `clear_pushed_marks` takes an optional Window; `pair`
passes it, the three post-wipe paths still clear everything (the Pi holds
nothing then). The first cut changed `push.py --resend-all` the same way;
review reverted that (`3098337`), because pipeline §4.6/§7.6 bind *"clear
every `pushed_at`"* and management §9.1 freezes `push.py`'s CLI — so
`--resend-all` still produces this divergence, recorded at the call site. Re-verified from a fresh copy of the
real store: `pair` → `13 sent`, then `✓ Working`, **Readings 15/15, Coverage
2026-09-04**.

⚠ The fix picks the common case. A **replacement** Pi adopts a Desktop Id
without a wipe (`check_desktop_id`: no stored id → adopt, `wiped: false`),
so after pairing one, marks outside the Window now claim Readings the new
Pi never received — *missing N Readings* until a `wipe`. Push marks are not
per-Pi, and no Batch can re-send outside the Window, so one of the two cases
has to pay; the old code made the frequent one pay. Raised on #87.

**Defect 3** was cosmetic but misleading: `Pi  replied in 10.9s`. Timing each
phase: scan ~1 s, connect ~4 s, the two writes ~2 s. "Replied in" was the
whole BLE session, and read like a slow Pi. **Fixed (`c840eeb`)**: it times
the status write-and-Ack only — **0.1 s** on hardware, against the spec's
illustrative 1.2 s.

The reply itself, `--json`, against the Pi's own state read directly:

| Field | Reply | Pi, read directly |
|---|---|---|
| `readings` | 15 | `COUNT(*) FROM readings` = 15 |
| `coverage_start` | `2026-09-04` | `meta.coverage_start` = `2026-09-04` |
| `schema_version` | 1 | `PRAGMA user_version` = 1 |
| `uptime_s` | 354 | process elapsed 357 s, read 3 s later |
| `frame` / `since_redraw_s` | `historic` / 53 | journal: `render: {'historic': True}` 53 s earlier |
| `panel` | `ok` | no render exception, no watchdog line |

`meta` also held `setting.idle_keepalive_s = 86400`: the re-assertion at the
head of the connection had written the Setting as a side effect, exactly as
§7.2 intends.

## 3. `btmon`: the status Ack on the wire (§5.4, §5.5)

Captured on the Pi with `btmon -w` across one `status`:

| ATT PDU | Measured | Spec |
|---|---|---|
| Exchange MTU, both directions | 517 | 517 (#78) |
| Settings Payload (Write Request value) | 95 B | ~92 B |
| Settings Ack (notification) | 52 B | — |
| status request (Write Request value) | **71 B** | **71 B** |
| status Ack (notification) | **229 B** | 234 typical / 253 worst |

The 5-byte gap to 234 is exactly the digits: `readings: 15` vs the spec's
312, `since_redraw_s: 53` vs 143, `uptime_s: 354` vs 110000. Both
notifications went out as **`Handle Multiple Value Notification (0x23)`**,
re-confirming #78's finding that the overhead is `ATT_MTU − 5`. The status
Ack uses **45 %** of `MAX_ACK_BYTES`.

## 4. `redraw` queues behind the floor (§5.3, ADR-0008, ADR-0013)

Two `redraw`s eight seconds apart, 91 s after the last draw (15:33:34):

```
== redraw 1 @ 15:35:05
Queued on the Pi. It will draw in 3m 23s.
ADR-0008 gates every draw at 300s, so the Pi holds this rather than overriding the floor. …
== redraw 2 @ 15:35:13
Queued on the Pi. It will draw in 3m 16s.
```

Both exit 0. `3m 23s` lands on exactly 15:38:34. The Pi's journal then shows
**one** draw — `15:38:34.741 render: {'historic': True}`, **300.1 s** after
the previous one — for two `redraw`s and a Batch's pending redraw together.
Queued, not rejected, not overriding, and coalesced.

And the other branch: a `redraw` at 15:50:19, 309 s after the last draw
(15:45:10) with nothing pending, printed **`Drawn.`** (exit 0), and the Pi
journal shows `15:50:26.924 render: {'historic': True}` two seconds into
that connection.

## 5. Busy: absorbed, and past the wait (§7.1, §8.5) — and defect 4

**A real Batch colliding with `status`** — `push.py --batch-only
--resend-all` started, `status` two seconds later. The CLI's 15 s wait
absorbed it as §7.1 designed (the Batch held the link ~11 s), but the
result was **defect 4**:

```
15:35:47.6 [push]   Batch complete: 13 sent, 0 failed, wiped=False
15:35:56.6 [status]   ✗ Not working — the Pi holds 13 Readings this Desktop did not send.
```

`status` read the Desktop's push marks **before** waiting for the lock — mid-
Batch, with the Window's marks cleared and not yet re-set — then compared
that snapshot against the Pi's post-Batch reply. A normal Batch sets its
marks one by one, so it races the same way. The very collision the wait
exists to absorb produced a false *not working*. The real archive was
checked immediately and was untouched; the scratch store's marks were all
correct afterwards — the error was purely in *when* they were read.

**Fixed (`3cd1a23`)**: the summary is read inside the connection, under the
lock, just before the status write. Also: up to 15 s of silence before the
"Scanning" line read as hung, so `ble_lock` now says it is waiting (never on
the service's `wait_s=0` path; on stderr since review, so it cannot spoil
`status --json`). Re-run:

```
15:39:05.0 [push]   Scanning for the Pi (up to 10s)…
15:39:06.6 [status] Waiting for the link — another zeropi-display job holds it (up to 15s)…
15:39:16.6 [push]   Batch complete: 13 sent, 0 failed, wiped=False
15:39:24.6 [status]   ✓ Working. Pi up 10m 48s, historic frame drawn 47s ago,
15:39:24.6 [status]     15 Readings agreed from 2026-09-04.
```

**Busy past the wait.** A 13-Reading Batch holds the link ~11 s, inside the
wait, so a real one cannot produce this case on this corpus — a Batch of
the ~2 min §7.1 budgets for would. The lock was held instead with
`flock -x ~/.local/state/zeropi-display/ble.lock sleep 40`, the *held past
the wait* row of §7.1's table through the same file:

```
  ?  Can't tell yet — the link is busy.
     the BLE link is busy: …/ble.lock is held by another zeropi-display job (waited 15s).
  …
  Nothing was queued. Retry in a moment — a Batch takes about two minutes.
```

Exit 2, no scan attempted, the word *unreachable* nowhere. `redraw` under the
same lock: `✗ Refused — the link is busy … Not queued.` — and here was
**defect 5**: exit **2**. §9.6 is explicit that 3 is *"a Command refused
because the Pi is Unreachable"* and 2 is the Verdict's *can't tell*; #86 had
wired both verbs to 2 and pinned it in tests. **Fixed (`79d6c41`)**; `push`
and `pair` keep 2, since a Batch is not a Command.

## 6. Settings: land, persist, survive a reboot (§6.1, §6.2)

```
$ cli.py config set pi.idle_keepalive_s 7200
Set pi.idle_keepalive_s = 7200. Pushed to the Pi — it applies immediately (§6.1).
$ cli.py config set pi.idle_keepalive_s 600
✗ Refused. value=600 is below the floor 3600
    Floor one hour: ADR-0007 is full-refresh-only, …
    Nothing was written.                                   (exit 3, no BLE opened)
```

`meta` on the Pi: `setting.idle_keepalive_s = '7200'`. Then `systemctl
reboot` on the Pi. After boot, **before any Desktop connection** (the
journal shows none), `meta` still read `7200` — persistence, not a
re-assertion. The first `status` after boot:

```
  ✓  Working.
  Pi        replied in 0.1s, up 37s
  Panel      ✓  startup frame, drew 36s ago
  Restarted  ·  up 37s, less than the 245s since the last push
```

§8.2's motivating case, on hardware: a Pi that rebooted 37 s ago and is
drawing its startup frame is **working, with one note** — not ✗.

⚠ **Not observed on hardware: §6.1's live binding.** Proving that a new
keepalive changes *when* the idle redraw fires needs an idle Pi and at least
an hour (the floor of the range). It is covered by the unit test §10.4
requires (`RedrawGate` driven with an injected keepalive), not by this run.

## 7. Absent (§7.1, §8.5)

`status` issued three seconds after `systemctl reboot` on the Pi — a Pi that
is genuinely down, not a stopped service:

```
  ?  Can't tell — the Pi is unreachable.
     no reply from paired Pi B8:27:EB:7C:97:0F within 10.0s.
  …
  Nothing was queued. Re-run when the Pi is back.
real 0m10,273s                                             (exit 2)
```

**10.3 s** — the full scan timeout §7.1 says an absent Pi costs. `redraw`
against the down Pi: `✗ Refused — the Pi is unreachable … Not queued. Re-run
when the Pi is back.`, exit 3. Two distinct headlines for the two cases,
both *can't tell*, both saying nothing was queued.

§9.2's reference rendering also says *"Normal when it is powered off or out
of range. Not a fault."*; the CLI omitted it. **Fixed (`33a006c`)**, absent
only, and re-checked on hardware with the receiver stopped.

## 8. `wipe` as repair (§5.3, §9.4)

```
$ echo b827ec | cli.py wipe
This deletes all 15 Readings on the Pi, then re-pushes them from here.
Type the Pi's short id to confirm [b827eb]: ✗ Confirmation did not match. Nothing was sent.   (exit 3)
$ echo b827eb | cli.py wipe
Wiped. Re-pushing the archive.
Batch: 13 sent, 0 failed.                                                                     (exit 0)
```

On the Pi afterwards: 13 Readings, `coverage_start` moved to `2026-09-05`,
and **`setting.idle_keepalive_s` still `7200`** — #83's review fix
(`clear_settings=False` for the repair wipe; only the ADR-0006 hand-off wipe
resets Settings) confirmed on hardware. `status` → `✓ Working … 13
Readings agreed from 2026-09-05`. The two 2026-09-04 Readings are gone for
good: outside the Window, no Batch can re-send them. That is what the repair
wipe means, not a loss the run caused.

## 9. The rest of the surface

`restart` with no `zeropi-push` unit on this Desktop → `✗ Failed to restart
zeropi-push: … Unit zeropi-push.service not found.`, exit 3. `config set
REDRAW_FLOOR_S 60` → `✗ REDRAW_FLOOR_S is not a setting.` with its ADR,
exit 3. `config` lists all three Tiers, the Pi-projected key marked `→ Pi`.

## What was wrong, in one place

| # | Defect | Symptom on hardware | Commit |
|---|---|---|---|
| 1 | An error Ack to `status` became `Reach.ABSENT` | an answering Pi reported as *unreachable, re-run later*, exit 2 | `d2f23e1` |
| 2 | `pair` cleared marks outside the Window | *not working — 2 unexpected Readings*, permanently, after a plain re-pair | `c840eeb` |
| 3 | "Replied in" timed the whole BLE session | `replied in 10.9s` for a 0.1 s reply | `c840eeb` |
| 4 | `status` read the Desktop's marks before waiting for the lock | *not working — 13 unexpected* after an absorbed Batch collision | `3cd1a23` |
| 5 | A refused Command exited 2, not §9.6's 3 | a script cannot tell *refused* from *can't tell* | `79d6c41` |

`/code-review` of those fixes then found one regression in them — reading
the Desktop's store under the lock put it inside the BLE `try`, whose
catch-all means *absent*, so a store error would have read as a powered-off
Pi (`3098337`, re-raised now) — plus the `--resend-all` revert, trap 6's
explicit naming, and the stderr move above. Re-smoked on hardware after:
`status --json` under a held lock leaves stdout parseable, `redraw` drew.

Plus two renderings brought into line with §9.2 (`never ago`; the absent
case's *not a fault* line) and a silent lock wait now announced.

**The pattern, for whoever reviews the next thing here**: defects 1, 2 and 4
are all *a correct comparison fed the wrong input*. `verdict.py` was right
every time; what it was handed was stale (4), over-cleared (2), or
mislabelled on the way in (1). #85's pure-function design is what made each
one quick to pin down — and is also why none of them could be seen from
inside its tests.

## Still open

- **The replacement-Pi side of defect 2** (above), and **`--resend-all`
  still clearing every mark** — both raised on #87 as design questions; the
  second needs pipeline §7.6 amended, not a code change on its own.
- **`--json` is only clean when nothing is printed first.** The
  `Scanning…`/`Found…`/`Connected…` progress lines of `_with_ble_connection`
  are on stdout (pre-existing — `test_cli.py`'s `_extract_json` parses from
  the first brace to cope). Harmless for a human, a trap for §9.5's intended
  consumer. Not changed here: the same lines are `push.py`'s and the
  service's journal output.
- **No-Ack is inconsistent between verbs.** Connected but no Ack is *absent*
  (exit 2) for `status` and a refusal (exit 1) for `redraw`/`wipe` via
  `_ack_refusal`. Pre-existing; not met on hardware.
- **§5.1 defers to a field §5.4 does not have.** §5.1 keeps the Settings Ack
  minimal because *"confirming what the Pi now holds … belongs to the status
  verb (§5.4)"*, but none of §5.4's seven fields carries a Setting. This run
  could only confirm `7200` by reading the Pi's `meta` over SSH. Re-assertion
  on every connection makes it low-stakes, but the spec's own cross-reference
  does not resolve. Raised on #87.
- **The Pi's journal records no Command and no Setting.** A `wipe` — the one
  destructive verb — leaves no line on the Pi at all, only the Desktop's
  Connected/Disconnected pair. Worth a line each; not done here because it is
  Pi-side and nothing in §10.6 needed it.
- **`push`/`pair` still cannot tell absent from rejected** (#86's accepted
  gap). Not re-litigated; `cmd_push`'s absent branch is dead code for the
  same reason.
- **ADR-0012's open question** — whether `status` should ever be *pushed* —
  gains one data point: the status Ack is 229 B, so the byte cost that
  argued against carrying it on every Ack still holds.
- **§6.1's live binding** — see §6.

## Reproducing this

```bash
# Pi (on the Pi, with a tty for sudo)
curl -fsSL https://raw.githubusercontent.com/peterderkoala/zeropi.display/dev/install.sh | bash -s -- pi

# Desktop, against scratch copies so the real archive is never written
cp ~/.local/share/zeropi-display/usage-archive.db /tmp/run/store.db
C="--config /tmp/run/config.db --store /tmp/run/store.db"
.venv/bin/python desktop/cli.py $C pair
.venv/bin/python desktop/cli.py $C status          # --json, --brief
.venv/bin/python desktop/cli.py $C redraw          # twice, to see it queue
.venv/bin/python desktop/cli.py $C config set pi.idle_keepalive_s 7200
echo <short id> | .venv/bin/python desktop/cli.py $C wipe

# busy past the wait
flock -x ~/.local/state/zeropi-display/ble.lock sleep 40 &

# the wire (on the Pi)
sudo btmon -w /tmp/st.btsnoop     # then: sudo btmon -r /tmp/st.btsnoop
```

Afterwards the Pi's `data.db` was restored from the pre-run backup
(md5 `8c5fb6dc…` again: 12 Readings, no `setting.*` row), so the dev Pi holds
what the maintainer's real archive records having pushed — the run's `wipe`
and scratch-store pushes would otherwise leave the real archive and the Pi
disagreeing. It stays on the `10876a5` image. The backup is at
`/home/pi/data.db.bak-87`.
