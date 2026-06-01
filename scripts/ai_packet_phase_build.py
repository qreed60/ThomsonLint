#!/usr/bin/env python3
"""Build deterministic AI packet scaffolding for data completion.

PR 26 scope only: create packet queues, bounded context, prompts, and status
files for later AI-assisted extraction. This script does not call an AI model,
extract datasheet values, mutate topology artifacts, create findings, or make
pass/fail/compliance judgments.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


QUEUE_SCHEMA_VERSION = "ai_packet_queue_v1"
DEFAULT_PROJECT = "example"
DEFAULT_PHASE_ID = "12"
DEFAULT_PHASE_NAME = "AI Data Completion"
DEFAULT_MAX_ITEMS_PER_PACKET = 5
MAX_ATTEMPTS = 2
MAX_TARGET_EVIDENCE_ROWS = 5
MAX_SCHEMATIC_SNIPPETS = 10
SEMANTIC_TARGET_TYPE_EXAMPLES = "connector, mosfet, capacitor, resistor, regulator, fuse, IC, or diode"

PACKET_LIFECYCLE = [
    "pending",
    "prompt_ready",
    "running",
    "raw_response_saved",
    "validation_passed",
    "validation_failed",
    "accepted",
    "rejected",
    "human_review_needed",
    "skipped",
]

FORBIDDEN_OUTPUTS = [
    "uncited_values",
    "guessed_values",
    "findings",
    "pass_fail_judgments",
    "compliance_judgments",
    "final_calculations",
]

STAGES: dict[str, dict[str, str]] = {
    "12A": {
        "stage_name": "Datasheet Role / Pin Extraction",
        "packet_type": "datasheet_role_pin_extraction",
        "target_type": "component_role_model",
        "required_output_schema": "component_role_pin_model_v1",
    },
    "12B": {
        "stage_name": "Datasheet Current Model Extraction",
        "packet_type": "datasheet_current_extraction",
        "target_type": "component_current_model",
        "required_output_schema": "component_current_model_v1",
    },
    "12C": {
        "stage_name": "Datasheet Rating Extraction",
        "packet_type": "datasheet_rating_extraction",
        "target_type": "component_rating_model",
        "required_output_schema": "component_rating_model_v1",
    },
    "12D": {
        "stage_name": "Passive / Support Component Extraction",
        "packet_type": "datasheet_passive_support_extraction",
        "target_type": "passive_support_model",
        "required_output_schema": "passive_support_model_v1",
    },
    "12H": {
        "stage_name": "Human Review Routing",
        "packet_type": "human_review_packet",
        "target_type": "human_review_decision",
        "required_output_schema": "human_review_packet_v1",
    },
}

STAGE_BY_CATEGORY = {
    "component_role_unknown": "12A",
    "relationship_direction_unknown": "12A",
    "source_sink_not_resolved": "12A",
    "power_path_direction_unknown": "12A",
    "regulator_output_unknown": "12A",
    "regulator_input_output_unknown": "12A",
    "branch_current_unknown": "12B",
    "current_model_missing": "12B",
    "rail_current_unknown": "12B",
    "rating_missing": "12C",
    "connector_pin_rating_unknown": "12C",
    "fuse_rating_unknown": "12C",
    "regulator_rating_unknown": "12C",
    "esr_missing": "12D",
    "ripple_current_missing": "12D",
    "ferrite_rating_missing": "12D",
    "capacitor_voltage_rating_missing": "12D",
}

DETERMINISTIC_OR_HUMAN_CATEGORIES = {
    "copper_thickness_missing",
    "geometry_width_missing",
    "geometry_area_missing",
    "geometry_length_missing",
    "layer_unknown",
    "via_geometry_missing",
    "via_hole_missing",
    "via_plating_missing",
}

HIGH_RISK_BLOCKERS = {
    "current_allocation",
    "fuse_margin",
    "connector_pin_current_margin",
    "regulator_load_margin",
}

REFDES_FIELD_ALIASES = (
    "refdes",
    "ref des",
    "reference designator",
    "reference",
    "references",
    "designator",
    "designators",
    "part",
)
MPN_FIELD_ALIASES = ("mpn", "manufacturer part number", "manufacturer_part_number", "mfg p/n", "mfg p/n_1", "mpn_1", "part number")
MANUFACTURER_FIELD_ALIASES = ("manufacturer", "mfg", "mfg_1")
VALUE_FIELD_ALIASES = ("value", "part value")
DESCRIPTION_FIELD_ALIASES = ("description", "desc", "item name")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def json_safe(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(key): json_safe(child) for key, child in value.items()}
    if isinstance(value, list):
        return [json_safe(child) for child in value]
    return value


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_safe(data), indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def safe_id(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "_", str(value or "")).strip("_")
    return text or "unknown"


def item_id(item: dict[str, Any]) -> str:
    return str(item.get("manifest_id") or item.get("id") or item.get("source_missing_data_id") or "")


def source_artifact(artifact_type: str, path: Path | None, present: bool | None = None) -> dict[str, Any]:
    return {
        "artifact_type": artifact_type,
        "path": str(path) if path else None,
        "present": bool(path.exists()) if present is None and path else bool(present),
    }


def load_optional(path: Path | None, label: str, warnings: list[str]) -> dict[str, Any] | None:
    if path is None:
        warnings.append(f"optional {label} input not provided")
        return None
    if not path.exists():
        warnings.append(f"optional {label} input missing: {path}")
        return None
    data = load_json(path)
    if not isinstance(data, dict):
        raise ValueError(f"{label} artifact must be a JSON object: {path}")
    return data


def manifest_items(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [item for item in as_list(manifest.get("manifest_items")) if isinstance(item, dict)]
    if not rows:
        rows = [item for item in as_list(manifest.get("missing_data_items")) if isinstance(item, dict)]
    return rows


def refdes_sort_key(value: str) -> tuple[str, int, str]:
    match = re.fullmatch(r"([A-Za-z]+)(\d+)(.*)", value or "")
    if not match:
        return value, -1, ""
    return match.group(1).upper(), int(match.group(2)), match.group(3)


def normalized_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def first_text(value: Any) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if value is not None and not isinstance(value, (dict, list)):
        text = str(value).strip()
        return text or None
    return None


def find_field(row: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    wanted = {normalized_key(alias) for alias in aliases}
    for source in (row, row.get("fields") if isinstance(row.get("fields"), dict) else None, row.get("custom_metadata") if isinstance(row.get("custom_metadata"), dict) else None):
        if not isinstance(source, dict):
            continue
        for key, value in source.items():
            if normalized_key(str(key)) in wanted:
                return value
    return None


def split_refdes_values(value: Any) -> list[str]:
    values: list[str] = []
    raw_values = value if isinstance(value, list) else [value]
    for raw in raw_values:
        text = first_text(raw)
        if not text:
            continue
        for token in re.split(r"[\s,;]+", text):
            token = token.strip()
            if token:
                values.append(token)
    return values


def row_refdes_values(row: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for alias in REFDES_FIELD_ALIASES:
        value = find_field(row, (alias,))
        refs.extend(split_refdes_values(value))
    seen: set[str] = set()
    unique: list[str] = []
    for ref in refs:
        key = ref.upper()
        if key not in seen:
            seen.add(key)
            unique.append(ref)
    return unique


def iterable_artifact_rows(data: dict[str, Any] | None) -> list[tuple[str, int, dict[str, Any]]]:
    if not isinstance(data, dict):
        return []
    rows: list[tuple[str, int, dict[str, Any]]] = []
    for key in ("components", "bom", "parts", "items", "rows"):
        for idx, row in enumerate(as_list(data.get(key))):
            if isinstance(row, dict):
                rows.append((key, idx, row))
    return rows


def primary_manufacturer(row: dict[str, Any]) -> str | None:
    manufacturer = first_text(find_field(row, MANUFACTURER_FIELD_ALIASES))
    if manufacturer:
        return manufacturer
    for entry in as_list(row.get("manufacturers")):
        if isinstance(entry, dict):
            manufacturer = first_text(entry.get("manufacturer"))
            if manufacturer:
                return manufacturer
    bom = row.get("bom")
    if isinstance(bom, dict):
        return first_text(bom.get("manufacturer"))
    return None


def primary_mpn(row: dict[str, Any]) -> str | None:
    mpn = first_text(find_field(row, MPN_FIELD_ALIASES))
    if mpn:
        return mpn
    for entry in as_list(row.get("manufacturers")):
        if isinstance(entry, dict):
            mpn = first_text(entry.get("mpn") or entry.get("manufacturer_part_number") or entry.get("part_number"))
            if mpn:
                return mpn
    bom = row.get("bom")
    if isinstance(bom, dict):
        mpn = first_text(bom.get("mpn") or bom.get("manufacturer_part_number") or bom.get("part_number"))
        if mpn:
            return mpn
    return first_text(row.get("part_number"))


def bounded_bom_row_evidence(row: dict[str, Any], *, source_key: str, source_index: int, refdes: str) -> dict[str, Any]:
    fields = row.get("fields") if isinstance(row.get("fields"), dict) else {}
    custom_metadata = row.get("custom_metadata") if isinstance(row.get("custom_metadata"), dict) else {}
    placement = row.get("placement_data") if isinstance(row.get("placement_data"), dict) else {}
    return {
        "source": "bom",
        "source_key": source_key,
        "source_index": source_index,
        "source_row_id": row.get("row_id") or row.get("id") or row.get("raw_row_index") or source_index,
        "matched_refdes": refdes,
        "refdes": row_refdes_values(row)[:50],
        "fields": {
            "value": first_text(find_field(row, VALUE_FIELD_ALIASES)),
            "description": first_text(find_field(row, DESCRIPTION_FIELD_ALIASES)),
            "manufacturer": primary_manufacturer(row),
            "mpn": primary_mpn(row),
            "quantity": first_text(fields.get("quantity") or row.get("quantity")),
            "footprint": first_text(fields.get("footprint") or row.get("footprint")),
            "dnp": fields.get("dnp") if "dnp" in fields else row.get("dnp"),
        },
        "manufacturers": as_list(row.get("manufacturers"))[:3],
        "custom_metadata": {str(key): value for key, value in list(custom_metadata.items())[:10]},
        "target_placement": placement.get(refdes) if isinstance(placement.get(refdes), dict) else None,
    }


def build_bom_refdes_index(bom: dict[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for source_key, source_index, row in iterable_artifact_rows(bom):
        refs = row_refdes_values(row)
        for ref in refs:
            index.setdefault(ref.upper(), []).append(bounded_bom_row_evidence(row, source_key=source_key, source_index=source_index, refdes=ref))
    return index


def bounded_schematic_evidence(schematic: dict[str, Any] | None, refdes: str, item_ids: set[str]) -> list[dict[str, Any]]:
    if not isinstance(schematic, dict):
        return []
    snippets: list[dict[str, Any]] = []
    tokens = {refdes, *item_ids}
    for key in ("components", "pins", "relationships", "snippets", "nets"):
        for idx, row in enumerate(as_list(schematic.get(key))):
            if not isinstance(row, dict):
                continue
            scalar_values = {str(value) for value in row.values() if value is not None and not isinstance(value, (dict, list))}
            nested_bom = row.get("bom") if isinstance(row.get("bom"), dict) else {}
            if refdes == str(row.get("refdes") or row.get("designator") or "") or scalar_values.intersection(tokens):
                snippets.append(
                    {
                        "source": "schematic_export",
                        "source_key": key,
                        "source_index": idx,
                        "refdes": row.get("refdes"),
                        "value": row.get("value"),
                        "footprint": row.get("footprint"),
                        "part_number": row.get("part_number"),
                        "bom": {
                            "description": nested_bom.get("description"),
                            "manufacturer": nested_bom.get("manufacturer"),
                            "mpn": nested_bom.get("mpn"),
                            "quantity": nested_bom.get("quantity"),
                            "dnp": nested_bom.get("dnp"),
                        }
                        if nested_bom
                        else None,
                    }
                )
    return snippets[:MAX_SCHEMATIC_SNIPPETS]


def target_enrichment(refdes: str, bom_index: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    matches = bom_index.get(refdes.upper(), [])
    evidence = matches[:MAX_TARGET_EVIDENCE_ROWS]
    primary = evidence[0] if evidence else None
    fields = primary.get("fields", {}) if isinstance(primary, dict) else {}
    mpn = first_text(fields.get("mpn")) if isinstance(fields, dict) else None
    return {
        "target_refdes": refdes,
        "target_mpn": mpn,
        "target_manufacturer": first_text(fields.get("manufacturer")) if isinstance(fields, dict) else None,
        "target_value": first_text(fields.get("value")) if isinstance(fields, dict) else None,
        "target_description": first_text(fields.get("description")) if isinstance(fields, dict) else None,
        "target_bom_row_id": primary.get("source_row_id") if isinstance(primary, dict) else None,
        "target_bom_evidence": evidence,
        "target_bom_match_status": "matched" if evidence else "no_component_bom_match",
        "target_bom_match_reason": None if evidence else f"no BOM row matched target_refdes {refdes}",
    }


def target_refdes(item: dict[str, Any]) -> str:
    if isinstance(item.get("refdes"), str):
        return item["refdes"]
    target_id = item.get("target_id")
    target_type = str(item.get("target_type") or "")
    if target_type in {"component", "connector", "fuse", "regulator"} and isinstance(target_id, str):
        return target_id
    components = [str(value) for value in as_list(item.get("affected_components")) if value]
    if components:
        return sorted(components, key=refdes_sort_key)[0]
    return str(target_id or item.get("normalized_target") or "project")


def route_item(item: dict[str, Any]) -> tuple[str | None, bool, str | None]:
    category = str(item.get("category") or "")
    recommended = str(item.get("recommended_resolution") or item.get("resolution_path") or item.get("resolution_queue") or "")
    if recommended in {"manual", "human_review", "deterministic_rule"} and category in {"source_sink_not_resolved", "power_path_direction_unknown"}:
        return "12H", True, "item requested manual or deterministic handling"
    if category in DETERMINISTIC_OR_HUMAN_CATEGORIES:
        return None, False, "geometry/stackup data is not routed to datasheet AI by default"
    if category in STAGE_BY_CATEGORY:
        return STAGE_BY_CATEGORY[category], False, None
    return "12H", True, f"unsupported missing-data category routed to human review: {category or 'unknown'}"


def group_key(item: dict[str, Any], stage_id: str, bom_index: dict[str, list[dict[str, Any]]]) -> tuple[str, str, str]:
    refdes = target_refdes(item)
    mpn = target_enrichment(refdes, bom_index).get("target_mpn") or ""
    return stage_id, refdes, mpn


def split_items(items: list[dict[str, Any]], max_items: int) -> list[list[dict[str, Any]]]:
    chunks: list[list[dict[str, Any]]] = []
    for item in items:
        item_blocks = {str(block) for block in as_list(item.get("blocks"))}
        limit = 1 if item_blocks.intersection(HIGH_RISK_BLOCKERS) else max_items
        if not chunks or len(chunks[-1]) >= limit or limit == 1:
            chunks.append([])
        chunks[-1].append(item)
    return chunks


def bounded_artifact_refs(paths: dict[str, Path | None]) -> list[dict[str, Any]]:
    return [source_artifact(label, path) for label, path in paths.items()]


def context_for_packet(
    *,
    packet_id: str,
    project: str,
    phase_id: str,
    phase_name: str,
    stage_id: str,
    stage: dict[str, str],
    target_ref: str,
    target: dict[str, Any],
    items: list[dict[str, Any]],
    optional_data: dict[str, dict[str, Any] | None],
    source_paths: dict[str, Path | None],
) -> dict[str, Any]:
    item_ids = {item_id(item) for item in items}
    relevant_schematic = bounded_schematic_evidence(optional_data.get("schematic_export"), target_ref, item_ids)
    target_part = first_text(target.get("target_mpn"))
    return {
        "project": project,
        "phase_id": phase_id,
        "phase_name": phase_name,
        "packet_id": packet_id,
        "stage_id": stage_id,
        "stage_name": stage["stage_name"],
        "packet_type": stage["packet_type"],
        "target_type": stage["target_type"],
        "expected_target_type": stage["target_type"],
        "allowed_target_type": stage["target_type"],
        "target_refdes": target_ref,
        "target_mpn": target_part,
        "target_manufacturer": target.get("target_manufacturer"),
        "target_value": target.get("target_value"),
        "target_description": target.get("target_description"),
        "target_bom_row_id": target.get("target_bom_row_id"),
        "target_bom_evidence": target.get("target_bom_evidence", []),
        "target_bom_match_status": target.get("target_bom_match_status"),
        "target_bom_match_reason": target.get("target_bom_match_reason"),
        "missing_data_items": items,
        "target_bom_data": target.get("target_bom_evidence", []),
        "role_resolution": bounded_target_rows(optional_data.get("role_resolution"), target_ref),
        "rail_relationships": bounded_link_rows(optional_data.get("rail_relationships"), items),
        "branch_topology_enriched": bounded_link_rows(optional_data.get("branch_topology_enriched"), items),
        "schematic_snippets": relevant_schematic,
        "datasheet_references": bounded_target_rows(optional_data.get("datasheet_manifest"), target_ref)
        + bounded_target_rows(optional_data.get("datasheet_index"), target_ref)
        + bounded_target_rows(optional_data.get("part_info_index"), target_ref)
        + bounded_datasheet_evidence_rows(optional_data.get("datasheet_evidence_index"), target_ref, target_part, items),
        "source_artifacts": bounded_artifact_refs(source_paths),
        "context_bounds": {
            "includes_only_packet_missing_data_items": True,
            "missing_data_item_count": len(items),
            "max_items_per_packet": len(items),
        },
    }


def bounded_target_rows(data: dict[str, Any] | None, refdes: str) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    rows: list[dict[str, Any]] = []
    for key in ("components", "bom", "parts", "items", "rows", "component_roles", "datasheets", "documents"):
        for row in as_list(data.get(key)):
            if not isinstance(row, dict):
                continue
            values = {str(value) for value in row.values() if value is not None}
            if refdes in values:
                rows.append(row)
    return rows[:20]


def bounded_datasheet_evidence_rows(
    data: dict[str, Any] | None,
    refdes: str,
    mpn: str | None,
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    tokens = {refdes, str(mpn or "")}
    for item in items:
        tokens.add(str(item.get("target_id") or ""))
        tokens.update(str(value) for value in as_list(item.get("affected_components")) if value is not None)
    tokens = {token for token in tokens if token}
    rows: list[dict[str, Any]] = []
    normalized_tokens = {re.sub(r"[^a-z0-9]+", "", token.lower()) for token in tokens if token}
    document_tokens = {refdes, str(mpn or "")}
    normalized_document_tokens = {re.sub(r"[^a-z0-9]+", "", token.lower()) for token in document_tokens if token}
    for doc in as_list(data.get("documents")):
        if not isinstance(doc, dict):
            continue
        doc_values = {
            str(value)
            for key in ("filename", "source_file", "matched_mpn", "matched_manufacturer")
            for value in [doc.get(key)]
            if value is not None
        }
        matched_refdes = doc.get("matched_refdes")
        if isinstance(matched_refdes, list):
            doc_values.update(str(value) for value in matched_refdes if value is not None)
        elif matched_refdes is not None:
            doc_values.add(str(matched_refdes))
        matched_bom = doc.get("matched_bom_entry") if isinstance(doc.get("matched_bom_entry"), dict) else {}
        doc_values.update(str(value) for value in as_list(matched_bom.get("refdes")) if value is not None)
        if matched_bom.get("mpn") is not None:
            doc_values.add(str(matched_bom.get("mpn")))
        normalized_doc_values = {re.sub(r"[^a-z0-9]+", "", value.lower()) for value in doc_values}
        if normalized_document_tokens.intersection(normalized_doc_values) or any(token and any(token in value for value in normalized_doc_values) for token in normalized_document_tokens):
            first_block = next((block for block in as_list(doc.get("page_evidence_blocks")) if isinstance(block, dict)), {})
            rows.append(
                {
                    "document_id": doc.get("document_id"),
                    "filename": doc.get("filename"),
                    "source_file": doc.get("source_file"),
                    "page": first_block.get("page"),
                    "evidence_quote": first_block.get("text_snippet"),
                    "matched_mpn": doc.get("matched_mpn"),
                    "matched_manufacturer": doc.get("matched_manufacturer"),
                    "matched_refdes": doc.get("matched_refdes"),
                    "text_extraction_status": doc.get("text_extraction_status"),
                    "extracted_text_available": doc.get("extracted_text_available"),
                    "extraction_warnings": doc.get("extraction_warnings", []),
                }
            )
    for row in as_list(data.get("candidates")):
        if not isinstance(row, dict):
            continue
        values = {str(value) for value in row.values() if value is not None and not isinstance(value, (dict, list))}
        if values.intersection(tokens):
            rows.append(
                {
                    "candidate_id": row.get("candidate_id"),
                    "target_type": row.get("target_type"),
                    "target_identity": row.get("target_identity"),
                    "parameter_name": row.get("parameter_name"),
                    "value_raw": row.get("value_raw"),
                    "unit_raw": row.get("unit_raw"),
                    "condition": row.get("condition"),
                    "source_file": row.get("source_file"),
                    "page": row.get("page"),
                    "evidence_quote": row.get("evidence_quote"),
                    "confidence": row.get("confidence"),
                    "requires_human_review": row.get("requires_human_review"),
                }
            )
    return rows[:20]


def bounded_link_rows(data: dict[str, Any] | None, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    tokens = {
        str(value)
        for item in items
        for key in ("target_id", "normalized_target", "group_id")
        for value in [item.get(key)]
        if value is not None
    }
    for item in items:
        tokens.update(str(value) for value in as_list(item.get("affected_rails")) if value is not None)
        tokens.update(str(value) for value in as_list(item.get("affected_branches")) if value is not None)
        tokens.update(str(value) for value in as_list(item.get("affected_components")) if value is not None)
    rows: list[dict[str, Any]] = []
    for key in ("relationships", "rail_relationships", "branches", "branch_readiness", "nets", "items", "rows"):
        for row in as_list(data.get(key)):
            if isinstance(row, dict) and any(token in {str(value) for value in row.values()} for token in tokens):
                rows.append(row)
    return rows[:20]


def prompt_for_packet(packet: dict[str, Any], context: dict[str, Any]) -> str:
    item_ids = ", ".join(packet["missing_data_item_ids"])
    forbidden = "\n".join(f"- {value}" for value in packet["forbidden_outputs"])
    target_type_guidance = ""
    if packet["packet_type"] == "datasheet_current_extraction":
        expected_target_type = packet["expected_target_type"]
        target_type_guidance = f"""
## Target Type Contract
- Use target_type exactly as provided in request.json for component operating-current items.
- For component operating-current items in this packet, target_type must be {expected_target_type}.
- Do not replace component operating-current target_type with component class words such as {SEMANTIC_TARGET_TYPE_EXAMPLES}.
- If the datasheet evidence is a connector current rating/capability rather than actual operating current, emit it as target_type connector_rating with field_name current_max.
- Connector current ratings are rating/capability candidates only; they must not claim to resolve branch_current_a or any actual load/operating current.
- Connector current ratings must include condition, especially wire gauge/contact condition when present, such as AC/DC, AWG #22.
- Component class may be described in notes or evidence, but not in component operating-current target_type.
"""
    return f"""# {packet['packet_id']} - {packet['stage_name']}

## Packet Objective
Extract only the bounded data needed for this packet type: {packet['packet_type']}.

## Target
- Refdes: {packet['target_refdes']}
- MPN: {packet.get('target_mpn') or 'unknown'}
- Missing data item IDs: {item_ids}
- Expected target_type: {packet['expected_target_type']}

## Required Output
- Required output schema: {packet['required_output_schema']}
- Allowed output fields: values explicitly defined by the required schema, source evidence, units, confidence, and unknown markers.
{target_type_guidance}

## Provided Context Summary
- Missing data items: {len(context['missing_data_items'])}
- BOM rows: {len(context['target_bom_data'])}
- Schematic snippets: {len(context['schematic_snippets'])}
- Datasheet references/chunks: {len(context['datasheet_references'])}

## Evidence Requirements
- Datasheets are the primary evidence source for component facts.
- Do not guess.
- If the value is not present, return unknown.
- Every extracted numeric value must include unit and evidence.
- Cite the source artifact, page/table/section/chunk when available.

## Forbidden Outputs
{forbidden}
- Do not produce findings.
- Do not produce pass/fail.
- Do not produce compliance judgments.
- Do not perform final calculations.
- Do not mutate topology artifacts.

## Validation Checklist
- Output matches {packet['required_output_schema']}.
- Every returned value has evidence or is marked unknown.
- No uncited numeric value is present.
- No findings, pass/fail, compliance judgment, final calculation, or topology mutation is included.
"""


def packet_request(packet: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    return {
        "packet_id": packet["packet_id"],
        "stage_id": packet["stage_id"],
        "stage_name": packet["stage_name"],
        "packet_type": packet["packet_type"],
        "target_type": packet["target_type"],
        "expected_target_type": packet["expected_target_type"],
        "allowed_target_type": packet["expected_target_type"],
        "target_refdes": packet["target_refdes"],
        "target_mpn": packet.get("target_mpn"),
        "target_manufacturer": packet.get("target_manufacturer"),
        "target_value": packet.get("target_value"),
        "target_description": packet.get("target_description"),
        "target_bom_row_id": packet.get("target_bom_row_id"),
        "target_bom_evidence": packet.get("target_bom_evidence", []),
        "target_bom_match_status": packet.get("target_bom_match_status"),
        "target_bom_match_reason": packet.get("target_bom_match_reason"),
        "missing_data_item_ids": packet["missing_data_item_ids"],
        "required_output_schema": packet["required_output_schema"],
        "acceptable_sources": packet["acceptable_sources"],
        "forbidden_outputs": packet["forbidden_outputs"],
        "context": {
            "path": packet["context_path"],
            "missing_data_item_count": len(context["missing_data_items"]),
        },
        "status": "prompt_ready",
    }


def packet_status(packet_id: str) -> dict[str, Any]:
    return {
        "packet_id": packet_id,
        "status": "prompt_ready",
        "attempt_count": 0,
        "max_attempts": MAX_ATTEMPTS,
        "raw_response_path": None,
        "validated_result_path": None,
        "patch_path": None,
        "errors": [],
        "warnings": [],
    }


def build_packets(
    *,
    project: str,
    phase_id: str,
    phase_name: str,
    manifest_path: Path,
    out_dir: Path,
    max_items_per_packet: int,
    optional_paths: dict[str, Path | None],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[tuple[dict[str, Any], dict[str, Any], str, dict[str, Any]]]]:
    manifest = load_json(manifest_path)
    if not isinstance(manifest, dict):
        raise ValueError(f"missing-data-manifest artifact must be a JSON object: {manifest_path}")

    warnings: list[str] = []
    optional_data = {label: load_optional(path, label, warnings) for label, path in optional_paths.items()}
    bom_index = build_bom_refdes_index(optional_data.get("bom"))
    items = sorted(manifest_items(manifest), key=lambda row: item_id(row))
    if not items:
        warnings.append("missing-data manifest contains no manifest_items")

    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    skipped: list[dict[str, Any]] = []
    for item in items:
        stage_id, human_review, reason = route_item(item)
        if stage_id is None:
            skipped.append({"missing_data_item_id": item_id(item), "category": item.get("category"), "reason": reason})
            if reason:
                warnings.append(f"{item_id(item)} skipped: {reason}")
            continue
        if reason:
            warnings.append(f"{item_id(item)} routed to {stage_id}: {reason}")
        grouped.setdefault(group_key(item, stage_id, bom_index), []).append(item)

    packet_entries: list[dict[str, Any]] = []
    packet_files: list[tuple[dict[str, Any], dict[str, Any], str, dict[str, Any]]] = []
    stage_counts = {stage_id: 0 for stage_id in STAGES}
    source_paths = {"missing_data_manifest": manifest_path, **optional_paths}

    for key in sorted(grouped, key=lambda row: (row[0], refdes_sort_key(row[1]), row[2])):
        stage_id, refdes, mpn = key
        chunks = split_items(sorted(grouped[key], key=lambda row: item_id(row)), max(1, max_items_per_packet))
        for chunk in chunks:
            stage_counts[stage_id] += 1
            packet_id = f"{stage_id}-{stage_counts[stage_id]:03d}"
            stage = STAGES[stage_id]
            target = target_enrichment(refdes, bom_index)
            if target.get("target_bom_match_reason"):
                warnings.append(f"{packet_id} {refdes}: {target['target_bom_match_reason']}")
            packet = {
                "packet_id": packet_id,
                "stage_id": stage_id,
                "stage_name": stage["stage_name"],
                "packet_type": stage["packet_type"],
                "target_type": stage["target_type"],
                "expected_target_type": stage["target_type"],
                "allowed_target_type": stage["target_type"],
                "target_refdes": refdes,
                "target_mpn": target.get("target_mpn"),
                "target_manufacturer": target.get("target_manufacturer"),
                "target_value": target.get("target_value"),
                "target_description": target.get("target_description"),
                "target_bom_row_id": target.get("target_bom_row_id"),
                "target_bom_evidence": target.get("target_bom_evidence", []),
                "target_bom_match_status": target.get("target_bom_match_status"),
                "target_bom_match_reason": target.get("target_bom_match_reason"),
                "missing_data_item_ids": [item_id(item) for item in chunk],
                "blocked_calculations": sorted({str(block) for item in chunk for block in as_list(item.get("blocks"))}),
                "required_output_schema": stage["required_output_schema"],
                "acceptable_sources": ["datasheet"] if stage_id != "12H" else ["human_review", "local_artifact"],
                "forbidden_outputs": FORBIDDEN_OUTPUTS,
                "context_path": f"packets/{packet_id}/context.json",
                "prompt_path": f"packets/{packet_id}/prompt.md",
                "status_path": f"packets/{packet_id}/status.json",
                "status": "prompt_ready",
                "human_review_needed": stage_id == "12H",
            }
            context = context_for_packet(
                packet_id=packet_id,
                project=project,
                phase_id=phase_id,
                phase_name=phase_name,
                stage_id=stage_id,
                stage=stage,
                target_ref=refdes,
                target=target,
                items=chunk,
                optional_data=optional_data,
                source_paths=source_paths,
            )
            status = packet_status(packet_id)
            packet_entries.append(packet)
            packet_files.append((packet, context, prompt_for_packet(packet, context), status))

    missing_optional_count = len(warnings)
    summary = {
        "packet_count": len(packet_entries),
        "stage_12a_count": sum(1 for packet in packet_entries if packet["stage_id"] == "12A"),
        "stage_12b_count": sum(1 for packet in packet_entries if packet["stage_id"] == "12B"),
        "stage_12c_count": sum(1 for packet in packet_entries if packet["stage_id"] == "12C"),
        "stage_12d_count": sum(1 for packet in packet_entries if packet["stage_id"] == "12D"),
        "human_review_packet_count": sum(1 for packet in packet_entries if packet["stage_id"] == "12H"),
        "skipped_item_count": len(skipped),
        "missing_datasheet_context_count": 1 if optional_data.get("datasheet_manifest") is None and optional_data.get("datasheet_index") is None else 0,
        "missing_bom_context_count": 1 if optional_data.get("bom") is None else 0,
        "missing_schematic_context_count": 1 if optional_data.get("schematic_export") is None else 0,
        "missing_optional_artifact_warning_count": missing_optional_count,
        "error_count": 0,
        "warning_count": len(warnings),
    }
    queue = {
        "project": project,
        "phase_id": phase_id,
        "phase_name": phase_name,
        "schema_version": QUEUE_SCHEMA_VERSION,
        "generated_at_utc": utc_now(),
        "source_artifacts": bounded_artifact_refs(source_paths),
        "packets": packet_entries,
        "summary": summary,
        "errors": [],
        "warnings": warnings,
    }
    phase_status = {
        "project": project,
        "phase_id": phase_id,
        "phase_name": phase_name,
        "status": "queued",
        "packet_count": len(packet_entries),
        "pending_count": len(packet_entries),
        "accepted_count": 0,
        "rejected_count": 0,
        "human_review_count": sum(1 for packet in packet_entries if packet["human_review_needed"]),
        "retry_count": 0,
        "source_artifacts": bounded_artifact_refs(source_paths),
        "errors": [],
        "warnings": warnings,
        # Safety flags — AI packet phases never apply core artifacts.
        "safe_for_core_apply": False,
        "ready_for_core_apply": False,
        "qwen_vision_invoked": False,
    }
    phase_summary = {
        "project": project,
        "phase_id": phase_id,
        "phase_name": phase_name,
        "schema_version": QUEUE_SCHEMA_VERSION,
        "generated_at_utc": queue["generated_at_utc"],
        "summary": summary,
        "packet_lifecycle": PACKET_LIFECYCLE,
        "skipped_items": skipped,
        "errors": [],
        "warnings": warnings,
    }
    validate_outputs(queue, packet_files, {item_id(item) for item in items})
    return queue, phase_status, phase_summary, packet_files


def validate_outputs(
    queue: dict[str, Any],
    packet_files: list[tuple[dict[str, Any], dict[str, Any], str, dict[str, Any]]],
    valid_item_ids: set[str],
) -> None:
    packet_ids = [packet["packet_id"] for packet in queue["packets"]]
    if len(packet_ids) != len(set(packet_ids)):
        raise ValueError("packet IDs are not unique")
    for packet, context, prompt, status in packet_files:
        if not packet.get("required_output_schema"):
            raise ValueError(f"{packet['packet_id']} missing required_output_schema")
        if packet.get("expected_target_type") != packet.get("target_type"):
            raise ValueError(f"{packet['packet_id']} expected_target_type must match target_type")
        if context.get("expected_target_type") != packet.get("expected_target_type"):
            raise ValueError(f"{packet['packet_id']} context expected_target_type mismatch")
        if not set(packet["missing_data_item_ids"]).issubset(valid_item_ids):
            raise ValueError(f"{packet['packet_id']} references unknown missing data item")
        if len(context["missing_data_items"]) != len(packet["missing_data_item_ids"]):
            raise ValueError(f"{packet['packet_id']} context is not bounded to packet missing data items")
        for phrase in ("Do not guess", "Do not produce findings", "Do not produce pass/fail", "Do not produce compliance judgments"):
            if phrase not in prompt:
                raise ValueError(f"{packet['packet_id']} prompt missing guardrail: {phrase}")
        if packet["packet_type"] == "datasheet_current_extraction":
            for phrase in ("Use target_type exactly as provided in request.json", "target_type must be component_current_model", "Do not replace component operating-current target_type with component class words", "Connector current ratings are rating/capability candidates only"):
                if phrase not in prompt:
                    raise ValueError(f"{packet['packet_id']} prompt missing target_type guardrail: {phrase}")
        if status["packet_id"] != packet["packet_id"]:
            raise ValueError(f"{packet['packet_id']} status mismatch")
    summary = queue["summary"]
    if summary["packet_count"] != len(queue["packets"]):
        raise ValueError("summary packet_count does not match packet list")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build deterministic AI data-completion packet scaffolding.")
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--missing-data-manifest", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--phase-id", default=DEFAULT_PHASE_ID)
    parser.add_argument("--phase-name", default=DEFAULT_PHASE_NAME)
    parser.add_argument("--datasheet-manifest", default=None)
    parser.add_argument("--datasheet-index", default=None)
    parser.add_argument("--datasheet-evidence-index", default=None)
    parser.add_argument("--bom", default=None)
    parser.add_argument("--schematic-export", default=None)
    parser.add_argument("--role-resolution", default=None)
    parser.add_argument("--rail-relationships", default=None)
    parser.add_argument("--branch-topology-enriched", default=None)
    parser.add_argument("--part-info-index", default=None)
    parser.add_argument("--max-items-per-packet", type=int, default=DEFAULT_MAX_ITEMS_PER_PACKET)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    manifest_path = Path(args.missing_data_manifest)
    out_dir = Path(args.out_dir)
    optional_paths = {
        "datasheet_manifest": Path(args.datasheet_manifest) if args.datasheet_manifest else None,
        "datasheet_index": Path(args.datasheet_index) if args.datasheet_index else None,
        "datasheet_evidence_index": Path(args.datasheet_evidence_index) if args.datasheet_evidence_index else None,
        "bom": Path(args.bom) if args.bom else None,
        "schematic_export": Path(args.schematic_export) if args.schematic_export else None,
        "role_resolution": Path(args.role_resolution) if args.role_resolution else None,
        "rail_relationships": Path(args.rail_relationships) if args.rail_relationships else None,
        "branch_topology_enriched": Path(args.branch_topology_enriched) if args.branch_topology_enriched else None,
        "part_info_index": Path(args.part_info_index) if args.part_info_index else None,
    }
    try:
        if not manifest_path.exists():
            raise FileNotFoundError(f"missing missing-data-manifest JSON: {manifest_path}")
        queue, phase_status, phase_summary, packet_files = build_packets(
            project=args.project,
            phase_id=str(args.phase_id),
            phase_name=args.phase_name,
            manifest_path=manifest_path,
            out_dir=out_dir,
            max_items_per_packet=args.max_items_per_packet,
            optional_paths=optional_paths,
        )
        if not args.dry_run:
            write_json(out_dir / "packet_queue.json", queue)
            write_json(out_dir / "phase_status.json", phase_status)
            write_json(out_dir / f"phase_{args.phase_id}_summary.json", phase_summary)
            for packet, context, prompt, status in packet_files:
                packet_dir = out_dir / "packets" / packet["packet_id"]
                write_json(packet_dir / "request.json", packet_request(packet, context))
                write_json(packet_dir / "context.json", context)
                write_text(packet_dir / "prompt.md", prompt)
                write_json(packet_dir / "status.json", status)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    summary = queue["summary"]
    print(
        "ai packet phase build: "
        f"project={queue['project']} phase={queue['phase_id']} "
        f"packets={summary['packet_count']} "
        f"12A={summary['stage_12a_count']} 12B={summary['stage_12b_count']} "
        f"12C={summary['stage_12c_count']} 12D={summary['stage_12d_count']} "
        f"human={summary['human_review_packet_count']} skipped={summary['skipped_item_count']} "
        f"errors={summary['error_count']} warnings={summary['warning_count']} "
        f"out={out_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
