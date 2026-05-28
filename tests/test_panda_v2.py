import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from panda_v2.artifacts import write_contracts_sidecar, write_falsifier_sidecar
from panda_v2.contracts import (
    CLAIM_STATUSES,
    CONTRACTS_FILENAME,
    FALSIFIER_FILENAME,
    validate_contract_payload,
    validate_falsifier_payload,
)
from panda_v2.extractors import contract_report_from_text, falsifier_report_from_text
from panda_v2.prompts import protocol_v2_return_addendum


class PandaV2ContractTests(unittest.TestCase):
    def test_valid_contract_payload_preserves_multiple_candidates(self) -> None:
        payload = {
            "claims": [
                {
                    "claim": "Player ownership is keyed by user_id",
                    "status": "inferred",
                    "evidence_refs": ["persistence/player_repository.go"],
                },
                {
                    "claim": "Player ownership is keyed by user_name",
                    "status": "candidate",
                    "evidence_refs": ["legacy query call site"],
                },
            ],
            "files_inspected": ["persistence/player_repository.go"],
        }

        normalized, warnings = validate_contract_payload(payload)

        self.assertEqual(warnings, [])
        self.assertIsNotNone(normalized)
        assert normalized is not None
        self.assertEqual(len(normalized["claims"]), 2)
        self.assertEqual(normalized["claims"][1]["status"], "candidate")

    def test_unknown_status_invalidates_payload(self) -> None:
        payload = {
            "claims": [{"claim": "some claim", "status": "certain", "evidence_refs": []}],
            "files_inspected": [],
        }

        normalized, warnings = validate_contract_payload(payload)

        self.assertIsNone(normalized)
        self.assertTrue(any("claim_invalid_status" in warning for warning in warnings))
        self.assertNotIn("certain", CLAIM_STATUSES)

    def test_wrong_contract_schema_is_invalid_and_has_no_claims(self) -> None:
        report = contract_report_from_text(
            "claude",
            """```panda_contracts_v2
{"schema_version":1,"artifact_kind":"contracts","reports":[]}
```""",
        )

        self.assertEqual(report["parse_status"], "invalid")
        self.assertEqual(report["claims"], [])
        self.assertTrue(any("claims_missing" in warning for warning in report["warnings"]))

    def test_missing_contract_block_has_no_claims(self) -> None:
        report = contract_report_from_text("claude", "plain response")

        self.assertEqual(report["parse_status"], "missing")
        self.assertEqual(report["claims"], [])
        self.assertTrue(report["warnings"])

    def test_malformed_contract_block_has_no_claims(self) -> None:
        report = contract_report_from_text("claude", "```panda_contracts_v2\n{\"claims\": [\n```")

        self.assertEqual(report["parse_status"], "malformed")
        self.assertEqual(report["claims"], [])
        self.assertTrue(any("malformed_json" in warning for warning in report["warnings"]))
        malformed = next(warning for warning in report["warnings"] if "malformed_json" in warning)
        self.assertIn("line=", malformed)
        self.assertIn("column=", malformed)
        self.assertIn("snippet=", malformed)

    def test_unrelated_json_fence_is_ignored(self) -> None:
        report = contract_report_from_text("claude", "```json\n{\"claims\": []}\n```")

        self.assertEqual(report["parse_status"], "missing")
        self.assertEqual(report["claims"], [])

    def test_json_fence_with_contract_label_line_parses(self) -> None:
        text = """```json
panda_contracts_v2
{"claims":[{"claim":"DEFAULT_LOG_FORMAT changes","status":"confirmed","evidence_refs":["src/_pytest/logging.py:18"]}],"files_inspected":["src/_pytest/logging.py"]}
```"""

        report = contract_report_from_text("opencode", text)

        self.assertEqual(report["parse_status"], "parsed")
        self.assertEqual(report["claims"][0]["claim"], "DEFAULT_LOG_FORMAT changes")
        self.assertIn("extracted_via_label_line", report["warnings"])

    def test_wrapper_contract_json_parses(self) -> None:
        text = """```json
{"panda_contracts_v2":{"claims":[{"claim":"UsernameValidator regex anchors change","status":"confirmed","evidence_refs":["django/contrib/auth/validators.py:10"]}],"files_inspected":["django/contrib/auth/validators.py"]}}
```"""

        report = contract_report_from_text("qwen", text)

        self.assertEqual(report["parse_status"], "parsed")
        self.assertEqual(report["claims"][0]["status"], "confirmed")
        self.assertIn("extracted_via_wrapper", report["warnings"])

    def test_direct_unnamed_contract_json_parses_when_schema_gated(self) -> None:
        text = """```json
{"claims":[{"claim":"Poly3DCollection initializes face colors lazily","status":"inferred","evidence_refs":["lib/mpl_toolkits/mplot3d/art3d.py"]}],"files_inspected":["lib/mpl_toolkits/mplot3d/art3d.py"]}
```"""

        report = contract_report_from_text("qwen", text)

        self.assertEqual(report["parse_status"], "parsed")
        self.assertEqual(report["claims"][0]["status"], "inferred")
        self.assertIn("extracted_via_unnamed_contract_shape", report["warnings"])

    def test_direct_unnamed_contract_with_empty_claims_is_ignored(self) -> None:
        text = """```json
{"claims":[],"files_inspected":["some/file.py"]}
```"""

        report = contract_report_from_text("qwen", text)

        self.assertEqual(report["parse_status"], "missing")
        self.assertEqual(report["claims"], [])

    def test_direct_unnamed_contract_with_invalid_status_is_ignored(self) -> None:
        text = """```json
{"claims":[{"claim":"looks contract shaped","status":"certain","evidence_refs":[]}],"files_inspected":["some/file.py"]}
```"""

        report = contract_report_from_text("qwen", text)

        self.assertEqual(report["parse_status"], "missing")
        self.assertEqual(report["claims"], [])

    def test_unterminated_wrapper_contract_before_stderr_recovers(self) -> None:
        text = """```json
{"panda_contracts_v2":{"claims":[{"claim":"coth typo is cotm vs cothm","status":"confirmed","evidence_refs":["sympy/functions/elementary/hyperbolic.py:590"]}],"files_inspected":["sympy/functions/elementary/hyperbolic.py"]}}

[stderr]
tool output starts here
"""

        report = contract_report_from_text("qwen", text)

        self.assertEqual(report["parse_status"], "parsed")
        self.assertEqual(report["claims"][0]["claim"], "coth typo is cotm vs cothm")
        self.assertIn("unterminated_fence_recovered", report["warnings"])
        self.assertIn("extracted_via_wrapper", report["warnings"])

    def test_malformed_label_line_regex_json_has_no_claims(self) -> None:
        text = """```json
panda_contracts_v2
{"claims":[{"claim":"regex r'^[\\w.@+-]+$'","status":"confirmed","evidence_refs":["django/contrib/auth/validators.py:10"]}],"files_inspected":["django/contrib/auth/validators.py"]}
```"""

        report = contract_report_from_text("opencode", text)

        self.assertEqual(report["parse_status"], "malformed")
        self.assertEqual(report["claims"], [])
        malformed = next(warning for warning in report["warnings"] if "malformed_json" in warning)
        self.assertIn("Invalid \\escape", malformed)
        self.assertIn("line=", malformed)
        self.assertIn("column=", malformed)
        self.assertIn("snippet=", malformed)
        self.assertIn("\\\\w", malformed)

    def test_protocol_v2_prompts_request_json_self_validation(self) -> None:
        contract_prompt = protocol_v2_return_addendum("implementation-review")
        falsifier_prompt = protocol_v2_return_addendum("contract-falsifier")

        for prompt in (contract_prompt, falsifier_prompt):
            self.assertIn("Double-escape regex or path backslashes as `\\\\`", prompt)
            self.assertIn("mentally validate", prompt)
            self.assertIn("strict JSON", prompt)

    def test_not_found_claim_keeps_empty_evidence(self) -> None:
        text = """```panda_contracts_v2
{"claims":[{"claim":"Expected Player.IP field","status":"not_found","evidence_refs":[]}],"files_inspected":["model/player.go"]}
```"""

        report = contract_report_from_text("qwen", text)

        self.assertEqual(report["parse_status"], "parsed")
        self.assertEqual(report["claims"][0]["status"], "not_found")
        self.assertEqual(report["claims"][0]["evidence_refs"], [])

    def test_multiple_blocks_records_warning_and_uses_first(self) -> None:
        text = """```panda_contracts_v2
{"claims":[{"claim":"first","status":"candidate","evidence_refs":[]}],"files_inspected":[]}
```
```panda_contracts_v2
{"claims":[{"claim":"second","status":"candidate","evidence_refs":[]}],"files_inspected":[]}
```"""

        report = contract_report_from_text("opencode", text)

        self.assertEqual(report["parse_status"], "multiple")
        self.assertEqual(report["claims"][0]["claim"], "first")
        self.assertTrue(any("multiple_fenced_blocks" in warning for warning in report["warnings"]))

    def test_contract_sidecar_written_adjacent_to_evidence_dir(self) -> None:
        result = {
            "stdout": """```panda_contracts_v2
{"claims":[{"claim":"CredentialsStore.extractCredential exists","status":"candidate","evidence_refs":["internal/oci/auth_test.go"]}],"files_inspected":["internal/oci/auth_test.go"]}
```"""
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)

            info = write_contracts_sidecar(output_dir, {"qwen": result}, ["qwen"])

            sidecar = output_dir / CONTRACTS_FILENAME
            self.assertEqual(info["path"], str(sidecar))
            data = json.loads(sidecar.read_text(encoding="utf-8"))
            self.assertEqual(data["artifact_kind"], "contracts")
            self.assertEqual(data["reports"][0]["claims"][0]["status"], "candidate")
            self.assertEqual(data["parse_quality"]["reports_total"], 1)
            self.assertEqual(data["parse_quality"]["parsed"], 1)
            self.assertEqual(data["parse_quality"]["claims_total"], 1)
            self.assertEqual(data["parse_quality"]["fallback_parsed"], 0)

    def test_contract_sidecar_parse_quality_counts_statuses_and_fallbacks(self) -> None:
        raw_results = {
            "claude": {
                "stdout": """```panda_contracts_v2
{"claims":[{"claim":"exact fence","status":"confirmed","evidence_refs":[]}],"files_inspected":["a.py"]}
```""",
            },
            "opencode": {
                "stdout": """```json
panda_contracts_v2
{"claims":[{"claim":"label fallback","status":"inferred","evidence_refs":[]}],"files_inspected":["b.py"]}
```""",
            },
            "qwen": {
                "stdout": """```json
panda_contracts_v2
{"claims":[{"claim":"regex r'^[\\w]+$'","status":"confirmed","evidence_refs":[]}],"files_inspected":["c.py"]}
```""",
            },
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            info = write_contracts_sidecar(
                Path(tmpdir),
                raw_results,
                ["claude", "opencode", "qwen"],
            )

            quality = info["artifact"]["parse_quality"]
            self.assertEqual(quality["reports_total"], 3)
            self.assertEqual(quality["parsed"], 2)
            self.assertEqual(quality["malformed"], 1)
            self.assertEqual(quality["missing"], 0)
            self.assertEqual(quality["fallback_parsed"], 1)
            self.assertEqual(quality["fallback_counts"]["extracted_via_label_line"], 1)
            self.assertEqual(quality["claims_total"], 2)
            self.assertEqual(quality["reports_with_claims"], 2)

    def test_contract_sidecar_replaces_without_merging_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            sidecar = output_dir / CONTRACTS_FILENAME
            sidecar.write_text(
                json.dumps({"stale": True, "reports": [{"claims": [{"claim": "old"}]}]}),
                encoding="utf-8",
            )

            write_contracts_sidecar(output_dir, {"qwen": {"stdout": "plain response"}}, ["qwen"])

            data = json.loads(sidecar.read_text(encoding="utf-8"))
            self.assertNotIn("stale", data)
            self.assertEqual(data["reports"][0]["parse_status"], "missing")
            self.assertEqual(data["reports"][0]["claims"], [])

    def test_falsifier_report_schema(self) -> None:
        text = """```panda_falsifier_v2
{"claims_audited":2,"contradictions":[{"claim":"bad"}],"unverifiable":[{"claim":"maybe"}],"not_found":[{"claim":"missing"}]}
```"""

        report = falsifier_report_from_text("claude", text)

        self.assertEqual(report["claims_audited"], 2)
        self.assertEqual(len(report["contradictions"]), 1)
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            info = write_falsifier_sidecar(output_dir, {"claude": {"stdout": text}}, ["claude"])
            data = json.loads((output_dir / FALSIFIER_FILENAME).read_text(encoding="utf-8"))
            self.assertEqual(info["path"], str(output_dir / FALSIFIER_FILENAME))
            self.assertEqual(data["artifact_kind"], "falsifier")
            self.assertEqual(data["claims_audited"], 2)
            self.assertEqual(data["parse_quality"]["reports_total"], 1)
            self.assertEqual(data["parse_quality"]["parsed"], 1)
            self.assertEqual(data["parse_quality"]["claims_audited_total"], 2)
            self.assertEqual(data["parse_quality"]["contradictions_total"], 1)

    def test_falsifier_json_label_line_parses(self) -> None:
        text = """```json
panda_falsifier_v2
{"claims_audited":1,"contradictions":[],"unverifiable":[{"claim":"maybe"}],"not_found":[]}
```"""

        report = falsifier_report_from_text("claude", text)

        self.assertEqual(report["parse_status"], "parsed")
        self.assertEqual(report["claims_audited"], 1)
        self.assertIn("extracted_via_label_line", report["warnings"])

    def test_falsifier_wrapper_json_parses(self) -> None:
        text = """```json
{"panda_falsifier_v2":{"claims_audited":1,"contradictions":[{"claim":"bad"}],"unverifiable":[],"not_found":[]}}
```"""

        report = falsifier_report_from_text("claude", text)

        self.assertEqual(report["parse_status"], "parsed")
        self.assertEqual(report["claims_audited"], 1)
        self.assertIn("extracted_via_wrapper", report["warnings"])

    def test_falsifier_direct_unnamed_json_parses(self) -> None:
        text = """```json
{"claims_audited":1,"contradictions":[],"unverifiable":[],"not_found":[{"claim":"missing"}]}
```"""

        report = falsifier_report_from_text("claude", text)

        self.assertEqual(report["parse_status"], "parsed")
        self.assertEqual(report["claims_audited"], 1)
        self.assertIn("extracted_via_unnamed_falsifier_shape", report["warnings"])

    def test_falsifier_wrong_schema_is_invalid(self) -> None:
        normalized, warnings = validate_falsifier_payload({"claims_audited": "two"})

        self.assertIsNone(normalized)
        self.assertTrue(any("contradictions_missing" in warning for warning in warnings))

    def test_falsifier_list_items_require_claim_text(self) -> None:
        normalized, warnings = validate_falsifier_payload({
            "claims_audited": 1,
            "contradictions": ["bad"],
            "unverifiable": [{"reason": "missing claim"}],
            "not_found": [],
        })

        self.assertIsNone(normalized)
        self.assertTrue(any("contradictions[0]:item_not_object" in warning for warning in warnings))
        self.assertTrue(any("unverifiable[0]:claim_missing_text" in warning for warning in warnings))


if __name__ == "__main__":
    unittest.main()
