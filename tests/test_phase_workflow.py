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


def minimal_findings(project: str = "TestProject") -> dict:
    return {
        "project_name": project,
        "review_date": "2026-06-03",
        "issues": [],
        "verified_checks": [],
        "cross_checks": [],
    }


def run_gen_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    profile: str | None = None,
    candidate: dict | None = None,
) -> subprocess.CompletedProcess[str]:
    exports = tmp_path / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    findings = exports / "TestProject-findings.json"
    findings.write_text(json.dumps(minimal_findings()), encoding="utf-8")
    if candidate is not None:
        (exports / "TestProject-candidate-findings.json").write_text(json.dumps(candidate), encoding="utf-8")
    env = os.environ.copy()
    env.pop("THOMSONLINT_ASSESSMENT_PROFILE", None)
    if profile:
        env["THOMSONLINT_ASSESSMENT_PROFILE"] = profile
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "gen_report.py"),
            str(findings),
            "--output",
            str(exports),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        env=env,
    )


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
        "datasheet_check_candidates": [
            {
                "candidate_id": "ds_001",
                "candidate_type": "datasheet_check_candidate",
                "title": "Check regulator capacitor requirements",
                "statement": "Review U1 datasheet for input and output capacitor requirements.",
                "engineering_basis": "Vision annotation suggests a regulator section but capacitor requirements are unverified.",
                "evidence_refs": ["exports/TestProject-vision-engineering-annotations.json#annotation-1"],
                "source_artifacts": ["exports/TestProject-vision-engineering-annotations.json"],
                "observed_refdes": ["U1"],
                "missing_information": ["U1 datasheet capacitor requirements"],
                "recommended_next_check": "Open U1 datasheet and record required capacitor values and ESR constraints.",
                "confidence": 0.6,
                "promotion_eligibility": "needs_datasheet",
                "final_finding_allowed": False,
                "notes": [],
            }
        ],
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
        "human_review_candidates": [
            {
                "candidate_id": "hr_001",
                "candidate_type": "human_review_candidate",
                "title": "Confirm regulator load ownership",
                "statement": "Confirm whether U1 supplies all 3V3 loads on the annotated page.",
                "engineering_basis": "Load ownership is ambiguous from image-only evidence.",
                "evidence_refs": ["exports/TestProject-vision-engineering-annotations.json#annotation-1"],
                "source_artifacts": ["exports/TestProject-vision-engineering-annotations.json"],
                "observed_refdes": ["U1"],
                "observed_nets": ["3V3"],
                "missing_information": ["load ownership"],
                "recommended_next_check": "Engineer should confirm load tree before final thermal/current claims.",
                "confidence": 0.55,
                "promotion_eligibility": "human_review",
                "final_finding_allowed": False,
                "notes": [],
            }
        ],
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
            "datasheet_check_candidate_count": 1,
            "calculation_candidate_count": 1,
            "human_review_candidate_count": 1,
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
            "datasheet_check_candidate_count": 1,
            "calculation_candidate_count": 1,
            "human_review_candidate_count": 1,
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
            "datasheet_check_candidate_count": 1,
            "calculation_candidate_count": 1,
            "human_review_candidate_count": 1,
            "rejected_or_unsupported_candidate_count": 1,
        },
    )
    path.write_text(json.dumps(artifact), encoding="utf-8")

    ok, blockers = ensure.artifact_passes(path, 18)

    assert ok is False
    assert any("generic visual claim" in blocker for blocker in blockers)


def test_report_strict_profile_preserves_existing_behavior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = run_gen_report(tmp_path, monkeypatch)

    exports = tmp_path / "exports"
    assert result.returncode == 0, result.stderr
    assert (exports / "TestProject-review.html").exists()
    assert not (exports / "TestProject-engineering-assessment-report-sections.json").exists()
    assert '"assessment_profile": "strict"' in (exports / "TestProject-review.html").read_text(encoding="utf-8")


def test_report_engineering_profile_creates_separate_assessment_sections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = run_gen_report(tmp_path, monkeypatch, profile="engineering", candidate=phase18_expanded_candidate_artifact())

    exports = tmp_path / "exports"
    sections = json.loads((exports / "TestProject-engineering-assessment-report-sections.json").read_text(encoding="utf-8"))
    html = (exports / "TestProject-review.html").read_text(encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert sections["summary"]["verified_findings_count"] == 0
    assert sections["summary"]["engineering_concerns_count"] == 1
    assert sections["summary"]["blocked_verifications_count"] == 1
    assert sections["summary"]["datasheet_checks_needed_count"] == 1
    assert sections["summary"]["calculations_needed_count"] == 1
    assert sections["summary"]["human_review_questions_count"] == 1
    assert sections["engineering_concerns"][0]["summary"] == "Apparent load-switching section needs review"
    assert sections["blocked_verifications"][0]["summary"] == "Impedance cannot be verified"
    assert sections["verified_findings"] == []
    assert "Engineering Concerns" in html
    assert "Blocked Verifications" in html
    assert "Datasheet Checks Needed" in html
    assert "Calculations Needed" in html
    assert "Human Review Questions" in html


def test_report_engineering_sections_do_not_promote_concerns_to_verified_findings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = run_gen_report(tmp_path, monkeypatch, profile="balanced", candidate=phase18_expanded_candidate_artifact())

    sections = json.loads((tmp_path / "exports" / "TestProject-engineering-assessment-report-sections.json").read_text(encoding="utf-8"))
    concern_text = json.dumps(sections["engineering_concerns"])
    verified_text = json.dumps(sections["verified_findings"])
    assert result.returncode == 0, result.stderr
    assert "V24P0 high-side PMOS" in concern_text
    assert "V24P0 high-side PMOS" not in verified_text
    assert "Impedance cannot be verified" in json.dumps(sections["blocked_verifications"])
    assert "Impedance cannot be verified" not in verified_text


def test_report_engineering_rejects_unsupported_final_style_verified_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = phase18_expanded_candidate_artifact(
        verified_finding_candidates=[
            {
                "candidate_id": "vf_bad",
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
            "datasheet_check_candidate_count": 1,
            "calculation_candidate_count": 1,
            "human_review_candidate_count": 1,
            "rejected_or_unsupported_candidate_count": 1,
        },
    )

    result = run_gen_report(tmp_path, monkeypatch, profile="engineering", candidate=candidate)

    assert result.returncode == 1
    assert "unsupported final-style claim" in result.stderr


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


# =============================================================================
# Phase 13 Vision Review / Annotation Completeness Regression Tests (PR-T2B)
# =============================================================================

def _make_inventory(exports: Path, project: str, schematic_count: int, layout_count: int) -> None:
    """Create a minimal inventory file with the given number of images."""
    schematic_pngs = [f"{project}-img-sch-p{i}.png" for i in range(1, schematic_count + 1)]
    layout_pngs = [f"{project}-img-layout-p{j}.png" for j in range(1, layout_count + 1)]
    output_files = []
    for s in schematic_pngs:
        output_files.append({"file": s})
    for l in layout_pngs:
        output_files.append({"path": l})
    inv = {
        "project": project,
        "schematic_pngs": schematic_pngs,
        "layout_pngs": layout_pngs,
        "output_files": output_files,
    }
    (exports / f"{project}-image-evidence-inventory.json").write_text(
        json.dumps(inv, indent=2), encoding="utf-8"
    )


def _make_review_observation(file_name: str, page_actually_opened: bool = True) -> dict:
    """Create a valid observation dict for testing."""
    return {
        "file": file_name,
        "kind": "schematic",
        "model": "test-model",
        "raw_response_path": f"raw/{Path(file_name).name}.attempt1.txt",
        "raw_response_paths": [f"raw/{Path(file_name).name}.attempt1.txt"],
        "response": {
            "image_id": Path(file_name).name,
            "source_file": file_name,
            "page_number": 1,
            "page_type": "schematic",
            "visual_review_performed": True,
            "confirmation_no_pixel_quantitative_claims": True,
            "page_actually_opened": page_actually_opened,
            "actual_image_review_performed": page_actually_opened,
            "errors": [],
            "warnings": [],
        },
    }


def _make_failed_observation(file_name: str) -> dict:
    """Create an observation with page_actually_opened=False."""
    return {
        "file": file_name,
        "kind": "schematic",
        "model": "test-model",
        "raw_response_path": f"raw/{Path(file_name).name}.attempt1.txt",
        "raw_response_paths": [f"raw/{Path(file_name).name}.attempt1.txt"],
        "response": {
            "image_id": Path(file_name).name,
            "source_file": file_name,
            "page_number": 1,
            "page_type": "schematic",
            "visual_review_performed": True,
            "confirmation_no_pixel_quantitative_claims": True,
            "page_actually_opened": False,
            "actual_image_review_performed": False,
            "errors": [],
            "warnings": [],
        },
    }


def test_phase13_completeness_partial_opened_fails(tmp_path: Path) -> None:
    """Test A: Inventory has 38 images. Review artifact has per_page_vision_observations length 38, but only 19 page_actually_opened true. Validation must fail and list the 19 invalid/missing IDs."""
    import scripts.vision_image_review as vr

    exports = tmp_path / "exports"
    exports.mkdir()
    project = "TestProject"

    # Create inventory with 38 images (18 schematic + 20 layout)
    _make_inventory(exports, project, 18, 20)

    # Verify canonical ID extraction works
    canonical_ids = vr.expected_image_ids_from_inventory(exports, project)
    assert len(canonical_ids) == 38, f"Expected 38 canonical IDs, got {len(canonical_ids)}"

    # Create observations: exactly 19 with page_actually_opened=True, 19 with False
    # Schematic p1-p9 True (9), p10-p18 False (9) = 18 total
    # Layout p1-p10 True (10), p11-p20 False (10) = 20 total
    # Total True = 9 + 10 = 19, Total images = 38
    observations = []
    for i in range(1, 19):
        obs = _make_review_observation(f"{project}-img-sch-p{i}.png", page_actually_opened=(i <= 9))
        observations.append(obs)
    for j in range(1, 21):
        obs = _make_review_observation(f"{project}-img-layout-p{j}.png", page_actually_opened=(j <= 10))
        observations.append(obs)

    artifact = vr.artifact_for(
        project=project,
        base_url="http://test",
        model="test-model",
        expected=38,
        observations=observations,
        errors=[],
    )

    assert artifact["reviewed_image_count"] == 19, f"Expected reviewed_image_count=19, got {artifact['reviewed_image_count']}"
    assert artifact["pages_actually_opened_count"] == 19, f"Expected pages_actually_opened_count=19, got {artifact['pages_actually_opened_count']}"
    assert artifact["overall_pass"] is False, "Overall pass should be False when not all pages opened"
    assert artifact["phase_13_completed"] is False, "Phase 13 should not be completed"
    assert len(artifact.get("failed_or_missing_ids", [])) > 0, "Should list failed/missing IDs"
    assert len(artifact["invalid_records"]) == 19
    assert artifact["missing_image_ids"] == []


def test_phase13_completeness_all_opened_passes(tmp_path: Path) -> None:
    """Test B: Simulated successful model responses for 38 images. Review validation passes with reviewed_image_count == 38, pages_actually_opened_count == 38, overall_pass == True."""
    import scripts.vision_image_review as vr

    exports = tmp_path / "exports"
    exports.mkdir()
    project = "TestProject"

    _make_inventory(exports, project, 18, 20)

    canonical_ids = vr.expected_image_ids_from_inventory(exports, project)
    assert len(canonical_ids) == 38

    # Create observations: all with page_actually_opened=True
    observations = []
    for i in range(1, 19):
        obs = _make_review_observation(f"{project}-img-sch-p{i}.png", page_actually_opened=True)
        observations.append(obs)
    for j in range(1, 21):
        obs = _make_review_observation(f"{project}-img-layout-p{j}.png", page_actually_opened=True)
        observations.append(obs)

    artifact = vr.artifact_for(
        project=project,
        base_url="http://test",
        model="test-model",
        expected=38,
        observations=observations,
        errors=[],
    )

    assert artifact["reviewed_image_count"] == 38, f"Expected reviewed_image_count=38, got {artifact['reviewed_image_count']}"
    assert artifact["pages_actually_opened_count"] == 38, f"Expected pages_actually_opened_count=38, got {artifact['pages_actually_opened_count']}"
    assert artifact["overall_pass"] is True, "Overall pass should be True when all images reviewed and opened"
    assert artifact["phase_13_completed"] is True, "Phase 13 should be completed"
    assert len(artifact["valid_image_ids"]) == 38
    assert artifact["invalid_image_ids"] == []
    assert artifact["invalid_records"] == []


def test_phase13_latest_run_shape_without_nested_self_attestation_passes(tmp_path: Path) -> None:
    """38 canonical rows with top-level runtime facts pass even without nested model self-attestation."""
    import scripts.vision_image_review as vr

    exports = tmp_path / "exports"
    exports.mkdir()
    project = "TestProject"
    _make_inventory(exports, project, 18, 20)
    canonical_ids = vr.expected_image_ids_from_inventory(exports, project)

    observations = []
    for img_id in canonical_ids:
        obs = _make_review_observation(img_id, page_actually_opened=True)
        obs["page_actually_opened"] = True
        obs["actual_image_review_performed"] = True
        obs["parse_status"] = "parsed"
        obs["validation_status"] = "passed"
        obs["response"].pop("visual_review_performed", None)
        obs["response"].pop("confirmation_no_pixel_quantitative_claims", None)
        obs["response"].pop("page_actually_opened", None)
        obs["response"].pop("actual_image_review_performed", None)
        obs["response"]["brief_description"] = "Reviewed visible page content without pixel-derived measurements."
        observations.append(obs)

    artifact = vr.artifact_for(
        project=project,
        base_url="http://test",
        model="test-model",
        expected=38,
        observations=observations,
        errors=[],
        expected_image_ids=canonical_ids,
    )
    annotations = vr.annotations_artifact_for(
        project=project,
        assessment_profile="engineering",
        base_artifact=artifact,
        expected_image_ids=canonical_ids,
    )

    assert artifact["overall_pass"] is True
    assert artifact["phase_13_completed"] is True
    assert artifact["reviewed_image_count"] == 38
    assert artifact["pages_actually_opened_count"] == 38
    assert artifact["invalid_image_ids"] == []
    assert artifact["missing_image_ids"] == []
    assert artifact["invalid_records"] == []
    assert annotations["annotation_count"] == 38


def test_phase13_validation_lists_fourteen_invalid_records(tmp_path: Path) -> None:
    """Review validation with 38 rows and 14 invalid rows explains every invalid record."""
    import scripts.vision_image_review as vr

    exports = tmp_path / "exports"
    exports.mkdir()
    project = "TestProject"
    _make_inventory(exports, project, 18, 20)
    canonical_ids = vr.expected_image_ids_from_inventory(exports, project)

    observations = []
    for idx, img_id in enumerate(canonical_ids):
        observations.append(_make_review_observation(img_id, page_actually_opened=idx >= 14))

    artifact = vr.artifact_for(
        project=project,
        base_url="http://test",
        model="test-model",
        expected=38,
        observations=observations,
        errors=[],
        expected_image_ids=canonical_ids,
    )

    assert artifact["overall_pass"] is False
    assert artifact["reviewed_image_count"] == 24
    assert artifact["pages_actually_opened_count"] == 24
    assert len(artifact["invalid_records"]) == 14
    assert len(artifact["invalid_image_ids"]) == 14
    assert artifact["missing_image_ids"] == []
    assert all(record["reason"] for record in artifact["invalid_records"])


def test_phase13_validation_gap_is_explained_by_missing_ids(tmp_path: Path) -> None:
    """A reviewed-count gap must be represented in missing_image_ids or invalid_records."""
    import scripts.vision_image_review as vr

    observations = [_make_review_observation(f"TestProject-img-sch-p{i}.png") for i in range(1, 25)]

    artifact = vr.artifact_for(
        project="TestProject",
        base_url="http://test",
        model="test-model",
        expected=38,
        observations=observations,
        errors=[],
    )

    assert artifact["overall_pass"] is False
    assert artifact["reviewed_image_count"] == 24
    assert artifact["reviewed_image_count"] < artifact["expected_image_count"]
    assert artifact["missing_image_ids"] or artifact["invalid_records"]
    assert len(artifact["missing_image_ids"]) == 14


def test_phase13_annotation_repair_warnings_do_not_invalidate_base_review(tmp_path: Path) -> None:
    """Engineering annotation repair warnings stay separate from base image review validity."""
    import scripts.vision_image_review as vr

    project = "TestProject"
    observation = _make_review_observation(f"{project}-img-sch-p1.png", page_actually_opened=True)
    observation["response"]["observed_circuits"] = []
    observation["response"]["observed_refdes"] = []
    observation["response"]["observed_nets"] = []
    artifact = vr.artifact_for(
        project=project,
        base_url="http://test",
        model="test-model",
        expected=1,
        observations=[observation],
        errors=[],
        expected_image_ids=[f"{project}-img-sch-p1.png"],
    )

    annotations = vr.annotations_artifact_for(
        project=project,
        assessment_profile="engineering",
        base_artifact=artifact,
        expected_image_ids=[f"{project}-img-sch-p1.png", f"{project}-img-sch-p2.png"],
    )

    assert artifact["overall_pass"] is True
    assert artifact["per_page_vision_observations"][0]["validation_status"] == "passed"
    assert annotations["minimal_repaired_count"] == 1
    assert annotations["warnings"]


def _make_annotations_artifact(
    project: str,
    profile: str,
    observations: list[dict],
    expected_count: int,
    overall_pass: bool = True,
    canonical_ids: list[str] | None = None,
) -> dict:
    """Helper to create an annotations artifact for testing."""
    import scripts.vision_image_review as vr

    base_artifact = {
        "expected_image_count": expected_count,
        "overall_pass": overall_pass,
        "per_page_vision_observations": observations,
    }
    return vr.annotations_artifact_for(
        project=project,
        assessment_profile=profile,
        base_artifact=base_artifact,
        expected_image_ids=canonical_ids,
    )


def test_phase13_annotations_missing_fails(tmp_path: Path) -> None:
    """Test C: Engineering profile with 38 review records but only 19 annotations. Annotation validation fails and lists missing annotation IDs."""
    import scripts.vision_image_review as vr

    exports = tmp_path / "exports"
    exports.mkdir()
    project = "TestProject"

    _make_inventory(exports, project, 18, 20)

    canonical_ids = vr.expected_image_ids_from_inventory(exports, project)
    assert len(canonical_ids) == 38

    # Create observations: all valid (page_actually_opened=True) for exactly the first 19 canonical IDs.
    # canonical_ids order is sorted: schematic p1-p18 then layout p1-p20.
    # We create observations for the first 19 (all 18 schematic + layout p1).
    observations = []
    for img_id in canonical_ids[:19]:
        obs = _make_review_observation(img_id, page_actually_opened=True)
        observations.append(obs)

    ann_artifact = _make_annotations_artifact(
        project=project,
        profile="engineering",
        observations=observations,
        expected_count=38,
        overall_pass=False,  # review didn't pass for all images
        canonical_ids=canonical_ids,
    )

    # In engineering mode with canonical IDs, minimal repaired annotations should be written for missing images
    assert ann_artifact["annotation_count"] == 38, f"Expected annotation_count=38 (with repairs), got {ann_artifact['annotation_count']}"
    assert ann_artifact.get("minimal_repaired_count", 0) == 19, "Should have 19 minimal repaired records"
    # overall_pass should be False because base review didn't pass
    assert ann_artifact["overall_pass"] is False, "Annotation validation should fail when image evidence review did not pass"


def test_phase13_annotations_minimal_repair(tmp_path: Path) -> None:
    """Test D: Engineering profile with missing rich annotation content for some images. Minimal repaired annotation records are written for those images. annotation_count == expected_image_count. overall_pass true if minimal repaired records are allowed by policy. Minimal repaired records do not invent refdes/nets/circuits."""
    import scripts.vision_image_review as vr

    exports = tmp_path / "exports"
    exports.mkdir()
    project = "TestProject"

    _make_inventory(exports, project, 18, 20)

    canonical_ids = vr.expected_image_ids_from_inventory(exports, project)
    assert len(canonical_ids) == 38

    # Create observations: all valid but with no rich annotation content (empty arrays)
    # Use the first 20 canonical IDs so they match exactly.
    observations = []
    for img_id in canonical_ids[:20]:
        obs = _make_review_observation(img_id, page_actually_opened=True)
        # No rich annotation fields - just the bare minimum
        obs["response"]["observed_circuits"] = []
        obs["response"]["observed_refdes"] = []
        obs["response"]["observed_nets"] = []
        observations.append(obs)

    ann_artifact = _make_annotations_artifact(
        project=project,
        profile="engineering",
        observations=observations,
        expected_count=38,
        overall_pass=True,  # review passed for the images we have
        canonical_ids=canonical_ids,
    )

    assert ann_artifact["annotation_count"] == 38, f"Expected annotation_count=38, got {ann_artifact['annotation_count']}"
    minimal_repaired = [a for a in ann_artifact["annotations"] if a.get("validation_status") == "repaired_minimal"]
    assert len(minimal_repaired) > 0, "Should have some minimal repaired records"

    # Verify no fabricated claims in minimal repaired records
    for rec in minimal_repaired:
        assert rec["observed_circuits"] == [], f"Minimal record should not invent circuits: {rec['image_id']}"
        assert rec["observed_refdes"] == [], f"Minimal record should not invent refdes: {rec['image_id']}"
        assert rec["observed_nets"] == [], f"Minimal record should not invent nets: {rec['image_id']}"
        assert "Engineering annotation unavailable or incomplete for this image" in rec.get("blocked_verification_candidates", [])


def test_phase13_resume_completeness_aware(tmp_path: Path) -> None:
    """Test E: Resume skips only images with both valid review and valid annotation records. Images with valid review but missing annotation are repaired/retried."""

    project = "TestProject"
    exports = tmp_path / "exports"
    exports.mkdir()

    # Create an inventory with exactly 6 images (3 schematic + 3 layout)
    _make_inventory(exports, project, 3, 3)

    import scripts.vision_image_review as vr

    canonical_ids = vr.expected_image_ids_from_inventory(exports, project)
    assert len(canonical_ids) == 6

    # Create 3 complete observations (valid review + valid annotation with rich content)
    completed_observations = []
    for i in range(1, 4):
        obs = _make_review_observation(canonical_ids[i - 1], page_actually_opened=True)
        # Add rich annotation content so these get full annotations (not minimal repair)
        obs["response"]["observed_circuits"] = ["Power supply section with U1 regulator"]
        obs["response"]["observed_refdes"] = ["U1", "C1"]
        obs["response"]["observed_nets"] = ["VCC_3V3", "GND"]
        completed_observations.append(obs)

    # Create 2 observations with valid review but no rich annotation content (missing annotation)
    incomplete_observations = []
    for i in range(4, 6):
        obs = _make_review_observation(canonical_ids[i - 1], page_actually_opened=True)
        obs["response"]["observed_circuits"] = []
        obs["response"]["observed_refdes"] = []
        obs["response"]["observed_nets"] = []
        incomplete_observations.append(obs)

    # Add the 6th observation (from canonical_ids[5]) with valid review but no rich content
    obs6 = _make_review_observation(canonical_ids[5], page_actually_opened=True)
    obs6["response"]["observed_circuits"] = []
    obs6["response"]["observed_refdes"] = []
    obs6["response"]["observed_nets"] = []
    incomplete_observations.append(obs6)

    all_observations = completed_observations + incomplete_observations

    ann_artifact = _make_annotations_artifact(
        project=project,
        profile="engineering",
        observations=all_observations,
        expected_count=6,
        overall_pass=True,
        canonical_ids=canonical_ids,
    )

    # All 6 should have annotations (3 from observations + 3 minimal repaired)
    assert ann_artifact["annotation_count"] == 6, f"Expected annotation_count=6, got {ann_artifact['annotation_count']}"

    # Check that completed_image_ids would only include the 3 with valid review AND rich content
    completed_image_ids: set[str] = set()
    for obs in all_observations:
        if not isinstance(obs, dict):
            continue
        resp = obs.get("response")
        if not isinstance(resp, dict):
            continue
        img_id = str(resp.get("image_id", "")) or Path(str(obs.get("file", ""))).name
        normalized = vr.normalize_review_observation(existing=obs)
        if img_id and vr.observation_successful(normalized):
            # Only count as "complete" for resume purposes if it has rich annotation content
            circuits = resp.get("observed_circuits", [])
            refdes = resp.get("observed_refdes", [])
            nets = resp.get("observed_nets", [])
            if isinstance(circuits, list) and isinstance(refdes, list) and isinstance(nets, list):
                if circuits or refdes:  # Has some concrete evidence
                    completed_image_ids.add(img_id)

    assert len(completed_image_ids) == 3, f"Expected 3 complete images for resume skip, got {len(completed_image_ids)}"


def test_phase13_annotation_quality_filtering(tmp_path: Path) -> None:
    """Test F: A generic claim like 'routing verified' is rejected. A confidence 0.0 record with specific concerns is downgraded/rejected unless it contains concrete evidence refs."""
    import scripts.vision_image_review as vr

    # Test generic claim rejection
    generic_claim = "routing verified"
    assert vr.is_generic_vision_claim(generic_claim), f"'{generic_claim}' should be recognized as generic"

    filtered, rejected = vr.filter_annotation_claims([generic_claim])
    assert len(rejected) == 1, "Generic claim should be rejected"
    assert len(filtered) == 0, "No claims should pass filtering"

    # Test confidence 0.0 downgrade
    annotation_with_0_confidence = {
        "image_id": "TestProject-img-sch-p1.png",
        "source_file": "TestProject-img-sch-p1.png",
        "page_number": 1,
        "page_type": "schematic",
        "observed_circuits": ["power distribution network"],
        "observed_refdes": [],
        "observed_nets": [],
        "component_role_observations": [],
        "engineering_concern_candidates": ["U1 regulator output unstable"],  # Has refdes U1 - should pass
        "blocked_verification_candidates": [],
        "datasheet_check_needed": [],
        "calculation_needed": ["check resistor values"],  # No evidence - should be rejected
        "human_review_questions": [],
        "not_verifiable_from_image": [],
        "confidence": 0.0,
    }

    result, warnings = vr.downgrade_low_confidence_annotation(annotation_with_0_confidence)

    # The concern with U1 refdes should pass; the calculation without evidence should be rejected
    assert len(result["engineering_concern_candidates"]) == 1, "U1 concern should pass (has concrete ref)"
    assert len(result["calculation_needed"]) == 0, "Calculation without evidence should be rejected"
    assert any("rejected_generic_calculation" in w for w in warnings), "Should have warning about rejected calculation"

    # Test generic circuit rejection at confidence 0.0
    annotation_with_generic_circuits = {
        "image_id": "TestProject-img-sch-p2.png",
        "source_file": "TestProject-img-sch-p2.png",
        "page_number": 2,
        "page_type": "schematic",
        "observed_circuits": ["power distribution network", "signal routing paths"],
        "observed_refdes": [],
        "observed_nets": [],
        "component_role_observations": [],
        "engineering_concern_candidates": [],
        "blocked_verification_candidates": [],
        "datasheet_check_needed": [],
        "calculation_needed": [],
        "human_review_questions": [],
        "not_verifiable_from_image": [],
        "confidence": 0.0,
    }

    result2, warnings2 = vr.downgrade_low_confidence_annotation(annotation_with_generic_circuits)
    assert len(result2["observed_circuits"]) == 0, f"Generic circuits should be rejected: {result2['observed_circuits']}"
    assert len(warnings2) > 0, "Should have warnings about rejected generic circuits"

    # Test final-style claim rejection
    final_style_claim = "regulator fails thermal check"
    assert vr.is_final_style_claim(final_style_claim), f"'{final_style_claim}' should be recognized as final style"

    annotation_with_final = {
        "image_id": "TestProject-img-sch-p3.png",
        "source_file": "TestProject-img-sch-p3.png",
        "page_number": 3,
        "page_type": "schematic",
        "observed_circuits": [],
        "observed_refdes": [],
        "observed_nets": [],
        "component_role_observations": [],
        "engineering_concern_candidates": [final_style_claim],
        "blocked_verification_candidates": ["connectivity verified"],  # Generic vision claim
        "datasheet_check_needed": [],
        "calculation_needed": [],
        "human_review_questions": [],
        "not_verifiable_from_image": [],
        "confidence": 1.0,
    }

    result3, warnings3 = vr.reject_final_style_claims(annotation_with_final)
    assert len(result3["engineering_concern_candidates"]) == 0, f"Final-style claim should be rejected: {result3['engineering_concern_candidates']}"
    assert len(warnings3) > 0, "Should have warnings about rejected final-style/generic claims"
