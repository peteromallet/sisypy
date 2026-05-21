"""
sisypy — shared, project-neutral agentic evaluation.

One must imagine the test suite happy.

This package owns harness mechanics: scenario loading, brief rendering, actor
dispatch, run isolation, evidence-pack assembly, generic universal checks, LLM
assessment, cross-assessor diff, and pattern synthesis.

Project adapters (via AgenticProjectAdapter ABC) own project semantics:
fixture staging, allowed/forbidden commands, repo mutation capture,
project-specific graph/runtime checks, and live-mode evidence.

The shared core MUST NOT import from project packages (VibeComfy, Astrid, etc.).
"""

from sisypy.schema import (
    ActorRun,
    AgentSpec,
    Assessment,
    EvidencePack,
    RunMode,
    Scenario,
    ScenarioOutcome,
    SuccessProofLevel,
)
from sisypy.adapters import (
    AgenticProjectAdapter,
    FakeProjectAdapter,
)
from sisypy.dispatch import (
    ActorDispatcher,
    ActorRunResult,
    FakeActorDispatcher,
    HermesDispatcher,
    SubagentLauncherDispatcher,
)
from sisypy.evidence import (
    capture_evidence,
)
from sisypy.universal_checks import (
    check_bypass_patterns,
    check_contradictions,
    check_deliverable_shape,
    check_forbidden_commands,
    check_success_proof_ladder,
    run_all_checks,
)
from sisypy.assessor import (
    assess,
)
from sisypy.compare import (
    compare,
)
from sisypy.cross_assessor_diff import (
    format_diff_report,
    run_diff,
)
from sisypy.pattern_finder import (
    format_synthesis_report,
    synthesize,
)
from sisypy.public_api import (
    Boulder,
    BoulderError,
    Hill,
    Push,
    absurd,
    eternal,
    push,
    summit,
    cli,
    console_cli,
    summary_exit_code,
    build_cli_parser,
    run_from_args,
)

_RUNNER_EXPORTS = {
    "_load_scenario",
    "_load_scenarios_from_dir",
    "_render_brief",
    "_filter_scenarios",
    "run_scenario",
    "run_all",
}


def __getattr__(name: str):
    if name in _RUNNER_EXPORTS:
        from importlib import import_module

        runner = import_module("sisypy.runner")
        return getattr(runner, name)
    raise AttributeError(f"module 'sisypy' has no attribute {name!r}")

__all__ = [
    # Schema
    "ActorRun",
    "AgentSpec",
    "Assessment",
    "EvidencePack",
    "RunMode",
    "Scenario",
    "ScenarioOutcome",
    "SuccessProofLevel",
    # Public naming layer
    "Boulder",
    "Hill",
    "Push",
    "BoulderError",
    "summit",
    "eternal",
    "absurd",
    "push",
    # Public CLI helpers
    "cli",
    "console_cli",
    "summary_exit_code",
    "build_cli_parser",
    "run_from_args",
    # Adapters
    "AgenticProjectAdapter",
    "FakeProjectAdapter",
    # Dispatch
    "ActorDispatcher",
    "ActorRunResult",
    "FakeActorDispatcher",
    "HermesDispatcher",
    "SubagentLauncherDispatcher",
    # Compare
    "compare",
    # Evidence
    "capture_evidence",
    # Universal checks
    "check_bypass_patterns",
    "check_contradictions",
    "check_deliverable_shape",
    "check_forbidden_commands",
    "check_success_proof_ladder",
    "run_all_checks",
    # Assessor
    "assess",
    # Cross-assessor diff
    "format_diff_report",
    "run_diff",
    # Pattern finder
    "format_synthesis_report",
    "synthesize",
    # Runner
    "_load_scenario",
    "_load_scenarios_from_dir",
    "_render_brief",
    "_filter_scenarios",
    "run_scenario",
    "run_all",
]
