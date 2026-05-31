from __future__ import annotations

import json
import math
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import jsonschema


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ai_candidate_core_input_ingest_workflow.py"
SCHEMA = ROOT / "schemas" / "ai_candidate_core_input_ingest_schema.json"
DOC = ROOT / "docs" / "ai_candidate_core_input_ingest_workflow.md"
CURRENT_SCRIPT = ROOT / "scripts" / "current_model_ingest.py"
RATING_SCRIPT = ROOT / "scripts" / "rating_model_ingest.py"
OUTPUT_NAMES = {
    "ai-candidate-core-input-ingest-manifest.json",
    "ai-candidate-core-input-ingest-status.json",
    "ai-candidate-current-model-ingest-input.json",
    "ai-candidate-rating-current-model-ingest-input.json",
    "ai-candidate-current-models-normalized.json",
    "ai-candidate-rating-current-models-normalized.json",
    "ai-candidate-rating-models-normalized.json",
    "ai-candidate-addenda-ingest-index.json",
    "ai-candidate-core-input-ingest-review.json",
    "ai-candidate-core-input-ingest-blockers.json",
}
FORBIDDEN_KEYS = {
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


def write_json(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8")
    return path


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def run_workflow(tmp_path: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--project",
            "TestProject",
            "--candidate-input-dir",
            str(tmp_path / "exports" / "TestProject" / "ai_candidate_core_inputs"),
            "--out-dir",
            str(tmp_path / "exports" / "TestProject" / "ai_candidate_core_ingested"),
            *extra,
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )


def out_dir(tmp_path: Path) -> Path:
    return tmp_path / "exports" / "TestProject" / "ai_candidate_core_ingested"


def output(tmp_path: Path, name: str) -> dict[str, Any]:
    return read_json(out_dir(tmp_path) / name)


def all_values(value: Any) -> list[Any]:
    values = [value]
    if isinstance(value, dict):
        for child in value.values():
            values.extend(all_values(child))
    elif isinstance(value, list):
        for child in value:
            values.extend(all_values(child))
    return values


def all_keys(value: Any) -> list[str]:
    keys: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            keys.append(str(key))
            keys.extend(all_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.extend(all_keys(child))
    return keys


def current_record() -> dict[str, Any]:
    return {
        "candidate_record_id": "candidate_current_001",
        "candidate_input": True,
        "source_pr34_operation_id": "op_current",
        "source_promotion_candidate_id": "pc_current",
        "source_approval_item_id": "aq_current",
        "source_decision_id": "decision_current",
        "target_identity": {"refdes": "U2", "rail_name": "V3P3"},
        "candidate_value": {"current_a": 0.085, "unit": "A", "condition": "max"},
        "evidence_refs": ["datasheets/U2.pdf:92"],
    }


def rating_record() -> dict[str, Any]:
    return {
        "candidate_record_id": "candidate_rating_001",
        "candidate_input": True,
        "source_pr34_operation_id": "op_rating",
        "source_promotion_candidate_id": "pc_rating",
        "source_approval_item_id": "aq_rating",
        "source_decision_id": "decision_rating",
        "target_identity": {"connector_ref": "J1"},
        "candidate_value": {"rating_a": 3.0, "unit": "A", "condition": "connector_wide"},
        "evidence_refs": ["datasheets/J1.pdf:14"],
    }


def fixtures(tmp_path: Path, *, status: str = "candidate_apply_pass", current_records: list[dict[str, Any]] | None = None, rating_records: list[dict[str, Any]] | None = None) -> Path:
    input_dir = tmp_path / "exports" / "TestProject" / "ai_candidate_core_inputs"
    current_records = [current_record()] if current_records is None else current_records
    rating_records = [rating_record()] if rating_records is None else rating_records
    write_json(input_dir / "ai-candidate-core-input-apply-manifest.json", {"project": "TestProject", "schema_version": "ai_candidate_core_input_apply_v1", "summary": {"candidate_current_record_count": len(current_records), "candidate_rating_record_count": len(rating_records)}, "errors": [], "warnings": []})
    write_json(input_dir / "ai-candidate-core-input-apply-status.json", {"project": "TestProject", "schema_version": "ai_candidate_core_input_apply_v1", "status": status, "candidate_apply_only": True, "errors": [], "warnings": []})
    write_json(input_dir / "ai-candidate-current-model-input.json", {"project": "TestProject", "schema_version": "ai_candidate_core_input_apply_v1", "candidate_apply_only": True, "current_model_inputs": current_records, "summary": {}, "errors": [], "warnings": []})
    write_json(input_dir / "ai-candidate-rating-model-input.json", {"project": "TestProject", "schema_version": "ai_candidate_core_input_apply_v1", "candidate_apply_only": True, "rating_model_inputs": rating_records, "summary": {}, "errors": [], "warnings": []})
    write_json(input_dir / "ai-candidate-role-addenda.json", {"project": "TestProject", "role_addenda": [{"candidate_record_id": "role_1"}], "safe_to_merge_automatically": False, "merged_addenda": False})
    write_json(input_dir / "ai-candidate-pin-role-addenda.json", {"project": "TestProject", "pin_role_addenda": [{"candidate_record_id": "pin_1"}], "safe_to_merge_automatically": False, "merged_addenda": False})
    write_json(input_dir / "ai-candidate-rail-relationship-hints.json", {"project": "TestProject", "rail_relationship_hints": [{"candidate_record_id": "rail_1"}], "safe_to_merge_automatically": False, "merged_addenda": False})
    write_json(input_dir / "ai-candidate-passive-support-inputs.json", {"project": "TestProject", "passive_support_inputs": [{"candidate_record_id": "passive_1"}], "safe_to_merge_automatically": False, "merged_addenda": False})
    write_json(tmp_path / "exports" / "TestProject" / "ai_promotion" / "ai-candidate-promotion-plan.json", {"source": "pr32"})
    write_json(tmp_path / "exports" / "TestProject" / "ai_promotion" / "ai-approval-decisions.json", {"source": "pr33"})
    write_json(tmp_path / "exports" / "TestProject" / "ai_promotion_apply_dry_run" / "ai-approved-promotion-apply-dry-run.json", {"source": "pr34"})
    return input_dir


def create_repo_script(name: str, content: str) -> Path:
    path = ROOT / name
    path.write_text(content, encoding="utf-8")
    return path


def test_missing_and_malformed_inputs_exit_2(tmp_path: Path) -> None:
    assert run_workflow(tmp_path).returncode == 2
    input_dir = tmp_path / "exports" / "TestProject" / "ai_candidate_core_inputs"
    input_dir.mkdir(parents=True)
    assert run_workflow(tmp_path).returncode == 2
    write_json(input_dir / "ai-candidate-core-input-apply-manifest.json", {})
    assert run_workflow(tmp_path).returncode == 2
    (input_dir / "ai-candidate-core-input-apply-manifest.json").write_text("{bad", encoding="utf-8")
    write_json(input_dir / "ai-candidate-core-input-apply-status.json", {})
    assert run_workflow(tmp_path).returncode == 2
    write_json(input_dir / "ai-candidate-core-input-apply-manifest.json", {})
    (input_dir / "ai-candidate-core-input-apply-status.json").write_text("{bad", encoding="utf-8")
    assert run_workflow(tmp_path).returncode == 2


def test_failed_pr35_status_blocks_in_strict_mode(tmp_path: Path) -> None:
    fixtures(tmp_path, status="candidate_apply_failed")
    result = run_workflow(tmp_path, "--strict")
    assert result.returncode == 1
    blockers = output(tmp_path, "ai-candidate-core-input-ingest-blockers.json")["blocker_records"]
    assert any(row["reason_code"] == "candidate_input_status_failed" for row in blockers)


def test_output_shape_status_json_safety_and_schema(tmp_path: Path) -> None:
    fixtures(tmp_path)
    result = run_workflow(tmp_path)
    assert result.returncode == 0, result.stderr + result.stdout
    assert {path.name for path in out_dir(tmp_path).iterdir()} == OUTPUT_NAMES
    schema = read_json(SCHEMA)
    for name in ["ai-candidate-core-input-ingest-manifest.json", "ai-candidate-core-input-ingest-status.json", "ai-candidate-addenda-ingest-index.json", "ai-candidate-core-input-ingest-review.json", "ai-candidate-core-input-ingest-blockers.json"]:
        artifact = output(tmp_path, name)
        jsonschema.validate(instance=artifact, schema=schema)
        assert not any(isinstance(value, float) and not math.isfinite(value) for value in all_values(artifact))
    manifest = output(tmp_path, "ai-candidate-core-input-ingest-manifest.json")
    status = output(tmp_path, "ai-candidate-core-input-ingest-status.json")
    assert {"project", "generated_at_utc", "schema_version", "source_artifacts", "candidate_outputs", "steps", "summary", "errors", "warnings"}.issubset(manifest)
    assert {"project", "status", "candidate_ingest_only", "wrote_candidate_normalized_outputs", "wrote_core_artifacts", "requires_future_core_apply_stage"}.issubset(status)


def test_ingestion_steps_run_into_isolated_candidate_outputs(tmp_path: Path) -> None:
    fixtures(tmp_path)
    assert run_workflow(tmp_path).returncode == 0
    assert (out_dir(tmp_path) / "ai-candidate-current-models-normalized.json").exists()
    assert (out_dir(tmp_path) / "ai-candidate-rating-current-models-normalized.json").exists()
    assert (out_dir(tmp_path) / "ai-candidate-rating-models-normalized.json").exists()
    assert (out_dir(tmp_path) / "ai-candidate-current-model-ingest-input.json").exists()
    assert (out_dir(tmp_path) / "ai-candidate-rating-current-model-ingest-input.json").exists()
    assert read_json(out_dir(tmp_path) / "ai-candidate-current-models-normalized.json")["normalized_currents"]
    assert read_json(out_dir(tmp_path) / "ai-candidate-rating-current-models-normalized.json")["normalized_currents"]
    assert read_json(out_dir(tmp_path) / "ai-candidate-rating-models-normalized.json")["normalized_ratings"]
    steps = {step["step_id"]: step for step in output(tmp_path, "ai-candidate-core-input-ingest-manifest.json")["steps"]}
    assert steps["current_candidate_ingest"]["status"] == "passed"
    assert steps["rating_current_candidate_ingest"]["status"] == "passed"
    assert steps["rating_model_candidate_ingest"]["status"] == "passed"
    for step in steps.values():
        assert isinstance(step["arguments"], list)
        assert step["script"]
        assert step["input_path"]
        assert step["output_path"]
        assert "return_code" in step
        assert "stdout_preview" in step
        assert "stderr_preview" in step
    manifest = output(tmp_path, "ai-candidate-core-input-ingest-manifest.json")
    assert manifest["summary"]["current_ingest_input_record_count"] == 1
    assert manifest["summary"]["rating_current_ingest_input_record_count"] == 1
    for path in manifest["intermediate_outputs"].values():
        Path(path).resolve().relative_to(out_dir(tmp_path).resolve())


def test_valid_candidate_records_are_not_silently_dropped(tmp_path: Path) -> None:
    fixtures(tmp_path)
    assert run_workflow(tmp_path).returncode == 0
    review = output(tmp_path, "ai-candidate-core-input-ingest-review.json")
    blockers = output(tmp_path, "ai-candidate-core-input-ingest-blockers.json")["blocker_records"]
    assert output(tmp_path, "ai-candidate-core-input-ingest-manifest.json")["summary"]["current_candidate_normalized_record_count"] > 0
    assert output(tmp_path, "ai-candidate-core-input-ingest-manifest.json")["summary"]["rating_candidate_normalized_record_count"] > 0
    assert not any(row["reason_code"] in {"candidate_current_adapter_empty", "candidate_rating_adapter_empty"} for row in blockers)
    assert all("empty" not in gap["detail"] for gap in review["provenance_gaps"])


def test_skip_flags_skip_expected_steps(tmp_path: Path) -> None:
    fixtures(tmp_path)
    assert run_workflow(tmp_path, "--skip-current").returncode == 0
    steps = {step["step_id"]: step for step in output(tmp_path, "ai-candidate-core-input-ingest-manifest.json")["steps"]}
    assert steps["current_candidate_ingest"]["status"] == "skipped"
    assert run_workflow(tmp_path, "--skip-rating").returncode == 0
    steps = {step["step_id"]: step for step in output(tmp_path, "ai-candidate-core-input-ingest-manifest.json")["steps"]}
    assert steps["rating_current_candidate_ingest"]["status"] == "skipped"
    assert steps["rating_model_candidate_ingest"]["status"] == "skipped"


def test_failed_steps_record_failure_and_dependency_skip(tmp_path: Path) -> None:
    fixtures(tmp_path)
    failing = create_repo_script("_tmp_pr36_fail.py", "import sys\nprint('x' * 5000)\nprint('y' * 5000, file=sys.stderr)\nsys.exit(3)\n")
    try:
        result = run_workflow(tmp_path, "--current-model-ingest-script", str(failing), "--skip-rating")
        assert result.returncode == 1
        step = output(tmp_path, "ai-candidate-core-input-ingest-manifest.json")["steps"][0]
        assert step["status"] == "failed"
        assert "[truncated]" in step["stdout_preview"]
        result = run_workflow(tmp_path, "--current-model-ingest-script", str(failing), "--skip-current")
        assert result.returncode == 1
        steps = {row["step_id"]: row for row in output(tmp_path, "ai-candidate-core-input-ingest-manifest.json")["steps"]}
        assert steps["rating_current_candidate_ingest"]["status"] == "failed"
        assert steps["rating_model_candidate_ingest"]["status"] == "skipped"
    finally:
        failing.unlink(missing_ok=True)


def test_script_override_must_exist_and_stay_inside_repo(tmp_path: Path) -> None:
    fixtures(tmp_path)
    assert run_workflow(tmp_path, "--current-model-ingest-script", str(ROOT / "missing_pr36.py")).returncode == 2
    outside = tmp_path / "outside.py"
    outside.write_text("print('outside')\n", encoding="utf-8")
    assert run_workflow(tmp_path, "--current-model-ingest-script", str(outside)).returncode == 2


def test_outputs_forbidden_names_and_sources_not_modified(tmp_path: Path) -> None:
    input_dir = fixtures(tmp_path)
    source_files = list(input_dir.glob("*.json")) + list((tmp_path / "exports" / "TestProject").rglob("ai-*.json"))
    before = {path: path.read_text(encoding="utf-8") for path in source_files}
    assert run_workflow(tmp_path).returncode == 0
    for path in out_dir(tmp_path).iterdir():
        path.resolve().relative_to(out_dir(tmp_path).resolve())
    forbidden = {"TestProject-current-models-normalized.json", "TestProject-rating-models-normalized.json", "TestProject-topology-current-allocation.json", "TestProject-topology-copper-calculations.json", "TestProject-topology-margin-calculations.json"}
    assert forbidden.isdisjoint({path.name for path in out_dir(tmp_path).glob("*.json")})
    assert forbidden.isdisjoint({path.name for path in [out_dir(tmp_path) / "ai-candidate-current-model-ingest-input.json", out_dir(tmp_path) / "ai-candidate-rating-current-model-ingest-input.json"]})
    after = {path: path.read_text(encoding="utf-8") for path in before}
    assert before == after
    assert "ai_candidate_core_input_ingest_workflow" not in CURRENT_SCRIPT.read_text(encoding="utf-8")
    assert "ai_candidate_core_input_ingest_workflow" not in RATING_SCRIPT.read_text(encoding="utf-8")


def test_empty_and_malformed_candidate_inputs(tmp_path: Path) -> None:
    input_dir = fixtures(tmp_path, current_records=[], rating_records=[])
    assert run_workflow(tmp_path).returncode == 0
    assert read_json(out_dir(tmp_path) / "ai-candidate-current-models-normalized.json")["normalized_currents"] == []
    (input_dir / "ai-candidate-current-model-input.json").write_text("{bad", encoding="utf-8")
    assert run_workflow(tmp_path).returncode == 2
    fixtures(tmp_path)
    (input_dir / "ai-candidate-rating-model-input.json").write_text("{bad", encoding="utf-8")
    assert run_workflow(tmp_path).returncode == 2


def test_unmappable_candidate_records_are_blocked(tmp_path: Path) -> None:
    bad_current = current_record()
    bad_current["target_identity"] = {}
    bad_rating = rating_record()
    bad_rating["candidate_value"] = {"rating_a": 3.0}
    fixtures(tmp_path, current_records=[bad_current], rating_records=[bad_rating])
    assert run_workflow(tmp_path).returncode == 0
    blockers = output(tmp_path, "ai-candidate-core-input-ingest-blockers.json")["blocker_records"]
    codes = {row["reason_code"] for row in blockers}
    assert "missing_candidate_record_target_identity" in codes
    assert "missing_candidate_record_unit" in codes
    assert "candidate_current_adapter_empty" in codes
    assert "candidate_rating_adapter_empty" in codes


def test_addenda_index_review_blockers_and_summary(tmp_path: Path) -> None:
    fixtures(tmp_path)
    assert run_workflow(tmp_path).returncode == 0
    addenda = output(tmp_path, "ai-candidate-addenda-ingest-index.json")
    assert addenda["safe_to_merge_automatically"] is False
    assert addenda["merged_addenda"] is False
    assert len(addenda["indexed_addenda_files"]) == 4
    assert all(row["safe_to_merge_automatically"] is False for row in addenda["indexed_addenda_files"])
    review = output(tmp_path, "ai-candidate-core-input-ingest-review.json")
    assert "current allocation" in review["not_performed_steps"]
    assert review["provenance_gaps"]
    blockers = output(tmp_path, "ai-candidate-core-input-ingest-blockers.json")["blocker_records"]
    assert any(row["reason_code"] == "addenda_requires_merge_validator" for row in blockers)
    assert any(row["reason_code"] == "provenance_gap" for row in blockers)
    summary = output(tmp_path, "ai-candidate-core-input-ingest-manifest.json")["summary"]
    assert summary["blocker_count"] == len(blockers)
    assert summary["addenda_records_seen"] == 4


def test_source_artifacts_and_status_safety_booleans(tmp_path: Path) -> None:
    fixtures(tmp_path)
    assert run_workflow(tmp_path).returncode == 0
    manifest = output(tmp_path, "ai-candidate-core-input-ingest-manifest.json")
    status = output(tmp_path, "ai-candidate-core-input-ingest-status.json")
    artifact_types = {row["artifact_type"] for row in manifest["source_artifacts"]}
    assert {"ai_candidate_core_input_apply_manifest", "ai_candidate_core_input_apply_status", "ai_candidate_current_model_input", "ai_candidate_rating_model_input"}.issubset(artifact_types)
    assert status["wrote_core_artifacts"] is False
    assert status["wrote_core_normalized_outputs"] is False
    assert status["ran_current_allocation"] is False
    assert status["ran_calculations"] is False
    assert status["merged_addenda"] is False
    assert status["safe_for_core_apply"] is False


def test_no_forbidden_terms_or_core_write_instructions_are_emitted(tmp_path: Path) -> None:
    fixtures(tmp_path)
    assert run_workflow(tmp_path).returncode == 0
    for path in out_dir(tmp_path).glob("*.json"):
        artifact = read_json(path)
        assert not FORBIDDEN_KEYS.intersection(all_keys(artifact))
        payload = json.dumps(artifact)
        assert '"safe_to_merge_automatically": true' not in payload
        assert '"wrote_core_artifacts": true' not in payload
        assert '"wrote_core_normalized_outputs": true' not in payload
        assert '"ran_current_allocation": true' not in payload
        assert '"ran_calculations": true' not in payload
        assert '"merged_addenda": true' not in payload


def test_subprocess_guardrails_and_no_network_imports() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "shell=True" not in text
    assert "subprocess.run(command" in text
    lowered = text.lower()
    for token in ("openai", "anthropic", "requests", "httpx", "urllib", "socket", "google.generative"):
        assert token not in lowered
    for script_name in ("topology_current_allocate.py", "topology_copper_calculate.py", "topology_margin_calculate.py"):
        assert script_name not in text


def test_output_order_and_repeated_run_stable_except_timestamp(tmp_path: Path) -> None:
    fixtures(tmp_path)
    assert run_workflow(tmp_path).returncode == 0
    first = output(tmp_path, "ai-candidate-core-input-ingest-manifest.json")
    assert [step["step_id"] for step in first["steps"]] == ["current_candidate_ingest", "rating_current_candidate_ingest", "rating_model_candidate_ingest"]
    assert run_workflow(tmp_path).returncode == 0
    second = output(tmp_path, "ai-candidate-core-input-ingest-manifest.json")
    first["generated_at_utc"] = "<ts>"
    second["generated_at_utc"] = "<ts>"
    assert first == second


def test_docs_state_limits_future_stage_and_manual_commands() -> None:
    text = DOC.read_text(encoding="utf-8").lower()
    for phrase in [
        "consumes pr35",
        "does not call ai",
        "not core outputs",
        "does not write core normalized outputs",
        "does not run allocation",
        "does not run calculations",
        "does not merge addenda",
        "future explicit stages",
        "provenance gaps",
        "manual validation commands",
    ]:
        assert phrase in text
