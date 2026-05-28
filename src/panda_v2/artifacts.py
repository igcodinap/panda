"""Artifact writers for opt-in Panda V2 sidecars."""

from __future__ import annotations

import json
import os
from pathlib import Path
import uuid

from .contracts import (
    CONTRACTS_FILENAME,
    FALSIFIER_FILENAME,
    contracts_artifact,
    falsifier_artifact,
)
from .extractors import contract_report_from_text, falsifier_report_from_text


def write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as file_obj:
            file_obj.write(json.dumps(data, indent=2) + "\n")
            file_obj.flush()
            os.fsync(file_obj.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def write_contracts_sidecar(
    output_dir: Path,
    raw_results: dict[str, dict],
    tool_order,
    *,
    prompt_version: int = 2,
) -> dict:
    reports = [
        contract_report_from_text(tool, str(raw_results[tool].get("stdout") or ""))
        for tool in tool_order
    ]
    artifact = contracts_artifact(reports=reports, prompt_version=prompt_version)
    path = output_dir / CONTRACTS_FILENAME
    write_json_atomic(path, artifact)
    return {"path": str(path), "artifact": artifact}


def write_falsifier_sidecar(
    output_dir: Path,
    raw_results: dict[str, dict],
    tool_order,
    *,
    prompt_version: int = 2,
) -> dict:
    reports = [
        falsifier_report_from_text(tool, str(raw_results[tool].get("stdout") or ""))
        for tool in tool_order
    ]
    warnings = [
        warning
        for report in reports
        for warning in report.get("warnings", [])
    ]
    artifact = falsifier_artifact(
        reports=reports,
        prompt_version=prompt_version,
        warnings=warnings,
    )
    path = output_dir / FALSIFIER_FILENAME
    write_json_atomic(path, artifact)
    return {"path": str(path), "artifact": artifact}

