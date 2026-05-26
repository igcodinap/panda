#!/usr/bin/env python3
"""Create and score small Panda evaluation runs."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from typing import Iterable, Optional


SCHEMA_VERSION = 1
LEGACY_VARIANTS = ("codex_alone", "panda_explore")
HARD_LOCAL_VARIANTS = ("codex_alone_scout", "panda_replay")
VARIANTS = (*LEGACY_VARIANTS, *HARD_LOCAL_VARIANTS)
SCOUT_VARIANT = "codex_alone_scout"
REPLAY_VARIANT = "panda_replay"
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
HARD_LOCAL_TARGET_COUNT = 20
HARD_LOCAL_EXPANSION_COUNT = 10
HARD_LOCAL_MAX_COUNT = 30
HARD_LOCAL_REPO_CAP = 3
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


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def inspect_panda_run(output_dir: Path, required_tools: Iterable[str] = REQUIRED_PANDA_TOOLS) -> dict:
    required = tuple(required_tools)
    artifact_failures = []
    core_status = {
        tool: {
            "status": "missing",
            "returncode": None,
            "timed_out": False,
            "budget_failure": False,
        }
        for tool in required
    }
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
            if variant in {"panda_explore", REPLAY_VARIANT}
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
    scout_by_task = {result.get("task_id"): result for result in scout_results}
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
    replay_accepted = sum(1 for result in replay_results if result.get("accepted"))
    replay_rescues = sum(1 for result in replay_denominator if result.get("accepted"))
    replay_panda_total = len(replay_results)
    replay_panda_failed = sum(1 for result in replay_results if result.get("panda_run_failed"))
    replay_claude_budget = sum(1 for result in replay_results if result.get("claude_budget_failure"))
    replay_evidence_used = sum(1 for result in replay_results if result.get("evidence_used"))
    replay_times = [
        result.get("wall_seconds")
        for result in replay_results
        if result.get("accepted") and isinstance(result.get("wall_seconds"), (int, float))
    ]
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
        "panda_runner_failure_rate": (
            replay_panda_failed / replay_panda_total if replay_panda_total else None
        ),
        "claude_budget_failure_rate": (
            replay_claude_budget / replay_panda_total if replay_panda_total else None
        ),
        "evidence_use_rate": (
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
    record_parser.add_argument("--notes", default="")
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
