"""
cross_assessor_diff.py — regrade a frozen evidence pack with a second model config
and diff the two verdict sets.

Ports the architecture from Astrid's cross_assessor_diff.py while removing all
Astrid-project-specific evidence assumptions.  This module is parameterized so
project adapters can supply custom aggregation category lists.

Key mechanics:
  1. Run a second assessment on an existing frozen evidence pack using a different
     model config (or the same model with different temperature/seed).
  2. Diff the two verdict sets: enforced flips, graded score deltas,
     contradiction agreement, overall_passed flips.
  3. Produce a structured diff report that is itself scorable by universal checks.

Contains zero hardcoded project imports or paths.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sisypy.assessor import assess
from sisypy.schema import EvidencePack, Scenario

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Default model config for the second assessment pass.
_SECOND_MODEL = "deepseek-v4-pro"
_SECOND_TEMPERATURE = 0.3  # slight non-zero temp to elicit disagreements

# ---------------------------------------------------------------------------
# Core: run a second assessment and diff
# ---------------------------------------------------------------------------


def run_diff(
    evidence_pack: EvidencePack,
    scenario: Scenario,
    *,
    first_assessment: dict[str, Any] | None = None,
    second_model: str = _SECOND_MODEL,
    second_temperature: float = _SECOND_TEMPERATURE,
    second_base_url: str = "https://api.deepseek.com/v1",
    second_max_tokens: int = 12000,
    # Aggregation categories supplied by the project adapter.
    aggregation_categories: list[str] | None = None,
) -> dict[str, Any]:
    """Regrade an existing frozen evidence pack with a second model config.

    If *first_assessment* is provided (from a prior assess() call), it is used
    directly.  Otherwise, the primary model is re-invoked fresh (same parameters
    as the default assessor).

    A second assessment is always run with *second_model* / *second_temperature*.

    Returns a structured diff dict:
        first_assessment: dict
        second_assessment: dict
        diff: dict with keys:
            overall_flip: bool — whether overall_passed differs.
            first_passed: bool
            second_passed: bool
            enforced_flips: list of dicts describing enforced verdict flips.
            graded_deltas: list of dicts with score changes.
            contradiction_agreement: float — Jaccard-ish agreement ratio.
            strengths_agreement: float
            weaknesses_agreement: float
            summary: str — one-line summary of the diff.
        model_first: str
        model_second: str
        aggregation_categories: list[str]
    """
    # --- 1. Obtain or produce first assessment ---
    if first_assessment is not None:
        a1 = dict(first_assessment)
        model_first = a1.get("model", "unknown")
    else:
        a1 = assess(evidence_pack, scenario)
        model_first = a1.get("model", "default")

    # --- 2. Run second assessment ---
    a2 = assess(
        evidence_pack,
        scenario,
        model=second_model,
        base_url=second_base_url,
        max_tokens=second_max_tokens,
        temperature=second_temperature,
    )
    model_second = a2.get("model", second_model)

    # --- 3. Diff ---
    diff = _diff_assessments(a1, a2)

    # --- 4. Assemble result ---
    categories = aggregation_categories or _default_aggregation_categories()

    return {
        "first_assessment": a1,
        "second_assessment": a2,
        "diff": diff,
        "model_first": model_first,
        "model_second": model_second,
        "aggregation_categories": categories,
    }


# ---------------------------------------------------------------------------
# Diff helpers
# ---------------------------------------------------------------------------


def _diff_assessments(
    a1: dict[str, Any], a2: dict[str, Any]
) -> dict[str, Any]:
    """Compute a structured diff between two assessment result dicts."""

    # --- overall flip ---
    first_passed = a1.get("overall_passed", False)
    second_passed = a2.get("overall_passed", False)
    overall_flip = first_passed != second_passed

    # --- enforced flips ---
    enforced_flips: list[dict[str, Any]] = []
    v1_enforced = _verdicts_map(a1, "enforced")
    v2_enforced = _verdicts_map(a2, "enforced")
    all_items = set(v1_enforced.keys()) | set(v2_enforced.keys())
    for item in sorted(all_items):
        p1 = v1_enforced.get(item)
        p2 = v2_enforced.get(item)
        if p1 != p2:
            enforced_flips.append({
                "item": item,
                "first_passed": p1,
                "second_passed": p2,
                "first_reasoning": _verdict_reasoning(a1, "enforced", item),
                "second_reasoning": _verdict_reasoning(a2, "enforced", item),
            })

    # --- graded deltas ---
    graded_deltas: list[dict[str, Any]] = []
    s1_graded = _score_map(a1)
    s2_graded = _score_map(a2)
    all_graded = set(s1_graded.keys()) | set(s2_graded.keys())
    for item in sorted(all_graded):
        s1 = s1_graded.get(item, 0)
        s2 = s2_graded.get(item, 0)
        if s1 != s2:
            graded_deltas.append({
                "item": item,
                "first_score": s1,
                "second_score": s2,
                "delta": s2 - s1,
            })

    # --- contradiction agreement (Jaccard-ish) ---
    c1 = set(a1.get("contradictions", []) or [])
    c2 = set(a2.get("contradictions", []) or [])
    if c1 or c2:
        contrad_agree = len(c1 & c2) / max(len(c1 | c2), 1)
    else:
        contrad_agree = 1.0

    # --- strengths/weaknesses agreement ---
    s1 = set(a1.get("strengths", []) or [])
    s2 = set(a2.get("strengths", []) or [])
    strengths_agree = len(s1 & s2) / max(len(s1 | s2), 1) if (s1 or s2) else 1.0

    w1 = set(a1.get("weaknesses", []) or [])
    w2 = set(a2.get("weaknesses", []) or [])
    weaknesses_agree = len(w1 & w2) / max(len(w1 | w2), 1) if (w1 or w2) else 1.0

    # --- summary ---
    if a1.get("ungraded") or a2.get("ungraded"):
        summary = "Cannot diff: one or both assessments are ungraded."
    elif overall_flip:
        summary = (
            f"OUTCOME FLIP: first={first_passed}, second={second_passed}. "
            f"{len(enforced_flips)} enforced disagreement(s), "
            f"{len(graded_deltas)} graded delta(s)."
        )
    elif enforced_flips or graded_deltas:
        summary = (
            f"Same overall outcome but {len(enforced_flips)} enforced disagreement(s) "
            f"and {len(graded_deltas)} graded delta(s)."
        )
    else:
        summary = "Full agreement between the two assessments."

    return {
        "overall_flip": overall_flip,
        "first_passed": first_passed,
        "second_passed": second_passed,
        "enforced_flips": enforced_flips,
        "graded_deltas": graded_deltas,
        "contradiction_agreement": round(contrad_agree, 3),
        "strengths_agreement": round(strengths_agree, 3),
        "weaknesses_agreement": round(weaknesses_agree, 3),
        "summary": summary,
    }


def _verdicts_map(
    assessment: dict[str, Any], section: str
) -> dict[str, bool | None]:
    """Return a mapping of item name → passed (or None if unknown)."""
    verdicts = assessment.get("verdicts", {})
    items = verdicts.get(section, []) if isinstance(verdicts, dict) else []
    result: dict[str, bool | None] = {}
    for v in items:
        if isinstance(v, dict) and "item" in v:
            result[v["item"]] = v.get("passed")
    return result


def _score_map(assessment: dict[str, Any]) -> dict[str, int]:
    """Return a mapping of graded item name → score (0–100)."""
    verdicts = assessment.get("verdicts", {})
    items = verdicts.get("graded", []) if isinstance(verdicts, dict) else []
    result: dict[str, int] = {}
    for v in items:
        if isinstance(v, dict) and "item" in v:
            result[v["item"]] = v.get("score", 0)
    return result


def _verdict_reasoning(
    assessment: dict[str, Any], section: str, item_name: str
) -> str:
    """Extract reasoning for a specific verdict item."""
    verdicts = assessment.get("verdicts", {})
    items = verdicts.get(section, []) if isinstance(verdicts, dict) else []
    for v in items:
        if isinstance(v, dict) and v.get("item") == item_name:
            return v.get("reasoning", "")
    return ""


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------


def format_diff_report(diff_result: dict[str, Any]) -> str:
    """Render a cross-assessor diff result as a markdown report.

    Args:
        diff_result: The dict returned by run_diff().

    Returns:
        Markdown string suitable for saving or display.
    """
    diff = diff_result.get("diff", {})
    lines: list[str] = []

    lines.append("# Cross-Assessor Diff Report\n")
    lines.append(
        f"**First model:** {diff_result.get('model_first', '?')}  \n"
        f"**Second model:** {diff_result.get('model_second', '?')}\n"
    )
    lines.append(f"**Summary:** {diff.get('summary', '')}\n")

    if diff.get("overall_flip"):
        lines.append(
            f"## ⚠️ Outcome Flip\n\n"
            f"- First assessment: **{'PASS' if diff['first_passed'] else 'FAIL'}**\n"
            f"- Second assessment: **{'PASS' if diff['second_passed'] else 'FAIL'}**\n\n"
        )

    # Enforced disagreements.
    flips = diff.get("enforced_flips", [])
    if flips:
        lines.append(f"## Enforced Disagreements ({len(flips)})\n")
        for f in flips:
            lines.append(
                f"### {f['item']}\n\n"
                f"- First: {'✅ passed' if f['first_passed'] else '❌ failed'} — {f['first_reasoning']}\n"
                f"- Second: {'✅ passed' if f['second_passed'] else '❌ failed'} — {f['second_reasoning']}\n\n"
            )

    # Graded deltas.
    deltas = diff.get("graded_deltas", [])
    if deltas:
        lines.append(f"## Graded Score Deltas ({len(deltas)})\n")
        for d in deltas:
            direction = "↑" if d["delta"] > 0 else "↓" if d["delta"] < 0 else "—"
            lines.append(
                f"- **{d['item']}**: {d['first_score']} → {d['second_score']} "
                f"({direction}{abs(d['delta'])})\n"
            )
        lines.append("")

    # Agreement metrics.
    lines.append("## Agreement Metrics\n\n")
    lines.append(f"- Contradiction agreement: {diff.get('contradiction_agreement', 0):.1%}\n")
    lines.append(f"- Strengths agreement: {diff.get('strengths_agreement', 0):.1%}\n")
    lines.append(f"- Weaknesses agreement: {diff.get('weaknesses_agreement', 0):.1%}\n")
    lines.append("")

    lines.append("## Aggregation Categories\n\n")
    for cat in diff_result.get("aggregation_categories", []):
        lines.append(f"- {cat}\n")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Default aggregation categories
# ---------------------------------------------------------------------------


def _default_aggregation_categories() -> list[str]:
    """Return the default set of cross-assessor aggregation categories.

    Project adapters can override these via the *aggregation_categories*
    parameter of run_diff().
    """
    return [
        "overall_outcome_flip",
        "enforced_disagreement",
        "graded_score_delta",
        "contradiction_overlap",
        "evidence_interpretation_gap",
        "model_bias_flag",
    ]
