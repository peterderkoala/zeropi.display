# Research: per-model token pricing for the cost table

Resolves [#14](https://github.com/peterderkoala/zeropi.display/issues/14) (child of
[map #13](https://github.com/peterderkoala/zeropi.display/issues/13)).

Researched 2026-09-04. Primary sources: the Anthropic pricing, service-tiers and
Models API reference docs (linked inline). Cross-checked arithmetically against
the 7 `type:"cost-state"` entries in the maintainer's own
`~/.claude/projects/**/*.jsonl` logs.

**Headline: the table below reproduces all 7 `cost-state` totals to
floating-point exactness (worst delta 3.6e-15 USD), but only once the
cache-write TTL split is honoured. Costing off `cache_creation_input_tokens`
alone is wrong by up to +5.4% / -4.1% on a real session.**

---

## 1. The pricing table

Source: <https://platform.claude.com/docs/en/about-claude/pricing> § Model
pricing. All figures USD per million tokens (MTok).

| Model id | Input | Output | Cache write 5m | Cache write 1h | Cache read |
|---|---|---|---|---|---|
| `claude-opus-5` | 5.00 | 25.00 | 6.25 | 10.00 | 0.50 |
| `claude-sonnet-5` | 2.00 | 10.00 | 2.50 | 4.00 | 0.20 |
| `claude-haiku-4-5` | 1.00 | 5.00 | 1.25 | 2.00 | 0.10 |

`claude-haiku-4-5` is not in the map's measured-model list, but it **does**
appear in every non-empty `cost-state` entry, and it was the key that unlocked
the web-search rate (§4). Its logged id is date-suffixed
(`claude-haiku-4-5-20251001`), so the table lookup must match on prefix, not on
exact string equality.

The multipliers are structural, not per-model (pricing doc § Prompt caching):
5m write = **1.25x** input, 1h write = **2x** input, cache read = **0.1x**
input. Sonnet 5's $2/$10 is confirmed permanent — the doc carries an explicit
note that the scheduled 2026-09-01 rise to $3/$15 "will not occur".

Not relevant to us, but they are the things that would invalidate the table if
they ever showed up in the logs: Batch API (-50%), fast mode (Opus 5 at
$10/$50), and `inference_geo: "us"` (1.1x on everything). None appear in this
corpus.

## 2. The cache-write TTL split — the thing that matters

`message.usage.cache_creation` splits into `ephemeral_1h_input_tokens` and
`ephemeral_5m_input_tokens`, priced at **2x** and **1.25x** base input
respectively. They are *not* interchangeable.

**Verified over all 11,593 `type:"assistant"` usage entries in the corpus:**

- `ephemeral_1h_input_tokens + ephemeral_5m_input_tokens ==
  cache_creation_input_tokens` in **11,593 / 11,593** entries. Zero mismatches.
  The sum relationship holds absolutely; `cache_creation` is always present.
- Corpus-wide the mix is **44.9% 1h / 55.1% 5m** (16,417,947 vs 20,159,765
  tokens). This is not a rounding-error concern — it is roughly half the
  cache-write spend sitting on the wrong side of a 1.6x price gap.

**Cost impact, measured.** Session `d280732c` genuinely mixes both TTLs
(88,679 tokens at 1h, 113,096 at 5m in the transcript). Pricing its
`cacheCreationInputTokens` at a single flat rate gives:

| Assumption | Computed session total | Delta vs logged $3.2866289 |
|---|---|---|
| all 1h ($4.00) | $3.4629224 | **+$0.1763 (+5.36%)** |
| all 5m ($2.50) | $3.1507634 | **-$0.1359 (-4.13%)** |
| true split (90,577 @ 1h / 117,529 @ 5m) | $3.2866289 | **$0.0000000** |

So the ticket's premise is confirmed empirically. `desktop/usage.py` must read
`usage.cache_creation.ephemeral_1h_input_tokens` and
`.ephemeral_5m_input_tokens` and price them separately. Using
`cache_creation_input_tokens` is a silent several-percent error.

A caveat for the Payload schema: the map settled that the Payload carries "all
four token classes". Cache-write is really **two** billed classes. Either the
Payload carries five token counts, or the Desktop keeps cache-write split
internally for costing and sums it to one number for transport. Cost is
computed on the Desktop anyway, so the second option is fine — but it must be a
deliberate choice, not an accident, because the Pi can never recover the split.

## 3. Thinking tokens

**Billed as output, and already inside `output_tokens`. Do not add them.**

Two independent confirmations:

1. Over all 11,593 entries, `output_tokens_details.thinking_tokens <=
   output_tokens` holds in **11,593 / 11,593** cases. Zero violations.
2. The reconciliation in §5 uses `output_tokens * output_price` with no
   thinking term at all and lands on the logged cost exactly. Adding
   `thinking_tokens` separately would have overshot session `11f844d4` by
   $0.0986 (9,856 thinking tokens x $10/MTok).

Corpus-wide, thinking is 953,383 of 7,788,688 output tokens (**12.2%**) — so
double-counting it would inflate the output line by 12% and the session total by
a few percent. Worth an explicit test.

Parsing note: **8,616 of 11,593 entries have no `output_tokens_details` key at
all.** Default it to 0; do not assume the key exists. `thinking_tokens` is
therefore a *reporting* breakdown, useful for the display, but it must be
excluded from the cost arithmetic.

## 4. Server tools

Source: pricing doc § Web search tool / § Web fetch tool.

- **`web_search_requests`: billed, $10 per 1,000 searches = $0.01 per request**,
  on top of token costs. Errored searches are not billed; each search counts
  once regardless of result count.
- **`web_fetch_requests`: not billed.** "The web fetch tool is available on the
  Claude API at no additional cost." You pay only for the fetched content as
  input tokens, which `input_tokens` already covers.

The $0.01 figure is not just doc-derived — it falls out of the logs exactly.
Session `11f844d4`'s Haiku line has zero cache tokens, making it a clean
three-term equation:

```
  21,684 input  x $1/MTok = $0.021684
     773 output x $5/MTok = $0.003865
                            ----------
                    tokens = $0.025549
            logged costUSD = $0.045549
                  residual = $0.020000   =   2 web searches x $0.01
```

`webSearchRequests` in `cost-state` therefore maps 1:1 to
`server_tool_use.web_search_requests`. In this corpus both counters are **0**
across all assistant entries (the 2 searches in `11f844d4` were made by Haiku,
whose calls are not written to the transcript — see §6), so the term is
currently always zero for the Desktop's purposes. Implement it anyway; it costs
one multiply and it is the difference between a correct row and a quietly wrong
one the first time a session uses search.

## 5. Cross-check against the 7 `cost-state` entries

Method: recompute each entry's `modelUsage` from the table in §1 —
`input x in + output x out + cacheRead x read + cacheCreation x write +
webSearchRequests x $0.01`, thinking excluded — and compare to the logged
`costUSD` and `totalCostUSD`.

| # | Session | Logged `totalCostUSD` | Recomputed | Delta |
|---|---|---|---|---|
| 1 | `11f844d4` | 1.3705342000 | 1.3705342000 | 0.0e+00 |
| 2 | `6fa80b56` | 7.9367310000 | 7.9367310000 | -3.6e-15 |
| 3 | `0c96c5e3` | 2.3630290000 | 2.3630290000 | -8.9e-16 |
| 4 | `d280732c` | 3.2866289000 | 3.2866289000 | 0.0e+00 * |
| 5 | `382559ff` | 0.0000000000 | 0.0000000000 | 0.0e+00 |
| 6 | `382559ff` | 0.0000000000 | 0.0000000000 | 0.0e+00 |
| 7 | `f8733782` | 7.6405785000 | 7.6405785000 | -8.9e-16 |

**All 7 reconcile to floating-point noise.** Deltas are 1e-15 or smaller —
double-precision rounding, not pricing error.

\* Entries 1, 2, 3 and 7 reconcile with `cacheCreationInputTokens` priced
entirely at the **1h** rate, because those sessions really are 100% 1h in the
transcript (verified: 5m totals are literally 0). Entry 4 is the mixed one and
needs the split, as shown in §2. Note that `cost-state` only stores the *sum*,
so entry 4 cannot be re-priced from the `cost-state` fields alone — the split
was recovered by solving `x*$4 + (208,106-x)*$2.50 = $0.6561305`, giving
`x = 90,577` exactly (an integer, which is itself a good sign). The transcript's
own deduped split for that session is 88,679 / 113,096 — a 43.95% 1h share
against the 43.52% the arithmetic implies. Independent corroboration.

Entry 7 is the only `claude-opus-5` sample and it reconciles exactly at
$5 / $25 / $10 / $0.50, so the Opus 5 row is confirmed by measurement and not
only by the doc.

## 6. Limitation of the oracle (important)

`cost-state.modelUsage` is a sound oracle **for the pricing table**. It is
**not** a sound oracle for the Desktop's parser output. The transcript does not
contain every API call the cost accumulator saw:

| Session | Field | `cost-state` | Deduped transcript |
|---|---|---|---|
| `d280732c` | sonnet-5 input | 10,065 | 218 |
| `d280732c` | sonnet-5 output | 47,654 | 33,562 |
| `f8733782` | opus-5 cache read | 9,843,757 | 8,727,247 |
| all 5 | haiku-4-5 (any field) | non-zero | **0** |

Haiku 4.5 never appears as a `type:"assistant"` entry anywhere in the corpus —
its calls (title generation, quota checks, and similar) are billed and counted
but not transcribed. Sonnet/Opus output lands within ~1% but input and cache
read do not.

Practical consequence for the spec's testing section: **the test that these
entries support is "given `cost-state`'s own `modelUsage` numbers, does my
pricing function return `costUSD`?"** — a pure unit test of the price table,
with no parsing involved. It is *not* a valid end-to-end assertion that the
Desktop's parsed totals equal `totalCostUSD`; they legitimately will not, and
the Desktop's number will be the lower one. Anyone who wires the pipeline's
output straight to `totalCostUSD` and expects a match will chase a bug that
isn't there.

## 7. `service_tier`

**No pricing effect. Ignore the field.**

Source: <https://platform.claude.com/docs/en/api/service-tiers>. The three tiers
are Priority, Standard and Batch. Standard is the default and is list price;
the tier burndown rates in that doc "reflect the relative pricing of each token
type" and are the same 1.25x / 2x / 0.1x multipliers already in §1 — i.e. tier
changes availability and prioritisation, not the rate card.

Two further reasons it cannot matter here:

- **Priority Tier is explicitly unsupported on Claude Opus 5 and Claude
  Sonnet 5** (that doc, § Supported models), which are the only two models in
  our data.
- Empirically, `usage.service_tier` is `"standard"` in **11,585 / 11,593**
  entries; the 8 exceptions are the `<synthetic>` entries, which have no tier
  because there was no API call.

Priority Tier capacity commitments are also no longer purchasable at all.

## 8. Unknown-model fallback — confirmed necessary

The map already decided the behaviour (count tokens, set
`cost_complete: false`, never hard-fail). This ticket's job was to confirm no
stable pricing lookup exists that would make the fallback unnecessary.

**It does not.** The Models API (`GET /v1/models`,
<https://platform.claude.com/docs/en/api/models-list>) returns `id`,
`display_name`, `created_at`, `max_input_tokens`, `max_tokens`, `type` and a
`capabilities` object. There is **no price field of any kind** — no input price,
no output price, no cost-per-token. Pricing lives only in prose on the pricing
page. A hardcoded table plus a graceful fallback is the only correct design.

Corroborating evidence that Anthropic's own tooling does the same thing: the
`cost-state` entries carry a `hasUnknownModelCost` boolean (`false` in all 7),
which is exactly this fallback flag under a different name.

`<synthetic>` (8 entries) has no `service_tier`, no usage worth pricing, and
represents no API call — the map's "genuinely free" ruling is correct.

## 9. Recommended shape for the spec

```python
# USD per million tokens. Prefix match on model id (ids may carry a date suffix).
PRICING = {
    "claude-opus-5":    {"in": 5.00, "out": 25.00, "cw_5m": 6.25, "cw_1h": 10.00, "cr": 0.50},
    "claude-sonnet-5":  {"in": 2.00, "out": 10.00, "cw_5m": 2.50, "cw_1h":  4.00, "cr": 0.20},
    "claude-haiku-4-5": {"in": 1.00, "out":  5.00, "cw_5m": 1.25, "cw_1h":  2.00, "cr": 0.10},
}
WEB_SEARCH_USD_PER_REQUEST = 0.01   # $10 / 1,000 searches
# web_fetch_requests: free, do not price
```

Rules that the evidence above pins down:

1. Match model ids by **prefix** — logged ids may be date-suffixed.
2. Price `ephemeral_1h_input_tokens` and `ephemeral_5m_input_tokens`
   **separately**. Never price `cache_creation_input_tokens`.
3. Do **not** add `thinking_tokens` to the cost — it is inside `output_tokens`.
   Default a missing `output_tokens_details` to 0.
4. Add `web_search_requests * 0.01`. Ignore `web_fetch_requests`.
5. Ignore `service_tier`.
6. `<synthetic>` is free. Any other unmatched id: sum tokens, set
   `cost_complete: false`.

## Open / not determined

- **Whether Claude Code's own accumulator would ever disagree with the API's
  actual billing.** Everything here reconciles against Claude Code's computed
  `costUSD`, which is itself derived from a table. It matches the published rate
  card exactly on 7/7 sessions across three models, so the two agree — but this
  was not checked against a real Anthropic invoice or the Usage & Cost Admin
  API, which would need an Admin API key.
- **Why some sessions are 100% 1h and one is mixed.** The TTL is chosen by the
  client, and the mechanism (session length? a version change? cache-warming
  behaviour?) was not investigated. It does not affect the pricing logic — the
  split is read per entry, not inferred — but it means a fixture built from a
  single pure-1h session would pass a buggy flat-rate implementation. **The
  pytest fixture must include mixed-TTL entries**, or the most likely bug in
  this whole area goes undetected.
- **Long-context surcharge.** The docs state the 1M window is standard-priced
  for 4.6+ models, and no surcharge appears in the reconciliation. Not
  separately stress-tested, since no request here approached 1M input.
