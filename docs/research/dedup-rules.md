# Research: dedup and attribution rules for the Claude Code JSONL logs

Resolves [#15](https://github.com/peterderkoala/zeropi.display/issues/15) (child of
[map #13](https://github.com/peterderkoala/zeropi.display/issues/13)).

Researched 2026-09-04. Primary sources: the maintainer's own
`~/.claude/projects/**/*.jsonl` (measured directly, throwaway scripts), and the
`ccusage` Claude adapter source
(<https://github.com/ryoppippi/ccusage>, `rust/adapters/claude/src/lib.rs` and
its `README.md`, at `63a2fb3`). No log content is reproduced here — counts, ids
and structure only.

**Headline: `(requestId, message.id)` is correct and sufficient — but it is only
half the rule. The other half is *which* copy of a duplicate group you keep.
Keeping the first occurrence, the obvious implementation, silently discards
26.2% of all output tokens, because Claude Code writes a provisional usage
record before the final one. `ccusage` gets this right and gets the key wrong;
we should take its selection policy and reject its key.**

Corpus snapshot: 126 files (36 session, 90 sub-agent), 27,105 lines, 11,689
usage entries, 3 projects.

> **The corpus is live.** The research session was itself writing to
> `-home-ryzen-git-zeropi-display` while reading, so totals drift upward by a few
> tenths of a percent between passes (11,503 usage entries when the map was
> charted, 11,573 early in this ticket, 11,689 at the final pass). Every figure
> in §1–§6 comes from one consistent pass; §7–§9 figures are from a slightly
> later pass and are marked where the difference is visible. **Ratios are stable;
> absolute totals are not, and should not be treated as fixtures.**

---

## 1. The recommended rule

Copy this into the spec.

**Selection.** Consider only entries with `type == "assistant"` that carry a
`message.usage` object. No other entry type in the corpus carries usage — see §6.

**Dedup key.** `(requestId, message.id)`. Both are strings on the entry
(`requestId`) and on `message` (`id`). Do **not** add `sessionId` (§5).

**Winner selection within a duplicate group.** Rank the group and keep the
single highest-ranking entry, by this comparison in order:

1. Prefer `isSidechain` falsy over `isSidechain` truthy.
2. Else prefer the higher sum of
   `input_tokens + output_tokens + cache_creation_input_tokens + cache_read_input_tokens`.
3. Else prefer the entry whose `message.usage.speed` is non-null.

This is `ccusage`'s `should_replace_deduped_entry`, arrived at independently
here from the data and then found to match. Sorting the group by
`(file, line)` and keeping the **last** entry gives a bit-identical result on
this corpus, and is a legitimate simpler implementation — but it relies on file
order, so prefer the explicit ranking.

**Read the totals from the top-level `message.usage` fields**, never from
`message.usage.iterations` (§4).

**Cost inputs.** Take cache-write tokens from
`message.usage.cache_creation.ephemeral_5m_input_tokens` and
`ephemeral_1h_input_tokens`, not from `cache_creation_input_tokens`. They sum to
the top-level field in 5,801 / 5,801 deduped entries, and the 1h half is
7,550,205 tokens against the 5m half's 7,468,731 — near a 50/50 split, at double
the price. (This confirms the split flagged in the map and priced in
[#14](https://github.com/peterderkoala/zeropi.display/issues/14).)

**Unkeyed entries: drop them.** Rule: skip any usage entry lacking `requestId`
or `message.id` (§3).

**Project attribution.** See §7 for the cascade.

### What it is worth

| Rule | Total tokens | vs recommended |
|---|---|---|
| No dedup | 1,785,492,272 | **1.920x — inflated** |
| Dedup, keep **first** occurrence | 928,823,661 | loses 1,071,647 output tokens (**26.2%**) |
| Dedup, keep **last** occurrence | 929,895,308 | identical to recommended |
| Dedup, keep **max token total** | 929,895,308 | identical to recommended |
| **Recommended (ccusage rank)** | **929,895,308** | — |

The keep-first error is invisible in the *total* (output is only 0.44% of all
tokens, which are dominated by cache reads) but it is very visible in **cost**,
where output is priced 50x cache read.

---

## 2. Why the duplication exists — the mechanism

The map hypothesised sub-agent replay, resumed sessions, or compaction
rewrites. **It is none of those.** There are two mechanisms, and the big one is
mundane.

### Mechanism A — content-block fan-out (99.6% of duplication)

**Every usage-carrying line in the corpus has exactly one content block**
(`len(message.content) == 1`, for all 11,689 entries, no exceptions). One API
assistant response containing a thinking block, a text block and three
`tool_use` blocks is written as **five consecutive JSONL lines**, each repeating
the whole `message.usage` for the request.

Evidence:

- 4,339 of the 4,357 multi-occurrence keys are duplicated **within a single
  file**.
- 3,911 of those occupy strictly **contiguous line numbers**, and in exactly
  those 3,911 each entry's `parentUuid` equals the previous entry's `uuid` —
  they are a linked chain, not independent records.
- The remaining 430 within-file groups are non-contiguous only because
  `attachment` / `user` lines are interleaved between the blocks; same session,
  same chain.
- Every entry has a distinct `uuid` and a distinct `timestamp` (identical
  timestamps in 1 group of 4,340).
- The observed content-block sequences are exactly what a single assistant turn
  looks like: `('thinking','tool_use')` 1,984 times, `('text','tool_use')`
  1,112, `('thinking','text','tool_use')` 471, and so on.

Multiplicity runs from x2 (3,353 keys) to x12 (1 key) — i.e. a response with
twelve content blocks.

**This is the finding that changes the rule.** Because the copies are written as
the response streams, the *early* copies carry a **provisional** usage snapshot
and the *final* copy carries the real one:

- In 1,238 groups the copies disagree on `output_tokens`. In **all 1,238** the
  first copy is below the maximum; in 1,189 the maximum sits on the last line
  (the other 49 are ties on the last two lines, so keep-last still wins).
- The discriminator is perfectly clean: of 3,104 entries in disagreeing groups,
  the 1,817 provisional ones **all** have `iterations`, `speed` and
  `server_tool_use` simultaneously `null`, and the 1,287 final ones **all** have
  all three non-null. Zero exceptions either way.
- Typical magnitude: a group where the provisional copy reports
  `output_tokens: 5` and the final copy reports `114`.
- `input_tokens`, `cache_creation_input_tokens` and `cache_read_input_tokens`
  **never** differ within a group (0 groups). Only output is provisional.

So the key is complete, but naive "insert if not seen" dedup is wrong. You must
keep the *last/largest*, not the first.

### Mechanism B — session fork/resume replay (0.4%)

17 keys appear in two different top-level session files, all in one project.
The two files (`064f449b-…` and `98088fcd-…`) share an identical first
timestamp; one ends 26 minutes after the other. The shorter is a prefix of the
longer: resuming or forking a session **copies the earlier transcript into the
new session file**, preserving `requestId` and `message.id`, so the dedup key
collapses them correctly.

Corroboration: a single `sessionId` value (`0c96c5e3-…`) appears as a field
inside **four different files**. `sessionId` is not confined to its own file.

1 further key spans a session file and one of its `subagents/` files.

Neither mechanism defeats `(requestId, message.id)`.

---

## 3. The entries with no key

Both of them (now 2 of 11,689) are the same thing:

| File | Line | Model | `isApiErrorMessage` | Tokens |
|---|---|---|---|---|
| `…messagebroker-demo/0dc6505c-….jsonl` | 58 | `<synthetic>` | `true` | 0 |
| `…messagebroker-demo/ff2febc5-…/subagents/agent-ac774….jsonl` | 87 | `<synthetic>` | `true` | 0 |

They have `message.id` but **no `requestId`** — because no API request was made.
They are locally-generated API **error** messages: they carry an `error` key and
`isApiErrorMessage: true`, their model is `<synthetic>`, and every field of
`message.usage` is `0` or `null`.

**Rule: drop entries lacking `requestId` or `message.id`.** They are numerically
free (all-zero) so counting them is harmless, but dropping them is the honest
rule and avoids a synthetic-key mechanism that would have to be maintained. Note
this makes the rule agree with the `<synthetic>`-is-free decision already in the
map.

Aside: `requestId` alone and `message.id` alone each produce the same total as
the pair on this corpus (5,801 / 5,803 groups, identical token sums). The pair
is redundant but strictly safer, and it costs nothing. Keep both.

---

## 4. `iterations` restates the totals — ignore it, with one caveat

`message.usage.iterations` is an array of per-iteration usage objects.

- Length histogram across the corpus: **9,198 entries with exactly 1**, 1 entry
  with 0, 2,397 with `null`. Never more than 1.
- For all 9,198 single-element cases, the iteration's `input_tokens`,
  `output_tokens`, `cache_creation_input_tokens` and `cache_read_input_tokens`
  each **exactly equal** the corresponding top-level field. It is a pure
  restatement.
- The 1 empty-array case has non-zero top-level tokens, so `iterations` is also
  not reliably populated.

**Confirmed: the top-level `usage` fields are authoritative, and adding
`iterations` on top would double every number.** Ignore the array.

**Caveat, from the ccusage README (not observable in this corpus).** Iteration
records carry their own `type`. All 9,239 iteration records here are
`type: "message"`. ccusage documents a second kind, `type: "advisor_message"`,
which is **not** included in the top-level totals and which ccusage counts
separately under the record's own model. If advisor records ever appear, "ignore
iterations" would start *under*counting. The spec should state the rule as:

> Ignore `message.usage.iterations` entirely, except that any element with
> `type == "advisor_message"` is additional usage not represented in the
> top-level totals. None exist in the current corpus; if one appears, the
> unknown-model path applies.

---

## 5. `ccusage` uses a different key — and it is wrong for us

`ccusage` keys on **`(message.id, requestId, effective sessionId)`**
(`usage_dedupe_hash`, `rust/adapters/claude/src/lib.rs:212`), where the
effective session id is the entry's own `sessionId` field falling back to the
file/directory-derived one.

Its documented reason (adapter `README.md`) is to *"keep gateway responses that
reuse a message ID in different sessions separate"* — i.e. it defends against
API-gateway or proxy setups where `message.id` may collide across sessions.

**Measured on this corpus, adding `sessionId` is a net loss.** It splits exactly
the 17 fork/resume keys from §2 Mechanism B and **double-counts 946,816 tokens**
(+0.102%). The split groups are provably the same API call — identical token
totals (40,014, 40,216, 45,446 …) recorded under two different session ids.

| Key | Groups | Total tokens |
|---|---|---|
| `(requestId, message.id)` | 5,801 | 929,895,308 |
| `+ sessionId` (ccusage) | 5,818 | 930,842,124 (**over by 946,816**) |

The collision `sessionId` defends against cannot arise for us: `requestId` is a
server-assigned globally-unique token (`req_011C…`), so the pair is already
unambiguous. We read one machine's local logs, with no gateway. **Keep the
two-part key.**

ccusage also carries a sidechain-replay path for `/btw` aside-question logs,
which replay parent messages *with a different `requestId`* (its issue #913).
That would defeat our key. It does not occur in this corpus — see §8.

Where ccusage is right, and where this research independently landed first, is
the **selection policy** (§1): it explicitly replaces an already-seen entry when
the new one has a higher token total or has `speed` set. That is the fix for the
26.2% output-token loss.

---

## 6. Session-linking entry types imply nothing about usage

Checked every type the ticket named. **None of them carry `message.usage`.**
Across all 27,105 lines, `message.usage` appears on `type: "assistant"` and
nothing else.

| Type / field | Count | Shape | Implies replay of usage? |
|---|---|---|---|
| `bridge-session` | 370 | `sessionId`, `bridgeSessionId` (`cse_…`), `lastSequenceNum`, `ownerAccountUuid`, `ownerOrganizationUuid` | No — no usage, no message |
| `fork-context-ref` | 1 | `agentId`, `parentSessionId`, `parentLastUuid`, `contextLength` | No — a pointer only. The *only* place `parentSessionId` occurs |
| `frame-link` | 1 | `sessionId`, `path`, `frameUrl`, `title` | No — an artifact link |
| `leafUuid` | 1,016 | only on `type: "last-prompt"` | No |
| `isSnapshotUpdate` | 383 | only on `type: "file-history-snapshot"` | No |
| `sessionKind` | 231 | only value is `"bg"`; appears on `user`/`assistant`/`system`/`attachment` | No — but see below |
| `cost-state` | 7 | `totalCostUSD`, `modelUsage`, `hasUnknownModelCost`, durations | No — it is the oracle, not input |

`sessionKind: "bg"` marks background-session entries, 122 of them `assistant`.
Those are real API calls with real usage and are already counted by the rule —
no special handling, just don't filter them out.

---

## 7. Sub-agents are not double-counted, and are 17.7% of spend

**Confirmed: sub-agent entries do not appear in the parent transcript.**

- Of 5,801 deduped keys, exactly **1** appears in both a session file and a
  `subagents/` file. Sub-agent keys: 1,856. Top-level keys: 3,906. Overlap: 1.
- `isSidechain` is a perfect proxy for file location: 7,465 usage entries in
  session files are all `isSidechain: false`; 4,149 in `subagents/` files are
  all `isSidechain: true`. No exceptions.
- Every one of the 90 sub-agent files has exactly one `sessionId`, equal to the
  `<session-uuid>` directory it sits under.

So there is no double-count hazard, and the one overlapping key is handled by
the dedup key anyway (with the sidechain-deprioritising tiebreak in §1 picking
the parent copy, matching ccusage).

Sub-agent work is **~18%** of deduped tokens (167,710,965 of 930,970,767,
attributed by the winning entry's file). It must be counted, as the map decided.

---

## 8. Project attribution — a better rule than the map's, and the worktree fallback

### The exact rule

The map's label rule is "basename of the most common non-worktree `cwd`", which
is statistical and has no answer for a worktree-only directory. There is an
**exact** rule available, because the project directory name is a lossy but
*verifiable* encoding of the project root:

```
dirname == re.sub(r'[^a-zA-Z0-9]', '-', cwd)
```

Verified on all three projects: `/home/ryzen/git/cinescout` →
`-home-ryzen-git-cinescout`; `/home/ryzen/git/messagebroker_demo` →
`-home-ryzen-git-messagebroker-demo`; `/home/ryzen/git/zeropi.display` →
`-home-ryzen-git-zeropi-display`.

The encoding is **not invertible** — `_` and `.` both map to `-`, so
`-home-ryzen-git-zeropi-display` cannot be decoded back to `zeropi.display`. But
it can be *checked*: a candidate `cwd` is the project root iff its encoding
equals the directory name. That turns a guess into a test.

### The cascade (recommended)

Group by the top-level directory under `~/.claude/projects/` (unchanged from the
map). For the label, collect every `cwd` seen anywhere in that directory
(including `subagents/`) and apply, in order:

- **R1.** If any `cwd` encodes to the directory name → `basename(cwd)`.
- **R2.** Else, for any `cwd` containing `/.claude/worktrees/`, truncate at that
  marker; if the truncation encodes to the directory name → `basename(root)`.
- **R3.** Else, walk each `cwd`'s ancestors; if an ancestor encodes to the
  directory name → `basename(ancestor)`.
- **R4.** Else, give up on `cwd`: use the directory name with its leading `-`
  stripped, taking the segment after the last `-`. **Mark the row's label as
  unverified** (this is lossy and can be wrong — see below).

Tested by simulation against the real corpus:

| Scenario | cinescout | messagebroker | zeropi.display |
|---|---|---|---|
| Real data | `cinescout` (R1) | `messagebroker_demo` (R1) | `zeropi.display` (R1) |
| **Only worktree sessions** | `cinescout` (R2) | `messagebroker_demo` (R2) | n/a (no worktrees) |
| Only a subdirectory `cwd` | `cinescout` (R3) | `messagebroker_demo` (R3) | `zeropi.display` (R3) |
| Only an unrelated `/tmp` `cwd` | `cinescout` (R4) | — | — |
| R4 output (worst case) | `cinescout` ✓ | `demo` ✗ | `display` ✗ |

**R2 answers the ticket's question directly**: a project directory containing
only worktree sessions is fully recoverable, exactly, with no statistics —
because worktrees live at `<project-root>/.claude/worktrees/agent-<id>` and the
root is exactly what the directory name encodes. In the corpus, worktree `cwd`s
account for 3,561 of cinescout's 14,322 `cwd`-bearing entries, and every one of
them truncates to a root that passes the encoding check.

R4 is the genuine dead end, and the table shows why it must be flagged rather
than trusted: it yields `demo` and `display` instead of `messagebroker_demo` and
`zeropi.display`. It is unreachable in practice — it requires a project
directory whose every `cwd` lies outside the project root (the corpus has one
such `cwd`, a `/tmp` scratchpad, but never as the only one).

Note the map's "most common non-worktree `cwd`" rule also survives contact with
the data (root beats subdirectory 10,686:1 in cinescout, 3,592:42 in
messagebroker, 1,944:661 in zeropi.display) — R1–R3 is simply exact where that
is merely likely, and defined where that is undefined.

---

## 9. The `cost-state` oracle is a *lower*-bound check, not an equality check

Worth recording, because [#14](https://github.com/peterderkoala/zeropi.display/issues/14)
validated the *pricing formula* against `cost-state`'s own `modelUsage` token
counts, and it is tempting to then expect transcript-derived counts to match too.
**They don't, and they can't.**

Applying #14's pricing table to the deduped transcript, per session with a
`cost-state` entry:

| Session | `cost-state` | Transcript | Ratio |
|---|---|---|---|
| `0c96c5e3` | $2.3630 | $2.0232 | 85.6% |
| `11f844d4` | $1.3705 | $1.2413 | 90.6% |
| `6fa80b56` | $7.9367 | $7.8521 | 98.9% |
| `d280732c` | $3.2866 | $2.8541 | 86.8% |
| `f8733782` | $7.6406 | $6.9963 | 91.6% |
| **Total** | **$22.5975** | **$20.9670** | **92.8%** |

Always under, never over — which is the reassuring direction, and is itself
evidence the dedup rule does not overcount.

The shortfall is API calls that are billed to the session but never written to
the transcript as `assistant` entries:

- **`claude-haiku-4-5-20251001` is 100% missing.** It appears in every non-empty
  `cost-state.modelUsage` (e.g. 21,684 input tokens in one session) and has
  **zero** entries in the whole transcript. Haiku runs the background chores —
  conversation titles (940 `ai-title` entries), agent names (9 `agent-name`) —
  and none of those record usage.
- **`input_tokens` is short by 70–98%** even for Sonnet (oracle 5,932 vs
  transcript 106 in one session), while `thinkingTokens` matches the oracle
  **exactly** in 4 of 5 sessions. Uncached input is exactly what auxiliary
  one-shot calls consume, which points the same way.

So: the model census in the map (`claude-sonnet-5`, `claude-opus-5`,
`<synthetic>`) is correct *for the transcript*, and the display's numbers will
sit around 93% of what `/cost` reports. That is a property of the data source,
not a bug to chase. Use `cost-state` as a sanity band ("transcript should be
85–100% of oracle, never above"), not as an equality assertion.

---

## 10. What I could not determine

- **Whether `iterations` ever holds more than one element**, and therefore
  whether `advisor_message` records are a live risk. The corpus has 9,198
  single-element and zero multi-element arrays. §4's caveat rests on ccusage's
  README, not on observation here.
- **The exact composition of the 7% cost-state shortfall.** Haiku background
  calls are confirmed absent from the transcript and explain the haiku line
  entirely, but the residual Sonnet/Opus `input_tokens` and `cache_read` gap is
  inferred, not proven — it would need a session run under instrumentation.
- **Whether `/btw` sidechain replay (ccusage issue #913) can occur here.** It
  replays parent messages with a *different* `requestId`, which would defeat the
  recommended key. No `/btw` usage exists in this corpus, so it is untested. If
  the maintainer starts using `/btw`, revisit — the mitigation is ccusage's
  fallback key `(message.id, sessionId)` plus timestamp equality.
- **Compaction.** No compaction-summary entry type was found, and no evidence of
  rewritten history appeared. Either this corpus has none, or it leaves no
  distinctive trace.
