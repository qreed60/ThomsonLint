from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import phase_driver  # noqa: E402


FIXTURE_DIR = ROOT / "tests" / "fixtures" / "topology_ai_rating_non_empty"
RESPONSES_DIR = FIXTURE_DIR / "responses"
APPROVAL_DECISIONS = FIXTURE_DIR / "approval-decisions.json"
SAFETY_SCRIPTS = [
    ROOT / "scripts" / "phase_driver.py",
    ROOT / "scripts" / "topology_ai_phase_registry.py",
    ROOT / "scripts" / "ai_extraction_validate.py",
    ROOT / "scripts" / "ai_patch_build.py",
    ROOT / "scripts" / "ai_candidate_materialize.py",
    ROOT / "scripts" / "ai_candidate_adapter_build.py",
    ROOT / "scripts" / "ai_candidate_ingest_workflow.py",
    ROOT / "scripts" / "ai_candidate_promotion_plan.py",
    ROOT / "scripts" / "ai_approval_decision_edit.py",
    ROOT / "scripts" / "ai_promotion_apply_dry_run.py",
    ROOT / "scripts" / "ai_candidate_core_input_apply.py",
    ROOT / "scripts" / "ai_candidate_core_input_ingest_workflow.py",
    ROOT / "scripts" / "ai_candidate_normalized_promotion_review.py",
]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def run_topology_ai(tmp_path: Path, *args: str) -> Path:
    out_dir = tmp_path / f"run_{len(list(tmp_path.glob('run_*'))):02d}"
    result = phase_driver.main([
        "TestProject",
        "--workflow",
        "topology_ai",
        "--start",
        "pre01",
        "--end",
        "pr37",
        "--out-dir",
        str(out_dir),
        "--allow-existing-outputs",
        *args,
    ])
    assert result == 0
    return out_dir


def stage(out_dir: Path, phase_id: str) -> dict[str, Any]:
    rows = read_json(out_dir / "phase-driver-stage-results.json")["stage_results"]
    return next(row for row in rows if row["phase_id"] == phase_id)


def test_rating_fixture_flows_to_pending_approval_without_auto_approval(tmp_path: Path) -> None:
    out_dir = run_topology_ai(tmp_path, "--responses-dir", str(RESPONSES_DIR))

    validation = read_json(out_dir / "pr27_ai_extraction_validate" / "ai-extraction-validation.json")
    patch_bundle = read_json(out_dir / "pr28_ai_patch_build" / "ai-patch-bundle.json")
    candidates = read_json(out_dir / "pr29_ai_candidate_materialize" / "ai-candidate-inputs.json")
    adapter = read_json(out_dir / "pr30_ai_candidate_adapter_outputs" / "ai-adapter-manifest.json")
    ingest = read_json(out_dir / "pr31_ai_candidate_ingest" / "ai-candidate-ingestion-manifest.json")
    ratings = read_json(out_dir / "pr31_ai_candidate_ingest" / "ai-rating-models-normalized.json")
    queue = read_json(out_dir / "pr32_ai_promotion_plan" / "ai-candidate-approval-queue.json")
    decisions = read_json(out_dir / "pr32_ai_promotion_plan" / "ai-approval-decisions.json")
    dry_run = read_json(out_dir / "pr34_ai_promotion_apply_dry_run" / "ai-approved-promotion-apply-dry-run.json")
    manifest = read_json(out_dir / "phase-driver-manifest.json")

    assert validation["summary"]["accepted_item_count"] == 1
    assert validation["summary"]["rating_item_count"] == 1
    assert patch_bundle["summary"]["rating_model_patch_count"] == 1
    assert candidates["summary"]["rating_model_candidate_count"] == 1
    assert adapter["summary"]["rating_adapter_record_count"] == 1
    assert ingest["summary"]["rating_adapter_record_count"] == 1
    assert ingest["summary"]["rating_normalized_record_count"] == 1
    assert ratings["summary"]["fuse_rating_count"] == 1
    assert ratings["summary"]["connector_pin_rating_count"] == 0
    assert ratings["summary"]["regulator_rating_count"] == 0
    assert ratings["normalized_ratings"][0]["normalized_target_type"] == "fuse"
    assert ratings["normalized_ratings"][0]["normalized_rating_name"] == "current_max"
    assert "connector pin rating is not expanded" not in json.dumps(ratings)
    assert queue["summary"]["approval_queue_count"] == 1
    assert decisions["summary"]["pending_count"] == 1
    assert decisions["summary"]["approved_count"] == 0
    assert all(decision["safe_to_apply"] is False for decision in decisions["decisions"])
    assert dry_run["summary"]["dry_run_operation_count"] == 0
    assert dry_run["summary"]["rating_model_operation_count"] == 0
    assert dry_run["summary"]["wrote_core_artifacts"] is False
    assert manifest["model_routing_summary"]["qwen_vision_invoked"] is False


def test_rating_explicit_approval_drives_pr34_preview_without_core_writes(tmp_path: Path) -> None:
    out_dir = run_topology_ai(
        tmp_path,
        "--responses-dir",
        str(RESPONSES_DIR),
        "--approval-decisions",
        str(APPROVAL_DECISIONS),
    )

    decisions = read_json(out_dir / "pr32_ai_promotion_plan" / "ai-approval-decisions.json")
    decision_validation = read_json(out_dir / "pr32_ai_promotion_plan" / "ai-approval-decision-validation.json")
    dry_run = read_json(out_dir / "pr34_ai_promotion_apply_dry_run" / "ai-approved-promotion-apply-dry-run.json")
    pr35 = read_json(out_dir / "pr35_ai_candidate_core_input_apply" / "ai-candidate-core-input-apply-manifest.json")
    pr36 = read_json(out_dir / "pr36_ai_candidate_core_input_ingest" / "ai-candidate-core-input-ingest-manifest.json")
    pr37 = read_json(out_dir / "pr37_ai_candidate_normalized_review" / "ai-candidate-normalized-promotion-review.json")
    manifest = read_json(out_dir / "phase-driver-manifest.json")

    assert stage(out_dir, "pr33_ai_approval_decisions")["status"] == "passed"
    assert stage(out_dir, "pr34_ai_promotion_apply_dry_run")["status"] == "passed"
    assert stage(out_dir, "pr35_ai_candidate_core_input_apply")["status"] == "passed"
    assert stage(out_dir, "pr36_ai_candidate_core_input_ingest")["status"] == "passed"
    assert stage(out_dir, "pr37_ai_candidate_normalized_review")["status"] == "passed"
    assert decisions["summary"]["approved_count"] == 1
    assert decisions["summary"]["safe_to_apply_count"] == 0
    assert decision_validation["summary"]["valid_decision_count"] == 1
    assert decision_validation["summary"]["safe_for_future_apply_count"] == 0
    assert dry_run["summary"]["dry_run_operation_count"] == 1
    assert dry_run["summary"]["rating_model_operation_count"] == 1
    assert dry_run["summary"]["would_add_count"] == 1
    assert dry_run["summary"]["wrote_core_artifacts"] is False
    assert dry_run["summary"]["merged_addenda"] is False
    assert pr35["summary"]["blocked_operation_count"] == 1
    assert pr35["summary"]["candidate_apply_operation_count"] == 0
    assert pr35["summary"]["wrote_core_artifacts"] is False
    assert pr35["summary"]["safe_for_core_apply"] is False
    assert pr36["summary"]["wrote_core_artifacts"] is False
    assert pr36["summary"]["safe_for_core_apply"] is False
    assert pr37["summary"]["wrote_core_artifacts"] is False
    assert pr37["summary"]["safe_for_core_apply"] is False
    assert manifest["safe_for_core_apply"] is False
    assert manifest["ready_for_core_apply"] is False
    assert manifest["model_routing_summary"]["qwen_vision_invoked"] is False


def test_rating_fixture_safety_guards_do_not_add_ai_network_or_shell_shortcuts() -> None:
    banned_import = re.compile(r"^\s*(?:import|from)\s+(requests|httpx|aiohttp|openai|litellm)\b", re.MULTILINE)
    for path in SAFETY_SCRIPTS:
        text = path.read_text(encoding="utf-8")
        assert "shell=True" not in text
        assert banned_import.search(text) is None


def test_default_no_response_behavior_still_blocks_at_pr27_for_rating_fixture_tests(tmp_path: Path) -> None:
    out_dir = run_topology_ai(tmp_path)
    pr27 = stage(out_dir, "pr27_ai_extraction_validate")
    pr28 = stage(out_dir, "pr28_ai_patch_build")

    assert pr27["status"] == "blocked_missing_input"
    assert "will not fabricate" in pr27["reason"]
    assert pr28["status"] == "skipped"
