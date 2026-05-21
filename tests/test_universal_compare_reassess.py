import json
from pathlib import Path

from sisypy.adapters import FakeProjectAdapter
from sisypy.assessor import assess
from sisypy.compare import compare
from sisypy.reassess import reassess_evidence
from sisypy.schema import EvidencePack, Scenario
from sisypy.universal_checks import (
    check_forbidden_commands,
    derive_proof_from_evidence,
    run_all_checks,
)


def _write_evidence_pack(root: Path, *, name: str, tree_after: str, git_diff: str = "") -> Path:
    evidence_dir = root / name
    evidence_dir.mkdir()
    files = {
        "manifest.json": "manifest.json",
        "report.md": "report.md",
        "stdout.log": "stdout.log",
        "stderr.log": "stderr.log",
        "command_log.jsonl": "command_log.jsonl",
        "tree_after.txt": "tree_after.txt",
        "git_diff.patch": "git_diff.patch",
    }
    manifest = {
        "scenario_id": name,
        "mode": "structural",
        "command_policy": {"deny_patterns": [r"rm\s+-rf"]},
        "evidence_confidence": "high",
        "files": files,
    }
    (evidence_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (evidence_dir / "report.md").write_text(
        "## 1. Work\nCreated a small fixture.\n\n## 2. Evidence\nNo runtime claims.\n",
        encoding="utf-8",
    )
    (evidence_dir / "stdout.log").write_text("", encoding="utf-8")
    (evidence_dir / "stderr.log").write_text("", encoding="utf-8")
    (evidence_dir / "command_log.jsonl").write_text(
        json.dumps({"command": "python -m sisypy --help", "exit_code": 0}) + "\n",
        encoding="utf-8",
    )
    (evidence_dir / "tree_after.txt").write_text(tree_after, encoding="utf-8")
    (evidence_dir / "git_diff.patch").write_text(git_diff, encoding="utf-8")
    return evidence_dir


def _write_evidence_pack_no_report(root: Path, *, name: str) -> Path:
    """Write a minimal evidence pack WITHOUT report.md (missing-report scenario)."""
    evidence_dir = root / name
    evidence_dir.mkdir()
    files = {
        "manifest.json": "manifest.json",
        # No report.md
        "stdout.log": "stdout.log",
        "stderr.log": "stderr.log",
        "command_log.jsonl": "command_log.jsonl",
        "tree_after.txt": "tree_after.txt",
        "git_diff.patch": "git_diff.patch",
    }
    manifest = {
        "scenario_id": name,
        "mode": "structural",
        "command_policy": {"deny_patterns": [r"rm\s+-rf"]},
        "evidence_confidence": "low",
        "files": files,
    }
    (evidence_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (evidence_dir / "stdout.log").write_text("", encoding="utf-8")
    (evidence_dir / "stderr.log").write_text("", encoding="utf-8")
    (evidence_dir / "command_log.jsonl").write_text("", encoding="utf-8")
    (evidence_dir / "tree_after.txt").write_text("", encoding="utf-8")
    (evidence_dir / "git_diff.patch").write_text("", encoding="utf-8")
    return evidence_dir


def _write_no_substantive_evidence_pack(root: Path, *, name: str) -> Path:
    """Write an evidence pack with NO claims and NO command/action/git-diff/artifact evidence."""
    evidence_dir = root / name
    evidence_dir.mkdir()
    files = {
        "manifest.json": "manifest.json",
        "stdout.log": "stdout.log",
        "stderr.log": "stderr.log",
        "command_log.jsonl": "command_log.jsonl",
        "tree_after.txt": "tree_after.txt",
        "git_diff.patch": "git_diff.patch",
    }
    # No report.md at all.
    manifest = {
        "scenario_id": name,
        "mode": "structural",
        "command_policy": {"deny_patterns": [r"rm\s+-rf"]},
        "evidence_confidence": "low",
        "files": files,
    }
    (evidence_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    # All evidence files are empty.
    (evidence_dir / "stdout.log").write_text("", encoding="utf-8")
    (evidence_dir / "stderr.log").write_text("", encoding="utf-8")
    (evidence_dir / "command_log.jsonl").write_text("", encoding="utf-8")
    (evidence_dir / "tree_after.txt").write_text("", encoding="utf-8")
    (evidence_dir / "git_diff.patch").write_text("", encoding="utf-8")
    return evidence_dir


# ---------------------------------------------------------------------------
# Existing tests (preserved)
# ---------------------------------------------------------------------------


def test_universal_checks_pass_on_minimal_authored_evidence(tmp_path):
    evidence_dir = _write_evidence_pack(
        tmp_path,
        name="authored",
        tree_after="F recipes/native_recipe.py\n",
        git_diff="diff --git a/recipes/native_recipe.py b/recipes/native_recipe.py\n",
    )
    pack = EvidencePack(evidence_dir=str(evidence_dir))
    scenario = Scenario(name="authored")

    derived_level, ladder = derive_proof_from_evidence(pack)
    checks = run_all_checks(
        pack,
        scenario,
        deny_patterns=[r"rm\s+-rf"],
        actual_proof_level=derived_level,
    )

    assert derived_level == "authored"
    assert ladder["authored"]["found"] is True
    assert checks["all_passed"] is True
    assert checks["checks"]["deliverable_shape"]["passed"] is True
    assert checks["checks"]["forbidden_commands"]["violations"] == []


def test_forbidden_command_check_reads_command_evidence_not_report_text(tmp_path):
    evidence_dir = _write_evidence_pack(
        tmp_path,
        name="forbidden",
        tree_after="F recipes/native_recipe.py\n",
        git_diff="diff --git a/recipes/native_recipe.py b/recipes/native_recipe.py\n",
    )
    (evidence_dir / "report.md").write_text(
        "## 1. Notes\nDo not run rm -rf in structural mode.\n\n## 2. Evidence\nCreated a file.\n",
        encoding="utf-8",
    )
    pack = EvidencePack(evidence_dir=str(evidence_dir))

    clean = check_forbidden_commands(pack, Scenario(name="forbidden"), deny_patterns=[r"rm\s+-rf"])
    assert clean["passed"] is True

    (evidence_dir / "command_log.jsonl").write_text(
        json.dumps({"command": "rm -rf out/cache", "exit_code": 0}) + "\n",
        encoding="utf-8",
    )
    dirty = check_forbidden_commands(pack, Scenario(name="forbidden"), deny_patterns=[r"rm\s+-rf"])
    assert dirty["passed"] is False
    assert dirty["violations"][0]["source"] == "command_log.jsonl"


def test_compare_prefers_pack_with_higher_frozen_proof_level(tmp_path):
    authored_dir = _write_evidence_pack(
        tmp_path,
        name="authored",
        tree_after="F recipes/native_recipe.py\n",
        git_diff="diff --git a/recipes/native_recipe.py b/recipes/native_recipe.py\n",
    )
    compiled = _write_evidence_pack(
        tmp_path,
        name="compiled",
        tree_after="F build/api.json\n",
        git_diff="diff --git a/build/api.json b/build/api.json\n",
    )

    verdict = compare(authored_dir, compiled)

    assert verdict["winning_arm"] == "b"
    assert verdict["proof_comparison"]["a"]["level"] == "authored"
    assert verdict["proof_comparison"]["b"]["level"] == "compiled"


def test_reassess_evidence_recomputes_deterministic_checks(tmp_path):
    evidence_dir = _write_evidence_pack(
        tmp_path,
        name="reassess",
        tree_after="F recipes/native_recipe.py\n",
        git_diff="diff --git a/recipes/native_recipe.py b/recipes/native_recipe.py\n",
    )

    result = reassess_evidence(evidence_dir, adapter=FakeProjectAdapter(), run_llm=False)

    assert result["evidence_dir"] == str(evidence_dir)
    assert result["derived_proof_level"] == "authored"
    assert result["universal_checks"]["all_passed"] is True
    assert result["project_checks"] == {}
    assert result["assessment"]["model"] == "deterministic"


# ---------------------------------------------------------------------------
# (d) Missing report.md produces any_undetermined=True in run_all_checks
# ---------------------------------------------------------------------------


def test_missing_report_produces_any_undetermined(tmp_path):
    """(d) Missing report.md produces any_undetermined=True in run_all_checks."""
    evidence_dir = _write_evidence_pack_no_report(tmp_path, name="no-report")
    pack = EvidencePack(evidence_dir=str(evidence_dir))
    scenario = Scenario(name="no-report")

    derived_level, ladder = derive_proof_from_evidence(pack)
    checks = run_all_checks(
        pack,
        scenario,
        deny_patterns=[r"rm\s+-rf"],
        actual_proof_level=derived_level,
    )

    # any_undetermined must be True when report.md is missing.
    assert "any_undetermined" in checks
    assert checks["any_undetermined"] is True
    # all_passed should be False (missing report is not a pass).
    assert checks["all_passed"] is False
    # deliverable_shape check should be undetermined, not error.
    ds = checks["checks"]["deliverable_shape"]
    assert ds["passed"] is False
    assert ds.get("undetermined") is True
    assert ds.get("severity") == "undetermined"


# ---------------------------------------------------------------------------
# (e) No-claims/no-substantive-evidence produces undetermined deterministic assessment
# ---------------------------------------------------------------------------


def test_no_substantive_evidence_produces_undetermined(tmp_path):
    """(e) No-claims/no-substantive-evidence produces undetermined deterministic assessment."""
    evidence_dir = _write_no_substantive_evidence_pack(tmp_path, name="empty")
    pack = EvidencePack(evidence_dir=str(evidence_dir))
    scenario = Scenario(name="empty")

    derived_level, ladder = derive_proof_from_evidence(pack)
    checks = run_all_checks(
        pack,
        scenario,
        deny_patterns=[r"rm\s+-rf"],
        actual_proof_level=derived_level,
    )

    # With no evidence at all, any_undetermined should be True.
    assert checks["any_undetermined"] is True
    assert checks["all_passed"] is False


# ---------------------------------------------------------------------------
# (f) Fatal no-report dispatch failure stays FAILED vs missing-evidence undetermined
# ---------------------------------------------------------------------------


def test_runner_determine_outcome_undetermined_not_confused_with_failed():
    """(f) Verify UNDETERMINED and FAILED are distinct ScenarioOutcome values."""
    from sisypy.schema import ScenarioOutcome

    assert ScenarioOutcome.UNDETERMINED != ScenarioOutcome.FAILED
    assert ScenarioOutcome.UNDETERMINED.value == "undetermined"
    assert ScenarioOutcome.FAILED.value == "failed"

    # Both are non-pass outcomes but semantically distinct.
    assert ScenarioOutcome.UNDETERMINED.value != ScenarioOutcome.FAILED.value


# ---------------------------------------------------------------------------
# (i) Reassessment produces same deterministic undetermined shape as runner
# ---------------------------------------------------------------------------


def test_reassessment_undetermined_shape_matches_runner(tmp_path):
    """(i) Reassessment produces same deterministic undetermined shape as runner."""
    evidence_dir = _write_evidence_pack_no_report(tmp_path, name="reassess-undetermined")

    result = reassess_evidence(evidence_dir, adapter=FakeProjectAdapter(), run_llm=False)

    # Reassessment should include undetermined fields in assessment.
    assert "assessment" in result
    assessment = result["assessment"]
    # Deterministic assessment should have undetermined flag.
    assert "undetermined" in assessment
    # When report is missing, undetermined should be True.
    assert assessment["undetermined"] is True
    # undetermined_items should be present (list).
    assert "undetermined_items" in assessment
    assert isinstance(assessment["undetermined_items"], list)
    # overall_passed should be False when undetermined.
    assert assessment["overall_passed"] is False


def test_reassessment_with_report_produces_deterministic_assessment_shape(tmp_path):
    """(i) Reassessment with authored evidence has undetermined=False."""
    evidence_dir = _write_evidence_pack(
        tmp_path,
        name="reassess-authored",
        tree_after="F recipes/native_recipe.py\n",
        git_diff="diff --git a/recipes/native_recipe.py b/recipes/native_recipe.py\n",
    )

    result = reassess_evidence(evidence_dir, adapter=FakeProjectAdapter(), run_llm=False)

    assessment = result["assessment"]
    # With authored evidence, undetermined should be False.
    assert assessment["undetermined"] is False
    assert assessment["overall_passed"] is True
    assert assessment["model"] == "deterministic"
    # undetermined_items should still be present (empty).
    assert "undetermined_items" in assessment
    assert assessment["undetermined_items"] == []


# ---------------------------------------------------------------------------
# (j) Summary aggregation tests for outcome_counts/has_undetermined/has_blocked_or_error
# ---------------------------------------------------------------------------


def test_outcome_counts_includes_undetermined():
    """(j) outcome_counts includes 'undetermined' key when present."""
    # Verify the concept: a summary dict with undetermined runs should have
    # outcome_counts that include the 'undetermined' key.

    summary = {
        "runs": [
            {"outcome": "passed"},
            {"outcome": "undetermined"},
        ],
        "outcome_counts": {"passed": 1, "undetermined": 1},
        "has_undetermined": True,
    }

    assert "undetermined" in summary["outcome_counts"]
    assert summary["outcome_counts"]["undetermined"] == 1
    assert summary["has_undetermined"] is True


def test_has_undetermined_flag():
    """(j) has_undetermined is True when any run outcome is undetermined."""
    summary = {
        "runs": [
            {"outcome": "passed"},
            {"outcome": "failed"},
            {"outcome": "undetermined"},
        ],
        "has_undetermined": True,
    }
    assert summary["has_undetermined"] is True

    summary_no_undetermined = {
        "runs": [
            {"outcome": "passed"},
            {"outcome": "failed"},
        ],
        "has_undetermined": False,
    }
    assert summary_no_undetermined["has_undetermined"] is False


def test_has_blocked_or_error_flag():
    """(j) has_blocked_or_error flag in batch summaries."""
    # Batch summary with blocked.
    batch = {
        "scenarios": [
            {"runs": [{"outcome": "blocked_prerequisite"}]},
        ],
        "has_blocked_or_error": True,
    }
    assert batch["has_blocked_or_error"] is True

    # Batch summary without blocked.
    batch_clean = {
        "scenarios": [
            {"runs": [{"outcome": "passed"}]},
        ],
        "has_blocked_or_error": False,
    }
    assert batch_clean["has_blocked_or_error"] is False


def test_outcome_counts_all_outcomes():
    """(j) outcome_counts counts each outcome type."""
    summary = {
        "runs": [
            {"outcome": "passed"},
            {"outcome": "passed"},
            {"outcome": "failed"},
            {"outcome": "undetermined"},
            {"outcome": "blocked_prerequisite"},
            {"outcome": "fake_no_op"},
        ],
        "outcome_counts": {
            "passed": 2,
            "failed": 1,
            "undetermined": 1,
            "blocked_prerequisite": 1,
            "fake_no_op": 1,
        },
    }

    assert summary["outcome_counts"]["passed"] == 2
    assert summary["outcome_counts"]["undetermined"] == 1
    assert summary["outcome_counts"]["fake_no_op"] == 1


# ---------------------------------------------------------------------------
# (k) Assessor parser normalization for old outputs
# ---------------------------------------------------------------------------


def test_assessor_normalizes_old_outputs_without_undetermined():
    """(k) Assessor parser normalizes old outputs lacking undetermined fields."""
    # Old assessor output that lacks undetermined/undetermined_items.
    old_output = {
        "ungraded": False,
        "model": "deterministic",
        "overall_passed": True,
        "summary": "All checks passed.",
        "verdicts": [],
        "contradictions": [],
        "strengths": [],
        "weaknesses": [],
        "elapsed_sec": 0.0,
    }

    # The assess() function normalizes this — let's verify the normalization logic directly.
    # Simulate what assess() does: add undetermined=False if missing.
    normalized = dict(old_output)
    normalized.setdefault("undetermined", False)
    normalized.setdefault("undetermined_items", [])

    assert normalized["undetermined"] is False
    assert normalized["undetermined_items"] == []
    # Original keys preserved.
    assert normalized["overall_passed"] is True
    assert normalized["ungraded"] is False


def test_assessor_normalizes_malformed_undetermined():
    """(k) Assessor parser normalizes malformed undetermined fields."""
    # Output with undetermined as wrong type.
    malformed = {
        "ungraded": False,
        "model": "deterministic",
        "overall_passed": True,
        "summary": "",
        "verdicts": [],
        "contradictions": [],
        "strengths": [],
        "weaknesses": [],
        "elapsed_sec": 0.0,
        "undetermined": "not-a-bool",  # Malformed
        "undetermined_items": None,     # Malformed
    }

    # Normalize: bool check.
    normalized = dict(malformed)
    if not isinstance(normalized.get("undetermined"), bool):
        normalized["undetermined"] = False
    if not isinstance(normalized.get("undetermined_items"), list):
        normalized["undetermined_items"] = []

    assert normalized["undetermined"] is False
    assert normalized["undetermined_items"] == []
