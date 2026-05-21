---
name: sisypy-author
description: >
  Author or revise concrete Sisypy scenarios: write user-shaped briefs,
  scenario YAML, evidence-backed enforced/graded/observed rubric items,
  matched negative or recovery cases, and reviewer checks.
---

# Sisypy Author

Use this skill to turn an approved scenario candidate into runnable coverage.

Read:

- `docs/scenario-authoring.md`
- `docs/evidence.md`

> **Doc paths**: All `docs/` references below are relative to the Sisypy repository root (the directory containing `pyproject.toml`). If this skill is symlinked into `~/.claude/skills/` or `~/.codex/skills/`, resolve the symlink or locate the cloned repo at the symlink target to find the referenced files.

## Workflow

1. Confirm the user ask and scenario shape.
2. Write the brief first.
3. Run the fake or structural path before finalizing the rubric.
4. Inspect the evidence pack.
5. Write rubric items only for claims the evidence can prove or disprove.
6. Mark insufficiently captured claims as `undetermined`; do not pass them.
   Use `capture_gaps` in `manifest.json` to find structured missing-evidence
   records. The assessor now supports `undetermined` verdicts with
   `evidence_checked` and `missing_capture` fields.
7. Add a matched negative or recovery scenario for important positive paths.
8. Run a reviewer pass: can each enforced item be answered without trusting
   the actor report?

## Output

```text
Scenario files:
Brief summary:
Rubric summary:
Evidence requirements:
Negative/recovery companion:
Reviewer concerns:
Verification:
```
