import dataclasses
import json
import sys

import pytest


def test_top_level_public_imports_work():
    import sisypy
    from sisypy import Boulder, Push, cli, compare

    assert sisypy is not None
    assert cli is sisypy.cli
    assert Boulder is sisypy.Scenario
    assert Push is sisypy.ActorRun
    assert compare is sisypy.compare


def test_scenario_outcome_fake_no_op_round_trips_by_value():
    from sisypy.schema import ScenarioOutcome

    assert ScenarioOutcome.FAKE_NO_OP.value == "fake_no_op"
    assert ScenarioOutcome("fake_no_op") is ScenarioOutcome.FAKE_NO_OP
    assert json.loads(json.dumps({"outcome": ScenarioOutcome.FAKE_NO_OP})) == {
        "outcome": "fake_no_op"
    }


def test_scenario_outcome_undetermined_round_trips_by_value():
    """(a) ScenarioOutcome round-trip for undetermined and all existing outcomes."""
    from sisypy.schema import ScenarioOutcome

    # UNDETERMINED round-trip
    assert ScenarioOutcome.UNDETERMINED.value == "undetermined"
    assert ScenarioOutcome("undetermined") is ScenarioOutcome.UNDETERMINED
    assert json.loads(json.dumps({"outcome": ScenarioOutcome.UNDETERMINED})) == {
        "outcome": "undetermined"
    }

    # All outcomes round-trip
    for name in ("passed", "failed", "blocked_prerequisite", "skipped_live",
                 "violation", "fake_no_op", "undetermined"):
        member = ScenarioOutcome(name)
        assert member.value == name
        assert ScenarioOutcome(name) is member


def test_scenario_outcome_enum_membership():
    """Verify UNDETERMINED is in the enum membership list."""
    from sisypy.schema import ScenarioOutcome
    members = list(ScenarioOutcome)
    values = [m.value for m in members]
    assert "undetermined" in values
    assert "fake_no_op" in values
    assert "passed" in values
    assert "failed" in values


def test_fake_no_op_regression():
    """(g) Fake no-op regression: FAKE_NO_OP semantics unchanged."""
    from sisypy.schema import ScenarioOutcome

    assert ScenarioOutcome.FAKE_NO_OP.value == "fake_no_op"
    # Fake no-op is a plumbing outcome, not a product failure.
    assert ScenarioOutcome.FAKE_NO_OP != ScenarioOutcome.FAILED
    assert ScenarioOutcome.FAKE_NO_OP != ScenarioOutcome.PASSED
    assert ScenarioOutcome.FAKE_NO_OP != ScenarioOutcome.UNDETERMINED


def test_skipped_live_preservation():
    """(h) SKIPPED_LIVE value preserved."""
    from sisypy.schema import ScenarioOutcome

    assert ScenarioOutcome.SKIPPED_LIVE.value == "skipped_live"
    assert ScenarioOutcome("skipped_live") is ScenarioOutcome.SKIPPED_LIVE


# ---------------------------------------------------------------------------
# summary_exit_code tests
# ---------------------------------------------------------------------------


def test_summary_exit_code_all_pass_returns_0():
    """(l) All-pass → exit code 0."""
    from sisypy import summary_exit_code

    summary = {
        "runs": [
            {"outcome": "passed"},
            {"outcome": "passed"},
        ],
    }
    assert summary_exit_code(summary) == 0


def test_summary_exit_code_fake_only_returns_0():
    """(l) Fake-only → exit code 0."""
    from sisypy import summary_exit_code

    summary = {
        "runs": [
            {"outcome": "fake_no_op"},
        ],
    }
    assert summary_exit_code(summary) == 0


def test_summary_exit_code_mixed_pass_fake_returns_0():
    """(l) Mixed pass + fake → exit code 0."""
    from sisypy import summary_exit_code

    summary = {
        "runs": [
            {"outcome": "passed"},
            {"outcome": "fake_no_op"},
        ],
    }
    assert summary_exit_code(summary) == 0


def test_summary_exit_code_failed_returns_1():
    """(l) Failed present → exit code 1."""
    from sisypy import summary_exit_code

    summary = {
        "runs": [
            {"outcome": "passed"},
            {"outcome": "failed"},
        ],
    }
    assert summary_exit_code(summary) == 1


def test_summary_exit_code_violation_returns_1():
    """(l) Violation present → exit code 1."""
    from sisypy import summary_exit_code

    summary = {
        "runs": [
            {"outcome": "violation"},
        ],
    }
    assert summary_exit_code(summary) == 1


def test_summary_exit_code_mixed_failed_undetermined_returns_1():
    """(l) Mixed failed + undetermined → exit code 1 (failed takes precedence over undetermined)."""
    from sisypy import summary_exit_code

    summary = {
        "runs": [
            {"outcome": "passed"},
            {"outcome": "failed"},
            {"outcome": "undetermined"},
        ],
    }
    assert summary_exit_code(summary) == 1


def test_summary_exit_code_undetermined_only_returns_2():
    """(l) Undetermined-only (no failures) → exit code 2."""
    from sisypy import summary_exit_code

    summary = {
        "runs": [
            {"outcome": "undetermined"},
        ],
    }
    assert summary_exit_code(summary) == 2


def test_summary_exit_code_undetermined_and_passed_returns_2():
    """(l) Undetermined + passed (no failures) → exit code 2."""
    from sisypy import summary_exit_code

    summary = {
        "runs": [
            {"outcome": "passed"},
            {"outcome": "undetermined"},
        ],
    }
    assert summary_exit_code(summary) == 2


def test_summary_exit_code_blocked_returns_3():
    """(l) Blocked prerequisite → exit code 3."""
    from sisypy import summary_exit_code

    summary = {
        "runs": [
            {"outcome": "blocked_prerequisite"},
        ],
    }
    assert summary_exit_code(summary) == 3


def test_summary_exit_code_skipped_live_returns_3():
    """(l) Skipped live → exit code 3."""
    from sisypy import summary_exit_code

    summary = {
        "runs": [
            {"outcome": "skipped_live"},
        ],
    }
    assert summary_exit_code(summary) == 3


def test_summary_exit_code_mixed_blocked_undetermined_returns_3():
    """(l) Mixed blocked + undetermined → 3 (blocked takes precedence)."""
    from sisypy import summary_exit_code

    summary = {
        "runs": [
            {"outcome": "blocked_prerequisite"},
            {"outcome": "undetermined"},
        ],
    }
    assert summary_exit_code(summary) == 3


def test_summary_exit_code_error_key_returns_3():
    """(l) Summary with error key → exit code 3."""
    from sisypy import summary_exit_code

    summary = {
        "error": "something went wrong",
        "runs": [
            {"outcome": "passed"},
        ],
    }
    assert summary_exit_code(summary) == 3


def test_summary_exit_code_has_blocked_or_error_flag_returns_3():
    """(l) Summary with has_blocked_or_error flag → exit code 3."""
    from sisypy import summary_exit_code

    summary = {
        "has_blocked_or_error": True,
        "runs": [
            {"outcome": "passed"},
        ],
    }
    assert summary_exit_code(summary) == 3


def test_summary_exit_code_batch_shape():
    """(l) Batch summary (scenarios key) → correct exit code."""
    from sisypy import summary_exit_code

    summary = {
        "scenarios": [
            {"runs": [{"outcome": "passed"}]},
            {"runs": [{"outcome": "undetermined"}]},
        ],
    }
    assert summary_exit_code(summary) == 2


def test_summary_exit_code_results_shape():
    """(l) Flat results shape → correct exit code."""
    from sisypy import summary_exit_code

    summary = {
        "results": [
            {"outcome": "passed"},
            {"outcome": "failed"},
        ],
    }
    assert summary_exit_code(summary) == 1


def test_summary_exit_code_empty_runs_returns_3():
    """(l) No runs at all → exit code 3."""
    from sisypy import summary_exit_code

    assert summary_exit_code({}) == 3
    assert summary_exit_code({"runs": []}) == 3


# ---------------------------------------------------------------------------
# summit() undetermined tests
# ---------------------------------------------------------------------------


def test_summit_raises_boulder_error_for_undetermined():
    """(n) summit() raises BoulderError naming undetermined for undetermined outcomes."""
    from sisypy import BoulderError, summit
    from sisypy.schema import ActorRun, ScenarioOutcome

    run = ActorRun(id="undetermined-run")
    run.outcome = ScenarioOutcome.UNDETERMINED

    with pytest.raises(BoulderError) as exc_info:
        summit(run)

    msg = str(exc_info.value)
    assert "undetermined" in msg.lower()
    assert "insufficient evidence" in msg.lower()


def test_summit_raises_boulder_error_for_undetermined_dict():
    """(n) summit() raises BoulderError for undetermined dict input."""
    from sisypy import BoulderError, summit

    attempt = {"outcome": "undetermined", "undetermined_items": [], "capture_gaps": {}}

    with pytest.raises(BoulderError) as exc_info:
        summit(attempt)

    msg = str(exc_info.value)
    assert "undetermined" in msg.lower()


def test_summit_raises_boulder_error_for_failed():
    """(n) summit() raises BoulderError for failed outcome (not undetermined-specific)."""
    from sisypy import BoulderError, summit
    from sisypy.schema import ActorRun, ScenarioOutcome

    run = ActorRun(id="failed-run")
    run.outcome = ScenarioOutcome.FAILED

    with pytest.raises(BoulderError) as exc_info:
        summit(run)

    # Failed message should NOT use the undetermined wording.
    msg = str(exc_info.value)
    assert "insufficient evidence" not in msg.lower()


def test_summit_passes_for_passed_dict():
    """summit() returns attempt for passed outcome."""
    from sisypy import summit

    attempt = {"outcome": "passed", "success_proof_level": "validated"}
    result = summit(attempt, min_level="authored")
    assert result is attempt


# ---------------------------------------------------------------------------
# console_cli tests
# ---------------------------------------------------------------------------


def test_console_cli_help_exits_zero(capsys):
    """(m) console_cli with --help exits with code 0."""
    from sisypy import FakeProjectAdapter, console_cli

    with pytest.raises(SystemExit) as exc_info:
        console_cli(FakeProjectAdapter(), argv=["--help"])

    assert exc_info.value.code == 0


def test_console_cli_exits_with_correct_code_for_undetermined_summary(monkeypatch):
    """(m) console_cli exits with code 2 when cli() returns undetermined result."""
    from sisypy import FakeProjectAdapter, console_cli

    # Patch cli to return an undetermined summary.
    undetermined_summary = {
        "runs": [{"outcome": "undetermined"}],
        "outcome_counts": {"undetermined": 1},
        "has_undetermined": True,
    }

    monkeypatch.setattr(
        "sisypy.public_api.cli",
        lambda adapter, argv=None, configure_parser=None, before_run=None: undetermined_summary,
    )

    with pytest.raises(SystemExit) as exc_info:
        console_cli(FakeProjectAdapter(), argv=["--mode", "structural"])

    assert exc_info.value.code == 2


def test_console_cli_exits_with_code_0_for_all_pass(monkeypatch):
    """(m) console_cli exits with code 0 when all pass."""
    from sisypy import FakeProjectAdapter, console_cli

    pass_summary = {
        "runs": [{"outcome": "passed"}],
        "outcome_counts": {"passed": 1},
        "has_undetermined": False,
    }

    monkeypatch.setattr(
        "sisypy.public_api.cli",
        lambda adapter, argv=None, configure_parser=None, before_run=None: pass_summary,
    )

    with pytest.raises(SystemExit) as exc_info:
        console_cli(FakeProjectAdapter(), argv=["--mode", "structural"])

    assert exc_info.value.code == 0
