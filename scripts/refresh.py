# /// script
# requires-python = ">=3.13"
# dependencies = ["pyyaml==6.0.3"]
# ///
"""Deterministic engine for the self-improving KB refresh loop.

`prepare` discovers Claude Code sessions for the source repos named in
`kb.config.json` that are newer than the recorded watermarks — each session's
subagent transcripts (`<session>/subagents/agent-*.jsonl`) included — plus
settled `.local/` artifacts in those repos and the per-project Claude memory
files. It digests them, redacts secrets, and packs mining batches into a run
directory; the agent-driven `/kb-refresh` skill consumes the manifest it writes.
`finalize` promotes the watermarks and appends a run record to log.md after the
skill has applied its changes to the working tree.

    uv run scripts/refresh.py prepare   [--run-dir DIR]
    uv run scripts/refresh.py finalize  --run-dir DIR
"""

import argparse
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

KB = Path(__file__).resolve().parent.parent
CONFIG = KB / "kb.config.json"
DIGEST = KB / "scripts" / "digest.sh"
WATERMARKS = KB / ".refresh" / "watermarks.json"
USAGE = KB / ".refresh" / "usage.json"
REVIEW = KB / ".refresh" / "review.json"
PROJECTS = Path.home() / ".claude" / "projects"
MAX_BATCH_BYTES = 200_000
MAX_BATCH_SESSIONS = 60
DIRS = [
    "architecture",
    "decisions",
    "conventions",
    "gotchas",
    "runbooks",
    "integrations",
    "testing",
    "references",
]
CONCEPT_PATH_RE = re.compile(
    rf"{re.escape(str(KB))}/(?:{'|'.join(DIRS)})/[A-Za-z0-9._-]+\.md"
)
B64_RUN = re.compile(r"[A-Za-z0-9+/=_-]{80,}")
PEM = re.compile(
    r"-----BEGIN [A-Z ]*(?:PRIVATE KEY|CERTIFICATE)-----.*?"
    r"(?:-----END [A-Z ]*(?:PRIVATE KEY|CERTIFICATE)-----|\Z)",
    re.DOTALL,
)


def load_json(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def load_config() -> dict:
    if not CONFIG.exists():
        raise SystemExit(f"refresh: {CONFIG} missing; run /kb-init first")
    cfg = load_json(CONFIG)
    repos = [Path(p).expanduser().resolve() for p in cfg.get("source_repos", [])]
    if not repos:
        raise SystemExit("refresh: kb.config.json has no source_repos")
    cfg["source_repos"] = repos
    cfg["run_dir"] = Path(
        cfg.get("run_dir") or f"/private/tmp/{cfg['name']}-kb-refresh"
    ).expanduser()
    cfg.setdefault("review_slice", 10)
    cfg.setdefault("mine_since", "")
    return cfg


def encode_project(path: Path) -> str:
    """The directory name Claude Code uses under ~/.claude/projects for a cwd."""
    return re.sub(r"[^A-Za-z0-9-]", "-", str(path))


def project_dirs(repos: list[Path]) -> list[tuple[str, str, Path]]:
    """(repo basename, watermark key, project dir) for every transcript dir of a source repo.

    A repo owns its exact project dir plus every dir whose name extends it with `-`:
    `<repo>--claude-worktrees-*` (sessions run in its worktrees) and `<repo>-<subdir>`
    (sessions started in a subdirectory). When two configured repos both match, the
    longer encoded path wins, so `vouch-infra` is not mined twice as a child of `vouch`.
    The knowledge base's own sessions are never mined.
    The watermark key is the repo basename plus whatever suffix the dir name adds.
    """
    encoded = sorted(
        ((encode_project(r), r.name) for r in repos), key=lambda e: -len(e[0])
    )
    own = encode_project(KB)
    claimed: dict[Path, tuple[str, str]] = {}
    for enc, name in encoded:
        for project in PROJECTS.glob(f"{enc}*"):
            if project.name == own or project.name.startswith(f"{own}-"):
                continue
            if project.name == enc or project.name.startswith(f"{enc}-"):
                claimed.setdefault(project, (name, name + project.name[len(enc) :]))
    return sorted((name, key, project) for project, (name, key) in claimed.items())


def session_max_ts(path: Path) -> str:
    """Largest message timestamp in a transcript (chronological, so near the end)."""
    best = ""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        idx = line.find('"timestamp":"')
        if idx != -1:
            ts = line[idx + 13 : idx + 13 + 24]
            best = max(best, ts)
    return best


def discover(cfg: dict, watermarks: dict[str, str]) -> dict[str, list[dict]]:
    """Map watermark key -> {session, path, ts} for transcripts newer than its watermark.

    A session's subagent transcripts are separate entries named
    `<session>--agent-<id>`: the orchestrator transcript holds the user's words, the
    subagents hold the implementation work, and both earn facts.
    """
    new: dict[str, list[dict]] = {}
    for _repo, key, project in project_dirs(cfg["source_repos"]):
        mark = watermarks.get(key, cfg["mine_since"])
        for jsonl in project.glob("*.jsonl"):
            transcripts = [(jsonl.stem, jsonl)]
            transcripts += [
                (f"{jsonl.stem}--{sub.stem}", sub)
                for sub in sorted(
                    (project / jsonl.stem / "subagents").glob("agent-*.jsonl")
                )
                if sub.with_suffix(".meta.json").exists()
            ]
            for name, path in transcripts:
                ts = session_max_ts(path)
                if ts and ts > mark:
                    new.setdefault(key, []).append(
                        {"session": name, "path": str(path), "ts": ts}
                    )
    return new


def artifact_ts(path: Path) -> str:
    """File mtime in the same UTC ISO shape transcript timestamps use."""
    dt = datetime.datetime.fromtimestamp(path.stat().st_mtime, tz=datetime.UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def discover_local(cfg: dict, watermarks: dict[str, str]) -> dict[str, list[dict]]:
    """Map repo basename -> settled .local artifacts newer than the local: watermark.

    Mines only top-level .local/*.md and .local/plan{,s}/*.md — handoff/ and the
    other subdirs are transient inter-agent traffic the transcripts already cover.
    """
    new: dict[str, list[dict]] = {}
    for repo_dir in cfg["source_repos"]:
        local = repo_dir / ".local"
        mark = watermarks.get(f"local:{repo_dir.name}", cfg["mine_since"])
        candidates = [
            *local.glob("*.md"),
            *(local / "plan").glob("*.md"),
            *(local / "plans").glob("*.md"),
        ]
        for md in sorted(candidates):
            ts = artifact_ts(md)
            if ts > mark:
                new.setdefault(repo_dir.name, []).append(
                    {"relpath": str(md.relative_to(local)), "path": str(md), "ts": ts}
                )
    return new


def discover_memory(cfg: dict, watermarks: dict[str, str]) -> dict[str, list[dict]]:
    """Map repo basename -> Claude memory files newer than the memory: watermark.

    Memory files are rewritten in place rather than appended, so a file is re-mined
    whenever its mtime passes the watermark. MEMORY.md is only the index.
    """
    new: dict[str, list[dict]] = {}
    for repo in cfg["source_repos"]:
        project = PROJECTS / encode_project(repo)
        mark = watermarks.get(f"memory:{repo.name}", cfg["mine_since"])
        for md in sorted((project / "memory").glob("*.md")):
            if md.name == "MEMORY.md":
                continue
            ts = artifact_ts(md)
            if ts > mark:
                new.setdefault(repo.name, []).append(
                    {"name": md.name, "path": str(md), "ts": ts}
                )
    return new


def redact_tree(digest_dir: Path) -> int:
    """Run gitleaks over the digests, redact every finding, and hard-fail if any survive."""
    total = 0
    for _ in range(4):
        report = digest_dir.parent / "gitleaks.json"
        proc = subprocess.run(
            [
                "gitleaks",
                "dir",
                str(digest_dir),
                "--no-banner",
                "--report-path",
                str(report),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and not report.exists():
            return total
        findings = (
            json.loads(report.read_text(encoding="utf-8")) if report.exists() else []
        )
        if not findings:
            return total
        by_file: dict[str, set[str]] = {}
        for f in findings:
            secret = f.get("Secret", "")
            if len(secret) >= 8:
                by_file.setdefault(f["File"], set()).add(secret)
        for file_path, secrets in by_file.items():
            text = Path(file_path).read_text(encoding="utf-8")
            for secret in sorted(secrets, key=len, reverse=True):
                text = text.replace(secret, "[REDACTED]")
            text = PEM.sub("[REDACTED:private-key]", text)
            text = B64_RUN.sub("[REDACTED:b64]", text)
            Path(file_path).write_text(text, encoding="utf-8")
            total += len(secrets)
    proc = subprocess.run(
        ["gitleaks", "dir", str(digest_dir), "--no-banner"], check=False
    )
    if proc.returncode != 0:
        raise SystemExit(
            "refresh: gitleaks findings survived redaction; aborting before mining"
        )
    return total


def pack_batches(entries: list[dict], digest_dir: Path) -> list[dict]:
    """Greedy chronological batches within a repo, capped by bytes and session count."""
    batches: list[dict] = []
    cur: list[dict] = []
    cur_bytes = 0
    for e in sorted(entries, key=lambda x: (x["repo"], x["ts"])):
        size = e["digest_bytes"]
        crosses = cur and (
            e["repo"] != cur[0]["repo"]
            or cur_bytes + size > MAX_BATCH_BYTES
            or len(cur) >= MAX_BATCH_SESSIONS
        )
        if crosses:
            batches.append(_flush(cur, len(batches), digest_dir))
            cur, cur_bytes = [], 0
        cur.append(e)
        cur_bytes += size
    if cur:
        batches.append(_flush(cur, len(batches), digest_dir))
    return batches


def _flush(cur: list[dict], index: int, digest_dir: Path) -> dict:
    bid = f"batch-{index:03d}"
    manifest = digest_dir.parent / "batches" / f"{bid}.txt"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        "\n".join(e["digest_path"] for e in cur) + "\n", encoding="utf-8"
    )
    return {
        "id": bid,
        "repo": cur[0]["repo"],
        "sessions": len(cur),
        "bytes": sum(e["digest_bytes"] for e in cur),
        "first": cur[0]["ts"],
        "last": cur[-1]["ts"],
        "manifest": str(manifest),
    }


def write_kb_manifest(dest: Path) -> list[tuple[str, dict]]:
    """Snapshot the current bundle (path/type/title/description) for dedup targets."""
    rows = ["path\ttype\ttitle\tdescription"]
    concepts: list[tuple[str, dict]] = []
    for d in DIRS:
        for path in sorted((KB / d).glob("*.md")):
            if path.name == "index.md":
                continue
            text = path.read_text(encoding="utf-8")
            end = text.find("\n---\n", 4)
            fm = yaml.safe_load(text[4:end]) if end != -1 else {}
            rel = str(path.relative_to(KB))
            rows.append(
                f"{rel}\t{fm.get('type', '')}\t{fm.get('title', '')}\t{fm.get('description', '')}"
            )
            concepts.append((rel, fm))
    dest.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return concepts


def select_review(
    concepts: list[tuple[str, dict]], review_state: dict[str, str], count: int
) -> list[str]:
    """The least-recently-verified concepts, due for a staleness review this run.

    A concept's last-verified date is the later of its recorded review date and its
    own timestamp — a recent CAS update already re-verified it against the source.
    """
    due: list[tuple[str, str]] = []
    for rel, fm in concepts:
        if fm.get("status") == "superseded":
            continue
        ts = fm.get("timestamp")
        ts = ts.isoformat() if isinstance(ts, datetime.datetime) else str(ts or "")
        due.append((max(review_state.get(rel, ""), ts[:10]), rel))
    due.sort()
    return [rel for _, rel in due[:count]]


def _text_of(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
CMD_WRAPPERS = {"sudo", "env", "time", "exec", "command", "nohup", "then", "do"}


def bash_executable(command: str) -> str:
    """The real program a Bash command runs, past env-var prefixes, comments, and wrappers."""
    for raw in command.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        tokens = line.split()
        i = 0
        while i < len(tokens) and (
            ENV_ASSIGN.match(tokens[i]) or tokens[i] in CMD_WRAPPERS
        ):
            i += 1
        return tokens[i] if i < len(tokens) else "?"
    return "?"


def _tool_parts(path: Path) -> list[dict]:
    parts: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if '"tool_use"' not in line and '"tool_result"' not in line:
            continue
        try:
            content = json.loads(line).get("message", {}).get("content")
        except json.JSONDecodeError:
            continue
        if isinstance(content, list):
            parts.extend(
                p
                for p in content
                if isinstance(p, dict) and p.get("type") in {"tool_use", "tool_result"}
            )
    return parts


def extract_kb_reads(parts: list[dict]) -> set[str]:
    """Repo-relative KB concept paths one session read via Read, Grep, or Bash."""
    prefix = len(str(KB)) + 1
    reads: set[str] = set()
    for p in parts:
        if p.get("type") != "tool_use":
            continue
        inp = p.get("input")
        if not isinstance(inp, dict):
            continue
        text = {
            "Read": str(inp.get("file_path", "")),
            "Grep": str(inp.get("path", "")),
            "Bash": str(inp.get("command", "")),
        }.get(p.get("name", ""), "")
        for hit in CONCEPT_PATH_RE.findall(text):
            rel = hit[prefix:]
            if not rel.endswith("/index.md"):
                reads.add(rel)
    return reads


def extract_tools(parts: list[dict]) -> dict:
    """Per-tool call/error counts and a Bash-executable breakdown for one transcript.

    Two passes so a result is attributed even if it is read before its call: first
    map every tool_use id to its name (and Bash executable), then tally.
    """
    id2name: dict[str, str] = {}
    id2exe: dict[str, str] = {}
    for p in parts:
        if p.get("type") == "tool_use":
            id2name[p.get("id", "")] = p.get("name", "unknown")
            if p.get("name") == "Bash":
                id2exe[p.get("id", "")] = bash_executable(
                    str(p.get("input", {}).get("command", ""))
                )

    by_tool: dict = {}
    bash_exe: dict = {}
    for p in parts:
        if p.get("type") == "tool_use":
            name = p.get("name", "unknown")
            by_tool.setdefault(name, {"calls": 0, "errors": 0, "error_samples": []})[
                "calls"
            ] += 1
            if name == "Bash":
                exe = id2exe.get(p.get("id", ""), "?")
                bash_exe.setdefault(exe, {"calls": 0, "errors": 0})["calls"] += 1
        else:
            tuid = p.get("tool_use_id", "")
            name = id2name.get(tuid, "unknown")
            slot = by_tool.setdefault(
                name, {"calls": 0, "errors": 0, "error_samples": []}
            )
            if not p.get("is_error"):
                continue
            slot["errors"] += 1
            snip = _text_of(p.get("content"))[:160].replace("\n", " ").strip()
            if snip and len(slot["error_samples"]) < 3:
                slot["error_samples"].append(snip)
            if name == "Bash":
                bash_exe.setdefault(id2exe.get(tuid, "?"), {"calls": 0, "errors": 0})[
                    "errors"
                ] += 1
    return {"by_tool": by_tool, "bash_exe": bash_exe}


def merge_tools(agg: dict, src: dict) -> None:
    for name, s in src["by_tool"].items():
        t = agg["by_tool"].setdefault(
            name, {"calls": 0, "errors": 0, "error_samples": []}
        )
        t["calls"] += s["calls"]
        t["errors"] += s["errors"]
        for sample in s["error_samples"]:
            if sample not in t["error_samples"] and len(t["error_samples"]) < 5:
                t["error_samples"].append(sample)
    for exe, s in src["bash_exe"].items():
        t = agg["bash_exe"].setdefault(exe, {"calls": 0, "errors": 0})
        t["calls"] += s["calls"]
        t["errors"] += s["errors"]


def write_tool_report(dest: Path, agg: dict, sessions: int) -> None:
    """Cross-session tool-use summary for the miner to scan for systematic patterns."""
    lines = [
        f"# Tool-use report — {sessions} new sessions\n",
        "## Tools (by error rate)\n",
    ]
    lines.append("| tool | calls | errors | error % |")
    lines.append("|------|------:|------:|-------:|")
    for name, s in sorted(
        agg["by_tool"].items(), key=lambda kv: (-kv[1]["errors"], -kv[1]["calls"])
    ):
        rate = (100 * s["errors"] / s["calls"]) if s["calls"] else 0
        lines.append(f"| {name} | {s['calls']} | {s['errors']} | {rate:.0f}% |")
    lines.append("\n## Bash by executable (calls / errors)\n")
    for exe, s in sorted(agg["bash_exe"].items(), key=lambda kv: -kv[1]["errors"]):
        if s["errors"] or s["calls"] >= 5:
            lines.append(f"- `{exe}`: {s['calls']} calls, {s['errors']} errors")
    lines.append("\n## Notable errors (redacted, deduped)\n")
    for name, s in agg["by_tool"].items():
        for sample in s["error_samples"]:
            lines.append(f"- **{name}**: {sample}")
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")


def cmd_prepare(cfg: dict, run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    for stale in ("metrics.json", "proposal-summary.md"):
        (run_dir / stale).unlink(missing_ok=True)
    digest_dir = run_dir / "digests"
    watermarks = load_json(WATERMARKS)
    new = discover(cfg, watermarks)

    entries: list[dict] = []
    proposed = dict(watermarks)
    tools_agg: dict = {"by_tool": {}, "bash_exe": {}}
    usage_counts: dict[str, int] = {}
    usage_last: dict[str, str] = {}
    for key, sessions in new.items():
        repo = key.split("--", 1)[0]
        repo_dir = digest_dir / key
        repo_dir.mkdir(parents=True, exist_ok=True)
        for s in sessions:
            out = repo_dir / f"{s['session']}.md"
            digest = subprocess.run(
                ["bash", str(DIGEST), s["path"]],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            out.write_text(digest, encoding="utf-8")
            parts = _tool_parts(Path(s["path"]))
            merge_tools(tools_agg, extract_tools(parts))
            for rel in extract_kb_reads(parts):
                usage_counts[rel] = usage_counts.get(rel, 0) + 1
                usage_last[rel] = max(usage_last.get(rel, ""), s["ts"][:10])
            entries.append(
                {
                    "repo": repo,
                    "session": s["session"],
                    "ts": s["ts"],
                    "digest_path": str(out),
                    "digest_bytes": len(digest.encode("utf-8")),
                }
            )
            proposed[key] = max(proposed.get(key, ""), s["ts"])
    session_count = len(entries)

    for repo, artifacts in discover_local(cfg, watermarks).items():
        repo_dir = digest_dir / repo
        repo_dir.mkdir(parents=True, exist_ok=True)
        for a in artifacts:
            out = repo_dir / ("local--" + a["relpath"].replace("/", "--"))
            digest = (
                f"# LOCAL ARTIFACT {repo}/.local/{a['relpath']}\n"
                f"modified: {a['ts']}\n\n"
                + Path(a["path"]).read_text(encoding="utf-8", errors="replace")
            )
            out.write_text(digest, encoding="utf-8")
            entries.append(
                {
                    "repo": repo,
                    "session": f"local:{a['relpath']}",
                    "ts": a["ts"],
                    "digest_path": str(out),
                    "digest_bytes": len(digest.encode("utf-8")),
                }
            )
            key = f"local:{repo}"
            proposed[key] = max(proposed.get(key, ""), a["ts"])
    local_count = len(entries) - session_count

    for repo, files in discover_memory(cfg, watermarks).items():
        repo_dir = digest_dir / repo
        repo_dir.mkdir(parents=True, exist_ok=True)
        for m in files:
            out = repo_dir / f"memory--{m['name']}"
            digest = (
                f"# MEMORY FILE {repo}/memory/{m['name']}\n"
                f"modified: {m['ts']}\n\n"
                + Path(m["path"]).read_text(encoding="utf-8", errors="replace")
            )
            out.write_text(digest, encoding="utf-8")
            entries.append(
                {
                    "repo": repo,
                    "session": f"memory:{m['name']}",
                    "ts": m["ts"],
                    "digest_path": str(out),
                    "digest_bytes": len(digest.encode("utf-8")),
                }
            )
            key = f"memory:{repo}"
            proposed[key] = max(proposed.get(key, ""), m["ts"])

    tool_report = None
    if session_count:
        tool_report = digest_dir / "tool_report.md"
        write_tool_report(tool_report, tools_agg, session_count)
    redacted = redact_tree(digest_dir) if entries else 0
    batches = pack_batches(entries, digest_dir) if entries else []
    kb_manifest = run_dir / "kb_manifest.tsv"
    concepts = write_kb_manifest(kb_manifest)
    usage_path = run_dir / "usage.json"
    usage_path.write_text(
        json.dumps(
            {
                rel: {"sessions": usage_counts[rel], "last_read": usage_last[rel]}
                for rel in sorted(usage_counts)
            },
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )

    manifest = {
        "run_dir": str(run_dir),
        "new_sessions": session_count,
        "local_artifacts": local_count,
        "memory_files": len(entries) - session_count - local_count,
        "secrets_redacted": redacted,
        "batches": batches,
        "kb_manifest": str(kb_manifest),
        "kb_concepts": len(concepts),
        "usage": str(usage_path),
        "kb_reads": len(usage_counts),
        "review": select_review(concepts, load_json(REVIEW), cfg["review_slice"]),
        "tool_report": str(tool_report) if tool_report else None,
        "tool_calls": sum(t["calls"] for t in tools_agg["by_tool"].values()),
        "tool_errors": sum(t["errors"] for t in tools_agg["by_tool"].values()),
        "proposed_watermarks": proposed,
    }
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=1), encoding="utf-8"
    )
    (run_dir / "proposed_watermarks.json").write_text(
        json.dumps(proposed, indent=1), encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in manifest.items() if k != "batches"}, indent=1))
    print(f"batches: {len(batches)} | manifest: {run_dir / 'manifest.json'}")


def apply_pruned(pruned: list[str]) -> None:
    """Delete concepts the review pass retired; decisions are never deleted."""
    for rel in pruned:
        parts = Path(rel).parts
        deletable = (
            len(parts) == 2
            and parts[0] in DIRS
            and parts[0] != "decisions"
            and rel.endswith(".md")
            and parts[1] != "index.md"
        )
        if not deletable:
            raise SystemExit(
                f"refresh: refusing to prune {rel}: not a deletable concept path"
            )
        path = KB / rel
        if not path.exists():
            raise SystemExit(f"refresh: refusing to prune {rel}: no such file")
        path.unlink()


def merge_usage(run_dir: Path, today: str) -> None:
    """Fold this run's per-concept reads into the cumulative .refresh/usage.json."""
    run_usage_path = run_dir / "usage.json"
    if not run_usage_path.exists():
        raise SystemExit(
            f"refresh: {run_usage_path} missing; rerun prepare before finalize"
        )
    run_usage = json.loads(run_usage_path.read_text(encoding="utf-8"))
    cumulative = load_json(USAGE)
    concepts = cumulative.get("concepts", {})
    for rel, u in run_usage.items():
        slot = concepts.setdefault(rel, {"reads": 0, "last_read": ""})
        slot["reads"] += u["sessions"]
        slot["last_read"] = max(slot["last_read"], u["last_read"])
    concepts = {rel: v for rel, v in sorted(concepts.items()) if (KB / rel).exists()}
    USAGE.write_text(
        json.dumps(
            {"since": cumulative.get("since", today), "concepts": concepts}, indent=1
        )
        + "\n",
        encoding="utf-8",
    )


def merge_review(reviewed: list[str], today: str) -> None:
    """Record review dates and drop entries for concepts that no longer exist."""
    state = load_json(REVIEW)
    for rel in reviewed:
        state[rel] = today
    state = {rel: d for rel, d in sorted(state.items()) if (KB / rel).exists()}
    REVIEW.write_text(json.dumps(state, indent=1) + "\n", encoding="utf-8")


def log_line(metrics: dict, date: str) -> str:
    extras = [
        f"+{metrics[k]} {label}"
        for k, label in (
            ("local_artifacts", "local artifacts"),
            ("memory_files", "memory files"),
        )
        if metrics.get(k)
    ]
    reviewed = len(metrics.get("reviewed", []))
    pruned = len(metrics.get("pruned", []))
    parts = [
        (
            f"- {date} — Refresh run: {metrics.get('sessions', 0)} new sessions"
            f"{f' ({', '.join(extras)})' if extras else ''}"
        ),
        f"{metrics.get('created', 0)} concepts created",
        f"{metrics.get('updated', 0)} updated",
        f"{metrics.get('contradictions_fixed', 0)} contradictions reconciled",
    ]
    if reviewed:
        parts.append(f"{reviewed} reviewed" + (f" ({pruned} pruned)" if pruned else ""))
    if metrics.get("tool_patterns"):
        parts.append(f"{metrics['tool_patterns']} tool patterns")
    if metrics.get("recurring_gotchas"):
        parts.append(f"{metrics['recurring_gotchas']} recurring gotchas revised")
    line = ", ".join(parts)
    if metrics.get("reflection"):
        line += "; reflection proposed criteria edits"
    return line + "."


def cmd_finalize(run_dir: Path) -> None:
    proposed = json.loads(
        (run_dir / "proposed_watermarks.json").read_text(encoding="utf-8")
    )
    WATERMARKS.parent.mkdir(parents=True, exist_ok=True)
    WATERMARKS.write_text(
        json.dumps(proposed, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )

    metrics = load_json(run_dir / "metrics.json")
    today = datetime.date.today().isoformat()
    apply_pruned(metrics.get("pruned", []))
    merge_usage(run_dir, today)
    merge_review(metrics.get("reviewed", []), today)
    line = log_line(metrics, today)
    log = KB / "log.md"
    log.write_text(
        log.read_text(encoding="utf-8").rstrip("\n") + "\n" + line + "\n",
        encoding="utf-8",
    )
    print(f"watermarks promoted; log.md appended: {line}")


def main() -> int:
    parser = argparse.ArgumentParser(prog="refresh.py")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_prep = sub.add_parser("prepare")
    p_prep.add_argument("--run-dir", type=Path)
    p_fin = sub.add_parser("finalize")
    p_fin.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.cmd == "prepare":
        cfg = load_config()
        run_dir = args.run_dir or cfg["run_dir"] / "current"
        cmd_prepare(cfg, run_dir.resolve())
    else:
        cmd_finalize(args.run_dir.resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
