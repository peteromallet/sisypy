# Scenario Authoring

Use this skill after the user has approved a scenario candidate and the repo has, or is about to have, a Sisypy runner.

If you have not chosen scenarios yet, read [Scenario Design](scenario-design.md). If you need to create the adapter and runner first, read [Embedding Sisypy](embedding.md). If you are operating an existing suite, read [Running and Maintaining](running-and-maintaining.md).

## Goal

Turn one approved user ask into a scenario that can be run, captured, assessed, and reviewed.

The output is:

```text
brief markdown
scenario YAML
evidence-capture additions if needed
rubric with enforced / graded / observed signals
one fake or structural run
review notes on false-positive risk
```

## Write The Brief First

The brief is the actor's only instruction. It should sound like a user asking for work, not a test author hinting at implementation.

Good brief:

```markdown
I need to add this repo's canonical image-generation path to our agentic test suite.

Please find the documented way to discover and validate that path. Set up the smallest structural check that proves the path is wired correctly without using GPU, network, or paid services. Do not claim success unless you can point to captured evidence.
```

Avoid:

- naming the exact rubric item the actor must satisfy;
- telling the actor which file to edit unless a real user would know that;
- asking for internals rather than a user outcome;
- hiding impossible requirements in the prompt.

## Run Before Rubric

Run the brief once with the fake or structural path before finalizing the rubric.

Inspect the evidence pack:

- `brief.md`
- `report.md`
- `stdout.log`
- `stderr.log`
- `actions.jsonl`
- `tree_before.txt`
- `tree_after.txt`
- `git_diff.patch`
- `capture.notes`
- `manifest.json`

If the evidence pack cannot prove the key claim, fix capture or instrumentation before adding the rubric item.

## Write The Rubric

Use the three signal tiers.

| Tier | Use for | Keep it honest |
|---|---|---|
| Enforced | Hard contracts that gate the scenario | 1-3 items, binary, evidence-backed |
| Graded | Semantic quality | cite evidence, never gate alone |
| Observed | Telemetry | record without pass/fail pressure |

Every rubric item should name the evidence it expects. If the evidence is missing, the outcome is `undetermined`, not pass and not fail.

Example:

```yaml
assessment:
  enforced:
    - id: used_project_runner
      question: Did the actor run the embedded project runner rather than calling Sisypy internals directly?
      evidence: [actions.jsonl, stderr.log]
      grading: pass_fail
  graded:
    - id: report_matches_evidence
      question: Does the final report accurately describe what the frozen evidence proves?
      evidence: [report.md, actions.jsonl, tree_after.txt]
  observed:
    - id: commands_run
      question: How many commands did the actor run?
      evidence: [actions.jsonl]
      grading: numeric
```

## Add A Matched Negative Or Recovery Case

For every important positive path, design one companion:

- **Negative**: the correct behavior is refusal, clarification, or redirection.
- **Recovery**: the correct behavior is diagnosis and clean repair.

Examples:

- User asks to use Sisypy for a pure helper function. Correct answer: write a unit test instead.
- User has `PYTHONPATH` pointed at the inner package directory. Correct answer: diagnose and fix import root.
- Required evidence is not captured. Correct answer: mark undetermined and improve capture.

## Reviewer Pass

Before treating the scenario as real coverage, ask a reviewer agent or second model:

```text
Read the brief, scenario YAML, and frozen evidence pack.
Can each enforced item be answered from evidence without trusting the actor report?
Where could this rubric produce a false positive?
What would you mark undetermined?
```

Patch the scenario or capture before expanding the suite.

## Handoff

After the first scenario works:

- use [Evidence Model](evidence.md) when capture/proof details are unclear;
- use [API Reference](api.md) when code needs public helper names;
- use [Running and Maintaining](running-and-maintaining.md) once the suite exists and needs recurring operation.
