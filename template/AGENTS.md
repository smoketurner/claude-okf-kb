---
type: doc
title: Agent protocol for {{KB_NAME}}
description: How agents read, create, and update concepts in this knowledge base, and the
  inclusion bar a fact must clear to earn one.
version: v1
content_sha256: 0000000000000000000000000000000000000000000000000000000000000000
tags: [cross]
generated: { by: process:kb-init, at: {{DATE}}T00:00:00Z }
---

# Agent protocol

This repository is an [Open Knowledge Format v0.2](https://github.com/GoogleCloudPlatform/open-knowledge-format/blob/main/SPEC.md)
bundle holding durable knowledge from building {{TITLE}} ({{REPOS}}). Start at
[index.md](/index.md) for the bundle map. Read the directory `index.md` files for progressive
disclosure; open concept files only when relevant.

## Layout

Directory = concept type: `architecture/`, `decisions/`, `conventions/`, `gotchas/`, `runbooks/`,
`integrations/`, `testing/`, `references/`. Filename = kebab-case slug. The first tag of every
concept is its repo scope: {{SCOPES}}. Add a scope here when a new source repository joins
`kb.config.json`, so the list always matches the bundle.

## Frontmatter contract

Every `.md` file except `index.md` and `log.md` carries YAML frontmatter with `type`, `title`,
`description`, `version` (`^v[1-9][0-9]*$`), `content_sha256`, `tags`, `generated`, and — for
concepts — a `sources` provenance list. Optional: `verified`, `status`, `superseded_by`,
`stale_after`. `index.md` and `log.md` carry no frontmatter at all (the root `index.md` holds only
`okf_version`). `version`, `content_sha256`, `superseded_by`, and the `project` and `path` keys on
a source entry are this bundle's extensions to the spec; everything else is OKF v0.2 (§5 and §7).

- `generated: {by, at}` records who produced the current content and when it last meaningfully
  changed (`at` is an ISO 8601 datetime with an explicit offset, e.g. `{{DATE}}T00:00:00Z`).
  Actors follow the spec's convention: `<producer>/<version>` for an agent
  (`kb-refresh/<model-id>` for the refresh loop, `claude-code/<model-id>` for any other agent
  session), `human:<id>` for a person, `process:<id>` for a script. Keep `at` unchanged on a
  frontmatter-only edit; a content change sets it to the edit's date.
- `verified` is a list of `{by, at}` events recording that someone confirmed the concept against
  the live source without changing it. The refresh loop's staleness review appends one per
  concept it checked and found accurate; a `human:` entry makes the concept human-reviewed.
  Verification is independent of `generated.at`.
- `sources` entries are `{resource, path, last_modified, project}`. `resource` is the portable
  descriptor of the source: `session:<uuid>` for a transcript, `memory:<file>.md` for a memory
  file, `<repo>/.local/<relpath>` for an artifact. `path` is where that source lives on the
  machine that mined it (the digest header's `provenance:` line gives it verbatim).
  `last_modified` is the source's own last timestamp and `project` the repo scope. The refresh
  loop greps `resource` values to find the concept that already cites a source, so keep them in
  exactly these forms.
- `status` is `draft`, `stable` (the default when absent) or `deprecated`; a deprecated decision
  names its replacement in `superseded_by` (bundle-relative link).
- `stale_after` (optional, ISO 8601 datetime) marks when a dated claim should be re-checked —
  use it on absence claims ("as of {{DATE}} the CLI supports only X") instead of hoping a reader
  notices the date.

- `content_sha256` covers the **body only**: the UTF-8 bytes after the closing `---` line,
  normalized with trailing whitespace stripped from every line and exactly one trailing newline
  (the same output the whitespace pre-commit hooks produce). Never compute or edit it by hand —
  run `uv run scripts/okf.py stamp <file>`, which also writes the body in normalized form.
- `version` bumps by exactly one on **any** change, frontmatter-only edits included.
- The pair `(version, content_sha256)` is the compare-and-set token for concurrent updates.
- Single-quote every `title`/`description` value when writing a concept, not just ones that
  visibly need it — in a plain YAML scalar, ` #` starts a comment (any "PR #123" reference) and
  `:: ` breaks the mapping, and both fail `stamp`. Conditional quoting keeps failing in practice.

## Inclusion bar

A fact earns a concept only if it survives this bar. **Include:** decisions and their rationale,
including rejected alternatives; dead ends — approaches tried that failed, and why; user corrections
of an agent's approach or assumption; gotchas that took more than one exchange to resolve;
operational and environment knowledge (tooling, CI, local setup, where credentials live — never
their values); external API and spec quirks; **tool-use patterns** visible only across sessions — a
tool that errors repeatedly, a recurring misconfiguration, a workflow that consistently wastes
turns; and **guidance recorded here that failed in practice** — when a session shows a concept's
own advice or current-state claim to be wrong, correct it and say why the old version looked right,
so it is not re-derived.

**Exclude:** anything reconstructible by reading the current code; one-off bug fixes with no durable
lesson; restatements of what a file contains; trivia. A high error rate with no identified cause is
not a fact. Verify a claimed cause against the live source before asserting it. **A bare path or
tuned constant is not a fact either** — record *why* a value is what it is and the symbol to grep
for, never the number or the path alone; locators rot silently and then read as current truth. When
a fact does turn on a specific value, state what it protects against and name the constant, so a
reader can re-read the live value instead of trusting a stale copy. **A plan is not a state:** facts
mined from planning or design sessions must be verified against the live source before being
recorded as current-state claims, or phrased as intentions. **Absence claims expire too:** "the CLI
supports only X" is true until a feature lands, and nothing in the concept flags it. Phrase them as
of a date ("as of {{DATE}}") and point to the symbol or file a reader can check, and keep the
decision's *rule* separate from the snapshot it was applied to. **In-flight state expires fastest:**
"on the draft branch", "waits on <repo>#N", "filed as later-phase work" are true when mined and go
stale as soon as that work lands. Name the issue or PR whose closing ends the state, phrase it as of
a date, and set `stale_after`. **A user rule can outrun the repo's own docs:** when the user changes
a rule in a session or memory file while the source repo's skills, `CLAUDE.md` or agent definitions
still say the old thing, the newer rule wins (recency), and the concept names the stale in-repo
file and the date it was checked, so a reader following that file is warned rather than misled.

## Creating a concept

1. Check the target directory's `index.md` for an existing concept covering the fact. If one
   exists, follow the update protocol instead.
2. Write the file with `version: v1`, then run `uv run scripts/okf.py stamp <file>`.
3. Run `uv run scripts/okf.py index <dir>` and commit the concept and index together.

## Updating a concept (compare-and-set)

1. Read the file. Record `version` and `content_sha256` — this pair is your CAS token.
2. Prepare your updated content locally. Do not write yet.
3. Re-read the file from disk (if a remote exists, `git pull --rebase` first). If
   `(version, content_sha256)` differ from your token, your basis is stale: discard your draft,
   re-read the full content, merge your change into the new content, and return to step 1 with
   the new token.
4. Write the file with `version` = old + 1, an updated `generated` (`by` = you, `at` = now), and
   your `sources` entry appended. Run `uv run scripts/okf.py stamp <file>`.
   **Rewrite superseded claims in place — do not append dated correction layers.** The body
   asserts only the current truth; an appended "since PR X this changed" block below a
   still-standing stale sentence lets readers (and step 4's grep) cite the false claim. Keep
   history only where this document requires it — decision rationale, rejected alternatives, and
   a brief why-the-old-version-looked-right on reversals — as a subordinate note, not the
   structure.
   **Re-read the `title` and `description` against the change you just made and correct them if they
   still assert the old claim.** They are not decoration: step 4 of the refresh loop greps exactly
   these fields to decide whether a fact is already covered, so a body that says one thing under a
   frontmatter that says another misleads the next run *and* anyone citing the concept from the
   manifest without opening it.
5. Commit immediately — one concept per commit, subject `kb(<type>): <slug> vN`. If a push is
   rejected, `git pull --rebase`; a conflict on your file means the CAS failed after all —
   abort the rebase resolution for that file, and restart from step 1.

Git is the final arbiter; the pre-write re-read is only the fast path.

## Rules

- Never resolve a merge conflict inside a concept body by keeping both sides — restart the
  update instead. (A botched resolution breaks the hash and fails validation anyway.)
- Decisions are never deleted. Mark them `status: deprecated` and link the replacement with
  `superseded_by`. Other types may be deleted with a plain git commit.
- Never store credential values, tokens, or key material — reference where they live instead.
- Qualify every PR/issue reference with its repository (`<repo>#N`; external repos as
  `owner/repo#N`). The bundle spans several repos, so a bare `#N` is ambiguous.
- Record mining runs and structural changes in [log.md](/log.md) as a bullet under a
  `## YYYY-MM-DD` heading, newest date first, not routine concept edits.
- `uv run scripts/okf.py validate` must pass before any commit; the prek pre-commit hook
  enforces it, including the version-bump check against git HEAD.

## Refreshing from transcripts

`kb.config.json` names the source repositories whose Claude Code sessions this bundle mines.
`.refresh/watermarks.json` records the last-mined session timestamp per transcript directory
(promoted by `refresh.py finalize`); `log.md` holds only one-bullet run summaries. To fold in newer
sessions: run `uv run scripts/refresh.py prepare`, mine the digests it writes against the current
bundle (concepts are updated via the CAS protocol, not duplicated), then finalize — the
`/kb-refresh` skill walks through the whole loop.

`prepare` digests each session's **subagent transcripts** too (`<session>/subagents/agent-*.jsonl`,
the implementers and reviewers an orchestrator spawned), as separate digests named
`<session>--agent-<id>` whose header names the parent session and the agent's task. A fact mined
from one records the *parent* session's uuid in `sources`; the subagent id is not stable
provenance. The USER turns in a subagent digest are the orchestrator's brief, not the user's words.

The refresh loop also mines settled `.local/` artifacts from the source repos — top-level
`*.md` and `plan{,s}/*.md` only, never `handoff/` — under `local:<repo>` watermark keys. A fact
mined from an artifact records its provenance as
`{resource: "<repo>/.local/<relpath>", path: ..., last_modified: ..., project: ...}` instead of a
`session:<uuid>` resource.

It also mines each source repo's Claude memory files (`~/.claude/projects/<project>/memory/*.md`,
excluding the `MEMORY.md` index) under `memory:<repo>` watermark keys. Memory files are rewritten
in place, so one is re-mined whenever its mtime passes the watermark; reconcile it against the
concept that already cites it rather than creating a second. A fact mined from one records
`{resource: "memory:<file>.md", path: ..., last_modified: ..., project: ...}`.

One more state file lives beside the watermarks, merged by `finalize` so it lands only when the
refresh PR merges, and deliberately outside concept frontmatter so reads never cause version churn:
`.refresh/usage.json` (cumulative per-concept read counts mined from session tool calls — a concept
counts when a call names its path or when a search aimed at the bundle returns it). Each refresh run
also reviews the least-recently-verified concepts (newest `verified[].at` or `generated.at`,
whichever is later) against the live source: accurate concepts get a `verified` event appended via
CAS, stale claims are corrected via CAS, reversed decisions are marked `status: deprecated` with
`superseded_by`, and obsolete concepts of other types are deleted by `finalize`.
