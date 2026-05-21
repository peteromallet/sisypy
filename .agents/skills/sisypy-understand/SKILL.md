---
name: sisypy-understand
description: >
  Explain Sisypy and agentic testing before design or implementation. Use
  when someone asks what Sisypy is for, how it differs from unit/integration
  tests, what "evidence over narrative" means, or whether a workflow belongs
  in Sisypy.
---

# Sisypy Understand

Use this skill for the mental model only. Do not scaffold files or write
scenarios from this skill alone.

Read:

- `docs/applied-philosophy.md`

> **Doc paths**: All `docs/` references below are relative to the Sisypy repository root (the directory containing `pyproject.toml`). If this skill is symlinked into `~/.claude/skills/` or `~/.codex/skills/`, resolve the symlink or locate the cloned repo at the symlink target to find the referenced files.

## Workflow

1. Explain the user-agent-system loop.
2. Decide whether the user's target workflow needs agentic testing or an
   ordinary deterministic test.
3. Explain actor / harness / assessor roles.
4. Explain evidence over narrative and `undetermined`.
5. Hand off to `sisypy-design` if Sisypy is appropriate.

## Output

```text
What Sisypy would test:
Why ordinary tests are or are not enough:
Appropriate next skill:
```
