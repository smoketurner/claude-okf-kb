# Scripts

The deterministic half of the knowledge-base loop. The `/kb-refresh` skill drives them; every
command runs from the knowledge-base repo root and reads `kb.config.json` for the project's
source repositories and scratch directory. Requirements: `uv`, `jq`, `gitleaks`, `git`, and either `gh` (logged in) or `curl` with `GITHUB_TOKEN`/`GH_TOKEN` set for opening the PR.

| script | role |
| --- | --- |
| `okf.py` | `stamp`, `validate`, `index` — the bundle contract enforcer (also the pre-commit hook) |
| `refresh.py` | `prepare` digests new transcripts (subagent transcripts included), `.local/` artifacts, and memory files into a run dir; `finalize` promotes watermarks and bookkeeping |
| `digest.sh` | one transcript (or subagent transcript) → mineable markdown digest |
| `refresh-propose.sh` | commit the working tree on a branch and open the PR (or commit bookkeeping-only runs straight to the base branch) |
