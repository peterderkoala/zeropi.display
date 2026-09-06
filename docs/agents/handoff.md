# Handoff document

The project's continuity document is `handoff/handoff.md`, tracked in git.
It is the source of truth for "where things stand" between sessions — not a
temp file, not a fresh document per session.

**This overrides any generic handoff skill's default of writing to the OS
temp directory.** A temp file is invisible to the next session unless
someone happens to hand it over by hand; that has proven unreliable here.
`handoff/handoff.md` survives because it's committed, `grep`-able, and part
of the repo a fresh agent already reads.

## When to update it

Any time a session changes something a fresh agent would otherwise have to
rediscover or get wrong by trusting a stale line: a ticket resolves or
closes, a map's frontier changes, a fact recorded here turns out to be
wrong, a hard-won gotcha surfaces. Not every commit needs one — routine
implementation work that doesn't change the tracker state or add a
non-obvious fact doesn't need a handoff edit.

## How to update it

**Edit in place; don't append a running log.** Find the section the change
affects (`Where things stand`, `Maps` → the specific map, `Hard-won facts`,
`Suggested skills`) and update it there. Old, reversed decisions get struck
through rather than deleted so the change is visible — that's the one
exception to editing in place.

**Fix the summary and the detail together.** This file has a short
top-of-file/top-of-map summary ("#7 has two tickets left, #34 the only
takeable one") and a longer detail section below it. When a ticket
resolves, both change in the same edit — a summary line that still says
"the only takeable one" about a ticket that's since closed is exactly the
inconsistency this file exists to prevent.

**Commit it with the rest of the ticket's work**, using the repo's existing
message convention: `Handoff: record #N's resolution and <what it changes>`
(see `git log --oneline | grep Handoff` for the pattern). It can be the same
commit as the code change or a separate one — this repo has done both.

## What NOT to duplicate here

Full resolution detail belongs in the issue's resolution comment and the
map's Decisions-so-far, not here. This file links to those and gives the
one- or two-sentence version a fresh agent needs to decide what to read
next — not a second copy of the spec, the ADR, or the code diff.
