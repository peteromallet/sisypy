"""
pattern_finder.py — synthesize cross-scenario reports from completed run summaries.

Takes a collection of scenario summaries (each the product of a full runner
lifecycle: dispatch → capture → checks → assess) and produces a cross-scenario
synthesis report.  The report highlights:

  - Repeated failures: scenarios that fail consistently across actors/models.
  - Forbidden actions: scenarios that triggered forbidden-command violations.
  - Proof-level gaps: scenarios where actor claims exceeded evidence.
  - Assessor disagreements: scenarios where cross-assessor diff showed flips.
  - Project-specific patterns: synthesized from adapter-supplied categories.

Supports both an LLM-driven synthesis path and a deterministic fallback.

Parameterized: project adapters supply custom aggregation categories via the
*categories* parameter.  Contains zero hardcoded project imports.
"""

from __future__ import annotations

import json
from typing import Any

from sisypy.schema import Scenario

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_SUMMARY_LEN_FOR_LLM = 2000

# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------


def _collect_summaries(
    run_results: list[dict[str, Any]],
    *,
    categories: list[str] | None = None,
) -> dict[str, Any]:
    """Collect structured metrics from a list of scenario-level run summaries.

    Each *run_result* dict is expected to contain at minimum:
        - scenario_name: str
        - outcome: str  (one of passed / failed / blocked_prerequisite / etc.)
        - success_proof_level: str
        - universal_checks: dict (from run_all_checks)
        - assessment: dict (from assess())
        - cross_assessor_diff: dict or None (from run_diff())

    Returns a collection dict with aggregated stats grouped by category.
    """
    cats = categories or _default_aggregation_categories()
    n = len(run_results)

    # --- basic counts ---
    outcome_counts: dict[str, int] = {}
    proof_level_counts: dict[str, int] = {}
    repeated_failures: list[str] = []
    undetermined_scenarios: list[str] = []
    evidence_uncertainty: list[dict[str, Any]] = []
    forbidden_violations: list[dict[str, Any]] = []
    proof_gaps: list[dict[str, Any]] = []
    assessor_disagreements: list[dict[str, Any]] = []

    for rr in run_results:
        name = rr.get("scenario_name", "?")
        outcome = rr.get("outcome", "?")
        proof = rr.get("success_proof_level", "?")

        outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
        proof_level_counts[proof] = proof_level_counts.get(proof, 0) + 1

        # Repeated failures — explicitly exclude undetermined.
        if outcome in ("failed", "violation"):
            repeated_failures.append(name)

        # Undetermined / evidence uncertainty — separate from failures.
        if outcome == "undetermined":
            undetermined_scenarios.append(name)
            undetermined_items = rr.get("undetermined_items", [])
            capture_gaps = rr.get("capture_gaps", {})
            evidence_uncertainty.append({
                "scenario": name,
                "undetermined_items": undetermined_items,
                "capture_gaps": capture_gaps,
            })

        # Normalize universal_checks access: handle both nested dicts
        # ({checks: {...}}) and legacy flat dicts (key is check name).
        uc = rr.get("universal_checks", {})
        checks = uc.get("checks", uc) if isinstance(uc, dict) else {}

        # Forbidden actions.
        fc = checks.get("forbidden_commands", {}) if isinstance(checks, dict) else {}
        if isinstance(fc, dict) and not fc.get("passed", True):
            forbidden_violations.append({
                "scenario": name,
                "violations": fc.get("violations", []),
                "detail": fc.get("detail", ""),
            })

        # Proof-level gaps (contradictions or ladder failures).
        contrad = checks.get("contradictions", {}) if isinstance(checks, dict) else {}
        ladder = checks.get("success_proof_ladder", {}) if isinstance(checks, dict) else {}
        if isinstance(contrad, dict) and not contrad.get("passed", True):
            proof_gaps.append({
                "scenario": name,
                "type": "contradiction",
                "detail": contrad.get("detail", ""),
                "contradictions": contrad.get("contradictions", []),
            })
        if isinstance(ladder, dict) and not ladder.get("passed", True):
            proof_gaps.append({
                "scenario": name,
                "type": "ladder",
                "detail": ladder.get("detail", ""),
                "unsupported_claims": ladder.get("unsupported_claims", []),
            })

        # Assessor disagreements.
        diff = rr.get("cross_assessor_diff")
        if diff and isinstance(diff, dict):
            diff_data = diff.get("diff", {})
            if diff_data.get("overall_flip") or diff_data.get("enforced_flips") or diff_data.get("graded_deltas"):
                assessor_disagreements.append({
                    "scenario": name,
                    "overall_flip": diff_data.get("overall_flip", False),
                    "enforced_flips": len(diff_data.get("enforced_flips", [])),
                    "graded_deltas": len(diff_data.get("graded_deltas", [])),
                    "summary": diff_data.get("summary", ""),
                })

    return {
        "scenario_count": n,
        "outcome_counts": outcome_counts,
        "proof_level_counts": proof_level_counts,
        "repeated_failures": repeated_failures,
        "undetermined_scenarios": undetermined_scenarios,
        "undetermined_count": len(undetermined_scenarios),
        "evidence_uncertainty": evidence_uncertainty,
        "forbidden_violations": forbidden_violations,
        "proof_gaps": proof_gaps,
        "assessor_disagreements": assessor_disagreements,
        "categories": cats,
    }


# ---------------------------------------------------------------------------
# Deterministic synthesis
# ---------------------------------------------------------------------------


def _deterministic_synthesis(
    collection: dict[str, Any],
) -> dict[str, Any]:
    """Deterministic (non-LLM) cross-scenario synthesis.

    Produces structured findings from the collected metrics without any LLM call.
    """
    findings: list[dict[str, Any]] = []
    tiers: list[dict[str, Any]] = []

    # Tier 1 — structural (always reported).
    n = collection["scenario_count"]
    outcome = collection["outcome_counts"]
    passed = outcome.get("passed", 0)
    failed = outcome.get("failed", 0)
    violations = outcome.get("violation", 0)
    undetermined = collection.get("undetermined_count", outcome.get("undetermined", 0))

    tier1 = {
        "title": "Scenario outcome distribution",
        "body": (
            f"{n} scenario(s) evaluated. "
            f"Passed: {passed}, Failed: {failed}, Violations: {violations}, "
            f"Undetermined: {undetermined}, "
            f"Blocked: {outcome.get('blocked_prerequisite', 0)}, "
            f"Skipped: {outcome.get('skipped_live', 0)}."
        ),
        "severity": "error" if failed + violations > 0 else "warning" if undetermined > 0 else "ok",
    }
    tiers.append(tier1)

    # Proof level distribution.
    proof_levels = collection["proof_level_counts"]
    tier1b = {
        "title": "Success proof level distribution",
        "body": "; ".join(f"{k}: {v}" for k, v in sorted(proof_levels.items())),
        "severity": "ok",
    }
    tiers.append(tier1b)

    # Tier 2 — repeated failures.
    rf = collection["repeated_failures"]
    if rf:
        tiers.append({
            "title": "Repeated failures",
            "body": f"The following scenarios failed: {', '.join(rf)}.",
            "severity": "error",
        })
        findings.append({
            "category": "repeated_failures",
            "scenarios": rf,
            "count": len(rf),
        })

    # Tier 2 — forbidden actions.
    fv = collection["forbidden_violations"]
    if fv:
        fv_scenarios = [f["scenario"] for f in fv]
        tiers.append({
            "title": "Forbidden command violations",
            "body": (
                f"Found forbidden-command violations in {len(fv)} scenario(s): "
                f"{', '.join(fv_scenarios)}."
            ),
            "severity": "error",
        })
        findings.append({
            "category": "forbidden_actions",
            "scenarios": fv_scenarios,
            "details": fv,
        })

    # Tier 3 — proof-level gaps.
    pg = collection["proof_gaps"]
    if pg:
        pg_scenarios = list({p["scenario"] for p in pg})
        tiers.append({
            "title": "Proof-level gaps",
            "body": (
                f"Found proof-level gaps in {len(pg_scenarios)} scenario(s): "
                f"{', '.join(pg_scenarios)}."
            ),
            "severity": "warning",
        })
        findings.append({
            "category": "proof_gaps",
            "scenarios": pg_scenarios,
            "details": pg,
        })

    # Tier 3 — assessor disagreements.
    ad = collection["assessor_disagreements"]
    if ad:
        ad_scenarios = [a["scenario"] for a in ad]
        flips = sum(1 for a in ad if a["overall_flip"])
        tiers.append({
            "title": "Assessor disagreements",
            "body": (
                f"Found assessor disagreements in {len(ad)} scenario(s) "
                f"(including {flips} outcome flip(s)): "
                f"{', '.join(ad_scenarios)}."
            ),
            "severity": "warning" if flips > 0 else "ok",
        })
        findings.append({
            "category": "assessor_disagreements",
            "scenarios": ad_scenarios,
            "details": ad,
        })

    return {
        "synthesis_method": "deterministic",
        "tiers": tiers,
        "findings": findings,
        "categories_used": collection.get("categories", []),
    }


# ---------------------------------------------------------------------------
# LLM-assisted synthesis
# ---------------------------------------------------------------------------


def _trim_summary_for_prompt(run_result: dict[str, Any], max_len: int = _MAX_SUMMARY_LEN_FOR_LLM) -> dict[str, Any]:
    """Trim a single run result to fit within LLM prompt byte budgets.

    Returns a smaller dict with only the fields needed for pattern analysis.
    """
    assessment = run_result.get("assessment", {})
    trimmed_assessment = {
        "overall_passed": assessment.get("overall_passed"),
        "summary": assessment.get("summary", "")[:max_len],
        "contradictions": (assessment.get("contradictions") or [])[:10],
    }

    checks = run_result.get("universal_checks", {})
    trimmed_checks = {}
    for check_name in ("deliverable_shape", "forbidden_commands", "contradictions", "success_proof_ladder"):
        c = checks.get(check_name, {})
        if isinstance(c, dict):
            trimmed_checks[check_name] = {
                "passed": c.get("passed"),
                "severity": c.get("severity"),
                "detail": str(c.get("detail", ""))[:500],
            }

    return {
        "scenario_name": run_result.get("scenario_name", "?"),
        "outcome": run_result.get("outcome", "?"),
        "success_proof_level": run_result.get("success_proof_level", "?"),
        "assessment": trimmed_assessment,
        "universal_checks": trimmed_checks,
    }


def _llm_synthesis_prompt(collection: dict[str, Any], trimmed_results: list[dict[str, Any]]) -> str:
    """Build a prompt for the LLM-driven synthesis path."""
    stats = {
        "scenario_count": collection["scenario_count"],
        "outcome_counts": collection["outcome_counts"],
        "proof_level_counts": collection["proof_level_counts"],
        "repeated_failures": collection["repeated_failures"],
        "undetermined_count": collection.get("undetermined_count", 0),
        "undetermined_scenarios": collection.get("undetermined_scenarios", []),
        "evidence_uncertainty_count": len(collection.get("evidence_uncertainty", [])),
        "forbidden_violations_count": len(collection["forbidden_violations"]),
        "proof_gaps_count": len(collection["proof_gaps"]),
        "assessor_disagreements_count": len(collection["assessor_disagreements"]),
    }

    return json.dumps({
        "instruction": (
            "You are a pattern-finding analyst. Below are statistics and trimmed summaries "
            "from agentic test scenario runs. Identify cross-scenario patterns: "
            "repeated failures, undetermined/insufficient-evidence patterns, "
            "forbidden actions, proof-level gaps, assessor disagreements, "
            "and any systemic issues. "
            "Note: undetermined scenarios have insufficient evidence — treat them "
            "as evidence/capture gaps, NOT as product failures. "
            "Produce a structured JSON report."
        ),
        "statistics": stats,
        "scenarios": trimmed_results,
        "categories": collection.get("categories", []),
    }, indent=2, default=str)


def _parse_llm_synthesis(raw_response: str) -> dict[str, Any]:
    """Parse the LLM's synthesis response into structured findings."""
    try:
        return json.loads(raw_response)
    except json.JSONDecodeError:
        import re
        m = re.search(r"\{.*\}", raw_response, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    # Fallback: wrap the raw response.
    return {
        "synthesis_method": "llm (parse-failed)",
        "raw_response": raw_response[:2000],
        "findings": [],
    }


# ---------------------------------------------------------------------------
# Main synthesis entry point
# ---------------------------------------------------------------------------


def synthesize(
    run_results: list[dict[str, Any]],
    *,
    categories: list[str] | None = None,
    use_llm: bool = False,
    # LLM dispatch dependencies (only used when use_llm=True).
    api_key: str | None = None,
    model: str = "deepseek-v4-pro",
    base_url: str = "https://api.deepseek.com/v1",
) -> dict[str, Any]:
    """Produce a cross-scenario synthesis report.

    Args:
        run_results: List of per-scenario result dicts (from runner summary.json).
        categories: Project-adapter-supplied aggregation categories.
            If None, default categories are used.
        use_llm: If True, dispatch an LLM call for richer synthesis.
            If False or if the LLM call fails, a deterministic synthesis is used.
        api_key: DeepSeek API key (only needed when use_llm=True).
        model: Model identifier for LLM synthesis.
        base_url: API base URL for LLM synthesis.

    Returns:
        dict with keys:
            synthesis_method: "deterministic" or "llm"
            tiers: list of tier dicts (title, body, severity)
            findings: list of structured finding dicts
            categories_used: list of category names
            collection: raw collected metrics
    """
    cats = categories or _default_aggregation_categories()

    # 1. Collect metrics.
    collection = _collect_summaries(run_results, categories=cats)

    # 2. Try LLM if requested.
    if use_llm and api_key:
        try:
            trimmed = [_trim_summary_for_prompt(rr) for rr in run_results]
            prompt = _llm_synthesis_prompt(collection, trimmed)

            from sisypy.assessor import _call_with_retry
            system = (
                "You are an objective pattern analyst for an agentic testing harness. "
                "Identify cross-scenario patterns from the provided statistics and "
                "scenario summaries. Output a valid JSON object."
            )
            response, error = _call_with_retry(
                api_key, system, prompt,
                model=model, base_url=base_url,
                max_tokens=8000, temperature=0.0,
            )
            if not error and response:
                llm_result = _parse_llm_synthesis(response)
                llm_result["collection"] = collection
                llm_result["categories_used"] = cats
                return llm_result
        except Exception:
            pass  # fall through to deterministic.

    # 3. Deterministic fallback.
    det_result = _deterministic_synthesis(collection)
    det_result["collection"] = collection
    det_result["categories_used"] = cats
    return det_result


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------


def format_synthesis_report(synthesis: dict[str, Any]) -> str:
    """Render a synthesis result as a markdown report.

    Args:
        synthesis: The dict returned by synthesize().

    Returns:
        Markdown string.
    """
    lines: list[str] = []
    lines.append("# Cross-Scenario Pattern Synthesis\n")
    lines.append(f"**Method:** {synthesis.get('synthesis_method', '?')}\n")
    lines.append(f"**Categories:** {', '.join(synthesis.get('categories_used', []))}\n")

    tiers = synthesis.get("tiers", [])
    for t in tiers:
        sev = t.get("severity", "ok")
        icon = {"error": "🔴", "warning": "🟡", "ok": "🟢"}.get(sev, "⚪")
        lines.append(f"## {icon} {t.get('title', '')}\n")
        lines.append(f"{t.get('body', '')}\n\n")

    findings = synthesis.get("findings", [])
    if findings:
        lines.append("## Detailed Findings\n\n")
        for f in findings:
            lines.append(f"### {f.get('category', '?')}\n")
            scenarios = f.get("scenarios", [])
            if scenarios:
                lines.append(f"Scenarios: {', '.join(scenarios)}\n\n")
            details = f.get("details", [])
            if details and isinstance(details, list):
                for d in details:
                    if isinstance(d, dict):
                        lines.append(f"- **{d.get('scenario', d.get('title', '?'))}**: {d.get('summary', d.get('detail', ''))}\n")
                lines.append("\n")

    # Stats from collection.
    collection = synthesis.get("collection", {})
    if collection:
        lines.append("## Collection Statistics\n\n")
        lines.append(f"- Total scenarios: {collection.get('scenario_count', 0)}\n")
        outcome_counts = collection.get("outcome_counts", {})
        lines.append(f"- Outcomes: {json.dumps(outcome_counts)}\n")
        proof_levels = collection.get("proof_level_counts", {})
        lines.append(f"- Proof levels: {json.dumps(proof_levels)}\n")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Default aggregation categories
# ---------------------------------------------------------------------------


def _default_aggregation_categories() -> list[str]:
    """Return the default set of cross-scenario aggregation categories.

    Project adapters can override these via the *categories* parameter of
    synthesize().
    """
    return [
        "repeated_failures",
        "forbidden_actions",
        "proof_level_gaps",
        "assessor_disagreements",
        "structural_violations",
        "live_blocked_prerequisites",
        "success_proof_ladder_distribution",
    ]
