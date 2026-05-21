---
name: sisypy-debug-evidence
description: >
  Diagnose a Sisypy evidence pack or suspicious assessment. Use when a
  scenario passed, failed, or became undetermined in a confusing way; when
  the user suspects narrative was graded instead of evidence; or when capture
  seems incomplete.
---

# Sisypy Debug Evidence

Use this skill for pack-level diagnosis, not scenario design.

Read:

- `docs/evidence.md`
- `docs/api.md`
- `docs/roadmap.md`

> **Doc paths**: All `docs/` references below are relative to the Sisypy repository root (the directory containing `pyproject.toml`). If this skill is symlinked into `~/.claude/skills/` or `~/.codex/skills/`, resolve the symlink or locate the cloned repo at the symlink target to find the referenced files.

## Workflow

1. Locate the evidence pack under `out/agentic/reports/.../evidence/<run>`.
2. Read the evidence pack anatomy in `docs/evidence.md#evidence-pack-anatomy`.
3. Check `manifest.json` `capture_gaps` for structured missing-evidence records;
   compare with raw `capture.notes` for full context.
4. Compare actor claims in `report.md` with command/action/file evidence.
5. Classify each contested claim as supported, contradicted, or undetermined.
6. Identify whether the issue is product behavior, actor behavior, harness
   capture, rubric wording, or assessor behavior.
7. Recommend the smallest fix.

## Output

```text
Evidence pack:
Claims checked:
Supported:
Contradicted:
Undetermined:
Capture gaps:
Rubric/assessor risks:
Recommended fix:
```
