#!/usr/bin/env python3
"""Review PR36 candidate normalized outputs for a future explicit core apply."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jsonschema


SCHEMA_VERSION = "ai_candidate_normalized_promotion_review_v1"
READINESS_SCHEMA_VERSION = "ai_candidate_normalized_approval_readiness_v1"
DEFAULT_PROJECT = "example"
DEFAULT_SCHEMA = "schemas/ai_candidate_normalized_promotion_review_schema.json"

INPUTS = {
    "manifest": "ai-candidate-core-input-ingest-manifest.json",
    "status": "ai-candidate-core-input-ingest-status.json",
    "current": "ai-candidate-current-models-normalized.json",
    "rating": "ai-candidate-rating-models-normalized.json",
    "review": "ai-candidate-core-input-ingest-review.json",
    "blockers": "ai-candidate-core-input-ingest-blockers.json",
    "addenda": "ai-candidate-addenda-ingest-index.json",
}

OUTPUTS = {
    "review": "ai-candidate-normalized-promotion-review.json",
    "status": "ai-candidate-normalized-promotion-status.json",
    "current_diff": "ai-candidate-current-normalized-diff.json",
    "rating_diff": "ai-candidate-rating-normalized-diff.json",
    "readiness": "ai-candidate-normalized-approval-readiness.json",
    "blockers": "ai-candidate-normalized-review-blockers.json",
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
    "missing_candidate_ingested_dir",
    "missing_candidate_ingest_manifest",
    "missing_candidate_ingest_status",
    "malformed_candidate_ingest_manifest",
    "malformed_candidate_ingest_status",
    "candidate_ingest_status_failed",
    "missing_candidate_current_normalized",
    "missing_candidate_rating_normalized",
    "malformed_candidate_current_normalized",
    "malformed_candidate_rating_normalized",
    "missing_core_current_normalized",
    "missing_core_rating_normalized",
    "malformed_core_current_normalized",
    "malformed_core_rating_normalized",
    "missing_identity",
    "missing_value",
    "missing_unit",
    "conflict_with_core",
    "provenance_gap",
    "unsupported_record",
    "attempted_core_write_blocked",
    "attempted_ingestion_blocked",
    "attempted_allocation_blocked",
    "attempted_calculation_blocked",
    "addenda_requires_merge_validator",
}

CLASSIFICATIONS = {
    "add_candidate",
    "duplicate_existing",
    "conflict_with_core",
    "missing_identity",
    "missing_value",
    "missing_unit",
    "provenance_gap",
    "unsupported_record",
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


def optional_object(path: Path | None, malformed_code: str) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    return require_object(path, malformed_code)


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


def sort_key(value: Any) -> str:
    return str(value or "")


def digest_id(*values: Any) -> str:
    payload = json.dumps([json_safe(value) for value in values], sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


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
        raise ValueError(f"attempted_core_write_blocked: {path.name}")
    if not path_is_relative_to(path, out_dir):
        raise ValueError(f"output_path_outside_out_dir: {path}")


def source_artifact(artifact_type: str, path: Path | None, notes: str | None = None) -> dict[str, Any]:
    return {"artifact_type": artifact_type, "path": str(path) if path else None, "notes": notes}


def blocker(reason_code: str, details: str, review_item_id: str | None = None, candidate_record_id: str | None = None) -> dict[str, Any]:
    if reason_code not in BLOCKER_CODES:
        reason_code = "unsupported_record"
    return {
        "reason_code": reason_code,
        "details": details,
        "review_item_id": review_item_id,
        "candidate_record_id": candidate_record_id,
    }


def current_records(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in as_list(data.get("normalized_currents")) if isinstance(row, dict)]


def rating_records(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in as_list(data.get("normalized_ratings")) if isinstance(row, dict)]


def current_identity(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row.get(key) for key in ("record_type", "target_type", "branch_id", "rail_name", "net_name", "refdes", "pin", "current_type") if row.get(key) not in (None, "", [])}


def rating_identity(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row.get(key) for key in ("target_type", "normalized_target_type", "refdes", "pin", "rail_name", "branch_id", "net_name", "normalized_rating_name") if row.get(key) not in (None, "", [])}


def current_value(row: dict[str, Any]) -> dict[str, Any]:
    return {"value": row.get("value"), "unit": row.get("unit"), "condition": row.get("current_type")}


def rating_value(row: dict[str, Any]) -> dict[str, Any]:
    return {"value": row.get("value_a"), "unit": row.get("unit"), "condition": row.get("normalized_rating_name")}


def missing_identity(identity: dict[str, Any], family: str) -> bool:
    if family == "current_model":
        return not any(identity.get(key) for key in ("branch_id", "rail_name", "refdes", "net_name"))
    return not any(identity.get(key) for key in ("branch_id", "rail_name", "refdes", "net_name"))


def missing_value(value: dict[str, Any]) -> bool:
    raw = value.get("value")
    return raw is None or (isinstance(raw, float) and not math.isfinite(raw))


def missing_unit(value: dict[str, Any]) -> bool:
    return value.get("unit") in (None, "")


def identities_match(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if not left or not right:
        return False
    return left == right


def values_match(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return left == right


def find_core_match(identity: dict[str, Any], core_rows: list[dict[str, Any]], family: str) -> tuple[dict[str, Any] | None, dict[str, Any], dict[str, Any]]:
    for core in core_rows:
        core_identity = current_identity(core) if family == "current_model" else rating_identity(core)
        if not identities_match(identity, core_identity):
            continue
        core_value = current_value(core) if family == "current_model" else rating_value(core)
        return core, core_identity, core_value
    return None, {}, {}


def candidate_record_id(row: dict[str, Any], family: str) -> str:
    if row.get("candidate_record_id"):
        return str(row["candidate_record_id"])
    if family == "current_model":
        return str(row.get("record_id") or digest_id("current", row))
    return str(row.get("rating_id") or row.get("source_record_id") or digest_id("rating", row))


def candidate_source(row: dict[str, Any], family: str) -> dict[str, Any]:
    return {
        "record_id": row.get("record_id"),
        "rating_id": row.get("rating_id"),
        "source_record_id": row.get("source_record_id"),
        "record_family": family,
        "source_artifacts": as_list(row.get("source_artifacts")),
    }


def review_item(
    row: dict[str, Any],
    family: str,
    classification: str,
    identity: dict[str, Any],
    value: dict[str, Any],
    core_match: dict[str, Any],
    blockers: list[dict[str, Any]],
    warnings: list[str],
) -> dict[str, Any]:
    candidate_id = candidate_record_id(row, family)
    review_id = "review_" + digest_id(family, candidate_id, identity, value)
    return {
        "review_item_id": review_id,
        "candidate_record_id": candidate_id,
        "record_family": family,
        "review_classification": classification,
        "candidate_identity": identity,
        "candidate_value": value,
        "core_match": core_match,
        "candidate_source": candidate_source(row, family),
        "provenance": {
            "source_artifacts": as_list(row.get("source_artifacts")),
            "evidence_refs": as_list(row.get("evidence_refs")),
            "basis": row.get("basis"),
        },
        "ready_for_core_apply": False,
        "requires_human_review": True,
        "requires_future_core_apply_stage": True,
        "blockers": blockers,
        "warnings": warnings,
    }


def classify_records(
    *,
    family: str,
    candidate_rows: list[dict[str, Any]],
    core_rows: list[dict[str, Any]],
    core_missing: bool,
    allow_missing_core: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    items: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    warnings: list[str] = []
    missing_core_warning = "core current normalized artifact is missing" if family == "current_model" else "core rating normalized artifact is missing"
    missing_core_code = "missing_core_current_normalized" if family == "current_model" else "missing_core_rating_normalized"
    for row in sorted(candidate_rows, key=lambda item: candidate_record_id(item, family)):
        identity = current_identity(row) if family == "current_model" else rating_identity(row)
        value = current_value(row) if family == "current_model" else rating_value(row)
        item_blockers: list[dict[str, Any]] = []
        item_warnings: list[str] = []
        classification = "add_candidate"
        if row.get("record_type") not in (None, "branch_current", "rail_current", "component_current", "rating") and family == "current_model":
            classification = "unsupported_record"
        elif missing_identity(identity, family):
            classification = "missing_identity"
        elif missing_value(value):
            classification = "missing_value"
        elif missing_unit(value):
            classification = "missing_unit"
        elif core_missing:
            classification = "add_candidate"
            if allow_missing_core:
                item_warnings.append(missing_core_warning)
                if missing_core_warning not in warnings:
                    warnings.append(missing_core_warning)
            else:
                item_warnings.append(missing_core_warning)
        else:
            match, core_identity, core_value = find_core_match(identity, core_rows, family)
            if match is None:
                classification = "add_candidate"
            elif values_match(value, core_value):
                classification = "duplicate_existing"
            else:
                classification = "conflict_with_core"
        candidate_id = candidate_record_id(row, family)
        core_match = {"match_status": "none", "identity": None, "value": None, "record": None}
        if classification in {"duplicate_existing", "conflict_with_core"}:
            match, core_identity, core_value = find_core_match(identity, core_rows, family)
            core_match = {"match_status": classification, "identity": core_identity, "value": core_value, "record": match}
        if classification in {"missing_identity", "missing_value", "missing_unit", "unsupported_record", "conflict_with_core"}:
            item_blockers.append(blocker(classification, f"{family} candidate classified as {classification}", None, candidate_id))
        if core_missing and not allow_missing_core:
            item_blockers.append(blocker(missing_core_code, missing_core_warning, None, candidate_id))
        item = review_item(row, family, classification, identity, value, core_match, item_blockers, item_warnings)
        for item_blocker in item_blockers:
            item_blocker["review_item_id"] = item["review_item_id"]
            blockers.append(item_blocker)
        items.append(item)
    return items, blockers, warnings


def provenance_gaps_from_review(source_review: dict[str, Any], source_blockers: dict[str, Any]) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for gap in as_list(source_review.get("provenance_gaps")):
        if isinstance(gap, dict):
            gaps.append({"reason_code": "provenance_gap", "field": gap.get("field"), "detail": str(gap.get("detail") or "PR36 provenance gap")})
    for row in as_list(source_blockers.get("blocker_records")):
        if isinstance(row, dict) and row.get("reason_code") == "provenance_gap":
            detail = str(row.get("details") or row.get("detail") or "PR36 provenance gap")
            if not any(existing.get("detail") == detail for existing in gaps):
                gaps.append({"reason_code": "provenance_gap", "field": row.get("field"), "detail": detail})
    return sorted(gaps, key=lambda row: sort_key(row.get("detail")))


def build_outputs(
    *,
    project: str,
    candidate_ingested_dir: Path,
    out_dir: Path,
    core_current_path: Path | None,
    core_rating_path: Path | None,
    strict: bool,
    allow_missing_core: bool,
) -> tuple[dict[str, dict[str, Any]], int]:
    candidate_ingested_dir = candidate_ingested_dir.resolve()
    out_dir = out_dir.resolve()
    for filename in OUTPUTS.values():
        verify_output_path(out_dir / filename, out_dir, project)

    manifest_path = candidate_ingested_dir / INPUTS["manifest"]
    status_path = candidate_ingested_dir / INPUTS["status"]
    current_path = candidate_ingested_dir / INPUTS["current"]
    rating_path = candidate_ingested_dir / INPUTS["rating"]
    review_path = candidate_ingested_dir / INPUTS["review"]
    blockers_path = candidate_ingested_dir / INPUTS["blockers"]
    addenda_path = candidate_ingested_dir / INPUTS["addenda"]

    if not current_path.exists():
        raise FileNotFoundError("missing_candidate_current_normalized")
    if not rating_path.exists():
        raise FileNotFoundError("missing_candidate_rating_normalized")

    source_manifest = require_object(manifest_path, "malformed_candidate_ingest_manifest")
    source_status = require_object(status_path, "malformed_candidate_ingest_status")
    candidate_current = require_object(current_path, "malformed_candidate_current_normalized")
    candidate_rating = require_object(rating_path, "malformed_candidate_rating_normalized")
    source_review = optional_object(review_path, "malformed_candidate_ingest_review")
    source_blockers = optional_object(blockers_path, "malformed_candidate_ingest_blockers")
    addenda_index = optional_object(addenda_path, "malformed_candidate_ingest_addenda")

    blockers: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []
    if source_status.get("status") == "candidate_ingest_failed":
        blockers.append(blocker("candidate_ingest_status_failed", "PR36 candidate ingest status is failed"))
        if strict:
            errors.append("PR36 candidate ingest status is failed")

    core_current_missing = core_current_path is None or not core_current_path.exists()
    core_rating_missing = core_rating_path is None or not core_rating_path.exists()
    core_current = optional_object(core_current_path, "malformed_core_current_normalized") if not core_current_missing else {}
    core_rating = optional_object(core_rating_path, "malformed_core_rating_normalized") if not core_rating_missing else {}
    if core_current_missing:
        detail = "core current normalized artifact is missing"
        warnings.append(detail)
        blockers.append(blocker("missing_core_current_normalized", detail))
        if strict and not allow_missing_core:
            errors.append(detail)
    if core_rating_missing:
        detail = "core rating normalized artifact is missing"
        warnings.append(detail)
        blockers.append(blocker("missing_core_rating_normalized", detail))
        if strict and not allow_missing_core:
            errors.append(detail)

    current_items, current_blockers, current_warnings = classify_records(
        family="current_model",
        candidate_rows=current_records(candidate_current),
        core_rows=current_records(core_current),
        core_missing=core_current_missing,
        allow_missing_core=allow_missing_core,
    )
    rating_items, rating_blockers, rating_warnings = classify_records(
        family="rating_model",
        candidate_rows=rating_records(candidate_rating),
        core_rows=rating_records(core_rating),
        core_missing=core_rating_missing,
        allow_missing_core=allow_missing_core,
    )
    blockers.extend(current_blockers)
    blockers.extend(rating_blockers)
    warnings.extend(w for w in current_warnings + rating_warnings if w not in warnings)

    provenance_gaps = provenance_gaps_from_review(source_review, source_blockers)
    for gap in provenance_gaps:
        blockers.append(blocker("provenance_gap", str(gap.get("detail") or "PR36 provenance gap")))

    for row in as_list(addenda_index.get("indexed_addenda_files")):
        if isinstance(row, dict) and int(row.get("record_count") or 0) > 0:
            blockers.append(blocker("addenda_requires_merge_validator", "candidate addenda require a future merge validator"))

    all_items = current_items + rating_items
    blocked_items = [item for item in all_items if item["blockers"] or item["review_classification"] in {"missing_identity", "missing_value", "missing_unit", "unsupported_record", "conflict_with_core", "provenance_gap"}]
    review_ready_items = [item for item in all_items if item["review_classification"] in {"add_candidate", "duplicate_existing", "conflict_with_core"}]
    class_counts = {name: sum(1 for item in all_items if item["review_classification"] == name) for name in CLASSIFICATIONS}
    summary = {
        "candidate_current_record_count": len(current_records(candidate_current)),
        "candidate_rating_record_count": len(rating_records(candidate_rating)),
        "core_current_record_count": 0 if core_current_missing else len(current_records(core_current)),
        "core_rating_record_count": 0 if core_rating_missing else len(rating_records(core_rating)),
        "current_review_item_count": len(current_items),
        "rating_review_item_count": len(rating_items),
        "add_candidate_count": class_counts["add_candidate"],
        "duplicate_existing_count": class_counts["duplicate_existing"],
        "conflict_with_core_count": class_counts["conflict_with_core"],
        "missing_identity_count": class_counts["missing_identity"],
        "missing_value_count": class_counts["missing_value"],
        "missing_unit_count": class_counts["missing_unit"],
        "provenance_gap_count": len(provenance_gaps) + class_counts["provenance_gap"],
        "unsupported_record_count": class_counts["unsupported_record"],
        "blocked_item_count": len(blocked_items),
        "review_ready_item_count": len(review_ready_items),
        "human_review_required_count": len(all_items),
        "applied_anything": False,
        "wrote_core_artifacts": False,
        "wrote_core_normalized_outputs": False,
        "ran_ingestion": False,
        "ran_current_allocation": False,
        "ran_calculations": False,
        "merged_addenda": False,
        "safe_for_core_apply": False,
        "error_count": len(errors),
        "warning_count": len(warnings),
    }
    generated = utc_now()
    source_artifacts = [
        source_artifact("ai_candidate_core_input_ingest_manifest", manifest_path),
        source_artifact("ai_candidate_core_input_ingest_status", status_path),
        source_artifact("ai_candidate_current_models_normalized", current_path),
        source_artifact("ai_candidate_rating_models_normalized", rating_path),
        source_artifact("ai_candidate_core_input_ingest_review", review_path if review_path.exists() else None),
        source_artifact("ai_candidate_core_input_ingest_blockers", blockers_path if blockers_path.exists() else None),
    ]
    candidate_outputs = {key: str(out_dir / filename) for key, filename in OUTPUTS.items()}
    status_text = "review_failed" if errors else ("review_with_warnings" if warnings or blockers else "review_pass")
    review = {
        "project": project,
        "generated_at_utc": generated,
        "schema_version": SCHEMA_VERSION,
        "review_only": True,
        "source_artifacts": source_artifacts,
        "source_candidate_ingest_manifest": str(manifest_path),
        "source_candidate_ingest_status": str(status_path),
        "core_artifacts": {
            "current_models_normalized": str(core_current_path) if core_current_path else None,
            "rating_models_normalized": str(core_rating_path) if core_rating_path else None,
        },
        "candidate_outputs": candidate_outputs,
        "current_model_review_items": current_items,
        "rating_model_review_items": rating_items,
        "provenance_gaps": provenance_gaps,
        "blocked_items": blocked_items,
        "summary": summary,
        "errors": errors,
        "warnings": warnings,
    }
    status = {
        "project": project,
        "generated_at_utc": generated,
        "schema_version": SCHEMA_VERSION,
        "status": status_text,
        "review_only": True,
        "applied_anything": False,
        "wrote_core_artifacts": False,
        "wrote_core_normalized_outputs": False,
        "ran_ingestion": False,
        "ran_current_allocation": False,
        "ran_calculations": False,
        "merged_addenda": False,
        "safe_for_core_apply": False,
        "requires_future_core_apply_stage": True,
        "current_review_item_count": len(current_items),
        "rating_review_item_count": len(rating_items),
        "blocked_item_count": len(blocked_items),
        "provenance_gap_count": summary["provenance_gap_count"],
        "errors": errors,
        "warnings": warnings,
    }
    current_diff = {
        "project": project,
        "generated_at_utc": generated,
        "schema_version": SCHEMA_VERSION,
        "review_only": True,
        "record_family": "current_model",
        "core_artifact": str(core_current_path) if core_current_path else None,
        "candidate_artifact": str(current_path),
        "review_items": current_items,
        "summary": summary,
        "errors": errors,
        "warnings": warnings,
    }
    rating_diff = {
        "project": project,
        "generated_at_utc": generated,
        "schema_version": SCHEMA_VERSION,
        "review_only": True,
        "record_family": "rating_model",
        "core_artifact": str(core_rating_path) if core_rating_path else None,
        "candidate_artifact": str(rating_path),
        "review_items": rating_items,
        "summary": summary,
        "errors": errors,
        "warnings": warnings,
    }
    readiness = {
        "project": project,
        "generated_at_utc": generated,
        "schema_version": READINESS_SCHEMA_VERSION,
        "review_only": True,
        "ready_for_core_apply": False,
        "safe_for_core_apply": False,
        "requires_future_core_apply_stage": True,
        "human_review_required": True,
        "review_ready_items": review_ready_items,
        "blocked_items": blocked_items,
        "summary": summary,
        "errors": errors,
        "warnings": warnings,
    }
    blockers_artifact = {
        "project": project,
        "generated_at_utc": generated,
        "schema_version": SCHEMA_VERSION,
        "review_only": True,
        "blocker_records": sorted(blockers, key=lambda row: (sort_key(row.get("reason_code")), sort_key(row.get("candidate_record_id")), sort_key(row.get("details")))),
        "summary": {"blocker_count": len(blockers)},
        "errors": errors,
        "warnings": warnings,
    }
    return {
        "review": review,
        "status": status,
        "current_diff": current_diff,
        "rating_diff": rating_diff,
        "readiness": readiness,
        "blockers": blockers_artifact,
    }, 1 if errors else 0


def validate_outputs(outputs: list[dict[str, Any]], schema_path: Path) -> None:
    schema = load_json(schema_path)
    jsonschema.Draft7Validator.check_schema(schema)
    for output in outputs:
        forbidden = sorted(walk_keys(output).intersection(FORBIDDEN_FIELDS))
        if forbidden:
            raise ValueError(f"review output contains forbidden field(s): {', '.join(forbidden)}")
        if has_forbidden_true_safe_merge(output):
            raise ValueError("review output contains safe_to_merge_automatically true")
        jsonschema.validate(instance=output, schema=schema)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Review PR36 candidate normalized outputs without applying them.")
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--candidate-ingested-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--core-current-models-normalized")
    parser.add_argument("--core-rating-models-normalized")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--allow-missing-core", action="store_true")
    parser.add_argument("--schema", default=DEFAULT_SCHEMA)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    candidate_ingested_dir = Path(args.candidate_ingested_dir).resolve()
    try:
        if not candidate_ingested_dir.exists():
            raise FileNotFoundError("missing_candidate_ingested_dir")
        if not (candidate_ingested_dir / INPUTS["manifest"]).exists():
            raise FileNotFoundError("missing_candidate_ingest_manifest")
        if not (candidate_ingested_dir / INPUTS["status"]).exists():
            raise FileNotFoundError("missing_candidate_ingest_status")
        outputs, code = build_outputs(
            project=args.project,
            candidate_ingested_dir=candidate_ingested_dir,
            out_dir=Path(args.out_dir),
            core_current_path=Path(args.core_current_models_normalized).resolve() if args.core_current_models_normalized else None,
            core_rating_path=Path(args.core_rating_models_normalized).resolve() if args.core_rating_models_normalized else None,
            strict=args.strict,
            allow_missing_core=args.allow_missing_core,
        )
        validate_outputs(list(outputs.values()), Path(args.schema))
        out_dir = Path(args.out_dir).resolve()
        write_json(out_dir / OUTPUTS["review"], outputs["review"])
        write_json(out_dir / OUTPUTS["status"], outputs["status"])
        write_json(out_dir / OUTPUTS["current_diff"], outputs["current_diff"])
        write_json(out_dir / OUTPUTS["rating_diff"], outputs["rating_diff"])
        write_json(out_dir / OUTPUTS["readiness"], outputs["readiness"])
        write_json(out_dir / OUTPUTS["blockers"], outputs["blockers"])
        summary = outputs["review"]["summary"]
        print(
            "ai candidate normalized promotion review: "
            f"project={args.project} current={summary['current_review_item_count']} "
            f"rating={summary['rating_review_item_count']} blockers={summary['blocked_item_count']} "
            f"out={out_dir}"
        )
        return code
    except (FileNotFoundError, ValueError, json.JSONDecodeError, jsonschema.ValidationError) as exc:
        print(f"ai candidate normalized promotion review error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
