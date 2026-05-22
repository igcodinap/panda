#!/usr/bin/env python3
"""Run consultations against local Claude Code and OpenCode CLIs."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime as dt
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Iterable, Optional
import uuid


ROLE_GUIDANCE = {
    "brainstorm": "Propose distinct implementation approaches with tradeoffs.",
    "research": "Research repo/docs/API behavior and report evidence, tradeoffs, and uncertainties.",
    "planning": "Turn goals into a concrete implementation plan with risks, sequencing, and validation.",
    "implementation-review": "Critique the proposed implementation path for risks, simpler alternatives, and verification.",
    "debugging": "Suggest likely root causes, discriminating checks, and the smallest useful diagnostic step.",
    "code-review": "Review for behavioral bugs, edge cases, missing tests, and maintainability risks.",
    "test-plan": "Propose focused tests and manual verification for the described change.",
}

DEFAULT_OPENCODE_MODEL = "opencode-go/glm-5.1"
DEFAULT_APPROVAL_MODE = "unsupervised"
EXECUTION_CHOICES = ("auto", "parallel", "sequential")
APPROVAL_MODE_CHOICES = ("unsupervised", "supervised")
CLAUDE_EFFORT_CHOICES = ("low", "medium", "high", "xhigh", "max")
MODEL_PROFILES = {
    "fast": {
        "claude_model": "sonnet",
        "claude_effort": "medium",
        "opencode_model": DEFAULT_OPENCODE_MODEL,
        "cost_tier": "low",
    },
    "balanced": {
        "claude_model": "sonnet",
        "claude_effort": "high",
        "opencode_model": DEFAULT_OPENCODE_MODEL,
        "cost_tier": "medium",
    },
    "deep": {
        "claude_model": "opus",
        "claude_effort": "max",
        "opencode_model": DEFAULT_OPENCODE_MODEL,
        "cost_tier": "high",
    },
}
ROLE_DEFAULT_PROFILES = {
    "brainstorm": "balanced",
    "research": "deep",
    "planning": "deep",
    "implementation-review": "deep",
    "debugging": "balanced",
    "code-review": "balanced",
    "test-plan": "fast",
}
HARD_FALLBACK_PROFILE = "balanced"
SCHEMA_VERSION = 1
DEFAULT_STRAGGLER_TIMEOUT = 120
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
CLAUDE_EFFORT_SUPPORT_CACHE: dict[str, bool] = {}
EXPLICIT_ARG_FLAGS = {
    "--mode": "mode",
    "--execution": "execution",
    "--approval-mode": "approval_mode",
    "--role": "role",
    "--profile": "profile",
    "--claude-model": "claude_model",
    "--claude-effort": "claude_effort",
    "--opencode-model": "opencode_model",
}

MODE_GUIDANCE = {
    "advisory": """Rules:
- Do not edit files.
- Do not run shell commands.
- Do not ask for credentials or secrets.
- Be concise and concrete.
- Call out assumptions and uncertainty.""",
    "explore": """Rules:
- You may run shell commands to inspect files, git state, tests, builds, logs, dependencies, and relevant documentation.
- Avoid source edits. Do not intentionally rewrite files, commit, push, publish, deploy, delete data, or alter production systems.
- If any command changes files, report every changed file and why it changed.
- Do not ask for credentials or secrets.
- Be concise and concrete.
- Call out assumptions and uncertainty.""",
    "patch": """Rules:
- You may make candidate changes only for the requested task.
- Report every changed file, summarize the diff, list commands/tests run, and call out risks.
- Do not commit, push, publish, deploy, delete data, or alter production systems.
- Do not ask for credentials or secrets.
- Be concise and concrete.
- Call out assumptions and uncertainty.""",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    explicit_flags = collect_explicit_flags(sys.argv[1:])
    parser.add_argument("--tool", choices=["claude", "opencode", "both"], default="both")
    parser.add_argument("--mode", choices=sorted(MODE_GUIDANCE), default="explore")
    parser.add_argument(
        "--execution",
        choices=EXECUTION_CHOICES,
        default=os.environ.get("AI_TEAM_EXECUTION", "auto"),
        help="Run multiple tools in parallel, sequentially, or auto mode. Auto parallelizes advisory/explore and serializes patch.",
    )
    parser.add_argument(
        "--approval-mode",
        choices=APPROVAL_MODE_CHOICES,
        default=os.environ.get("AI_TEAM_APPROVAL_MODE", DEFAULT_APPROVAL_MODE),
        help="Whether Claude Code/OpenCode should auto-approve their own tool prompts. Defaults to unsupervised.",
    )
    parser.add_argument("--role", choices=sorted(ROLE_GUIDANCE), default="brainstorm")
    parser.add_argument(
        "--profile",
        choices=sorted(MODEL_PROFILES),
        help="Model/effort profile. Defaults from --role unless explicitly provided.",
    )
    parser.add_argument("--prompt", help="Prompt text to send to the tools.")
    parser.add_argument("--prompt-file", type=Path, help="File containing prompt text.")
    parser.add_argument(
        "--session",
        nargs="?",
        const="",
        help="Enable session mode. Omit the value to create a new session, or provide a session ID to continue.",
    )
    parser.add_argument("--session-dir", type=Path, help="Directory for persistent AI team sessions.")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="Workspace to run explore/patch consultations in.")
    parser.add_argument("--output-dir", type=Path, help="Directory for responses and manifest.")
    parser.add_argument("--timeout", type=int, default=600, help="Timeout per tool in seconds.")
    parser.add_argument(
        "--straggler-timeout",
        type=int,
        default=DEFAULT_STRAGGLER_TIMEOUT,
        help="Session mode: seconds to wait for remaining tools after one collaborator finishes.",
    )
    parser.add_argument("--claude-bin", default=os.environ.get("CLAUDE_BIN", "claude"))
    parser.add_argument("--opencode-bin", default=os.environ.get("OPENCODE_BIN", "opencode"))
    parser.add_argument("--claude-model", help="Optional Claude Code model override.")
    parser.add_argument(
        "--claude-effort",
        choices=CLAUDE_EFFORT_CHOICES,
        help="Optional Claude Code reasoning effort override.",
    )
    parser.add_argument(
        "--opencode-model",
        help=f"OpenCode model in provider/model format. Defaults through profile/env to {DEFAULT_OPENCODE_MODEL}.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print commands without running tools.")
    args = parser.parse_args()
    validate_choice(parser, "AI_TEAM_EXECUTION/--execution", args.execution, EXECUTION_CHOICES)
    validate_choice(parser, "AI_TEAM_APPROVAL_MODE/--approval-mode", args.approval_mode, APPROVAL_MODE_CHOICES)
    if args.mode == "patch" and args.execution == "parallel":
        parser.error("--execution parallel is not allowed with --mode patch; patch consultations run sequentially.")
    if args.straggler_timeout < 1:
        parser.error("--straggler-timeout must be at least 1 second.")
    if args.timeout < 1:
        parser.error("--timeout must be at least 1 second.")
    args.explicit_flags = explicit_flags
    args.profile_resolution = resolve_profile(args)
    return args


def collect_explicit_flags(argv: list[str]) -> set[str]:
    explicit_flags = set()
    for arg in argv:
        for flag, dest in EXPLICIT_ARG_FLAGS.items():
            if arg == flag or arg.startswith(f"{flag}="):
                explicit_flags.add(dest)
                break
    return explicit_flags


def validate_choice(parser: argparse.ArgumentParser, label: str, value: str, choices: Iterable[str]) -> None:
    if value in choices:
        return
    parser.error(f"{label} must be one of: {', '.join(choices)}")


def resolve_profile(args: argparse.Namespace) -> dict:
    profile_name = args.profile or ROLE_DEFAULT_PROFILES.get(args.role) or HARD_FALLBACK_PROFILE
    if args.profile:
        profile_source = "cli"
    elif args.role in ROLE_DEFAULT_PROFILES:
        profile_source = "role_default"
    else:
        profile_source = "fallback"

    profile = MODEL_PROFILES[profile_name]
    env_opencode_model = os.environ.get("OPENCODE_MODEL")
    claude_model = args.claude_model or profile["claude_model"]
    claude_effort = args.claude_effort or profile["claude_effort"]
    if args.opencode_model:
        opencode_model = args.opencode_model
    elif args.profile:
        opencode_model = profile["opencode_model"]
    elif env_opencode_model:
        opencode_model = env_opencode_model
    else:
        opencode_model = profile["opencode_model"]

    return {
        "profile": profile_name,
        "profile_source": profile_source,
        "cost_tier": profile["cost_tier"],
        "effective_models": {
            "claude": claude_model,
            "opencode": opencode_model,
        },
        "effective_effort": {
            "claude": claude_effort,
        },
        "requested_models": {
            "claude": args.claude_model,
            "opencode": args.opencode_model or env_opencode_model or DEFAULT_OPENCODE_MODEL,
        },
        "requested_effort": {
            "claude": args.claude_effort,
        },
        "effort_support": {
            "claude": None,
        },
        "applied_effort": {
            "claude": None,
        },
    }


def get_profile_resolution(args: argparse.Namespace) -> dict:
    if not hasattr(args, "profile_resolution"):
        args.profile_resolution = resolve_profile(args)
    return args.profile_resolution


def profile_manifest_metadata(args: argparse.Namespace) -> dict:
    resolution = get_profile_resolution(args)
    return {
        "profile": resolution["profile"],
        "profile_source": resolution["profile_source"],
        "cost_tier": resolution["cost_tier"],
        "effective_models": dict(resolution["effective_models"]),
        "effective_effort": dict(resolution["effective_effort"]),
        "requested_models": dict(resolution["requested_models"]),
        "requested_effort": dict(resolution["requested_effort"]),
        "effort_support": dict(resolution["effort_support"]),
        "applied_effort": dict(resolution["applied_effort"]),
    }


def claude_supports_effort(claude_bin: str) -> bool:
    if claude_bin in CLAUDE_EFFORT_SUPPORT_CACHE:
        return CLAUDE_EFFORT_SUPPORT_CACHE[claude_bin]
    try:
        completed = subprocess.run(
            [claude_bin, "--help"],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        CLAUDE_EFFORT_SUPPORT_CACHE[claude_bin] = False
        return False
    help_text = f"{completed.stdout}\n{completed.stderr}"
    supported = "--effort" in help_text
    CLAUDE_EFFORT_SUPPORT_CACHE[claude_bin] = supported
    return supported


def record_claude_effort_support(args: argparse.Namespace, supported: bool, applied_effort: Optional[str]) -> None:
    resolution = get_profile_resolution(args)
    resolution.setdefault("effort_support", {})["claude"] = supported
    resolution.setdefault("applied_effort", {})["claude"] = applied_effort


def read_prompt(args: argparse.Namespace) -> str:
    parts: list[str] = []
    if args.prompt:
        parts.append(args.prompt)
    if args.prompt_file:
        parts.append(args.prompt_file.read_text(encoding="utf-8"))
    prompt = "\n\n".join(part.strip() for part in parts if part.strip())
    if not prompt:
        raise SystemExit("Provide --prompt or --prompt-file.")
    return prompt


def consultation_prompt(mode: str, role: str, approval_mode: str, user_prompt: str) -> str:
    return f"""You are advising Codex as an independent collaborator.

Mode: {mode}
Approval mode: {approval_mode}
Role: {role}
Role guidance: {ROLE_GUIDANCE[role]}

{MODE_GUIDANCE[mode]}

Approval guidance:
- If approval mode is unsupervised, proceed through local tool permission prompts without stopping for Codex approval.
- Even in unsupervised mode, do not commit, push, publish, deploy, delete data, rewrite history, or alter production systems unless the user explicitly requested that class of action.

User/context prompt:
{user_prompt}

Return:
- Recommendation
- Alternative worth considering
- Risks or edge cases
- Verification plan
"""


def quote_cmd(command: Iterable[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def default_session_root() -> Path:
    return Path(tempfile.gettempdir()) / "panda-sessions"


def validate_session_id(session_id: str) -> None:
    if session_id in {".", ".."}:
        raise SystemExit("Session IDs may not be '.' or '..'.")
    if SESSION_ID_PATTERN.match(session_id):
        return
    raise SystemExit("Session IDs may contain only letters, numbers, dots, dashes, and underscores.")


def process_is_running(pid: object) -> bool:
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return False
    if pid_int <= 0:
        return False
    try:
        os.kill(pid_int, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as file_obj:
            file_obj.write(json.dumps(data, indent=2) + "\n")
            file_obj.flush()
            os.fsync(file_obj.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def make_session(args: argparse.Namespace, workspace: Path) -> tuple[dict, Path, bool]:
    session_root = (args.session_dir or default_session_root()).expanduser()
    if args.session:
        session_id = args.session
        validate_session_id(session_id)
        session_path = session_root / session_id
        session_file = session_path / "session.json"
        if not session_file.exists():
            raise SystemExit(f"Session not found: {session_id}")
        session = read_json(session_file)
        if session.get("schema_version") != SCHEMA_VERSION:
            raise SystemExit(f"Unsupported session schema: {session.get('schema_version')!r}")
        if session.get("status") == "running":
            if process_is_running(session.get("runner_pid")):
                raise SystemExit(f"Session is already running: {session_id}")
            session["status"] = "waiting_for_user"
            session["last_turn_status"] = "degraded"
            session["latest_stopping_suggestion"] = "tool_timed_out"
            session["recovered_running_session_at"] = now_iso()
            session["runner_pid"] = None
        return session, session_path, False

    session_id = str(uuid.uuid4())
    session_path = session_root / session_id
    profile_metadata = profile_manifest_metadata(args)
    session = {
        "schema_version": SCHEMA_VERSION,
        "session_id": session_id,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "status": "created",
        "workspace": str(workspace),
        "mode": args.mode,
        "role": args.role,
        "approval_mode": args.approval_mode,
        "execution": args.execution,
        "tool_session_ids": {
            "claude": None,
            "opencode": None,
        },
        "models": dict(profile_metadata["effective_models"]),
        "profile": profile_metadata["profile"],
        "profile_source": profile_metadata["profile_source"],
        "cost_tier": profile_metadata["cost_tier"],
        "effective_models": dict(profile_metadata["effective_models"]),
        "effective_effort": dict(profile_metadata["effective_effort"]),
        "requested_models": dict(profile_metadata["requested_models"]),
        "requested_effort": dict(profile_metadata["requested_effort"]),
        "effort_support": dict(profile_metadata["effort_support"]),
        "applied_effort": dict(profile_metadata["applied_effort"]),
        "turn_count": 0,
        "last_turn_status": None,
        "latest_stopping_suggestion": None,
        "runner_pid": None,
    }
    return session, session_path, True


def inherit_session_args(args: argparse.Namespace, session: dict) -> None:
    explicit_flags = getattr(args, "explicit_flags", set())
    for field in ("mode", "role", "approval_mode", "execution"):
        if field not in explicit_flags and session.get(field):
            setattr(args, field, session[field])
    if "profile" not in explicit_flags and session.get("profile"):
        args.profile = session["profile"]

    requested_models = session.get("requested_models") or {}
    requested_effort = session.get("requested_effort") or {}
    if "claude_model" not in explicit_flags and requested_models.get("claude"):
        args.claude_model = requested_models["claude"]
    if "opencode_model" not in explicit_flags and requested_models.get("opencode"):
        args.opencode_model = requested_models["opencode"]
    if "claude_effort" not in explicit_flags and requested_effort.get("claude"):
        args.claude_effort = requested_effort["claude"]
    args.profile_resolution = resolve_profile(args)


def next_turn_dir(session_path: Path, turn_number: int) -> Path:
    return session_path / "turns" / f"{turn_number:03d}"


def parse_opencode_jsonl(raw: str) -> tuple[Optional[str], str]:
    session_id = None
    text_parts: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not session_id and isinstance(event.get("sessionID"), str):
            session_id = event["sessionID"]
        part = event.get("part")
        if event.get("type") == "text" and isinstance(part, dict) and isinstance(part.get("text"), str):
            text_parts.append(part["text"])
    return session_id, "".join(text_parts).strip()


def run_tool(name: str, command: list[str], cwd: Path, timeout: int, dry_run: bool) -> dict:
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    result = {
        "tool": name,
        "command": command,
        "cwd": str(cwd),
        "started_at": started,
        "returncode": None,
        "timed_out": False,
        "stdout": "",
        "stderr": "",
        "finished_at": None,
    }
    if dry_run:
        result["stdout"] = quote_cmd(command)
        result["returncode"] = 0
        result["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        return result
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        result["timed_out"] = True
        result["returncode"] = -1
        result["stdout"] = exc.stdout or ""
        result["stderr"] = exc.stderr or f"Timed out after {timeout} seconds."
        result["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        return result
    except OSError as exc:
        result["returncode"] = -1
        result["stderr"] = f"Failed to launch {name}: {exc}"
        result["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        return result
    result["returncode"] = completed.returncode
    result["stdout"] = completed.stdout
    result["stderr"] = completed.stderr
    result["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    return result


def failed_tool_result(name: str, command: list[str], cwd: Path, exc: Exception) -> dict:
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    return {
        "tool": name,
        "command": command,
        "cwd": str(cwd),
        "started_at": now,
        "returncode": -1,
        "timed_out": False,
        "stdout": "",
        "stderr": f"Unhandled runner error for {name}: {type(exc).__name__}: {exc}",
        "finished_at": now,
    }


def write_response(output_dir: Path, result: dict) -> None:
    body = result["stdout"].strip()
    if result["stderr"].strip():
        body += f"\n\n[stderr]\n{result['stderr'].strip()}\n"
    if result["timed_out"]:
        timeout_kind = result.get("timeout_kind")
        suffix = f" ({timeout_kind})" if timeout_kind else ""
        body += f"\n\n[status]\nTimed out{suffix}.\n"
    elif result["returncode"] != 0:
        body += f"\n\n[status]\nExited with code {result['returncode']}.\n"
    (output_dir / f"{result['tool']}.txt").write_text(body.strip() + "\n", encoding="utf-8")


def should_run_parallel(execution: str, mode: str, tool_count: int) -> bool:
    if tool_count < 2:
        return False
    if mode == "patch":
        return False
    if execution == "parallel":
        return True
    if execution == "sequential":
        return False
    return mode in {"advisory", "explore"}


def build_commands(
    args: argparse.Namespace,
    prompt: str,
    run_cwd: Path,
    session: Optional[dict] = None,
) -> tuple[dict[str, list[str]], set[str]]:
    requested = ["claude", "opencode"] if args.tool == "both" else [args.tool]
    commands: dict[str, list[str]] = {}
    json_tools: set[str] = set()
    profile_resolution = get_profile_resolution(args)

    if "claude" in requested:
        claude_bin = shutil.which(args.claude_bin) or args.claude_bin
        claude_command = [
            claude_bin,
            "-p",
            "--output-format",
            "text",
        ]
        if session is None:
            claude_command.append("--no-session-persistence")
        else:
            tool_sessions = session.setdefault("tool_session_ids", {})
            claude_session_id = tool_sessions.get("claude")
            if claude_session_id:
                claude_command.extend(["--resume", claude_session_id])
            else:
                claude_session_id = str(uuid.uuid4())
                if not args.dry_run:
                    tool_sessions["claude"] = claude_session_id
                claude_command.extend(["--session-id", claude_session_id])
        claude_model = profile_resolution["effective_models"]["claude"]
        claude_effort = profile_resolution["effective_effort"]["claude"]
        if claude_model:
            claude_command.extend(["--model", claude_model])
        supports_effort = claude_supports_effort(claude_bin)
        applied_effort = claude_effort if claude_effort and supports_effort else None
        record_claude_effort_support(args, supports_effort, applied_effort)
        if applied_effort:
            claude_command.extend(["--effort", applied_effort])
        if args.mode == "advisory":
            claude_command.extend(["--permission-mode", "plan", "--tools="])
        else:
            if args.approval_mode == "unsupervised":
                claude_command.extend(["--permission-mode", "bypassPermissions"])
            else:
                claude_command.extend(["--permission-mode", "default"])
        if args.mode == "explore":
            claude_command.extend([
                "--allowedTools=Read,Grep,Glob,LS,Bash,WebFetch,WebSearch",
            ])
        claude_command.append(prompt)
        commands["claude"] = claude_command

    if "opencode" in requested:
        opencode_bin = shutil.which(args.opencode_bin) or args.opencode_bin
        commands["opencode"] = [
            opencode_bin,
            "run",
            "--pure",
            "--title",
            f"panda-{args.mode}-{args.role}",
            "--dir",
            str(run_cwd),
        ]
        if session is not None:
            json_tools.add("opencode")
            opencode_session_id = session.setdefault("tool_session_ids", {}).get("opencode")
            if opencode_session_id:
                commands["opencode"].extend(["--session", opencode_session_id])
            commands["opencode"].extend(["--format", "json"])
        if args.approval_mode == "unsupervised":
            commands["opencode"].append("--dangerously-skip-permissions")
        opencode_model = profile_resolution["effective_models"]["opencode"]
        if opencode_model:
            commands["opencode"].extend(["--model", opencode_model])
        commands["opencode"].append(prompt)
    return commands, json_tools


def run_one_shot(args: argparse.Namespace, prompt: str) -> int:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = args.output_dir or Path(tempfile.gettempdir()) / "panda-consults" / stamp
    output_dir.mkdir(parents=True, exist_ok=True)
    isolated_cwd = output_dir / "isolated-cwd"
    isolated_cwd.mkdir(exist_ok=True)
    workspace = args.workspace.resolve()
    run_cwd = isolated_cwd if args.mode == "advisory" else workspace

    commands, _ = build_commands(args, prompt, run_cwd)

    run_parallel = should_run_parallel(args.execution, args.mode, len(commands))
    raw_results: dict[str, dict] = {}
    if run_parallel:
        with ThreadPoolExecutor(max_workers=len(commands)) as executor:
            futures = {
                executor.submit(run_tool, name, command, run_cwd, args.timeout, args.dry_run): name
                for name, command in commands.items()
            }
            for future in as_completed(futures):
                name = futures[future]
                try:
                    raw_results[name] = future.result()
                except Exception as exc:
                    raw_results[name] = failed_tool_result(name, commands[name], run_cwd, exc)
    else:
        for name, command in commands.items():
            try:
                raw_results[name] = run_tool(name, command, run_cwd, args.timeout, args.dry_run)
            except Exception as exc:
                raw_results[name] = failed_tool_result(name, command, run_cwd, exc)

    results = []
    for name in commands:
        result = raw_results[name]
        results.append({key: value for key, value in result.items() if key not in {"stdout", "stderr"}})
        write_response(output_dir, result)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "mode": args.mode,
        "execution": "parallel" if run_parallel else "sequential",
        "requested_execution": args.execution,
        "approval_mode": args.approval_mode,
        "role": args.role,
        "dry_run": args.dry_run,
        "output_dir": str(output_dir),
        "workspace": str(workspace),
        "run_cwd": str(run_cwd),
        "tools": results,
    }
    manifest.update(profile_manifest_metadata(args))
    write_json(output_dir / "manifest.json", manifest)

    print(f"Wrote consultation outputs to {output_dir}")
    for name in commands:
        print(f"- {name}: {output_dir / f'{name}.txt'}")
    return 0


def terminate_process(process: subprocess.Popen, grace_seconds: float = 5.0) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=grace_seconds)


def session_result(
    name: str,
    command: list[str],
    cwd: Path,
    started_at: str,
    stdout_path: Path,
    stderr_path: Path,
) -> dict:
    return {
        "tool": name,
        "command": command,
        "cwd": str(cwd),
        "started_at": started_at,
        "returncode": None,
        "timed_out": False,
        "timeout_kind": None,
        "status": "running",
        "stdout": "",
        "stderr": "",
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "finished_at": None,
    }


def read_text_if_exists(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def finalize_session_result(result: dict, json_tools: set[str]) -> None:
    raw_stdout = read_text_if_exists(Path(result["stdout_path"]))
    raw_stderr = read_text_if_exists(Path(result["stderr_path"]))
    result["stderr"] = raw_stderr
    if result["tool"] in json_tools:
        raw_path = Path(result["stdout_path"])
        raw_jsonl_path = raw_path.with_suffix(".raw.jsonl")
        if raw_path.exists():
            raw_path.replace(raw_jsonl_path)
            result["raw_stdout_path"] = str(raw_jsonl_path)
        session_id, text = parse_opencode_jsonl(raw_stdout)
        if session_id:
            result["tool_session_id"] = session_id
        result["stdout"] = text or raw_stdout
        return
    result["stdout"] = raw_stdout


def run_session_tools(
    commands: dict[str, list[str]],
    run_cwd: Path,
    timeout: int,
    straggler_timeout: int,
    dry_run: bool,
    output_dir: Path,
    json_tools: set[str],
) -> dict[str, dict]:
    if dry_run:
        results = {}
        for name, command in commands.items():
            result = run_tool(name, command, run_cwd, timeout, dry_run=True)
            result["status"] = "dry_run"
            result["timeout_kind"] = None
            results[name] = result
        return results

    results: dict[str, dict] = {}
    processes: dict[str, subprocess.Popen] = {}
    files = []
    starts: dict[str, float] = {}

    for name, command in commands.items():
        stdout_path = output_dir / f"{name}.stdout"
        stderr_path = output_dir / f"{name}.stderr"
        stdout_file = stdout_path.open("w", encoding="utf-8")
        stderr_file = stderr_path.open("w", encoding="utf-8")
        files.extend([stdout_file, stderr_file])
        started_at = now_iso()
        results[name] = session_result(name, command, run_cwd, started_at, stdout_path, stderr_path)
        try:
            process = subprocess.Popen(
                command,
                cwd=str(run_cwd),
                stdout=stdout_file,
                stderr=stderr_file,
                text=True,
            )
        except OSError as exc:
            stderr_file.write(f"Failed to launch {name}: {exc}\n")
            stderr_file.flush()
            results[name].update({
                "returncode": -1,
                "status": "failed",
                "stderr": f"Failed to launch {name}: {exc}",
                "finished_at": now_iso(),
            })
            stdout_file.close()
            stderr_file.close()
            continue
        processes[name] = process
        starts[name] = time.monotonic()

    first_finished_at: Optional[float] = None
    while processes:
        now = time.monotonic()
        for name, process in list(processes.items()):
            result = results[name]
            returncode = process.poll()
            if returncode is not None:
                result.update({
                    "returncode": returncode,
                    "status": "finished" if returncode == 0 else "failed",
                    "finished_at": now_iso(),
                })
                processes.pop(name)
                if first_finished_at is None:
                    first_finished_at = now
                continue

            if now - starts[name] >= timeout:
                terminate_process(process)
                result.update({
                    "returncode": process.returncode if process.returncode is not None else -1,
                    "timed_out": True,
                    "timeout_kind": "hard",
                    "status": "hard_timeout",
                    "finished_at": now_iso(),
                })
                processes.pop(name)
                if first_finished_at is None:
                    first_finished_at = now
                continue

            if first_finished_at is not None and now - first_finished_at >= straggler_timeout:
                terminate_process(process)
                result.update({
                    "returncode": process.returncode if process.returncode is not None else -1,
                    "timed_out": True,
                    "timeout_kind": "straggler",
                    "status": "straggler_timeout",
                    "finished_at": now_iso(),
                })
                processes.pop(name)
        if processes:
            time.sleep(0.2)

    for file_obj in files:
        if not file_obj.closed:
            file_obj.close()

    for result in results.values():
        finalize_session_result(result, json_tools)
    return results


def latest_stopping_suggestion(results: Iterable[dict]) -> Optional[str]:
    result_list = list(results)
    if any(result.get("timed_out") for result in result_list):
        return "tool_timed_out"
    if any(result.get("returncode") not in {0, None} for result in result_list):
        return "tool_failed"
    return None


def turn_status(results: Iterable[dict]) -> str:
    for result in results:
        if result.get("timed_out") or result.get("returncode") not in {0, None}:
            return "degraded"
    return "ok"


def run_session(args: argparse.Namespace, user_prompt: str) -> int:
    workspace = args.workspace.resolve()
    session, session_path, created = make_session(args, workspace)
    if not created:
        inherit_session_args(args, session)
    prompt = consultation_prompt(args.mode, args.role, args.approval_mode, user_prompt)
    session_path.mkdir(parents=True, exist_ok=True)
    turn_number = int(session.get("turn_count", 0)) + 1
    turn_dir = next_turn_dir(session_path, turn_number)
    turn_dir.mkdir(parents=True, exist_ok=False)
    (turn_dir / "prompt.txt").write_text(prompt + "\n", encoding="utf-8")

    isolated_cwd = session_path / "isolated-cwd"
    isolated_cwd.mkdir(exist_ok=True)
    run_cwd = isolated_cwd if args.mode == "advisory" else workspace

    commands, json_tools = build_commands(args, prompt, run_cwd, session=session)
    profile_metadata = profile_manifest_metadata(args)
    session.update({
        "updated_at": now_iso(),
        "status": "running",
        "runner_pid": os.getpid(),
        "runner_started_at": now_iso(),
        "workspace": str(workspace),
        "mode": args.mode,
        "role": args.role,
        "approval_mode": args.approval_mode,
        "execution": args.execution,
        "models": dict(profile_metadata["effective_models"]),
        "profile": profile_metadata["profile"],
        "profile_source": profile_metadata["profile_source"],
        "cost_tier": profile_metadata["cost_tier"],
        "effective_models": dict(profile_metadata["effective_models"]),
        "effective_effort": dict(profile_metadata["effective_effort"]),
        "requested_models": dict(profile_metadata["requested_models"]),
        "requested_effort": dict(profile_metadata["requested_effort"]),
        "effort_support": dict(profile_metadata["effort_support"]),
        "applied_effort": dict(profile_metadata["applied_effort"]),
    })
    write_json(session_path / "session.json", session)

    run_parallel = should_run_parallel(args.execution, args.mode, len(commands))
    if run_parallel:
        raw_results = run_session_tools(
            commands,
            run_cwd,
            args.timeout,
            args.straggler_timeout,
            args.dry_run,
            turn_dir,
            json_tools,
        )
    else:
        raw_results = {}
        for name, command in commands.items():
            raw_results.update(run_session_tools(
                {name: command},
                run_cwd,
                args.timeout,
                args.straggler_timeout,
                args.dry_run,
                turn_dir,
                {name} if name in json_tools else set(),
            ))

    if "opencode" in raw_results and raw_results["opencode"].get("tool_session_id"):
        session.setdefault("tool_session_ids", {})["opencode"] = raw_results["opencode"]["tool_session_id"]

    results = []
    for name in commands:
        result = raw_results[name]
        results.append({key: value for key, value in result.items() if key not in {"stdout", "stderr"}})
        write_response(turn_dir, result)

    stopping_suggestion = latest_stopping_suggestion(raw_results.values())
    current_turn_status = turn_status(raw_results.values())
    session.update({
        "updated_at": now_iso(),
        "status": "waiting_for_user",
        "runner_pid": None,
        "turn_count": turn_number,
        "last_turn_status": current_turn_status,
        "latest_stopping_suggestion": stopping_suggestion,
    })

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "session_id": session["session_id"],
        "session_created": created,
        "turn": turn_number,
        "mode": args.mode,
        "execution": "parallel" if run_parallel else "sequential",
        "requested_execution": args.execution,
        "approval_mode": args.approval_mode,
        "role": args.role,
        "dry_run": args.dry_run,
        "output_dir": str(turn_dir),
        "session_dir": str(session_path),
        "workspace": str(workspace),
        "run_cwd": str(run_cwd),
        "straggler_timeout": args.straggler_timeout,
        "timeout": args.timeout,
        "turn_status": current_turn_status,
        "stopping_suggestion": stopping_suggestion,
        "tools": results,
    }
    manifest.update(profile_metadata)
    write_json(turn_dir / "manifest.json", manifest)
    write_json(session_path / "session.json", session)

    print(f"Wrote AI team session turn to {turn_dir}")
    print(f"- session: {session['session_id']}")
    print(f"- session_dir: {session_path}")
    for name in commands:
        print(f"- {name}: {turn_dir / f'{name}.txt'}")
    if stopping_suggestion:
        print(f"- stopping_suggestion: {stopping_suggestion}")
    return 0


def main() -> int:
    args = parse_args()
    user_prompt = read_prompt(args)
    if args.session is not None:
        return run_session(args, user_prompt)
    prompt = consultation_prompt(args.mode, args.role, args.approval_mode, user_prompt)
    return run_one_shot(args, prompt)


if __name__ == "__main__":
    sys.exit(main())
