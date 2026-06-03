#!/usr/bin/env python3
"""Build a review-only report for AI extraction and promotion candidates.

Phase 11A scope only: collect existing PR27/PR31/PR32/PR37 artifacts into
operator-readable and machine-readable review reports. This script does not
apply candidates, edit approval decisions, overwrite core artifacts, rerun
allocation/calculations, or create pass/fail findings.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "ai_candidate_review_report_v1"

OUTPUT_JSON = "ai-candidate-review-report.json"
OUTPUT_MD = "ai-candidate-review-report.md"
OUTPUT_TXT = "ai-candidate-review-summary.txt"

ARTIFACTS: dict[str, tuple[str, bool]] = {
    "phase_driver_status": ("phase-driver-status.json", False),
    "phase_driver_manifest": ("phase-driver-manifest.json", False),
    "pr27_validation": ("pr27_ai_extraction_validate/ai-extraction-validation.json", True),
    "pr31_ingestion_review": ("pr31_ai_candidate_ingest/ai-candidate-ingestion-review.json", False),
    "pr31_ingestion_status": ("pr31_ai_candidate_ingest/ai-candidate-ingestion-status.json", False),
    "pr31_current_models": ("pr31_ai_candidate_ingest/ai-current-models-normalized.json", False),
    "pr31_rating_models": ("pr31_ai_candidate_ingest/ai-rating-models-normalized.json", False),
    "pr31_rating_current_models": ("pr31_ai_candidate_ingest/ai-rating-current-models-normalized.json", False),
    "pr31_human_review_index": ("pr31_ai_candidate_ingest/ai-human-review-index.json", False),
    "pr32_approval_queue": ("pr32_ai_promotion_plan/ai-candidate-approval-queue.json", False),
    "pr32_promotion_plan": ("pr32_ai_promotion_plan/ai-candidate-promotion-plan.json", False),
    "pr32_promotion_diff": ("pr32_ai_promotion_plan/ai-candidate-promotion-diff.json", False),
    "pr32_promotion_status": ("pr32_ai_promotion_plan/ai-candidate-promotion-status.json", False),
    "pr32_human_review_index": ("pr32_ai_promotion_plan/ai-human-review-promotion-index.json", False),
    "pr32_approval_decisions": ("pr32_ai_promotion_plan/ai-approval-decisions.json", False),
    "pr32_approval_decision_validation": ("pr32_ai_promotion_plan/ai-approval-decision-validation.json", False),
    "pr37_readiness": ("pr37_ai_candidate_normalized_review/ai-candidate-normalized-approval-readiness.json", False),
    "pr37_normalized_review": ("pr37_ai_candidate_normalized_review/ai-candidate-normalized-promotion-review.json", False),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def json_safe(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(key): json_safe(child) for key, child in value.items()}
    if isinstance(value, list):
        return [json_safe(child) for child in value]
    return value


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_safe(data), indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"artifact must be a JSON object: {path}")
    return data


def safe_load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return load_json(path)


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def first_non_empty(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def nested_get(value: Any, *keys: str) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def clip(value: Any, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def source_artifact(name: str, run_dir: Path, rel_path: str, required: bool) -> dict[str, Any]:
    path = run_dir / rel_path
    return {
        "artifact_name": name,
        "path": str(path),
        "required": required,
        "present": path.exists(),
    }


def artifact_index(run_dir: Path) -> dict[str, dict[str, Any]]:
    return {
        name: source_artifact(name, run_dir, rel_path, required)
        for name, (rel_path, required) in ARTIFACTS.items()
    }


def require_artifacts(index: dict[str, dict[str, Any]]) -> None:
    missing = [row["path"] for row in index.values() if row["required"] and not row["present"]]
    if missing:
        joined = "\n".join(f"  - {path}" for path in missing)
        raise FileNotFoundError(f"missing required Phase 11A source artifact(s):\n{joined}")


def load_artifacts(run_dir: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    index = artifact_index(run_dir)
    require_artifacts(index)
    data: dict[str, dict[str, Any]] = {}
    for name, meta in index.items():
        data[name] = safe_load(Path(meta["path"])) if meta["present"] else {}
    return index, data


def target_identity_from_row(row: dict[str, Any]) -> dict[str, Any]:
    identity = row.get("target_identity") if isinstance(row.get("target_identity"), dict) else {}
    return {
        "target_type": first_non_empty(row.get("target_type"), identity.get("target_type"), row.get("normalized_target_type")),
        "refdes": first_non_empty(row.get("target_refdes"), row.get("refdes"), identity.get("refdes")),
        "target_mpn": row.get("target_mpn"),
        "pin": first_non_empty(row.get("pin"), identity.get("pin")),
        "rail_name": first_non_empty(row.get("rail_name"), identity.get("rail_name")),
        "branch_id": first_non_empty(row.get("branch_id"), identity.get("branch_id")),
        "field_name": first_non_empty(
            row.get("field_name"),
            row.get("rating_name"),
            row.get("normalized_rating_name"),
            row.get("current_type"),
            identity.get("field_name"),
        ),
    }


def row_value(row: dict[str, Any]) -> tuple[Any, Any]:
    candidate_value = row.get("candidate_value") if isinstance(row.get("candidate_value"), dict) else {}
    value = first_non_empty(
        row.get("normalized_value"),
        row.get("value"),
        row.get("value_a"),
        row.get("original_value"),
        candidate_value.get("normalized_value"),
        candidate_value.get("value"),
    )
    unit = first_non_empty(
        row.get("normalized_unit"),
        row.get("unit"),
        row.get("original_unit"),
        candidate_value.get("normalized_unit"),
        candidate_value.get("unit"),
    )
    return value, unit


def row_evidence_refs(row: dict[str, Any]) -> list[str]:
    refs = as_list(row.get("evidence_refs"))
    if refs:
        return [str(ref) for ref in refs]
    source_file = row.get("source_file")
    quote = row.get("evidence_quote")
    if source_file and quote:
        page = row.get("source_page") if row.get("source_page") is not None else row.get("page")
        prefix = f"{source_file}:{page}" if page is not None else str(source_file)
        return [f"{prefix}:{clip(quote, 180)}"]
    return []


def evidence_quote_from_refs(row: dict[str, Any]) -> str | None:
    if row.get("evidence_quote"):
        return str(row.get("evidence_quote"))
    refs = row_evidence_refs(row)
    if refs:
        return refs[0]
    return None


def row_missing_ids(row: dict[str, Any]) -> list[str]:
    ids = as_list(row.get("missing_data_item_ids")) or as_list(row.get("missing_data_manifest_item_ids"))
    if row.get("missing_data_item_id") and row.get("missing_data_item_id") not in ids:
        ids.append(row.get("missing_data_item_id"))
    return [str(item) for item in ids if item not in (None, "")]


def classify_candidate(row: dict[str, Any]) -> tuple[str, list[str], str]:
    identity = target_identity_from_row(row)
    target_type = str(identity.get("target_type") or "")
    field_name = str(identity.get("field_name") or "")
    text = " ".join(
        str(part or "")
        for part in (
            row.get("basis"),
            row.get("condition"),
            row.get("evidence_quote"),
            " ".join(row_evidence_refs(row)),
        )
    ).lower()
    flags: list[str] = []
    classification = "candidate_fact"

    if field_name == "branch_current_a":
        classification = "branch_current_requires_board_evidence"
        flags.append("must_not_auto_approve_branch_current")
        if "board operating" not in text and "allocation" not in text:
            flags.append("missing_board_operating_current_or_allocation")
        return classification, sorted(set(flags)), "needs_info"

    rating_types = {
        "connector_rating",
        "connector_pin_rating",
        "connector",
        "connector_pin",
        "load_switch_rating",
        "load_switch",
        "regulator_rating",
        "regulator",
        "fuse_rating",
        "fuse",
        "ferrite_rating",
        "ferrite",
    }
    if target_type in rating_types or "rating" in target_type or "rating" in str(row.get("basis") or ""):
        flags.append("rating_or_capability_only")
        flags.append("not_branch_operating_current")

    if target_type in {"connector_rating", "connector_pin_rating", "connector", "connector_pin"} and field_name in {"current_max", "pin_current_max"}:
        classification = "connector_rating"
    elif target_type in {"load_switch_rating", "load_switch"} and field_name in {"current_max", "current_max_a"}:
        classification = "load_switch_or_mosfet_rating"
    elif target_type == "component_current_model" and field_name in {"max_current_a", "typ_current_a"}:
        classification = "component_capability_or_consumption_fact"
        if not any(term in text for term in ("supply", "quiescent", "current consumption", "icc", "idd", "iq")):
            flags.append("evidence_basis_needs_review")

    if not row_evidence_refs(row) and not row.get("evidence_quote"):
        flags.append("missing_clear_evidence")
        return classification, sorted(set(flags)), "needs_info"
    if bool(row.get("human_review_needed")):
        return classification, sorted(set(flags)), "human_review"
    if flags and any(flag in flags for flag in ("rating_or_capability_only", "not_branch_operating_current")):
        return classification, sorted(set(flags)), "review_rating_capability"
    return classification, sorted(set(flags)), "review_before_approval"


def candidate_record_id(row: dict[str, Any], source: str, index: int) -> str:
    return str(
        first_non_empty(
            row.get("candidate_record_id"),
            row.get("record_id"),
            row.get("rating_id"),
            row.get("human_review_item_id"),
            row.get("accepted_item_id"),
            row.get("source_record_id"),
            f"{source}_{index:06d}",
        )
    )


def make_candidate_row(
    row: dict[str, Any],
    *,
    source_stage: str,
    source_kind: str,
    index: int,
    approval_by_promo: dict[str, dict[str, Any]] | None = None,
    approval_by_source: dict[str, dict[str, Any]] | None = None,
    promotion_by_id: dict[str, dict[str, Any]] | None = None,
    promotion_by_source: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload = row.get("candidate_item") if isinstance(row.get("candidate_item"), dict) else {}
    row = {**payload, **row}
    approval_by_promo = approval_by_promo or {}
    approval_by_source = approval_by_source or {}
    promotion_by_id = promotion_by_id or {}
    promotion_by_source = promotion_by_source or {}

    source_item_id = first_non_empty(row.get("source_item_id"), row.get("source_record_id"), row.get("record_id"), row.get("rating_id"))
    source_accepted_item_id = first_non_empty(row.get("accepted_item_id"), row.get("source_ai_accepted_item_id"))
    promotion_id = row.get("promotion_candidate_id")
    promotion = promotion_by_id.get(str(promotion_id)) if promotion_id else None
    if not promotion and source_accepted_item_id:
        promotion = promotion_by_source.get(str(source_accepted_item_id))
    if not promotion and source_item_id:
        promotion = promotion_by_source.get(str(source_item_id))
    if promotion and not promotion_id:
        promotion_id = promotion.get("promotion_candidate_id")

    approval = approval_by_promo.get(str(promotion_id)) if promotion_id else None
    if not approval and source_accepted_item_id:
        approval = approval_by_source.get(str(source_accepted_item_id))
    if not approval and source_item_id:
        approval = approval_by_source.get(str(source_item_id))

    identity = target_identity_from_row(row)
    if promotion and isinstance(promotion.get("target_identity"), dict):
        p_identity = target_identity_from_row(promotion)
        identity = {key: first_non_empty(identity.get(key), p_identity.get(key)) for key in set(identity) | set(p_identity)}
    value, unit = row_value(row)
    if promotion and value is None:
        value, unit = row_value(promotion)
    classification, flags, action = classify_candidate({**row, **identity})
    quote = evidence_quote_from_refs(row)
    if not quote and promotion:
        quote = evidence_quote_from_refs(promotion)
    evidence_refs = row_evidence_refs(row) or (row_evidence_refs(promotion) if promotion else [])

    candidate = {
        "candidate_record_id": candidate_record_id(row, source_kind, index),
        "promotion_candidate_id": promotion_id,
        "approval_item_id": approval.get("approval_item_id") if approval else row.get("approval_item_id"),
        "source_stage": source_stage,
        "source_kind": source_kind,
        "source_packet_id": first_non_empty(row.get("packet_id"), row.get("source_ai_packet_id"), promotion.get("source_ai_packet_id") if promotion else None),
        "source_item_id": source_item_id,
        "source_accepted_item_id": source_accepted_item_id or (promotion.get("source_ai_accepted_item_id") if promotion else None),
        "missing_data_item_ids": row_missing_ids(row) or (as_list(promotion.get("missing_data_item_ids")) if promotion else []),
        "target_type": identity.get("target_type"),
        "refdes": identity.get("refdes"),
        "target_mpn": identity.get("target_mpn"),
        "target_identity": identity,
        "field_name": identity.get("field_name"),
        "rating_name": first_non_empty(row.get("rating_name"), row.get("normalized_rating_name")),
        "value": value,
        "unit": unit,
        "basis": first_non_empty(row.get("basis"), promotion.get("basis") if promotion else None),
        "confidence": first_non_empty(row.get("confidence"), promotion.get("confidence") if promotion else None),
        "human_review_needed": bool(first_non_empty(row.get("human_review_needed"), row.get("approval_required"), approval.get("approval_required") if approval else None, False)),
        "evidence_refs": evidence_refs,
        "source_file": row.get("source_file"),
        "source_page": first_non_empty(row.get("source_page"), row.get("page")),
        "evidence_quote": quote,
        "recommended_review_classification": classification,
        "review_risk_flags": flags,
        "recommended_action": action,
    }
    if approval:
        candidate["approval_status"] = approval.get("status") or approval.get("decision")
        candidate["approval_recommended_action"] = approval.get("recommended_action")
    if promotion:
        candidate["promotion_status"] = promotion.get("promotion_status")
        candidate["candidate_kind"] = promotion.get("candidate_kind")
        candidate["core_match"] = promotion.get("core_match")
        candidate["safe_to_apply_automatically"] = promotion.get("safe_to_apply_automatically")
    return candidate


def build_promotion_maps(plan: dict[str, Any], queue: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    promotion_by_id: dict[str, dict[str, Any]] = {}
    promotion_by_source: dict[str, dict[str, Any]] = {}
    for row in as_list(plan.get("promotion_candidates")):
        if not isinstance(row, dict):
            continue
        pid = row.get("promotion_candidate_id")
        if pid:
            promotion_by_id[str(pid)] = row
        for key in ("source_ai_accepted_item_id", "source_candidate_record_id"):
            value = row.get(key)
            if value:
                promotion_by_source[str(value)] = row

    approval_by_promo: dict[str, dict[str, Any]] = {}
    approval_by_source: dict[str, dict[str, Any]] = {}
    for row in as_list(queue.get("approval_items")):
        if not isinstance(row, dict):
            continue
        pid = row.get("promotion_candidate_id")
        if pid:
            approval_by_promo[str(pid)] = row
            promotion = promotion_by_id.get(str(pid))
            if promotion:
                for key in ("source_ai_accepted_item_id", "source_candidate_record_id"):
                    value = promotion.get(key)
                    if value:
                        approval_by_source[str(value)] = row
    return approval_by_promo, approval_by_source, promotion_by_id, promotion_by_source


def status_counts(packet_results: list[Any]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in packet_results:
        if isinstance(row, dict):
            counts[str(row.get("status") or "unknown")] += 1
    return counts


def build_summary(data: dict[str, dict[str, Any]]) -> dict[str, Any]:
    pr27 = data["pr27_validation"]
    pr27_summary = pr27.get("summary") if isinstance(pr27.get("summary"), dict) else {}
    packet_status = status_counts(as_list(pr27.get("packet_results")))
    plan_summary = nested_get(data, "pr32_promotion_plan", "summary") or {}
    queue_summary = nested_get(data, "pr32_approval_queue", "summary") or {}
    readiness_summary = nested_get(data, "pr37_readiness", "summary") or {}
    review_summary = nested_get(data, "pr37_normalized_review", "summary") or {}
    return {
        "pr27_packet_count": first_non_empty(pr27_summary.get("packet_count"), len(as_list(pr27.get("packet_results")))),
        "pr27_packet_status_counts": dict(sorted(packet_status.items())),
        "accepted_packet_count": first_non_empty(pr27_summary.get("accepted_packet_count"), packet_status.get("accepted", 0)),
        "human_review_needed_packet_count": first_non_empty(pr27_summary.get("human_review_packet_count"), packet_status.get("human_review_needed", 0)),
        "accepted_item_count": first_non_empty(pr27_summary.get("accepted_item_count"), len(as_list(pr27.get("accepted_items")))),
        "human_review_item_count": first_non_empty(pr27_summary.get("human_review_item_count"), len(as_list(pr27.get("human_review_items")))),
        "rejected_item_count": first_non_empty(pr27_summary.get("rejected_item_count"), len(as_list(pr27.get("rejected_items")))),
        "error_count": first_non_empty(pr27_summary.get("error_count"), len(as_list(pr27.get("errors")))),
        "warning_count": first_non_empty(pr27_summary.get("warning_count"), len(as_list(pr27.get("warnings")))),
        "promotion_candidate_count": first_non_empty(plan_summary.get("promotion_candidate_count"), len(as_list(data["pr32_promotion_plan"].get("promotion_candidates")))),
        "approval_queue_count": first_non_empty(queue_summary.get("approval_queue_count"), len(as_list(data["pr32_approval_queue"].get("approval_items")))),
        "normalized_review_count": (
            len(as_list(data["pr37_normalized_review"].get("current_model_review_items")))
            + len(as_list(data["pr37_normalized_review"].get("rating_model_review_items")))
            + len(as_list(data["pr37_readiness"].get("review_ready_items")))
        ),
        "pr37_review_summary": review_summary,
        "pr37_readiness_summary": readiness_summary,
    }


def duplicate_groups(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (
            str(row.get("target_type") or ""),
            str(row.get("refdes") or ""),
            str(row.get("field_name") or row.get("rating_name") or ""),
            str(row.get("value") or ""),
            str(row.get("unit") or ""),
        )
        buckets[key].append(row)
    groups: list[dict[str, Any]] = []
    for key, members in sorted(buckets.items()):
        if len(members) < 2:
            continue
        groups.append(
            {
                "target_type": key[0],
                "refdes": key[1],
                "field_name": key[2],
                "value": key[3],
                "unit": key[4],
                "count": len(members),
                "candidate_record_ids": [str(member.get("candidate_record_id")) for member in members],
            }
        )
    return groups


def build_candidate_groups(data: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    queue = data["pr32_approval_queue"]
    plan = data["pr32_promotion_plan"]
    approval_by_promo, approval_by_source, promotion_by_id, promotion_by_source = build_promotion_maps(plan, queue)
    kwargs = {
        "approval_by_promo": approval_by_promo,
        "approval_by_source": approval_by_source,
        "promotion_by_id": promotion_by_id,
        "promotion_by_source": promotion_by_source,
    }

    pr27 = data["pr27_validation"]
    accepted = [
        make_candidate_row(row, source_stage="pr27", source_kind="accepted_item", index=index, **kwargs)
        for index, row in enumerate(as_list(pr27.get("accepted_items")), start=1)
        if isinstance(row, dict)
    ]
    human_review = [
        make_candidate_row(row, source_stage="pr27", source_kind="human_review_item", index=index, **kwargs)
        for index, row in enumerate(as_list(pr27.get("human_review_items")), start=1)
        if isinstance(row, dict)
    ]
    rejected = [
        make_candidate_row(row, source_stage="pr27", source_kind="rejected_item", index=index, **kwargs)
        for index, row in enumerate(as_list(pr27.get("rejected_items")), start=1)
        if isinstance(row, dict)
    ]

    pr31_current = [
        make_candidate_row(row, source_stage="pr31", source_kind="current_model_normalized", index=index, **kwargs)
        for index, row in enumerate(as_list(data["pr31_current_models"].get("normalized_currents")), start=1)
        if isinstance(row, dict)
    ]
    pr31_rating = [
        make_candidate_row(row, source_stage="pr31", source_kind="rating_model_normalized", index=index, **kwargs)
        for index, row in enumerate(as_list(data["pr31_rating_models"].get("normalized_ratings")), start=1)
        if isinstance(row, dict)
    ]
    pr31_rating_current = [
        make_candidate_row(row, source_stage="pr31", source_kind="rating_current_model_normalized", index=index, **kwargs)
        for index, row in enumerate(as_list(data["pr31_rating_current_models"].get("normalized_currents")), start=1)
        if isinstance(row, dict)
    ]
    review_rows = [
        make_candidate_row(row, source_stage="pr37", source_kind="normalized_review_item", index=index, **kwargs)
        for index, row in enumerate(
            as_list(data["pr37_normalized_review"].get("current_model_review_items"))
            + as_list(data["pr37_normalized_review"].get("rating_model_review_items"))
            + as_list(data["pr37_readiness"].get("review_ready_items")),
            start=1,
        )
        if isinstance(row, dict)
    ]
    blocked = rejected + [
        make_candidate_row(row, source_stage="pr32", source_kind="blocked_candidate", index=index, **kwargs)
        for index, row in enumerate(as_list(plan.get("blocked_candidates")) + as_list(data["pr37_readiness"].get("blocked_items")), start=1)
        if isinstance(row, dict)
    ]
    approval_rows = [
        make_candidate_row(row, source_stage="pr32", source_kind="approval_queue_item", index=index, **kwargs)
        for index, row in enumerate(as_list(queue.get("approval_items")), start=1)
        if isinstance(row, dict)
    ]

    all_rows = accepted + human_review + pr31_current + pr31_rating + pr31_rating_current + review_rows + approval_rows
    current_model = [row for row in all_rows if row.get("target_type") == "component_current_model" or row.get("source_kind") == "current_model_normalized"]
    rating_model = [
        row for row in all_rows
        if row.get("target_type") in {"connector_rating", "connector_pin_rating", "connector", "connector_pin", "load_switch_rating", "load_switch", "regulator_rating", "regulator", "fuse_rating", "fuse", "ferrite_rating", "ferrite"}
        or row.get("source_kind") == "rating_model_normalized"
    ]
    return {
        "accepted_candidates": accepted,
        "current_model_candidates": current_model,
        "rating_model_candidates": rating_model,
        "rating_current_model_candidates": pr31_rating_current,
        "human_review_candidates": human_review + approval_rows,
        "blocked_candidates": blocked,
        "normalized_review_candidates": review_rows,
        "duplicate_or_related_candidates": duplicate_groups(accepted + human_review),
    }


def safety_section(data: dict[str, dict[str, Any]]) -> dict[str, Any]:
    status = data.get("phase_driver_status") or {}
    manifest = data.get("phase_driver_manifest") or {}
    promotion_status = data.get("pr32_promotion_status") or {}
    readiness = data.get("pr37_readiness") or {}
    return {
        "no_core_writes_observed": not bool(first_non_empty(status.get("wrote_core_artifacts"), manifest.get("wrote_core_artifacts"), False)),
        "safe_for_core_apply": bool(first_non_empty(status.get("safe_for_core_apply"), readiness.get("safe_for_core_apply"), False)),
        "ready_for_core_apply": bool(first_non_empty(status.get("ready_for_core_apply"), readiness.get("ready_for_core_apply"), False)),
        "ran_post_promotion_allocation": bool(first_non_empty(status.get("ran_post_promotion_allocation"), manifest.get("ran_post_promotion_allocation"), False)),
        "ran_post_promotion_calculations": bool(first_non_empty(status.get("ran_post_promotion_calculations"), manifest.get("ran_post_promotion_calculations"), False)),
        "wrote_core_artifacts": bool(first_non_empty(status.get("wrote_core_artifacts"), manifest.get("wrote_core_artifacts"), False)),
        "promotion_safe_to_apply_automatically": bool(promotion_status.get("safe_to_apply_automatically", False)),
        "promotion_safe_to_overwrite_core_artifacts": bool(promotion_status.get("safe_to_overwrite_core_artifacts", False)),
    }


def open_questions(groups: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    rows = groups["accepted_candidates"] + groups["human_review_candidates"] + groups["blocked_candidates"]
    lacking = [row for row in rows if "missing_clear_evidence" in as_list(row.get("review_risk_flags"))]
    ratings = [row for row in rows if "rating_or_capability_only" in as_list(row.get("review_risk_flags"))]
    branch = [row for row in rows if row.get("field_name") == "branch_current_a"]
    human = [row for row in rows if row.get("human_review_needed") or row.get("approval_item_id")]

    def brief(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "candidate_record_id": row.get("candidate_record_id"),
            "approval_item_id": row.get("approval_item_id"),
            "target_type": row.get("target_type"),
            "refdes": row.get("refdes"),
            "field_name": row.get("field_name"),
            "value": row.get("value"),
            "unit": row.get("unit"),
            "recommended_action": row.get("recommended_action"),
        }

    return {
        "candidates_lacking_clear_evidence": [brief(row) for row in lacking],
        "candidates_whose_value_is_rating_or_capability_only": [brief(row) for row in ratings],
        "candidates_that_must_not_be_used_as_branch_current_a": [brief(row) for row in ratings + branch],
        "candidates_that_require_human_approval_before_any_apply": [brief(row) for row in human],
    }


def build_report(run_dir: Path) -> dict[str, Any]:
    index, data = load_artifacts(run_dir)
    summary = build_summary(data)
    groups = build_candidate_groups(data)
    phase_status = data.get("phase_driver_status") or data.get("phase_driver_manifest") or {}
    report = {
        "artifact_type": "ai_candidate_review_report",
        "schema_version": SCHEMA_VERSION,
        "project": first_non_empty(data["pr27_validation"].get("project"), phase_status.get("project"), "unknown"),
        "workflow": first_non_empty(phase_status.get("workflow"), "topology_ai"),
        "run_dir": str(run_dir),
        "generated_at_utc": utc_now(),
        "source_artifacts": list(index.values()),
        "summary": summary,
        "candidate_groups": groups,
        "safety": safety_section(data),
        "open_questions": open_questions(groups),
        "review_only": True,
        "do_not_apply_yet": True,
    }
    return report


def markdown_table(rows: list[dict[str, Any]], columns: list[tuple[str, str]], *, limit: int = 40) -> list[str]:
    if not rows:
        return ["_None._"]
    out = ["| " + " | ".join(label for label, _ in columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    for row in rows[:limit]:
        cells = []
        for _, key in columns:
            value = row.get(key)
            if key == "evidence_quote":
                value = clip(value, 120)
            elif isinstance(value, list):
                value = ", ".join(str(item) for item in value[:3])
            elif isinstance(value, dict):
                value = clip(json.dumps(value, sort_keys=True), 120)
            cells.append(clip(value, 120).replace("|", "\\|"))
        out.append("| " + " | ".join(cells) + " |")
    if len(rows) > limit:
        out.append(f"\n_Showing {limit} of {len(rows)} rows._")
    return out


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    safety = report["safety"]
    groups = report["candidate_groups"]
    by_target = Counter(
        f"{row.get('target_type')}/{row.get('field_name') or row.get('rating_name')}"
        for row in groups["accepted_candidates"] + groups["human_review_candidates"]
    )
    columns = [
        ("ID", "candidate_record_id"),
        ("Packet", "source_packet_id"),
        ("Target", "refdes"),
        ("Type", "target_type"),
        ("Field", "field_name"),
        ("Value", "value"),
        ("Unit", "unit"),
        ("Action", "recommended_action"),
        ("Evidence", "evidence_quote"),
    ]
    lines = [
        "# AI Candidate Review Report",
        "",
        "**Status:** Do not apply yet. This report is review-only and does not approve or mutate core artifacts.",
        "",
        "## Run Summary",
        "",
        f"- Project: `{report['project']}`",
        f"- Workflow: `{report['workflow']}`",
        f"- Run dir: `{report['run_dir']}`",
        f"- PR27 packet count: {summary.get('pr27_packet_count')}",
        f"- PR27 status counts: `{summary.get('pr27_packet_status_counts')}`",
        f"- Accepted items: {summary.get('accepted_item_count')}",
        f"- Human-review items: {summary.get('human_review_item_count')}",
        f"- Rejected items: {summary.get('rejected_item_count')}",
        f"- Errors: {summary.get('error_count')}",
        f"- Promotion candidates: {summary.get('promotion_candidate_count')}",
        f"- Approval queue items: {summary.get('approval_queue_count')}",
        "",
        "## Safety",
        "",
        f"- No core writes observed: `{safety.get('no_core_writes_observed')}`",
        f"- safe_for_core_apply: `{safety.get('safe_for_core_apply')}`",
        f"- ready_for_core_apply: `{safety.get('ready_for_core_apply')}`",
        f"- ran_post_promotion_allocation: `{safety.get('ran_post_promotion_allocation')}`",
        f"- ran_post_promotion_calculations: `{safety.get('ran_post_promotion_calculations')}`",
        "",
        "## Accepted Candidates",
        "",
        *markdown_table(groups["accepted_candidates"], columns),
        "",
        "## Human Review Needed Candidates",
        "",
        *markdown_table(groups["human_review_candidates"], columns),
        "",
        "## Blocked Or Rejected Candidates",
        "",
        *markdown_table(groups["blocked_candidates"], columns),
        "",
        "## Grouped By Target And Field",
        "",
        "| Target/Field | Count |",
        "| --- | --- |",
    ]
    lines.extend(f"| {key} | {count} |" for key, count in sorted(by_target.items()))
    lines.extend(
        [
            "",
            "## Evidence Notes",
            "",
            "Rating and capability candidates are useful facts for review, but must not be used as `branch_current_a` without explicit board operating-current or allocation evidence.",
            "",
            "## Next Commands",
            "",
            "Review the approval queue and pending decisions before any future apply-stage work:",
            "",
            "```bash",
            f"python3 -m json.tool {Path(report['run_dir']) / 'pr32_ai_promotion_plan' / 'ai-candidate-approval-queue.json'} | sed -n '1,220p'",
            f"python3 -m json.tool {Path(report['run_dir']) / 'pr32_ai_promotion_plan' / 'ai-approval-decisions.json'} | sed -n '1,220p'",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def render_summary(report: dict[str, Any], out_dir: Path) -> str:
    summary = report["summary"]
    safety = report["safety"]
    groups = report["candidate_groups"]
    by_target = Counter(
        f"{row.get('target_type')}/{row.get('field_name') or row.get('rating_name')}"
        for row in groups["accepted_candidates"] + groups["human_review_candidates"]
    )
    return "\n".join(
        [
            "AI candidate review report",
            f"project={report['project']}",
            f"workflow={report['workflow']}",
            f"run_dir={report['run_dir']}",
            f"out_dir={out_dir}",
            f"packet_results={summary.get('pr27_packet_count')}",
            f"status_counts={summary.get('pr27_packet_status_counts')}",
            f"accepted_item_count={summary.get('accepted_item_count')}",
            f"human_review_item_count={summary.get('human_review_item_count')}",
            f"rejected_item_count={summary.get('rejected_item_count')}",
            f"error_count={summary.get('error_count')}",
            f"promotion_candidate_count={summary.get('promotion_candidate_count')}",
            f"approval_queue_count={summary.get('approval_queue_count')}",
            f"accepted_candidate_rows={len(groups['accepted_candidates'])}",
            f"human_review_candidate_rows={len(groups['human_review_candidates'])}",
            f"target_field_counts={dict(sorted(by_target.items()))}",
            f"no_core_writes_observed={safety.get('no_core_writes_observed')}",
            f"safe_for_core_apply={safety.get('safe_for_core_apply')}",
            f"ready_for_core_apply={safety.get('ready_for_core_apply')}",
            "do_not_apply_yet=True",
            "",
        ]
    )


def write_report(report: dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / OUTPUT_JSON, report)
    (out_dir / OUTPUT_MD).write_text(render_markdown(report) + "\n", encoding="utf-8")
    (out_dir / OUTPUT_TXT).write_text(render_summary(report, out_dir), encoding="utf-8")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a review-only AI candidate report from Phase 10 topology_ai artifacts.")
    parser.add_argument("--run-dir", required=True, type=Path, help="Phase topology_ai run directory containing PR27/PR31/PR32/PR37 outputs.")
    parser.add_argument("--out-dir", required=True, type=Path, help="Output directory for Phase 11A review report artifacts.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        report = build_report(args.run_dir)
        write_report(report, args.out_dir)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ai_candidate_review_report: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {args.out_dir / OUTPUT_JSON}")
    print(f"wrote {args.out_dir / OUTPUT_MD}")
    print(f"wrote {args.out_dir / OUTPUT_TXT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
