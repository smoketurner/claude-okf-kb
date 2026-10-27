#!/usr/bin/env bash
set -euo pipefail

# Open a pull request proposing the knowledge-base changes currently sitting in
# the working tree (applied by the /kb-refresh skill but not committed).
#
# Branches from the current HEAD, commits every change (concept edits plus the
# watermark/log advances, so they land atomically only on merge), pushes the
# branch, and opens a PR for human review. Run in plain shell so the git/gh
# calls are not subject to the interactive no-push-to-main guard. No-op when the
# working tree is clean.
#
# Reads kb.config.json: run_dir and base_branch.

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
[ -f kb.config.json ] || {
  echo "refresh-propose: kb.config.json missing; run /kb-init first" >&2
  exit 1
}

NAME="$(jq -r '.name' kb.config.json)"
RUN_DIR="$(jq -r --arg d "/private/tmp/${NAME}-kb-refresh" '.run_dir // $d' kb.config.json)/current"
BASE="$(jq -r '.base_branch // "main"' kb.config.json)"
if [ -z "$(git status --porcelain)" ]; then
  echo "refresh-propose: working tree clean; nothing to propose"
  exit 0
fi

# A run that processed sessions but changed no concepts leaves only the watermark
# and log advances. There is nothing to review, so commit that bookkeeping straight
# to the base branch rather than opening a noise PR. Anything else (a concept, an
# index, an AGENTS.md reflection edit) is real content and goes through a PR.
content="$(git status --porcelain | cut -c4- | grep -vE '^(\.refresh/|log\.md$)' || true)"
if [ -z "$content" ]; then
  git add -A
  git commit -m "kb(refresh): watermark/log bookkeeping — no concept changes"
  echo "refresh-propose: bookkeeping only; committed to ${BASE}, no PR"
  exit 0
fi

stamp="$(date +%Y%m%d-%H%M%S)"
branch="kb/refresh-${stamp}"
summary="$(grep -E '^- [0-9]{4}-[0-9]{2}-[0-9]{2} — Refresh' log.md | tail -1 | sed 's/^- //')"
[ -n "$summary" ] || summary="Knowledge-base refresh ${stamp}"

git checkout -b "$branch"
git add -A

body="$(mktemp)"
trap 'rm -f "$body"' EXIT
{
  echo "Proposed by the automated \`/kb-refresh\` loop for review."
  echo
  echo "**Summary:** ${summary}"
  if [ -f "${RUN_DIR}/proposal-summary.md" ]; then
    echo
    cat "${RUN_DIR}/proposal-summary.md"
  fi
  echo
  echo "### Changed files"
  echo '```'
  git diff --cached --stat
  echo '```'
  echo
  echo "### Review checklist"
  echo "- [ ] New/updated concepts are accurate against the current source"
  echo "- [ ] No stale claim presented as current (recency wins on contradictions)"
  echo "- [ ] Tool-use patterns captured as gotchas/conventions are real and durable"
  echo "- [ ] No secrets or credential values in any diff"
  echo "- [ ] Reviewed-slice outcomes are right: updates accurate, deletions/supersedes justified"
} >"$body"

git commit -F "$body"
git push -u origin "$branch"

if gh pr create --base "$BASE" --head "$branch" --title "KB refresh ${stamp}: ${summary}" --body-file "$body"; then
  echo "refresh-propose: PR opened for ${branch}"
else
  echo "refresh-propose: branch pushed but 'gh pr create' failed; open the PR manually for ${branch}" >&2
fi

git checkout "$BASE"
