# /// script
# requires-python = ">=3.13"
# dependencies = ["pyyaml==6.0.3"]
# ///
"""OKF v0.2 bundle tooling: stamp, validate, and index concept files.

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
ALLOWED_TYPES = set(CONCEPT_TYPES.values()) | {"doc"}
STATUSES = {"draft", "stable", "deprecated"}
VERSION_RE = re.compile(r"^v[1-9][0-9]*$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
LINK_RE = re.compile(r"\]\((/[^)#\s]+\.md)")
DATE_HEADING_RE = re.compile(r"^## (\d{4}-\d{2}-\d{2})$")


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


def is_reserved(path: Path) -> bool:
    """index.md and log.md carry no concept frontmatter (OKF v0.2 §8, §9)."""
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


def validate_reserved(path: Path, text: str, errors: list[str]) -> None:
    """index.md has no frontmatter (root may carry only okf_version); log.md is date-grouped."""
    rel = path.relative_to(REPO_ROOT)
    if path.name == "index.md":
        if path.parent == REPO_ROOT:
            if text.startswith("---\n"):
                fm_text, _ = split_document(text, path)
                fm = load_frontmatter(fm_text, path)
                if set(fm) != {"okf_version"}:
                    errors.append(
                        f"{rel}: root index.md frontmatter may hold only okf_version"
                    )
                elif str(fm["okf_version"]) != "0.2":
                    errors.append(f'{rel}: okf_version must be "0.2"')
        elif text.startswith("---\n"):
            errors.append(f"{rel}: index.md must not carry frontmatter")
        return
    if text.startswith("---\n"):
        errors.append(f"{rel}: log.md must not carry frontmatter")
    dates = [m.group(1) for m in map(DATE_HEADING_RE.match, text.splitlines()) if m]
    if dates != sorted(dates, reverse=True):
        errors.append(f"{rel}: log.md date headings must be newest first")
    for line in text.splitlines():
        if line.startswith("## ") and not DATE_HEADING_RE.match(line):
            errors.append(f"{rel}: log.md heading {line!r} is not ## YYYY-MM-DD")


def valid_datetime(value: object) -> bool:
    if isinstance(value, datetime.datetime):
        return True
    if not isinstance(value, str):
        return False
    try:
        datetime.datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def validate_file(
    path: Path, staged: bool, errors: list[str], warnings: list[str]
) -> None:
    text = path.read_text(encoding="utf-8")
    rel = path.relative_to(REPO_ROOT)
    try:
        if is_reserved(path):
            validate_reserved(path, text, errors)
            return
        fm_text, body = split_document(text, path)
        fm = load_frontmatter(fm_text, path)
    except KbError as exc:
        errors.append(str(exc))
        return

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

    version = str(fm.get("version", ""))
    if not VERSION_RE.match(version):
        errors.append(f"{rel}: version '{version}' does not match ^v[1-9][0-9]*$")
    digest = str(fm.get("content_sha256", ""))
    if not HASH_RE.match(digest):
        errors.append(f"{rel}: content_sha256 missing or not 64 lowercase hex chars")
    elif digest != body_hash(body):
        errors.append(f"{rel}: content_sha256 does not match body (run okf.py stamp)")

    generated = fm.get("generated")
    if not isinstance(generated, dict) or not isinstance(generated.get("by"), str):
        errors.append(
            f"{rel}: generated must be a mapping with 'by' (an actor) and 'at'"
        )
    elif not valid_datetime(generated.get("at")):
        errors.append(f"{rel}: generated.at is not an ISO 8601 datetime")
    verified = fm.get("verified")
    if verified is not None:
        entries = verified if isinstance(verified, list) else [verified]
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("by"), str):
                errors.append(f"{rel}: verified entries need 'by' (an actor) and 'at'")
            elif not valid_datetime(entry.get("at")):
                errors.append(f"{rel}: verified.at is not an ISO 8601 datetime")
    status = fm.get("status")
    if status is not None and status not in STATUSES:
        errors.append(f"{rel}: status '{status}' not in {sorted(STATUSES)}")
    if "stale_after" in fm and not valid_datetime(fm["stale_after"]):
        errors.append(f"{rel}: stale_after is not an ISO 8601 datetime")

    if ftype in CONCEPT_TYPES.values():
        sources = fm.get("sources")
        if not sources or not isinstance(sources, list):
            errors.append(f"{rel}: concept has no 'sources' provenance list")
        else:
            for i, src in enumerate(sources):
                if not isinstance(src, dict) or not src.get("resource"):
                    errors.append(f"{rel}: sources[{i}] has no 'resource'")
    if staged and VERSION_RE.match(version):
        prev = head_version(path)
        if prev is None:
            if version != "v1":
                errors.append(f"{rel}: new file must start at v1, got {version}")
        elif int(version[1:]) != int(prev[1:]) + 1:
            errors.append(
                f"{rel}: version must bump {prev} -> v{int(prev[1:]) + 1}, got {version}"
            )

    if not fm.get("title"):
        warnings.append(f"{rel}: missing 'title'")
    if not fm.get("description"):
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
    """Write a frontmatter-free directory listing in the OKF v0.2 §8 shape."""
    entries = []
    for path in sorted(directory.glob("*.md")):
        if path.name in RESERVED:
            continue
        fm_text, _ = split_document(path.read_text(encoding="utf-8"), path)
        fm = load_frontmatter(fm_text, path)
        title = fm.get("title", path.stem)
        desc = fm.get("description", "")
        status = " *(deprecated)*" if fm.get("status") == "deprecated" else ""
        entries.append(f"* [{title}](/{directory.name}/{path.name}){status} - {desc}")
    listing = "\n".join(entries) if entries else "_No concepts yet._"
    directory.joinpath("index.md").write_text(
        f"# {directory.name}\n\n{listing}\n", encoding="utf-8"
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
