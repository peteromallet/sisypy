"""
universal_checks.py — deterministic evidence-pack checks for the Sisypy.

Every function is a pure function over the frozen evidence pack + optional
adapter-supplied configuration.  No LLM calls.  No exceptions — every
check returns a plain dict on every path.

Ported patterns from Astrid's tests/agentic/universal_checks.py:
  - contradiction detection (narrative claim vs. evidence trace mismatch)
  - canonical-path bypass detection (parameterized via adapter)
  - deliverable-shape checks (report.md presence + numbered section coverage)
  - success-proof ladder checks (actor claims must not exceed captured evidence)

Additionally adds:
  - forbidden-command detection that reads adapter-provided deny-list patterns.
  - ClaimRule records with regex patterns and exclude_patterns for precise
    claim detection that avoids known false positives.
  - Section-aware report parsing that only scans claim-bearing sections.
  - Low-confidence proof capping for commands with evidence_confidence='low'.

All bypass patterns come from adapter.canonical_bypass_patterns(), never
hardcoded in this module.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sisypy.schema import EvidencePack, Scenario, SuccessProofLevel

# ---------------------------------------------------------------------------
# ClaimRule dataclass — precise claim detection
# ---------------------------------------------------------------------------


@dataclass
class ClaimRule:
    """A claim-matching rule with regex pattern and optional exclude patterns.

    Attributes:
        pattern: Regex pattern that indicates a claim at a given proof level.
        proof_level: The SuccessProofLevel this claim implies.
        exclude_patterns: Patterns that, if matched in the same section, negate
            the claim (e.g. 'regenerated' excludes a 'generated' artifact claim).
        section_only: If True, only match within claim-bearing sections of a
            markdown report.  If False (default), match in all evidence text.
    """

    pattern: str
    proof_level: SuccessProofLevel
    exclude_patterns: list[str] = field(default_factory=list)
    section_only: bool = False


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Legacy phrase-to-proof-level mapping kept for backward compatibility.
# New code should use _CLAIM_RULES instead.
# Exclusion patterns for legacy phrase matching.
# Maps a legacy phrase to a list of patterns that, if found in the same text,
# should suppress the match (to avoid false positives like "regenerated"→"generated").
_LEGACY_EXCLUDE_PATTERNS: dict[str, list[str]] = {
    "output file": [r"output filename prefix"],
    "output image": [r"output filename prefix"],
    "output video": [r"output filename prefix"],
    "output audio": [r"output filename prefix"],
    "generated": [r"regenerated"],
}


_CLAIM_TO_PROOF_LEVEL: dict[str, SuccessProofLevel] = {
    # authored-level claims
    "wrote a ": SuccessProofLevel.AUTHORED,
    "created a ": SuccessProofLevel.AUTHORED,
    "authored ": SuccessProofLevel.AUTHORED,
    "modified ": SuccessProofLevel.AUTHORED,
    # compiled-level claims
    "compiled ": SuccessProofLevel.COMPILED,
    "api json": SuccessProofLevel.COMPILED,
    "api.json": SuccessProofLevel.COMPILED,
    # validated-level claims
    "validated ": SuccessProofLevel.VALIDATED,
    "strict-ready": SuccessProofLevel.VALIDATED,
    # runtime claims
    "ran successfully": SuccessProofLevel.RUNTIME_PROVEN,
    "queued": SuccessProofLevel.RUNTIME_ATTEMPTED,
    "launched": SuccessProofLevel.RUNTIME_ATTEMPTED,
    "executed": SuccessProofLevel.RUNTIME_ATTEMPTED,
    # artifact claims
    "generated": SuccessProofLevel.ARTIFACT_PROVEN,
    "output file": SuccessProofLevel.ARTIFACT_PROVEN,
    "saved to": SuccessProofLevel.ARTIFACT_PROVEN,
    "rendered": SuccessProofLevel.ARTIFACT_PROVEN,
    "output image": SuccessProofLevel.ARTIFACT_PROVEN,
    "output video": SuccessProofLevel.ARTIFACT_PROVEN,
    "output audio": SuccessProofLevel.ARTIFACT_PROVEN,
    # quality claims
    "quality assessed": SuccessProofLevel.QUALITY_ASSESSED,
    "visually inspected": SuccessProofLevel.QUALITY_ASSESSED,
    "human review": SuccessProofLevel.QUALITY_ASSESSED,
}

# Claim-bearing section heading patterns (case-insensitive).
_CLAIM_BEARING_SECTIONS: list[str] = [
    r"what i did",
    r"what i changed",
    r"what i accomplished",
    r"evidence",
    r"results?",
    r"deliverables?",
    r"work done",
    r"actions? taken",
]

# Non-claim-bearing section heading patterns (case-insensitive).
# Claims in these sections are ignored.
_NON_CLAIM_SECTIONS: list[str] = [
    r"how to verify",
    r"open risks?",
    r"limitations?",
    r"next steps?",
    r"future work",
    r"caveats?",
    r"notes?",
    r"appendix",
]

# ClaimRule records — the authoritative claim-detection rules.
_CLAIM_RULES: list[ClaimRule] = [
    # --- authored-level claims ---
    ClaimRule(pattern=r"wrote\s+a\s+", proof_level=SuccessProofLevel.AUTHORED),
    ClaimRule(pattern=r"created\s+a\s+", proof_level=SuccessProofLevel.AUTHORED),
    ClaimRule(pattern=r"authored\s+", proof_level=SuccessProofLevel.AUTHORED),
    ClaimRule(pattern=r"modified\s+", proof_level=SuccessProofLevel.AUTHORED),

    # --- compiled-level claims ---
    ClaimRule(pattern=r"compiled\s+", proof_level=SuccessProofLevel.COMPILED),
    ClaimRule(pattern=r"api[\s._-]?json", proof_level=SuccessProofLevel.COMPILED),

    # --- validated-level claims ---
    ClaimRule(pattern=r"validated\s+", proof_level=SuccessProofLevel.VALIDATED),
    ClaimRule(pattern=r"strict[\s-]ready", proof_level=SuccessProofLevel.VALIDATED),

    # --- runtime claims ---
    ClaimRule(pattern=r"ran\s+successfully", proof_level=SuccessProofLevel.RUNTIME_PROVEN),
    ClaimRule(pattern=r"queued", proof_level=SuccessProofLevel.RUNTIME_ATTEMPTED),
    ClaimRule(pattern=r"launched", proof_level=SuccessProofLevel.RUNTIME_ATTEMPTED),
    ClaimRule(pattern=r"executed", proof_level=SuccessProofLevel.RUNTIME_ATTEMPTED),

    # --- artifact claims (with false-positive exclusions) ---
    ClaimRule(
        pattern=r"\bgenerated\b",
        proof_level=SuccessProofLevel.ARTIFACT_PROVEN,
        exclude_patterns=[
            r"regenerated",      # "regenerated" is not "generated"
            r"output filename prefix",  # "output filename prefix" is not an artifact claim
        ],
    ),
    ClaimRule(
        pattern=r"output\s+file",
        proof_level=SuccessProofLevel.ARTIFACT_PROVEN,
        exclude_patterns=[
            r"output filename prefix",  # narrative instruction, not artifact
        ],
    ),
    ClaimRule(pattern=r"saved\s+to", proof_level=SuccessProofLevel.ARTIFACT_PROVEN),
    ClaimRule(pattern=r"\brendered\b", proof_level=SuccessProofLevel.ARTIFACT_PROVEN),
    ClaimRule(
        pattern=r"output\s+image",
        proof_level=SuccessProofLevel.ARTIFACT_PROVEN,
        exclude_patterns=[
            r"output filename prefix",  # narrative instruction
        ],
    ),
    ClaimRule(
        pattern=r"output\s+video",
        proof_level=SuccessProofLevel.ARTIFACT_PROVEN,
        exclude_patterns=[
            r"output filename prefix",
        ],
    ),
    ClaimRule(
        pattern=r"output\s+audio",
        proof_level=SuccessProofLevel.ARTIFACT_PROVEN,
        exclude_patterns=[
            r"output filename prefix",
        ],
    ),

    # --- quality claims ---
    ClaimRule(pattern=r"quality\s+assessed", proof_level=SuccessProofLevel.QUALITY_ASSESSED),
    ClaimRule(pattern=r"visually\s+inspected", proof_level=SuccessProofLevel.QUALITY_ASSESSED),
    ClaimRule(pattern=r"human\s+review", proof_level=SuccessProofLevel.QUALITY_ASSESSED),
]

# Narrative model/cache paths that must NOT trigger forbidden-command or
# model-download detection.  These are explanatory prose mentions, not
# evidence of actual staging/download.
_NARRATIVE_MODEL_PATH_EXCLUSIONS: list[str] = [
    r"vendor/ComfyUI/models/",
    r"vendor/ComfyUI/custom_nodes/",
    r"\.safetensors\b",
    r"\.ckpt\b",
    r"\.pth\b",
    r"\.bin\b",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_evidence_file(evidence_dir: Path, filename: str) -> str:
    """Read a captured evidence file, returning '' on any error."""
    try:
        fp = evidence_dir / filename
        if not fp.is_file():
            return ""
        return fp.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _section_count(report_md: str) -> int:
    """Count numbered markdown sections (e.g. '## 1.', '## 2.')."""
    return len(re.findall(r"^#{1,6}\s+\d+\.", report_md, re.MULTILINE))


def _extract_claim_bearing_text(report_md: str) -> str:
    """Extract only claim-bearing sections from a markdown report.

    Sections with headings matching _CLAIM_BEARING_SECTIONS are included;
    sections with headings matching _NON_CLAIM_SECTIONS are excluded.
    Unmatched sections are included by default (conservative).
    """
    if not report_md.strip():
        return ""

    # Split report into sections by markdown headings.
    section_pattern = re.compile(r"^(#{1,6}\s+.+)$", re.MULTILINE)
    splits = section_pattern.split(report_md)

    if len(splits) <= 1:
        # No headings found — return full text.
        return report_md

    # splits[0] is text before first heading.
    # splits[1] is first heading, splits[2] is its content, etc.
    bearing_parts: list[str] = []
    # Include preamble text (before any heading) — conservative.
    if splits[0].strip():
        bearing_parts.append(splits[0])

    for i in range(1, len(splits), 2):
        if i + 1 >= len(splits):
            break
        heading = splits[i].strip().lower()
        content = splits[i + 1]

        # Remove the heading markers for matching.
        heading_text = re.sub(r"^#{1,6}\s+", "", heading).strip()

        # Check if this is a non-claim section.
        is_excluded = any(
            re.search(pat, heading_text, re.IGNORECASE)
            for pat in _NON_CLAIM_SECTIONS
        )
        if is_excluded:
            continue

        bearing_parts.append(content)

    return "\n".join(bearing_parts)


def _detect_claims_from_rules(text: str) -> dict[str, list[str]]:
    """Find supported-claim phrases using ClaimRule records.

    Each ClaimRule is checked against *text*. If the pattern matches and no
    exclude_pattern matches, the claim is recorded at its proof level.

    Returns a dict mapping proof level (str) → list of matched pattern strings.
    """
    found: dict[str, list[str]] = {}
    lower = text.lower()
    for rule in _CLAIM_RULES:
        try:
            if not re.search(rule.pattern, lower, re.IGNORECASE):
                continue
        except re.error:
            continue

        # Check exclude patterns.
        excluded = False
        for excl_pat in rule.exclude_patterns:
            try:
                if re.search(excl_pat, lower, re.IGNORECASE):
                    excluded = True
                    break
            except re.error:
                pass

        if excluded:
            continue

        found.setdefault(rule.proof_level.value, []).append(rule.pattern)

    return found


def _detect_claims(text: str) -> dict[str, list[str]]:
    """Find supported-claim phrases in *text* and group them by proof level.

    Uses the newer ClaimRule-based detection, falling back to legacy
    substring matching for backward compatibility.  Legacy matches are
    filtered through the same exclude-pattern logic to avoid known
    false positives (e.g. 'regenerated' matching 'generated').

    Returns a dict mapping proof level (str) → list of matched phrases.
    """
    # Primary: ClaimRule-based detection.
    rule_found = _detect_claims_from_rules(text)

    # Fallback: legacy substring detection, with exclude-pattern filtering.
    # Build a map of legacy phrase → ClaimRule (if one exists) so we can
    # apply the same exclude-pattern logic.
    rule_by_pattern: dict[str, ClaimRule] = {}
    for rule in _CLAIM_RULES:
        rule_by_pattern[rule.pattern] = rule

    legacy_found: dict[str, list[str]] = {}
    lower = text.lower()
    for phrase, level in _CLAIM_TO_PROOF_LEVEL.items():
        if phrase not in lower:
            continue

        # Check ClaimRule exclude patterns via rule_by_pattern.
        excluded = False
        for rule_pat, rule in rule_by_pattern.items():
            if phrase in rule_pat or rule_pat in phrase:
                for excl_pat in rule.exclude_patterns:
                    try:
                        if re.search(excl_pat, lower, re.IGNORECASE):
                            excluded = True
                            break
                    except re.error:
                        pass
            if excluded:
                break

        # Also check legacy exclude patterns.
        if not excluded and phrase in _LEGACY_EXCLUDE_PATTERNS:
            for excl_pat in _LEGACY_EXCLUDE_PATTERNS[phrase]:
                try:
                    if re.search(excl_pat, lower, re.IGNORECASE):
                        excluded = True
                        break
                except re.error:
                    pass

        if not excluded:
            legacy_found.setdefault(level.value, []).append(phrase)

    # Merge: rule_found takes precedence; add legacy only if not already in rule_found.
    merged: dict[str, list[str]] = dict(rule_found)
    for level, phrases in legacy_found.items():
        existing = merged.get(level, [])
        for p in phrases:
            if p not in existing:
                existing.append(p)
        merged[level] = existing

    return merged


def _highest_claimed_level(claims: dict[str, list[str]]) -> str:
    """Return the highest proof level claimed in *claims*.

    If no claims are detected, return the empty string.
    """
    order = [
        SuccessProofLevel.AUTHORED.value,
        SuccessProofLevel.COMPILED.value,
        SuccessProofLevel.VALIDATED.value,
        SuccessProofLevel.RUNTIME_ATTEMPTED.value,
        SuccessProofLevel.RUNTIME_PROVEN.value,
        SuccessProofLevel.ARTIFACT_PROVEN.value,
        SuccessProofLevel.QUALITY_ASSESSED.value,
    ]
    for level in reversed(order):
        if level in claims:
            return level
    return ""


def _level_rank(level: str) -> int:
    """Return a numeric rank for a proof level (higher = more evidence)."""
    ordering = {
        SuccessProofLevel.AUTHORED.value: 0,
        SuccessProofLevel.COMPILED.value: 1,
        SuccessProofLevel.VALIDATED.value: 2,
        SuccessProofLevel.RUNTIME_ATTEMPTED.value: 3,
        SuccessProofLevel.RUNTIME_PROVEN.value: 4,
        SuccessProofLevel.ARTIFACT_PROVEN.value: 5,
        SuccessProofLevel.QUALITY_ASSESSED.value: 6,
    }
    return ordering.get(level, -1)


def _load_actions(evidence_dir: Path) -> list[dict[str, Any]]:
    """Load actions.jsonl from the evidence directory, returning [] on any error."""
    actions_path = evidence_dir / "actions.jsonl"
    if not actions_path.is_file():
        return []
    try:
        actions: list[dict[str, Any]] = []
        for line in actions_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                actions.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return actions
    except Exception:
        return []


def _has_low_confidence_actions(actions: list[dict[str, Any]]) -> bool:
    """Check if any loaded action has evidence_confidence='low'."""
    for entry in actions:
        action = entry.get("action", {})
        if isinstance(action, dict):
            if action.get("evidence_confidence") == "low":
                return True
    return False


def _all_action_evidence_low_confidence(evidence_dir: Path) -> bool:
    """Check whether ALL available action evidence is low confidence.

    Reads actions.jsonl. Returns True if actions exist and ALL have
    evidence_confidence='low'. Returns False otherwise (including when
    no actions.jsonl exists).
    """
    actions = _load_actions(evidence_dir)
    if not actions:
        return False
    for entry in actions:
        action = entry.get("action", {})
        if isinstance(action, dict):
            if action.get("evidence_confidence") != "low":
                return False
    return True


# ---------------------------------------------------------------------------
# Helper: no-substantive-evidence detection
# ---------------------------------------------------------------------------


def _has_no_substantive_evidence(evidence_pack: EvidencePack) -> bool:
    """Check whether the frozen evidence pack contains zero substantive evidence.

    Reads ONLY frozen evidence files — no live repo or service state.  Returns
    True when ALL of the following are absent/empty:

    - No claims detected in report.md claim-bearing sections.
    - No actions.jsonl file (or empty content).
    - No command_log.jsonl file (or empty content).
    - No git_diff.patch file (or empty content).
    - No artifact-like entries in tree_after.txt (no F out/, F output/,
      F artifacts/, or media files like .png/.jpg/.mp4/.wav/.mp3).

    A True result means the evidence pack contains no substantive evidence
    at all — even authored-level indicators are missing.  This is stronger
    than a mere missing report; it means the entire pack is effectively empty.
    """
    evidence_dir = Path(evidence_pack.evidence_dir)

    # Check report.md for any claims.
    report_md = _read_evidence_file(evidence_dir, "report.md")
    if report_md:
        claim_text = _extract_claim_bearing_text(report_md)
        claims = _detect_claims(claim_text)
        if claims:
            return False

    # Check actions.jsonl.
    actions_text = _read_evidence_file(evidence_dir, "actions.jsonl")
    if actions_text.strip():
        return False

    # Check command_log.jsonl.
    cmd_log_text = _read_evidence_file(evidence_dir, "command_log.jsonl")
    if cmd_log_text.strip():
        return False

    # Check git_diff.patch.
    git_diff = _read_evidence_file(evidence_dir, "git_diff.patch")
    if git_diff.strip():
        return False

    # Check tree_after.txt for artifact-like entries.
    tree_after = _read_evidence_file(evidence_dir, "tree_after.txt")
    if tree_after.strip():
        if re.search(
            r"F out/|F output/|F artifacts/|\.png|\.jpg|\.mp4|\.wav|\.mp3",
            tree_after,
            re.IGNORECASE,
        ):
            return False

    return True


# ---------------------------------------------------------------------------
# Check: deliverable shape
# ---------------------------------------------------------------------------


def check_deliverable_shape(
    evidence_pack: EvidencePack,
    scenario: Scenario,
    *,
    min_sections: int = 2,
) -> dict[str, Any]:
    """Verify report.md exists and has sufficient numbered sections.

    Reads ONLY the frozen evidence pack (evidence_dir + files mapping).

    Returns a dict with these keys:
        passed: bool
        severity: str  — "undetermined", "error", "warning", or "ok"
        undetermined: bool — True when evidence is insufficient to conclude
        detail: str    — human-readable explanation of the finding.
        missing_report: bool
        section_count: int
        min_required: int
    """
    evidence_dir = Path(evidence_pack.evidence_dir)

    # Read report.md from the evidence pack.
    report_md = _read_evidence_file(evidence_dir, "report.md")

    if not report_md:
        return {
            "passed": False,
            "undetermined": True,
            "severity": "undetermined",
            "detail": "report.md is missing from the evidence pack — insufficient evidence to determine pass or fail.",
            "missing_report": True,
            "section_count": 0,
            "min_required": min_sections,
        }

    count = _section_count(report_md)

    if count < min_sections:
        return {
            "passed": False,
            "severity": "warning",
            "detail": (
                f"report.md present but has only {count} numbered section(s); "
                f"minimum required is {min_sections}."
            ),
            "missing_report": False,
            "section_count": count,
            "min_required": min_sections,
        }

    return {
        "passed": True,
        "severity": "ok",
        "detail": f"report.md present with {count} numbered sections.",
        "missing_report": False,
        "section_count": count,
        "min_required": min_sections,
    }


# ---------------------------------------------------------------------------
# Check: contradictions (claim vs. evidence)
# ---------------------------------------------------------------------------


def check_contradictions(
    evidence_pack: EvidencePack,
    scenario: Scenario,
    *,
    actual_proof_level: str = "",
) -> dict[str, Any]:
    """Detect unsupported narrative claims in the actor's output.

    Scans the claim-bearing sections of report.md (plus stdout/stderr) for
    phrases that imply a certain proof level, then compares the highest
    claimed level against *actual_proof_level* (the evidence-backed level).

    Returns:
        dict with keys:
            passed: bool
            severity: str
            detail: str
            claimed_levels: dict of level → list of matched phrases
            highest_claimed: str
            actual_level: str
            contradictions: list of contradiction descriptions
    """
    evidence_dir = Path(evidence_pack.evidence_dir)

    stdout = _read_evidence_file(evidence_dir, "stdout.log")
    stderr = _read_evidence_file(evidence_dir, "stderr.log")
    report_md = _read_evidence_file(evidence_dir, "report.md")

    # Extract only claim-bearing sections from the report.
    claim_text = _extract_claim_bearing_text(report_md)

    # Combine stdout + stderr + claim-bearing report sections.
    combined = f"{stdout}\n{stderr}\n{claim_text}"
    claims = _detect_claims(combined)
    highest_claimed = _highest_claimed_level(claims)

    contradictions: list[str] = []

    if highest_claimed and actual_proof_level:
        claimed_rank = _level_rank(highest_claimed)
        actual_rank = _level_rank(actual_proof_level)

        if claimed_rank > actual_rank:
            # Identify every level where claims exceed evidence.
            for level_name, phrases in claims.items():
                if _level_rank(level_name) > actual_rank:
                    contradictions.append(
                        f"Claims at '{level_name}' level ({', '.join(phrases)}) "
                        f"exceed actual evidence level '{actual_proof_level}'."
                    )

    # Also collect a full-report scan for informational purposes
    # (so callers can see what was excluded).
    full_combined = f"{stdout}\n{stderr}\n{report_md}"
    full_claims = _detect_claims(full_combined)

    if not contradictions and highest_claimed:
        return {
            "passed": True,
            "severity": "ok",
            "detail": "All narrative claims are supported by the evidence level.",
            "claimed_levels": claims,
            "full_claimed_levels": full_claims,
            "highest_claimed": highest_claimed,
            "actual_level": actual_proof_level,
            "contradictions": [],
        }

    if contradictions:
        return {
            "passed": False,
            "severity": "error",
            "detail": f"Found {len(contradictions)} unsupported claim(s).",
            "claimed_levels": claims,
            "full_claimed_levels": full_claims,
            "highest_claimed": highest_claimed,
            "actual_level": actual_proof_level,
            "contradictions": contradictions,
        }

    return {
        "passed": True,
        "severity": "ok",
        "detail": "No proof-level claims detected in actor output.",
        "claimed_levels": claims,
        "full_claimed_levels": full_claims,
        "highest_claimed": "",
        "actual_level": actual_proof_level,
        "contradictions": [],
    }


# ---------------------------------------------------------------------------
# Check: canonical-path bypass detection
# ---------------------------------------------------------------------------


def check_bypass_patterns(
    evidence_pack: EvidencePack,
    scenario: Scenario,
    *,
    bypass_patterns: list[str] | None = None,
) -> dict[str, Any]:
    """Detect bypass / canonical-path escape attempts in actor output.

    Patterns are compiled from *bypass_patterns* (supplied by the adapter via
    canonical_bypass_patterns()).  No patterns are hardcoded here.

    Returns:
        dict with keys:
            passed: bool
            severity: str
            detail: str
            matches: list of (pattern, matched_text) tuples
    """
    patterns = bypass_patterns or []
    if not patterns:
        return {
            "passed": True,
            "severity": "ok",
            "detail": "No bypass patterns configured — nothing to check.",
            "matches": [],
        }

    evidence_dir = Path(evidence_pack.evidence_dir)

    stdout = _read_evidence_file(evidence_dir, "stdout.log")
    stderr = _read_evidence_file(evidence_dir, "stderr.log")
    report_md = _read_evidence_file(evidence_dir, "report.md")

    # Use only claim-bearing sections for bypass detection in report.
    claim_text = _extract_claim_bearing_text(report_md)
    combined = f"{stdout}\n{stderr}\n{claim_text}"

    matches: list[dict[str, str]] = []
    for pat in patterns:
        try:
            for m in re.finditer(pat, combined, re.IGNORECASE | re.MULTILINE):
                snippet = m.group(0)[:200]
                matches.append({"pattern": pat, "match": snippet})
        except re.error:
            matches.append({"pattern": pat, "match": f"<invalid regex: {pat}>"})

    if matches:
        return {
            "passed": False,
            "severity": "warning",
            "detail": f"Found {len(matches)} bypass pattern match(es).",
            "matches": matches,
        }

    return {
        "passed": True,
        "severity": "ok",
        "detail": "No bypass pattern matches detected.",
        "matches": [],
    }


# ---------------------------------------------------------------------------
# Check: forbidden-command detection
# ---------------------------------------------------------------------------


def check_forbidden_commands(
    evidence_pack: EvidencePack,
    scenario: Scenario,
    *,
    deny_patterns: list[str] | None = None,
) -> dict[str, Any]:
    """Detect forbidden commands in executed command evidence.

    *deny_patterns* are regex patterns supplied by the adapter (typically
    via command_policy()['deny_patterns']).  They are matched against
    command_log.jsonl and launcher stderr terminal traces. Narrative report
    text is intentionally excluded so future-runtime instructions do not count
    as invocations.
    """
    patterns = deny_patterns or []
    if not patterns:
        return {
            "passed": True,
            "severity": "ok",
            "detail": "No deny-list patterns configured — nothing to check.",
            "violations": [],
        }

    evidence_dir = Path(evidence_pack.evidence_dir)

    # Collect text sources — intentionally exclude report.md.
    sources: list[tuple[str, str]] = [
        ("stderr.log", _read_evidence_file(evidence_dir, "stderr.log")),
        ("command_log.jsonl", _read_evidence_file(evidence_dir, "command_log.jsonl")),
    ]

    # Also scan actions.jsonl if available.
    actions_text = _read_evidence_file(evidence_dir, "actions.jsonl")
    if actions_text:
        sources.append(("actions.jsonl", actions_text))

    violations: list[dict[str, str]] = []
    for source_name, text in sources:
        if not text:
            continue
        for pat in patterns:
            try:
                for m in re.finditer(pat, text, re.IGNORECASE | re.MULTILINE):
                    snippet = m.group(0)[:200]
                    violations.append(
                        {"pattern": pat, "match": snippet, "source": source_name}
                    )
            except re.error:
                violations.append(
                    {
                        "pattern": pat,
                        "match": f"<invalid regex: {pat}>",
                        "source": source_name,
                    }
                )

    if violations:
        # Deduplicate by (pattern, match, source).
        seen: set[tuple[str, str, str]] = set()
        unique: list[dict[str, str]] = []
        for v in violations:
            key = (v["pattern"], v["match"], v["source"])
            if key not in seen:
                seen.add(key)
                unique.append(v)

        return {
            "passed": False,
            "severity": "error",
            "detail": f"Found {len(unique)} forbidden-command match(es).",
            "violations": unique,
        }

    return {
        "passed": True,
        "severity": "ok",
        "detail": "No forbidden commands detected.",
        "violations": [],
    }


# ---------------------------------------------------------------------------
# Shared proof derivation from evidence
# ---------------------------------------------------------------------------


def derive_proof_from_evidence(
    evidence_pack: EvidencePack,
) -> tuple[str, dict[str, dict[str, Any]]]:
    """Derive the highest evidence-backed proof level from a frozen evidence pack.

    This is the shared proof-derivation helper used by both the sisypy runner
    and VibeComfy adapter. It reads actions.jsonl (with legacy command_log.jsonl
    fallback), git_diff.patch, tree files, and project_specific artifacts.

    Low-confidence commands (evidence_confidence='low') are excluded from
    validated/runtime/artifact proof levels — they can only support
    forbidden-command detection.

    Returns:
        (derived_level_str, ladder_evidence_dict)
    """
    evidence_dir = Path(evidence_pack.evidence_dir)
    ladder_evidence: dict[str, dict[str, Any]] = {}

    # Read evidence files.
    git_diff = _read_evidence_file(evidence_dir, "git_diff.patch")
    tree_after = _read_evidence_file(evidence_dir, "tree_after.txt")
    stdout = _read_evidence_file(evidence_dir, "stdout.log")
    stderr = _read_evidence_file(evidence_dir, "stderr.log")
    cmd_log = _read_evidence_file(evidence_dir, "command_log.jsonl")
    ps_dir = evidence_dir / "project_specific"
    out_scratchpads_tree = _read_evidence_file(ps_dir, "out_scratchpads_tree.txt")

    # Load actions for low-confidence detection.
    actions = _load_actions(evidence_dir)
    all_low_confidence = _all_action_evidence_low_confidence(evidence_dir)

    # command_evidence_text is used for validated/runtime checks.
    # When all evidence is low confidence, restrict to forbidden detection only.
    actions_jsonl_text = _read_evidence_file(evidence_dir, "actions.jsonl")
    command_evidence = f"{stderr}\n{cmd_log}\n{actions_jsonl_text}"

    # authored: git diff or tree changes showing new/modified files.
    has_authored_evidence = bool(
        git_diff.strip()
        or "F recipes/" in tree_after
        or "F scratchpads/" in tree_after
    )
    ladder_evidence[SuccessProofLevel.AUTHORED.value] = {
        "found": has_authored_evidence,
        "sources": (
            ["git_diff.patch", "tree_after.txt"] if has_authored_evidence else []
        ),
    }

    # compiled: any mention of api.json in evidence.
    has_compiled = bool(
        re.search(
            r"api\.json",
            git_diff + tree_after + out_scratchpads_tree,
            re.IGNORECASE,
        )
    )
    ladder_evidence[SuccessProofLevel.COMPILED.value] = {
        "found": has_compiled,
        "sources": (
            ["git_diff.patch", "tree_after.txt", "project_specific/out_scratchpads_tree.txt"]
            if has_compiled else []
        ),
    }

    # validated: validate/doctor/port-check in command evidence.
    # Only valid when actions are not all low-confidence.
    has_validated = bool(
        re.search(
            r"(validate|doctor|port\.check|strict\.ready)", command_evidence, re.IGNORECASE
        )
    )
    if all_low_confidence:
        has_validated = False
    ladder_evidence[SuccessProofLevel.VALIDATED.value] = {
        "found": has_validated,
        "sources": (
            ["stderr.log", "command_log.jsonl", "actions.jsonl"] if has_validated else []
        ),
        "low_confidence_capped": all_low_confidence and bool(
            re.search(r"(validate|doctor|port\.check|strict\.ready)", command_evidence, re.IGNORECASE)
        ),
    }

    # runtime_attempted / runtime_proven: real runtime command or runtime artifacts.
    runtime_logs = ""
    runtime_dir = ps_dir / "runtime"
    if runtime_dir.is_dir():
        runtime_logs = "\n".join(
            _read_evidence_file(fp.parent, fp.name)
            for fp in runtime_dir.rglob("*") if fp.is_file()
        )
    runtime_evidence = f"{cmd_log}\n{runtime_logs}"

    has_runtime_attempted = bool(
        re.search(
            r"(?:python\s+-m\s+vibecomfy\.cli\s+run|vibecomfy\s+run|run_embedded_sync|queue_prompt)",
            runtime_evidence,
            re.IGNORECASE,
        )
    )
    if all_low_confidence:
        has_runtime_attempted = False

    has_runtime_proven = bool(
        runtime_logs.strip()
        and re.search(r"(completed|finished|success)", runtime_evidence, re.IGNORECASE)
    )
    if all_low_confidence:
        has_runtime_proven = False

    ladder_evidence[SuccessProofLevel.RUNTIME_ATTEMPTED.value] = {
        "found": has_runtime_attempted,
        "sources": (["stdout.log"] if has_runtime_attempted else []),
    }
    ladder_evidence[SuccessProofLevel.RUNTIME_PROVEN.value] = {
        "found": has_runtime_proven,
        "sources": (["stdout.log"] if has_runtime_proven else []),
    }

    # artifact_proven: output files present in tree_after.
    has_artifact = bool(
        re.search(
            r"F out/|F output/|F artifacts/|\.png|\.jpg|\.mp4|\.wav|\.mp3",
            tree_after,
            re.IGNORECASE,
        )
    )
    ladder_evidence[SuccessProofLevel.ARTIFACT_PROVEN.value] = {
        "found": has_artifact,
        "sources": (["tree_after.txt"] if has_artifact else []),
    }

    # quality_assessed: explicit quality review mention.
    has_quality = bool(re.search(r"(quality|assess|review|score)", runtime_logs, re.IGNORECASE))
    ladder_evidence[SuccessProofLevel.QUALITY_ASSESSED.value] = {
        "found": has_quality,
        "sources": (["stdout.log"] if has_quality else []),
    }

    # Derive the actual evidence-backed level.
    derived_level = SuccessProofLevel.AUTHORED.value
    for level in [
        SuccessProofLevel.COMPILED.value,
        SuccessProofLevel.VALIDATED.value,
        SuccessProofLevel.RUNTIME_ATTEMPTED.value,
        SuccessProofLevel.RUNTIME_PROVEN.value,
        SuccessProofLevel.ARTIFACT_PROVEN.value,
        SuccessProofLevel.QUALITY_ASSESSED.value,
    ]:
        if ladder_evidence[level]["found"]:
            derived_level = level

    return derived_level, ladder_evidence


# ---------------------------------------------------------------------------
# Check: success-proof ladder
# ---------------------------------------------------------------------------


def check_success_proof_ladder(
    evidence_pack: EvidencePack,
    scenario: Scenario,
    *,
    actual_proof_level: str = "",
) -> dict[str, Any]:
    """Verify that actor claims do not exceed the evidence-backed proof level.

    This is a wrapper around contradiction detection specifically focused
    on the success-proof ladder.  It also inspects the evidence pack for
    concrete artifacts that correspond to each rung of the ladder.

    Low-confidence action evidence (evidence_confidence='low') is capped at
    'authored' — commands with low confidence cannot prove validated or higher
    levels. They remain useful for forbidden-command detection only.
    """
    evidence_dir = Path(evidence_pack.evidence_dir)

    # Use the shared proof derivation.
    derived_level, ladder_evidence = derive_proof_from_evidence(evidence_pack)

    # Use the caller-supplied actual_proof_level if provided.
    evidence_level = actual_proof_level or derived_level

    # Detect claims from claim-bearing sections only.
    stdout = _read_evidence_file(evidence_dir, "stdout.log")
    stderr = _read_evidence_file(evidence_dir, "stderr.log")
    report_md = _read_evidence_file(evidence_dir, "report.md")
    claim_text = _extract_claim_bearing_text(report_md)
    combined = f"{stdout}\n{stderr}\n{claim_text}"
    claims = _detect_claims(combined)
    highest_claimed = _highest_claimed_level(claims)

    unsupported: list[str] = []
    if highest_claimed and evidence_level:
        claimed_rank = _level_rank(highest_claimed)
        actual_rank = _level_rank(evidence_level)
        if claimed_rank > actual_rank:
            for level_name, phrases in claims.items():
                if _level_rank(level_name) > actual_rank:
                    unsupported.append(
                        f"Actor claims '{level_name}' ({', '.join(phrases)}) but "
                        f"evidence only supports '{evidence_level}'."
                    )

    if unsupported:
        return {
            "passed": False,
            "severity": "error",
            "detail": f"Actor claims exceed evidence: {len(unsupported)} violation(s).",
            "actual_level": evidence_level,
            "derived_level": derived_level,
            "highest_claimed": highest_claimed,
            "unsupported_claims": unsupported,
            "ladder_evidence": ladder_evidence,
        }

    return {
        "passed": True,
        "severity": "ok",
        "detail": f"All actor claims are supported at evidence level '{evidence_level}'.",
        "actual_level": evidence_level,
        "derived_level": derived_level,
        "highest_claimed": highest_claimed,
        "unsupported_claims": [],
        "ladder_evidence": ladder_evidence,
    }


# ---------------------------------------------------------------------------
# Convenience: run all checks
# ---------------------------------------------------------------------------


def run_all_checks(
    evidence_pack: EvidencePack,
    scenario: Scenario,
    *,
    bypass_patterns: list[str] | None = None,
    deny_patterns: list[str] | None = None,
    actual_proof_level: str = "",
    min_report_sections: int = 2,
) -> dict[str, Any]:
    """Run every deterministic universal check and return a combined result.

    All checks are independent; a failure in one does not prevent the others
    from running, and no exception is ever raised.

    Returns:
        dict with keys:
            all_passed: bool — True if every check passed.
            any_undetermined: bool — True if any check is undetermined
                (insufficient evidence to conclude).
            checks: dict mapping check name → result dict.
    """
    checks: dict[str, Any] = {}

    # Deliverable shape.
    checks["deliverable_shape"] = check_deliverable_shape(
        evidence_pack, scenario, min_sections=min_report_sections
    )

    # Contradictions.
    checks["contradictions"] = check_contradictions(
        evidence_pack, scenario, actual_proof_level=actual_proof_level
    )

    # Bypass patterns.
    checks["bypass_patterns"] = check_bypass_patterns(
        evidence_pack, scenario, bypass_patterns=bypass_patterns
    )

    # Forbidden commands.
    checks["forbidden_commands"] = check_forbidden_commands(
        evidence_pack, scenario, deny_patterns=deny_patterns
    )

    # Success-proof ladder.
    checks["success_proof_ladder"] = check_success_proof_ladder(
        evidence_pack, scenario, actual_proof_level=actual_proof_level
    )

    all_passed = all(c.get("passed", False) for c in checks.values())
    any_undetermined = any(c.get("undetermined", False) for c in checks.values())

    return {
        "all_passed": all_passed,
        "any_undetermined": any_undetermined,
        "checks": checks,
    }
