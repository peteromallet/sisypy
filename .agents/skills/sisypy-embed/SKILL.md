---
name: sisypy-embed
description: >
  Embed Sisypy into a repository after scenario candidates are approved.
  Use when the user wants adapter.py, runner.py, first scenario YAML, first
  brief markdown, and a fake/no-GPU structural run that proves harness
  plumbing and evidence capture work.
---

# Sisypy Embed

Use this skill only after the user has approved the first scenario candidate.
If scenarios are not chosen yet, use `sisypy-design`.

Read:

- `docs/embedding.md`
- `docs/api.md`

> **Doc paths**: All `docs/` references below are relative to the Sisypy repository root (the directory containing `pyproject.toml`). If this skill is symlinked into `~/.claude/skills/` or `~/.codex/skills/`, resolve the symlink or locate the cloned repo at the symlink target to find the referenced files.

## Workflow

1. Install or import Sisypy from the repository root, not the inner package
   directory.
2. Create `tests/agentic/runner.py` using `sisypy.cli()` (dict-returning) or
   `sisypy.console_cli()` (exits with machine-readable exit code; preferred
   for CI/recurring shells). See `docs/api.md` for the exit-code contract.
3. Create `tests/agentic/adapter.py` by subclassing `FakeProjectAdapter`.
4. Add the first scenario YAML and user-shaped brief.
5. Run:

```bash
python -m tests.agentic.runner --help
python -m tests.agentic.runner first_path --actor fake --mode structural --no-parallel --verbose
```

6. Inspect `out/agentic/reports/`.
7. If the evidence pack cannot prove the claims, fix capture before adding
   more scenarios.

## Output

```text
Files created/changed:
Commands run:
Fake structural result:
Evidence pack path:
What the pack proves:
Capture gaps:
Next skill:
```
