import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Optional
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "consult_ai_team.py"
SPEC = importlib.util.spec_from_file_location("consult_ai_team", MODULE_PATH)
consult_ai_team = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(consult_ai_team)


class ExecutionModeTests(unittest.TestCase):
    def test_auto_parallelizes_read_only_modes(self) -> None:
        self.assertTrue(consult_ai_team.should_run_parallel("auto", "advisory", 2))
        self.assertTrue(consult_ai_team.should_run_parallel("auto", "explore", 2))

    def test_patch_mode_never_parallelizes(self) -> None:
        self.assertFalse(consult_ai_team.should_run_parallel("auto", "patch", 2))
        self.assertFalse(consult_ai_team.should_run_parallel("parallel", "patch", 2))

    def test_single_tool_never_parallelizes(self) -> None:
        self.assertFalse(consult_ai_team.should_run_parallel("parallel", "explore", 1))

    def test_sequential_override(self) -> None:
        self.assertFalse(consult_ai_team.should_run_parallel("sequential", "explore", 2))

    def test_opencode_tools_still_parallelize_by_default(self) -> None:
        self.assertTrue(consult_ai_team.should_run_parallel("auto", "explore", 2))
        parallel, serialized = consult_ai_team.split_serialized_opencode_commands({
            "claude": ["claude"],
            "opencode": ["opencode"],
            "qwen": ["opencode"],
        })
        self.assertEqual(list(parallel), ["claude"])
        self.assertEqual(list(serialized), ["opencode", "qwen"])


class ArgumentValidationTests(unittest.TestCase):
    def parse_with(self, argv: list[str], env: Optional[dict[str, str]] = None):
        env = env or {}
        with patch.object(sys, "argv", ["consult_ai_team.py", *argv]):
            with patch.dict(os.environ, env, clear=True):
                with redirect_stderr(io.StringIO()):
                    return consult_ai_team.parse_args()

    def test_invalid_execution_env_var_is_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            self.parse_with(["--prompt", "test"], {"AI_TEAM_EXECUTION": "paralel"})

    def test_invalid_approval_env_var_is_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            self.parse_with(["--prompt", "test"], {"AI_TEAM_APPROVAL_MODE": "sure"})

    def test_patch_mode_is_disabled_with_friendly_message(self) -> None:
        with patch.object(sys, "argv", ["consult_ai_team.py", "--mode", "patch", "--prompt", "test"]):
            with patch.dict(os.environ, {}, clear=True):
                stderr = io.StringIO()
                with redirect_stderr(stderr):
                    with self.assertRaises(SystemExit):
                        consult_ai_team.parse_args()
        self.assertIn("Patch mode is disabled", stderr.getvalue())

    def test_invalid_mode_is_rejected_after_custom_patch_check(self) -> None:
        with self.assertRaises(SystemExit):
            self.parse_with(["--mode", "unknown", "--prompt", "test"])

    def test_session_without_value_creates_new_session_mode(self) -> None:
        args = self.parse_with(["--session", "--prompt", "test"])
        self.assertEqual(args.session, "")

    def test_default_tool_runs_all_cores(self) -> None:
        args = self.parse_with(["--prompt", "test"])
        self.assertEqual(args.tool, "all")
        self.assertEqual(args.protocol, "v2")
        self.assertEqual(consult_ai_team.requested_tools(args.tool), ["claude", "opencode", "qwen"])

    def test_invalid_protocol_is_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            self.parse_with(["--protocol", "v1", "--prompt", "test"])

    def test_invalid_profile_is_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            self.parse_with(["--profile", "huge", "--prompt", "test"])

    def test_invalid_claude_effort_is_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            self.parse_with(["--claude-effort", "normal", "--prompt", "test"])

    def test_dot_session_ids_are_rejected(self) -> None:
        for session_id in [".", ".."]:
            with self.subTest(session_id=session_id):
                with self.assertRaises(SystemExit):
                    consult_ai_team.validate_session_id(session_id)

    def test_timeout_lower_bounds_are_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            self.parse_with(["--timeout", "0", "--prompt", "test"])
        with self.assertRaises(SystemExit):
            self.parse_with(["--straggler-timeout", "0", "--prompt", "test"])

    def test_serialize_opencode_flag_and_env_are_supported(self) -> None:
        args = self.parse_with(["--serialize-opencode", "--prompt", "test"])
        self.assertTrue(consult_ai_team.opencode_serialization_enabled(args))

        args = self.parse_with(["--prompt", "test"])
        with patch.dict(os.environ, {"PANDA_SERIALIZE_OPENCODE": "1"}):
            self.assertTrue(consult_ai_team.opencode_serialization_enabled(args))


class ProfileResolutionTests(unittest.TestCase):
    def parse_with(self, argv: list[str], env: Optional[dict[str, str]] = None):
        env = env or {}
        with patch.object(sys, "argv", ["consult_ai_team.py", *argv]):
            with patch.dict(os.environ, env, clear=True):
                with redirect_stderr(io.StringIO()):
                    return consult_ai_team.parse_args()

    def test_role_defaults_resolve_expected_profiles(self) -> None:
        expected = {
            "brainstorm": ("balanced", "sonnet", "high"),
            "research": ("deep", "opus", "max"),
            "planning": ("deep", "opus", "max"),
            "implementation-review": ("deep", "opus", "max"),
            "debugging": ("balanced", "sonnet", "high"),
            "code-review": ("balanced", "sonnet", "high"),
            "contract-falsifier": ("fast", "sonnet", "medium"),
            "test-plan": ("fast", "sonnet", "medium"),
        }

        for role, (profile, claude_model, claude_effort) in expected.items():
            with self.subTest(role=role):
                args = self.parse_with(["--role", role, "--prompt", "test"])
                resolution = args.profile_resolution
                self.assertEqual(resolution["profile"], profile)
                self.assertEqual(resolution["profile_source"], "role_default")
                self.assertEqual(resolution["effective_models"]["claude"], claude_model)
                self.assertEqual(resolution["effective_effort"]["claude"], claude_effort)
                self.assertEqual(
                    resolution["effective_models"]["opencode"],
                    consult_ai_team.DEFAULT_OPENCODE_MODEL,
                )
                self.assertEqual(
                    resolution["effective_models"]["qwen"],
                    consult_ai_team.DEFAULT_QWEN_MODEL,
                )

    def test_explicit_profile_overrides_role_default(self) -> None:
        args = self.parse_with(["--role", "planning", "--profile", "fast", "--prompt", "test"])
        resolution = args.profile_resolution

        self.assertEqual(resolution["profile"], "fast")
        self.assertEqual(resolution["profile_source"], "cli")
        self.assertEqual(resolution["effective_models"]["claude"], "sonnet")
        self.assertEqual(resolution["effective_effort"]["claude"], "medium")

    def test_explicit_model_and_effort_flags_override_profile(self) -> None:
        args = self.parse_with([
            "--profile",
            "deep",
            "--claude-model",
            "sonnet",
            "--claude-effort",
            "high",
            "--opencode-model",
            "provider/model",
            "--prompt",
            "test",
        ])
        resolution = args.profile_resolution

        self.assertEqual(resolution["profile"], "deep")
        self.assertEqual(resolution["effective_models"]["claude"], "sonnet")
        self.assertEqual(resolution["effective_effort"]["claude"], "high")
        self.assertEqual(resolution["effective_models"]["opencode"], "provider/model")

    def test_qwen_model_flag_overrides_profile(self) -> None:
        args = self.parse_with([
            "--profile",
            "deep",
            "--qwen-model",
            "provider/qwen",
            "--prompt",
            "test",
        ])

        self.assertEqual(args.profile_resolution["effective_models"]["qwen"], "provider/qwen")

    def test_opencode_env_default_is_used_when_profile_is_not_explicit(self) -> None:
        args = self.parse_with(["--role", "planning", "--prompt", "test"], {"OPENCODE_MODEL": "provider/env"})

        self.assertEqual(args.profile_resolution["profile"], "deep")
        self.assertEqual(args.profile_resolution["effective_models"]["opencode"], "provider/env")

    def test_explicit_profile_outranks_opencode_env_default(self) -> None:
        args = self.parse_with(
            ["--profile", "fast", "--prompt", "test"],
            {"OPENCODE_MODEL": "provider/env"},
        )

        self.assertEqual(
            args.profile_resolution["effective_models"]["opencode"],
            consult_ai_team.DEFAULT_OPENCODE_MODEL,
        )

    def test_opencode_command_for_glm_never_includes_variant(self) -> None:
        args = self.parse_with(["--tool", "opencode", "--profile", "deep", "--prompt", "test"])

        commands, _ = consult_ai_team.build_commands(args, "prompt", Path("/tmp"))

        self.assertIn("--model", commands["opencode"])
        self.assertIn(consult_ai_team.DEFAULT_OPENCODE_MODEL, commands["opencode"])
        self.assertNotIn("--variant", commands["opencode"])

    def test_qwen_command_uses_qwen_model_without_variant(self) -> None:
        args = self.parse_with(["--tool", "qwen", "--profile", "deep", "--prompt", "test"])

        commands, _ = consult_ai_team.build_commands(args, "prompt", Path("/tmp"))

        self.assertEqual(list(commands), ["qwen"])
        self.assertIn("--model", commands["qwen"])
        self.assertIn(consult_ai_team.DEFAULT_QWEN_MODEL, commands["qwen"])
        self.assertNotIn("--variant", commands["qwen"])

    def test_all_tool_runs_three_cores(self) -> None:
        args = self.parse_with(["--tool", "all", "--profile", "fast", "--prompt", "test"])

        with patch.object(consult_ai_team, "claude_supports_effort", return_value=False):
            commands, _ = consult_ai_team.build_commands(args, "prompt", Path("/tmp"))

        self.assertEqual(list(commands), ["claude", "opencode", "qwen"])

    def test_legacy_both_tool_is_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            self.parse_with(["--tool", "both", "--profile", "fast", "--prompt", "test"])


class FailureResultTests(unittest.TestCase):
    def test_failed_tool_result_preserves_manifest_shape(self) -> None:
        result = consult_ai_team.failed_tool_result(
            "claude",
            ["claude", "-p", "test"],
            Path("/tmp"),
            RuntimeError("boom"),
        )

        self.assertEqual(result["tool"], "claude")
        self.assertEqual(result["returncode"], -1)
        self.assertFalse(result["timed_out"])
        self.assertIn("RuntimeError: boom", result["stderr"])
        self.assertEqual(result["started_at"], result["finished_at"])


class JsonHelperTests(unittest.TestCase):
    def test_write_json_writes_atomically_shaped_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "nested" / "data.json"

            consult_ai_team.write_json(path, {"ok": True})

            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"ok": True})
            self.assertEqual(list(path.parent.glob("*.tmp")), [])


class PromptCompatibilityTests(unittest.TestCase):
    def test_default_consultation_prompt_uses_v2_contract_protocol(self) -> None:
        prompt = consult_ai_team.consultation_prompt(
            "advisory",
            "brainstorm",
            "unsupervised",
            "test",
        )

        self.assertIn("You are advising Codex as an independent collaborator.", prompt)
        self.assertIn("Panda V2 contract artifact", prompt)
        self.assertIn("panda_contracts_v2", prompt)
        self.assertIn("Double-escape regex or path backslashes", prompt)


class ManifestTests(unittest.TestCase):
    def parse_with(self, argv: list[str]):
        with patch.object(sys, "argv", ["consult_ai_team.py", *argv]):
            with patch.dict(os.environ, {}, clear=True):
                with redirect_stderr(io.StringIO()):
                    return consult_ai_team.parse_args()

    def test_one_shot_manifest_includes_profile_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            args = self.parse_with([
                "--tool",
                "all",
                "--dry-run",
                "--role",
                "planning",
                "--output-dir",
                tmpdir,
                "--prompt",
                "test",
            ])
            prompt = consult_ai_team.consultation_prompt(args.mode, args.role, args.approval_mode, "test")

            with patch.object(consult_ai_team, "claude_supports_effort", return_value=True):
                with redirect_stdout(io.StringIO()):
                    consult_ai_team.run_one_shot(args, prompt)

            manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema_version"], consult_ai_team.SCHEMA_VERSION)
            self.assertEqual(manifest["profile"], "deep")
            self.assertEqual(manifest["profile_source"], "role_default")
            self.assertEqual(manifest["cost_tier"], "high")
            self.assertEqual(manifest["effective_models"]["claude"], "opus")
            self.assertEqual(manifest["effective_models"]["opencode"], consult_ai_team.DEFAULT_OPENCODE_MODEL)
            self.assertEqual(manifest["effective_models"]["qwen"], consult_ai_team.DEFAULT_QWEN_MODEL)
            self.assertEqual(manifest["requested_tools"], ["claude", "opencode", "qwen"])
            self.assertEqual(
                manifest["active_models"],
                {
                    "claude": "opus",
                    "opencode": consult_ai_team.DEFAULT_OPENCODE_MODEL,
                    "qwen": consult_ai_team.DEFAULT_QWEN_MODEL,
                },
            )
            self.assertEqual(manifest["effective_effort"]["claude"], "max")
            self.assertEqual(manifest["applied_effort"]["claude"], "max")
            self.assertTrue(manifest["effort_support"]["claude"])
            claude_command = manifest["tools"][0]["command"]
            self.assertIn("--model", claude_command)
            self.assertIn("opus", claude_command)
            self.assertIn("--effort", claude_command)
            self.assertIn("max", claude_command)

    def test_manifest_records_unsupported_claude_effort_without_passing_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            args = self.parse_with([
                "--tool",
                "claude",
                "--dry-run",
                "--role",
                "planning",
                "--output-dir",
                tmpdir,
                "--prompt",
                "test",
            ])
            prompt = consult_ai_team.consultation_prompt(args.mode, args.role, args.approval_mode, "test")

            with patch.object(consult_ai_team, "claude_supports_effort", return_value=False):
                with redirect_stdout(io.StringIO()):
                    consult_ai_team.run_one_shot(args, prompt)

            manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            claude_command = manifest["tools"][0]["command"]
            self.assertEqual(manifest["requested_tools"], ["claude"])
            self.assertEqual(manifest["active_models"], {"claude": "opus"})
            self.assertEqual(manifest["effective_effort"]["claude"], "max")
            self.assertIsNone(manifest["applied_effort"]["claude"])
            self.assertFalse(manifest["effort_support"]["claude"])
            self.assertNotIn("--effort", claude_command)

    def test_single_opencode_manifest_records_only_active_opencode_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            args = self.parse_with([
                "--tool",
                "opencode",
                "--dry-run",
                "--profile",
                "fast",
                "--output-dir",
                tmpdir,
                "--prompt",
                "test",
            ])
            prompt = consult_ai_team.consultation_prompt(args.mode, args.role, args.approval_mode, "test")

            with redirect_stdout(io.StringIO()):
                consult_ai_team.run_one_shot(args, prompt)

            manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["requested_tools"], ["opencode"])
            self.assertEqual(manifest["active_models"], {"opencode": consult_ai_team.DEFAULT_OPENCODE_MODEL})
            self.assertEqual([tool["tool"] for tool in manifest["tools"]], ["opencode"])

    def test_manifest_records_prompt_soft_limit_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            prompt = "x" * (consult_ai_team.PROMPT_WARN_CHARS + 1)
            args = self.parse_with([
                "--tool",
                "claude",
                "--dry-run",
                "--output-dir",
                tmpdir,
                "--prompt",
                prompt,
            ])
            full_prompt = consult_ai_team.consultation_prompt(args.mode, args.role, args.approval_mode, prompt)

            with patch.object(consult_ai_team, "claude_supports_effort", return_value=False):
                with redirect_stdout(io.StringIO()):
                    consult_ai_team.run_one_shot(args, full_prompt)

            manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["telemetry"]["prompt"]["warning"])
            self.assertIn(
                {"tool": None, "code": "prompt_soft_limit_exceeded"},
                manifest["telemetry"]["warnings"],
            )

    def test_manifest_records_serialize_opencode_diagnostic_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            args = self.parse_with([
                "--tool",
                "all",
                "--dry-run",
                "--serialize-opencode",
                "--output-dir",
                tmpdir,
                "--prompt",
                "test",
            ])
            prompt = consult_ai_team.consultation_prompt(args.mode, args.role, args.approval_mode, "test")

            with patch.object(consult_ai_team, "claude_supports_effort", return_value=False):
                with redirect_stdout(io.StringIO()):
                    consult_ai_team.run_one_shot(args, prompt)

            manifest = json.loads((Path(tmpdir) / "manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["serialize_opencode"])
            self.assertEqual(manifest["execution"], "parallel")

    def test_prompt_file_is_included_in_one_shot_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "out"
            prompt_file = Path(tmpdir) / "prompt.txt"
            prompt_file.write_text("file prompt marker", encoding="utf-8")
            args = self.parse_with([
                "--tool",
                "claude",
                "--dry-run",
                "--output-dir",
                str(output_dir),
                "--prompt",
                "inline marker",
                "--prompt-file",
                str(prompt_file),
            ])
            prompt = consult_ai_team.consultation_prompt(args.mode, args.role, args.approval_mode, consult_ai_team.read_prompt(args))

            with patch.object(consult_ai_team, "claude_supports_effort", return_value=False):
                with redirect_stdout(io.StringIO()):
                    consult_ai_team.run_one_shot(args, prompt)

            manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            claude_prompt = manifest["tools"][0]["command"][-1]
            self.assertIn("inline marker", claude_prompt)
            self.assertIn("file prompt marker", claude_prompt)


class ArtifactTests(unittest.TestCase):
    def parse_with(self, argv: list[str]):
        with patch.object(sys, "argv", ["consult_ai_team.py", *argv]):
            with patch.dict(os.environ, {}, clear=True):
                with redirect_stderr(io.StringIO()):
                    return consult_ai_team.parse_args()

    def test_four_section_summary_extraction(self) -> None:
        text = """## Recommendation
Do this.
## Alternative worth considering
Try that.
## Risks or edge cases
Careful here.
## Verification plan
Run tests.
"""

        fields = consult_ai_team.extract_summary_fields(text)

        self.assertEqual(fields["recommendation"], "Do this.")
        self.assertEqual(fields["alternative"], "Try that.")
        self.assertEqual(fields["risks"], "Careful here.")
        self.assertEqual(fields["verification_plan"], "Run tests.")

    def test_bold_heading_summary_extraction(self) -> None:
        text = """**Recommendation**
Ship it.
**Verification plan**
Run the suite.
"""

        fields = consult_ai_team.extract_summary_fields(text)

        self.assertEqual(fields["recommendation"], "Ship it.")
        self.assertEqual(fields["verification_plan"], "Run the suite.")

    def test_missing_headings_leave_summary_fields_null(self) -> None:
        fields = consult_ai_team.extract_summary_fields("plain output")

        self.assertIsNone(fields["recommendation"])
        self.assertIsNone(fields["risks"])

    def test_one_shot_dry_run_writes_evidence_summaries_and_contract_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            args = self.parse_with([
                "--tool",
                "claude",
                "--dry-run",
                "--output-dir",
                tmpdir,
                "--prompt",
                "test",
            ])
            prompt = consult_ai_team.consultation_prompt(args.mode, args.role, args.approval_mode, "test")

            with patch.object(consult_ai_team, "claude_supports_effort", return_value=False):
                with redirect_stdout(io.StringIO()):
                    consult_ai_team.run_one_shot(args, prompt)

            evidence = json.loads((Path(tmpdir) / "evidence.json").read_text(encoding="utf-8"))
            summary = json.loads((Path(tmpdir) / "claude.summary.json").read_text(encoding="utf-8"))
            manifest = json.loads((Path(tmpdir) / "manifest.json").read_text(encoding="utf-8"))
            sidecar = json.loads((Path(tmpdir) / "panda_contracts.v2.json").read_text(encoding="utf-8"))

            self.assertEqual(evidence["schema_version"], consult_ai_team.SCHEMA_VERSION)
            self.assertEqual(evidence["findings"][0]["tool"], "claude")
            self.assertEqual(summary["raw_output_path"], str(Path(tmpdir) / "claude.txt"))
            self.assertIn("telemetry", manifest)
            self.assertEqual(manifest["protocol"], "v2")
            self.assertEqual(manifest["telemetry"]["tool_count"], 1)
            self.assertEqual(manifest["telemetry"]["artifact_paths"]["evidence"], str(Path(tmpdir) / "evidence.json"))
            self.assertEqual(manifest["telemetry"]["artifact_paths"]["contracts"], str(Path(tmpdir) / "panda_contracts.v2.json"))
            self.assertEqual(sidecar["artifact_kind"], "contracts")
            self.assertEqual(sidecar["reports"][0]["parse_status"], "missing")

    def test_explicit_protocol_v2_dry_run_writes_contract_sidecar_without_changing_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            args = self.parse_with([
                "--tool",
                "claude",
                "--dry-run",
                "--protocol",
                "v2",
                "--output-dir",
                tmpdir,
                "--prompt",
                "test",
            ])
            prompt = consult_ai_team.consultation_prompt(
                args.mode,
                args.role,
                args.approval_mode,
                "test",
                args.protocol,
            )

            with patch.object(consult_ai_team, "claude_supports_effort", return_value=False):
                with redirect_stdout(io.StringIO()):
                    consult_ai_team.run_one_shot(args, prompt)

            output_dir = Path(tmpdir)
            evidence = json.loads((output_dir / "evidence.json").read_text(encoding="utf-8"))
            sidecar = json.loads((output_dir / "panda_contracts.v2.json").read_text(encoding="utf-8"))
            manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))

            self.assertEqual(set(evidence), {"schema_version", "findings"})
            self.assertEqual(sidecar["artifact_kind"], "contracts")
            self.assertEqual(sidecar["reports"][0]["parse_status"], "missing")
            self.assertEqual(manifest["protocol"], "v2")
            self.assertEqual(
                manifest["telemetry"]["artifact_paths"]["contracts"],
                str(output_dir / "panda_contracts.v2.json"),
            )

    def test_contract_falsifier_protocol_v2_writes_falsifier_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            args = self.parse_with([
                "--tool",
                "claude",
                "--dry-run",
                "--role",
                "contract-falsifier",
                "--output-dir",
                tmpdir,
                "--prompt",
                "test",
            ])
            prompt = consult_ai_team.consultation_prompt(
                args.mode,
                args.role,
                args.approval_mode,
                "test",
                args.protocol,
            )

            with patch.object(consult_ai_team, "claude_supports_effort", return_value=False):
                with redirect_stdout(io.StringIO()):
                    consult_ai_team.run_one_shot(args, prompt)

            output_dir = Path(tmpdir)
            self.assertFalse((output_dir / "panda_contracts.v2.json").exists())
            sidecar = json.loads((output_dir / "panda_falsifier.v2.json").read_text(encoding="utf-8"))
            manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(sidecar["artifact_kind"], "falsifier")
            self.assertEqual(sidecar["claims_audited"], 0)
            self.assertEqual(
                manifest["telemetry"]["artifact_paths"]["falsifier"],
                str(output_dir / "panda_falsifier.v2.json"),
            )

    def test_failed_and_empty_results_still_produce_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            failed = consult_ai_team.failed_tool_result(
                "claude",
                ["missing"],
                output_dir,
                RuntimeError("boom"),
            )
            failed["stdout"] = ""
            consult_ai_team.write_response(output_dir, failed)

            artifact_info = consult_ai_team.write_run_artifacts(output_dir, {"claude": failed}, ["claude"])

            finding = artifact_info["evidence"]["findings"][0]
            self.assertEqual(finding["status"], "failure")
            self.assertEqual(finding["raw_output_path"], str(output_dir / "claude.txt"))

    def test_oversized_summary_is_truncated(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            result = {
                "tool": "claude",
                "command": ["claude"],
                "cwd": str(output_dir),
                "started_at": consult_ai_team.now_iso(),
                "finished_at": consult_ai_team.now_iso(),
                "returncode": 0,
                "timed_out": False,
                "stdout": "## Recommendation\n" + ("x" * (consult_ai_team.SUMMARY_MAX_CHARS * 2)),
                "stderr": "",
            }
            consult_ai_team.normalize_result_metadata(result)
            consult_ai_team.write_response(output_dir, result)

            artifact_info = consult_ai_team.write_run_artifacts(output_dir, {"claude": result}, ["claude"])

            summary_path = Path(artifact_info["summary_paths"]["claude"])
            summary_text = summary_path.read_text(encoding="utf-8")
            summary = json.loads(summary_text)
            self.assertLessEqual(len(summary_text), consult_ai_team.SUMMARY_MAX_CHARS)
            self.assertTrue(summary["truncated"])

    def test_turn_summary_limit_is_enforced_for_long_paths(self) -> None:
        evidence = {
            "findings": [
                {
                    "tool": f"tool{i}",
                    "status": "success",
                    "recommendation": "x" * 5000,
                    "risks": "y" * 5000,
                    "verification_plan": "z" * 5000,
                    "raw_output_path": "/very/deep/" + ("nested/" * 80) + f"tool{i}.txt",
                    "truncated": False,
                }
                for i in range(6)
            ]
        }

        summary = consult_ai_team.make_turn_summary("session", 1, "ok", None, evidence)

        self.assertLessEqual(len(json.dumps(summary, indent=2) + "\n"), consult_ai_team.SESSION_MEMORY_MAX_CHARS)
        self.assertTrue(summary.get("truncated"))

    def test_latency_seconds_handles_bad_timestamps(self) -> None:
        self.assertIsNone(consult_ai_team.latency_seconds({"started_at": "bad", "finished_at": "worse"}))


class SessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.effort_patch = patch.object(consult_ai_team, "claude_supports_effort", return_value=False)
        self.effort_patch.start()

    def tearDown(self) -> None:
        self.effort_patch.stop()

    def parse_with(self, argv: list[str]):
        with patch.object(sys, "argv", ["consult_ai_team.py", *argv]):
            with patch.dict(os.environ, {}, clear=True):
                with redirect_stderr(io.StringIO()):
                    return consult_ai_team.parse_args()

    def test_opencode_jsonl_session_id_and_text_extraction(self) -> None:
        raw = "\n".join([
            '{"type":"step_start","sessionID":"ses_123","part":{}}',
            '{"type":"text","sessionID":"ses_123","part":{"text":"hello"}}',
            '{"type":"text","sessionID":"ses_123","part":{"text":" world"}}',
        ])

        session_id, text, usage = consult_ai_team.parse_opencode_jsonl(raw)

        self.assertEqual(session_id, "ses_123")
        self.assertEqual(text, "hello world")
        self.assertEqual(usage, consult_ai_team.NULL_USAGE)

    def test_opencode_jsonl_usage_extraction_skips_malformed_lines(self) -> None:
        raw = "\n".join([
            "{bad",
            '{"type":"usage","usage":{"inputTokens":10,"outputTokens":4,"cacheReadTokens":2,"costUSD":0.01}}',
            '{"type":"text","sessionID":"ses_456","part":{"text":"done"}}',
        ])

        session_id, text, usage = consult_ai_team.parse_opencode_jsonl(raw)

        self.assertEqual(session_id, "ses_456")
        self.assertEqual(text, "done")
        self.assertEqual(usage["input_tokens"], 10)
        self.assertEqual(usage["output_tokens"], 4)
        self.assertEqual(usage["cache_read_tokens"], 2)
        self.assertEqual(usage["cost_usd"], 0.01)

    def test_usage_extraction_prefers_usage_container_over_top_level_defaults(self) -> None:
        raw = '{"type":"usage","input_tokens":0,"usage":{"inputTokens":1500,"outputTokens":250}}'

        _session_id, _text, usage = consult_ai_team.parse_opencode_jsonl(raw)

        self.assertEqual(usage["input_tokens"], 1500)
        self.assertEqual(usage["output_tokens"], 250)

    def test_opencode_jsonl_warnings_are_best_effort(self) -> None:
        raw = "\n".join([
            "{bad",
            '{"type":"text","part":{"text":"hello"}}',
        ])

        session_id, text, usage, warnings = consult_ai_team.parse_opencode_jsonl(raw, include_warnings=True)

        self.assertIsNone(session_id)
        self.assertEqual(text, "hello")
        self.assertEqual(usage, consult_ai_team.NULL_USAGE)
        self.assertIn("opencode_jsonl_malformed_lines_skipped", warnings)
        self.assertIn("opencode_session_id_missing", warnings)
        self.assertIn("opencode_usage_missing", warnings)

    def test_session_dry_run_creates_and_continues_turns(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            args = self.parse_with([
                "--session",
                "--session-dir",
                tmpdir,
                "--tool",
                "all",
                "--dry-run",
                "--prompt",
                "first",
            ])
            with redirect_stdout(io.StringIO()):
                consult_ai_team.run_session(args, "first")

            session_dirs = list(Path(tmpdir).iterdir())
            self.assertEqual(len(session_dirs), 1)
            session_dir = session_dirs[0]
            session = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
            self.assertEqual(session["schema_version"], consult_ai_team.SCHEMA_VERSION)
            self.assertEqual(session["turn_count"], 1)
            self.assertEqual(session["profile"], "balanced")
            self.assertEqual(session["effective_models"]["claude"], "sonnet")
            self.assertEqual(session["effective_models"]["qwen"], consult_ai_team.DEFAULT_QWEN_MODEL)
            self.assertEqual(session["requested_tools"], ["claude", "opencode", "qwen"])
            self.assertEqual(
                session["active_models"],
                {
                    "claude": "sonnet",
                    "opencode": consult_ai_team.DEFAULT_OPENCODE_MODEL,
                    "qwen": consult_ai_team.DEFAULT_QWEN_MODEL,
                },
            )
            self.assertEqual(session["effective_effort"]["claude"], "high")
            self.assertIsNone(session["tool_session_ids"]["claude"])
            self.assertIsNone(session["tool_session_ids"]["qwen"])
            self.assertTrue((session_dir / "turns" / "001" / "manifest.json").exists())
            manifest = json.loads((session_dir / "turns" / "001" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["profile"], "balanced")
            self.assertEqual(manifest["effective_models"]["opencode"], consult_ai_team.DEFAULT_OPENCODE_MODEL)
            self.assertEqual(manifest["effective_models"]["qwen"], consult_ai_team.DEFAULT_QWEN_MODEL)
            self.assertEqual(manifest["requested_tools"], ["claude", "opencode", "qwen"])
            self.assertEqual(
                manifest["active_models"],
                {
                    "claude": "sonnet",
                    "opencode": consult_ai_team.DEFAULT_OPENCODE_MODEL,
                    "qwen": consult_ai_team.DEFAULT_QWEN_MODEL,
                },
            )

            args = self.parse_with([
                "--session",
                session["session_id"],
                "--session-dir",
                tmpdir,
                "--tool",
                "claude",
                "--dry-run",
                "--prompt",
                "second",
            ])
            with redirect_stdout(io.StringIO()):
                consult_ai_team.run_session(args, "second")

            session = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
            self.assertEqual(session["turn_count"], 2)
            self.assertTrue((session_dir / "turns" / "002" / "manifest.json").exists())

    def test_missing_session_id_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            args = self.parse_with([
                "--session",
                "missing",
                "--session-dir",
                tmpdir,
                "--prompt",
                "test",
            ])

            with self.assertRaises(SystemExit):
                consult_ai_team.make_session(args, Path.cwd())

    def test_session_advisory_uses_stable_isolated_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            args = self.parse_with([
                "--session",
                "--session-dir",
                tmpdir,
                "--tool",
                "claude",
                "--mode",
                "advisory",
                "--dry-run",
                "--prompt",
                "first",
            ])
            with redirect_stdout(io.StringIO()):
                consult_ai_team.run_session(args, "first")

            session_dir = next(Path(tmpdir).iterdir())
            session = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
            args = self.parse_with([
                "--session",
                session["session_id"],
                "--session-dir",
                tmpdir,
                "--tool",
                "claude",
                "--mode",
                "advisory",
                "--dry-run",
                "--prompt",
                "second",
            ])
            with redirect_stdout(io.StringIO()):
                consult_ai_team.run_session(args, "second")

            turn_1 = json.loads((session_dir / "turns" / "001" / "manifest.json").read_text(encoding="utf-8"))
            turn_2 = json.loads((session_dir / "turns" / "002" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(turn_1["run_cwd"], turn_2["run_cwd"])
            self.assertTrue(turn_1["run_cwd"].endswith("/isolated-cwd"))

    def test_session_sequential_execution_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            args = self.parse_with([
                "--session",
                "--session-dir",
                tmpdir,
                "--tool",
                "all",
                "--execution",
                "sequential",
                "--dry-run",
                "--prompt",
                "first",
            ])

            with redirect_stdout(io.StringIO()):
                consult_ai_team.run_session(args, "first")

            session_dir = next(Path(tmpdir).iterdir())
            manifest = json.loads((session_dir / "turns" / "001" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["execution"], "sequential")
            self.assertEqual([tool["tool"] for tool in manifest["tools"]], ["claude", "opencode", "qwen"])

    def test_session_continuation_inherits_role_and_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            args = self.parse_with([
                "--session",
                "--session-dir",
                tmpdir,
                "--tool",
                "claude",
                "--role",
                "planning",
                "--dry-run",
                "--prompt",
                "first",
            ])
            with redirect_stdout(io.StringIO()):
                consult_ai_team.run_session(args, "first")

            session_dir = next(Path(tmpdir).iterdir())
            session = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
            args = self.parse_with([
                "--session",
                session["session_id"],
                "--session-dir",
                tmpdir,
                "--tool",
                "claude",
                "--dry-run",
                "--prompt",
                "second",
            ])
            with redirect_stdout(io.StringIO()):
                consult_ai_team.run_session(args, "second")

            turn_2 = json.loads((session_dir / "turns" / "002" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(turn_2["role"], "planning")
            self.assertEqual(turn_2["profile"], "deep")
            prompt = (session_dir / "turns" / "002" / "prompt.txt").read_text(encoding="utf-8")
            self.assertIn("Role: planning", prompt)

    def test_session_continuation_inherits_tool_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            args = self.parse_with([
                "--session",
                "--session-dir",
                tmpdir,
                "--tool",
                "qwen",
                "--dry-run",
                "--prompt",
                "first",
            ])
            with redirect_stdout(io.StringIO()):
                consult_ai_team.run_session(args, "first")

            session_dir = next(Path(tmpdir).iterdir())
            session = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
            args = self.parse_with([
                "--session",
                session["session_id"],
                "--session-dir",
                tmpdir,
                "--dry-run",
                "--prompt",
                "second",
            ])
            with redirect_stdout(io.StringIO()):
                consult_ai_team.run_session(args, "second")

            turn_2 = json.loads((session_dir / "turns" / "002" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(turn_2["tool"], "qwen")
            self.assertEqual([tool["tool"] for tool in turn_2["tools"]], ["qwen"])
            self.assertEqual(turn_2["requested_tools"], ["qwen"])
            self.assertEqual(turn_2["active_models"], {"qwen": consult_ai_team.DEFAULT_QWEN_MODEL})

    def test_running_session_with_live_pid_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "active"
            session_dir.mkdir()
            consult_ai_team.write_json(session_dir / "session.json", {
                "schema_version": consult_ai_team.SCHEMA_VERSION,
                "session_id": "active",
                "status": "running",
                "runner_pid": os.getpid(),
            })
            args = self.parse_with([
                "--session",
                "active",
                "--session-dir",
                tmpdir,
                "--prompt",
                "test",
            ])

            with self.assertRaises(SystemExit):
                consult_ai_team.make_session(args, Path.cwd())

    def test_stale_running_session_is_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "stale"
            session_dir.mkdir()
            consult_ai_team.write_json(session_dir / "session.json", {
                "schema_version": consult_ai_team.SCHEMA_VERSION,
                "session_id": "stale",
                "status": "running",
                "runner_pid": 99999999,
            })
            args = self.parse_with([
                "--session",
                "stale",
                "--session-dir",
                tmpdir,
                "--prompt",
                "test",
            ])

            session, _, created = consult_ai_team.make_session(args, Path.cwd())

            self.assertFalse(created)
            self.assertEqual(session["status"], "waiting_for_user")
            self.assertEqual(session["last_turn_status"], "degraded")
            self.assertIsNone(session["runner_pid"])

    def test_stale_running_session_recovery_is_recorded_in_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "stale"
            session_dir.mkdir()
            consult_ai_team.write_json(session_dir / "session.json", {
                "schema_version": consult_ai_team.SCHEMA_VERSION,
                "session_id": "stale",
                "status": "running",
                "runner_pid": 99999999,
                "turn_count": 0,
                "tool_session_ids": {},
            })
            args = self.parse_with([
                "--session",
                "stale",
                "--session-dir",
                tmpdir,
                "--tool",
                "claude",
                "--dry-run",
                "--prompt",
                "test",
            ])

            with redirect_stdout(io.StringIO()):
                consult_ai_team.run_session(args, "test")

            manifest = json.loads((session_dir / "turns" / "001" / "manifest.json").read_text(encoding="utf-8"))
            self.assertIn("recovered_stale_running_session", manifest["recovery_notes"])
            self.assertIn(
                {"tool": None, "code": "recovered_stale_running_session"},
                manifest["telemetry"]["warnings"],
            )

    def test_existing_turn_directory_is_preserved_and_next_turn_is_allocated(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "partial"
            (session_dir / "turns" / "002").mkdir(parents=True)
            consult_ai_team.write_json(session_dir / "session.json", {
                "schema_version": consult_ai_team.SCHEMA_VERSION,
                "session_id": "partial",
                "status": "waiting_for_user",
                "turn_count": 1,
                "tool_session_ids": {},
            })
            args = self.parse_with([
                "--session",
                "partial",
                "--session-dir",
                tmpdir,
                "--tool",
                "claude",
                "--dry-run",
                "--prompt",
                "test",
            ])

            with redirect_stdout(io.StringIO()):
                consult_ai_team.run_session(args, "test")

            self.assertTrue((session_dir / "turns" / "002").exists())
            self.assertTrue((session_dir / "turns" / "003" / "manifest.json").exists())
            manifest = json.loads((session_dir / "turns" / "003" / "manifest.json").read_text(encoding="utf-8"))
            self.assertIn("skipped_existing_turn_dir:002", manifest["recovery_notes"])
            session = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
            self.assertEqual(session["turn_count"], 3)

    def test_same_session_subprocess_continuations_allocate_unique_turns(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "concurrent"
            session_dir.mkdir()
            consult_ai_team.write_json(session_dir / "session.json", {
                "schema_version": consult_ai_team.SCHEMA_VERSION,
                "session_id": "concurrent",
                "status": "waiting_for_user",
                "turn_count": 0,
                "tool_session_ids": {},
            })
            env = dict(os.environ)
            env["PYTHONPYCACHEPREFIX"] = str(Path(tmpdir) / "pycache")
            command = [
                sys.executable,
                str(MODULE_PATH),
                "--session",
                "concurrent",
                "--session-dir",
                tmpdir,
                "--tool",
                "claude",
                "--dry-run",
                "--prompt",
                "test",
            ]

            proc_1 = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
            proc_2 = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
            out_1, err_1 = proc_1.communicate(timeout=10)
            out_2, err_2 = proc_2.communicate(timeout=10)

            self.assertEqual(proc_1.returncode, 0, err_1 + out_1)
            self.assertEqual(proc_2.returncode, 0, err_2 + out_2)
            self.assertTrue((session_dir / "turns" / "001" / "manifest.json").exists())
            self.assertTrue((session_dir / "turns" / "002" / "manifest.json").exists())
            session = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
            self.assertEqual(session["turn_count"], 2)

    def test_patch_mode_session_continuation_is_rejected_clearly(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "old_patch"
            session_dir.mkdir()
            consult_ai_team.write_json(session_dir / "session.json", {
                "schema_version": consult_ai_team.SCHEMA_VERSION,
                "session_id": "old_patch",
                "mode": "patch",
                "status": "waiting_for_user",
                "turn_count": 0,
            })
            args = self.parse_with([
                "--session",
                "old_patch",
                "--session-dir",
                tmpdir,
                "--dry-run",
                "--prompt",
                "test",
            ])

            with self.assertRaises(SystemExit) as raised:
                consult_ai_team.run_session(args, "test")

            self.assertIn("Patch mode is disabled", str(raised.exception))

    def test_patch_mode_session_is_rejected_even_with_explicit_mode_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "old_patch"
            session_dir.mkdir()
            consult_ai_team.write_json(session_dir / "session.json", {
                "schema_version": consult_ai_team.SCHEMA_VERSION,
                "session_id": "old_patch",
                "mode": "patch",
                "status": "waiting_for_user",
                "turn_count": 0,
            })
            args = self.parse_with([
                "--session",
                "old_patch",
                "--session-dir",
                tmpdir,
                "--mode",
                "explore",
                "--dry-run",
                "--prompt",
                "test",
            ])

            with self.assertRaises(SystemExit) as raised:
                consult_ai_team.run_session(args, "test")

            self.assertIn("Patch mode is disabled", str(raised.exception))

    def test_session_without_mode_does_not_infer_patch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "old"
            session_dir.mkdir()
            consult_ai_team.write_json(session_dir / "session.json", {
                "schema_version": consult_ai_team.SCHEMA_VERSION,
                "session_id": "old",
                "status": "waiting_for_user",
                "turn_count": 0,
            })
            args = self.parse_with([
                "--session",
                "old",
                "--session-dir",
                tmpdir,
                "--tool",
                "claude",
                "--dry-run",
                "--prompt",
                "test",
            ])

            with redirect_stdout(io.StringIO()):
                consult_ai_team.run_session(args, "test")

            manifest = json.loads((session_dir / "turns" / "001" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["mode"], "explore")

    def test_session_command_construction_uses_native_sessions(self) -> None:
        args = self.parse_with(["--session", "--tool", "all", "--prompt", "test"])
        session = {
            "tool_session_ids": {
                "claude": None,
                "opencode": None,
                "qwen": None,
            }
        }

        commands, json_tools = consult_ai_team.build_commands(args, "prompt", Path("/tmp"), session=session)

        self.assertIn("--session-id", commands["claude"])
        self.assertNotIn("--no-session-persistence", commands["claude"])
        self.assertIn("--format", commands["opencode"])
        self.assertIn("--format", commands["qwen"])
        self.assertIn("opencode", json_tools)
        self.assertIn("qwen", json_tools)

        session["tool_session_ids"]["opencode"] = "ses_123"
        session["tool_session_ids"]["qwen"] = "ses_qwen"
        commands, _ = consult_ai_team.build_commands(args, "prompt", Path("/tmp"), session=session)
        self.assertIn("--resume", commands["claude"])
        self.assertIn("--session", commands["opencode"])
        self.assertIn("ses_qwen", commands["qwen"])

    def test_session_persists_qwen_native_session_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            args = self.parse_with([
                "--session",
                "--session-dir",
                tmpdir,
                "--tool",
                "qwen",
                "--prompt",
                "first",
            ])

            def fake_run_session_tools(*_args, **_kwargs):
                return {
                    "qwen": {
                        "tool": "qwen",
                        "command": ["opencode"],
                        "cwd": "/tmp",
                        "started_at": "start",
                        "returncode": 0,
                        "timed_out": False,
                        "stdout": "ok",
                        "stderr": "",
                        "finished_at": "finish",
                        "tool_session_id": "ses_qwen",
                    }
                }

            with patch.object(consult_ai_team, "run_session_tools", side_effect=fake_run_session_tools):
                with redirect_stdout(io.StringIO()):
                    consult_ai_team.run_session(args, "first")

            session_dir = next(Path(tmpdir).iterdir())
            session = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
            self.assertEqual(session["tool_session_ids"]["qwen"], "ses_qwen")

    def test_session_writes_turn_summary_and_injects_previous_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            args = self.parse_with([
                "--session",
                "--session-dir",
                tmpdir,
                "--tool",
                "claude",
                "--dry-run",
                "--prompt",
                "first",
            ])
            with redirect_stdout(io.StringIO()):
                consult_ai_team.run_session(args, "first")

            session_dir = next(Path(tmpdir).iterdir())
            session = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
            self.assertTrue((session_dir / "turns" / "001" / "turn_summary.json").exists())

            args = self.parse_with([
                "--session",
                session["session_id"],
                "--session-dir",
                tmpdir,
                "--tool",
                "claude",
                "--dry-run",
                "--prompt",
                "second",
            ])
            with redirect_stdout(io.StringIO()):
                consult_ai_team.run_session(args, "second")

            prompt = (session_dir / "turns" / "002" / "prompt.txt").read_text(encoding="utf-8")
            self.assertIn("Previous Panda turn summary", prompt)
            self.assertIn('"turn": 1', prompt)

    def test_no_session_memory_flag_prevents_prompt_injection(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            args = self.parse_with([
                "--session",
                "--session-dir",
                tmpdir,
                "--tool",
                "claude",
                "--dry-run",
                "--prompt",
                "first",
            ])
            with redirect_stdout(io.StringIO()):
                consult_ai_team.run_session(args, "first")

            session_dir = next(Path(tmpdir).iterdir())
            session = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
            args = self.parse_with([
                "--session",
                session["session_id"],
                "--session-dir",
                tmpdir,
                "--tool",
                "claude",
                "--no-session-memory",
                "--dry-run",
                "--prompt",
                "second",
            ])
            with redirect_stdout(io.StringIO()):
                consult_ai_team.run_session(args, "second")

            prompt = (session_dir / "turns" / "002" / "prompt.txt").read_text(encoding="utf-8")
            self.assertNotIn("Previous Panda turn summary", prompt)

    def test_no_session_memory_env_prevents_prompt_injection(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            args = self.parse_with([
                "--session",
                "--session-dir",
                tmpdir,
                "--tool",
                "claude",
                "--dry-run",
                "--prompt",
                "first",
            ])
            with redirect_stdout(io.StringIO()):
                consult_ai_team.run_session(args, "first")

            session_dir = next(Path(tmpdir).iterdir())
            session = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
            args = self.parse_with([
                "--session",
                session["session_id"],
                "--session-dir",
                tmpdir,
                "--tool",
                "claude",
                "--dry-run",
                "--prompt",
                "second",
            ])
            with patch.dict(os.environ, {"PANDA_NO_SESSION_MEMORY": "1"}):
                with redirect_stdout(io.StringIO()):
                    consult_ai_team.run_session(args, "second")

            prompt = (session_dir / "turns" / "002" / "prompt.txt").read_text(encoding="utf-8")
            self.assertNotIn("Previous Panda turn summary", prompt)

    def test_malformed_previous_summary_is_skipped_and_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            args = self.parse_with([
                "--session",
                "--session-dir",
                tmpdir,
                "--tool",
                "claude",
                "--dry-run",
                "--prompt",
                "first",
            ])
            with redirect_stdout(io.StringIO()):
                consult_ai_team.run_session(args, "first")

            session_dir = next(Path(tmpdir).iterdir())
            session = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
            (session_dir / "turns" / "001" / "turn_summary.json").write_text("{bad", encoding="utf-8")
            args = self.parse_with([
                "--session",
                session["session_id"],
                "--session-dir",
                tmpdir,
                "--tool",
                "claude",
                "--dry-run",
                "--prompt",
                "second",
            ])

            with redirect_stdout(io.StringIO()):
                consult_ai_team.run_session(args, "second")

            prompt = (session_dir / "turns" / "002" / "prompt.txt").read_text(encoding="utf-8")
            manifest = json.loads((session_dir / "turns" / "002" / "manifest.json").read_text(encoding="utf-8"))
            self.assertNotIn("Previous Panda turn summary", prompt)
            self.assertFalse(manifest["session_memory"]["injected"])
            self.assertEqual(manifest["session_memory"]["skip_reason"], "summary_unreadable")

    def test_session_tools_straggler_timeout_returns_partial_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            commands = {
                "fast": [sys.executable, "-c", "print('fast', flush=True)"],
                "slow": [sys.executable, "-c", "import time; print('slow', flush=True); time.sleep(3)"],
            }

            results = consult_ai_team.run_session_tools(
                commands,
                Path.cwd(),
                timeout=10,
                straggler_timeout=1,
                dry_run=False,
                output_dir=output_dir,
                json_tools=set(),
            )

            self.assertEqual(results["fast"]["status"], "finished")
            self.assertEqual(results["slow"]["status"], "straggler_timeout")
            self.assertTrue(results["slow"]["timed_out"])
            self.assertEqual(results["slow"]["timeout_kind"], "straggler")
            self.assertIn("slow", results["slow"]["stdout"])

    def test_session_tools_hard_timeout_preserves_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            commands = {
                "slow": [sys.executable, "-c", "import time; print('start', flush=True); time.sleep(3)"],
            }

            results = consult_ai_team.run_session_tools(
                commands,
                Path.cwd(),
                timeout=1,
                straggler_timeout=1,
                dry_run=False,
                output_dir=output_dir,
                json_tools=set(),
            )

            self.assertEqual(results["slow"]["status"], "hard_timeout")
            self.assertTrue(results["slow"]["timed_out"])
            self.assertEqual(results["slow"]["timeout_kind"], "hard")
            self.assertIn("start", results["slow"]["stdout"])

    def test_session_tools_hard_timeout_kills_child_process_group(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            child_pid_file = output_dir / "child.pid"
            script = (
                "import subprocess, sys, time; "
                "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
                f"open({str(child_pid_file)!r}, 'w').write(str(child.pid)); "
                "print('spawned', child.pid, flush=True); "
                "time.sleep(60)"
            )

            results = consult_ai_team.run_session_tools(
                {"slow": [sys.executable, "-c", script]},
                Path.cwd(),
                timeout=1,
                straggler_timeout=1,
                dry_run=False,
                output_dir=output_dir,
                json_tools=set(),
            )

            self.assertTrue(results["slow"]["timed_out"])
            child_pid = int(child_pid_file.read_text(encoding="utf-8"))
            for _ in range(20):
                if not consult_ai_team.process_is_running(child_pid):
                    break
                time.sleep(0.1)
            self.assertFalse(consult_ai_team.process_is_running(child_pid))

    def test_session_tools_exception_cleanup_kills_running_processes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            launched = []
            real_popen = consult_ai_team.popen_process

            def recording_popen(*args, **kwargs):
                process = real_popen(*args, **kwargs)
                launched.append(process)
                return process

            with patch.object(consult_ai_team, "popen_process", side_effect=recording_popen):
                with patch.object(consult_ai_team.time, "sleep", side_effect=RuntimeError("boom")):
                    with self.assertRaises(RuntimeError):
                        consult_ai_team.run_session_tools(
                            {"slow": [sys.executable, "-c", "import time; time.sleep(60)"]},
                            Path.cwd(),
                            timeout=30,
                            straggler_timeout=30,
                            dry_run=False,
                            output_dir=output_dir,
                            json_tools=set(),
                        )

            self.assertEqual(len(launched), 1)
            self.assertIsNotNone(launched[0].poll())

    def test_session_tools_exception_cleanup_closes_output_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            real_popen = consult_ai_team.popen_process

            with patch.object(consult_ai_team, "popen_process", side_effect=real_popen):
                with patch.object(consult_ai_team.time, "sleep", side_effect=RuntimeError("boom")):
                    with self.assertRaises(RuntimeError):
                        consult_ai_team.run_session_tools(
                            {"slow": [sys.executable, "-c", "import time; time.sleep(60)"]},
                            Path.cwd(),
                            timeout=30,
                            straggler_timeout=30,
                            dry_run=False,
                            output_dir=output_dir,
                            json_tools=set(),
                        )

            self.assertTrue((output_dir / "slow.stdout").exists())
            self.assertTrue((output_dir / "slow.stderr").exists())

    def test_timeout_evidence_references_raw_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            results = consult_ai_team.run_session_tools(
                {"slow": [sys.executable, "-c", "import time; print('start', flush=True); time.sleep(3)"]},
                Path.cwd(),
                timeout=1,
                straggler_timeout=1,
                dry_run=False,
                output_dir=output_dir,
                json_tools=set(),
            )
            consult_ai_team.write_response(output_dir, results["slow"])

            artifact_info = consult_ai_team.write_run_artifacts(output_dir, results, ["slow"])

            finding = artifact_info["evidence"]["findings"][0]
            self.assertTrue(finding["timed_out"])
            self.assertEqual(finding["raw_output_path"], str(output_dir / "slow.txt"))
            self.assertEqual(finding["stderr_path"], str(output_dir / "slow.stderr"))

    def test_failed_tool_marks_degraded_turn(self) -> None:
        results = [
            {"returncode": 1, "timed_out": False},
            {"returncode": 0, "timed_out": False},
        ]

        self.assertEqual(consult_ai_team.turn_status(results), "degraded")
        self.assertEqual(consult_ai_team.latest_stopping_suggestion(results), "tool_failed")


if __name__ == "__main__":
    unittest.main()
