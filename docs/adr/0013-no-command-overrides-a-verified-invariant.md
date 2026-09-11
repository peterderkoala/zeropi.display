# No Command may override a verified invariant

Several of this project's constants are **findings, not preferences**: a value
established by a hardware verification run and recorded in an ADR. The redraw
floor is the clearest — the panel is rated for one update per 180 s, 300 s is
the operating point ADR-0008 fixed, and the Pi is the enforcer.

The Management Surface deliberately puts such values out of reach of a settings
form: they are **Tier 3**, displayed read-only beside their ADR and not stored
as Configuration at all. This decision closes the other door.

**A Command may never override a verified invariant. It queues behind one.**
Concretely: `redraw` is accepted, Acked, and drawn when the floor next allows
it, reporting `floor_remaining_s` — it is neither rejected nor granted an
exemption.

## Why not a force flag

A `redraw --now` that bypassed the floor is the natural thing to reach for, and
it reads as harmless: one extra refresh, by an adult, who wanted it.

It is the *enforcement location* that makes it wrong. Today the floor is a
property of the Pi — nothing outside it can cause an over-rate refresh, so the
panel's wear limit holds no matter what any Desktop does. A force flag
relocates that enforcement to whoever types the command, where the failure mode
is invisible and cumulative: a shell loop, a retry wrapper, a watchdog script, a
web UI button someone clicks twelve times because the panel takes four seconds
to respond. None of those look like abuse at the moment they are written, and
the damage is to a panel with a rated lifetime rather than to anything that
throws an error.

The general form of the rule matters more than the `redraw` case, which is why
this is an ADR and not a line in the spec. **Enforcement that a Tier moved out
of reach of a settings form must not be reachable through a verb instead.**
[#72](https://github.com/peterderkoala/zeropi.display/issues/72) made verified
invariants unstorable so a form could not invalidate a hardware run; a verb that
overrode one at runtime would achieve exactly what that ruling prevents,
by a different route. A management surface must not be able to invalidate the
evidence its own numbers rest on.

## Consequences

**`redraw` has a latency the caller did not choose**, up to 300 s. That is
surfaced rather than hidden: the Ack carries `floor_remaining_s`, and the CLI
says *"Queued on the Pi. It will draw in 3m 22s"*, naming ADR-0008 as the
reason. An unexplained delay would be read as a bug; a named one is read as the
system working.

⚠ **This on-Pi latency is not a Desktop queue and must not grow into one.**
[ADR-0011](./0011-management-actions-are-never-deferred.md) refuses every Command
against an Unreachable Pi at the moment it is typed. The two are easy to
conflate: a `redraw` that *reached* the Pi waits there for the floor; a `redraw`
that never reached it is refused, not held.

**This is a membership rule for the verb list, applied before a verb is added
rather than checked afterwards** — the same discipline as natural idempotency. A
proposed verb whose only purpose is to defeat a verified invariant does not join
the vocabulary. That is what keeps a short, closed list from drifting into an
RPC surface, where each new call is individually reasonable and the set is not.

**A verified invariant still changes** — by amending its ADR and re-running the
verification, which is how ADR-0008 and ADR-0010 were both amended on
2026-09-09. The path is deliberately slow and leaves a record. Ranges derived
from such a constant move with it automatically, because the Management
Surface's schema writes them as expressions over the constant rather than as
copied literals.
