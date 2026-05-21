# Embedding Sisypy

Sisypy is project-neutral. A project supplies an adapter that teaches the shared runner how to prime workspaces, capture project-specific evidence, enforce command policy, and classify proof.

Use this skill after the user has approved the first scenario candidates. If you still need to decide what to test, read [Scenario Design](scenario-design.md). If the philosophy is unclear, read [Applied Philosophy](applied-philosophy.md). If the runner already exists and you are writing stronger briefs and rubrics, read [Scenario Authoring](scenario-authoring.md).

## Install or Import

Install Sisypy from the repository root:

```bash
git clone https://github.com/peteromallet/sisypy
cd sisypy
python3 -m pip install -e .
```

For local development without install:

```bash
PYTHONPATH=/path/to/sisypy python3 -m sisypy --help
```

Use the Sisypy repository root on `PYTHONPATH`, not the inner `sisypy/` package directory.

## Minimal Project Runner

Create this shape in the target repo:

```text
tests/agentic/
  __init__.py
  adapter.py
  runner.py
  scenarios/
    first_path.yaml
  briefs/
    first_path.md
```

`tests/agentic/runner.py`:

```python
from sisypy import cli, console_cli
from tests.agentic.adapter import MyProjectAdapter

def main(argv=None):
    """Programmatic entry point — returns the summary dict."""
    return cli(MyProjectAdapter(), argv=argv)

if __name__ == "__main__":
    # CI/recurring-shell entry point — prints JSON and exits with exit code.
    console_cli(MyProjectAdapter())
```

Run it as:

```bash
python -m tests.agentic.runner --help
python -m tests.agentic.runner first_path --actor fake --mode structural --no-parallel --verbose
```

Projects that need custom flags can use the public hooks:

```python
from sisypy import cli

def configure_parser(parser):
    parser.add_argument("--live-local", action="store_true")

def before_run(args):
    if args.live_local:
        ...

def main(argv=None):
    return cli(
        MyProjectAdapter(),
        argv=argv,
        configure_parser=configure_parser,
        before_run=before_run,
    )
```

## Minimal Adapter

Start by subclassing `FakeProjectAdapter`. This gives you a working structural harness while you add project-specific checks deliberately.

`tests/agentic/adapter.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

from sisypy import FakeProjectAdapter
from sisypy.schema import ActorRun, EvidencePack, Scenario, SuccessProofLevel


class MyProjectAdapter(FakeProjectAdapter):
    def __init__(self, repo_root: Path | None = None) -> None:
        super().__init__(name="my_project", repo_root=repo_root or Path.cwd())

    def capture(self, scenario: Scenario, run: ActorRun, evidence_dir: Path) -> None:
        super().capture(scenario, run, evidence_dir)
        project_dir = evidence_dir / "project_specific"
        project_dir.mkdir(parents=True, exist_ok=True)
        # Add deterministic project evidence here:
        # CLI help, relevant file listings, generated metadata, etc.

    def project_universal_checks(
        self,
        scenario: Scenario,
        evidence_dir: Path,
    ) -> dict[str, Any]:
        return {}

    def classify_success(
        self,
        scenario: Scenario,
        evidence_pack: EvidencePack,
    ) -> SuccessProofLevel:
        return SuccessProofLevel.AUTHORED
```

Only override methods when you have project semantics to add. The shared Sisypy package should never import your project.

## Minimal Scenario

`tests/agentic/scenarios/first_path.yaml`:

```yaml
name: first_path
tier: 1
description: Smoke-test whether an agent can find and explain the main user path.
brief: first_path.md
mode: structural
agents:
  - id: fake
    dispatcher: fake
    model: fake
assessment:
  enforced:
    - id: report_exists
      question: Did the agent produce a report?
      evidence: [report.md]
      grading: pass_fail
  graded: []
  observed: []
tags: [agentic, smoke]
```

`tests/agentic/briefs/first_path.md`:

```markdown
Inspect this repository as a user-facing product. Identify the main workflow a user would ask an agent to perform, then write a concise report describing:

1. the user goal;
2. the commands, docs, or tools the agent should discover;
3. what evidence would prove the path was completed correctly;
4. one plausible way an agent could fail while still claiming success.

Do not run external services, download models, spend money, or mutate product files.
```

This first scenario is intentionally simple. Its job is to prove the harness runs and to produce evidence for designing better scenarios.

## Adapter Responsibilities

An adapter implements project-specific semantics:

- build actor environment variables;
- prime a run workspace;
- capture project-specific evidence after dispatch;
- run deterministic project checks over frozen evidence;
- define bypass/forbidden patterns;
- classify the highest proof level reached;
- report live-mode prerequisites;
- provide command policy.

The shared Sisypy package should not import project code.

## First Scenarios

Start with structural scenarios:

- no GPU;
- no network;
- no model downloads;
- no cloud machines;
- fake actor first, then one real actor;
- evidence pack before narrative grading.

Good early scenarios are paths where an agent could misunderstand, fabricate success, mutate the wrong state, skip required evidence, or hit a legitimate product blocker.

## First Verification Commands

Run these before adding real actors:

```bash
python -m tests.agentic.runner --help
python -m tests.agentic.runner first_path --actor fake --mode structural --no-parallel --verbose
```

Then inspect the generated evidence pack under `out/agentic/reports/`. If the evidence pack does not prove what the report claims, fix capture before adding more scenarios.

Next: use [Scenario Authoring](scenario-authoring.md) to turn the approved scenario into evidence-backed rubric items, then use [Running and Maintaining](running-and-maintaining.md) for recurring runs.
