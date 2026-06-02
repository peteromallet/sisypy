import json

from sisypy.assessor import assess
from sisypy.schema import Assessment, EvidencePack, Scenario


def _write_launcher(path, response: dict | str, *, exit_code: int = 0) -> None:
    payload = response if isinstance(response, str) else json.dumps(response)
    path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "import sys",
                f"print({payload!r})",
                f"sys.exit({exit_code})",
            ]
        ),
        encoding="utf-8",
    )
    path.chmod(0o755)


def test_assess_uses_agent_launcher_before_api_key(monkeypatch, tmp_path):
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    (evidence_dir / "compiled_api.json").write_text("{}", encoding="utf-8")

    launcher = tmp_path / "launch_hermes_agent.py"
    _write_launcher(
        launcher,
        {
            "overall_passed": True,
            "summary": "agent verified",
            "verdicts": {
                "enforced": [{"item": "check", "passed": True, "reasoning": "read compiled_api.json"}],
                "graded": [],
                "observed": [],
            },
            "contradictions": [],
            "strengths": ["verified by agent"],
            "weaknesses": [],
            "undetermined": False,
            "undetermined_items": [],
        },
    )

    monkeypatch.setenv("SISYPY_ASSESSOR", "agent")
    monkeypatch.setenv("SISYPY_ASSESSOR_LAUNCHER", str(launcher))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    result = assess(
        EvidencePack(evidence_dir=str(evidence_dir)),
        Scenario(name="agent", assessment=Assessment(enforced=["check"])),
    )

    assert result["ungraded"] is False
    assert result["model"] == "deepseek:deepseek-v4-pro"
    assert result["overall_passed"] is True
    assert result["error"] == ""
    assert result["verdicts"]["enforced"][0]["passed"] is True


def test_assess_falls_back_when_agent_output_unparseable(monkeypatch, tmp_path):
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()

    launcher = tmp_path / "launch_hermes_agent.py"
    _write_launcher(launcher, "not json")

    monkeypatch.setenv("SISYPY_ASSESSOR", "agent")
    monkeypatch.setenv("SISYPY_ASSESSOR_LAUNCHER", str(launcher))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    result = assess(EvidencePack(evidence_dir=str(evidence_dir)), Scenario(name="fallback"))

    assert result["ungraded"] is True
    assert result["model"] == "none"
    assert "DEEPSEEK_API_KEY" in result["error"]
