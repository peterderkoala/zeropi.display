# Deduplicating usage entries requires a winner rank, not just a key

The Desktop's Claude Code logs contain each usage record many times over —
4,328 of 5,708 `(requestId, message.id)` pairs appear more than once. The
cause is streaming content-block fan-out: one response with thinking, text
and three tool calls is written as five consecutive JSONL lines, each
restating the full usage.

The obvious implementation — key on `(requestId, message.id)`, keep the first
copy seen — is **wrong, and wrong quietly**. Copies are written *as the
response streams*, so early copies carry a provisional usage snapshot
(`output_tokens: 5` where the final says `114`). First-wins loses **26.2% of
all output tokens**. It is nearly invisible in the token total, because
output is 0.44% of tokens, and very visible in cost. So the key is only half
the rule: within each duplicate group the **final** copy must be selected, by
rank — prefer non-sidechain, then higher token total, then a non-null
`speed`.

Sidechain (sub-agent) entries are **included**, not filtered. They are ~18%
of real spend and they do not double-count: 1 overlapping key out of 5,801.

## Consequences

This is a one-way door. The rank runs at ingest into the Desktop store and
only the winner is retained, so a later change to the rank rule applies to
newly-ingested entries only — stored history cannot be re-ranked.

Do not "validate" the pipeline by asserting its total equals the logs'
`cost-state` accumulator. Transcript-derived cost is ~92.8% of it, always
under and never over, because the transcript does not contain every call the
accumulator saw. That gap is expected, and is itself evidence the rule is not
over-counting; chasing it is a phantom bug hunt.

## Considered Options

- **`ccusage`'s key**, `(message.id, requestId, sessionId)`, which defends
  against gateway id collisions. Rejected: here it splits the 17 legitimate
  session-resume keys and over-counts by 946,816 tokens. Its three-step
  winner-selection rank is adopted; its key is not.
