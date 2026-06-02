"""
assessor.py — LLM-driven rubric grader for the Sisypy.

Reads ONLY the frozen evidence pack + hidden rubric.  Dispatches
DeepSeek V4 Pro (or a configurable model) via OpenAI-compatible API.

Key sourcing (ported from Astrid):
  1. DEEPSEEK_API_KEY environment variable.
  2. ~/.hermes/.env fallback.

Missing keys return a schema-complete *ungraded* result
(ungraded=true, model='none', verdicts empty) — never crash.

Evidence segments are capped to byte budgets to control prompt size.
Retry on 429/5xx with exponential backoff.

Output structured JSON: verdicts, contradictions, overall_passed,
summary, model, elapsed_sec.

Contains zero Astrid-project-specific evidence assumptions
(runs/*/events.jsonl, .astrid-session, plan.json, etc.).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from sisypy.schema import Assessment, EvidencePack, Scenario

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Maximum bytes to include from each evidence segment.
DEFAULT_EVIDENCE_BYTE_CAP = 8000
DEFAULT_REPORT_BYTE_CAP = 16000
DEFAULT_STDOUT_BYTE_CAP = 8000
DEFAULT_STDERR_BYTE_CAP = 4000
DEFAULT_COMMAND_LOG_BYTE_CAP = 4000
DEFAULT_ACTIONS_LOG_BYTE_CAP = 4000  # cap for actions.jsonl section
DEFAULT_TREE_BYTE_CAP = 4000
DEFAULT_DIFF_BYTE_CAP = 8000

# Default model config.
DEFAULT_MODEL = "deepseek-v4-pro"
DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MAX_TOKENS = 16000
DEFAULT_TEMPERATURE = 0.0
DEFAULT_TIMEOUT_SEC = 300

# Agentic assessor config.  The model name intentionally includes the provider
# prefix expected by subagent-launcher, unlike the legacy chat-completions model.
DEFAULT_AGENT_MODEL = "deepseek:deepseek-v4-pro"
DEFAULT_AGENT_TOOLSETS = "file,terminal"
DEFAULT_AGENT_MAX_TOKENS = 32768
DEFAULT_AGENT_TIMEOUT_SEC = 2400

# Retry configuration.
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2.0  # seconds
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}

# ---------------------------------------------------------------------------
# Key sourcing
# ---------------------------------------------------------------------------


def _load_deepseek_api_key() -> str | None:
    """Source DEEPSEEK_API_KEY from environment or ~/.hermes/.env.

    Ported from Astrid's assessor.py _load_env_key pattern.
    """
    # 1. Environment variable.
    key = os.environ.get("DEEPSEEK_API_KEY")
    if key:
        return key

    # 2. ~/.hermes/.env fallback.
    hermes_env = Path.home() / ".hermes" / ".env"
    if hermes_env.is_file():
        try:
            for line in hermes_env.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("DEEPSEEK_API_KEY="):
                    _, _, val = line.partition("=")
                    val = val.strip().strip('"').strip("'")
                    if val:
                        return val
        except Exception:
            return None

    return None


# ---------------------------------------------------------------------------
# Evidence assembly (byte-capped, evidence-pack only)
# ---------------------------------------------------------------------------


def _cap_text(text: str, max_bytes: int) -> str:
    """Truncate *text* to at most *max_bytes* bytes (UTF-8 aware).

    Appends a truncation marker when truncation occurs.
    """
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text
    # Truncate and decode, replacing any split multi-byte char.
    truncated = encoded[: max_bytes - 40].decode("utf-8", errors="replace")
    return truncated + "\n\n... [truncated due to length limit]\n"


def _read_file_capped(path: Path, max_bytes: int) -> str:
    """Read a file and cap at *max_bytes*, returning '' on any error."""
    try:
        if not path.is_file():
            return ""
        raw = path.read_text(encoding="utf-8", errors="replace")
        return _cap_text(raw, max_bytes)
    except Exception:
        return ""


def _assemble_evidence_sections(
    evidence_pack: EvidencePack,
    *,
    report_cap: int = DEFAULT_REPORT_BYTE_CAP,
    stdout_cap: int = DEFAULT_STDOUT_BYTE_CAP,
    stderr_cap: int = DEFAULT_STDERR_BYTE_CAP,
    command_log_cap: int = DEFAULT_COMMAND_LOG_BYTE_CAP,
    actions_log_cap: int = DEFAULT_ACTIONS_LOG_BYTE_CAP,
    tree_cap: int = DEFAULT_TREE_BYTE_CAP,
    diff_cap: int = DEFAULT_DIFF_BYTE_CAP,
    brief_cap: int = DEFAULT_EVIDENCE_BYTE_CAP,
    notes_cap: int = DEFAULT_EVIDENCE_BYTE_CAP,
) -> str:
    """Build a text block of evidence sections for the assessor prompt.

    Reads ONLY from the frozen evidence pack (evidence_dir / files).
    Caps each section to its byte budget.

    Returns a plain-text string suitable for inclusion in the assessor
    system prompt.
    """
    evidence_dir = Path(evidence_pack.evidence_dir)
    sections: list[str] = []

    # Helper.
    def _add_section(title: str, content: str) -> None:
        if content.strip():
            sections.append(f"### {title}\n\n{content.strip()}\n")

    brief = _read_file_capped(evidence_dir / "brief.md", brief_cap)
    _add_section("User-Shaped Brief", brief)

    report = _read_file_capped(evidence_dir / "report.md", report_cap)
    _add_section("Actor Report (report.md)", report)

    stdout = _read_file_capped(evidence_dir / "stdout.log", stdout_cap)
    _add_section("Actor stdout (stdout.log)", stdout)

    stderr = _read_file_capped(evidence_dir / "stderr.log", stderr_cap)
    _add_section("Actor stderr (stderr.log)", stderr)

    cmd_log = _read_file_capped(evidence_dir / "command_log.jsonl", command_log_cap)
    _add_section("Command Log (command_log.jsonl)", cmd_log)

    actions_log = _read_file_capped(evidence_dir / "actions.jsonl", actions_log_cap)
    _add_section("Action Log (actions.jsonl)", actions_log)

    tree_after = _read_file_capped(evidence_dir / "tree_after.txt", tree_cap)
    _add_section("File Tree After Run (tree_after.txt)", tree_after)

    git_diff = _read_file_capped(evidence_dir / "git_diff.patch", diff_cap)
    _add_section("Git Diff (git_diff.patch)", git_diff)

    notes = _read_file_capped(evidence_dir / "capture.notes", notes_cap)
    _add_section("Capture Notes (capture.notes)", notes)

    # Include manifest summary.
    manifest = evidence_pack.manifest
    if manifest:
        manifest_summary = {
            "scenario_id": manifest.get("scenario_id", ""),
            "actor_id": manifest.get("actor_id", ""),
            "mode": manifest.get("mode", ""),
            "dispatcher": manifest.get("dispatcher", ""),
            "success_proof_level": manifest.get("success_proof_level", ""),
        }
        _add_section("Manifest Summary", json.dumps(manifest_summary, indent=2))

    # Include capture_gaps from manifest (structured evidence-gap index).
    capture_gaps = evidence_pack.capture_gaps
    if capture_gaps:
        _add_section("Capture Gaps (evidence gaps indexed from capture)", json.dumps(capture_gaps, indent=2))

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Rubric assembly
# ---------------------------------------------------------------------------


def _assemble_rubric(assessment: Assessment) -> str:
    """Build a text block describing the hidden rubric.

    The rubric is organised into enforced, graded, and observed sections.
    """
    parts: list[str] = []

    if assessment.enforced:
        items = "\n".join(f"- {item}" for item in assessment.enforced)
        parts.append(f"**Enforced (hard pass/fail):**\n{items}\n")

    if assessment.graded:
        items = "\n".join(f"- {item}" for item in assessment.graded)
        parts.append(f"**Graded (scalar/categorical scoring):**\n{items}\n")

    if assessment.observed:
        items = "\n".join(f"- {item}" for item in assessment.observed)
        parts.append(f"**Observed (note but do not score):**\n{items}\n")

    return "\n".join(parts) if parts else "(No rubric provided)"


# ---------------------------------------------------------------------------
# The assessor system prompt
# ---------------------------------------------------------------------------

_ASSESSOR_SYSTEM_PROMPT = """\
You are an objective rubric grader for an agentic testing harness.

Your job: read the frozen evidence pack and the hidden rubric, then produce a
structured JSON assessment.  You MUST grade ONLY from the evidence provided
below — never assume capabilities, fabricate outputs, or trust unreferenced
narrative claims.

## Output format (valid JSON only, no markdown wrappers):

{
  "overall_passed": true/false,
  "summary": "one-line result summary",
  "verdicts": {
    "enforced": [
      {"item": "...", "passed": true/false, "reasoning": "..."}
    ],
    "graded": [
      {"item": "...", "passed": true/false, "score": 0-100, "reasoning": "..."}
    ],
    "observed": [
      {"item": "...", "note": "..."}
    ]
  },
  "contradictions": [
    "description of any claim that contradicts the evidence"
  ],
  "strengths": ["notable positive findings"],
  "weaknesses": ["areas for improvement"],
  "undetermined": false,
  "undetermined_items": []
}

## Verdict rules

Each rubric item verdict may be "passed", "failed", or "undetermined":

- "passed": The evidence clearly supports the rubric item.
- "failed": The evidence clearly contradicts the rubric item.
- "undetermined": The evidence is insufficient to decide. This is NOT the
  same as failed — use it ONLY when the relevant evidence files or fields
  are missing, empty, or too incomplete to support a conclusion.

When you return an undetermined verdict, you MUST include:
  - "evidence_checked": list of files/fields you examined
  - "missing_capture": what specific evidence was missing

The top-level "undetermined" field must be true when ANY verdict item
(in enforced, graded, or observed) is "undetermined".  The
"undetermined_items" array must list each undetermined item with its
evidence_checked and missing_capture fields.

When undetermined is true, overall_passed must be false regardless of
other passed verdicts — insufficient evidence is not success.

## Rules

1. Every enforced item that fails makes overall_passed=false.
2. Graded items are scored 0–100; no single graded item dictates overall_passed.
3. Observed items are informative only — they do not affect the outcome.
4. If the actor's report claims something not present in the evidence (e.g.
   "generated an image" with no artifact file, "validated" with no validate
   command in logs), flag it as a contradiction.
5. Be concise in reasoning — one or two sentences per item.
6. Do NOT output markdown code fences — output pure JSON.
"""

# ---------------------------------------------------------------------------
# LLM dispatch with retry
# ---------------------------------------------------------------------------


def _call_with_retry(
    api_key: str,
    system_prompt: str,
    user_prompt: str,
    *,
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_BASE_URL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = DEFAULT_TEMPERATURE,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    max_retries: int = MAX_RETRIES,
) -> tuple[str, str]:
    """Call the OpenAI-compatible chat completions API with retry.

    Retries on 429 and 5xx status codes with exponential backoff.

    Returns (response_text, error_string).  One of them will be empty.
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    # --- Attempt 1: openai Python SDK ---
    sdk_error = ""
    try:
        import openai  # type: ignore[import-untyped]

        client = openai.OpenAI(api_key=api_key, base_url=base_url)

        for attempt in range(max_retries + 1):
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    timeout=timeout_sec,
                )
                content = response.choices[0].message.content or ""
                return content, ""
            except Exception as exc:
                status = _extract_status(exc)
                if status in RETRYABLE_STATUSES and attempt < max_retries:
                    wait = RETRY_BACKOFF_BASE ** (attempt + 1)
                    time.sleep(wait)
                    continue
                sdk_error = str(exc)
                break
    except Exception as exc:
        sdk_error = str(exc)

    # --- Attempt 2: raw urllib fallback ---
    try:
        import urllib.error
        import urllib.request

        body = json.dumps(
            {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        ).encode("utf-8")

        for attempt in range(max_retries + 1):
            try:
                req = urllib.request.Request(
                    f"{base_url}/chat/completions",
                    data=body,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                )
                with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                content = data["choices"][0]["message"]["content"]
                return content, ""
            except urllib.error.HTTPError as exc:
                if exc.code in RETRYABLE_STATUSES and attempt < max_retries:
                    wait = RETRY_BACKOFF_BASE ** (attempt + 1)
                    time.sleep(wait)
                    continue
                return "", f"HTTP {exc.code}: {exc.reason}"
            except Exception as exc:
                if attempt < max_retries:
                    wait = RETRY_BACKOFF_BASE ** (attempt + 1)
                    time.sleep(wait)
                    continue
                return "", f"urllib error: {exc}"

    except Exception as exc:
        return "", f"openai-sdk: {sdk_error}; urllib: {exc}"

    return "", f"openai-sdk: {sdk_error}; urllib: all attempts exhausted"


def _extract_status(exc: Exception) -> int | None:
    """Try to extract an HTTP status code from an exception."""
    for attr in ("status_code", "http_status", "status", "code"):
        val = getattr(exc, attr, None)
        if isinstance(val, int):
            return val
    return None


# ---------------------------------------------------------------------------
# Ungraded fallback result
# ---------------------------------------------------------------------------


def _ungraded_result(reason: str = "DEEPSEEK_API_KEY not found") -> dict[str, Any]:
    """Return a schema-complete ungraded result.

    This is used when the API key is missing or the LLM call fails
    irrevocably.  Never crashes.
    """
    return {
        "ungraded": True,
        "model": "none",
        "overall_passed": False,
        "summary": f"Assessment skipped: {reason}",
        "verdicts": {
            "enforced": [],
            "graded": [],
            "observed": [],
        },
        "contradictions": [],
        "strengths": [],
        "weaknesses": [reason],
        "elapsed_sec": 0.0,
        "error": reason,
        "undetermined": False,
        "undetermined_items": [],
    }


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def _parse_assessor_response(raw: str) -> dict[str, Any]:
    """Parse the LLM's JSON response, with fallback on parse failure."""
    # Try direct parse first.
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Try extracting JSON from markdown code fences.
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # Try finding the outermost JSON object.
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass

    # Fallback: return a structured error.
    return {
        "overall_passed": False,
        "summary": "Failed to parse assessor response.",
        "verdicts": {"enforced": [], "graded": [], "observed": []},
        "contradictions": [],
        "strengths": [],
        "weaknesses": ["LLM response was not valid JSON."],
        "_raw_response": raw[:1000],
    }


# ---------------------------------------------------------------------------
# Agentic assessor
# ---------------------------------------------------------------------------


def _default_launcher_path() -> Path:
    return Path.home() / ".claude" / "skills" / "subagent-launcher" / "launch_hermes_agent.py"


def _resolve_assessor_launcher() -> Path | None:
    raw = os.environ.get("SISYPY_ASSESSOR_LAUNCHER")
    launcher = Path(raw).expanduser() if raw else _default_launcher_path()
    return launcher if launcher.is_file() else None


def _build_agent_assessor_brief(evidence_pack: EvidencePack, scenario: Scenario) -> str:
    evidence_dir = Path(evidence_pack.evidence_dir).resolve()
    rubric_text = _assemble_rubric(scenario.assessment)

    manifest_summary = ""
    if evidence_pack.manifest:
        manifest_summary = json.dumps(evidence_pack.manifest, indent=2, sort_keys=True)

    capture_gaps = ""
    if evidence_pack.capture_gaps:
        capture_gaps = json.dumps(evidence_pack.capture_gaps, indent=2, sort_keys=True)

    return f"""\
You are the Sisypy assessor. Your current working directory is the frozen
evidence pack:

{evidence_dir}

Assess the run against the hidden rubric below. The evidence pack, not the
actor's report alone, is the only source of truth.

## Hidden Rubric

{rubric_text}

## Evidence Pack Orientation

Open and parse the actual files in this directory. Do not rely on a summarized
or truncated snippet. Check files such as:

- compiled_api.json and any project_specific/**/compiled_api.json copies
- metadata.json and manifest.json
- actions.jsonl and command_log.jsonl
- report.md
- stdout.log and stderr.log
- git_diff.patch
- tree_before.txt and tree_after.txt
- command sidecars under commands/

Manifest, if available:

{manifest_summary or "(none)"}

Capture gaps, if available:

{capture_gaps or "(none)"}

## Required Method

1. Verify every enforced, graded, and observed rubric item against real bytes
   from the frozen evidence pack.
2. Use file reads plus grep/python as needed. For JSON artifacts, load the JSON
   and assert exact facts instead of eyeballing prose.
3. For structural "compiles but wrong" checks, actually inspect compiled graph
   fields: node ids, class_type values, and exact inputs.<field> references such
   as ["48", 0]. Confirm whether removed nodes are absent and consumers are
   rewired as required.
4. For action/process claims, inspect actions.jsonl, command_log.jsonl, stdout,
   stderr, command sidecars, and report.md for supporting or contradictory
   evidence.

## Rules

- Evidence over narrative. A report claim is not enough unless the files support it.
- READ ONLY. Never modify, create, delete, or rewrite files in the evidence pack.
- If a check cannot be proven from the files, mark that item undetermined or not
  passed. Never assume success.
- If evidence contradicts the actor's claims, record the contradiction.
- Keep reasoning concise but cite the concrete files/fields examined.
- Output ONLY valid JSON. No markdown, no code fences, no commentary.

## Output Schema

Return exactly this JSON shape:

{{
  "overall_passed": true/false,
  "summary": "one-line result summary",
  "verdicts": {{
    "enforced": [
      {{"item": "...", "passed": true/false, "reasoning": "..."}}
    ],
    "graded": [
      {{"item": "...", "passed": true/false, "score": 0-100, "reasoning": "..."}}
    ],
    "observed": [
      {{"item": "...", "note": "..."}}
    ]
  }},
  "contradictions": [
    "description of any claim that contradicts the evidence"
  ],
  "strengths": ["notable positive findings"],
  "weaknesses": ["areas for improvement"],
  "undetermined": false,
  "undetermined_items": []
}}

When an item is undetermined, include evidence_checked and missing_capture for
that item and list it in undetermined_items. If any item is undetermined,
overall_passed must be false.
"""


def _normalize_assessor_result(
    parsed: dict[str, Any],
    *,
    model: str,
    elapsed: float,
    error: str = "",
) -> dict[str, Any]:
    undetermined = parsed.get("undetermined", False)
    if not isinstance(undetermined, bool):
        undetermined = False

    undetermined_items = parsed.get("undetermined_items", [])
    if not isinstance(undetermined_items, list):
        undetermined_items = []

    verdicts = parsed.get("verdicts", {"enforced": [], "graded": [], "observed": []})
    if not isinstance(verdicts, dict):
        verdicts = {"enforced": [], "graded": [], "observed": []}
    verdicts.setdefault("enforced", [])
    verdicts.setdefault("graded", [])
    verdicts.setdefault("observed", [])

    return {
        "ungraded": False,
        "model": model,
        "overall_passed": bool(parsed.get("overall_passed", False)),
        "summary": parsed.get("summary", "Assessment complete."),
        "verdicts": verdicts,
        "contradictions": parsed.get("contradictions", []),
        "strengths": parsed.get("strengths", []),
        "weaknesses": parsed.get("weaknesses", []),
        "elapsed_sec": elapsed,
        "error": error,
        "undetermined": undetermined,
        "undetermined_items": undetermined_items,
    }


def _agent_parse_failed(parsed: dict[str, Any]) -> bool:
    return bool(parsed.get("_raw_response")) and parsed.get("summary") == "Failed to parse assessor response."


def _assess_with_agent(
    evidence_pack: EvidencePack,
    scenario: Scenario,
    *,
    model: str = DEFAULT_AGENT_MODEL,
    timeout_sec: int = DEFAULT_AGENT_TIMEOUT_SEC,
    toolsets: str = DEFAULT_AGENT_TOOLSETS,
    max_tokens: int = DEFAULT_AGENT_MAX_TOKENS,
) -> dict[str, Any] | None:
    """Assess by launching a read-only exploring subagent over the evidence dir.

    Returns None for any launcher, runtime, empty-output, or parse problem so the
    caller can fall back to the legacy single-shot LLM path.
    """
    launcher = _resolve_assessor_launcher()
    if launcher is None:
        return None

    evidence_dir = Path(evidence_pack.evidence_dir).resolve()
    if not evidence_dir.is_dir():
        return None

    t0 = time.monotonic()
    prompt_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            suffix=".md",
            prefix="sisypy-assessor-",
            delete=False,
        ) as fp:
            fp.write(_build_agent_assessor_brief(evidence_pack, scenario))
            prompt_path = Path(fp.name)

        cmd = [
            sys.executable,
            str(launcher),
            "--model",
            model,
            "--toolsets",
            toolsets,
            "--query_file",
            str(prompt_path),
            "--max_tokens",
            str(max_tokens),
            "--project_dir",
            str(evidence_dir),
        ]

        proc_env = os.environ.copy()
        proc_env.setdefault("PYENV_VERSION", "3.11.11")

        completed = subprocess.run(
            cmd,
            cwd=str(evidence_dir),
            env=proc_env,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
        elapsed = round(time.monotonic() - t0, 3)
        stdout = completed.stdout.strip()
        if completed.returncode != 0 or not stdout:
            return None

        parsed = _parse_assessor_response(stdout)
        if _agent_parse_failed(parsed):
            return None

        return _normalize_assessor_result(parsed, model=model, elapsed=elapsed)
    except Exception:
        return None
    finally:
        if prompt_path is not None:
            try:
                prompt_path.unlink(missing_ok=True)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Main assess entry point
# ---------------------------------------------------------------------------


def assess(
    evidence_pack: EvidencePack,
    scenario: Scenario,
    *,
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_BASE_URL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = DEFAULT_TEMPERATURE,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    # Evidence byte caps.
    report_cap: int = DEFAULT_REPORT_BYTE_CAP,
    stdout_cap: int = DEFAULT_STDOUT_BYTE_CAP,
    stderr_cap: int = DEFAULT_STDERR_BYTE_CAP,
    command_log_cap: int = DEFAULT_COMMAND_LOG_BYTE_CAP,
    actions_log_cap: int = DEFAULT_ACTIONS_LOG_BYTE_CAP,
    tree_cap: int = DEFAULT_TREE_BYTE_CAP,
    diff_cap: int = DEFAULT_DIFF_BYTE_CAP,
) -> dict[str, Any]:
    """Run the rubric grader against a frozen evidence pack.

    This is the main entry point.  It:

    1. Tries the exploring-agent assessor by default.
    2. Falls back to the legacy single-shot LLM assessor when forced by
       SISYPY_ASSESSOR=llm or when the agent path cannot run.
    3. Sources DEEPSEEK_API_KEY from env or ~/.hermes/.env for the legacy path.
    4. Returns *ungraded* result if no assessor can run (no crash).
    5. Assembles evidence sections from ONLY the frozen evidence pack,
       capping each segment to a byte budget.
    6. Constructs the hidden rubric from scenario.assessment.
    7. Dispatches the assessor prompt via DeepSeek V4 Pro with retry.
    8. Parses the structured JSON response.
    9. Returns a complete result dict.

    Args:
        evidence_pack: The frozen EvidencePack to grade.
        scenario: The Scenario (contains the hidden assessment rubric).
        model: Model identifier (default: deepseek-v4-pro).
        base_url: API base URL.
        max_tokens: Max completion tokens.
        temperature: Sampling temperature (0.0 = deterministic).
        timeout_sec: Request timeout in seconds.
        report_cap, stdout_cap, stderr_cap, command_log_cap, tree_cap, diff_cap:
            Byte caps for each evidence section.

    Returns:
        dict with keys:
            ungraded: bool           — True if assessment was skipped.
            model: str               — model used (or 'none').
            overall_passed: bool     — aggregate pass/fail.
            summary: str             — one-line summary.
            verdicts: dict           — {enforced, graded, observed} verdict lists.
            contradictions: list     — detected contradictions.
            strengths: list          — notable positives.
            weaknesses: list         — areas for improvement.
            elapsed_sec: float       — wall-clock time for the assessor call.
            error: str               — error message, empty on success.
    """
    t0 = time.monotonic()

    assessor_mode = os.environ.get("SISYPY_ASSESSOR", "agent").strip().lower()
    if assessor_mode not in {"agent", "llm"}:
        assessor_mode = "agent"

    if assessor_mode == "agent":
        agent_model = os.environ.get("SISYPY_ASSESSOR_MODEL", DEFAULT_AGENT_MODEL)
        try:
            agent_timeout = int(os.environ.get("SISYPY_ASSESSOR_TIMEOUT_SEC", DEFAULT_AGENT_TIMEOUT_SEC))
        except ValueError:
            agent_timeout = DEFAULT_AGENT_TIMEOUT_SEC

        agent_result = _assess_with_agent(
            evidence_pack,
            scenario,
            model=agent_model,
            timeout_sec=agent_timeout,
        )
        if agent_result is not None:
            return agent_result

    # 1. Key sourcing for the legacy single-shot path.
    api_key = _load_deepseek_api_key()
    if not api_key:
        result = _ungraded_result("DEEPSEEK_API_KEY not found in env or ~/.hermes/.env")
        result["elapsed_sec"] = round(time.monotonic() - t0, 3)
        return result

    # 2. Assemble evidence (evidence-pack only, byte-capped).
    evidence_text = _assemble_evidence_sections(
        evidence_pack,
        report_cap=report_cap,
        stdout_cap=stdout_cap,
        stderr_cap=stderr_cap,
        command_log_cap=command_log_cap,
        actions_log_cap=actions_log_cap,
        tree_cap=tree_cap,
        diff_cap=diff_cap,
    )

    # 3. Assemble rubric.
    rubric_text = _assemble_rubric(scenario.assessment)

    # 4. Construct the full user prompt.
    user_prompt = (
        "## Hidden Rubric\n\n"
        f"{rubric_text}\n\n"
        "## Frozen Evidence Pack\n\n"
        f"{evidence_text}\n\n"
        "Please produce your assessment JSON now."
    )

    # 5. Dispatch with retry.
    response_text, error = _call_with_retry(
        api_key,
        _ASSESSOR_SYSTEM_PROMPT,
        user_prompt,
        model=model,
        base_url=base_url,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout_sec=timeout_sec,
    )

    elapsed = round(time.monotonic() - t0, 3)

    if error:
        # API call failed after all retries.
        result = _ungraded_result(f"LLM API error: {error}")
        result["elapsed_sec"] = elapsed
        return result

    # 6. Parse the response.
    parsed = _parse_assessor_response(response_text)

    # 7. Normalize and build the final result.
    return _normalize_assessor_result(parsed, model=model, elapsed=elapsed)
