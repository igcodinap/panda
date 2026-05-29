#!/usr/bin/env python3
"""Create and score small Panda evaluation runs."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import shlex
import subprocess
import sys
import tempfile
import time
from typing import Iterable, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from panda_v2.contracts import CONTRACTS_FILENAME, FALSIFIER_FILENAME
from panda_v2.prompts import contract_first_v2_addendum, falsifier_user_prompt


SCHEMA_VERSION = 1
LEGACY_VARIANTS = ("codex_alone", "panda_explore")
SCOUT_VARIANT = "codex_alone_scout"
REPLAY_VARIANT = "panda_replay"
SECOND_PASS_VARIANT = "panda_replay_second_pass"
HARD_LOCAL_VARIANTS = (SCOUT_VARIANT, REPLAY_VARIANT, SECOND_PASS_VARIANT)
VARIANTS = (*LEGACY_VARIANTS, *HARD_LOCAL_VARIANTS)
PANDA_RESULT_VARIANTS = {"panda_explore", REPLAY_VARIANT, SECOND_PASS_VARIANT}
ADVICE_QUALITY_FIELDS = (
    "panda_direction_correct",
    "panda_missed_contract",
    "codex_implementation_error",
    "evidence_was_actionable",
)
SCOUT_STRUGGLE_CLASSIFICATIONS = {
    "failed_tests",
    "timeout",
    "no_patch",
    "low_confidence",
    "slow_solve",
}
RESULT_CLASSIFICATIONS = (
    "accepted",
    "failed_tests",
    "timeout",
    "no_patch",
    "environment_failure",
    "low_confidence",
    "slow_solve",
    "contaminated",
)
DEFAULT_PROFILE = "fast"
DEFAULT_TIMEOUT = 420
HARD_LOCAL_TIMEOUT = 600
DEFAULT_STRAGGLER_TIMEOUT = 120
DEFAULT_RUN_ROOT = Path(tempfile.gettempdir()) / "panda-eval"
REQUIRED_PANDA_TOOLS = ("claude", "opencode", "qwen")
PANDA_TOOL_CHOICES = ("claude", "opencode", "qwen", "codex", "all", "auto")
HARD_LOCAL_TARGET_COUNT = 20
HARD_LOCAL_EXPANSION_COUNT = 10
HARD_LOCAL_MAX_COUNT = 30
HARD_LOCAL_REPO_CAP = 3
FIRST_PASS_TASK_MAX_CHARS = 4000
FIRST_PASS_PROMPT_MAX_CHARS = 32000
SECOND_PASS_TASK_MAX_CHARS = 2000
SECOND_PASS_EVIDENCE_MAX_CHARS = 8000
SECOND_PASS_PATCH_MAX_CHARS = 8000
SECOND_PASS_PATCH_MAX_LINES = 200
SECOND_PASS_TEST_MAX_CHARS = 8000
SECOND_PASS_FAILURE_CONTEXT_LINES = 3
SECOND_PASS_PROMPT_MAX_CHARS = 48000
FALSIFIER_CONTRACTS_MAX_CHARS = 12000
FALSIFIER_PROMPT_MAX_CHARS = 48000
FALSIFIER_VARIANT = "panda_falsifier"
WORKSPACE_IGNORE_NAMES = frozenset({
    ".git",
    ".gitmodules",
    ".hg",
    ".svn",
    "__pycache__",
    ".cache",
    ".gradle",
    ".mypy_cache",
    ".nox",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    ".next",
    ".turbo",
    "coverage",
    "node_modules",
    "target",
    "venv",
})
GOLD_BENCHMARK_FIELDS = frozenset({"patch", "test_patch", "FAIL_TO_PASS"})
COMMIT_SHA_PATTERN = re.compile(r"\b[0-9a-f]{40}\b", re.IGNORECASE)
HARD_DATASET_SOURCES = (
    ("swebench_pro", "ScaleAI/SWE-bench_Pro", "test"),
    ("swebench_full", "princeton-nlp/SWE-bench", "test"),
    ("swebench_full", "SWE-bench/SWE-bench", "test"),
    ("swebench_verified", "SWE-bench/SWE-bench_Verified", "test"),
    ("swebench_verified", "princeton-nlp/SWE-bench_Verified", "test"),
)
BUDGET_FAILURE_PATTERNS = (
    r"\b429\b",
    r"auth(?:entication)?(?:\s+failed|\s+error|\s+required)",
    r"billing(?:\s+error|\s+issue|\s+required|\s+limit)",
    r"budget(?:\s+exceeded|\s+exhausted|\s+limit|\s+reached)",
    r"credit(?:s)?(?:\s+exhausted|\s+depleted|\s+insufficient)",
    r"exceeded\s+(?:quota|usage|rate|billing|budget)",
    r"login required",
    r"quota(?:\s+exceeded|\s+exhausted|\s+limit|\s+reached)",
    r"rate[\s_-]*limit",
    r"too many requests",
    r"usage(?:\s+limit|\s+quota)(?:\s+exceeded|\s+reached)?",
)
DEFAULT_TASKS = [
    {
        "task_id": "astropy__astropy-14995",
        "dataset": "SWE-bench_Lite",
        "repo_hint": "astropy/astropy",
        "source": "OpenHands SWE-bench Lite public report",
    },
    {
        "task_id": "django__django-11099",
        "dataset": "SWE-bench_Lite",
        "repo_hint": "django/django",
        "source": "OpenHands SWE-bench Lite public report",
    },
    {
        "task_id": "matplotlib__matplotlib-23562",
        "dataset": "SWE-bench_Lite",
        "repo_hint": "matplotlib/matplotlib",
        "source": "OpenHands SWE-bench Lite public report",
    },
    {
        "task_id": "pytest-dev__pytest-5227",
        "dataset": "SWE-bench_Lite",
        "repo_hint": "pytest-dev/pytest",
        "source": "OpenHands SWE-bench Lite public report",
    },
    {
        "task_id": "sympy__sympy-13480",
        "dataset": "SWE-bench_Lite",
        "repo_hint": "sympy/sympy",
        "source": "OpenHands SWE-bench Lite public report",
    },
]


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def consult_runner() -> Path:
    return repo_root() / "scripts" / "consult_ai_team.py"


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def today_stamp(eval_mode: str = "nightly") -> str:
    return dt.datetime.now().strftime(f"%Y%m%d-{eval_mode}")


def default_run_dir(eval_mode: str = "nightly") -> Path:
    return DEFAULT_RUN_ROOT / today_stamp(eval_mode)


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_json_safe(path: Path) -> tuple[Optional[dict], Optional[str]]:
    try:
        return read_json(path), None
    except OSError as exc:
        return None, f"missing:{path}:{exc}"
    except json.JSONDecodeError as exc:
        return None, f"malformed:{path}:{exc}"


def git_commit() -> Optional[str]:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root()),
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def load_tasks(path: Optional[Path], *, allow_empty: bool = False) -> list[dict]:
    if path is None:
        return [dict(task) for task in DEFAULT_TASKS]
    data = read_json(path)
    tasks = data.get("tasks") if isinstance(data, dict) else data
    if not isinstance(tasks, list):
        raise SystemExit("Task file must contain a task list.")
    if not tasks and not allow_empty:
        raise SystemExit("Task file must contain at least one task.")
    if len(tasks) > HARD_LOCAL_MAX_COUNT:
        raise SystemExit(f"Task file must contain at most {HARD_LOCAL_MAX_COUNT} tasks.")
    for task in tasks:
        if not isinstance(task, dict) or not task.get("task_id"):
            raise SystemExit("Each task must be an object with task_id.")
    return tasks


def load_records_file(path: Path) -> list[dict]:
    if path.suffix == ".jsonl":
        records = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
        return records
    data = read_json(path)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("records", "tasks", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    raise SystemExit(f"Could not find records in {path}")


def parse_list_field(value: object) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def patch_stats(patch_text: object) -> dict:
    text = patch_text if isinstance(patch_text, str) else ""
    files = set()
    changed_lines = 0
    for line in text.splitlines():
        match = re.match(r"diff --git a/(.*?) b/(.*)", line)
        if match:
            files.add(match.group(2))
            continue
        if (line.startswith("+") and not line.startswith("+++")) or (
            line.startswith("-") and not line.startswith("---")
        ):
            changed_lines += 1
    return {
        "changed_file_count": len(files),
        "changed_line_count": changed_lines,
    }


def repo_hint_for_record(record: dict) -> str:
    repo = record.get("repo")
    if isinstance(repo, str) and repo:
        return repo
    task_id = record.get("instance_id") or record.get("task_id") or ""
    if "__" in task_id:
        owner, repo_name = task_id.split("__", 1)
        return f"{owner}/{repo_name.rsplit('-', 1)[0]}"
    return ""


def hardness_metadata(record: dict, source_name: str, dataset_name: str, split: str) -> dict:
    patch = patch_stats(record.get("patch"))
    test_patch = patch_stats(record.get("test_patch"))
    fail_to_pass = parse_list_field(record.get("FAIL_TO_PASS"))
    problem = record.get("problem_statement") or ""
    problem_text = problem if isinstance(problem, str) else ""
    problem_lower = problem_text.lower()
    ambiguity_terms = ("regression", "compat", "performance", "concurrency", "migration", "serialization")
    traceback_like = any(term in problem_lower for term in ("traceback", "nameerror", "attributeerror"))
    obvious = (
        bool(record.get("patch"))
        and patch["changed_file_count"] <= 1
        and patch["changed_line_count"] <= 5
        and (
            traceback_like
            or "typo" in problem_lower
            or len(problem_text) < 700
        )
    )
    score = 0
    score += min(patch["changed_file_count"], 5) * 5
    score += min(patch["changed_line_count"], 120) / 6
    score += min(test_patch["changed_file_count"], 5) * 3
    score += min(test_patch["changed_line_count"], 120) / 10
    score += min(len(fail_to_pass), 8) * 2
    score += 3 if len(problem_text) > 1500 else 0
    score += 3 if any(term in problem_lower for term in ambiguity_terms) else 0
    score -= 12 if obvious else 0
    return {
        "source": source_name,
        "dataset_name": dataset_name,
        "split": split,
        "score": round(score, 3),
        "patch_changed_files": patch["changed_file_count"],
        "patch_changed_lines": patch["changed_line_count"],
        "test_patch_changed_files": test_patch["changed_file_count"],
        "test_patch_changed_lines": test_patch["changed_line_count"],
        "fail_to_pass_count": len(fail_to_pass),
        "problem_statement_chars": len(problem_text),
        "traceback_like": traceback_like,
        "obvious_one_line_candidate": obvious,
    }


def sanitized_task_record(record: dict, hardness: dict) -> dict:
    task_id = record.get("task_id") or record.get("instance_id")
    task = {
        "task_id": task_id,
        "dataset": hardness["source"],
        "dataset_name": hardness["dataset_name"],
        "split": hardness["split"],
        "repo_hint": repo_hint_for_record(record),
        "base_commit": record.get("base_commit"),
        "problem_statement": record.get("problem_statement"),
        "hardness": hardness,
        "contamination": False,
    }
    if record.get("hints_text"):
        task["hints_text_available"] = True
    return task


def source_matches(selected_source: str, source_name: str) -> bool:
    return selected_source == "auto" or selected_source == source_name


def load_hf_records(dataset_name: str, split: str, *, allow_network: bool) -> list[dict]:
    try:
        from datasets import load_dataset
    except ImportError as exc:
        raise RuntimeError("Python package 'datasets' is not installed.") from exc
    kwargs = {"split": split}
    if not allow_network:
        kwargs["download_mode"] = "reuse_cache_if_exists"
    dataset = load_dataset(dataset_name, **kwargs)
    return [dict(row) for row in dataset]


def load_selector_records(args: argparse.Namespace) -> tuple[list[dict], str, str, str, list[str]]:
    warnings = []
    if args.records_file:
        return (
            load_records_file(args.records_file.expanduser()),
            "records_file",
            str(args.records_file.expanduser()),
            "local",
            warnings,
        )
    for source_name, dataset_name, split in HARD_DATASET_SOURCES:
        if not source_matches(args.source, source_name):
            continue
        try:
            records = load_hf_records(dataset_name, split, allow_network=args.allow_network)
        except Exception as exc:  # pragma: no cover - exact HF failure types vary.
            warnings.append(f"{dataset_name}:{type(exc).__name__}:{exc}")
            continue
        return records, source_name, dataset_name, split, warnings
    tried = ", ".join(dataset for _, dataset, _ in HARD_DATASET_SOURCES)
    raise SystemExit(f"No hard-task source could be loaded. Tried: {tried}. Warnings: {warnings}")


def select_hard_records(
    records: list[dict],
    *,
    source_name: str,
    dataset_name: str,
    split: str,
    target_count: int,
    repo_cap: int,
) -> tuple[list[dict], dict]:
    scored = []
    rejected_obvious = 0
    missing_task_id = 0
    for record in records:
        task_id = record.get("task_id") or record.get("instance_id")
        if not task_id:
            missing_task_id += 1
            continue
        hardness = hardness_metadata(record, source_name, dataset_name, split)
        if hardness["obvious_one_line_candidate"]:
            rejected_obvious += 1
            continue
        scored.append((hardness["score"], task_id, record, hardness))
    scored.sort(key=lambda item: (-item[0], item[1]))

    selected = []
    repo_counts: dict[str, int] = {}
    for _, _, record, hardness in scored:
        repo = repo_hint_for_record(record) or "unknown"
        if repo_counts.get(repo, 0) >= repo_cap:
            continue
        selected.append(sanitized_task_record(record, hardness))
        repo_counts[repo] = repo_counts.get(repo, 0) + 1
        if len(selected) >= target_count:
            break
    diagnostics = {
        "records_seen": len(records),
        "records_scored": len(scored),
        "selected_count": len(selected),
        "target_count": target_count,
        "repo_cap": repo_cap,
        "rejected_obvious_count": rejected_obvious,
        "missing_task_id_count": missing_task_id,
        "repo_counts": repo_counts,
    }
    return selected, diagnostics


def update_manifest_selected_tasks(run_dir: Path, tasks: list[dict], selector: dict) -> None:
    manifest_path = run_dir / "run_manifest.json"
    if not manifest_path.exists():
        return
    manifest = read_json(manifest_path)
    manifest["task_ids"] = [task["task_id"] for task in tasks]
    manifest["task_selection"] = selector
    manifest.setdefault("success_bar", {})["external_task_count"] = len(tasks)
    write_json(manifest_path, manifest)


def select_hard_tasks(args: argparse.Namespace) -> int:
    if args.target_count < 1 or args.target_count > args.max_count:
        raise SystemExit("--target-count must be between 1 and --max-count.")
    if args.max_count > HARD_LOCAL_MAX_COUNT:
        raise SystemExit(f"--max-count must be at most {HARD_LOCAL_MAX_COUNT}.")
    run_dir = args.run_dir.expanduser() if args.run_dir else default_run_dir("hard-local")
    run_dir.mkdir(parents=True, exist_ok=True)
    records, source_name, dataset_name, split, warnings = load_selector_records(args)
    selected, diagnostics = select_hard_records(
        records,
        source_name=source_name,
        dataset_name=dataset_name,
        split=split,
        target_count=args.target_count,
        repo_cap=args.repo_cap,
    )
    selector = {
        "schema_version": SCHEMA_VERSION,
        "created_at": now_iso(),
        "source": source_name,
        "dataset_name": dataset_name,
        "split": split,
        "allow_network": bool(args.allow_network),
        "fallback_source_order": [
            {"source": source, "dataset_name": dataset, "split": dataset_split}
            for source, dataset, dataset_split in HARD_DATASET_SOURCES
        ],
        "diagnostics": diagnostics,
        "warnings": warnings,
        "gold_leakage_policy": "patch and test_patch content omitted; only numeric hardness metadata is written",
    }
    tasks_payload = {"schema_version": SCHEMA_VERSION, "tasks": selected}
    output_file = args.output_file.expanduser() if args.output_file else run_dir / "hard_candidates.json"
    write_json(output_file, tasks_payload)
    write_json(run_dir / "tasks.json", tasks_payload)
    write_json(run_dir / "candidate_manifest.json", selector)
    update_manifest_selected_tasks(run_dir, selected, selector)
    print(output_file)
    return 0


def init_run(args: argparse.Namespace) -> int:
    eval_mode = args.eval_mode
    run_dir = args.run_dir.expanduser() if args.run_dir else default_run_dir(eval_mode)
    if run_dir.exists() and any(run_dir.iterdir()) and not args.force:
        raise SystemExit(f"Run directory already exists and is not empty: {run_dir}")
    tasks = [] if eval_mode == "hard-local" and args.tasks_file is None else load_tasks(
        args.tasks_file,
        allow_empty=eval_mode == "hard-local",
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    write_json(run_dir / "tasks.json", {"schema_version": SCHEMA_VERSION, "tasks": tasks})
    timeout = args.timeout
    if timeout is None:
        timeout = HARD_LOCAL_TIMEOUT if eval_mode == "hard-local" else DEFAULT_TIMEOUT
    variants = HARD_LOCAL_VARIANTS if eval_mode == "hard-local" else LEGACY_VARIANTS
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "eval_mode": eval_mode,
        "created_at": now_iso(),
        "panda_commit": git_commit(),
        "task_ids": [task["task_id"] for task in tasks],
        "variants": list(variants),
        "panda": {
            "mode": "explore",
            "tool": "all",
            "profile": args.profile,
            "timeout": timeout,
            "straggler_timeout": args.straggler_timeout,
            "claude_budget_failure_counts_as_failure": True,
        },
        "success_bar": {
            "reliability_canary_artifact_failures": 0,
            "external_task_count": len(tasks) if tasks else (
                HARD_LOCAL_TARGET_COUNT if eval_mode == "hard-local" else len(DEFAULT_TASKS)
            ),
            "stop_on_budget_exhaustion": True,
        },
    }
    if eval_mode == "hard-local":
        manifest["hard_local"] = {
            "target_candidate_count": HARD_LOCAL_TARGET_COUNT,
            "expansion_candidate_count": HARD_LOCAL_EXPANSION_COUNT,
            "max_candidate_count": HARD_LOCAL_MAX_COUNT,
            "repo_cap": HARD_LOCAL_REPO_CAP,
            "scout_budget_minutes": 20,
            "max_test_loops": 3,
            "panda_replay_limit": 8,
            "primary_metric": "failure_to_success_rescue_rate",
        }
    write_json(run_dir / "run_manifest.json", manifest)
    write_json(run_dir / "results.json", {"schema_version": SCHEMA_VERSION, "results": []})
    print(run_dir)
    return 0


def bool_arg(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "y", "on"}:
        return True
    if lowered in {"0", "false", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError("expected true or false")


def contains_budget_failure(text: str) -> bool:
    lowered = text.lower()
    return any(re.search(pattern, lowered) for pattern in BUDGET_FAILURE_PATTERNS)


def result_dir(run_dir: Path, task_id: str, variant: str) -> Path:
    return run_dir / "tasks" / task_id / variant


def safe_task_dir(run_dir: Path, task_id: str) -> Path:
    return run_dir / "tasks" / safe_path_slug(task_id)


def safe_result_dir(run_dir: Path, task_id: str, variant: str) -> Path:
    return safe_task_dir(run_dir, task_id) / variant


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def clip_text(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    marker = "\n[truncated]\n"
    keep = max(0, max_chars - len(marker))
    return text[:keep].rstrip() + marker, True


def redact_commit_shas(text: str) -> tuple[str, int]:
    matches = COMMIT_SHA_PATTERN.findall(text)
    return COMMIT_SHA_PATTERN.sub("[redacted-sha]", text), len(matches)


def safe_path_slug(value: str) -> str:
    redacted, _ = redact_commit_shas(value)
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", redacted).strip("._")
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
    return f"{slug or 'task'}-{digest}"


def path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def workspace_ignore(_: str, names: list[str]) -> set[str]:
    return {name for name in names if name in WORKSPACE_IGNORE_NAMES}


def count_workspace_files(workspace: Path) -> tuple[int, int]:
    file_count = 0
    total_bytes = 0
    for path in workspace.rglob("*"):
        try:
            stat = path.lstat()
        except OSError:
            continue
        if path.is_dir() and not path.is_symlink():
            continue
        file_count += 1
        total_bytes += stat.st_size
    return file_count, total_bytes


def sensitive_strings_for_task(task: dict) -> list[str]:
    values = [task.get("task_id"), task.get("base_commit")]
    strings = []
    for value in values:
        if isinstance(value, str):
            strings.extend(COMMIT_SHA_PATTERN.findall(value))
    return sorted(set(strings))


def read_small_text_file(path: Path, max_bytes: int = 1_000_000) -> Optional[str]:
    try:
        if path.lstat().st_size > max_bytes:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeError):
        return None


def check_workspace_leakage(
    workspace: Path,
    *,
    sensitive_strings: Optional[Iterable[str]] = None,
) -> dict:
    workspace = workspace.expanduser()
    violations = []
    warnings = []
    sensitive = sorted({value for value in (sensitive_strings or []) if value})
    checked_at = now_iso()

    if not workspace.exists():
        return {
            "schema_version": SCHEMA_VERSION,
            "checked_at": checked_at,
            "workspace": str(workspace),
            "isolation_status": "missing",
            "isolated": False,
            "violations": [f"missing_workspace:{workspace}"],
            "warnings": warnings,
            "file_count": 0,
            "byte_count": 0,
            "sensitive_strings_checked": bool(sensitive),
        }
    if not workspace.is_dir():
        return {
            "schema_version": SCHEMA_VERSION,
            "checked_at": checked_at,
            "workspace": str(workspace),
            "isolation_status": "invalid",
            "isolated": False,
            "violations": [f"not_a_directory:{workspace}"],
            "warnings": warnings,
            "file_count": 0,
            "byte_count": 0,
            "sensitive_strings_checked": bool(sensitive),
        }

    root = workspace.resolve()
    for git_path in workspace.rglob(".git"):
        kind = "directory" if git_path.is_dir() else "file"
        violations.append(f"git_{kind}:{git_path.relative_to(workspace)}")
    for gitmodules_path in workspace.rglob(".gitmodules"):
        violations.append(f"gitmodules_trace:{gitmodules_path.relative_to(workspace)}")

    for path in workspace.rglob("*"):
        if not path.is_symlink():
            continue
        try:
            raw_target = os.readlink(path)
        except OSError as exc:
            violations.append(f"unreadable_symlink:{path.relative_to(workspace)}:{exc}")
            continue
        target_path = Path(raw_target)
        if not target_path.is_absolute():
            target_path = path.parent / target_path
        resolved_target = target_path.resolve(strict=False)
        if not path_is_relative_to(resolved_target, root):
            violations.append(f"out_of_tree_symlink:{path.relative_to(workspace)}")

    for json_path in workspace.rglob("*.json"):
        data, warning = read_json_safe(json_path)
        if warning:
            warnings.append(warning)
            continue
        if not isinstance(data, dict):
            continue
        leaked_fields = sorted(GOLD_BENCHMARK_FIELDS.intersection(data.keys()))
        if leaked_fields:
            violations.append(
                f"gold_benchmark_fields:{json_path.relative_to(workspace)}:{','.join(leaked_fields)}"
            )

    if sensitive:
        for path in workspace.rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue
            text = read_small_text_file(path)
            if text is None:
                continue
            for value in sensitive:
                if value in text:
                    violations.append(f"sensitive_commit_string:{path.relative_to(workspace)}")
                    break

    file_count, byte_count = count_workspace_files(workspace)
    isolated = not violations
    return {
        "schema_version": SCHEMA_VERSION,
        "checked_at": checked_at,
        "workspace": str(workspace),
        "isolation_status": "clean" if isolated else "leakage_detected",
        "isolated": isolated,
        "violations": violations,
        "warnings": warnings,
        "file_count": file_count,
        "byte_count": byte_count,
        "sensitive_strings_checked": bool(sensitive),
    }


def prepare_workspace(args: argparse.Namespace) -> int:
    run_dir = args.run_dir.expanduser()
    source = args.source_workspace.expanduser()
    destination = args.output_dir.expanduser() if args.output_dir else (
        safe_task_dir(run_dir, args.task_id) / "workspace"
    )
    if not source.exists() or not source.is_dir():
        raise SystemExit(f"Missing source workspace: {source}")
    if destination.exists():
        raise SystemExit(f"Destination workspace already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    ignored_patterns = sorted(WORKSPACE_IGNORE_NAMES)
    shutil.copytree(source, destination, ignore=workspace_ignore, symlinks=True)
    sensitive = [args.target_commit] if args.target_commit else []
    leakage = check_workspace_leakage(destination, sensitive_strings=sensitive)
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "created_at": now_iso(),
        "task_id": args.task_id,
        "source_path": str(source),
        "destination_path": str(destination),
        "ignored_patterns": ignored_patterns,
        "isolation_status": leakage["isolation_status"],
        "isolated": leakage["isolated"],
        "warnings": leakage["warnings"],
        "violations": leakage["violations"],
        "file_count": leakage["file_count"],
        "byte_count": leakage["byte_count"],
        "target_commit_checked": bool(args.target_commit),
    }
    metadata_path = destination.parent / "workspace_metadata.json"
    write_json(metadata_path, metadata)
    print(metadata_path)
    if leakage["violations"]:
        raise SystemExit("Workspace isolation check failed; see workspace_metadata.json.")
    return 0


def check_workspace_command(args: argparse.Namespace) -> int:
    sensitive = [args.target_commit] if args.target_commit else []
    result = check_workspace_leakage(args.workspace.expanduser(), sensitive_strings=sensitive)
    if args.output_file:
        write_json(args.output_file.expanduser(), result)
        print(args.output_file.expanduser())
    else:
        print(json.dumps(result, indent=2))
    return 1 if args.strict and result["violations"] else 0


def load_result_record(run_dir: Path, task_id: str, variant: str) -> Optional[dict]:
    path = result_dir(run_dir, task_id, variant) / "result.json"
    if not path.exists():
        return None
    try:
        return read_json(path)
    except json.JSONDecodeError:
        return None


def load_task_context(run_dir: Path, task_id: str) -> tuple[dict, list[str]]:
    warnings = []
    tasks_path = run_dir / "tasks.json"
    task = {"task_id": task_id}
    data, warning = read_json_safe(tasks_path)
    if warning:
        warnings.append(warning)
        return task, warnings
    tasks = data.get("tasks") if isinstance(data, dict) else []
    for candidate in tasks:
        if isinstance(candidate, dict) and candidate.get("task_id") == task_id:
            task = {
                "task_id": candidate.get("task_id"),
                "dataset": candidate.get("dataset"),
                "dataset_name": candidate.get("dataset_name"),
                "repo_hint": candidate.get("repo_hint"),
                "base_commit": candidate.get("base_commit"),
                "problem_statement": candidate.get("problem_statement"),
                "hardness": candidate.get("hardness"),
            }
            break
    return {key: value for key, value in task.items() if value is not None}, warnings


def compact_task_context(task: dict, max_chars: int) -> tuple[str, bool]:
    task_id, _ = redact_commit_shas(str(task.get("task_id")))
    repo_hint, _ = redact_commit_shas(str(task.get("repo_hint") or "unknown"))
    dataset, _ = redact_commit_shas(str(task.get("dataset") or task.get("dataset_name") or "unknown"))
    lines = [
        f"task_id: {task_id}",
        f"repo_hint: {repo_hint}",
        f"dataset: {dataset}",
    ]
    if task.get("problem_statement"):
        problem, _ = redact_commit_shas(str(task["problem_statement"]))
        lines.append("")
        lines.append("problem_statement:")
        lines.append(problem)
    return clip_text("\n".join(lines), max_chars)


def compact_first_pass_task_context(task: dict, max_chars: int) -> tuple[str, dict]:
    raw_lines = [
        f"task_id: {task.get('task_id')}",
        f"repo_hint: {task.get('repo_hint') or 'unknown'}",
        f"dataset: {task.get('dataset') or task.get('dataset_name') or 'unknown'}",
    ]
    if task.get("problem_statement"):
        raw_lines.extend(["", "problem_statement:", str(task["problem_statement"])])
    redacted, redacted_count = redact_commit_shas("\n".join(raw_lines))
    clipped, truncated = clip_text(redacted, max_chars)
    return clipped, {
        "truncated": truncated,
        "max_chars": max_chars,
        "redacted_sha_count": redacted_count,
    }


def resolve_first_pass_inputs(args: argparse.Namespace) -> dict:
    run_dir = args.run_dir.expanduser()
    output_dir = args.output_dir or safe_result_dir(run_dir, args.task_id, REPLAY_VARIANT)
    return {
        "run_dir": run_dir,
        "output_dir": output_dir.expanduser(),
        "workspace": args.workspace.expanduser(),
    }


def build_first_pass_prompt(args: argparse.Namespace) -> tuple[str, dict]:
    inputs = resolve_first_pass_inputs(args)
    task, task_warnings = load_task_context(inputs["run_dir"], args.task_id)
    task_text, task_meta = compact_first_pass_task_context(task, FIRST_PASS_TASK_MAX_CHARS)
    workspace_check = check_workspace_leakage(
        inputs["workspace"],
        sensitive_strings=sensitive_strings_for_task(task),
    )
    warnings = list(task_warnings) + workspace_check["warnings"]
    if workspace_check["violations"]:
        warnings.extend(f"workspace_leakage:{violation}" for violation in workspace_check["violations"])
    prompt = f"""You are advising Codex as an independent Panda collaborator before Codex edits a benchmark task.

Goal:
- Inspect the local workspace and produce a contract-first implementation review for Codex.
- Focus on API contracts, public/local tests, persistence or schema behavior, edge cases, and evaluator-like assertions.
- Codex remains the only editor. Inspect and advise only.

Rules:
- Do not edit files.
- Do not inspect or request gold benchmark patch, test_patch, FAIL_TO_PASS, target commit, or hidden test source.
- Use only the task context below and evidence you can find in the provided workspace.
- Treat any hidden-test expectation as an inference and label uncertainty clearly.

Task context:
{task_text}

Please inspect the workspace and return:
- Contract map: affected functions, types, endpoints, schemas, persisted fields, or integration boundaries.
- Local evidence: public/local tests, fixtures, migrations, call sites, docs, and commands that support the contract map.
- Likely evaluator assertions: what hidden or official tests may assert, phrased as inference from local evidence.
- Recommendation: the smallest implementation direction Codex should take.
- Alternative worth considering.
- Risks or edge cases.
- Falsifiers or uncertainties: what would prove this advice wrong.
- Verification plan: focused tests or commands Codex should run.
"""
    prompt_version = getattr(args, "prompt_version", 2)
    if prompt_version != 2:
        raise SystemExit("Panda V1 first-pass prompts were removed; use prompt version 2.")
    prompt = f"{prompt}\n{contract_first_v2_addendum()}"
    prompt, prompt_redacted_count = redact_commit_shas(prompt)
    prompt, prompt_truncated = clip_text(prompt, FIRST_PASS_PROMPT_MAX_CHARS)
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "created_at": now_iso(),
        "task_id": args.task_id,
        "prompt_kind": "contract_first_first_pass",
        "prompt_version": prompt_version,
        "prompt_chars": len(prompt),
        "prompt_truncated": prompt_truncated,
        "redacted_sha_count": task_meta["redacted_sha_count"] + prompt_redacted_count,
        "input_paths": {
            "run_dir": str(inputs["run_dir"]),
            "workspace": str(inputs["workspace"]),
        },
        "sections": {
            "task": task_meta,
            "prompt": {"max_chars": FIRST_PASS_PROMPT_MAX_CHARS},
        },
        "workspace_check": workspace_check,
        "warnings": warnings,
    }
    if args.strict_workspace and workspace_check["violations"]:
        metadata["strict_workspace_failed"] = True
    return prompt, metadata


def prepare_first_pass(args: argparse.Namespace) -> int:
    inputs = resolve_first_pass_inputs(args)
    output_dir = inputs["output_dir"]
    output_dir.mkdir(parents=True, exist_ok=True)
    prompt, metadata = build_first_pass_prompt(args)
    if args.strict_workspace and metadata["workspace_check"]["violations"]:
        write_json(output_dir / "prompt_metadata.json", metadata)
        raise SystemExit("Workspace leakage detected; refusing to prepare first-pass prompt.")
    prompt_path = output_dir / "panda_prompt.txt"
    prompt_path.write_text(prompt, encoding="utf-8")
    command = [
        sys.executable,
        str(consult_runner()),
        "--tool",
        args.tool,
        "--mode",
        "explore",
        "--role",
        args.role,
        "--profile",
        args.profile,
        "--timeout",
        str(args.timeout),
        "--output-dir",
        str(output_dir / "panda"),
        "--prompt-file",
        str(prompt_path),
        "--workspace",
        str(inputs["workspace"]),
    ]
    command.extend(["--protocol", "v2"])
    metadata["prompt_path"] = str(prompt_path)
    metadata["command"] = command
    write_json(output_dir / "prompt_metadata.json", metadata)
    print(shlex.join(command))
    return 0


def compact_evidence(first_pass_dir: Optional[Path], max_chars: int) -> tuple[str, dict]:
    metadata = {
        "path": str(first_pass_dir) if first_pass_dir else None,
        "truncated": False,
        "warnings": [],
    }
    if not first_pass_dir:
        metadata["warnings"].append("missing:first_pass_panda_output_dir")
        return "First-pass Panda output dir was not provided.", metadata
    evidence_path = first_pass_dir / "evidence.json"
    evidence, warning = read_json_safe(evidence_path)
    if warning:
        metadata["warnings"].append(warning)
        return f"Could not load first-pass evidence at {evidence_path}.", metadata
    lines = [f"first_pass_evidence_path: {evidence_path}"]
    for tool in REQUIRED_PANDA_TOOLS:
        summary_path = first_pass_dir / f"{tool}.summary.json"
        if summary_path.exists():
            lines.append(f"{tool}_summary_path: {summary_path}")
    lines.append("")
    findings = evidence.get("findings", []) if isinstance(evidence, dict) else []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        lines.append(f"## {finding.get('tool', 'unknown')}")
        lines.append(f"status: {finding.get('status')} timed_out: {finding.get('timed_out')}")
        if finding.get("raw_output_path"):
            lines.append(f"raw_output_path: {finding.get('raw_output_path')}")
        for key in ("recommendation", "alternative", "risks", "verification_plan"):
            value = finding.get(key)
            if value:
                clipped, was_truncated = clip_text(str(value), 1200)
                metadata["truncated"] = metadata["truncated"] or was_truncated
                lines.append(f"{key}:")
                lines.append(clipped)
        warnings = finding.get("warnings")
        if warnings:
            lines.append(f"warnings: {json.dumps(warnings)}")
        lines.append("")
    text, was_truncated = clip_text("\n".join(lines).strip(), max_chars)
    metadata["truncated"] = metadata["truncated"] or was_truncated
    return text, metadata


def compact_contracts_artifact(path: Optional[Path], max_chars: int) -> tuple[str, dict]:
    metadata = {
        "path": str(path) if path else None,
        "truncated": False,
        "warnings": [],
    }
    if not path:
        metadata["warnings"].append("missing:panda_contracts_v2_path")
        return "Panda V2 contracts artifact path was not provided.", metadata
    data, warning = read_json_safe(path)
    if warning:
        metadata["warnings"].append(warning)
        return f"Could not load Panda V2 contracts artifact at {path}.", metadata

    lines = [f"panda_contracts_v2_path: {path}", ""]
    reports = data.get("reports", []) if isinstance(data, dict) else []
    for report in reports:
        if not isinstance(report, dict):
            continue
        lines.append(f"## {report.get('tool', 'unknown')}")
        lines.append(f"parse_status: {report.get('parse_status')}")
        if report.get("warnings"):
            lines.append(f"warnings: {json.dumps(report.get('warnings'))}")
        files = report.get("files_inspected") or []
        if files:
            lines.append(f"files_inspected: {json.dumps(files[:20])}")
        claims = report.get("claims") or []
        for claim in claims[:40]:
            if not isinstance(claim, dict):
                continue
            lines.append(
                "- "
                + json.dumps(
                    {
                        "claim": claim.get("claim"),
                        "status": claim.get("status"),
                        "evidence_refs": claim.get("evidence_refs") or [],
                    },
                    sort_keys=True,
                )
            )
        if len(claims) > 40:
            metadata["truncated"] = True
            lines.append(f"... {len(claims) - 40} additional claims omitted")
        lines.append("")
    text, was_truncated = clip_text("\n".join(lines).strip(), max_chars)
    metadata["truncated"] = metadata["truncated"] or was_truncated
    return text, metadata


def compact_patch(path: Optional[Path], max_chars: int, max_changed_lines: int) -> tuple[str, dict]:
    metadata = {
        "path": str(path) if path else None,
        "truncated": False,
        "changed_line_limit": max_changed_lines,
        "warnings": [],
    }
    if not path:
        metadata["warnings"].append("missing:patch_path")
        return "Candidate patch path was not provided.", metadata
    text = read_text(path)
    if not text:
        metadata["warnings"].append(f"empty_or_missing:{path}")
        return f"Candidate patch was empty or missing at {path}.", metadata
    stats = patch_stats(text)
    metadata.update(stats)
    lines = [
        f"patch_path: {path}",
        f"changed_files: {stats['changed_file_count']}",
        f"changed_lines: {stats['changed_line_count']}",
        "",
    ]
    emitted_changed = 0
    for line in text.splitlines():
        is_changed = (line.startswith("+") and not line.startswith("+++")) or (
            line.startswith("-") and not line.startswith("---")
        )
        is_header = (
            line.startswith("diff --git")
            or line.startswith("+++")
            or line.startswith("---")
            or line.startswith("@@")
        )
        if is_changed:
            if emitted_changed >= max_changed_lines:
                metadata["truncated"] = True
                continue
            emitted_changed += 1
            lines.append(line)
        elif is_header:
            lines.append(line)
    clipped, was_truncated = clip_text("\n".join(lines), max_chars)
    metadata["truncated"] = metadata["truncated"] or was_truncated
    metadata["emitted_changed_lines"] = emitted_changed
    return clipped, metadata


def extract_failing_tests(text: str) -> list[str]:
    patterns = [
        r"--- FAIL:\s+([A-Za-z0-9_./:-]+)",
        r"=== RUN\s+([A-Za-z0-9_./:-]*Fail[A-Za-z0-9_./:-]*)",
        r"\bFAILED\s+([A-Za-z0-9_./:-]+)",
    ]
    names = []
    for pattern in patterns:
        names.extend(re.findall(pattern, text))
    lines = text.splitlines()
    failure_marker = re.compile(r"(FAIL|FAILED|ERROR|Assertion|Traceback|panic)", re.IGNORECASE)
    for idx, line in enumerate(lines):
        match = re.match(r"^\s*Test:\s+([A-Za-z0-9_./:-]+)\s*$", line)
        if not match:
            continue
        name = match.group(1)
        if not (name.startswith("Test") or name.startswith("test_") or "::" in name):
            continue
        nearby = "\n".join(lines[max(0, idx - 3) : min(len(lines), idx + 4)])
        if failure_marker.search(nearby):
            names.append(name)
    return sorted({name.strip() for name in names if name.strip()})


def extract_path_hints(text: str) -> list[str]:
    hints = set()
    for match in re.findall(r"([A-Za-z0-9_./-]+\.(?:go|py|js|jsx|ts|tsx|rb|rs|java)):\d+", text):
        hints.add(match)
    for match in re.findall(r"\bFAIL\s+([A-Za-z0-9_./-]+)", text):
        if "/" in match or "." in match:
            hints.add(match)
    return sorted(hints)


def focused_failure_excerpt(text: str, max_chars: int, context_lines: int) -> tuple[str, bool]:
    if not text:
        return "", False
    lines = text.splitlines()
    interesting = []
    marker = re.compile(
        r"(FAIL|FAILED|ERROR|panic|Traceback|Exception|Assertion|timeout|timed out|Missing Region|Target error|--- FAIL)",
        re.IGNORECASE,
    )
    for idx, line in enumerate(lines):
        if marker.search(line):
            start = max(0, idx - context_lines)
            end = min(len(lines), idx + context_lines + 1)
            interesting.extend(range(start, end))
    if not interesting:
        interesting = list(range(min(len(lines), 80)))
    selected = []
    previous = None
    for idx in sorted(set(interesting)):
        if previous is not None and idx > previous + 1:
            selected.append("...")
        selected.append(lines[idx])
        previous = idx
    return clip_text("\n".join(selected), max_chars)


def compact_test_output(path: Optional[Path], max_chars: int) -> tuple[str, dict]:
    metadata = {
        "path": str(path) if path else None,
        "truncated": False,
        "failing_tests": [],
        "path_hints": [],
        "warnings": [],
    }
    if not path:
        metadata["warnings"].append("missing:test_output_path")
        return "Failed test output path was not provided.", metadata
    text = read_text(path)
    if not text:
        metadata["warnings"].append(f"empty_or_missing:{path}")
        return f"Failed test output was empty or missing at {path}.", metadata
    metadata["failing_tests"] = extract_failing_tests(text)
    metadata["path_hints"] = extract_path_hints(text)
    excerpt, was_truncated = focused_failure_excerpt(
        text,
        max_chars=max_chars,
        context_lines=SECOND_PASS_FAILURE_CONTEXT_LINES,
    )
    metadata["truncated"] = was_truncated
    header = [
        f"test_output_path: {path}",
        f"failing_tests: {json.dumps(metadata['failing_tests'])}",
        f"path_hints: {json.dumps(metadata['path_hints'])}",
        "",
        "focused_failure_excerpt:",
        excerpt,
    ]
    return "\n".join(header), metadata


def resolve_second_pass_inputs(args: argparse.Namespace) -> dict:
    run_dir = args.run_dir.expanduser()
    first_record = load_result_record(run_dir, args.task_id, REPLAY_VARIANT) or {}
    first_pass_dir = args.first_pass_panda_output_dir or (
        Path(first_record["panda_output_dir"]) if first_record.get("panda_output_dir") else None
    )
    patch_path = args.patch_path or (
        Path(first_record["patch_path"]) if first_record.get("patch_path") else None
    )
    test_output_path = args.test_output_path or (
        Path(first_record["test_output_path"]) if first_record.get("test_output_path") else None
    )
    output_dir = args.output_dir or safe_result_dir(run_dir, args.task_id, SECOND_PASS_VARIANT)
    return {
        "run_dir": run_dir,
        "first_record": first_record,
        "first_pass_dir": first_pass_dir.expanduser() if first_pass_dir else None,
        "patch_path": patch_path.expanduser() if patch_path else None,
        "test_output_path": test_output_path.expanduser() if test_output_path else None,
        "output_dir": output_dir.expanduser(),
        "workspace": args.workspace.expanduser() if args.workspace else None,
    }


def build_second_pass_prompt(args: argparse.Namespace) -> tuple[str, dict]:
    inputs = resolve_second_pass_inputs(args)
    task, task_warnings = load_task_context(inputs["run_dir"], args.task_id)
    task_text, task_truncated = compact_task_context(task, SECOND_PASS_TASK_MAX_CHARS)
    workspace_check = None
    workspace_warnings = []
    if inputs["workspace"]:
        workspace_check = check_workspace_leakage(
            inputs["workspace"],
            sensitive_strings=sensitive_strings_for_task(task),
        )
        workspace_warnings = workspace_check["warnings"] + [
            f"workspace_leakage:{violation}" for violation in workspace_check["violations"]
        ]
    evidence_text, evidence_meta = compact_evidence(inputs["first_pass_dir"], SECOND_PASS_EVIDENCE_MAX_CHARS)
    patch_text, patch_meta = compact_patch(
        inputs["patch_path"],
        SECOND_PASS_PATCH_MAX_CHARS,
        SECOND_PASS_PATCH_MAX_LINES,
    )
    test_text, test_meta = compact_test_output(inputs["test_output_path"], SECOND_PASS_TEST_MAX_CHARS)
    first_record = inputs["first_record"]
    first_result = {
        "classification": first_record.get("classification"),
        "accepted": first_record.get("accepted"),
        "panda_run_failed": first_record.get("panda_run_failed"),
        "claude_budget_failure": first_record.get("claude_budget_failure"),
        "notes": first_record.get("notes"),
    }
    gating_warnings = []
    if not first_record:
        gating_warnings.append("missing:first_pass_replay_result")
    elif first_record.get("panda_run_failed"):
        gating_warnings.append("first_pass_panda_run_failed")
    elif first_record.get("accepted"):
        gating_warnings.append("first_pass_already_accepted")
    if not inputs["patch_path"]:
        gating_warnings.append("missing:candidate_patch")
    if not inputs["test_output_path"]:
        gating_warnings.append("missing:failed_test_output")
    prompt = f"""You are advising Codex as an independent Panda collaborator in a second-pass recovery review.

Goal:
- Identify what the first Panda pass missed after Codex implemented a candidate patch and verification failed.
- Focus on the exact API, test, hidden-contract, synchronization, or behavioral assumption violated by the current patch.
- Codex remains the only editor. Inspect and advise only.

Core question:
- What did the first Panda pass miss, and what exact API/test contract does this patch violate?

Rules:
- Do not edit files.
- Do not inspect or request gold benchmark patch or test_patch content.
- Use compact evidence first; raw artifact paths are provided only when exact details are needed.
- Treat hidden-test details inferred from failure output as uncertain unless the local repo confirms them.

Task context:
{task_text}

First-pass Panda replay result:
{json.dumps(first_result, indent=2)}

First-pass Panda evidence summary and artifact paths:
{evidence_text}

Codex candidate patch summary:
{patch_text}

Failed verification output:
{test_text}

Please return:
- Recommendation: the smallest correction Codex should make.
- Missed contract: what the first pass or current patch failed to satisfy.
- Alternative worth considering.
- Risks or edge cases.
- Verification plan with the most focused tests/commands.
"""
    prompt, prompt_redacted_count = redact_commit_shas(prompt)
    prompt, prompt_truncated = clip_text(prompt, SECOND_PASS_PROMPT_MAX_CHARS)
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "created_at": now_iso(),
        "task_id": args.task_id,
        "prompt_chars": len(prompt),
        "prompt_truncated": prompt_truncated,
        "redacted_sha_count": prompt_redacted_count,
        "input_paths": {
            "run_dir": str(inputs["run_dir"]),
            "first_pass_panda_output_dir": str(inputs["first_pass_dir"]) if inputs["first_pass_dir"] else None,
            "patch_path": str(inputs["patch_path"]) if inputs["patch_path"] else None,
            "test_output_path": str(inputs["test_output_path"]) if inputs["test_output_path"] else None,
            "workspace": str(inputs["workspace"]) if inputs["workspace"] else None,
        },
        "workspace_check": workspace_check,
        "sections": {
            "task": {"truncated": task_truncated, "max_chars": SECOND_PASS_TASK_MAX_CHARS},
            "evidence": {"truncated": evidence_meta["truncated"], "max_chars": SECOND_PASS_EVIDENCE_MAX_CHARS},
            "patch": {
                "truncated": patch_meta["truncated"],
                "max_chars": SECOND_PASS_PATCH_MAX_CHARS,
                "max_changed_lines": SECOND_PASS_PATCH_MAX_LINES,
            },
            "test_output": {"truncated": test_meta["truncated"], "max_chars": SECOND_PASS_TEST_MAX_CHARS},
            "prompt": {"max_chars": SECOND_PASS_PROMPT_MAX_CHARS},
        },
        "failing_tests": test_meta["failing_tests"],
        "path_hints": test_meta["path_hints"],
        "warnings": task_warnings
        + workspace_warnings
        + evidence_meta["warnings"]
        + patch_meta["warnings"]
        + test_meta["warnings"]
        + gating_warnings,
    }
    if args.strict_workspace and workspace_check and workspace_check["violations"]:
        metadata["strict_workspace_failed"] = True
    return prompt, metadata


def prepare_second_pass(args: argparse.Namespace) -> int:
    inputs = resolve_second_pass_inputs(args)
    output_dir = inputs["output_dir"]
    output_dir.mkdir(parents=True, exist_ok=True)
    prompt, metadata = build_second_pass_prompt(args)
    if args.strict_workspace and metadata.get("workspace_check", {}).get("violations"):
        write_json(output_dir / "prompt_metadata.json", metadata)
        raise SystemExit("Workspace leakage detected; refusing to prepare second-pass prompt.")
    prompt_path = output_dir / "panda_prompt.txt"
    prompt_path.write_text(prompt, encoding="utf-8")
    command = [
        sys.executable,
        str(consult_runner()),
        "--tool",
        args.tool,
        "--mode",
        "explore",
        "--role",
        args.role,
        "--profile",
        args.profile,
        "--timeout",
        str(args.timeout),
        "--output-dir",
        str(output_dir / "panda"),
        "--prompt-file",
        str(prompt_path),
    ]
    if inputs["workspace"]:
        command.extend(["--workspace", str(inputs["workspace"])])
    command.extend(["--protocol", "v2"])
    metadata["prompt_path"] = str(prompt_path)
    metadata["command"] = command
    write_json(output_dir / "prompt_metadata.json", metadata)
    print(shlex.join(command))
    return 0


def resolve_falsifier_inputs(args: argparse.Namespace) -> dict:
    run_dir = args.run_dir.expanduser()
    first_record = load_result_record(run_dir, args.task_id, REPLAY_VARIANT) or {}
    first_pass_dir = args.first_pass_panda_output_dir or (
        Path(first_record["panda_output_dir"]) if first_record.get("panda_output_dir") else None
    )
    contracts_path = args.contracts_path
    if contracts_path is None and first_pass_dir:
        candidate = first_pass_dir / CONTRACTS_FILENAME
        contracts_path = candidate if candidate.exists() else None
    test_output_path = args.test_output_path or (
        Path(first_record["test_output_path"]) if first_record.get("test_output_path") else None
    )
    output_dir = args.output_dir or safe_result_dir(run_dir, args.task_id, FALSIFIER_VARIANT)
    return {
        "run_dir": run_dir,
        "first_record": first_record,
        "first_pass_dir": first_pass_dir.expanduser() if first_pass_dir else None,
        "contracts_path": contracts_path.expanduser() if contracts_path else None,
        "test_output_path": test_output_path.expanduser() if test_output_path else None,
        "output_dir": output_dir.expanduser(),
        "workspace": args.workspace.expanduser() if args.workspace else None,
    }


def build_falsifier_prompt(args: argparse.Namespace) -> tuple[str, dict]:
    inputs = resolve_falsifier_inputs(args)
    task, task_warnings = load_task_context(inputs["run_dir"], args.task_id)
    task_text, task_truncated = compact_task_context(task, SECOND_PASS_TASK_MAX_CHARS)
    evidence_text, evidence_meta = compact_evidence(inputs["first_pass_dir"], SECOND_PASS_EVIDENCE_MAX_CHARS)
    contracts_text, contracts_meta = compact_contracts_artifact(
        inputs["contracts_path"],
        FALSIFIER_CONTRACTS_MAX_CHARS,
    )
    test_text, test_meta = ("Failed verification output was not provided.", {
        "path": None,
        "truncated": False,
        "failing_tests": [],
        "path_hints": [],
        "warnings": [],
    })
    if inputs["test_output_path"]:
        test_text, test_meta = compact_test_output(inputs["test_output_path"], SECOND_PASS_TEST_MAX_CHARS)

    prompt = f"""You are advising Codex as an independent Panda contract falsifier.

Goal:
- Audit concrete Panda V2 contract claims once, before Codex integrates them.
- Focus only on exact API, field, method, type, endpoint, schema, test seam, foreign-key, synchronization, and backward-compatibility assumptions.
- Codex remains the only editor and integrator.

Rules:
- Do not edit files.
- Do not ask the original advisors to rebut your findings.
- Do not vote, debate, or propose a full implementation plan.
- Use contradictions only when local evidence directly conflicts with a claim.
- Use unverifiable or not_found when available evidence cannot confirm the claim.

Task context:
{task_text}

{falsifier_user_prompt()}

Panda V2 contract artifact summary:
{contracts_text}

First-pass Panda evidence summary:
{evidence_text}

Optional failed verification output:
{test_text}

Please return:
- Recommendation: how Codex should treat the audited claims.
- Contradictions: exact claims contradicted by local evidence.
- Unverifiable or not_found claims: exact claims Codex should verify before relying on them.
- Verification plan: focused checks Codex should run.
"""
    prompt, prompt_redacted_count = redact_commit_shas(prompt)
    prompt, prompt_truncated = clip_text(prompt, FALSIFIER_PROMPT_MAX_CHARS)
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "created_at": now_iso(),
        "task_id": args.task_id,
        "prompt_kind": "contract_falsifier",
        "prompt_version": 2,
        "prompt_chars": len(prompt),
        "prompt_truncated": prompt_truncated,
        "redacted_sha_count": prompt_redacted_count,
        "input_paths": {
            "run_dir": str(inputs["run_dir"]),
            "first_pass_panda_output_dir": str(inputs["first_pass_dir"]) if inputs["first_pass_dir"] else None,
            "contracts_path": str(inputs["contracts_path"]) if inputs["contracts_path"] else None,
            "test_output_path": str(inputs["test_output_path"]) if inputs["test_output_path"] else None,
            "workspace": str(inputs["workspace"]) if inputs["workspace"] else None,
        },
        "sections": {
            "task": {"truncated": task_truncated, "max_chars": SECOND_PASS_TASK_MAX_CHARS},
            "evidence": {"truncated": evidence_meta["truncated"], "max_chars": SECOND_PASS_EVIDENCE_MAX_CHARS},
            "contracts": {"truncated": contracts_meta["truncated"], "max_chars": FALSIFIER_CONTRACTS_MAX_CHARS},
            "test_output": {"truncated": test_meta["truncated"], "max_chars": SECOND_PASS_TEST_MAX_CHARS},
            "prompt": {"max_chars": FALSIFIER_PROMPT_MAX_CHARS},
        },
        "warnings": task_warnings
        + evidence_meta["warnings"]
        + contracts_meta["warnings"]
        + test_meta["warnings"],
    }
    return prompt, metadata


def prepare_falsifier(args: argparse.Namespace) -> int:
    inputs = resolve_falsifier_inputs(args)
    output_dir = inputs["output_dir"]
    output_dir.mkdir(parents=True, exist_ok=True)
    prompt, metadata = build_falsifier_prompt(args)
    prompt_path = output_dir / "panda_prompt.txt"
    prompt_path.write_text(prompt, encoding="utf-8")
    command = [
        sys.executable,
        str(consult_runner()),
        "--tool",
        args.tool,
        "--mode",
        "explore",
        "--role",
        "contract-falsifier",
        "--profile",
        args.profile,
        "--timeout",
        str(args.timeout),
        "--output-dir",
        str(output_dir / "panda"),
        "--prompt-file",
        str(prompt_path),
        "--protocol",
        "v2",
    ]
    if inputs["workspace"]:
        command.extend(["--workspace", str(inputs["workspace"])])
    metadata["prompt_path"] = str(prompt_path)
    metadata["command"] = command
    metadata["expected_artifact"] = str(output_dir / "panda" / FALSIFIER_FILENAME)
    write_json(output_dir / "prompt_metadata.json", metadata)
    print(shlex.join(command))
    return 0


def manifest_requested_tools(manifest: Optional[dict]) -> tuple[str, ...]:
    if not isinstance(manifest, dict):
        return ()

    requested = manifest.get("requested_tools")
    if isinstance(requested, list):
        tools = [tool for tool in requested if isinstance(tool, str)]
        if tools:
            return tuple(dict.fromkeys(tools))

    manifest_tools = manifest.get("tools")
    if isinstance(manifest_tools, list):
        tools = [
            tool.get("tool")
            for tool in manifest_tools
            if isinstance(tool, dict) and isinstance(tool.get("tool"), str)
        ]
        if tools:
            return tuple(dict.fromkeys(tools))

    return ()


def inspect_panda_run(output_dir: Path, required_tools: Optional[Iterable[str]] = None) -> dict:
    artifact_failures = []
    evidence = None
    manifest = None

    for relative in ("manifest.json", "evidence.json"):
        path = output_dir / relative
        if not path.exists():
            artifact_failures.append(f"missing:{relative}")
            continue
        try:
            parsed = read_json(path)
        except json.JSONDecodeError:
            artifact_failures.append(f"malformed:{relative}")
            continue
        if relative == "manifest.json":
            manifest = parsed
        else:
            evidence = parsed

    if required_tools is None:
        required = manifest_requested_tools(manifest) or REQUIRED_PANDA_TOOLS
    else:
        required = tuple(required_tools)
    core_status = {
        tool: {
            "status": "missing",
            "returncode": None,
            "timed_out": False,
            "budget_failure": False,
        }
        for tool in required
    }

    for tool in required:
        for suffix in (".txt", ".summary.json"):
            path = output_dir / f"{tool}{suffix}"
            if not path.exists():
                artifact_failures.append(f"missing:{tool}{suffix}")

    if evidence:
        for finding in evidence.get("findings", []):
            if not isinstance(finding, dict):
                continue
            tool = finding.get("tool")
            if tool not in core_status:
                continue
            raw_text = read_text(Path(finding.get("raw_output_path") or output_dir / f"{tool}.txt"))
            budget_failure = contains_budget_failure(raw_text)
            core_status[tool] = {
                "status": finding.get("status"),
                "returncode": finding.get("returncode"),
                "timed_out": bool(finding.get("timed_out")),
                "budget_failure": budget_failure,
            }

    for tool in required:
        if core_status[tool]["status"] == "missing":
            continue
        summary_path = output_dir / f"{tool}.summary.json"
        if summary_path.exists():
            try:
                summary_text = summary_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                summary_text = ""
            if contains_budget_failure(summary_text):
                core_status[tool]["budget_failure"] = True

    claude_budget_failure = bool(core_status.get("claude", {}).get("budget_failure"))
    non_success = [
        tool
        for tool, status in core_status.items()
        if status.get("status") != "success"
    ]
    panda_run_failed = bool(artifact_failures or non_success or claude_budget_failure)
    return {
        "output_dir": str(output_dir),
        "artifact_failures": artifact_failures,
        "panda_core_status": core_status,
        "panda_run_failed": panda_run_failed,
        "claude_budget_failure": claude_budget_failure,
        "manifest_loaded": manifest is not None,
        "evidence_loaded": evidence is not None,
    }


def record_result(args: argparse.Namespace) -> int:
    run_dir = args.run_dir.expanduser()
    if args.variant not in VARIANTS:
        raise SystemExit(f"--variant must be one of: {', '.join(VARIANTS)}")
    accepted = args.accepted
    if accepted is None:
        accepted = bool(args.tests_passed and not args.regression)
    classification = args.classification
    if classification is None:
        classification = "accepted" if accepted else "failed_tests"
    if args.contaminated:
        classification = "contaminated"
    record = {
        "schema_version": SCHEMA_VERSION,
        "task_id": args.task_id,
        "variant": args.variant,
        "tests_passed": bool(args.tests_passed),
        "accepted": bool(accepted),
        "regression": bool(args.regression),
        "classification": classification,
        "contaminated": bool(args.contaminated),
        "benchmark_invalid": bool(args.benchmark_invalid),
        "wall_seconds": args.wall_seconds,
        "panda_run_failed": False,
        "claude_budget_failure": False,
        "evidence_used": bool(args.evidence_used),
        "notes": args.notes or "",
        "patch_path": str(args.patch_path) if args.patch_path else None,
        "test_output_path": str(args.test_output_path) if args.test_output_path else None,
        "recorded_at": now_iso(),
    }
    workspace_isolated = getattr(args, "workspace_isolated", None)
    workspace_metadata_path = getattr(args, "workspace_metadata_path", None)
    if workspace_isolated is not None or workspace_metadata_path:
        record["workspace_preparation"] = {
            "workspace_isolated": bool(workspace_isolated) if workspace_isolated is not None else None,
            "workspace_metadata_path": str(workspace_metadata_path) if workspace_metadata_path else None,
        }
    for field in ADVICE_QUALITY_FIELDS:
        value = getattr(args, field, None)
        if value is not None:
            record[field] = bool(value)
    second_pass_used = getattr(args, "second_pass_used", None)
    if second_pass_used is not None:
        record["second_pass_used"] = bool(second_pass_used)
    elif args.variant == SECOND_PASS_VARIANT:
        record["second_pass_used"] = True
    second_pass_prompt_path = getattr(args, "second_pass_prompt_path", None)
    if second_pass_prompt_path:
        record["second_pass_prompt_path"] = str(second_pass_prompt_path)
    advice_quality_notes = getattr(args, "advice_quality_notes", None)
    if advice_quality_notes:
        record["advice_quality_notes"] = advice_quality_notes
    if args.panda_output_dir:
        panda = inspect_panda_run(args.panda_output_dir.expanduser())
        record.update({
            "panda_output_dir": panda["output_dir"],
            "panda_core_status": panda["panda_core_status"],
            "panda_artifact_failures": panda["artifact_failures"],
            "panda_run_failed": panda["panda_run_failed"],
            "claude_budget_failure": panda["claude_budget_failure"],
        })
    out_dir = result_dir(run_dir, args.task_id, args.variant)
    write_json(out_dir / "result.json", record)
    append_result(run_dir, record)
    print(out_dir / "result.json")
    return 0


def append_result(run_dir: Path, record: dict) -> None:
    path = run_dir / "results.json"
    data = {"schema_version": SCHEMA_VERSION, "results": []}
    if path.exists():
        data = read_json(path)
    results = [
        result
        for result in data.get("results", [])
        if not (result.get("task_id") == record["task_id"] and result.get("variant") == record["variant"])
    ]
    results.append(record)
    write_json(path, {"schema_version": SCHEMA_VERSION, "results": results})
    update_run_manifest_budget_failures(run_dir, results)


def update_run_manifest_budget_failures(run_dir: Path, results: list[dict]) -> None:
    manifest_path = run_dir / "run_manifest.json"
    if not manifest_path.exists():
        return
    manifest = read_json(manifest_path)
    budget_failures = [
        {
            "task_id": result.get("task_id"),
            "variant": result.get("variant"),
            "recorded_at": result.get("recorded_at"),
        }
        for result in results
        if result.get("claude_budget_failure")
    ]
    manifest["budget_failures"] = budget_failures
    write_json(manifest_path, manifest)


def run_command(command: list[str], cwd: Path, stdout_path: Path, stderr_path: Path, timeout: Optional[int]) -> dict:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        stdout = completed.stdout
        stderr = completed.stderr
        returncode = completed.returncode
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or f"Timed out after {timeout} seconds."
        returncode = -1
        timed_out = True
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    return {
        "command": command,
        "returncode": returncode,
        "timed_out": timed_out,
        "wall_seconds": round(time.monotonic() - started, 3),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
    }


def validate_canary_artifacts(output_dir: Path, required_tools: Iterable[str] = REQUIRED_PANDA_TOOLS) -> dict:
    inspection = inspect_panda_run(output_dir, required_tools)
    return {
        "output_dir": str(output_dir),
        "ok": not inspection["panda_run_failed"],
        **inspection,
    }


def find_lingering_processes(marker: str) -> dict:
    try:
        completed = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"checked": False, "matches": []}
    if completed.returncode != 0:
        return {"checked": False, "matches": []}
    current_pid = str(os.getpid())
    matches = []
    for line in completed.stdout.splitlines():
        stripped = line.strip()
        if not stripped or marker not in stripped:
            continue
        pid = stripped.split(maxsplit=1)[0]
        if pid == current_pid:
            continue
        matches.append(stripped)
    return {"checked": True, "matches": matches}


def run_canary(args: argparse.Namespace) -> int:
    run_dir = args.run_dir.expanduser()
    canary_dir = run_dir / "canary"
    canary_dir.mkdir(parents=True, exist_ok=True)
    results = []

    unittest_result = run_command(
        [sys.executable, "-W", "error::ResourceWarning", "-m", "unittest", "tests/test_consult_ai_team.py"],
        repo_root(),
        canary_dir / "unittest" / "stdout.txt",
        canary_dir / "unittest" / "stderr.txt",
        timeout=args.test_timeout,
    )
    unittest_result["name"] = "unit_resourcewarning"
    unittest_result["ok"] = unittest_result["returncode"] == 0
    results.append(unittest_result)

    marker = "Panda eval canary"
    dry_dir = canary_dir / "panda-dry-run"
    dry_result = run_command(
        [
            sys.executable,
            str(consult_runner()),
            "--tool",
            "all",
            "--mode",
            "explore",
            "--profile",
            args.profile,
            "--timeout",
            str(args.panda_timeout),
            "--serialize-opencode",
            "--dry-run",
            "--output-dir",
            str(dry_dir),
            "--prompt",
            f"{marker}: dry-run artifact validation.",
        ],
        repo_root(),
        canary_dir / "panda-dry-run-command" / "stdout.txt",
        canary_dir / "panda-dry-run-command" / "stderr.txt",
        timeout=args.panda_timeout,
    )
    dry_result["name"] = "panda_dry_run"
    dry_result["artifact_validation"] = validate_canary_artifacts(dry_dir)
    dry_result["ok"] = dry_result["returncode"] == 0 and dry_result["artifact_validation"]["ok"]
    results.append(dry_result)

    if args.skip_real_panda:
        real_result = {
            "name": "panda_real_explore",
            "skipped": True,
            "ok": True,
            "reason": "skip_real_panda",
        }
    else:
        real_dir = canary_dir / "panda-real"
        real_result = run_command(
            [
                sys.executable,
                str(consult_runner()),
                "--tool",
                "all",
                "--mode",
                "explore",
                "--profile",
                args.profile,
                "--timeout",
                str(args.panda_timeout),
                "--output-dir",
                str(real_dir),
                "--prompt",
                (
                    f"{marker}: inspect this Panda repo at a high level. "
                    "Return one sentence on whether the eval harness should preserve compact artifacts. "
                    "Do not edit files."
                ),
            ],
            repo_root(),
            canary_dir / "panda-real-command" / "stdout.txt",
            canary_dir / "panda-real-command" / "stderr.txt",
            timeout=args.panda_timeout + 30,
        )
        real_result["name"] = "panda_real_explore"
        real_result["artifact_validation"] = validate_canary_artifacts(real_dir)
        real_result["ok"] = real_result["returncode"] == 0 and real_result["artifact_validation"]["ok"]
    results.append(real_result)

    process_check = find_lingering_processes(marker)
    process_check["ok"] = not process_check.get("matches")
    canary = {
        "schema_version": SCHEMA_VERSION,
        "created_at": now_iso(),
        "run_dir": str(run_dir),
        "results": results,
        "process_check": process_check,
        "ok": all(result.get("ok") for result in results) and process_check["ok"],
    }
    write_json(canary_dir / "result.json", canary)
    manifest_path = run_dir / "run_manifest.json"
    if manifest_path.exists():
        manifest = read_json(manifest_path)
        manifest["canary"] = {
            "ok": canary["ok"],
            "result_path": str(canary_dir / "result.json"),
            "claude_budget_failure": any(
                (
                    result.get("artifact_validation", {})
                    .get("panda_core_status", {})
                    .get("claude", {})
                    .get("budget_failure")
                )
                for result in results
                if isinstance(result, dict)
            ),
        }
        write_json(manifest_path, manifest)
    print(canary_dir / "result.json")
    return 0 if canary["ok"] else 1


def metric_summary(results: list[dict]) -> dict:
    by_variant = {}
    for variant in VARIANTS:
        variant_results = [result for result in results if result.get("variant") == variant]
        total = len(variant_results)
        accepted = sum(1 for result in variant_results if result.get("accepted"))
        panda_results = [
            result
            for result in variant_results
            if variant in PANDA_RESULT_VARIANTS
        ]
        panda_total = len(panda_results)
        panda_failed = sum(1 for result in panda_results if result.get("panda_run_failed"))
        claude_budget = sum(1 for result in panda_results if result.get("claude_budget_failure"))
        evidence_used = sum(1 for result in panda_results if result.get("evidence_used"))
        accepted_times = [
            result.get("wall_seconds")
            for result in variant_results
            if result.get("accepted") and isinstance(result.get("wall_seconds"), (int, float))
        ]
        by_variant[variant] = {
            "total": total,
            "accepted": accepted,
            "pass_rate": accepted / total if total else None,
            "panda_runner_failure_rate": panda_failed / panda_total if panda_total else None,
            "claude_budget_failure_rate": claude_budget / panda_total if panda_total else None,
            "evidence_use_rate": evidence_used / panda_total if panda_total else None,
            "mean_time_to_green": round(sum(accepted_times) / len(accepted_times), 3) if accepted_times else None,
        }
    scout_results = [
        result
        for result in results
        if result.get("variant") == SCOUT_VARIANT
        and not result.get("contaminated")
        and not result.get("benchmark_invalid")
    ]
    replay_results = [
        result
        for result in results
        if result.get("variant") == REPLAY_VARIANT
        and not result.get("contaminated")
        and not result.get("benchmark_invalid")
    ]
    second_pass_results = [
        result
        for result in results
        if result.get("variant") == SECOND_PASS_VARIANT
        and not result.get("contaminated")
        and not result.get("benchmark_invalid")
    ]
    scout_by_task = {result.get("task_id"): result for result in scout_results}
    replay_by_task = {result.get("task_id"): result for result in replay_results}
    struggle_task_ids = {
        result.get("task_id")
        for result in scout_results
        if result.get("classification") in SCOUT_STRUGGLE_CLASSIFICATIONS
    }
    replay_denominator = [
        result
        for result in replay_results
        if result.get("task_id") in struggle_task_ids
    ]
    second_pass_denominator = [
        result
        for result in second_pass_results
        if result.get("task_id") in struggle_task_ids
        and result.get("task_id") in replay_by_task
        and not replay_by_task[result.get("task_id")].get("accepted")
    ]
    replay_accepted = sum(1 for result in replay_results if result.get("accepted"))
    replay_rescues = sum(1 for result in replay_denominator if result.get("accepted"))
    replay_panda_total = len(replay_results)
    replay_panda_failed = sum(1 for result in replay_results if result.get("panda_run_failed"))
    replay_claude_budget = sum(1 for result in replay_results if result.get("claude_budget_failure"))
    replay_evidence_used = sum(1 for result in replay_results if result.get("evidence_used"))
    hard_local_panda_results = replay_results + second_pass_results
    hard_local_panda_total = len(hard_local_panda_results)
    hard_local_panda_failed = sum(1 for result in hard_local_panda_results if result.get("panda_run_failed"))
    hard_local_claude_budget = sum(1 for result in hard_local_panda_results if result.get("claude_budget_failure"))
    hard_local_evidence_used = sum(1 for result in hard_local_panda_results if result.get("evidence_used"))
    replay_times = [
        result.get("wall_seconds")
        for result in replay_results
        if result.get("accepted") and isinstance(result.get("wall_seconds"), (int, float))
    ]
    second_pass_accepted = sum(1 for result in second_pass_results if result.get("accepted"))
    second_pass_rescues = sum(1 for result in second_pass_denominator if result.get("accepted"))
    incremental_second_pass_rescues = sum(
        1
        for result in second_pass_denominator
        if result.get("accepted")
    )
    second_pass_panda_total = len(second_pass_results)
    second_pass_panda_failed = sum(1 for result in second_pass_results if result.get("panda_run_failed"))
    second_pass_claude_budget = sum(1 for result in second_pass_results if result.get("claude_budget_failure"))
    second_pass_evidence_used = sum(1 for result in second_pass_results if result.get("evidence_used"))
    advice_quality = {}
    for field in ADVICE_QUALITY_FIELDS:
        rated = [result for result in hard_local_panda_results if isinstance(result.get(field), bool)]
        true_count = sum(1 for result in rated if result.get(field))
        advice_quality[field] = {
            "rated_count": len(rated),
            "true_count": true_count,
            "true_rate": true_count / len(rated) if rated else None,
        }
    hard_local = {
        "codex_scout_total": len(scout_results),
        "codex_scout_pass_rate": (
            sum(1 for result in scout_results if result.get("accepted")) / len(scout_results)
            if scout_results
            else None
        ),
        "codex_struggle_count": len(struggle_task_ids),
        "panda_replay_total": len(replay_results),
        "panda_replay_pass_rate": replay_accepted / len(replay_results) if replay_results else None,
        "failure_to_success_rescue_rate": (
            replay_rescues / len(replay_denominator) if replay_denominator else None
        ),
        "panda_replay_second_pass_total": len(second_pass_results),
        "panda_replay_second_pass_pass_rate": (
            second_pass_accepted / len(second_pass_results) if second_pass_results else None
        ),
        "second_pass_rescue_rate": (
            second_pass_rescues / len(second_pass_denominator) if second_pass_denominator else None
        ),
        "incremental_second_pass_rescue_count": incremental_second_pass_rescues,
        "second_pass_min_sample_met": len(second_pass_denominator) >= 5,
        "panda_runner_failure_rate": (
            hard_local_panda_failed / hard_local_panda_total if hard_local_panda_total else None
        ),
        "claude_budget_failure_rate": (
            hard_local_claude_budget / hard_local_panda_total if hard_local_panda_total else None
        ),
        "evidence_use_rate": (
            hard_local_evidence_used / hard_local_panda_total if hard_local_panda_total else None
        ),
        "panda_replay_runner_failure_rate": (
            replay_panda_failed / replay_panda_total if replay_panda_total else None
        ),
        "panda_replay_claude_budget_failure_rate": (
            replay_claude_budget / replay_panda_total if replay_panda_total else None
        ),
        "panda_replay_evidence_use_rate": (
            replay_evidence_used / replay_panda_total if replay_panda_total else None
        ),
        "mean_time_to_green": (
            round(sum(replay_times) / len(replay_times), 3) if replay_times else None
        ),
        "contaminated_task_count": len({result.get("task_id") for result in results if result.get("contaminated")}),
        "benchmark_invalid_task_count": len({result.get("task_id") for result in results if result.get("benchmark_invalid")}),
        "replay_without_matching_scout_count": sum(
            1 for result in replay_results if result.get("task_id") not in scout_by_task
        ),
        "second_pass_without_matching_scout_count": sum(
            1 for result in second_pass_results if result.get("task_id") not in scout_by_task
        ),
        "second_pass_without_matching_replay_count": sum(
            1 for result in second_pass_results if result.get("task_id") not in replay_by_task
        ),
        "second_pass_runner_failure_rate": (
            second_pass_panda_failed / second_pass_panda_total if second_pass_panda_total else None
        ),
        "second_pass_claude_budget_failure_rate": (
            second_pass_claude_budget / second_pass_panda_total if second_pass_panda_total else None
        ),
        "second_pass_evidence_use_rate": (
            second_pass_evidence_used / second_pass_panda_total if second_pass_panda_total else None
        ),
        "advice_quality": advice_quality,
    }
    by_variant["hard_local"] = hard_local
    by_variant.update(hard_local)
    return by_variant


def summarize(args: argparse.Namespace) -> int:
    run_dir = args.run_dir.expanduser()
    results_path = run_dir / "results.json"
    if not results_path.exists():
        raise SystemExit(f"Missing results file: {results_path}")
    results = read_json(results_path).get("results", [])
    canary_path = run_dir / "canary" / "result.json"
    canary = read_json(canary_path) if canary_path.exists() else None
    summary = {
        "schema_version": SCHEMA_VERSION,
        "created_at": now_iso(),
        "run_dir": str(run_dir),
        "canary_ok": canary.get("ok") if isinstance(canary, dict) else None,
        "metrics": metric_summary(results),
        "completed_task_variants": len(results),
        "budget_exhausted": any(result.get("claude_budget_failure") for result in results),
    }
    write_json(run_dir / "summary.json", summary)
    print(json.dumps(summary["metrics"], indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create a nightly Panda eval run directory.")
    init_parser.add_argument("--run-dir", type=Path)
    init_parser.add_argument("--eval-mode", choices=("nightly", "hard-local"), default="nightly")
    init_parser.add_argument("--tasks-file", type=Path)
    init_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    init_parser.add_argument("--timeout", type=int)
    init_parser.add_argument("--straggler-timeout", type=int, default=DEFAULT_STRAGGLER_TIMEOUT)
    init_parser.add_argument("--force", action="store_true")
    init_parser.set_defaults(func=init_run)

    select_parser = subparsers.add_parser("select-hard", help="Select hard local SWE-bench-style candidates.")
    select_parser.add_argument("--run-dir", type=Path)
    select_parser.add_argument("--records-file", type=Path)
    select_parser.add_argument("--output-file", type=Path)
    select_parser.add_argument("--source", choices=("auto", "swebench_pro", "swebench_full", "swebench_verified"), default="auto")
    select_parser.add_argument("--target-count", type=int, default=HARD_LOCAL_TARGET_COUNT)
    select_parser.add_argument("--max-count", type=int, default=HARD_LOCAL_MAX_COUNT)
    select_parser.add_argument("--repo-cap", type=int, default=HARD_LOCAL_REPO_CAP)
    select_parser.add_argument("--allow-network", action="store_true")
    select_parser.set_defaults(func=select_hard_tasks)

    canary_parser = subparsers.add_parser("canary", help="Run the Panda reliability canary.")
    canary_parser.add_argument("--run-dir", type=Path, default=default_run_dir())
    canary_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    canary_parser.add_argument("--panda-timeout", type=int, default=DEFAULT_TIMEOUT)
    canary_parser.add_argument("--test-timeout", type=int, default=120)
    canary_parser.add_argument("--skip-real-panda", action="store_true")
    canary_parser.set_defaults(func=run_canary)

    workspace_parser = subparsers.add_parser(
        "prepare-workspace",
        help="Copy a benchmark workspace without git metadata or transient caches.",
    )
    workspace_parser.add_argument("--run-dir", type=Path, default=default_run_dir("hard-local"))
    workspace_parser.add_argument("--task-id", required=True)
    workspace_parser.add_argument("--source-workspace", type=Path, required=True)
    workspace_parser.add_argument("--output-dir", type=Path)
    workspace_parser.add_argument("--target-commit")
    workspace_parser.set_defaults(func=prepare_workspace)

    check_workspace_parser = subparsers.add_parser(
        "check-workspace",
        help="Check a Panda benchmark workspace for leakage risks.",
    )
    check_workspace_parser.add_argument("--workspace", type=Path, required=True)
    check_workspace_parser.add_argument("--target-commit")
    check_workspace_parser.add_argument("--output-file", type=Path)
    check_workspace_parser.add_argument("--strict", action="store_true")
    check_workspace_parser.set_defaults(func=check_workspace_command)

    first_pass_parser = subparsers.add_parser(
        "prepare-first-pass",
        help="Prepare a bounded contract-first Panda replay prompt.",
    )
    first_pass_parser.add_argument("--run-dir", type=Path, default=default_run_dir("hard-local"))
    first_pass_parser.add_argument("--task-id", required=True)
    first_pass_parser.add_argument("--workspace", type=Path, required=True)
    first_pass_parser.add_argument("--output-dir", type=Path)
    first_pass_parser.add_argument("--tool", choices=PANDA_TOOL_CHOICES, default="all")
    first_pass_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    first_pass_parser.add_argument("--timeout", type=int, default=HARD_LOCAL_TIMEOUT)
    first_pass_parser.add_argument("--role", default="implementation-review")
    first_pass_parser.add_argument("--prompt-version", type=int, choices=(2,), default=2)
    first_pass_parser.add_argument("--strict-workspace", action="store_true")
    first_pass_parser.set_defaults(func=prepare_first_pass)

    second_pass_parser = subparsers.add_parser(
        "prepare-second-pass",
        help="Prepare a bounded Panda second-pass recovery prompt.",
    )
    second_pass_parser.add_argument("--run-dir", type=Path, default=default_run_dir("hard-local"))
    second_pass_parser.add_argument("--task-id", required=True)
    second_pass_parser.add_argument("--first-pass-panda-output-dir", type=Path)
    second_pass_parser.add_argument("--patch-path", type=Path)
    second_pass_parser.add_argument("--test-output-path", type=Path)
    second_pass_parser.add_argument("--workspace", type=Path)
    second_pass_parser.add_argument("--output-dir", type=Path)
    second_pass_parser.add_argument("--tool", choices=PANDA_TOOL_CHOICES, default="all")
    second_pass_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    second_pass_parser.add_argument("--timeout", type=int, default=HARD_LOCAL_TIMEOUT)
    second_pass_parser.add_argument("--role", default="debugging")
    second_pass_parser.add_argument("--strict-workspace", action="store_true")
    second_pass_parser.set_defaults(func=prepare_second_pass)

    falsifier_parser = subparsers.add_parser(
        "prepare-falsifier",
        help="Prepare a one-pass Panda V2 contract falsifier prompt.",
    )
    falsifier_parser.add_argument("--run-dir", type=Path, default=default_run_dir("hard-local"))
    falsifier_parser.add_argument("--task-id", required=True)
    falsifier_parser.add_argument("--first-pass-panda-output-dir", type=Path)
    falsifier_parser.add_argument("--contracts-path", type=Path)
    falsifier_parser.add_argument("--test-output-path", type=Path)
    falsifier_parser.add_argument("--workspace", type=Path)
    falsifier_parser.add_argument("--output-dir", type=Path)
    falsifier_parser.add_argument("--tool", choices=PANDA_TOOL_CHOICES, default="all")
    falsifier_parser.add_argument("--profile", default=DEFAULT_PROFILE)
    falsifier_parser.add_argument("--timeout", type=int, default=HARD_LOCAL_TIMEOUT)
    falsifier_parser.set_defaults(func=prepare_falsifier)

    record_parser = subparsers.add_parser("record", help="Record one task/variant result.")
    record_parser.add_argument("--run-dir", type=Path, default=default_run_dir())
    record_parser.add_argument("--task-id", required=True)
    record_parser.add_argument("--variant", required=True, choices=VARIANTS)
    record_parser.add_argument("--tests-passed", type=bool_arg, required=True)
    record_parser.add_argument("--accepted", type=bool_arg)
    record_parser.add_argument("--regression", type=bool_arg, default=False)
    record_parser.add_argument("--classification", choices=RESULT_CLASSIFICATIONS)
    record_parser.add_argument("--contaminated", action="store_true")
    record_parser.add_argument("--benchmark-invalid", action="store_true")
    record_parser.add_argument("--wall-seconds", type=float, required=True)
    record_parser.add_argument("--panda-output-dir", type=Path)
    record_parser.add_argument("--evidence-used", action="store_true")
    record_parser.add_argument("--patch-path", type=Path)
    record_parser.add_argument("--test-output-path", type=Path)
    record_parser.add_argument("--workspace-metadata-path", type=Path)
    record_parser.add_argument("--workspace-isolated", type=bool_arg)
    record_parser.add_argument("--notes", default="")
    record_parser.add_argument("--panda-direction-correct", type=bool_arg)
    record_parser.add_argument("--panda-missed-contract", type=bool_arg)
    record_parser.add_argument("--codex-implementation-error", type=bool_arg)
    record_parser.add_argument("--evidence-was-actionable", type=bool_arg)
    record_parser.add_argument("--second-pass-used", type=bool_arg)
    record_parser.add_argument("--second-pass-prompt-path", type=Path)
    record_parser.add_argument("--advice-quality-notes", default="")
    record_parser.set_defaults(func=record_result)

    summarize_parser = subparsers.add_parser("summarize", help="Summarize task results.")
    summarize_parser.add_argument("--run-dir", type=Path, default=default_run_dir())
    summarize_parser.set_defaults(func=summarize)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
