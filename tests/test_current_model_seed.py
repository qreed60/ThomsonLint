from __future__ import annotations

import ast
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any

import jsonschema


ROOT = Path(__file__).resolve().parents[1]
SEED_SCRIPT = ROOT / "scripts" / "current_model_seed.py"
INGEST_SCRIPT = ROOT / "scripts" / "current_model_ingest.py"
SCHEMA = ROOT / "schemas" / "current_model_seed_schema.json"


def write_json(path: Path, data: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    return path


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def branch_topology_fixture() -> dict[str, Any]:
    return {
        "project": "TestProject",
        "branches": [
            {
                "branch_id": "br_v3p3_main",
                "rail_name": "V3P3",
                "segments": [{"refdes": "U1"}],
            }
        ],
    }


def run_seed(tmp_path: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    branch = write_json(tmp_path / "branch-topology-enriched.json", branch_topology_fixture())
    manifest = write_json(
        tmp_path / "missing-data-manifest.json",
        {
            "project": "TestProject",
            "manifest_items": [
                {
                    "category": "branch_current_unknown",
                    "target_type": "branch",
                    "target_id": "br_v3p3_main",
                }
            ],
        },
    )
    return subprocess.run(
        [
            sys.executable,
            str(SEED_SCRIPT),
            "--project",
            "TestProject",
            "--branch-topology-enriched",
            str(branch),
            "--missing-data-manifest",
            str(manifest),
            "--out",
            str(tmp_path / "current-model-seed.json"),
            "--template-out",
            str(tmp_path / "current-model-template.json"),
            "--status-out",
            str(tmp_path / "current-model-seed-status.json"),
            "--blockers-out",
            str(tmp_path / "current-model-seed-blockers.json"),
            "--review-out",
            str(tmp_path / "current-model-seed-review.json"),
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


def test_seed_script_creates_all_five_output_artifacts_and_schema_validates(tmp_path: Path) -> None:
    result = run_seed(tmp_path)
    assert result.returncode == 0, result.stderr + result.stdout
    schema = read_json(SCHEMA)
    for filename in [
        "current-model-seed.json",
        "current-model-template.json",
        "current-model-seed-status.json",
        "current-model-seed-blockers.json",
        "current-model-seed-review.json",
    ]:
        path = tmp_path / filename
        assert path.exists()
        jsonschema.validate(instance=read_json(path), schema=schema)


def test_missing_existing_current_model_creates_manual_seed_not_failure(tmp_path: Path) -> None:
    result = run_seed(tmp_path, "--existing-current-model", str(tmp_path / "missing-current-model.json"))
    assert result.returncode == 0, result.stderr + result.stdout
    seed = read_json(tmp_path / "current-model-seed.json")
    status = read_json(tmp_path / "current-model-seed-status.json")
    assert seed["reason"] == "missing_explicit_current_model"
    assert status["reason"] == "missing_explicit_current_model"
    assert seed["branch_currents"] == []
    assert seed["rail_currents"] == []
    assert seed["component_currents"] == []
    assert seed["ratings"] == []


def test_unknown_current_is_not_zero_and_placeholders_require_review(tmp_path: Path) -> None:
    result = run_seed(tmp_path)
    assert result.returncode == 0, result.stderr + result.stdout
    seed = read_json(tmp_path / "current-model-seed.json")
    placeholders = seed["manual_review_placeholders"]
    assert placeholders
    assert all(row["value_raw"] is None for row in placeholders)
    assert all(row["value_normalized"] is None for row in placeholders)
    assert all(row["requires_human_review"] is True for row in placeholders)
    assert all(row["usable_for_allocation"] is False for row in placeholders)
    assert all(row["unknown_current_treated_as_zero"] is False for row in placeholders)
    assert 0 not in [row["value_normalized"] for row in placeholders]


def test_outputs_have_no_nan_or_infinity(tmp_path: Path) -> None:
    result = run_seed(tmp_path)
    assert result.returncode == 0, result.stderr + result.stdout
    for path in tmp_path.glob("current-model-*.json"):
        for value in all_values(read_json(path)):
            if isinstance(value, float):
                assert math.isfinite(value)


def test_explicit_existing_current_model_is_preserved_without_source_mutation(tmp_path: Path) -> None:
    existing = write_json(
        tmp_path / "explicit-current-model.json",
        {
            "project": "TestProject",
            "branch_currents": [
                {
                    "branch_id": "br_v3p3_main",
                    "branch_current_a": 0.25,
                    "basis": "manual_design_requirement",
                    "confidence": 1.0,
                }
            ],
            "rail_currents": [],
            "component_currents": [],
            "ratings": [],
        },
    )
    before = existing.read_text(encoding="utf-8")
    result = run_seed(tmp_path, "--existing-current-model", str(existing))
    assert result.returncode == 0, result.stderr + result.stdout
    after = existing.read_text(encoding="utf-8")
    seed = read_json(tmp_path / "current-model-seed.json")
    assert before == after
    assert seed["branch_currents"] == read_json(existing)["branch_currents"]
    assert seed["current_model_seed_metadata"]["source"] == "existing_current_model"
    assert read_json(tmp_path / "current-model-seed-status.json")["safe_for_allocation"] is True


def test_current_model_ingest_accepts_empty_manual_seed_without_fabricating_current(tmp_path: Path) -> None:
    result = run_seed(tmp_path)
    assert result.returncode == 0, result.stderr + result.stdout
    out = tmp_path / "current-models-normalized.json"
    ingest = subprocess.run(
        [
            sys.executable,
            str(INGEST_SCRIPT),
            "--project",
            "TestProject",
            "--current-model",
            str(tmp_path / "current-model-seed.json"),
            "--out",
            str(out),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert ingest.returncode == 0, ingest.stderr + ingest.stdout
    artifact = read_json(out)
    assert artifact["summary"]["input_record_count"] == 0
    assert artifact["summary"]["normalized_count"] == 0
    assert artifact["summary"]["manual_review_placeholder_count"] > 0
    assert artifact["normalized_currents"] == []
    seed = read_json(tmp_path / "current-model-seed.json")
    assert all(row["value_normalized"] is None for row in seed["manual_review_placeholders"])


def test_no_shell_true_or_ai_network_imports() -> None:
    tree = ast.parse(SEED_SCRIPT.read_text(encoding="utf-8"))
    banned = {"requests", "httpx", "aiohttp", "openai", "litellm"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not ({alias.name.split(".")[0] for alias in node.names} & banned)
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] not in banned
        if isinstance(node, ast.Call):
            for keyword in node.keywords:
                assert not (keyword.arg == "shell" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True)
