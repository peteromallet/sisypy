# Roadmap

Use this for known implementation gaps after reading the operational docs. The main skill path is [Applied Philosophy](applied-philosophy.md) → [Scenario Design](scenario-design.md) → [Embedding Sisypy](embedding.md) → [Scenario Authoring](scenario-authoring.md) → [Running and Maintaining](running-and-maintaining.md).

## First-Class Undetermined Assessment Results

First-class `UNDETERMINED` / insufficient-evidence semantics are now implemented. The following pieces are done:

- `ScenarioOutcome.UNDETERMINED` enum value with round-trip support.
- Universal checks return `{passed: False, undetermined: True, severity: 'undetermined'}` for missing-evidence cases.
- `run_all_checks()` returns `any_undetermined` alongside `all_passed` and `checks`.
- `_deterministic_assessment()` (in both runner and reassess) returns `undetermined: bool` and `undetermined_items: list[dict]`.
- `_determine_outcome()` returns `UNDETERMINED` for missing-evidence cases; fatal no-report dispatch errors remain `FAILED`.
- Summaries include `outcome_counts`, `has_undetermined`, and `has_blocked_or_error`.
- Markdown reports show `❓ UNDETERMINED` distinct from `❌ FAIL`.
- Assessor prompt supports `passed`, `failed`, or `undetermined` verdicts with `evidence_checked` and `missing_capture`.
- `summary_exit_code()` returns exit codes 0/1/2/3 with precedence 3 > 1 > 2 > 0.
- `console_cli()` exits with machine-readable codes; `cli()` remains dict-returning.
- `summit()` raises `BoulderError` with undetermined-specific messages.
- `capture_gaps` in evidence pack manifests with backward-compatible loading.

Remaining deferred items:

- `--futile` runner wiring (affects `absurd()` metadata, expected-failure runs, and batch summaries).
- Dispatch-capable `push()` (currently a record-construction helper; `run_scenario()`/`run_all()` handle dispatch).

## `--futile` Runner Wiring

`--futile` is documented as a public CLI concept for running known-failing scenarios intentionally, but it is not fully wired into the runner yet.

When implemented:

- `--futile` should interact with `absurd()` metadata, expected-failure runs, and batch summaries;
- known-failing scenarios should not count as harness failures;
- reports should distinguish futile runs from ordinary runs.

## Dispatch-Capable `push()`

`sisypy.push(agent, test)` currently creates a `Push` / `ActorRun` record only. Use `run_scenario()` or `run_all()` for dispatch.

A future pass should decide whether `push()` becomes the primary dispatch API or remains a conservative record-construction helper. If it becomes dispatch-capable, it must route through the same evidence-freezing and assessment path as the runner.
