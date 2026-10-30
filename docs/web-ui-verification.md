# Web UI E2E verification (2026-10-30)

Hardware verification of `docs/spec-web-ui.md`, ticket
[#109](https://github.com/peterderkoala/zeropi.display/issues/109), the last
child of [map #102](https://github.com/peterderkoala/zeropi.display/issues/102),
and the run §13 says the map owes. Everything below ran against the real dev Pi
over real BLE, through the installed `zeropi-web` and a real browser
(Playwright's Chromium). Nothing here is `tests/web_demo.py` or a fixture.

**Result: every §13 item that this hardware can run passes: 1, 3, 4, 5 and
6.** Item 2 needs a second board and was not run. The run found and fixed **two
defects**.

## Environment

| | |
|---|---|
| Pi | the dev Pi Zero 2W, `B8:27:EB:7C:97:0F` (short id `7c970f`), up 8h 50m at the start, image schema 1 |
| Desktop | Pop!_OS 24.04, systemd 255, Python 3.12.3, `bleak` 3.0.2, `fastapi` 0.162.0, `uvicorn` 0.54.0 |
| Install | `desktop/install-desktop.sh` run by the maintainer, both layouts, in-place last. `zeropi-web` and `zeropi-push` run from the clone's `.venv` |
| Browser | Playwright's Chromium at `http://127.0.0.1:8737` |
| Suite | `573 passed` at the start, **`577 passed`** at the end |

SSH to the Pi came late in the run, with access details as in the gitignored
`infrastructure.md`. It was confirmed to be the same Pi (`hciconfig`:
`B8:27:EB:7C:97:0F`). Until then, Pi-side facts came from what the Pi says over
BLE (`status` replies, Acks). Afterwards, they came from its `meta` table and
`systemctl`.

## 1. From scratch, both layouts (§13.1)

**From scratch, standalone, by the curl bootstrap** (23:01, run by the agent
at the maintainer's request). The starting state:

- both units stopped, disabled and removed (`daemon-reload`; no `zeropi*` unit
  files left);
- `pi.address` deleted from `~/.config/zeropi-display/config.db`;
- the config store and both unit files backed up first.

The standalone directory was left in place, because it holds the usage archive
(`usage-archive.db`, 7.7 MB). So the venv was reused, and the install took
3.8 s. Then, from `/tmp`:
`curl -fsSL …/dev/install.sh | bash -s -- desktop`.

- The bootstrap resolved `dev` to `605aff4` and ran the standalone layout. It
  wrote both units and created **only `zeropi-web`'s** enable link. Afterwards
  `zeropi-push` was `disabled` and `inactive`, so §9.4's safety property held.
- **The browser opened**: `xdg-open` reached the maintainer's session browser.
  The journal shows `GET /` at 23:01:15 and then, since nothing was paired,
  the one automatic `discover` (§9.3): *One Pi found.*
- **Setup**, walked in Playwright's Chromium:
  - `/` redirected to `/setup`, with no banner;
  - step 1 listed `zeropi-display B8:27:EB:7C:97:0F −43 dBm`, preselected;
  - steps 3 and 4 said *After pairing*;
  - Pair → `Coupled. This Desktop is a6805332…` and `✓ Paired with
    B8:27:EB:7C:97:0F. Batch: 1 sent, 0 failed.` (the same Desktop Id, so no
    wipe);
  - after the modal closed, step 4's automatic check showed `✓ Working.` and
    *Status →*;
  - *Start zeropi-push* turned step 3 ✓ (*running, up 0s*).
  
  `zeropi-push`'s enable link was then created at 23:02, after the pair, as
  §9.4 orders (`enable --now`).
- **In-place**, last, so the clone is live again. Both units were rewritten to
  the clone's `.venv` and `desktop/`, both stayed enabled, and `cli.py status`
  gave `✓ Working`.

Earlier in the run, the installer had also run over existing units (not from
scratch), in both layouts:

- **Standalone** (`--prefix ~/.local/share/zeropi-display`, run from `/tmp`).
  Both units were rewritten to the standalone venv and copy. The only enable link
  created was `zeropi-web`'s. `zeropi-push`'s link dates from 13:54, from the
  earlier setup, so §9.4 held: the installer never enables it. The URL answered
  with `200`, and the page's one automatic check (§7.3) ran.
- **In-place**, last. Both units were rewritten to the clone's `.venv` and
  `desktop/`, both services restarted at 22:35:30, and `/` answered with `200`.

## 2. Two Pis (§13.2): not run

Only one board is available. Every real `discover` in this run listed exactly
one Pi (`zeropi-display B8:27:EB:7C:97:0F`, −40 to −44 dBm), preselected, as
§9.3 says. The two-Pi list was checked only on fake BLE
(`tests/web_demo.py setup_two`, #108).

## 3. The §10.7 states on hardware (§13.3)

| State | How it was produced | What the page showed |
|---|---|---|
| Working | the installed service, real data | `✓ Working.`, six ✓ tiles, the Desktop and Pi facts line |
| `busy` | `flock ~/.local/state/zeropi-display/ble.lock sleep 30`, then *Check now* | `? Can't tell yet — the link is busy.`, with the lock path, *waited 15s* and *Nothing was queued* |
| A `push` in one tab, a second tab attaching | *Push now* in tab 1, `/` opened in tab 2 | tab 2's nav showed `● push running`. Opening it replayed the whole log through `✓ Batch: 1 sent, 0 failed.` |
| `zeropi-web` restarted mid-`push` | *Push now*, then `systemctl --user restart zeropi-web` 2 s in | the stop took **1.18 s** (journal: *timeout graceful shutdown exceeded*, §5.3). The page said *The web service restarted while push was running. Its outcome is unknown. Nothing was queued.* |
| `zeropi-push` stopped | `systemctl --user stop zeropi-push` | the banner *zeropi-push is not running …* on Configuration, linking to `/setup#service`. Step 3's *Start zeropi-push* started the real unit, every step turned ✓ and the banner cleared |
| Two-phase partial `wipe` | *Wipe…* with `7c970f`, with `flock -w 60 … sleep 30` blocked on the lock so it took the link between the wipe and its re-push | `Wiped. Re-pushing the archive.`, then `? Wiped, but could not re-push yet — the BLE link is busy …`, outcome `cant_tell` and a *Push now* button, which re-pushed `1 sent, 0 failed` once the lock was free |
| A second action refused with `busy_here` | `POST /api/operations` `push` (`202`), then `redraw` 1 s later | `409`, `busy_here`, *push is already running here*, carrying the running push's id. The push finished `ok`. The page attaching on that refusal was checked on fake BLE only (#107) |
| `absent` | `sudo systemctl stop zeropi-display` on the Pi (no advertisement; the board stays powered), then *Check now* | `? Can't tell — the Pi is unreachable.` with *no reply from paired Pi B8:27:EB:7C:97:0F within 10.0s. Normal when it is powered off or out of range. Not a fault.* and *Nothing was queued*; the tiles were replaced. After `systemctl start`, `cli.py status` → `✓ Working`, with the receiver's restart noted |

⚠ `busy` and the colliding `wipe` used `flock(1)` as the link's holder, not
the CLI or `zeropi-push`. For the `wipe`, the real service's collision needs
its tick to land in the few milliseconds between the two phases, which cannot
be staged on demand. A short CLI `push` holding the link (§13.3's own wording) does not produce
`busy`: the page's check waits up to 15 s (`lock_wait`), then runs. That is
the intended behaviour. `flock(1)` on the same lock stood in for a holder that
outlasts the wait. A holder of the real link is either the CLI or
`zeropi-push`, and the lock is all the page sees of either.

## 4. The guard against a real browser (§13.4)

A page served from `http://127.0.0.1:18799` attempted, at 22:37:32:

| Attempt | Result |
|---|---|
| JSON `fetch` `POST /api/operations` | blocked at the preflight: `OPTIONS` got `405`, with no `Access-Control-*` header |
| `no-cors` `text/plain` `fetch` | `403` |
| form `POST` (`enctype=text/plain`, JSON-shaped body) | `403` |
| `<iframe src="http://127.0.0.1:8737/">` | refused: *violates … `frame-ancestors 'none'`* |
| `curl -H 'Host: evil.example'`, `GET /` and `POST /api/operations` | `400`, both |

No Operation started: the journal shows no `202` in that window.

## 5. A `→ Pi` Setting from Configuration (§13.5)

`pi.idle_keepalive_s` was set to `7200` from the Configuration tab. The save ran
as an Operation in the modal: `✓ Set pi.idle_keepalive_s = 7200. Pushed to the
Pi — it applies immediately (§6.1).`, outcome `ok`. That is the Pi's Settings
Ack. The row then showed **pending restart**, by design (`operations.py`
`pending_restart`). The running `zeropi-push` froze its Settings at startup and
would re-assert the old value on its next connection. *Restart zeropi-push*
from the notice cleared it. The restarted service's first connection logged no `Settings re-assertion
failed` line. The service logs only failures, so this shows no failure, not the
value it sent. The same path restored
`86400`.

On the Pi, read as #87 did, once SSH was available: saved `7200` from
Configuration again, and `meta` read `setting.idle_keepalive_s = '7200'`.
Restored `86400` the same way (save, then *Restart zeropi-push*), and `meta`
read `'86400'`. #87's open point stands: no `status` field carries a Setting,
so over BLE alone the Ack is all the evidence there is.

## 6. Reboot survival (§13.6)

The maintainer rebooted the Desktop (booted 23:06:34) and logged in:

- **Both units came back on their own** at 23:07:58, and
  `systemctl --user is-active zeropi-web zeropi-push` gave `active` twice.
- **`zeropi-push`'s `print()` reached the journal.** It logged `Scanning…`,
  `Found…` and `Connected…`, then `Batch complete: 1 sent, 0 failed,
  wiped=False` at 23:08:09.
- **`zeropi-web`'s `print()` reached the journal too.** A `push` through
  `POST /api/operations` (`202`) logged `Batch complete: 1 sent, 0 failed,
  wiped=False` at 23:10:01. Both units' `PYTHONUNBUFFERED=1` works.

The first `cli.py status` after the boot reported *unreachable* (exit 2),
although `zeropi-push` had just pushed to the Pi. A retry a minute later was
`✓ Working`, showing a gauge frame drawn 1m 22s earlier. That was a single
10 s scan that missed the advertisement, and it was not chased further.

## Defects found and fixed

1. **Re-pairing the same Pi left Setup stuck in *choosing*.** After *Pair a
   different Pi…* → *Find another Pi* → *Pair* onto the Pi already paired,
   steps 1 and 2 stayed open with the list and the Pair button. The re-pair
   mode was reset only when `pi.address` changed, and re-pairing the same
   address does not change it. A successful `pair` Operation now ends it too
   (`app.js` `Setup`). Rechecked on hardware twice in a row: every step
   returns to ✓, and the second *Find another Pi* still scans. Only one Pi
   exists, so re-pairing it is the only re-pair this hardware can do.
2. **Browsers kept running an old `app.js` after an upgrade.** The page and
   its files went out with no `Cache-Control`, so Chromium cached them
   heuristically. After `zeropi-web` restarted on new code, an ordinary
   reload still ran the old script. That is what made defect 1's first
   recheck look like the fix had failed. The pages and `/static/*` now send
   `Cache-Control: no-cache`, so the browser revalidates on every load.
   `/static/*` answers a matching `If-None-Match` with `304`. The page routes
   (`FileResponse`) always resend `index.html`, which is small. This is pinned by
   `test_the_page_and_its_files_are_revalidated_on_every_load`. ⚠ A browser
   that cached a pre-fix copy needs **one** hard reload; after that, plain
   reloads pick up changes.

## Found, not fixed

- **Setup's step 4 after a re-pair.** §9.5 defines step 4 for the first pair,
  where the slot is empty and §7.3 checks. After re-pairing, the slot is
  marked out of date, not emptied, so step 4 keeps the *previous* Verdict,
  which can belong to the previous Pi, and shows it without the out-of-date
  label. Unchanged here: the spec does not say what a re-pair's step 4 shows.

## Not run

- **Two Pis (§13.2)**: only one board. See §2.
