from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any

import jsonschema


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ai_candidate_normalized_promotion_review.py"
SCHEMA = ROOT / "schemas" / "ai_candidate_normalized_promotion_review_schema.json"
DOC = ROOT / "docs" / "ai_candidate_normalized_promotion_review.md"
OUTPUT_NAMES = {
    "ai-candidate-normalized-promotion-review.json",
    "ai-candidate-normalized-promotion-status.json",
    "ai-candidate-current-normalized-diff.json",
    "ai-candidate-rating-normalized-diff.json",
    "ai-candidate-normalized-approval-readiness.json",
    "ai-candidate-normalized-review-blockers.json",
    "ai-candidate-normalized-review-summary.txt",
}
JSON_OUTPUT_NAMES = OUTPUT_NAMES - {"ai-candidate-normalized-review-summary.txt"}
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


def ingested_dir(tmp_path: Path) -> Path:
    return tmp_path / "exports" / "TestProject" / "ai_candidate_core_ingested"


def out_dir(tmp_path: Path) -> Path:
    return tmp_path / "exports" / "TestProject" / "ai_candidate_normalized_review"


def output(tmp_path: Path, name: str) -> dict[str, Any]:
    return read_json(out_dir(tmp_path) / name)


def run_review(tmp_path: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--project",
            "TestProject",
            "--candidate-ingested-dir",
            str(ingested_dir(tmp_path)),
            "--out-dir",
            str(out_dir(tmp_path)),
            *extra,
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )


def current_record(**overrides: Any) -> dict[str, Any]:
    record = {
        "record_id": "cur_component_current_u2_v3p3_000001_max",
        "record_type": "component_current",
        "target_type": "component",
        "branch_id": None,
        "rail_name": "V3P3",
        "net_name": None,
        "refdes": "U2",
        "pin": None,
        "value": 0.085,
        "unit": "A",
        "current_type": "max",
        "basis": "ai_candidate_core_input_apply",
        "evidence_refs": ["datasheets/U2.pdf:92"],
        "source_artifacts": [{"artifact_type": "current_model", "path": "candidate-current-input.json", "record_id": "source_record_000001"}],
        "provenance": {"original_value": 0.085, "original_unit": "A", "normalized_unit": "A"},
    }
    record.update(overrides)
    return record


def rating_record(**overrides: Any) -> dict[str, Any]:
    record = {
        "candidate_record_id": "candidate_rating_001",
        "source_pr34_operation_id": "op_rating",
        "source_promotion_candidate_id": "pc_rating",
        "source_approval_item_id": "aq_rating",
        "source_decision_id": "decision_rating",
        "rating_id": "rating_connector_j1_unknown_current_max_000001",
        "source_record_id": "cur_rating_connector_j1_unknown_000001",
        "target_type": "connector",
        "normalized_target_type": "connector",
        "refdes": "J1",
        "pin": None,
        "rail_name": None,
        "branch_id": None,
        "net_name": None,
        "rating_name": "current_max",
        "normalized_rating_name": "current_max",
        "value_a": 3.0,
        "unit": "A",
        "basis": "ai_candidate_core_input_apply",
        "evidence_refs": ["datasheets/J1.pdf:14"],
        "explicit_not_branch_current_a": True,
        "source_artifacts": [{"artifact_type": "current_model", "path": "candidate-rating-input.json", "record_id": "source_record_000001"}],
        "provenance": {"original_value": 3.0, "original_unit": "A", "normalized_unit": "A"},
    }
    record.update(overrides)
    return record


def fixtures(
    tmp_path: Path,
    *,
    status: str = "candidate_ingest_pass",
    current_records: list[dict[str, Any]] | None = None,
    rating_records: list[dict[str, Any]] | None = None,
    provenance_gaps: list[dict[str, Any]] | None = None,
    addenda_records: int = 0,
) -> Path:
    source = ingested_dir(tmp_path)
    current_records = [current_record()] if current_records is None else current_records
    rating_records = [rating_record()] if rating_records is None else rating_records
    provenance_gaps = [{"reason_code": "provenance_gap", "field": "source_decision_id", "detail": "source_decision_id not present"}] if provenance_gaps is None else provenance_gaps
    write_json(source / "ai-candidate-core-input-ingest-manifest.json", {"project": "TestProject", "schema_version": "ai_candidate_core_input_ingest_v1", "summary": {"current_candidate_normalized_record_count": len(current_records), "rating_candidate_normalized_record_count": len(rating_records)}, "errors": [], "warnings": []})
    write_json(source / "ai-candidate-core-input-ingest-status.json", {"project": "TestProject", "schema_version": "ai_candidate_core_input_ingest_v1", "status": status, "candidate_ingest_only": True, "errors": [], "warnings": []})
    write_json(source / "ai-candidate-current-models-normalized.json", {"project": "TestProject", "schema_version": "1.0", "normalized_currents": current_records, "summary": {"normalized_count": len(current_records)}, "errors": [], "warnings": []})
    write_json(source / "ai-candidate-rating-models-normalized.json", {"project": "TestProject", "schema_version": "1.0", "normalized_ratings": rating_records, "summary": {"normalized_rating_count": len(rating_records)}, "errors": [], "warnings": []})
    write_json(source / "ai-candidate-core-input-ingest-review.json", {"project": "TestProject", "schema_version": "ai_candidate_core_input_ingest_v1", "provenance_gaps": provenance_gaps, "errors": [], "warnings": []})
    write_json(source / "ai-candidate-core-input-ingest-blockers.json", {"project": "TestProject", "schema_version": "ai_candidate_core_input_ingest_v1", "blocker_records": [], "errors": [], "warnings": []})
    write_json(source / "ai-candidate-addenda-ingest-index.json", {"project": "TestProject", "schema_version": "ai_candidate_core_input_ingest_v1", "indexed_addenda_files": [{"addenda_type": "role_addenda", "path": "role.json", "record_count": addenda_records, "safe_to_merge_automatically": False, "merged_addenda": False}], "safe_to_merge_automatically": False, "merged_addenda": False, "errors": [], "warnings": []})
    write_json(tmp_path / "exports" / "TestProject" / "ai_candidate_core_inputs" / "ai-candidate-core-input-apply-manifest.json", {"source": "pr35"})
    write_json(tmp_path / "exports" / "TestProject" / "ai_promotion_apply_dry_run" / "ai-approved-promotion-apply-dry-run.json", {"source": "pr34"})
    write_json(tmp_path / "exports" / "TestProject" / "ai_promotion" / "ai-approval-decisions.json", {"source": "pr33"})
    write_json(tmp_path / "exports" / "TestProject" / "ai_promotion" / "ai-candidate-promotion-plan.json", {"source": "pr32"})
    return source


def write_core(tmp_path: Path, *, current_records: list[dict[str, Any]] | None = None, rating_records: list[dict[str, Any]] | None = None) -> tuple[Path, Path]:
    current_path = tmp_path / "exports" / "TestProject-current-models-normalized.json"
    rating_path = tmp_path / "exports" / "TestProject-rating-models-normalized.json"
    write_json(current_path, {"project": "TestProject", "normalized_currents": [] if current_records is None else current_records, "summary": {}})
    write_json(rating_path, {"project": "TestProject", "normalized_ratings": [] if rating_records is None else rating_records, "summary": {}})
    return current_path, rating_path


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


def classifications(tmp_path: Path) -> list[str]:
    review = output(tmp_path, "ai-candidate-normalized-promotion-review.json")
    rows = review["current_model_review_items"] + review["rating_model_review_items"]
    return [row["review_classification"] for row in rows]


def test_missing_and_malformed_inputs_exit_2(tmp_path: Path) -> None:
    assert run_review(tmp_path).returncode == 2
    source = ingested_dir(tmp_path)
    source.mkdir(parents=True)
    assert run_review(tmp_path).returncode == 2
    write_json(source / "ai-candidate-core-input-ingest-manifest.json", {})
    assert run_review(tmp_path).returncode == 2
    (source / "ai-candidate-core-input-ingest-manifest.json").write_text("{bad", encoding="utf-8")
    write_json(source / "ai-candidate-core-input-ingest-status.json", {})
    assert run_review(tmp_path).returncode == 2
    write_json(source / "ai-candidate-core-input-ingest-manifest.json", {})
    (source / "ai-candidate-core-input-ingest-status.json").write_text("{bad", encoding="utf-8")
    assert run_review(tmp_path).returncode == 2


def test_failed_pr36_status_blocks_in_strict_mode(tmp_path: Path) -> None:
    fixtures(tmp_path, status="candidate_ingest_failed")
    result = run_review(tmp_path, "--strict", "--allow-missing-core")
    assert result.returncode == 1
    blockers = output(tmp_path, "ai-candidate-normalized-review-blockers.json")["blocker_records"]
    assert any(row["reason_code"] == "candidate_ingest_status_failed" for row in blockers)


def test_output_shape_status_json_safety_and_schema(tmp_path: Path) -> None:
    fixtures(tmp_path, provenance_gaps=[])
    result = run_review(tmp_path, "--allow-missing-core")
    assert result.returncode == 0, result.stderr + result.stdout
    assert {path.name for path in out_dir(tmp_path).iterdir()} == OUTPUT_NAMES
    schema = read_json(SCHEMA)
    for name in JSON_OUTPUT_NAMES:
        artifact = output(tmp_path, name)
        jsonschema.validate(instance=artifact, schema=schema)
        assert not any(isinstance(value, float) and not math.isfinite(value) for value in all_values(artifact))
    review = output(tmp_path, "ai-candidate-normalized-promotion-review.json")
    status = output(tmp_path, "ai-candidate-normalized-promotion-status.json")
    assert {"project", "generated_at_utc", "schema_version", "review_only", "source_artifacts", "core_artifacts", "current_model_review_items", "rating_model_review_items", "summary"}.issubset(review)
    assert {"project", "status", "review_only", "applied_anything", "wrote_core_artifacts", "requires_future_core_apply_stage"}.issubset(status)


def test_add_duplicate_and_conflict_classifications(tmp_path: Path) -> None:
    fixtures(tmp_path)
    core_current, core_rating = write_core(tmp_path)
    assert run_review(tmp_path, "--core-current-models-normalized", str(core_current), "--core-rating-models-normalized", str(core_rating)).returncode == 0
    assert classifications(tmp_path).count("add_candidate") == 2

    fixtures(tmp_path)
    core_current, core_rating = write_core(tmp_path, current_records=[current_record()], rating_records=[rating_record()])
    assert run_review(tmp_path, "--core-current-models-normalized", str(core_current), "--core-rating-models-normalized", str(core_rating)).returncode == 0
    assert classifications(tmp_path).count("duplicate_existing") == 2

    fixtures(tmp_path)
    core_current, core_rating = write_core(tmp_path, current_records=[current_record(value=0.1)], rating_records=[rating_record(value_a=4.0)])
    assert run_review(tmp_path, "--core-current-models-normalized", str(core_current), "--core-rating-models-normalized", str(core_rating)).returncode == 0
    assert classifications(tmp_path).count("conflict_with_core") == 2


def test_missing_identity_value_and_unit_are_blocked(tmp_path: Path) -> None:
    bad_current = [
        current_record(record_id="c_missing_identity", refdes=None, rail_name=None, branch_id=None, net_name=None),
        current_record(record_id="c_missing_value", value=None),
        current_record(record_id="c_missing_unit", unit=None),
    ]
    bad_rating = [
        rating_record(rating_id="r_missing_identity", refdes=None, rail_name=None, branch_id=None, net_name=None),
        rating_record(rating_id="r_missing_value", value_a=None),
        rating_record(rating_id="r_missing_unit", unit=None),
    ]
    fixtures(tmp_path, current_records=bad_current, rating_records=bad_rating)
    core_current, core_rating = write_core(tmp_path)
    assert run_review(tmp_path, "--core-current-models-normalized", str(core_current), "--core-rating-models-normalized", str(core_rating)).returncode == 0
    classes = classifications(tmp_path)
    assert classes.count("missing_identity") == 2
    assert classes.count("missing_value") == 2
    assert classes.count("missing_unit") == 2
    codes = {row["reason_code"] for row in output(tmp_path, "ai-candidate-normalized-review-blockers.json")["blocker_records"]}
    assert {"missing_identity", "missing_value", "missing_unit"}.issubset(codes)


def test_missing_core_handling_allow_and_strict(tmp_path: Path) -> None:
    fixtures(tmp_path, provenance_gaps=[])
    assert run_review(tmp_path, "--allow-missing-core").returncode == 0
    assert "add_candidate" in classifications(tmp_path)
    blockers = output(tmp_path, "ai-candidate-normalized-review-blockers.json")["blocker_records"]
    assert {"missing_core_current_normalized", "missing_core_rating_normalized"}.isdisjoint({row["reason_code"] for row in blockers})
    assert {"core current normalized artifact is missing", "core rating normalized artifact is missing"}.issubset(set(output(tmp_path, "ai-candidate-normalized-promotion-status.json")["warnings"]))
    fixtures(tmp_path, provenance_gaps=[])
    assert run_review(tmp_path, "--strict").returncode == 1
    status = output(tmp_path, "ai-candidate-normalized-promotion-status.json")
    assert status["status"] == "review_failed"
    blockers = output(tmp_path, "ai-candidate-normalized-review-blockers.json")["blocker_records"]
    assert {"missing_core_current_normalized", "missing_core_rating_normalized"}.issubset({row["reason_code"] for row in blockers})


def test_q1_q2_review_ready_items_preserve_candidate_provenance(tmp_path: Path) -> None:
    q1 = rating_record(
        candidate_record_id="candidate_q1",
        source_pr34_operation_id="op_33ac928966d3",
        source_promotion_candidate_id="promo_rating_model_c6924c781f53",
        source_approval_item_id="approve_9c4366f44e61",
        source_decision_id="decision_7a903398d9e8",
        rating_id="rating_load_switch_q1_unknown_current_max_000001",
        source_record_id="cur_rating_load_switch_q1_unknown_000001",
        target_type="load_switch",
        normalized_target_type="load_switch",
        refdes="Q1",
        value_a=0.2,
        original_value=0.2,
        evidence_refs=["BSS138W page 1"],
    )
    q2 = rating_record(
        candidate_record_id="candidate_q2",
        source_pr34_operation_id="op_cde90c46cbb4",
        source_promotion_candidate_id="promo_rating_model_457260c0879c",
        source_approval_item_id="approve_1179c269a25b",
        source_decision_id="decision_5b0836a84c7d",
        rating_id="rating_load_switch_q2_unknown_current_max_000002",
        source_record_id="cur_rating_load_switch_q2_unknown_000002",
        target_type="load_switch",
        normalized_target_type="load_switch",
        refdes="Q2",
        value_a=8.8,
        original_value=8.8,
        evidence_refs=["FDS4435BZ page 1"],
    )
    fixtures(tmp_path, current_records=[], rating_records=[q1, q2], provenance_gaps=[])
    assert run_review(tmp_path, "--allow-missing-core", "--strict").returncode == 0
    readiness = output(tmp_path, "ai-candidate-normalized-approval-readiness.json")
    blockers = output(tmp_path, "ai-candidate-normalized-review-blockers.json")["blocker_records"]
    assert blockers == []
    assert len(readiness["review_ready_items"]) == 2
    assert readiness["ready_for_core_apply"] is False
    assert readiness["safe_for_core_apply"] is False
    for item in readiness["review_ready_items"]:
        assert item["record_family"] == "rating_model"
        assert item["review_classification"] == "add_candidate"
        assert item["candidate_identity"]["normalized_target_type"] == "load_switch"
        assert item["candidate_identity"]["normalized_rating_name"] == "current_max"
        assert item["provenance"]["candidate_record_id"] in {"candidate_q1", "candidate_q2"}
        assert item["provenance"]["source_approval_item_id"] in {"approve_9c4366f44e61", "approve_1179c269a25b"}
        assert item["provenance"]["source_pr34_operation_id"] in {"op_33ac928966d3", "op_cde90c46cbb4"}
        assert item["provenance"]["source_promotion_candidate_id"] in {"promo_rating_model_c6924c781f53", "promo_rating_model_457260c0879c"}
        assert item["provenance"]["source_decision_id"] in {"decision_7a903398d9e8", "decision_5b0836a84c7d"}
        assert item["provenance"]["evidence_refs"]
        assert item["provenance"]["explicit_not_branch_current_a"] is True
        assert item["candidate_identity"].get("field_name") != "branch_current_a"
        assert item["record_family"] != "current_model"


def test_rating_guardrails_no_pin_expansion_or_regulator_side_inference(tmp_path: Path) -> None:
    connector = rating_record(rating_id="connector_wide", target_type="connector", normalized_target_type="connector", refdes="J1", pin=None)
    regulator = rating_record(rating_id="regulator_generic", target_type="regulator", normalized_target_type="regulator", refdes="U5", pin=None, normalized_rating_name="current_max")
    fixtures(tmp_path, current_records=[], rating_records=[connector, regulator])
    core_current, core_rating = write_core(tmp_path)
    assert run_review(tmp_path, "--core-current-models-normalized", str(core_current), "--core-rating-models-normalized", str(core_rating)).returncode == 0
    rows = output(tmp_path, "ai-candidate-rating-normalized-diff.json")["review_items"]
    assert len(rows) == 2
    assert all(row["candidate_identity"].get("pin") is None for row in rows)
    assert {row["candidate_identity"]["normalized_target_type"] for row in rows} == {"connector", "regulator"}


def test_unknown_current_is_not_treated_as_zero(tmp_path: Path) -> None:
    fixtures(tmp_path, current_records=[current_record(value=None)], rating_records=[])
    core_current, core_rating = write_core(tmp_path, current_records=[current_record(value=0.0)])
    assert run_review(tmp_path, "--core-current-models-normalized", str(core_current), "--core-rating-models-normalized", str(core_rating)).returncode == 0
    item = output(tmp_path, "ai-candidate-current-normalized-diff.json")["review_items"][0]
    assert item["review_classification"] == "missing_value"
    assert item["candidate_value"]["value"] is None


def test_provenance_addenda_readiness_and_summary(tmp_path: Path) -> None:
    fixtures(tmp_path, addenda_records=1)
    core_current, core_rating = write_core(tmp_path)
    assert run_review(tmp_path, "--core-current-models-normalized", str(core_current), "--core-rating-models-normalized", str(core_rating)).returncode == 0
    review = output(tmp_path, "ai-candidate-normalized-promotion-review.json")
    readiness = output(tmp_path, "ai-candidate-normalized-approval-readiness.json")
    blockers = output(tmp_path, "ai-candidate-normalized-review-blockers.json")["blocker_records"]
    assert review["provenance_gaps"]
    assert readiness["ready_for_core_apply"] is False
    assert readiness["safe_for_core_apply"] is False
    all_items = review["current_model_review_items"] + review["rating_model_review_items"]
    assert all(row["requires_future_core_apply_stage"] is True for row in all_items)
    assert all(row["requires_human_review"] is True for row in all_items)
    codes = {row["reason_code"] for row in blockers}
    assert {"provenance_gap", "addenda_requires_merge_validator"}.issubset(codes)
    summary = review["summary"]
    assert summary["current_review_item_count"] == len(review["current_model_review_items"])
    assert summary["rating_review_item_count"] == len(review["rating_model_review_items"])
    assert summary["human_review_required_count"] == len(all_items)


def test_outputs_sources_and_core_artifacts_not_modified(tmp_path: Path) -> None:
    source = fixtures(tmp_path)
    core_current, core_rating = write_core(tmp_path, current_records=[current_record()], rating_records=[rating_record()])
    source_files = list(source.glob("*.json")) + list((tmp_path / "exports" / "TestProject").rglob("ai-*.json")) + [core_current, core_rating]
    before = {path: path.read_text(encoding="utf-8") for path in source_files}
    assert run_review(tmp_path, "--core-current-models-normalized", str(core_current), "--core-rating-models-normalized", str(core_rating)).returncode == 0
    for path in out_dir(tmp_path).iterdir():
        path.resolve().relative_to(out_dir(tmp_path).resolve())
    forbidden = {"TestProject-current-models-normalized.json", "TestProject-rating-models-normalized.json", "TestProject-topology-current-allocation.json", "TestProject-topology-copper-calculations.json", "TestProject-topology-margin-calculations.json"}
    assert forbidden.isdisjoint({path.name for path in out_dir(tmp_path).glob("*.json")})
    after = {path: path.read_text(encoding="utf-8") for path in before}
    assert before == after


def test_no_script_invocation_subprocess_network_or_forbidden_fields(tmp_path: Path) -> None:
    fixtures(tmp_path)
    assert run_review(tmp_path, "--allow-missing-core").returncode == 0
    text = SCRIPT.read_text(encoding="utf-8")
    assert "subprocess" not in text
    for token in ("openai", "anthropic", "requests", "httpx", "urllib", "socket", "google.generative"):
        assert token not in text.lower()
    for script_name in ("current_model_ingest.py", "rating_model_ingest.py", "topology_current_allocate.py", "topology_copper_calculate.py", "topology_margin_calculate.py"):
        assert script_name not in text
    for path in out_dir(tmp_path).glob("*.json"):
        artifact = read_json(path)
        assert not FORBIDDEN_KEYS.intersection(all_keys(artifact))
        payload = json.dumps(artifact)
        assert '"safe_to_merge_automatically": true' not in payload
        assert '"applied_anything": true' not in payload
        assert '"wrote_core_artifacts": true' not in payload
        assert '"wrote_core_normalized_outputs": true' not in payload
        assert '"ran_ingestion": true' not in payload
        assert '"ran_current_allocation": true' not in payload
        assert '"ran_calculations": true' not in payload
        assert '"merged_addenda": true' not in payload
        assert '"safe_for_core_apply": true' not in payload
        assert '"ready_for_core_apply": true' not in payload


def test_review_item_ids_order_and_repeat_stable_except_timestamps(tmp_path: Path) -> None:
    fixtures(tmp_path)
    core_current, core_rating = write_core(tmp_path)
    assert run_review(tmp_path, "--core-current-models-normalized", str(core_current), "--core-rating-models-normalized", str(core_rating)).returncode == 0
    first = output(tmp_path, "ai-candidate-normalized-promotion-review.json")
    ids = [row["review_item_id"] for row in first["current_model_review_items"] + first["rating_model_review_items"]]
    assert ids == sorted(ids)
    assert run_review(tmp_path, "--core-current-models-normalized", str(core_current), "--core-rating-models-normalized", str(core_rating)).returncode == 0
    second = output(tmp_path, "ai-candidate-normalized-promotion-review.json")
    for artifact in (first, second):
        artifact["generated_at_utc"] = "<ts>"
    assert first == second


def test_docs_state_limits_future_stage_and_manual_commands() -> None:
    text = DOC.read_text(encoding="utf-8").lower()
    for phrase in [
        "consumes pr36",
        "does not call ai",
        "does not write core artifacts",
        "does not run ingestion",
        "does not run allocation",
        "does not run calculations",
        "does not merge addenda",
        "does not make pass/fail",
        "human review remains required",
        "future core apply stage",
        "manual validation commands",
    ]:
        assert phrase in text
