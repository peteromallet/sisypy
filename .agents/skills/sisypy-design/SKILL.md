---
name: sisypy-design
description: >
  Design Sisypy scenario candidates for a repository. Use when adding Sisypy
  to a new repo or expanding coverage: inspect real user workflows, map
  agent-facing surfaces, identify failure modes and evidence needs, produce
  a scenario-candidate table, then stop for user confirmation before
  implementation.
---

# Sisypy Design

Use this skill after the mental model is clear and before writing adapter or
scenario files.

Read:

- `docs/scenario-design.md`
- `docs/applied-philosophy.md` if the philosophy is unclear

> **Doc paths**: All `docs/` references below are relative to the Sisypy repository root (the directory containing `pyproject.toml`). If this skill is symlinked into `~/.claude/skills/` or `~/.codex/skills/`, resolve the symlink or locate the cloned repo at the symlink target to find the referenced files.

## Workflow

1. Inspect the target repo's README, docs, examples, CLIs, tools, issues,
   support notes, and real usage traces.
2. Run focused passes: user-path researcher, surface mapper, failure-mode
   analyst, evidence auditor, negative-scenario designer.
3. Synthesize the scenario-candidate table.
4. Recommend the first scenario and matched negative/recovery scenario.
5. Stop and emit `AWAITING_USER_CONFIRMATION`. Do not advance until the user responds.

Do not mutate files, spend money, run live services, or implement scenarios
during discovery unless the user explicitly asks.

## Output

```text
Scenario-candidate table:
Recommended first scenario:
Why this first:
Minimum evidence pack:
Matched negative/recovery:
Open questions:
```

## Handoff

After confirmation, use `sisypy-embed` and `sisypy-author`.
