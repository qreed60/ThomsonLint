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


def write_testproject_pdf_set(tmp_path: Path, *, with_manifest: bool = False) -> tuple[Path, Path]:
    datasheets = tmp_path / ".agents_tmp" / "pre_clean_phase6_run_20260525T104621Z" / "datasheets"
    datasheets.mkdir(parents=True)
    for name in [
        "032_ONSEMI_FDS4435BZ.pdf",
        "002_Murata_GRM155R71H104KE14D.pdf",
        "014_JST_S2B-XH-A_LF_SN.pdf",
    ]:
        (datasheets / name).write_text("not a real pdf", encoding="utf-8")
    bom = tmp_path / "TestProject-bom.json"
    bom.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "refdes": ["Q2"],
                        "fields": {"manufacturer": "ONSEMI", "mpn": "FDS4435BZ", "description": "MOSFET"},
                        "manufacturers": [{"manufacturer": "ONSEMI", "mpn": "FDS4435BZ", "rank": 1}],
                        "raw_row_index": 32,
                    },
                    {
                        "refdes": ["C40", "C41"],
                        "fields": {"manufacturer": "Murata", "mpn": "GRM155R71H104KE14D", "description": "CAP"},
                        "manufacturers": [{"manufacturer": "Murata", "mpn": "GRM155R71H104KE14D", "rank": 1}],
                        "raw_row_index": 2,
                    },
                    {
                        "refdes": ["P4"],
                        "fields": {"manufacturer": "JST", "mpn": "S2B-XH-A (LF)(SN)", "description": "CON"},
                        "manufacturers": [{"manufacturer": "JST", "mpn": "S2B-XH-A (LF)(SN)", "rank": 1}],
                        "raw_row_index": 14,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    if with_manifest:
        rows = [
            {"bom_row_index": 32, "reference_designators": "Q2", "selected_manufacturer": "ONSEMI", "selected_mpn": "FDS4435BZ", "local_saved_path": str(datasheets / "032_ONSEMI_FDS4435BZ.pdf"), "status": "found"},
            {"bom_row_index": 2, "reference_designators": "C40 C41", "selected_manufacturer": "Murata", "selected_mpn": "GRM155R71H104KE14D", "local_saved_path": str(datasheets / "002_Murata_GRM155R71H104KE14D.pdf"), "status": "found"},
            {"bom_row_index": 14, "reference_designators": "P4", "selected_manufacturer": "JST", "selected_mpn": "S2B-XH-A (LF)(SN)", "local_saved_path": str(datasheets / "014_JST_S2B-XH-A_LF_SN.pdf"), "status": "found"},
        ]
        (datasheets / "datasheet_manifest.jsonl").write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return datasheets, bom


def test_explicit_datasheets_dir_with_pdfs_under_hidden_path_indexes_documents(tmp_path: Path) -> None:
    datasheets, bom = write_testproject_pdf_set(tmp_path)
    out_dir = tmp_path / "out"
    result = run_index("--project", "TestProject", "--datasheets-dir", str(datasheets), "--bom", str(bom), "--out-dir", str(out_dir), "--text-only")
    assert result.returncode == 0, result.stderr + result.stdout
    index = read_json(out_dir / "datasheet-evidence-index.json")
    assert index["summary"]["datasheet_file_count"] == 3
    assert len(index["documents"]) == 3
    assert {doc["filename"] for doc in index["documents"]} == {
        "032_ONSEMI_FDS4435BZ.pdf",
        "002_Murata_GRM155R71H104KE14D.pdf",
        "014_JST_S2B-XH-A_LF_SN.pdf",
    }


def test_filename_matching_maps_testproject_mpns_to_pdfs(tmp_path: Path) -> None:
    datasheets, bom = write_testproject_pdf_set(tmp_path)
    out_dir = tmp_path / "out"
    result = run_index("--project", "TestProject", "--datasheets-dir", str(datasheets), "--bom", str(bom), "--out-dir", str(out_dir), "--text-only")
    assert result.returncode == 0, result.stderr + result.stdout
    docs = {doc["filename"]: doc for doc in read_json(out_dir / "datasheet-evidence-index.json")["documents"]}
    assert docs["032_ONSEMI_FDS4435BZ.pdf"]["matched_mpn"] == "FDS4435BZ"
    assert docs["032_ONSEMI_FDS4435BZ.pdf"]["matched_refdes"] == ["Q2"]
    assert docs["002_Murata_GRM155R71H104KE14D.pdf"]["matched_mpn"] == "GRM155R71H104KE14D"
    assert docs["002_Murata_GRM155R71H104KE14D.pdf"]["matched_refdes"] == ["C40", "C41"]
    assert docs["014_JST_S2B-XH-A_LF_SN.pdf"]["matched_mpn"] == "S2B-XH-A (LF)(SN)"
    assert docs["014_JST_S2B-XH-A_LF_SN.pdf"]["matched_refdes"] == ["P4"]


def test_datasheet_manifest_jsonl_is_parsed_when_present_but_not_required(tmp_path: Path) -> None:
    datasheets, bom = write_testproject_pdf_set(tmp_path, with_manifest=True)
    out_dir = tmp_path / "with_manifest"
    result = run_index("--project", "TestProject", "--datasheets-dir", str(datasheets), "--bom", str(bom), "--out-dir", str(out_dir), "--text-only")
    assert result.returncode == 0, result.stderr + result.stdout
    status = read_json(out_dir / "datasheet-extraction-status.json")
    assert status["summary"]["datasheet_manifest_row_count"] == 3
    docs = read_json(out_dir / "datasheet-evidence-index.json")["documents"]
    assert all(doc["matched_manifest_entry"] for doc in docs)

    datasheets_without_manifest, bom_without_manifest = write_testproject_pdf_set(tmp_path / "no_manifest")
    out_dir_without_manifest = tmp_path / "without_manifest"
    result_without_manifest = run_index("--project", "TestProject", "--datasheets-dir", str(datasheets_without_manifest), "--bom", str(bom_without_manifest), "--out-dir", str(out_dir_without_manifest), "--text-only")
    assert result_without_manifest.returncode == 0, result_without_manifest.stderr + result_without_manifest.stdout
    assert read_json(out_dir_without_manifest / "datasheet-extraction-status.json")["summary"]["datasheet_manifest_row_count"] == 0


def test_empty_datasheets_dir_still_blocks_missing_datasheets(tmp_path: Path) -> None:
    datasheets = tmp_path / "datasheets"
    datasheets.mkdir()
    out_dir = tmp_path / "out"
    result = run_index("--project", "TestProject", "--datasheets-dir", str(datasheets), "--out-dir", str(out_dir), "--strict")
    assert result.returncode == 1
    blockers = read_json(out_dir / "datasheet-extraction-blockers.json")["blockers"]
    assert blockers and blockers[0]["blocker_id"] == "missing_datasheets"


def test_no_unrelated_project_datasheets_are_auto_injected(tmp_path: Path) -> None:
    explicit = tmp_path / "explicit"
    explicit.mkdir()
    (explicit / "032_ONSEMI_FDS4435BZ.pdf").write_text("not a real pdf", encoding="utf-8")
    global_like = tmp_path / "exports" / "datasheets"
    global_like.mkdir(parents=True)
    (global_like / "002_Murata_GRM155R71H104KE14D.pdf").write_text("not a real pdf", encoding="utf-8")
    out_dir = tmp_path / "out"
    result = run_index("--project", "TestProject", "--datasheets-dir", str(explicit), "--out-dir", str(out_dir), "--text-only")
    assert result.returncode == 0, result.stderr + result.stdout
    docs = read_json(out_dir / "datasheet-evidence-index.json")["documents"]
    assert [doc["filename"] for doc in docs] == ["032_ONSEMI_FDS4435BZ.pdf"]


def test_datasheet_index_safety_flags_absent_or_false(tmp_path: Path) -> None:
    result, out_dir, _ = invoke(tmp_path)
    assert result.returncode == 0, result.stderr + result.stdout
    for filename in ["datasheet-evidence-index.json", "datasheet-extraction-status.json"]:
        artifact = read_json(out_dir / filename)
        assert artifact.get("safe_for_core_apply") in (None, False)
        assert artifact.get("ready_for_core_apply") in (None, False)


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


def test_pr26_consumes_matching_document_evidence_without_unrelated_docs(tmp_path: Path) -> None:
    datasheets, bom = write_testproject_pdf_set(tmp_path)
    out_dir = tmp_path / "out"
    result = run_index("--project", "TestProject", "--datasheets-dir", str(datasheets), "--bom", str(bom), "--out-dir", str(out_dir), "--text-only")
    assert result.returncode == 0, result.stderr + result.stdout
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "project": "TestProject",
                "manifest_items": [
                    {
                        "manifest_id": "mdi_q2",
                        "category": "branch_current_unknown",
                        "target_type": "component",
                        "target_id": "Q2",
                        "refdes": "Q2",
                        "affected_components": ["Q2"],
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
        "--bom",
        str(bom),
        "--datasheet-evidence-index",
        str(out_dir / "datasheet-evidence-index.json"),
    )
    assert packet_result.returncode == 0, packet_result.stderr + packet_result.stdout
    context = read_json(packet_out / "packets" / "12B-001" / "context.json")
    filenames = {row.get("filename") for row in context["datasheet_references"]}
    assert "032_ONSEMI_FDS4435BZ.pdf" in filenames
    assert "002_Murata_GRM155R71H104KE14D.pdf" not in filenames
    assert "014_JST_S2B-XH-A_LF_SN.pdf" not in filenames
