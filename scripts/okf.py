# /// script
# requires-python = ">=3.13"
# dependencies = ["pyyaml==6.0.3"]
# ///
"""OKF bundle tooling: stamp, validate, and index concept files.

Usage:
    uv run scripts/okf.py stamp <file>...
    uv run scripts/okf.py validate [--staged] [<file>...]
    uv run scripts/okf.py index <dir>...
"""

import argparse
import datetime
import hashlib
import re
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
RESERVED = {"index.md", "log.md"}
CONCEPT_TYPES = {
    "architecture": "architecture",
    "decisions": "decision",
    "conventions": "convention",
    "gotchas": "gotcha",
    "runbooks": "runbook",
    "integrations": "integration",
    "testing": "testing",
    "references": "reference",
}
ALLOWED_TYPES = set(CONCEPT_TYPES.values()) | {"doc", "index", "log"}
VERSION_RE = re.compile(r"^v[1-9][0-9]*$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
LINK_RE = re.compile(r"\]\((/[^)#\s]+\.md)")


class KbError(Exception):
    """A validation failure with a file path and reason."""


def split_document(text: str, path: Path) -> tuple[str, str]:
    """Split a concept file into (frontmatter_yaml, body)."""
    if not text.startswith("---\n"):
        raise KbError(f"{path}: missing opening '---' frontmatter delimiter")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise KbError(f"{path}: missing closing '---' frontmatter delimiter")
    return text[4:end], text[end + 5 :]


def normalize_body(body: str) -> str:
    """Trailing whitespace stripped per line, exactly one trailing LF.

    Matches what the trailing-whitespace and end-of-file-fixer hooks produce,
    so those hooks can never change a body after it has been stamped.
    """
    lines = [line.rstrip() for line in body.split("\n")]
    return "\n".join(lines).rstrip() + "\n"


def body_hash(body: str) -> str:
    """SHA-256 of the normalized body."""
    return hashlib.sha256(normalize_body(body).encode("utf-8")).hexdigest()


def load_frontmatter(fm_text: str, path: Path) -> dict:
    try:
        fm = yaml.safe_load(fm_text)
    except yaml.YAMLError as exc:
        raise KbError(f"{path}: unparseable YAML frontmatter: {exc}") from exc
    if not isinstance(fm, dict):
        raise KbError(f"{path}: frontmatter is not a YAML mapping")
    return fm


def is_exempt(path: Path) -> bool:
    """index.md and log.md are regenerated/append-only: no version/hash."""
    return path.name in RESERVED


def stamp(path: Path) -> str:
    """Recompute content_sha256 and rewrite that one frontmatter line in place."""
    text = path.read_text(encoding="utf-8")
    fm_text, body = split_document(text, path)
    load_frontmatter(fm_text, path)
    digest = body_hash(body)
    lines = fm_text.split("\n")
    replaced = False
    for i, line in enumerate(lines):
        if line.startswith("content_sha256:"):
            lines[i] = f"content_sha256: {digest}"
            replaced = True
            break
    if not replaced:
        for i, line in enumerate(lines):
            if line.startswith("version:"):
                lines[i] = f"{line}\ncontent_sha256: {digest}"
                replaced = True
                break
    if not replaced:
        lines.append(f"content_sha256: {digest}")
    path.write_text(
        "---\n" + "\n".join(lines) + "\n---\n" + normalize_body(body), encoding="utf-8"
    )
    return digest


def expected_type(path: Path) -> str | None:
    """The type a file must declare based on its location, if constrained."""
    rel = path.relative_to(REPO_ROOT)
    if path.name == "index.md":
        return "index"
    if path.name == "log.md":
        return "log"
    if len(rel.parts) == 2 and rel.parts[0] in CONCEPT_TYPES:
        return CONCEPT_TYPES[rel.parts[0]]
    if len(rel.parts) == 1:
        return "doc"
    return None


def head_version(path: Path) -> str | None:
    """Return the version recorded at git HEAD for path, or None if new."""
    rel = path.relative_to(REPO_ROOT)
    proc = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "show", f"HEAD:{rel}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    fm_text, _ = split_document(proc.stdout, path)
    fm = load_frontmatter(fm_text, path)
    version = fm.get("version")
    return str(version) if version is not None else None


def validate_file(
    path: Path, staged: bool, errors: list[str], warnings: list[str]
) -> None:
    text = path.read_text(encoding="utf-8")
    try:
        fm_text, body = split_document(text, path)
        fm = load_frontmatter(fm_text, path)
    except KbError as exc:
        errors.append(str(exc))
        return
    rel = path.relative_to(REPO_ROOT)

    ftype = fm.get("type")
    if not ftype or not isinstance(ftype, str):
        errors.append(f"{rel}: missing or empty 'type'")
        return
    if ftype not in ALLOWED_TYPES:
        errors.append(
            f"{rel}: type '{ftype}' not in allowed set {sorted(ALLOWED_TYPES)}"
        )
    want = expected_type(path)
    if want is not None and ftype != want:
        errors.append(f"{rel}: type '{ftype}' but location requires '{want}'")

    if not is_exempt(path):
        version = str(fm.get("version", ""))
        if not VERSION_RE.match(version):
            errors.append(f"{rel}: version '{version}' does not match ^v[1-9][0-9]*$")
        digest = str(fm.get("content_sha256", ""))
        if not HASH_RE.match(digest):
            errors.append(
                f"{rel}: content_sha256 missing or not 64 lowercase hex chars"
            )
        elif digest != body_hash(body):
            errors.append(
                f"{rel}: content_sha256 does not match body (run okf.py stamp)"
            )
        ts = fm.get("timestamp")
        try:
            if isinstance(ts, str):
                datetime.datetime.fromisoformat(ts)
            elif not isinstance(ts, (datetime.date, datetime.datetime)):
                raise TypeError(f"unsupported timestamp {ts!r}")
        except (TypeError, ValueError):
            errors.append(f"{rel}: timestamp is not ISO 8601")
        if ftype in CONCEPT_TYPES.values() and not fm.get("sources"):
            errors.append(f"{rel}: concept has no 'sources' provenance list")
        if staged and VERSION_RE.match(version):
            prev = head_version(path)
            if prev is None:
                if version != "v1":
                    errors.append(f"{rel}: new file must start at v1, got {version}")
            elif int(version[1:]) != int(prev[1:]) + 1:
                errors.append(
                    f"{rel}: version must bump {prev} -> v{int(prev[1:]) + 1}, got {version}"
                )

    if not fm.get("title") and ftype not in {"index", "log"}:
        warnings.append(f"{rel}: missing 'title'")
    if not fm.get("description") and ftype not in {"index", "log"}:
        warnings.append(f"{rel}: missing 'description'")
    for match in LINK_RE.finditer(body):
        target = REPO_ROOT / match.group(1).lstrip("/")
        if not target.exists():
            warnings.append(f"{rel}: broken bundle-relative link {match.group(1)}")


EXCLUDED_DIRS = {".git", ".claude", ".claude-plugin", "scripts", "template", ".refresh"}


def all_markdown_files() -> list[Path]:
    return sorted(
        p for p in REPO_ROOT.rglob("*.md") if EXCLUDED_DIRS.isdisjoint(p.parts)
    )


def staged_markdown_files() -> list[Path]:
    proc = subprocess.run(
        [
            "git",
            "-C",
            str(REPO_ROOT),
            "diff",
            "--cached",
            "--name-only",
            "--diff-filter=ACM",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return [
        REPO_ROOT / line
        for line in proc.stdout.splitlines()
        if line.endswith(".md") and EXCLUDED_DIRS.isdisjoint(Path(line).parts)
    ]


def regenerate_index(directory: Path) -> None:
    entries = []
    for path in sorted(directory.glob("*.md")):
        if path.name in RESERVED:
            continue
        fm_text, _ = split_document(path.read_text(encoding="utf-8"), path)
        fm = load_frontmatter(fm_text, path)
        title = fm.get("title", path.stem)
        desc = fm.get("description", "")
        status = " *(superseded)*" if fm.get("status") == "superseded" else ""
        entries.append(f"- [{title}](/{directory.name}/{path.name}){status} — {desc}")
    listing = "\n".join(entries) if entries else "_No concepts yet._"
    directory.joinpath("index.md").write_text(
        f"---\ntype: index\ntitle: {directory.name}\n---\n\n# {directory.name}\n\n{listing}\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(prog="okf.py")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_stamp = sub.add_parser("stamp")
    p_stamp.add_argument("files", nargs="+", type=Path)
    p_validate = sub.add_parser("validate")
    p_validate.add_argument("--staged", action="store_true")
    p_validate.add_argument("files", nargs="*", type=Path)
    p_index = sub.add_parser("index")
    p_index.add_argument("dirs", nargs="+", type=Path)
    args = parser.parse_args()

    if args.cmd == "stamp":
        for path in args.files:
            digest = stamp(path.resolve())
            print(f"stamped {path}: {digest}")
        return 0

    if args.cmd == "index":
        for directory in args.dirs:
            regenerate_index(directory.resolve())
            print(f"indexed {directory}")
        return 0

    if args.files:
        targets = [p.resolve() for p in args.files]
    elif args.staged:
        targets = staged_markdown_files()
    else:
        targets = all_markdown_files()
    errors: list[str] = []
    warnings: list[str] = []
    for path in targets:
        if path.exists():
            validate_file(path, args.staged, errors, warnings)
    for warning in warnings:
        print(f"warning: {warning}")
    for error in errors:
        print(f"error: {error}", file=sys.stderr)
    print(
        f"validated {len(targets)} files: {len(errors)} errors, {len(warnings)} warnings"
    )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
