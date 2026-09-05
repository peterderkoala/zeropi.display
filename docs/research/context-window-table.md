# Research: per-model context-window size for the context-percentage gauge

Resolves [#31](https://github.com/peterderkoala/zeropi.display/issues/31) (child of
[map #13](https://github.com/peterderkoala/zeropi.display/issues/13)). Settled by
[#24](https://github.com/peterderkoala/zeropi.display/issues/24): the context-size
readout is `tokens used / context window` against a hardcoded per-model table,
mirroring the pricing table built for [#14](https://github.com/peterderkoala/zeropi.display/issues/14)
(`docs/research/pricing-table.md`, branch `research/pricing-table`).

Researched 2026-09-05. Primary sources: the Anthropic models-overview,
context-windows, pricing, and Models API reference docs (linked inline).

**Headline: the commonly-assumed 200K window is wrong for two of the three
models in scope.** `claude-opus-5` and `claude-sonnet-5` both have a
**1,000,000-token** context window as of the 4.6+ generation, and it is now
**standard, not beta** — no opt-in header, no long-context pricing surcharge.
Only `claude-haiku-4-5` is still 200K. The session measured at 210,641 tokens
in #24 was on a model with a 1M window all along; it was never close to the
real limit, only past the stale 200K assumption.

---

## 1. The context-window table

Source: <https://platform.claude.com/docs/en/models/overview> (the models
comparison table — the doc that `about-claude/models` now redirects to) and
<https://platform.claude.com/docs/en/build-with-claude/context-windows>.

| Model id | Standard context window | Max output tokens | Extended/beta context window | Notes |
|---|---|---|---|---|
| `claude-opus-5` | **1,000,000 tokens** (combined input+output; see §2) | 128,000 tokens (sync Messages API); 300,000 with the `output-300k-2026-03-24` beta on the **Batch API only** | None — 1M *is* the standard window, GA, no beta header | Context window and output cap sourced from the models-overview comparison table |
| `claude-sonnet-5` | **1,000,000 tokens** (combined input+output; see §2) | 128,000 tokens (sync Messages API); 300,000 with `output-300k-2026-03-24` on Batch API | None — 1M is standard, GA, no beta header | Same table |
| `claude-haiku-4-5` | **200,000 tokens** | 64,000 tokens | **Not supported.** Haiku 4.5 is not in the list of 1M-context models on the context-windows doc | Listed model ID in the comparison table is date-suffixed, `claude-haiku-4-5-20251001`; the dateless `claude-haiku-4-5` is the Claude API alias — matches the pricing table's finding that Haiku 4.5's logged id needs prefix matching |

## 2. Is the window combined input+output, or input-only with separate max output?

**Combined.** The context-windows doc is explicit: "The context window
capacity (up to 1M tokens, depending on the model) holds the conversation
history plus the new output Claude generates" — and separately, "A single
request to any of them can generate up to 128k output tokens (`max_tokens`)."
So for `claude-opus-5` / `claude-sonnet-5`: the 1M figure is the ceiling on
(system + tools + messages + thinking + output) combined, and 128K is a
*sub-limit* on the output portion of that same window, not a separate window
stacked on top. Source: same context-windows doc, "How the context window
works" and "Context window sizes by model" sections.

This matters for the gauge's arithmetic in #24: the percentage should be
computed as `(input + cache_creation + cache_read + output) / context_window`,
not input alone — thinking tokens are billed as output and already inside
`output_tokens` (confirmed independently by the pricing research in #14 §3),
so they should not be added a second time.

## 3. The "1M context beta" — now GA, not beta, no pricing delta

This is the most consequential finding versus the ticket's framing. The
1M-token window used to be gated behind an opt-in beta header
(`context-1m-2025-08-07`, still listed as a valid `anthropic-beta` value in
the [Models API reference](https://platform.claude.com/docs/en/api/models-list)'s
header enum) for older models. **That is no longer how it works for the
current generation:**

> "Claude Fable 5.1, Claude Mythos 5.1, Claude Fable 5, Claude Mythos 5,
> Claude Opus 5, Claude Opus 4.8, Claude Opus 4.7, Claude Opus 4.6, Claude
> Sonnet 5, Claude Sonnet 4.6, and Claude Mythos Preview have a 1M-token
> context window. [...] Other Claude models, including Claude Sonnet 4.5,
> have a 200k-token context window."
>
> "For every model with a 1M-token context window, **1M is the default: you
> don't need a beta header**, and long-context requests are billed at
> standard pricing."

— <https://platform.claude.com/docs/en/build-with-claude/context-windows>
§ "Context window sizes by model"

Pricing confirms no surcharge:

> "Claude 4.6 and later models and Claude Mythos Preview include the full 1M
> token context window at standard pricing. (A 900k-token request is billed
> at the same per-token rate as a 9k-token request.) Prompt caching and batch
> processing discounts apply at standard rates across the full context
> window."

— <https://platform.claude.com/docs/en/about-claude/pricing> § "Long context
pricing"

So for `claude-opus-5` and `claude-sonnet-5`:
- **GA, not beta.**
- **No opt-in mechanism needed** — it's the model's only/default context size, not a variant you switch into.
- **No usage-tier gating documented** for the window size itself (usage tiers gate *rate limits*, not context-window size — see <https://platform.claude.com/docs/en/api/rate-limits>, not fetched in this pass since it's out of scope for #31).
- **No pricing delta** — same $5/$25 (Opus 5) and $2/$10 (Sonnet 5) per-MTok rates in the model-pricing table apply whether the request is 9K or 900K tokens.

`claude-haiku-4-5` has **no extended/1M variant at all** — it is not in the
1M-context model list on either the models-overview or context-windows docs,
and there is no beta header or opt-in that raises it beyond 200K.

**This resolves the pricing-table's own open question.** `docs/research/pricing-table.md`
§ "Open / not determined" flagged: *"Long-context surcharge. The docs state the
1M window is standard-priced for 4.6+ models, and no surcharge appears in the
reconciliation. Not separately stress-tested, since no request here approached
1M input."* This research confirms that note was correct and there is nothing
for #14 to add — the pricing table's flat per-model rates are already right at
any context size up to 1M, for both models that have a 1M window.

## 4. Max output tokens (detail)

Source: <https://platform.claude.com/docs/en/models/overview> comparison
table, "Max output" row, footnoted:

> "Max output: Synchronous Messages API limit. On the Message Batches API,
> Claude Opus 5, Claude Sonnet 5, Claude Opus 4.8, Claude Opus 4.7, Claude
> Opus 4.6, and Claude Sonnet 4.6 support up to 300k output tokens with the
> `output-300k-2026-03-24` beta header."

| Model id | Sync max output | Batch API max output (beta) |
|---|---|---|
| `claude-opus-5` | 128,000 | 300,000 (`output-300k-2026-03-24`) |
| `claude-sonnet-5` | 128,000 | 300,000 (`output-300k-2026-03-24`) |
| `claude-haiku-4-5` | 64,000 | not listed as supporting the beta |

Not relevant to the gauge itself (which cares about the context-window
denominator, not the output ceiling), but included since the issue asked for
it explicitly and to avoid conflating "max output" with "context window" —
they're two different numbers on two different axes, as the context-windows
doc's combined-window framing in §2 makes clear.

## 5. Models API fields (for a live/self-updating alternative)

Source: <https://platform.claude.com/docs/en/api/models-list>. `GET
/v1/models` and `GET /v1/models/{id}` return, per model:

- `max_input_tokens` — "Maximum input context window size in tokens for this model."
- `max_tokens` — "Maximum value for the `max_tokens` parameter when using this model."
- a `capabilities` object (thinking types, effort levels, batch/citations/code-execution support, etc.) — no context-window field there, it's top-level.

No pricing field, confirming the pricing-table's finding (#14 §8) that
pricing must stay a hardcoded table; but **context window is a per-model
static value that could in principle be fetched live via this endpoint
instead of hardcoded**, if the desktop wants to avoid staleness the way the
pricing table cannot. The map (#24) already decided hardcoded-table to
mirror pricing, so this is noted as an option, not a recommendation to
deviate.

The reference doc's example response for `claude-opus-5` shows
`"max_input_tokens": 0, "max_tokens": 0` — these are clearly placeholder/schema
example values, not real figures, and should not be read as data.

## 6. Recommended shape for the spec

```python
# Tokens. Prefix match on model id (ids may carry a date suffix, e.g. claude-haiku-4-5-20251001).
CONTEXT_WINDOW = {
    "claude-opus-5":    1_000_000,
    "claude-sonnet-5":  1_000_000,
    "claude-haiku-4-5":   200_000,
}
```

Rules the evidence above pins down:

1. Match model ids by **prefix**, same as the pricing table (#14 rule 1) —
   Haiku 4.5's logged id is date-suffixed.
2. The percentage numerator is `input + cache_creation + cache_read + output`
   (all four token classes already carried in `usage`), against the
   denominator above. Do not add `thinking_tokens` separately — it is already
   inside `output_tokens` (per #14 §3, confirmed by the context-windows doc's
   "billed as output tokens once" language in §2 above).
3. No pricing or context-window surcharge applies at any size up to the
   window limit for `claude-opus-5` / `claude-sonnet-5` — the percentage
   calculation and the pricing calculation are independent; neither needs to
   branch on token count.
4. `claude-haiku-4-5` has no long-context variant; 200,000 is its ceiling,
   full stop.

## Sources

- <https://platform.claude.com/docs/en/models/overview> — model comparison table (context window, max output, model IDs)
- <https://platform.claude.com/docs/en/build-with-claude/context-windows> — context window mechanics, 1M-model list, GA/no-beta-header statement, combined input+output framing
- <https://platform.claude.com/docs/en/about-claude/pricing> — model pricing table, "Long context pricing" section (no surcharge)
- <https://platform.claude.com/docs/en/api/models-list> — Models API reference: `max_input_tokens`, `max_tokens`, `capabilities` fields; beta-header enum (shows `context-1m-2025-08-07` still exists as a legacy value)
- `docs/research/pricing-table.md` (branch `research/pricing-table`) — prior research this document mirrors and cross-references (prefix matching, thinking-tokens-are-output finding, the long-context open question this resolves)

## Open / not determined

- **Whether `context-1m-2025-08-07` still does anything.** It remains a valid
  `anthropic-beta` header value per the Models API reference's enum, but the
  context-windows doc says the 4.6+ models (including both models in scope
  here) get 1M by default with **no beta header required**. Whether that beta
  flag is a no-op on current models, still required for some older model not
  in our three-model scope (e.g. Sonnet 4 / Opus 4.1), or purely vestigial
  was not directly tested — not needed for this ticket since neither
  `claude-opus-5` nor `claude-sonnet-5` requires it, and `claude-haiku-4-5`
  cannot reach 1M at all regardless of headers.
- **Usage-tier gating.** The rate-limits doc (not fetched in this pass — out
  of scope for a context-window ticket) was not checked to confirm that
  context-window *size* (as opposed to requests/tokens-per-minute) is truly
  unaffected by usage tier. The context-windows and pricing docs both frame
  1M as unconditional for 4.6+ models with no tier caveat, so this is treated
  as settled, but it wasn't cross-checked against the rate-limits page
  directly.
- **Live discovery via the Models API as an alternative to the hardcoded
  table.** §5 notes `max_input_tokens` is available per-model live; this
  ticket does not evaluate whether the desktop should call it instead of
  hardcoding, since #24 already settled on a hardcoded table mirroring the
  pricing approach. Flagged only as a design option, not a finding.
