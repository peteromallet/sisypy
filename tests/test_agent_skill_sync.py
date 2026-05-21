from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SKILLS = {
    "sisypy-understand",
    "sisypy-design",
    "sisypy-embed",
    "sisypy-author",
    "sisypy-run",
    "sisypy-debug-evidence",
}


def test_repo_agent_skills_exist_with_frontmatter() -> None:
    skills_dir = ROOT / ".agents" / "skills"

    assert {path.name for path in skills_dir.iterdir() if path.is_dir()} >= EXPECTED_SKILLS
    for name in EXPECTED_SKILLS:
        text = (skills_dir / name / "SKILL.md").read_text(encoding="utf-8")
        assert text.startswith("---\n")
        assert f"name: {name}" in text
        assert "description:" in text


def test_agent_skill_sync_check_passes() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/sync_agent_skills.py"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Sisypy skill sources are valid." in result.stdout


CANONICAL_HEADER = "| # | User ask | Canonical path | Failure mode | Evidence to capture | Shape |"


def test_canonical_candidate_table_header_in_docs_and_readme() -> None:
    """(a) The canonical candidate table header must appear in scenario-design.md,
    applied-philosophy.md, and README.md."""
    files = [
        ROOT / "docs" / "scenario-design.md",
        ROOT / "docs" / "applied-philosophy.md",
        ROOT / "README.md",
    ]
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert CANONICAL_HEADER in text, (
            f"Canonical table header {CANONICAL_HEADER!r} not found in {path.relative_to(ROOT)}"
        )


def test_awaiting_user_confirmation_in_skill_and_docs() -> None:
    """(b) AWAITING_USER_CONFIRMATION must appear in sisypy-design SKILL.md,
    scenario-design.md, and README.md."""
    token = "AWAITING_USER_CONFIRMATION"
    files = [
        ROOT / ".agents" / "skills" / "sisypy-design" / "SKILL.md",
        ROOT / "docs" / "scenario-design.md",
        ROOT / "README.md",
    ]
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert token in text, (
            f"{token!r} not found in {path.relative_to(ROOT)}"
        )


def test_evidence_pack_anatomy_includes_git_status_files() -> None:
    """(c) The evidence pack anatomy table in docs/evidence.md must include
    both git_status_before.txt and git_status_after.txt."""
    evidence_path = ROOT / "docs" / "evidence.md"
    text = evidence_path.read_text(encoding="utf-8")
    assert "git_status_before.txt" in text, (
        "`git_status_before.txt` not found in docs/evidence.md evidence pack anatomy table"
    )
    assert "git_status_after.txt" in text, (
        "`git_status_after.txt` not found in docs/evidence.md evidence pack anatomy table"
    )
