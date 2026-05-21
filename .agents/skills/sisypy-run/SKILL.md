---
name: sisypy-run
description: >
  Run and maintain an existing Sisypy suite. Use when the user asks to run
  agentic tests, check current status, compare results, triage failures,
  interpret undetermined outcomes, or decide which scenarios to add, revise,
  retire, or promote to recurring coverage.
---

# Sisypy Run

Use this skill when Sisypy is already embedded in the target repo.

Read:

- `docs/running-and-maintaining.md`
- `docs/evidence.md`

> **Doc paths**: All `docs/` references below are relative to the Sisypy repository root (the directory containing `pyproject.toml`). If this skill is symlinked into `~/.claude/skills/` or `~/.codex/skills/`, resolve the symlink or locate the cloned repo at the symlink target to find the referenced files.

## Workflow

1. Prefer cheap structural scenarios first.
2. Run the project runner, not private Sisypy internals.
3. Use `console_cli()` for CI/recurring runs — it exits with a machine-readable
   code (0=pass, 1=fail, 2=undetermined, 3=blocked/error).
4. Inspect failed and undetermined evidence packs.
5. Reassess frozen evidence if rubric or assessor behavior changed.
6. Run real actors only after structural capture is healthy.
7. Run live or paid scenarios only when prerequisites and budget are explicit.
8. Classify issues as product failure, actor failure, harness/capture gap,
   blocked prerequisite, or assessor uncertainty.

## Output

```text
Sisypy maintenance report
Date:
Repo/ref:
Command(s):
Summary:
New regressions:
Undetermined items:
Evidence gaps:
Likely product blockers:
Likely harness issues:
Scenario changes recommended:
Next run:
```
