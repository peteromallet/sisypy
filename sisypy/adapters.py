"""
adapters.py — project adapter ABC and fake adapter for testing.

AgenticProjectAdapter is the abstract base class that every project
(VibeComfy, Astrid, etc.) must subclass.  It owns project-specific
semantics: fixture staging, command policy, repo mutation capture,
project-specific checks, live-mode prerequisites, and success classification.

FakeProjectAdapter provides no-op / sensible-default implementations of
every ABC method for use in unit tests and structural CI runs.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from sisypy.schema import (
    ActorRun,
    EvidencePack,
    Scenario,
    SuccessProofLevel,
)


# ---------------------------------------------------------------------------
# AgenticProjectAdapter — abstract base
# ---------------------------------------------------------------------------


class AgenticProjectAdapter(ABC):
    """Protocol that every project must implement to use the Sisypy.

    The harness calls these methods at well-defined points in the runner
    lifecycle.  Implementations own project semantics — the shared core
    never imports project-specific symbols.

    Required attributes (set by subclass __init__):
        name: str       — short project identifier (e.g. "vibecomfy").
        repo_root: Path — absolute path to the project repository root.
    """

    name: str
    repo_root: Path

    # -- interval capture capability -----------------------------------------
    # Adapters that cannot safely support concurrent filesystem reads during
    # dispatch should set this to False.

    supports_interval_capture: bool = True

    # -- environment --------------------------------------------------------

    @abstractmethod
    def build_env(self, scenario: Scenario, run: ActorRun) -> dict[str, str]:
        """Return environment variables for the actor subprocess.

        The adapter may strip dangerous keys (RUNPOD_API_KEY, cloud creds)
        in structural mode and inject project-required vars in live mode.

        Returns:
            dict of VAR=value strings to merge into the actor's environment.
        """
        ...

    # -- priming ------------------------------------------------------------

    @abstractmethod
    def prime(self, scenario: Scenario, run: ActorRun) -> None:
        """Prepare the workspace before the actor is dispatched.

        Typical responsibilities:
        - Stage fixture files from workflow_corpus/ or test fixtures.
        - Create run-specific work directories.
        - Write any project-level priming artifacts the actor may need.

        The actor sees ONLY the primed workspace + the rendered brief.
        Hidden rubrics and scenario YAML MUST NOT be visible to the actor.
        """
        ...

    # -- evidence capture ---------------------------------------------------

    @abstractmethod
    def capture(self, scenario: Scenario, run: ActorRun, evidence_dir: Path) -> None:
        """Capture project-specific evidence after the actor finishes.

        The harness already captures generic artifacts (brief, report,
        stdout, stderr, git diffs, tree listings).  This method adds
        project-specific files under evidence_dir/project_specific/.

        Typical VibeComfy captures:
        - ready_templates_diff.patch, workflow_corpus_diff.patch
        - recipes_tree.txt, scratchpads_tree.txt
        - validation/* (inspect, analyze, validate, doctor, port_check)
        - runtime/* (metadata, comfy log, runpod manifest)
        """
        ...

    # -- universal checks ---------------------------------------------------

    @abstractmethod
    def project_universal_checks(
        self, scenario: Scenario, evidence_dir: Path
    ) -> dict[str, Any]:
        """Run project-specific deterministic checks against captured evidence.

        These are pure functions over the evidence pack — no LLM calls,
        no live repo state.

        Returns:
            dict with keys like "ready_template_protection", "forbidden_actions",
            "model_downloads", "media_probes", etc.  Each value is a structured
            check result (typically a dict with "passed", "severity", "detail").
        """
        ...

    # -- bypass patterns ----------------------------------------------------

    @abstractmethod
    def canonical_bypass_patterns(self, scenario: Scenario) -> list[str]:
        """Return regex patterns that describe project-specific bypass/escape
        attempts the universal contradiction checker should scan for.

        Patterns are compiled by the harness and matched against actor
        stdout/stderr and command logs.
        """
        ...

    # -- success classification ----------------------------------------------

    @abstractmethod
    def classify_success(
        self, scenario: Scenario, evidence_pack: EvidencePack
    ) -> SuccessProofLevel:
        """Classify the highest success proof level achieved by this run.

        This method interprets project-specific evidence artifacts to
        determine whether the actor actually authored, compiled, validated,
        ran, produced artifacts, etc.
        """
        ...

    # -- live prerequisites -------------------------------------------------

    @abstractmethod
    def live_prerequisites(self, scenario: Scenario) -> dict[str, bool]:
        """Check whether all prerequisites for live execution are satisfied.

        Returns:
            dict mapping prerequisite name → satisfied (bool).
            Typical keys: "RUNPOD_API_KEY", "budget", "timeout",
            "runtime_packages", "gpu_available", etc.
        """
        ...

    # -- command policy -----------------------------------------------------

    @abstractmethod
    def command_policy(
        self, scenario: Scenario, run: ActorRun
    ) -> dict[str, Any]:
        """Return structural-mode command allow/deny lists and enforcement config.

        Returns:
            dict with keys:
                allow_patterns: list[str] — regex patterns for allowed commands.
                deny_patterns: list[str]  — regex patterns for forbidden commands.
                enforce: bool             — whether to actively block or just warn.
        """
        ...


# ---------------------------------------------------------------------------
# FakeProjectAdapter — no-op stub for tests
# ---------------------------------------------------------------------------


class FakeProjectAdapter(AgenticProjectAdapter):
    """No-op adapter that satisfies every ABC method with sensible defaults.

    Used in unit tests and CI runs that do not exercise real project surfaces.
    Every method returns empty/dummy data rather than raising NotImplementedError.
    """

    def __init__(self, name: str = "fake", repo_root: Path | None = None) -> None:
        """Initialise the fake adapter.

        Args:
            name: Short project identifier (default "fake").
            repo_root: Path to treat as repo root (default cwd).
        """
        self.name = name
        self.repo_root = Path(repo_root) if repo_root else Path.cwd()

    # -- environment --------------------------------------------------------

    def build_env(self, scenario: Scenario, run: ActorRun) -> dict[str, str]:
        """Return an empty environment — structural mode strips credentials."""
        return {}

    # -- priming ------------------------------------------------------------

    def prime(self, scenario: Scenario, run: ActorRun) -> None:
        """No-op: fake mode needs no workspace priming."""
        return

    # -- evidence capture ---------------------------------------------------

    def capture(self, scenario: Scenario, run: ActorRun, evidence_dir: Path) -> None:
        """No-op: fake mode captures no project-specific evidence."""
        evidence_dir.mkdir(parents=True, exist_ok=True)
        (evidence_dir / "project_specific").mkdir(parents=True, exist_ok=True)

    # -- universal checks ---------------------------------------------------

    def project_universal_checks(
        self, scenario: Scenario, evidence_dir: Path
    ) -> dict[str, Any]:
        """Return empty check results — fake mode has no project rules."""
        return {}

    # -- bypass patterns ----------------------------------------------------

    def canonical_bypass_patterns(self, scenario: Scenario) -> list[str]:
        """Return empty list — fake mode has no bypass patterns."""
        return []

    # -- success classification ----------------------------------------------

    def classify_success(
        self, scenario: Scenario, evidence_pack: EvidencePack
    ) -> SuccessProofLevel:
        """Return AUTHORED — fake mode produces authored artifacts by default."""
        return SuccessProofLevel.AUTHORED

    # -- live prerequisites -------------------------------------------------

    def live_prerequisites(self, scenario: Scenario) -> dict[str, bool]:
        """Return all prerequisites as satisfied in fake mode."""
        return {"RUNPOD_API_KEY": True, "budget": True, "timeout": True}

    # -- command policy -----------------------------------------------------

    def command_policy(
        self, scenario: Scenario, run: ActorRun
    ) -> dict[str, Any]:
        """Return permissive policy — fake mode blocks nothing."""
        return {
            "allow_patterns": [r".*"],
            "deny_patterns": [],
            "enforce": False,
        }
