"""
reassess.py — frozen-evidence reassessment for the Sisypy.

Provides ``reassess_evidence()``, which recomputes all deterministic checks
and proof levels from a frozen evidence pack **without re-running the actor**.

The function reads ONLY the frozen evidence files; it never reads live repo
state except through adapter code explicitly handed the frozen directory.

Public API::

    result = reassess_evidence(
        evidence_dir,
        scenario=optional_scenario,
        adapter=optional_adapter,
        run_llm=False,
    )
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sisypy.schema import EvidencePack, Scenario, SuccessProofLevel
from sisypy.evidence import load_evidence_pack
from sisypy.universal_checks import (
    derive_proof_from_evidence,
    run_all_checks,
)


def reassess_evidence(
    evidence_dir: str | Path,
    *,
    scenario: Scenario | None = None,
    adapter: Any = None,
    run_llm: bool = False,
) -> dict[str, Any]:
    """Recompute checks and proof level from a frozen evidence pack.

    Loads the evidence pack via ``load_evidence_pack()``, recomputes shared
    universal checks (via ``run_all_checks``), project-specific checks (when
    an *adapter* is supplied), the evidence-backed proof level (via
    ``derive_proof_from_evidence``), and a deterministic assessment.

    **Never** reads live repo state — all evidence comes from the frozen
    directory.  The original evidence pack is never mutated.

    Args:
        evidence_dir: Path to a frozen evidence directory (must contain
            ``manifest.json``).
        scenario: Optional live Scenario.  If omitted, a minimal scenario is
            built from manifest fields.  Note that reloading a live scenario
            from the repository can cause drift if the scenario YAML changed
            after the original run (Sprint 2 workspace isolation will freeze
            the full scenario in the manifest).
        adapter: Optional project adapter.  When supplied, project-specific
            checks (``project_universal_checks``) and success classification
            (``classify_success``) are included.
        run_llm: If True and an adapter is supplied, the LLM assessor is
            invoked.  Default False (deterministic-only reassessment).

    Returns:
        A machine-readable dict with keys:

        * ``evidence_dir`` — the evidence directory path (str).
        * ``derived_proof_level`` — evidence-backed proof level string.
        * ``evidence_confidence`` — ``"high"``, ``"low"``, or ``"unknown"``.
        * ``universal_checks`` — dict from ``run_all_checks()``.
        * ``project_checks`` — dict from adapter (or empty).
        * ``assessment`` — deterministic (or LLM) assessment dict.
    """
    dir_path = Path(evidence_dir)

    # 1. Load frozen evidence.
    try:
        pack = load_evidence_pack(dir_path)
    except FileNotFoundError:
        # Build a minimal EvidencePack for ad-hoc directories.
        manifest_path = dir_path / "manifest.json"
        manifest = {}
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        pack = EvidencePack(evidence_dir=str(dir_path), manifest=manifest)

    manifest = pack.manifest

    # 2. Resolve scenario.
    if scenario is None and manifest:
        scenario = Scenario(
            name=manifest.get("scenario_id", "reassessed"),
            mode=manifest.get("mode", "structural"),
            tier=manifest.get("tier", "unit"),
        )

    if scenario is None:
        scenario = Scenario(name="reassessed")

    # 3. Evidence confidence from manifest or action log.
    evidence_confidence = manifest.get("evidence_confidence", "unknown")
    if evidence_confidence == "unknown":
        # Attempt to derive from actions.
        actions_path = dir_path / "actions.jsonl"
        if actions_path.is_file():
            try:
                for line in actions_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    entry = json.loads(line)
                    action = entry.get("action", {})
                    if isinstance(action, dict):
                        conf = action.get("evidence_confidence", "")
                        if conf == "high":
                            evidence_confidence = "high"
                            break
                        elif conf == "low" and evidence_confidence != "high":
                            evidence_confidence = "low"
            except (json.JSONDecodeError, OSError):
                pass

    # 4. Derive proof level from evidence.
    derived_proof_level, _ladder_evidence = derive_proof_from_evidence(pack)

    # 5. Recompute shared universal checks.
    # Build deny/bypass patterns from adapter if available.
    bypass_patterns: list[str] = []
    deny_patterns: list[str] = []
    if adapter is not None:
        try:
            bypass_patterns = adapter.canonical_bypass_patterns(scenario)
        except Exception:
            pass
        try:
            policy = adapter.command_policy(scenario, None)
            if isinstance(policy, dict):
                deny_patterns = policy.get("deny_patterns", [])
        except Exception:
            pass

    universal_checks = run_all_checks(
        pack,
        scenario,
        bypass_patterns=bypass_patterns,
        deny_patterns=deny_patterns,
        actual_proof_level=derived_proof_level,
    )

    # 6. Project-specific checks (when adapter is supplied).
    project_checks: dict[str, Any] = {}
    if adapter is not None:
        try:
            project_checks = adapter.project_universal_checks(scenario, dir_path)
            if not isinstance(project_checks, dict):
                project_checks = {}
        except Exception:
            pass

    # 7. Assessment.
    assessment: dict[str, Any]
    if run_llm and adapter is not None:
        try:
            from sisypy.assessor import assess
            assessment = assess(pack, scenario)
        except Exception:
            assessment = _deterministic_assessment(universal_checks)
    else:
        assessment = _deterministic_assessment(universal_checks)

    return {
        "evidence_dir": str(dir_path),
        "derived_proof_level": derived_proof_level,
        "evidence_confidence": evidence_confidence,
        "universal_checks": universal_checks,
        "project_checks": project_checks,
        "assessment": assessment,
    }


def _deterministic_assessment(all_checks: dict[str, Any]) -> dict[str, Any]:
    """Build a schema-complete assessor result without an LLM call."""
    passed = bool(all_checks.get("all_passed", False))
    checks = all_checks.get("checks", {})
    failed = [
        name
        for name, check in checks.items()
        if isinstance(check, dict) and not check.get("passed", False)
    ]
    undetermined_items: list[dict[str, str]] = [
        {"check_name": name, "detail": check.get("detail", "Undetermined")}
        for name, check in checks.items()
        if isinstance(check, dict) and check.get("undetermined", False)
    ]
    any_undetermined = len(undetermined_items) > 0

    # When any check is undetermined, overall_passed must be False
    # even if all_passed (non-undetermined checks) is True.
    overall_passed = passed and not any_undetermined

    return {
        "ungraded": False,
        "model": "deterministic",
        "overall_passed": overall_passed,
        "summary": (
            "Deterministic checks passed."
            if overall_passed
            else f"Deterministic checks failed: {', '.join(failed)}"
            if not any_undetermined
            else f"Insufficient evidence: {', '.join(item['check_name'] for item in undetermined_items)}"
        ),
        "verdicts": {
            "enforced": {
                "passed": overall_passed,
                "rationale": "Derived from deterministic universal checks.",
            }
        },
        "contradictions": [],
        "strengths": [],
        "weaknesses": failed,
        "elapsed_sec": 0.0,
        "undetermined": any_undetermined,
        "undetermined_items": undetermined_items,
    }
