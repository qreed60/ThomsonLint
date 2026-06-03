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
SCRIPT = ROOT / "scripts" / "ai_candidate_core_input_apply.py"
SCHEMA = ROOT / "schemas" / "ai_candidate_core_input_apply_schema.json"
DOC = ROOT / "docs" / "ai_candidate_core_input_apply.md"
OUTPUT_NAMES = [
    "ai-candidate-core-input-apply-manifest.json",
    "ai-candidate-core-input-apply-status.json",
    "ai-candidate-current-model-input.json",
    "ai-candidate-rating-model-input.json",
    "ai-candidate-role-addenda.json",
    "ai-candidate-pin-role-addenda.json",
    "ai-candidate-rail-relationship-hints.json",
    "ai-candidate-passive-support-inputs.json",
    "ai-candidate-core-input-apply-diff.json",
    "ai-candidate-core-input-apply-blockers.json",
]
REPORT_NAMES = [
    "ai-candidate-core-input-apply-summary.txt",
    "ai-candidate-core-input-apply-report.md",
]
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
CORE_OUTPUT_NAMES = {
    "TestProject-current-models-normalized.json",
    "TestProject-rating-models-normalized.json",
    "TestProject-topology-current-allocation.json",
    "TestProject-topology-copper-calculations.json",
    "TestProject-topology-margin-calculations.json",
}


def write_json(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8")
    return path


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def run_apply(tmp_path: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--project",
            "TestProject",
            "--dry-run-dir",
            str(tmp_path / "exports" / "TestProject" / "ai_promotion_apply_dry_run"),
            "--out-dir",
            str(tmp_path / "exports" / "TestProject" / "ai_candidate_core_inputs"),
            *extra,
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )


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


def op(
    operation_id: str,
    kind: str = "current_model",
    dry_operation: str = "would_add",
    status: str = "preview_only",
    target: dict[str, Any] | None = None,
    value: dict[str, Any] | None = None,
    evidence: list[str] | None = None,
    blockers: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "operation_id": operation_id,
        "promotion_candidate_id": f"pc_{operation_id}",
        "approval_item_id": f"aq_{operation_id}",
        "decision_id": f"decision_{operation_id}",
        "candidate_kind": kind,
        "dry_run_operation": dry_operation,
        "operation_status": status,
        "dry_run_only": True,
        "writes_core_artifact": False,
        "safe_to_apply_in_pr34": False,
        "requires_future_apply_stage": True,
        "target_identity": target if target is not None else {"refdes": "U2", "rail_name": "V3P3"},
        "candidate_value": value if value is not None else {"current_a": 0.085, "unit": "A", "condition": "max"},
        "core_match": {"match_status": "no_core_match", "matched_core_record_ids": []},
        "approval": {"decision": "approved", "reviewer": "qa", "reviewed_at_utc": "2026-05-31T00:00:00Z", "approval_note": "approved from evidence"},
        "preview_target": {"preview_only": True},
        "evidence_refs": evidence if evidence is not None else ["datasheets/U2.pdf:92"],
        "blockers": blockers or [],
        "warnings": [],
    }


def fixtures(
    tmp_path: Path,
    operations: list[dict[str, Any]] | None = None,
    skipped: list[dict[str, Any]] | None = None,
    blocked: list[dict[str, Any]] | None = None,
    status: str = "dry_run_pass",
) -> Path:
    dry_dir = tmp_path / "exports" / "TestProject" / "ai_promotion_apply_dry_run"
    operations = operations if operations is not None else [op("001")]
    blocked = blocked or []
    skipped = skipped or []
    write_json(
        dry_dir / "ai-approved-promotion-apply-dry-run.json",
        {
            "project": "TestProject",
            "generated_at_utc": "2026-05-31T00:00:00Z",
            "schema_version": "ai_promotion_apply_dry_run_v1",
            "dry_run_only": True,
            "source_artifacts": [],
            "source_promotion_plan": "promotion",
            "source_approval_queue": "queue",
            "source_decisions": "decisions",
            "source_decision_validation": "validation",
            "core_artifacts": {},
            "approved_decision_count": len(operations),
            "dry_run_operations": operations,
            "blocked_operations": blocked,
            "skipped_decisions": skipped,
            "summary": {},
            "errors": [],
            "warnings": [],
        },
    )
    write_json(
        dry_dir / "ai-approved-promotion-apply-status.json",
        {
            "project": "TestProject",
            "generated_at_utc": "2026-05-31T00:00:00Z",
            "schema_version": "ai_promotion_apply_dry_run_v1",
            "status": status,
            "dry_run_only": True,
            "applied_anything": False,
            "wrote_core_artifacts": False,
            "ran_ingestion": False,
            "ran_current_allocation": False,
            "ran_calculations": False,
            "merged_addenda": False,
            "safe_to_apply_in_pr34": False,
            "requires_future_apply_stage": True,
            "approved_decision_count": len(operations),
            "dry_run_operation_count": len(operations),
            "blocked_operation_count": len(blocked),
            "skipped_decision_count": len(skipped),
            "errors": [],
            "warnings": [],
        },
    )
    write_json(dry_dir / "ai-promotion-apply-blockers.json", {"project": "TestProject", "schema_version": "ai_promotion_apply_dry_run_v1", "dry_run_only": True, "blocker_records": blocked, "summary": {}, "errors": [], "warnings": []})
    write_json(tmp_path / "exports" / "TestProject" / "ai_promotion" / "ai-candidate-promotion-plan.json", {"source": "pr32"})
    write_json(tmp_path / "exports" / "TestProject" / "ai_promotion" / "ai-approval-decisions.json", {"source": "pr33"})
    return dry_dir


def out_dir(tmp_path: Path) -> Path:
    return tmp_path / "exports" / "TestProject" / "ai_candidate_core_inputs"


def output(tmp_path: Path, name: str) -> dict[str, Any]:
    return read_json(out_dir(tmp_path) / name)


def outputs(tmp_path: Path) -> dict[str, dict[str, Any]]:
    return {name: output(tmp_path, name) for name in OUTPUT_NAMES}


def test_missing_dry_run_inputs_exit_2(tmp_path: Path) -> None:
    assert run_apply(tmp_path).returncode == 2
    dry_dir = tmp_path / "exports" / "TestProject" / "ai_promotion_apply_dry_run"
    dry_dir.mkdir(parents=True)
    assert run_apply(tmp_path).returncode == 2
    write_json(dry_dir / "ai-approved-promotion-apply-dry-run.json", {"project": "TestProject"})
    assert run_apply(tmp_path).returncode == 2
    (dry_dir / "ai-approved-promotion-apply-dry-run.json").write_text("{bad", encoding="utf-8")
    write_json(dry_dir / "ai-approved-promotion-apply-status.json", {"status": "dry_run_pass"})
    assert run_apply(tmp_path).returncode == 2


def test_output_shape_manifest_status_json_safety_and_schema(tmp_path: Path) -> None:
    fixtures(tmp_path)
    result = run_apply(tmp_path)
    assert result.returncode == 0, result.stderr + result.stdout
    artifacts = outputs(tmp_path)
    assert set(artifacts) == set(OUTPUT_NAMES)
    assert set(path.name for path in out_dir(tmp_path).iterdir()) == set(OUTPUT_NAMES + REPORT_NAMES)
    schema = read_json(SCHEMA)
    for artifact in artifacts.values():
        jsonschema.validate(instance=artifact, schema=schema)
        assert not any(isinstance(v, float) and not math.isfinite(v) for v in all_values(artifact))
    manifest = output(tmp_path, "ai-candidate-core-input-apply-manifest.json")
    status = output(tmp_path, "ai-candidate-core-input-apply-status.json")
    for key in ["project", "generated_at_utc", "schema_version", "source_artifacts", "candidate_outputs", "summary", "errors", "warnings"]:
        assert key in manifest
    for key in ["project", "status", "candidate_apply_only", "wrote_candidate_inputs", "wrote_core_artifacts", "requires_future_core_apply_stage"]:
        assert key in status


def test_current_and_rating_would_add_create_candidate_records(tmp_path: Path) -> None:
    rating_value = {"rating_a": 3.0, "unit": "A", "condition": "connector_wide"}
    fixtures(tmp_path, [op("001"), op("002", "rating_model", value=rating_value, target={"connector_ref": "J1"})])
    assert run_apply(tmp_path).returncode == 0
    current = output(tmp_path, "ai-candidate-current-model-input.json")["current_model_inputs"]
    rating = output(tmp_path, "ai-candidate-rating-model-input.json")["rating_model_inputs"]
    assert len(current) == 1
    assert len(rating) == 1
    assert rating[0]["candidate_value"] == rating_value
    assert "pin" not in rating[0]["target_identity"]
    assert "regulator_side" not in rating[0]["candidate_value"]
    assert "input_side" not in rating[0]["candidate_value"]
    assert "output_side" not in rating[0]["candidate_value"]


def test_pr34_skipped_and_blocked_operations_do_not_become_records(tmp_path: Path) -> None:
    blocked_op = op("blocked", blockers=["core_conflict"])
    blocked_op["operation_status"] = "blocked_dry_run"
    skipped = [{"approval_item_id": "aq_skip", "decision": "rejected", "reason_code": "decision_not_approved"}]
    fixtures(tmp_path, [blocked_op], skipped=skipped)
    assert run_apply(tmp_path).returncode == 0
    assert output(tmp_path, "ai-candidate-current-model-input.json")["current_model_inputs"] == []
    blockers = output(tmp_path, "ai-candidate-core-input-apply-blockers.json")["blocker_records"]
    assert any(row["reason_code"] == "operation_blocked_in_pr34" for row in blockers)


def test_would_skip_duplicate_records_skip_without_duplicate_candidate(tmp_path: Path) -> None:
    fixtures(tmp_path, [op("dup", dry_operation="would_skip_duplicate")])
    assert run_apply(tmp_path).returncode == 0
    assert output(tmp_path, "ai-candidate-current-model-input.json")["current_model_inputs"] == []
    diff = output(tmp_path, "ai-candidate-core-input-apply-diff.json")
    assert diff["duplicate_operations_skipped"][0]["reason_code"] == "duplicate_skipped"


def test_provenance_evidence_and_value_are_preserved_exactly(tmp_path: Path) -> None:
    value = {"current_a": None, "unit": "A", "condition": "unknown_current_not_zero"}
    evidence = ["datasheets/U2.pdf:92", "datasheets/U2.pdf:93"]
    fixtures(tmp_path, [op("001", value=value, evidence=evidence)])
    assert run_apply(tmp_path).returncode == 0
    record = output(tmp_path, "ai-candidate-current-model-input.json")["current_model_inputs"][0]
    assert record["candidate_value"] == value
    assert record["evidence_refs"] == evidence
    assert record["approval"]["approval_note"] == "approved from evidence"
    assert record["source_pr34_operation_id"] == "001"
    assert record["pr34_operation"]["dry_run_operation"] == "would_add"


def test_operator_preview_evidence_ref_fallback_is_preserved(tmp_path: Path) -> None:
    operation = op("rating", "rating_model", target={"target_type": "load_switch", "refdes": "Q1", "field_name": "current_max"}, value={"value": 0.2, "unit": "A"})
    operation.pop("evidence_refs")
    operation["operator_preview"] = {
        "evidence_ref": "BSS138W page 1",
        "explicit_note": "rating only; not branch_current_a",
        "not_branch_current_a": True,
    }
    fixtures(tmp_path, [operation])
    assert run_apply(tmp_path).returncode == 0
    rating = output(tmp_path, "ai-candidate-rating-model-input.json")["rating_model_inputs"]
    current = output(tmp_path, "ai-candidate-current-model-input.json")["current_model_inputs"]
    assert len(rating) == 1
    assert current == []
    assert rating[0]["evidence_refs"] == ["BSS138W page 1"]
    assert rating[0]["explicit_not_branch_current_a"] is True


def test_base_inputs_are_preserved_and_candidates_append_after_base(tmp_path: Path) -> None:
    fixtures(tmp_path, [op("001"), op("002", "rating_model", value={"rating_a": 1.5, "unit": "A"})])
    base_current = write_json(tmp_path / "base-current.json", {"current_model_inputs": [{"base_record_id": "base_current"}]})
    base_rating = write_json(tmp_path / "base-rating.json", {"rating_model_inputs": [{"base_record_id": "base_rating"}]})
    assert run_apply(tmp_path, "--base-current-input", str(base_current), "--base-rating-input", str(base_rating)).returncode == 0
    current = output(tmp_path, "ai-candidate-current-model-input.json")["current_model_inputs"]
    rating = output(tmp_path, "ai-candidate-rating-model-input.json")["rating_model_inputs"]
    assert current[0]["base_record_id"] == "base_current"
    assert rating[0]["base_record_id"] == "base_rating"
    assert current[1]["candidate_input"] is True
    assert rating[1]["candidate_input"] is True


def test_missing_base_inputs_create_candidate_only_files(tmp_path: Path) -> None:
    fixtures(tmp_path, [op("001"), op("002", "rating_model", value={"rating_a": 1.5, "unit": "A"})])
    assert run_apply(tmp_path).returncode == 0
    assert output(tmp_path, "ai-candidate-current-model-input.json")["base_records_preserved"] == 0
    assert output(tmp_path, "ai-candidate-rating-model-input.json")["base_records_preserved"] == 0


def test_invalid_operations_are_blocked_by_reason(tmp_path: Path) -> None:
    operations = [
        op("missing_target", target={}),
        op("missing_value", value={}),
        op("missing_evidence", evidence=[]),
        op("unsupported_kind", kind="mystery_model"),
        op("unsupported_op", dry_operation="would_update"),
    ]
    fixtures(tmp_path, operations)
    assert run_apply(tmp_path).returncode == 0
    codes = {row["reason_code"] for row in output(tmp_path, "ai-candidate-core-input-apply-blockers.json")["blocker_records"]}
    assert {"missing_target_identity", "missing_candidate_value", "missing_evidence", "unsupported_candidate_kind", "unsupported_dry_run_operation"}.issubset(codes)


def test_q1_q2_rating_operations_with_operator_evidence_create_only_rating_inputs(tmp_path: Path) -> None:
    q2 = op(
        "op_cde90c46cbb4",
        "rating_model",
        target={"target_type": "load_switch", "refdes": "Q2", "field_name": "current_max"},
        value={"value": 8.8, "unit": "A"},
    )
    q2.update({
        "promotion_candidate_id": "promo_rating_model_457260c0879c",
        "approval_item_id": "approve_1179c269a25b",
        "operator_preview": {
            "evidence_ref": "FDS4435BZ page 1",
            "explicit_note": "rating only; not branch_current_a",
            "not_branch_current_a": True,
        },
    })
    q2.pop("evidence_refs")
    q1 = op(
        "op_33ac928966d3",
        "rating_model",
        target={"target_type": "load_switch", "refdes": "Q1", "field_name": "current_max"},
        value={"value": 0.2, "unit": "A"},
    )
    q1.update({
        "promotion_candidate_id": "promo_rating_model_c6924c781f53",
        "approval_item_id": "approve_9c4366f44e61",
        "operator_preview": {
            "evidence_ref": "BSS138W page 1",
            "explicit_note": "rating only; not branch_current_a",
            "not_branch_current_a": True,
        },
    })
    q1.pop("evidence_refs")
    fixtures(tmp_path, [q2, q1])
    assert run_apply(tmp_path).returncode == 0
    rating = output(tmp_path, "ai-candidate-rating-model-input.json")["rating_model_inputs"]
    current = output(tmp_path, "ai-candidate-current-model-input.json")["current_model_inputs"]
    manifest = output(tmp_path, "ai-candidate-core-input-apply-manifest.json")
    assert len(rating) == 2
    assert current == []
    assert manifest["summary"]["candidate_apply_operation_count"] == 2
    assert manifest["summary"]["rating_model_records_added"] == 2
    assert manifest["summary"]["current_model_records_added"] == 0
    assert manifest["summary"]["blocked_operation_count"] == 0
    assert {row["source_approval_item_id"] for row in rating} == {"approve_1179c269a25b", "approve_9c4366f44e61"}
    assert {row["source_promotion_candidate_id"] for row in rating} == {"promo_rating_model_457260c0879c", "promo_rating_model_c6924c781f53"}
    assert {row["source_pr34_operation_id"] for row in rating} == {"op_cde90c46cbb4", "op_33ac928966d3"}
    assert "branch_current_a" not in {name for row in rating for name in target_field_names_for_test(row["target_identity"], row["candidate_value"])}


def target_field_names_for_test(*values: Any) -> set[str]:
    names: set[str] = set()
    for value in values:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"field_name", "field", "source_item_id", "target_field"} and child is not None:
                    names.add(str(child))
                names.update(target_field_names_for_test(child))
        elif isinstance(value, list):
            for child in value:
                names.update(target_field_names_for_test(child))
    return names


def test_rating_operation_targeting_branch_current_a_is_blocked(tmp_path: Path) -> None:
    operation = op("rating", "rating_model", target={"target_type": "load_switch", "refdes": "Q1", "field_name": "branch_current_a"}, value={"value": 0.2, "unit": "A"})
    fixtures(tmp_path, [operation])
    assert run_apply(tmp_path).returncode == 0
    blockers = output(tmp_path, "ai-candidate-core-input-apply-blockers.json")["blocker_records"]
    assert blockers[0]["reason_code"] == "attempted_core_write_blocked"


def test_safety_flags_still_block_candidate_apply(tmp_path: Path) -> None:
    unsafe_write = op("unsafe_write")
    unsafe_write["writes_core_artifact"] = True
    not_dry = op("not_dry")
    not_dry["dry_run_only"] = False
    safe_pr34 = op("safe_pr34")
    safe_pr34["safe_to_apply_in_pr34"] = True
    fixtures(tmp_path, [unsafe_write, not_dry, safe_pr34])
    assert run_apply(tmp_path).returncode == 0
    codes = {row["operation_id"]: row["reason_code"] for row in output(tmp_path, "ai-candidate-core-input-apply-blockers.json")["blocker_records"]}
    assert codes["unsafe_write"] == "attempted_core_write_blocked"
    assert codes["not_dry"] == "operation_not_approved_for_candidate_apply"
    assert codes["safe_pr34"] == "operation_not_approved_for_candidate_apply"


def test_addenda_blocked_by_default_and_include_addenda_writes_isolated_files(tmp_path: Path) -> None:
    addenda_ops = [
        op("role", "role_addendum", "would_require_merge_validator"),
        op("pin", "pin_role_addendum", "would_require_merge_validator"),
        op("rail", "rail_relationship_hint", "would_require_merge_validator"),
        op("passive", "passive_support", "would_require_merge_validator"),
    ]
    fixtures(tmp_path, addenda_ops)
    assert run_apply(tmp_path).returncode == 0
    assert output(tmp_path, "ai-candidate-role-addenda.json")["role_addenda"] == []
    assert any(row["reason_code"] == "addenda_requires_merge_validator" for row in output(tmp_path, "ai-candidate-core-input-apply-blockers.json")["blocker_records"])
    assert run_apply(tmp_path, "--include-addenda").returncode == 0
    role = output(tmp_path, "ai-candidate-role-addenda.json")
    pin = output(tmp_path, "ai-candidate-pin-role-addenda.json")
    rail = output(tmp_path, "ai-candidate-rail-relationship-hints.json")
    passive = output(tmp_path, "ai-candidate-passive-support-inputs.json")
    assert len(role["role_addenda"]) == 1
    assert len(pin["pin_role_addenda"]) == 1
    assert len(rail["rail_relationship_hints"]) == 1
    assert len(passive["passive_support_inputs"]) == 1
    for artifact in [role, pin, rail, passive]:
        assert artifact["safe_to_merge_automatically"] is False
        assert artifact["merged_addenda"] is False


def test_diff_blockers_and_summary_counts_match_outputs(tmp_path: Path) -> None:
    operations = [op("current"), op("rating", "rating_model", value={"rating_a": 1.0, "unit": "A"}), op("dup", dry_operation="would_skip_duplicate"), op("bad", dry_operation="would_update")]
    fixtures(tmp_path, operations)
    assert run_apply(tmp_path).returncode == 0
    manifest = output(tmp_path, "ai-candidate-core-input-apply-manifest.json")
    diff = output(tmp_path, "ai-candidate-core-input-apply-diff.json")
    blockers = output(tmp_path, "ai-candidate-core-input-apply-blockers.json")
    summary = manifest["summary"]
    assert diff["current_model_records_added"] == 1
    assert diff["rating_model_records_added"] == 1
    assert len(diff["duplicate_operations_skipped"]) == 1
    assert summary["blocked_operation_count"] == len(blockers["blocker_records"])
    assert summary["candidate_current_record_count"] == len(output(tmp_path, "ai-candidate-current-model-input.json")["current_model_inputs"])
    assert summary["candidate_rating_record_count"] == len(output(tmp_path, "ai-candidate-rating-model-input.json")["rating_model_inputs"])


def test_outputs_inside_out_dir_forbidden_core_names_and_sources_not_modified(tmp_path: Path) -> None:
    fixtures(tmp_path)
    dry_dir = tmp_path / "exports" / "TestProject" / "ai_promotion_apply_dry_run"
    source_files = list(dry_dir.glob("*.json")) + list((tmp_path / "exports" / "TestProject" / "ai_promotion").glob("*.json"))
    core = write_json(tmp_path / "TestProject-current-models-normalized.json", {"core": True})
    before = {path: path.read_text(encoding="utf-8") for path in source_files + [core]}
    assert run_apply(tmp_path).returncode == 0
    assert {path.name for path in out_dir(tmp_path).iterdir()} == set(OUTPUT_NAMES + REPORT_NAMES)
    assert not CORE_OUTPUT_NAMES.intersection({path.name for path in out_dir(tmp_path).glob("*.json")})
    after = {path: path.read_text(encoding="utf-8") for path in before}
    assert before == after


def test_no_normalized_outputs_reruns_subprocess_shell_or_network_imports() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "subprocess" not in text
    assert "shell=True" not in text
    assert not re.search(r"import (openai|anthropic|requests|httpx|urllib|socket)", text)
    assert "google.generative" not in text
    for script_name in ["current_model_ingest.py", "rating_model_ingest.py", "topology_current_allocate.py", "topology_copper_calculate.py", "topology_margin_calculate.py"]:
        assert script_name not in text


def test_no_forbidden_fields_or_core_write_instructions_are_emitted(tmp_path: Path) -> None:
    fixtures(tmp_path)
    assert run_apply(tmp_path).returncode == 0
    for artifact in outputs(tmp_path).values():
        assert not FORBIDDEN_KEYS.intersection(all_keys(artifact))
        payload = json.dumps(artifact)
        assert '"safe_to_merge_automatically": true' not in payload
        assert '"wrote_core_artifacts": true' not in payload
        assert '"wrote_normalized_outputs": true' not in payload
        assert '"ran_ingestion": true' not in payload
        assert '"ran_current_allocation": true' not in payload
        assert '"ran_calculations": true' not in payload
        assert '"merged_addenda": true' not in payload


def test_summary_and_report_files_are_generated(tmp_path: Path) -> None:
    operation = op("rating", "rating_model", target={"target_type": "load_switch", "refdes": "Q1", "field_name": "current_max"}, value={"value": 0.2, "unit": "A"})
    operation.pop("evidence_refs")
    operation["operator_preview"] = {"evidence_ref": "BSS138W page 1", "explicit_note": "rating only; not branch_current_a", "not_branch_current_a": True}
    fixtures(tmp_path, [operation])
    assert run_apply(tmp_path).returncode == 0
    summary = (out_dir(tmp_path) / "ai-candidate-core-input-apply-summary.txt").read_text(encoding="utf-8")
    report = (out_dir(tmp_path) / "ai-candidate-core-input-apply-report.md").read_text(encoding="utf-8")
    for line in [
        "candidate_apply_operation_count=1",
        "rating_model_records_added=1",
        "current_model_records_added=0",
        "blocked_operation_count=0",
        "wrote_core_artifacts=false",
        "wrote_normalized_outputs=false",
        "safe_for_core_apply=false",
        "requires_future_core_apply_stage=true",
        "do_not_apply_to_core_yet=true",
    ]:
        assert line in summary
    assert "BSS138W page 1" in report
    assert "not_branch_current_a: true" in report


def test_candidate_record_ids_order_and_repeated_run_are_stable_except_timestamps(tmp_path: Path) -> None:
    fixtures(tmp_path, [op("b", "rating_model", value={"rating_a": 1.0, "unit": "A"}), op("a")])
    assert run_apply(tmp_path).returncode == 0
    first = outputs(tmp_path)
    first_ids = [row["candidate_record_id"] for row in output(tmp_path, "ai-candidate-current-model-input.json")["current_model_inputs"]]
    assert first_ids == sorted(first_ids)
    assert run_apply(tmp_path).returncode == 0
    second = outputs(tmp_path)
    for artifacts in (first, second):
        for artifact in artifacts.values():
            artifact["generated_at_utc"] = "<ts>"
    assert first == second


def test_docs_state_limits_future_stage_and_manual_commands() -> None:
    text = DOC.read_text(encoding="utf-8").lower()
    for phrase in [
        "consumes pr34",
        "isolated candidate core-input files",
        "does not call ai",
        "does not write core artifacts",
        "does not write core normalized outputs",
        "does not overwrite normalized outputs",
        "does not run ingestion",
        "does not run allocation",
        "does not run calculations",
        "does not merge addenda",
        "future core apply",
        "manual validation commands",
    ]:
        assert phrase in text
