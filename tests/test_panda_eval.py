import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "panda_eval.py"
SPEC = importlib.util.spec_from_file_location("panda_eval", MODULE_PATH)
panda_eval = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(panda_eval)


class PandaEvalTests(unittest.TestCase):
    def write_successful_panda_artifacts(self, output_dir: Path, tools=None) -> None:
        tools = tuple(tools or panda_eval.REQUIRED_PANDA_TOOLS)
        output_dir.mkdir(parents=True, exist_ok=True)
        evidence = {
            "schema_version": panda_eval.SCHEMA_VERSION,
            "findings": [
                {
                    "tool": tool,
                    "status": "success",
                    "returncode": 0,
                    "timed_out": False,
                    "raw_output_path": str(output_dir / f"{tool}.txt"),
                }
                for tool in tools
            ],
        }
        manifest = {
            "requested_tools": list(tools),
            "tools": [{"tool": tool, "status": "success"} for tool in tools],
        }
        (output_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (output_dir / "evidence.json").write_text(json.dumps(evidence), encoding="utf-8")
        for tool in tools:
            (output_dir / f"{tool}.summary.json").write_text("{}", encoding="utf-8")
            (output_dir / f"{tool}.txt").write_text("ok", encoding="utf-8")

    def test_init_run_writes_manifest_and_five_pinned_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            args = panda_eval.build_parser().parse_args(["init", "--run-dir", tmpdir])

            with redirect_stdout(io.StringIO()):
                panda_eval.init_run(args)

            manifest = json.loads((Path(tmpdir) / "run_manifest.json").read_text(encoding="utf-8"))
            tasks = json.loads((Path(tmpdir) / "tasks.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["variants"], ["codex_alone", "panda_explore"])
            self.assertEqual(len(manifest["task_ids"]), 5)
            self.assertEqual(len(tasks["tasks"]), 5)
            self.assertTrue(manifest["panda"]["claude_budget_failure_counts_as_failure"])

    def test_init_run_supports_hard_local_mode_without_initial_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            args = panda_eval.build_parser().parse_args([
                "init",
                "--run-dir",
                tmpdir,
                "--eval-mode",
                "hard-local",
            ])

            with redirect_stdout(io.StringIO()):
                panda_eval.init_run(args)

            manifest = json.loads((Path(tmpdir) / "run_manifest.json").read_text(encoding="utf-8"))
            tasks = json.loads((Path(tmpdir) / "tasks.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["eval_mode"], "hard-local")
            self.assertEqual(
                manifest["variants"],
                ["codex_alone_scout", "panda_replay", "panda_replay_second_pass"],
            )
            self.assertEqual(manifest["panda"]["timeout"], panda_eval.HARD_LOCAL_TIMEOUT)
            self.assertEqual(tasks["tasks"], [])

    def test_init_run_accepts_twenty_task_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_file = Path(tmpdir) / "tasks.json"
            tasks_file.write_text(
                json.dumps({"tasks": [{"task_id": f"task-{idx}"} for idx in range(20)]}),
                encoding="utf-8",
            )
            args = panda_eval.build_parser().parse_args([
                "init",
                "--run-dir",
                str(Path(tmpdir) / "run"),
                "--tasks-file",
                str(tasks_file),
            ])

            with redirect_stdout(io.StringIO()):
                panda_eval.init_run(args)

            tasks = json.loads((Path(tmpdir) / "run" / "tasks.json").read_text(encoding="utf-8"))
            self.assertEqual(len(tasks["tasks"]), 20)

    def test_inspect_panda_run_marks_claude_budget_failure_as_panda_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            evidence = {
                "schema_version": panda_eval.SCHEMA_VERSION,
                "findings": [
                    {
                        "tool": "claude",
                        "status": "failure",
                        "returncode": 1,
                        "timed_out": False,
                        "raw_output_path": str(output_dir / "claude.txt"),
                    },
                    {
                        "tool": "opencode",
                        "status": "success",
                        "returncode": 0,
                        "timed_out": False,
                        "raw_output_path": str(output_dir / "opencode.txt"),
                    },
                    {
                        "tool": "qwen",
                        "status": "success",
                        "returncode": 0,
                        "timed_out": False,
                        "raw_output_path": str(output_dir / "qwen.txt"),
                    },
                ],
            }
            (output_dir / "manifest.json").write_text("{}", encoding="utf-8")
            (output_dir / "evidence.json").write_text(json.dumps(evidence), encoding="utf-8")
            for tool in panda_eval.REQUIRED_PANDA_TOOLS:
                (output_dir / f"{tool}.summary.json").write_text("{}", encoding="utf-8")
                (output_dir / f"{tool}.txt").write_text("ok", encoding="utf-8")
            (output_dir / "claude.txt").write_text("Claude rate limit quota exhausted", encoding="utf-8")

            inspection = panda_eval.inspect_panda_run(output_dir)

            self.assertTrue(inspection["panda_run_failed"])
            self.assertTrue(inspection["claude_budget_failure"])
            self.assertTrue(inspection["panda_core_status"]["claude"]["budget_failure"])

    def test_budget_detector_ignores_non_failure_discussion(self) -> None:
        self.assertFalse(panda_eval.contains_budget_failure("Discuss budget-failure detection in the harness."))
        self.assertFalse(panda_eval.contains_budget_failure("Keep prompts bounded to fit the token budget."))
        self.assertTrue(panda_eval.contains_budget_failure("Claude quota exceeded during the run."))
        self.assertTrue(panda_eval.contains_budget_failure("usage limit reached"))

    def test_record_result_uses_manifest_tools_for_codex_only_panda_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            args = panda_eval.build_parser().parse_args(["init", "--run-dir", tmpdir])
            with redirect_stdout(io.StringIO()):
                panda_eval.init_run(args)
            panda_dir = run_dir / "panda"
            self.write_successful_panda_artifacts(panda_dir, tools=("codex",))
            args = panda_eval.build_parser().parse_args([
                "record",
                "--run-dir",
                tmpdir,
                "--task-id",
                "astropy__astropy-14995",
                "--variant",
                "panda_explore",
                "--tests-passed",
                "true",
                "--wall-seconds",
                "20",
                "--panda-output-dir",
                str(panda_dir),
            ])

            with redirect_stdout(io.StringIO()):
                panda_eval.record_result(args)

            result = json.loads(
                (run_dir / "tasks" / "astropy__astropy-14995" / "panda_explore" / "result.json")
                .read_text(encoding="utf-8")
            )
            self.assertFalse(result["panda_run_failed"])
            self.assertEqual(list(result["panda_core_status"]), ["codex"])
            self.assertEqual(result["panda_artifact_failures"], [])

    def test_record_result_persists_task_variant_and_updates_results_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            args = panda_eval.build_parser().parse_args([
                "init",
                "--run-dir",
                tmpdir,
            ])
            with redirect_stdout(io.StringIO()):
                panda_eval.init_run(args)
            args = panda_eval.build_parser().parse_args([
                "record",
                "--run-dir",
                tmpdir,
                "--task-id",
                "django__django-11099",
                "--variant",
                "codex_alone",
                "--tests-passed",
                "true",
                "--regression",
                "false",
                "--wall-seconds",
                "12.5",
                "--workspace-metadata-path",
                str(Path(tmpdir) / "workspace_metadata.json"),
                "--workspace-isolated",
                "true",
            ])

            with redirect_stdout(io.StringIO()):
                panda_eval.record_result(args)

            result = json.loads(
                (Path(tmpdir) / "tasks" / "django__django-11099" / "codex_alone" / "result.json")
                .read_text(encoding="utf-8")
            )
            index = json.loads((Path(tmpdir) / "results.json").read_text(encoding="utf-8"))
            manifest = json.loads((Path(tmpdir) / "run_manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(result["accepted"])
            self.assertEqual(result["wall_seconds"], 12.5)
            self.assertTrue(result["workspace_preparation"]["workspace_isolated"])
            self.assertEqual(
                result["workspace_preparation"]["workspace_metadata_path"],
                str(Path(tmpdir) / "workspace_metadata.json"),
            )
            self.assertEqual(len(index["results"]), 1)
            self.assertEqual(manifest["budget_failures"], [])

    def test_record_result_updates_run_manifest_budget_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            args = panda_eval.build_parser().parse_args(["init", "--run-dir", tmpdir])
            with redirect_stdout(io.StringIO()):
                panda_eval.init_run(args)
            panda_dir = run_dir / "panda"
            evidence = {
                "schema_version": panda_eval.SCHEMA_VERSION,
                "findings": [
                    {
                        "tool": tool,
                        "status": "success",
                        "returncode": 0,
                        "timed_out": False,
                        "raw_output_path": str(panda_dir / f"{tool}.txt"),
                    }
                    for tool in panda_eval.REQUIRED_PANDA_TOOLS
                ],
            }
            panda_dir.mkdir()
            (panda_dir / "manifest.json").write_text("{}", encoding="utf-8")
            (panda_dir / "evidence.json").write_text(json.dumps(evidence), encoding="utf-8")
            for tool in panda_eval.REQUIRED_PANDA_TOOLS:
                (panda_dir / f"{tool}.summary.json").write_text("{}", encoding="utf-8")
                (panda_dir / f"{tool}.txt").write_text("ok", encoding="utf-8")
            (panda_dir / "claude.txt").write_text("usage limit reached", encoding="utf-8")
            args = panda_eval.build_parser().parse_args([
                "record",
                "--run-dir",
                tmpdir,
                "--task-id",
                "astropy__astropy-14995",
                "--variant",
                "panda_explore",
                "--tests-passed",
                "false",
                "--wall-seconds",
                "20",
                "--panda-output-dir",
                str(panda_dir),
            ])

            with redirect_stdout(io.StringIO()):
                panda_eval.record_result(args)

            manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["budget_failures"][0]["task_id"], "astropy__astropy-14995")
            self.assertEqual(manifest["budget_failures"][0]["variant"], "panda_explore")

    def test_summary_metrics_include_panda_failure_and_evidence_rates(self) -> None:
        results = [
            {
                "task_id": "a",
                "variant": "codex_alone",
                "accepted": True,
                "wall_seconds": 10,
            },
            {
                "task_id": "a",
                "variant": "panda_explore",
                "accepted": False,
                "wall_seconds": 20,
                "panda_run_failed": True,
                "claude_budget_failure": True,
                "evidence_used": True,
            },
        ]

        metrics = panda_eval.metric_summary(results)

        self.assertEqual(metrics["codex_alone"]["pass_rate"], 1.0)
        self.assertEqual(metrics["panda_explore"]["pass_rate"], 0.0)
        self.assertEqual(metrics["panda_explore"]["panda_runner_failure_rate"], 1.0)
        self.assertEqual(metrics["panda_explore"]["claude_budget_failure_rate"], 1.0)
        self.assertEqual(metrics["panda_explore"]["evidence_use_rate"], 1.0)

    def test_hard_local_metrics_compute_rescue_rate_for_struggles(self) -> None:
        results = [
            {
                "task_id": "hard-a",
                "variant": "codex_alone_scout",
                "accepted": False,
                "classification": "failed_tests",
                "contaminated": False,
                "benchmark_invalid": False,
                "wall_seconds": 120,
            },
            {
                "task_id": "hard-b",
                "variant": "codex_alone_scout",
                "accepted": True,
                "classification": "accepted",
                "contaminated": False,
                "benchmark_invalid": False,
                "wall_seconds": 80,
            },
            {
                "task_id": "hard-c",
                "variant": "codex_alone_scout",
                "accepted": False,
                "classification": "contaminated",
                "contaminated": True,
                "benchmark_invalid": False,
                "wall_seconds": 50,
            },
            {
                "task_id": "hard-a",
                "variant": "panda_replay",
                "accepted": False,
                "classification": "failed_tests",
                "contaminated": False,
                "benchmark_invalid": False,
                "wall_seconds": 200,
                "panda_run_failed": False,
                "claude_budget_failure": False,
                "evidence_used": True,
                "panda_direction_correct": False,
            },
            {
                "task_id": "hard-a",
                "variant": "panda_replay_second_pass",
                "accepted": True,
                "classification": "accepted",
                "contaminated": False,
                "benchmark_invalid": False,
                "wall_seconds": 180,
                "panda_run_failed": False,
                "claude_budget_failure": False,
                "evidence_used": True,
                "panda_direction_correct": True,
                "panda_missed_contract": True,
                "codex_implementation_error": True,
                "evidence_was_actionable": True,
            },
        ]

        metrics = panda_eval.metric_summary(results)

        self.assertEqual(metrics["codex_scout_pass_rate"], 0.5)
        self.assertEqual(metrics["codex_struggle_count"], 1)
        self.assertEqual(metrics["panda_replay_pass_rate"], 0.0)
        self.assertEqual(metrics["failure_to_success_rescue_rate"], 0.0)
        self.assertEqual(metrics["panda_replay_second_pass_pass_rate"], 1.0)
        self.assertEqual(metrics["second_pass_rescue_rate"], 1.0)
        self.assertEqual(metrics["incremental_second_pass_rescue_count"], 1)
        self.assertEqual(metrics["evidence_use_rate"], 1.0)
        self.assertEqual(metrics["contaminated_task_count"], 1)
        self.assertEqual(metrics["advice_quality"]["panda_direction_correct"]["true_rate"], 0.5)
        self.assertEqual(metrics["advice_quality"]["panda_direction_correct"]["rated_count"], 2)

    def test_hard_local_metrics_include_second_pass_runner_failures(self) -> None:
        results = [
            {
                "task_id": "hard-a",
                "variant": "codex_alone_scout",
                "accepted": False,
                "classification": "failed_tests",
                "contaminated": False,
                "benchmark_invalid": False,
            },
            {
                "task_id": "hard-a",
                "variant": "panda_replay",
                "accepted": False,
                "classification": "failed_tests",
                "contaminated": False,
                "benchmark_invalid": False,
                "panda_run_failed": False,
                "claude_budget_failure": False,
                "evidence_used": True,
            },
            {
                "task_id": "hard-a",
                "variant": "panda_replay_second_pass",
                "accepted": False,
                "classification": "environment_failure",
                "contaminated": False,
                "benchmark_invalid": False,
                "panda_run_failed": True,
                "claude_budget_failure": True,
                "evidence_used": False,
            },
        ]

        metrics = panda_eval.metric_summary(results)

        self.assertEqual(metrics["panda_runner_failure_rate"], 0.5)
        self.assertEqual(metrics["claude_budget_failure_rate"], 0.5)
        self.assertEqual(metrics["evidence_use_rate"], 0.5)
        self.assertEqual(metrics["panda_replay_runner_failure_rate"], 0.0)
        self.assertEqual(metrics["second_pass_runner_failure_rate"], 1.0)

    def test_second_pass_rescue_requires_matching_failed_replay(self) -> None:
        results = [
            {
                "task_id": "hard-a",
                "variant": "codex_alone_scout",
                "accepted": False,
                "classification": "failed_tests",
                "contaminated": False,
                "benchmark_invalid": False,
            },
            {
                "task_id": "hard-a",
                "variant": "panda_replay_second_pass",
                "accepted": True,
                "classification": "accepted",
                "contaminated": False,
                "benchmark_invalid": False,
                "panda_run_failed": False,
                "claude_budget_failure": False,
                "evidence_used": True,
            },
        ]

        metrics = panda_eval.metric_summary(results)

        self.assertIsNone(metrics["second_pass_rescue_rate"])
        self.assertEqual(metrics["incremental_second_pass_rescue_count"], 0)
        self.assertEqual(metrics["second_pass_without_matching_replay_count"], 1)

    def test_record_result_supports_new_variants_and_classification(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            args = panda_eval.build_parser().parse_args([
                "init",
                "--run-dir",
                tmpdir,
                "--eval-mode",
                "hard-local",
            ])
            with redirect_stdout(io.StringIO()):
                panda_eval.init_run(args)
            panda_dir = run_dir / "panda"
            self.write_successful_panda_artifacts(panda_dir)
            scout_args = panda_eval.build_parser().parse_args([
                "record",
                "--run-dir",
                tmpdir,
                "--task-id",
                "hard-a",
                "--variant",
                "codex_alone_scout",
                "--tests-passed",
                "false",
                "--accepted",
                "false",
                "--classification",
                "failed_tests",
                "--wall-seconds",
                "120",
            ])
            replay_args = panda_eval.build_parser().parse_args([
                "record",
                "--run-dir",
                tmpdir,
                "--task-id",
                "hard-a",
                "--variant",
                "panda_replay",
                "--tests-passed",
                "true",
                "--classification",
                "accepted",
                "--wall-seconds",
                "220",
                "--panda-output-dir",
                str(panda_dir),
                "--evidence-used",
            ])
            second_prompt = run_dir / "second" / "panda_prompt.txt"
            second_prompt.parent.mkdir()
            second_prompt.write_text("prompt", encoding="utf-8")
            second_args = panda_eval.build_parser().parse_args([
                "record",
                "--run-dir",
                tmpdir,
                "--task-id",
                "hard-a",
                "--variant",
                "panda_replay_second_pass",
                "--tests-passed",
                "false",
                "--accepted",
                "false",
                "--classification",
                "failed_tests",
                "--wall-seconds",
                "180",
                "--panda-output-dir",
                str(panda_dir),
                "--evidence-used",
                "--second-pass-prompt-path",
                str(second_prompt),
                "--panda-direction-correct",
                "true",
                "--panda-missed-contract",
                "true",
                "--codex-implementation-error",
                "false",
                "--evidence-was-actionable",
                "true",
                "--advice-quality-notes",
                "useful but incomplete",
            ])

            with redirect_stdout(io.StringIO()):
                panda_eval.record_result(scout_args)
                panda_eval.record_result(replay_args)
                panda_eval.record_result(second_args)

            results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))["results"]
            metrics = panda_eval.metric_summary(results)
            self.assertEqual(metrics["codex_struggle_count"], 1)
            self.assertEqual(metrics["failure_to_success_rescue_rate"], 1.0)
            self.assertFalse(results[1]["panda_run_failed"])
            self.assertEqual(results[2]["variant"], "panda_replay_second_pass")
            self.assertTrue(results[2]["second_pass_used"])
            self.assertEqual(results[2]["second_pass_prompt_path"], str(second_prompt))
            self.assertTrue(results[2]["panda_direction_correct"])
            self.assertEqual(results[2]["advice_quality_notes"], "useful but incomplete")

    def test_select_hard_sanitizes_gold_content_and_enforces_repo_cap(self) -> None:
        long_problem = "Regression in behavior. " * 80
        records = [
            {
                "instance_id": "owner__repoA-1",
                "repo": "owner/repoA",
                "base_commit": "a" * 40,
                "problem_statement": long_problem,
                "patch": "diff --git a/a.py b/a.py\n+SECRET_PATCH_CONTENT\n-d\n"
                         "diff --git a/b.py b/b.py\n+x\n-y\n",
                "test_patch": "diff --git a/test_a.py b/test_a.py\n+assert 1\n",
                "FAIL_TO_PASS": "[\"test_a\"]",
            },
            {
                "instance_id": "owner__repoA-2",
                "repo": "owner/repoA",
                "base_commit": "b" * 40,
                "problem_statement": long_problem,
                "patch": "diff --git a/c.py b/c.py\n+x\n-y\n+x\n-y\n+x\n-y\n",
                "test_patch": "diff --git a/test_c.py b/test_c.py\n+assert 1\n",
                "FAIL_TO_PASS": "[\"test_c\"]",
            },
            {
                "instance_id": "owner__repoA-3",
                "repo": "owner/repoA",
                "base_commit": "c" * 40,
                "problem_statement": long_problem,
                "patch": "diff --git a/d.py b/d.py\n+x\n-y\n+x\n-y\n+x\n-y\n",
                "test_patch": "diff --git a/test_d.py b/test_d.py\n+assert 1\n",
                "FAIL_TO_PASS": "[\"test_d\"]",
            },
            {
                "instance_id": "owner__repoB-1",
                "repo": "owner/repoB",
                "base_commit": "d" * 40,
                "problem_statement": long_problem,
                "patch": "diff --git a/e.py b/e.py\n+x\n-y\n+x\n-y\n+x\n-y\n",
                "test_patch": "diff --git a/test_e.py b/test_e.py\n+assert 1\n",
                "FAIL_TO_PASS": "[\"test_e\"]",
            },
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            records_file = Path(tmpdir) / "records.json"
            records_file.write_text(json.dumps(records), encoding="utf-8")
            args = panda_eval.build_parser().parse_args([
                "select-hard",
                "--run-dir",
                tmpdir,
                "--records-file",
                str(records_file),
                "--target-count",
                "3",
                "--repo-cap",
                "2",
            ])

            with redirect_stdout(io.StringIO()):
                panda_eval.select_hard_tasks(args)

            tasks_text = (Path(tmpdir) / "tasks.json").read_text(encoding="utf-8")
            tasks = json.loads(tasks_text)["tasks"]
            repo_counts = {}
            for task in tasks:
                repo_counts[task["repo_hint"]] = repo_counts.get(task["repo_hint"], 0) + 1
                self.assertNotIn("patch", task)
                self.assertNotIn("test_patch", task)
                self.assertNotIn("FAIL_TO_PASS", task)
                self.assertIn("hardness", task)
            self.assertNotIn("SECRET_PATCH_CONTENT", tasks_text)
            self.assertLessEqual(repo_counts["owner/repoA"], 2)
            self.assertEqual(len(tasks), 3)
            manifest = json.loads((Path(tmpdir) / "candidate_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["fallback_source_order"][0]["dataset_name"], "ScaleAI/SWE-bench_Pro")

    def test_inspect_panda_run_fails_on_malformed_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            (output_dir / "manifest.json").write_text("{", encoding="utf-8")
            (output_dir / "evidence.json").write_text("{", encoding="utf-8")

            inspection = panda_eval.inspect_panda_run(output_dir)

            self.assertTrue(inspection["panda_run_failed"])
            self.assertIn("malformed:manifest.json", inspection["artifact_failures"])
            self.assertIn("malformed:evidence.json", inspection["artifact_failures"])

    def test_extract_failing_tests_skips_non_failure_test_log_lines(self) -> None:
        text = "\n".join([
            "INFO Test: setup_database",
            "msg=Test: connection established",
            "--- FAIL: TestStore_FetchWithECR (0.79s)",
            "Error: not equal",
            "Test: TestStore_FetchWithPrivateECR",
            "FAILED tests/test_registry.py::test_private_ecr - AssertionError",
        ])

        names = panda_eval.extract_failing_tests(text)

        self.assertIn("TestStore_FetchWithECR", names)
        self.assertIn("TestStore_FetchWithPrivateECR", names)
        self.assertIn("tests/test_registry.py::test_private_ecr", names)
        self.assertNotIn("setup_database", names)
        self.assertNotIn("connection", names)

    def test_safe_path_slug_redacts_shas_without_colliding(self) -> None:
        first = panda_eval.safe_path_slug("instance_flipt-io__flipt-" + ("a" * 40))
        second = panda_eval.safe_path_slug("instance_flipt-io__flipt-" + ("b" * 40))

        self.assertNotEqual(first, second)
        self.assertIn("redacted-sha", first)
        self.assertNotIn("a" * 40, first)
        self.assertNotIn("b" * 40, second)

    def test_prepare_first_pass_builds_contract_prompt_without_gold_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            workspace = run_dir / "workspace"
            workspace.mkdir()
            (workspace / "pkg.go").write_text("package main\n", encoding="utf-8")
            task_id = "instance_repo__repo-" + ("a" * 40)
            tasks = {
                "schema_version": panda_eval.SCHEMA_VERSION,
                "tasks": [
                    {
                        "task_id": task_id,
                        "repo_hint": "owner/repo",
                        "base_commit": "b" * 40,
                        "problem_statement": "Users persist without Player.UserId.",
                        "patch": "GOLD_PATCH_SHOULD_NOT_APPEAR",
                        "test_patch": "GOLD_TEST_PATCH_SHOULD_NOT_APPEAR",
                        "FAIL_TO_PASS": ["hidden_test_should_not_appear"],
                        "hardness": {"score": 99, "fail_to_pass_count": 7},
                    }
                ],
            }
            (run_dir / "tasks.json").write_text(json.dumps(tasks), encoding="utf-8")
            args = panda_eval.build_parser().parse_args([
                "prepare-first-pass",
                "--run-dir",
                tmpdir,
                "--task-id",
                task_id,
                "--workspace",
                str(workspace),
            ])

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                panda_eval.prepare_first_pass(args)

            command = stdout.getvalue()
            out_dir = panda_eval.safe_result_dir(run_dir, task_id, panda_eval.REPLAY_VARIANT)
            prompt = (out_dir / "panda_prompt.txt").read_text(encoding="utf-8")
            metadata = json.loads((out_dir / "prompt_metadata.json").read_text(encoding="utf-8"))
            self.assertIn("--mode explore", command)
            self.assertIn("--role implementation-review", command)
            self.assertIn("--protocol v2", command)
            self.assertIn("--prompt-file", command)
            self.assertIn("--workspace", command)
            self.assertIn("Contract map", prompt)
            self.assertIn("Additional Panda V2 contract focus", prompt)
            self.assertIn("unexported type names", prompt)
            self.assertIn("Local evidence", prompt)
            self.assertIn("Likely evaluator assertions", prompt)
            self.assertIn("Falsifiers or uncertainties", prompt)
            self.assertIn("Verification plan", prompt)
            self.assertIn("Codex remains the only editor", prompt)
            self.assertNotIn("GOLD_PATCH_SHOULD_NOT_APPEAR", prompt)
            self.assertNotIn("GOLD_TEST_PATCH_SHOULD_NOT_APPEAR", prompt)
            self.assertNotIn("hidden_test_should_not_appear", prompt)
            self.assertNotIn("hardness", prompt)
            self.assertNotIn("fail_to_pass_count", prompt)
            self.assertNotIn("a" * 40, prompt)
            self.assertNotIn("b" * 40, prompt)
            self.assertEqual(metadata["prompt_kind"], "contract_first_first_pass")
            self.assertEqual(metadata["prompt_version"], 2)
            self.assertFalse(metadata["prompt_truncated"])
            self.assertEqual(metadata["workspace_check"]["isolation_status"], "clean")

    def test_prepare_first_pass_prompt_version_two_is_the_main_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            workspace = run_dir / "workspace"
            workspace.mkdir()
            task_id = "hard-a"
            (run_dir / "tasks.json").write_text(
                json.dumps({
                    "schema_version": panda_eval.SCHEMA_VERSION,
                    "tasks": [
                        {
                            "task_id": task_id,
                            "repo_hint": "owner/repo",
                            "base_commit": "c" * 40,
                            "problem_statement": "Player identity mapping fails.",
                        }
                    ],
                }),
                encoding="utf-8",
            )
            args = panda_eval.build_parser().parse_args([
                "prepare-first-pass",
                "--run-dir",
                tmpdir,
                "--task-id",
                task_id,
                "--workspace",
                str(workspace),
                "--prompt-version",
                "2",
            ])

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                panda_eval.prepare_first_pass(args)

            out_dir = panda_eval.safe_result_dir(run_dir, task_id, panda_eval.REPLAY_VARIANT)
            prompt = (out_dir / "panda_prompt.txt").read_text(encoding="utf-8")
            metadata = json.loads((out_dir / "prompt_metadata.json").read_text(encoding="utf-8"))
            self.assertIn("--protocol v2", stdout.getvalue())
            self.assertNotIn("contract-falsifier", stdout.getvalue())
            self.assertIn("Additional Panda V2 contract focus", prompt)
            self.assertIn("field names", prompt)
            self.assertIn("unexported type names", prompt)
            self.assertIn("backward-compatibility seams", prompt)

    def test_prepare_first_pass_accepts_codex_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            workspace = run_dir / "workspace"
            workspace.mkdir()
            task_id = "hard-codex"
            (run_dir / "tasks.json").write_text(
                json.dumps({
                    "schema_version": panda_eval.SCHEMA_VERSION,
                    "tasks": [
                        {
                            "task_id": task_id,
                            "repo_hint": "owner/repo",
                            "problem_statement": "Need a portable reviewer.",
                        }
                    ],
                }),
                encoding="utf-8",
            )
            args = panda_eval.build_parser().parse_args([
                "prepare-first-pass",
                "--run-dir",
                tmpdir,
                "--task-id",
                task_id,
                "--workspace",
                str(workspace),
                "--tool",
                "codex",
            ])

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                panda_eval.prepare_first_pass(args)

            metadata = json.loads(
                (
                    panda_eval.safe_result_dir(run_dir, task_id, panda_eval.REPLAY_VARIANT)
                    / "prompt_metadata.json"
                ).read_text(encoding="utf-8")
            )
            self.assertIn("--tool codex", stdout.getvalue())
            self.assertEqual(metadata["command"][metadata["command"].index("--tool") + 1], "codex")
            self.assertEqual(metadata["prompt_version"], 2)
            self.assertIn("--protocol", metadata["command"])
            self.assertIn("v2", metadata["command"])

    def test_prepare_first_pass_prompt_version_one_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            workspace = run_dir / "workspace"
            workspace.mkdir()
            task_id = "hard-a"
            (run_dir / "tasks.json").write_text(
                json.dumps({
                    "schema_version": panda_eval.SCHEMA_VERSION,
                    "tasks": [
                        {
                            "task_id": task_id,
                            "repo_hint": "owner/repo",
                            "problem_statement": "Player identity mapping fails.",
                        }
                    ],
                }),
                encoding="utf-8",
            )
            with redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    panda_eval.build_parser().parse_args([
                        "prepare-first-pass",
                        "--run-dir",
                        tmpdir,
                        "--task-id",
                        task_id,
                        "--workspace",
                        str(workspace),
                        "--prompt-version",
                        "1",
                    ])

    def test_prepare_first_pass_respects_char_caps(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            workspace = run_dir / "workspace"
            workspace.mkdir()
            task_id = "hard-a"
            (run_dir / "tasks.json").write_text(
                json.dumps({
                    "schema_version": panda_eval.SCHEMA_VERSION,
                    "tasks": [
                        {
                            "task_id": task_id,
                            "repo_hint": "owner/repo",
                            "problem_statement": "x" * 20000,
                        }
                    ],
                }),
                encoding="utf-8",
            )
            args = panda_eval.build_parser().parse_args([
                "prepare-first-pass",
                "--run-dir",
                tmpdir,
                "--task-id",
                task_id,
                "--workspace",
                str(workspace),
            ])

            with redirect_stdout(io.StringIO()):
                panda_eval.prepare_first_pass(args)

            out_dir = panda_eval.safe_result_dir(run_dir, task_id, panda_eval.REPLAY_VARIANT)
            prompt = (out_dir / "panda_prompt.txt").read_text(encoding="utf-8")
            metadata = json.loads((out_dir / "prompt_metadata.json").read_text(encoding="utf-8"))
            self.assertLessEqual(len(prompt), panda_eval.FIRST_PASS_PROMPT_MAX_CHARS)
            self.assertTrue(metadata["sections"]["task"]["truncated"])

    def test_prepare_workspace_excludes_git_and_caches(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "source"
            source.mkdir()
            (source / ".git").mkdir()
            nested = source / "submodule"
            nested.mkdir()
            (nested / ".git").write_text("gitdir: ../.git/modules/submodule", encoding="utf-8")
            (source / "__pycache__").mkdir()
            (source / "__pycache__" / "x.pyc").write_text("cache", encoding="utf-8")
            (source / "node_modules").mkdir()
            (source / "node_modules" / "pkg.js").write_text("cache", encoding="utf-8")
            (source / "main.py").write_text("print('ok')\n", encoding="utf-8")
            args = panda_eval.build_parser().parse_args([
                "prepare-workspace",
                "--run-dir",
                str(root / "run"),
                "--task-id",
                "task-a",
                "--source-workspace",
                str(source),
            ])

            with redirect_stdout(io.StringIO()):
                panda_eval.prepare_workspace(args)

            destination = panda_eval.safe_task_dir(root / "run", "task-a") / "workspace"
            metadata = json.loads((panda_eval.safe_task_dir(root / "run", "task-a") / "workspace_metadata.json").read_text(encoding="utf-8"))
            self.assertTrue((destination / "main.py").exists())
            self.assertFalse((destination / ".git").exists())
            self.assertFalse((destination / "submodule" / ".git").exists())
            self.assertFalse((destination / "__pycache__").exists())
            self.assertFalse((destination / "node_modules").exists())
            self.assertTrue(metadata["isolated"])
            self.assertGreaterEqual(metadata["file_count"], 1)

    def test_check_workspace_flags_leakage(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir()
            (workspace / ".git").mkdir()
            nested = workspace / "nested"
            nested.mkdir()
            (nested / ".git").write_text("gitdir: ../.git/modules/nested", encoding="utf-8")
            (workspace / "task.json").write_text(json.dumps({"patch": "gold"}), encoding="utf-8")
            subdir = workspace / "subdir"
            subdir.mkdir()
            (subdir / "meta.json").write_text(json.dumps({"test_patch": "gold"}), encoding="utf-8")
            target_commit = "c" * 40
            (workspace / "notes.txt").write_text(f"target {target_commit}", encoding="utf-8")
            outside = Path(tmpdir) / "outside.txt"
            outside.write_text("outside", encoding="utf-8")
            (workspace / "outside-link").symlink_to(outside)

            result = panda_eval.check_workspace_leakage(
                workspace,
                sensitive_strings=[target_commit],
            )

            violations = "\n".join(result["violations"])
            self.assertFalse(result["isolated"])
            self.assertIn("git_directory:.git", violations)
            self.assertIn("git_file:nested/.git", violations)
            self.assertIn("gold_benchmark_fields:task.json:patch", violations)
            self.assertIn("gold_benchmark_fields:subdir/meta.json:test_patch", violations)
            self.assertIn("sensitive_commit_string:notes.txt", violations)
            self.assertIn("out_of_tree_symlink:outside-link", violations)

    def test_prepare_first_and_second_pass_strict_workspace_raise_on_leakage(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            workspace = run_dir / "workspace"
            workspace.mkdir()
            (workspace / ".git").mkdir()
            task_id = "hard-a"
            (run_dir / "tasks.json").write_text(
                json.dumps({"schema_version": panda_eval.SCHEMA_VERSION, "tasks": [{"task_id": task_id}]}),
                encoding="utf-8",
            )
            first_args = panda_eval.build_parser().parse_args([
                "prepare-first-pass",
                "--run-dir",
                tmpdir,
                "--task-id",
                task_id,
                "--workspace",
                str(workspace),
                "--strict-workspace",
            ])
            second_args = panda_eval.build_parser().parse_args([
                "prepare-second-pass",
                "--run-dir",
                tmpdir,
                "--task-id",
                task_id,
                "--workspace",
                str(workspace),
                "--strict-workspace",
            ])

            with self.assertRaises(SystemExit):
                panda_eval.prepare_first_pass(first_args)
            with self.assertRaises(SystemExit):
                panda_eval.prepare_second_pass(second_args)

            first_metadata = json.loads(
                (panda_eval.safe_result_dir(run_dir, task_id, panda_eval.REPLAY_VARIANT) / "prompt_metadata.json")
                .read_text(encoding="utf-8")
            )
            second_metadata = json.loads(
                (panda_eval.safe_result_dir(run_dir, task_id, panda_eval.SECOND_PASS_VARIANT) / "prompt_metadata.json")
                .read_text(encoding="utf-8")
            )
            self.assertTrue(first_metadata["strict_workspace_failed"])
            self.assertTrue(second_metadata["strict_workspace_failed"])

    def test_prepare_workspace_fails_on_out_of_tree_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "source"
            source.mkdir()
            outside = root / "outside.txt"
            outside.write_text("outside", encoding="utf-8")
            (source / "outside-link").symlink_to(outside)
            args = panda_eval.build_parser().parse_args([
                "prepare-workspace",
                "--run-dir",
                str(root / "run"),
                "--task-id",
                "task-a",
                "--source-workspace",
                str(source),
            ])

            with self.assertRaises(SystemExit):
                with redirect_stdout(io.StringIO()):
                    panda_eval.prepare_workspace(args)

            metadata = json.loads((panda_eval.safe_task_dir(root / "run", "task-a") / "workspace_metadata.json").read_text(encoding="utf-8"))
            self.assertFalse(metadata["isolated"])
            self.assertTrue(any("out_of_tree_symlink" in violation for violation in metadata["violations"]))

    def test_prepare_second_pass_builds_bounded_prompt_without_gold_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            task_id = "instance_flipt-io__flipt-96820c3ad10b0b2305e8877b6b303f7fafdf815f"
            tasks = {
                "schema_version": panda_eval.SCHEMA_VERSION,
                "tasks": [
                    {
                        "task_id": task_id,
                        "repo_hint": "flipt-io/flipt",
                        "base_commit": "b" * 40,
                        "problem_statement": "AWS ECR credentials expire and public/private registries differ.",
                        "patch": "GOLD_PATCH_SHOULD_NOT_APPEAR",
                        "test_patch": "GOLD_TEST_PATCH_SHOULD_NOT_APPEAR",
                        "hardness": {
                            "score": 90,
                            "patch_changed_files": 3,
                            "patch_changed_lines": 44,
                            "test_patch_changed_files": 2,
                            "test_patch_changed_lines": 55,
                            "fail_to_pass_count": 4,
                        },
                    }
                ],
            }
            (run_dir / "tasks.json").write_text(json.dumps(tasks), encoding="utf-8")
            first_pass = run_dir / "panda-first"
            first_pass.mkdir()
            evidence = {
                "schema_version": panda_eval.SCHEMA_VERSION,
                "findings": [
                    {
                        "tool": "claude",
                        "status": "success",
                        "timed_out": False,
                        "recommendation": "Use CredentialsStore and defaultClientFunc.",
                        "verification_plan": "Run TestStore_FetchWithECR.",
                        "raw_output_path": str(first_pass / "claude.txt"),
                    }
                ],
            }
            (first_pass / "evidence.json").write_text(json.dumps(evidence), encoding="utf-8")
            (first_pass / "claude.summary.json").write_text("{}", encoding="utf-8")
            patch_path = run_dir / "patch.diff"
            patch_path.write_text(
                "diff --git a/internal/oci/ecr/ecr.go b/internal/oci/ecr/ecr.go\n"
                + "\n".join(f"+line {idx}" for idx in range(260)),
                encoding="utf-8",
            )
            test_output = run_dir / "stdout.log"
            test_output.write_text(
                "\n".join([
                    "=== RUN   TestStore_FetchWithECR",
                    "/app/internal/oci/file_test.go:415: Missing Region",
                    "--- FAIL: TestStore_FetchWithECR (0.79s)",
                    "FAIL go.flipt.io/flipt/internal/oci",
                ])
                + "\n"
                + ("noise\n" * 1000),
                encoding="utf-8",
            )
            args = panda_eval.build_parser().parse_args([
                "prepare-second-pass",
                "--run-dir",
                tmpdir,
                "--task-id",
                task_id,
                "--first-pass-panda-output-dir",
                str(first_pass),
                "--patch-path",
                str(patch_path),
                "--test-output-path",
                str(test_output),
                "--workspace",
                str(run_dir / "repo"),
            ])

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                panda_eval.prepare_second_pass(args)

            command = stdout.getvalue()
            out_dir = panda_eval.safe_result_dir(run_dir, task_id, panda_eval.SECOND_PASS_VARIANT)
            prompt = (out_dir / "panda_prompt.txt").read_text(encoding="utf-8")
            metadata = json.loads((out_dir / "prompt_metadata.json").read_text(encoding="utf-8"))
            self.assertIn("--prompt-file", command)
            self.assertIn("--role debugging", command)
            self.assertIn("--protocol v2", command)
            self.assertIn("What did the first Panda pass miss", prompt)
            self.assertIn("TestStore_FetchWithECR", prompt)
            self.assertIn("internal/oci/file_test.go", prompt)
            self.assertIn("CredentialsStore", prompt)
            self.assertNotIn("GOLD_PATCH_SHOULD_NOT_APPEAR", prompt)
            self.assertNotIn("GOLD_TEST_PATCH_SHOULD_NOT_APPEAR", prompt)
            self.assertNotIn("hardness:", prompt)
            self.assertNotIn("test_patch_changed", prompt)
            self.assertNotIn("fail_to_pass_count", prompt)
            self.assertNotIn("96820c3ad10b0b2305e8877b6b303f7fafdf815f", prompt)
            self.assertNotIn("b" * 40, prompt)
            self.assertTrue(metadata["sections"]["patch"]["truncated"])
            self.assertIn("TestStore_FetchWithECR", metadata["failing_tests"])
            self.assertTrue(any("internal/oci/file_test.go" in hint for hint in metadata["path_hints"]))

    def test_prepare_second_pass_role_override_works(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            task_id = "hard-a"
            (run_dir / "tasks.json").write_text(
                json.dumps({"schema_version": panda_eval.SCHEMA_VERSION, "tasks": [{"task_id": task_id}]}),
                encoding="utf-8",
            )
            args = panda_eval.build_parser().parse_args([
                "prepare-second-pass",
                "--run-dir",
                tmpdir,
                "--task-id",
                task_id,
                "--role",
                "code-review",
            ])

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                panda_eval.prepare_second_pass(args)

            self.assertIn("--role code-review", stdout.getvalue())

    def test_prepare_falsifier_builds_one_pass_protocol_v2_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            task_id = "hard-a"
            first_pass = run_dir / "first-pass"
            first_pass.mkdir()
            (first_pass / "evidence.json").write_text(
                json.dumps({
                    "schema_version": panda_eval.SCHEMA_VERSION,
                    "findings": [
                        {
                            "tool": "qwen",
                            "status": "success",
                            "recommendation": "Use user_id.",
                            "raw_output_path": str(first_pass / "qwen.txt"),
                        }
                    ],
                }),
                encoding="utf-8",
            )
            (first_pass / "panda_contracts.v2.json").write_text(
                json.dumps({
                    "schema_version": 1,
                    "protocol_version": "v2",
                    "artifact_kind": "contracts",
                    "prompt_version": 2,
                    "parse_quality": {
                        "reports_total": 1,
                        "parsed": 1,
                        "missing": 0,
                        "malformed": 0,
                        "invalid": 0,
                        "multiple": 0,
                        "fallback_parsed": 0,
                        "fallback_counts": {},
                        "claims_total": 1,
                    },
                    "reports": [
                        {
                            "tool": "qwen",
                            "parse_status": "parsed",
                            "warnings": [],
                            "files_inspected": ["model/player.go"],
                            "claims": [
                                {
                                    "claim": "Player.UserId is the stable ownership key",
                                    "status": "inferred",
                                    "evidence_refs": ["model/player.go"],
                                }
                            ],
                        }
                    ],
                }),
                encoding="utf-8",
            )
            (run_dir / "tasks.json").write_text(
                json.dumps({
                    "schema_version": panda_eval.SCHEMA_VERSION,
                    "tasks": [
                        {
                            "task_id": task_id,
                            "repo_hint": "owner/repo",
                            "problem_statement": "Player identity mapping fails.",
                        }
                    ],
                }),
                encoding="utf-8",
            )
            args = panda_eval.build_parser().parse_args([
                "prepare-falsifier",
                "--run-dir",
                tmpdir,
                "--task-id",
                task_id,
                "--first-pass-panda-output-dir",
                str(first_pass),
            ])

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                panda_eval.prepare_falsifier(args)

            out_dir = panda_eval.safe_result_dir(run_dir, task_id, panda_eval.FALSIFIER_VARIANT)
            prompt = (out_dir / "panda_prompt.txt").read_text(encoding="utf-8")
            metadata = json.loads((out_dir / "prompt_metadata.json").read_text(encoding="utf-8"))
            command = stdout.getvalue()
            self.assertIn("--role contract-falsifier", command)
            self.assertIn("--protocol v2", command)
            self.assertIn("one-pass Panda contract falsifier", prompt)
            self.assertIn("Player.UserId is the stable ownership key", prompt)
            self.assertNotIn("parse_quality", prompt)
            self.assertNotIn("fallback_parsed", prompt)
            self.assertNotIn("c" * 40, prompt)
            self.assertEqual(metadata["prompt_kind"], "contract_falsifier")
            self.assertEqual(metadata["prompt_version"], 2)
            self.assertTrue(metadata["expected_artifact"].endswith("panda_falsifier.v2.json"))

    def test_prepare_falsifier_missing_auto_contracts_path_uses_clean_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            task_id = "hard-a"
            first_pass = run_dir / "first-pass"
            first_pass.mkdir()
            (first_pass / "evidence.json").write_text(
                json.dumps({"schema_version": panda_eval.SCHEMA_VERSION, "findings": []}),
                encoding="utf-8",
            )
            (run_dir / "tasks.json").write_text(
                json.dumps({
                    "schema_version": panda_eval.SCHEMA_VERSION,
                    "tasks": [{"task_id": task_id, "problem_statement": "No contracts sidecar yet."}],
                }),
                encoding="utf-8",
            )
            args = panda_eval.build_parser().parse_args([
                "prepare-falsifier",
                "--run-dir",
                tmpdir,
                "--task-id",
                task_id,
                "--first-pass-panda-output-dir",
                str(first_pass),
            ])

            with redirect_stdout(io.StringIO()):
                panda_eval.prepare_falsifier(args)

            out_dir = panda_eval.safe_result_dir(run_dir, task_id, panda_eval.FALSIFIER_VARIANT)
            prompt = (out_dir / "panda_prompt.txt").read_text(encoding="utf-8")
            metadata = json.loads((out_dir / "prompt_metadata.json").read_text(encoding="utf-8"))
            self.assertIn("Panda V2 contracts artifact path was not provided.", prompt)
            self.assertIn("missing:panda_contracts_v2_path", metadata["warnings"])
            self.assertNotIn("Could not load Panda V2 contracts artifact", prompt)

    def test_prepare_second_pass_handles_missing_and_malformed_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            task_id = "hard-a"
            (run_dir / "tasks.json").write_text(
                json.dumps({"schema_version": panda_eval.SCHEMA_VERSION, "tasks": [{"task_id": task_id}]}),
                encoding="utf-8",
            )
            first_pass = run_dir / "panda-first"
            first_pass.mkdir()
            (first_pass / "evidence.json").write_text("{", encoding="utf-8")
            args = panda_eval.build_parser().parse_args([
                "prepare-second-pass",
                "--run-dir",
                tmpdir,
                "--task-id",
                task_id,
                "--first-pass-panda-output-dir",
                str(first_pass),
            ])

            with redirect_stdout(io.StringIO()):
                panda_eval.prepare_second_pass(args)

            metadata = json.loads(
                (panda_eval.safe_result_dir(run_dir, task_id, panda_eval.SECOND_PASS_VARIANT) / "prompt_metadata.json")
                .read_text(encoding="utf-8")
            )
            self.assertTrue(any("malformed" in warning for warning in metadata["warnings"]))
            self.assertIn("missing:candidate_patch", metadata["warnings"])
            self.assertIn("missing:failed_test_output", metadata["warnings"])

    def test_canary_skip_real_panda_uses_dry_run_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            args = panda_eval.build_parser().parse_args([
                "canary",
                "--run-dir",
                tmpdir,
                "--skip-real-panda",
            ])

            with patch.object(
                panda_eval,
                "find_lingering_processes",
                return_value={"checked": True, "matches": []},
            ):
                with redirect_stdout(io.StringIO()):
                    status = panda_eval.run_canary(args)

            result = json.loads((Path(tmpdir) / "canary" / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(status, 0)
            self.assertTrue(result["ok"])
            self.assertTrue((Path(tmpdir) / "canary" / "panda-dry-run" / "evidence.json").exists())

    def test_agents_md_has_required_sections_and_existing_top_level_paths(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "AGENTS.md").read_text(encoding="utf-8")
        for section in [
            "## Repo Map",
            "## Edit Order",
            "## Main Flow Rules",
            "## Artifact Rules",
            "## Prompt Version Rules",
            "## Test Expectations",
        ]:
            self.assertIn(section, text)
        for path in ["SKILL.md", "scripts", "src/panda_v2", "tests", "references"]:
            self.assertTrue((root / path).exists(), path)
        for word in [
            "schemas",
            "extractors",
            "prompts",
            "artifacts",
            "script integration",
            "eval integration",
            "docs",
            "tests",
        ]:
            self.assertIn(word, text.lower())


if __name__ == "__main__":
    unittest.main()
