# Code lookups: code-review-graph

This repo has a code-review-graph (CRG): a Tree-sitter graph of the Python,
bash and JavaScript under `desktop/`, `pi/` and the installers, served by the
`code-review-graph` MCP server in `.mcp.json`. The tool table and the "verify
in the source" rules are in the CRG block at the end of `CLAUDE.md`; this file
says when the engineering skills must use it.

## Every code lookup starts with the graph

Find the symbol, its callers, callees, tests and impact through CRG, then read
the files it points to:

- **Where is X / what does X do**: `semantic_search_nodes_tool`, then
  `query_graph_tool` (`callers_of`, `callees_of`, `imports_of`).
- **What breaks if X changes**: `get_impact_radius_tool`,
  `get_affected_flows_tool`.
- **What tests cover X**: `query_graph_tool` with `tests_for`.
- **How is this area laid out**: `get_architecture_overview_tool`,
  `list_communities_tool`.

Plain Grep or whole-file reads come first only when the graph can't answer:
non-code files (the specs and verification runs in `docs/`, `CONTEXT.md`,
`docs/adr/`, `handoff/handoff.md`), string literals such as Payload keys or
log lines, and anything outside the indexed languages. An empty graph result
can mean "not indexed", so fall back to Grep before concluding something does
not exist.

## Before every code review

This covers `/code-review`, `mattpocock-skills:code-review`, any other review
skill and any review subagent: update the graph first with
`build_or_update_graph_tool`. Pass `full_rebuild=true` when there are
uncommitted or untracked files, because the incremental update diffs against
git and misses them. Then start from `detect_changes_tool` and
`get_review_context_tool`.

## Grilling, specs and planning

When a question in grilling, domain modeling or an issue spec touches existing
code, check what exists, who calls it and what a change would affect
(`get_impact_radius_tool`, `tests_for`) before asking the maintainer or writing
scope into the spec.

## Keeping it fresh

`.claude/settings.json` runs `code-review-graph update` after every Edit/Write
and reports status at session start. Those hooks skip silently when the
`code-review-graph` CLI is not on `PATH`, so a stale graph is possible on a
fresh machine: run `build_or_update_graph_tool` if results look out of date.
