"""Schemas and validation helpers for opt-in Panda V2 artifacts."""

from __future__ import annotations

import datetime as dt
from typing import Any


SCHEMA_VERSION = 1
PROTOCOL_VERSION = "v2"
CONTRACTS_ARTIFACT = "contracts"
FALSIFIER_ARTIFACT = "falsifier"
CONTRACTS_FILENAME = "panda_contracts.v2.json"
FALSIFIER_FILENAME = "panda_falsifier.v2.json"
CONTRACTS_FENCE = "panda_contracts_v2"
FALSIFIER_FENCE = "panda_falsifier_v2"

CLAIM_STATUSES = frozenset({
    "confirmed",
    "inferred",
    "candidate",
    "not_found",
    "unverifiable",
})
PARSE_STATUS_ORDER = ("parsed", "missing", "malformed", "invalid", "multiple")
PARSE_STATUSES = frozenset(PARSE_STATUS_ORDER)
FALLBACK_WARNING_NAMES = frozenset({
    "extracted_via_label_line",
    "extracted_via_wrapper",
    "extracted_via_unnamed_contract_shape",
    "extracted_via_unnamed_falsifier_shape",
    "unterminated_fence_recovered",
})

STATUS_GUIDANCE = {
    "confirmed": "directly supported by local evidence",
    "inferred": "follows from evidence but is not directly stated",
    "candidate": "plausible competing contract or extracted natural-language claim",
    "not_found": "explicitly searched for and absent",
    "unverifiable": "cannot be checked with available evidence",
}


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def coerce_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    return [str(value)]


def validate_contract_claim(raw: Any) -> tuple[dict | None, list[str]]:
    warnings: list[str] = []
    if not isinstance(raw, dict):
        return None, ["claim_not_object"]

    claim_text = raw.get("claim")
    status = raw.get("status")
    if not isinstance(claim_text, str) or not claim_text.strip():
        warnings.append("claim_missing_text")
    if status not in CLAIM_STATUSES:
        warnings.append(f"claim_invalid_status:{status!r}")
    if warnings:
        return None, warnings

    claim = {
        "claim": claim_text.strip(),
        "status": status,
        "evidence_refs": coerce_string_list(raw.get("evidence_refs") or raw.get("evidence")),
    }
    for key in ("confidence", "source_agent", "codex_action", "severity_if_wrong"):
        if raw.get(key) is not None:
            claim[key] = raw[key]
    return claim, []


def validate_contract_payload(payload: Any) -> tuple[dict | None, list[str]]:
    if not isinstance(payload, dict):
        return None, ["payload_not_object"]
    warnings: list[str] = []
    if "claims" not in payload:
        warnings.append("claims_missing")
    if "files_inspected" not in payload:
        warnings.append("files_inspected_missing")
    if warnings:
        return None, warnings

    claims_raw = payload.get("claims")
    files_raw = payload.get("files_inspected")
    if not isinstance(claims_raw, list):
        return None, ["claims_not_list"]
    if not isinstance(files_raw, list):
        return None, ["files_inspected_not_list"]

    claims: list[dict] = []
    for idx, raw_claim in enumerate(claims_raw):
        claim, claim_warnings = validate_contract_claim(raw_claim)
        if claim_warnings:
            warnings.extend(f"claims[{idx}]:{warning}" for warning in claim_warnings)
        if claim is not None:
            claims.append(claim)

    if warnings:
        return None, warnings

    normalized = {
        "claims": claims,
        "files_inspected": coerce_string_list(files_raw),
    }
    if isinstance(payload.get("warnings"), list):
        normalized["warnings"] = coerce_string_list(payload.get("warnings"))
    return normalized, []


def contract_report(
    *,
    tool: str,
    parse_status: str,
    warnings: list[str] | None = None,
    claims: list[dict] | None = None,
    files_inspected: list[str] | None = None,
) -> dict:
    if parse_status not in PARSE_STATUSES:
        raise ValueError(f"Unsupported parse status: {parse_status}")
    return {
        "tool": tool,
        "parse_status": parse_status,
        "warnings": warnings or [],
        "claims": claims or [],
        "files_inspected": files_inspected or [],
    }


def fallback_warning_name(warning: str) -> str | None:
    if warning in FALLBACK_WARNING_NAMES:
        return warning
    if warning.startswith("multiple_fallback_blocks:"):
        return "multiple_fallback_blocks"
    return None


def parse_quality_metrics(reports: list[dict]) -> dict:
    status_counts = {status: 0 for status in PARSE_STATUS_ORDER}
    fallback_counts: dict[str, int] = {}
    fallback_parsed = 0
    reports_with_claims = 0
    claims_total = 0
    files_inspected_total = 0

    for report in reports:
        if not isinstance(report, dict):
            continue
        status = report.get("parse_status")
        if status in status_counts:
            status_counts[status] += 1
        warnings = coerce_string_list(report.get("warnings"))
        report_fallback_names = {
            name
            for warning in warnings
            for name in [fallback_warning_name(warning)]
            if name is not None
        }
        if status == "parsed" and report_fallback_names:
            fallback_parsed += 1
            for name in sorted(report_fallback_names):
                fallback_counts[name] = fallback_counts.get(name, 0) + 1
        claims = report.get("claims") or []
        if isinstance(claims, list):
            claims_total += len(claims)
            if claims:
                reports_with_claims += 1
        files = report.get("files_inspected") or []
        if isinstance(files, list):
            files_inspected_total += len(files)

    return {
        "reports_total": len(reports),
        **status_counts,
        "fallback_parsed": fallback_parsed,
        "fallback_counts": fallback_counts,
        "reports_with_claims": reports_with_claims,
        "claims_total": claims_total,
        "files_inspected_total": files_inspected_total,
    }


def contracts_artifact(*, reports: list[dict], prompt_version: int = 2) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "artifact_kind": CONTRACTS_ARTIFACT,
        "prompt_version": prompt_version,
        "created_at": now_iso(),
        "parse_quality": parse_quality_metrics(reports),
        "reports": reports,
    }


def validate_falsifier_payload(payload: Any) -> tuple[dict | None, list[str]]:
    if not isinstance(payload, dict):
        return None, ["payload_not_object"]
    warnings = []
    for key in ("claims_audited", "contradictions", "unverifiable", "not_found"):
        if key not in payload:
            warnings.append(f"{key}_missing")
    if warnings:
        return None, warnings
    try:
        claims_audited = int(payload.get("claims_audited") or 0)
    except (TypeError, ValueError):
        return None, ["claims_audited_not_int"]
    output: dict[str, Any] = {
        "claims_audited": claims_audited,
        "contradictions": payload.get("contradictions") or [],
        "unverifiable": payload.get("unverifiable") or [],
        "not_found": payload.get("not_found") or [],
    }
    for key in ("contradictions", "unverifiable", "not_found"):
        if not isinstance(output[key], list):
            warnings.append(f"{key}_not_list")
            continue
        for idx, item in enumerate(output[key]):
            if not isinstance(item, dict):
                warnings.append(f"{key}[{idx}]:item_not_object")
                continue
            claim_text = item.get("claim")
            if not isinstance(claim_text, str) or not claim_text.strip():
                warnings.append(f"{key}[{idx}]:claim_missing_text")
    if warnings:
        return None, warnings
    return output, []


def falsifier_artifact(
    *,
    reports: list[dict],
    prompt_version: int = 2,
    warnings: list[str] | None = None,
) -> dict:
    claims_audited = 0
    contradictions: list[Any] = []
    unverifiable: list[Any] = []
    not_found: list[Any] = []
    for report in reports:
        claims_audited += int(report.get("claims_audited") or 0)
        contradictions.extend(report.get("contradictions") or [])
        unverifiable.extend(report.get("unverifiable") or [])
        not_found.extend(report.get("not_found") or [])
    parse_quality = parse_quality_metrics(reports)
    parse_quality.update({
        "claims_audited_total": claims_audited,
        "contradictions_total": len(contradictions),
        "unverifiable_total": len(unverifiable),
        "not_found_total": len(not_found),
    })
    return {
        "schema_version": SCHEMA_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "artifact_kind": FALSIFIER_ARTIFACT,
        "prompt_version": prompt_version,
        "created_at": now_iso(),
        "parse_quality": parse_quality,
        "claims_audited": claims_audited,
        "contradictions": contradictions,
        "unverifiable": unverifiable,
        "not_found": not_found,
        "reports": reports,
        "warnings": warnings or [],
    }
