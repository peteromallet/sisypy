# Sisypy Skills/Docs Usability Polish

## Outcome

Tighten Sisypy's agent-facing skills and docs based on the five DeepSeek agent-perspective reviews so a first-time coding agent can follow the skill path without ambiguity.

## Scope

In scope:

- Canonicalize the scenario-candidate table contract everywhere it appears.
- Define `Shape` consistently as `positive / negative / recovery`.
- Strengthen the user-confirmation stop gate with an explicit `AWAITING_USER_CONFIRMATION` protocol.
- Add repo-root/doc-path guidance to skills whose files may be synced into `~/.claude/skills` or `~/.codex/skills`.
- Fix the embedding guide's import/path mismatch for the documented `tests/agentic/` layout.
- Add an evidence-pack anatomy block showing the expected files under `out/agentic/reports/.../evidence/<run>/`.
- Update README and cross-references as needed.
- Update focused tests for the skill sync/docs contract where useful.

Out of scope:

- Changing evaluator semantics.
- Adding `UNDETERMINED` to implementation schemas.
- Adding summary JSON, exit-code contracts, baseline comparison, or CI semantics.
- Broad package restructuring.

## Locked Decisions

- Canonical candidate table:

  ```text
  | # | User ask | Canonical path | Failure mode | Evidence to capture | Shape |
  ```

- `Shape = positive / negative / recovery`.
- The design phase must stop after the scenario-candidate deliverable and print `AWAITING_USER_CONFIRMATION`.
- For the documented `tests/agentic/` layout, the runner template should import the adapter in a way that works with `python -m tests.agentic.runner`.
- Skills are canonical in `.agents/skills/` and synced by `scripts/sync_agent_skills.py`.

## Done Criteria

- `PYENV_VERSION=3.11.11 pytest -q` passes.
- `PYENV_VERSION=3.11.11 python scripts/sync_agent_skills.py` passes.
- README copyable prompt references skill sync and the explicit confirmation gate.
- No stale `docs/philosophy.md` references.
- New/updated docs remain focused on their individual skill slice.
