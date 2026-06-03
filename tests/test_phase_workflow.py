from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_phase_prompt(tmp_path: Path, phase: int, profile: str | None = None) -> str:
    out = tmp_path / f"phase{phase:02d}_prompt.md"
    env = os.environ.copy()
    env.pop("THOMSONLINT_ASSESSMENT_PROFILE", None)
    if profile is not None:
        env["THOMSONLINT_ASSESSMENT_PROFILE"] = profile
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "write_phase_prompt.py"),
            "--project",
            "example",
            "--phase",
            str(phase),
            "--out",
            str(out),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
        env=env,
    )
    return out.read_text(encoding="utf-8")


def checkpoint_row(phase: int, phase_name: str) -> dict:
    return {
        "phase_number": phase,
        "phase_name": phase_name,
        "started_at_utc": "2026-05-28T00:00:00Z",
        "completed_at_utc": "2026-05-28T00:00:00Z",
        "required_artifacts": [],
        "artifacts_verified": [],
        "validation_artifacts": [],
        "validation_passed": True,
        "blockers": [],
        "phase_passed": True,
        "failed_phase_number": None,
        "repair_required": False,
    }


def phase18_expanded_candidate_artifact(**overrides: object) -> dict:
    artifact = {
        "project": "TestProject",
        "phase": 18,
        "assessment_profile": "engineering",
        "verified_finding_candidates": [],
        "engineering_concern_candidates": [
            {
                "candidate_id": "ec_001",
                "candidate_type": "engineering_concern_candidate",
                "title": "Apparent load-switching section needs review",
                "statement": "Observed apparent V24P0 high-side PMOS/load-switching section. Verify FET Vds, Vgs, gate pull network, transient exposure, and load current.",
                "engineering_basis": "Vision annotation references Q2/R68/R69/R70 and V24P0/P20 context.",
                "evidence_refs": ["exports/TestProject-vision-engineering-annotations.json#annotation-1"],
                "source_artifacts": ["exports/TestProject-vision-engineering-annotations.json"],
                "observed_refdes": ["Q2", "R68", "R69", "R70"],
                "observed_nets": ["V24P0", "P20"],
                "missing_information": ["exact gate-source voltage", "load current"],
                "recommended_next_check": "Cross-check schematic and datasheet constraints before any final claim.",
                "confidence": 0.7,
                "promotion_eligibility": "not_verified",
                "final_finding_allowed": False,
                "notes": [],
            }
        ],
        "blocked_verification_candidates": [
            {
                "candidate_id": "bv_001",
                "candidate_type": "blocked_verification_candidate",
                "title": "Impedance cannot be verified",
                "statement": "Impedance cannot be verified because stackup evidence lacks impedance rules. Do not report impedance failure.",
                "engineering_basis": "Controlled impedance requirements are not present in stackup evidence.",
                "evidence_refs": ["exports/TestProject-stackup-evidence-review.json"],
                "source_artifacts": ["exports/TestProject-stackup-evidence-review.json"],
                "missing_information": ["controlled impedance requirements", "fabrication stackup constraints"],
                "recommended_next_check": "Request impedance requirements or fabrication stackup constraints.",
                "confidence": 0.8,
                "promotion_eligibility": "blocked",
                "final_finding_allowed": False,
                "notes": [],
            }
        ],
        "datasheet_check_candidates": [],
        "calculation_candidates": [
            {
                "candidate_id": "calc_001",
                "candidate_type": "calculation_candidate",
                "title": "Regulator thermal margin needs deterministic calculation",
                "statement": "Regulator thermal margin requires deterministic calculation.",
                "engineering_basis": "Missing branch current prevents thermal/current-density final finding.",
                "evidence_refs": ["exports/TestProject-bom-evidence-inventory.json"],
                "source_artifacts": ["exports/TestProject-bom-evidence-inventory.json"],
                "missing_information": ["Vin", "Vout", "output current", "package thermal resistance", "ambient assumption", "copper area"],
                "recommended_next_check": "Run deterministic thermal calculation after current data is available.",
                "confidence": 0.65,
                "promotion_eligibility": "needs_calculation",
                "final_finding_allowed": False,
                "notes": [],
            }
        ],
        "human_review_candidates": [],
        "rejected_or_unsupported_candidates": [
            {
                "candidate_id": "rej_001",
                "candidate_type": "rejected_or_unsupported_candidate",
                "title": "Generic visual claim rejected",
                "statement": "Routing verified.",
                "engineering_basis": "Generic visual claim lacks page-specific evidence.",
                "evidence_refs": [],
                "source_artifacts": [],
                "promotion_eligibility": "rejected",
                "final_finding_allowed": False,
                "notes": [],
            }
        ],
        "summary": {
            "verified_finding_candidate_count": 0,
            "engineering_concern_candidate_count": 1,
            "blocked_verification_candidate_count": 1,
            "datasheet_check_candidate_count": 0,
            "calculation_candidate_count": 1,
            "human_review_candidate_count": 0,
            "rejected_or_unsupported_candidate_count": 1,
        },
        "blockers": [],
        "warnings": [],
    }
    artifact.update(overrides)
    return artifact


def test_ensure_checkpoint_writes_one_jsonl_line(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "ensure_phase_checkpoint.py"),
            "--project",
            "example",
            "--phase",
            "1",
            "--exports",
            str(tmp_path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert "phase_passed=True" in result.stdout

    checkpoint = tmp_path / "example-phase-checkpoints.jsonl"
    physical_lines = checkpoint.read_text(encoding="utf-8").splitlines()
    assert len(physical_lines) == 1
    row = json.loads(physical_lines[0])
    assert row["phase_number"] == 1
    assert row["phase_passed"] is True
    assert row["repair_required"] is False
    assert row["blockers"] == []


def test_replace_mode_recovers_from_pretty_printed_checkpoint(tmp_path: Path) -> None:
    checkpoint = tmp_path / "example-phase-checkpoints.jsonl"
    checkpoint.write_text(
        json.dumps(
            {
                "phase_number": 1,
                "phase_name": "Ingest ThomsonLint Workflow",
                "phase_passed": True,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "ensure_phase_checkpoint.py"),
            "--project",
            "example",
            "--phase",
            "1",
            "--exports",
            str(tmp_path),
            "--mode",
            "replace",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )

    physical_lines = checkpoint.read_text(encoding="utf-8").splitlines()
    assert len(physical_lines) == 1
    assert json.loads(physical_lines[0])["phase_number"] == 1


def test_phase_maps_cover_1_through_22() -> None:
    modules = [
        load_module(ROOT / "scripts" / "write_phase_prompt.py"),
        load_module(ROOT / "scripts" / "ensure_phase_checkpoint.py"),
        load_module(ROOT / "scripts" / "audit_phase.py"),
    ]
    expected = set(range(1, 23))
    phase_maps = [module.PHASES for module in modules]
    for phase_map in phase_maps:
        assert set(phase_map) == expected
    assert phase_maps[0] == phase_maps[1] == phase_maps[2]


def test_default_phase_prompt_omits_engineering_assessment_mode(tmp_path: Path) -> None:
    prompt = write_phase_prompt(tmp_path, 13)

    assert "Engineering Assessment Mode:" not in prompt
    assert "engineering_concern_candidate" not in prompt
    assert "blocked_verification_candidate" not in prompt


def test_strict_profile_omits_engineering_assessment_mode(tmp_path: Path) -> None:
    prompt = write_phase_prompt(tmp_path, 18, "strict")

    assert "Engineering Assessment Mode:" not in prompt
    assert "datasheet_check_needed" not in prompt
    assert "human_review_question" not in prompt


def test_balanced_profile_adds_engineering_assessment_to_phases_13_to_19(tmp_path: Path) -> None:
    for phase in range(13, 20):
        prompt = write_phase_prompt(tmp_path, phase, "balanced")

        assert "Engineering Assessment Mode:" in prompt
        assert "Assessment profile: balanced" in prompt
        assert "engineering_concern_candidate" in prompt
        assert "blocked_verification_candidate" in prompt
        assert "datasheet_check_needed" in prompt
        assert "human_review_question" in prompt
        assert "calculation_needed" in prompt
        assert "verified facts" in prompt
        assert "engineering observations" in prompt
        assert "hypotheses" in prompt
        assert "blocked verifications" in prompt
        assert "final findings" in prompt
        assert "Verify regulator load current and thermal dissipation; evidence page/refdes; missing current." in prompt
        assert "Regulator fails thermal check" in prompt
        assert "Only verified, evidence-backed items may become final findings." in prompt
        assert "must remain separately classified" in prompt


def test_engineering_profile_adds_assessment_only_to_target_phases(tmp_path: Path) -> None:
    phase12 = write_phase_prompt(tmp_path, 12, "engineering")
    phase14 = write_phase_prompt(tmp_path, 14, "engineering")
    phase20 = write_phase_prompt(tmp_path, 20, "engineering")

    assert "Engineering Assessment Mode:" not in phase12
    assert "Engineering Assessment Mode:" in phase14
    assert "Assessment profile: engineering" in phase14
    assert "Engineering Assessment Mode:" not in phase20


def test_engineering_profile_phase13_prompt_names_vision_annotation_artifact(tmp_path: Path) -> None:
    prompt = write_phase_prompt(tmp_path, 13, "engineering")

    assert "exports/example-vision-engineering-annotations.json" in prompt
    assert "Engineering annotations are not final findings" in prompt
    assert "Reject or record generic visual claims" in prompt
    assert "Do not make exact geometry claims" in prompt


def test_phase13_strict_profile_required_artifacts_exclude_vision_annotations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ensure = load_module(ROOT / "scripts" / "ensure_phase_checkpoint.py")
    monkeypatch.delenv("THOMSONLINT_ASSESSMENT_PROFILE", raising=False)

    artifacts = ensure.phase_artifact_templates(13)

    assert "exports/{project}-image-evidence-review.json" in artifacts
    assert "exports/{project}-vision-engineering-annotations.json" not in artifacts


def test_phase13_engineering_profile_required_artifacts_include_vision_annotations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ensure = load_module(ROOT / "scripts" / "ensure_phase_checkpoint.py")
    monkeypatch.setenv("THOMSONLINT_ASSESSMENT_PROFILE", "engineering")

    artifacts = ensure.phase_artifact_templates(13)

    assert "exports/{project}-vision-engineering-annotations.json" in artifacts


def test_phase18_strict_profile_preserves_candidate_artifact_compatibility(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ensure = load_module(ROOT / "scripts" / "ensure_phase_checkpoint.py")
    monkeypatch.delenv("THOMSONLINT_ASSESSMENT_PROFILE", raising=False)
    path = tmp_path / "TestProject-candidate-findings.json"
    path.write_text(json.dumps({"legacy_candidates": []}), encoding="utf-8")

    ok, blockers = ensure.artifact_passes(path, 18)

    assert ok is True
    assert blockers == []


def test_phase18_engineering_prompt_includes_expanded_candidate_categories(tmp_path: Path) -> None:
    prompt = write_phase_prompt(tmp_path, 18, "engineering")

    assert "exports/example-candidate-findings.json" in prompt
    assert "exports/example-vision-engineering-annotations.json" in prompt
    assert "verified_finding_candidates" in prompt
    assert "engineering_concern_candidates" in prompt
    assert "blocked_verification_candidates" in prompt
    assert "datasheet_check_candidates" in prompt
    assert "calculation_candidates" in prompt
    assert "human_review_candidates" in prompt
    assert "rejected_or_unsupported_candidates" in prompt
    assert "Regulator fails thermal check" in prompt
    assert "Impedance cannot be verified because stackup evidence lacks impedance rules" in prompt


def test_phase18_engineering_audit_accepts_zero_verified_with_concerns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit = load_module(ROOT / "scripts" / "audit_phase.py")
    monkeypatch.setenv("THOMSONLINT_ASSESSMENT_PROFILE", "engineering")
    path = tmp_path / "TestProject-candidate-findings.json"
    path.write_text(json.dumps(phase18_expanded_candidate_artifact()), encoding="utf-8")
    checkpoint = tmp_path / "TestProject-phase-checkpoints.jsonl"
    checkpoint.write_text(json.dumps(checkpoint_row(18, "Candidate Finding Development")) + "\n", encoding="utf-8")

    audit.audit_phase(tmp_path, "TestProject", 18)


def test_phase18_engineering_checkpoint_accepts_blocked_impedance_and_missing_current_categories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ensure = load_module(ROOT / "scripts" / "ensure_phase_checkpoint.py")
    monkeypatch.setenv("THOMSONLINT_ASSESSMENT_PROFILE", "engineering")
    path = tmp_path / "TestProject-candidate-findings.json"
    artifact = phase18_expanded_candidate_artifact()
    path.write_text(json.dumps(artifact), encoding="utf-8")

    ok, blockers = ensure.artifact_passes(path, 18)

    assert ok is True
    assert blockers == []
    assert artifact["verified_finding_candidates"] == []
    assert artifact["blocked_verification_candidates"][0]["candidate_type"] == "blocked_verification_candidate"
    assert artifact["calculation_candidates"][0]["candidate_type"] == "calculation_candidate"


def test_phase18_engineering_rejects_unsupported_final_style_verified_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ensure = load_module(ROOT / "scripts" / "ensure_phase_checkpoint.py")
    monkeypatch.setenv("THOMSONLINT_ASSESSMENT_PROFILE", "engineering")
    path = tmp_path / "TestProject-candidate-findings.json"
    artifact = phase18_expanded_candidate_artifact(
        verified_finding_candidates=[
            {
                "candidate_id": "vf_001",
                "candidate_type": "verified_finding_candidate",
                "title": "Regulator fails thermal check",
                "statement": "Regulator fails thermal check.",
                "engineering_basis": "No deterministic calculation cited.",
                "evidence_refs": ["exports/TestProject-vision-engineering-annotations.json"],
                "source_artifacts": ["exports/TestProject-vision-engineering-annotations.json"],
                "final_finding_allowed": False,
            }
        ],
        summary={
            "verified_finding_candidate_count": 1,
            "engineering_concern_candidate_count": 1,
            "blocked_verification_candidate_count": 1,
            "datasheet_check_candidate_count": 0,
            "calculation_candidate_count": 1,
            "human_review_candidate_count": 0,
            "rejected_or_unsupported_candidate_count": 1,
        },
    )
    path.write_text(json.dumps(artifact), encoding="utf-8")

    ok, blockers = ensure.artifact_passes(path, 18)

    assert ok is False
    assert any("unsupported final-style claim" in blocker for blocker in blockers)


def test_phase18_engineering_rejects_generic_visual_verified_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ensure = load_module(ROOT / "scripts" / "ensure_phase_checkpoint.py")
    monkeypatch.setenv("THOMSONLINT_ASSESSMENT_PROFILE", "engineering")
    path = tmp_path / "TestProject-candidate-findings.json"
    artifact = phase18_expanded_candidate_artifact(
        verified_finding_candidates=[
            {
                "candidate_id": "vf_002",
                "candidate_type": "verified_finding_candidate",
                "title": "Routing verified",
                "statement": "Routing verified.",
                "engineering_basis": "Generic visual claim.",
                "evidence_refs": ["exports/TestProject-vision-engineering-annotations.json"],
                "source_artifacts": ["exports/TestProject-vision-engineering-annotations.json"],
                "final_finding_allowed": False,
            }
        ],
        summary={
            "verified_finding_candidate_count": 1,
            "engineering_concern_candidate_count": 1,
            "blocked_verification_candidate_count": 1,
            "datasheet_check_candidate_count": 0,
            "calculation_candidate_count": 1,
            "human_review_candidate_count": 0,
            "rejected_or_unsupported_candidate_count": 1,
        },
    )
    path.write_text(json.dumps(artifact), encoding="utf-8")

    ok, blockers = ensure.artifact_passes(path, 18)

    assert ok is False
    assert any("generic visual claim" in blocker for blocker in blockers)


def test_phase11_validation_gate_allows_recorded_dfm_violations(tmp_path: Path) -> None:
    ensure = load_module(ROOT / "scripts" / "ensure_phase_checkpoint.py")
    validation = tmp_path / "example-dfm-evidence-inventory-validation.json"
    validation.write_text(
        json.dumps(
            {
                "phase": 11,
                "geometry_helpers_dfm_executed": True,
                "all_required_sections_present": True,
                "overall_pass": False,
                "validation_passed": True,
                "phase_gate_passed": True,
                "errors": [],
            }
        ),
        encoding="utf-8",
    )

    ok, blockers = ensure.artifact_passes(validation, 11)

    assert ok is True
    assert blockers == []


def test_phase11_audit_requires_required_dfm_checks(tmp_path: Path) -> None:
    audit = load_module(ROOT / "scripts" / "audit_phase.py")
    exports = tmp_path
    project = "example"
    inventory = exports / f"{project}-dfm-evidence-inventory.json"
    validation = exports / f"{project}-dfm-evidence-inventory-validation.json"
    inventory.write_text(
        json.dumps(
            {
                "phase": 11,
                "phase_name": "Review DFM and Manufacturing Specifications",
                "project": project,
                "geometry_helpers_dfm_results": {
                    "annular_ring": {},
                    "acid_traps": {},
                    "board_edge_clearance": {},
                    "copper_balance": {},
                    "voltage_spacing": {},
                },
                "summary": {
                    "geometry_helpers_dfm_executed": True,
                    "checks_run": [
                        "annular_ring",
                        "acid_traps",
                        "board_edge_clearance",
                        "copper_balance",
                        "voltage_spacing",
                    ],
                    "overall_pass": False,
                    "failing_checks": ["acid_traps"],
                    "total_violations": 1,
                },
            }
        ),
        encoding="utf-8",
    )
    validation.write_text(
        json.dumps(
            {
                "phase": 11,
                "geometry_helpers_dfm_executed": True,
                "overall_pass": False,
                "validation_passed": True,
                "phase_gate_passed": True,
                "errors": [],
            }
        ),
        encoding="utf-8",
    )

    audit.audit_phase11_dfm_gate(exports, project)

    data = json.loads(inventory.read_text(encoding="utf-8"))
    del data["geometry_helpers_dfm_results"]["voltage_spacing"]
    inventory.write_text(json.dumps(data), encoding="utf-8")

    try:
        audit.audit_phase11_dfm_gate(exports, project)
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("missing DFM checks should fail Phase 11 audit")


def test_phase11_audit_accepts_validation_execution_gate_without_summary_flag(tmp_path: Path) -> None:
    audit = load_module(ROOT / "scripts" / "audit_phase.py")
    exports = tmp_path
    project = "sunrise"
    inventory = exports / f"{project}-dfm-evidence-inventory.json"
    validation = exports / f"{project}-dfm-evidence-inventory-validation.json"
    inventory.write_text(
        json.dumps(
            {
                "phase": 11,
                "phase_name": "Review DFM and Manufacturing Specifications",
                "project": project,
                "geometry_helpers_dfm_results": {
                    "annular_ring": {},
                    "acid_traps": {},
                    "board_edge_clearance": {},
                    "copper_balance": {},
                    "voltage_spacing": {},
                },
                "summary": {
                    "total_checks_run": 5,
                    "checks_with_violations": ["acid_traps"],
                    "checks_passed": ["annular_ring"],
                },
            }
        ),
        encoding="utf-8",
    )
    validation.write_text(
        json.dumps(
            {
                "phase": 11,
                "phase_gate_passed": True,
                "execution_pass": True,
                "artifact_validation_pass": True,
                "required_checks_executed": True,
                "checks_recorded": True,
                "dfm_compliance_pass": False,
                "violations_found": True,
            }
        ),
        encoding="utf-8",
    )

    audit.audit_phase11_dfm_gate(exports, project)


def test_phase16_audit_allows_recorded_cross_source_discrepancies(tmp_path: Path) -> None:
    audit = load_module(ROOT / "scripts" / "audit_phase.py")
    exports = tmp_path
    project = "sunrise"
    review = exports / f"{project}-cross-source-review.json"
    validation = exports / f"{project}-cross-source-review-validation.json"
    review.write_text(
        json.dumps(
            {
                "phase": 16,
                "phase_name": "Cross-Source Consistency Review",
                "project": project,
                "checks": {
                    "refdes_reconciliation": {"status": "fail"},
                    "package_mismatches": {"status": "pass"},
                    "netlist_integrity": {"status": "fail"},
                    "voltage_derating": {"status": "fail"},
                },
                "cross_source_observations": [{"category": "BOM vs schematic"}],
                "downstream_constraints": ["preserve discrepancy for findings phase"],
                "gate": {"overall_pass": False},
            }
        ),
        encoding="utf-8",
    )
    validation.write_text(
        json.dumps(
            {
                "phase": 16,
                "overall_pass": False,
                "validations": [
                    {"check": "artifact_json_parses", "passed": True},
                    {"check": "top_level_key_findings_absent", "passed": True},
                    {"check": "no_key_finding_anywhere", "passed": True},
                    {"check": "no_key_severity_anywhere", "passed": True},
                    {"check": "no_key_rule_id_anywhere", "passed": True},
                    {"check": "required_checks_executed", "passed": True},
                    {"check": "cross_source_observations_present", "passed": True},
                    {"check": "downstream_constraints_present", "passed": True},
                ],
            }
        ),
        encoding="utf-8",
    )

    audit.audit_phase16_cross_source_gate(exports, project)


def test_phase17_accepts_phase16_execution_pass_with_cross_source_discrepancies(tmp_path: Path) -> None:
    audit = load_module(ROOT / "scripts" / "audit_phase.py")
    exports = tmp_path
    project = "sunrise"

    checkpoints = [
        checkpoint_row(phase, audit.PHASES[phase])
        for phase in range(8, 18)
    ]
    (exports / f"{project}-phase-checkpoints.jsonl").write_text(
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in checkpoints),
        encoding="utf-8",
    )
    (exports / f"{project}-cross-source-review.json").write_text(
        json.dumps(
            {
                "phase": 16,
                "phase_name": "Cross-Source Consistency Review",
                "project": project,
                "checks": {
                    "refdes_reconciliation": {"status": "FAIL"},
                    "package_mismatches": {"status": "PASS"},
                    "netlist_integrity": {"status": "FAIL"},
                    "voltage_derating": {"status": "FAIL"},
                },
                "cross_source_observations": [{"category": "BOM vs schematic"}],
                "gate": {"overall_pass": False},
            }
        ),
        encoding="utf-8",
    )
    (exports / f"{project}-cross-source-review-validation.json").write_text(
        json.dumps(
            {
                "phase": 16,
                "overall_pass": False,
                "phase_gate_passed": True,
                "execution_pass": True,
                "cross_source_consistency_pass": False,
                "artifact_validation_pass": True,
            }
        ),
        encoding="utf-8",
    )
    (exports / f"{project}-pre-findings-gate.json").write_text(
        json.dumps(
            {
                "phase": 17,
                "phase_name": "Pre-Findings Gate Check",
                "project": project,
                "blockers": [],
                "overall_gate_pass": True,
            }
        ),
        encoding="utf-8",
    )

    audit.audit_phase17_pre_findings_gate(exports, project)


def test_shell_scripts_do_not_contain_crlf() -> None:
    for path in sorted((ROOT / "scripts").glob("*.sh")):
        data = path.read_bytes()
        assert b"\r\n" not in data, f"{path} contains CRLF line endings"
        assert data.startswith(b"#!/usr/bin/env bash\n"), f"{path} has an invalid shebang"
