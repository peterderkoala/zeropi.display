# Synthetic JSONL fixture (spec §11.2)

Everything under `tests/fixtures/claude_projects/` is **hand-constructed,
synthetic** data shaped like `~/.claude/projects/<project-key>/**/*.jsonl`
(spec §2). It is **not derived from any real `~/.claude` logs** — no prompts,
no file contents, no real session ids or paths. It exists only to exercise
`desktop/usage.py`'s reader once that module lands (#42); this ticket (#45)
only builds the harness and the fixture, not assertions against it (the
module doesn't exist yet in this worktree).

Project-key directory names below were produced by hand-applying the encode
rule from spec §4.3 (`re.sub(r"[^a-zA-Z0-9]", "-", path)`) to the fictional
absolute paths named in each comment, so a future test can assert
`encode(cwd) == dirname` the same way `usage.py` will.

## Layout and case coverage

Case numbers are spec §11.2's table.

### `claude_projects/-home-tester-code-zeropi-fixture/` (project root `/home/tester/code/zeropi-fixture`)

- **`session-main.jsonl`** — session `sess-mainA`:
  - Line 1: a `type: "user"` entry with no `message.usage` — confirms only
    `type == "assistant"` entries carrying `message.usage` are considered.
  - Lines 2-4 (**case 1**): duplicate group `req-dup1` / `msg-dup1`. Two
    provisional early copies (`output_tokens: 5`, then `40`, both
    `speed: null`) followed by the final copy (`output_tokens: 114`,
    `speed: 182.3`). Keep-first must lose 96.5% of this group's output
    tokens; keep-winner must not. Also carries a `usage.iterations` object on
    every line, restating input/output, to confirm totals are read from the
    top-level `usage` fields and never from `iterations`.
  - Line 5 (**case 2**): `req-cache1` — mixed-TTL cache write, both
    `cache_creation.ephemeral_5m_input_tokens` (1000) and
    `.ephemeral_1h_input_tokens` (2000) non-zero, summing to the top-level
    `cache_creation_input_tokens: 3000`.
  - Line 6 (**case 3**): `req-nodetails` — `output_tokens_details` key is
    absent entirely from `message.usage`.
  - Line 7 (**case 4**): `req-thinking` — `output_tokens_details.thinking_tokens: 150`,
    non-zero and `<= output_tokens` (200). Must not be added to cost (already
    inside `output_tokens`).
  - Line 8 (**case 5**): `req-datesuffix` — `model: "claude-haiku-4-5-20251001"`,
    a date-suffixed id that must prefix-match `claude-haiku-4-5` and be
    *stored* normalised to that prefix (§4.6).
  - Line 9 (**case 6**): `req-unknownmodel` — `model: "claude-unknown-9"`,
    not in the pricing table. Tokens must still be counted, cost `0.0`,
    `cost_complete: false`, no exception.
  - Lines 10-11 (**case 7**): `req-synth1` / `msg-synth1` is a `<synthetic>`
    all-zero entry that **has** `requestId`/`message.id` — kept, free,
    `cost_complete` stays `true`. The following line has **no `requestId`**
    field at all — must be dropped regardless of its content.
  - Line 12: `req-postsidechain` — a non-sidechain entry timestamped
    *before* the sidechain line below (and before case 10's two lines that
    follow it — see below).
  - Line 13 (**case 8**, reader half): `req-sidechain1`, `isSidechain: true`,
    distinctive usage (`999`/`999`) so a test can assert these tokens *are*
    counted by `usage.py`'s reader (§4.1) but, paired with line 12 above,
    would be *wrongly* picked as "latest entry" by a context reader that
    doesn't skip sidechains — the opposite assertion for gauge.py's §5.2
    context read. ⚠ Because case 10's two lines below are chronologically
    later still, a §5.2 context-read test exercising this pair must slice
    the file up to and including line 13 (e.g. read only `sess-mainA`'s
    entries with `timestamp < "2026-09-01T18:00:00Z"`, or copy lines 1-13 to
    a dedicated single-purpose fixture) rather than reading the whole file
    and asserting on "the latest non-sidechain entry" — read whole, the
    true latest non-sidechain entry is `req-midnight-after` (case 10, line
    15), not `req-postsidechain`.
  - Lines 14-15 (**case 10**): `req-midnight-before` at `2026-09-02T02:00:00Z`
    and `req-midnight-after` at `2026-09-02T05:00:00Z`. Local midnight for
    `America/New_York` (UTC-4 in September) falls at `04:00Z`, so under that
    zone these two timestamps — 3 hours apart, same UTC calendar day — bucket
    to **different** local dates (`2026-09-01` and `2026-09-02`
    respectively). A test exercising this case should set `TZ=America/New_York`
    before bucketing.

- **`sess-mainA/subagents/sub1.jsonl`** — a sub-agent log nested under the
  session directory, per spec §2's "sub-agent logs are nested, so a
  recursive glob attributes them to the right project". One `isSidechain: true`
  entry (`req-subagent1`), contributing to the same **case 8** reader-side
  assertion (sidechain entries are ~18% of real spend and are counted).

- **`session-second.jsonl`** (**case 9**): session `sess-mainB`, one entry
  `req-secondsession` on the same date (`2026-09-01`), same project, same
  model (`claude-opus-5`) as entries in `session-main.jsonl` — a second,
  distinct `sessionId` in the same `(date, project, model)` group, for
  `session_count` distinctness.

- **`session-case14-reread.jsonl`** (**case 14**): `req-dup1` / `msg-dup1` —
  the *same key* as case 1's duplicate group, but `isSidechain: true` and
  `output_tokens: 5` (a strict loser against the stored winner from
  `session-main.jsonl`). Represents a line re-read on a later ingest run: the
  stored winner must not be overwritten, and its `pushed_at` must not be
  cleared.

### `claude_projects/-home-tester-code-zeropi-allzero/` (project root `/home/tester/code/zeropi-allzero`)

- **`session-allzero.jsonl`** (**case 11**): one entry, `req-allzero-day`,
  with every usage field zero, on a day with no other entries — the
  aggregated Reading for `(2026-09-03, this project, claude-opus-5)` sums to
  all-zero and must be **dropped before pushing**, never sent as a zero row.

### `claude_projects/-home-tester-code-myproj/` (project root `/home/tester/code/myproj`)

- **`session-worktree.jsonl`** (**case 12**): one entry whose `cwd` is
  `/home/tester/code/myproj/.claude/worktrees/feature-x` — a worktree path.
  No `cwd` anywhere under this project directory ever equals the bare
  project root, so R1 of the label cascade (§4.3) fails; truncating at
  `/.claude/worktrees/` yields `/home/tester/code/myproj`, which *does*
  encode to the directory name, so R2 must succeed with label `myproj`.

### `claude_projects/-home-tester-code-zeropi-append/` (project root `/home/tester/code/zeropi-append`)

- **`session-case13-stage1.jsonl`** / **`session-case13-stage2.jsonl`**
  (**case 13**): two snapshots of what is conceptually the *same* session
  file at two points in time. Stage 1 has one entry (`req-append1`); stage 2
  has that identical line (byte-for-byte — verified by hand, `md5sum` of the
  first line matches across both files) plus one appended entry
  (`req-append2`). A test simulates "appended between ingest runs" by
  copying stage 1 into place as the live filename, ingesting (recording a
  high-water mark at the end of stage 1's one line), then overwriting the
  same filename with stage 2's content and ingesting again — the resume
  logic (§4.5, `MAX(source_end_offset)` per `source_file`) must pick up only
  `req-append2`, not re-process or skip `req-append1`.

## What is deliberately not here

- No fixture data for `gauge.py`'s rate-limit snapshot or session registry
  (spec §5) — §11.2's 14 cases are all about the JSONL usage logs. Fixtures
  for the Gauge reader are `desktop/gauge.py`'s ticket (#43) to add.
- No stub `usage.py` or `gauge.py`. Per this ticket's scope, those are left
  entirely to #42/#43.
