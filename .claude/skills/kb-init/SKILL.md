---
name: kb-init
description: Scaffold a new OKF knowledge base in the current repository that mines local Claude Code sessions for one or more source repos. Use when asked to initialize/create/set up a knowledge base, start a new "-kb" repo, or bootstrap this template. Gathers the project name and source repo paths, runs the deterministic scaffolder, verifies the bundle, and explains the first refresh.
---

# Initialize a knowledge base

You turn the current directory into an [Open Knowledge Format](https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf)
bundle that `/kb-refresh` keeps up to date from Claude Code transcripts. The work itself is done by a
deterministic script; your job is to gather correct inputs, run it, and verify the result.

## 1. Locate the scaffolder

The script is `scripts/init.py` under the plugin root when this skill runs from the installed
`okf-kb` plugin: `${CLAUDE_PLUGIN_ROOT}`. If that placeholder was not expanded to a real path (it
appears literally above), this skill is running from a template checkout and the scaffolder is
`./scripts/init.py` in the current directory. Call the resolved path `SOURCE/scripts/init.py`
below.

Refuse to proceed if the current directory already holds `kb.config.json` (it is already a
knowledge base) or is not a git repository (`git rev-parse --show-toplevel`); tell the user to
create or clone the empty target repo first.

## 2. Gather inputs

Ask the user with AskUserQuestion when they have not already said:

- **Project name** (`--name`): a lowercase kebab-case slug such as `vouch`; and the human
  **title** (`--title`), such as `Vouch`. Default the name to the repo directory name minus a
  trailing `-kb`.
- **Source repositories** (`--repo`, repeatable): absolute paths of the checkouts whose sessions
  to mine. Before asking, list candidates: `ls ~/.claude/projects` shows one directory per
  working directory Claude Code has run in, named by the path with `/` and `.` replaced by `-`
  (`-Users-you-envs-git-vouch`); directories containing `--claude-worktrees-` are worktrees of
  the preceding repo and are mined automatically. Offer the directories whose names contain the
  project name, mapped back to their real paths, and confirm each path exists. Only repos listed
  here are mined — a sibling that merely shares the name prefix is not.
- **History floor** (`--since`, optional `YYYY-MM-DD`): sessions older than this are never mined.
  Suggest it when `ls ~/.claude/projects/<dir>/*.jsonl | wc -l` is in the hundreds, because the
  first refresh otherwise digests everything (which is fine, just long).

`--run-dir` (scratch space for digests, default `/private/tmp/<name>-kb-refresh`) and
`--base-branch` (default `main`) rarely need changing.

## 3. Scaffold

Run, from the repo root:

```
uv run SOURCE/scripts/init.py --name <name> --title "<Title>" --repo <path> [--repo <path> ...] [--since YYYY-MM-DD]
```

It copies the bundle skeleton, `scripts/`, and the `/kb-refresh` skill into the repo, writes
`kb.config.json`, `.claude/settings.json` (the out-of-tree directories and command allow-list the
refresh needs), and `.refresh/watermarks.json`, stamps the docs, regenerates the eight type-directory indexes, and runs `okf.py validate`. In a template
checkout it also removes the template-only files (`template/`, `.claude-plugin/`, this skill, the
scaffolder) so what remains is a plain knowledge-base repo.

## 4. Verify and hand off

- `uv run scripts/okf.py validate` is clean and `git status` shows the scaffold.
- `jq . kb.config.json` lists exactly the agreed repos; `.claude/settings.json` grants them.
- Install the hooks: `prek install` (fall back to `pre-commit install` if prek is absent).
- Confirm `jq`, `gitleaks`, and `uv` are on PATH — `refresh.py prepare` needs all three. Opening
  the refresh PR needs either `gh` logged in or `GITHUB_TOKEN`/`GH_TOKEN` in the environment.

Do not commit unless the user asks. Report what was created, then tell the user:

1. Run `/kb-refresh` for the first mining pass (with no watermarks it mines every session after
   the history floor; expect a Workflow fan-out when there are many batches, and a PR at the end).
2. Edit the **Layout** scope list in `AGENTS.md` whenever a source repo is added to
   `kb.config.json` (bump its `version` and re-stamp).
