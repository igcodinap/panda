#!/usr/bin/env python3
"""Run consultations against local Claude Code and OpenCode CLIs."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
from typing import Iterable


ROLE_GUIDANCE = {
    "brainstorm": "Propose distinct implementation approaches with tradeoffs.",
    "implementation-review": "Critique the proposed implementation path for risks, simpler alternatives, and verification.",
    "debugging": "Suggest likely root causes, discriminating checks, and the smallest useful diagnostic step.",
    "code-review": "Review for behavioral bugs, edge cases, missing tests, and maintainability risks.",
    "test-plan": "Propose focused tests and manual verification for the described change.",
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
    parser.add_argument("--tool", choices=["claude", "opencode", "both"], default="both")
    parser.add_argument("--mode", choices=sorted(MODE_GUIDANCE), default="explore")
    parser.add_argument("--role", choices=sorted(ROLE_GUIDANCE), default="brainstorm")
    parser.add_argument("--prompt", help="Prompt text to send to the tools.")
    parser.add_argument("--prompt-file", type=Path, help="File containing prompt text.")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="Workspace to run explore/patch consultations in.")
    parser.add_argument("--output-dir", type=Path, help="Directory for responses and manifest.")
    parser.add_argument("--timeout", type=int, default=600, help="Timeout per tool in seconds.")
    parser.add_argument("--claude-bin", default=os.environ.get("CLAUDE_BIN", "claude"))
    parser.add_argument("--opencode-bin", default=os.environ.get("OPENCODE_BIN", "opencode"))
    parser.add_argument("--claude-model", help="Optional Claude Code model override.")
    parser.add_argument("--opencode-model", help="Optional OpenCode model override in provider/model format.")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without running tools.")
    return parser.parse_args()


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


def consultation_prompt(mode: str, role: str, user_prompt: str) -> str:
    return f"""You are advising Codex as an independent collaborator.

Mode: {mode}
Role: {role}
Role guidance: {ROLE_GUIDANCE[role]}

{MODE_GUIDANCE[mode]}

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
    }
    if dry_run:
        result["stdout"] = quote_cmd(command)
        result["returncode"] = 0
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
        return result
    result["returncode"] = completed.returncode
    result["stdout"] = completed.stdout
    result["stderr"] = completed.stderr
    return result


def write_response(output_dir: Path, result: dict) -> None:
    body = result["stdout"].strip()
    if result["stderr"].strip():
        body += f"\n\n[stderr]\n{result['stderr'].strip()}\n"
    if result["timed_out"]:
        body += "\n\n[status]\nTimed out.\n"
    elif result["returncode"] != 0:
        body += f"\n\n[status]\nExited with code {result['returncode']}.\n"
    (output_dir / f"{result['tool']}.txt").write_text(body.strip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    prompt = consultation_prompt(args.mode, args.role, read_prompt(args))
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = args.output_dir or Path(tempfile.gettempdir()) / "ai-team-consults" / stamp
    output_dir.mkdir(parents=True, exist_ok=True)
    isolated_cwd = output_dir / "isolated-cwd"
    isolated_cwd.mkdir(exist_ok=True)
    workspace = args.workspace.resolve()
    run_cwd = isolated_cwd if args.mode == "advisory" else workspace

    requested = ["claude", "opencode"] if args.tool == "both" else [args.tool]
    commands: dict[str, list[str]] = {}

    if "claude" in requested:
        claude_bin = shutil.which(args.claude_bin) or args.claude_bin
        claude_command = [
            claude_bin,
            "-p",
            "--no-session-persistence",
            "--output-format",
            "text",
        ]
        if args.claude_model:
            claude_command.extend(["--model", args.claude_model])
        if args.mode == "advisory":
            claude_command.extend(["--permission-mode", "plan", "--tools", ""])
        elif args.mode == "explore":
            claude_command.extend([
                "--permission-mode",
                "default",
                "--allowedTools",
                "Read,Grep,Glob,LS,Bash,WebFetch,WebSearch",
            ])
        else:
            claude_command.extend(["--permission-mode", "default"])
        claude_command.append(prompt)
        commands["claude"] = claude_command

    if "opencode" in requested:
        opencode_bin = shutil.which(args.opencode_bin) or args.opencode_bin
        commands["opencode"] = [
            opencode_bin,
            "run",
            "--pure",
            "--title",
            f"ai-team-{args.mode}-{args.role}",
            "--dir",
            str(run_cwd),
        ]
        if args.opencode_model:
            commands["opencode"].extend(["--model", args.opencode_model])
        commands["opencode"].append(prompt)

    results = []
    for name, command in commands.items():
        result = run_tool(name, command, run_cwd, args.timeout, args.dry_run)
        results.append({key: value for key, value in result.items() if key not in {"stdout", "stderr"}})
        write_response(output_dir, result)

    manifest = {
        "mode": args.mode,
        "role": args.role,
        "dry_run": args.dry_run,
        "output_dir": str(output_dir),
        "workspace": str(workspace),
        "run_cwd": str(run_cwd),
        "requested_models": {
            "claude": args.claude_model,
            "opencode": args.opencode_model,
        },
        "tools": results,
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(f"Wrote consultation outputs to {output_dir}")
    for name in commands:
        print(f"- {name}: {output_dir / f'{name}.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
