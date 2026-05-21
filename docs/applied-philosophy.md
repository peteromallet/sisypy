# Applied Philosophy: Testing the Agent, Not the Function

Programmatic tests check whether code behaves correctly when called correctly. Agentic tests check whether an agent, given only what a real user would provide, can discover the right path, act safely, produce evidence, and fulfill both the product's purpose and the user's purpose.

This document tells an agent how to turn that distinction into useful Sisypy scenarios for your codebase.

Read this first when you need the mental model. Stop here once you understand the loop, roles, evidence discipline, and signal tiers. To choose repo-specific scenarios, continue to [Scenario Design](scenario-design.md). To wire files after scenarios are approved, use [Embedding Sisypy](embedding.md).

## The Frame

The thing under test is the whole loop:

```text
user intent -> agent discovery -> system action -> evidence -> user outcome
```

The system can pass every unit test and still fail this loop. Common failures look like this:

- The agent never finds the canonical tool.
- The agent picks the wrong layer or file.
- The agent uses a shortcut that bypasses the real interface.
- The agent claims success on an impossible request.
- The report sounds correct, but the evidence does not prove the work happened.

Sisypy exists to make that loop observable, fair, and repeatable.

## When To Use Sisypy

Use Sisypy for agent-facing workflows where correctness depends on discovery, judgment, state mutation, recovery, or evidence.

Good fits:

- CLI, MCP, skill, script, or docs surfaces an agent must discover.
- Multi-step user workflows where order and context matter.
- State-changing flows where the wrong edit can look successful.
- Product blockers where the right answer is honest pushback.
- Workflows where the agent might produce a confident narrative without proof.

Keep deterministic tests for deterministic contracts. Use Sisypy for the user-agent-system loop.

## The Scenario Unit

A scenario is data, not code. It has three parts:

- **Brief**: the only instruction the actor agent receives.
- **Priming**: state the harness prepares before the actor starts.
- **Rubric**: questions answered after the actor finishes, using frozen evidence.

Write scenarios as YAML so agents can add coverage without editing the runner.

## The Three Roles

There are three roles:

- **Actor**: sees the brief.
- **Harness**: the Sisypy runner and embedding code that prepares priming, runs the actor, and freezes evidence. This is not another agent.
- **Assessor**: sees the evidence pack and rubric. It should be skeptical of the actor's claims until the frozen evidence supports them.

Do not blur those roles.

## Evidence Before Narrative

An agent can write a polished report that is completely unsupported. Sisypy should grade what happened, not what the actor said happened.

The minimum evidence discipline:

1. Freeze an evidence pack after the actor finishes.
2. Include stdout, stderr, action logs, relevant files, generated artifacts, state snapshots, and the actor report.
3. Make deterministic checks and LLM assessors read only that frozen pack.
4. Refuse to grade a claim if the evidence pack cannot prove or disprove it.

In practice, "refuse to grade" means the result is **undetermined**, not pass and not fail. The assessor should cite the evidence locations it checked and name the missing capture or instrumentation that prevents a verdict.

If the agent could have written outside the captured tree, capture is incomplete. If the system never emits the event you need, instrumentation is incomplete. Fix those before writing a confident rubric.

## The Signal Model

Every rubric question belongs to one tier.

| Tier | Purpose | Examples |
|---|---|---|
| **Enforced** | Binary contracts that gate the scenario. Keep this to 1-3 questions. | Did the agent use the canonical CLI? Did it create the required artifact? Did it avoid fabricating success? |
| **Graded** | Semantic quality judged from evidence. Contributes to score, but does not gate. | Was the diagnosis useful? Was the recovery path reasonable? Was the final report clear? |
| **Observed** | Telemetry for later analysis. Never gates. | Shell call count, chosen path, failure category, elapsed time. |

If one question mixes tiers, split it. If a question cannot be answered from evidence, do not include it yet.

## How To Find Scenarios

Do not start from functions. Start from user asks.

This section gives the principle. For the operational skill, including outputs and the user checkpoint, use [Scenario Design](scenario-design.md).

If you are an agent applying Sisypy to a repo, do not do one linear skim. Split discovery into focused subagents, then synthesize the results.

Recommended passes:

- **User-path researcher**: read README, docs, examples, issues, support notes, product briefs, and real usage traces. Return `User ask | Source | User's desired outcome | Why this matters`.
- **Surface mapper**: inspect CLIs, commands, MCP tools, scripts, public APIs, skills, examples, and error messages. Return `User ask | Canonical surface | Required discovery step | Easy wrong turn`.
- **Failure-mode analyst**: find where an agent could fabricate, choose the wrong layer, mutate the wrong file, skip a preflight, spend money accidentally, or claim success without proof. Return `User ask | Wrong path | Why it looks plausible | User-visible damage`.
- **Evidence auditor**: inspect what the repo can capture today: logs, reports, generated files, command traces, events, diffs, artifacts. Return `Claim to grade | Existing evidence | Missing evidence | Instrumentation needed`.
- **Negative-scenario designer**: turn impossible asks, unsupported modes, missing assets, ambiguous instructions, and corrupted state into pushback or recovery scenarios. Return `User ask | Trap | Correct behavior | Evidence of correct pushback or recovery`.

Each pass should return a short table, not an essay.

Then build a scenario-candidate document:

| # | User ask | Canonical path | Failure mode | Evidence to capture | Shape |
|---|---|---|---|---|
| # | Plain-language request | Docs, commands, APIs, or tools the agent should find | How the user gets let down | Files, logs, actions, artifacts, or events to capture | positive / negative / recovery |

Share that document with the user before writing scenarios. Users usually spot missing real workflows immediately. That review is part of the method.

## Scenario Shapes

Use three basic shapes.

- **Positive**: the agent should complete the normal path the user expected.
- **Negative**: the agent should reject, clarify, or redirect an impossible or unsafe ask.
- **Recovery**: the agent should diagnose a broken setup, corrupted state, missing dependency, or transient failure and choose a clean recovery path.

Every important scenario family should eventually have a matched negative or recovery case. A rubric that only sees happy paths teaches you very little about agent reliability.

## Implementation Sequence

Use this sequence when adding Sisypy to a repo.

1. Read this guide and the embedding guide.
2. Run the discovery passes above.
3. Produce the scenario-candidate document and review it with the user.
4. Pick one high-value user ask.
5. Write the brief first. Do not write the rubric from imagination.
6. Build the smallest adapter and fake/no-GPU actor path that can produce an evidence pack.
7. Run the brief once and inspect the pack.
8. Write enforced, graded, and observed rubric questions from the evidence that actually exists.
9. Ask a reviewer agent or second model to challenge whether the rubric grades evidence or narrative.
10. Add missing instrumentation or capture before accepting the scenario.
11. Commit the scenario YAML only after the evidence and rubric are aligned.

This order matters. If you write the rubric before watching a real run, you will usually grade the story you expected instead of the evidence the harness captured.

The implementation details live in [Embedding Sisypy](embedding.md) and [Scenario Authoring](scenario-authoring.md). This guide intentionally stops at the method.

## False Positives

Agentic evaluators are vulnerable to cooperative false positives: the actor writes a plausible report, and the assessor rewards the plausibility.

Defenses:

- Prefer deterministic checks for concrete patterns.
- Make every assessor cite evidence locations.
- Add contradiction checks between report claims and action logs.
- Use a second vendor or reviewer for high-value scenarios.
- Include negative scenarios so the rubric has demonstrated failure behavior.
- Treat assessor disagreement as useful data, not noise.

Do not chase a single aggregate score until the false-positive surface is understood.

## A Minimal Scenario Draft

Use this skeleton when drafting.

Example brief:

```markdown
I want to add this project's canonical image-generation workflow to our agentic test suite.

Please find the documented way an agent should discover, validate, and run that workflow. Set up the smallest structural scenario that proves the path is wired correctly without spending GPU money. Do not claim success unless you can point to captured evidence.
```

```yaml
name: canonical_tool_discovery
tier: core
description: |
  Verifies that an agent can discover and use the canonical project command
  for a real user task instead of hand-editing state or fabricating success.

brief: canonical_tool_discovery.md
target_orchestrator: builtin.agent_probe

priming:
  - create_project: $SLUG

assessment:
  universal_checks: true
  enforced:
    - id: used_canonical_command
      question: Did the agent invoke the documented command for the task?
      evidence: [actions, stderr]
      grading: pass_fail
      weight: 2
    - id: artifact_exists
      question: Did the expected output artifact exist in the captured tree?
      evidence: [tree, files]
      grading: pass_fail
      weight: 2
  graded:
    - id: report_matches_evidence
      question: Did the final report accurately describe what the evidence shows?
      evidence: [report, actions, files]
      weight: 1
  observed:
    - id: shell_calls_count
      question: How many shell calls did the agent make?
      evidence: [actions]
      grading: numeric
```

Adapt the IDs and evidence names to the embedding project. Keep the brief user-shaped and the rubric evidence-shaped.

## What Good Looks Like

A good Sisypy slice has:

- One user-shaped brief.
- A fake or structural actor path for cheap local iteration.
- A frozen evidence pack that captures the relevant mutation surface.
- A small number of enforced questions.
- Graded questions that assess judgment, not mere existence.
- Observed fields that help future debugging.
- At least one negative or recovery scenario for the same scenario family.
- A reviewer pass that tries to falsify the rubric.

The result should not be a giant evaluator. It should be a small repeatable experiment that answers one question:

> Would the user's agent, given only what a real user would provide, accomplish what the user actually wanted?

Next: use [Scenario Design](scenario-design.md) to produce a scenario-candidate table and stop for user confirmation.
