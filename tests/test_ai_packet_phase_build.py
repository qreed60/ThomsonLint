from __future__ import annotations

import json
import math
import subprocess
import sys
import importlib.util
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ai_packet_phase_build.py"
DOC = ROOT / "docs" / "ai_packet_phase_driver.md"


def run_build(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )


def write_json(path: Path, data: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def mdi(
    item_id: str,
    category: str,
    target_type: str,
    target_id: str,
    *,
    refdes: str | None = None,
    mpn: str | None = None,
    affected_components: list[str] | None = None,
    affected_rails: list[str] | None = None,
    affected_branches: list[str] | None = None,
    blocks: list[str] | None = None,
    recommended: str = "datasheet_extraction",
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "manifest_id": item_id,
        "source_missing_data_id": f"source_{item_id}",
        "category": category,
        "target_type": target_type,
        "target_id": target_id,
        "normalized_target": target_id,
        "affected_components": affected_components if affected_components is not None else ([refdes] if refdes else []),
        "affected_rails": affected_rails or [],
        "affected_branches": affected_branches or [],
        "blocks": blocks or ["copper_calculation"],
        "priority": "medium",
        "resolution_path": recommended,
        "group_id": f"group_{category}_{target_id}",
        "evidence": [],
        "notes": f"{category} for {target_id}",
    }
    if refdes:
        row["refdes"] = refdes
    if mpn:
        row["mpn"] = mpn
    return row


def manifest_fixture(items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "project": "TestProject",
        "manifest_items": items
        if items is not None
        else [
            mdi("mdi_u2_current", "branch_current_unknown", "component", "U2", refdes="U2", affected_rails=["V3P3"]),
            mdi("mdi_u1_role", "component_role_unknown", "component", "U1", refdes="U1", blocks=["current_allocation"], recommended="ai_rule_packet"),
            mdi("mdi_f1_rating", "rating_missing", "component", "F1", refdes="F1", blocks=["fuse_margin"]),
        ],
        "groups": [],
        "warnings": [],
        "errors": [],
    }


def bom_fixture() -> dict[str, Any]:
    return {
        "project": "TestProject",
        "components": [
            {"refdes": "U1", "mpn": "REG-123", "manufacturer": "RegCo", "value": "3V3", "description": "Regulator"},
            {"refdes": "U2", "mpn": "MCU-456", "manufacturer": "ChipCo", "value": "MCU", "description": "Controller"},
            {"refdes": "F1", "mpn": "FUSE-789", "manufacturer": "FuseCo", "value": "1A", "description": "Fuse"},
            {"refdes": "J1", "mpn": "CONN-001", "manufacturer": "ConnCo", "value": "2x5", "description": "Connector"},
        ],
    }


def schematic_fixture() -> dict[str, Any]:
    return {
        "project": "TestProject",
        "components": [
            {
                "refdes": "U2",
                "value": "MCU",
                "footprint": "QFN",
                "part_number": "MCU-456",
                "bom": {"description": "Controller", "manufacturer": "ChipCo", "mpn": "MCU-456", "quantity": "1", "dnp": None},
                "pins": [{"pin": "1", "net": "V3P3"}],
            },
            {"refdes": "U9", "value": "OTHER", "part_number": "OTHER-999"},
        ],
    }


def invoke(
    tmp_path: Path,
    items: list[dict[str, Any]] | None = None,
    *,
    max_items_per_packet: int | None = None,
    with_bom: bool = True,
    bom: dict[str, Any] | None = None,
    with_schematic: bool = False,
    schematic: dict[str, Any] | None = None,
    with_datasheet_manifest: bool = True,
    datasheet_evidence_index: dict[str, Any] | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    manifest = write_json(tmp_path / "manifest.json", manifest_fixture(items))
    out_dir = tmp_path / "exports" / "TestProject" / "ai_packets" / "phase_12"
    args = [
        "--project",
        "TestProject",
        "--missing-data-manifest",
        str(manifest),
        "--out-dir",
        str(out_dir),
        "--phase-id",
        "12",
        "--phase-name",
        "AI Data Completion",
    ]
    if max_items_per_packet is not None:
        args.extend(["--max-items-per-packet", str(max_items_per_packet)])
    if with_bom:
        args.extend(["--bom", str(write_json(tmp_path / "bom.json", bom if bom is not None else bom_fixture()))])
    if with_schematic:
        args.extend(["--schematic-export", str(write_json(tmp_path / "schematic.json", schematic if schematic is not None else schematic_fixture()))])
    if with_datasheet_manifest:
        args.extend(["--datasheet-manifest", str(write_json(tmp_path / "datasheets.json", {"datasheets": []}))])
    if datasheet_evidence_index is not None:
        args.extend(["--datasheet-evidence-index", str(write_json(tmp_path / "datasheet-evidence-index.json", datasheet_evidence_index))])
    return run_build(*args), out_dir


def queue(out_dir: Path) -> dict[str, Any]:
    return read_json(out_dir / "packet_queue.json")


def phase_status(out_dir: Path) -> dict[str, Any]:
    return read_json(out_dir / "phase_status.json")


def packets(out_dir: Path) -> list[dict[str, Any]]:
    return queue(out_dir)["packets"]


def only_packet(out_dir: Path) -> dict[str, Any]:
    rows = packets(out_dir)
    assert len(rows) == 1
    return rows[0]


def all_values(value: Any) -> list[Any]:
    values = [value]
    if isinstance(value, dict):
        for child in value.values():
            values.extend(all_values(child))
    elif isinstance(value, list):
        for child in value:
            values.extend(all_values(child))
    return values


def load_packet_build_module():
    spec = importlib.util.spec_from_file_location("ai_packet_phase_build", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def current_evidence_index() -> dict[str, Any]:
    return {
        "documents": [
            {
                "document_id": "doc_u2",
                "filename": "MCU-456.pdf",
                "source_file": "datasheets/MCU-456.pdf",
                "matched_mpn": "MCU-456",
                "matched_refdes": ["U2"],
                "text_extraction_status": "ok",
                "page_evidence_blocks": [
                    {
                        "evidence_block_id": "p1_general",
                        "page": 1,
                        "text_snippet": "General description for the MCU-456 controller.",
                    },
                    {
                        "evidence_block_id": "p4_absmax",
                        "page": 4,
                        "text_snippet": "Absolute maximum ratings: output current IO 20 mA. Stresses beyond ratings may damage the device.",
                    },
                    {
                        "evidence_block_id": "p8_static",
                        "page": 8,
                        "text_snippet": "Electrical characteristics. Static characteristics. ICC supply current, Max 10 uA.",
                    },
                ],
            }
        ]
    }


def test_missing_manifest_exits_2(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    result = run_build("--project", "TestProject", "--missing-data-manifest", str(tmp_path / "missing.json"), "--out-dir", str(out_dir))
    assert result.returncode == 2
    assert not out_dir.exists()


def test_malformed_manifest_exits_2(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not-json", encoding="utf-8")
    out_dir = tmp_path / "out"
    result = run_build("--project", "TestProject", "--missing-data-manifest", str(bad), "--out-dir", str(out_dir))
    assert result.returncode == 2
    assert not out_dir.exists()


def test_output_directory_shape_created(tmp_path: Path) -> None:
    result, out_dir = invoke(tmp_path)
    assert result.returncode == 0, result.stderr + result.stdout
    assert (out_dir / "packet_queue.json").exists()
    assert (out_dir / "phase_status.json").exists()
    assert (out_dir / "phase_12_summary.json").exists()
    assert (out_dir / "packets").is_dir()


def test_packet_queue_has_expected_top_level_shape(tmp_path: Path) -> None:
    result, out_dir = invoke(tmp_path)
    assert result.returncode == 0, result.stderr + result.stdout
    expected = {"project", "phase_id", "phase_name", "schema_version", "generated_at_utc", "source_artifacts", "packets", "summary", "errors", "warnings"}
    assert expected.issubset(queue(out_dir))


def test_phase_status_has_expected_shape(tmp_path: Path) -> None:
    result, out_dir = invoke(tmp_path)
    assert result.returncode == 0, result.stderr + result.stdout
    expected = {
        "project",
        "phase_id",
        "phase_name",
        "status",
        "packet_count",
        "pending_count",
        "accepted_count",
        "rejected_count",
        "human_review_count",
        "retry_count",
        "source_artifacts",
        "errors",
        "warnings",
    }
    assert expected.issubset(phase_status(out_dir))


def test_packet_ids_are_unique(tmp_path: Path) -> None:
    result, out_dir = invoke(tmp_path)
    assert result.returncode == 0, result.stderr + result.stdout
    ids = [packet["packet_id"] for packet in packets(out_dir)]
    assert len(ids) == len(set(ids))


def test_packet_ids_are_deterministic(tmp_path: Path) -> None:
    result1, out1 = invoke(tmp_path / "a")
    result2, out2 = invoke(tmp_path / "b")
    assert result1.returncode == 0, result1.stderr + result1.stdout
    assert result2.returncode == 0, result2.stderr + result2.stdout
    assert [packet["packet_id"] for packet in packets(out1)] == [packet["packet_id"] for packet in packets(out2)]


def test_summary_counts_match_packet_files(tmp_path: Path) -> None:
    result, out_dir = invoke(tmp_path)
    assert result.returncode == 0, result.stderr + result.stdout
    artifact = queue(out_dir)
    packet_dirs = [path for path in (out_dir / "packets").iterdir() if path.is_dir()]
    assert artifact["summary"]["packet_count"] == len(artifact["packets"]) == len(packet_dirs)
    assert phase_status(out_dir)["packet_count"] == len(packet_dirs)


def test_output_json_has_no_nan_or_infinity(tmp_path: Path) -> None:
    result, out_dir = invoke(tmp_path)
    assert result.returncode == 0, result.stderr + result.stdout
    for path in out_dir.rglob("*.json"):
        for value in all_values(read_json(path)):
            assert not (isinstance(value, float) and not math.isfinite(value))


def test_current_evidence_scoring_is_json_safe_for_sets_lists_dicts_and_nulls() -> None:
    module = load_packet_build_module()
    row = {
        "terms": {"supply current", "static characteristics"},
        "nested": [{"value": None}, {"quote": "ICC supply current Max 10 uA"}],
    }
    assert module.datasheet_current_evidence_score(row) > 0


def test_pr26_builds_with_nested_page_evidence_blocks(tmp_path: Path) -> None:
    result, out_dir = invoke(
        tmp_path,
        [mdi("mdi_current", "branch_current_unknown", "component", "U2", refdes="U2")],
        datasheet_evidence_index={"wrapper": {"documents": current_evidence_index()["documents"]}},
    )
    assert result.returncode == 0, result.stderr + result.stdout
    context = read_json(out_dir / only_packet(out_dir)["context_path"])
    assert any(row.get("evidence_block_id") == "p8_static" for row in context["datasheet_references"])


def test_branch_current_unknown_routes_to_stage_12b_current_extraction(tmp_path: Path) -> None:
    result, out_dir = invoke(tmp_path, [mdi("mdi_current", "branch_current_unknown", "component", "U2", refdes="U2")])
    assert result.returncode == 0, result.stderr + result.stdout
    packet = only_packet(out_dir)
    assert packet["stage_id"] == "12B"
    assert packet["packet_type"] == "datasheet_current_extraction"


def test_branch_current_unknown_prefers_active_affected_component_over_passives(tmp_path: Path) -> None:
    result, out_dir = invoke(
        tmp_path,
        [
            mdi(
                "mdi_branch",
                "branch_current_unknown",
                "branch",
                "br_v3p3",
                affected_components=["C10", "R4", "P1", "U2"],
            )
        ],
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert only_packet(out_dir)["target_refdes"] == "U2"


def test_current_packetization_covers_distinct_active_ic_bom_parts(tmp_path: Path) -> None:
    bom = {
        "components": [
            {"refdes": "U40, U41", "mpn": "74LVC157A", "manufacturer": "Nexperia", "description": "mux"},
            {"refdes": "U46", "mpn": "TS5A22362", "manufacturer": "TI", "description": "mux"},
            {"refdes": "U50", "mpn": "PCA9515A", "manufacturer": "NXP", "description": "repeater"},
            {"refdes": "C40", "mpn": "CAP-1", "manufacturer": "CapCo", "description": "capacitor"},
        ]
    }
    result, out_dir = invoke(
        tmp_path,
        [
            mdi(
                "mdi_branch",
                "branch_current_unknown",
                "branch",
                "br_v3p3",
                affected_components=["C40", "U40", "U41", "U46", "U50"],
            )
        ],
        bom=bom,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert {packet["target_refdes"] for packet in packets(out_dir)} == {"U40", "U46", "U50"}


def test_current_model_missing_routes_to_stage_12b(tmp_path: Path) -> None:
    result, out_dir = invoke(tmp_path, [mdi("mdi_model", "current_model_missing", "component", "U2", refdes="U2")])
    assert result.returncode == 0, result.stderr + result.stdout
    assert only_packet(out_dir)["stage_id"] == "12B"


def test_rating_missing_routes_to_stage_12c_rating_extraction(tmp_path: Path) -> None:
    result, out_dir = invoke(tmp_path, [mdi("mdi_rating", "rating_missing", "component", "F1", refdes="F1", blocks=["fuse_margin"])])
    assert result.returncode == 0, result.stderr + result.stdout
    packet = only_packet(out_dir)
    assert packet["stage_id"] == "12C"
    assert packet["packet_type"] == "datasheet_rating_extraction"


def test_component_role_unknown_routes_to_stage_12a_role_pin_extraction(tmp_path: Path) -> None:
    result, out_dir = invoke(tmp_path, [mdi("mdi_role", "component_role_unknown", "component", "U1", refdes="U1", blocks=["current_allocation"])])
    assert result.returncode == 0, result.stderr + result.stdout
    assert only_packet(out_dir)["stage_id"] == "12A"


def test_relationship_direction_unknown_routes_to_stage_12a(tmp_path: Path) -> None:
    result, out_dir = invoke(tmp_path, [mdi("mdi_rel", "relationship_direction_unknown", "relationship", "rel1", affected_components=["U1"])])
    assert result.returncode == 0, result.stderr + result.stdout
    assert only_packet(out_dir)["stage_id"] == "12A"


def test_geometry_missing_does_not_route_to_datasheet_ai_by_default(tmp_path: Path) -> None:
    result, out_dir = invoke(tmp_path, [mdi("mdi_cu", "copper_thickness_missing", "branch", "br1", affected_branches=["br1"])])
    assert result.returncode == 0, result.stderr + result.stdout
    assert queue(out_dir)["packets"] == []
    assert queue(out_dir)["summary"]["skipped_item_count"] == 1


def test_unknown_category_routes_to_human_review_or_skipped_with_warning(tmp_path: Path) -> None:
    result, out_dir = invoke(tmp_path, [mdi("mdi_unknown", "future_unknown_category", "component", "U9", refdes="U9")])
    assert result.returncode == 0, result.stderr + result.stdout
    artifact = queue(out_dir)
    assert artifact["warnings"]
    assert only_packet(out_dir)["human_review_needed"] is True


def test_each_packet_has_request_context_prompt_and_status_files(tmp_path: Path) -> None:
    result, out_dir = invoke(tmp_path)
    assert result.returncode == 0, result.stderr + result.stdout
    for packet in packets(out_dir):
        packet_dir = out_dir / "packets" / packet["packet_id"]
        assert (packet_dir / "request.json").exists()
        assert (packet_dir / "context.json").exists()
        assert (packet_dir / "prompt.md").exists()
        assert (packet_dir / "status.json").exists()


def test_prompt_contains_no_guessing_guardrail(tmp_path: Path) -> None:
    result, out_dir = invoke(tmp_path, [mdi("mdi_current", "branch_current_unknown", "component", "U2", refdes="U2")])
    assert result.returncode == 0, result.stderr + result.stdout
    prompt = (out_dir / only_packet(out_dir)["prompt_path"]).read_text(encoding="utf-8")
    assert "Do not guess." in prompt
    assert "return unknown" in prompt


def test_prompt_contains_evidence_requirement(tmp_path: Path) -> None:
    result, out_dir = invoke(tmp_path, [mdi("mdi_current", "branch_current_unknown", "component", "U2", refdes="U2")])
    assert result.returncode == 0, result.stderr + result.stdout
    prompt = (out_dir / only_packet(out_dir)["prompt_path"]).read_text(encoding="utf-8")
    assert "Every extracted numeric value must include unit and evidence" in prompt


def test_12b_prompt_requires_exact_component_current_model_target_type(tmp_path: Path) -> None:
    result, out_dir = invoke(tmp_path, [mdi("mdi_current", "branch_current_unknown", "component", "Q2", refdes="Q2")])
    assert result.returncode == 0, result.stderr + result.stdout
    packet = only_packet(out_dir)
    prompt = (out_dir / packet["prompt_path"]).read_text(encoding="utf-8")

    assert packet["packet_type"] == "datasheet_current_extraction"
    assert "Use target_type exactly as provided in request.json for component operating-current items." in prompt
    assert "For component operating-current items in this packet, target_type must be component_current_model." in prompt
    assert "Do not replace component operating-current target_type with component class words such as connector, mosfet, capacitor, resistor, regulator, fuse, IC, or diode." in prompt
    assert "Component class may be described in notes or evidence, but not in component operating-current target_type." in prompt


def test_12b_prompt_routes_connector_current_rating_as_capability_not_branch_current(tmp_path: Path) -> None:
    result, out_dir = invoke(tmp_path, [mdi("mdi_current", "branch_current_unknown", "component", "P4", refdes="P4")])
    assert result.returncode == 0, result.stderr + result.stdout
    prompt = (out_dir / only_packet(out_dir)["prompt_path"]).read_text(encoding="utf-8")

    assert "emit it as target_type connector_rating with field_name current_max" in prompt
    assert "Connector current ratings are rating/capability candidates only" in prompt
    assert "must not claim to resolve branch_current_a or any actual load/operating current" in prompt
    assert "especially wire gauge/contact condition when present, such as AC/DC, AWG #22" in prompt


def test_12b_request_and_context_expose_expected_target_type(tmp_path: Path) -> None:
    result, out_dir = invoke(tmp_path, [mdi("mdi_current", "current_model_missing", "component", "P4", refdes="P4")])
    assert result.returncode == 0, result.stderr + result.stdout
    packet = only_packet(out_dir)
    request = read_json(out_dir / "packets" / packet["packet_id"] / "request.json")
    context = read_json(out_dir / packet["context_path"])

    assert packet["target_type"] == "component_current_model"
    assert packet["expected_target_type"] == "component_current_model"
    assert packet["allowed_target_type"] == "component_current_model"
    assert request["target_type"] == "component_current_model"
    assert request["expected_target_type"] == "component_current_model"
    assert request["allowed_target_type"] == "component_current_model"
    assert context["target_type"] == "component_current_model"
    assert context["expected_target_type"] == "component_current_model"
    assert context["allowed_target_type"] == "component_current_model"
    allowed = [
        "component_current_model",
        "connector_rating",
        "connector_pin_rating",
        "fuse_rating",
        "regulator_rating",
        "load_switch_rating",
        "ferrite_rating",
    ]
    assert request["allowed_extracted_target_types"] == allowed
    assert context["allowed_extracted_target_types"] == allowed


def test_12b_context_prioritizes_current_datasheet_evidence(tmp_path: Path) -> None:
    result, out_dir = invoke(
        tmp_path,
        [mdi("mdi_current", "branch_current_unknown", "component", "U2", refdes="U2")],
        datasheet_evidence_index=current_evidence_index(),
    )
    assert result.returncode == 0, result.stderr + result.stdout
    context = read_json(out_dir / only_packet(out_dir)["context_path"])
    rows = context["datasheet_references"]
    assert rows[0]["evidence_block_id"] == "p8_static"
    assert "ICC supply current" in rows[0]["evidence_quote"]


def test_12b_context_does_not_rank_absolute_max_above_supply_characteristics(tmp_path: Path) -> None:
    result, out_dir = invoke(
        tmp_path,
        [mdi("mdi_current", "branch_current_unknown", "component", "U2", refdes="U2")],
        datasheet_evidence_index=current_evidence_index(),
    )
    assert result.returncode == 0, result.stderr + result.stdout
    context = read_json(out_dir / only_packet(out_dir)["context_path"])
    ids = [row.get("evidence_block_id") for row in context["datasheet_references"]]
    assert ids[0] == "p8_static"
    if "p4_absmax" in ids:
        assert ids.index("p8_static") < ids.index("p4_absmax")


def test_prompt_forbids_findings_pass_fail_and_compliance(tmp_path: Path) -> None:
    result, out_dir = invoke(tmp_path, [mdi("mdi_current", "branch_current_unknown", "component", "U2", refdes="U2")])
    assert result.returncode == 0, result.stderr + result.stdout
    prompt = (out_dir / only_packet(out_dir)["prompt_path"]).read_text(encoding="utf-8")
    assert "Do not produce findings" in prompt
    assert "Do not produce pass/fail" in prompt
    assert "Do not produce compliance judgments" in prompt


def test_context_is_bounded_to_relevant_missing_items(tmp_path: Path) -> None:
    items = [
        mdi("mdi_u2_1", "branch_current_unknown", "component", "U2", refdes="U2"),
        mdi("mdi_u2_2", "current_model_missing", "component", "U2", refdes="U2"),
        mdi("mdi_u3", "current_model_missing", "component", "U3", refdes="U3"),
    ]
    result, out_dir = invoke(tmp_path, items)
    assert result.returncode == 0, result.stderr + result.stdout
    for packet in packets(out_dir):
        context = read_json(out_dir / packet["context_path"])
        assert {item["manifest_id"] for item in context["missing_data_items"]} == set(packet["missing_data_item_ids"])


def test_actual_testproject_bom_field_names_populate_target_metadata(tmp_path: Path) -> None:
    bom = read_json(ROOT / "TestProject" / "post_conversion" / "TestProject-bom.json")
    result, out_dir = invoke(tmp_path, [mdi("mdi_c40", "rating_missing", "component", "C40", refdes="C40")], bom=bom)
    assert result.returncode == 0, result.stderr + result.stdout
    packet = only_packet(out_dir)
    assert packet["target_mpn"] == "GRM155R71H104KE14D"
    assert packet["target_manufacturer"] == "Murata"
    assert packet["target_value"] == "0.1UF"
    assert packet["target_description"] == "CAP_CER_0.1UF_50V_10%_X7R_0402"


def test_bom_refdes_index_supports_multi_refdes_rows(tmp_path: Path) -> None:
    bom = {
        "items": [
            {
                "Ref Des": "C1, C2 C3",
                "Value": "0.1UF",
                "Description": "shared capacitor row",
                "Manufacturer": "CapCo",
                "MPN": "CAP-001",
            }
        ]
    }
    result, out_dir = invoke(tmp_path, [mdi("mdi_c2", "rating_missing", "component", "C2", refdes="C2")], bom=bom)
    assert result.returncode == 0, result.stderr + result.stdout
    packet = only_packet(out_dir)
    assert packet["target_mpn"] == "CAP-001"
    context = read_json(out_dir / packet["context_path"])
    assert context["target_bom_evidence"][0]["matched_refdes"] == "C2"


def test_request_json_contains_enriched_target_fields(tmp_path: Path) -> None:
    result, out_dir = invoke(tmp_path, [mdi("mdi_current", "branch_current_unknown", "component", "U2", refdes="U2")])
    assert result.returncode == 0, result.stderr + result.stdout
    packet = only_packet(out_dir)
    request = read_json(out_dir / "packets" / packet["packet_id"] / "request.json")
    assert request["target_mpn"] == "MCU-456"
    assert request["target_bom_row_id"] is not None
    assert request["target_bom_evidence"][0]["source"] == "bom"


def test_context_json_contains_bounded_target_bom_evidence(tmp_path: Path) -> None:
    result, out_dir = invoke(tmp_path, [mdi("mdi_current", "branch_current_unknown", "component", "U2", refdes="U2")])
    assert result.returncode == 0, result.stderr + result.stdout
    packet = only_packet(out_dir)
    context = read_json(out_dir / packet["context_path"])
    assert len(context["target_bom_evidence"]) == 1
    assert context["target_bom_evidence"][0]["fields"]["mpn"] == "MCU-456"
    assert "target_placement" in context["target_bom_evidence"][0]


def test_unmatched_refdes_remains_without_mpn_and_records_reason(tmp_path: Path) -> None:
    result, out_dir = invoke(tmp_path, [mdi("mdi_u9", "branch_current_unknown", "component", "U9", refdes="U9")])
    assert result.returncode == 0, result.stderr + result.stdout
    packet = only_packet(out_dir)
    assert packet["target_mpn"] is None
    assert packet["target_bom_match_status"] == "no_component_bom_match"
    assert "no BOM row matched" in packet["target_bom_match_reason"]


def test_rail_names_are_not_given_fake_mpns(tmp_path: Path) -> None:
    result, out_dir = invoke(tmp_path, [mdi("mdi_v5p0", "rail_current_unknown", "rail", "V5P0", affected_rails=["V5P0"])])
    assert result.returncode == 0, result.stderr + result.stdout
    packet = only_packet(out_dir)
    assert packet["target_refdes"] == "V5P0"
    assert packet["target_mpn"] is None
    assert packet["target_bom_evidence"] == []


def test_no_unrelated_bom_rows_are_included_in_packet_context(tmp_path: Path) -> None:
    result, out_dir = invoke(tmp_path, [mdi("mdi_current", "branch_current_unknown", "component", "U2", refdes="U2")])
    assert result.returncode == 0, result.stderr + result.stdout
    context = read_json(out_dir / only_packet(out_dir)["context_path"])
    serialized = json.dumps(context["target_bom_evidence"])
    assert "MCU-456" in serialized
    assert "REG-123" not in serialized
    assert "FUSE-789" not in serialized


def test_schematic_evidence_is_bounded_when_included(tmp_path: Path) -> None:
    result, out_dir = invoke(
        tmp_path,
        [mdi("mdi_current", "branch_current_unknown", "component", "U2", refdes="U2")],
        with_schematic=True,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    context = read_json(out_dir / only_packet(out_dir)["context_path"])
    assert len(context["schematic_snippets"]) == 1
    snippet = context["schematic_snippets"][0]
    assert snippet["source"] == "schematic_export"
    assert snippet["bom"]["mpn"] == "MCU-456"
    assert "pins" not in snippet


def test_ai_packet_builder_uses_no_shell_true_or_network_ai_imports() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "shell=True" not in text
    for forbidden in ("requests", "httpx", "aiohttp", "openai", "litellm"):
        assert f"import {forbidden}" not in text
        assert f"from {forbidden}" not in text


def test_missing_optional_datasheet_manifest_is_warning_not_failure(tmp_path: Path) -> None:
    result, out_dir = invoke(tmp_path, [mdi("mdi_current", "branch_current_unknown", "component", "U2", refdes="U2")], with_datasheet_manifest=False)
    assert result.returncode == 0, result.stderr + result.stdout
    assert queue(out_dir)["summary"]["missing_datasheet_context_count"] == 1
    assert queue(out_dir)["warnings"]


def test_missing_optional_bom_is_warning_not_failure(tmp_path: Path) -> None:
    result, out_dir = invoke(tmp_path, [mdi("mdi_current", "branch_current_unknown", "component", "U2", refdes="U2")], with_bom=False)
    assert result.returncode == 0, result.stderr + result.stdout
    assert queue(out_dir)["summary"]["missing_bom_context_count"] == 1
    assert queue(out_dir)["warnings"]


def test_packets_group_by_refdes_or_target_not_entire_board(tmp_path: Path) -> None:
    items = [
        mdi("mdi_u2", "branch_current_unknown", "component", "U2", refdes="U2"),
        mdi("mdi_u3", "branch_current_unknown", "component", "U3", refdes="U3"),
    ]
    result, out_dir = invoke(tmp_path, items)
    assert result.returncode == 0, result.stderr + result.stdout
    assert {packet["target_refdes"] for packet in packets(out_dir)} == {"U2", "U3"}


def test_max_items_per_packet_is_respected(tmp_path: Path) -> None:
    items = [mdi(f"mdi_u2_{idx}", "branch_current_unknown", "component", "U2", refdes="U2") for idx in range(5)]
    result, out_dir = invoke(tmp_path, items, max_items_per_packet=2)
    assert result.returncode == 0, result.stderr + result.stdout
    assert all(len(packet["missing_data_item_ids"]) <= 2 for packet in packets(out_dir))
    assert len(packets(out_dir)) == 3


def test_high_risk_items_are_not_grouped_into_large_packets(tmp_path: Path) -> None:
    items = [mdi(f"mdi_f1_{idx}", "rating_missing", "component", "F1", refdes="F1", blocks=["fuse_margin"]) for idx in range(3)]
    result, out_dir = invoke(tmp_path, items, max_items_per_packet=5)
    assert result.returncode == 0, result.stderr + result.stdout
    assert all(len(packet["missing_data_item_ids"]) == 1 for packet in packets(out_dir))


def test_docs_describe_phase_stage_packet_model() -> None:
    text = DOC.read_text(encoding="utf-8")
    assert "Phase -> Stage -> Packet -> Item" in text


def test_docs_state_ai_does_not_mutate_core_artifacts() -> None:
    text = DOC.read_text(encoding="utf-8")
    assert "does not mutate core topology artifacts" in text


def test_docs_state_datasheets_are_primary_evidence_for_component_facts() -> None:
    text = DOC.read_text(encoding="utf-8")
    assert "Datasheets are the primary evidence source for component facts" in text
