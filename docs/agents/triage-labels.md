# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the actual label strings used in this repo's issue tracker.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from this table.

Edit the right-hand column to match whatever vocabulary you actually use.

## Repo note

Of these, only `wontfix` exists on the repo today (it is one of GitHub's stock labels, and means the same thing here). The other four have not been created yet — `triage` will need to create them on first use, e.g. `gh label create needs-triage --color <hex> --description "..."`.

These are separate from the `wayfinder:*` labels (`wayfinder:map`, `wayfinder:task`, `wayfinder:grilling`), which mark a ticket's role on a wayfinder map rather than its triage state. See the Wayfinding operations section of `issue-tracker.md`.
