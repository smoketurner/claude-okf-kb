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
`description`, `version` (`^v[1-9][0-9]*$`), `content_sha256`, `tags`, `generated`
(`{ by: <actor>, at: <ISO 8601 datetime> }`, the OKF v0.2 record of who last changed the
content and when), and — for concepts — a `sources` provenance list whose every entry has a
`resource`. `index.md` and `log.md` carry no frontmatter at all (the root `index.md` holds only
`okf_version`). Optional: `status: deprecated` plus a `supersedes` bundle-relative link on the
replacement, and `verified` entries (`{ by: <actor>, at: ... }`) when a human or process has
re-confirmed the content. `version`, `content_sha256`, `supersedes`, and the `project` key on a
source are this bundle's extensions to the spec; everything else is OKF v0.2.

Actors follow the spec's convention: `human:<id>` for a person, `process:<id>` for a script, and
`<producer>/<version>` for an agent (for example `kb-refresh/claude-fable-5-1`).

- `content_sha256` covers the **body only**: the UTF-8 bytes after the closing `---` line,
  normalized with trailing whitespace stripped from every line and exactly one trailing newline
  (the same output the whitespace pre-commit hooks produce). Never compute or edit it by hand —
  run `uv run scripts/okf.py stamp <file>`, which also writes the body in normalized form.
- `version` bumps by exactly one on **any** change, frontmatter-only edits included, and
  `generated.at` moves to the time of that change.
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
decision's *rule* separate from the snapshot it was applied to.

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
  `supersedes`. Other types may be deleted with a plain git commit.
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

The refresh loop also mines settled `.local/` artifacts from the source repos — top-level
`*.md` and `plan{,s}/*.md` only, never `handoff/` — under `local:<repo>` watermark keys. A fact
mined from an artifact records its provenance as
`{resource: "<repo>/.local/<relpath>", last_modified: ..., project: ...}` instead of a
`session:<uuid>` resource.

It also mines each source repo's Claude memory files (`~/.claude/projects/<project>/memory/*.md`,
excluding the `MEMORY.md` index) under `memory:<repo>` watermark keys. Memory files are rewritten
in place, so one is re-mined whenever its mtime passes the watermark; reconcile it against the
concept that already cites it rather than creating a second. A fact mined from one records
`{resource: "memory:<file>.md", last_modified: ..., project: ...}`.

Two more state files live beside the watermarks, both merged by `finalize` so they land only when
the refresh PR merges, and both deliberately outside concept frontmatter so reads and reviews never
cause version churn: `.refresh/usage.json` (cumulative per-concept read counts mined from session
tool calls) and `.refresh/review.json` (last-reviewed dates). Each refresh run also reviews the
least-recently-verified concepts against the live source: stale claims are corrected via CAS,
reversed decisions are marked `status: deprecated`, and obsolete concepts of other types are
deleted by `finalize`.
