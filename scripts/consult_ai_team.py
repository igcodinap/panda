#!/usr/bin/env python3
"""Run consultations against local Claude Code, OpenCode, and Codex CLIs."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Iterable, Optional
import uuid

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback.
    fcntl = None

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from panda_v2 import artifacts as panda_v2_artifacts
from panda_v2.prompts import protocol_v2_return_addendum


ROLE_GUIDANCE = {
    "brainstorm": "Propose distinct implementation approaches with tradeoffs.",
    "research": "Research repo/docs/API behavior and report evidence, tradeoffs, and uncertainties.",
    "planning": "Turn goals into a concrete implementation plan with risks, sequencing, and validation.",
    "implementation-review": "Critique the proposed implementation path for risks, simpler alternatives, and verification.",
    "debugging": "Suggest likely root causes, discriminating checks, and the smallest useful diagnostic step.",
    "code-review": "Review for behavioral bugs, edge cases, missing tests, and maintainability risks.",
    "contract-falsifier": "Audit concrete contract claims once; report contradictions, unverifiable claims, and not-found claims without debate.",
    "test-plan": "Propose focused tests and manual verification for the described change.",
}

DEFAULT_OPENCODE_MODEL = "opencode-go/glm-5.1"
DEFAULT_QWEN_MODEL = "opencode-go/qwen3.6-plus"
DEFAULT_CODEX_MODEL = "gpt-5.5"
DEFAULT_CODEX_EFFORT = "medium"
DEFAULT_APPROVAL_MODE = "unsupervised"
CODEX_REVIEWER_EXPORT_MESSAGE = (
    "Codex reviewer execution requires explicit export approval because it can send the "
    "Panda prompt, repository context, uncommitted diff details, and test output to the "
    "Codex backend. Pass --privacy-mode advisory-summary for summary-only review, "
    "--privacy-mode full-context for approved repository-context review, "
    "--allow-codex-reviewer, or set PANDA_ALLOW_CODEX_REVIEWER=1 only when the "
    "selected context is approved for that export."
)
PRIVATE_CONTEXT_EXPORT_MESSAGE = (
    "Full-context Panda code review can export the prompt, repository files, uncommitted "
    "diff details, and test output to external collaborator CLIs. For private changes, "
    "prefer --privacy-mode advisory-summary with --mode advisory and a Codex-prepared "
    "summary that excludes raw code, diffs, secrets, and logs. Use --privacy-mode "
    "full-context or set PANDA_ALLOW_PRIVATE_CONTEXT_EXPORT=1 only when this workspace "
    "is approved for external code review."
)
EXECUTION_CHOICES = ("auto", "parallel", "sequential")
APPROVAL_MODE_CHOICES = ("unsupervised", "supervised")
PRIVACY_MODE_CHOICES = ("normal", "advisory-summary", "full-context")
CLAUDE_EFFORT_CHOICES = ("low", "medium", "high", "xhigh", "max")
CODEX_EFFORT_CHOICES = ("low", "medium", "high", "xhigh")
LEGACY_ALL_TOOLS = ("claude", "opencode", "qwen")
AUTO_TOOL_ORDER = ("claude", "opencode", "qwen", "codex")
TOOL_CHOICES = ("claude", "opencode", "qwen", "codex", "all", "auto")
PROTOCOL_CHOICES = ("v2",)
CLAUDE_AUTH_FAILURE_RE = re.compile(r"\bnot logged in\b\W+please run /login\b", re.IGNORECASE)
PREFERENCES_SCHEMA_VERSION = 1
PREFERENCE_FIELDS = (
    "tool",
    "profile",
    "claude_model",
    "claude_effort",
    "opencode_model",
    "qwen_model",
    "codex_model",
    "codex_effort",
)
PREFERENCE_META_FIELDS = ("schema_version", "created_at", "updated_at")
PREFERENCE_MODEL_FIELDS = ("claude_model", "opencode_model", "qwen_model", "codex_model")
PREFERENCE_TARGET_FIELDS = {
    "claude_model": "claude",
    "claude_effort": "claude",
    "opencode_model": "opencode",
    "qwen_model": "qwen",
    "codex_model": "codex",
    "codex_effort": "codex",
}
PREFERENCE_MAX_MODEL_CHARS = 256
PATCH_MODE = "patch"
PATCH_MODE_DISABLED_MESSAGE = (
    "Patch mode is disabled; use --mode advisory or --mode explore. Codex is the only editor."
)
SUMMARY_MAX_CHARS = 8000
EVIDENCE_MAX_CHARS = 50000
SESSION_MEMORY_MAX_CHARS = 2000
PROMPT_WARN_CHARS = 50000
OPENCODE_BACKED_TOOLS = {"opencode", "qwen"}
NULL_USAGE = {
    "input_tokens": None,
    "output_tokens": None,
    "cache_read_tokens": None,
    "cost_usd": None,
}
MODEL_PROFILES = {
    "fast": {
        "claude_model": "sonnet",
        "claude_effort": "medium",
        "opencode_model": DEFAULT_OPENCODE_MODEL,
        "qwen_model": DEFAULT_QWEN_MODEL,
        "codex_model": DEFAULT_CODEX_MODEL,
        "codex_effort": DEFAULT_CODEX_EFFORT,
        "cost_tier": "low",
    },
    "balanced": {
        "claude_model": "sonnet",
        "claude_effort": "high",
        "opencode_model": DEFAULT_OPENCODE_MODEL,
        "qwen_model": DEFAULT_QWEN_MODEL,
        "codex_model": DEFAULT_CODEX_MODEL,
        "codex_effort": DEFAULT_CODEX_EFFORT,
        "cost_tier": "medium",
    },
    "deep": {
        "claude_model": "opus",
        "claude_effort": "max",
        "opencode_model": DEFAULT_OPENCODE_MODEL,
        "qwen_model": DEFAULT_QWEN_MODEL,
        "codex_model": DEFAULT_CODEX_MODEL,
        "codex_effort": DEFAULT_CODEX_EFFORT,
        "cost_tier": "high",
    },
}
PREFERENCE_FIELD_CHOICES = {
    "tool": TOOL_CHOICES,
    "profile": tuple(sorted(MODEL_PROFILES)),
    "claude_effort": CLAUDE_EFFORT_CHOICES,
    "codex_effort": CODEX_EFFORT_CHOICES,
}
ROLE_DEFAULT_PROFILES = {
    "brainstorm": "balanced",
    "research": "deep",
    "planning": "deep",
    "implementation-review": "deep",
    "debugging": "balanced",
    "code-review": "balanced",
    "contract-falsifier": "fast",
    "test-plan": "fast",
}
HARD_FALLBACK_PROFILE = "balanced"
SCHEMA_VERSION = 1
EXPORT_MANIFEST_SCHEMA_VERSION = 1
EXPORT_MANIFEST_NAME = "panda_export.v1.json"
DEFAULT_STRAGGLER_TIMEOUT = 120
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
CLAUDE_EFFORT_SUPPORT_CACHE: dict[str, bool] = {}
EXPLICIT_ARG_FLAGS = {
    "--tool": "tool",
    "--mode": "mode",
    "--execution": "execution",
    "--approval-mode": "approval_mode",
    "--role": "role",
    "--protocol": "protocol",
    "--privacy-mode": "privacy_mode",
    "--profile": "profile",
    "--claude-model": "claude_model",
    "--claude-effort": "claude_effort",
    "--opencode-model": "opencode_model",
    "--qwen-model": "qwen_model",
    "--codex-model": "codex_model",
    "--codex-effort": "codex_effort",
    "--agent": "agents",
}
AGENT_BACKEND_CHOICES = ("claude", "opencode", "codex")
AGENT_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")

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
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    explicit_flags = collect_explicit_flags(sys.argv[1:])
    parser.add_argument(
        "--tool",
        choices=TOOL_CHOICES,
        default="codex",
        help=(
            "Collaborator core to run. Defaults to Codex only. "
            "'all' runs the legacy Claude+GLM+Qwen team; 'auto' runs available cores with Codex fallback."
        ),
    )
    parser.add_argument("--mode", default="explore", metavar="{advisory,explore}")
    parser.add_argument(
        "--execution",
        choices=EXECUTION_CHOICES,
        default=os.environ.get("AI_TEAM_EXECUTION", "auto"),
        help="Run multiple tools in parallel, sequentially, or auto mode. Auto parallelizes advisory/explore.",
    )
    parser.add_argument(
        "--approval-mode",
        choices=APPROVAL_MODE_CHOICES,
        default=os.environ.get("AI_TEAM_APPROVAL_MODE", DEFAULT_APPROVAL_MODE),
        help="Whether Claude Code/OpenCode should auto-approve their own tool prompts. Defaults to unsupervised.",
    )
    parser.add_argument("--role", choices=sorted(ROLE_GUIDANCE), default="brainstorm")
    parser.add_argument(
        "--protocol",
        choices=PROTOCOL_CHOICES,
        default="v2",
        help="Artifact/prompt protocol. V2 is the formal Panda flow and writes contract sidecars.",
    )
    parser.add_argument(
        "--privacy-mode",
        choices=PRIVACY_MODE_CHOICES,
        default="normal",
        help=(
            "Control how much private workspace context Panda may expose. "
            "'advisory-summary' requires --mode advisory and reviews only the supplied summary "
            "from an isolated directory. 'full-context' explicitly allows external agents to "
            "inspect repository context."
        ),
    )
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
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="Workspace to run explore consultations in.")
    parser.add_argument("--output-dir", type=Path, help="Directory for responses and manifest.")
    parser.add_argument(
        "--prepare-export-manifest",
        action="store_true",
        help=(
            f"Write {EXPORT_MANIFEST_NAME} to --output-dir and exit without launching reviewers. "
            "Requires --output-dir and is intended for pre-approval workflows."
        ),
    )
    parser.add_argument(
        "--export-manifest",
        type=Path,
        help=(
            f"Validate a precomputed {EXPORT_MANIFEST_NAME} before launching reviewers, "
            "then copy it into the run output for audit."
        ),
    )
    parser.add_argument("--timeout", type=int, default=600, help="Timeout per tool in seconds.")
    parser.add_argument(
        "--straggler-timeout",
        type=int,
        default=DEFAULT_STRAGGLER_TIMEOUT,
        help="Session mode: seconds to wait for remaining tools after one collaborator finishes.",
    )
    parser.add_argument("--claude-bin", default=os.environ.get("CLAUDE_BIN", "claude"))
    parser.add_argument("--opencode-bin", default=os.environ.get("OPENCODE_BIN", "opencode"))
    parser.add_argument("--codex-bin", default=os.environ.get("CODEX_BIN", "codex"))
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
    parser.add_argument(
        "--qwen-model",
        help=f"OpenCode Qwen model in provider/model format. Defaults through profile to {DEFAULT_QWEN_MODEL}.",
    )
    parser.add_argument(
        "--codex-model",
        help=f"Codex reviewer model. Defaults through profile/env to {DEFAULT_CODEX_MODEL}.",
    )
    parser.add_argument(
        "--codex-effort",
        choices=CODEX_EFFORT_CHOICES,
        help=f"Codex reviewer reasoning effort. Defaults through profile/env to {DEFAULT_CODEX_EFFORT}.",
    )
    parser.add_argument(
        "--allow-codex-reviewer",
        action="store_true",
        help=(
            "Allow live Codex reviewer execution. This can export prompt, repository context, "
            "diff details, and test output to the Codex backend."
        ),
    )
    parser.add_argument(
        "--agent",
        action="append",
        default=[],
        metavar="NAME=BACKEND:MODEL[@EFFORT]",
        help=(
            "Configure one Panda agent. BACKEND is claude, opencode, or codex. "
            "Repeat to run several agents, e.g. --agent kimi=opencode:opencode-go/kimi-k2.6."
        ),
    )
    parser.add_argument(
        "--no-session-memory",
        action="store_true",
        help="Session mode: do not inject the previous turn's compact summary into the next prompt.",
    )
    parser.add_argument(
        "--serialize-opencode",
        action="store_true",
        help="Run OpenCode-backed tools one at a time as a diagnostic fallback.",
    )
    parser.add_argument(
        "--save-preferences",
        action="store_true",
        help="Save explicitly provided Panda tool/profile/model/effort flags as user defaults, then exit.",
    )
    parser.add_argument(
        "--show-preferences",
        action="store_true",
        help="Print the current Panda preferences file path and contents, then exit.",
    )
    parser.add_argument(
        "--reset-preferences",
        action="store_true",
        help="Remove the current Panda preferences file, then exit.",
    )
    parser.add_argument(
        "--ignore-preferences",
        action="store_true",
        help="Do not load saved Panda preferences for this invocation.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print commands without running tools.")
    args = parser.parse_args()
    preference_command_count = sum(
        int(flag)
        for flag in (args.save_preferences, args.show_preferences, args.reset_preferences)
    )
    if preference_command_count > 1:
        parser.error("Use only one of --save-preferences, --show-preferences, or --reset-preferences.")
    if len(args.agent) > 1 and not args.save_preferences:
        parser.error("Use one --agent for one-off Panda runs; use --save-preferences to store a multi-agent behavior profile.")
    if args.agent and "tool" in explicit_flags:
        parser.error("Use either --agent or --tool, not both.")
    validate_choice(parser, "AI_TEAM_EXECUTION/--execution", args.execution, EXECUTION_CHOICES)
    validate_choice(parser, "AI_TEAM_APPROVAL_MODE/--approval-mode", args.approval_mode, APPROVAL_MODE_CHOICES)
    validate_choice(parser, "--privacy-mode", args.privacy_mode, PRIVACY_MODE_CHOICES)
    env_codex_effort = os.environ.get("CODEX_REASONING_EFFORT") or os.environ.get("CODEX_EFFORT")
    if args.codex_effort is None and env_codex_effort:
        validate_choice(parser, "CODEX_REASONING_EFFORT/CODEX_EFFORT", env_codex_effort, CODEX_EFFORT_CHOICES)
    if args.mode == PATCH_MODE:
        parser.error(PATCH_MODE_DISABLED_MESSAGE)
    validate_choice(parser, "--mode", args.mode, MODE_GUIDANCE)
    if args.privacy_mode == "advisory-summary" and args.mode != "advisory":
        parser.error("--privacy-mode advisory-summary requires --mode advisory.")
    if args.straggler_timeout < 1:
        parser.error("--straggler-timeout must be at least 1 second.")
    if args.timeout < 1:
        parser.error("--timeout must be at least 1 second.")
    if args.prepare_export_manifest and args.output_dir is None:
        parser.error("--prepare-export-manifest requires --output-dir.")
    if args.prepare_export_manifest and args.session is not None:
        parser.error("--prepare-export-manifest is supported for one-shot runs only.")
    args.explicit_flags = explicit_flags
    args.preference_sourced_fields = set()
    args.preferences_metadata = base_preferences_metadata(args)
    args.configured_agents = parse_agent_specs(args.agent)
    if not preference_management_requested(args):
        apply_preferences(args)
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


def default_preferences_path() -> Path:
    explicit_path = os.environ.get("PANDA_PREFERENCES_FILE")
    if explicit_path:
        return Path(explicit_path).expanduser()
    xdg_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_home:
        return Path(xdg_home).expanduser() / "panda" / "preferences.json"
    return Path.home() / ".config" / "panda" / "preferences.json"


def preference_management_requested(args: argparse.Namespace) -> bool:
    return bool(args.save_preferences or args.show_preferences or args.reset_preferences)


def preferences_disabled(args: argparse.Namespace) -> tuple[bool, Optional[str]]:
    if getattr(args, "ignore_preferences", False):
        return True, "ignore_preferences"
    if env_flag_enabled("PANDA_NO_PREFERENCES"):
        return True, "PANDA_NO_PREFERENCES"
    return False, None


def base_preferences_metadata(args: argparse.Namespace) -> dict:
    disabled, reason = preferences_disabled(args)
    metadata = {
        "enabled": not disabled,
        "path": str(default_preferences_path()),
        "schema_version": None,
        "loaded": False,
        "applied_fields": [],
        "ignored_fields": {},
        "warnings": [],
    }
    if reason:
        metadata["warnings"].append(reason)
    return metadata


def add_preference_warning(args: argparse.Namespace, warning: str) -> None:
    getattr(args, "preferences_metadata", {}).setdefault("warnings", []).append(warning)


def mark_preference_ignored(args: argparse.Namespace, field: str, reason: str) -> None:
    metadata = getattr(args, "preferences_metadata", None)
    if not metadata:
        return
    metadata.setdefault("ignored_fields", {})[field] = reason
    sourced = getattr(args, "preference_sourced_fields", set())
    if isinstance(sourced, set):
        sourced.discard(field)


def validate_model_preference(field: str, value: object) -> str:
    if not isinstance(value, str):
        raise SystemExit(f"Preference {field} must be a string.")
    if not value.strip():
        raise SystemExit(f"Preference {field} must not be empty.")
    if len(value) > PREFERENCE_MAX_MODEL_CHARS:
        raise SystemExit(
            f"Preference {field} must be {PREFERENCE_MAX_MODEL_CHARS} characters or fewer."
        )
    if value.startswith("-"):
        raise SystemExit(f"Preference {field} must not start with '-'.")
    if any(ch.isspace() or ch in ";|&$`" for ch in value):
        raise SystemExit(f"Preference {field} contains unsupported characters.")
    return value


def validate_agent_name(name: object) -> str:
    if not isinstance(name, str) or not AGENT_NAME_PATTERN.match(name):
        raise SystemExit(
            "Agent names must start with a letter and contain only letters, numbers, dots, dashes, or underscores."
        )
    return name


def validate_agent_spec(agent: object, index: int = 0) -> dict:
    if not isinstance(agent, dict):
        raise SystemExit(f"Preference profile agent #{index + 1} must be a JSON object.")
    name = validate_agent_name(agent.get("name"))
    backend = agent.get("backend")
    if backend not in AGENT_BACKEND_CHOICES:
        raise SystemExit(f"Agent {name!r} backend must be one of: {', '.join(AGENT_BACKEND_CHOICES)}")
    model = validate_model_preference(f"agent {name} model", agent.get("model"))
    normalized = {
        "name": name,
        "backend": backend,
        "model": model,
    }
    effort = agent.get("effort")
    if effort is not None:
        if not isinstance(effort, str):
            raise SystemExit(f"Agent {name!r} effort must be a string.")
        choices = CLAUDE_EFFORT_CHOICES if backend == "claude" else CODEX_EFFORT_CHOICES
        if backend == "opencode":
            raise SystemExit(f"Agent {name!r} uses OpenCode, which does not support an effort setting.")
        if effort not in choices:
            raise SystemExit(f"Agent {name!r} effort must be one of: {', '.join(choices)}")
        normalized["effort"] = effort
    return normalized


def parse_agent_spec(raw: str) -> dict:
    if "=" not in raw or ":" not in raw:
        raise SystemExit("Agent specs must use NAME=BACKEND:MODEL[@EFFORT].")
    name, backend_and_model = raw.split("=", 1)
    backend, model_and_effort = backend_and_model.split(":", 1)
    effort = None
    model = model_and_effort
    if "@" in model_and_effort:
        model, effort = model_and_effort.rsplit("@", 1)
    agent = {
        "name": name,
        "backend": backend,
        "model": model,
    }
    if effort:
        agent["effort"] = effort
    return validate_agent_spec(agent)


def parse_agent_specs(raw_specs: list[str]) -> list[dict]:
    agents = [parse_agent_spec(raw_spec) for raw_spec in raw_specs]
    names = [agent["name"] for agent in agents]
    if len(names) != len(set(names)):
        raise SystemExit("Agent names must be unique.")
    return agents


def profile_agents_from_preferences(preferences: dict) -> list[dict]:
    profile = preferences.get("profile")
    if not isinstance(profile, dict):
        return []
    agents = profile.get("agents")
    if agents is None:
        return []
    if not isinstance(agents, list) or not agents:
        raise SystemExit("Preference profile.agents must be a non-empty list.")
    normalized_agents = [
        validate_agent_spec(agent, index)
        for index, agent in enumerate(agents)
    ]
    names = [agent["name"] for agent in normalized_agents]
    if len(names) != len(set(names)):
        raise SystemExit("Preference profile agent names must be unique.")
    return normalized_agents


def agent_name_from_backend_model(backend: str, model: str, fallback: str) -> str:
    if backend != "opencode":
        return fallback
    normalized = model.lower()
    if "kimi" in normalized:
        return "kimi"
    if "glm" in normalized:
        return "glm"
    if "qwen" in normalized:
        return "qwen"
    return fallback


def legacy_agents_from_args(args: argparse.Namespace) -> list[dict]:
    resolution = resolve_profile(args)
    agents = []
    for tool in requested_tools(args.tool, args):
        if tool == "claude":
            agent = {
                "name": "claude",
                "backend": "claude",
                "model": resolution["effective_models"]["claude"],
            }
            if resolution["effective_effort"]["claude"]:
                agent["effort"] = resolution["effective_effort"]["claude"]
            agents.append(agent)
        elif tool == "opencode":
            model = resolution["effective_models"]["opencode"]
            agents.append({
                "name": agent_name_from_backend_model("opencode", model, "opencode"),
                "backend": "opencode",
                "model": model,
            })
        elif tool == "qwen":
            model = resolution["effective_models"]["qwen"]
            agents.append({
                "name": agent_name_from_backend_model("opencode", model, "qwen"),
                "backend": "opencode",
                "model": model,
            })
        elif tool == "codex":
            agent = {
                "name": "codex",
                "backend": "codex",
                "model": resolution["effective_models"]["codex"],
            }
            if resolution["effective_effort"]["codex"]:
                agent["effort"] = resolution["effective_effort"]["codex"]
            agents.append(agent)
    seen = {}
    for agent in agents:
        name = agent["name"]
        count = seen.get(name, 0)
        seen[name] = count + 1
        if count:
            agent["name"] = f"{name}-{count + 1}"
    return agents


def configured_agents_for_preferences(args: argparse.Namespace) -> list[dict]:
    resolution = resolve_profile(args)
    agents = []
    for agent in getattr(args, "configured_agents", []):
        saved_agent = dict(agent)
        if "effort" not in saved_agent:
            if saved_agent["backend"] == "claude" and resolution["effective_effort"]["claude"]:
                saved_agent["effort"] = resolution["effective_effort"]["claude"]
            if saved_agent["backend"] == "codex" and resolution["effective_effort"]["codex"]:
                saved_agent["effort"] = resolution["effective_effort"]["codex"]
        agents.append(saved_agent)
    return agents


def selected_preference_targets(args: argparse.Namespace) -> set[str]:
    configured_agents = getattr(args, "configured_agents", [])
    if configured_agents:
        targets = {agent["name"] for agent in configured_agents}
        targets.update(agent["backend"] for agent in configured_agents)
        return targets
    return set(requested_tools(args.tool, args))


def validate_save_preference_targets(args: argparse.Namespace, explicit_fields: list[str]) -> None:
    explicit_target_fields = [
        field
        for field in explicit_fields
        if field in PREFERENCE_TARGET_FIELDS
    ]
    if not explicit_target_fields:
        return
    selected_targets = selected_preference_targets(args)
    ignored_fields = [
        field
        for field in explicit_target_fields
        if PREFERENCE_TARGET_FIELDS[field] not in selected_targets
    ]
    if not ignored_fields:
        return
    ignored_flags = ", ".join(f"--{field.replace('_', '-')}" for field in ignored_fields)
    selected_text = ", ".join(sorted(selected_targets)) or "none"
    raise SystemExit(
        f"--save-preferences received {ignored_flags}, but the current behavior selects "
        f"{selected_text}. Add a matching --tool/--agent selection, or encode the model directly "
        "with --agent NAME=BACKEND:MODEL."
    )


def validate_preferences_payload(payload: object, path: Path) -> tuple[dict, list[str]]:
    if not isinstance(payload, dict):
        raise SystemExit(f"Preferences file must contain a JSON object: {path}")

    schema_version = payload.get("schema_version")
    if schema_version != PREFERENCES_SCHEMA_VERSION:
        raise SystemExit(
            f"Unsupported Panda preferences schema at {path}: {schema_version!r}. "
            f"Expected {PREFERENCES_SCHEMA_VERSION}."
        )

    warnings = []
    allowed_fields = set(PREFERENCE_FIELDS) | set(PREFERENCE_META_FIELDS)
    preferences = {"schema_version": PREFERENCES_SCHEMA_VERSION}
    for field, value in payload.items():
        if field not in allowed_fields:
            warnings.append(f"ignored_unknown_preference_field:{field}")
            continue
        if value is None:
            continue
        if field == "schema_version":
            continue
        if field in {"created_at", "updated_at"}:
            if not isinstance(value, str):
                warnings.append(f"ignored_invalid_preference_timestamp:{field}")
                continue
            preferences[field] = value
            continue
        if field == "profile" and isinstance(value, dict):
            agents = profile_agents_from_preferences({"profile": value})
            preferences["profile"] = {"agents": agents}
            continue
        if field in PREFERENCE_MODEL_FIELDS:
            preferences[field] = validate_model_preference(field, value)
            continue
        if not isinstance(value, str):
            raise SystemExit(f"Preference {field} must be a string.")
        choices = PREFERENCE_FIELD_CHOICES.get(field)
        if choices and value not in choices:
            raise SystemExit(f"Preference {field} must be one of: {', '.join(choices)}")
        preferences[field] = value

    return preferences, warnings


def read_preferences(path: Path) -> tuple[Optional[dict], list[str]]:
    if not path.exists():
        return None, []
    try:
        raw_preferences = read_json(path)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Malformed Panda preferences at {path}: {exc}") from exc
    return validate_preferences_payload(raw_preferences, path)


def apply_preferences(args: argparse.Namespace) -> None:
    disabled, _ = preferences_disabled(args)
    if disabled:
        args.preferences_metadata = base_preferences_metadata(args)
        return

    path = default_preferences_path()
    args.preferences_metadata = base_preferences_metadata(args)
    preferences, warnings = read_preferences(path)
    args.preferences_metadata["warnings"].extend(warnings)
    if not preferences:
        return

    args.preferences_metadata["loaded"] = True
    args.preferences_metadata["schema_version"] = preferences["schema_version"]
    explicit_flags = getattr(args, "explicit_flags", set())
    sourced_fields = set()
    profile_agents = profile_agents_from_preferences(preferences)
    if profile_agents:
        if "tool" in explicit_flags or "agents" in explicit_flags:
            args.preferences_metadata.setdefault("ignored_fields", {})["profile.agents"] = "explicit_agent_selection"
        else:
            args.configured_agents = profile_agents
            sourced_fields.add("agents")
            args.preferences_metadata.setdefault("applied_fields", []).append("profile.agents")
    for field in PREFERENCE_FIELDS:
        if field not in preferences:
            continue
        if field == "profile" and isinstance(preferences[field], dict):
            continue
        if field in explicit_flags:
            args.preferences_metadata.setdefault("ignored_fields", {})[field] = "explicit_cli_flag"
            continue
        setattr(args, field, preferences[field])
        sourced_fields.add(field)
        args.preferences_metadata.setdefault("applied_fields", []).append(field)

    args.preference_sourced_fields = sourced_fields
    validate_preference_tool_availability(args)


def validate_preference_tool_availability(args: argparse.Namespace) -> None:
    if "agents" in getattr(args, "preference_sourced_fields", set()):
        validate_agents_available(args.configured_agents, args, "Saved Panda preference")
        return
    if "tool" not in getattr(args, "preference_sourced_fields", set()):
        return
    if args.tool == "auto":
        return
    unavailable = [
        tool
        for tool in requested_tools(args.tool, args=None)
        if not tool_is_available(tool, args)
    ]
    if unavailable:
        unavailable_text = ", ".join(unavailable)
        raise SystemExit(
            f"Saved Panda preference requested tool={args.tool!r}, but {unavailable_text} "
            "is unavailable. Use --tool to override, --ignore-preferences to bypass, "
            "or --reset-preferences to clear saved preferences."
        )


def validate_agents_available(agents: list[dict], args: argparse.Namespace, source: str) -> None:
    for agent in agents:
        backend = agent["backend"]
        if backend == "claude":
            available = tool_is_available("claude", args)
        elif backend == "opencode":
            available = tool_is_available("opencode", args)
        elif backend == "codex":
            available = tool_is_available("codex", args)
        else:
            available = False
        if not available:
            raise SystemExit(
                f"{source} requested agent {agent['name']!r} using {backend}, but {backend} is unavailable. "
                "Use --agent or --tool to override, --ignore-preferences to bypass, "
                "or --reset-preferences to clear saved preferences."
            )


def preference_manifest_metadata(args: argparse.Namespace) -> dict:
    metadata = getattr(args, "preferences_metadata", None)
    if not metadata:
        return base_preferences_metadata(args)
    return {
        "enabled": bool(metadata.get("enabled")),
        "path": metadata.get("path"),
        "schema_version": metadata.get("schema_version"),
        "loaded": bool(metadata.get("loaded")),
        "applied_fields": list(metadata.get("applied_fields", [])),
        "ignored_fields": dict(metadata.get("ignored_fields", {})),
        "warnings": list(metadata.get("warnings", [])),
    }


def write_preferences_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as file_obj:
            file_obj.write(json.dumps(data, indent=2) + "\n")
            file_obj.flush()
            os.fsync(file_obj.fileno())
        os.replace(tmp_path, path)
        os.chmod(path, 0o600)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def smoke_preferences_payload(args: argparse.Namespace, preferences: dict) -> dict:
    agents = profile_agents_from_preferences(preferences)
    if not agents:
        raise SystemExit("Saved Panda preferences smoke test failed: profile.agents is missing.")

    smoke_args = argparse.Namespace(**vars(args))
    smoke_args.configured_agents = [dict(agent) for agent in agents]
    smoke_args.tool = "codex"
    smoke_args.dry_run = True
    smoke_args.session = None
    smoke_args.output_dir = None
    smoke_args.prompt = None
    smoke_args.prompt_file = None
    smoke_args.preference_sourced_fields = {"agents"}
    smoke_args.preferences_metadata = {
        "enabled": True,
        "path": str(default_preferences_path()),
        "schema_version": preferences["schema_version"],
        "loaded": True,
        "applied_fields": ["profile.agents"],
        "ignored_fields": {},
        "warnings": [],
    }
    smoke_args.profile_resolution = resolve_profile(smoke_args)
    validate_preference_tool_availability(smoke_args)
    prompt = consultation_prompt(
        smoke_args.mode,
        smoke_args.role,
        smoke_args.approval_mode,
        "Panda saved-preferences smoke test.",
        smoke_args.protocol,
        smoke_args.privacy_mode,
    )
    commands, _ = build_commands(smoke_args, prompt, smoke_args.workspace.resolve())
    if not commands:
        raise SystemExit("Saved Panda preferences smoke test failed: no commands were built.")
    return {
        "requested_tools": requested_tools(smoke_args.tool, smoke_args),
        "active_models": active_models(smoke_args),
        "applied_effort": dict(get_profile_resolution(smoke_args)["applied_effort"]),
        "commands": list(commands),
    }


def save_preferences(args: argparse.Namespace) -> int:
    explicit_fields = [
        field
        for field in PREFERENCE_FIELDS
        if field in getattr(args, "explicit_flags", set())
    ]
    explicit_agents = bool(getattr(args, "configured_agents", []))
    if not explicit_fields and not explicit_agents:
        raise SystemExit(
            "--save-preferences requires at least one of: "
            "--agent, --tool, --profile, --claude-model, --claude-effort, "
            "--opencode-model, --qwen-model, --codex-model, or --codex-effort."
        )
    validate_save_preference_targets(args, explicit_fields)

    path = default_preferences_path()
    existing: dict = {}
    if not getattr(args, "ignore_preferences", False) and not env_flag_enabled("PANDA_NO_PREFERENCES"):
        loaded, _ = read_preferences(path)
        if loaded:
            existing = loaded
    now = now_iso()
    payload = dict(existing)
    payload["schema_version"] = PREFERENCES_SCHEMA_VERSION
    payload.setdefault("created_at", now)
    payload["updated_at"] = now
    if explicit_agents:
        payload = {
            "schema_version": PREFERENCES_SCHEMA_VERSION,
            "created_at": payload.get("created_at", now),
            "updated_at": now,
            "profile": {"agents": configured_agents_for_preferences(args)},
        }
    elif explicit_fields:
        payload = {
            "schema_version": PREFERENCES_SCHEMA_VERSION,
            "created_at": payload.get("created_at", now),
            "updated_at": now,
            "profile": {"agents": legacy_agents_from_args(args)},
        }

    validated, warnings = validate_preferences_payload(payload, path)
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    smoke_preferences_payload(args, validated)
    write_preferences_json(path, validated)
    roundtrip_preferences, roundtrip_warnings = read_preferences(path)
    for warning in roundtrip_warnings:
        print(f"warning: {warning}", file=sys.stderr)
    smoke_summary = smoke_preferences_payload(args, roundtrip_preferences or validated)
    print(f"Saved Panda preferences to {path}")
    print(json.dumps(validated, indent=2))
    print("Panda preferences smoke test passed:")
    print(json.dumps(smoke_summary, indent=2))
    return 0


def show_preferences(args: argparse.Namespace) -> int:
    path = default_preferences_path()
    preferences, warnings = read_preferences(path)
    print(f"Panda preferences path: {path}")
    if not preferences:
        print("No Panda preferences saved.")
        return 0
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    print(json.dumps(preferences, indent=2))
    return 0


def reset_preferences(args: argparse.Namespace) -> int:
    path = default_preferences_path()
    if path.exists():
        path.unlink()
        print(f"Removed Panda preferences at {path}")
    else:
        print(f"No Panda preferences saved at {path}")
    return 0


def resolve_profile(args: argparse.Namespace) -> dict:
    profile_name = args.profile or ROLE_DEFAULT_PROFILES.get(args.role) or HARD_FALLBACK_PROFILE
    if args.profile:
        if "profile" in getattr(args, "preference_sourced_fields", set()):
            profile_source = "preferences"
        else:
            profile_source = "cli"
    elif args.role in ROLE_DEFAULT_PROFILES:
        profile_source = "role_default"
    else:
        profile_source = "fallback"

    profile = MODEL_PROFILES[profile_name]
    env_opencode_model = os.environ.get("OPENCODE_MODEL")
    env_codex_model = os.environ.get("CODEX_MODEL")
    env_codex_effort = os.environ.get("CODEX_REASONING_EFFORT") or os.environ.get("CODEX_EFFORT")
    claude_model = args.claude_model or profile["claude_model"]
    claude_effort = args.claude_effort or profile["claude_effort"]
    qwen_model = args.qwen_model or profile["qwen_model"]
    codex_model = args.codex_model or env_codex_model or profile["codex_model"]
    codex_effort = args.codex_effort or env_codex_effort or profile["codex_effort"]
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
            "qwen": qwen_model,
            "codex": codex_model,
        },
        "effective_effort": {
            "claude": claude_effort,
            "codex": codex_effort,
        },
        "requested_models": {
            "claude": args.claude_model,
            "opencode": args.opencode_model or env_opencode_model or DEFAULT_OPENCODE_MODEL,
            "qwen": args.qwen_model or DEFAULT_QWEN_MODEL,
            "codex": args.codex_model or env_codex_model or DEFAULT_CODEX_MODEL,
        },
        "requested_effort": {
            "claude": args.claude_effort,
            "codex": args.codex_effort or env_codex_effort,
        },
        "effort_support": {
            "claude": None,
            "codex": True,
        },
        "applied_effort": {
            "claude": None,
            "codex": None,
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
        "active_models": active_models(args),
        "effective_effort": dict(resolution["effective_effort"]),
        "requested_models": dict(resolution["requested_models"]),
        "requested_effort": dict(resolution["requested_effort"]),
        "effort_support": dict(resolution["effort_support"]),
        "applied_effort": dict(resolution["applied_effort"]),
    }


def active_models(args: argparse.Namespace) -> dict[str, str]:
    configured_agents = getattr(args, "configured_agents", [])
    if configured_agents:
        return {
            agent["name"]: agent["model"]
            for agent in configured_agents
        }
    resolution = get_profile_resolution(args)
    models = resolution["effective_models"]
    return {tool: models[tool] for tool in requested_tools(args.tool, args)}


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


def record_codex_effort(args: argparse.Namespace, applied_effort: Optional[str]) -> None:
    resolution = get_profile_resolution(args)
    resolution.setdefault("effort_support", {})["codex"] = True
    resolution.setdefault("applied_effort", {})["codex"] = applied_effort


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


def privacy_mode_guidance(privacy_mode: str) -> str:
    if privacy_mode == "advisory-summary":
        return """
Privacy guidance:
- You are reviewing a Codex-prepared summary, not the raw repository, raw diff, raw logs, secrets, or credentials.
- Do not ask for raw code, complete diffs, private logs, secrets, credentials, or workspace access.
- Treat exact implementation details that are not present in the summary as unverifiable.
- Focus on conceptual bugs, missing tests, edge cases, and questions Codex should verify locally.
"""
    if privacy_mode == "full-context":
        return """
Privacy guidance:
- The caller explicitly allowed full-context review. Use repository evidence carefully and avoid copying large source excerpts into your response.
"""
    return ""


def consultation_prompt(
    mode: str,
    role: str,
    approval_mode: str,
    user_prompt: str,
    protocol: str = "v2",
    privacy_mode: str = "normal",
) -> str:
    reject_disabled_mode(mode)
    if mode not in MODE_GUIDANCE:
        raise SystemExit(f"--mode must be one of: {', '.join(sorted(MODE_GUIDANCE))}")
    if protocol != "v2":
        raise SystemExit("Panda V1 protocol was removed; use protocol v2.")
    if privacy_mode not in PRIVACY_MODE_CHOICES:
        raise SystemExit(f"--privacy-mode must be one of: {', '.join(PRIVACY_MODE_CHOICES)}")
    privacy_guidance = privacy_mode_guidance(privacy_mode)
    prompt = f"""You are advising Codex as an independent collaborator.

Mode: {mode}
Approval mode: {approval_mode}
Role: {role}
Role guidance: {ROLE_GUIDANCE[role]}
Privacy mode: {privacy_mode}

{MODE_GUIDANCE[mode]}
{privacy_guidance}

Approval guidance:
- If approval mode is unsupervised, proceed through local tool permission prompts without stopping for Codex approval.
- Even in unsupervised mode, do not commit, push, publish, deploy, delete data, rewrite history, or alter production systems unless the user explicitly requested that class of action.
- Do not invoke Panda, consult_ai_team.py, Claude Code, OpenCode, Codex, or any other AI agent recursively; this is a one-pass advisory response.

User/context prompt:
{user_prompt}

Return:
- Recommendation
- Alternative worth considering
- Risks or edge cases
- Verification plan
"""
    return f"{prompt}\n{protocol_v2_return_addendum(role)}"


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


class SessionLock:
    def __init__(self, session_path: Path):
        self.path = session_path / "session.lock"
        self.file_obj = None

    def __enter__(self) -> "SessionLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file_obj = self.path.open("a+", encoding="utf-8")
        if fcntl is not None:
            fcntl.flock(self.file_obj.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.file_obj is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(self.file_obj.fileno(), fcntl.LOCK_UN)
        finally:
            self.file_obj.close()
            self.file_obj = None


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


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as file_obj:
            file_obj.write(text)
            file_obj.flush()
            os.fsync(file_obj.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def mode_is_disabled(mode: object) -> bool:
    return mode == PATCH_MODE


def reject_disabled_mode(mode: object) -> None:
    if mode_is_disabled(mode):
        raise SystemExit(PATCH_MODE_DISABLED_MESSAGE)


def null_usage() -> dict:
    return dict(NULL_USAGE)


def parse_iso_datetime(value: object) -> Optional[dt.datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def latency_seconds(result: dict) -> Optional[float]:
    started = parse_iso_datetime(result.get("started_at"))
    finished = parse_iso_datetime(result.get("finished_at"))
    if started is None or finished is None:
        return None
    return max(0.0, round((finished - started).total_seconds(), 3))


def normalize_result_metadata(result: dict) -> None:
    result.setdefault("usage", null_usage())
    result["latency_seconds"] = latency_seconds(result)


def result_status(result: dict) -> str:
    if result.get("timed_out"):
        return "timeout"
    if result.get("returncode") == 0:
        return "success"
    if str(result.get("stdout") or "").strip():
        return "partial"
    return "failure"


SECTION_KEYS = {
    "recommendation": "recommendation",
    "alternative worth considering": "alternative",
    "alternative": "alternative",
    "risks or edge cases": "risks",
    "risks": "risks",
    "verification plan": "verification_plan",
    "confidence": "confidence",
}
SECTION_HEADING_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\*\*)?"
    r"(Recommendation|Alternative worth considering|Alternative|Risks or edge cases|Risks|Verification plan|Confidence)"
    r"(?:\*\*)?\s*:?\s*$",
    re.IGNORECASE,
)


def extract_summary_fields(text: str) -> dict:
    fields = {
        "recommendation": None,
        "alternative": None,
        "risks": None,
        "verification_plan": None,
        "confidence": None,
    }
    current_key: Optional[str] = None
    buffers: dict[str, list[str]] = {}
    for line in text.splitlines():
        match = SECTION_HEADING_RE.match(line)
        if match:
            heading = match.group(1).strip().lower()
            current_key = SECTION_KEYS.get(heading)
            if current_key:
                buffers.setdefault(current_key, [])
            continue
        if current_key:
            buffers[current_key].append(line)

    for key, lines in buffers.items():
        value = "\n".join(lines).strip()
        fields[key] = value or None
    return fields


def truncate_text(value: object, limit: int) -> tuple[object, bool]:
    if not isinstance(value, str) or len(value) <= limit:
        return value, False
    if limit <= 20:
        return value[:limit], True
    return value[: limit - 18].rstrip() + "\n[truncated]", True


def artifact_json_text(data: dict) -> str:
    return json.dumps(data, indent=2) + "\n"


def enforce_artifact_limit(data: dict, max_chars: int) -> dict:
    if len(artifact_json_text(data)) <= max_chars:
        return data

    limited = json.loads(json.dumps(data))
    findings = limited.get("findings")
    target_findings = findings if isinstance(findings, list) else [limited]
    for finding in target_findings:
        if not isinstance(finding, dict):
            continue
        finding["truncated"] = True
        for field in ("recommendation", "alternative", "risks", "verification_plan"):
            value, _ = truncate_text(finding.get(field), 1000)
            finding[field] = value

    if len(artifact_json_text(limited)) <= max_chars:
        return limited

    for finding in target_findings:
        if not isinstance(finding, dict):
            continue
        for field in ("recommendation", "alternative", "risks", "verification_plan"):
            value, _ = truncate_text(finding.get(field), 200)
            finding[field] = value

    if len(artifact_json_text(limited)) <= max_chars:
        return limited

    for finding in target_findings:
        if not isinstance(finding, dict):
            continue
        for field in ("recommendation", "alternative", "risks", "verification_plan"):
            finding[field] = None
        finding["truncated"] = True
        for field in ("raw_output_path", "stderr_path"):
            value, _ = truncate_text(finding.get(field), 240)
            finding[field] = value

    if len(artifact_json_text(limited)) <= max_chars:
        return limited

    if isinstance(findings, list):
        limited["findings"] = [
            {
                "tool": finding.get("tool"),
                "status": finding.get("status"),
                "truncated": True,
            }
            for finding in findings
            if isinstance(finding, dict)
        ]
        limited["truncated"] = True
        if len(artifact_json_text(limited)) <= max_chars:
            return limited

    if "findings" in limited:
        omitted_count = len(findings) if isinstance(findings, list) else 1
        limited["findings"] = []
        limited["omitted_findings_count"] = omitted_count
        limited["truncated"] = True
        if len(artifact_json_text(limited)) <= max_chars:
            return limited

    for key, value in list(limited.items()):
        if isinstance(value, str):
            limited[key], _ = truncate_text(value, 120)
    return limited


def write_limited_json(path: Path, data: dict, max_chars: int) -> dict:
    limited = enforce_artifact_limit(data, max_chars)
    write_json(path, limited)
    return limited


def result_raw_output_path(output_dir: Path, tool: str) -> str:
    return str(output_dir / f"{tool}.txt")


def make_finding(tool: str, result: dict, output_dir: Path) -> dict:
    raw_output_path = result_raw_output_path(output_dir, tool)
    fields = extract_summary_fields(str(result.get("stdout") or ""))
    finding = {
        "tool": tool,
        "status": result_status(result),
        "returncode": result.get("returncode"),
        "timed_out": bool(result.get("timed_out")),
        "latency_seconds": result.get("latency_seconds"),
        "recommendation": fields["recommendation"],
        "alternative": fields["alternative"],
        "risks": fields["risks"],
        "verification_plan": fields["verification_plan"],
        "confidence": fields["confidence"],
        "usage": result.get("usage") or null_usage(),
        "raw_output_path": raw_output_path,
        "stderr_path": result.get("stderr_path"),
        "warnings": result.get("warnings") or [],
        "truncated": False,
    }
    return enforce_artifact_limit(finding, SUMMARY_MAX_CHARS)


def write_run_artifacts(output_dir: Path, raw_results: dict[str, dict], tool_order: Iterable[str]) -> dict:
    findings = []
    summary_paths = {}
    for tool in tool_order:
        finding = make_finding(tool, raw_results[tool], output_dir)
        summary_path = output_dir / f"{tool}.summary.json"
        finding = write_limited_json(summary_path, finding, SUMMARY_MAX_CHARS)
        findings.append(finding)
        summary_paths[tool] = str(summary_path)

    evidence = {
        "schema_version": SCHEMA_VERSION,
        "findings": findings,
    }
    evidence_path = output_dir / "evidence.json"
    evidence = write_limited_json(evidence_path, evidence, EVIDENCE_MAX_CHARS)
    return {
        "evidence": evidence,
        "evidence_path": str(evidence_path),
        "summary_paths": summary_paths,
    }


def write_protocol_artifacts(
    output_dir: Path,
    raw_results: dict[str, dict],
    tool_order: Iterable[str],
    *,
    protocol: str,
    role: str,
) -> dict:
    if protocol != "v2":
        raise ValueError(f"Unsupported Panda protocol: {protocol}")
    if role == "contract-falsifier":
        sidecar = panda_v2_artifacts.write_falsifier_sidecar(output_dir, raw_results, tool_order)
        return {"falsifier": sidecar["path"]}
    sidecar = panda_v2_artifacts.write_contracts_sidecar(output_dir, raw_results, tool_order)
    return {"contracts": sidecar["path"]}


def prompt_telemetry(prompt: str) -> dict:
    char_count = len(prompt)
    return {
        "characters": char_count,
        "soft_limit": PROMPT_WARN_CHARS,
        "warning": char_count > PROMPT_WARN_CHARS,
    }


def collect_result_warnings(raw_results: dict[str, dict]) -> list[dict]:
    warnings = []
    for tool, result in raw_results.items():
        for warning in result.get("warnings") or []:
            warnings.append({"tool": tool, "code": warning})
    return warnings


def build_telemetry(
    raw_results: dict[str, dict],
    artifact_paths: dict,
    prompt: Optional[str] = None,
    extra_warnings: Optional[list[dict]] = None,
    opencode_runtime: Optional[dict] = None,
) -> dict:
    latencies = [
        result.get("latency_seconds")
        for result in raw_results.values()
        if isinstance(result.get("latency_seconds"), (int, float))
    ]
    latency = {
        "min": min(latencies) if latencies else None,
        "max": max(latencies) if latencies else None,
        "total": round(sum(latencies), 3) if latencies else None,
    }
    warnings = collect_result_warnings(raw_results)
    if extra_warnings:
        warnings.extend(extra_warnings)
    telemetry = {
        "tool_count": len(raw_results),
        "failed_tool_count": sum(1 for result in raw_results.values() if result_status(result) == "failure"),
        "timed_out_tool_count": sum(1 for result in raw_results.values() if result.get("timed_out")),
        "latency": latency,
        "artifact_paths": artifact_paths,
    }
    if prompt is not None:
        telemetry["prompt"] = prompt_telemetry(prompt)
        if telemetry["prompt"]["warning"]:
            warnings.append({"tool": None, "code": "prompt_soft_limit_exceeded"})
    if opencode_runtime:
        telemetry["opencode_runtime"] = opencode_runtime
    telemetry["warnings"] = warnings
    return telemetry


def session_root(args: argparse.Namespace) -> Path:
    return (args.session_dir or default_session_root()).expanduser()


def recover_running_session(session: dict) -> list[str]:
    if session.get("status") != "running":
        return []
    session_id = session.get("session_id", "<unknown>")
    if process_is_running(session.get("runner_pid")):
        raise SystemExit(f"Session is already running: {session_id}")
    session["status"] = "waiting_for_user"
    session["last_turn_status"] = "degraded"
    session["latest_stopping_suggestion"] = "tool_timed_out"
    session["recovered_running_session_at"] = now_iso()
    session["runner_pid"] = None
    return ["recovered_stale_running_session"]


def load_existing_session(session_path: Path) -> tuple[dict, list[str]]:
    session_file = session_path / "session.json"
    if not session_file.exists():
        raise SystemExit(f"Session not found: {session_path.name}")
    session = read_json(session_file)
    if session.get("schema_version") != SCHEMA_VERSION:
        raise SystemExit(f"Unsupported session schema: {session.get('schema_version')!r}")
    recovery_notes = recover_running_session(session)
    return session, recovery_notes


def new_session_state(args: argparse.Namespace, workspace: Path, session_id: str) -> dict:
    profile_metadata = profile_manifest_metadata(args)
    state = {
        "schema_version": SCHEMA_VERSION,
        "session_id": session_id,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "status": "created",
        "workspace": str(workspace),
        "tool": args.tool,
        "tool_selector": args.tool,
        "tool_selection_source": tool_selection_source(args),
        "mode": args.mode,
        "role": args.role,
        "approval_mode": args.approval_mode,
        "privacy_mode": args.privacy_mode,
        "execution": args.execution,
        "requested_tools": requested_tools(args.tool, args),
        "tool_session_ids": {
            "claude": None,
            "opencode": None,
            "qwen": None,
            "codex": None,
        },
        "models": dict(profile_metadata["effective_models"]),
        "profile": profile_metadata["profile"],
        "profile_source": profile_metadata["profile_source"],
        "cost_tier": profile_metadata["cost_tier"],
        "effective_models": dict(profile_metadata["effective_models"]),
        "active_models": dict(profile_metadata["active_models"]),
        "effective_effort": dict(profile_metadata["effective_effort"]),
        "requested_models": dict(profile_metadata["requested_models"]),
        "requested_effort": dict(profile_metadata["requested_effort"]),
        "effort_support": dict(profile_metadata["effort_support"]),
        "applied_effort": dict(profile_metadata["applied_effort"]),
        "preferences": preference_manifest_metadata(args),
        "agents": [dict(agent) for agent in getattr(args, "configured_agents", [])],
        "turn_count": 0,
        "last_turn_status": None,
        "latest_stopping_suggestion": None,
        "runner_pid": None,
    }
    for agent in getattr(args, "configured_agents", []):
        state["tool_session_ids"].setdefault(agent["name"], None)
    state["protocol"] = args.protocol
    return state


def make_session(args: argparse.Namespace, workspace: Path) -> tuple[dict, Path, bool]:
    root = session_root(args)
    if args.session:
        session_id = args.session
        validate_session_id(session_id)
        session_path = root / session_id
        session, recovery_notes = load_existing_session(session_path)
        if recovery_notes:
            session["_recovery_notes"] = recovery_notes
        return session, session_path, False

    session_id = str(uuid.uuid4())
    session_path = root / session_id
    session = new_session_state(args, workspace, session_id)
    return session, session_path, True


def inherit_session_args(args: argparse.Namespace, session: dict) -> None:
    explicit_flags = getattr(args, "explicit_flags", set())
    for field in ("tool", "mode", "role", "approval_mode", "privacy_mode", "execution", "protocol"):
        if field not in explicit_flags and session.get(field):
            mark_preference_ignored(args, field, "session_state")
            setattr(args, field, session[field])
    if "profile" not in explicit_flags and session.get("profile"):
        mark_preference_ignored(args, "profile", "session_state")
        args.profile = session["profile"]

    requested_models = session.get("requested_models") or {}
    requested_effort = session.get("requested_effort") or {}
    if "claude_model" not in explicit_flags and requested_models.get("claude"):
        mark_preference_ignored(args, "claude_model", "session_state")
        args.claude_model = requested_models["claude"]
    if "opencode_model" not in explicit_flags and requested_models.get("opencode"):
        mark_preference_ignored(args, "opencode_model", "session_state")
        args.opencode_model = requested_models["opencode"]
    if "qwen_model" not in explicit_flags and requested_models.get("qwen"):
        mark_preference_ignored(args, "qwen_model", "session_state")
        args.qwen_model = requested_models["qwen"]
    if "codex_model" not in explicit_flags and requested_models.get("codex"):
        mark_preference_ignored(args, "codex_model", "session_state")
        args.codex_model = requested_models["codex"]
    if "claude_effort" not in explicit_flags and requested_effort.get("claude"):
        mark_preference_ignored(args, "claude_effort", "session_state")
        args.claude_effort = requested_effort["claude"]
    if "codex_effort" not in explicit_flags and requested_effort.get("codex"):
        mark_preference_ignored(args, "codex_effort", "session_state")
        args.codex_effort = requested_effort["codex"]
    if "agents" not in explicit_flags and "tool" not in explicit_flags and session.get("agents"):
        mark_preference_ignored(args, "profile.agents", "session_state")
        args.configured_agents = [dict(agent) for agent in session["agents"]]
    if args.tool == "auto" and "tool" not in explicit_flags and session.get("requested_tools"):
        args._auto_requested_tools = list(session["requested_tools"])
    args.profile_resolution = resolve_profile(args)


def next_turn_dir(session_path: Path, turn_number: int) -> Path:
    return session_path / "turns" / f"{turn_number:03d}"


def next_available_turn_dir(
    session_path: Path,
    preferred_turn_number: int,
    recovery_notes: list[str],
) -> tuple[int, Path]:
    turn_number = preferred_turn_number
    while True:
        turn_dir = next_turn_dir(session_path, turn_number)
        if not turn_dir.exists():
            return turn_number, turn_dir
        recovery_notes.append(f"skipped_existing_turn_dir:{turn_number:03d}")
        turn_number += 1


def find_numeric_value(data: object, keys: Iterable[str]) -> Optional[float]:
    key_set = set(keys)
    if isinstance(data, dict):
        for key, value in data.items():
            if key in key_set and isinstance(value, (int, float)):
                return float(value)
            found = find_numeric_value(value, key_set)
            if found is not None:
                return found
    elif isinstance(data, list):
        for item in data:
            found = find_numeric_value(item, key_set)
            if found is not None:
                return found
    return None


def parse_usage_event(event: dict) -> dict:
    usage = null_usage()
    usage_sources = []
    for key in ("usage", "tokens", "tokenUsage"):
        source = event.get(key)
        if isinstance(source, dict):
            usage_sources.append(source)
    usage_sources.append(event)
    key_groups = {
        "input_tokens": ("input_tokens", "inputTokens", "prompt_tokens", "promptTokens"),
        "output_tokens": ("output_tokens", "outputTokens", "completion_tokens", "completionTokens"),
        "cache_read_tokens": (
            "cache_read_tokens",
            "cacheReadTokens",
            "cached_tokens",
            "cachedTokens",
            "cache_read",
            "cacheRead",
        ),
        "cost_usd": ("cost_usd", "costUSD", "cost", "total_cost", "totalCost"),
    }
    for target, keys in key_groups.items():
        value = None
        for source in usage_sources:
            value = find_numeric_value(source, keys)
            if value is not None:
                break
        if value is None:
            continue
        usage[target] = value if target == "cost_usd" else int(value)
    return usage


def merge_usage(base: dict, update: dict) -> dict:
    merged = dict(base)
    for key in NULL_USAGE:
        if update.get(key) is not None:
            merged[key] = update[key]
    return merged


def parse_opencode_jsonl(raw: str, include_warnings: bool = False):
    session_id = None
    text_parts: list[str] = []
    usage = null_usage()
    malformed_lines = 0
    parsed_events = 0
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            malformed_lines += 1
            continue
        if not isinstance(event, dict):
            malformed_lines += 1
            continue
        parsed_events += 1
        if not session_id and isinstance(event.get("sessionID"), str):
            session_id = event["sessionID"]
        usage = merge_usage(usage, parse_usage_event(event))
        part = event.get("part")
        if event.get("type") == "text" and isinstance(part, dict) and isinstance(part.get("text"), str):
            text_parts.append(part["text"])
    warnings = []
    if malformed_lines:
        warnings.append("opencode_jsonl_malformed_lines_skipped")
    if raw.strip() and parsed_events == 0:
        warnings.append("opencode_jsonl_no_parseable_events")
    if not session_id:
        warnings.append("opencode_session_id_missing")
    if all(usage.get(key) is None for key in NULL_USAGE):
        warnings.append("opencode_usage_missing")
    parsed = (session_id, "".join(text_parts).strip(), usage)
    if include_warnings:
        return (*parsed, warnings)
    return parsed


def popen_process(
    command: list[str],
    cwd: Path,
    stdout_file,
    stderr_file,
    env_overrides: Optional[dict[str, str]] = None,
) -> subprocess.Popen:
    env = None
    if env_overrides:
        env = os.environ.copy()
        env.update(env_overrides)
    return subprocess.Popen(
        command,
        cwd=str(cwd),
        stdout=stdout_file,
        stderr=stderr_file,
        text=True,
        env=env,
        start_new_session=(os.name == "posix"),
    )


def terminate_process(process: subprocess.Popen, grace_seconds: float = 5.0) -> None:
    if process.poll() is not None:
        return
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        except OSError:
            process.terminate()
    else:
        process.terminate()
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            except OSError:
                process.kill()
        else:
            process.kill()
        process.wait(timeout=grace_seconds)


def run_tool(
    name: str,
    command: list[str],
    cwd: Path,
    timeout: int,
    dry_run: bool,
    output_dir: Optional[Path] = None,
    env_overrides: Optional[dict[str, str]] = None,
) -> dict:
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
    if env_overrides:
        result["env"] = dict(env_overrides)
    if dry_run:
        result["stdout"] = quote_cmd(command)
        result["returncode"] = 0
        result["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        normalize_result_metadata(result)
        return result
    temp_dir = None
    if output_dir is None:
        temp_dir = tempfile.TemporaryDirectory()
        artifact_dir = Path(temp_dir.name)
    else:
        artifact_dir = output_dir
        artifact_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = artifact_dir / f"{name}.stdout"
    stderr_path = artifact_dir / f"{name}.stderr"
    result["stdout_path"] = str(stdout_path)
    result["stderr_path"] = str(stderr_path)
    process = None
    stdout_file = None
    stderr_file = None
    try:
        stdout_file = stdout_path.open("w", encoding="utf-8")
        stderr_file = stderr_path.open("w", encoding="utf-8")
        process = popen_process(command, cwd, stdout_file, stderr_file, env_overrides)
        try:
            result["returncode"] = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            terminate_process(process)
            result["timed_out"] = True
            result["returncode"] = process.returncode if process.returncode is not None else -1
        result["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    except OSError as exc:
        if stderr_file is not None:
            stderr_file.write(f"Failed to launch {name}: {exc}\n")
            stderr_file.flush()
        result["returncode"] = -1
        result["stderr"] = f"Failed to launch {name}: {exc}"
        result["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    except Exception:
        if process is not None:
            terminate_process(process)
        raise
    finally:
        if stdout_file is not None and not stdout_file.closed:
            stdout_file.close()
        if stderr_file is not None and not stderr_file.closed:
            stderr_file.close()
    if not result["finished_at"]:
        result["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    result["stdout"] = read_text_if_exists(stdout_path)
    stderr_text = read_text_if_exists(stderr_path)
    if result["stderr"]:
        result["stderr"] = f"{result['stderr']}\n{stderr_text}".strip()
    else:
        result["stderr"] = stderr_text
    if result["timed_out"] and not result["stderr"].strip():
        result["stderr"] = f"Timed out after {timeout} seconds."
    warning = claude_auth_failure_warning(name, command, result)
    if warning:
        append_tool_warning(
            result,
            warning,
            "Claude Code reported that it was not logged in when launched by the Panda runner. "
            "If `claude -p` works directly, run Panda outside the Codex filesystem sandbox so "
            "Claude can access its OAuth/keychain login state.",
        )
    warning = opencode_data_dir_failure_warning(result, env_overrides)
    if warning:
        append_tool_warning(
            result,
            warning,
            "OpenCode failed while using Panda-managed XDG_DATA_HOME; inspect the recorded "
            "opencode data dir and retry after clearing stale runtime state.",
        )
    if temp_dir is not None:
        temp_dir.cleanup()
        result.pop("stdout_path", None)
        result.pop("stderr_path", None)
    normalize_result_metadata(result)
    return result


def failed_tool_result(name: str, command: list[str], cwd: Path, exc: Exception) -> dict:
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    result = {
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
    normalize_result_metadata(result)
    return result


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
    write_text_atomic(output_dir / f"{result['tool']}.txt", body.strip() + "\n")


def should_run_parallel(execution: str, mode: str, tool_count: int) -> bool:
    if tool_count < 2:
        return False
    if mode not in {"advisory", "explore"}:
        return False
    if execution == "parallel":
        return True
    if execution == "sequential":
        return False
    return True


def opencode_serialization_enabled(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "serialize_opencode", False)) or env_flag_enabled("PANDA_SERIALIZE_OPENCODE")


def opencode_auto_approval_enabled(args: argparse.Namespace) -> bool:
    return args.approval_mode == "unsupervised" and args.privacy_mode != "advisory-summary"


def opencode_backed_command_names(args: argparse.Namespace) -> set[str]:
    configured_agents = getattr(args, "configured_agents", [])
    if configured_agents:
        return {
            agent["name"]
            for agent in configured_agents
            if agent["backend"] == "opencode"
        }
    return set(OPENCODE_BACKED_TOOLS)


def opencode_xdg_data_home(root: Path) -> Path:
    return root / "opencode-data"


def build_tool_env_overrides(
    args: argparse.Namespace,
    commands: dict[str, list[str]],
    opencode_data_home: Path,
) -> dict[str, dict[str, str]]:
    opencode_names = opencode_backed_command_names(args)
    overrides = {}
    for name in commands:
        if name in opencode_names:
            opencode_data_home.mkdir(parents=True, exist_ok=True)
            overrides[name] = {"XDG_DATA_HOME": str(opencode_data_home)}
    return overrides


def opencode_runtime_metadata(env_overrides: dict[str, dict[str, str]]) -> Optional[dict]:
    tools = sorted(
        name
        for name, overrides in env_overrides.items()
        if overrides.get("XDG_DATA_HOME")
    )
    if not tools:
        return None
    xdg_data_home = env_overrides[tools[0]]["XDG_DATA_HOME"]
    return {
        "tools": tools,
        "xdg_data_home": xdg_data_home,
        "data_dir": str(Path(xdg_data_home) / "opencode"),
    }


def command_basename(command: list[str]) -> str:
    if not command:
        return ""
    return Path(str(command[0])).name


def append_tool_warning(result: dict, code: str, message: str) -> None:
    result.setdefault("warnings", []).append(code)
    result["stderr"] = (
        result.get("stderr", "").rstrip()
        + f"\nPanda warning: {message}"
    ).strip()


def claude_auth_failure_warning(name: str, command: list[str], result: dict) -> Optional[str]:
    if result_status(result) == "success":
        return None
    if name != "claude" and "claude" not in command_basename(command):
        return None
    combined = f"{result.get('stdout') or ''}\n{result.get('stderr') or ''}"
    if not CLAUDE_AUTH_FAILURE_RE.search(combined):
        return None
    return "claude_auth_unavailable_to_subprocess"


def opencode_data_dir_failure_warning(result: dict, env_overrides: Optional[dict[str, str]]) -> Optional[str]:
    if not env_overrides or not env_overrides.get("XDG_DATA_HOME"):
        return None
    if result_status(result) == "success":
        return None
    combined = f"{result.get('stdout') or ''}\n{result.get('stderr') or ''}".lower()
    if "sqlite" not in combined and "pragma" not in combined and "wal_checkpoint" not in combined:
        return None
    return "opencode_managed_data_dir_failure"


def split_serialized_opencode_commands(
    commands: dict[str, list[str]],
    opencode_backed_names: Optional[set[str]] = None,
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    opencode_names = set(OPENCODE_BACKED_TOOLS) if opencode_backed_names is None else opencode_backed_names
    serialized = {name: command for name, command in commands.items() if name in opencode_names}
    parallel = {name: command for name, command in commands.items() if name not in opencode_names}
    return parallel, serialized


def run_one_shot_command_group(
    commands: dict[str, list[str]],
    run_cwd: Path,
    timeout: int,
    dry_run: bool,
    output_dir: Path,
    env_overrides: Optional[dict[str, dict[str, str]]] = None,
) -> dict[str, dict]:
    results = {}
    for name, command in commands.items():
        try:
            results[name] = run_tool(
                name,
                command,
                run_cwd,
                timeout,
                dry_run,
                output_dir,
                (env_overrides or {}).get(name),
            )
        except Exception as exc:
            results[name] = failed_tool_result(name, command, run_cwd, exc)
    return results


def run_one_shot_commands(
    commands: dict[str, list[str]],
    run_cwd: Path,
    timeout: int,
    dry_run: bool,
    output_dir: Path,
    run_parallel: bool,
    serialize_opencode: bool,
    opencode_backed_names: Optional[set[str]] = None,
    env_overrides: Optional[dict[str, dict[str, str]]] = None,
) -> dict[str, dict]:
    if not run_parallel:
        return run_one_shot_command_group(commands, run_cwd, timeout, dry_run, output_dir, env_overrides)

    raw_results: dict[str, dict] = {}
    parallel_commands, serialized_opencode = ({}, {})
    if serialize_opencode:
        parallel_commands, serialized_opencode = split_serialized_opencode_commands(commands, opencode_backed_names)
    else:
        parallel_commands = commands

    with ThreadPoolExecutor(max_workers=max(1, len(parallel_commands) + bool(serialized_opencode))) as executor:
        futures = {}
        for name, command in parallel_commands.items():
            futures[
                executor.submit(
                    run_tool,
                    name,
                    command,
                    run_cwd,
                    timeout,
                    dry_run,
                    output_dir,
                    (env_overrides or {}).get(name),
                )
            ] = name
        if serialized_opencode:
            futures[
                executor.submit(
                    run_one_shot_command_group,
                    serialized_opencode,
                    run_cwd,
                    timeout,
                    dry_run,
                    output_dir,
                    env_overrides,
                )
            ] = "__serialized_opencode__"
        for future in as_completed(futures):
            name = futures[future]
            try:
                value = future.result()
            except Exception as exc:
                if name == "__serialized_opencode__":
                    for tool_name, command in serialized_opencode.items():
                        raw_results[tool_name] = failed_tool_result(tool_name, command, run_cwd, exc)
                else:
                    raw_results[name] = failed_tool_result(name, parallel_commands[name], run_cwd, exc)
                continue
            if name == "__serialized_opencode__":
                raw_results.update(value)
            else:
                raw_results[name] = value
    return raw_results


def executable_available(binary: str) -> bool:
    resolved = shutil.which(binary)
    if resolved:
        return True
    if os.sep not in binary and (os.altsep is None or os.altsep not in binary):
        return False
    path = Path(binary).expanduser()
    return path.exists() and os.access(path, os.X_OK)


def tool_is_available(tool: str, args: argparse.Namespace) -> bool:
    if tool == "claude":
        return executable_available(args.claude_bin)
    if tool in OPENCODE_BACKED_TOOLS:
        return executable_available(args.opencode_bin)
    if tool == "codex":
        return executable_available(args.codex_bin)
    return False


def resolve_auto_tools(args: argparse.Namespace) -> list[str]:
    cached = getattr(args, "_auto_requested_tools", None)
    if cached:
        return list(cached)

    requested = [tool for tool in AUTO_TOOL_ORDER if tool_is_available(tool, args)]
    skipped = [tool for tool in AUTO_TOOL_ORDER if tool not in requested]
    if not requested:
        args._auto_requested_tools = []
        args._auto_skipped_tools = list(AUTO_TOOL_ORDER)
        raise SystemExit(
            "--tool auto could not find any advisor CLI. Install Claude Code, OpenCode, or Codex CLI, "
            "or pass an explicit --tool with its matching --*-bin option."
        )

    args._auto_requested_tools = list(requested)
    args._auto_skipped_tools = skipped
    return list(requested)


def requested_tools(tool: str, args: Optional[argparse.Namespace] = None) -> list[str]:
    if args is not None and getattr(args, "configured_agents", []):
        return [agent["name"] for agent in args.configured_agents]
    if tool == "all":
        return list(LEGACY_ALL_TOOLS)
    if tool == "auto":
        if args is None:
            return ["codex"]
        return resolve_auto_tools(args)
    return [tool]


def tool_selection_source(args: argparse.Namespace) -> str:
    if getattr(args, "configured_agents", []):
        return "agents"
    if args.tool == "auto":
        return "auto"
    return "tool"


def tool_selection_warnings(args: argparse.Namespace) -> list[dict]:
    if getattr(args, "configured_agents", []) or args.tool != "auto":
        return []
    requested_tools(args.tool, args)
    return [
        {"tool": tool, "code": "auto_tool_unavailable"}
        for tool in getattr(args, "_auto_skipped_tools", [])
    ]


def runner_version() -> str:
    pyproject = REPO_ROOT / "pyproject.toml"
    if pyproject.exists():
        match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject.read_text(encoding="utf-8"), re.MULTILINE)
        if match:
            return match.group(1)
    try:
        from importlib import metadata as importlib_metadata

        return importlib_metadata.version("panda")
    except Exception:
        pass
    return "unknown"


def prompt_source(args: argparse.Namespace) -> str:
    sources = []
    if getattr(args, "prompt", None):
        sources.append("prompt")
    if getattr(args, "prompt_file", None):
        sources.append("prompt_file")
    if getattr(args, "session", None) is not None:
        sources.append("session")
    return "+".join(sources) or "unknown"


def export_mode(args: argparse.Namespace) -> str:
    if args.privacy_mode == "advisory-summary":
        return "summary-review"
    if args.privacy_mode == "full-context" and args.mode == "explore":
        return "full-context-review"
    return "standard-advisory"


def export_destinations(args: argparse.Namespace) -> list[dict]:
    destinations = []
    configured_agents = getattr(args, "configured_agents", [])
    if configured_agents:
        for agent in configured_agents:
            destination = {
                "name": agent["name"],
                "backend": agent["backend"],
                "model": agent["model"],
            }
            if agent.get("effort"):
                destination["effort"] = agent["effort"]
            destinations.append(destination)
        return destinations

    resolution = get_profile_resolution(args)
    for tool in requested_tools(args.tool, args):
        if tool == "claude":
            destination = {
                "name": "claude",
                "backend": "claude",
                "model": resolution["effective_models"]["claude"],
            }
            if resolution["effective_effort"]["claude"]:
                destination["effort"] = resolution["effective_effort"]["claude"]
        elif tool == "opencode":
            destination = {
                "name": "opencode",
                "backend": "opencode",
                "model": resolution["effective_models"]["opencode"],
            }
        elif tool == "qwen":
            destination = {
                "name": "qwen",
                "backend": "opencode",
                "model": resolution["effective_models"]["qwen"],
            }
        elif tool == "codex":
            destination = {
                "name": "codex",
                "backend": "codex",
                "model": resolution["effective_models"]["codex"],
            }
            if resolution["effective_effort"]["codex"]:
                destination["effort"] = resolution["effective_effort"]["codex"]
        else:
            destination = {
                "name": tool,
                "backend": "unknown",
                "model": None,
            }
        destinations.append(destination)
    return destinations


def export_approval_metadata(args: argparse.Namespace) -> dict:
    env = {
        "PANDA_ALLOW_CODEX_REVIEWER": env_flag_enabled("PANDA_ALLOW_CODEX_REVIEWER"),
        "PANDA_ALLOW_PRIVATE_CONTEXT_EXPORT": env_flag_enabled("PANDA_ALLOW_PRIVATE_CONTEXT_EXPORT"),
    }
    sources = []
    if args.privacy_mode in {"advisory-summary", "full-context"}:
        sources.append("privacy_mode")
    if getattr(args, "allow_codex_reviewer", False):
        sources.append("allow_codex_reviewer")
    if any(env.values()):
        sources.append("env")
    return {
        "sources": sources or ["none"],
        "privacy_mode": args.privacy_mode,
        "allow_codex_reviewer": bool(getattr(args, "allow_codex_reviewer", False)),
        "env": env,
    }


def build_export_contract(
    args: argparse.Namespace,
    prompt: str,
    workspace: Path,
    run_cwd: Path,
) -> dict:
    mode = export_mode(args)
    raw_repo_access = args.mode == "explore"
    return {
        "schema_version": EXPORT_MANIFEST_SCHEMA_VERSION,
        "created_at": now_iso(),
        "runner_version": runner_version(),
        "workspace": str(workspace.resolve()),
        "run_cwd": str(run_cwd.resolve()),
        "mode": args.mode,
        "role": args.role,
        "privacy_mode": args.privacy_mode,
        "export_mode": mode,
        "raw_repo_access": raw_repo_access,
        "raw_diff_included": False,
        "raw_logs_included": False,
        "shell_explore_allowed": args.mode == "explore",
        "tool_selector": args.tool,
        "tool_selection_source": tool_selection_source(args),
        "requested_tools": requested_tools(args.tool, args),
        "agents": [dict(agent) for agent in getattr(args, "configured_agents", [])],
        "destinations": export_destinations(args),
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "prompt_characters": len(prompt),
        "prompt_source": prompt_source(args),
        "approval": export_approval_metadata(args),
    }


def validate_export_contract_semantics(contract: dict) -> None:
    if contract.get("schema_version") != EXPORT_MANIFEST_SCHEMA_VERSION:
        raise SystemExit(
            f"Unsupported Panda export manifest schema: {contract.get('schema_version')!r}."
        )
    if contract.get("export_mode") == "summary-review":
        if contract.get("privacy_mode") != "advisory-summary":
            raise SystemExit("Invalid Panda export manifest: summary-review requires privacy_mode=advisory-summary.")
        if contract.get("mode") != "advisory":
            raise SystemExit("Invalid Panda export manifest: advisory-summary requires mode=advisory.")
        if contract.get("raw_repo_access"):
            raise SystemExit("Invalid Panda export manifest: summary-review must not have raw repo access.")
        if contract.get("shell_explore_allowed"):
            raise SystemExit("Invalid Panda export manifest: summary-review must not allow shell exploration.")
        if contract.get("workspace") == contract.get("run_cwd"):
            raise SystemExit("Invalid Panda export manifest: summary-review must use an isolated run_cwd.")
    if contract.get("privacy_mode") == "advisory-summary" and contract.get("export_mode") != "summary-review":
        raise SystemExit("Invalid Panda export manifest: advisory-summary must use summary-review.")
    if contract.get("export_mode") == "full-context-review":
        if contract.get("privacy_mode") != "full-context":
            raise SystemExit("Invalid Panda export manifest: full-context-review requires privacy_mode=full-context.")
        if contract.get("mode") != "explore":
            raise SystemExit("Invalid Panda export manifest: full-context-review requires mode=explore.")
        if not contract.get("raw_repo_access"):
            raise SystemExit("Invalid Panda export manifest: full-context-review must have raw repo access.")
        if not contract.get("shell_explore_allowed"):
            raise SystemExit("Invalid Panda export manifest: full-context-review must allow shell exploration.")


EXPORT_MANIFEST_MATCH_FIELDS = (
    "schema_version",
    "workspace",
    "run_cwd",
    "mode",
    "role",
    "privacy_mode",
    "export_mode",
    "raw_repo_access",
    "raw_diff_included",
    "raw_logs_included",
    "shell_explore_allowed",
    "tool_selector",
    "tool_selection_source",
    "requested_tools",
    "agents",
    "destinations",
    "prompt_sha256",
    "prompt_characters",
    "prompt_source",
    "approval",
)


def validate_supplied_export_manifest(expected: dict, path: Path) -> dict:
    try:
        supplied = read_json(path)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Malformed Panda export manifest at {path}: {exc}") from exc
    except OSError as exc:
        raise SystemExit(f"Unable to read Panda export manifest at {path}: {exc}") from exc
    if not isinstance(supplied, dict):
        raise SystemExit(f"Invalid Panda export manifest at {path}: expected a JSON object.")
    validate_export_contract_semantics(supplied)
    mismatches = [
        field
        for field in EXPORT_MANIFEST_MATCH_FIELDS
        if supplied.get(field) != expected.get(field)
    ]
    if mismatches:
        raise SystemExit(
            "Panda export manifest mismatch before reviewer launch: "
            + ", ".join(mismatches)
        )
    return supplied


def write_export_contract(output_dir: Path, contract: dict) -> str:
    validate_export_contract_semantics(contract)
    path = output_dir / EXPORT_MANIFEST_NAME
    write_json(path, contract)
    return str(path)


def codex_reviewer_export_allowed(args: argparse.Namespace) -> bool:
    return (
        bool(getattr(args, "allow_codex_reviewer", False))
        or getattr(args, "privacy_mode", "normal") in {"advisory-summary", "full-context"}
        or env_flag_enabled("PANDA_ALLOW_CODEX_REVIEWER")
    )


def selected_codex_reviewers(args: argparse.Namespace) -> list[str]:
    configured_agents = getattr(args, "configured_agents", [])
    if configured_agents:
        return [
            agent["name"]
            for agent in configured_agents
            if agent.get("backend") == "codex"
        ]
    return [
        tool
        for tool in requested_tools(args.tool, args)
        if tool == "codex"
    ]


def enforce_codex_reviewer_export_policy(args: argparse.Namespace) -> None:
    if getattr(args, "dry_run", False):
        return
    codex_reviewers = selected_codex_reviewers(args)
    if not codex_reviewers or codex_reviewer_export_allowed(args):
        return
    selected_text = ", ".join(codex_reviewers)
    raise SystemExit(f"{CODEX_REVIEWER_EXPORT_MESSAGE} Selected Codex reviewer: {selected_text}.")


def selected_external_reviewers(args: argparse.Namespace) -> list[str]:
    configured_agents = getattr(args, "configured_agents", [])
    if configured_agents:
        return [
            agent["name"]
            for agent in configured_agents
            if agent.get("backend") in {"claude", "opencode", "codex"}
        ]
    return requested_tools(args.tool, args)


def private_context_export_allowed(args: argparse.Namespace) -> bool:
    return (
        getattr(args, "privacy_mode", "normal") == "full-context"
        or env_flag_enabled("PANDA_ALLOW_PRIVATE_CONTEXT_EXPORT")
    )


def enforce_private_context_export_policy(args: argparse.Namespace) -> None:
    if getattr(args, "dry_run", False):
        return
    if args.mode != "explore" or args.role != "code-review":
        return
    if private_context_export_allowed(args):
        return
    reviewers = selected_external_reviewers(args)
    if not reviewers:
        return
    selected_text = ", ".join(reviewers)
    raise SystemExit(f"{PRIVATE_CONTEXT_EXPORT_MESSAGE} Selected reviewers: {selected_text}.")


def build_commands(
    args: argparse.Namespace,
    prompt: str,
    run_cwd: Path,
    session: Optional[dict] = None,
) -> tuple[dict[str, list[str]], set[str]]:
    configured_agents = getattr(args, "configured_agents", [])
    if configured_agents:
        return build_agent_commands(args, configured_agents, prompt, run_cwd, session=session)

    requested = requested_tools(args.tool, args)
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

    def add_opencode_backed_command(name: str, model_key: str, title_suffix: Optional[str] = None) -> None:
        opencode_bin = shutil.which(args.opencode_bin) or args.opencode_bin
        title = f"panda-{args.mode}-{args.role}"
        if title_suffix:
            title = f"{title}-{title_suffix}"
        commands[name] = [
            opencode_bin,
            "run",
            "--pure",
            "--title",
            title,
            "--dir",
            str(run_cwd),
        ]
        if session is not None:
            json_tools.add(name)
            opencode_session_id = session.setdefault("tool_session_ids", {}).get(name)
            if opencode_session_id:
                commands[name].extend(["--session", opencode_session_id])
            commands[name].extend(["--format", "json"])
        if opencode_auto_approval_enabled(args):
            commands[name].append("--dangerously-skip-permissions")
        opencode_model = profile_resolution["effective_models"][model_key]
        if opencode_model:
            commands[name].extend(["--model", opencode_model])
        commands[name].append(prompt)

    if "opencode" in requested:
        add_opencode_backed_command("opencode", "opencode")
    if "qwen" in requested:
        add_opencode_backed_command("qwen", "qwen", "qwen")
    if "codex" in requested:
        codex_bin = shutil.which(args.codex_bin) or args.codex_bin
        codex_command = [
            codex_bin,
            "--ask-for-approval",
            "never",
            "exec",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "--color",
            "never",
            "-C",
            str(run_cwd),
        ]
        if args.mode == "advisory":
            codex_command.insert(codex_command.index("--ephemeral"), "--skip-git-repo-check")
        codex_model = profile_resolution["effective_models"]["codex"]
        codex_effort = profile_resolution["effective_effort"]["codex"]
        if codex_model:
            codex_command.extend(["--model", codex_model])
        if codex_effort:
            codex_command.extend(["-c", f'model_reasoning_effort="{codex_effort}"'])
        record_codex_effort(args, codex_effort)
        codex_command.append(prompt)
        commands["codex"] = codex_command
    return commands, json_tools


def build_agent_commands(
    args: argparse.Namespace,
    agents: list[dict],
    prompt: str,
    run_cwd: Path,
    session: Optional[dict] = None,
) -> tuple[dict[str, list[str]], set[str]]:
    commands: dict[str, list[str]] = {}
    json_tools: set[str] = set()
    profile_resolution = get_profile_resolution(args)

    for agent in agents:
        name = agent["name"]
        backend = agent["backend"]
        model = agent["model"]
        if backend == "claude":
            claude_bin = shutil.which(args.claude_bin) or args.claude_bin
            command = [
                claude_bin,
                "-p",
                "--output-format",
                "text",
            ]
            if session is None:
                command.append("--no-session-persistence")
            else:
                tool_sessions = session.setdefault("tool_session_ids", {})
                claude_session_id = tool_sessions.get(name)
                if claude_session_id:
                    command.extend(["--resume", claude_session_id])
                else:
                    claude_session_id = str(uuid.uuid4())
                    if not args.dry_run:
                        tool_sessions[name] = claude_session_id
                    command.extend(["--session-id", claude_session_id])
            command.extend(["--model", model])
            requested_effort = agent.get("effort") or profile_resolution["effective_effort"]["claude"]
            supports_effort = claude_supports_effort(claude_bin)
            applied_effort = requested_effort if requested_effort and supports_effort else None
            record_claude_effort_support(args, supports_effort, applied_effort)
            if applied_effort:
                command.extend(["--effort", applied_effort])
            if args.mode == "advisory":
                command.extend(["--permission-mode", "plan", "--tools="])
            else:
                permission_mode = "bypassPermissions" if args.approval_mode == "unsupervised" else "default"
                command.extend(["--permission-mode", permission_mode])
            if args.mode == "explore":
                command.extend(["--allowedTools=Read,Grep,Glob,LS,Bash,WebFetch,WebSearch"])
            command.append(prompt)
            commands[name] = command
            continue

        if backend == "opencode":
            opencode_bin = shutil.which(args.opencode_bin) or args.opencode_bin
            title = f"panda-{args.mode}-{args.role}-{name}"
            command = [
                opencode_bin,
                "run",
                "--pure",
                "--title",
                title,
                "--dir",
                str(run_cwd),
            ]
            if session is not None:
                json_tools.add(name)
                opencode_session_id = session.setdefault("tool_session_ids", {}).get(name)
                if opencode_session_id:
                    command.extend(["--session", opencode_session_id])
                command.extend(["--format", "json"])
            if opencode_auto_approval_enabled(args):
                command.append("--dangerously-skip-permissions")
            command.extend(["--model", model])
            command.append(prompt)
            commands[name] = command
            continue

        if backend == "codex":
            codex_bin = shutil.which(args.codex_bin) or args.codex_bin
            effort = agent.get("effort") or profile_resolution["effective_effort"]["codex"]
            command = [
                codex_bin,
                "--ask-for-approval",
                "never",
                "exec",
                "--ephemeral",
                "--sandbox",
                "read-only",
                "--color",
                "never",
                "-C",
                str(run_cwd),
                "--model",
                model,
            ]
            if args.mode == "advisory":
                command.insert(command.index("--ephemeral"), "--skip-git-repo-check")
            if effort:
                command.extend(["-c", f'model_reasoning_effort="{effort}"'])
            record_codex_effort(args, effort)
            command.append(prompt)
            commands[name] = command

    return commands, json_tools


def default_output_dir() -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    suffix = uuid.uuid4().hex[:8]
    return Path(tempfile.gettempdir()) / "panda-consults" / f"{stamp}-{suffix}"


def run_one_shot(args: argparse.Namespace, prompt: str) -> int:
    output_dir = args.output_dir or default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    isolated_cwd = output_dir / "isolated-cwd"
    isolated_cwd.mkdir(exist_ok=True)
    workspace = args.workspace.resolve()
    run_cwd = isolated_cwd if args.mode == "advisory" else workspace

    export_contract = build_export_contract(args, prompt, workspace, run_cwd)
    if args.export_manifest:
        export_contract = validate_supplied_export_manifest(export_contract, args.export_manifest)
    export_manifest_path = write_export_contract(output_dir, export_contract)
    if args.prepare_export_manifest:
        print(f"Wrote Panda export manifest to {export_manifest_path}")
        return 0

    enforce_private_context_export_policy(args)
    enforce_codex_reviewer_export_policy(args)

    commands, _ = build_commands(args, prompt, run_cwd)
    run_parallel = should_run_parallel(args.execution, args.mode, len(commands))
    serialize_opencode = opencode_serialization_enabled(args)
    env_overrides = build_tool_env_overrides(
        args,
        commands,
        opencode_xdg_data_home(output_dir),
    )
    raw_results = run_one_shot_commands(
        commands,
        run_cwd,
        args.timeout,
        args.dry_run,
        output_dir,
        run_parallel,
        serialize_opencode,
        opencode_backed_command_names(args),
        env_overrides,
    )

    for result in raw_results.values():
        normalize_result_metadata(result)

    results = []
    for name in commands:
        result = raw_results[name]
        results.append({key: value for key, value in result.items() if key not in {"stdout", "stderr"}})
        write_response(output_dir, result)

    artifact_info = write_run_artifacts(output_dir, raw_results, commands)
    protocol_artifact_paths = write_protocol_artifacts(
        output_dir,
        raw_results,
        commands,
        protocol=args.protocol,
        role=args.role,
    )
    artifact_paths = {
        "evidence": artifact_info["evidence_path"],
        "summaries": artifact_info["summary_paths"],
        "export_manifest": export_manifest_path,
    }
    if protocol_artifact_paths:
        artifact_paths.update(protocol_artifact_paths)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "tool": args.tool,
        "tool_selector": args.tool,
        "tool_selection_source": tool_selection_source(args),
        "mode": args.mode,
        "execution": "parallel" if run_parallel else "sequential",
        "requested_execution": args.execution,
        "approval_mode": args.approval_mode,
        "privacy_mode": args.privacy_mode,
        "role": args.role,
        "dry_run": args.dry_run,
        "output_dir": str(output_dir),
        "workspace": str(workspace),
        "run_cwd": str(run_cwd),
        "requested_tools": requested_tools(args.tool, args),
        "serialize_opencode": serialize_opencode,
        "export_manifest": export_contract,
        "export_manifest_path": export_manifest_path,
        "preferences": preference_manifest_metadata(args),
        "agents": [dict(agent) for agent in getattr(args, "configured_agents", [])],
        "tools": results,
        "telemetry": build_telemetry(
            raw_results,
            artifact_paths,
            prompt=prompt,
            extra_warnings=tool_selection_warnings(args),
            opencode_runtime=opencode_runtime_metadata(env_overrides),
        ),
    }
    manifest["protocol"] = args.protocol
    manifest.update(profile_manifest_metadata(args))
    write_json(output_dir / "manifest.json", manifest)

    print(f"Wrote consultation outputs to {output_dir}")
    for name in commands:
        print(f"- {name}: {output_dir / f'{name}.txt'}")
    return 0


def session_result(
    name: str,
    command: list[str],
    cwd: Path,
    started_at: str,
    stdout_path: Path,
    stderr_path: Path,
    env_overrides: Optional[dict[str, str]] = None,
) -> dict:
    result = {
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
    if env_overrides:
        result["env"] = dict(env_overrides)
    return result


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
        session_id, text, usage, warnings = parse_opencode_jsonl(raw_stdout, include_warnings=True)
        if session_id:
            result["tool_session_id"] = session_id
        result["warnings"] = warnings
        result["usage"] = usage
        result["stdout"] = text or raw_stdout
        warning = opencode_data_dir_failure_warning(result, result.get("env"))
        if warning:
            append_tool_warning(
                result,
                warning,
                "OpenCode failed while using Panda-managed XDG_DATA_HOME; inspect the recorded "
                "opencode data dir and retry after clearing stale runtime state.",
            )
        return
    result["stdout"] = raw_stdout
    warning = claude_auth_failure_warning(result["tool"], result.get("command") or [], result)
    if warning:
        append_tool_warning(
            result,
            warning,
            "Claude Code reported that it was not logged in when launched by the Panda runner. "
            "If `claude -p` works directly, run Panda outside the Codex filesystem sandbox so "
            "Claude can access its OAuth/keychain login state.",
        )
    warning = opencode_data_dir_failure_warning(result, result.get("env"))
    if warning:
        append_tool_warning(
            result,
            warning,
            "OpenCode failed while using Panda-managed XDG_DATA_HOME; inspect the recorded "
            "opencode data dir and retry after clearing stale runtime state.",
        )


def run_session_tools(
    commands: dict[str, list[str]],
    run_cwd: Path,
    timeout: int,
    straggler_timeout: int,
    dry_run: bool,
    output_dir: Path,
    json_tools: set[str],
    env_overrides: Optional[dict[str, dict[str, str]]] = None,
) -> dict[str, dict]:
    if dry_run:
        results = {}
        for name, command in commands.items():
            result = run_tool(
                name,
                command,
                run_cwd,
                timeout,
                dry_run=True,
                env_overrides=(env_overrides or {}).get(name),
            )
            result["status"] = "dry_run"
            result["timeout_kind"] = None
            results[name] = result
        return results

    results: dict[str, dict] = {}
    processes: dict[str, subprocess.Popen] = {}
    files = []
    starts: dict[str, float] = {}

    try:
        for name, command in commands.items():
            stdout_path = output_dir / f"{name}.stdout"
            stderr_path = output_dir / f"{name}.stderr"
            stdout_file = stdout_path.open("w", encoding="utf-8")
            stderr_file = stderr_path.open("w", encoding="utf-8")
            files.extend([stdout_file, stderr_file])
            started_at = now_iso()
            tool_env = (env_overrides or {}).get(name)
            results[name] = session_result(name, command, run_cwd, started_at, stdout_path, stderr_path, tool_env)
            try:
                process = popen_process(command, run_cwd, stdout_file, stderr_file, tool_env)
            except OSError as exc:
                stderr_file.write(f"Failed to launch {name}: {exc}\n")
                stderr_file.flush()
                results[name].update({
                    "returncode": -1,
                    "status": "failed",
                    "stderr": f"Failed to launch {name}: {exc}",
                    "finished_at": now_iso(),
                })
                normalize_result_metadata(results[name])
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
    finally:
        try:
            for process in list(processes.values()):
                try:
                    terminate_process(process)
                except Exception:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
        finally:
            processes.clear()
            for file_obj in files:
                if not file_obj.closed:
                    file_obj.close()

    for result in results.values():
        finalize_session_result(result, json_tools)
        normalize_result_metadata(result)
    return results


def run_session_command_group(
    commands: dict[str, list[str]],
    run_cwd: Path,
    timeout: int,
    straggler_timeout: int,
    dry_run: bool,
    output_dir: Path,
    json_tools: set[str],
    env_overrides: Optional[dict[str, dict[str, str]]] = None,
) -> dict[str, dict]:
    raw_results = {}
    for name, command in commands.items():
        raw_results.update(run_session_tools(
            {name: command},
            run_cwd,
            timeout,
            straggler_timeout,
            dry_run,
            output_dir,
            {name} if name in json_tools else set(),
            env_overrides,
        ))
    return raw_results


def run_session_commands(
    commands: dict[str, list[str]],
    run_cwd: Path,
    timeout: int,
    straggler_timeout: int,
    dry_run: bool,
    output_dir: Path,
    json_tools: set[str],
    run_parallel: bool,
    serialize_opencode: bool,
    opencode_backed_names: Optional[set[str]] = None,
    env_overrides: Optional[dict[str, dict[str, str]]] = None,
) -> dict[str, dict]:
    if not run_parallel:
        return run_session_command_group(
            commands,
            run_cwd,
            timeout,
            straggler_timeout,
            dry_run,
            output_dir,
            json_tools,
            env_overrides,
        )
    if not serialize_opencode:
        return run_session_tools(commands, run_cwd, timeout, straggler_timeout, dry_run, output_dir, json_tools, env_overrides)

    raw_results = {}
    parallel_commands, serialized_opencode = split_serialized_opencode_commands(commands, opencode_backed_names)
    with ThreadPoolExecutor(max_workers=max(1, len(parallel_commands) + bool(serialized_opencode))) as executor:
        futures = {}
        if parallel_commands:
            futures[
                executor.submit(
                    run_session_tools,
                    parallel_commands,
                    run_cwd,
                    timeout,
                    straggler_timeout,
                    dry_run,
                    output_dir,
                    json_tools.intersection(parallel_commands),
                    env_overrides,
                )
            ] = "__parallel__"
        if serialized_opencode:
            futures[
                executor.submit(
                    run_session_command_group,
                    serialized_opencode,
                    run_cwd,
                    timeout,
                    straggler_timeout,
                    dry_run,
                    output_dir,
                    json_tools.intersection(serialized_opencode),
                    env_overrides,
                )
            ] = "__serialized_opencode__"
        for future in as_completed(futures):
            try:
                raw_results.update(future.result())
            except Exception as exc:
                failed_commands = parallel_commands if futures[future] == "__parallel__" else serialized_opencode
                for name, command in failed_commands.items():
                    raw_results[name] = failed_tool_result(name, command, run_cwd, exc)
    return raw_results


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


def env_flag_enabled(name: str) -> bool:
    value = os.environ.get(name)
    return isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "on"}


def session_memory_disabled(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "no_session_memory", False)) or env_flag_enabled("PANDA_NO_SESSION_MEMORY")


def compact_json_context(data: dict, max_chars: int) -> str:
    text = json.dumps(data, indent=2)
    value, _ = truncate_text(text, max_chars)
    return str(value)


def load_previous_turn_memory(session: dict, session_path: Path) -> tuple[Optional[str], dict]:
    session_id = session.get("session_id")
    previous_turn = int(session.get("turn_count", 0))
    info = {
        "enabled": True,
        "injected": False,
        "skip_reason": None,
        "summary_path": None,
        "characters": 0,
    }
    if previous_turn < 1:
        info["skip_reason"] = "no_previous_turn"
        return None, info
    summary_path = next_turn_dir(session_path, previous_turn) / "turn_summary.json"
    info["summary_path"] = str(summary_path)
    try:
        summary = read_json(summary_path)
    except (OSError, json.JSONDecodeError):
        info["skip_reason"] = "summary_unreadable"
        return None, info
    if summary.get("schema_version") != SCHEMA_VERSION:
        info["skip_reason"] = "wrong_schema"
        return None, info
    if summary.get("session_id") != session_id:
        info["skip_reason"] = "wrong_session"
        return None, info
    if summary.get("turn") != previous_turn:
        info["skip_reason"] = "wrong_turn"
        return None, info
    if summary.get("truncated"):
        info["skip_reason"] = "summary_truncated"
        return None, info
    if not summary.get("turn_status") and not summary.get("findings"):
        info["skip_reason"] = "empty_summary"
        return None, info
    context = compact_json_context(summary, SESSION_MEMORY_MAX_CHARS)
    info["injected"] = True
    info["characters"] = len(context)
    return context, info


def load_previous_turn_context(session: dict, session_path: Path) -> Optional[str]:
    context, _info = load_previous_turn_memory(session, session_path)
    return context


def add_session_memory_to_prompt(user_prompt: str, context: Optional[str]) -> str:
    if not context:
        return user_prompt
    return (
        f"{user_prompt}\n\n"
        f"Previous Panda turn summary (capped at {SESSION_MEMORY_MAX_CHARS} characters):\n"
        f"{context}"
    )


def make_turn_summary(
    session_id: str,
    turn_number: int,
    current_turn_status: str,
    stopping_suggestion: Optional[str],
    evidence: dict,
) -> dict:
    compact_findings = []
    for finding in evidence.get("findings", []):
        if not isinstance(finding, dict):
            continue
        compact_findings.append({
            "tool": finding.get("tool"),
            "status": finding.get("status"),
            "recommendation": finding.get("recommendation"),
            "risks": finding.get("risks"),
            "verification_plan": finding.get("verification_plan"),
            "raw_output_path": finding.get("raw_output_path"),
            "truncated": finding.get("truncated", False),
        })
    summary = {
        "schema_version": SCHEMA_VERSION,
        "session_id": session_id,
        "turn": turn_number,
        "created_at": now_iso(),
        "turn_status": current_turn_status,
        "stopping_suggestion": stopping_suggestion,
        "findings": compact_findings,
    }
    return enforce_artifact_limit(summary, SESSION_MEMORY_MAX_CHARS)


def run_session(args: argparse.Namespace, user_prompt: str) -> int:
    workspace = args.workspace.resolve()
    created = not bool(args.session)
    if created:
        session_id = str(uuid.uuid4())
        session_path = session_root(args) / session_id
    else:
        validate_session_id(args.session)
        session_path = session_root(args) / args.session
        if not (session_path / "session.json").exists():
            raise SystemExit(f"Session not found: {args.session}")

    with SessionLock(session_path):
        recovery_notes: list[str] = []
        if created:
            session = new_session_state(args, workspace, session_path.name)
            memory_info = {
                "enabled": not session_memory_disabled(args),
                "injected": False,
                "skip_reason": "new_session",
                "summary_path": None,
                "characters": 0,
            }
        else:
            session, recovery_notes = load_existing_session(session_path)
            if mode_is_disabled(session.get("mode")):
                raise SystemExit(PATCH_MODE_DISABLED_MESSAGE)
            inherit_session_args(args, session)
            if session_memory_disabled(args):
                previous_context = None
                memory_info = {
                    "enabled": False,
                    "injected": False,
                    "skip_reason": "disabled",
                    "summary_path": None,
                    "characters": 0,
                }
            else:
                previous_context, memory_info = load_previous_turn_memory(session, session_path)
                memory_info["enabled"] = True
        reject_disabled_mode(args.mode)

        if created or session_memory_disabled(args):
            previous_context = None
        prompt_user_context = add_session_memory_to_prompt(user_prompt, previous_context)
        prompt = consultation_prompt(
            args.mode,
            args.role,
            args.approval_mode,
            prompt_user_context,
            args.protocol,
            args.privacy_mode,
        )
        session_path.mkdir(parents=True, exist_ok=True)
        turn_number, turn_dir = next_available_turn_dir(
            session_path,
            int(session.get("turn_count", 0)) + 1,
            recovery_notes,
        )
        turn_dir.mkdir(parents=True, exist_ok=False)
        write_text_atomic(turn_dir / "prompt.txt", prompt + "\n")

        isolated_cwd = session_path / "isolated-cwd"
        isolated_cwd.mkdir(exist_ok=True)
        run_cwd = isolated_cwd if args.mode == "advisory" else workspace

        export_contract = build_export_contract(args, prompt, workspace, run_cwd)
        if args.export_manifest:
            export_contract = validate_supplied_export_manifest(export_contract, args.export_manifest)
        export_manifest_path = write_export_contract(turn_dir, export_contract)

        enforce_private_context_export_policy(args)
        enforce_codex_reviewer_export_policy(args)

        commands, json_tools = build_commands(args, prompt, run_cwd, session=session)
        profile_metadata = profile_manifest_metadata(args)
        session.update({
            "updated_at": now_iso(),
            "status": "running",
            "runner_pid": os.getpid(),
            "runner_started_at": now_iso(),
            "workspace": str(workspace),
            "tool": args.tool,
            "tool_selector": args.tool,
            "tool_selection_source": tool_selection_source(args),
            "mode": args.mode,
            "role": args.role,
            "approval_mode": args.approval_mode,
            "privacy_mode": args.privacy_mode,
            "execution": args.execution,
            "requested_tools": requested_tools(args.tool, args),
            "models": dict(profile_metadata["effective_models"]),
            "profile": profile_metadata["profile"],
            "profile_source": profile_metadata["profile_source"],
            "cost_tier": profile_metadata["cost_tier"],
            "effective_models": dict(profile_metadata["effective_models"]),
            "active_models": dict(profile_metadata["active_models"]),
            "effective_effort": dict(profile_metadata["effective_effort"]),
            "requested_models": dict(profile_metadata["requested_models"]),
            "requested_effort": dict(profile_metadata["requested_effort"]),
            "effort_support": dict(profile_metadata["effort_support"]),
            "applied_effort": dict(profile_metadata["applied_effort"]),
            "preferences": preference_manifest_metadata(args),
            "agents": [dict(agent) for agent in getattr(args, "configured_agents", [])],
        })
        session["protocol"] = args.protocol
        if recovery_notes:
            session["recovery_notes"] = recovery_notes
        write_json(session_path / "session.json", session)

        run_parallel = should_run_parallel(args.execution, args.mode, len(commands))
        serialize_opencode = opencode_serialization_enabled(args)
        env_overrides = build_tool_env_overrides(
            args,
            commands,
            opencode_xdg_data_home(session_path),
        )
        raw_results = run_session_commands(
            commands,
            run_cwd,
            args.timeout,
            args.straggler_timeout,
            args.dry_run,
            turn_dir,
            json_tools,
            run_parallel,
            serialize_opencode,
            opencode_backed_command_names(args),
            env_overrides,
        )

        for name in json_tools:
            if name in raw_results and raw_results[name].get("tool_session_id"):
                session.setdefault("tool_session_ids", {})[name] = raw_results[name]["tool_session_id"]

        for result in raw_results.values():
            normalize_result_metadata(result)

        results = []
        for name in commands:
            result = raw_results[name]
            results.append({key: value for key, value in result.items() if key not in {"stdout", "stderr"}})
            write_response(turn_dir, result)

        artifact_info = write_run_artifacts(turn_dir, raw_results, commands)
        protocol_artifact_paths = write_protocol_artifacts(
            turn_dir,
            raw_results,
            commands,
            protocol=args.protocol,
            role=args.role,
        )
        artifact_paths = {
            "evidence": artifact_info["evidence_path"],
            "summaries": artifact_info["summary_paths"],
            "export_manifest": export_manifest_path,
        }
        if protocol_artifact_paths:
            artifact_paths.update(protocol_artifact_paths)

        stopping_suggestion = latest_stopping_suggestion(raw_results.values())
        current_turn_status = turn_status(raw_results.values())
        turn_summary = make_turn_summary(
            session["session_id"],
            turn_number,
            current_turn_status,
            stopping_suggestion,
            artifact_info["evidence"],
        )
        write_limited_json(turn_dir / "turn_summary.json", turn_summary, SESSION_MEMORY_MAX_CHARS)
        session.update({
            "updated_at": now_iso(),
            "status": "waiting_for_user",
            "runner_pid": None,
            "turn_count": turn_number,
            "last_turn_status": current_turn_status,
            "latest_stopping_suggestion": stopping_suggestion,
        })

        recovery_warnings = [{"tool": None, "code": note} for note in recovery_notes]
        extra_warnings = recovery_warnings + tool_selection_warnings(args)
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "session_id": session["session_id"],
            "session_created": created,
            "turn": turn_number,
            "mode": args.mode,
            "execution": "parallel" if run_parallel else "sequential",
            "requested_execution": args.execution,
            "approval_mode": args.approval_mode,
            "privacy_mode": args.privacy_mode,
            "role": args.role,
            "dry_run": args.dry_run,
            "output_dir": str(turn_dir),
            "session_dir": str(session_path),
            "workspace": str(workspace),
            "tool": args.tool,
            "tool_selector": args.tool,
            "tool_selection_source": tool_selection_source(args),
            "run_cwd": str(run_cwd),
            "requested_tools": requested_tools(args.tool, args),
            "serialize_opencode": serialize_opencode,
            "export_manifest": export_contract,
            "export_manifest_path": export_manifest_path,
            "preferences": preference_manifest_metadata(args),
            "agents": [dict(agent) for agent in getattr(args, "configured_agents", [])],
            "session_memory": memory_info,
            "recovery_notes": recovery_notes,
            "straggler_timeout": args.straggler_timeout,
            "timeout": args.timeout,
            "turn_status": current_turn_status,
            "stopping_suggestion": stopping_suggestion,
            "tools": results,
            "telemetry": build_telemetry(
                raw_results,
                artifact_paths,
                prompt=prompt,
                extra_warnings=extra_warnings,
                opencode_runtime=opencode_runtime_metadata(env_overrides),
            ),
        }
        manifest["protocol"] = args.protocol
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
    if args.show_preferences:
        return show_preferences(args)
    if args.reset_preferences:
        return reset_preferences(args)
    if args.save_preferences:
        return save_preferences(args)
    user_prompt = read_prompt(args)
    if args.session is not None:
        return run_session(args, user_prompt)
    prompt = consultation_prompt(
        args.mode,
        args.role,
        args.approval_mode,
        user_prompt,
        args.protocol,
        args.privacy_mode,
    )
    return run_one_shot(args, prompt)


if __name__ == "__main__":
    sys.exit(main())
