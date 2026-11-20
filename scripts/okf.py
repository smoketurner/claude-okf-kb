# /// script
# requires-python = ">=3.13"
# dependencies = ["pyyaml==6.0.3"]
# ///
"""OKF v0.3 bundle tooling: stamp, validate, and index concept files.

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
OKF_VERSION = "0.3"
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
# OKF v0.3 §7: `<producer>/<version>` for agents and tools, `<scheme>:<id>` otherwise.
ACTOR_RE = re.compile(r"^(?:[a-z][a-z0-9+.-]*:\S+|[^\s/:]+/\S+)$")
ACTOR_FORMS = "<producer>/<version> or <scheme>:<id>"
ROOT_INDEX_KEYS = {"okf_version", "title", "description", "license"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LINK_RE = re.compile(r"\]\(([^)\s]+\.md)(?:#[^)]*)?\)")
INDEX_ENTRY_RE = re.compile(r"^\* \[[^\]]+\]\([^)\s]+\)(?: - .*)?$")
LOG_DATE_RE = re.compile(r"^## (\d{4}-\d{2}-\d{2})$")
CITATIONS_RE = re.compile(r"^#+\s*Citations\s*$", re.MULTILINE)


class KbError(Exception):
    """A validation failure with a file path and reason."""


class _Loader(yaml.SafeLoader):
    """SafeLoader that keeps timestamps as the text the author wrote.

    PyYAML's YAML 1.1 resolver turns `2026-06-30T14:00:00Z` into a datetime and
    dumps it back as `2026-06-30 14:00:00+00:00`, which is not the ISO 8601 form
    the spec requires. Dropping the resolver keeps every value a string, so the
    validator checks the author's text and the stamp round-trips it unchanged.
    """


_Loader.yaml_implicit_resolvers = {
    ch: [(tag, regexp) for tag, regexp in rs if tag != "tag:yaml.org,2002:timestamp"]
    for ch, rs in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def has_frontmatter(text: str) -> bool:
    return text.startswith("---\n")


def split_document(text: str, path: Path) -> tuple[str, str]:
    """Split a concept file into (frontmatter_yaml, body)."""
    if not has_frontmatter(text):
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
        fm = yaml.load(fm_text, Loader=_Loader)
    except yaml.YAMLError as exc:
        raise KbError(f"{path}: unparseable YAML frontmatter: {exc}") from exc
    if not isinstance(fm, dict):
        raise KbError(f"{path}: frontmatter is not a YAML mapping")
    return fm


def read_frontmatter(path: Path) -> dict:
    fm_text, _ = split_document(path.read_text(encoding="utf-8"), path)
    return load_frontmatter(fm_text, path)


def is_reserved(path: Path) -> bool:
    """index.md and log.md carry no frontmatter and are validated structurally."""
    return path.name in RESERVED


def parse_datetime(value: object) -> datetime.datetime | None:
    """An RFC 3339 date-time with an explicit offset (OKF v0.3 §5), else None."""
    if not isinstance(value, str) or "T" not in value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def parse_timestamp(value: object) -> datetime.datetime | None:
    """A date-time with an offset, or a date-only value as the start of that UTC day (§5)."""
    if isinstance(value, str) and DATE_RE.match(value):
        try:
            day = datetime.date.fromisoformat(value)
        except ValueError:
            return None
        return datetime.datetime.combine(day, datetime.time(), datetime.UTC)
    return parse_datetime(value)


def normalize_verified(fm: dict) -> list:
    """`verified` as a list; a bare mapping is a one-element list (§5.2)."""
    verified = fm.get("verified")
    if verified is None:
        return []
    if isinstance(verified, dict):
        return [verified]
    return verified if isinstance(verified, list) else [verified]


def last_checked(fm: dict) -> datetime.datetime:
    """The later of the newest `verified[].at` and `generated.at`; the epoch when neither parses.

    Compared as instants, not text, so mixed offsets and date-only values order correctly.
    """
    generated = fm.get("generated")
    ats = [e.get("at") for e in normalize_verified(fm) if isinstance(e, dict)]
    if isinstance(generated, dict):
        ats.append(generated.get("at"))
    parsed = [t for t in map(parse_timestamp, ats) if t is not None]
    return max(parsed, default=datetime.datetime.fromtimestamp(0, datetime.UTC))


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


def resolve_link(target: str, path: Path) -> Path | None:
    """The file a markdown link names, or None for links outside the bundle."""
    if "://" in target:
        return None
    if target.startswith("/"):
        return REPO_ROOT / target.lstrip("/")
    return (path.parent / target).resolve()


def check_links(body: str, path: Path, rel: Path, warnings: list[str]) -> None:
    for match in LINK_RE.finditer(body):
        target = resolve_link(match.group(1), path)
        if target is not None and not target.exists():
            warnings.append(f"{rel}: broken link {match.group(1)}")


def check_actor_event(
    event: object, label: str, rel: Path, errors: list[str], *, require_at: bool
) -> None:
    if not isinstance(event, dict):
        errors.append(f"{rel}: {label} must be a mapping with 'by' and 'at'")
        return
    by = event.get("by")
    if not isinstance(by, str) or not ACTOR_RE.match(by):
        errors.append(f"{rel}: {label}.by {by!r} is not an actor ({ACTOR_FORMS})")
    at = event.get("at")
    if at is None and not require_at:
        return
    if parse_datetime(at) is None:
        errors.append(
            f"{rel}: {label}.at {at!r} is not an RFC 3339 date-time with an offset"
        )


def validate_concept(
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

    version = str(fm.get("version", ""))
    if not VERSION_RE.match(version):
        errors.append(f"{rel}: version '{version}' does not match ^v[1-9][0-9]*$")
    digest = str(fm.get("content_sha256", ""))
    if not HASH_RE.match(digest):
        errors.append(f"{rel}: content_sha256 missing or not 64 lowercase hex chars")
    elif digest != body_hash(body):
        errors.append(f"{rel}: content_sha256 does not match body (run okf.py stamp)")

    if "timestamp" in fm:
        errors.append(
            f"{rel}: 'timestamp' is superseded by 'generated.at' (since OKF v0.2)"
        )
    if "generated" not in fm:
        errors.append(f"{rel}: missing 'generated' ({{by, at}})")
    else:
        check_actor_event(fm["generated"], "generated", rel, errors, require_at=True)

    for i, event in enumerate(normalize_verified(fm)):
        check_actor_event(event, f"verified[{i}]", rel, errors, require_at=True)

    status = fm.get("status")
    if status is not None and status not in STATUSES:
        errors.append(f"{rel}: status '{status}' not in {sorted(STATUSES)}")
    superseded_by = fm.get("superseded_by")
    if superseded_by is not None:
        if status != "deprecated":
            errors.append(f"{rel}: 'superseded_by' requires status: deprecated")
        target = resolve_link(str(superseded_by), path)
        if target is None or not target.exists():
            warnings.append(f"{rel}: superseded_by target {superseded_by} not found")
    if "stale_after" in fm and parse_timestamp(fm["stale_after"]) is None:
        errors.append(
            f"{rel}: stale_after {fm['stale_after']!r} is not an RFC 3339 date-time "
            "with an offset or a YYYY-MM-DD date"
        )

    sources = fm.get("sources")
    if ftype in CONCEPT_TYPES.values() and not sources:
        errors.append(f"{rel}: concept has no 'sources' provenance list")
    if sources is not None:
        if not isinstance(sources, list):
            errors.append(f"{rel}: 'sources' must be a list")
        else:
            for i, entry in enumerate(sources):
                if not isinstance(entry, dict) or not entry.get("resource"):
                    errors.append(f"{rel}: sources[{i}] has no 'resource'")
                    continue
                lm = entry.get("last_modified")
                if lm is not None and parse_timestamp(lm) is None:
                    errors.append(
                        f"{rel}: sources[{i}].last_modified {lm!r} is not an RFC 3339 "
                        "date-time with an offset or a YYYY-MM-DD date"
                    )
                author = entry.get("author")
                if author is not None and not ACTOR_RE.match(str(author)):
                    errors.append(
                        f"{rel}: sources[{i}].author {author!r} is not an actor ({ACTOR_FORMS})"
                    )
    if CITATIONS_RE.search(body):
        errors.append(f"{rel}: '# Citations' body list is superseded by 'sources'")

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
    check_links(body, path, rel, warnings)


def validate_index(path: Path, errors: list[str], warnings: list[str]) -> None:
    """An index.md is list sections; only the root one has frontmatter, bundle metadata (§8.1)."""
    text = path.read_text(encoding="utf-8")
    rel = path.relative_to(REPO_ROOT)
    body = text
    if has_frontmatter(text):
        if path.parent != REPO_ROOT:
            errors.append(f"{rel}: index.md must not carry frontmatter")
            return
        try:
            fm_text, body = split_document(text, path)
            fm = load_frontmatter(fm_text, path)
        except KbError as exc:
            errors.append(str(exc))
            return
        extra = sorted(
            k for k in fm if k not in ROOT_INDEX_KEYS and not str(k).startswith("x-")
        )
        if extra:
            errors.append(
                f"{rel}: root index frontmatter may carry only {sorted(ROOT_INDEX_KEYS)} "
                f"and x- extension keys, found {extra}"
            )
        if str(fm.get("okf_version")) != OKF_VERSION:
            errors.append(f'{rel}: okf_version must be "{OKF_VERSION}"')
    elif path.parent == REPO_ROOT:
        errors.append(f'{rel}: root index.md must declare okf_version: "{OKF_VERSION}"')
    for line in body.splitlines():
        if line.startswith(("* [", "- [")) and not INDEX_ENTRY_RE.match(line):
            errors.append(
                f"{rel}: index entry must be '* [Title](link) - description': {line}"
            )
    check_links(body, path, rel, warnings)


def validate_log(path: Path, errors: list[str], warnings: list[str]) -> None:
    """A log.md is frontmatter-free, date-grouped under ISO headings, newest first (§9)."""
    text = path.read_text(encoding="utf-8")
    rel = path.relative_to(REPO_ROOT)
    if has_frontmatter(text):
        errors.append(f"{rel}: log.md must not carry frontmatter")
        return
    dates: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            match = LOG_DATE_RE.match(line)
            if match is None:
                errors.append(f"{rel}: log heading must be '## YYYY-MM-DD': {line}")
            else:
                dates.append(match.group(1))
    if dates != sorted(dates, reverse=True):
        errors.append(f"{rel}: log date headings must be newest first")
    if len(dates) != len(set(dates)):
        errors.append(f"{rel}: log has a repeated date heading")
    check_links(text, path, rel, warnings)


def validate_file(
    path: Path, staged: bool, errors: list[str], warnings: list[str]
) -> None:
    if path.name == "index.md":
        validate_index(path, errors, warnings)
    elif path.name == "log.md":
        validate_log(path, errors, warnings)
    else:
        validate_concept(path, staged, errors, warnings)


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
    """Write the directory's index.md in the §8 shape: one section, relative links."""
    if directory == REPO_ROOT:
        raise KbError(
            f"{directory}: the root index.md carries bundle metadata; edit it by hand"
        )
    entries = []
    for path in sorted(directory.glob("*.md")):
        if path.name in RESERVED:
            continue
        fm = read_frontmatter(path)
        title = fm.get("title", path.stem)
        desc = fm.get("description", "")
        status = " *(deprecated)*" if fm.get("status") == "deprecated" else ""
        entries.append(f"* [{title}]({path.name}){status} - {desc}")
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
