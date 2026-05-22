import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
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

    def test_auto_and_forced_parallel_keep_patch_sequential(self) -> None:
        self.assertFalse(consult_ai_team.should_run_parallel("auto", "patch", 2))
        self.assertFalse(consult_ai_team.should_run_parallel("parallel", "patch", 2))

    def test_single_tool_never_parallelizes(self) -> None:
        self.assertFalse(consult_ai_team.should_run_parallel("parallel", "explore", 1))

    def test_sequential_override(self) -> None:
        self.assertFalse(consult_ai_team.should_run_parallel("sequential", "explore", 2))


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

    def test_parallel_patch_is_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            self.parse_with(["--mode", "patch", "--execution", "parallel", "--prompt", "test"])

    def test_session_without_value_creates_new_session_mode(self) -> None:
        args = self.parse_with(["--session", "--prompt", "test"])
        self.assertEqual(args.session, "")

    def test_default_tool_runs_all_cores(self) -> None:
        args = self.parse_with(["--prompt", "test"])
        self.assertEqual(args.tool, "all")
        self.assertEqual(consult_ai_team.requested_tools(args.tool), ["claude", "opencode", "qwen"])

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

    def test_both_tool_remains_legacy_claude_and_glm(self) -> None:
        args = self.parse_with(["--tool", "both", "--profile", "fast", "--prompt", "test"])

        with patch.object(consult_ai_team, "claude_supports_effort", return_value=False):
            commands, _ = consult_ai_team.build_commands(args, "prompt", Path("/tmp"))

        self.assertEqual(list(commands), ["claude", "opencode"])


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
                "both",
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
            self.assertEqual(manifest["effective_effort"]["claude"], "max")
            self.assertIsNone(manifest["applied_effort"]["claude"])
            self.assertFalse(manifest["effort_support"]["claude"])
            self.assertNotIn("--effort", claude_command)

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

        session_id, text = consult_ai_team.parse_opencode_jsonl(raw)

        self.assertEqual(session_id, "ses_123")
        self.assertEqual(text, "hello world")

    def test_session_dry_run_creates_and_continues_turns(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            args = self.parse_with([
                "--session",
                "--session-dir",
                tmpdir,
                "--tool",
                "both",
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
            self.assertEqual(session["effective_effort"]["claude"], "high")
            self.assertIsNone(session["tool_session_ids"]["claude"])
            self.assertIsNone(session["tool_session_ids"]["qwen"])
            self.assertTrue((session_dir / "turns" / "001" / "manifest.json").exists())
            manifest = json.loads((session_dir / "turns" / "001" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["profile"], "balanced")
            self.assertEqual(manifest["effective_models"]["opencode"], consult_ai_team.DEFAULT_OPENCODE_MODEL)
            self.assertEqual(manifest["effective_models"]["qwen"], consult_ai_team.DEFAULT_QWEN_MODEL)

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
                "both",
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
            self.assertEqual([tool["tool"] for tool in manifest["tools"]], ["claude", "opencode"])

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

    def test_failed_tool_marks_degraded_turn(self) -> None:
        results = [
            {"returncode": 1, "timed_out": False},
            {"returncode": 0, "timed_out": False},
        ]

        self.assertEqual(consult_ai_team.turn_status(results), "degraded")
        self.assertEqual(consult_ai_team.latest_stopping_suggestion(results), "tool_failed")


if __name__ == "__main__":
    unittest.main()
