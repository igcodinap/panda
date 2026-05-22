import importlib.util
import io
import os
from pathlib import Path
import sys
from typing import Optional
import unittest
from contextlib import redirect_stderr
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
            with patch.dict(os.environ, env, clear=False):
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


if __name__ == "__main__":
    unittest.main()
