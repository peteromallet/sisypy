# Evidence Model

Sisypy grades frozen evidence, not actor narrative. After dispatch, the runner writes an evidence pack and every deterministic checker or assessor reads that pack instead of live repo state.

Use this as a reference when a scenario cannot prove its claims, when an assessor should return `undetermined`, or when capture/proof behavior is unclear. For the broader method, read [Applied Philosophy](applied-philosophy.md). For writing scenario rubrics, read [Scenario Authoring](scenario-authoring.md). For operating an existing suite, read [Running and Maintaining](running-and-maintaining.md).

## Evidence Pack Anatomy

Each run produces a frozen evidence pack. The canonical path template is:

```text
out/agentic/reports/<tag>-<scenario>/evidence/<run_id>/
```

| File | Description |
|------|-------------|
| `manifest.json` | Run metadata, capture trigger, evidence confidence, capture_gaps |
| `brief.md` | The user-shaped brief given to the actor |
| `report.md` | Actor's final report |
| `stdout.log` | Combined stdout from the actor dispatcher |
| `stderr.log` | Combined stderr from the actor dispatcher |
| `actions.jsonl` | Structured action log (one `ActionLogEntry` per line) |
| `command_log.jsonl` | Legacy command log (backward compatibility) |
| `tree_before.txt` | Recursive file listing before the run |
| `tree_after.txt` | Recursive file listing after the run |
| `git_status_before.txt` | `git status` snapshot before the run |
| `git_status_after.txt` | `git status` snapshot after the run |
| `git_diff.patch` | Git diff since the run started |
| `capture.notes` | Best-effort per-step capture notes (skip/failure details) |
| `capture/<adapter>/` | Adapter-specific captured evidence |
| `commands/` | Per-command stdout/stderr sidecar files |
| `intervals/<timestamp>/` | Periodic interval snapshots (when enabled) |

## Capture Triggers and Partial Capture

`capture_evidence()` records a `capture_trigger` in `manifest.json`.

Valid values:

- `normal`
- `blocked`
- `timeout`
- `failure`
- `interval`

Best-effort partial capture contract:

- each capture step is independently best-effort;
- missing files produce `skip ...` notes in `capture.notes`, not exceptions;
- failed or timed-out dispatches still produce evidence packs with whatever artifacts exist;
- capture notes distinguish missing artifacts from capture errors.

## Capture Gaps and Manifest Anatomy

`manifest.json` includes a `capture_gaps` field — a structured dict keyed by capture step that records what evidence was expected but not captured. This is an additive, structured index derived from raw `capture.notes` entries during `capture_evidence()`.

```json
{
  "capture_gaps": {
    "report_md": {
      "reason": "skip (not generated)",
      "source": "capture.notes"
    },
    "tree_after": {
      "reason": "skip (tree snapshot failed)",
      "source": "capture.notes"
    }
  }
}
```

Key design points:

- `capture.notes` raw list remains unchanged — `capture_gaps` is an additive structured index.
- `capture_gaps` is computed before `manifest.json` is written, so agents and assessors can read it without parsing raw notes.
- Older evidence packs without a `capture_gaps` key load with `capture_gaps={}`.
- Both `capture_gaps` and `capture.notes` are included in the evidence context passed to the assessor.

| Field | Type | Description |
|-------|------|-------------|
| `key` | `str` | Capture step name (e.g. `report_md`, `tree_after`, `git_diff`) |
| `reason` | `str` | Why the capture step was skipped or failed |
| `source` | `str` | Origin of the gap record (always `capture.notes`) |

## Periodic / Interval Capture

`run_scenario()` and `run_all()` accept `capture_interval_sec: float | None = None`. When positive and the adapter supports it, a daemon thread writes interval snapshots under:

```text
evidence/<slug>/intervals/<timestamp>/
```

Each interval snapshot includes:

| File | Description |
|------|-------------|
| `manifest.json` | `capture_trigger='interval'`, interval sequence, ids, timestamps |
| `tree_after.txt` | Recursive file listing at snapshot time |
| `git_diff.patch` | Git diff since the run started |
| `capture.notes` | Skip/failure notes |

Interval capture is best-effort. A failed snapshot appends a non-fatal message to `run.errors` and never kills the actor. The authoritative evidence pack is still the final post-dispatch capture.

## Structured Action Evidence

Every run writes:

```text
evidence/<slug>/actions.jsonl
```

Each line is an `ActionLogEntry` wrapping a `CommandAction`.

```json
{"seq": 1, "timestamp": "2026-05-20T10:44:10Z", "action": {"action_id": "0001", "action_type": "command", "command": "python -m vibecomfy.cli validate recipes/demo.py", "cwd": "/workspace", "exit_code": 0, "duration_sec": 1.84, "source": "dispatcher", "evidence_confidence": "high"}}
```

`CommandAction` fields:

| Field | Type | Description |
|-------|------|-------------|
| `action_id` | `str` | Unique id within the run |
| `action_type` | `str` | Always `command` in v1 |
| `command` | `str` | Full command text |
| `cwd` | `str` | Working directory |
| `exit_code` | `int | None` | Subprocess exit code |
| `duration_sec` | `float | None` | Wall-clock duration |
| `stdout_path` | `str` | Relative stdout sidecar path |
| `stderr_path` | `str` | Relative stderr sidecar path |
| `stdout_preview` | `str` | Short stdout preview |
| `stderr_preview` | `str` | Short stderr preview |
| `source` | `str` | `dispatcher`, `stderr-parse`, or `legacy-command-log` |
| `evidence_confidence` | `str` | `high`, `low`, or `unknown` |
| `metadata` | `dict` | Extra fields |

Per-command stdout/stderr sidecars are written under:

```text
evidence/<slug>/commands/
```

## Legacy Command Logs

`command_log.jsonl` is preserved for backward compatibility. When structured actions are unavailable, Sisypy normalizes legacy rows into low-confidence `CommandAction` entries and writes both files.

## Evidence Confidence

`evidence_confidence` in `manifest.json` is one of:

- `high`: all captured actions are high-confidence structured dispatcher capture;
- `low`: at least one low-confidence action source;
- `mixed`: combination of high and low sources;
- `supported_empty`: action capture is supported but no actions were recorded;
- `unknown`: action capture is unsupported or unavailable.

Low-confidence commands support forbidden-command detection only. They cannot prove `validated`, `runtime_proven`, or `artifact_proven`.

## Proof Ladder

The proof ladder is derived from structured evidence, not actor prose:

1. `authored`: git diff or tree changes show new/modified files.
2. `compiled`: compiled API artifact is present.
3. `validated`: validation command evidence exists.
4. `runtime_attempted`: runtime command evidence exists.
5. `runtime_proven`: runtime logs show completion.
6. `artifact_proven`: output artifact files exist.
7. `quality_assessed`: explicit quality review appears in runtime logs.

## Claim Rules

Claim detection uses `ClaimRule` records with regex patterns and exclude patterns. Claims are detected only from claim-bearing report sections such as `What I did`, `Evidence`, `Results`, `Deliverables`, `Work done`, and `Actions taken`.

Non-claim sections are excluded, including `How to verify`, `Open risks`, `Limitations`, `Next steps`, `Future work`, `Caveats`, `Notes`, and `Appendix`.

Forbidden-command detection scans command evidence (`stderr.log`, `command_log.jsonl`, `actions.jsonl`), never narrative report text.
