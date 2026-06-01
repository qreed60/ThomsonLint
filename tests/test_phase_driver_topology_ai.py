from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import jsonschema
import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import phase_driver  # noqa: E402


SCHEMA = ROOT / "schemas" / "phase_driver_run_schema.json"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def phase_output_dir(out_dir: Path, phase_id: str) -> Path:
    """Find the output directory for a given phase under out_dir."""
    candidate = out_dir / phase_id
    if candidate.is_dir():
        return candidate
    # Fallback: search one level deep (run_dir/phase_id pattern).
    for d in out_dir.iterdir():
        if d.is_dir() and (d / phase_id).is_dir():
            return d / phase_id
    raise FileNotFoundError(f"No output directory found for {phase_id} under {out_dir}")


def run_driver(tmp_path: Path, *args: str) -> Path:
    out_dir = tmp_path / "run"
    result = phase_driver.main(["TestProject", "--workflow", "topology_ai", "--out-dir", str(out_dir), "--allow-existing-outputs", *args])
    assert result == 0
    return out_dir


def stage_results(out_dir: Path) -> list[dict[str, Any]]:
    return read_json(out_dir / "phase-driver-stage-results.json")["stage_results"]


def create_post_conversion_inputs(root: Path, project: str = "TestProject", *, stack: bool = True) -> Path:
    post = root / project / "post_conversion"
    post.mkdir(parents=True, exist_ok=True)
    for suffix in [
        "bom.json",
        "thomson-export-sch.json",
        "thomson-export-brd.json",
    ]:
        (post / f"{project}-{suffix}").write_text("{}", encoding="utf-8")
    if stack:
        (post / f"{project}-thomson-export-stack.json").write_text("{}", encoding="utf-8")
    schemas = root / "schemas"
    schemas.mkdir(exist_ok=True)
    (schemas / "calculation_readiness_schema.json").write_text("{}", encoding="utf-8")
    (schemas / "calculation_input_schema.json").write_text("{}", encoding="utf-8")
    (schemas / "calculation_result_schema.json").write_text("{}", encoding="utf-8")
    return post


def fake_completed(command: list[str], returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, returncode, "ok", "")


def fake_run_creating_outputs(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
    if "--out" in command:
        out = Path(command[command.index("--out") + 1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("{}", encoding="utf-8")
    if "--validate-out" in command:
        validate_out = Path(command[command.index("--validate-out") + 1])
        validate_out.parent.mkdir(parents=True, exist_ok=True)
        validate_out.write_text("{}", encoding="utf-8")
    if "--power-out" in command:
        power = Path(command[command.index("--power-out") + 1])
        power.parent.mkdir(parents=True, exist_ok=True)
        power.write_text("{}", encoding="utf-8")
    for flag in ["--template-out", "--status-out", "--blockers-out", "--review-out"]:
        if flag in command:
            path = Path(command[command.index(flag) + 1])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")
    if "--out-dir" in command:
        out_dir = Path(command[command.index("--out-dir") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        script = Path(command[1]).name
        outputs_by_script = {
            "ai_packet_phase_build.py": ["packet_queue.json", "phase_status.json"],
            "ai_packet_response_import.py": [
                "ai-packet-response-import-manifest.json",
                "ai-packet-response-import-status.json",
                "ai-packet-response-import-blockers.json",
                "ai-packet-response-import-review.json",
                "ai-packet-response-import-index.json",
            ],
            "ai_patch_build.py": ["ai-patch-bundle.json"],
            "ai_candidate_materialize.py": ["ai-candidate-inputs.json"],
            "ai_candidate_adapter_build.py": ["ai-adapter-manifest.json"],
            "ai_candidate_ingest_workflow.py": ["ai-candidate-ingestion-manifest.json"],
            "ai_candidate_promotion_plan.py": ["ai-candidate-promotion-plan.json", "ai-candidate-approval-queue.json"],
            "ai_promotion_apply_dry_run.py": ["ai-approved-promotion-apply-dry-run.json"],
            "ai_candidate_core_input_apply.py": ["ai-candidate-core-input-apply-manifest.json"],
            "ai_candidate_core_input_ingest_workflow.py": ["ai-candidate-core-input-ingest-manifest.json"],
            "ai_candidate_normalized_promotion_review.py": ["ai-candidate-normalized-promotion-review.json"],
        }
        for filename in outputs_by_script.get(script, []):
            (out_dir / filename).write_text("{}", encoding="utf-8")
        if script == "ai_packet_phase_build.py":
            # Determine output directory: --out-dir arg is set by _cmd_pr26 to the packet dir.
            pkt_dir = out_dir  # default fallback
            try:
                idx = command.index("--out-dir")
                pkt_dir = Path(command[idx + 1])
            except (ValueError, IndexError):
                pass
            pkt_dir.mkdir(parents=True, exist_ok=True)
            packet_sub = pkt_dir / "packets" / "packet_001"
            packet_sub.mkdir(parents=True, exist_ok=True)
            (packet_sub / "status.json").write_text("{}", encoding="utf-8")
            (pkt_dir / "packet_queue.json").write_text(json.dumps({"packets": [{"packet_id": "packet_001"}]}), encoding="utf-8")

            # Build source_artifacts list from discovered/overridden paths in the command.
            source_artifacts: list[dict[str, Any]] = []
            for label, flag in [("bom", "--bom"), ("schematic_export", "--schematic-export"),
                                ("datasheet_manifest", "--datasheet-manifest"),
                                ("datasheet_index", "--datasheet-index"),
                                ("datasheet_evidence_index", "--datasheet-evidence-index"),
                                ("part_info_index", "--part-info-index")]:
                if flag in command:
                    path = Path(command[command.index(flag) + 1])
                    source_artifacts.append({
                        "label": label,
                        "path": str(path),
                        "present": path.exists(),
                    })
                else:
                    # Check auto-discovery paths for BOM and schematic.
                    if label == "bom":
                        bom_path = Path(pkt_dir).parents[0] / "TestProject" / "post_conversion" / "TestProject-bom.json"
                        source_artifacts.append({
                            "label": "bom",
                            "path": str(bom_path),
                            "present": bom_path.exists(),
                        })
                    elif label == "schematic_export":
                        sch_path = Path(pkt_dir).parents[0] / "TestProject" / "post_conversion" / "TestProject-thomson-export-sch.json"
                        source_artifacts.append({
                            "label": "schematic_export",
                            "path": str(sch_path),
                            "present": sch_path.exists(),
                        })

            (pkt_dir / "phase_status.json").write_text(
                json.dumps({
                    "safe_for_core_apply": False,
                    "ready_for_core_apply": False,
                    "qwen_vision_invoked": False,
                    "source_artifacts": source_artifacts,
                }),
                encoding="utf-8",
            )
            # Also write to out_dir/phase_id for test convenience.
            phase_id_from_cmd = None
            try:
                idx2 = command.index("--phase-id")
                phase_id_from_cmd = command[idx2 + 1]
            except (ValueError, IndexError):
                pass
            if phase_id_from_cmd:
                test_dir = out_dir / phase_id_from_cmd
                test_dir.mkdir(parents=True, exist_ok=True)
                (test_dir / "phase_status.json").write_text(
                    json.dumps({
                        "safe_for_core_apply": False,
                        "ready_for_core_apply": False,
                        "qwen_vision_invoked": False,
                        "source_artifacts": source_artifacts,
                    }),
                    encoding="utf-8",
                )
        if script == "ai_packet_response_import.py":
            packet_dir = Path(command[command.index("--packet-dir") + 1])
            raw = packet_dir / "packets" / "packet_001" / "raw_response.json"
            raw.parent.mkdir(parents=True, exist_ok=True)
            raw.write_text(json.dumps({"packet_id": "packet_001", "schema_version": "ai_extraction_result_v1", "status": "completed", "extracted_items": [], "unknown_items": []}), encoding="utf-8")
    return fake_completed(command)


def test_legacy_command_compatibility_dry_run_parses() -> None:
    result = subprocess.run(
        [str(ROOT / "scripts" / "run_phase_driver.sh"), "TestProject", "1", "22", "--dry-run"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "evidence_review dry-run" in result.stdout


def test_topology_ai_dry_run_order_and_no_subprocess(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_run(*_: Any, **__: Any) -> subprocess.CompletedProcess[str]:
        raise AssertionError("dry-run must not execute subprocess stage commands")

    monkeypatch.setattr(phase_driver.subprocess, "run", fail_run)
    out_dir = run_driver(tmp_path, "--start", "pr16", "--end", "pr37", "--dry-run")
    rows = stage_results(out_dir)
    assert [row["phase_id"] for row in rows] == [
        "pre01_locate_post_conversion_exports",
        "pre02_topology_map",
        "pre03_topology_role_resolution",
        "pre04_rail_relationships",
        "pre05_copper_net_association",
        "pre06_branch_topology",
        "pre07_topology_geometry_review",
        "pre08_branch_topology_enrichment",
        "pr16_calculation_readiness",
        "pre09_missing_data_manifest_or_readiness_seed",
        "pre10_current_model_seed",
        "pr17_schema_available",
        "pr18_copper_calculation",
        "pr19_current_model_ingest",
        "pr20_current_allocation",
        "pr21_copper_with_allocated_current",
        "pr22_via_current_density",
        "pr23_rating_model_ingest",
        "pr24_fuse_margin",
        "pr25_connector_pin_margin",
        "pr26_ai_packet_build",
        "pr26_ai_packet_response_import",
        "pr27_ai_extraction_validate",
        "pr28_ai_patch_build",
        "pr29_ai_candidate_materialize",
        "pr30_ai_candidate_adapter_outputs",
        "pr31_ai_candidate_ingest",
        "pr32_ai_promotion_plan",
        "pr33_ai_approval_decisions",
        "pr34_ai_promotion_apply_dry_run",
        "pr35_ai_candidate_core_input_apply",
        "pr36_ai_candidate_core_input_ingest",
        "pr37_ai_candidate_normalized_review",
    ]
    assert rows[0]["command"] == []
    assert rows[9]["script"] == "missing_data_manifest.py"
    assert rows[10]["script"] == "current_model_seed.py"
    assert rows[11]["command"] == []
    assert rows[11]["status"] == "not_applicable"
    assert rows[16]["script"] == "topology_copper_calculate.py"
    assert rows[16]["stage_kind"] == "copper_via"
    assert rows[17]["phase_id"] == "pr23_rating_model_ingest"
    assert rows[18]["script"] == "topology_margin_calculate.py"
    assert rows[19]["script"] == "topology_margin_calculate.py"
    assert rows[21]["script"] == "ai_packet_response_import.py"
    assert "outputs/artifacts" in rows[25]["reason"]


def test_missing_raw_ai_blocks_pr27_and_later_without_fabrication(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_root = tmp_path / "repo"
    create_post_conversion_inputs(fake_root)
    monkeypatch.setattr(phase_driver, "repo_root", lambda: fake_root)
    monkeypatch.setattr(phase_driver.subprocess, "run", fake_run_creating_outputs)
    out_dir = tmp_path / "run"
    manifest = out_dir / "pre09_missing_data_manifest_or_readiness_seed" / "missing-data-manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}", encoding="utf-8")
    result = phase_driver.main(["TestProject", "--workflow", "topology_ai", "--start", "pr26", "--end", "pr37", "--out-dir", str(out_dir), "--allow-existing-outputs"])
    assert result == 0
    rows = stage_results(out_dir)
    pr27 = next(row for row in rows if row["phase_id"] == "pr27_ai_extraction_validate")
    assert pr27["status"] == "blocked_missing_input"
    assert "will not fabricate" in pr27["reason"]
    assert not any((out_dir / "pr26_ai_packet_build").glob("packets/*/raw_response.json"))
    assert all(row["status"] == "skipped" for row in rows if row["pr_number"] >= 28)


def test_phase_driver_with_responses_dir_imports_before_pr27(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_root = tmp_path / "repo"
    create_post_conversion_inputs(fake_root)
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    (responses_dir / "packet_001.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(phase_driver, "repo_root", lambda: fake_root)
    monkeypatch.setattr(phase_driver.subprocess, "run", fake_run_creating_outputs)
    out_dir = tmp_path / "run"
    manifest = out_dir / "pre09_missing_data_manifest_or_readiness_seed" / "missing-data-manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}", encoding="utf-8")
    result = phase_driver.main([
        "TestProject",
        "--workflow",
        "topology_ai",
        "--start",
        "pr26",
        "--end",
        "pr27",
        "--out-dir",
        str(out_dir),
        "--allow-existing-outputs",
        "--responses-dir",
        str(responses_dir),
    ])
    assert result == 0
    rows = stage_results(out_dir)
    importer = next(row for row in rows if row["phase_id"] == "pr26_ai_packet_response_import")
    pr27 = next(row for row in rows if row["phase_id"] == "pr27_ai_extraction_validate")
    assert importer["status"] == "passed"
    assert importer["command"]
    assert importer["command"].index("--responses-dir") > 0
    assert pr27["status"] == "passed"
    assert read_json(out_dir / "phase-driver-manifest.json")["model_routing_summary"]["qwen_vision_invoked"] is False


def test_outputs_stay_under_workflow_run_dir_and_safety_flags(tmp_path: Path) -> None:
    out_dir = run_driver(tmp_path, "--dry-run")
    manifest = read_json(out_dir / "phase-driver-manifest.json")
    artifact_index = read_json(out_dir / "phase-driver-artifact-index.json")
    assert manifest["workflow_run_only"] is True
    assert manifest["wrote_core_artifacts"] is False
    assert manifest["applied_promotions_to_core"] is False
    assert manifest["merged_addenda"] is False
    assert manifest["ran_post_promotion_allocation"] is False
    assert manifest["ran_post_promotion_calculations"] is False
    assert manifest["safe_for_core_apply"] is False
    assert manifest["ready_for_core_apply"] is False
    pr26_plus = [row for row in artifact_index["artifacts"] if row["pr_number"] >= 26]
    assert pr26_plus
    assert all(row["inside_workflow_run_dir"] is True for row in artifact_index["artifacts"])
    assert any(row["is_prerequisite_output"] for row in artifact_index["artifacts"])


def test_pr33_uses_script_accepted_run_local_promotion_paths(tmp_path: Path) -> None:
    out_dir = run_driver(tmp_path, "--start", "pr33", "--end", "pr34", "--dry-run")
    rows = stage_results(out_dir)
    pr33 = next(row for row in rows if row["phase_id"] == "pr33_ai_approval_decisions")
    pr34 = next(row for row in rows if row["phase_id"] == "pr34_ai_promotion_apply_dry_run")
    decisions = out_dir / "pr32_ai_promotion_plan" / "ai-approval-decisions.json"
    validation = out_dir / "pr32_ai_promotion_plan" / "ai-approval-decision-validation.json"

    assert pr33["command"][pr33["command"].index("--promotion-dir") + 1] == str(out_dir / "pr32_ai_promotion_plan")
    assert pr33["command"][pr33["command"].index("--out") + 1] == str(decisions)
    assert pr33["command"][pr33["command"].index("--validate-out") + 1] == str(validation)
    decisions.resolve().relative_to(out_dir.resolve())
    validation.resolve().relative_to(out_dir.resolve())
    assert pr33["output_paths"] == [str(decisions), str(validation)]
    assert pr34["input_paths"] == [
        str(out_dir / "pr32_ai_promotion_plan" / "ai-candidate-promotion-plan.json"),
        str(decisions),
        str(validation),
    ]
    assert pr34["command"][pr34["command"].index("--decisions") + 1] == str(decisions)
    assert pr34["command"][pr34["command"].index("--decision-validation") + 1] == str(validation)


def test_pr33_real_script_advances_to_pr34_without_path_containment_error(tmp_path: Path) -> None:
    out_dir = tmp_path / "run"
    promo_dir = out_dir / "pr32_ai_promotion_plan"
    promo_dir.mkdir(parents=True)
    (promo_dir / "ai-candidate-approval-queue.json").write_text(
        json.dumps(
            {
                "project": "TestProject",
                "schema_version": "ai_candidate_approval_queue_v1",
                "approval_items": [],
            }
        ),
        encoding="utf-8",
    )
    (promo_dir / "ai-candidate-promotion-plan.json").write_text(
        json.dumps({"project": "TestProject", "schema_version": "ai_candidate_promotion_plan_v1", "promotion_candidates": []}),
        encoding="utf-8",
    )

    result = phase_driver.main([
        "TestProject",
        "--workflow",
        "topology_ai",
        "--start",
        "pr33",
        "--end",
        "pr34",
        "--out-dir",
        str(out_dir),
        "--allow-existing-outputs",
    ])

    assert result == 0
    rows = stage_results(out_dir)
    pr33 = next(row for row in rows if row["phase_id"] == "pr33_ai_approval_decisions")
    pr34 = next(row for row in rows if row["phase_id"] == "pr34_ai_promotion_apply_dry_run")
    decisions = promo_dir / "ai-approval-decisions.json"
    validation = promo_dir / "ai-approval-decision-validation.json"
    assert pr33["status"] == "passed"
    assert "output path must be inside out-dir" not in pr33["stderr_preview"]
    assert decisions.exists()
    assert validation.exists()
    assert pr34["status"] != "blocked_missing_input"
    assert read_json(decisions)["summary"]["safe_to_apply_count"] == 0
    assert read_json(out_dir / "phase-driver-manifest.json")["safe_for_core_apply"] is False
    assert read_json(out_dir / "phase-driver-manifest.json")["ready_for_core_apply"] is False


def test_pr33_uses_explicit_approval_decisions_when_provided(tmp_path: Path) -> None:
    approval_decisions = tmp_path / "approval-decisions.json"
    approval_decisions.write_text("{}", encoding="utf-8")
    out_dir = run_driver(tmp_path, "--start", "pr33", "--end", "pr34", "--dry-run", "--approval-decisions", str(approval_decisions))
    rows = stage_results(out_dir)
    pr33 = next(row for row in rows if row["phase_id"] == "pr33_ai_approval_decisions")
    decisions = out_dir / "pr32_ai_promotion_plan" / "ai-approval-decisions.json"
    validation = out_dir / "pr32_ai_promotion_plan" / "ai-approval-decision-validation.json"

    assert str(approval_decisions) in pr33["input_paths"]
    assert "--decision-template" not in pr33["command"]
    assert pr33["command"][pr33["command"].index("--decisions") + 1] == str(approval_decisions)
    assert "--validate-only" in pr33["command"]
    assert pr33["command"][pr33["command"].index("--out") + 1] == str(decisions)
    assert pr33["command"][pr33["command"].index("--validate-out") + 1] == str(validation)


def test_qwen_vision_reporting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("THOMSONLINT_VISION_MODEL", "qwen_vision")
    out_dir = run_driver(tmp_path, "--dry-run")
    routing = read_json(out_dir / "phase-driver-manifest.json")["model_routing_summary"]
    assert routing["qwen_vision_configured"] is True
    assert routing["qwen_vision_invoked"] is False


def test_schema_validates_all_run_artifacts(tmp_path: Path) -> None:
    out_dir = run_driver(tmp_path, "--dry-run")
    schema = read_json(SCHEMA)
    for filename in [
        "phase-driver-manifest.json",
        "phase-driver-status.json",
        "phase-driver-stage-results.json",
        "phase-driver-artifact-index.json",
        "phase-driver-blockers.json",
    ]:
        jsonschema.validate(instance=read_json(out_dir / filename), schema=schema)


def test_missing_project_inputs_block_without_crash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_root = tmp_path / "repo"
    fake_root.mkdir()
    monkeypatch.setattr(phase_driver, "repo_root", lambda: fake_root)
    out_dir = run_driver(tmp_path)
    rows = stage_results(out_dir)
    assert rows[0]["status"] == "blocked_missing_input"
    assert rows[0]["blocker_id"]
    assert read_json(out_dir / "phase-driver-blockers.json")["blockers"]


def test_driver_locates_post_conversion_inputs_and_pr16_uses_run_dir_enriched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_root = tmp_path / "repo"
    create_post_conversion_inputs(fake_root)
    monkeypatch.setattr(phase_driver, "repo_root", lambda: fake_root)
    monkeypatch.setattr(phase_driver.subprocess, "run", fake_run_creating_outputs)
    out_dir = tmp_path / "run"
    result = phase_driver.main(["TestProject", "--workflow", "topology_ai", "--start", "pre01", "--end", "pr16", "--out-dir", str(out_dir), "--allow-existing-outputs"])
    assert result == 0
    rows = stage_results(out_dir)
    pre01 = rows[0]
    assert pre01["status"] == "passed"
    assert str(fake_root / "TestProject" / "post_conversion" / "TestProject-thomson-export-sch.json") in pre01["input_paths"]
    pr16 = next(row for row in rows if row["phase_id"] == "pr16_calculation_readiness")
    enriched = out_dir / "pre08_branch_topology_enrichment" / "branch-topology-enriched.json"
    assert str(enriched) in pr16["command"]
    assert "exports/TestProject-branch-topology-enriched.json" not in " ".join(pr16["command"])


def test_missing_post_conversion_required_inputs_have_exact_blockers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_root = tmp_path / "repo"
    fake_root.mkdir()
    monkeypatch.setattr(phase_driver, "repo_root", lambda: fake_root)
    out_dir = run_driver(tmp_path, "--start", "pre01", "--end", "pr16")
    blockers = read_json(out_dir / "phase-driver-blockers.json")["blockers"]
    missing = set(blockers[0]["missing_paths"])
    assert str(fake_root / "TestProject" / "post_conversion" / "TestProject-bom.json") in missing
    assert str(fake_root / "TestProject" / "post_conversion" / "TestProject-thomson-export-sch.json") in missing
    assert str(fake_root / "TestProject" / "post_conversion" / "TestProject-thomson-export-brd.json") in missing


def test_missing_optional_post_conversion_inputs_are_warnings_not_blockers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_root = tmp_path / "repo"
    create_post_conversion_inputs(fake_root, stack=False)
    monkeypatch.setattr(phase_driver, "repo_root", lambda: fake_root)
    out_dir = tmp_path / "run"
    result = phase_driver.main(["TestProject", "--workflow", "topology_ai", "--start", "pre01", "--end", "pre01", "--out-dir", str(out_dir), "--allow-existing-outputs"])
    assert result == 0
    rows = stage_results(out_dir)
    assert rows[0]["status"] == "passed"
    source_index = read_json(out_dir / "pre01_locate_post_conversion_exports" / "post-conversion-source-index.json")
    assert source_index["warnings"]
    assert read_json(out_dir / "phase-driver-blockers.json")["blockers"] == []


def test_pr17_independent_when_pr16_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_root = tmp_path / "repo"
    fake_root.mkdir()
    monkeypatch.setattr(phase_driver, "repo_root", lambda: fake_root)
    out_dir = run_driver(tmp_path, "--start", "pr16", "--end", "pr18")
    rows = stage_results(out_dir)
    pr17 = next(row for row in rows if row["phase_id"] == "pr17_schema_available")
    pr18 = next(row for row in rows if row["phase_id"] == "pr18_copper_calculation")
    assert pr17["status"] == "not_applicable"
    assert pr18["status"] == "skipped"
    assert pr18["blocker_id"] == rows[0]["blocker_id"]


def test_pr17_passes_when_required_schemas_are_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_root = tmp_path / "repo"
    create_post_conversion_inputs(fake_root)
    monkeypatch.setattr(phase_driver, "repo_root", lambda: fake_root)
    out_dir = run_driver(tmp_path, "--start", "pr17", "--end", "pr17")
    rows = stage_results(out_dir)
    blockers = read_json(out_dir / "phase-driver-blockers.json")["blockers"]

    assert rows[0]["phase_id"] == "pr17_schema_available"
    assert rows[0]["status"] == "passed"
    assert rows[0]["blocker_id"] is None
    assert blockers == []


def test_pr17_reports_genuinely_missing_required_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_root = tmp_path / "repo"
    create_post_conversion_inputs(fake_root)
    (fake_root / "schemas" / "calculation_readiness_schema.json").unlink()
    monkeypatch.setattr(phase_driver, "repo_root", lambda: fake_root)
    out_dir = run_driver(tmp_path, "--start", "pr17", "--end", "pr17")
    rows = stage_results(out_dir)
    blockers = read_json(out_dir / "phase-driver-blockers.json")["blockers"]

    assert rows[0]["status"] == "not_applicable"
    assert rows[0]["blocker_id"] == "pr17_schema_available_missing_schema"
    assert blockers[0]["blocker_id"] == "pr17_schema_available_missing_schema"
    assert blockers[0]["reason"] == "schema_not_available"
    assert blockers[0]["missing_paths"] == [str(fake_root / "schemas" / "calculation_readiness_schema.json")]


def test_normal_pr17_status_has_no_schema_blocker_and_preserves_safety_flags(tmp_path: Path) -> None:
    out_dir = run_driver(tmp_path, "--start", "pr17", "--end", "pr17")
    status = read_json(out_dir / "phase-driver-status.json")
    blockers = read_json(out_dir / "phase-driver-blockers.json")["blockers"]
    rows = stage_results(out_dir)

    assert status["overall_status"] == "passed"
    assert status["blocker_count"] == 0
    assert rows[0]["status"] == "passed"
    assert not any(blocker["blocker_id"] == "pr17_schema_available_missing_schema" for blocker in blockers)
    assert status["safe_for_core_apply"] is False
    assert status["ready_for_core_apply"] is False
    assert status["workflow_run_only"] is True
    assert status["wrote_core_artifacts"] is False


def test_downstream_pr18_blocks_on_missing_previous_stage_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_root = tmp_path / "repo"
    create_post_conversion_inputs(fake_root)
    monkeypatch.setattr(phase_driver, "repo_root", lambda: fake_root)
    out_dir = tmp_path / "run"
    result = phase_driver.main(["TestProject", "--workflow", "topology_ai", "--start", "pr18", "--end", "pr18", "--out-dir", str(out_dir), "--allow-existing-outputs"])
    assert result == 0
    rows = stage_results(out_dir)
    assert rows[-1]["phase_id"] == "pr18_copper_calculation"
    assert rows[-1]["status"] == "blocked_missing_input"
    assert str(out_dir / "pr16_calculation_readiness" / "calculation-readiness-inventory.json") in rows[-1]["input_paths"]


def test_pr19_uses_run_dir_current_model_seed_not_exports_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_root = tmp_path / "repo"
    create_post_conversion_inputs(fake_root)
    monkeypatch.setattr(phase_driver, "repo_root", lambda: fake_root)
    monkeypatch.setattr(phase_driver.subprocess, "run", fake_run_creating_outputs)
    out_dir = tmp_path / "run"
    result = phase_driver.main(["TestProject", "--workflow", "topology_ai", "--start", "pre01", "--end", "pr19", "--out-dir", str(out_dir), "--allow-existing-outputs"])
    assert result == 0
    rows = stage_results(out_dir)
    pre10 = next(row for row in rows if row["phase_id"] == "pre10_current_model_seed")
    pr19 = next(row for row in rows if row["phase_id"] == "pr19_current_model_ingest")
    seed = out_dir / "pre10_current_model_seed" / "current-model-seed.json"
    assert str(seed) in pre10["output_paths"]
    assert str(seed) in pr19["command"]
    assert str(fake_root / "exports" / "TestProject-current-model.json") not in pr19["input_paths"]
    assert "exports/TestProject-current-model.json" not in " ".join(pr19["command"])


def test_missing_exports_current_model_no_longer_blocks_pr19(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_root = tmp_path / "repo"
    create_post_conversion_inputs(fake_root)
    monkeypatch.setattr(phase_driver, "repo_root", lambda: fake_root)
    monkeypatch.setattr(phase_driver.subprocess, "run", fake_run_creating_outputs)
    out_dir = tmp_path / "run"
    result = phase_driver.main(["TestProject", "--workflow", "topology_ai", "--start", "pre01", "--end", "pr19", "--out-dir", str(out_dir), "--allow-existing-outputs"])
    assert result == 0
    pr19 = next(row for row in stage_results(out_dir) if row["phase_id"] == "pr19_current_model_ingest")
    assert pr19["status"] == "passed"
    assert all("TestProject-current-model.json" not in path for path in pr19["input_paths"])


def test_pr26_can_run_from_missing_data_manifest_after_current_blocker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_root = tmp_path / "repo"
    create_post_conversion_inputs(fake_root)
    monkeypatch.setattr(phase_driver, "repo_root", lambda: fake_root)
    monkeypatch.setattr(phase_driver.subprocess, "run", fake_run_creating_outputs)
    out_dir = tmp_path / "run"
    manifest = out_dir / "pre09_missing_data_manifest_or_readiness_seed" / "missing-data-manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}", encoding="utf-8")
    result = phase_driver.main(["TestProject", "--workflow", "topology_ai", "--start", "pr20", "--end", "pr26", "--out-dir", str(out_dir), "--allow-existing-outputs"])
    assert result == 0
    rows = stage_results(out_dir)
    pr20 = next(row for row in rows if row["phase_id"] == "pr20_current_allocation")
    pr26 = next(row for row in rows if row["phase_id"] == "pr26_ai_packet_build")
    assert pr20["status"] == "blocked_missing_input"
    assert pr26["status"] == "passed"
    assert (out_dir / "pr26_ai_packet_build" / "packet_queue.json").exists()


def test_stage_results_include_required_fields(tmp_path: Path) -> None:
    out_dir = run_driver(tmp_path, "--dry-run")
    required = {
        "command",
        "return_code",
        "stdout_preview",
        "stderr_preview",
        "input_paths",
        "output_paths",
        "reason",
    }
    assert all(required.issubset(row) for row in stage_results(out_dir))


def test_no_shell_true_or_ai_network_imports_in_new_driver_code() -> None:
    banned_imports = {"requests", "aiohttp", "httpx", "openai", "litellm"}
    for path in [ROOT / "scripts" / "phase_driver.py", ROOT / "scripts" / "topology_ai_phase_registry.py"]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for keyword in node.keywords:
                    assert not (keyword.arg == "shell" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True)
            if isinstance(node, ast.Import):
                assert not ({alias.name.split(".")[0] for alias in node.names} & banned_imports)
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in banned_imports


# ---------------------------------------------------------------------------
# PR26 source artifact discovery tests
# ---------------------------------------------------------------------------


def _ensure_pre09_manifest(out_dir: Path) -> None:
    """Create pre09 output directory with missing-data-manifest.json so PR26 is not blocked."""
    pre09_out = out_dir / "pre09_missing_data_manifest_or_readiness_seed"
    pre09_out.mkdir(parents=True, exist_ok=True)
    (pre09_out / "missing-data-manifest.json").write_text("{}", encoding="utf-8")


def test_pr26_command_includes_discovered_bom_when_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PR26 command should include --bom when TestProject-bom.json exists in post_conversion."""
    fake_root = tmp_path / "repo"
    create_post_conversion_inputs(fake_root)
    _ensure_pre09_manifest(tmp_path / "run")
    monkeypatch.setattr(phase_driver, "repo_root", lambda: fake_root)
    monkeypatch.setattr(phase_driver.subprocess, "run", fake_run_creating_outputs)
    out_dir = tmp_path / "run"
    phase_driver.main([
        "TestProject", "--workflow", "topology_ai",
        "--start", "pr26", "--end", "pr26",
        "--out-dir", str(out_dir), "--allow-existing-outputs",
    ])
    rows = stage_results(out_dir)
    pr26 = next(row for row in rows if row["phase_id"] == "pr26_ai_packet_build")
    assert pr26["status"] == "passed"
    cmd_str = " ".join(pr26["command"])
    bom_path = str(fake_root / "TestProject" / "post_conversion" / "TestProject-bom.json")
    assert f"--bom {bom_path}" in cmd_str, f"BOM path not found in PR26 command: {cmd_str}"


def test_pr26_command_includes_discovered_schematic_when_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PR26 command should include --schematic-export when TestProject-thomson-export-sch.json exists."""
    fake_root = tmp_path / "repo"
    create_post_conversion_inputs(fake_root)
    _ensure_pre09_manifest(tmp_path / "run")
    monkeypatch.setattr(phase_driver, "repo_root", lambda: fake_root)
    monkeypatch.setattr(phase_driver.subprocess, "run", fake_run_creating_outputs)
    out_dir = tmp_path / "run"
    phase_driver.main([
        "TestProject", "--workflow", "topology_ai",
        "--start", "pr26", "--end", "pr26",
        "--out-dir", str(out_dir), "--allow-existing-outputs",
    ])
    rows = stage_results(out_dir)
    pr26 = next(row for row in rows if row["phase_id"] == "pr26_ai_packet_build")
    assert pr26["status"] == "passed"
    cmd_str = " ".join(pr26["command"])
    sch_path = str(fake_root / "TestProject" / "post_conversion" / "TestProject-thomson-export-sch.json")
    assert f"--schematic-export {sch_path}" in cmd_str, f"Schematic path not found in PR26 command: {cmd_str}"


def test_pr26_command_does_not_include_sunrise_artifacts_for_testproject(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PR26 command for TestProject must NOT include sunrise BOM or evidence artifacts."""
    fake_root = tmp_path / "repo"
    create_post_conversion_inputs(fake_root)
    # Also create sunrise artifacts to ensure they are not mixed in.
    sunrise_dir = fake_root / "exports" / "sunrise"
    sunrise_dir.mkdir(parents=True, exist_ok=True)
    (sunrise_dir / "sunrise-bom.json").write_text("{}", encoding="utf-8")
    (fake_root / "exports" / "datasheets").mkdir(parents=True, exist_ok=True)
    _ensure_pre09_manifest(tmp_path / "run")
    monkeypatch.setattr(phase_driver, "repo_root", lambda: fake_root)
    monkeypatch.setattr(phase_driver.subprocess, "run", fake_run_creating_outputs)
    out_dir = tmp_path / "run"
    phase_driver.main([
        "TestProject", "--workflow", "topology_ai",
        "--start", "pr26", "--end", "pr26",
        "--out-dir", str(out_dir), "--allow-existing-outputs",
    ])
    rows = stage_results(out_dir)
    pr26 = next(row for row in rows if row["phase_id"] == "pr26_ai_packet_build")
    cmd_str = " ".join(pr26["command"])
    assert "sunrise" not in cmd_str.lower(), f"Sunrise artifacts leaked into TestProject PR26: {cmd_str}"
    assert "sunrise-bom.json" not in cmd_str


def test_optional_artifact_absence_remains_warning_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When optional source artifacts are missing, PR26 should still pass (warning-only)."""
    fake_root = tmp_path / "repo"
    # Create post_conversion without BOM and schematic (only stack file)
    post = fake_root / "TestProject" / "post_conversion"
    post.mkdir(parents=True, exist_ok=True)
    (post / "TestProject-thomson-export-stack.json").write_text("{}", encoding="utf-8")
    schemas = fake_root / "schemas"
    schemas.mkdir(exist_ok=True)
    (schemas / "calculation_input_schema.json").write_text("{}", encoding="utf-8")
    (schemas / "calculation_result_schema.json").write_text("{}", encoding="utf-8")
    _ensure_pre09_manifest(tmp_path / "run")
    monkeypatch.setattr(phase_driver, "repo_root", lambda: fake_root)
    monkeypatch.setattr(phase_driver.subprocess, "run", fake_run_creating_outputs)
    out_dir = tmp_path / "run"
    result = phase_driver.main([
        "TestProject", "--workflow", "topology_ai",
        "--start", "pr26", "--end", "pr26",
        "--out-dir", str(out_dir), "--allow-existing-outputs",
    ])
    assert result == 0, "PR26 should not fail when optional artifacts are missing"
    rows = stage_results(out_dir)
    pr26 = next(row for row in rows if row["phase_id"] == "pr26_ai_packet_build")
    assert pr26["status"] == "passed", f"PR26 status should be 'passed' not '{pr26['status']}' when optional artifacts are missing"


def test_cli_override_bom_is_passed_through(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--bom CLI override should take precedence over auto-discovery."""
    fake_root = tmp_path / "repo"
    create_post_conversion_inputs(fake_root)
    # Create an alternate BOM file.
    alt_bom = fake_root / "alt-bom.json"
    alt_bom.write_text("{}", encoding="utf-8")
    _ensure_pre09_manifest(tmp_path / "run")
    monkeypatch.setattr(phase_driver, "repo_root", lambda: fake_root)
    monkeypatch.setattr(phase_driver.subprocess, "run", fake_run_creating_outputs)
    out_dir = tmp_path / "run"
    phase_driver.main([
        "TestProject", "--workflow", "topology_ai",
        "--start", "pr26", "--end", "pr26",
        "--out-dir", str(out_dir), "--allow-existing-outputs",
        "--bom", str(alt_bom),
    ])
    rows = stage_results(out_dir)
    pr26 = next(row for row in rows if row["phase_id"] == "pr26_ai_packet_build")
    cmd_str = " ".join(pr26["command"])
    assert f"--bom {alt_bom}" in cmd_str, f"CLI --bom override not found in PR26 command: {cmd_str}"


def test_cli_override_schematic_export_is_passed_through(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--schematic-export CLI override should take precedence over auto-discovery."""
    fake_root = tmp_path / "repo"
    create_post_conversion_inputs(fake_root)
    alt_sch = fake_root / "alt-schematic.json"
    alt_sch.write_text("{}", encoding="utf-8")
    _ensure_pre09_manifest(tmp_path / "run")
    monkeypatch.setattr(phase_driver, "repo_root", lambda: fake_root)
    monkeypatch.setattr(phase_driver.subprocess, "run", fake_run_creating_outputs)
    out_dir = tmp_path / "run"
    phase_driver.main([
        "TestProject", "--workflow", "topology_ai",
        "--start", "pr26", "--end", "pr26",
        "--out-dir", str(out_dir), "--allow-existing-outputs",
        "--schematic-export", str(alt_sch),
    ])
    rows = stage_results(out_dir)
    pr26 = next(row for row in rows if row["phase_id"] == "pr26_ai_packet_build")
    cmd_str = " ".join(pr26["command"])
    assert f"--schematic-export {alt_sch}" in cmd_str


def test_cli_override_datasheet_evidence_index_is_passed_through(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--datasheet-evidence-index CLI override should be passed through to PR26."""
    fake_root = tmp_path / "repo"
    create_post_conversion_inputs(fake_root)
    ds_evidence = fake_root / "custom-datasheet-evidence.json"
    ds_evidence.write_text("{}", encoding="utf-8")
    _ensure_pre09_manifest(tmp_path / "run")
    monkeypatch.setattr(phase_driver, "repo_root", lambda: fake_root)
    monkeypatch.setattr(phase_driver.subprocess, "run", fake_run_creating_outputs)
    out_dir = tmp_path / "run"
    phase_driver.main([
        "TestProject", "--workflow", "topology_ai",
        "--start", "pr26", "--end", "pr26",
        "--out-dir", str(out_dir), "--allow-existing-outputs",
        "--datasheet-evidence-index", str(ds_evidence),
    ])
    rows = stage_results(out_dir)
    pr26 = next(row for row in rows if row["phase_id"] == "pr26_ai_packet_build")
    cmd_str = " ".join(pr26["command"])
    assert f"--datasheet-evidence-index {ds_evidence}" in cmd_str


def test_cli_override_datasheets_dir_is_passed_through(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--datasheets-dir CLI override should be passed through to PR26."""
    fake_root = tmp_path / "repo"
    create_post_conversion_inputs(fake_root)
    ds_dir = fake_root / "custom-datasheets"
    ds_dir.mkdir(parents=True, exist_ok=True)
    (ds_dir / "test.pdf").write_text("fake pdf", encoding="utf-8")
    _ensure_pre09_manifest(tmp_path / "run")
    monkeypatch.setattr(phase_driver, "repo_root", lambda: fake_root)
    monkeypatch.setattr(phase_driver.subprocess, "run", fake_run_creating_outputs)
    out_dir = tmp_path / "run"
    phase_driver.main([
        "TestProject", "--workflow", "topology_ai",
        "--start", "pr26", "--end", "pr26",
        "--out-dir", str(out_dir), "--allow-existing-outputs",
        "--datasheets-dir", str(ds_dir),
    ])
    rows = stage_results(out_dir)
    pr26 = next(row for row in rows if row["phase_id"] == "pr26_ai_packet_build")
    cmd_str = " ".join(pr26["command"])
    assert f"--datasheets-dir {ds_dir}" not in cmd_str  # datasheets-dir is not a PR26 flag; it triggers pre-stage


def test_cli_override_part_info_index_is_passed_through(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--part-info-index CLI override should be passed through to PR26."""
    fake_root = tmp_path / "repo"
    create_post_conversion_inputs(fake_root)
    part_info = fake_root / "custom-part-info.json"
    part_info.write_text("{}", encoding="utf-8")
    _ensure_pre09_manifest(tmp_path / "run")
    monkeypatch.setattr(phase_driver, "repo_root", lambda: fake_root)
    monkeypatch.setattr(phase_driver.subprocess, "run", fake_run_creating_outputs)
    out_dir = tmp_path / "run"
    phase_driver.main([
        "TestProject", "--workflow", "topology_ai",
        "--start", "pr26", "--end", "pr26",
        "--out-dir", str(out_dir), "--allow-existing-outputs",
        "--part-info-index", str(part_info),
    ])
    rows = stage_results(out_dir)
    pr26 = next(row for row in rows if row["phase_id"] == "pr26_ai_packet_build")
    cmd_str = " ".join(pr26["command"])
    assert f"--part-info-index {part_info}" in cmd_str


def test_cli_override_datasheet_manifest_is_passed_through(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--datasheet-manifest CLI override should be passed through to PR26."""
    fake_root = tmp_path / "repo"
    create_post_conversion_inputs(fake_root)
    ds_manifest = fake_root / "custom-datasheet-manifest.jsonl"
    ds_manifest.write_text("", encoding="utf-8")
    _ensure_pre09_manifest(tmp_path / "run")
    monkeypatch.setattr(phase_driver, "repo_root", lambda: fake_root)
    monkeypatch.setattr(phase_driver.subprocess, "run", fake_run_creating_outputs)
    out_dir = tmp_path / "run"
    phase_driver.main([
        "TestProject", "--workflow", "topology_ai",
        "--start", "pr26", "--end", "pr26",
        "--out-dir", str(out_dir), "--allow-existing-outputs",
        "--datasheet-manifest", str(ds_manifest),
    ])
    rows = stage_results(out_dir)
    pr26 = next(row for row in rows if row["phase_id"] == "pr26_ai_packet_build")
    cmd_str = " ".join(pr26["command"])
    assert f"--datasheet-manifest {ds_manifest}" in cmd_str


def test_phase_status_source_artifacts_present_true_when_discovered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """phase_status.json should show present=true for discovered BOM and schematic."""
    fake_root = tmp_path / "repo"
    create_post_conversion_inputs(fake_root)
    _ensure_pre09_manifest(tmp_path / "run")
    monkeypatch.setattr(phase_driver, "repo_root", lambda: fake_root)
    monkeypatch.setattr(phase_driver.subprocess, "run", fake_run_creating_outputs)
    out_dir = tmp_path / "run"
    phase_driver.main([
        "TestProject", "--workflow", "topology_ai",
        "--start", "pr26", "--end", "pr26",
        "--out-dir", str(out_dir), "--allow-existing-outputs",
    ])
    pr26_out = phase_output_dir(out_dir, "pr26_ai_packet_build")
    status = read_json(pr26_out / "phase_status.json")
    source_artifacts = {sa["label"]: sa for sa in status.get("source_artifacts", [])}
    bom_sa = source_artifacts.get("bom", {})
    assert bom_sa.get("present") is True, f"BOM present should be true: {bom_sa}"
    sch_sa = source_artifacts.get("schematic_export", {})
    assert sch_sa.get("present") is True, f"Schematic export present should be true: {sch_sa}"


def test_phase_status_source_artifacts_present_false_when_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """phase_status.json should show present=false for missing optional artifacts."""
    fake_root = tmp_path / "repo"
    # Create post_conversion without BOM and schematic.
    post = fake_root / "TestProject" / "post_conversion"
    post.mkdir(parents=True, exist_ok=True)
    (post / "TestProject-thomson-export-stack.json").write_text("{}", encoding="utf-8")
    schemas = fake_root / "schemas"
    schemas.mkdir(exist_ok=True)
    (schemas / "calculation_input_schema.json").write_text("{}", encoding="utf-8")
    (schemas / "calculation_result_schema.json").write_text("{}", encoding="utf-8")
    _ensure_pre09_manifest(tmp_path / "run")
    monkeypatch.setattr(phase_driver, "repo_root", lambda: fake_root)
    monkeypatch.setattr(phase_driver.subprocess, "run", fake_run_creating_outputs)
    out_dir = tmp_path / "run"
    phase_driver.main([
        "TestProject", "--workflow", "topology_ai",
        "--start", "pr26", "--end", "pr26",
        "--out-dir", str(out_dir), "--allow-existing-outputs",
    ])
    pr26_out = phase_output_dir(out_dir, "pr26_ai_packet_build")
    status = read_json(pr26_out / "phase_status.json")
    source_artifacts = {sa["label"]: sa for sa in status.get("source_artifacts", [])}
    bom_sa = source_artifacts.get("bom", {})
    assert bom_sa.get("present") is False, f"BOM present should be false: {bom_sa}"
    sch_sa = source_artifacts.get("schematic_export", {})
    assert sch_sa.get("present") is False, f"Schematic export present should be false: {sch_sa}"


def test_safe_for_core_apply_remains_false_in_pr26_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """phase_status.json safe_for_core_apply must remain false after PR26."""
    fake_root = tmp_path / "repo"
    create_post_conversion_inputs(fake_root)
    _ensure_pre09_manifest(tmp_path / "run")
    monkeypatch.setattr(phase_driver, "repo_root", lambda: fake_root)
    monkeypatch.setattr(phase_driver.subprocess, "run", fake_run_creating_outputs)
    out_dir = tmp_path / "run"
    phase_driver.main([
        "TestProject", "--workflow", "topology_ai",
        "--start", "pr26", "--end", "pr26",
        "--out-dir", str(out_dir), "--allow-existing-outputs",
    ])
    pr26_out = phase_output_dir(out_dir, "pr26_ai_packet_build")
    status = read_json(pr26_out / "phase_status.json")
    assert status.get("safe_for_core_apply") is False


def test_ready_for_core_apply_remains_false_in_pr26_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """phase_status.json ready_for_core_apply must remain false after PR26."""
    fake_root = tmp_path / "repo"
    create_post_conversion_inputs(fake_root)
    _ensure_pre09_manifest(tmp_path / "run")
    monkeypatch.setattr(phase_driver, "repo_root", lambda: fake_root)
    monkeypatch.setattr(phase_driver.subprocess, "run", fake_run_creating_outputs)
    out_dir = tmp_path / "run"
    phase_driver.main([
        "TestProject", "--workflow", "topology_ai",
        "--start", "pr26", "--end", "pr26",
        "--out-dir", str(out_dir), "--allow-existing-outputs",
    ])
    pr26_out = phase_output_dir(out_dir, "pr26_ai_packet_build")
    status = read_json(pr26_out / "phase_status.json")
    assert status.get("ready_for_core_apply") is False


def test_qwen_vision_invoked_false_in_pr26_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """phase_status.json qwen_vision_invoked must remain false after PR26."""
    fake_root = tmp_path / "repo"
    create_post_conversion_inputs(fake_root)
    _ensure_pre09_manifest(tmp_path / "run")
    monkeypatch.setattr(phase_driver, "repo_root", lambda: fake_root)
    monkeypatch.setattr(phase_driver.subprocess, "run", fake_run_creating_outputs)
    out_dir = tmp_path / "run"
    phase_driver.main([
        "TestProject", "--workflow", "topology_ai",
        "--start", "pr26", "--end", "pr26",
        "--out-dir", str(out_dir), "--allow-existing-outputs",
    ])
    pr26_out = phase_output_dir(out_dir, "pr26_ai_packet_build")
    status = read_json(pr26_out / "phase_status.json")
    assert status.get("qwen_vision_invoked") is False
