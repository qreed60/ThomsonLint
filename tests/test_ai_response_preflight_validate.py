from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


SCRIPT = Path("scripts/ai_response_preflight_validate.py")


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def make_packet(tmp_path: Path) -> tuple[Path, Path, Path]:
    packet_dir = tmp_path / "packets" / "12B-001"
    packet_dir.mkdir(parents=True)

    write_json(
        packet_dir / "request.json",
        {
            "packet_id": "12B-001",
            "target_type": "component_current_model",
        },
    )

    packet_list = tmp_path / "packet-list.txt"
    packet_list.write_text(str(packet_dir) + "\n")

    responses = tmp_path / "responses"
    responses.mkdir()

    return packet_dir, packet_list, responses


def base_response() -> dict:
    return {
        "packet_id": "12B-001",
        "schema_version": "ai_extraction_result_v1",
        "status": "completed",
        "notes": [],
        "warnings": [],
        "extracted_items": [],
        "unknown_items": [
            {
                "missing_data_item_id": "mdi_current_unknown",
                "target_type": "component_current_model",
                "target_refdes": "U1",
                "field_name": "max_current_a",
                "reason_code": "not_found_in_provided_context",
                "detail": "No explicit current was found in the provided packet context.",
            }
        ],
    }


def valid_extracted_item(item_id: str = "12B-001:connector_rating:current_max:P20:000001") -> dict:
    return {
        "item_id": item_id,
        "missing_data_item_ids": ["mdi_current_unknown"],
        "missing_data_item_id": "mdi_current_unknown",
        "target_type": "connector_rating",
        "target_refdes": "P20",
        "field_name": "current_max",
        "value": 8.5,
        "unit": "A",
        "condition": "current derating table; application dependent",
        "basis": "datasheet_rating_evidence",
        "source_file": "datasheets/molex.pdf",
        "evidence_quote": "CURRENT DERATING 18 AWG 8.5 Amps",
        "confidence": 0.7,
    }


def run_preflight(responses: Path, packet_list: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--responses-dir",
            str(responses),
            "--packet-list",
            str(packet_list),
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def test_preflight_accepts_valid_response(tmp_path: Path) -> None:
    _, packet_list, responses = make_packet(tmp_path)
    write_json(responses / "12B-001.json", base_response())

    result = run_preflight(responses, packet_list)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "response_preflight_pass True" in result.stdout
    assert "expected_packet_count 1" in result.stdout
    assert "response_count 1" in result.stdout
    assert "missing_response_ids []" in result.stdout
    assert "total_unknown_items 1" in result.stdout
    assert "total_extracted_items 0" in result.stdout
    assert "unknowns_by_field_name {'max_current_a': 1}" in result.stdout
    assert "extracted_items_by_target_type_field_name {}" in result.stdout
    assert "unknown_only_packet_ids ['12B-001']" in result.stdout
    assert "unknown_only_12b_count 0" in result.stdout
    assert "unknown_only_by_target_prefix" in result.stdout
    assert "unknown_only_by_target_class" in result.stdout
    assert "duplicate_extracted_fact_count 0" in result.stdout
    assert "rating_candidates_count 0" in result.stdout
    assert "component_current_model_count 0" in result.stdout
    assert "branch_current_only_unknown_count 0" in result.stdout
    assert "context_selection_suspect_count 0" in result.stdout
    assert "top_missing_evidence_reasons" in result.stdout


def test_preflight_rejects_missing_warnings(tmp_path: Path) -> None:
    _, packet_list, responses = make_packet(tmp_path)
    response = base_response()
    response.pop("warnings")
    write_json(responses / "12B-001.json", response)

    result = run_preflight(responses, packet_list)

    assert result.returncode != 0
    assert "warnings not list" in result.stdout


def test_preflight_rejects_bad_reason_code(tmp_path: Path) -> None:
    _, packet_list, responses = make_packet(tmp_path)
    response = base_response()
    response["unknown_items"][0]["reason_code"] = "model_marked_unknown"
    write_json(responses / "12B-001.json", response)

    result = run_preflight(responses, packet_list)

    assert result.returncode != 0
    assert "bad reason_code" in result.stdout


def test_preflight_reports_unknown_only_target_class_and_context_suspect(tmp_path: Path) -> None:
    packet_dir, packet_list, responses = make_packet(tmp_path)
    write_json(
        packet_dir / "request.json",
        {
            "packet_id": "12B-001",
            "stage_id": "12B",
            "packet_type": "datasheet_current_extraction",
            "target_type": "component_current_model",
            "target_refdes": "P4",
            "target_mpn": "S2B-XH-A",
            "target_description": "CON_HDR_1X2 connector",
        },
    )
    write_json(
        packet_dir / "context.json",
        {
            "datasheet_references": [
                {"evidence_quote": "Connector mechanical layout and packaging only."}
            ]
        },
    )
    response = base_response()
    response["unknown_items"][0]["target_refdes"] = "P4"
    response["unknown_items"][0]["field_name"] = "branch_current_a"
    write_json(responses / "12B-001.json", response)

    result = run_preflight(responses, packet_list)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "unknown_only_12b_count 1" in result.stdout
    assert "unknown_only_by_target_prefix {'P': 1}" in result.stdout
    assert "unknown_only_by_target_class {'connector': 1}" in result.stdout
    assert "context_selection_suspect_count 1" in result.stdout
    assert "branch_current_only_unknown_count 1" in result.stdout


def test_preflight_reports_duplicate_extracted_facts_and_rating_counts(tmp_path: Path) -> None:
    packet_dir, packet_list, responses = make_packet(tmp_path)
    write_json(
        packet_dir / "request.json",
        {
            "packet_id": "12B-001",
            "stage_id": "12B",
            "packet_type": "datasheet_current_extraction",
            "target_type": "component_current_model",
            "allowed_extracted_target_types": ["component_current_model", "connector_rating"],
            "target_refdes": "P20",
        },
    )
    response = base_response()
    response["extracted_items"] = [
        valid_extracted_item("12B-001:connector_rating:current_max:P20:000001"),
        valid_extracted_item("12B-001:connector_rating:current_max:P20:000002"),
    ]
    write_json(responses / "12B-001.json", response)

    result = run_preflight(responses, packet_list)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "duplicate_extracted_fact_count 1" in result.stdout
    assert "rating_candidates_count 2" in result.stdout


def test_preflight_rejects_extracted_item_missing_item_id(tmp_path: Path) -> None:
    packet_dir, packet_list, responses = make_packet(tmp_path)
    write_json(
        packet_dir / "request.json",
        {
            "packet_id": "12B-001",
            "stage_id": "12B",
            "packet_type": "datasheet_current_extraction",
            "target_type": "component_current_model",
            "allowed_extracted_target_types": ["component_current_model", "connector_rating"],
            "target_refdes": "P20",
            "missing_data_item_ids": ["mdi_current_unknown"],
        },
    )
    response = base_response()
    item = valid_extracted_item()
    item.pop("item_id")
    response["extracted_items"] = [item]
    write_json(responses / "12B-001.json", response)

    result = run_preflight(responses, packet_list)

    assert result.returncode != 0
    assert "missing required field item_id" in result.stdout
    assert "response_preflight_pass False" in result.stdout


def test_preflight_rejects_extracted_item_missing_missing_data_item_ids(tmp_path: Path) -> None:
    packet_dir, packet_list, responses = make_packet(tmp_path)
    write_json(
        packet_dir / "request.json",
        {
            "packet_id": "12B-001",
            "stage_id": "12B",
            "packet_type": "datasheet_current_extraction",
            "target_type": "component_current_model",
            "allowed_extracted_target_types": ["component_current_model", "connector_rating"],
            "target_refdes": "P20",
            "missing_data_item_ids": ["mdi_current_unknown"],
        },
    )
    response = base_response()
    item = valid_extracted_item()
    item.pop("missing_data_item_ids")
    response["extracted_items"] = [item]
    write_json(responses / "12B-001.json", response)

    result = run_preflight(responses, packet_list)

    assert result.returncode != 0
    assert "missing required field missing_data_item_ids" in result.stdout
    assert "response_preflight_pass False" in result.stdout


def test_preflight_rejects_duplicate_extracted_item_id(tmp_path: Path) -> None:
    packet_dir, packet_list, responses = make_packet(tmp_path)
    write_json(
        packet_dir / "request.json",
        {
            "packet_id": "12B-001",
            "stage_id": "12B",
            "packet_type": "datasheet_current_extraction",
            "target_type": "component_current_model",
            "allowed_extracted_target_types": ["component_current_model", "connector_rating"],
            "target_refdes": "P20",
            "missing_data_item_ids": ["mdi_current_unknown"],
        },
    )
    response = base_response()
    response["extracted_items"] = [valid_extracted_item("dup"), valid_extracted_item("dup")]
    write_json(responses / "12B-001.json", response)

    result = run_preflight(responses, packet_list)

    assert result.returncode != 0
    assert "duplicate item_id dup" in result.stdout
    assert "response_preflight_pass False" in result.stdout


def test_preflight_accepts_valid_extracted_item_schema_fields(tmp_path: Path) -> None:
    packet_dir, packet_list, responses = make_packet(tmp_path)
    write_json(
        packet_dir / "request.json",
        {
            "packet_id": "12B-001",
            "stage_id": "12B",
            "packet_type": "datasheet_current_extraction",
            "target_type": "component_current_model",
            "allowed_extracted_target_types": ["component_current_model", "connector_rating"],
            "target_refdes": "P20",
            "missing_data_item_ids": ["mdi_current_unknown"],
        },
    )
    response = base_response()
    response["extracted_items"] = [valid_extracted_item()]
    write_json(responses / "12B-001.json", response)

    result = run_preflight(responses, packet_list)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "total_extracted_items 1" in result.stdout
    assert "response_preflight_pass True" in result.stdout
