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
