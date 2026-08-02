import os
import platform
import time
import signal
import subprocess

from src.types.tools import Tool

DEFAULT_TIMEOUT_MS = 120_000
MAX_TIMEOUT_MS = 600_000
MAX_OUTPUT_BYTES = 1_048_576

_OS_NAME = platform.system() or "unknown"
_SHELL = "cmd" if os.name == "nt" else "bash"


def _kill_process_group(pid: int, force_kill_after: int = 3):
    if os.name == "nt":
        subprocess.run(["taskkill", "/pid", str(pid), "/T", "/F"], capture_output=True)
        return
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + force_kill_after
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
            time.sleep(0.1)
        except ProcessLookupError:
            return
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def execute(command: str, workdir: str = "", timeout: int = 0, **kwargs) -> dict:
    cwd = workdir or os.getcwd()
    cwd = os.path.abspath(os.path.expanduser(cwd))

    if not os.path.isdir(cwd):
        return {
            "title": command,
            "output": f"Working directory is not a directory: {cwd}",
            "metadata": {"error": True},
        }

    timeout_ms = DEFAULT_TIMEOUT_MS
    if timeout > 0:
        timeout_ms = min(timeout, MAX_TIMEOUT_MS)

    env = os.environ.copy()
    env["PWD"] = cwd

    try:
        proc = subprocess.Popen(
            command,
            shell=True,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            preexec_fn=os.setsid if os.name != "nt" else None,
        )
    except OSError as e:
        return {
            "title": command,
            "output": f"Unable to execute command: {e}",
            "metadata": {"error": True},
        }

    try:
        stdout_bytes, _ = proc.communicate(timeout=timeout_ms / 1000)
    except subprocess.TimeoutExpired:
        _kill_process_group(proc.pid, force_kill_after=3)
        return {
            "title": command,
            "output": f"Command exceeded timeout of {timeout_ms} ms. Retry with a larger timeout if the command is expected to take longer.",
            "metadata": {"exit": None, "truncated": False, "timeout": True},
        }

    truncated = False
    if len(stdout_bytes) > MAX_OUTPUT_BYTES:
        stdout_bytes = stdout_bytes[:MAX_OUTPUT_BYTES]
        truncated = True

    output_text = stdout_bytes.decode("utf-8", errors="replace") or "(no output)"
    if truncated:
        output_text += "\n\n[output capture truncated at the in-memory safety limit]"

    return {
        "title": command,
        "output": output_text,
        "metadata": {"exit": proc.returncode, "truncated": truncated, "timeout": False},
    }


def to_model_output(data: dict) -> str:
    meta = data.get("metadata", {})
    if meta.get("error"):
        return data["output"]

    output = data.get("output", "")
    exit_code = meta.get("exit")

    if exit_code is None:
        return f"Command timed out before completion.\n\n{output}"

    return f"Command exited with code {exit_code}.\n\n{output}"


tool = Tool(
    name="bash",
    description=f"""Executes a bash command with optional timeout and working directory.

OS: {_OS_NAME}, Shell: {_SHELL}.

DO NOT use for file operations (read/write/edit/search) — use dedicated tools instead.

Execution rules:
- Quote file paths containing spaces with double quotes
- Prefer dedicated tools: Glob > find/ls, Grep > grep/rg, Read > cat/head/tail,
  Edit > sed/awk, Write > echo/cat, text output > echo/printf
- Output exceeding 2000 lines or 51200 bytes is auto-truncated to a file;
  use Read offset/limit or Grep to search — NOT head/tail/sed/awk

Multiple commands:
- Independent tasks: parallel tool calls in one message
- Sequential dependent tasks: chain with `&&`
  (e.g., `git add . && git commit -m "msg" && git push`)
- Best-effort sequence: use `;`
- Use `workdir` parameter, NOT `cd <dir> && <cmd>`
- No newlines between commands (use `&&` or `;`); newlines OK inside quoted strings

Git rules:
- Only commit/push/PR when explicitly requested
- Before commit: check `git status`, `git diff`, `git log --oneline -10`
- Stage only intended files; never commit secrets
- Write concise commit messages; do not amend failed commits
- Before PR: check status, diff, remote tracking, recent commits, diff from base
- Review all commits in the PR
- Use `gh` for GitHub tasks; return the PR URL""",
    parameters={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to execute",
            },
            "workdir": {
                "type": "string",
                "description": "Working directory. Defaults to the current working directory; relative paths resolve from this directory.",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in milliseconds (default 120000, max 600000)",
            },
        },
        "required": ["command"],
    },
    execute=execute,
    to_model_output=to_model_output,
)
