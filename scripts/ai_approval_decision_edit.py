#!/usr/bin/env python3
"""AI Approval Queue Editor v0 — create and validate human approval decision artifacts.

PR33 scope only: read PR32 promotion-plan artifacts, produce a decision artifact
(ai-approval-decisions.json) with pending/approved/rejected/needs_info decisions per
approval item, and optionally validate that artifact (ai-approval-decision-validation.json).

This script does not call AI, apply approvals, overwrite core artifacts, run ingestion,
run allocation/calculations, merge addenda, create findings, or make pass/fail or
compliance judgments.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jsonschema


SCHEMA_VERSION_DECISION = "ai_approval_decisions_v1"
SCHEMA_VERSION_VALIDATION = "ai_approval_decision_validation_v1"
DEFAULT_PROJECT = "example"
DEFAULT_SCHEMA = "schemas/ai_approval_decision_schema.json"

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
}

ALLOWED_REASON_CODES = {
    "approved_evidence_sufficient",
    "approved_matches_datasheet",
    "approved_engineer_verified",
    "rejected_insufficient_evidence",
    "rejected_conflicts_with_core",
    "rejected_wrong_target",
    "rejected_wrong_unit",
    "rejected_wrong_condition",
    "rejected_duplicate_not_needed",
    "needs_info_missing_datasheet_page",
    "needs_info_ambiguous_condition",
    "needs_info_unclear_target",
    "needs_info_requires_engineer_review",
    "datasheet_rating_verified",
}

DECISION_STATUSES = {"pending", "approved", "rejected", "needs_info"}


# ---------------------------------------------------------------------------
# Utilities (mirrors PR32 patterns)
# ---------------------------------------------------------------------------


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def safe_load(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    data = load_json(path)
    return data if isinstance(data, dict) else {}


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


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def safe_id(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "_", str(value or "")).strip("_")
    return text or "unknown"


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


def path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def verify_output_path(path: Path, out_dir: Path, project: str) -> None:
    forbidden = {name.format(project=project) for name in FORBIDDEN_OUTPUT_FILENAMES}
    if path.name in forbidden:
        raise ValueError(f"forbidden core output filename: {path.name}")
    if not path_is_relative_to(path, out_dir):
        raise ValueError(f"output path must be inside out-dir: {path}")


def check_forbidden_fields(data: Any) -> list[str]:
    return sorted(walk_keys(data).intersection(FORBIDDEN_FIELDS))


# ---------------------------------------------------------------------------
# Decision template builder
# ---------------------------------------------------------------------------


def build_decision_template(project: str, approval_queue_data: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Create pending decisions for every approval queue item."""
    approval_items = as_list(approval_queue_data.get("approval_items"))
    source_artifacts = as_list(approval_queue_data.get("source_artifacts"))

    # Sort by approval_item_id for deterministic output
    sorted_items = sorted(approval_items, key=lambda x: safe_id(x.get("approval_item_id", "")))

    decisions: list[dict[str, Any]] = []
    for item in sorted_items:
        aid = str(item.get("approval_item_id", "unknown"))
        pid = item.get("promotion_candidate_id")
        decision_id = f"decision_{digest_id(aid, 'template')}"

        source_queue_item = {
            "review_type": str(item.get("review_type", "")),
            "priority": str(item.get("priority", "")),
            "target_summary": str(item.get("target_summary", "")),
            "candidate_summary": str(item.get("candidate_summary", "")),
            "core_summary": str(item.get("core_summary", "")),
        }

        decisions.append({
            "decision_id": decision_id,
            "approval_item_id": aid,
            "promotion_candidate_id": pid if isinstance(pid, (str, type(None))) else None,
            "decision": "pending",
            "reviewer": None,
            "reviewed_at_utc": None,
            "approval_note": None,
            "reason_code": None,
            "safe_to_apply": False,
            "source_queue_item": source_queue_item,
        })

    summary = {
        "approval_queue_count": len(approval_items),
        "decision_count": len(decisions),
        "pending_count": len(decisions),
        "approved_count": 0,
        "rejected_count": 0,
        "needs_info_count": 0,
        "invalid_decision_count": 0,
        "missing_decision_count": 0,
        "safe_to_apply_count": 0,
        "error_count": 0,
        "warning_count": 0,
    }

    return decisions, summary


# ---------------------------------------------------------------------------
# Decision editor (single-item edit)
# ---------------------------------------------------------------------------


def edit_decision(
    decisions: list[dict[str, Any]],
    approval_item_id: str,
    action: str,
    note: str | None = None,
    reason_code: str | None = None,
    reviewer: str | None = None,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Apply a single approval/reject/needs-info edit to one decision item."""
    errors: list[str] = []
    warnings: list[str] = []

    # Find existing decision for this approval_item_id
    target = None
    for d in decisions:
        if str(d.get("approval_item_id", "")) == approval_item_id:
            target = d
            break

    if target is None:
        errors.append(f"no decision found for approval_item_id={approval_item_id}")
        return decisions, errors, warnings

    now = utc_now()

    if action == "approve":
        if not note:
            errors.append("--approve requires --note")
            return decisions, errors, warnings
        target["decision"] = "approved"
        target["approval_note"] = note
        target["reviewed_at_utc"] = now
        target["reason_code"] = reason_code

    elif action == "reject":
        if not note and not reason_code:
            errors.append("--reject requires --note or --reason-code")
            return decisions, errors, warnings
        target["decision"] = "rejected"
        target["approval_note"] = note
        target["reviewed_at_utc"] = now
        target["reason_code"] = reason_code

    elif action == "needs_info":
        if not note:
            errors.append("--needs-info requires --note")
            return decisions, errors, warnings
        target["decision"] = "needs_info"
        target["approval_note"] = note
        target["reviewed_at_utc"] = now
        target["reason_code"] = reason_code

    else:
        errors.append(f"unknown action: {action}")
        return decisions, errors, warnings

    if reviewer is not None:
        target["reviewer"] = reviewer

    # safe_to_apply always remains false — never set it to true
    target["safe_to_apply"] = False

    return decisions, errors, warnings


# ---------------------------------------------------------------------------
# Decision validator
# ---------------------------------------------------------------------------


def validate_decisions(
    decisions_data: dict[str, Any],
    approval_queue_data: dict[str, Any],
    promotion_plan_data: dict[str, Any] | None,
    strict: bool = False,
    promotion_status_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a decision artifact against PR32 queue/plan data."""
    decisions = as_list(decisions_data.get("decisions"))
    approval_items = as_list(approval_queue_data.get("approval_items"))

    # Build lookup sets from PR32 artifacts
    valid_approval_ids: set[str] = set()
    queue_item_map: dict[str, dict[str, Any]] = {}
    for item in approval_items:
        aid = str(item.get("approval_item_id", ""))
        valid_approval_ids.add(aid)
        queue_item_map[aid] = item

    # Build valid promotion_candidate_id set from plan and queue
    valid_promotion_candidates: set[str | None] = {None}
    if promotion_plan_data:
        for candidate in as_list(promotion_plan_data.get("promotion_candidates", [])):
            cid = candidate.get("promotion_candidate_id")
            if isinstance(cid, str):
                valid_promotion_candidates.add(cid)
    for item in approval_items:
        pid = item.get("promotion_candidate_id")
        if isinstance(pid, str):
            valid_promotion_candidates.add(pid)

    validated: list[dict[str, Any]] = []
    invalid_list: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []

    # Track seen approval_item_ids for duplicate detection
    seen_approval_ids: set[str] = set()

    # Sort decisions by decision_id for deterministic output
    sorted_decisions = sorted(decisions, key=lambda d: str(d.get("decision_id", "")))

    for dec in sorted_decisions:
        aid = str(dec.get("approval_item_id", "unknown"))
        did = str(dec.get("decision_id", "unknown"))
        pid = dec.get("promotion_candidate_id")
        decision_val = dec.get("decision", "pending")
        note = dec.get("approval_note")
        reason_code = dec.get("reason_code")
        reviewed_at = dec.get("reviewed_at_utc")

        item_errors: list[str] = []
        item_warnings: list[str] = []

        # Check approval_item_id exists in queue
        if aid not in valid_approval_ids:
            item_errors.append(f"unknown approval_item_id={aid}")

        # Check for duplicate approval_item_id
        if aid in seen_approval_ids:
            item_errors.append(f"duplicate decision for approval_item_id={aid}")
        else:
            seen_approval_ids.add(aid)

        # Check promotion_candidate_id matches plan/queue
        if pid is not None and pid not in valid_promotion_candidates:
            item_errors.append(f"unknown promotion_candidate_id={pid}")

        # Decision-specific validation rules
        if decision_val not in DECISION_STATUSES:
            item_errors.append(f"invalid decision value={decision_val}")
        elif decision_val == "approved":
            if not dec.get("reviewer"):
                item_errors.append("approved decisions require non-empty reviewer")
            if not reason_code:
                item_errors.append("approved decisions require non-empty reason_code")
            if not note:
                item_errors.append("approved decisions require non-empty approval_note")
        elif decision_val == "rejected":
            if not dec.get("reviewer"):
                item_errors.append("rejected decisions require non-empty reviewer")
            if not reason_code:
                item_errors.append("rejected decisions require non-empty reason_code")
            if not note:
                item_errors.append("rejected decisions require non-empty approval_note")
        elif decision_val == "needs_info":
            if not dec.get("reviewer"):
                item_errors.append("needs_info decisions require non-empty reviewer")
            if not reason_code:
                item_errors.append("needs_info decisions require non-empty reason_code")
            if not note:
                item_errors.append("needs_info decisions require non-empty approval_note")

        # Validate reviewed_at_utc ISO-8601 format
        if reviewed_at is not None and isinstance(reviewed_at, str):
            try:
                datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                item_errors.append(f"invalid reviewed_at_utc format: {reviewed_at}")

        # Validate reason_code against allowed set
        if reason_code is not None and isinstance(reason_code, str) and reason_code not in ALLOWED_REASON_CODES:
            item_warnings.append(f"unknown reason_code={reason_code}")

        # Check safe_to_apply is false
        if dec.get("safe_to_apply") is True:
            item_errors.append("safe_to_apply must always be false")

        validation_status = "invalid" if item_errors else ("valid" if decision_val != "pending" else "pending")

        validated_entry = {
            "decision_id": did,
            "approval_item_id": aid,
            "promotion_candidate_id": pid,
            "decision": decision_val,
            "validation_status": validation_status,
            "safe_for_future_apply_stage": False,
            "reason_codes": [str(reason_code)] if reason_code and isinstance(reason_code, str) else None,
            "warnings": item_warnings,
        }

        if item_errors:
            invalid_list.append({
                "decision_id": did if did != "unknown" else None,
                "approval_item_id": aid,
                "validation_status": "invalid",
                "reasons": item_errors,
            })
            errors.extend(item_errors)
        else:
            validated.append(validated_entry)

    # Check for missing decisions (queue items without corresponding decision)
    missing: list[dict[str, Any]] = []
    for aid in sorted(valid_approval_ids):
        if aid not in seen_approval_ids:
            qi = queue_item_map.get(aid, {})
            missing.append({
                "approval_item_id": aid,
                "promotion_candidate_id": qi.get("promotion_candidate_id"),
                "review_type": str(qi.get("review_type", "")),
                "priority": str(qi.get("priority", "")),
                "target_summary": str(qi.get("target_summary", "")),
                "candidate_summary": str(qi.get("candidate_summary", "")),
                "core_summary": str(qi.get("core_summary", "")),
            })

    # Strict mode: check PR32 status
    if strict and promotion_status_data:
        status_val = promotion_status_data.get("status")
        if status_val == "failed":
            warnings.append("PR32 promotion status is failed; decisions marked invalid in strict mode")
            for v in validated:
                v["validation_status"] = "invalid"
                v["warnings"].append("promotion status failed (strict mode)")
            # Move all validated to invalid
            for v in list(validated):
                invalid_list.append({
                    "decision_id": v.get("decision_id"),
                    "approval_item_id": v.get("approval_item_id"),
                    "validation_status": "invalid",
                    "reasons": ["promotion status failed (strict mode)"],
                })
            validated = []

    # Compute summary counts
    decision_count = len(sorted_decisions)
    valid_count = sum(1 for d in validated if d["validation_status"] == "valid")
    invalid_count = len(invalid_list)
    pending_count = sum(1 for d in sorted_decisions if d.get("decision") == "pending")
    approved_count = sum(1 for d in sorted_decisions if d.get("decision") == "approved")
    rejected_count = sum(1 for d in sorted_decisions if d.get("decision") == "rejected")
    needs_info_count = sum(1 for d in sorted_decisions if d.get("decision") == "needs_info")
    safe_for_future_count = 0  # PR33 never sets this to true
    duplicate_decision_count = max(0, len(sorted_decisions) - len(seen_approval_ids))
    unknown_approval_item_id_count = sum(1 for d in sorted_decisions if str(d.get("approval_item_id", "")) not in valid_approval_ids)
    safe_to_apply_count = sum(1 for d in sorted_decisions if d.get("safe_to_apply") is True)

    summary = {
        "decision_count": decision_count,
        "valid_decision_count": valid_count,
        "invalid_decision_count": invalid_count,
        "pending_decision_count": pending_count,
        "pending_count": pending_count,
        "missing_decision_count": len(missing),
        "duplicate_decision_count": duplicate_decision_count,
        "unknown_approval_item_id_count": unknown_approval_item_id_count,
        "approved_count": approved_count,
        "rejected_count": rejected_count,
        "needs_info_count": needs_info_count,
        "safe_for_future_apply_count": safe_for_future_count,
        "safe_to_apply_count": safe_to_apply_count,
        "error_count": len(errors),
        "warning_count": len(warnings),
    }

    validation_pass = invalid_count == 0 and not (strict and promotion_status_data and promotion_status_data.get("status") == "failed")

    return {
        "project": decisions_data.get("project", ""),
        "generated_at_utc": utc_now(),
        "schema_version": SCHEMA_VERSION_VALIDATION,
        "source_artifacts": as_list(decisions_data.get("source_artifacts")),
        "source_decisions": None,  # set by caller
        "validation_pass": validation_pass,
        "validated_decisions": validated,
        "invalid_decisions": invalid_list,
        "missing_decisions": missing,
        "summary": summary,
        "errors": errors,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Summary builders
# ---------------------------------------------------------------------------


def compute_decision_summary(decisions: list[dict[str, Any]], errors: list[str], warnings: list[str]) -> dict[str, Any]:
    return {
        "approval_queue_count": 0,  # set by caller from queue data
        "decision_count": len(decisions),
        "pending_count": sum(1 for d in decisions if d.get("decision") == "pending"),
        "approved_count": sum(1 for d in decisions if d.get("decision") == "approved"),
        "rejected_count": sum(1 for d in decisions if d.get("decision") == "rejected"),
        "needs_info_count": sum(1 for d in decisions if d.get("decision") == "needs_info"),
        "invalid_decision_count": 0,  # set by caller from validation
        "missing_decision_count": 0,  # set by caller from validation
        "safe_to_apply_count": 0,
        "error_count": len(errors),
        "warning_count": len(warnings),
    }


# ---------------------------------------------------------------------------
# Phase 11B path-based review helpers
# ---------------------------------------------------------------------------


def clip_text(value: Any, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def source_queue_item_from_approval_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "review_type": str(item.get("review_type", "")),
        "priority": str(item.get("priority", "")),
        "target_summary": str(item.get("target_summary", "")),
        "candidate_summary": str(item.get("candidate_summary", "")),
        "core_summary": str(item.get("core_summary", "")),
    }


def queue_map(approval_queue_data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    items: dict[str, dict[str, Any]] = {}
    for item in as_list(approval_queue_data.get("approval_items")):
        if isinstance(item, dict) and item.get("approval_item_id"):
            items[str(item["approval_item_id"])] = item
    return items


def decision_maps(decisions_data: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], set[str]]:
    decisions: dict[str, dict[str, Any]] = {}
    duplicates: set[str] = set()
    for decision in as_list(decisions_data.get("decisions")):
        if not isinstance(decision, dict):
            continue
        aid = str(decision.get("approval_item_id", ""))
        if not aid:
            continue
        if aid in decisions:
            duplicates.add(aid)
        else:
            decisions[aid] = decision
    return decisions, duplicates


def decision_template_for_item(item: dict[str, Any]) -> dict[str, Any]:
    aid = str(item.get("approval_item_id", "unknown"))
    return {
        "decision_id": f"decision_{digest_id(aid, 'template')}",
        "approval_item_id": aid,
        "promotion_candidate_id": item.get("promotion_candidate_id") if isinstance(item.get("promotion_candidate_id"), (str, type(None))) else None,
        "decision": "pending",
        "reviewer": None,
        "reviewed_at_utc": None,
        "approval_note": None,
        "reason_code": None,
        "safe_to_apply": False,
        "source_queue_item": source_queue_item_from_approval_item(item),
    }


def ensure_decisions_for_queue(project: str, approval_queue_data: dict[str, Any], decisions_data: dict[str, Any] | None = None) -> dict[str, Any]:
    decisions_data = decisions_data if isinstance(decisions_data, dict) else {}
    existing, _ = decision_maps(decisions_data)
    decisions = []
    for item in sorted(as_list(approval_queue_data.get("approval_items")), key=lambda row: safe_id(row.get("approval_item_id", "")) if isinstance(row, dict) else ""):
        if not isinstance(item, dict):
            continue
        aid = str(item.get("approval_item_id", "unknown"))
        decisions.append(existing.get(aid) or decision_template_for_item(item))
    for decision in as_list(decisions_data.get("decisions")):
        if isinstance(decision, dict) and str(decision.get("approval_item_id", "")) not in {d.get("approval_item_id") for d in decisions}:
            decisions.append(decision)
    return {
        "project": decisions_data.get("project") or approval_queue_data.get("project") or project,
        "generated_at_utc": utc_now(),
        "schema_version": SCHEMA_VERSION_DECISION,
        "source_artifacts": as_list(decisions_data.get("source_artifacts")) or as_list(approval_queue_data.get("source_artifacts")),
        "source_promotion_plan": decisions_data.get("source_promotion_plan"),
        "source_approval_queue": decisions_data.get("source_approval_queue"),
        "decision_set_status": "draft",
        "decisions": decisions,
        "summary": compute_decision_summary(decisions, [], []),
        "errors": [],
        "warnings": [],
    }


def validate_decisions_phase11(decisions_data: dict[str, Any], approval_queue_data: dict[str, Any]) -> dict[str, Any]:
    validation = validate_decisions(decisions_data, approval_queue_data, promotion_plan_data=None)
    queue = queue_map(approval_queue_data)
    decisions = as_list(decisions_data.get("decisions"))
    seen: set[str] = set()
    duplicates: set[str] = set()
    unknown_count = 0
    duplicate_count = 0
    extra_errors: list[str] = []
    invalid_by_aid: dict[str, set[str]] = defaultdict(set)

    for dec in decisions:
        if not isinstance(dec, dict):
            continue
        aid = str(dec.get("approval_item_id", "unknown"))
        decision_val = dec.get("decision")
        if aid in seen:
            duplicates.add(aid)
            duplicate_count += 1
            invalid_by_aid[aid].add(f"duplicate decision for approval_item_id={aid}")
        else:
            seen.add(aid)
        if aid not in queue:
            unknown_count += 1
            invalid_by_aid[aid].add(f"unknown approval_item_id={aid}")
        if decision_val not in DECISION_STATUSES:
            invalid_by_aid[aid].add(f"invalid decision value={decision_val}")
        if decision_val in {"approved", "rejected", "needs_info"}:
            if not dec.get("reviewer"):
                invalid_by_aid[aid].add(f"{decision_val} decisions require reviewer")
            if not dec.get("reason_code"):
                invalid_by_aid[aid].add(f"{decision_val} decisions require reason_code")
            if not dec.get("approval_note"):
                invalid_by_aid[aid].add(f"{decision_val} decisions require approval_note")
        if dec.get("safe_to_apply") is True:
            if decision_val != "approved":
                invalid_by_aid[aid].add("safe_to_apply=true requires approved decision")
            queue_item = queue.get(aid, {})
            if queue_item.get("safe_to_apply_automatically") is False:
                invalid_by_aid[aid].add("safe_to_apply=true is not allowed when queue safe_to_apply_automatically=false")

    invalid_list = as_list(validation.get("invalid_decisions"))
    existing_invalid = {str(row.get("approval_item_id", "")) for row in invalid_list if isinstance(row, dict)}
    for aid, reasons in sorted(invalid_by_aid.items()):
        extra_errors.extend(sorted(reasons))
        if aid in existing_invalid:
            for row in invalid_list:
                if isinstance(row, dict) and str(row.get("approval_item_id", "")) == aid:
                    row["reasons"] = sorted(set(as_list(row.get("reasons")) + list(reasons)))
        else:
            invalid_list.append({
                "decision_id": next((str(dec.get("decision_id", "")) for dec in decisions if isinstance(dec, dict) and str(dec.get("approval_item_id", "")) == aid), None),
                "approval_item_id": aid,
                "validation_status": "invalid",
                "reasons": sorted(reasons),
            })

    summary = validation.get("summary") if isinstance(validation.get("summary"), dict) else {}
    summary.update({
        "pending_count": sum(1 for d in decisions if isinstance(d, dict) and d.get("decision") == "pending"),
        "duplicate_decision_count": duplicate_count,
        "unknown_approval_item_id_count": unknown_count,
        "safe_to_apply_count": sum(1 for d in decisions if isinstance(d, dict) and d.get("safe_to_apply") is True),
    })
    summary["invalid_decision_count"] = len(invalid_list)
    summary["error_count"] = summary.get("error_count", 0) + len(extra_errors)
    validation["invalid_decisions"] = invalid_list
    validation["summary"] = summary
    validation["errors"] = as_list(validation.get("errors")) + extra_errors
    validation["validation_pass"] = summary["invalid_decision_count"] == 0 and summary.get("missing_decision_count", 0) == 0
    return validation


def list_approval_items(approval_queue_data: dict[str, Any], decisions_data: dict[str, Any]) -> str:
    decisions, duplicates = decision_maps(decisions_data)
    lines = []
    for item in sorted(as_list(approval_queue_data.get("approval_items")), key=lambda row: safe_id(row.get("approval_item_id", "")) if isinstance(row, dict) else ""):
        if not isinstance(item, dict):
            continue
        aid = str(item.get("approval_item_id", ""))
        decision = decisions.get(aid, {})
        lines.extend([
            f"approval_item_id: {aid}",
            f"  decision_id: {decision.get('decision_id')}",
            f"  decision: {decision.get('decision', 'missing')}",
            f"  promotion_candidate_id: {item.get('promotion_candidate_id')}",
            f"  target_summary: {item.get('target_summary')}",
            f"  candidate_summary: {item.get('candidate_summary')}",
            f"  evidence_refs: {clip_text('; '.join(str(ref) for ref in as_list(item.get('evidence_refs'))), 220)}",
            f"  safe_to_apply_automatically: {item.get('safe_to_apply_automatically')}",
            f"  safe_to_apply: {decision.get('safe_to_apply')}",
            f"  status: {item.get('status')}",
            f"  validation_status: {'duplicate' if aid in duplicates else ('present' if decision else 'missing')}",
        ])
    return "\n".join(lines) + ("\n" if lines else "")


def show_approval_item(approval_queue_data: dict[str, Any], decisions_data: dict[str, Any], approval_item_id: str, validation_data: dict[str, Any] | None = None) -> tuple[str, int]:
    queue = queue_map(approval_queue_data)
    if approval_item_id not in queue:
        return f"ERROR: unknown approval_item_id={approval_item_id}\n", 2
    decisions, _ = decision_maps(decisions_data)
    validation_data = validation_data or validate_decisions_phase11(decisions_data, approval_queue_data)
    validation_rows = [
        row for row in as_list(validation_data.get("validated_decisions")) + as_list(validation_data.get("invalid_decisions"))
        if isinstance(row, dict) and str(row.get("approval_item_id", "")) == approval_item_id
    ]
    payload = {
        "approval_queue_item": queue[approval_item_id],
        "decision": decisions.get(approval_item_id),
        "validation": validation_rows,
        "recommendation": {
            "rating_capability_not_branch_current": True,
            "approval_scope": "Approval marks this candidate as review-approved only; it does not mutate core artifacts or resolve branch_current_a.",
            "phase11b_apply_stage_run": False,
        },
    }
    return json.dumps(json_safe(payload), indent=2, sort_keys=True, allow_nan=False) + "\n", 0


def apply_phase11_edit(
    *,
    project: str,
    approval_queue_data: dict[str, Any],
    decisions_data: dict[str, Any],
    approval_item_id: str,
    decision_value: str,
    reviewer: str | None,
    reason_code: str | None,
    approval_note: str | None,
    safe_to_apply: bool,
) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    queue = queue_map(approval_queue_data)
    if approval_item_id not in queue:
        return decisions_data, [f"unknown approval_item_id={approval_item_id}"]
    existing, duplicates = decision_maps(decisions_data)
    if approval_item_id in duplicates:
        return decisions_data, [f"duplicate decision for approval_item_id={approval_item_id}"]
    if decision_value in {"approved", "rejected", "needs_info"}:
        if not reviewer:
            errors.append(f"{decision_value} requires --reviewer")
        if not reason_code:
            errors.append(f"{decision_value} requires --reason-code")
        if not approval_note:
            errors.append(f"{decision_value} requires --approval-note or --note")
    if safe_to_apply and queue[approval_item_id].get("safe_to_apply_automatically") is False:
        errors.append("cannot set safe_to_apply=true because queue item safe_to_apply_automatically=false")
    if errors:
        return decisions_data, errors

    artifact = ensure_decisions_for_queue(project, approval_queue_data, decisions_data)
    for decision in artifact["decisions"]:
        if str(decision.get("approval_item_id", "")) != approval_item_id:
            continue
        decision["decision"] = decision_value
        if decision_value == "pending":
            decision["reviewer"] = None
            decision["reviewed_at_utc"] = None
            decision["approval_note"] = None
            decision["reason_code"] = None
            decision["safe_to_apply"] = False
        else:
            decision["reviewer"] = reviewer
            decision["reviewed_at_utc"] = utc_now()
            decision["approval_note"] = approval_note
            decision["reason_code"] = reason_code
            decision["safe_to_apply"] = bool(safe_to_apply)
        decision["promotion_candidate_id"] = queue[approval_item_id].get("promotion_candidate_id")
        decision["source_queue_item"] = source_queue_item_from_approval_item(queue[approval_item_id])
        break
    artifact["summary"] = compute_decision_summary(artifact["decisions"], [], [])
    return artifact, []


def write_decision_summary(path: Path, validation: dict[str, Any]) -> None:
    summary = validation.get("summary") if isinstance(validation.get("summary"), dict) else {}
    decisions_by_status: dict[str, list[str]] = defaultdict(list)
    for row in as_list(validation.get("validated_decisions")) + as_list(validation.get("invalid_decisions")):
        if isinstance(row, dict):
            decisions_by_status[str(row.get("decision", row.get("validation_status", "unknown")))].append(str(row.get("approval_item_id", "")))
    lines = [
        "AI approval decision summary",
        "review_only=True",
        "no_core_artifacts_modified=True",
        "apply_stage_run=False",
        f"decision_count={summary.get('decision_count', 0)}",
        f"approved_count={summary.get('approved_count', 0)}",
        f"rejected_count={summary.get('rejected_count', 0)}",
        f"needs_info_count={summary.get('needs_info_count', 0)}",
        f"pending_count={summary.get('pending_count', summary.get('pending_decision_count', 0))}",
        f"invalid_decision_count={summary.get('invalid_decision_count', 0)}",
        f"missing_decision_count={summary.get('missing_decision_count', 0)}",
        f"duplicate_decision_count={summary.get('duplicate_decision_count', 0)}",
        f"unknown_approval_item_id_count={summary.get('unknown_approval_item_id_count', 0)}",
        f"safe_to_apply_count={summary.get('safe_to_apply_count', 0)}",
        f"validation_pass={validation.get('validation_pass')}",
        f"approval_ids_by_status={dict(sorted(decisions_by_status.items()))}",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    json_path = path.with_suffix(".json")
    write_json(json_path, {"artifact_type": "ai_approval_decision_summary", "summary": summary, "approval_ids_by_status": dict(decisions_by_status), "validation_pass": validation.get("validation_pass"), "review_only": True})


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="AI Approval Queue Editor v0 — create and validate human approval decision artifacts.",
    )
    parser.add_argument("--project", default=DEFAULT_PROJECT, help="Project name")
    parser.add_argument("--promotion-dir", default=None, help="PR32 ai_promotion output directory")
    parser.add_argument("--approval-queue", default=None, help="Path to ai-candidate-approval-queue.json for Phase 11B path-based mode")
    parser.add_argument("--decisions-in", default=None, help="Path to input ai-approval-decisions.json for Phase 11B path-based mode")
    parser.add_argument("--decisions-out", default=None, help="Path to output decisions artifact for Phase 11B path-based mode")
    parser.add_argument("--list", action="store_true", help="List approval queue items with current decisions")
    parser.add_argument("--show", default=None, metavar="APPROVAL_ITEM_ID", help="Show one approval item with decision and validation context")
    parser.add_argument("--validate", action="store_true", help="Validate decisions in Phase 11B path-based mode")
    parser.add_argument("--summary-out", default=None, help="Write a Phase 11B decision summary text file")
    parser.add_argument("--out", default=None, help="Output path for decisions artifact (default: <promotion-dir>/ai-approval-decisions.json)")
    parser.add_argument("--validate-out", "--validation-out", dest="validate_out", default=None, help="Output path for validation artifact (default: <promotion-dir>/ai-approval-decision-validation.json)")

    # Template mode
    parser.add_argument("--decision-template", action="store_true", help="Create pending decision template for every approval queue item")

    # Edit modes (mutually exclusive)
    edit_group = parser.add_mutually_exclusive_group()
    edit_group.add_argument("--approve", metavar="APPROVAL_ITEM_ID", default=None, help="Approve a single approval item")
    edit_group.add_argument("--reject", metavar="APPROVAL_ITEM_ID", default=None, help="Reject a single approval item")
    edit_group.add_argument("--needs-info", metavar="APPROVAL_ITEM_ID", default=None, help="Mark a single approval item as needs_info")
    edit_group.add_argument("--pending", metavar="APPROVAL_ITEM_ID", default=None, help="Reset a single approval item to pending")

    # Edit options
    parser.add_argument("--note", default=None, help="Human note for the decision (required for approve/reject/needs-info)")
    parser.add_argument("--approval-note", default=None, help="Human approval note/details for Phase 11B edits")
    parser.add_argument("--reason-code", default=None, help="Reason code for rejection or other decisions")
    parser.add_argument("--reviewer", default=None, help="Optional reviewer label")
    parser.add_argument("--safe-to-apply", action="store_true", help="Mark an approved decision safe for a future apply stage; never runs apply")

    # Validation mode
    parser.add_argument("--decisions", default=None, help="Path to existing decisions artifact to validate")
    parser.add_argument("--validate-only", action="store_true", help="Validate without mutating the decision file")
    parser.add_argument("--strict", action="store_true", help="Strict validation: fail if PR32 status is failed")

    # Schema
    parser.add_argument("--schema", default=DEFAULT_SCHEMA, help="Path to JSON schema for validation")

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.approval_queue:
        queue_path = Path(args.approval_queue).resolve()
        if not queue_path.exists():
            print(f"ERROR: missing approval queue: {queue_path}", file=sys.stderr)
            return 2
        try:
            approval_queue_data = load_json(queue_path)
        except (json.JSONDecodeError, ValueError) as exc:
            print(f"ERROR: malformed approval queue: {exc}", file=sys.stderr)
            return 2
        project_name = str(approval_queue_data.get("project") or args.project)

        decisions_in = Path(args.decisions_in).resolve() if args.decisions_in else None
        decisions_data = safe_load(decisions_in) if decisions_in else ensure_decisions_for_queue(project_name, approval_queue_data)
        if decisions_in and not decisions_in.exists():
            print(f"ERROR: decisions-in file does not exist: {decisions_in}", file=sys.stderr)
            return 2

        if args.list:
            print(list_approval_items(approval_queue_data, decisions_data), end="")
            return 0

        if args.show:
            validation_path = queue_path.parent / "ai-approval-decision-validation.json"
            validation_data = safe_load(validation_path)
            text, code = show_approval_item(approval_queue_data, decisions_data, args.show, validation_data if validation_data else None)
            stream = sys.stderr if code else sys.stdout
            print(text, end="", file=stream)
            return code

        action: str | None = None
        approval_item_id: str | None = None
        if args.approve:
            action = "approved"
            approval_item_id = args.approve
        elif args.reject:
            action = "rejected"
            approval_item_id = args.reject
        elif args.needs_info:
            action = "needs_info"
            approval_item_id = args.needs_info
        elif args.pending:
            action = "pending"
            approval_item_id = args.pending

        if action and approval_item_id:
            if not args.decisions_out:
                print("ERROR: Phase 11B edits require --decisions-out", file=sys.stderr)
                return 2
            decisions_out = Path(args.decisions_out).resolve()
            edited, edit_errors = apply_phase11_edit(
                project=project_name,
                approval_queue_data=approval_queue_data,
                decisions_data=decisions_data,
                approval_item_id=approval_item_id,
                decision_value=action,
                reviewer=args.reviewer,
                reason_code=args.reason_code,
                approval_note=args.approval_note or args.note,
                safe_to_apply=args.safe_to_apply,
            )
            if edit_errors:
                for err in edit_errors:
                    print(f"ERROR: {err}", file=sys.stderr)
                return 2
            write_json(decisions_out, edited)
            print(f"ai approval decision edited: project={project_name} item={approval_item_id} decision={action} out={decisions_out}")
            return 0

        if args.validate:
            validation = validate_decisions_phase11(decisions_data, approval_queue_data)
            validation["source_decisions"] = str(decisions_in) if decisions_in else None
            validate_out = Path(args.validate_out).resolve() if args.validate_out else (queue_path.parent / "ai-approval-decision-validation.json").resolve()
            try:
                verify_output_path(validate_out, validate_out.parent, project_name)
            except ValueError as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                return 2
            write_json(validate_out, validation)
            if args.summary_out:
                write_decision_summary(Path(args.summary_out), validation)
                print(f"wrote {args.summary_out}")
            summary = validation.get("summary", {})
            print(
                f"ai approval decision validation: project={project_name} "
                f"approved={summary.get('approved_count', 0)} rejected={summary.get('rejected_count', 0)} "
                f"needs_info={summary.get('needs_info_count', 0)} pending={summary.get('pending_count', summary.get('pending_decision_count', 0))} "
                f"invalid={summary.get('invalid_decision_count', 0)} missing={summary.get('missing_decision_count', 0)} "
                f"duplicates={summary.get('duplicate_decision_count', 0)} unknown={summary.get('unknown_approval_item_id_count', 0)} "
                f"safe_to_apply={summary.get('safe_to_apply_count', 0)} pass={validation['validation_pass']}"
            )
            return 0 if validation["validation_pass"] else 1

        print("ERROR: Phase 11B path mode requires --list, --show, an edit action, or --validate", file=sys.stderr)
        return 2

    if not args.promotion_dir:
        print("ERROR: --promotion-dir is required unless --approval-queue is used", file=sys.stderr)
        return 2

    promotion_dir = Path(args.promotion_dir).resolve()

    # 1. Verify promotion dir exists
    if not promotion_dir.exists():
        print(f"ERROR: promotion directory does not exist: {promotion_dir}", file=sys.stderr)
        return 2

    # 2. Load PR32 artifacts
    queue_path = promotion_dir / "ai-candidate-approval-queue.json"
    plan_path = promotion_dir / "ai-candidate-promotion-plan.json"
    status_path = promotion_dir / "ai-candidate-promotion-status.json"

    if not queue_path.exists():
        print(f"ERROR: missing approval queue: {queue_path}", file=sys.stderr)
        return 2

    try:
        approval_queue_data = load_json(queue_path)
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"ERROR: malformed approval queue: {exc}", file=sys.stderr)
        return 2

    promotion_plan_data = safe_load(plan_path)
    promotion_status_data = safe_load(status_path)

    # 3. Resolve output paths
    decisions_out = Path(args.out).resolve() if args.out else (promotion_dir / "ai-approval-decisions.json").resolve()
    validate_out = Path(args.validate_out).resolve() if args.validate_out else (promotion_dir / "ai-approval-decision-validation.json").resolve()

    # 4. Verify output paths are safe
    try:
        verify_output_path(decisions_out, promotion_dir, args.project)
        verify_output_path(validate_out, promotion_dir, args.project)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    # 5. Branch: --decision-template
    if args.decision_template:
        decisions, summary = build_decision_template(args.project, approval_queue_data)

        source_artifacts = [
            {"artifact_type": "ai_candidate_promotion_plan", "path": str(plan_path), "notes": None},
            {"artifact_type": "ai_candidate_approval_queue", "path": str(queue_path), "notes": None},
        ]

        artifact = {
            "project": args.project,
            "generated_at_utc": utc_now(),
            "schema_version": SCHEMA_VERSION_DECISION,
            "source_artifacts": source_artifacts,
            "source_promotion_plan": str(plan_path),
            "source_approval_queue": str(queue_path),
            "decision_set_status": "draft",
            "decisions": decisions,
            "summary": summary,
            "errors": [],
            "warnings": [],
        }

        write_json(decisions_out, artifact)
        validation_artifact = validate_decisions(
            artifact,
            approval_queue_data,
            promotion_plan_data if promotion_plan_data else None,
            strict=args.strict,
            promotion_status_data=promotion_status_data if promotion_status_data else {},
        )
        validation_artifact["source_decisions"] = str(decisions_out)
        write_json(validate_out, validation_artifact)
        validation_summary = validation_artifact.get("summary", {})
        print(
            f"ai approval decision template: project={args.project} decisions={len(decisions)} out={decisions_out} "
            f"validation_out={validate_out} validation_pass={validation_artifact['validation_pass']} "
            f"invalid={validation_summary.get('invalid_decision_count', 0)} "
            f"missing={validation_summary.get('missing_decision_count', 0)}"
        )
        return 0

    # 6. Branch: edit (--approve / --reject / --needs-info)
    if args.approve or args.reject or args.needs_info:
        action = None
        approval_item_id = None
        if args.approve:
            action = "approve"
            approval_item_id = args.approve
        elif args.reject:
            action = "reject"
            approval_item_id = args.reject
        elif args.needs_info:
            action = "needs_info"
            approval_item_id = args.needs_info

        # Load existing decisions or create from queue
        if Path(args.decisions).exists() if args.decisions else False:
            try:
                decisions_data = load_json(Path(args.decisions))
                decisions = as_list(decisions_data.get("decisions"))
            except (json.JSONDecodeError, ValueError) as exc:
                print(f"ERROR: malformed decisions file: {exc}", file=sys.stderr)
                return 2
        else:
            decisions, _ = build_decision_template(args.project, approval_queue_data)

        decisions, edit_errors, edit_warnings = edit_decision(
            decisions,
            approval_item_id,
            action,
            note=args.note,
            reason_code=args.reason_code,
            reviewer=args.reviewer,
        )

        if edit_errors:
            for err in edit_errors:
                print(f"ERROR: {err}", file=sys.stderr)
            return 2

        summary = compute_decision_summary(decisions, edit_errors, edit_warnings)
        source_artifacts = [
            {"artifact_type": "ai_candidate_promotion_plan", "path": str(plan_path), "notes": None},
            {"artifact_type": "ai_candidate_approval_queue", "path": str(queue_path), "notes": None},
        ]

        artifact = {
            "project": args.project,
            "generated_at_utc": utc_now(),
            "schema_version": SCHEMA_VERSION_DECISION,
            "source_artifacts": source_artifacts,
            "source_promotion_plan": str(plan_path),
            "source_approval_queue": str(queue_path),
            "decision_set_status": "draft",
            "decisions": decisions,
            "summary": summary,
            "errors": edit_errors,
            "warnings": edit_warnings,
        }

        write_json(decisions_out, artifact)
        print(f"ai approval decision edited: project={args.project} item={approval_item_id} action={action} out={decisions_out}")
        return 0

    # 7. Branch: --validate-only or --decisions
    decisions_path = Path(args.decisions).resolve() if args.decisions else decisions_out

    if not decisions_path.exists():
        print(f"ERROR: decisions file does not exist: {decisions_path}", file=sys.stderr)
        return 2

    try:
        decisions_data = load_json(decisions_path)
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"ERROR: malformed decisions file: {exc}", file=sys.stderr)
        return 2

    validation_decisions_path = decisions_path
    if args.out and decisions_out != decisions_path:
        write_json(decisions_out, decisions_data)
        validation_decisions_path = decisions_out

    validation_artifact = validate_decisions(
        decisions_data,
        approval_queue_data,
        promotion_plan_data if promotion_plan_data else None,
        strict=args.strict,
        promotion_status_data=promotion_status_data if promotion_status_data else {},
    )
    validation_artifact["source_decisions"] = str(validation_decisions_path)

    write_json(validate_out, validation_artifact)
    summary = validation_artifact.get("summary", {})
    print(
        f"ai approval decision validation: project={args.project} "
        f"valid={summary.get('valid_decision_count', 0)} invalid={summary.get('invalid_decision_count', 0)} "
        f"missing={summary.get('missing_decision_count', 0)} pass={validation_artifact['validation_pass']} out={validate_out}"
    )
    return 0 if validation_artifact["validation_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
