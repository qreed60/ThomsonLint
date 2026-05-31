from __future__ import annotations

import ast
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any

import jsonschema
import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "datasheet_evidence_index.py"
PACKET_SCRIPT = ROOT / "scripts" / "ai_packet_phase_build.py"
SCHEMA = ROOT / "schemas" / "datasheet_evidence_index_schema.json"
DOC = ROOT / "docs" / "datasheet_evidence_index.md"


def run_index(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=ROOT, text=True, capture_output=True)


def run_packet(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(PACKET_SCRIPT), *args], cwd=ROOT, text=True, capture_output=True)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_text_datasheet(tmp_path: Path) -> Path:
    datasheets = tmp_path / "datasheets"
    datasheets.mkdir(parents=True)
    text = """REG-123 Datasheet
Absolute Maximum Ratings
Voltage Rating 500 mV
Power Rating 250 mW
Current Rating 1500 mA continuous
Regulator current 1 A
Regulator Output Current 800 mA
Load switch current limit 2 A
Electrical Characteristics
Rds(on) resistance 50 mOhm at VGS=4.5 V
Capacitance 10 uF
ESR 20 mOhm
Inductance 4.7 uH
Saturation Current 2500 mA
Thermal Characteristics
Thermal resistance 40 C/W
Pin Description
Connector current rating 3 A per contact
Fuse hold current 500 mA
Fuse trip current 1 A
Recommended Operating Conditions
Operating current 1-2 A depending on temperature
"""
    path = datasheets / "REG-123.txt"
    path.write_text(text, encoding="utf-8")
    return path


def invoke(tmp_path: Path, *extra: str) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    datasheet = write_text_datasheet(tmp_path)
    bom = tmp_path / "bom.json"
    bom.write_text(json.dumps({"components": [{"refdes": "U1", "mpn": "REG-123", "manufacturer": "Acme"}]}), encoding="utf-8")
    out_dir = tmp_path / "out"
    result = run_index(
        "--project",
        "TestProject",
        "--datasheets-dir",
        str(datasheet.parent),
        "--bom",
        str(bom),
        "--out-dir",
        str(out_dir),
        *extra,
    )
    return result, out_dir, datasheet


def candidates(out_dir: Path) -> list[dict[str, Any]]:
    return read_json(out_dir / "datasheet-extraction-candidates.json")["candidates"]


def find_candidate(rows: list[dict[str, Any]], parameter: str) -> dict[str, Any]:
    matches = [row for row in rows if row["parameter_name"] == parameter]
    assert matches, parameter
    return matches[0]


def all_values(value: Any) -> list[Any]:
    values = [value]
    if isinstance(value, dict):
        for child in value.values():
            values.extend(all_values(child))
    elif isinstance(value, list):
        for child in value:
            values.extend(all_values(child))
    return values


def test_parses_plain_text_fixture_and_emits_all_artifacts(tmp_path: Path) -> None:
    result, out_dir, _ = invoke(tmp_path)
    assert result.returncode == 0, result.stderr + result.stdout
    for filename in [
        "datasheet-evidence-index.json",
        "datasheet-extraction-candidates.json",
        "datasheet-extraction-status.json",
        "datasheet-extraction-blockers.json",
        "datasheet-extraction-review.json",
    ]:
        assert (out_dir / filename).exists()
    index = read_json(out_dir / "datasheet-evidence-index.json")
    assert index["documents"][0]["extracted_text_available"] is True
    assert index["documents"][0]["page_evidence_blocks"]
    assert {row["section_name"] for row in index["documents"][0]["section_candidates"]} >= {
        "Absolute Maximum Ratings",
        "Recommended Operating Conditions",
        "Electrical Characteristics",
        "Thermal Characteristics",
        "Pin Description",
    }


def test_schema_validates_outputs_and_no_nan_or_infinity(tmp_path: Path) -> None:
    result, out_dir, _ = invoke(tmp_path)
    assert result.returncode == 0, result.stderr + result.stdout
    schema = read_json(SCHEMA)
    for path in out_dir.glob("*.json"):
        artifact = read_json(path)
        jsonschema.validate(instance=artifact, schema=schema)
        assert not any(isinstance(value, float) and not math.isfinite(value) for value in all_values(artifact))


def test_extracts_required_candidate_types_with_evidence(tmp_path: Path) -> None:
    result, out_dir, _ = invoke(tmp_path)
    assert result.returncode == 0, result.stderr + result.stdout
    rows = candidates(out_dir)
    for parameter in [
        "current_rating",
        "voltage_rating",
        "power_rating",
        "fuse_hold_current",
        "fuse_trip_current",
        "connector_current_rating",
        "regulator_output_current",
        "load_switch_current_limit",
        "rds_on",
        "capacitance",
        "esr",
        "inductor_saturation_current",
        "thermal_resistance_or_derating_note",
    ]:
        row = find_candidate(rows, parameter)
        assert row["source_file"]
        assert row["page"] == 1
        assert row["evidence_quote"]
        assert row["extraction_method"] == "regex_line_context_v1"
        assert 0 <= row["confidence"] <= 1


def test_connector_rating_not_expanded_to_pins_and_regulator_side_not_inferred(tmp_path: Path) -> None:
    result, out_dir, _ = invoke(tmp_path)
    assert result.returncode == 0, result.stderr + result.stdout
    rows = candidates(out_dir)
    connector = find_candidate(rows, "connector_current_rating")
    assert connector["target_type"] == "connector"
    assert "pin" not in json.dumps(connector).lower()
    ambiguous_regulator = [row for row in rows if row["parameter_name"] == "current_rating" and "Regulator current" in row["evidence_quote"]]
    assert ambiguous_regulator
    assert ambiguous_regulator[0]["requires_human_review"] is True
    assert ambiguous_regulator[0]["value_normalized"] is None


def test_unit_normalization_and_ambiguous_range_review(tmp_path: Path) -> None:
    result, out_dir, _ = invoke(tmp_path)
    assert result.returncode == 0, result.stderr + result.stdout
    rows = candidates(out_dir)
    assert find_candidate(rows, "current_rating")["value_normalized"] == 1.5
    assert find_candidate(rows, "voltage_rating")["value_normalized"] == 0.5
    assert find_candidate(rows, "power_rating")["value_normalized"] == 0.25
    assert find_candidate(rows, "rds_on")["value_normalized"] == 0.05
    assert find_candidate(rows, "capacitance")["value_normalized"] == pytest.approx(10e-6)
    assert find_candidate(rows, "esr")["value_normalized"] == 0.02
    assert find_candidate(rows, "inductor_saturation_current")["value_normalized"] == 2.5
    range_rows = [row for row in rows if "1-2 A" in row["evidence_quote"]]
    assert range_rows
    assert range_rows[0]["requires_human_review"] is True
    assert range_rows[0]["value_normalized"] is None


def test_missing_evidence_blocks_candidate_usability(tmp_path: Path) -> None:
    result, out_dir, _ = invoke(tmp_path)
    assert result.returncode == 0, result.stderr + result.stdout
    for row in candidates(out_dir):
        if not row["evidence_quote"]:
            assert row["requires_human_review"] is True
            assert row["usable_for_ai_packet_context"] is False


def test_candidate_ids_are_deterministic_and_source_datasheet_not_modified(tmp_path: Path) -> None:
    result, out_dir, datasheet = invoke(tmp_path)
    assert result.returncode == 0, result.stderr + result.stdout
    before = datasheet.read_bytes()
    first_ids = [row["candidate_id"] for row in candidates(out_dir)]
    result2, out_dir2, _ = invoke(tmp_path / "second")
    assert result2.returncode == 0, result2.stderr + result2.stdout
    assert first_ids == [row["candidate_id"] for row in candidates(out_dir2)]
    assert datasheet.read_bytes() == before


def test_outputs_stay_inside_out_dir(tmp_path: Path) -> None:
    result, out_dir, _ = invoke(tmp_path)
    assert result.returncode == 0, result.stderr + result.stdout
    for path in out_dir.glob("*.json"):
        path.resolve().relative_to(out_dir.resolve())


def test_pdf_text_extraction_fallback_graceful(tmp_path: Path) -> None:
    datasheets = tmp_path / "datasheets"
    datasheets.mkdir()
    (datasheets / "not-a-real.pdf").write_text("not a pdf", encoding="utf-8")
    out_dir = tmp_path / "out"
    result = run_index("--project", "TestProject", "--datasheets-dir", str(datasheets), "--out-dir", str(out_dir))
    assert result.returncode == 0, result.stderr + result.stdout
    status = read_json(out_dir / "datasheet-extraction-status.json")
    assert status["summary"]["document_count"] == 1
    assert read_json(out_dir / "datasheet-evidence-index.json")["documents"][0]["extraction_warnings"]


def test_no_shell_true_or_ai_network_imports() -> None:
    banned = {"requests", "aiohttp", "httpx", "openai", "litellm"}
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for keyword in node.keywords:
                assert not (keyword.arg == "shell" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True)
        if isinstance(node, ast.Import):
            assert not ({alias.name.split(".")[0] for alias in node.names} & banned)
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] not in banned


def test_docs_state_evidence_only_and_not_directly_applied() -> None:
    text = DOC.read_text(encoding="utf-8").lower()
    assert "does not call ai" in text
    assert "not directly apply" in text or "not directly applied" in text
    assert "evidence" in text


def test_pr26_can_consume_datasheet_evidence_index_context(tmp_path: Path) -> None:
    result, out_dir, _ = invoke(tmp_path)
    assert result.returncode == 0, result.stderr + result.stdout
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "project": "TestProject",
                "manifest_items": [
                    {
                        "manifest_id": "mdi_current",
                        "category": "branch_current_unknown",
                        "target_type": "component",
                        "target_id": "U1",
                        "affected_components": ["U1"],
                        "blocks": ["copper_calculation"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    packet_out = tmp_path / "packets"
    packet_result = run_packet(
        "--project",
        "TestProject",
        "--missing-data-manifest",
        str(manifest),
        "--out-dir",
        str(packet_out),
        "--datasheet-evidence-index",
        str(out_dir / "datasheet-extraction-candidates.json"),
    )
    assert packet_result.returncode == 0, packet_result.stderr + packet_result.stdout
    context = read_json(packet_out / "packets" / "12B-001" / "context.json")
    assert any(row.get("candidate_id") for row in context["datasheet_references"])
