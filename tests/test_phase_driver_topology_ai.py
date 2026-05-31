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


def run_driver(tmp_path: Path, *args: str) -> Path:
    out_dir = tmp_path / "run"
    result = phase_driver.main(["TestProject", "--workflow", "topology_ai", "--out-dir", str(out_dir), "--allow-existing-outputs", *args])
    assert result == 0
    return out_dir


def stage_results(out_dir: Path) -> list[dict[str, Any]]:
    return read_json(out_dir / "phase-driver-stage-results.json")["stage_results"]


def create_project_inputs(root: Path, project: str = "TestProject") -> None:
    exports = root / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    for suffix in [
        "branch-topology-enriched.json",
        "topology-roles.json",
        "rail-relationships.json",
        "topology-geometry-review.json",
        "current-model.json",
        "missing-data-manifest.json",
    ]:
        (exports / f"{project}-{suffix}").write_text("{}", encoding="utf-8")
    schemas = root / "schemas"
    schemas.mkdir()
    (schemas / "calculation_input_schema.json").write_text("{}", encoding="utf-8")
    (schemas / "calculation_result_schema.json").write_text("{}", encoding="utf-8")


def fake_completed(command: list[str], returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, returncode, "ok", "")


def fake_run_creating_outputs(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
    if "--out" in command:
        out = Path(command[command.index("--out") + 1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("{}", encoding="utf-8")
    if "--out-dir" in command:
        out_dir = Path(command[command.index("--out-dir") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        script = Path(command[1]).name
        outputs_by_script = {
            "ai_packet_phase_build.py": ["packet_queue.json", "phase_status.json"],
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
            packet_dir = out_dir / "packets" / "packet_001"
            packet_dir.mkdir(parents=True, exist_ok=True)
            (packet_dir / "status.json").write_text("{}", encoding="utf-8")
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
        "pr16_calculation_readiness",
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
    assert rows[1]["command"] == []
    assert rows[1]["status"] == "not_applicable"
    assert rows[6]["script"] == "topology_copper_calculate.py"
    assert rows[6]["stage_kind"] == "copper_via"
    assert rows[7]["phase_id"] == "pr23_rating_model_ingest"
    assert rows[8]["script"] == "topology_margin_calculate.py"
    assert rows[9]["script"] == "topology_margin_calculate.py"
    assert "outputs/artifacts" in rows[14]["reason"]


def test_missing_raw_ai_blocks_pr27_and_later_without_fabrication(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_root = tmp_path / "repo"
    create_project_inputs(fake_root)
    monkeypatch.setattr(phase_driver, "repo_root", lambda: fake_root)
    monkeypatch.setattr(phase_driver.subprocess, "run", fake_run_creating_outputs)
    out_dir = tmp_path / "run"
    result = phase_driver.main(["TestProject", "--workflow", "topology_ai", "--out-dir", str(out_dir), "--allow-existing-outputs"])
    assert result == 0
    rows = stage_results(out_dir)
    pr27 = next(row for row in rows if row["phase_id"] == "pr27_ai_extraction_validate")
    assert pr27["status"] == "blocked_missing_input"
    assert "will not fabricate" in pr27["reason"]
    assert not any((out_dir / "pr26_ai_packet_build").glob("packets/*/raw_response.json"))
    assert all(row["status"] == "skipped" for row in rows if row["pr_number"] >= 28)


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
    assert all(row["inside_workflow_run_dir"] is True for row in pr26_plus)


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
