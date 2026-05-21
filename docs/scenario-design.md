# Scenario Design

Use this skill after you understand Sisypy's applied philosophy and before you write adapter code.

If the philosophy is unclear, read [Applied Philosophy](applied-philosophy.md). If the user has already approved the first scenarios and you need to wire files, go to [Embedding Sisypy](embedding.md). If the harness is already wired and you are writing real briefs and rubrics, go to [Scenario Authoring](scenario-authoring.md).

## Goal

Turn a repo into a short, reviewable scenario-candidate document. Do not implement scenarios yet.

The output is:

```text
scenario-candidate table
recommended first scenario
minimum evidence needed
open questions for the user
```

Then stop and ask the user to confirm priorities.

## Discovery Passes

Run these as focused subagents or separate passes. Each pass should produce a table.

| Pass | Question | Output columns |
|---|---|---|
| User-path researcher | What do users actually ask an agent to do? | `User ask | Source | User's desired outcome | Why this matters` |
| Surface mapper | What canonical path should the agent discover? | `User ask | Canonical surface | Required discovery step | Easy wrong turn` |
| Failure-mode analyst | How could the agent fail while sounding successful? | `User ask | Wrong path | Why it looks plausible | User-visible damage` |
| Evidence auditor | What can the repo prove today? | `Claim to grade | Existing evidence | Missing evidence | Instrumentation needed` |
| Negative-scenario designer | Where should the agent push back or recover? | `User ask | Trap | Correct behavior | Evidence of correct pushback or recovery` |

Keep the tables short. Prefer five strong rows over twenty speculative ones.

## Candidate Table

Merge the passes into this table:

| # | User ask | Canonical path | Failure mode | Evidence to capture | Shape |
|---|---|---|---|---|
| # | Plain-language task | Command, API, doc, tool, or workflow the agent should find | How the user gets let down | Frozen files, logs, actions, artifacts, or events | positive / negative / recovery |

Use ordinary user language in `User ask`. Do not start from internal functions.

## Pick The First Scenario

Choose the first scenario with these rules:

1. It represents a real user ask.
2. It exercises a path agents are likely to discover or misunderstand.
3. It can run structurally: no GPU, no network, no cloud spend.
4. Its success can be proven from a frozen evidence pack.
5. It has an obvious negative or recovery companion.

The first scenario should usually be a positive path that proves the harness can capture useful evidence. The second should usually be the matched negative or recovery case.

## Stop Point

Before implementation, show the user:

```text
Recommended first scenario:
Why this first:
Minimum evidence pack:
Matched negative/recovery:
Open questions:
```

After printing the recommendation, emit `AWAITING_USER_CONFIRMATION`. Do not advance to embedding or authoring until the user responds.

Do not create files until the user confirms. Users know which paths matter in practice; this checkpoint prevents building a polished suite around the wrong workflow.

## Handoff

After confirmation:

- use [Embedding Sisypy](embedding.md) to create the adapter, runner, first YAML scenario, and brief;
- use [Scenario Authoring](scenario-authoring.md) to turn the approved scenario into a brief, rubric, evidence requirements, and reviewer pass.
