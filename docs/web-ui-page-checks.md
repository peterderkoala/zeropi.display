# Checking the web UI's pages by hand

There is no JS test runner (web-ui spec §3.2: no node). Everything a test should
pin lives in Python and is covered by `tests/test_web.py`. The page is checked
by hand against spec §10.7's state list (§12.4). This document is how. It
settles map #102's fog item *checking the pages by hand* (decided in #107).

## The method

Run the real `zeropi-web` app against fake BLE:

```bash
.venv/bin/python tests/web_demo.py <scenario> [--port 18737] [--delay 1.5]
```

`tests/web_demo.py` uses the real operations, a real Verdict and real stores
in a temporary directory. It fakes only the radio (`operations.send_command`,
`push.run_batch_pass`, `push.find_pi`, `push.scan_for_pis`) and `systemctl`.
Every fake BLE step sleeps `--delay` seconds, so a running Operation can be
looked at. Open the printed URL in any browser. Several scenarios can run at
once on different ports.

Check each state below, at desktop width and at about 375 px (§10.6: one
column of tiles, the hero's buttons wrapped under the headline, and no
horizontal scroll, i.e. `document.documentElement.scrollWidth` equals
`clientWidth`).

⚠ **A screenshot taken straight after an action can show an older frame.**
Before you call a state broken, wait for its text to appear. #107 lost time
to this.

## §10.7 states and how to reach each

| State | Scenario | Steps |
|---|---|---|
| Working | `working` | Open `/`. The empty slot starts one automatic check (§7.3), with no modal. |
| Not working (a fault headline) | `not_working` | Open `/`. The Panel tile shows ✗. |
| Unreachable `absent` | `absent` | Open `/`. The Unreachable block replaces the tiles. |
| Unreachable `busy` | `busy` | Open `/`. |
| Empty last-status slot (automatic check) | any paired | The first load after the server starts. A reload makes no second check. |
| Out of date after a mutating action | `working` | Check, then Push now / Redraw / Wipe…. The hero shows `before the <verb> at HH:MM`. |
| `zeropi-push` stopped / failed / not installed | `stopped` / `failed` / `not_installed` | A banner shows above the tabs on every tab and links to Setup. |
| An Operation running | any, `--delay 3` | Start any action. The modal shows *Hide (keeps running)* and has no cancel. Reload the page and it reattaches and replays the log. |
| A second action refused with `busy_here`, and attached | `working`, `--delay 3` | Start a push from a second tab (or `fetch('/api/operations', …)`), then open `/` in the first tab. Its automatic check gets `busy_here` and follows the push. |
| *Outcome unknown* after a `zeropi-web` restart | `working`, `--delay 5` | Start a push, then kill the server and start it again. The page says the outcome is unknown. Shutdown must not wait for the Operation (§5.3): the old process has to exit within about a second. |
| A two-phase partial `wipe` | `wipe_partial` | Wipe… with `7c970f`. The modal shows `Wiped. The re-push hasn't happened yet…` and a *Push now* button. |
| Not paired (redirect to Setup) | `not_paired` | Open `/`. It lands on `/setup`, shows no banner and starts no Operation. |
| Configuration | `working` | Saving `usage.window_days` = `99` shows `out_of_range` inline with `config.py`'s text. `7` shows *pending restart* and a single *Restart zeropi-push* button. `pi.idle_keepalive_s` opens the Operation modal. `pi.address` links to Setup. |
| Setup with no Pi / one Pi / two Pis, the unit missing | (#108) | Setup's own states belong to #108. |

## Record of the check run in #107 (2026-10-30)

Run with the Playwright browser against `tests/web_demo.py`. Every row above
except the Setup states rendered. Running the check found and fixed four
defects:

- §7.3's automatic check raced the page's own reattachment on load. It now
  waits until loading has finished.
- A failed check empties the slot (§7.3), so its Unreachable result was never
  shown. The status result now carries `checked_at`, and the page shows the
  last status Operation's result while the slot is empty.
- uvicorn's graceful shutdown waited for the open events stream, so stopping
  `zeropi-web` mid-Operation held the process until the Operation ended,
  which contradicts §5.3. It is now `timeout_graceful_shutdown=1`.
- An events stream that dropped while the server was down left the
  Operation "running" for good. The page now treats an unreachable server as
  a restart, and says the outcome is unknown.

`/code-review` on #107 then found six more defects, all fixed and the affected
states rechecked:

- The automatic check could fire again: after a failed manual check, or on
  every return to the Status tab. It is now decided once, in the page's load.
- The out-of-date label for a service Batch drifted, because it was computed
  from an ageing duration. It now uses `last_batch_at`.
- A `→ Pi` save that got `busy_here` did not attach to the running Operation.
- The result sentences moved into Python (`summary`, pinned by
  `tests/test_web.py`) and use the CLI's words. That fixed a `config_set`
  fallback that could never show.
- A stale `onerror` handler could overwrite a newer Operation.
- The modal got Escape, focus and `aria-modal`, and the link-styled actions
  became real buttons.
