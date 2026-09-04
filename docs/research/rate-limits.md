# Research: Claude Code's 5h and weekly limits, and whether the limit is obtainable

Resolves [#22](https://github.com/peterderkoala/zeropi.display/issues/22). Child of
map [#13](https://github.com/peterderkoala/zeropi.display/issues/13).

Measured on the maintainer's machine 2026-09-05, Claude Code v2.1.260/2.1.261,
account `organizationType: claude_pro`, `organizationRateLimitTier:
default_claude_ai`.

---

## Verdict

**The percentage is achievable — and the ticket's framing was wrong in a way
that makes the problem easier, not harder.**

The map assumed we needed a *denominator* to divide our locally-parsed token
numerator by. That assumption is false in both directions:

- **The denominator does not exist in any obtainable form.** Anthropic never
  publishes it, never sends it, and it is not a token count at all (§3).
  Chasing it was never going to work.
- **We do not need it.** The server computes the percentage itself and sends it
  to Claude Code on every API response. Claude Code re-exposes it to arbitrary
  local scripts through the **status line `rate_limits` object** — a
  documented, stable, first-party interface (§2).

So the gauge does not compute `tokens / limit`. It **reads a percentage the
server already computed**, together with the window's `resets_at`. The fallback
hand-configured constant is **not needed** for the 5h and 7d windows.

This also silently fixes two problems the map had parked:

- **Multi-machine usage** (map, "Not yet specified"). The server's percentage is
  account-wide, so usage from a second machine, from claude.ai chat, and from
  Cowork is already inside it. A locally-summed numerator could never have seen
  those.
- **Sub-agent accounting.** Same reason — it is server-side, so the question of
  whether to include sidechain rows does not arise for the gauge (§6).

One real constraint remains, and it drives the architecture: **the live value
lives only in a running Claude Code process**, and the one file that persists it
is badly stale (§4). The pipeline needs a small status-line shim to bridge it to
disk. That is the single new mechanism this ticket introduces.

---

## 1. The window definitions

### 5-hour window

Anthropic's own docs call it a **rolling** window and are deliberately vague
about its anchor:

> The `rate_limits` object contains a rolling `five_hour` window and a weekly
> `seven_day` window.
> — [statusline docs](https://code.claude.com/docs/en/statusline)

> Your session-based usage limit will reset every five hours.
> — [What is the Pro plan?](https://support.claude.com/en/articles/8325606-what-is-the-pro-plan)

> each member's Claude Code usage draws from a per-seat allowance that resets on
> a rolling five-hour window and a weekly window.
> — [Manage costs](https://code.claude.com/docs/en/costs)

The 5-hour duration is confirmed in Claude Code's own shipped code: its
rate-limit test harness computes a `five_hour` reset as `Date.now()/1000 +
18000` (= 5h) and a `seven_day` reset as `+ 604800` (= 7d).

**⚠ The anchor is NOT reconstructible from local logs — measured, not assumed.**
This is the finding that kills the "model the window ourselves" approach:

| Observation | Value |
|---|---|
| `five_hour.resets_at` on 2026-09-04 | `19:50:00.411Z` |
| Implied window start (reset − 5h) | `14:50:00.411Z` |
| Nearest local activity of any kind | `14:58:09.794Z` (8 min *later*) |
| First activity that day, after a 358 h gap | `12:00:19.424Z` |
| Largest intra-day gap before the anchor | 92.6 min (`13:25:42` → `14:58:17`) — nowhere near 5 h |

So the anchor at 14:50 matches **neither** ccusage's "first activity after a
≥5 h gap" model (that predicts 12:00:19), **nor** a fixed clock boundary (14:50
is not one), **nor** any local event at all. Candidate explanations, none
verifiable from this machine: account-wide activity on another surface
(claude.ai chat, Cowork, another device); a background chore that leaves no
transcript entry (the map already established `claude-haiku-4-5` runs
`ai-title`/`agent-name` chores that record no transcript usage); or a
server-side model that is genuinely sliding rather than a discrete block.

**Conclusion: do not model the 5h window locally. Read `resets_at` from the
server.** Any local reimplementation would be wrong by an unbounded offset, and
wrong precisely when the user is near the limit.

### Weekly windows

Weekly limits exist, and there is more than one kind. From
[the Pro plan article](https://support.claude.com/en/articles/8325606-what-is-the-pro-plan):

> Pro plans have a weekly usage limit that applies across all models. Weekly
> limits reset at a fixed time each week that is assigned to your account.
> [...] Your reset day and time stay the same regardless of when you start using
> Claude or when your subscription begins.

This is corroborated locally: `seven_day.resets_at` is `2026-09-11T13:00:00Z` —
an exact clock hour, unlike the 5h anchor. So **the weekly window IS a fixed
account-assigned slot**, and the two windows have genuinely different shapes.

Claude Code's shipped schema enumerates these window kinds:

```
five_hour, seven_day, seven_day_oauth_apps, seven_day_opus,
seven_day_sonnet, seven_day_overage_included, overage
```

plus a `model_scoped` array of `{display_name, utilization, resets_at}` for
per-model weekly buckets (the server labels them, e.g. `"Fable"`). On this
account the model-scoped ones are all `null` — they populate only when the
server returns per-model windows.

**How they interact:** they are independent ceilings, and the *most constraining
one* is what actually stops you. Claude Code tracks a
`anthropic-ratelimit-unified-representative-claim` header naming which window is
currently binding. The docs describe the user-visible consequence:

> "You've hit your session limit" or "You've hit your weekly limit": a seat-based
> usage window on a subscription plan, **shared across all models**, so the
> developer can't restore access by switching models with `/model`. [...] After
> the model-specific "You've hit your Opus limit" or "You've hit your Sonnet
> limit" message, switching to a model outside that family **does** keep the
> developer working.
> — [Manage costs](https://code.claude.com/docs/en/costs)

---

## 2. Where the percentage comes from (the answer)

### Transport: unified rate-limit response headers

Every API response carries the account's rate-limit state in
`anthropic-ratelimit-unified-*` headers. The full set present in the v2.1.261
binary:

```
anthropic-ratelimit-unified-status                 (allowed | allowed_warning | rejected)
anthropic-ratelimit-unified-reset
anthropic-ratelimit-unified-representative-claim   (which window is binding)
anthropic-ratelimit-unified-fallback
anthropic-ratelimit-unified-grace-status
anthropic-ratelimit-unified-overage-status
anthropic-ratelimit-unified-overage-utilization
anthropic-ratelimit-unified-overage-in-use
anthropic-ratelimit-unified-overage-period
anthropic-ratelimit-unified-overage-period-monthly-utilization
anthropic-ratelimit-unified-overage-period-channel-utilization
anthropic-ratelimit-unified-overage-reset
anthropic-ratelimit-unified-overage-disabled-reason
anthropic-ratelimit-unified-overage-surpassed-threshold
anthropic-ratelimit-unified-slow-status
anthropic-ratelimit-unified-slow-budget-utilization
anthropic-ratelimit-unified-slow-budget-reset
anthropic-ratelimit-unified-slow-max-wait
anthropic-ratelimit-unified-slow-retry-after
anthropic-ratelimit-unified-slow-offer
anthropic-ratelimit-unified-upgrade-paths
```

Claude Code's internal schema for the decoded form is:

```js
{ status, resetsAt, rateLimitType, utilization,
  unifiedWindows: { five_hour:                  { utilization, resetsAt },
                    seven_day:                  { utilization, resetsAt },
                    seven_day_overage_included: { utilization, resetsAt } } }
```

**Note what is and is not there: `utilization` (a percent) and `resetsAt`. There
is no `limit` and no `remaining` field anywhere in the window shape.** The only
absolute quantities in the whole surface are *dollar* fields, and those belong
to usage credits / gateway spend limits, not to the plan window.

### Interface we can actually use: the status line `rate_limits` object

Claude Code passes JSON on stdin to any status-line command. From the
[official status line docs](https://code.claude.com/docs/en/statusline):

```json
"rate_limits": {
  "five_hour":   { "used_percentage": 23.5, "resets_at": 1738425600 },
  "seven_day":   { "used_percentage": 41.2, "resets_at": 1738857600 },
  "spend_limit": { "used_percentage": 62.8, "resets_at": 1740787200 }
}
```

| Field | Meaning (verbatim from the docs) |
|---|---|
| `rate_limits.five_hour.used_percentage`, `rate_limits.seven_day.used_percentage` | "Percentage of the 5-hour or 7-day rate limit consumed, from 0 to 100" |
| `rate_limits.five_hour.resets_at`, `rate_limits.seven_day.resets_at` | "Unix epoch seconds when the 5-hour or 7-day rate limit window resets" |
| `rate_limits.spend_limit.*` | Only behind a Claude apps gateway with spend limits. Its percentage *can exceed 100*. v2.1.251+ |

Claude Code's shipped schema also defines, alongside `rate_limits`, the sibling
fields `rate_limits_available` — documented in the binary as *"False when plan
rate limits do not apply (API key, Bedrock, Vertex, or missing profile scope) —
rate_limits will be null"* — and a `model_scoped` array of
`{display_name, utilization, resets_at}`.

**Availability caveats, verbatim from the docs — these are the spec's edge
cases:**

> `rate_limits`: appears only for Claude.ai Pro and Max subscribers, or behind a
> Claude apps gateway that sets a spend limit for you, and **only after the first
> API response in the session**. Each window (`five_hour`, `seven_day`,
> `spend_limit`) may be **independently absent**, and **Claude Code drops a
> window once its `resets_at` time passes.**

That last clause matters for the gauge: *a missing `five_hour` is not 0% and not
an error — it means the window lapsed.* Render it as "window reset" or fall back
to the last known value with an "as of" note; do not render 0%.

**Unit trap:** status-line `resets_at` is **Unix epoch seconds (a number)**.
The cached copy in `~/.claude.json` (§4) uses **ISO-8601 strings**. Same concept,
two encodings; the spec must say which one each reader parses.

### Update cadence

The status line is event-driven, debounced at 300 ms, and re-runs on an optional
`refreshInterval` timer (minimum 1 s). The docs warn that event triggers "can go
quiet when the main session is idle, for example while a coordinator waits on
background subagents", and recommend `refreshInterval` for exactly that case.
This is a good match for the map's settled "event-driven with a redraw floor"
cadence ([#25](https://github.com/peterderkoala/zeropi.display/issues/25)).

---

## 3. What the limits actually are, and in what unit

**Anthropic publishes no numeric limit for any consumer plan, and the limit is
not denominated in tokens, messages, or requests.**

The Pro plan article is explicit that usage is a *composite*:

> Usage is measured by message length, including the length of files you attach,
> the length of your current conversation, and the model or feature you use.

and elsewhere reserves the right to change the shape of the ceilings:

> capacity management [...] such as weekly and monthly caps or model and feature
> usage, at our discretion.

The only quantitative statement offered is relative, not absolute: Pro gives "at
least five times the usage per session compared to our free service".

[How do usage and length limits work?](https://support.claude.com/en/articles/11647753-how-do-usage-and-length-limits-work)
likewise gives no numbers and no unit, listing only the factors: "the length and
complexity of your conversations, the features you use, which Claude model
you're chatting with, and the effort level you've selected."

**Consequences for the spec:**

1. A hand-configured constant denominator would have been **meaningless**, not
   merely imprecise. There is no number to configure, because the quantity being
   limited is not a token count. The map's agreed fallback should be recorded as
   **withdrawn as unnecessary and unsound**, not merely unused.
2. Because usage is model- and feature-weighted, our locally-parsed token sum is
   **not proportional** to limit consumption. Two runs with identical token totals
   can consume different fractions of the window. So the token sum must never be
   presented as a proxy for the gauge, on any scale.
3. Tokens and cost remain correct for the **historic** view (map's secondary
   feature), which is priced in dollars and is a different question.

The published numeric limits that *do* exist — the
[API rate limits](https://platform.claude.com/docs/en/api/rate-limits) tables and
the TPM/RPM per-user recommendations in the costs doc — are **organization-level
API tier limits**, a different mechanism from subscription plan windows. Do not
conflate them; the maintainer's account is on a subscription
(`billingType: stripe_subscription`), so the API tier tables do not apply.

---

## 4. Local artifacts: exactly one, and it is stale

A sweep of `~/.claude/` and `~/.claude.json` for `five_hour` /
`used_percentage` / `resets_at` / `unified-utilization` found rate-limit data in
**exactly one place** (excluding this session's own tool output and the
changelog cache):

### `~/.claude.json` → `cachedUsageUtilization`

```jsonc
{
  "fetchedAtMs": 1788539427545,          // 2026-09-04T16:30:27.545Z
  "accountUuid": "...",
  "utilization": {
    "five_hour":  { "utilization": 17, "resets_at": "2026-09-04T19:50:00.411294+00:00",
                    "limit_dollars": null, "used_dollars": null,
                    "remaining_dollars": null, "locked_reason": null },
    "seven_day":  { "utilization": 2,  "resets_at": "2026-09-11T13:00:00.411315+00:00",
                    "limit_dollars": null, ... },
    "seven_day_opus": null, "seven_day_sonnet": null, "seven_day_oauth_apps": null,
    "limits": [
      { "kind": "session",     "group": "session", "percent": 17, "severity": "normal",
        "resets_at": "2026-09-04T19:50:00.411294+00:00", "is_active": true  },
      { "kind": "weekly_all",  "group": "weekly",  "percent": 2,  "severity": "normal",
        "resets_at": "2026-09-11T13:00:00.411315+00:00", "is_active": false }
    ],
    "spend": { "used": { "amount_minor": 0, "currency": "USD", "exponent": 2 },
               "limit": null, "percent": 0, "enabled": false, ... }
  }
}
```

Note again: `utilization` is a percent; every absolute field
(`limit_dollars`, `used_dollars`, `remaining_dollars`) is `null` on this plan.
The `limits[]` array is the same data in the shape `/usage` renders, including a
`severity` the display could reuse.

**⚠ It is a low-frequency cache and MUST NOT back a live gauge.** Measured
across the five rotating `~/.claude/backups/.claude.json.backup.*` snapshots plus
the live file:

| File mtime (local) | `fetchedAtMs` | 5h |
|---|---|---|
| 09-04 20:54:41 | 2026-09-04T16:30:27.545Z | 17% |
| 09-04 20:58:33 | 2026-09-04T16:30:27.545Z | 17% |
| 09-04 23:02:07 | 2026-09-04T16:30:27.545Z | 17% |
| 09-04 23:14:43 | 2026-09-04T16:30:27.545Z | 17% |
| 09-04 23:25:34 | 2026-09-04T16:30:27.545Z | 17% |
| 09-04 23:26:34 (live) | 2026-09-04T16:30:27.545Z | 17% |

`fetchedAtMs` never moved across ~7 hours of continuous heavy use, and the
`five_hour.resets_at` it carries (19:50Z) had **already elapsed** by the time
the later snapshots were written. It appears to refresh only on demand (opening
`/usage`, and the docs describe a 60-minute last-known-usage window for that
screen). **Reading this file would have shipped a gauge frozen at a stale
percentage of an expired window — the exact failure mode the map warned about.**

### What is NOT there

- `~/.claude/stats-cache.json` — confirmed stale (`lastComputedDate:
  2026-07-19`), no limits. Matches the map's finding.
- `~/.claude/sessions/<pid>.json` — the live session registry the map found for
  active-session detection. Carries no usage or limit fields.
- No `~/.claude/usage-data/` on this machine (it is created by `/insights`, and
  holds HTML reports, not limits).
- `~/.local/state/claude/` — version locks only.
- Session JSONL transcripts — nothing, confirming the map's charting result.
- **`~/.claude/.credentials.json` was not read, per the ticket's constraint.**

### The server endpoint (for completeness — do not call it)

Claude Code fetches this from **`/api/oauth/usage`** (an OAuth-authenticated
endpoint; the binary also references `/api/claude_code/policy_limits`). Calling
it directly would require the credentials this ticket forbids touching, and would
duplicate an authentication path the pipeline has no business owning. **The
status line gives us the same numbers with no credential handling at all.**

---

## 5. How ccusage and comparable tools source a denominator

**They don't — they ask the user, or infer it, and both are wrong for us.**

ccusage's `blocks` feature models 5-hour windows entirely client-side. From
[its docs](https://ccusage.com/guide/blocks-reports):

> **Block Start**: Triggered by your first message · **Block Duration**: Lasts
> exactly 5 hours from start time · **UTC Time Handling**: Block boundaries are
> calculated in UTC.

For the denominator it offers exactly two options:

- `ccusage blocks --token-limit 500000` — a hand-supplied constant.
- `ccusage blocks --token-limit max` — **"Use highest previous block as limit"**,
  i.e. inference from observed maxima.

**Both are unsuitable, for reasons the map already anticipated.** `--token-limit
max` is precisely the circular inference the ticket forbids: it rescales silently
when the plan changes, and it can never read above 100%. And the explicit
constant is denominated in *tokens*, which §3 shows is not the unit the real
limit is measured in — so even a correct-looking number would drift against the
true window in a model- and feature-dependent way.

Separately, ccusage's **window model is measurably wrong for this account** (§1):
its "first message" anchor predicts a window start of `12:00:19Z`, where the
server reported `14:50:00Z`. That is a 2h50m error, ~57% of a window.

**Conclusion: ccusage's blocks feature is the wrong model to copy.** Its
selection-rank policy remains adopted for dedup (map, #15); its *window and
limit* modelling should be explicitly rejected in the spec so a future
implementer doesn't reach for it.

---

## 6. Does sidechain / sub-agent usage count?

**Yes — and for the gauge the question is moot, because the counting is
server-side.**

Direct documentary evidence that sub-agent usage counts against plan limits:

> On a Pro, Max, Team, or Enterprise plan, `/usage` also shows a breakdown of
> **what counts against your plan limits**: Attribution: recent usage attributed
> to **skills, subagents, plugins**, and individual MCP servers, each shown as a
> percentage of the total.
> — [Manage costs](https://code.claude.com/docs/en/costs)

The changelog states the same:

> `/usage` now shows a per-category breakdown of what's driving your limits
> usage — skills, subagents, plugins, and per-MCP-server cost.

And sub-agents are themselves subject to the account limit — several changelog
entries describe sub-agents being "cut off by a rate limit", including one fixing
sub-agents that "reported API errors (e.g. usage limit reached) as successful
results".

So the map's cost-side decision (include `isSidechain` rows) is consistent with
limit accounting. **But the gauge never has to act on it**: the server's
`utilization` already includes sub-agent traffic, along with agent teammates,
scheduled `/loop` tasks, background summarization, goal check-ins, and usage from
claude.ai and other machines. The sidechain rule stays a *historic-view* concern
only.

---

## 7. What I could NOT determine

Stated plainly, so the spec does not assume more than was established:

1. **The exact anchoring rule for the 5-hour window.** Measured evidence rules
   out ccusage's first-activity model and fixed clock boundaries for this
   account, but does not establish what the rule *is*. Anthropic does not
   document it. **This is fine — `resets_at` makes it unnecessary — but nobody
   should later "optimise" the shim away by reimplementing the window.**
2. **The numeric limit, in any unit.** Not published, not transmitted, and per
   §3 not a token count. Treat as permanently unobtainable.
3. **Whether `seven_day_opus` / `seven_day_sonnet` / `model_scoped` ever
   populate on a Pro plan.** They are `null` here. The schema supports them; I
   could not trigger them. The reader must tolerate their appearance without
   assuming it.
4. **The exact refresh trigger for `cachedUsageUtilization`.** Measured as "not
   in 7 hours of heavy use"; the precise condition (probably opening `/usage`)
   was not isolated. Doesn't matter given the recommendation not to use it.
5. **Whether a status line runs at all in headless/`-p` sessions.** Untested. If
   the maintainer ever wants the gauge fed by a non-interactive session, this
   needs checking.
6. **Behaviour at ≥100%.** `used_percentage` is documented as 0–100 for plan
   windows (only `spend_limit` is documented as able to exceed 100). Whether a
   plan window clamps at 100 or reports higher was not observed. The display
   should clamp defensively.

---

## 8. Recommendation for the spec

1. **Do not compute a percentage. Read one.** Source
   `rate_limits.five_hour.used_percentage` and `rate_limits.seven_day.used_percentage`
   from the status-line JSON. Never divide a local token sum by anything.
2. **Add a status-line shim** — a tiny script Claude Code invokes — that writes
   `{five_hour: {used_percentage, resets_at}, seven_day: {...}, updated_at}` to a
   fixed JSON path, and prints whatever the maintainer wants displayed. `push.py`
   reads that file. This is the only new moving part.
   - Prior art exists and is worth copying rather than inventing: the installed
     **claude-hud** statusline (`~/.claude/plugins/marketplaces/claude-hud`)
     already has exactly this feature — `display.externalUsageWritePath` writes a
     usage snapshot to disk, throttled and change-gated, with
     `externalUsageFreshnessMs` (default 300 000) for staleness. Since the
     maintainer *already runs claude-hud as their status line*, this may need
     configuration rather than code.
3. **Carry a staleness timestamp end-to-end.** The snapshot's `updated_at`, and
   `resets_at`, must reach the Pi so the panel can distinguish "17% and current"
   from "17% as of three hours ago" — the same staleness rule as
   [#24](https://github.com/peterderkoala/zeropi.display/issues/24).
4. **Handle the documented absences as first-class states**, not errors: window
   absent because it lapsed; `rate_limits` absent before the session's first API
   response; `rate_limits_available: false` under an API key or a cloud provider.
   None of these is 0%.
5. **Record the withdrawn fallback.** The map's "hand-configured constant,
   displayed as *of your configured limit*" should be struck: it is unnecessary
   (the real percentage is available) and unsound (§3 — there is no number to
   configure). Keep the *labelling* discipline though: the gauge is a percentage
   of a rolling window whose absolute size is unknown and unknowable, so the
   panel should say "5h window", never imply a token budget.
6. **Reject ccusage's blocks model explicitly**, so it isn't reached for later
   (§5).

---

## Sources

Primary sources only. Local-artifact claims are measured on the maintainer's
machine 2026-09-05 and are labelled as such above.

- [Customize your status line](https://code.claude.com/docs/en/statusline) —
  the `rate_limits` schema, field semantics, units, absence rules, update cadence
- [Manage costs effectively](https://code.claude.com/docs/en/costs) —
  rolling 5h + weekly seat allowance, limit-vs-model-limit behaviour, `/usage`
  attribution of sub-agents against plan limits
- [What is the Pro plan?](https://support.claude.com/en/articles/8325606-what-is-the-pro-plan) —
  5h session reset, fixed weekly reset slot, the "message length" unit, absence of published numbers
- [How do usage and length limits work?](https://support.claude.com/en/articles/11647753-how-do-usage-and-length-limits-work) —
  factors affecting usage; confirms no published unit or numbers
- [API rate limits](https://platform.claude.com/docs/en/api/rate-limits) —
  the org-level API tier mechanism, distinct from subscription windows
- [ccusage — Blocks reports](https://ccusage.com/guide/blocks-reports) —
  block anchoring model and `--token-limit` / `--token-limit max`
- Claude Code v2.1.261 shipped binary
  (`~/.local/share/claude/versions/2.1.261`) — `anthropic-ratelimit-unified-*`
  header set, internal window schema, window-kind enumeration, `/api/oauth/usage`
- Claude Code changelog cache (`~/.claude/cache/changelog.md`) — status-line
  `rate_limits` history, sub-agent rate-limit handling, `/usage` limit breakdown
- claude-hud v-installed source
  (`~/.claude/plugins/marketplaces/claude-hud/src/{types,stdin,external-usage}.ts`)
  — an independent implementation of the same stdin contract, and the
  external-snapshot write path
