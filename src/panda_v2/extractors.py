"""Extraction helpers for Panda V2 fenced JSON blocks."""

from __future__ import annotations

import json
import re
from typing import Any

from .contracts import (
    CLAIM_STATUSES,
    CONTRACTS_FENCE,
    FALSIFIER_FENCE,
    contract_report,
    validate_contract_payload,
    validate_falsifier_payload,
)


FENCE_RE = re.compile(r"```(?P<label>[A-Za-z0-9_-]+)?\s*\n(?P<body>.*?)\n```", re.DOTALL)
OPEN_FENCE_RE = re.compile(r"```(?P<label>[A-Za-z0-9_-]+)?\s*\n")
TOOL_OUTPUT_MARKERS = ("\n[stderr]", "\n[stdout]", "\n[status]")
MALFORMED_SNIPPET_RADIUS = 40
HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
VALID_SIMPLE_JSON_ESCAPES = frozenset({'"', "\\", "/", "b", "f", "n", "r", "t"})


def fenced_blocks(text: str, fence_name: str) -> list[str]:
    blocks = []
    for match in OPEN_FENCE_RE.finditer(text or ""):
        if (match.group("label") or "").strip() != fence_name:
            continue
        close = text.find("\n```", match.end())
        if close != -1:
            blocks.append(text[match.end():close].strip())
    return blocks


def fenced_json_blocks(text: str) -> list[str]:
    blocks = []
    for match in OPEN_FENCE_RE.finditer(text or ""):
        label = (match.group("label") or "").strip()
        if label not in ("", "json"):
            continue
        close = text.find("\n```", match.end())
        if close != -1:
            blocks.append(text[match.end():close].strip())
    return blocks


def first_non_empty_line(body: str) -> tuple[str, str]:
    lines = body.splitlines()
    for idx, line in enumerate(lines):
        if line.strip():
            return line.strip(), "\n".join(lines[idx + 1:]).strip()
    return "", ""


def malformed_json_warning(body: str, exc: json.JSONDecodeError) -> str:
    lines = body.splitlines() or [body]
    if 1 <= exc.lineno <= len(lines):
        line = lines[exc.lineno - 1]
    else:
        line = body
    col_index = max(exc.colno - 1, 0)
    start = max(col_index - MALFORMED_SNIPPET_RADIUS, 0)
    end = min(col_index + MALFORMED_SNIPPET_RADIUS, len(line))
    snippet = line[start:end].strip()
    encoded_snippet = json.dumps(snippet, ensure_ascii=True)
    return (
        f"malformed_json:{exc.msg}:line={exc.lineno}:"
        f"column={exc.colno}:snippet={encoded_snippet}"
    )


def can_recover_invalid_json_escape(exc: json.JSONDecodeError) -> bool:
    return exc.msg in {"Invalid \\escape", "Invalid \\uXXXX escape"}


def recover_invalid_json_escapes(body: str) -> tuple[str, int]:
    output: list[str] = []
    in_string = False
    escaping = False
    repaired = 0

    for idx, char in enumerate(body):
        if not in_string:
            output.append(char)
            if char == '"':
                in_string = True
            continue

        if escaping:
            if char == "u":
                digits = body[idx + 1:idx + 5]
                valid_escape = len(digits) == 4 and all(
                    digit in HEX_DIGITS for digit in digits
                )
            else:
                valid_escape = char in VALID_SIMPLE_JSON_ESCAPES
            if not valid_escape:
                output.append("\\")
                repaired += 1
            output.append(char)
            escaping = False
            continue

        output.append(char)
        if char == "\\":
            escaping = True
        elif char == '"':
            in_string = False

    return "".join(output), repaired


def json_loads_or_malformed(body: str, warnings: list[str]) -> tuple[Any | None, str, list[str]]:
    try:
        return json.loads(body), "parsed", warnings
    except json.JSONDecodeError as exc:
        malformed_warning = malformed_json_warning(body, exc)
        if can_recover_invalid_json_escape(exc):
            recovered_body, repaired = recover_invalid_json_escapes(body)
            if repaired:
                try:
                    return json.loads(recovered_body), "parsed", warnings + [
                        malformed_warning,
                        "recovered_invalid_json_escape",
                        f"recovered_invalid_json_escape_count:{repaired}",
                    ]
                except json.JSONDecodeError as recovered_exc:
                    return None, "malformed", warnings + [
                        malformed_warning,
                        "invalid_json_escape_recovery_failed",
                        malformed_json_warning(recovered_body, recovered_exc),
                    ]
        return None, "malformed", warnings + [malformed_warning]


def unwrap_named_payload(payload: Any, fence_name: str) -> tuple[Any, list[str]]:
    if isinstance(payload, dict) and fence_name in payload:
        warnings = ["extracted_via_wrapper"]
        if len(payload) > 1:
            warnings.append("wrapper_ignored_sibling_keys")
        return payload[fence_name], warnings
    return payload, []


def has_valid_contract_shape(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    claims = payload.get("claims")
    files = payload.get("files_inspected")
    if not isinstance(claims, list) or not isinstance(files, list):
        return False
    return any(isinstance(claim, dict) and claim.get("status") in CLAIM_STATUSES for claim in claims)


def has_valid_falsifier_shape(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    return all(key in payload for key in ("claims_audited", "contradictions", "unverifiable", "not_found"))


def has_direct_shape(payload: Any, fence_name: str) -> bool:
    if fence_name == CONTRACTS_FENCE:
        return has_valid_contract_shape(payload)
    if fence_name == FALSIFIER_FENCE:
        return has_valid_falsifier_shape(payload)
    return False


def direct_shape_warning(fence_name: str) -> str:
    if fence_name == CONTRACTS_FENCE:
        return "extracted_via_unnamed_contract_shape"
    if fence_name == FALSIFIER_FENCE:
        return "extracted_via_unnamed_falsifier_shape"
    return "extracted_via_unnamed_shape"


def parse_json_fallback_blocks(text: str, fence_name: str) -> tuple[Any | None, str, list[str]] | None:
    json_blocks = fenced_json_blocks(text)

    label_line_blocks = []
    for body in json_blocks:
        first_line, remainder = first_non_empty_line(body)
        if first_line == fence_name:
            label_line_blocks.append(remainder)
    if label_line_blocks:
        warnings = ["extracted_via_label_line"]
        if len(label_line_blocks) > 1:
            warnings.append(f"multiple_fallback_blocks:{fence_name}:label_line")
        return json_loads_or_malformed(label_line_blocks[0], warnings)

    wrapper_blocks = []
    malformed_wrapper_warnings = []
    for body in json_blocks:
        if fence_name not in body:
            continue
        payload, status, warnings = json_loads_or_malformed(body, [])
        if status == "malformed":
            malformed_wrapper_warnings.extend(warnings)
            continue
        unwrapped, wrapper_warnings = unwrap_named_payload(payload, fence_name)
        if wrapper_warnings:
            wrapper_blocks.append((unwrapped, wrapper_warnings))
    if wrapper_blocks:
        payload, warnings = wrapper_blocks[0]
        if len(wrapper_blocks) > 1:
            warnings = warnings + [f"multiple_fallback_blocks:{fence_name}:wrapper"]
        return payload, "parsed", warnings
    if malformed_wrapper_warnings:
        return None, "malformed", malformed_wrapper_warnings

    direct_blocks = []
    malformed_direct_warnings = []
    for body in json_blocks:
        if '"claims"' not in body and '"claims_audited"' not in body:
            continue
        payload, status, warnings = json_loads_or_malformed(body, [])
        if status == "malformed":
            if '"files_inspected"' in body or '"claims_audited"' in body:
                malformed_direct_warnings.extend(warnings)
            continue
        if has_direct_shape(payload, fence_name):
            direct_blocks.append(payload)
    if direct_blocks:
        warnings = [direct_shape_warning(fence_name)]
        if len(direct_blocks) > 1:
            warnings.append(f"multiple_fallback_blocks:{fence_name}:unnamed")
        return direct_blocks[0], "parsed", warnings
    if malformed_direct_warnings:
        return None, "malformed", malformed_direct_warnings

    return None


def unterminated_json_bodies(text: str) -> list[str]:
    bodies = []
    for match in OPEN_FENCE_RE.finditer(text or ""):
        label = (match.group("label") or "").strip()
        if label not in ("", "json"):
            continue
        start = match.end()
        next_close = text.find("\n```", start)
        next_open = text.find("\n```", start)
        if next_close != -1:
            continue
        end = len(text)
        for marker in TOOL_OUTPUT_MARKERS:
            marker_index = text.find(marker, start)
            if marker_index != -1:
                end = min(end, marker_index)
        bodies.append(text[start:end].strip())
    return bodies


def parse_unterminated_json_block(text: str, fence_name: str) -> tuple[Any | None, str, list[str]] | None:
    for body in unterminated_json_bodies(text):
        payload, status, warnings = json_loads_or_malformed(body, ["unterminated_fence_recovered"])
        if status == "malformed":
            continue
        unwrapped, wrapper_warnings = unwrap_named_payload(payload, fence_name)
        if wrapper_warnings:
            return unwrapped, "parsed", warnings + wrapper_warnings
        if has_direct_shape(payload, fence_name):
            return payload, "parsed", warnings + [direct_shape_warning(fence_name)]
    return None


def parse_named_json_block(text: str, fence_name: str) -> tuple[Any | None, str, list[str]]:
    text = text or ""
    blocks = fenced_blocks(text, fence_name)
    if not blocks:
        fallback = parse_json_fallback_blocks(text, fence_name)
        if fallback is not None:
            return fallback
        unterminated = parse_unterminated_json_block(text, fence_name)
        if unterminated is not None:
            return unterminated
        return None, "missing", [f"missing_fenced_block:{fence_name}"]

    parse_status = "multiple" if len(blocks) > 1 else "parsed"
    warnings = [f"multiple_fenced_blocks:{fence_name}"] if len(blocks) > 1 else []
    payload, status, parse_warnings = json_loads_or_malformed(blocks[0], warnings)
    return payload, "malformed" if status == "malformed" else parse_status, parse_warnings


def contract_report_from_text(tool: str, text: str) -> dict:
    payload, parse_status, warnings = parse_named_json_block(text, CONTRACTS_FENCE)
    if payload is None:
        return contract_report(tool=tool, parse_status=parse_status, warnings=warnings)

    normalized, validation_warnings = validate_contract_payload(payload)
    if normalized is None:
        return contract_report(
            tool=tool,
            parse_status="invalid",
            warnings=warnings + validation_warnings,
        )

    return contract_report(
        tool=tool,
        parse_status=parse_status,
        warnings=warnings + normalized.get("warnings", []),
        claims=normalized["claims"],
        files_inspected=normalized["files_inspected"],
    )


def falsifier_report_from_text(tool: str, text: str) -> dict:
    payload, parse_status, warnings = parse_named_json_block(text, FALSIFIER_FENCE)
    report = {
        "tool": tool,
        "parse_status": parse_status,
        "warnings": warnings,
        "claims_audited": 0,
        "contradictions": [],
        "unverifiable": [],
        "not_found": [],
    }
    if payload is None:
        return report

    normalized, validation_warnings = validate_falsifier_payload(payload)
    if normalized is None:
        report["parse_status"] = "invalid"
        report["warnings"] = warnings + validation_warnings
        return report

    report.update(normalized)
    return report
