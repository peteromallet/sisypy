"""Public naming layer for Sisypy.

This module introduces the Boulder/Hill/Push vocabulary without changing the
existing runner engine.  The names are intentionally thin aliases and helpers
until the fuller public API lands.

It also exposes stable public helpers for CLI integration so project runners
can embed Sisypy without importing private runner internals.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TypeVar

from sisypy.adapters import AgenticProjectAdapter
from sisypy.compare import compare
from sisypy.schema import ActorRun, AgentSpec, RunMode, Scenario, SuccessProofLevel

Boulder = Scenario
Push = ActorRun

_T = TypeVar("_T", bound=Callable[..., Any])


class BoulderError(AssertionError):
    """Raised when a Push did not reach the required proof level."""


@dataclass(frozen=True)
class Hill:
    """The repo/system/environment an agent climbs during a Sisypy run."""

    root: Path = Path(".")
    adapter: AgenticProjectAdapter | None = None
    config: dict[str, Any] = field(default_factory=dict)


_PROOF_ORDER = {
    SuccessProofLevel.AUTHORED: 0,
    SuccessProofLevel.COMPILED: 1,
    SuccessProofLevel.VALIDATED: 2,
    SuccessProofLevel.RUNTIME_ATTEMPTED: 3,
    SuccessProofLevel.RUNTIME_PROVEN: 4,
    SuccessProofLevel.ARTIFACT_PROVEN: 5,
    SuccessProofLevel.QUALITY_ASSESSED: 6,
}


def eternal(fn: _T) -> _T:
    """Mark a test function as part of the recurring Sisypy suite."""

    setattr(fn, "__sisypy_eternal__", True)
    return fn


def absurd(reason: str = "", **extra: Any) -> dict[str, Any]:
    """Return metadata for an intentionally futile/blocked boulder."""

    marker = {"futile": True}
    if reason:
        marker["reason"] = reason
    marker.update(extra)
    return marker


def push(
    agent: AgentSpec | str,
    test: Scenario | str,
    *,
    mode: RunMode | str | None = None,
    tag: str = "",
    workdir: str | Path | None = None,
) -> ActorRun:
    """Create a Push record from an agent and boulder.

    This is a conservative bridge to the current runner data model.  It does
    not dispatch an actor yet; use ``run_scenario`` or ``run_all`` for engine
    execution until the public API grows a real execution wrapper.
    """

    scenario_name = test.name if isinstance(test, Scenario) else str(test)
    scenario_mode = test.mode if isinstance(test, Scenario) else RunMode.STRUCTURAL
    resolved_mode = RunMode(mode) if mode is not None else scenario_mode

    if isinstance(agent, AgentSpec):
        agent_id = agent.id
        dispatcher = agent.dispatcher
    else:
        agent_id = str(agent)
        dispatcher = "fake"

    return ActorRun(
        scenario_name=scenario_name,
        agent_id=agent_id,
        mode=resolved_mode,
        dispatcher=dispatcher,
        tag=tag,
        workdir=str(workdir) if workdir is not None else None,
    )


def summit(
    attempt: ActorRun | dict[str, Any],
    *,
    min_level: SuccessProofLevel | str = SuccessProofLevel.VALIDATED,
) -> ActorRun | dict[str, Any]:
    """Assert that a Push reached at least ``min_level``."""

    required = SuccessProofLevel(min_level)
    if isinstance(attempt, ActorRun):
        level = attempt.success_proof_level
        outcome = attempt.outcome
        summary = attempt.summary
        undetermined_items: list[dict[str, Any]] = getattr(attempt, "undetermined_items", [])
        capture_gaps: dict[str, Any] = getattr(attempt, "capture_gaps", {})
    else:
        level = SuccessProofLevel(attempt.get("success_proof_level", "authored"))
        outcome = str(attempt.get("outcome", ""))
        summary = str(attempt.get("summary", ""))
        undetermined_items = attempt.get("undetermined_items", [])
        capture_gaps = attempt.get("capture_gaps", {})

    if outcome and outcome != "passed":
        msg_parts = []
        if outcome == "undetermined":
            msg_parts.append("undetermined (insufficient evidence)")
            if undetermined_items:
                item_names = [item.get("check_name", "?") for item in undetermined_items if isinstance(item, dict)]
                msg_parts.append(f"undetermined_items: {', '.join(item_names) if item_names else 'none'}")
            if capture_gaps:
                gap_keys = sorted(capture_gaps.keys())
                msg_parts.append(f"capture_gaps: {', '.join(gap_keys) if gap_keys else 'none'}")
            raise BoulderError("; ".join(msg_parts))
        else:
            raise BoulderError(summary or f"push outcome was {outcome}")
    if _PROOF_ORDER[level] < _PROOF_ORDER[required]:
        raise BoulderError(f"push reached {level.value}, below {required.value}")
    return attempt


# ---------------------------------------------------------------------------
# Public CLI helpers
# ---------------------------------------------------------------------------


def build_cli_parser(
    adapter: AgenticProjectAdapter,
    *,
    configure_parser: Callable[[argparse.ArgumentParser], None] | None = None,
) -> argparse.ArgumentParser:
    """Build the CLI argument parser for the Sisypy runner.

    This is a stable public wrapper around the private ``_build_arg_parser()``.
    Projects can customize the parser by passing a ``configure_parser`` callback
    that adds or overrides arguments.

    Args:
        adapter: Project-specific adapter instance (used for metadata only;
            the base parser is adapter-neutral).
        configure_parser: Optional callback ``f(parser)`` called after the base
            parser is built, so projects can add flags (e.g. ``--live-local``,
            ``--allow-downloads``) without importing private Sisypy internals.

    Returns:
        An ``argparse.ArgumentParser`` ready for ``parse_args()``.
    """
    from sisypy.runner import _build_arg_parser

    parser = _build_arg_parser()
    if configure_parser is not None:
        configure_parser(parser)
    return parser


def run_from_args(
    adapter: AgenticProjectAdapter,
    args: argparse.Namespace,
) -> dict[str, Any]:
    """Execute a Sisypy run from already-parsed CLI arguments.

    This is the stable public execution helper that maps an argparse Namespace
    to ``run_all()`` parameters.  Projects that want full control over argument
    parsing can call ``build_cli_parser()`` + ``parse_args()`` themselves, then
    pass the result here.

    Args:
        adapter: Project-specific adapter instance.
        args: Parsed argparse Namespace from ``build_cli_parser()``.

    Returns:
        Batch summary dict from ``run_all()``.
    """
    from sisypy.runner import run_all
    from sisypy.reassess import reassess_evidence
    import json

    # --reassess: skip everything and reassess frozen evidence.
    if getattr(args, "reassess", None):
        result = reassess_evidence(
            args.reassess,
            adapter=adapter,
            run_llm=False,
        )
        print(json.dumps(result, indent=2, default=str))
        return result

    # Parse variables.
    variables: dict[str, str] = {}
    if getattr(args, "var", None):
        for pair in args.var:
            if "=" in pair:
                k, v = pair.split("=", 1)
                variables[k] = v

    mode = RunMode(getattr(args, "mode", "structural"))
    use_parallel = getattr(args, "parallel", True) and not getattr(args, "no_parallel", False)

    # --verbose: install stderr progress callback so phase events are visible.
    progress_callback = None
    if getattr(args, "verbose", False):

        def _stderr_progress(event: dict[str, Any]) -> None:
            import json as _json
            print(_json.dumps(event, default=str), file=sys.stderr)

        progress_callback = _stderr_progress

    return run_all(
        adapter=adapter,
        scenarios_dir=Path(getattr(args, "scenarios_dir", "tests/agentic/scenarios")),
        briefs_dir=Path(getattr(args, "briefs_dir", "tests/agentic/briefs")),
        reports_root=Path(getattr(args, "reports_dir", "out/agentic/reports")),
        mode=mode,
        actor=getattr(args, "actor", "fake"),
        tag=getattr(args, "tag", "run"),
        names=getattr(args, "scenarios", None) or None,
        tags=getattr(args, "tags", None),
        variables=variables,
        dry_run=getattr(args, "dry_run", False),
        run_cross_diff=getattr(args, "cross_diff", False),
        parallel=use_parallel,
        capture_interval_sec=getattr(args, "capture_interval_sec", None),
        progress_callback=progress_callback,
    )


def load_scenario(path: str | Path) -> Scenario:
    """Load a Sisypy scenario YAML file through the public API."""

    from sisypy.runner import load_scenario as _load_public_scenario

    return _load_public_scenario(Path(path))


def load_scenarios_from_dir(
    scenarios_dir: str | Path,
    *,
    briefs_dir: str | Path | None = None,
) -> list[Scenario]:
    """Load Sisypy scenario YAML files through the public API."""

    from sisypy.runner import load_scenarios_from_dir as _load_public_scenarios_from_dir

    resolved_briefs_dir = Path(briefs_dir) if briefs_dir is not None else None
    return _load_public_scenarios_from_dir(Path(scenarios_dir), briefs_dir=resolved_briefs_dir)


def render_brief(brief: str, variables: dict[str, str] | None = None) -> str:
    """Render a Sisypy brief through the public API."""

    from sisypy.runner import render_brief as _render_public_brief

    return _render_public_brief(brief, variables)


def cli(
    adapter: AgenticProjectAdapter,
    *,
    argv: list[str] | None = None,
    configure_parser: Callable[[argparse.ArgumentParser], None] | None = None,
    before_run: Callable[[argparse.Namespace], None] | None = None,
) -> dict[str, Any]:
    """Stable public CLI entry point for Sisypy.

    Builds the argument parser, parses *argv*, optionally customises the parser
    via *configure_parser* and runs a pre-execution hook via *before_run*, then
    dispatches to ``run_all()`` through ``run_from_args()``.

    This is the canonical public replacement for importing the private
    ``_cli_entry_point`` helper.  Project runners should depend on this
    function and the two hooks, not on any ``sisypy.runner`` private symbols.

    Args:
        adapter: Project-specific adapter instance.
        argv: Command-line arguments (default: ``sys.argv[1:]``).
        configure_parser: Optional callback ``f(parser)`` called after the base
            parser is built.  Use this to add project-specific flags.
        before_run: Optional callback ``f(args)`` called after successful
            parsing and variable extraction, but before dispatching
            ``run_all()``.  Use this to set environment variables, override
            mode, or apply live-mode configuration.

    Returns:
        Batch summary dict from ``run_all()``.

    Raises:
        SystemExit: If ``--help`` is passed (standard argparse behaviour).
    """
    parser = build_cli_parser(adapter, configure_parser=configure_parser)
    args = parser.parse_args(argv or sys.argv[1:])

    if before_run is not None:
        before_run(args)

    return run_from_args(adapter, args)


# ---------------------------------------------------------------------------
# Exit-code helper
# ---------------------------------------------------------------------------


def summary_exit_code(summary: dict[str, Any]) -> int:
    """Compute a machine-readable exit code from a run summary.

    Precedence (highest wins):

    3 — blocked_prerequisite, skipped_live, scenario error, runner exception
        (infrastructure/setup failure).
    1 — any ``failed`` or ``violation`` outcome present.
    2 — one or more ``undetermined`` outcomes AND zero failures, violations,
        blocked, or errors.
    0 — all runs ``passed``, or only fake no-op plumbing runs.

    Accepts three formats:

    * Single-scenario summary dict with a ``runs`` key.
    * Batch summary dict with a ``scenarios`` key.
    * Dict with a ``results`` key (flat run results list).

    Returns:
        int exit code (0, 1, 2, or 3).
    """
    # --- Resolve runs from the three supported shapes ---
    runs: list[dict[str, Any]] = []

    if "runs" in summary:
        # Single-scenario summary.
        runs = summary.get("runs", [])
    elif "scenarios" in summary:
        # Batch summary — flatten all per-scenario runs.
        for ss in summary.get("scenarios", []):
            runs.extend(ss.get("runs", []))
    elif "results" in summary:
        # Flat run results list.
        runs = summary.get("results", [])

    if not runs:
        # No runs at all → treat as error/blocked (3).
        return 3

    # Check for error / blocked / skipped at the summary level.
    has_blocked_or_error = False
    if "error" in summary:
        has_blocked_or_error = True
    if summary.get("has_blocked_or_error"):
        has_blocked_or_error = True

    # Walk each run.
    has_failed = False
    has_violation = False
    has_undetermined = False
    has_pass_or_fake = False

    for rr in runs:
        outcome = rr.get("outcome", "")

        if outcome in ("blocked_prerequisite", "skipped_live"):
            has_blocked_or_error = True
        elif outcome in ("failed",):
            has_failed = True
        elif outcome in ("violation",):
            has_violation = True
        elif outcome in ("undetermined",):
            has_undetermined = True
        elif outcome in ("passed", "fake_no_op"):
            has_pass_or_fake = True

    # Precedence: 3 > 1 > 2 > 0
    if has_blocked_or_error:
        return 3
    if has_failed or has_violation:
        return 1
    if has_undetermined:
        return 2
    if has_pass_or_fake:
        return 0

    # Fallback — no recognisable outcomes.
    return 3


# ---------------------------------------------------------------------------
# Console CLI wrapper (exits, does not return)
# ---------------------------------------------------------------------------


def console_cli(
    adapter: AgenticProjectAdapter,
    *,
    argv: list[str] | None = None,
    configure_parser: Callable[[argparse.ArgumentParser], None] | None = None,
    before_run: Callable[[argparse.Namespace], None] | None = None,
) -> None:
    """Stable public CLI entry point that exits with a machine-readable code.

    Builds the parser via ``build_cli_parser()``, parses *argv*, optionally
    customises the parser via *configure_parser* and runs a pre-execution hook
    via *before_run*, then dispatches to ``cli()``.  The result is printed as
    JSON to stdout **once** and the process exits with ``summary_exit_code()``.

    This is the recommended entry point for CI and recurring-shell workflows.
    Programmatic callers that need the raw dict should use ``cli()`` instead.

    Args:
        adapter: Project-specific adapter instance.
        argv: Command-line arguments (default: ``sys.argv[1:]``).
        configure_parser: Optional callback ``f(parser)`` called after the base
            parser is built.
        before_run: Optional callback ``f(args)`` called after successful
            parsing but before dispatching.

    Raises:
        SystemExit: Always — exits with the computed exit code.
    """
    import json as _json

    result = cli(
        adapter,
        argv=argv,
        configure_parser=configure_parser,
        before_run=before_run,
    )
    print(_json.dumps(result, indent=2, default=str))
    sys.exit(summary_exit_code(result))
