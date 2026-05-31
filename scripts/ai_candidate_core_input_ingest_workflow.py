#!/usr/bin/env python3
"""Run PR35 candidate core-input files through isolated ingestion outputs."""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jsonschema


SCHEMA_VERSION = "ai_candidate_core_input_ingest_v1"
DEFAULT_PROJECT = "example"
DEFAULT_SCHEMA = "schemas/ai_candidate_core_input_ingest_schema.json"
DEFAULT_CURRENT_SCRIPT = "scripts/current_model_ingest.py"
DEFAULT_RATING_SCRIPT = "scripts/rating_model_ingest.py"
PREVIEW_LIMIT = 4000

OUTPUTS = {
    "manifest": "ai-candidate-core-input-ingest-manifest.json",
    "status": "ai-candidate-core-input-ingest-status.json",
    "current_ingest_input": "ai-candidate-current-model-ingest-input.json",
    "rating_current_ingest_input": "ai-candidate-rating-current-model-ingest-input.json",
    "current_normalized": "ai-candidate-current-models-normalized.json",
    "rating_current_normalized": "ai-candidate-rating-current-models-normalized.json",
    "rating_normalized": "ai-candidate-rating-models-normalized.json",
    "addenda_index": "ai-candidate-addenda-ingest-index.json",
    "review": "ai-candidate-core-input-ingest-review.json",
    "blockers": "ai-candidate-core-input-ingest-blockers.json",
}

INPUTS = {
    "manifest": "ai-candidate-core-input-apply-manifest.json",
    "status": "ai-candidate-core-input-apply-status.json",
    "current": "ai-candidate-current-model-input.json",
    "rating": "ai-candidate-rating-model-input.json",
    "role": "ai-candidate-role-addenda.json",
    "pin": "ai-candidate-pin-role-addenda.json",
    "rail": "ai-candidate-rail-relationship-hints.json",
    "passive": "ai-candidate-passive-support-inputs.json",
}

FORBIDDEN_OUTPUT_FILENAMES = {
    "{project}-current-models-normalized.json",
    "{project}-rating-models-normalized.json",
    "{project}-topology-current-allocation.json",
    "{project}-topology-copper-calculations.json",
    "{project}-topology-margin-calculations.json",
}

FORBIDDEN_FIELDS = {
    "finding_id",
    "issue_id",
    "violation",
    "severity",
    "compliance_pass",
    "compliance_fail",
    "pass_fail",
    "margin_pass",
    "margin_fail",
    "acceptable",
    "unacceptable",
    "final_finding",
    "recommendation_severity",
    "apply_to_artifact",
    "mutate_artifact",
    "overwrite",
    "delete_existing",
    "replace_existing",
    "safe_to_apply",
}

BLOCKER_CODES = {
    "missing_candidate_input_dir",
    "missing_candidate_input_manifest",
    "missing_candidate_input_status",
    "malformed_candidate_input_manifest",
    "malformed_candidate_input_status",
    "candidate_input_status_failed",
    "missing_candidate_current_input",
    "missing_candidate_rating_input",
    "malformed_candidate_current_input",
    "malformed_candidate_rating_input",
    "current_ingest_failed",
    "rating_current_ingest_failed",
    "rating_model_ingest_failed",
    "script_missing",
    "script_outside_repo",
    "output_path_outside_out_dir",
    "forbidden_core_output_path",
    "attempted_allocation_blocked",
    "attempted_calculation_blocked",
    "addenda_requires_merge_validator",
    "provenance_gap",
    "candidate_current_adapter_empty",
    "candidate_rating_adapter_empty",
    "candidate_input_record_unmappable",
    "missing_candidate_record_value",
    "missing_candidate_record_unit",
    "missing_candidate_record_target_identity",
}

PROVENANCE_FIELDS = {
    "candidate_record_id",
    "source_pr34_operation_id",
    "source_promotion_candidate_id",
    "source_approval_item_id",
    "source_decision_id",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def require_object(path: Path, malformed_code: str) -> dict[str, Any]:
    try:
        data = load_json(path)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{malformed_code}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{malformed_code}: expected JSON object")
    return data


def safe_load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = load_json(path)
    return data if isinstance(data, dict) else {}


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def json_safe(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    return value


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_safe(data), indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def preview(text: str) -> str:
    return text if len(text) <= PREVIEW_LIMIT else text[:PREVIEW_LIMIT] + "...[truncated]"


def sort_key(value: Any) -> str:
    return str(value or "")


def walk_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            keys.add(str(key))
            keys.update(walk_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(walk_keys(child))
    return keys


def has_forbidden_true_safe_merge(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "safe_to_merge_automatically" and child is True:
                return True
            if has_forbidden_true_safe_merge(child):
                return True
    elif isinstance(value, list):
        return any(has_forbidden_true_safe_merge(child) for child in value)
    return False


def path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def verify_output_path(path: Path, out_dir: Path, project: str) -> None:
    forbidden = {name.format(project=project) for name in FORBIDDEN_OUTPUT_FILENAMES}
    if path.name in forbidden:
        raise ValueError(f"forbidden_core_output_path: {path.name}")
    if not path_is_relative_to(path, out_dir):
        raise ValueError(f"output_path_outside_out_dir: {path}")


def verify_script_path(path: Path, repo_root: Path) -> Path:
    resolved = path.resolve()
    if not resolved.exists() or not resolved.is_file():
        raise FileNotFoundError(f"script_missing: {path}")
    if not path_is_relative_to(resolved, repo_root):
        raise ValueError(f"script_outside_repo: {path}")
    return resolved


def source_artifact(artifact_type: str, path: Path, notes: str | None = None) -> dict[str, Any]:
    return {"artifact_type": artifact_type, "path": str(path), "notes": notes}


def blocker(reason_code: str, details: str, step_id: str | None = None) -> dict[str, Any]:
    if reason_code not in BLOCKER_CODES:
        reason_code = "provenance_gap"
    return {"reason_code": reason_code, "details": details, "step_id": step_id}


def candidate_records(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    return [row for row in as_list(data.get(key)) if isinstance(row, dict)]


def candidate_record_value(record: dict[str, Any], value_keys: tuple[str, ...]) -> tuple[Any, str | None]:
    candidate_value = record.get("candidate_value")
    if not isinstance(candidate_value, dict):
        candidate_value = {}
    for key in value_keys:
        if key in candidate_value:
            return candidate_value.get(key), key
    if "value" in candidate_value:
        return candidate_value.get("value"), "value"
    return None, None


def candidate_record_unit(record: dict[str, Any]) -> Any:
    candidate_value = record.get("candidate_value")
    return candidate_value.get("unit") if isinstance(candidate_value, dict) else None


def candidate_record_condition(record: dict[str, Any]) -> str | None:
    candidate_value = record.get("candidate_value")
    if not isinstance(candidate_value, dict):
        return None
    condition = candidate_value.get("condition") or candidate_value.get("current_type") or candidate_value.get("basis")
    return str(condition) if condition is not None else None


def evidence_refs(record: dict[str, Any]) -> list[str]:
    refs = record.get("evidence_refs")
    if isinstance(refs, list):
        return [str(ref) for ref in refs if ref is not None]
    return []


def base_adapter_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_record_id": record.get("candidate_record_id"),
        "source_pr34_operation_id": record.get("source_pr34_operation_id"),
        "source_promotion_candidate_id": record.get("source_promotion_candidate_id"),
        "source_approval_item_id": record.get("source_approval_item_id"),
        "source_decision_id": record.get("source_decision_id"),
        "basis": "ai_candidate_core_input_apply",
        "evidence_refs": evidence_refs(record),
    }


def map_current_record(record: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None, dict[str, Any] | None]:
    target = record.get("target_identity")
    if not isinstance(target, dict) or not target:
        return "missing_candidate_record_target_identity", None, None
    value, value_key = candidate_record_value(record, ("current_a", "branch_current_a", "rail_current_a", "max_current_a", "typ_current_a"))
    if value is None:
        return "missing_candidate_record_value", None, None
    unit = candidate_record_unit(record)
    if not unit:
        return "missing_candidate_record_unit", None, None
    row = base_adapter_record(record)
    row["unit"] = unit
    condition = candidate_record_condition(record)
    if "branch_id" in target:
        row["branch_id"] = target["branch_id"]
        row["branch_current_a"] = value if value_key == "branch_current_a" else value
        return None, {"branch_currents": row}, None
    if "refdes" in target:
        row["refdes"] = target["refdes"]
        if "rail_name" in target:
            row["rail_name"] = target["rail_name"]
        if "pin" in target:
            row["pin"] = target["pin"]
        if condition and condition.lower() in {"typ", "typical"}:
            row["typ_current_a"] = value
        elif value_key == "typ_current_a":
            row["typ_current_a"] = value
        else:
            row["max_current_a"] = value
        return None, {"component_currents": row}, None
    if "rail_name" in target or "rail" in target:
        row["rail_name"] = target.get("rail_name") or target.get("rail")
        row["rail_current_a"] = value
        return None, {"rail_currents": row}, None
    return "candidate_input_record_unmappable", None, None


def rating_target_type(target: dict[str, Any]) -> str | None:
    if "target_type" in target:
        return str(target["target_type"])
    if "connector_ref" in target:
        return "connector"
    if "refdes" in target:
        return "component"
    if "branch_id" in target:
        return "branch"
    if "rail_name" in target or "rail" in target:
        return "rail"
    return None


def map_rating_record(record: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    target = record.get("target_identity")
    if not isinstance(target, dict) or not target:
        return "missing_candidate_record_target_identity", None
    value, _ = candidate_record_value(record, ("rating_a", "current_a", "value"))
    if value is None:
        return "missing_candidate_record_value", None
    unit = candidate_record_unit(record)
    if not unit:
        return "missing_candidate_record_unit", None
    target_type = rating_target_type(target)
    if target_type is None:
        return "candidate_input_record_unmappable", None
    row = base_adapter_record(record)
    row["target_type"] = target_type
    row["value"] = value
    row["unit"] = unit
    row["rating_name"] = "current_max"
    condition = candidate_record_condition(record)
    if condition:
        row["condition"] = condition
    if "pin" in target:
        row["pin"] = target["pin"]
    if "refdes" in target:
        row["refdes"] = target["refdes"]
    elif "connector_ref" in target:
        row["refdes"] = target["connector_ref"]
    elif "branch_id" in target:
        row["branch_id"] = target["branch_id"]
    elif "rail_name" in target or "rail" in target:
        row["rail_name"] = target.get("rail_name") or target.get("rail")
    elif "target_id" in target:
        row["refdes"] = target["target_id"]
    else:
        return "candidate_input_record_unmappable", None
    return None, row


def build_ingest_adapters(project: str, current_input: dict[str, Any], rating_input: dict[str, Any], out_dir: Path) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    blockers: list[dict[str, Any]] = []
    current_adapter = {
        "project": project,
        "generated_at_utc": utc_now(),
        "schema_version": SCHEMA_VERSION,
        "candidate_ingest_only": True,
        "branch_currents": [],
        "rail_currents": [],
        "component_currents": [],
        "ratings": [],
        "source_candidate_input": "ai-candidate-current-model-input.json",
        "errors": [],
        "warnings": [],
    }
    rating_adapter = {
        "project": project,
        "generated_at_utc": utc_now(),
        "schema_version": SCHEMA_VERSION,
        "candidate_ingest_only": True,
        "branch_currents": [],
        "rail_currents": [],
        "component_currents": [],
        "ratings": [],
        "source_candidate_input": "ai-candidate-rating-model-input.json",
        "errors": [],
        "warnings": [],
    }
    for record in sorted(candidate_records(current_input, "current_model_inputs"), key=lambda row: sort_key(row.get("candidate_record_id"))):
        reason, mapped, _ = map_current_record(record)
        if reason:
            blockers.append(blocker(reason, f"current candidate record is not mappable: {record.get('candidate_record_id')}"))
            continue
        assert mapped is not None
        for key, row in mapped.items():
            current_adapter[key].append(row)
    for record in sorted(candidate_records(rating_input, "rating_model_inputs"), key=lambda row: sort_key(row.get("candidate_record_id"))):
        reason, row = map_rating_record(record)
        if reason:
            blockers.append(blocker(reason, f"rating candidate record is not mappable: {record.get('candidate_record_id')}"))
            continue
        assert row is not None
        rating_adapter["ratings"].append(row)

    current_count = sum(len(as_list(current_adapter[key])) for key in ("branch_currents", "rail_currents", "component_currents", "ratings"))
    rating_count = len(as_list(rating_adapter["ratings"]))
    if candidate_records(current_input, "current_model_inputs") and current_count == 0:
        blockers.append(blocker("candidate_current_adapter_empty", "valid current candidate input records produced no current ingest adapter records"))
    if candidate_records(rating_input, "rating_model_inputs") and rating_count == 0:
        blockers.append(blocker("candidate_rating_adapter_empty", "valid rating candidate input records produced no rating ingest adapter records"))

    write_json(out_dir / OUTPUTS["current_ingest_input"], current_adapter)
    write_json(out_dir / OUTPUTS["rating_current_ingest_input"], rating_adapter)
    return current_adapter, rating_adapter, blockers


def step_record(
    *,
    step_id: str,
    step_name: str,
    script: Path | None,
    arguments: list[str],
    input_path: Path | None,
    output_path: Path | None,
    return_code: int | None,
    status: str,
    stdout: str = "",
    stderr: str = "",
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "step_id": step_id,
        "step_name": step_name,
        "script": str(script) if script else None,
        "arguments": arguments,
        "input_path": str(input_path) if input_path else None,
        "output_path": str(output_path) if output_path else None,
        "return_code": return_code,
        "status": status,
        "stdout_preview": preview(stdout),
        "stderr_preview": preview(stderr),
        "warnings": warnings or [],
        "errors": errors or [],
    }


def run_step(step_id: str, step_name: str, command: list[str], script: Path, input_path: Path, output_path: Path, repo_root: Path) -> dict[str, Any]:
    completed = subprocess.run(command, cwd=repo_root, text=True, capture_output=True)
    status = "passed" if completed.returncode == 0 and output_path.exists() else "failed"
    errors = [] if status == "passed" else [f"{step_id} returned {completed.returncode}"]
    return step_record(
        step_id=step_id,
        step_name=step_name,
        script=script,
        arguments=command,
        input_path=input_path,
        output_path=output_path,
        return_code=completed.returncode,
        status=status,
        stdout=completed.stdout,
        stderr=completed.stderr,
        errors=errors,
    )


def count_input_records(data: dict[str, Any], key: str) -> int:
    return len([row for row in as_list(data.get(key)) if isinstance(row, dict)])


def count_normalized(path: Path, key: str) -> int:
    data = safe_load(path)
    return len(as_list(data.get(key)))


def addenda_rows(data: dict[str, Any], key: str) -> list[Any]:
    return as_list(data.get(key))


def build_addenda_index(project: str, candidate_input_dir: Path, source_artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    files = {
        "role_addenda": (candidate_input_dir / INPUTS["role"], "role_addenda"),
        "pin_role_addenda": (candidate_input_dir / INPUTS["pin"], "pin_role_addenda"),
        "rail_relationship_hints": (candidate_input_dir / INPUTS["rail"], "rail_relationship_hints"),
        "passive_support_inputs": (candidate_input_dir / INPUTS["passive"], "passive_support_inputs"),
    }
    indexed = []
    total = 0
    for addenda_type, (path, key) in files.items():
        data = safe_load(path)
        count = len(addenda_rows(data, key))
        total += count
        indexed.append({
            "addenda_type": addenda_type,
            "path": str(path),
            "record_count": count,
            "safe_to_merge_automatically": False,
            "merged_addenda": False,
            "requires_merge_validator": True,
        })
    return {
        "project": project,
        "generated_at_utc": utc_now(),
        "schema_version": SCHEMA_VERSION,
        "candidate_ingest_only": True,
        "source_artifacts": source_artifacts,
        "indexed_addenda_files": indexed,
        "safe_to_merge_automatically": False,
        "merged_addenda": False,
        "summary": {"addenda_file_count": len(indexed), "addenda_records_seen": total},
        "errors": [],
        "warnings": [],
    }


def detect_provenance_gaps(input_artifacts: list[dict[str, Any]], output_paths: list[Path]) -> list[dict[str, Any]]:
    source_fields: set[str] = set()
    for artifact in input_artifacts:
        for key in ("current_model_inputs", "rating_model_inputs", "branch_currents", "rail_currents", "component_currents", "ratings"):
            for row in as_list(artifact.get(key)):
                if isinstance(row, dict):
                    source_fields.update(set(row).intersection(PROVENANCE_FIELDS))
    output_fields: set[str] = set()
    for path in output_paths:
        data = safe_load(path)
        for key in ("normalized_currents", "normalized_ratings"):
            for row in as_list(data.get(key)):
                if isinstance(row, dict):
                    output_fields.update(row.keys())
                    provenance = row.get("provenance")
                    if isinstance(provenance, dict):
                        output_fields.update(provenance.keys())
    gaps = []
    for field in sorted(source_fields - output_fields):
        gaps.append({"reason_code": "provenance_gap", "field": field, "detail": f"{field} not present in normalized output from existing ingestion scripts"})
    return gaps


def status_for(steps: list[dict[str, Any]], step_id: str) -> str:
    for step in steps:
        if step.get("step_id") == step_id:
            return str(step.get("status"))
    return "not_run"


def build_workflow(
    *,
    project: str,
    candidate_input_dir: Path,
    out_dir: Path,
    current_script: Path,
    rating_script: Path,
    skip_current: bool,
    skip_rating: bool,
    strict: bool,
) -> dict[str, dict[str, Any]]:
    repo_root = Path(__file__).resolve().parents[1]
    candidate_input_dir = candidate_input_dir.resolve()
    out_dir = out_dir.resolve()
    current_script = verify_script_path(current_script, repo_root)
    rating_script = verify_script_path(rating_script, repo_root)

    for filename in OUTPUTS.values():
        verify_output_path(out_dir / filename, out_dir, project)
    manifest_path = candidate_input_dir / INPUTS["manifest"]
    status_path = candidate_input_dir / INPUTS["status"]
    current_input_path = candidate_input_dir / INPUTS["current"]
    rating_input_path = candidate_input_dir / INPUTS["rating"]
    if not current_input_path.exists():
        raise FileNotFoundError("missing_candidate_current_input")
    if not rating_input_path.exists():
        raise FileNotFoundError("missing_candidate_rating_input")
    current_input = require_object(current_input_path, "malformed_candidate_current_input")
    rating_input = require_object(rating_input_path, "malformed_candidate_rating_input")
    source_manifest = require_object(manifest_path, "malformed_candidate_input_manifest")
    source_status = require_object(status_path, "malformed_candidate_input_status")

    source_artifacts = [
        source_artifact("ai_candidate_core_input_apply_manifest", manifest_path),
        source_artifact("ai_candidate_core_input_apply_status", status_path),
        source_artifact("ai_candidate_current_model_input", current_input_path),
        source_artifact("ai_candidate_rating_model_input", rating_input_path),
    ]
    out_dir.mkdir(parents=True, exist_ok=True)
    current_adapter, rating_adapter, adapter_blockers = build_ingest_adapters(project, current_input, rating_input, out_dir)
    current_out = out_dir / OUTPUTS["current_normalized"]
    rating_current_out = out_dir / OUTPUTS["rating_current_normalized"]
    rating_out = out_dir / OUTPUTS["rating_normalized"]
    current_ingest_input_path = out_dir / OUTPUTS["current_ingest_input"]
    rating_current_ingest_input_path = out_dir / OUTPUTS["rating_current_ingest_input"]

    steps: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = list(adapter_blockers)
    warnings: list[str] = []
    errors: list[str] = []
    if source_status.get("status") == "candidate_apply_failed":
        blockers.append(blocker("candidate_input_status_failed", "PR35 candidate input status is failed"))
        if strict:
            errors.append("PR35 candidate input status is failed")

    if skip_current:
        steps.append(step_record(
            step_id="current_candidate_ingest",
            step_name="current_candidate_ingest",
            script=current_script,
            arguments=[],
            input_path=current_ingest_input_path,
            output_path=current_out,
            return_code=None,
            status="skipped",
            warnings=["skip-current flag set"],
        ))
    else:
        command = [sys.executable, str(current_script), "--project", project, "--current-model", str(current_ingest_input_path), "--out", str(current_out)]
        steps.append(run_step("current_candidate_ingest", "current_candidate_ingest", command, current_script, current_ingest_input_path, current_out, repo_root))

    if skip_rating:
        for step_id, name, input_path, output_path in (
            ("rating_current_candidate_ingest", "rating_current_candidate_ingest", rating_current_ingest_input_path, rating_current_out),
            ("rating_model_candidate_ingest", "rating_model_candidate_ingest", rating_current_out, rating_out),
        ):
            steps.append(step_record(
                step_id=step_id,
                step_name=name,
                script=current_script if "current" in step_id else rating_script,
                arguments=[],
                input_path=input_path,
                output_path=output_path,
                return_code=None,
                status="skipped",
                warnings=["skip-rating flag set"],
            ))
    else:
        rating_current_command = [sys.executable, str(current_script), "--project", project, "--current-model", str(rating_current_ingest_input_path), "--out", str(rating_current_out)]
        rating_current_step = run_step("rating_current_candidate_ingest", "rating_current_candidate_ingest", rating_current_command, current_script, rating_current_ingest_input_path, rating_current_out, repo_root)
        steps.append(rating_current_step)
        if rating_current_step["status"] == "passed":
            rating_command = [sys.executable, str(rating_script), "--project", project, "--current-models-normalized", str(rating_current_out), "--out", str(rating_out)]
            steps.append(run_step("rating_model_candidate_ingest", "rating_model_candidate_ingest", rating_command, rating_script, rating_current_out, rating_out, repo_root))
        else:
            steps.append(step_record(
                step_id="rating_model_candidate_ingest",
                step_name="rating_model_candidate_ingest",
                script=rating_script,
                arguments=[],
                input_path=rating_current_out,
                output_path=rating_out,
                return_code=None,
                status="skipped",
                warnings=["rating current candidate ingest failed; dependent rating ingest skipped"],
            ))

    for step in steps:
        if step["status"] == "failed":
            code = {
                "current_candidate_ingest": "current_ingest_failed",
                "rating_current_candidate_ingest": "rating_current_ingest_failed",
                "rating_model_candidate_ingest": "rating_model_ingest_failed",
            }.get(step["step_id"], "current_ingest_failed")
            blockers.append(blocker(code, f"{step['step_id']} failed", step["step_id"]))
    provenance_gaps = detect_provenance_gaps([current_adapter, rating_adapter], [current_out, rating_current_out, rating_out])
    for gap in provenance_gaps:
        blockers.append(blocker("provenance_gap", gap["detail"]))

    addenda_index = build_addenda_index(project, candidate_input_dir, source_artifacts)
    addenda_seen = int(addenda_index["summary"]["addenda_records_seen"])
    for _ in range(addenda_seen):
        blockers.append(blocker("addenda_requires_merge_validator", "candidate addenda require a future merge validator"))

    step_errors = [error for step in steps for error in as_list(step.get("errors"))]
    step_warnings = [warning for step in steps for warning in as_list(step.get("warnings"))]
    errors.extend(step_errors)
    warnings.extend(step_warnings)
    workflow_failed = bool(errors) or any(step["status"] == "failed" for step in steps)
    status_text = "candidate_ingest_failed" if workflow_failed else ("candidate_ingest_with_warnings" if warnings or blockers else "candidate_ingest_pass")
    summary = {
        "candidate_current_input_record_count": count_input_records(current_input, "current_model_inputs"),
        "candidate_rating_input_record_count": count_input_records(rating_input, "rating_model_inputs"),
        "current_ingest_input_record_count": sum(len(as_list(current_adapter[key])) for key in ("branch_currents", "rail_currents", "component_currents", "ratings")),
        "rating_current_ingest_input_record_count": sum(len(as_list(rating_adapter[key])) for key in ("branch_currents", "rail_currents", "component_currents", "ratings")),
        "current_candidate_normalized_record_count": count_normalized(current_out, "normalized_currents"),
        "rating_current_candidate_normalized_record_count": count_normalized(rating_current_out, "normalized_currents"),
        "rating_candidate_normalized_record_count": count_normalized(rating_out, "normalized_ratings"),
        "addenda_file_count": int(addenda_index["summary"]["addenda_file_count"]),
        "addenda_records_seen": addenda_seen,
        "step_count": len(steps),
        "passed_step_count": sum(1 for step in steps if step["status"] == "passed"),
        "failed_step_count": sum(1 for step in steps if step["status"] == "failed"),
        "skipped_step_count": sum(1 for step in steps if step["status"] == "skipped"),
        "blocker_count": len(blockers),
        "provenance_gap_count": len(provenance_gaps),
        "wrote_candidate_normalized_outputs": True,
        "wrote_core_artifacts": False,
        "wrote_core_normalized_outputs": False,
        "ran_current_model_ingest": any(step["step_id"] in {"current_candidate_ingest", "rating_current_candidate_ingest"} and step["status"] == "passed" for step in steps),
        "ran_rating_model_ingest": any(step["step_id"] == "rating_model_candidate_ingest" and step["status"] == "passed" for step in steps),
        "ran_current_allocation": False,
        "ran_calculations": False,
        "merged_addenda": False,
        "safe_for_core_apply": False,
        "error_count": len(errors),
        "warning_count": len(warnings),
    }
    generated = utc_now()
    candidate_outputs = {
        "current_model_ingest_input": str(current_ingest_input_path),
        "rating_current_model_ingest_input": str(rating_current_ingest_input_path),
        "current_models_normalized": str(current_out),
        "rating_current_models_normalized": str(rating_current_out),
        "rating_models_normalized": str(rating_out),
        "addenda_index": str(out_dir / OUTPUTS["addenda_index"]),
        "review": str(out_dir / OUTPUTS["review"]),
        "blockers": str(out_dir / OUTPUTS["blockers"]),
    }
    manifest = {
        "project": project,
        "generated_at_utc": generated,
        "schema_version": SCHEMA_VERSION,
        "source_artifacts": source_artifacts,
        "source_candidate_input_manifest": str(manifest_path),
        "source_candidate_input_status": str(status_path),
        "candidate_outputs": candidate_outputs,
        "intermediate_outputs": {
            "current_model_ingest_input": str(current_ingest_input_path),
            "rating_current_model_ingest_input": str(rating_current_ingest_input_path),
        },
        "steps": steps,
        "core_outputs_written": False,
        "normalized_core_outputs_written": False,
        "ran_current_model_ingest": summary["ran_current_model_ingest"],
        "ran_rating_model_ingest": summary["ran_rating_model_ingest"],
        "ran_current_allocation": False,
        "ran_calculations": False,
        "merged_addenda": False,
        "safe_for_core_apply": False,
        "requires_future_core_apply_stage": True,
        "summary": summary,
        "errors": errors,
        "warnings": warnings,
    }
    status = {
        "project": project,
        "generated_at_utc": generated,
        "schema_version": SCHEMA_VERSION,
        "status": status_text,
        "candidate_ingest_only": True,
        "wrote_candidate_normalized_outputs": True,
        "wrote_core_artifacts": False,
        "wrote_core_normalized_outputs": False,
        "ran_current_model_ingest": summary["ran_current_model_ingest"],
        "ran_rating_model_ingest": summary["ran_rating_model_ingest"],
        "ran_current_allocation": False,
        "ran_calculations": False,
        "merged_addenda": False,
        "safe_for_core_apply": False,
        "requires_future_core_apply_stage": True,
        "current_ingest_status": status_for(steps, "current_candidate_ingest"),
        "rating_current_ingest_status": status_for(steps, "rating_current_candidate_ingest"),
        "rating_model_ingest_status": status_for(steps, "rating_model_candidate_ingest"),
        "errors": errors,
        "warnings": warnings,
    }
    review = {
        "project": project,
        "generated_at_utc": generated,
        "schema_version": SCHEMA_VERSION,
        "candidate_ingest_only": True,
        "not_performed_steps": [
            "current allocation",
            "copper calculations",
            "via calculations",
            "margin calculations",
            "authoritative addenda merge",
            "core normalized output write",
        ],
        "skipped_steps": [step for step in steps if step["status"] == "skipped"],
        "failed_steps": [step for step in steps if step["status"] == "failed"],
        "provenance_gaps": provenance_gaps,
        "source_candidate_input_summary": source_manifest.get("summary", {}),
        "candidate_output_summary": summary,
        "addenda_review_summary": addenda_index["summary"],
        "future_required_steps": [
            "explicit candidate normalized promotion review",
            "explicit core-input apply with opt-in flag",
            "addenda merge validator",
            "missing-data readiness rerun",
            "allocation/calculation rerun only after explicit promotion",
        ],
        "errors": errors,
        "warnings": warnings,
    }
    blockers_artifact = {
        "project": project,
        "generated_at_utc": generated,
        "schema_version": SCHEMA_VERSION,
        "candidate_ingest_only": True,
        "blocker_records": sorted(blockers, key=lambda row: (sort_key(row.get("reason_code")), sort_key(row.get("step_id")))),
        "summary": {"blocker_count": len(blockers)},
        "errors": errors,
        "warnings": warnings,
    }
    return {
        "manifest": manifest,
        "status": status,
        "addenda_index": addenda_index,
        "review": review,
        "blockers": blockers_artifact,
    }


def validate_outputs(outputs: list[dict[str, Any]], schema_path: Path) -> None:
    schema = load_json(schema_path)
    jsonschema.Draft7Validator.check_schema(schema)
    for output in outputs:
        forbidden = sorted(walk_keys(output).intersection(FORBIDDEN_FIELDS))
        if forbidden:
            raise ValueError(f"workflow output contains forbidden field(s): {', '.join(forbidden)}")
        if has_forbidden_true_safe_merge(output):
            raise ValueError("workflow output contains safe_to_merge_automatically true")
        jsonschema.validate(instance=output, schema=schema)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run PR35 candidate core inputs through isolated ingestion scripts.")
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--candidate-input-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--current-model-ingest-script", default=DEFAULT_CURRENT_SCRIPT)
    parser.add_argument("--rating-model-ingest-script", default=DEFAULT_RATING_SCRIPT)
    parser.add_argument("--skip-current", action="store_true")
    parser.add_argument("--skip-rating", action="store_true")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--schema", default=DEFAULT_SCHEMA)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    candidate_input_dir = Path(args.candidate_input_dir).resolve()
    try:
        if not candidate_input_dir.exists():
            raise FileNotFoundError("missing_candidate_input_dir")
        if not (candidate_input_dir / INPUTS["manifest"]).exists():
            raise FileNotFoundError("missing_candidate_input_manifest")
        if not (candidate_input_dir / INPUTS["status"]).exists():
            raise FileNotFoundError("missing_candidate_input_status")
        outputs = build_workflow(
            project=args.project,
            candidate_input_dir=candidate_input_dir,
            out_dir=Path(args.out_dir),
            current_script=Path(args.current_model_ingest_script),
            rating_script=Path(args.rating_model_ingest_script),
            skip_current=args.skip_current,
            skip_rating=args.skip_rating,
            strict=args.strict,
        )
        validate_outputs(list(outputs.values()), Path(args.schema).resolve())
        out_dir = Path(args.out_dir).resolve()
        for key in ("manifest", "status", "addenda_index", "review", "blockers"):
            write_json(out_dir / OUTPUTS[key], outputs[key])
    except (OSError, ValueError, jsonschema.ValidationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    summary = outputs["manifest"]["summary"]
    print(
        "ai candidate core input ingest workflow: "
        f"project={outputs['manifest']['project']} "
        f"current={summary['current_candidate_normalized_record_count']} "
        f"rating={summary['rating_candidate_normalized_record_count']} "
        f"steps={summary['passed_step_count']}/{summary['step_count']} "
        f"out={Path(args.out_dir).resolve()}"
    )
    return 1 if outputs["status"]["status"] == "candidate_ingest_failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
