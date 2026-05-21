"""
schema.py — typed dataclasses for the shared Sisypy.

These types define the contract between the harness runner, dispatchers,
evidence capture, universal checks, assessor, and project adapters.

All types are plain dataclasses with no project-specific imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class RunMode(str, Enum):
    """Execution mode for agentic scenarios.

    STRUCTURAL: no-GPU, no model downloads, no custom-node installs.
        Discovery, authoring, compile/validate/doctor/port checks only.
    LIVE: explicit opt-in for local embedded or cloud execution.
        Requires CLI flags, env prerequisites, budget caps, timeouts.
    """

    STRUCTURAL = "structural"
    LIVE = "live"


class SuccessProofLevel(str, Enum):
    """Success proof ladder — what level of evidence a run has proven.

    Every scenario result must classify the highest evidence level reached.
    Structural mode can pass at VALIDATED.  Live mode should not pass below
    ARTIFACT_PROVEN unless the scenario expects a blocked/skipped result.

    Ladder (lowest → highest):
      authored         — agent wrote/modified a reusable artifact.
      compiled         — workflow materialized to API JSON.
      validated        — static/project validation passed.
      runtime_attempted — real runtime was launched.
      runtime_proven   — runtime queued/completed the workflow.
      artifact_proven  — expected media file exists and is plausible.
      quality_assessed — output quality was explicitly evaluated.
    """

    AUTHORED = "authored"
    COMPILED = "compiled"
    VALIDATED = "validated"
    RUNTIME_ATTEMPTED = "runtime_attempted"
    RUNTIME_PROVEN = "runtime_proven"
    ARTIFACT_PROVEN = "artifact_proven"
    QUALITY_ASSESSED = "quality_assessed"


class ScenarioOutcome(str, Enum):
    """Top-level result classification for a scenario run.

    PASSED              — scenario succeeded at the expected proof level.
    FAILED              — scenario failed (contradictions, missing artifacts, …).
    BLOCKED_PREREQUISITE — live / runtime prerequisite missing (e.g. API key).
    SKIPPED_LIVE        — live mode requested but prerequisites unsatisfied.
    VIOLATION           — structural mode attempted a forbidden live/cost action.
    FAKE_NO_OP          — plumbing/no-actor result (no real actor attempted).
    UNDETERMINED        — insufficient evidence to determine pass or fail.
    """

    PASSED = "passed"
    FAILED = "failed"
    BLOCKED_PREREQUISITE = "blocked_prerequisite"
    SKIPPED_LIVE = "skipped_live"
    VIOLATION = "violation"
    FAKE_NO_OP = "fake_no_op"
    """Plumbing/no-actor result: no real actor attempted the scenario.
    The fake dispatcher is a synchronous, deterministic no-op used to verify
    harness plumbing (prime, capture, checks, assess) works end-to-end.  A
    ``FAKE_NO_OP`` outcome is not a product failure — it explicitly signals
    that no actor was configured or dispatched against the scenario."""

    UNDETERMINED = "undetermined"
    """Insufficient evidence to determine whether the scenario passed or failed.
    This is a first-class outcome distinct from both PASSED and FAILED.
    The harness detected missing evidence (e.g. no report, no actions, no
    artifacts) that prevents a conclusive determination.  UNDETERMINED is
    not treated as success and carries a distinct exit code (2) for
    recurring/CI workflows."""


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class AgentSpec:
    """Specification for a single actor agent.

    Attributes:
        id: Unique slug for this agent within a scenario (e.g. "hermes-v4").
        model: Model identifier string (e.g. "deepseek-v4-pro").
        dispatcher: Dispatcher backend name ("fake", "hermes", "codex", …).
        config: Arbitrary key-value config forwarded to the dispatcher.
    """

    id: str
    model: str = ""
    dispatcher: str = "fake"
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class Assessment:
    """Hidden rubric for scenario assessment.

    These rubric sections are NEVER exposed to the actor.  The assessor
    reads them alongside the frozen evidence pack.

    enforced: hard pass/fail checks (contradictions, forbidden actions, …).
    graded:   scalar / categorical grading dimensions.
    observed: items to note in the report but that do not affect scoring.
    """

    enforced: list[str] = field(default_factory=list)
    graded: list[str] = field(default_factory=list)
    observed: list[str] = field(default_factory=list)


@dataclass
class Scenario:
    """A test scenario combining a user-shaped brief with a hidden rubric.

    Scenarios are typically loaded from YAML files.  The brief is rendered
    into a markdown prompt for the actor; the assessment rubric is kept
    hidden and given only to the assessor.

    Attributes:
        name: Unique scenario slug (e.g. "vague_controlnet_image").
        tier: Difficulty / risk tier (1=basic, 2=intermediate, 3=advanced).
        description: Human-readable one-liner.
        brief: User-shaped task prompt in markdown.
        mode: Default execution mode (structural or live).
        agents: Actor agent specifications for this scenario.
        budget: Cost / timeout budget constraints (live mode).
        priming: Workspace priming steps (adapter-defined).
        assessment: Hidden rubric (enforced, graded, observed).
        tags: Free-form tags for filtering / aggregation.
        extras: Project-specific extension fields (adapter-consumed).
    """

    name: str
    tier: int = 1
    description: str = ""
    brief: str = ""
    mode: RunMode = RunMode.STRUCTURAL
    agents: list[AgentSpec] = field(default_factory=list)
    budget: dict[str, Any] = field(default_factory=dict)
    priming: list[str] = field(default_factory=list)
    assessment: Assessment = field(default_factory=Assessment)
    tags: list[str] = field(default_factory=list)
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class ActorRun:
    """Record of a single actor dispatch within a scenario.

    An ActorRun tracks one invocation of one agent against one scenario.
    Multiple ActorRuns per scenario are expected (cross-model comparison,
    fake-actor baseline, etc.).

    Attributes:
        id: Unique run id (e.g. "<scenario>-<agent>-<timestamp>").
        scenario_name: Scenario this run belongs to.
        agent_id: AgentSpec.id of the dispatched agent.
        mode: Execution mode for this run.
        dispatcher: Resolved dispatcher name.
        tag: Human-readable label for report grouping.
        started_at: ISO-8601 start timestamp.
        finished_at: ISO-8601 finish timestamp.
        outcome: ScenarioOutcome value (set after assessment).
        success_proof_level: Highest SuccessProofLevel achieved.
        summary: One-line human-readable summary.
        errors: Non-fatal errors / warnings encountered.
    """

    id: str = ""
    scenario_name: str = ""
    agent_id: str = ""
    mode: RunMode = RunMode.STRUCTURAL
    dispatcher: str = "fake"
    tag: str = ""
    started_at: str = ""
    finished_at: str = ""
    outcome: str = ""
    success_proof_level: SuccessProofLevel = SuccessProofLevel.AUTHORED
    summary: str = ""
    errors: list[str] = field(default_factory=list)
    workdir: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class CommandAction:
    """A single command executed by the actor, captured as structured evidence.

    This is the primary action-evidence type for v1.  File / tool / artifact
    action types are future-compatible ``action_type`` values not yet implemented.

    Attributes:
        action_id: Unique id for this action within the run (e.g. "0001").
        action_type: Discriminator — always ``"command"`` in v1.
        command: Full command text.
        cwd: Working directory the command ran in.
        exit_code: Subprocess exit code (None if unknown).
        duration_sec: Wall-clock duration in seconds (None if unknown).
        stdout_path: Relative path to sidecar file with full stdout.
        stderr_path: Relative path to sidecar file with full stderr.
        stdout_preview: Truncated inline preview of stdout (first ~200 chars).
        stderr_preview: Truncated inline preview of stderr (first ~200 chars).
        source: Origin of this action record (e.g. "dispatcher", "stderr-parse").
        evidence_confidence: ``"high"`` | ``"low"`` | ``"unknown"``.
        metadata: Arbitrary extra fields (launcher tool, env, etc.).
    """

    action_id: str = ""
    action_type: str = "command"
    command: str = ""
    cwd: str = ""
    exit_code: int | None = None
    duration_sec: float | None = None
    stdout_path: str = ""
    stderr_path: str = ""
    stdout_preview: str = ""
    stderr_preview: str = ""
    source: str = ""
    evidence_confidence: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ActionLogEntry:
    """Wrapper for a single row in an action log (actions.jsonl).

    Adds sequencing and timestamp metadata around a CommandAction (or future
    action types).  Each line in actions.jsonl is a JSON-serialised
    ActionLogEntry.
    """

    seq: int = 0
    timestamp: str = ""
    action: CommandAction = field(default_factory=CommandAction)


@dataclass
class EvidencePack:
    """Frozen evidence pack captured after an actor run.

    The evidence pack is the assessor's **only** source of truth.  It must
    never read live repo state or actor narrative alone.

    Attributes:
        manifest: Metadata about the capture (scenario, agent, timestamps, …).
        evidence_dir: Absolute path to the evidence root directory.
        files: Mapping of logical artifact name → relative path within evidence_dir.
        capture_notes: Human-readable notes about what was captured or skipped.
    """

    manifest: dict[str, Any] = field(default_factory=dict)
    evidence_dir: str = ""
    files: dict[str, str] = field(default_factory=dict)
    capture_notes: list[str] = field(default_factory=list)
    capture_gaps: dict[str, Any] = field(default_factory=dict)
