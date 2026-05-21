"""
runner.py — core Sisypy runner.

Loads YAML scenarios from a configurable directory, renders user-shaped briefs
(variable substitution only, no named-template prescription), creates run
directories under out/agentic/reports/, dispatches actors, freezes evidence,
runs universal_checks + project_universal_checks, invokes assessor, summarises
results, emits summary.json + markdown reports, and classifies every result on
the success-proof ladder.

Supports:
  --mode structural|live
  --actor fake|hermes|deepseek-subagent
  --tag TAG
  --dry-run

Includes a structural-mode guard that enforces no-GPU constraints:
  - Strips RUNPOD_API_KEY and cloud env vars.
  - Scans evidence post-run for forbidden actions.

Contains zero hardcoded project imports.  All project-specific semantics
are sourced from the AgenticProjectAdapter interface.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

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
from sisypy.adapters import AgenticProjectAdapter
from sisypy.dispatch import (
    ActorDispatcher,
    ActorRunResult,
    FakeActorDispatcher,
    HermesDispatcher,
    SubagentLauncherDispatcher,
)
from sisypy.evidence import capture_evidence
from sisypy.universal_checks import run_all_checks
from sisypy.assessor import assess
from sisypy.cross_assessor_diff import run_diff, format_diff_report
from sisypy.pattern_finder import synthesize, format_synthesis_report

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_SCENARIOS_DIR = "tests/agentic/scenarios"
_DEFAULT_BRIEFS_DIR = "tests/agentic/briefs"
_DEFAULT_REPORTS_DIR = "out/agentic/reports"
_DEFAULT_TAG = "run"

# Structural-mode forbidden environment variable prefixes / keys.
_STRUCTURAL_FORBIDDEN_ENV_PREFIXES = (
    "RUNPOD_", "AWS_", "GCLOUD_", "AZURE_", "MODEL_PATH", "COMFY_MODEL",
)

# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------


def _load_scenario(path: Path) -> Scenario:
    """Load a single YAML scenario file into a Scenario dataclass.

    Args:
        path: Path to a .yaml scenario file.

    Returns:
        Populated Scenario instance.

    Raises:
        FileNotFoundError: if *path* does not exist.
        ValueError: if the YAML is malformed or missing required fields.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Scenario file {path} must contain a YAML mapping.")

    name = raw.get("name", path.stem)
    if not name:
        raise ValueError(f"Scenario file {path} is missing required 'name' field.")

    # Parse mode.
    mode_str = raw.get("mode", "structural")
    try:
        mode = RunMode(mode_str)
    except ValueError:
        mode = RunMode.STRUCTURAL

    # Parse agents.
    agents: list[AgentSpec] = []
    for a in raw.get("agents") or []:
        agents.append(AgentSpec(
            id=a.get("id", f"agent-{len(agents)}"),
            model=a.get("model", ""),
            dispatcher=a.get("dispatcher", "fake"),
            config=a.get("config", {}),
        ))

    # Parse assessment rubric (hidden from actor).
    raw_assess = raw.get("assessment") or {}
    assessment = Assessment(
        enforced=raw_assess.get("enforced", []),
        graded=raw_assess.get("graded", []),
        observed=raw_assess.get("observed", []),
    )

    return Scenario(
        name=name,
        tier=raw.get("tier", 1),
        description=raw.get("description", ""),
        brief=raw.get("brief", ""),
        mode=mode,
        agents=agents,
        budget=raw.get("budget", {}),
        priming=raw.get("priming", []),
        assessment=assessment,
        tags=raw.get("tags", []),
        extras=raw.get("extras", {}),
    )


def load_scenario(path: Path) -> Scenario:
    """Load a single YAML scenario file into a Scenario dataclass."""

    return _load_scenario(path)


def _load_scenarios_from_dir(
    scenarios_dir: Path,
    *,
    briefs_dir: Path | None = None,
) -> list[Scenario]:
    """Load all YAML scenario files from a directory.

    If the scenario YAML has no 'brief' field, the runner looks for a matching
    markdown brief file in *briefs_dir* (e.g. ``<name>.md``).

    Args:
        scenarios_dir: Directory containing .yaml scenario files.
        briefs_dir: Directory containing .md user-shaped briefs (optional).

    Returns:
        List of Scenario instances.
    """
    scenarios: list[Scenario] = []
    for yaml_path in sorted(scenarios_dir.glob("*.yaml")):
        scenario = _load_scenario(yaml_path)

        # If no inline brief, try loading from briefs/ directory.
        if not scenario.brief and briefs_dir and briefs_dir.is_dir():
            brief_path = briefs_dir / f"{scenario.name}.md"
            if brief_path.is_file():
                scenario.brief = brief_path.read_text(encoding="utf-8")

        scenarios.append(scenario)
    return scenarios


def load_scenarios_from_dir(
    scenarios_dir: Path,
    *,
    briefs_dir: Path | None = None,
) -> list[Scenario]:
    """Load all YAML scenario files from a directory."""

    return _load_scenarios_from_dir(scenarios_dir, briefs_dir=briefs_dir)


# ---------------------------------------------------------------------------
# Variable-substitution brief rendering
# ---------------------------------------------------------------------------

_VARIABLE_RE = re.compile(r"\$\{([^}]+)\}")


def _render_brief(brief: str, variables: dict[str, str] | None = None) -> str:
    """Render a user-shaped brief with ``${VAR}`` variable substitution only.

    No named-template prescription — this is a simple text-substitution pass.
    Unknown variables are left as-is.

    Args:
        brief: The markdown brief text with optional ``${VAR}`` placeholders.
        variables: Mapping of variable name → replacement value.

    Returns:
        Rendered brief string.
    """
    vars_ = variables or {}

    def _replacer(m: re.Match[str]) -> str:
        key = m.group(1)
        return vars_.get(key, m.group(0))

    return _VARIABLE_RE.sub(_replacer, brief)


def render_brief(brief: str, variables: dict[str, str] | None = None) -> str:
    """Render a user-shaped brief with ``${VAR}`` variable substitution only."""

    return _render_brief(brief, variables)


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


def _filter_scenarios(
    scenarios: list[Scenario],
    *,
    names: list[str] | None = None,
    tags: list[str] | None = None,
) -> list[Scenario]:
    """Filter scenarios by name and/or tags.

    Args:
        scenarios: Full list of loaded scenarios.
        names: If provided, only keep scenarios whose name is in this list.
        tags: If provided, only keep scenarios that have at least one matching tag.

    Returns:
        Filtered list.
    """
    result = scenarios
    if names:
        name_set = set(names)
        result = [s for s in result if s.name in name_set]
    if tags:
        tag_set = set(tags)
        result = [s for s in result if tag_set & set(s.tags)]
    return result


# ---------------------------------------------------------------------------
# Dispatcher factory
# ---------------------------------------------------------------------------


def _resolve_dispatcher(
    dispatcher_name: str,
    *,
    model: str = "deepseek-v4-pro",
) -> ActorDispatcher:
    """Resolve a dispatcher name to an ActorDispatcher instance.

    Args:
        dispatcher_name: "fake", "hermes", or "deepseek-subagent".
        model: Model identifier (used by real dispatchers).

    Returns:
        An ActorDispatcher instance.

    Raises:
        ValueError: if *dispatcher_name* is unrecognised.
    """
    if dispatcher_name == "fake":
        return FakeActorDispatcher()
    elif dispatcher_name == "hermes":
        return HermesDispatcher(model=model)
    elif dispatcher_name in {
        "deepseek-subagent",
        "subagent-launcher",
        "deepseek-v4-pro",
        "deepseek",
    }:
        return SubagentLauncherDispatcher(model=model)
    else:
        raise ValueError(
            f"Unknown dispatcher: '{dispatcher_name}'. "
            f"Supported: fake, hermes, deepseek-subagent."
        )


# ---------------------------------------------------------------------------
# Structural-mode guard
# ---------------------------------------------------------------------------


def _enforce_structural_mode(
    scenario: Scenario,
    run: ActorRun,
    adapter: AgenticProjectAdapter,
) -> tuple[dict[str, str], list[str]]:
    """Apply structural-mode environment and constraint enforcement.

    Args:
        scenario: The scenario being run.
        run: ActorRun metadata.
        adapter: Project adapter supplying build_env and command_policy.

    Returns:
        (env_dict, warnings_list) — environment variables to inject and any
        warnings from structural enforcement.
    """
    warnings: list[str] = []

    if run.mode != RunMode.STRUCTURAL:
        return adapter.build_env(scenario, run), warnings

    # Build environment with credential stripping.
    env = adapter.build_env(scenario, run)

    # Additional blanket stripping of known dangerous prefixes.
    for key in list(os.environ):
        if any(key.startswith(prefix) for prefix in _STRUCTURAL_FORBIDDEN_ENV_PREFIXES):
            env[key] = ""

    # Log the guard.
    warnings.append(
        "Structural-mode guard active: RUNPOD_API_KEY and cloud credentials stripped, "
        "no-GPU constraints enforced."
    )

    return env, warnings


def _scan_evidence_for_forbidden(
    evidence_pack: EvidencePack,
    adapter: AgenticProjectAdapter,
    scenario: Scenario,
) -> list[dict[str, Any]]:
    """Post-run evidence scan for forbidden actions.

    Reads the frozen evidence pack and applies adapter-specific forbidden-action
    detection patterns.

    Args:
        evidence_pack: Frozen evidence pack.
        adapter: Project adapter (supplies _forbidden_evidence_patterns if available).
        scenario: The scenario (context).

    Returns:
        List of violation dicts (empty if none found).
    """
    violations: list[dict[str, Any]] = []

    # Collect all text from evidence — intentionally exclude report.md.
    # Narrative prose in the actor's report is not evidence of executed
    # commands. Only stdout.log, stderr.log, and command_log.jsonl contain
    # actual command traces and terminal output.
    evidence_dir = Path(evidence_pack.evidence_dir)
    text_sources: list[str] = []
    for fname in ("stdout.log", "stderr.log", "command_log.jsonl"):
        fp = evidence_dir / fname
        if fp.is_file():
            try:
                text_sources.append(fp.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                pass

    combined = "\n".join(text_sources)

    # Use adapter's _forbidden_evidence_patterns if available.
    patterns_fn = getattr(adapter, "_forbidden_evidence_patterns", None)
    if patterns_fn and callable(patterns_fn):
        patterns = patterns_fn()
        for pat in patterns:
            try:
                for m in re.finditer(pat, combined, re.IGNORECASE | re.MULTILINE):
                    violations.append({
                        "pattern": pat,
                        "match": m.group(0)[:200],
                        "source": "evidence_scan",
                    })
            except re.error:
                pass

    return violations


def _missing_prerequisites(prereqs: dict[str, bool]) -> list[str]:
    """Return prerequisite names whose value is false."""
    return [name for name, ok in prereqs.items() if not ok]


def _deterministic_assessment(
    all_checks: dict[str, Any],
    *,
    summary: str = "",
) -> dict[str, Any]:
    """Build a schema-complete assessor result without an LLM call.

    Fake actor and CI paths must not depend on networked model calls. This
    result uses frozen deterministic check outcomes as the grading surface.
    """
    passed = bool(all_checks.get("all_passed", False))
    checks = all_checks.get("checks", {})
    failed = [
        name for name, check in checks.items()
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
        "summary": summary or (
            "Deterministic checks passed."
            if overall_passed else
            f"Deterministic checks failed: {', '.join(failed)}"
            if not any_undetermined else
            f"Insufficient evidence: {', '.join(item['check_name'] for item in undetermined_items)}"
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


# ---------------------------------------------------------------------------
# Run lifecycle: dispatch → capture → checks → assess → summarise
# ---------------------------------------------------------------------------


def _ts_now() -> str:
    """ISO-8601 timestamp for right now."""
    return datetime.now(timezone.utc).isoformat()


def _run_slug(scenario_name: str, agent_id: str, tag: str) -> str:
    """Build a human-readable run slug."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{tag}-{scenario_name}-{agent_id}-{ts}"


def _report_dir(reports_root: Path, tag: str, scenario: Scenario) -> Path:
    """Compute the report directory for a scenario.

    Convention: ``out/agentic/reports/<tag>-<scenario>/``
    """
    d = reports_root / f"{tag}-{scenario.name}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_summary_json(report_dir: Path, summary: dict[str, Any]) -> None:
    """Write summary.json to the report directory."""
    path = report_dir / "summary.json"
    path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")


def _write_markdown_report(report_dir: Path, summary: dict[str, Any]) -> None:
    """Write a human-readable markdown report for a single scenario."""
    lines: list[str] = []
    lines.append(f"# Agentic Run Report — {summary.get('scenario_name', '?')}\n")

    lines.append(f"**Tag:** {summary.get('tag', '?')}  \n")
    lines.append(f"**Mode:** {summary.get('mode', '?')}  \n")
    lines.append(f"**Dispatchers:** {', '.join(summary.get('dispatchers_used', []))}  \n")
    lines.append(f"**Started:** {summary.get('started_at', '?')}  \n")
    lines.append(f"**Finished:** {summary.get('finished_at', '?')}  \n\n")

    # Per-agent results.
    for run_result in summary.get("runs", []):
        agent_id = run_result.get("agent_id", "?")
        outcome = run_result.get("outcome", "?")
        proof = run_result.get("success_proof_level", "?")
        disp = run_result.get("dispatcher", "?")

        if disp == "fake":
            lines.append(f"## Agent: {agent_id} (Fake Actor — no-op plumbing check)\n")
        else:
            lines.append(f"## Agent: {agent_id} ({disp})\n")

        # Outcome with distinct undetermined icon.
        if outcome == "undetermined":
            lines.append(f"- **Outcome:** ❓ UNDETERMINED (insufficient evidence)\n")
        elif outcome == "failed":
            lines.append(f"- **Outcome:** ❌ FAILED\n")
        elif outcome == "passed":
            lines.append(f"- **Outcome:** ✅ PASSED\n")
        elif outcome == "violation":
            lines.append(f"- **Outcome:** 🚫 VIOLATION\n")
        elif outcome == "blocked_prerequisite":
            lines.append(f"- **Outcome:** ⛔ BLOCKED\n")
        elif outcome == "fake_no_op":
            lines.append(f"- **Outcome:** 🔧 FAKE_NO_OP\n")
        else:
            lines.append(f"- **Outcome:** {outcome}\n")

        lines.append(f"- **Success proof level:** {proof}\n")
        actions_count = run_result.get("actions_count", "?")
        evidence_confidence = run_result.get("evidence_confidence", "?")
        lines.append(f"- **Actions:** {actions_count} (confidence: {evidence_confidence})\n")

        assessment = run_result.get("assessment", {})
        if assessment:
            if assessment.get("undetermined"):
                overall = "❓ UNDETERMINED"
            elif assessment.get("overall_passed"):
                overall = "✅ PASS"
            else:
                overall = "❌ FAIL"
            lines.append(f"- **Assessor verdict:** {overall}\n")
            lines.append(f"  - Summary: {assessment.get('summary', 'N/A')}\n")
            contradictions = assessment.get("contradictions", [])
            if contradictions:
                lines.append(f"  - Contradictions: {len(contradictions)}\n")

        universal = run_result.get("universal_checks", {})
        if universal:
            any_undetermined = universal.get("any_undetermined", False)
            all_ok = universal.get("all_passed", False)
            if any_undetermined:
                lines.append(f"- **Universal checks:** ❓ UNDETERMINED\n")
            elif all_ok:
                lines.append(f"- **Universal checks:** ✅ PASS\n")
            else:
                lines.append(f"- **Universal checks:** ❌ FAIL\n")
            for cname, cresult in universal.get("checks", {}).items():
                c_undetermined = cresult.get("undetermined", False) if isinstance(cresult, dict) else False
                c_passed = cresult.get("passed", False) if isinstance(cresult, dict) else False
                if c_undetermined:
                    icon = "❓"
                elif c_passed:
                    icon = "✅"
                else:
                    icon = "❌"
                lines.append(f"  - {icon} {cname}: {cresult.get('detail', '') if isinstance(cresult, dict) else cresult}\n")

        # Capture gaps section.
        capture_gaps = run_result.get("capture_gaps")
        if capture_gaps:
            lines.append(f"- **Capture gaps:** {json.dumps(capture_gaps, default=str)}\n")

        errors = run_result.get("errors", [])
        if errors:
            lines.append(f"- **Errors:** {', '.join(errors)}\n")

        lines.append("")

    # Synthesis.
    synthesis = summary.get("synthesis")
    if synthesis:
        lines.append(format_synthesis_report(synthesis))

    path = report_dir / "report.md"
    path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Single-scenario runner
# ---------------------------------------------------------------------------


def _run_workdir(run: "ActorRun", adapter: "AgenticProjectAdapter") -> "Path":
    """Return the run workdir as a Path, falling back to adapter.repo_root."""
    from pathlib import Path
    if run.workdir is not None:
        return Path(run.workdir)
    return adapter.repo_root


def _detect_capture_trigger(dispatch_result: "ActorRunResult") -> str:
    """Determine the capture trigger from a dispatch result.

    Returns 'normal' when ok=True, 'timeout' when errors/stderr indicate
    timeout, 'failure' otherwise.  String-based detection is intentionally
    conservative for v1.
    """
    if dispatch_result.ok:
        return "normal"

    combined = (
        " ".join(dispatch_result.errors) + " " + dispatch_result.stderr
    ).lower()
    if any(kw in combined for kw in ("timed out", "timeoutexpired", "timeout")):
        return "timeout"

    return "failure"


def _capture_run_evidence(
    *,
    scenario: "Scenario",
    run: "ActorRun",
    adapter: "AgenticProjectAdapter",
    report_dir: "Path",
    brief_md: str,
    dispatch_result: "ActorRunResult",
    policy: dict[str, Any],
    tag: str,
    capture_trigger: str = "normal",
    command_log_override: list[dict[str, Any]] | None = None,
    report_md_override: str | None = None,
    actions_override: list | None = None,
) -> "EvidencePack":
    """Capture evidence and run adapter capture with non-throwing error handling.

    This deduplicates the capture_evidence + adapter.capture + error-handling
    sequence used by all three capture paths (live-prerequisite blocked,
    dispatcher-level blocked, and normal dispatch).

    Exceptions from capture_evidence or adapter.capture are caught, appended
    to run.errors, and never prevent the caller from continuing.
    """
    cmd_log = (
        command_log_override
        if command_log_override is not None
        else (dispatch_result.command_log if dispatch_result.command_log else None)
    )
    report_md = (
        report_md_override
        if report_md_override is not None
        else dispatch_result.report_md
    )
    # Resolve actions: explicit override > dispatch_result.actions > None (unsupported).
    # None means action capture is unknown/unsupported for this dispatcher.
    # [] means action capture is supported but no actions were recorded.
    actions = (
        actions_override
        if actions_override is not None
        else (dispatch_result.actions if dispatch_result.actions else None)
    )

    try:
        evidence_pack = capture_evidence(
            scenario=scenario,
            run=run,
            workdir=_run_workdir(run, adapter),
            report_dir=report_dir,
            brief_md=brief_md,
            report_md=report_md,
            stdout=dispatch_result.stdout,
            stderr=dispatch_result.stderr,
            command_log=cmd_log,
            actions=actions,
            tag=tag,
            command_policy=policy,
            capture_trigger=capture_trigger,
        )
    except Exception as exc:
        run.errors.append(f"evidence capture failed: {exc}")
        # Build a minimal EvidencePack so the caller always has something.
        from sisypy.schema import EvidencePack as EP
        evidence_pack = EP(evidence_dir=str(report_dir / "evidence" / (run.id or run.agent_id or "unnamed")))
        return evidence_pack

    try:
        adapter.capture(scenario, run, Path(evidence_pack.evidence_dir))
    except Exception as exc:
        run.errors.append(f"project capture failed: {exc}")

    return evidence_pack



# ---------------------------------------------------------------------------
# Progress event helper
# ---------------------------------------------------------------------------


def _emit_progress(
    callback: Callable[[dict[str, Any]], None] | None,
    **event: Any,
) -> None:
    """Emit a progress event to *callback* if it is not None.

    The event dict receives an automatic ``timestamp`` in ISO-8601 UTC format.
    All event values must be JSON-serializable.  If *callback* raises an
    exception it is caught and logged to stderr so a broken callback cannot
    crash the run.
    """
    if callback is None:
        return
    event["timestamp"] = _ts_now()
    try:
        callback(event)
    except Exception:
        import traceback
        print(
            f"[sisypy] progress callback raised: {traceback.format_exc()}",
            file=sys.stderr,
        )




def run_scenario(
    scenario: Scenario,
    *,
    adapter: AgenticProjectAdapter,
    dispatchers: dict[str, ActorDispatcher],
    mode: RunMode,
    tag: str = _DEFAULT_TAG,
    reports_root: Path | None = None,
    variables: dict[str, str] | None = None,
    dry_run: bool = False,
    run_cross_diff: bool = False,
    capture_interval_sec: float | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run a single scenario through the full harness lifecycle.

    Args:
        scenario: The Scenario to run.
        adapter: Project-specific adapter.
        dispatchers: Mapping of dispatcher name → ActorDispatcher instance.
        mode: Execution mode (structural or live).
        tag: Human-readable label for report grouping.
        reports_root: Root directory for reports (default: out/agentic/reports/).
        variables: Optional variable substitution dict for brief rendering.
        dry_run: If True, load and render but skip dispatch.
        run_cross_diff: If True, run cross-assessor diff for each agent.
        capture_interval_sec: Interval in seconds for periodic evidence snapshots
            during long-running actor dispatches.  When set to a positive value
            and the adapter supports it (``supports_interval_capture`` flag),
            a daemon background thread writes interval snapshots under
            ``evidence/<slug>/intervals/<timestamp>/``.  Snapshots are
            best-effort; a failed interval capture never crashes the run or
            kills the actor.  Disabled by default (None).
        progress_callback: Optional callback ``f(event)`` receiving a JSON-
            serializable dict with keys ``event``, ``scenario_name``,
            ``agent_id``, ``run_id``, ``timestamp``, and optional ``message``.
            See ``_emit_progress`` for the schema.
    """

    reports = reports_root or Path(_DEFAULT_REPORTS_DIR)
    report_dir = _report_dir(reports, tag, scenario)
    started_at = _ts_now()

    # Resolve mode (scenario default or override).
    effective_mode = mode if mode else scenario.mode

    _emit_progress(progress_callback, event="scenario_start",
                   scenario_name=scenario.name, mode=effective_mode.value, tag=tag)

    runs_outcomes: list[dict[str, Any]] = []
    dispatchers_used: set[str] = set()

    for agent_spec in scenario.agents:
        slug = _run_slug(scenario.name, agent_spec.id, tag)

        # Resolve dispatcher.
        dispatcher_name = agent_spec.dispatcher or "fake"
        dispatcher = dispatchers.get(dispatcher_name)
        if dispatcher is None:
            dispatcher = _resolve_dispatcher(dispatcher_name, model=agent_spec.model)
            dispatchers[dispatcher_name] = dispatcher
        dispatchers_used.add(dispatcher_name)

        # Create ActorRun record.
        run = ActorRun(
            id=slug,
            scenario_name=scenario.name,
            agent_id=agent_spec.id,
            mode=effective_mode,
            dispatcher=dispatcher_name,
            tag=tag,
            started_at=_ts_now(),
        )
        run.workdir = str(adapter.repo_root)
        run_errors: list[str] = []

        # --- 0. Live prerequisite gate ---
        if effective_mode == RunMode.LIVE:
            prereqs = adapter.live_prerequisites(scenario)
            missing = _missing_prerequisites(prereqs)
            if missing:
                run.outcome = ScenarioOutcome.BLOCKED_PREREQUISITE.value
                run.success_proof_level = SuccessProofLevel.AUTHORED
                run.summary = (
                    "Live execution prerequisites missing: "
                    + ", ".join(missing)
                )
                run.errors = [run.summary]
                run.finished_at = _ts_now()
                policy = adapter.command_policy(scenario, run)
                dispatch_result = ActorRunResult(
                    slug=slug,
                    ok=False,
                    stdout=f"[blocked_prerequisite] {', '.join(missing)}\n",
                    stderr="",
                    report_md=(
                        "# Live Execution Blocked\n\n"
                        "## 1. Missing prerequisites\n\n"
                        + "\n".join(f"- `{item}`" for item in missing)
                        + "\n\n## 2. Outcome\n\n"
                        "The scenario was blocked before actor dispatch."
                    ),
                )
                evidence_pack = _capture_run_evidence(
                    scenario=scenario,
                    run=run,
                    adapter=adapter,
                    report_dir=report_dir,
                    brief_md=_render_brief(scenario.brief, variables),
                    dispatch_result=dispatch_result,
                    policy=policy,
                    tag=tag,
                    capture_trigger="blocked",
                    command_log_override=[],
                    actions_override=[],
                )
                try:
                    live_stub = Path(evidence_pack.evidence_dir) / "project_specific" / "runtime" / "runpod_stub.json"
                    live_stub.parent.mkdir(parents=True, exist_ok=True)
                    live_stub.write_text(
                        json.dumps(
                            {
                                "enabled": False,
                                "reason": "blocked_prerequisite",
                                "missing": missing,
                                "pod_id": None,
                                "terminated": False,
                            },
                            indent=2,
                        ),
                        encoding="utf-8",
                    )
                except Exception as exc:
                    run.errors.append(f"blocked evidence capture failed: {exc}")
                det_assess = _deterministic_assessment(
                    {"all_passed": False, "checks": {}},
                    summary=run.summary,
                )
                runs_outcomes.append({
                    "agent_id": agent_spec.id,
                    "dispatcher": dispatcher_name,
                    "outcome": run.outcome,
                    "success_proof_level": run.success_proof_level.value,
                    "summary": run.summary,
                    "errors": run.errors,
                    "workdir": run.workdir,
                    "evidence_dir": evidence_pack.evidence_dir,
                    "actions_count": 0,
                    "evidence_confidence": "unknown",
                    "universal_checks": {},
                    "assessment": det_assess,
                    "cross_assessor_diff": None,
                    "undetermined": bool(det_assess.get("undetermined", False)),
                    "undetermined_items": det_assess.get("undetermined_items", []),
                    "capture_gaps": evidence_pack.manifest.get("capture_gaps", {}),
                })
                continue

        # --- 1. Prime workspace ---
        _emit_progress(progress_callback, event="prime_start",
                       scenario_name=scenario.name, agent_id=agent_spec.id,
                       run_id=run.id)
        try:
            adapter.prime(scenario, run)
        except Exception as exc:
            run_errors.append(f"prime failed: {exc}")
        _emit_progress(progress_callback, event="prime_end",
                       scenario_name=scenario.name, agent_id=agent_spec.id,
                       run_id=run.id)

        # --- 2. Build env + structural guard ---
        env, guard_warnings = _enforce_structural_mode(scenario, run, adapter)
        run_errors.extend(guard_warnings)

        # --- 3. Render brief ---
        brief = _render_brief(scenario.brief, variables)

        # --- 4. Dispatch actor ---
        _emit_progress(progress_callback, event="dispatch_start",
                       scenario_name=scenario.name, agent_id=agent_spec.id,
                       run_id=run.id, dispatcher=dispatcher_name)

        # --- Interval capture thread (best-effort periodic snapshots) ---
        interval_stop = threading.Event()
        interval_thread = None
        interval_seq_counter = [0]  # mutable counter for thread

        def _interval_capture_loop() -> None:
            """Background thread: periodically snapshot evidence during dispatch."""
            import time as _time
            from sisypy.evidence import capture_interval_snapshot
            from pathlib import Path as _Path
            workdir_path = _run_workdir(run, adapter)
            while not interval_stop.wait(timeout=capture_interval_sec):
                try:
                    interval_seq_counter[0] += 1
                    capture_interval_snapshot(
                        run=run,
                        workdir=workdir_path,
                        report_dir=report_dir,
                        interval_seq=interval_seq_counter[0],
                    )
                    _emit_progress(progress_callback, event="interval_capture_end",
                                   scenario_name=scenario.name, agent_id=agent_spec.id,
                                   run_id=run.id, interval_seq=interval_seq_counter[0])
                except Exception as exc:
                    run_errors.append(f"interval capture snapshot failed (seq={interval_seq_counter[0]}): {exc}")
                    _emit_progress(progress_callback, event="interval_capture_failure",
                                   scenario_name=scenario.name, agent_id=agent_spec.id,
                                   run_id=run.id, interval_seq=interval_seq_counter[0],
                                   error=str(exc))

        supports_interval = getattr(adapter, "supports_interval_capture", True)
        if capture_interval_sec is not None and capture_interval_sec > 0:
            if supports_interval:
                _emit_progress(progress_callback, event="interval_capture_start",
                               scenario_name=scenario.name, agent_id=agent_spec.id,
                               run_id=run.id, interval_sec=capture_interval_sec)
                interval_thread = threading.Thread(
                    target=_interval_capture_loop, daemon=True
                )
                interval_thread.start()
            else:
                run_errors.append(
                    f"interval capture requested ({capture_interval_sec}s) but adapter "
                    f"does not support it; skipping interval snapshots"
                )

        if dry_run:
            dispatch_result = ActorRunResult(
                slug=slug,
                ok=True,
                stdout="[dry-run] dispatch skipped",
                report_md="",
                elapsed_sec=0.0,
            )
        else:
            try:
                dispatch_result = dispatcher.dispatch(
                    brief,
                    slug=slug,
                    env=env,
                    workdir=_run_workdir(run, adapter),
                    extra_config=agent_spec.config,
                )
            except Exception as exc:
                dispatch_result = ActorRunResult(
                    slug=slug,
                    ok=False,
                    stdout="",
                    stderr=str(exc),
                    errors=[f"dispatch exception: {exc}"],
                )
                run_errors.append(f"dispatch exception: {exc}")

        # Stop interval capture thread.
        if interval_thread is not None:
            interval_stop.set()
            interval_thread.join(timeout=5.0)

        _emit_progress(progress_callback, event="dispatch_end",
                       scenario_name=scenario.name, agent_id=agent_spec.id,
                       run_id=run.id, dispatcher=dispatcher_name,
                       ok=dispatch_result.ok)

        # --- If fake actor, mark as no-op plumbing check ---
        if dispatcher_name == "fake" and not dry_run:
            run.outcome = ScenarioOutcome.FAKE_NO_OP.value
            run.summary = "Fake actor no-op plumbing check; no real actor attempted the scenario."

        if dispatch_result.blocked:
            run.outcome = ScenarioOutcome.BLOCKED_PREREQUISITE.value
            run.success_proof_level = SuccessProofLevel.AUTHORED
            run.summary = f"Blocked: {dispatch_result.blocked}"
            run.errors = [dispatch_result.blocked] + dispatch_result.errors
            run.finished_at = _ts_now()

            policy = adapter.command_policy(scenario, run)
            evidence_pack = _capture_run_evidence(
                scenario=scenario,
                run=run,
                adapter=adapter,
                report_dir=report_dir,
                brief_md=brief,
                dispatch_result=dispatch_result,
                policy=policy,
                tag=tag,
                capture_trigger="blocked",
                command_log_override=[],
                actions_override=[],
            )

            runs_outcomes.append({
                "agent_id": agent_spec.id,
                "dispatcher": dispatcher_name,
                "outcome": run.outcome,
                "success_proof_level": run.success_proof_level.value,
                "summary": run.summary,
                "errors": run.errors,
                "workdir": run.workdir,
                "evidence_dir": evidence_pack.evidence_dir,
                "actions_count": 0,
                "evidence_confidence": "unknown",
                "universal_checks": {},
                "assessment": {},
                "cross_assessor_diff": None,
                "undetermined": False,
                "undetermined_items": [],
                "capture_gaps": evidence_pack.manifest.get("capture_gaps", {}),
            })
            continue

        if not dispatch_result.ok and not dispatch_result.report_md:
            run_errors.append("dispatch failed with no report")

        # --- 5. Capture evidence ---
        _emit_progress(progress_callback, event="capture_start",
                       scenario_name=scenario.name, agent_id=agent_spec.id,
                       run_id=run.id)
        policy = adapter.command_policy(scenario, run)
        capture_trigger = _detect_capture_trigger(dispatch_result)

        evidence_pack = _capture_run_evidence(
            scenario=scenario,
            run=run,
            adapter=adapter,
            report_dir=report_dir,
            brief_md=brief,
            dispatch_result=dispatch_result,
            policy=policy,
            tag=tag,
            capture_trigger=capture_trigger,
            actions_override=None,
        )

        _emit_progress(progress_callback, event="capture_end",
                       scenario_name=scenario.name, agent_id=agent_spec.id,
                       run_id=run.id, evidence_dir=evidence_pack.evidence_dir)

        # --- 6. Classify success-proof level ---
        proof_level = adapter.classify_success(scenario, evidence_pack)
        run.success_proof_level = proof_level

        # --- 7. Universal checks ---
        bypass_patterns = adapter.canonical_bypass_patterns(scenario)
        deny_patterns = policy.get("deny_patterns", [])

        _emit_progress(progress_callback, event="checks_start",
                       scenario_name=scenario.name, agent_id=agent_spec.id,
                       run_id=run.id)

        shared_checks = run_all_checks(
            evidence_pack,
            scenario,
            bypass_patterns=bypass_patterns,
            deny_patterns=deny_patterns,
            actual_proof_level=proof_level.value,
        )

        # --- 7b. Project-universal checks ---
        project_checks = adapter.project_universal_checks(
            scenario, Path(evidence_pack.evidence_dir)
        )

        # Merge project checks into universal checks dict.
        all_checks = dict(shared_checks)
        all_checks.setdefault("checks", {})
        for k, v in (project_checks or {}).items():
            all_checks["checks"][k] = v
        # Recompute all_passed.
        all_checks["all_passed"] = all(
            c.get("passed", False) for c in all_checks["checks"].values()
        )
        # Recompute any_undetermined after merging project checks.
        all_checks["any_undetermined"] = any(
            c.get("undetermined", False) for c in all_checks["checks"].values()
        )

        # --- 8. Post-run evidence scan for forbidden actions ---
        forbidden_violations = _scan_evidence_for_forbidden(
            evidence_pack, adapter, scenario
        )
        if forbidden_violations:
            all_checks["checks"]["post_run_forbidden_scan"] = {
                "passed": False,
                "severity": "error",
                "detail": f"Found {len(forbidden_violations)} forbidden artifact(s) in evidence.",
                "violations": forbidden_violations,
            }
            all_checks["all_passed"] = False

        _emit_progress(progress_callback, event="checks_end",
                       scenario_name=scenario.name, agent_id=agent_spec.id,
                       run_id=run.id, all_passed=all_checks.get("all_passed", False))

        # --- 9. Assessor ---
        if (
            dry_run
            or dispatcher_name == "fake"
            or os.environ.get("AGENTIC_SKIP_LLM_ASSESSOR") == "1"
        ):
            _emit_progress(progress_callback, event="assess_start",
                           scenario_name=scenario.name, agent_id=agent_spec.id,
                           run_id=run.id, assessor_type="deterministic")
            assessor_result = _deterministic_assessment(all_checks)
        else:
            _emit_progress(progress_callback, event="assess_start",
                           scenario_name=scenario.name, agent_id=agent_spec.id,
                           run_id=run.id)
            assessor_result = assess(evidence_pack, scenario)

        _emit_progress(progress_callback, event="assess_end",
                       scenario_name=scenario.name, agent_id=agent_spec.id,
                       run_id=run.id)

        # --- 10. Cross-assessor diff (optional) ---
        cross_diff_result = None
        if run_cross_diff:
            try:
                cross_diff_result = run_diff(
                    evidence_pack,
                    scenario,
                    first_assessment=assessor_result,
                )
            except Exception as exc:
                run_errors.append(f"cross-assessor diff failed: {exc}")

        # --- 11. Determine outcome ---
        run.outcome = _determine_outcome(
            scenario=scenario,
            run=run,
            dispatch_result=dispatch_result,
            all_checks=all_checks,
            assessor_result=assessor_result,
            forbidden_violations=forbidden_violations,
            run_errors=run_errors,
        )

        run.errors = run_errors
        run.summary = assessor_result.get("summary", dispatch_result.stdout[:300])
        run.finished_at = _ts_now()

        # Collect undetermined status from both deterministic checks and assessor.
        run_undetermined = bool(
            all_checks.get("any_undetermined", False)
            or assessor_result.get("undetermined", False)
        )
        run_undetermined_items: list[dict[str, Any]] = []
        for cname, cresult in all_checks.get("checks", {}).items():
            if isinstance(cresult, dict) and cresult.get("undetermined", False):
                run_undetermined_items.append({
                    "check_name": cname,
                    "detail": cresult.get("detail", ""),
                })
        run_undetermined_items.extend(
            assessor_result.get("undetermined_items", [])
        )

        runs_outcomes.append({
            "agent_id": agent_spec.id,
            "dispatcher": dispatcher_name,
            "outcome": run.outcome,
            "success_proof_level": run.success_proof_level.value,
            "summary": run.summary,
            "errors": run_errors,
            "workdir": run.workdir,
            "evidence_dir": evidence_pack.evidence_dir,
            "actions_count": len(dispatch_result.actions or []),
            "evidence_confidence": evidence_pack.manifest.get("evidence_confidence", "unknown"),
            "universal_checks": all_checks,
            "assessment": assessor_result,
            "cross_assessor_diff": cross_diff_result,
            "undetermined": run_undetermined,
            "undetermined_items": run_undetermined_items,
            "capture_gaps": evidence_pack.manifest.get("capture_gaps", {}),
        })

    # --- Aggregate ---
    finished_at = _ts_now()

    # Compute outcome counts across all runs.
    outcome_counts: dict[str, int] = {}
    has_undetermined = False
    for rr in runs_outcomes:
        oc = rr.get("outcome", "unknown")
        outcome_counts[oc] = outcome_counts.get(oc, 0) + 1
        if oc == "undetermined":
            has_undetermined = True

    summary = {
        "scenario_name": scenario.name,
        "scenario_tier": scenario.tier,
        "tag": tag,
        "mode": effective_mode.value,
        "dispatchers_used": sorted(dispatchers_used),
        "started_at": started_at,
        "finished_at": finished_at,
        "runs": runs_outcomes,
        "outcome_counts": outcome_counts,
        "has_undetermined": has_undetermined,
    }

    # --- Write outputs ---
    _emit_progress(progress_callback, event="report_write_start",
                   scenario_name=scenario.name, tag=tag)
    _write_summary_json(report_dir, summary)
    _write_markdown_report(report_dir, summary)
    _emit_progress(progress_callback, event="report_write_end",
                   scenario_name=scenario.name, tag=tag)

    _emit_progress(progress_callback, event="scenario_end",
                   scenario_name=scenario.name, tag=tag,
                   runs_count=len(runs_outcomes))

    return summary


# ---------------------------------------------------------------------------
# Outcome determination
# ---------------------------------------------------------------------------


def _determine_outcome(
    *,
    scenario: Scenario,
    run: ActorRun,
    dispatch_result: ActorRunResult,
    all_checks: dict[str, Any],
    assessor_result: dict[str, Any],
    forbidden_violations: list[dict[str, Any]],
    run_errors: list[str],
) -> str:
    """Determine the ScenarioOutcome for a single agent run.

    Outcome precedence (highest first):

    1. Pre-set plumbing outcomes — blocked_prerequisite, fake_no_op,
       skipped_live — are returned immediately without further checks.
    2. Fatal no-report dispatch errors (not ok + no report + run_errors)
       → FAILED.
    3. Structural violations (forbidden commands, env var leaks, etc.)
       → VIOLATION.
    4. Ungraded assessor (missing API key, malformed response) → FAILED.
    5. Insufficient evidence / undetermined — triggered by any of:
       - Deterministic check undetermined flag (any_undetermined).
       - Assessor undetermined flag.
       → UNDETERMINED.
    6. Assessor judgement not passed → FAILED.
    7. All checks and assessor passed → PASSED.
    """

    # 1. Preserve pre-set plumbing outcomes.
    if run.outcome in (
        ScenarioOutcome.BLOCKED_PREREQUISITE.value,
        ScenarioOutcome.FAKE_NO_OP.value,
        ScenarioOutcome.SKIPPED_LIVE.value,
    ):
        return run.outcome

    # 2. Fatal no-report dispatch failures → FAILED.
    #    Only when: dispatch was not ok, no report was produced,
    #    AND there are run-level errors (e.g. subprocess crash, timeout).
    if not dispatch_result.ok and not dispatch_result.report_md and run_errors:
        return ScenarioOutcome.FAILED.value

    # 3. Structural violations take priority over undetermined.
    if forbidden_violations and run.mode == RunMode.STRUCTURAL:
        return ScenarioOutcome.VIOLATION.value

    checks = all_checks.get("checks", {})
    fc = checks.get("forbidden_commands", {})
    if isinstance(fc, dict) and not fc.get("passed", True):
        return ScenarioOutcome.VIOLATION.value

    # 4. Ungraded assessor stays FAILED — never undetermined.
    if assessor_result.get("ungraded"):
        return ScenarioOutcome.FAILED.value

    # 5. Undetermined check — must come AFTER blocked/fake/skipped
    #    preservation and VIOLATION/ungraded gates, but BEFORE the
    #    final "assessor says not passed → FAILED" fallthrough.
    #
    #    Eligible paths:
    #      - ok + no report (no-report runs funnel through deterministic
    #        checks which set any_undetermined=True when report.md is missing).
    #      - not ok + no report + no run_errors (silent no-report failure).
    #      - Deterministic check any_undetermined flag.
    #      - Assessor undetermined flag.
    has_undetermined_checks = bool(all_checks.get("any_undetermined", False))
    assessor_undetermined = bool(assessor_result.get("undetermined", False))

    if has_undetermined_checks or assessor_undetermined:
        return ScenarioOutcome.UNDETERMINED.value

    # 6. Assessor says not passed → FAILED.
    if not assessor_result.get("overall_passed", False):
        return ScenarioOutcome.FAILED.value

    return ScenarioOutcome.PASSED.value


# ---------------------------------------------------------------------------
# Multi-scenario runner
# ---------------------------------------------------------------------------


def run_all(
    adapter: AgenticProjectAdapter,
    *,
    scenarios_dir: Path | None = None,
    briefs_dir: Path | None = None,
    reports_root: Path | None = None,
    mode: RunMode = RunMode.STRUCTURAL,
    actor: str = "fake",
    tag: str = _DEFAULT_TAG,
    names: list[str] | None = None,
    tags: list[str] | None = None,
    variables: dict[str, str] | None = None,
    dry_run: bool = False,
    run_cross_diff: bool = False,
    parallel: bool = True,
    max_workers: int = 4,
    capture_interval_sec: float | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run all scenarios discovered in *scenarios_dir*.

    This is the top-level entry point that loads all scenarios, filters them,
    and runs each through the harness lifecycle.

    Args:
        adapter: Project-specific adapter.
        scenarios_dir: Directory of YAML scenario files.
        briefs_dir: Directory of markdown brief files.
        reports_root: Root for output reports.
        mode: RunMode (structural or live).
        actor: Default actor dispatcher name. Overrides scenario YAML agents.
        tag: Human-readable label for report grouping.
        names: Optional list of scenario names to filter to.
        tags: Optional list of tags to filter to.
        variables: Variable substitution dict for brief rendering.
        dry_run: If True, load and render but skip dispatch.
        run_cross_diff: If True, run cross-assessor diff for each agent.
        parallel: If True, run scenarios in parallel via ThreadPoolExecutor.
        max_workers: Max threads for parallel execution.

    Returns:
        Full batch summary dict with per-scenario summaries and optional
        cross-scenario synthesis.
    """
    sd = scenarios_dir or Path(_DEFAULT_SCENARIOS_DIR)
    bd = briefs_dir or Path(_DEFAULT_BRIEFS_DIR)
    rr = reports_root or Path(_DEFAULT_REPORTS_DIR)

    # Load.
    all_scenarios = _load_scenarios_from_dir(sd, briefs_dir=bd)

    # Filter.
    selected = _filter_scenarios(all_scenarios, names=names, tags=tags)

    # The CLI actor should be authoritative. Scenario YAML keeps a dispatcher
    # field so individual fixtures can describe their default, but batch runs
    # must be able to swap in a real testing model without editing fixtures.
    if actor:
        for scenario in selected:
            for agent in scenario.agents:
                agent.dispatcher = actor

    if not selected:
        return {
            "batch_tag": tag,
            "scenario_count": 0,
            "scenario_names": [],
            "mode": mode.value,
            "scenarios": [],
            "dry_run": dry_run,
        }

    # Resolve dispatcher.
    model = "deepseek-v4-pro"
    dispatchers: dict[str, ActorDispatcher] = {
        actor: _resolve_dispatcher(actor, model=model),
    }

    # Run.
    scenario_summaries: list[dict[str, Any]] = []

    if parallel and not dry_run:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures: dict[Any, str] = {}
            for s in selected:
                fut = executor.submit(
                    run_scenario,
                    scenario=s,
                    adapter=adapter,
                    dispatchers=dispatchers,
                    mode=mode,
                    tag=tag,
                    reports_root=rr,
                    variables=variables,
                    dry_run=dry_run,
                    run_cross_diff=run_cross_diff,
                    capture_interval_sec=capture_interval_sec,
                    progress_callback=progress_callback,
                )
                futures[fut] = s.name

            for fut in as_completed(futures):
                name = futures[fut]
                try:
                    summary = fut.result()
                    scenario_summaries.append(summary)
                except Exception as exc:
                    scenario_summaries.append({
                        "scenario_name": name,
                        "error": str(exc),
                        "runs": [],
                    })
    else:
        for s in selected:
            try:
                summary = run_scenario(
                    scenario=s,
                    adapter=adapter,
                    dispatchers=dispatchers,
                    mode=mode,
                    tag=tag,
                    reports_root=rr,
                    variables=variables,
                    dry_run=dry_run,
                    run_cross_diff=run_cross_diff,
                    capture_interval_sec=capture_interval_sec,
                    progress_callback=progress_callback,
                )
                scenario_summaries.append(summary)
            except Exception as exc:
                scenario_summaries.append({
                    "scenario_name": s.name,
                    "error": str(exc),
                    "runs": [],
                })

    # --- Cross-scenario synthesis ---
    synthesis = None
    if not dry_run and scenario_summaries:
        # Flatten all runs from all scenarios for pattern finder.
        all_run_results: list[dict[str, Any]] = []
        for ss in scenario_summaries:
            for run_res in ss.get("runs", []):
                rr_i = dict(run_res)
                rr_i["scenario_name"] = ss.get("scenario_name", "?")
                all_run_results.append(rr_i)

        try:
            synthesis = synthesize(all_run_results, use_llm=False)
        except Exception:
            pass

    # --- Compute batch-level aggregates ---
    batch_outcome_counts: dict[str, int] = {}
    batch_has_undetermined = False
    batch_has_blocked_or_error = False
    for ss in scenario_summaries:
        # Per-scenario outcome_counts.
        for oc, cnt in ss.get("outcome_counts", {}).items():
            batch_outcome_counts[oc] = batch_outcome_counts.get(oc, 0) + cnt
        if ss.get("has_undetermined"):
            batch_has_undetermined = True
        # Check for blocked, skipped_live, or scenario error.
        if ss.get("error"):
            batch_has_blocked_or_error = True
        else:
            for rr in ss.get("runs", []):
                outcome = rr.get("outcome", "")
                if outcome in ("blocked_prerequisite", "skipped_live"):
                    batch_has_blocked_or_error = True
                elif rr.get("errors") or ss.get("error"):
                    batch_has_blocked_or_error = True

    # --- Write batch summary ---
    batch_summary = {
        "batch_tag": tag,
        "scenario_count": len(scenario_summaries),
        "scenario_names": [s.get("scenario_name", "?") for s in scenario_summaries],
        "mode": mode.value,
        "dry_run": dry_run,
        "scenarios": scenario_summaries,
        "synthesis": synthesis,
        "outcome_counts": batch_outcome_counts,
        "has_undetermined": batch_has_undetermined,
        "has_blocked_or_error": batch_has_blocked_or_error,
    }

    batch_path = rr / f"{tag}-batch-summary.json"
    batch_path.parent.mkdir(parents=True, exist_ok=True)
    batch_path.write_text(
        json.dumps(batch_summary, indent=2, default=str), encoding="utf-8"
    )

    return batch_summary


# ---------------------------------------------------------------------------
# CLI entry point  (usable as `python -m sisypy.runner`)
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser for the Sisypy runner."""
    parser = argparse.ArgumentParser(
        description="Sisypy Runner — run agentic test scenarios.",
    )

    parser.add_argument(
        "scenarios",
        nargs="*",
        help="Scenario names to run (default: all scenarios in the scenarios directory).",
    )
    parser.add_argument(
        "--scenarios-dir",
        default=_DEFAULT_SCENARIOS_DIR,
        help=f"Directory of YAML scenario files (default: {_DEFAULT_SCENARIOS_DIR}).",
    )
    parser.add_argument(
        "--briefs-dir",
        default=_DEFAULT_BRIEFS_DIR,
        help=f"Directory of markdown brief files (default: {_DEFAULT_BRIEFS_DIR}).",
    )
    parser.add_argument(
        "--reports-dir",
        default=_DEFAULT_REPORTS_DIR,
        help=f"Root directory for output reports (default: {_DEFAULT_REPORTS_DIR}).",
    )
    parser.add_argument(
        "--mode",
        choices=["structural", "live"],
        default="structural",
        help="Execution mode: structural (no-GPU) or live (default: structural).",
    )
    parser.add_argument(
        "--actor",
        choices=["fake", "hermes", "deepseek-subagent", "subagent-launcher"],
        default="fake",
        help=(
            "Actor dispatcher: fake (scripted), hermes (direct DeepSeek API), "
            "or deepseek-subagent (DeepSeek V4 Pro via subagent-launcher) "
            "(default: fake)."
        ),
    )
    parser.add_argument(
        "--tag",
        default=_DEFAULT_TAG,
        help=f"Human-readable label for report grouping (default: {_DEFAULT_TAG}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Load and render scenarios but skip actor dispatch.",
    )
    parser.add_argument(
        "--cross-diff",
        action="store_true",
        help="Run cross-assessor diff for each agent.",
    )
    parser.add_argument(
        "--tags",
        nargs="*",
        help="Filter scenarios by tag(s).",
    )
    parser.add_argument(
        "--var",
        nargs="*",
        help="Variable substitutions for brief rendering (KEY=VALUE format).",
    )
    parser.add_argument(
        "--parallel",
        action="store_true",
        default=True,
        help="Run scenarios in parallel (default: True).",
    )
    parser.add_argument(
        "--no-parallel",
        action="store_true",
        help="Run scenarios sequentially.",
    )
    parser.add_argument(
        "--reassess",
        metavar="EVIDENCE_DIR",
        default=None,
        help=(
            "Reassess a frozen evidence directory without re-running the actor. "
            "When provided, scenario running arguments (--actor, --mode, --tag, "
            "etc.) are ignored."
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Print progress events (phase boundaries) to stderr as JSON lines.",
    )
    parser.add_argument(
        "--capture-interval-sec",
        type=float,
        default=None,
        help=(
            "Interval in seconds for periodic evidence snapshots during "
            "long-running actor dispatches (default: disabled). "
            "Snapshots are best-effort; a failed interval capture does not "
            "kill the actor or crash the run."
        ),
    )

    return parser


def _cli_entry_point(
    adapter: AgenticProjectAdapter,
    argv: list[str] | None = None,
) -> dict[str, Any]:
    """CLI entry point: parse arguments, load scenarios, run the harness.

    Args:
        adapter: Project-specific adapter instance.
        argv: Command-line arguments (default: sys.argv[1:]).

    Returns:
        Batch summary dict.
    """
    parser = _build_arg_parser()
    args = parser.parse_args(argv or sys.argv[1:])

    # --reassess: skip scenario loading and dispatch, just reassess frozen evidence.
    if args.reassess:
        from sisypy.reassess import reassess_evidence
        result = reassess_evidence(
            args.reassess,
            adapter=adapter,
            run_llm=False,
        )
        print(json.dumps(result, indent=2, default=str))
        return result

    # Parse variables.
    variables: dict[str, str] = {}
    if args.var:
        for pair in args.var:
            if "=" in pair:
                k, v = pair.split("=", 1)
                variables[k] = v

    mode = RunMode(args.mode)
    use_parallel = args.parallel and not args.no_parallel

    # --verbose: install progress_callback that prints JSON lines to stderr.
    progress_callback = None
    if args.verbose:

        def _stderr_progress(event: dict[str, Any]) -> None:
            import json as _json
            print(_json.dumps(event, default=str), file=sys.stderr)

        progress_callback = _stderr_progress

    return run_all(
        adapter=adapter,
        scenarios_dir=Path(args.scenarios_dir),
        briefs_dir=Path(args.briefs_dir),
        reports_root=Path(args.reports_dir),
        mode=mode,
        actor=args.actor,
        tag=args.tag,
        names=args.scenarios if args.scenarios else None,
        tags=args.tags,
        variables=variables,
        dry_run=args.dry_run,
        run_cross_diff=args.cross_diff,
        parallel=use_parallel,
        capture_interval_sec=args.capture_interval_sec,
        progress_callback=progress_callback,
    )


def main(argv: list[str] | None = None) -> dict[str, Any]:
    """Run the Sisypy CLI with the no-op default adapter.

    Returns a dict for programmatic callers.  For CI / recurring-run
    workflows that need a non-zero exit code on undetermined outcomes,
    use ``console_main()`` instead.
    """

    from sisypy.adapters import FakeProjectAdapter

    return _cli_entry_point(FakeProjectAdapter(), argv=argv)


def console_main(argv: list[str] | None = None) -> None:
    """Run the Sisypy CLI with the no-op default adapter and exit.

    Prints the result as JSON and exits with ``summary_exit_code()``.
    This is the recommended entry point for ``python -m sisypy`` and
    the ``sisypy`` console script.  It never returns.
    """

    from sisypy.public_api import console_cli
    from sisypy.adapters import FakeProjectAdapter

    console_cli(FakeProjectAdapter(), argv=argv)


# ---------------------------------------------------------------------------
# __main__ block
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(json.dumps(main(), indent=2, default=str))
