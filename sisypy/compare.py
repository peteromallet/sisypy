"""
compare.py — cross-arm evidence-pack comparison for Sisypy.

Provides a public ``compare(pack_a, pack_b)`` helper that reads two frozen
evidence packs, recomputes deterministic checks from their frozen files, and
returns a structured verdict.

The compare helper is strictly conservative: it reads ONLY frozen evidence
files and never falls back to manifest ``success_proof_level`` to pick a
winner when ladder evidence is inconclusive.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sisypy.schema import EvidencePack, Scenario, SuccessProofLevel
from sisypy.evidence import load_evidence_pack
from sisypy.universal_checks import (
    _level_rank,
    check_forbidden_commands,
    check_success_proof_ladder,
)


def _load_pack(path_or_pack: str | Path | EvidencePack) -> EvidencePack:
    """Delegate to the public load_evidence_pack helper.

    Kept for backward compatibility within this module.
    """
    return load_evidence_pack(path_or_pack)


def compare(
    pack_a: str | Path | EvidencePack,
    pack_b: str | Path | EvidencePack,
) -> dict[str, Any]:
    """Compare two evidence packs and return a structured verdict.

    Reads ONLY frozen evidence files.  Never reads live repo state, scenario
    YAML, or any files outside the evidence pack directories.

    Comparison dimensions (v1):
      * Proof-level ladder evidence (from frozen tree_after, git_diff, etc.)
      * Forbidden-command violations (deny_patterns extracted from manifest)
      * Failed-check counts

    Tie-breaking (conservative):
      1. Higher proof level wins.
      2. Fewer forbidden-command violations wins.
      3. Fewer failed checks wins.
      4. Otherwise → tie with ``confidence='low'``.

    Args:
        pack_a: First evidence pack (directory path or EvidencePack).
        pack_b: Second evidence pack (directory path or EvidencePack).

    Returns:
        dict with keys:
            winning_arm: str  — 'a', 'b', or 'tie'
            confidence: str   — 'high', 'medium', 'low', or 'insufficient_evidence'
            proof_comparison: dict with per-arm derived levels and ranks
            forbidden_comparison: dict with per-arm violation counts
            failed_check_comparison: dict with per-arm failed-check counts
            evidence_paths: dict mapping 'a'/'b' → evidence_dir
            limitations: list of strings describing known limitations
    """
    ep_a = _load_pack(pack_a)
    ep_b = _load_pack(pack_b)

    # Evidence paths (caller-supplied).
    evidence_paths = {
        "a": ep_a.evidence_dir,
        "b": ep_b.evidence_dir,
    }

    limitations: list[str] = [
        "v1 uses only proof ladder and forbidden commands; "
        "project-specific checks are out of scope.",
        "String-based proof-level derivation from frozen files is "
        "conservative and may under-estimate evidence.",
    ]

    # --- Extract deny_patterns from frozen manifests ---
    deny_a: list[str] = []
    deny_b: list[str] = []
    try:
        cmd_policy_a = ep_a.manifest.get("command_policy", {}) or {}
        deny_a = list(cmd_policy_a.get("deny_patterns", []))
    except Exception:
        pass
    try:
        cmd_policy_b = ep_b.manifest.get("command_policy", {}) or {}
        deny_b = list(cmd_policy_b.get("deny_patterns", []))
    except Exception:
        pass

    # --- Run deterministic checks from frozen files ---
    scenario = Scenario(name="compare")  # minimal placeholder

    ladder_a = check_success_proof_ladder(ep_a, scenario, actual_proof_level="")
    ladder_b = check_success_proof_ladder(ep_b, scenario, actual_proof_level="")

    forbidden_a = check_forbidden_commands(ep_a, scenario, deny_patterns=deny_a)
    forbidden_b = check_forbidden_commands(ep_b, scenario, deny_patterns=deny_b)

    # --- Derive evidence-backed proof levels from ladder evidence ---
    def _derive_level(ladder_result: dict[str, Any]) -> str:
        """Derive the highest evidence-backed proof level from ladder check."""
        lev_ev = ladder_result.get("ladder_evidence", {})
        order = [
            SuccessProofLevel.AUTHORED.value,
            SuccessProofLevel.COMPILED.value,
            SuccessProofLevel.VALIDATED.value,
            SuccessProofLevel.RUNTIME_ATTEMPTED.value,
            SuccessProofLevel.RUNTIME_PROVEN.value,
            SuccessProofLevel.ARTIFACT_PROVEN.value,
            SuccessProofLevel.QUALITY_ASSESSED.value,
        ]
        best = SuccessProofLevel.AUTHORED.value
        for level in order:
            entry = lev_ev.get(level, {})
            if isinstance(entry, dict) and entry.get("found"):
                best = level
        return best

    level_a = _derive_level(ladder_a)
    level_b = _derive_level(ladder_b)
    rank_a = _level_rank(level_a)
    rank_b = _level_rank(level_b)

    # --- Count forbidden violations ---
    violations_a = len(forbidden_a.get("violations", []))
    violations_b = len(forbidden_b.get("violations", []))

    # --- Count failed checks ---
    def _failed_count(check_result: dict[str, Any]) -> int:
        return 0 if check_result.get("passed", True) else 1

    failed_a = (
        (0 if ladder_a.get("passed", True) else 1)
        + (0 if forbidden_a.get("passed", True) else 1)
    )
    failed_b = (
        (0 if ladder_b.get("passed", True) else 1)
        + (0 if forbidden_b.get("passed", True) else 1)
    )

    proof_comparison = {
        "a": {"level": level_a, "rank": rank_a},
        "b": {"level": level_b, "rank": rank_b},
    }

    forbidden_comparison = {
        "a": {"violations": violations_a, "passed": forbidden_a.get("passed", True)},
        "b": {"violations": violations_b, "passed": forbidden_b.get("passed", True)},
    }

    failed_check_comparison = {
        "a": {"failed_count": failed_a},
        "b": {"failed_count": failed_b},
    }

    # --- Tie-breaking ---
    winning_arm = "tie"
    confidence = "low"

    # If both packs have no evidence, return insufficient_evidence.
    if rank_a <= 0 and rank_b <= 0:
        return {
            "winning_arm": "tie",
            "confidence": "insufficient_evidence",
            "proof_comparison": proof_comparison,
            "forbidden_comparison": forbidden_comparison,
            "failed_check_comparison": failed_check_comparison,
            "evidence_paths": evidence_paths,
            "limitations": limitations + [
                "Neither pack contains enough ladder evidence to compare."
            ],
        }

    if rank_a > rank_b:
        winning_arm = "a"
        confidence = "high" if rank_a - rank_b >= 2 else "medium"
    elif rank_b > rank_a:
        winning_arm = "b"
        confidence = "high" if rank_b - rank_a >= 2 else "medium"
    elif violations_a < violations_b:
        winning_arm = "a"
        confidence = "medium"
    elif violations_b < violations_a:
        winning_arm = "b"
        confidence = "medium"
    elif failed_a < failed_b:
        winning_arm = "a"
        confidence = "low"
    elif failed_b < failed_a:
        winning_arm = "b"
        confidence = "low"
    else:
        winning_arm = "tie"
        confidence = "low"

    return {
        "winning_arm": winning_arm,
        "confidence": confidence,
        "proof_comparison": proof_comparison,
        "forbidden_comparison": forbidden_comparison,
        "failed_check_comparison": failed_check_comparison,
        "evidence_paths": evidence_paths,
        "limitations": limitations,
    }
