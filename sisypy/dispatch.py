"""
dispatch.py — pluggable actor dispatchers for the shared Sisypy.

Provides:
  ActorRunResult  — structured result from one actor dispatch.
  ActorDispatcher — ABC/protocol for actor dispatch backends.
  FakeActorDispatcher — scripted responses for deterministic testing.
  HermesDispatcher   — MVP real actor path: invokes DeepSeek V4 Pro
                        via an OpenAI-compatible API.

Hermes dispatcher sources DEEPSEEK_API_KEY from env or ~/.hermes/.env.
No hardcoded Astrid paths.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from sisypy.schema import CommandAction  # noqa: E402  # shared schema types
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# ActorRunResult
# ---------------------------------------------------------------------------


@dataclass
class ActorRunResult:
    """Structured result from a single actor dispatch.

    Attributes:
        slug: Run slug (e.g. "<scenario>-<agent>-<timestamp>").
        ok: True if the dispatch ran without infrastructure errors.
        stdout: Captured stdout from the actor.
        stderr: Captured stderr from the actor.
        report_md: Markdown report produced by the actor (if any).
        command_log: List of commands the actor executed (if logged) — legacy format.
        actions: Structured CommandAction list (v1 evidence model).
        exit_code: Subprocess exit code (None if not subprocess-based).
        elapsed_sec: Wall-clock time for the dispatch.
        errors: Non-fatal errors / warnings.
        blocked: Reason the dispatch was blocked (e.g. missing API key),
                 or empty string if not blocked.
    """

    slug: str = ""
    ok: bool = False
    stdout: str = ""
    stderr: str = ""
    report_md: str = ""
    command_log: list[dict[str, Any]] = field(default_factory=list)
    actions: list[CommandAction] = field(default_factory=list)
    exit_code: int | None = None
    elapsed_sec: float = 0.0
    errors: list[str] = field(default_factory=list)
    blocked: str = ""


# ---------------------------------------------------------------------------
# ActorDispatcher ABC
# ---------------------------------------------------------------------------


class ActorDispatcher(ABC):
    """Protocol for actor dispatch backends.

    Subclass this to add a new dispatch method (fake, hermes, codex, etc.).
    """

    name: str = ""

    @abstractmethod
    def dispatch(
        self,
        brief: str,
        *,
        slug: str = "",
        env: dict[str, str] | None = None,
        workdir: Path | None = None,
        extra_config: dict[str, Any] | None = None,
    ) -> ActorRunResult:
        """Dispatch an actor against a user-shaped brief.

        Args:
            brief: The user-shaped task prompt in markdown.
            slug: Human-readable label for this run.
            env: Environment variables to inject.
            workdir: Working directory for the actor.
            extra_config: Backend-specific configuration overrides.

        Returns:
            ActorRunResult capturing stdout, stderr, report, timing, etc.
        """
        ...


# ---------------------------------------------------------------------------
# FakeActorDispatcher
# ---------------------------------------------------------------------------


# Known response types the fake dispatcher can produce.
_FAKE_RESPONSES: dict[str, dict[str, Any]] = {
    "success": {
        "report_md": (
            "# Success Report\n\n"
            "## 1. What I changed\n\n"
            "I prepared a reusable recipe\n"
            "at `recipes/my_recipe.py`, compiled it to API JSON, and ran\n"
            "`vibecomfy validate` which passed.\n\n"
            "## 2. How to verify\n\n"
            "Run `python -m vibecomfy.cli validate recipes/my_recipe.py`.\n"
            "The workflow is ready for structural validation.\n"
        ),
        "stdout": (
            "[inspect] Found 3 matching workflows.\n"
            "[compile] API JSON written to out/recipes/my_recipe.api.json\n"
            "[validate] my_recipe: VALID (no errors)\n"
        ),
        "stderr": "",
        "ok": True,
        "exit_code": 0,
    },
    "forbidden-command": {
        "report_md": (
            "# Attempted Run\n\n"
            "I tried to launch the workflow with `python -m vibecomfy.cli run`\n"
            "but the command was not available in this environment.\n"
        ),
        "stdout": "",
        "stderr": (
            "PermissionError: vibecomfy.cli run is forbidden in structural mode\n"
        ),
        "ok": False,
        "exit_code": 1,
    },
    "missing-report": {
        "report_md": "",
        "stdout": "Started processing...\n",
        "stderr": "ConnectionError: timeout after 30s\n",
        "ok": False,
        "exit_code": 2,
    },
    "live-blocked": {
        "report_md": (
            "# Live Execution Blocked\n\n"
            "I identified that live execution requires a RunPod API key\n"
            "which is not available. The task cannot proceed in live mode.\n"
        ),
        "stdout": (
            "[prerequisite-check] RUNPOD_API_KEY: MISSING\n"
            "[outcome] blocked_prerequisite\n"
        ),
        "stderr": "",
        "ok": True,
        "exit_code": 0,
    },
    "empty": {
        "report_md": "",
        "stdout": "",
        "stderr": "",
        "ok": True,
        "exit_code": 0,
    },
}


class FakeActorDispatcher(ActorDispatcher):
    """Returns predetermined scripted responses for deterministic testing.

    Responses are keyed by a 'response' extra_config value that selects from
    the built-in catalogue: success, forbidden-command, missing-report,
    live-blocked, empty.

    When no response key is provided, defaults to 'success'.
    """

    name: str = "fake"

    def dispatch(
        self,
        brief: str,
        *,
        slug: str = "",
        env: dict[str, str] | None = None,
        workdir: Path | None = None,
        extra_config: dict[str, Any] | None = None,
    ) -> ActorRunResult:
        extra = extra_config or {}
        response_key = extra.get("response", "success")
        data = _FAKE_RESPONSES.get(response_key, _FAKE_RESPONSES["success"])

        t0 = time.monotonic()
        result = ActorRunResult(
            slug=slug,
            ok=data["ok"],
            stdout=data["stdout"],
            stderr=data["stderr"],
            report_md=data["report_md"],
            exit_code=data["exit_code"],
            elapsed_sec=round(time.monotonic() - t0, 3),
        )
        # Simulate a minimal command log for evidence capture.
        if "inspect" in data["stdout"]:
            result.command_log.append(
                {"command": "python -m vibecomfy.cli inspect image", "exit_code": 0}
            )
            result.actions.append(
                CommandAction(
                    action_id="0001",
                    action_type="command",
                    command="python -m vibecomfy.cli inspect image",
                    exit_code=0,
                    source="dispatcher",
                    evidence_confidence="high",
                    metadata={"response_key": response_key},
                )
            )
        if "compile" in data["stdout"]:
            result.command_log.append(
                {"command": "python -m vibecomfy.cli validate my_recipe", "exit_code": 0}
            )
            result.actions.append(
                CommandAction(
                    action_id="0002",
                    action_type="command",
                    command="python -m vibecomfy.cli validate my_recipe",
                    exit_code=0,
                    source="dispatcher",
                    evidence_confidence="high",
                    metadata={"response_key": response_key},
                )
            )
        return result


# ---------------------------------------------------------------------------
# HermesDispatcher  (real DeepSeek V4 Pro via OpenAI-compatible API)
# ---------------------------------------------------------------------------

_HERMES_SYSTEM_PROMPT = """\
You are a VibeComfy assistant. VibeComfy is a tool for discovering, editing, \
validating, and running ComfyUI workflows. You help users with image/video/audio \
generation tasks by authoring Python recipes/scratchpads, compiling to API JSON, \
and running validation commands. You are honest about what you can and cannot do. \
You never fabricate output paths, model names, or runtime claims."""


def _load_deepseek_api_key() -> str | None:
    """Source DEEPSEEK_API_KEY from environment or ~/.hermes/.env."""
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


def _call_deepseek_via_openai(
    api_key: str,
    prompt: str,
    *,
    model: str = "deepseek-v4-pro",
    base_url: str = "https://api.deepseek.com/v1",
    max_tokens: int = 32000,
    temperature: float = 0.0,
    timeout_sec: int = 300,
) -> tuple[str, str, list[dict[str, Any]]]:
    """Invoke DeepSeek V4 Pro via OpenAI-compatible chat completions API.

    Returns (content_text, error_string, command_log_entries).

    Tries openai Python SDK first, then falls back to raw requests.
    """
    messages = [
        {"role": "system", "content": _HERMES_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    # -- attempt 1: openai Python SDK --
    try:
        import openai  # type: ignore[import-untyped]

        client = openai.OpenAI(api_key=api_key, base_url=base_url)
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout_sec,
        )
        content = response.choices[0].message.content or ""
        return content, "", []
    except Exception as exc1:
        sdk_error = str(exc1)

    # -- attempt 2: raw requests fallback --
    try:
        import urllib.request
        import urllib.error

        body = json.dumps(
            {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        ).encode("utf-8")

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
        return content, "", []
    except Exception as exc2:
        return "", f"openai-sdk: {sdk_error}; requests: {exc2}", []


class HermesDispatcher(ActorDispatcher):
    """Real actor dispatcher via DeepSeek V4 Pro OpenAI-compatible API.

    Sources DEEPSEEK_API_KEY from env or ~/.hermes/.env.  When the key is
    absent the dispatcher returns an ungraded-style blocked result rather
    than raising an exception.
    """

    name: str = "hermes"

    def __init__(
        self,
        *,
        model: str = "deepseek-v4-pro",
        base_url: str = "https://api.deepseek.com/v1",
        max_tokens: int = 32000,
        temperature: float = 0.0,
        timeout_sec: int = 300,
    ) -> None:
        self.model = model
        self.base_url = base_url
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout_sec = timeout_sec

    def dispatch(
        self,
        brief: str,
        *,
        slug: str = "",
        env: dict[str, str] | None = None,
        workdir: Path | None = None,
        extra_config: dict[str, Any] | None = None,
    ) -> ActorRunResult:
        t0 = time.monotonic()

        api_key = _load_deepseek_api_key()
        if not api_key:
            elapsed = round(time.monotonic() - t0, 3)
            return ActorRunResult(
                slug=slug,
                ok=False,
                blocked="DEEPSEEK_API_KEY not found in env or ~/.hermes/.env",
                errors=["Missing DEEPSEEK_API_KEY — cannot dispatch Hermes actor."],
                elapsed_sec=elapsed,
            )

        extra = extra_config or {}
        model = extra.get("model", self.model)
        max_tokens = extra.get("max_tokens", self.max_tokens)
        temperature = extra.get("temperature", self.temperature)
        timeout = extra.get("timeout_sec", self.timeout_sec)

        content, error, command_log = _call_deepseek_via_openai(
            api_key,
            brief,
            model=model,
            base_url=self.base_url,
            max_tokens=int(max_tokens),
            temperature=float(temperature),
            timeout_sec=int(timeout),
        )

        elapsed = round(time.monotonic() - t0, 3)

        if error:
            return ActorRunResult(
                slug=slug,
                ok=False,
                stderr=error,
                errors=[f"Hermes API call failed: {error}"],
                elapsed_sec=elapsed,
            )

        # Hermes responses are treated as stdout-like narrative; report_md
        # is extracted from the response if it contains markdown headings.
        report_md = content
        stdout = content
        stderr = ""

        return ActorRunResult(
            slug=slug,
            ok=True,
            stdout=stdout,
            stderr=stderr,
            report_md=report_md,
            command_log=command_log,
            exit_code=0,
            elapsed_sec=elapsed,
        )


# ---------------------------------------------------------------------------
# SubagentLauncherDispatcher  (DeepSeek V4 Pro via Hermes agentic launcher)
# ---------------------------------------------------------------------------

_SUBAGENT_WRAPPER_PROMPT = """\
You are the actor inside a VibeComfy agentic test.

Work from this repository directory:

{workdir}

Treat the brief below as the user's actual request. Do not ask follow-up
questions. If an input is missing, make a conservative test assumption and use
staged fixtures from `fixtures/` or `workflow_corpus/input/` when available.

In structural mode, do not run workflows, launch ComfyUI, download model files,
install custom nodes, provision cloud machines, or spend GPU budget.

You have terminal access for safe structural checks only. Prefer commands like:

- python -m vibecomfy.cli workflows list --ready
- python -m vibecomfy.cli inspect <workflow-or-template>
- python -m vibecomfy.cli analyze info <workflow-or-template>
- python -m vibecomfy.cli validate <recipe-or-template>
- python -m vibecomfy.cli doctor <recipe-or-template>
- python -m vibecomfy.cli port check <workflow-or-template> --json
- python -c "import ...; ..."

Forbidden in structural mode: vibecomfy run, run_embedded_sync, queue_prompt,
ComfyUI launch, RunPod commands, model fetch/stage/download, custom-node
install/ensure, pytest --runpod, or any command that needs GPU/runtime assets.

When using terminal commands, start from the repository directory above. Prefix
commands with `cd {workdir} && ...` if the terminal opens elsewhere.

Produce a concrete deliverable. Prefer a recipe under `recipes/` or a
scratchpad under `out/scratchpads/` when the user asks to set something up.

Return your final answer to stdout as a concise markdown report with exactly
these numbered headings:

## 1. What I did
## 2. Evidence
## 3. How to verify
## 4. Open risks

User brief:

"""


def _numbered_section_count(markdown: str) -> int:
    """Count numbered markdown sections in a final actor report."""
    import re

    return len(re.findall(r"^#{1,6}\s+\d+\.", markdown, re.MULTILINE))


def _normalize_report_markdown(stdout: str) -> str:
    """Ensure actor output satisfies the harness report shape contract."""
    if _numbered_section_count(stdout) >= 2:
        return stdout
    body = stdout.strip() or "(actor produced no final stdout)"
    return (
        "## 1. Actor Output\n\n"
        f"{body}\n\n"
        "## 2. Harness Notes\n\n"
        "The actor did not return numbered markdown sections, so the harness "
        "wrapped the raw final output without changing its content.\n"
    )


def _launcher_model_name(model: str | None) -> str:
    """Normalize a model name for the subagent-launcher Hermes wrapper."""
    if not model:
        return "deepseek:deepseek-v4-pro"
    if ":" in model:
        return model
    if model.startswith("deepseek"):
        return f"deepseek:{model}"
    return model



def _parse_agentic_tool_log(jsonl_path):
    """Parse AGENTIC_TOOL_LOG_PATH JSONL into CommandAction rows (high confidence)."""
    import json
    from pathlib import Path
    from sisypy.schema import CommandAction

    path = Path(jsonl_path)
    if not path.is_file():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    if not raw.strip():
        return []

    actions = []
    seq = 0
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        seq += 1
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        # Support both flat and nested shapes.
        if isinstance(entry, dict):
            inner = entry.get("action", entry)
        else:
            inner = entry if isinstance(entry, dict) else {}
        if not isinstance(inner, dict):
            continue
        action_id = str(inner.get("action_id", f"{seq + 1:04d}"))
        command = inner.get("command", inner.get("tool_call", ""))
        if not command:
            continue
        actions.append(CommandAction(
            action_id=action_id,
            action_type=inner.get("action_type", "command"),
            command=str(command),
            cwd=inner.get("cwd", ""),
            exit_code=inner.get("exit_code"),
            duration_sec=inner.get("duration_sec"),
            stdout_path=inner.get("stdout_path", ""),
            stderr_path=inner.get("stderr_path", ""),
            stdout_preview=inner.get("stdout_preview", ""),
            stderr_preview=inner.get("stderr_preview", ""),
            source=inner.get("source", "tool_log"),
            evidence_confidence="high",
            metadata=inner.get("metadata", {}),
        ))
    return actions


# Regex patterns for stderr tool-trace parsing.
# Regex patterns for stderr tool-trace parsing.
_TOOL_MARKER_RE = re.compile(
    r'(?:🔧|🛠️?)\s*\[tool\]\s*(.+)',
)
_TOOL_DONE_RE = re.compile(
    r'(?:✅|✔)\s*\[done\]',
)


def _parse_stderr_tool_traces(stderr, *, start_seq=0):
    """Parse [tool]/[done] lines from stderr into low-confidence CommandAction rows.

    Returns a list of CommandAction with evidence_confidence='low'.
    Exit status and duration are extracted from [done] lines when visible.
    Unknown fields are left at their defaults.

    Handles two common stderr trace formats:

    1. Multi-line (Hermes/DeepSeek subagent):
       🔧 [tool] terminal
       python -m vibecomfy.cli validate recipes/demo.py
       ✅ [done] terminal (exit=0, 1.5s)

    2. Single-line fallback:
       🔧 [tool] python -m vibecomfy.cli validate recipes/demo.py
       ✅ [done] exit=0
    """
    from sisypy.schema import CommandAction

    if not stderr:
        return []

    lines = stderr.splitlines()
    actions: list = []
    in_tool = False
    cmd_lines: list[str] = []
    current_tool_name = ""
    seq_counter = start_seq

    for line in lines:
        tool_match = _TOOL_MARKER_RE.match(line)
        done_match = _TOOL_DONE_RE.search(line)

        if tool_match and not in_tool:
            in_tool = True
            current_tool_name = tool_match.group(1).strip()
            continue

        if done_match and in_tool:
            # Extract exit code and duration from the [done] line.
            # Supports formats:
            #   (exit=0, 1.5s)
            #   (exit=0, duration=1.5s)
            #   exit=0
            exit_code = None
            duration_sec = None

            # Try parenthesized form first: (exit=0, 1.5s)
            paren_match = re.search(r'\(exit[=:]\s*(\d+)[,\s]*([\d.]+)\s*s?\)', line)
            if paren_match:
                exit_code = int(paren_match.group(1))
                duration_sec = float(paren_match.group(2))
            else:
                # Try bare exit= form.
                exit_match = re.search(r'exit[=:]\s*(\d+)', line)
                if exit_match:
                    exit_code = int(exit_match.group(1))
                dur_match = re.search(r'(?:duration[=:]?\s*|^)([\d.]+)\s*s?', line)
                if dur_match:
                    try:
                        duration_sec = float(dur_match.group(1))
                    except (ValueError, TypeError):
                        pass

            # Build command text from collected lines.
            cmd_text = "\n".join(cmd_lines).strip() if cmd_lines else current_tool_name

            if not cmd_text:
                cmd_text = current_tool_name

            # Skip pure tool-name placeholders (no actual command).
            if cmd_text and cmd_text not in ("terminal", "bash", "shell", "subprocess"):
                if len(cmd_text) > 2000:
                    cmd_text = cmd_text[:2000] + "…"

                seq_counter += 1
                actions.append(CommandAction(
                    action_id=f"{seq_counter:04d}",
                    action_type="command",
                    command=cmd_text,
                    cwd="",
                    exit_code=exit_code,
                    duration_sec=duration_sec,
                    source="stderr-parse",
                    evidence_confidence="low",
                    metadata={
                        "parse_method": "stderr_tool_trace",
                        "tool_name": current_tool_name,
                    },
                ))

            # Reset for next tool block.
            in_tool = False
            cmd_lines = []
            current_tool_name = ""
            continue

        if in_tool:
            # Collect command text lines.
            stripped = line.strip()
            if stripped:
                cmd_lines.append(stripped)

    return actions

class SubagentLauncherDispatcher(ActorDispatcher):
    """Dispatch DeepSeek V4 Pro through the file/web-enabled Hermes launcher.

    This is the real agentic path used when the harness should evaluate from an
    agent perspective: the launched actor receives file and web tools through
    ``subagent-launcher`` instead of a plain chat-completions prompt.
    """

    name: str = "deepseek-subagent"

    def __init__(
        self,
        *,
        model: str = "deepseek:deepseek-v4-pro",
        toolsets: str = "file,web,terminal",
        max_tokens: int = 32768,
        timeout_sec: int = 2400,
        launcher_path: Path | str | None = None,
    ) -> None:
        self.model = _launcher_model_name(model)
        self.toolsets = toolsets
        self.max_tokens = max_tokens
        self.timeout_sec = timeout_sec
        self.launcher_path = Path(
            launcher_path
            or Path.home()
            / ".claude"
            / "skills"
            / "subagent-launcher"
            / "launch_hermes_agent.py"
        )

    def dispatch(
        self,
        brief: str,
        *,
        slug: str = "",
        env: dict[str, str] | None = None,
        workdir: Path | None = None,
        extra_config: dict[str, Any] | None = None,
    ) -> ActorRunResult:
        t0 = time.monotonic()
        if not self.launcher_path.is_file():
            return ActorRunResult(
                slug=slug,
                ok=False,
                blocked=f"subagent-launcher not found: {self.launcher_path}",
                errors=[f"Missing launcher script: {self.launcher_path}"],
                elapsed_sec=round(time.monotonic() - t0, 3),
            )

        extra = extra_config or {}
        model = _launcher_model_name(str(extra.get("model", self.model)))
        toolsets = str(extra.get("toolsets", self.toolsets))
        max_tokens=int(extra.get("max_tokens", self.max_tokens))
        timeout = int(extra.get("timeout_sec", self.timeout_sec))
        cwd = Path(workdir or Path.cwd())

        prompt_path: Path | None = None
        tool_log_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                suffix=".md",
                prefix=f"{slug or 'agentic'}-",
                delete=False,
            ) as fp:
                fp.write(_SUBAGENT_WRAPPER_PROMPT.format(workdir=cwd))
                fp.write(brief)
                prompt_path = Path(fp.name)

            # Create a temp JSONL file for AGENTIC_TOOL_LOG_PATH so the
            # subagent-launcher can write structured tool traces if it supports
            # the env var.  We request it; we don't require it.
            tool_log_fd = tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                suffix=".jsonl",
                prefix=f"{slug or 'agentic'}-tools-",
                delete=False,
            )
            tool_log_fd.close()
            tool_log_path = Path(tool_log_fd.name)

            cmd = [
                sys.executable,
                str(self.launcher_path),
                "--model",
                model,
                "--toolsets",
                toolsets,
                "--query_file",
                str(prompt_path),
                "--max_tokens",
                str(max_tokens),
                "--project_dir",
                str(cwd),
            ]

            proc_env = os.environ.copy()
            proc_env.update(env or {})
            proc_env.setdefault("PYENV_VERSION", "3.11.11")
            proc_env["AGENTIC_TOOL_LOG_PATH"] = str(tool_log_path)

            completed = subprocess.run(
                cmd,
                cwd=str(cwd),
                env=proc_env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            elapsed = round(time.monotonic() - t0, 3)

            # --- Build CommandActions from evidence ---
            actions: list[CommandAction] = []
            capture_notes: list[str] = []
            evidence_confidence: str = "high"

            # Action 0001: the launcher subprocess itself (always high confidence).
            launcher_action = CommandAction(
                action_id="0001",
                action_type="command",
                command=" ".join(cmd),
                cwd=str(cwd),
                exit_code=completed.returncode,
                duration_sec=elapsed,
                stdout_preview=completed.stdout[:200] if completed.stdout else "",
                stderr_preview=completed.stderr[:200] if completed.stderr else "",
                source="dispatcher",
                evidence_confidence="high",
                metadata={"model": model, "toolsets": toolsets},
            )
            actions.append(launcher_action)

            # Attempt structured tool-log parsing.
            tool_actions = _parse_agentic_tool_log(tool_log_path)
            if tool_actions:
                for ta in tool_actions:
                    actions.append(ta)
            else:
                # Fall back to stderr tool-trace parsing.
                stderr_actions = _parse_stderr_tool_traces(
                    completed.stderr, start_seq=len(actions)
                )
                if stderr_actions:
                    evidence_confidence = "low"
                    capture_notes.append(
                        "Tool commands parsed from stderr [tool]/[done] traces "
                        "(low confidence -- command text may be truncated or "
                        "incomplete). AGENTIC_TOOL_LOG_PATH JSONL was empty or "
                        "unsupported by the launcher."
                    )
                    for sa in stderr_actions:
                        actions.append(sa)
                else:
                    capture_notes.append(
                        "No structured tool traces available -- neither "
                        "AGENTIC_TOOL_LOG_PATH JSONL nor stderr tool-trace "
                        "patterns found. SubagentLauncherDispatcher evidence "
                        "is limited to the top-level launcher invocation."
                    )

            command_log = [{
                "command": (
                    "subagent-launcher "
                    f"--model {model} --toolsets {toolsets} "
                    f"--query_file {prompt_path} --project_dir {cwd}"
                ),
                "exit_code": completed.returncode,
            }]

            errors: list[str] = []
            if completed.returncode != 0:
                errors.append(
                    f"subagent-launcher exited {completed.returncode}"
                )
            if evidence_confidence == "low":
                errors.extend(capture_notes)

            return ActorRunResult(
                slug=slug,
                ok=completed.returncode == 0,
                stdout=completed.stdout,
                stderr=completed.stderr,
                report_md=_normalize_report_markdown(completed.stdout),
                command_log=command_log,
                actions=actions,
                exit_code=completed.returncode,
                elapsed_sec=elapsed,
                errors=errors,
            )
        except subprocess.TimeoutExpired as exc:
            elapsed = round(time.monotonic() - t0, 3)
            return ActorRunResult(
                slug=slug,
                ok=False,
                stdout=exc.stdout or "",
                stderr=exc.stderr or "",
                report_md=exc.stdout or "",
                exit_code=None,
                elapsed_sec=elapsed,
                errors=[f"subagent-launcher timed out after {timeout}s"],
            )
        finally:
            if prompt_path:
                try:
                    prompt_path.unlink()
                except OSError:
                    pass
            if tool_log_path:
                try:
                    tool_log_path.unlink()
                except OSError:
                    pass
