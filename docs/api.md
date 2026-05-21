# API Reference

Use this as a stable-name reference while implementing a runner, adapter, tests, or reassessment tooling. If you are choosing scenarios, start with [Scenario Design](scenario-design.md). If you are wiring a repo, start with [Embedding Sisypy](embedding.md). If you are operating an existing suite, use [Running and Maintaining](running-and-maintaining.md).

## Public Naming Layer

```python
import sisypy
from sisypy import Boulder, Hill, Push, BoulderError, summit

@sisypy.eternal
def test_resume_after_crash():
    ...

boulder = Boulder(name="resume_after_crash", brief="Resume a failed run.")
attempt = sisypy.push("fake", boulder)
summit(attempt, min_level="authored")
```

- `Boulder`: alias for `Scenario`.
- `Hill`: lightweight repo/system/environment descriptor.
- `Push`: alias for `ActorRun`.
- `sisypy.push(agent, test)`: creates a `Push` record. Use `run_scenario()` or `run_all()` for dispatch.
- `sisypy.eternal`: marker for tests that belong in the recurring suite.
- `summit()`: assertion that a push reached a minimum proof level. Raises `BoulderError` with outcome-specific messages: for `undetermined` outcomes, the message includes `"undetermined (insufficient evidence)"`, undetermined item names, and capture gap keys.
- `BoulderError`: raised when a push did not reach the requested level or is undetermined.
- `sisypy.absurd()`: metadata marker for explicitly expected futility.
- `boulder.toml`: package/project configuration file.

## CLI

```bash
python -m sisypy --help
python -m sisypy.runner --help
```

Project runners should embed Sisypy through `sisypy.cli()` rather than importing private runner internals.

## Public CLI Helpers

```python
from sisypy import cli, console_cli, summary_exit_code, build_cli_parser, run_from_args
```

### `sisypy.cli(adapter, *, argv=None, configure_parser=None, before_run=None)`

Stable public CLI entry point for **programmatic callers**. It builds the argument parser, parses `argv`, optionally customizes the parser via `configure_parser`, runs a pre-execution hook via `before_run`, then dispatches through `run_from_args()`.

- `configure_parser(parser)`: add project-specific flags.
- `before_run(args)`: apply project-specific setup after parsing and before dispatch.

Returns the batch summary dict from `run_all()`. Raises `SystemExit` on `--help`.

Use `cli()` when you need the raw summary dict for further processing. Use `console_cli()` (below) for CI and recurring-shell workflows that need a machine-readable exit code.

### `sisypy.console_cli(adapter, *, argv=None, configure_parser=None, before_run=None)`

Stable public CLI entry point for **CI and recurring-shell workflows**. Same signature as `cli()`, but calls `cli()`, prints the result as JSON to stdout once, then calls `sys.exit(summary_exit_code(result))`. Does not return.

This is the recommended entry point for non-interactive environments. Programmatic callers that need the raw dict should use `cli()` instead.

### `sisypy.summary_exit_code(summary) -> int`

Compute a machine-readable exit code from a run summary dict. Accepts single-scenario summaries (with `runs` key), batch summaries (with `scenarios` key), and flat result lists (with `results` key).

Precedence (highest wins):

| Code | Condition |
|------|-----------|
| 3 | Blocked prerequisite, skipped live, scenario error, or runner exception |
| 1 | Any failed or violation outcome present |
| 2 | One or more undetermined outcomes AND zero failures, violations, blocked, or errors |
| 0 | All runs passed, or only fake no-op plumbing runs |

### `sisypy.build_cli_parser(adapter, *, configure_parser=None)`

Build the shared argument parser. Projects can add flags via `configure_parser`.

### `sisypy.run_from_args(adapter, args)`

Execute from an already-parsed `argparse.Namespace`. It forwards `--verbose`, `--capture-interval-sec`, and `--reassess` to the runner.

## Scenario and Brief Helpers

Public wrappers are available for projects and tests that need to load scenario data directly:

```python
from sisypy.runner import load_scenario, load_scenarios_from_dir, render_brief
```

The old underscore-prefixed helpers remain for backward compatibility but are not the embedding contract.

## Run Workdir

`ActorRun.workdir` records the actor working directory for a run as a JSON-friendly `str | None`.

```python
from sisypy import push

run = push("fake", boulder, workdir="/tmp/scratch-123")
assert run.workdir == "/tmp/scratch-123"
```

Runners set `run.workdir` automatically before dispatch. If `workdir` is `None` in old packs, callers should fall back to the adapter's `repo_root`.

## Progress Events and `--verbose`

`run_scenario()` and `run_all()` accept:

```python
progress_callback: Callable[[dict[str, Any]], None] | None = None
```

The callback receives JSON-serializable events. Common keys:

| Key | Type | Description |
|-----|------|-------------|
| `event` | `str` | Phase name |
| `timestamp` | `str` | ISO-8601 UTC timestamp |
| `scenario_name` | `str` | Scenario id |
| `agent_id` | `str` | Agent id |
| `run_id` | `str` | Unique run id |
| `mode` | `str` | `structural` or `live` |
| `tag` | `str` | Report tag |
| `message` | `str` | Optional human-readable message |

Emitted events include:

- `scenario_start`, `scenario_end`
- `prime_start`, `prime_end`
- `dispatch_start`, `dispatch_end`
- `interval_capture_start`, `interval_capture_end`, `interval_capture_failure`
- `capture_start`, `capture_end`
- `checks_start`, `checks_end`
- `assess_start`, `assess_end`
- `report_write_start`, `report_write_end`

Callbacks are caller-owned. If a callback raises, the exception is logged and the run continues.

Pass `--verbose` to print one JSON line per event to stderr:

```bash
python -m tests.agentic.runner <scenario> --verbose
```

## Fake Actor Semantics

`FakeActorDispatcher` is a synchronous, deterministic no-op for harness plumbing. It proves prime/capture/check/assess wiring, not product behavior.

Fake runs use:

```python
from sisypy import ScenarioOutcome
assert ScenarioOutcome.FAKE_NO_OP.value == "fake_no_op"
```

Markdown reports label the fake dispatcher as a no-op plumbing check rather than pass/fail.

## `ActorRun.extras`

`ActorRun.extras: dict[str, Any]` carries adapter-level metadata for one run:

```python
from sisypy import Push

run = Push(scenario_name="demo", agent_id="hermes")
run.extras["gpu_type"] = "A100"
```

The default factory is independent per run and round-trips through JSON.

## Cross-Arm Comparison

`sisypy.compare(pack_a, pack_b)` reads two frozen evidence packs, recomputes deterministic checks from frozen files, and returns a structured verdict.

```python
from sisypy import compare

verdict = compare("path/to/pack_a", "path/to/pack_b")
```

Tie-breaking is conservative:

1. Higher proof level wins.
2. Fewer forbidden-command violations wins.
3. Fewer failed checks wins.
4. Otherwise, tie with low confidence.

When frozen ladder evidence is inconclusive, comparison returns `winning_arm='tie'` with `confidence='insufficient_evidence'`. It does not fall back to manifest claims to pick a winner.

## Summary Dict Shape

`run_scenario()` and `run_all()` return summary dicts with these keys:

### Single-Scenario Summary

| Key | Type | Description |
|-----|------|-------------|
| `scenario_name` | `str` | Scenario identifier |
| `scenario_tier` | `int` | Scenario tier |
| `tag` | `str` | Report tag |
| `mode` | `str` | `structural` or `live` |
| `dispatchers_used` | `list[str]` | Agent dispatchers used |
| `started_at` | `str` | ISO-8601 UTC start timestamp |
| `finished_at` | `str` | ISO-8601 UTC finish timestamp |
| `runs` | `list[dict]` | Per-agent run result entries |
| `outcome_counts` | `dict[str, int]` | Counts per outcome (e.g. `{"passed": 2, "undetermined": 1}`) |
| `has_undetermined` | `bool` | `True` when any run outcome is `undetermined` |

### Batch Summary (additional keys)

| Key | Type | Description |
|-----|------|-------------|
| `scenarios` | `list[dict]` | Per-scenario summaries |
| `outcome_counts` | `dict[str, int]` | Aggregated counts across all scenarios |
| `has_undetermined` | `bool` | `True` when any scenario has an undetermined run |
| `has_blocked_or_error` | `bool` | `True` when any run has `blocked_prerequisite`, `skipped_live`, or an error key |

### Per-Run Entry

Each `runs` entry includes:

| Key | Type | Description |
|-----|------|-------------|
| `outcome` | `str` | One of `passed`, `failed`, `undetermined`, `violation`, `blocked_prerequisite`, `skipped_live`, `fake_no_op` |
| `undetermined` | `bool` | Whether the deterministic assessment flagged insufficient evidence |
| `undetermined_items` | `list[dict]` | Items with `{check_name, detail}` from undetermined checks |
| `capture_gaps` | `dict` | Structured capture gaps from the evidence pack manifest |

## Reassessment

Frozen evidence packs can be reassessed without re-running the actor:

```bash
python -m sisypy --reassess out/agentic/reports/<tag>-<scenario>/evidence/<run>
```

Programmatic API:

```python
from sisypy.reassess import reassess_evidence

result = reassess_evidence("path/to/evidence_dir", adapter=my_adapter)
```

The original evidence pack is never mutated.
