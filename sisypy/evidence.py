"""
evidence.py — deterministic evidence-pack capture for the Sisypy.

Captures a frozen snapshot of an actor run into
\x60out/agentic/reports/<tag>-<scenario>/evidence/<slug>/\x60.

Artifacts captured:
  brief.md              — the rendered user-shaped brief.
  report.md             — actor-produced markdown report.
  stdout.log            — captured stdout.
  stderr.log            — captured stderr.
  command_log.jsonl     — structured command log (when available).
  actions.jsonl         — structured action log (v1 evidence model).
  commands/             — per-command stdout/stderr sidecars (0001.stdout.log, …).
  tree_before.txt       — recursive file listing before the run.
  tree_after.txt        — recursive file listing after the run.
  git_status_before.txt — git status before the run.
  git_status_after.txt  — git status after the run.
  git_diff.patch        — git diff of all changes.
  capture.notes         — human-readable notes about captures/skips.
  manifest.json         — structured metadata about the capture.

Every step is independently best-effort: missing files produce a skip
note in capture.notes, never an exception.

Ported patterns from Astrid's tests/agentic/capture.py (_safe_copy, _write_tree).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sisypy.schema import ActorRun, CommandAction, EvidencePack, Scenario

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_TREE_LINES = 1000
_CAPTURE_NOTES_FILENAME = "capture.notes"
_MANIFEST_FILENAME = "manifest.json"
_STDOUT_PREVIEW_LEN = 200
_STDERR_PREVIEW_LEN = 200

# ---------------------------------------------------------------------------
# Low-level helpers (ported from Astrid)
# ---------------------------------------------------------------------------


def _safe_copy(
    src: Path,
    dst: Path,
    notes: list[str],
    label: str,
    *,
    mkdir: bool = True,
) -> None:
    """Copy a single file, recording a skip note if missing or on error."""
    try:
        if not src.is_file():
            notes.append(f"skip {label}: source not present at {src}")
            return
        if mkdir:
            dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    except Exception as exc:
        notes.append(f"skip {label}: copy failed ({exc})")


def _write_tree(
    root_dir: Path,
    dst: Path,
    notes: list[str],
    *,
    label: str = "tree.txt",
    max_lines: int = _MAX_TREE_LINES,
) -> None:
    """Write a find-equivalent listing capped at max_lines.

    Pure Python (no subprocess) for self-contained, idempotent capture.
    Excludes .git/ internals.
    """
    try:
        if not root_dir.is_dir():
            notes.append(f"skip {label}: directory missing at {root_dir}")
            dst.write_text("", encoding="utf-8")
            return
        lines: list[str] = []
        for p in sorted(root_dir.rglob("*")):
            try:
                rel = p.relative_to(root_dir)
            except ValueError:
                continue
            parts = rel.parts
            if any(part == ".git" for part in parts):
                continue
            prefix = "D " if p.is_dir() else "F "
            lines.append(f"{prefix}{rel}")
            if len(lines) >= max_lines:
                lines.append(f"... truncated at {max_lines} entries")
                break
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    except Exception as exc:
        notes.append(f"skip {label}: walk failed ({exc})")
        try:
            dst.write_text("", encoding="utf-8")
        except Exception:
            pass


def _run_git_command(
    args: list[str],
    cwd: Path,
    notes: list[str],
    label: str,
) -> str:
    """Run a git command in the given directory, best-effort.

    Returns stdout on success, empty string on any failure (noted).
    """
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.stdout
    except FileNotFoundError:
        notes.append(f"skip {label}: git executable not found")
        return ""
    except subprocess.TimeoutExpired:
        notes.append(f"skip {label}: git command timed out")
        return ""
    except Exception as exc:
        notes.append(f"skip {label}: git command failed ({exc})")
        return ""


def _write_text_safe(path: Path, content: str, notes: list[str], label: str) -> None:
    """Write text content to a file, recording skip on error."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except Exception as exc:
        notes.append(f"skip {label}: write failed ({exc})")


def _write_json_safe(
    path: Path, data: dict[str, Any], notes: list[str], label: str
) -> None:
    """Write JSON data to a file, recording skip on error."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    except Exception as exc:
        notes.append(f"skip {label}: json write failed ({exc})")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _capture_gaps_from_notes(notes: list[str]) -> dict[str, Any]:
    """Build a structured capture_gaps index from raw capture notes.

    Extracts which evidence artifacts were skipped (and why) from the
    best-effort capture notes, producing a dict keyed by artifact name
    with a ``reason`` field.  The raw notes list is never modified —
    this is an additive, derived index suitable for embedding in the
    manifest so that deterministic checks and the assessor can reason
    about missing evidence without parsing free-text notes.

    Returns a dict like::

        {
            "report.md": {"reason": "skip report.md: actor produced no report content"},
            "command_log.jsonl": {"reason": "skip command_log.jsonl: no command log available"},
        }

    If there are no skip notes, returns an empty dict.
    """
    gaps: dict[str, dict[str, Any]] = {}
    for note in notes:
        if not note.startswith("skip "):
            continue
        # Parse "skip <artifact>: <reason>"
        remainder = note[len("skip "):]
        if ": " not in remainder:
            # Malformed skip note — still record it under a generic key.
            gaps.setdefault("_unparsable", []).append(note)
            continue
        artifact, reason = remainder.split(": ", 1)
        gaps[artifact] = {"reason": note}
    return gaps


def _command_action_to_dict(action: CommandAction) -> dict[str, Any]:
    """Convert a CommandAction to a JSON-safe dict."""
    import dataclasses
    return dataclasses.asdict(action)


def _normalize_legacy_command_log(
    command_log: list[dict[str, Any]],
) -> list[CommandAction]:
    """Convert legacy command_log dicts into low-detail CommandAction rows.

    Each legacy dict should have at least 'command' and optionally 'exit_code'.
    Evidence confidence is set to 'low' for all normalized rows since the
    provenance is ambiguous (no duration, cwd, stdout/stderr previews, etc.).
    """
    actions: list[CommandAction] = []
    for i, entry in enumerate(command_log):
        if not isinstance(entry, dict):
            continue
        cmd_text = entry.get("command", "") or ""
        if not cmd_text:
            continue
        action_id = f"{i + 1:04d}"
        actions.append(
            CommandAction(
                action_id=action_id,
                action_type="command",
                command=cmd_text,
                cwd=entry.get("cwd", ""),
                exit_code=entry.get("exit_code"),
                duration_sec=entry.get("duration_sec"),
                source="legacy-command-log",
                evidence_confidence="low",
                metadata={"legacy_entry": entry},
            )
        )
    return actions


def _write_actions_jsonl(
    evidence_dir: Path,
    actions: list[CommandAction],
    notes: list[str],
) -> None:
    """Write actions.jsonl from a list of CommandAction objects."""
    dst = evidence_dir / "actions.jsonl"
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        with open(dst, "w", encoding="utf-8") as fh:
            for i, action in enumerate(actions):
                entry = {
                    "seq": i + 1,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "action": _command_action_to_dict(action),
                }
                fh.write(json.dumps(entry, default=str) + "\n")
    except Exception as exc:
        notes.append(f"skip actions.jsonl: write failed ({exc})")


def _write_command_sidecars(
    evidence_dir: Path,
    actions: list[CommandAction],
    notes: list[str],
) -> int:
    """Write per-command stdout/stderr sidecars under commands/.

    Uses deterministic names: 0001.stdout.log, 0001.stderr.log, etc.

    Returns the number of sidecar files written.
    """
    sidecar_count = 0
    commands_dir = evidence_dir / "commands"
    for action in actions:
        if action.action_type != "command" or not action.command:
            continue
        aid = action.action_id or f"{sidecar_count + 1:04d}"
        try:
            commands_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            continue

        # Write stdout sidecar.
        if action.stdout_preview:
            stdout_path = commands_dir / f"{aid}.stdout.log"
            try:
                stdout_path.write_text(action.stdout_preview, encoding="utf-8")
                sidecar_count += 1
            except Exception as exc:
                notes.append(f"skip {aid}.stdout.log: write failed ({exc})")

        # Write stderr sidecar.
        if action.stderr_preview:
            stderr_path = commands_dir / f"{aid}.stderr.log"
            try:
                stderr_path.write_text(action.stderr_preview, encoding="utf-8")
                sidecar_count += 1
            except Exception as exc:
                notes.append(f"skip {aid}.stderr.log: write failed ({exc})")

    return sidecar_count


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def load_evidence_pack(path: str | Path | EvidencePack) -> EvidencePack:
    """Normalise a path or EvidencePack into an EvidencePack.

    When given a directory path, load manifest.json from that directory
    and build an EvidencePack with ``evidence_dir`` set to the **caller-
    supplied path** (never the manifest's ``evidence_dir`` field, which
    may be stale if the pack was moved).

    This is the public version of the pattern previously internal to
    compare.py's ``_load_pack``.
    """
    if isinstance(path, EvidencePack):
        return path

    dir_path = Path(path)
    manifest_path = dir_path / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"No manifest.json found in {dir_path} — not a valid evidence pack"
        )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    # Build file map from manifest if present, else discover.
    files_raw = manifest.get("files", {})
    # Normalise: manifest may store files as a dict or a list.
    if isinstance(files_raw, dict):
        files: dict[str, str] = dict(files_raw)
    elif isinstance(files_raw, list):
        files = {fn: fn for fn in files_raw if isinstance(fn, str)}
    else:
        files = {}
    if not files:
        for child in sorted(dir_path.iterdir()):
            if child.is_file() and child.name != "manifest.json":
                files[child.name] = child.name

    capture_gaps = manifest.get("capture_gaps", {})
    if not isinstance(capture_gaps, dict):
        capture_gaps = {}

    # Load capture.notes from the evidence directory when present.
    capture_notes: list[str] = []
    capture_notes_path = dir_path / _CAPTURE_NOTES_FILENAME
    if capture_notes_path.is_file():
        raw = capture_notes_path.read_text(encoding="utf-8")
        capture_notes = [
            line for line in raw.splitlines() if line.strip()
        ]

    return EvidencePack(
        manifest=manifest,
        evidence_dir=str(dir_path),
        files=files,
        capture_notes=capture_notes,
        capture_gaps=capture_gaps,
    )


# ---------------------------------------------------------------------------
# Main capture entry point
# ---------------------------------------------------------------------------


def capture_evidence(
    scenario: Scenario,
    run: ActorRun,
    *,
    workdir: Path,
    report_dir: Path,
    brief_md: str,
    report_md: str,
    stdout: str,
    stderr: str,
    command_log: list[dict[str, Any]] | None = None,
    actions: list[CommandAction] | None = None,
    tree_before: str = "",
    tree_after: str = "",
    git_status_before: str = "",
    git_status_after: str = "",
    git_diff: str = "",
    tag: str = "",
    command_policy: dict[str, Any] | None = None,
    capture_trigger: str = "normal",
) -> EvidencePack:
    """Freeze a complete evidence pack for an actor run.

    Args:
        scenario: The scenario being run.
        run: ActorRun metadata for this dispatch.
        workdir: The actor's working directory (for tree/git capture).
        report_dir: Base report directory (typically \x60out/agentic/reports/<tag>-<scenario>/\x60).
        brief_md: Rendered user-shaped brief.
        report_md: Actor-produced markdown report.
        stdout: Captured stdout.
        stderr: Captured stderr.
        command_log: Optional legacy structured command log (backward compat).
        actions: Optional structured CommandAction list (v1 evidence model).
            ``None`` means action capture is unsupported / unknown.
            ``[]`` means action capture is supported but no actions were recorded.
        tree_before: Pre-captured tree listing (empty if not yet captured).
        tree_after: Post-captured tree listing (empty if not yet captured).
        git_status_before: Pre-captured git status.
        git_status_after: Post-captured git status.
        git_diff: Pre-captured git diff.
        tag: Human-readable label for report grouping.
        command_policy: Optional command policy dict (for manifest).
        capture_trigger: How capture was triggered ('normal', 'blocked', 'timeout', 'failure').

    Returns:
        EvidencePack with populated manifest, evidence_dir, files, and capture_notes.
    """
    slug = run.id or run.agent_id or "unnamed"
    evidence_dir = report_dir / "evidence" / slug
    evidence_dir.mkdir(parents=True, exist_ok=True)

    notes: list[str] = []
    files: dict[str, str] = {}

    # Resolve actions: None means unsupported; [] means supported but empty.
    resolved_actions: list[CommandAction] | None = None
    actions_supported: bool | None = None  # None=unknown, True=supported

    if actions is not None:
        # Caller provided explicit actions (supported but possibly empty).
        resolved_actions = list(actions)
        actions_supported = True
    elif command_log:
        # No explicit actions, but legacy command_log exists — normalize it.
        resolved_actions = _normalize_legacy_command_log(command_log)
        actions_supported = True if resolved_actions else None
    else:
        # Neither actions nor command_log provided — unknown/unsupported.
        resolved_actions = None
        actions_supported = None

    # --- 1. brief.md ---
    brief_dst = evidence_dir / "brief.md"
    _write_text_safe(brief_dst, brief_md, notes, "brief.md")
    files["brief.md"] = "brief.md"

    # --- 2. report.md ---
    report_dst = evidence_dir / "report.md"
    if report_md:
        _write_text_safe(report_dst, report_md, notes, "report.md")
        files["report.md"] = "report.md"
    else:
        notes.append("skip report.md: actor produced no report content")

    # --- 3. stdout.log ---
    stdout_dst = evidence_dir / "stdout.log"
    _write_text_safe(stdout_dst, stdout, notes, "stdout.log")
    files["stdout.log"] = "stdout.log"

    # --- 4. stderr.log ---
    stderr_dst = evidence_dir / "stderr.log"
    _write_text_safe(stderr_dst, stderr, notes, "stderr.log")
    files["stderr.log"] = "stderr.log"

    # --- 5a. legacy command_log.jsonl (always write when available) ---
    if command_log:
        cmd_dst = evidence_dir / "command_log.jsonl"
        try:
            cmd_dst.parent.mkdir(parents=True, exist_ok=True)
            with open(cmd_dst, "w", encoding="utf-8") as fh:
                for entry in command_log:
                    fh.write(json.dumps(entry, default=str) + "\n")
            files["command_log.jsonl"] = "command_log.jsonl"
        except Exception as exc:
            notes.append(f"skip command_log.jsonl: write failed ({exc})")
    else:
        notes.append("skip command_log.jsonl: no command log available")

    # --- 5b. actions.jsonl (new v1 evidence model) ---
    if resolved_actions is not None and len(resolved_actions) > 0:
        _write_actions_jsonl(evidence_dir, resolved_actions, notes)
        files["actions.jsonl"] = "actions.jsonl"

        # --- 5c. per-command sidecars ---
        sidecar_count = _write_command_sidecars(evidence_dir, resolved_actions, notes)
        if sidecar_count > 0:
            files["commands/"] = "commands/"
            notes.append(f"wrote {sidecar_count} command sidecar(s) under commands/")
    elif resolved_actions is not None and len(resolved_actions) == 0:
        # Supported but empty — no actions recorded.
        notes.append("skip actions.jsonl: action capture supported but no actions recorded")
    elif actions_supported is None:
        notes.append("skip actions.jsonl: no action evidence available (actions unsupported)")
    else:
        notes.append("skip actions.jsonl: action capture supported but no actions recorded")

    # --- 6. tree_before.txt ---
    if tree_before:
        _write_text_safe(
            evidence_dir / "tree_before.txt", tree_before, notes, "tree_before.txt"
        )
        files["tree_before.txt"] = "tree_before.txt"
    else:
        # Capture now if workdir is available.
        _write_tree(workdir, evidence_dir / "tree_before.txt", notes, label="tree_before.txt")
        files["tree_before.txt"] = "tree_before.txt"

    # --- 7. tree_after.txt ---
    if tree_after:
        _write_text_safe(
            evidence_dir / "tree_after.txt", tree_after, notes, "tree_after.txt"
        )
        files["tree_after.txt"] = "tree_after.txt"
    else:
        # Capture now if workdir is available.
        _write_tree(workdir, evidence_dir / "tree_after.txt", notes, label="tree_after.txt")
        files["tree_after.txt"] = "tree_after.txt"

    # --- 8. git_status_before.txt ---
    if git_status_before:
        _write_text_safe(
            evidence_dir / "git_status_before.txt",
            git_status_before,
            notes,
            "git_status_before.txt",
        )
    else:
        gs = _run_git_command(["status", "--porcelain"], workdir, notes, "git_status_before.txt")
        _write_text_safe(evidence_dir / "git_status_before.txt", gs, notes, "git_status_before.txt")
    files["git_status_before.txt"] = "git_status_before.txt"

    # --- 9. git_status_after.txt ---
    if git_status_after:
        _write_text_safe(
            evidence_dir / "git_status_after.txt",
            git_status_after,
            notes,
            "git_status_after.txt",
        )
    else:
        gs = _run_git_command(["status", "--porcelain"], workdir, notes, "git_status_after.txt")
        _write_text_safe(evidence_dir / "git_status_after.txt", gs, notes, "git_status_after.txt")
    files["git_status_after.txt"] = "git_status_after.txt"

    # --- 10. git_diff.patch ---
    if git_diff:
        _write_text_safe(evidence_dir / "git_diff.patch", git_diff, notes, "git_diff.patch")
    else:
        gd = _run_git_command(["diff", "--patch"], workdir, notes, "git_diff.patch")
        _write_text_safe(evidence_dir / "git_diff.patch", gd, notes, "git_diff.patch")
    files["git_diff.patch"] = "git_diff.patch"

    # --- Determine evidence_confidence for manifest ---
    evidence_confidence = "unknown"
    if actions_supported is True and resolved_actions:
        all_high = all(
            a.evidence_confidence == "high" for a in resolved_actions
        )
        any_low = any(
            a.evidence_confidence == "low" for a in resolved_actions
        )
        if all_high and not any_low:
            evidence_confidence = "high"
        elif any_low:
            evidence_confidence = "low"
        else:
            evidence_confidence = "mixed"
    elif actions_supported is True and not resolved_actions:
        evidence_confidence = "supported_empty"
    elif command_log:
        evidence_confidence = "low"
    else:
        evidence_confidence = "unknown"

    # --- 11. compute capture_gaps from current notes ---
    capture_gaps = _capture_gaps_from_notes(notes)

    # --- 12. manifest.json (first write, includes capture_gaps) ---
    manifest = {
        "scenario_id": scenario.name,
        "actor_id": run.agent_id,
        "mode": run.mode.value if hasattr(run.mode, "value") else str(run.mode),
        "dispatcher": run.dispatcher,
        "tag": tag or run.tag,
        "slug": slug,
        "started_at": run.started_at,
        "finished_at": run.finished_at or datetime.now(timezone.utc).isoformat(),
        "command_policy": command_policy or {},
        "success_proof_level": run.success_proof_level.value
        if hasattr(run.success_proof_level, "value")
        else str(run.success_proof_level),
        "evidence_dir": str(evidence_dir),
        "workdir": run.workdir if run.workdir is not None else str(workdir),
        "capture_trigger": capture_trigger,
        "evidence_confidence": evidence_confidence,
        "actions_supported": actions_supported,
        "capture_gaps": capture_gaps,
        "files": files,
    }
    notes_before_manifest = len(notes)
    _write_json_safe(evidence_dir / _MANIFEST_FILENAME, manifest, notes, _MANIFEST_FILENAME)
    files[_MANIFEST_FILENAME] = _MANIFEST_FILENAME

    # --- 13. capture.notes (written after manifest so manifest-write skip notes are included) ---
    notes_before_capture_notes = len(notes)
    _write_text_safe(
        evidence_dir / _CAPTURE_NOTES_FILENAME,
        "\n".join(notes) + ("\n" if notes else ""),
        notes,
        _CAPTURE_NOTES_FILENAME,
    )
    files[_CAPTURE_NOTES_FILENAME] = _CAPTURE_NOTES_FILENAME

    # --- 14. best-effort manifest rewrite if capture.notes write appended new notes ---
    if len(notes) > notes_before_capture_notes:
        updated_gaps = _capture_gaps_from_notes(notes)
        if updated_gaps != capture_gaps:
            manifest["capture_gaps"] = updated_gaps
            _write_json_safe(
                evidence_dir / _MANIFEST_FILENAME,
                manifest,
                notes,
                f"{_MANIFEST_FILENAME} (rewrite)",
            )

    return EvidencePack(
        manifest=manifest,
        evidence_dir=str(evidence_dir),
        files=files,
        capture_notes=notes,
        capture_gaps=manifest.get("capture_gaps", {}),
    )

# ---------------------------------------------------------------------------
# Interval capture (best-effort periodic snapshots during long dispatches)
# ---------------------------------------------------------------------------


def capture_interval_snapshot(
    *,
    run: "ActorRun",
    workdir: "Path",
    report_dir: "Path",
    interval_seq: int,
) -> dict[str, Any] | None:
    """Write a best-effort interval snapshot during a long-running dispatch.

    Captures the current state of the workdir without blocking the actor.
    Writes under ``evidence/<slug>/intervals/<timestamp>/`` with a manifest
    that records ``capture_trigger='interval'`` and an ``interval_seq`` number.

    Every operation is independently best-effort: missing files produce
    skip notes in ``capture.notes``, never an exception.  Returns the
    interval manifest dict on success, or None if the snapshot could not
    be written at all.

    Args:
        run: ActorRun metadata for the dispatch.
        workdir: The actor's working directory (for tree/git capture).
        report_dir: Base report directory.
        interval_seq: Monotonically increasing interval sequence number.

    Returns:
        Interval manifest dict, or None on unrecoverable error.
    """
    from datetime import datetime, timezone

    slug = run.id or run.agent_id or "unnamed"
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    interval_dir = report_dir / "evidence" / slug / "intervals" / ts
    notes: list[str] = []

    try:
        interval_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        # Cannot create directory — unrecoverable for this interval.
        return None

    # --- tree_after.txt ---
    _write_tree(workdir, interval_dir / "tree_after.txt", notes, label="interval_tree_after.txt")

    # --- git_diff.patch ---
    gd = _run_git_command(["diff", "--patch"], workdir, notes, "interval_git_diff.patch")
    _write_text_safe(interval_dir / "git_diff.patch", gd, notes, "interval_git_diff.patch")

    # --- manifest.json ---
    manifest = {
        "capture_trigger": "interval",
        "interval_seq": interval_seq,
        "scenario_id": run.scenario_name,
        "actor_id": run.agent_id,
        "slug": slug,
        "workdir": run.workdir if run.workdir is not None else str(workdir),
        "started_at": run.started_at,
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_json_safe(interval_dir / "manifest.json", manifest, notes, "interval_manifest.json")

    # --- capture.notes ---
    _write_text_safe(
        interval_dir / "capture.notes",
        "\n".join(notes) + ("\n" if notes else ""),
        notes,
        "interval_capture.notes",
    )

    return manifest

