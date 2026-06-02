#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

ALLOWED_REASON_CODES = {
    "not_found_in_provided_context",
    "ambiguous",
    "insufficient_context",
    "not_datasheet_sourced",
    "requires_manual_requirement",
    "unsupported_field",
}

SEMANTIC_TARGET_TYPES = {
    "connector",
    "mosfet",
    "capacitor",
    "resistor",
    "regulator",
    "fuse",
    "ic",
    "diode",
}

RATING_TARGET_TYPES = {
    "connector_rating",
    "connector_pin_rating",
    "fuse_rating",
    "regulator_rating",
    "load_switch_rating",
    "ferrite_rating",
}

SUPPORTED_FIELDS_BY_TARGET_TYPE = {
    "component_current_model": {"max_current_a", "typ_current_a"},
    "connector_rating": {"current_max"},
    "connector_pin_rating": {"pin_current_max"},
    "fuse_rating": {"current_max", "hold_current", "trip_current"},
    "regulator_rating": {"current_max"},
    "load_switch_rating": {"current_max"},
    "ferrite_rating": {"current_max"},
}


def target_prefix(req: dict) -> str:
    refdes = str(req.get("target_refdes") or "")
    for idx, char in enumerate(refdes):
        if char.isdigit():
            return refdes[:idx].upper() or "unknown"
    return refdes.upper() or "unknown"


def classify_target_device(req: dict, context: dict | None = None) -> str:
    refdes = str(req.get("target_refdes") or "").upper()
    text = json.dumps({
        "target_refdes": req.get("target_refdes"),
        "target_value": req.get("target_value"),
        "target_description": req.get("target_description"),
        "target_mpn": req.get("target_mpn"),
        "bom": (context or {}).get("target_bom_evidence") or (context or {}).get("target_bom_data") or [],
        "datasheets": (context or {}).get("datasheet_references") or [],
    }, ensure_ascii=False).lower()
    if refdes.startswith("Q") or any(term in text for term in ("mosfet", "fet", "load switch", "transistor")):
        return "mosfet"
    if refdes.startswith("U"):
        if "regulator" in text or "ldo" in text or "buck" in text or "boost" in text:
            return "regulator"
        return "ic"
    if refdes.startswith(("P", "J")) or any(term in text for term in ("connector", "con_hdr", "header", "receptacle", "terminal block")):
        return "connector"
    if "regulator" in text or "ldo" in text or "buck" in text or "boost" in text:
        return "regulator"
    if "fuse" in text or "ptc" in text or "efuse" in text:
        return "fuse"
    if refdes.startswith("FB") or "ferrite" in text or "inductor" in text:
        return "ferrite"
    if refdes.startswith(("D", "LED")) or any(term in text for term in ("diode", "tvs", "schottky", "led")):
        return "diode_led"
    return "unknown"


def context_has_class_evidence(target_class: str, context: dict | None) -> bool:
    text = json.dumps(context or {}, ensure_ascii=False).lower()
    terms_by_class = {
        "connector": ("current rating", "rated current", "current derating", "current capacity", "carrying current"),
        "mosfet": ("drain current", "id max", "continuous", "maximum ratings", "product summary", "rds"),
        "regulator": ("maximum output current", "iout(max)", "quiescent current", "standby current"),
        "fuse": ("rated current", "hold current", "trip current", "current limit"),
        "ferrite": ("rated current", "allowable current", "saturation current", "irms", "isat"),
        "ic": ("supply current", "icc", "idd", "iq", "quiescent current", "static characteristics"),
    }
    return any(term in text for term in terms_by_class.get(target_class, ()))


def allowed_target_types_for_section(req: dict, section_name: str) -> set[str]:
    base = {req.get("target_type")}

    explicit = req.get("allowed_extracted_target_types")
    if section_name == "extracted_items" and isinstance(explicit, list):
        return {str(x) for x in explicit}

    if section_name == "extracted_items" and req.get("packet_type") == "datasheet_current_extraction":
        return base | RATING_TARGET_TYPES

    return base


def packet_missing_ids(packet_dir: Path, req: dict, context: dict | None) -> set[str]:
    ids = {str(value) for value in req.get("missing_data_item_ids", []) if value is not None}
    if isinstance(context, dict):
        for item in context.get("missing_data_items", []) if isinstance(context.get("missing_data_items"), list) else []:
            if isinstance(item, dict):
                value = item.get("manifest_id") or item.get("id") or item.get("source_missing_data_id")
                if value:
                    ids.add(str(value))
    return ids


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--responses-dir", required=True)
    parser.add_argument("--packet-list", required=True)
    args = parser.parse_args()

    responses_dir = Path(args.responses_dir)
    packet_dirs = [
        Path(x.strip())
        for x in Path(args.packet_list).read_text().splitlines()
        if x.strip()
    ]

    failed = False
    expected_ids = []
    total_extracted = 0
    total_unknown = 0
    unknown_fields: Counter[str] = Counter()
    extracted_fields: Counter[str] = Counter()
    unknown_only_packet_ids: list[str] = []
    unknown_only_12b_count = 0
    unknown_only_by_prefix: Counter[str] = Counter()
    unknown_only_by_target_class: Counter[str] = Counter()
    unknown_only_packet_details: list[dict] = []
    missing_reasons: Counter[str] = Counter()
    duplicate_facts: Counter[tuple[str, str, str, str, str, str]] = Counter()
    rating_candidates_count = 0
    component_current_model_count = 0
    branch_current_only_unknown_count = 0
    context_selection_suspect_count = 0
    context_selection_suspects: list[dict] = []

    for packet_dir in packet_dirs:
        req = json.loads((packet_dir / "request.json").read_text())
        context_path = packet_dir / "context.json"
        context = json.loads(context_path.read_text()) if context_path.exists() else {}
        valid_missing_ids = packet_missing_ids(packet_dir, req, context)
        packet_id = req["packet_id"]
        expected_ids.append(packet_id)

        path = responses_dir / f"{packet_id}.json"
        if not path.exists():
            print("ERROR missing response", packet_id)
            failed = True
            continue

        data = json.loads(path.read_text())

        extracted = data.get("extracted_items")
        unknown = data.get("unknown_items")
        notes = data.get("notes")
        warnings = data.get("warnings")

        print(
            path.name,
            "schema=", data.get("schema_version"),
            "status=", data.get("status"),
            "notes_type=", type(notes).__name__,
            "warnings_type=", type(warnings).__name__,
            "extracted=", len(extracted) if isinstance(extracted, list) else "BAD",
            "unknown=", len(unknown) if isinstance(unknown, list) else "BAD",
        )

        checks = [
            (data.get("packet_id") == packet_id, "packet_id mismatch"),
            (data.get("schema_version") == "ai_extraction_result_v1", "bad schema_version"),
            (data.get("status") in {"completed", "partial"}, "bad status"),
            (isinstance(notes, list), "notes not list"),
            (isinstance(warnings, list), "warnings not list"),
            (isinstance(extracted, list), "extracted_items not list"),
            (isinstance(unknown, list), "unknown_items not list"),
        ]

        for ok, msg in checks:
            if not ok:
                print("ERROR", packet_id, msg)
                failed = True

        extracted_item_ids: set[str] = set()

        for section_name, items in (("extracted_items", extracted), ("unknown_items", unknown)):
            if not isinstance(items, list):
                continue
            if section_name == "extracted_items":
                total_extracted += len(items)
                for item in items:
                    if isinstance(item, dict):
                        extracted_fields[f"{item.get('target_type')}/{item.get('field_name')}"] += 1
                        if item.get("target_type") in RATING_TARGET_TYPES:
                            rating_candidates_count += 1
                        if item.get("target_type") == "component_current_model":
                            component_current_model_count += 1
                        fact_key = (
                            str(item.get("target_refdes") or req.get("target_refdes") or ""),
                            str(item.get("target_type") or ""),
                            str(item.get("field_name") or ""),
                            str(item.get("value") or ""),
                            str(item.get("source_file") or ""),
                            str(item.get("evidence_quote") or "")[:160],
                        )
                        duplicate_facts[fact_key] += 1
            if section_name == "unknown_items":
                total_unknown += len(items)
                for item in items:
                    if isinstance(item, dict):
                        unknown_fields[str(item.get("field_name"))] += 1
                        detail = str(item.get("detail") or item.get("reason_code") or "unspecified")
                        missing_reasons[detail] += 1

            for i, item in enumerate(items):
                if not isinstance(item, dict):
                    print("ERROR", packet_id, section_name, i, "not object")
                    failed = True
                    continue

                target_type = item.get("target_type")
                allowed_target_types = allowed_target_types_for_section(req, section_name)
                if target_type not in allowed_target_types:
                    print(
                        "ERROR",
                        packet_id,
                        section_name,
                        i,
                        "target_type mismatch",
                        target_type,
                        "allowed=",
                        sorted(allowed_target_types),
                    )
                    failed = True

                if target_type in SEMANTIC_TARGET_TYPES:
                    print("ERROR", packet_id, section_name, i, "semantic target_type", target_type)
                    failed = True

                if section_name == "extracted_items":
                    for required_key in ("item_id", "missing_data_item_ids", "target_type", "field_name", "value", "basis", "confidence"):
                        if required_key not in item or item.get(required_key) in (None, ""):
                            print(
                                "ERROR",
                                packet_id,
                                section_name,
                                i,
                                "missing required field",
                                required_key,
                                "target_type=",
                                item.get("target_type"),
                                "field_name=",
                                item.get("field_name"),
                            )
                            failed = True
                    item_id = item.get("item_id")
                    if isinstance(item_id, str) and item_id:
                        if item_id in extracted_item_ids:
                            print("ERROR", packet_id, section_name, i, "duplicate item_id", item_id)
                            failed = True
                        extracted_item_ids.add(item_id)
                    missing_data_item_ids = item.get("missing_data_item_ids")
                    if not isinstance(missing_data_item_ids, list) or not missing_data_item_ids:
                        print("ERROR", packet_id, section_name, i, "missing_data_item_ids must be a non-empty list", "target_type=", target_type, "field_name=", item.get("field_name"))
                        failed = True
                    elif any(not isinstance(value, str) or not value for value in missing_data_item_ids):
                        print("ERROR", packet_id, section_name, i, "missing_data_item_ids must contain non-empty strings", "target_type=", target_type, "field_name=", item.get("field_name"))
                        failed = True
                    elif valid_missing_ids and not set(missing_data_item_ids).issubset(valid_missing_ids):
                        print("ERROR", packet_id, section_name, i, "missing_data_item_ids outside packet", sorted(set(missing_data_item_ids) - valid_missing_ids), "target_type=", target_type, "field_name=", item.get("field_name"))
                        failed = True
                    confidence = item.get("confidence")
                    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not (0 <= float(confidence) <= 1):
                        print("ERROR", packet_id, section_name, i, "confidence must be a number between 0 and 1", "target_type=", target_type, "field_name=", item.get("field_name"))
                        failed = True
                    allowed_fields = SUPPORTED_FIELDS_BY_TARGET_TYPE.get(str(target_type))
                    if allowed_fields is not None and item.get("field_name") not in allowed_fields:
                        print("ERROR", packet_id, section_name, i, "unsupported target_type/field_name pair", target_type, item.get("field_name"))
                        failed = True
                    if item.get("unit") in (None, "") and item.get("field_name") in {"max_current_a", "typ_current_a", "current_max", "pin_current_max", "hold_current", "trip_current"}:
                        print("ERROR", packet_id, section_name, i, "missing unit", "target_type=", target_type, "field_name=", item.get("field_name"))
                        failed = True
                    if item.get("source_file") in (None, "") or item.get("evidence_quote") in (None, ""):
                        print("ERROR", packet_id, section_name, i, "missing source_file/evidence_quote", "target_type=", target_type, "field_name=", item.get("field_name"))
                        failed = True

                if section_name == "unknown_items":
                    rc = item.get("reason_code")
                    if rc not in ALLOWED_REASON_CODES:
                        print("ERROR", packet_id, section_name, i, "bad reason_code", rc)
                        failed = True

        if isinstance(extracted, list) and isinstance(unknown, list) and not extracted and unknown:
            unknown_only_packet_ids.append(packet_id)
            if req.get("stage_id") == "12B" or req.get("packet_type") == "datasheet_current_extraction":
                unknown_only_12b_count += 1
            prefix = target_prefix(req)
            target_class = classify_target_device(req, context)
            unknown_only_by_prefix[prefix] += 1
            unknown_only_by_target_class[target_class] += 1
            detail = {
                "packet_id": packet_id,
                "target_refdes": req.get("target_refdes"),
                "target_mpn": req.get("target_mpn"),
                "target_description": req.get("target_description"),
                "target_class": target_class,
            }
            unknown_only_packet_details.append(detail)
            if target_class in {"connector", "mosfet", "regulator", "fuse", "ferrite", "ic"} and not context_has_class_evidence(target_class, context):
                context_selection_suspect_count += 1
                context_selection_suspects.append(detail)

        if isinstance(extracted, list) and isinstance(unknown, list) and not extracted and unknown:
            if all(isinstance(item, dict) and item.get("field_name") == "branch_current_a" for item in unknown):
                branch_current_only_unknown_count += 1

    response_files = sorted(p.stem for p in responses_dir.glob("*.json"))
    extra = sorted(set(response_files) - set(expected_ids))
    missing = sorted(set(expected_ids) - set(response_files))

    print()
    print("expected_packet_count", len(expected_ids))
    print("response_count", len(response_files))
    print("missing_response_ids", missing)
    print("extra_response_files", extra)
    print("total_packets", len(expected_ids))
    print("total_unknown_items", total_unknown)
    print("total_extracted_items", total_extracted)
    print("unknowns_by_field_name", dict(sorted(unknown_fields.items())))
    print("extracted_items_by_target_type_field_name", dict(sorted(extracted_fields.items())))
    print("unknown_only_packet_ids", unknown_only_packet_ids)
    print("unknown_only_12b_count", unknown_only_12b_count)
    print("unknown_only_by_target_prefix", dict(sorted(unknown_only_by_prefix.items())))
    print("unknown_only_by_target_class", dict(sorted(unknown_only_by_target_class.items())))
    print("unknown_only_packet_details", unknown_only_packet_details)
    print("duplicate_extracted_fact_count", sum(count - 1 for count in duplicate_facts.values() if count > 1))
    print("rating_candidates_count", rating_candidates_count)
    print("component_current_model_count", component_current_model_count)
    print("branch_current_only_unknown_count", branch_current_only_unknown_count)
    print("context_selection_suspect_count", context_selection_suspect_count)
    print("context_selection_suspects", context_selection_suspects)
    print("top_missing_evidence_reasons", missing_reasons.most_common(10))
    print("response_preflight_pass", not failed and not missing and not extra)

    return 1 if failed or missing or extra else 0

if __name__ == "__main__":
    raise SystemExit(main())
