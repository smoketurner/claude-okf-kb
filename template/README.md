---
type: doc
title: {{KB_NAME}}
description: Knowledge base distilled from AI coding sessions building {{TITLE}}.
version: v1
content_sha256: 0000000000000000000000000000000000000000000000000000000000000000
tags: [cross]
generated: { by: process:kb-init, at: {{DATE}}T00:00:00Z }
---

# {{KB_NAME}}

Durable knowledge from building {{TITLE}} — decisions and their rationale, dead-ends,
conventions, gotchas, runbooks, and integration quirks mined from Claude Code session transcripts
across these repositories:

{{REPO_PATHS}}

- Format: [Open Knowledge Format v0.2](https://github.com/GoogleCloudPlatform/open-knowledge-format/blob/main/SPEC.md) —
  markdown concepts with YAML frontmatter, one file per concept.
- Start here: [index.md](index.md) (bundle map) → per-directory `index.md` listings.
- Contributing (human or agent): [AGENTS.md](AGENTS.md) — frontmatter contract, versioning, and
  the compare-and-set update protocol.
- Refreshing: run `/kb-refresh` in Claude Code (scripts described in
  [scripts/README.md](scripts/README.md)).
- Tooling: `uv run scripts/okf.py {stamp,validate,index}`; hooks via `prek install`.

Scaffolded from [claude-okf-kb](https://github.com/smoketurner/claude-okf-kb).
