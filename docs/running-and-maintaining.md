# Running And Maintaining

Use this skill when Sisypy is already embedded in a repo and you need to run, interpret, or evolve the suite.

If the repo does not have a Sisypy runner yet, read [Embedding Sisypy](embedding.md). If you need to add new scenarios, read [Scenario Design](scenario-design.md) and [Scenario Authoring](scenario-authoring.md). If you need to understand proof levels or evidence files, read [Evidence Model](evidence.md).

## Goal

Run the suite as an operational check, then produce a concise maintenance report.

The output is:

```text
run id
commands run
scenario outcomes
failures / regressions / undetermined items
evidence gaps
recommended fixes
scenarios to add, revise, retire, or promote
```

## Run Order

Prefer this sequence:

1. Run cheap structural scenarios.
2. Inspect failed or undetermined evidence packs.
3. Reassess frozen evidence if rubric or assessor behavior changed.
4. Run real actors only after structural capture is healthy.
5. Run live or paid scenarios only when prerequisites and budget are explicit.

Example commands:

```bash
python -m tests.agentic.runner --help
python -m tests.agentic.runner --actor fake --mode structural --verbose
python -m tests.agentic.runner <scenario> --actor fake --mode structural --no-parallel --verbose
python -m tests.agentic.runner --reassess out/agentic/reports/<tag>-<scenario>/evidence/<run>
```

Use the project runner, not private Sisypy internals.

## Interpret Outcomes

Classify each issue before fixing it.

| Outcome | Meaning | Next action |
|---|---|---|
| Passed | Evidence supports the required proof level | Keep, compare against prior runs |
| Failed | Evidence disproves the required behavior | Fix product, agent instructions, or scenario |
| Undetermined | Evidence cannot prove or disprove the claim | Fix capture, instrumentation, or rubric |
| Blocked prerequisite | Required env/service/model/key is missing | Document prerequisite or skip appropriately |
| Violation | Structural run attempted forbidden live/cost action | Tighten command policy or actor instructions |
| Fake no-op | Plumbing run completed without real actor work | Use only as harness validation |

Do not collapse undetermined into pass or ordinary failure. It means the test cannot currently know.

## Exit Codes

Sisypy returns structured exit codes suitable for CI and recurring-shell workflows. Use `console_cli()` (which exits with the code) or `summary_exit_code()` (which returns the int for programmatic callers).

| Code | Meaning |
|------|---------|
| 0 | All runs passed, or only fake no-op plumbing runs |
| 1 | Any failed or violation outcome present |
| 2 | One or more undetermined outcomes AND zero failures, violations, blocked, or errors |
| 3 | Blocked prerequisite, skipped live, scenario error, or runner exception (infrastructure/setup failure) |

Precedence: 3 > 1 > 2 > 0.

### Recurring-Run Interpretation

For recurring (CI/scheduled) runs, read the summary dict:

- `outcome_counts`: dict mapping each outcome string to its count (e.g. `{"passed": 3, "undetermined": 1}`).
- `has_undetermined`: `True` when any run outcome is `undetermined`.
- `has_blocked_or_error`: `True` when any run has `blocked_prerequisite`, `skipped_live`, or an error key (batch summaries only).

A recurring runner should:

1. Exit non-zero on undetermined (code 2) so the CI surface can distinguish "not enough evidence" from "pass" (code 0) and "failure" (code 1).
2. Treat code 3 as an infrastructure alert (missing API key, network down, prereq not met).
3. Compare `outcome_counts` against the last known-good run when available.

## Monthly Or Periodic Review

For recurring runs, produce this report:

```text
Sisypy maintenance report
Date:
Repo/ref:
Command(s):
Summary:
- passed:
- failed:
- undetermined:
- blocked:

New regressions:
Evidence gaps:
Likely product blockers:
Likely harness issues:
Scenario changes recommended:
Next run:
```

Compare against the last known-good run when available.

## Scenario Lifecycle

Use these labels mentally, even if the project has no metadata for them yet:

- **Draft**: fake/structural only; proving capture.
- **Active**: regularly run; failures matter.
- **Periodic**: run on a schedule or before important releases.
- **Live**: uses real actors, services, models, or paid resources.
- **Blocked**: waiting on prerequisite, instrumentation, or product support.
- **Retired**: user path no longer matters.

When a scenario changes, keep the reason in the maintenance report. Silent rubric drift destroys trend value.

## Adding New Coverage From A Failure

When a user reports a real failure:

1. Capture the user ask in their words.
2. Add it to the scenario-candidate table.
3. Decide whether it is positive, negative, or recovery.
4. Identify what frozen evidence would have caught it.
5. Add capture before adding the rubric if needed.
6. Author the smallest scenario that would have failed before the fix.

## Handoff

- Use [Scenario Design](scenario-design.md) to pick new scenarios.
- Use [Scenario Authoring](scenario-authoring.md) to write or revise them.
- Use [Evidence Model](evidence.md) to debug capture and proof-level questions.
- Use [Roadmap](roadmap.md) for deferred features not yet implemented.
