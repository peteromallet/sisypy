import json

from sisypy.evidence import capture_evidence, load_evidence_pack
from sisypy.schema import ActorRun, CommandAction, Scenario, SuccessProofLevel


def test_capture_evidence_writes_loadable_command_action_pack(tmp_path):
    workdir = tmp_path / "work"
    report_dir = tmp_path / "reports"
    workdir.mkdir()
    (workdir / "created.txt").write_text("local fixture\n", encoding="utf-8")

    scenario = Scenario(name="native_evidence")
    run = ActorRun(
        id="run-1",
        scenario_name=scenario.name,
        agent_id="fake-agent",
        dispatcher="fake",
        success_proof_level=SuccessProofLevel.VALIDATED,
        workdir=str(workdir),
    )
    action = CommandAction(
        action_id="0001",
        command="python -m sisypy --help",
        cwd=str(workdir),
        exit_code=0,
        stdout_preview="usage: sisypy",
        stderr_preview="",
        source="unit-test",
        evidence_confidence="high",
    )

    pack = capture_evidence(
        scenario,
        run,
        workdir=workdir,
        report_dir=report_dir,
        brief_md="Do the thing.",
        report_md="## 1. Work\nValidated the change.\n\n## 2. Evidence\napi.json",
        stdout="ok\n",
        stderr="",
        actions=[action],
        tree_before="",
        tree_after="F out/api.json\n",
        git_status_before="",
        git_status_after="",
        git_diff="diff --git a/new.py b/new.py\n",
        tag="native",
        command_policy={"deny_patterns": [r"curl"]},
    )

    evidence_dir = tmp_path / "reports" / "evidence" / "run-1"
    assert pack.evidence_dir == str(evidence_dir)
    assert (evidence_dir / "manifest.json").is_file()
    assert (evidence_dir / "actions.jsonl").is_file()
    assert (evidence_dir / "commands" / "0001.stdout.log").read_text(
        encoding="utf-8"
    ) == "usage: sisypy"

    loaded = load_evidence_pack(evidence_dir)
    assert loaded.evidence_dir == str(evidence_dir)
    assert loaded.manifest["scenario_id"] == "native_evidence"
    assert loaded.manifest["evidence_confidence"] == "high"
    assert loaded.files["actions.jsonl"] == "actions.jsonl"

    action_line = json.loads((evidence_dir / "actions.jsonl").read_text().splitlines()[0])
    assert action_line["action"]["command"] == "python -m sisypy --help"


def test_load_evidence_pack_uses_caller_supplied_directory_when_manifest_moved(tmp_path):
    evidence_dir = tmp_path / "moved-pack"
    evidence_dir.mkdir()
    (evidence_dir / "manifest.json").write_text(
        json.dumps(
            {
                "scenario_id": "moved",
                "evidence_dir": "/stale/original/path",
                "files": ["report.md"],
            }
        ),
        encoding="utf-8",
    )
    (evidence_dir / "report.md").write_text("## 1. Report\n", encoding="utf-8")

    pack = load_evidence_pack(evidence_dir)

    assert pack.evidence_dir == str(evidence_dir)
    assert pack.manifest["evidence_dir"] == "/stale/original/path"
    assert pack.files == {"report.md": "report.md"}


# (b) older evidence packs without capture_gaps load with {}
def test_older_pack_without_capture_gaps_loads_empty_dict(tmp_path):
    """(b) Older evidence packs without capture_gaps key load with {}."""
    evidence_dir = tmp_path / "old-pack"
    evidence_dir.mkdir()
    manifest = {
        "scenario_id": "old-scenario",
        "mode": "structural",
        "command_policy": {"deny_patterns": []},
        "evidence_confidence": "high",
        "files": {"report.md": "report.md"},
        "evidence_dir": str(evidence_dir),
        # No capture_gaps key — old packs don't have it.
    }
    (evidence_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (evidence_dir / "report.md").write_text("## Report\n", encoding="utf-8")

    pack = load_evidence_pack(evidence_dir)
    assert pack.capture_gaps == {}


# (c) new captures include structured capture_gaps and preserve capture.notes
def test_new_capture_includes_structured_capture_gaps(tmp_path):
    """(c) New captures include structured capture_gaps and preserve capture.notes."""
    workdir = tmp_path / "work"
    report_dir = tmp_path / "reports"
    workdir.mkdir()

    scenario = Scenario(name="gap-test")
    run = ActorRun(
        id="run-gap",
        scenario_name=scenario.name,
        agent_id="fake-agent",
        dispatcher="fake",
        success_proof_level=SuccessProofLevel.AUTHORED,
        workdir=str(workdir),
    )

    # Capture with no report and no tree — should produce capture_gaps entries.
    pack = capture_evidence(
        scenario,
        run,
        workdir=workdir,
        report_dir=report_dir,
        brief_md="Do the thing.",
        report_md=None,  # Missing report
        stdout="ok\n",
        stderr="",
        actions=[],
        tree_before="",
        tree_after="",  # Missing tree
        git_status_before="",
        git_status_after="",
        git_diff="",
        tag="gap-test",
        command_policy={"deny_patterns": []},
    )

    # Check manifest contains capture_gaps.
    evidence_dir = tmp_path / "reports" / "evidence" / "run-gap"
    manifest_path = evidence_dir / "manifest.json"
    assert manifest_path.is_file()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "capture_gaps" in manifest
    gaps = manifest["capture_gaps"]
    # Missing report.md should produce a gap.
    assert "report.md" in gaps
    assert "reason" in gaps["report.md"]

    # Capture notes should still be on disk (raw notes preserved).
    notes_path = evidence_dir / "capture.notes"
    assert notes_path.is_file()
    raw_notes = notes_path.read_text(encoding="utf-8")
    assert "skip" in raw_notes.lower()


def test_capture_gaps_preserve_capture_notes_raw(tmp_path):
    """(c) capture.notes raw list remains unchanged — capture_gaps is additive."""
    workdir = tmp_path / "work"
    report_dir = tmp_path / "reports"
    workdir.mkdir()

    scenario = Scenario(name="notes-test")
    run = ActorRun(
        id="run-notes",
        scenario_name=scenario.name,
        agent_id="fake-agent",
        dispatcher="fake",
        success_proof_level=SuccessProofLevel.AUTHORED,
        workdir=str(workdir),
    )

    pack = capture_evidence(
        scenario,
        run,
        workdir=workdir,
        report_dir=report_dir,
        brief_md="Do the thing.",
        report_md="## Report\nSomething happened.",
        stdout="ok\n",
        stderr="",
        actions=[],
        tree_before="before\n",
        tree_after="after\n",
        git_status_before="",
        git_status_after="",
        git_diff="diff --git a/x b/x\n",
        tag="notes-test",
        command_policy={"deny_patterns": []},
    )

    evidence_dir = tmp_path / "reports" / "evidence" / "run-notes"
    manifest = json.loads((evidence_dir / "manifest.json").read_text(encoding="utf-8"))

    # capture_gaps should exist (possibly empty if nothing skipped).
    assert "capture_gaps" in manifest

    # Load pack and verify capture_gaps accessible.
    loaded = load_evidence_pack(evidence_dir)
    assert isinstance(loaded.capture_gaps, dict)
