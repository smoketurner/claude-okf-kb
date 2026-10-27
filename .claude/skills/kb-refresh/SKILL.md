---
name: kb-refresh
description: Incrementally refresh new Claude Code sessions into this OKF knowledge base and self-improve it. Use when asked to update/refresh the KB from recent sessions, or run the periodic refresh by hand. Reads the recorded watermarks, mines only newer sessions (both their text and their tool-use patterns), applies updates via the compare-and-set protocol, reviews existing concepts for staleness, runs a quality gate, reflects on its own criteria, and proposes the result as a pull request.
---

# Knowledge-base refresh loop

You maintain this knowledge base by folding newly-recorded coding sessions into it. Run from the
knowledge-base repo root (the directory holding `kb.config.json`; if it is missing, stop and tell
the user to run `/kb-init` first). Read `kb.config.json` for the project name and the
`source_repos` whose sessions you mine and whose code is ground truth, then read
[AGENTS.md](/AGENTS.md) — it defines the frontmatter contract, the inclusion bar, and the
compare-and-set (CAS) update protocol you MUST follow.

Work top-to-bottom. Keep the bundle valid at every step (`uv run scripts/okf.py validate`). You apply
changes to the working tree only — you do **not** commit or push; the result is proposed as a PR
(step 9) so a human reviews before anything lands.

## 1. Prepare (deterministic)

Run: `uv run scripts/refresh.py prepare`

Read the printed `manifest.json`. If `new_sessions`, `local_artifacts` and `memory_files` are all
0, there is nothing to do — report that and stop. Otherwise note `run_dir`, `batches`, `kb_manifest`
(current concepts), `tool_report`, `tool_calls`/`tool_errors`, `review` (the existing concepts to
verify in step 6), and `kb_reads` (how many concepts the new sessions read — the per-concept counts
are in the manifest's `usage` file). `prepare` has already digested and **secret-redacted** the new
sessions and artifacts; never try to recover a `[REDACTED]` value.

## 2. Mine facts (fan-out)

For each batch, read every digest listed in its manifest file and extract durable facts, applying the
inclusion bar from AGENTS.md verbatim (decisions+rationale, dead-ends, user corrections, gotchas that
took >1 exchange, operational/env knowledge, external-API/spec quirks; exclude anything reconstructible
from current code, one-off fixes, trivia). Cap ~10 facts per batch; each needs a concrete transcript
quote as evidence. Digests named `<session>--agent-<id>.md` are subagent transcripts: the parent
session holds the user's words, the subagent holds the implementation work, and both earn facts.
Batches may also list `local--*.md` digests — settled artifacts agents wrote to `<repo>/.local/`
(top-level `*.md` and `plan{,s}/`); apply the same inclusion bar, quoting the artifact itself as
evidence. They may also list `memory--*.md` digests — the per-project Claude memory files under
`~/.claude/projects/<project>/memory/`, already distilled, so most of their content clears the bar;
a memory file is re-mined whenever it changes, so first find the concept that already cites
`memory:<file>.md` and fold the change in via CAS. With more than ~4 batches, run these as a
Workflow fan-out; otherwise mine inline.

## 3. Mine tool-use patterns

Read `tool_report` (the cross-session tool-use summary: per-tool call/error counts, a Bash-executable
breakdown, and redacted error snippets). Look for **systematic** patterns a single session can't reveal:
a tool erroring repeatedly across sessions, a recurring misconfiguration (e.g. a command that keeps
being denied or a flag that keeps failing), or a workflow that consistently wastes turns. Capture only
durable, actionable ones as `gotchas/` or `conventions/` concepts (e.g. "tool X needs config Y", "avoid
Z, it fails under W") — verify against the live source before asserting a cause. Ignore one-off errors.
A high error rate with no clear systematic cause is not a fact.

When an error pattern matches an **existing** `gotchas/` concept, do not skip it as "already
documented" — the concept exists but is not preventing the failure. Check its entry in
`.refresh/usage.json`: zero reads means a discoverability problem (rewrite its title/description so
it is findable by the symptoms someone would search for); nonzero reads with the error still
recurring means a content problem (correct or sharpen the advice). Apply as a CAS update and count
it in `recurring_gotchas`.

## 4. Reconcile against the current KB (CAS)

For each candidate fact (from step 2 or 3), decide **new concept** vs **update of an existing one**:
- Search the current bundle first: grep `kb_manifest` titles/descriptions and the concept bodies
  (`rg -i` the key symbols across the type dirs) before concluding a fact is new.
- If an existing concept covers the topic, UPDATE it via the CAS protocol in AGENTS.md: read its
  `(version, content_sha256)`, merge the new knowledge (do not delete still-valid content), append a
  `sources` entry in **block YAML style** —
  `{resource: "session:<uuid>", last_modified: "YYYY-MM-DDT00:00:00Z", project: "<repo>"}` for a
  transcript digest (a `<session>--agent-<id>` digest cites the parent `<session>` uuid),
  `{resource: "<repo>/.local/<relpath>", last_modified: ..., project: "<repo>"}` for a `local--*`
  digest, or `{resource: "memory:<file>.md", last_modified: ..., project: "<repo>"}` for a
  `memory--*` digest — bump `version` by one, set `generated` to
  `{ by: kb-refresh/<your model id>, at: <now, ISO 8601 UTC> }`, then
  `uv run scripts/okf.py stamp <file>`.
- If genuinely new, create it in the right type dir at `version: v1` per AGENTS.md, then stamp. Its
  first tag is the repo scope (the source repo's basename, or `cross`).
- **Recency wins on contradictions**: when a new session contradicts an existing concept's current-state
  claim, the newer session is authoritative — correct the concept (keep decisions, mark
  `status: deprecated` + `supersedes` only when a decision was truly reversed). When unsure whether a
  claim is current, verify against the live checkouts listed in `kb.config.json` `source_repos` —
  the code is ground truth.
- For every concept you create or update, note how many of this run's digests evidenced it — you
  will report that in the proposal summary (step 8).

## 5. Quality gate

Run `uv run scripts/okf.py index <changed dirs>` and `uv run scripts/okf.py validate` (fix everything
it flags). Then check the concepts you created/updated for duplicates (merge or cross-link, don't leave
two), unresolved contradictions, and broken bundle-relative links. Keep changes tight and cross-link
related concepts bidirectionally.

## 6. Review existing concepts (staleness + pruning)

The manifest's `review` list holds the least-recently-verified existing concepts (selected from
`.refresh/review.json` dates, falling back to each concept's own `generated.at`; the slice size is
`review_slice` in `kb.config.json`). For each one, verify its current-state claims against the live
source repos. Outcomes:

- **Still accurate** — touch nothing; just include it in `reviewed` (step 8).
- **Stale claim** — correct it via the CAS protocol (also counts toward `updated`).
- **Duplicate** — merge into the surviving concept, then treat the loser as pruned below.
- **No longer earns its place** — a decision gets `status: deprecated` plus a `supersedes` link
  (decisions are never deleted); any other type is deleted, but do NOT delete files yourself:
  first `rg` the bundle for inbound links and fix them, then add its path to `pruned` in metrics —
  `finalize` performs the removal. Zero usage in `.refresh/usage.json` never justifies deletion by
  itself; treat it as corroboration only once the file's `since` date shows ≥60 days of tracking.

Every reviewed path goes in `reviewed` regardless of outcome. Re-run
`uv run scripts/okf.py index <dir>` for any directory you changed.

## 7. Reflect (self-improvement)

Read the last ~5 refresh run records in [log.md](/log.md) and skim a sample of concepts you touched,
plus the step-6 review outcomes. Ask: is the inclusion bar in AGENTS.md producing trivia or missing a
recurring category (now including tool-use patterns)? Is a type directory overflowing or a taxonomy
line unclear? Are reviewed concepts going stale in a systematic way? If — and only if — you see a
concrete, recurring signal, make one small edit to the inclusion bar or taxonomy in AGENTS.md
(bump its `version`, update `generated`, stamp it) and note it. Do not churn AGENTS.md without a clear signal.

## 8. Metrics, proposal summary + finalize

Write `<run_dir>/metrics.json`: `{"sessions": N, "local_artifacts": N, "memory_files": N,
"created": N, "updated": N,
"contradictions_fixed": N, "tool_patterns": N, "recurring_gotchas": N,
"reviewed": ["<type>/<slug>.md", ...], "pruned": ["<type>/<slug>.md", ...],
"reflection": true|false}`.

Then write `<run_dir>/proposal-summary.md` for the PR body: a `### Proposed concepts` heading, one
line per created/updated concept with its evidence prevalence —
`- created gotchas/foo.md — evidenced by 3 sessions this run` or
`- updated decisions/bar.md (v4 -> v5) — evidenced by 1 session this run, 6 total sources` (total =
length of its `sources` list) — plus one line summarizing step 6, e.g.
`- reviewed 10 concepts: 8 unchanged, 1 updated, 1 pruned`.

Then run `uv run scripts/refresh.py finalize --run-dir <run_dir>` — it advances the watermarks,
deletes the `pruned` files, merges the run's usage into `.refresh/usage.json`, records `reviewed`
dates in `.refresh/review.json`, and adds the run record under today's date in log.md, all **in the working tree**
(these land only when the PR merges). After finalize, re-run `uv run scripts/okf.py index <dir>`
for any directory a pruned file left, and validate must be clean.

## 9. Propose (pull request)

`uv run scripts/okf.py validate` must be clean and the quality gate must have no unresolved
high-severity contradiction. Then **do not commit or push** — hand off to the proposal script, which
branches, commits every change (concepts + watermark/log together), pushes, and opens a PR:

```
bash scripts/refresh-propose.sh
```

If the run processed sessions but created/updated no concepts (only the watermarks, usage/review
records, and log advanced), that is a valid outcome — the proposal script detects the
bookkeeping-only case and commits it straight to the base branch instead of opening a noise PR. Tell
the user whether it opened a PR or just recorded bookkeeping (or, if they'd rather discard, that `git checkout -- . && git clean -fd`
reverts the working tree). If anything blocked (validation failed, an unresolved contradiction,
secrets suspected), do NOT finalize or propose — revert the working tree with
`git checkout -- . && git clean -fd`, report what blocked, and stop so no bad state is proposed.
