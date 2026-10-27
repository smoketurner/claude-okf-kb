#!/usr/bin/env bash
set -euo pipefail

# Extract a mineable digest from a Claude Code session transcript.
#
# Usage: digest.sh <session.jsonl>
#
# Emits a markdown digest on stdout: a header block (session id, date range,
# git branches, slug) followed by USER / ASSISTANT / SUMMARY sections in
# transcript order. Drops sidechains (subagent traffic), tool results,
# slash-command wrappers, system reminders, and harness bookkeeping lines.
# Assistant messages are capped at 4000 characters.
#
# A subagent transcript (`<session>/subagents/agent-*.jsonl`, with a sibling
# `.meta.json`) is every bit sidechain, so for those the sidechain filter is
# off and the header names the parent session and the agent's task.

f="$1"
session_id=$(basename "$f" .jsonl)
keep_sidechain=false
agent_line=""
meta="${f%.jsonl}.meta.json"
if [[ "$session_id" == agent-* && -f "$meta" ]]; then
  keep_sidechain=true
  parent=$(basename "$(dirname "$(dirname "$f")")")
  agent_line=$(jq -r --arg parent "$parent" \
    '"parent: \($parent) | agent: \(.customAgentType // .agentType // "?") | task: \(.description // "?")"' \
    "$meta")
fi

# Single pass, no `jq | head` pipes: under pipefail, head exiting early kills
# jq with SIGPIPE and fails the whole script.
meta_tsv=$(jq -rn '
  reduce inputs as $l ({first: null, last: null, slug: null, branches: []};
    {first: (.first // $l.timestamp),
     last: ($l.timestamp // .last),
     slug: (.slug // $l.slug),
     branches: (.branches + (if ($l.gitBranch // "") != "" then [$l.gitBranch] else [] end))})
  | [(.first // "?"), (.last // "?"), (.slug // "none"),
     (.branches | unique | join(",") | if . == "" then "none" else . end)]
  | @tsv' "$f")
IFS=$'\t' read -r first_ts last_ts slug branches <<<"$meta_tsv"

printf '# SESSION %s\n' "$session_id"
printf 'dates: %s .. %s | slug: %s | branches: %s\n' \
  "${first_ts:-?}" "${last_ts:-?}" "${slug:-none}" "${branches:-none}"
if [[ -n "$agent_line" ]]; then printf '%s\n' "$agent_line"; fi
printf '\n'

jq -r --argjson keep "$keep_sidechain" '
  select($keep or .isSidechain != true)
  | if .type == "summary" then "## SUMMARY\n\(.summary)\n"
    elif .type == "user" then
      ( .message.content
        | if type == "string" then .
          elif type == "array" then ([.[]? | select(.type == "text") | .text] | join("\n"))
          else "" end
        | select(length > 0)
        | select(test("^<(command-name|local-command|system-reminder|task-notification)") | not)
        | select(test("^\\[Request interrupted") | not)
        | "## USER\n\(.)\n" )
    elif .type == "assistant" then
      ( [.message.content[]? | select(.type == "text") | .text] | join("\n")
        | select(length > 0)
        | .[0:4000]
        | "## ASSISTANT\n\(.)\n" )
    else empty end' "$f"
