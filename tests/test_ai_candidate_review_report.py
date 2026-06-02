from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ai_candidate_review_report.py"


def write_json(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    return path


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def run_report(run_dir: Path, out_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--run-dir", str(run_dir), "--out-dir", str(out_dir)],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )


def accepted_item(**overrides: Any) -> dict[str, Any]:
    row = {
        "accepted_item_id": "accepted_12B-001_U2_max",
        "basis": "datasheet_supply_current",
        "confidence": 0.78,
        "evidence_quote": "Supply current ICC max 5 mA.",
        "field_name": "max_current_a",
        "human_review_needed": False,
        "missing_data_item_ids": ["mdi_u2_branch_current"],
        "normalized_unit": "A",
        "normalized_value": 0.005,
        "packet_id": "12B-001",
        "source_file": "datasheets/U2.pdf",
        "source_item_id": "12B-001:component_current_model:max_current_a:U2:000001",
        "source_page": 7,
        "target_mpn": "PCA9515A",
        "target_refdes": "U2",
        "target_type": "component_current_model",
        "unit": "A",
        "value": 0.005,
    }
    row.update(overrides)
    return row


def human_review_item(**overrides: Any) -> dict[str, Any]:
    candidate = {
        "basis": "datasheet_rating_evidence",
        "confidence": 0.7,
        "evidence_quote": "Current rating 3 A AC/DC.",
        "field_name": "current_max",
        "human_review_needed": True,
        "missing_data_item_ids": ["mdi_j1_branch_current"],
        "normalized_unit": "A",
        "normalized_value": 3.0,
        "packet_id": "12B-002",
        "source_file": "datasheets/J1.pdf",
        "source_item_id": "12B-002:connector_rating:current_max:J1:000001",
        "source_page": 2,
        "target_mpn": "S2B-XH-A",
        "target_refdes": "J1",
        "target_type": "connector_rating",
        "unit": "A",
        "value": 3.0,
    }
    row = {
        "candidate_item": candidate,
        "detail": "confidence is below acceptance threshold",
        "human_review_item_id": "review_12B-002_J1_current",
        "packet_id": "12B-002",
        "reason_code": "medium_confidence",
        "source_item_id": "12B-002:connector_rating:current_max:J1:000001",
    }
    row.update(overrides)
    return row


def rating_record(**overrides: Any) -> dict[str, Any]:
    row = {
        "rating_id": "rating_connector_j1_unknown_current_max_000001",
        "source_record_id": "cur_rating_connector_j1_unknown_000001",
        "target_type": "connector",
        "normalized_target_type": "connector",
        "refdes": "J1",
        "rating_name": "current_max",
        "normalized_rating_name": "current_max",
        "value_a": 3.0,
        "unit": "A",
        "basis": "ai_validated_datasheet",
        "confidence": 0.7,
        "evidence_refs": ["datasheets/J1.pdf:2:Current rating 3 A AC/DC."],
        "human_review_needed": True,
        "missing_data_manifest_item_ids": ["mdi_j1_branch_current"],
    }
    row.update(overrides)
    return row


def promotion_candidate(**overrides: Any) -> dict[str, Any]:
    row = {
        "promotion_candidate_id": "promo_rating_j1",
        "candidate_kind": "rating_model",
        "operation": "add_candidate",
        "promotion_status": "pending_human_approval",
        "safe_to_apply_automatically": False,
        "source_candidate_record_id": "rating_connector_j1_unknown_current_max_000001",
        "source_ai_packet_id": "12B-002",
        "source_ai_accepted_item_id": "review_12B-002_J1_current",
        "target_identity": {"target_type": "connector", "refdes": "J1", "field_name": "current_max"},
        "candidate_value": {"value": 3.0, "unit": "A", "normalized_value": 3.0, "normalized_unit": "A"},
        "basis": "ai_validated_datasheet",
        "confidence": 0.7,
        "evidence_refs": ["datasheets/J1.pdf:2:Current rating 3 A AC/DC."],
        "missing_data_item_ids": ["mdi_j1_branch_current"],
    }
    row.update(overrides)
    return row


def approval_item(**overrides: Any) -> dict[str, Any]:
    row = {
        "approval_item_id": "approve_j1",
        "promotion_candidate_id": "promo_rating_j1",
        "review_type": "approve_add",
        "priority": "medium",
        "reason_code": "candidate_not_in_core",
        "target_summary": "target_type=connector, refdes=J1, field_name=current_max",
        "candidate_summary": "3.0 A",
        "core_summary": "no_core_match",
        "evidence_refs": ["datasheets/J1.pdf:2:Current rating 3 A AC/DC."],
        "recommended_action": "review_only",
        "approval_required": True,
        "safe_to_apply_automatically": False,
        "status": "pending",
    }
    row.update(overrides)
    return row


def write_run_dir(path: Path, *, include_pr37: bool = True, missing_evidence: bool = False, branch_current: bool = False) -> Path:
    acc = accepted_item()
    human = human_review_item()
    if missing_evidence:
        human["candidate_item"].pop("evidence_quote", None)
        human["candidate_item"].pop("source_file", None)
    if branch_current:
        acc.update({"target_type": "component_current_model", "field_name": "branch_current_a", "source_item_id": "branch_current_a"})
    write_json(
        path / "phase-driver-status.json",
        {
            "project": "TestProject",
            "workflow": "topology_ai",
            "overall_status": "passed",
            "blocker_count": 0,
            "wrote_core_artifacts": False,
            "ran_post_promotion_allocation": False,
            "ran_post_promotion_calculations": False,
            "safe_for_core_apply": False,
            "ready_for_core_apply": False,
        },
    )
    write_json(
        path / "pr27_ai_extraction_validate" / "ai-extraction-validation.json",
        {
            "project": "TestProject",
            "schema_version": "ai_extraction_validation_v1",
            "accepted_items": [acc],
            "human_review_items": [human],
            "rejected_items": [],
            "errors": [],
            "warnings": [],
            "packet_results": [{"packet_id": "12B-001", "status": "accepted"}, {"packet_id": "12B-002", "status": "human_review_needed"}],
            "summary": {
                "packet_count": 2,
                "accepted_packet_count": 1,
                "human_review_packet_count": 1,
                "accepted_item_count": 1,
                "human_review_item_count": 1,
                "rejected_item_count": 0,
                "error_count": 0,
                "warning_count": 0,
            },
        },
    )
    write_json(path / "pr31_ai_candidate_ingest" / "ai-candidate-ingestion-review.json", {"project": "TestProject", "summary": {}})
    write_json(path / "pr31_ai_candidate_ingest" / "ai-candidate-ingestion-status.json", {"project": "TestProject", "status": "completed"})
    write_json(path / "pr31_ai_candidate_ingest" / "ai-current-models-normalized.json", {"project": "TestProject", "normalized_currents": [], "summary": {}})
    write_json(path / "pr31_ai_candidate_ingest" / "ai-rating-models-normalized.json", {"project": "TestProject", "normalized_ratings": [rating_record()], "summary": {"normalized_rating_count": 1}})
    write_json(path / "pr31_ai_candidate_ingest" / "ai-rating-current-models-normalized.json", {"project": "TestProject", "normalized_currents": [rating_record(record_id="cur_rating_connector_j1_unknown_000001")], "summary": {"normalized_count": 1}})
    write_json(path / "pr31_ai_candidate_ingest" / "ai-human-review-index.json", {"project": "TestProject", "human_review_records": [], "workflow_review_records": [], "summary": {}})
    write_json(path / "pr32_ai_promotion_plan" / "ai-candidate-approval-queue.json", {"project": "TestProject", "approval_items": [approval_item()], "summary": {"approval_queue_count": 1}})
    write_json(path / "pr32_ai_promotion_plan" / "ai-candidate-promotion-plan.json", {"project": "TestProject", "promotion_candidates": [promotion_candidate()], "blocked_candidates": [], "summary": {"promotion_candidate_count": 1}})
    write_json(path / "pr32_ai_promotion_plan" / "ai-candidate-promotion-status.json", {"project": "TestProject", "safe_to_apply_automatically": False, "safe_to_overwrite_core_artifacts": False})
    write_json(path / "pr32_ai_promotion_plan" / "ai-human-review-promotion-index.json", {"project": "TestProject", "approval_item_ids": ["approve_j1"], "human_review_records": [], "blocked_candidates": [], "conflicts": [], "summary": {}})
    write_json(path / "pr32_ai_promotion_plan" / "ai-approval-decisions.json", {"project": "TestProject", "decisions": [], "summary": {}})
    write_json(path / "pr32_ai_promotion_plan" / "ai-approval-decision-validation.json", {"project": "TestProject", "validated_decisions": [], "summary": {}})
    if include_pr37:
        write_json(path / "pr37_ai_candidate_normalized_review" / "ai-candidate-normalized-approval-readiness.json", {"project": "TestProject", "review_ready_items": [{"record_id": "ready_1", "target_type": "connector", "refdes": "J1", "field_name": "current_max", "value": 3.0, "unit": "A", "evidence_refs": ["datasheets/J1.pdf:2"]}], "blocked_items": [], "summary": {}})
        write_json(path / "pr37_ai_candidate_normalized_review" / "ai-candidate-normalized-promotion-review.json", {"project": "TestProject", "current_model_review_items": [], "rating_model_review_items": [{"record_id": "rating_review_1", "target_type": "connector", "refdes": "J1", "rating_name": "current_max", "value_a": 3.0, "unit": "A", "evidence_refs": ["datasheets/J1.pdf:2"]}], "blocked_items": [], "summary": {}})
    return path


def test_synthetic_pr27_counts_and_markdown(tmp_path: Path) -> None:
    run_dir = write_run_dir(tmp_path / "run")
    out_dir = tmp_path / "out"
    result = run_report(run_dir, out_dir)
    assert result.returncode == 0, result.stderr
    report = read_json(out_dir / "ai-candidate-review-report.json")
    assert report["summary"]["pr27_packet_count"] == 2
    assert report["summary"]["accepted_item_count"] == 1
    assert report["summary"]["human_review_item_count"] == 1
    assert report["summary"]["rejected_item_count"] == 0
    assert (out_dir / "ai-candidate-review-report.md").exists()
    assert "Do not apply yet" in (out_dir / "ai-candidate-review-report.md").read_text(encoding="utf-8")


def test_pr31_normalized_current_and_rating_candidates_are_grouped(tmp_path: Path) -> None:
    run_dir = write_run_dir(tmp_path / "run")
    result = run_report(run_dir, tmp_path / "out")
    assert result.returncode == 0, result.stderr
    groups = read_json(tmp_path / "out" / "ai-candidate-review-report.json")["candidate_groups"]
    assert groups["rating_model_candidates"]
    assert groups["rating_current_model_candidates"]


def test_pr32_approval_queue_rows_are_included(tmp_path: Path) -> None:
    run_dir = write_run_dir(tmp_path / "run")
    assert run_report(run_dir, tmp_path / "out").returncode == 0
    rows = read_json(tmp_path / "out" / "ai-candidate-review-report.json")["candidate_groups"]["human_review_candidates"]
    assert any(row.get("approval_item_id") == "approve_j1" for row in rows)


def test_pr37_normalized_review_rows_are_included_when_present(tmp_path: Path) -> None:
    run_dir = write_run_dir(tmp_path / "run", include_pr37=True)
    assert run_report(run_dir, tmp_path / "out").returncode == 0
    rows = read_json(tmp_path / "out" / "ai-candidate-review-report.json")["candidate_groups"]["normalized_review_candidates"]
    assert rows


def test_rating_candidates_are_flagged_not_branch_current(tmp_path: Path) -> None:
    run_dir = write_run_dir(tmp_path / "run")
    assert run_report(run_dir, tmp_path / "out").returncode == 0
    rows = read_json(tmp_path / "out" / "ai-candidate-review-report.json")["candidate_groups"]["human_review_candidates"]
    rating_rows = [row for row in rows if row.get("target_type") in {"connector", "connector_rating"}]
    assert rating_rows
    assert any("not_branch_operating_current" in row["review_risk_flags"] for row in rating_rows)


def test_branch_current_is_not_auto_approved_from_datasheet_rating(tmp_path: Path) -> None:
    run_dir = write_run_dir(tmp_path / "run", branch_current=True)
    assert run_report(run_dir, tmp_path / "out").returncode == 0
    rows = read_json(tmp_path / "out" / "ai-candidate-review-report.json")["candidate_groups"]["accepted_candidates"]
    branch_rows = [row for row in rows if row.get("field_name") == "branch_current_a"]
    assert branch_rows
    assert branch_rows[0]["recommended_action"] == "needs_info"
    assert "must_not_auto_approve_branch_current" in branch_rows[0]["review_risk_flags"]


def test_missing_evidence_creates_needs_info(tmp_path: Path) -> None:
    run_dir = write_run_dir(tmp_path / "run", missing_evidence=True)
    assert run_report(run_dir, tmp_path / "out").returncode == 0
    rows = read_json(tmp_path / "out" / "ai-candidate-review-report.json")["candidate_groups"]["human_review_candidates"]
    missing_rows = [row for row in rows if row.get("candidate_record_id") == "review_12B-002_J1_current"]
    assert missing_rows
    assert missing_rows[0]["recommended_action"] == "needs_info"


def test_script_fails_clearly_when_required_pr27_artifact_is_missing(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    result = run_report(run_dir, tmp_path / "out")
    assert result.returncode == 2
    assert "missing required Phase 11A source artifact" in result.stderr
