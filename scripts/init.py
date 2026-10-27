# /// script
# requires-python = ">=3.13"
# ///
"""Scaffold a new OKF knowledge base in the current directory.

Copies the bundle skeleton (`template/`), the maintenance scripts, and the
`/kb-refresh` skill from the plugin (or template checkout) at --source into the
target directory, fills in the project placeholders, writes `kb.config.json`
plus the Claude Code settings that embed the configured paths,
stamps the docs, and regenerates every index. The `/kb-init` skill drives it.

    uv run <source>/scripts/init.py --name vouch --title Vouch \
        --repo ~/envs/git/vouch --repo ~/envs/git/vouch-infra [--since 2026-01-01]
        [--run-dir DIR] [--base-branch main] [--target DIR]

When the target is the template checkout itself (a "Use this template" copy), the
template-only files — `template/`, `.claude-plugin/`, the init skill, and this
script — are removed afterwards so the result is a plain knowledge-base repository.
"""

import argparse
import datetime
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent
SCRIPTS = ["okf.py", "refresh.py", "digest.sh", "refresh-propose.sh"]
TEMPLATE_ONLY = [
    "template",
    ".claude-plugin",
    ".claude/skills/kb-init",
    "scripts/init.py",
    ".github/FUNDING.yml",
]
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
ALLOW = [
    "Read(/{home}/.claude/projects/**)",
    "Bash(uv run scripts/refresh.py:*)",
    "Bash(uv run scripts/okf.py:*)",
    "Bash(bash scripts/refresh-propose.sh)",
    "Bash(rg:*)",
    "Bash(grep:*)",
    "Bash(cat:*)",
    "Bash(ls:*)",
    "Bash(find:*)",
    "Bash(wc:*)",
    "Bash(head:*)",
    "Bash(tail:*)",
    "Bash(git status:*)",
    "Bash(git diff:*)",
    "Bash(git log:*)",
    "Bash(git show:*)",
]


def fill(text: str, values: dict[str, str]) -> str:
    out = re.sub(r"\{\{([A-Z_]+)\}\}", lambda m: values[m.group(1)], text)
    return out


# Stored without the leading dot so prek does not treat template/ as a nested workspace.
RENAMES = {"pre-commit-config.yaml": ".pre-commit-config.yaml"}


def copy_tree(src: Path, dst: Path, values: dict[str, str]) -> None:
    for path in sorted(src.rglob("*")):
        if path.is_dir():
            continue
        rel = path.relative_to(src)
        target = dst / rel.parent / RENAMES.get(rel.name, rel.name)
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix in {".md", ".yaml", ".yml", ".json", ".template"}:
            target.write_text(
                fill(path.read_text(encoding="utf-8"), values), encoding="utf-8"
            )
        else:
            shutil.copy2(path, target)


def main() -> int:
    parser = argparse.ArgumentParser(prog="init.py")
    parser.add_argument("--name", required=True, help="short project slug, e.g. vouch")
    parser.add_argument("--title", required=True, help="human project name, e.g. Vouch")
    parser.add_argument(
        "--repo", action="append", required=True, help="absolute path of a source repo"
    )
    parser.add_argument("--since", default="", help="ignore sessions before YYYY-MM-DD")
    parser.add_argument("--run-dir", default="")
    parser.add_argument("--base-branch", default="main")
    parser.add_argument("--target", type=Path, default=Path.cwd())
    args = parser.parse_args()

    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", args.name):
        raise SystemExit("init: --name must be a lowercase kebab-case slug")
    target = args.target.resolve()
    repos = [Path(r).expanduser().resolve() for r in args.repo]
    for repo in repos:
        if not repo.is_dir():
            raise SystemExit(f"init: source repo {repo} is not a directory")
        if repo == target:
            raise SystemExit("init: the knowledge base cannot mine itself")
    if (target / "kb.config.json").exists():
        raise SystemExit(f"init: {target} already holds a knowledge base")
    if args.since and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.since):
        raise SystemExit("init: --since must be YYYY-MM-DD")

    home = str(Path.home())
    run_dir = args.run_dir or f"/private/tmp/{args.name}-kb-refresh"
    scopes = [r.name for r in repos]
    values = {
        "NAME": args.name,
        "TITLE": args.title,
        "KB_NAME": target.name,
        "KB_PATH": str(target),
        "DATE": datetime.date.today().isoformat(),
        "REPOS": ", ".join(f"`{s}`" for s in scopes),
        "SCOPES": ", ".join(f"`{s}`" for s in [*scopes, "cross"]),
        "REPO_PATHS": "\n".join(f"- `{r}`" for r in repos),
    }

    in_place = target == SOURCE
    copy_tree(SOURCE / "template", target, values)
    if not in_place:
        (target / "scripts").mkdir(exist_ok=True)
        for name in [*SCRIPTS, "README.md"]:
            shutil.copy2(SOURCE / "scripts" / name, target / "scripts" / name)
        shutil.copytree(
            SOURCE / ".claude" / "skills" / "kb-refresh",
            target / ".claude" / "skills" / "kb-refresh",
            dirs_exist_ok=True,
        )

    config = {
        "name": args.name,
        "title": args.title,
        "source_repos": [str(r) for r in repos],
        "run_dir": run_dir,
        "mine_since": f"{args.since}T00:00:00.000Z" if args.since else "",
        "base_branch": args.base_branch,
        "review_slice": 10,
    }
    (target / "kb.config.json").write_text(
        json.dumps(config, indent=2) + "\n", encoding="utf-8"
    )
    settings = {
        "permissions": {
            "additionalDirectories": [
                f"{home}/.claude/projects",
                run_dir,
                *[str(r) for r in repos],
            ],
            "allow": [a.format(home=home) for a in ALLOW],
        }
    }
    (target / ".claude" / "settings.json").write_text(
        json.dumps(settings, indent=2) + "\n", encoding="utf-8"
    )
    (target / ".refresh" / "watermarks.json").write_text("{}\n", encoding="utf-8")

    for d in DIRS:
        (target / d).mkdir(exist_ok=True)
    okf = ["uv", "run", str(target / "scripts" / "okf.py")]
    subprocess.run(
        [*okf, "stamp", "AGENTS.md", "CLAUDE.md", "README.md"], cwd=target, check=True
    )
    subprocess.run([*okf, "index", *DIRS], cwd=target, check=True)

    if in_place:
        for rel in TEMPLATE_ONLY:
            path = target / rel
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
    subprocess.run([*okf, "validate"], cwd=target, check=True)
    print(
        f"init: scaffolded {target.name} for {args.title} ({len(repos)} source repos)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
