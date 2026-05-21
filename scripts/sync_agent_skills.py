#!/usr/bin/env python3
"""Sync Sisypy agent skills into local Claude and Codex skill directories.

The canonical skill source is ``.agents/skills/<name>/SKILL.md`` inside this
repo. ``--apply`` creates symlinks in ``~/.claude/skills`` and
``~/.codex/skills`` when the destination does not already exist. It never
overwrites existing files, directories, or symlinks.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / ".agents" / "skills"
TARGET_DIRS = (
    Path.home() / ".claude" / "skills",
    Path.home() / ".codex" / "skills",
)
EXPECTED_SKILLS = (
    "sisypy-understand",
    "sisypy-design",
    "sisypy-embed",
    "sisypy-author",
    "sisypy-run",
    "sisypy-debug-evidence",
)


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _skill_source(name: str) -> Path:
    return SOURCE_DIR / name


def _validate_sources() -> list[str]:
    errors: list[str] = []
    for name in EXPECTED_SKILLS:
        skill_dir = _skill_source(name)
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.is_file():
            errors.append(f"missing {_rel(skill_file)}")
            continue
        text = skill_file.read_text(encoding="utf-8")
        if not text.startswith("---\n") or f"name: {name}" not in text:
            errors.append(f"{_rel(skill_file)} is missing skill frontmatter for {name}")
    return errors


def check() -> int:
    errors = _validate_sources()
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    for target_dir in TARGET_DIRS:
        if not target_dir.exists():
            print(f"target missing: {target_dir}")
            continue
        for name in EXPECTED_SKILLS:
            dest = target_dir / name
            src = _skill_source(name)
            if dest.is_symlink() and dest.resolve() == src.resolve():
                print(f"linked {dest} -> {src}")
            elif dest.exists() or dest.is_symlink():
                print(f"exists {dest} (not modified)")
            else:
                print(f"missing {dest}")
    print("Sisypy skill sources are valid.")
    return 0


def apply() -> int:
    errors = _validate_sources()
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    created = 0
    skipped = 0
    missing_targets = 0
    for target_dir in TARGET_DIRS:
        if not target_dir.exists():
            print(f"skip target missing: {target_dir}", file=sys.stderr)
            missing_targets += 1
            continue
        for name in EXPECTED_SKILLS:
            src = _skill_source(name)
            dest = target_dir / name
            if dest.exists() or dest.is_symlink():
                print(f"skip   {dest} (exists)")
                skipped += 1
                continue
            dest.symlink_to(src)
            print(f"linked {dest} -> {src}")
            created += 1

    print(f"done: {created} created, {skipped} skipped, {missing_targets} targets missing")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="create missing skill symlinks")
    args = parser.parse_args(argv)
    return apply() if args.apply else check()


if __name__ == "__main__":
    raise SystemExit(main())
