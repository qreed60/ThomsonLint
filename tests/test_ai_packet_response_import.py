from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import jsonschema


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ai_packet_response_import.py"
SCHEMA = ROOT / "schemas" / "ai_packet_response_import_schema.json"


def write_json(path: Path, data: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    return path


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def packet_dir_fixture(tmp_path: Path, packet_ids: list[str] | None = None) -> Path:
    packet_ids = packet_ids or ["12B-001"]
    packet_dir = tmp_path / "packet_dir"
    packets = []
    for packet_id in packet_ids:
        packets.append({
            "packet_id": packet_id,
            "stage_id": "12B",
            "packet_type": "datasheet_current_extraction",
            "target_type": "component_current_model",
            "missing_data_item_ids": ["mdi_u2_current"],
        })
        write_json(packet_dir / "packets" / packet_id / "status.json", {"packet_id": packet_id, "status": "prompt_ready"})
    write_json(packet_dir / "packet_queue.json", {"project": "TestProject", "packets": packets})
    return packet_dir


def response(packet_id: str = "12B-001") -> dict[str, Any]:
    return {
        "packet_id": packet_id,
        "schema_version": "ai_extraction_result_v1",
        "status": "completed",
        "extracted_items": [],
        "unknown_items": [],
        "notes": [],
        "warnings": [],
    }


def run_import(tmp_path: Path, packet_dir: Path, responses_dir: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--project",
            "TestProject",
            "--packet-dir",
            str(packet_dir),
            "--responses-dir",
            str(responses_dir),
            "--out-dir",
            str(tmp_path / "import"),
            *extra,
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )


def output_paths(tmp_path: Path) -> list[Path]:
    return [
        tmp_path / "import" / "ai-packet-response-import-manifest.json",
        tmp_path / "import" / "ai-packet-response-import-status.json",
        tmp_path / "import" / "ai-packet-response-import-blockers.json",
        tmp_path / "import" / "ai-packet-response-import-review.json",
        tmp_path / "import" / "ai-packet-response-import-index.json",
    ]


def test_imports_valid_response_files_matched_to_packet_ids(tmp_path: Path) -> None:
    packet_dir = packet_dir_fixture(tmp_path)
    responses_dir = tmp_path / "responses"
    source = write_json(responses_dir / "12B-001.json", response())
    before = source.read_text(encoding="utf-8")
    result = run_import(tmp_path, packet_dir, responses_dir)
    assert result.returncode == 0, result.stderr + result.stdout
    destination = packet_dir / "packets" / "12B-001" / "raw_response.json"
    assert destination.exists()
    assert source.read_text(encoding="utf-8") == before
    index = read_json(tmp_path / "import" / "ai-packet-response-import-index.json")
    record = index["responses"][0]
    assert record["packet_id"] == "12B-001"
    assert record["status"] == "imported"
    assert record["json_valid"] is True
    assert record["matched_packet"] is True
    assert record["source_sha256"] == record["imported_sha256"]


def test_supported_filename_formats(tmp_path: Path) -> None:
    packet_dir = packet_dir_fixture(tmp_path, ["12B-001", "12B-002", "12B-003", "12B-004"])
    responses_dir = tmp_path / "responses"
    write_json(responses_dir / "12B-001_raw_response.json", response("12B-001"))
    write_json(responses_dir / "packet_2_response.json", response("12B-002"))
    write_json(responses_dir / "12B-003" / "raw_response.json", response("12B-003"))
    write_json(responses_dir / "arbitrary.json", response("12B-004"))
    result = run_import(tmp_path, packet_dir, responses_dir)
    assert result.returncode == 0, result.stderr + result.stdout
    for packet_id in ["12B-001", "12B-002", "12B-003", "12B-004"]:
        assert (packet_dir / "packets" / packet_id / "raw_response.json").exists()


def test_rejects_unknown_packet_ids(tmp_path: Path) -> None:
    packet_dir = packet_dir_fixture(tmp_path)
    responses_dir = tmp_path / "responses"
    write_json(responses_dir / "12B-999.json", response("12B-999"))
    result = run_import(tmp_path, packet_dir, responses_dir, "--allow-partial")
    assert result.returncode == 1
    blockers = read_json(tmp_path / "import" / "ai-packet-response-import-blockers.json")["blockers"]
    assert any(row["blocker_id"] == "unknown_packet_response_file" for row in blockers)


def test_missing_responses_block_unless_partial_allowed(tmp_path: Path) -> None:
    packet_dir = packet_dir_fixture(tmp_path, ["12B-001", "12B-002"])
    responses_dir = tmp_path / "responses"
    write_json(responses_dir / "12B-001.json", response("12B-001"))
    result = run_import(tmp_path, packet_dir, responses_dir)
    assert result.returncode == 1
    status = read_json(tmp_path / "import" / "ai-packet-response-import-status.json")
    assert status["status"] == "blocked"
    assert status["summary"]["missing_response_count"] == 1


def test_partial_responses_allowed_but_report_missing_packets(tmp_path: Path) -> None:
    packet_dir = packet_dir_fixture(tmp_path, ["12B-001", "12B-002"])
    responses_dir = tmp_path / "responses"
    write_json(responses_dir / "12B-001.json", response("12B-001"))
    result = run_import(tmp_path, packet_dir, responses_dir, "--allow-partial")
    assert result.returncode == 0, result.stderr + result.stdout
    review = read_json(tmp_path / "import" / "ai-packet-response-import-review.json")
    assert any(row["packet_id"] == "12B-002" and row["status"] == "missing" for row in review["review_items"])


def test_invalid_json_is_rejected_to_review(tmp_path: Path) -> None:
    packet_dir = packet_dir_fixture(tmp_path)
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    (responses_dir / "12B-001.json").write_text("{bad", encoding="utf-8")
    result = run_import(tmp_path, packet_dir, responses_dir, "--allow-partial")
    assert result.returncode == 1
    index = read_json(tmp_path / "import" / "ai-packet-response-import-index.json")
    assert index["responses"][0]["json_valid"] is False
    assert index["responses"][0]["status"] == "rejected"


def test_output_artifacts_schema_and_safety_fields(tmp_path: Path) -> None:
    packet_dir = packet_dir_fixture(tmp_path)
    responses_dir = tmp_path / "responses"
    write_json(responses_dir / "12B-001.json", response())
    result = run_import(tmp_path, packet_dir, responses_dir)
    assert result.returncode == 0, result.stderr + result.stdout
    schema = read_json(SCHEMA)
    for path in output_paths(tmp_path):
        artifact = read_json(path)
        jsonschema.validate(instance=artifact, schema=schema)
        assert artifact["imported_only"] is True
        assert artifact["generated_ai_content"] is False
        assert artifact["called_ai_service"] is False
        assert artifact["fabricated_response"] is False
        assert artifact["safe_for_core_apply"] is False
        assert artifact["ready_for_core_apply"] is False


def test_imported_files_stay_under_packet_dir(tmp_path: Path) -> None:
    packet_dir = packet_dir_fixture(tmp_path)
    responses_dir = tmp_path / "responses"
    write_json(responses_dir / "12B-001.json", response())
    result = run_import(tmp_path, packet_dir, responses_dir)
    assert result.returncode == 0, result.stderr + result.stdout
    destination = Path(read_json(tmp_path / "import" / "ai-packet-response-import-index.json")["responses"][0]["destination_response_file"])
    destination.resolve().relative_to(packet_dir.resolve())


def test_no_shell_true_or_ai_network_imports() -> None:
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    banned = {"requests", "httpx", "aiohttp", "openai", "litellm"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not ({alias.name.split(".")[0] for alias in node.names} & banned)
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] not in banned
        if isinstance(node, ast.Call):
            for keyword in node.keywords:
                assert not (keyword.arg == "shell" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True)
