import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "panda_eval.py"
SPEC = importlib.util.spec_from_file_location("panda_eval", MODULE_PATH)
panda_eval = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(panda_eval)


class PandaEvalTests(unittest.TestCase):
    def write_successful_panda_artifacts(self, output_dir: Path) -> None:
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
                for tool in panda_eval.REQUIRED_PANDA_TOOLS
            ],
        }
        (output_dir / "manifest.json").write_text("{}", encoding="utf-8")
        (output_dir / "evidence.json").write_text(json.dumps(evidence), encoding="utf-8")
        for tool in panda_eval.REQUIRED_PANDA_TOOLS:
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
            self.assertEqual(manifest["variants"], ["codex_alone_scout", "panda_replay"])
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
                "accepted": True,
                "classification": "accepted",
                "contaminated": False,
                "benchmark_invalid": False,
                "wall_seconds": 200,
                "panda_run_failed": False,
                "claude_budget_failure": False,
                "evidence_used": True,
            },
        ]

        metrics = panda_eval.metric_summary(results)

        self.assertEqual(metrics["codex_scout_pass_rate"], 0.5)
        self.assertEqual(metrics["codex_struggle_count"], 1)
        self.assertEqual(metrics["panda_replay_pass_rate"], 1.0)
        self.assertEqual(metrics["failure_to_success_rescue_rate"], 1.0)
        self.assertEqual(metrics["evidence_use_rate"], 1.0)
        self.assertEqual(metrics["contaminated_task_count"], 1)

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

            with redirect_stdout(io.StringIO()):
                panda_eval.record_result(scout_args)
                panda_eval.record_result(replay_args)

            results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))["results"]
            metrics = panda_eval.metric_summary(results)
            self.assertEqual(metrics["codex_struggle_count"], 1)
            self.assertEqual(metrics["failure_to_success_rescue_rate"], 1.0)
            self.assertFalse(results[1]["panda_run_failed"])

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


if __name__ == "__main__":
    unittest.main()
