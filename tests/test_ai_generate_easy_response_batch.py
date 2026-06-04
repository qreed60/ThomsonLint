from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ai_generate_easy_response_batch.py"
PREFLIGHT = ROOT / "scripts" / "ai_response_preflight_validate.py"


def load_module():
    spec = importlib.util.spec_from_file_location("ai_generate_easy_response_batch", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, data: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def packet_dir(
    tmp_path: Path,
    *,
    packet_id: str = "12B-001",
    missing_id: str = "mdi_current",
    refdes: str = "U1",
    target_value: str | None = "IC",
    target_description: str | None = "IC mux",
    datasheet_references: list[dict[str, Any]] | None = None,
) -> Path:
    path = tmp_path / "packets" / packet_id
    request = {
        "packet_id": packet_id,
        "stage_id": "12B",
        "packet_type": "datasheet_current_extraction",
        "target_type": "component_current_model",
        "expected_target_type": "component_current_model",
        "allowed_extracted_target_types": [
            "component_current_model",
            "connector_rating",
            "connector_pin_rating",
            "fuse_rating",
            "regulator_rating",
            "load_switch_rating",
            "ferrite_rating",
        ],
        "target_refdes": refdes,
        "target_mpn": "PART-1",
        "target_value": target_value,
        "target_description": target_description,
        "missing_data_item_ids": [missing_id],
    }
    context = {
        "packet_id": packet_id,
        "stage_id": "12B",
        "packet_type": "datasheet_current_extraction",
        "target_type": "component_current_model",
        "target_refdes": refdes,
        "target_value": target_value,
        "target_description": target_description,
        "target_bom_evidence": [
            {
                "fields": {
                    "value": target_value,
                    "description": target_description,
                    "mpn": "PART-1",
                }
            }
        ],
        "datasheet_references": datasheet_references or [],
    }
    write_json(path / "request.json", request)
    write_json(path / "context.json", context)
    (path / "prompt.md").write_text("Do not guess.\n", encoding="utf-8")
    return path


def unknown_easy(field_name: str = "max_current_a") -> dict[str, Any]:
    return {
        "packet_id": "12B-001",
        "items": {
            "mdi_current": {
                "status": "unknown",
                "target_type": "component_current_model",
                "field_name": field_name,
                "value": None,
                "unit": None,
                "reason": "no matched datasheet evidence",
            }
        },
    }


def convert(tmp_path: Path, **kwargs: Any) -> dict[str, Any]:
    module = load_module()
    return module.convert(packet_dir(tmp_path, **kwargs), unknown_easy())


def extracted_current(result: dict[str, Any]) -> dict[str, Any]:
    rows = [
        row for row in result["extracted_items"]
        if row.get("target_type") == "component_current_model"
        and row.get("field_name") in {"max_current_a", "typ_current_a"}
    ]
    assert len(rows) == 1
    return rows[0]


def rating_items(result: dict[str, Any], target_type: str | None = None, field_name: str | None = None) -> list[dict[str, Any]]:
    rows = [
        row for row in result["extracted_items"]
        if (target_type is None or row.get("target_type") == target_type)
        and (field_name is None or row.get("field_name") == field_name)
    ]
    return rows


def assert_schema_linkage(item: dict[str, Any], *, packet_id: str = "12B-001", missing_id: str = "mdi_current") -> None:
    assert item["item_id"].startswith(f"{packet_id}:")
    assert item["missing_data_item_ids"] == [missing_id]
    assert item["missing_data_item_id"] == missing_id
    assert item["basis"]
    assert item["confidence"] is not None


def test_qwen35_2b_non_thinking_text_extraction_request_settings_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_module()
    captured: dict[str, Any] = {}

    class FakeResponse:
        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"choices":[{"message":{"content":"{}"}}]}'

    def fake_urlopen(req: Any, timeout: int) -> FakeResponse:
        captured["timeout"] = timeout
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    module.chat(
        "http://example.invalid/v1",
        "local",
        "qwen35_2b",
        [{"role": "system", "content": "json"}, {"role": "user", "content": "packet"}],
        2048,
    )

    payload = captured["payload"]
    assert captured["timeout"] == 900
    assert module.request_settings()["profile"] == "qwen35_2b_non_thinking_text_extraction"
    assert payload["model"] == "qwen35_2b"
    assert payload["temperature"] == 1.0
    assert payload["top_p"] == 1.0
    assert payload["top_k"] == 20
    assert payload["min_p"] == 0.0
    assert payload["presence_penalty"] == 2.0
    assert payload["repetition_penalty"] == 1.0
    assert payload["stream"] is False


def test_print_request_settings_smoke() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--print-request-settings"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    settings = json.loads(result.stdout)
    assert settings == {
        "profile": "qwen35_2b_non_thinking_text_extraction",
        "temperature": 1.0,
        "top_p": 1.0,
        "top_k": 20,
        "min_p": 0.0,
        "presence_penalty": 2.0,
        "repetition_penalty": 1.0,
    }


def test_ic_fallback_extracts_representative_supply_current_quotes(tmp_path: Path) -> None:
    quotes = [
        ("ICC supply current, Max 10 uA.", 0.00001),
        ("Positive supply current IDD, Max 0.8 mA.", 0.0008),
        ("Supply current normal mode, Max 3 mA.", 0.003),
    ]
    for idx, (quote, expected) in enumerate(quotes):
        result = convert(
            tmp_path / str(idx),
            datasheet_references=[
                {
                    "source_file": "datasheets/ic.pdf",
                    "page": 8,
                    "evidence_block_id": f"b{idx}",
                    "evidence_quote": quote,
                }
            ],
        )
        item = extracted_current(result)
        assert item["field_name"] == "max_current_a"
        assert item["unit"] == "A"
        assert item["value"] == expected
        assert item["source_file"] == "datasheets/ic.pdf"
        assert item["evidence_block_id"] == f"b{idx}"
        assert item["confidence"] == 0.75
        assert_schema_linkage(item)


def test_current_value_to_a_accepts_microamp_spellings() -> None:
    module = load_module()
    assert module.current_value_to_a("10", "µA") == 0.00001
    assert module.current_value_to_a("10", "µa") == 0.00001
    assert module.current_value_to_a("10", "μA") == 0.00001
    assert module.current_value_to_a("10", "μa") == 0.00001
    assert module.current_value_to_a("10", "uA") == 0.00001
    assert module.current_value_to_a("10", "nA") == 0.00000001


def test_current_value_regex_finds_greek_mu_microamps() -> None:
    module = load_module()
    match = module.CURRENT_VALUE_RE.search("ICC Positive supply current Max 3.3 μA")
    assert match
    assert match.group("value") == "3.3"
    assert match.group("unit") == "μA"


def test_ts5a_positive_supply_current_with_greek_mu_extracts(tmp_path: Path) -> None:
    result = convert(
        tmp_path,
        refdes="U46",
        target_description="IC analog mux",
        datasheet_references=[
            {
                "source_file": "datasheets/ts5a.pdf",
                "page": 6,
                "evidence_block_id": "ts5a_p6",
                "evidence_quote": "Electrical Characteristics for 2.5-V Supply. ICC Positive supply current VCOM and VIN = VCC or GND, VNC and VNO = Floating MAX UNIT Supply 1.1 1.3 2.7 μA 3.3 μA.",
            }
        ],
    )
    item = extracted_current(result)
    assert item["field_name"] == "max_current_a"
    assert item["value"] == 0.0000033
    assert item["page"] == 6


def test_pca9515_static_icch_iccl_extracts_5ma_max(tmp_path: Path) -> None:
    quote = "Static characteristics. VCC = 3.0 V to 3.6 V. ICCH HIGH-level supply current both channels HIGH; VCC = 3.6 V; SDAn = SCLn = VCC - 0.8 5 mA ICCL LOW-level supply current both channels LOW; VCC = 3.6 V - 1.7 5 mA ICCLc contention LOW-level supply current VCC = 3.6 V - 1.6 5 mA."
    result = convert(
        tmp_path,
        refdes="U44",
        target_description="IC I2C repeater",
        datasheet_references=[{"source_file": "datasheets/pca9515.pdf", "page": 7, "evidence_block_id": "pca_p7", "evidence_quote": quote}],
    )
    item = extracted_current(result)
    assert item["field_name"] == "max_current_a"
    assert item["value"] == 0.005


def test_sn74ahct1g125_icc_extracts_conservative_max_not_delta_icc(tmp_path: Path) -> None:
    quote = "IOZ OFF-state output current - - 0.25 - 2.5 - 10 μA II input leakage current - - 0.1 - 1.0 - 2.0 μA ICC supply current VI = VCC or GND; IO = 0 A; VCC = 5.5 V - - 1.0 - 10 - 40 μA ΔICC additional per input pin; VI = 3.4 V; supply current other inputs at VCC or GND; IO = 0 A; VCC = 5.5 V - - 1.35 - 1.5 - 1.5 mA."
    result = convert(
        tmp_path,
        refdes="U51",
        target_description="IC buffer",
        datasheet_references=[{"source_file": "datasheets/sn74.pdf", "page": 5, "evidence_block_id": "sn74_p5", "evidence_quote": quote}],
    )
    item = extracted_current(result)
    assert item["field_name"] == "max_current_a"
    assert item["value"] == 0.00004


def test_tcan3413_existing_60ma_extract_remains(tmp_path: Path) -> None:
    quote = "Supply Characteristics. PARAMETER ICC Supply current normal mode TYP MAX UNIT Dominant TXD = 0 V, STB = 0 V RL = 60 ohm 42 55 mA Dominant TXD = 0 V, STB = 0 V RL = 50 ohm 50 60 mA."
    result = convert(
        tmp_path,
        refdes="U45",
        target_description="IC CAN transceiver",
        datasheet_references=[{"source_file": "datasheets/tcan.pdf", "page": 5, "evidence_block_id": "tcan_p5", "evidence_quote": quote}],
    )
    item = extracted_current(result)
    assert item["field_name"] == "max_current_a"
    assert item["value"] == 0.06


def test_ic_fallback_rejects_absolute_max_and_rating_only_current_text(tmp_path: Path) -> None:
    bad_quotes = [
        "Absolute maximum ratings: ICC supply current 50 mA. Stresses beyond limits.",
        "Output current rating: 24 mA.",
        "Current limit, peak current, Max 1 A.",
    ]
    for idx, quote in enumerate(bad_quotes):
        result = convert(
            tmp_path / str(idx),
            datasheet_references=[{"source_file": "datasheets/ic.pdf", "evidence_quote": quote}],
        )
        assert result["extracted_items"] == []
        assert result["unknown_items"]


def test_ic_fallback_rejects_leakage_rows_as_operating_current(tmp_path: Path) -> None:
    bad_quotes = [
        "II input leakage current VI = 5.5 V, VCC = 0 V to 5.5 V Max 2 μA.",
        "IOZ OFF-state output current VCC = 5.5 V Max 10 μA.",
        "On-state leakage current ICOM(ON) Max 250 nA.",
    ]
    for idx, quote in enumerate(bad_quotes):
        result = convert(
            tmp_path / str(idx),
            datasheet_references=[{"source_file": "datasheets/ic.pdf", "evidence_quote": quote}],
        )
        assert result["extracted_items"] == []
        assert result["unknown_items"][0]["detail"] != "required when status is unknown"


def test_74lvc157_without_icc_returns_precise_unknown(tmp_path: Path) -> None:
    result = convert(
        tmp_path,
        refdes="U40",
        target_description="IC mux",
        datasheet_references=[
            {
                "source_file": "datasheets/74lvc157.pdf",
                "page": 6,
                "evidence_block_id": "lvc_p6",
                "evidence_quote": "Static characteristics. Symbol Parameter Conditions Min Typ Max Unit VIH HIGH-level input voltage VIL LOW-level input voltage.",
            }
        ],
    )
    assert result["extracted_items"] == []
    assert result["unknown_items"][0]["detail"] == "No valid supply-current evidence was found in the packet datasheet context."


def test_regulator_iout_max_is_rating_not_component_current(tmp_path: Path) -> None:
    quote = "Electrical Characteristics. IOUT(MAX) Maximum Output Current VOUT = 3.3 V 300 400 mA. Quiescent Current performance characteristics graph only."
    result = convert(
        tmp_path,
        refdes="U50",
        target_description="300 mA CMOS LDO regulator",
        datasheet_references=[{"source_file": "datasheets/ap2127.pdf", "page": 5, "evidence_block_id": "ap_p5", "evidence_quote": quote}],
    )
    assert not any(item["target_type"] == "component_current_model" for item in result["extracted_items"])
    rating = [item for item in result["extracted_items"] if item["target_type"] == "regulator_rating"][0]
    assert rating["field_name"] == "current_max"
    assert rating["value"] == 0.4
    assert_schema_linkage(rating)
    assert result["unknown_items"][0]["field_name"] == "branch_current_a"


def test_connector_rating_fallback_preserves_branch_current_unknown(tmp_path: Path) -> None:
    result = convert(
        tmp_path,
        refdes="J1",
        target_value="2x5",
        target_description="connector",
        datasheet_references=[
            {
                "source_file": "datasheets/connector.pdf",
                "evidence_quote": "Current rating: 3 A AC/DC (AWG #22)",
            }
        ],
    )
    assert result["extracted_items"][0]["target_type"] == "connector_rating"
    assert result["extracted_items"][0]["field_name"] == "current_max"
    assert_schema_linkage(result["extracted_items"][0])
    assert result["unknown_items"][0]["field_name"] == "branch_current_a"


def test_molex_derating_table_extracts_connector_rating_and_rejects_test_plug(tmp_path: Path) -> None:
    quote = (
        "PRODUCT SPECIFICATION 4.2 CURRENT DERATING AND APPLICABLE WIRES. Actual current rating is application dependent "
        "and should be evaluated for each application. CURRENT DERATING REFERENCE INFORMATION W-W W-B Amps Amps "
        "18 AWG 7 8.5 20 AWG 6.5 7 22 AWG 5.5 6. 4.3 CURRENT FOR TEST PLUG 44242 2.5 Amps Maximum. "
        "Test plugs are for testing purposes only and not intended for continuous use."
    )
    result = convert(
        tmp_path,
        refdes="P20",
        target_value="043045-0414",
        target_description="CON_HDR_2X2_3MM0_TH_VER_PWR_5A connector",
        datasheet_references=[{"source_file": "datasheets/molex.pdf", "page": 2, "evidence_block_id": "molex_p2", "evidence_quote": quote}],
    )
    rating = rating_items(result, "connector_rating", "current_max")[0]
    assert rating["value"] == 8.5
    assert rating["human_review_needed"] is True
    assert "test plug" not in rating["evidence_quote"].lower()
    assert result["unknown_items"][0]["field_name"] == "branch_current_a"


def test_jst_style_current_rating_extracts_connector_rating(tmp_path: Path) -> None:
    result = convert(
        tmp_path,
        refdes="P4",
        target_value="S2B-XH-A",
        target_description="connector header",
        datasheet_references=[{"source_file": "datasheets/jst.pdf", "evidence_quote": "XH CONNECTOR Current rating: 3 A AC/DC (AWG #22)"}],
    )
    rating = rating_items(result, "connector_rating", "current_max")[0]
    assert rating["value"] == 3
    assert rating["condition"] == "AC/DC, AWG #22"
    assert result["unknown_items"][0]["field_name"] == "branch_current_a"


def test_connector_contact_resistance_test_current_is_rejected(tmp_path: Path) -> None:
    result = convert(
        tmp_path,
        refdes="P20",
        target_description="connector",
        datasheet_references=[{"source_file": "datasheets/connector.pdf", "evidence_quote": "Contact Resistance test condition: apply a current of 100 mA. 10 milliohms maximum."}],
    )
    assert rating_items(result, "connector_rating", "current_max") == []
    assert result["unknown_items"]


def test_bss138_mosfet_extracts_load_switch_current_capability(tmp_path: Path) -> None:
    quote = "BSS138W N-CHANNEL ENHANCEMENT MODE MOSFET Product Summary BVDSS RDS(ON) Max ID Max TA = +25C 50V 3.5Ω @ VGS = 10V 200mA. Load Switch application."
    result = convert(
        tmp_path,
        refdes="Q1",
        target_description="XTR_MOSFET_NCH_50V_200MA_BSS138_SOT323",
        datasheet_references=[{"source_file": "datasheets/bss138.pdf", "page": 1, "evidence_block_id": "bss_p1", "evidence_quote": quote}],
    )
    rating = rating_items(result, "load_switch_rating", "current_max")[0]
    assert rating["value"] == 0.2
    assert rating["human_review_needed"] is True
    assert_schema_linkage(rating)
    assert not any(item["target_type"] == "component_current_model" for item in result["extracted_items"])
    assert result["unknown_items"][0]["field_name"] == "branch_current_a"


def test_fds4435_mosfet_extracts_continuous_current_not_pulsed(tmp_path: Path) -> None:
    quote = "FDS4435 -30V P-Channel MOSFET. Features VDS (V) =-30V ID = -8.8 A(VGS = -10V). MOSFET Maximum Ratings Symbol ID Parameter Drain Current -Continuous TA=25C -Pulsed Ratings -8.8 -50 A."
    result = convert(
        tmp_path,
        refdes="Q2",
        target_description="XTR_MOSFET_PCH_30V_8.8A_SOIC8 load switch",
        datasheet_references=[{"source_file": "datasheets/fds4435.pdf", "page": 1, "evidence_block_id": "fds_p1", "evidence_quote": quote}],
    )
    rating = rating_items(result, "load_switch_rating", "current_max")[0]
    assert rating["value"] == 8.8
    assert rating["value"] != 50
    assert "pulsed" not in rating["evidence_quote"].lower() or "continuous" in rating["evidence_quote"].lower()


def test_fds4435_prefers_maximum_ratings_quote_over_typical_graph_quote(tmp_path: Path) -> None:
    maximum_quote = (
        "UMW R FDS4435 -30V P-Channel MOSFET General Description. Product Summary. "
        "VDS (V) = -30V ID = -8.8 A(VGS = -10V). MOSFET Maximum Ratings TA = 25C. "
        "Symbol ID Parameter Drain Current -Continuous TA = 25C (Note 1a) -Pulsed Ratings -8.8 -50 A."
    )
    graph_quote = (
        "R UMW FDS4435 -30V P-Channel MOSFET Typical Characteristics TJ = 25C. "
        "Figure 1. On-Region Characteristics. Figure 2. Normalized On-Resistance vs Drain Current. "
        "PULSE DURATION = 80us DUTY CYCLE = 0.5%MAX ID = -8.8A VGS = -10V."
    )
    result = convert(
        tmp_path,
        refdes="Q2",
        target_description="XTR_MOSFET_PCH_30V_8.8A_SOIC8 load switch",
        datasheet_references=[
            {"source_file": "datasheets/fds4435.pdf", "page": 3, "evidence_block_id": "fds_graph", "evidence_quote": graph_quote},
            {"source_file": "datasheets/fds4435.pdf", "page": 1, "evidence_block_id": "fds_max", "evidence_quote": maximum_quote},
        ],
    )
    rating = rating_items(result, "load_switch_rating", "current_max")[0]
    assert rating["value"] == 8.8
    assert rating["evidence_block_id"] == "fds_max"
    assert "maximum ratings" in rating["evidence_quote"].lower()
    assert "typical characteristics" not in rating["evidence_quote"].lower()
    assert "figure 1" not in rating["evidence_quote"].lower()


def test_graph_only_fet_current_is_low_confidence_human_review_candidate(tmp_path: Path) -> None:
    graph_quote = (
        "FDS4435 -30V P-Channel MOSFET Typical Characteristics. Figure 1. On-Region Characteristics. "
        "Normalized On-Resistance vs Drain Current. PULSE DURATION = 80us DUTY CYCLE = 0.5%MAX. "
        "ID = -8.8A VGS = -10V."
    )
    result = convert(
        tmp_path,
        refdes="Q2",
        target_description="XTR_MOSFET_PCH_30V_8.8A_SOIC8 load switch",
        datasheet_references=[{"source_file": "datasheets/fds4435.pdf", "page": 3, "evidence_block_id": "fds_graph", "evidence_quote": graph_quote}],
    )
    rating = rating_items(result, "load_switch_rating", "current_max")[0]
    assert rating["value"] == 8.8
    assert rating["human_review_needed"] is True
    assert rating["confidence"] == 0.45
    assert "graph/ambiguous evidence" in rating["condition"]
    assert result["unknown_items"][0]["field_name"] == "branch_current_a"


def test_mosfet_leakage_rows_are_rejected_as_operating_or_rating_current(tmp_path: Path) -> None:
    result = convert(
        tmp_path,
        refdes="Q2",
        target_description="MOSFET",
        datasheet_references=[{"source_file": "datasheets/fet.pdf", "evidence_quote": "Electrical Characteristics IDSS Zero Gate Voltage Drain Current VDS=-24V VGS=0V 1 μA. IGSS Gate to Source Leakage Current ±10 μA."}],
    )
    assert result["extracted_items"] == []
    assert result["unknown_items"]


def test_fuse_hold_trip_and_rated_current_terms_map_to_rating_not_branch_current(tmp_path: Path) -> None:
    result = convert(
        tmp_path,
        refdes="F1",
        target_description="resettable PTC fuse",
        datasheet_references=[
            {"source_file": "datasheets/fuse.pdf", "evidence_quote": "Resettable fuse electrical ratings. Hold current Ihold 1.1 A. Trip current Itrip 2.2 A. Rated current 1 A."}
        ],
    )
    ratings = rating_items(result, "fuse_rating")
    assert ratings
    assert ratings[0]["field_name"] in {"hold_current", "trip_current", "current_max"}
    assert_schema_linkage(ratings[0])
    assert result["unknown_items"][0]["field_name"] == "branch_current_a"


def test_ferrite_rated_current_maps_to_rating_not_operating_current(tmp_path: Path) -> None:
    result = convert(
        tmp_path,
        refdes="FB1",
        target_description="ferrite bead",
        datasheet_references=[{"source_file": "datasheets/ferrite.pdf", "evidence_quote": "Ferrite bead impedance 600 ohm. Rated current 2 A. DCR 0.05 ohm."}],
    )
    rating = rating_items(result, "ferrite_rating", "current_max")[0]
    assert rating["value"] == 2
    assert_schema_linkage(rating)
    assert result["unknown_items"][0]["field_name"] == "branch_current_a"


def test_diode_led_current_terms_do_not_fill_branch_current_without_supported_rating_type(tmp_path: Path) -> None:
    result = convert(
        tmp_path,
        refdes="D1",
        target_description="Schottky diode",
        datasheet_references=[{"source_file": "datasheets/diode.pdf", "evidence_quote": "Average forward current IF(AV) 1 A. Reverse leakage current 10 μA."}],
    )
    assert result["extracted_items"] == []
    assert result["unknown_items"][0]["field_name"] != "branch_current_a or current_max"


def test_capacitor_defaults_still_emit_config_sourced_defaults(tmp_path: Path) -> None:
    ceramic = convert(tmp_path / "cer", refdes="C1", target_value="0.1uF", target_description="CAP_CER ceramic capacitor")
    assert extracted_current(ceramic)["value"] == 0.00000001
    assert extracted_current(ceramic)["source_file"] == "config/current_model_defaults.json"
    assert_schema_linkage(extracted_current(ceramic))

    electrolytic = convert(tmp_path / "elec", refdes="C2", target_value="10uF", target_description="aluminum electrolytic capacitor")
    assert extracted_current(electrolytic)["value"] == 0.000005
    assert extracted_current(electrolytic)["source_file"] == "config/current_model_defaults.json"
    assert_schema_linkage(extracted_current(electrolytic))


def test_extracted_item_id_is_deterministic_and_unique(tmp_path: Path) -> None:
    kwargs = {
        "refdes": "U1",
        "target_description": "IC mux",
        "datasheet_references": [{"source_file": "datasheets/ic.pdf", "evidence_quote": "ICC supply current, Max 10 uA."}],
    }
    first = convert(tmp_path / "first", **kwargs)
    second = convert(tmp_path / "second", **kwargs)
    assert first["extracted_items"][0]["item_id"] == second["extracted_items"][0]["item_id"]
    item_ids = [item["item_id"] for item in first["extracted_items"]]
    assert len(item_ids) == len(set(item_ids))


def test_preflight_passes_after_deterministic_fallback(tmp_path: Path) -> None:
    packet = packet_dir(
        tmp_path,
        datasheet_references=[{"source_file": "datasheets/ic.pdf", "evidence_quote": "ICC supply current, Max 10 uA."}],
    )
    module = load_module()
    response = module.convert(packet, unknown_easy())
    responses = tmp_path / "responses"
    write_json(responses / "12B-001.json", response)
    packet_list = tmp_path / "packet-list.txt"
    packet_list.write_text(str(packet) + "\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(PREFLIGHT), "--responses-dir", str(responses), "--packet-list", str(packet_list)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "response_preflight_pass True" in result.stdout


def test_batch_generation_continues_after_one_packet_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_module()
    first = packet_dir(tmp_path, packet_id="12B-001", missing_id="mdi_one")
    second = packet_dir(tmp_path, packet_id="12B-002", missing_id="mdi_two")
    packet_list = tmp_path / "packet-list.txt"
    packet_list.write_text(f"{first}\n{second}\n", encoding="utf-8")
    out_dir = tmp_path / "responses"
    raw_dir = tmp_path / "raw"

    calls = {"12B-001": 0, "12B-002": 0}

    def fake_chat(_base_url: str, _api_key: str, _model: str, messages: list[dict[str, str]], _max_tokens: int, _settings: dict[str, Any]) -> str:
        prompt = messages[1]["content"]
        packet_id = "12B-001" if "12B-001" in prompt else "12B-002"
        calls[packet_id] += 1
        if packet_id == "12B-001":
            raise RuntimeError("boom")
        return json.dumps(
            {
                "packet_id": "12B-002",
                "items": {
                    "mdi_two": {
                        "status": "unknown",
                        "target_type": "component_current_model",
                        "field_name": "branch_current_a",
                        "value": None,
                        "reason": "No board operating current evidence.",
                    }
                },
            }
        )

    monkeypatch.setattr(module, "chat", fake_chat)
    monkeypatch.setenv("OPENAI_BASE_URL", "http://example.invalid/v1")
    monkeypatch.setenv("MODEL", "test-model")
    monkeypatch.setattr(sys, "argv", ["ai_generate_easy_response_batch.py", "--packet-list", str(packet_list), "--out-dir", str(out_dir), "--raw-dir", str(raw_dir)])

    with pytest.raises(SystemExit) as exc:
        module.main()

    assert exc.value.code == 1
    assert calls["12B-001"] == 3
    assert calls["12B-002"] == 1
    assert (raw_dir / "12B-001.failed.json").exists()
    assert (out_dir / "12B-002.json").exists()
    summary = json.loads((raw_dir / "batch-summary.json").read_text())
    assert summary["failed_packet_count"] == 1
