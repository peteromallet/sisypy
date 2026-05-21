# Sisypy Undetermined + Recurring Run Semantics

## Outcome

Make insufficient evidence and recurring operation first-class in Sisypy instead of only documented as philosophy. The runner, assessor/check outputs, summaries, and docs should distinguish pass, fail, blocked, violation, fake no-op, and undetermined states clearly enough for agents and CI-like workflows.

## Scope

In scope:

- Add or wire first-class `UNDETERMINED` / insufficient-evidence semantics where appropriate.
- Update assessor output contract so insufficient evidence can be reported without collapsing to pass or ordinary fail.
- Update deterministic/universal checks toward tri-state or explicit insufficient-evidence reporting where feasible.
- Ensure aggregate summaries preserve undetermined outcomes.
- Add a machine-readable run summary affordance if the current runner lacks one.
- Define and document an exit-code contract suitable for recurring runs.
- Improve capture-gap representation if needed so missing evidence can be detected by agents and assessors.
- Update `docs/evidence.md`, `docs/running-and-maintaining.md`, `docs/api.md`, roadmap, and skills as needed.
- Preserve compatibility with fake no-op, compare, and reassessment flows.

Out of scope:

- Rewriting the whole runner.
- Changing the public scenario YAML shape beyond what is necessary for the semantics.
- Adding a full CI workflow unless it is trivial documentation only.
- Expensive/live actor integrations.

## Locked Decisions

- Insufficient evidence must not be silently treated as success.
- Insufficient evidence should not be indistinguishable from ordinary product failure.
- Existing fake/no-op semantics remain a plumbing outcome, not a product failure.
- Frozen evidence remains the source of truth; no live-state reassessment.

## Open Questions For Prep

- Where exactly should `undetermined` live: `ScenarioOutcome`, assessor item status, check status, aggregate summary, or all of these?
- What backward-compatible shape should assessor results use?
- What exit-code contract best matches current runner behavior without surprising existing users?
- What is the smallest machine-readable summary that recurring agents need?
- How should capture gaps be represented without breaking existing evidence packs?

## Done Criteria

- `PYENV_VERSION=3.11.11 pytest -q` passes.
- Existing compare/reassess tests still pass.
- Docs and skills no longer overclaim that `undetermined` is fully implemented if any piece remains roadmap-only.
- The recurring-run guidance maps to concrete CLI/API behavior.
