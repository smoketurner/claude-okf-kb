# claude-okf-kb

A template repository and Claude Code plugin for building a knowledge base out of your own
Claude Code sessions. It mines the transcripts under `~/.claude/projects` for one or more source
repositories, distills durable facts (decisions and rationale, dead ends, conventions, gotchas,
runbooks, integration quirks, tool-use patterns) into an
[Open Knowledge Format v0.1](https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf)
bundle of markdown concepts, and keeps that bundle current with a self-improving refresh loop
that proposes every change as a pull request.

## What you get

- **`/kb-init`** scaffolds a knowledge base: the eight concept directories, `AGENTS.md` with the
  frontmatter contract and compare-and-set update protocol, `kb.config.json` naming the source
  repos, Claude Code settings, and pre-commit hooks.
- **`/kb-refresh`** mines sessions newer than the recorded watermarks (including subagent
  transcripts, settled `.local/` artifacts, and per-project memory files), redacts secrets with
  gitleaks, reconciles facts against existing concepts, reviews the least-recently-verified
  concepts for staleness, reflects on its own inclusion criteria, and opens a PR.
- **`scripts/`** — the deterministic half: `okf.py` (stamp, validate, index), `refresh.py`
  (prepare, finalize), `digest.sh`, and `refresh-propose.sh`. See
  [scripts/README.md](scripts/README.md).

Requirements: `uv`, `jq`, `gitleaks`, `git`, `gh`, and Claude Code. `prek` (or `pre-commit`)
for the hooks.

## Use as a plugin

```sh
claude plugin marketplace add smoketurner/claude-okf-kb
claude plugin install okf-kb@smoketurner
```

Then, inside an empty git repository that will hold the knowledge base:

```
/okf-kb:kb-init
```

The skill asks for a project name and the source repositories to mine, runs the scaffolder, and
copies `scripts/` and the `/kb-refresh` skill into the repo, so the knowledge base is
self-contained afterwards and works for collaborators without the plugin.

## Use as a template

Click **Use this template** on GitHub, clone the new repo, open Claude Code in it, and run
`/kb-init`. The scaffolder fills in the project placeholders and removes the template-only files
(`template/`, `.claude-plugin/`, the init skill, `scripts/init.py`) so what remains is a plain
knowledge-base repository.

## After init

1. `/kb-refresh` for the first mining pass. With empty watermarks it digests every session for the
   configured repos (bounded by the optional `--since` history floor), so the first run is the
   long one.
2. Review and merge the PR it opens. Re-run `/kb-refresh` whenever you want newer sessions folded in.

## Layout of this repository

```
.claude-plugin/     plugin + marketplace manifests
.claude/skills/     kb-init, kb-refresh
scripts/            okf.py, refresh.py, digest.sh, refresh-propose.sh, init.py
template/           bundle skeleton copied into a new knowledge base
```

## kb.config.json

| key | meaning |
| --- | --- |
| `name`, `title` | project slug and display name |
| `source_repos` | absolute paths of the checkouts to mine: every session transcript recorded for the repo, its worktrees, and its subdirectories (subagent transcripts included), its `.local/*.md` and `.local/plan{,s}/*.md` artifacts, and its Claude memory files |
| `run_dir` | scratch directory for digests (default `/private/tmp/<name>-kb-refresh`) |
| `mine_since` | ISO timestamp floor for transcripts with no watermark yet |
| `base_branch` | branch refresh PRs target (default `main`) |
| `review_slice` | how many existing concepts each refresh re-verifies (default 10) |
