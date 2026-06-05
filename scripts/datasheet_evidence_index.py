#!/usr/bin/env python3
"""Build deterministic datasheet evidence and candidate extraction artifacts.

PR39 scope: parse local datasheet text/PDF files, extract bounded candidate
facts with provenance, and emit review-only artifacts. This script does not
call AI services, fetch network content, mutate source datasheets, write core
current/rating/topology artifacts, or produce findings/compliance conclusions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "1.0"
DEFAULT_PROJECT = "example"
TEXT_SUFFIXES = {".txt", ".text", ".md"}
PDF_SUFFIXES = {".pdf"}
OUTPUT_FILES = {
    "index": "datasheet-evidence-index.json",
    "candidates": "datasheet-extraction-candidates.json",
    "status": "datasheet-extraction-status.json",
    "blockers": "datasheet-extraction-blockers.json",
    "review": "datasheet-extraction-review.json",
}
SECTION_NAMES = [
    "Absolute Maximum Ratings",
    "Recommended Operating Conditions",
    "Electrical Characteristics",
    "Thermal Characteristics",
    "Ordering Information",
    "Pin Description",
    "Package / Derating",
    "Typical Application",
]
FORBIDDEN_KEYS = {
    "finding",
    "findings",
    "pass_fail",
    "pass",
    "fail",
    "compliance",
    "severity",
    "conclusion",
}


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


def safe_id(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")
    return text or "unknown"


def norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def key_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def first_field(row: dict[str, Any], aliases: set[str]) -> Any:
    sources = [row]
    for key in ("raw", "fields", "custom_metadata"):
        if isinstance(row.get(key), dict):
            sources.append(row[key])
    for source in sources:
        for key, value in source.items():
            if key_name(key) in aliases and value not in (None, ""):
                return value
    return None


def parse_refdes(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item or "").strip()]
    if value is None:
        return []
    return [token for token in re.split(r"[\s,;]+", str(value).strip()) if token]


def manifest_rows(datasheets_dir: Path) -> list[dict[str, Any]]:
    path = datasheets_dir / "datasheet_manifest.jsonl"
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            rows.append({"manifest_line": line_no, "parse_error": "invalid JSONL row"})
            continue
        if not isinstance(row, dict):
            continue
        row["manifest_line"] = line_no
        rows.append(row)
    return rows


def parse_manifest_refdes(row: dict[str, Any]) -> list[str]:
    refs = row.get("reference_designators")
    if refs is None and isinstance(row.get("raw_bom_fields"), dict):
        refs = row["raw_bom_fields"].get("REF DES")
    return parse_refdes(refs)


def compact_manifest_match(row: dict[str, Any], path: Path) -> dict[str, Any]:
    return {
        "manifest_line": row.get("manifest_line"),
        "bom_row_index": row.get("bom_row_index"),
        "status": row.get("status"),
        "selected_mpn": row.get("selected_mpn"),
        "selected_manufacturer": row.get("selected_manufacturer"),
        "reference_designators": parse_manifest_refdes(row),
        "description": row.get("description"),
        "local_saved_path": row.get("local_saved_path"),
        "matched_filename": path.name,
        "mpn_text_verified": row.get("mpn_text_verified"),
        "approved_equivalent_or_family_match": row.get("approved_equivalent_or_family_match"),
    }


def bom_rows(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    data = load_json(path)
    rows = data if isinstance(data, list) else []
    if isinstance(data, dict):
        for key in ("components", "items", "rows", "bom"):
            value = data.get(key)
            if isinstance(value, list):
                rows = value
                break
            if isinstance(value, dict):
                for nested in ("components", "items", "rows"):
                    if isinstance(value.get(nested), list):
                        rows = value[nested]
                        break
    parsed = []
    for idx, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            continue
        mpn = first_field(row, {"mpn", "manufacturerpartnumber", "manufacturerpart", "partnumber", "mfgpn1", "mfgpn"})
        manufacturer = first_field(row, {"manufacturer", "mfr", "vendor", "mfg1"})
        refdes = first_field(row, {"refdes", "designator", "reference", "references"})
        manufacturers = row.get("manufacturers")
        if (not mpn or not manufacturer) and isinstance(manufacturers, list) and manufacturers:
            first = manufacturers[0] if isinstance(manufacturers[0], dict) else {}
            mpn = mpn or first.get("mpn")
            manufacturer = manufacturer or first.get("manufacturer")
        parsed.append(
            {
                "bom_row_index": idx,
                "refdes": parse_refdes(refdes),
                "mpn": str(mpn).strip() if mpn is not None else None,
                "manufacturer": str(manufacturer).strip() if manufacturer is not None else None,
                "normalized_mpn": norm(mpn) if mpn else None,
            }
        )
    return parsed


def matching_manifest_entry(path: Path, manifests: list[dict[str, Any]]) -> dict[str, Any] | None:
    file_norm = norm(path.name)
    for row in manifests:
        if row.get("parse_error"):
            continue
        saved = row.get("local_saved_path") or row.get("local_saved_filename")
        if saved and Path(str(saved)).name == path.name:
            return compact_manifest_match(row, path)
        for token in [row.get("selected_mpn"), row.get("approved_equivalent_or_family_match"), *(row.get("mpn_candidates") or [])]:
            normalized = norm(token)
            if normalized and normalized in file_norm:
                return compact_manifest_match(row, path)
    return None


def matching_bom_entry(path: Path, pages: list[dict[str, Any]], bom: list[dict[str, Any]]) -> dict[str, Any] | None:
    filename_haystack = norm(path.stem)
    for row in bom:
        normalized = row.get("normalized_mpn")
        if normalized and normalized in filename_haystack:
            return row
    haystack = norm(path.stem + " " + " ".join(page["text"][:500] for page in pages))
    for row in bom:
        normalized = row.get("normalized_mpn")
        if normalized and normalized in haystack:
            return row
    return bom[0] if len(bom) == 1 else None


def extract_pdf_text(path: Path, max_pages: int | None) -> tuple[list[dict[str, Any]], list[str], int | None]:
    command = ["pdftotext"]
    if max_pages is not None:
        command.extend(["-f", "1", "-l", str(max_pages)])
    command.extend([str(path), "-"])
    try:
        completed = subprocess.run(command, text=True, capture_output=True)
    except FileNotFoundError:
        return [], ["pdftotext unavailable; PDF text extraction skipped"], None
    if completed.returncode != 0:
        return [], [completed.stderr.strip() or "pdftotext failed; PDF text extraction skipped"], None
    page_texts = completed.stdout.split("\f")
    if max_pages is not None:
        page_texts = page_texts[:max_pages]
    return [{"page": idx + 1, "text": text} for idx, text in enumerate(page_texts) if text.strip()], [], len(page_texts)


def read_datasheet_text(path: Path, max_pages: int | None) -> tuple[list[dict[str, Any]], list[str], int | None]:
    if path.suffix.lower() in TEXT_SUFFIXES:
        text = path.read_text(encoding="utf-8", errors="replace")
        page_texts = text.split("\f")
        if max_pages is not None:
            page_texts = page_texts[:max_pages]
        return [{"page": idx + 1, "text": value} for idx, value in enumerate(page_texts) if value.strip()], [], len(page_texts)
    if path.suffix.lower() in PDF_SUFFIXES:
        return extract_pdf_text(path, max_pages)
    return [], [f"unsupported datasheet suffix: {path.suffix}"], None


def snippet(text: str, limit: int = 600) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    return compact[:limit]


def section_candidates(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for page in pages:
        lines = [line.strip() for line in page["text"].splitlines() if line.strip()]
        for idx, line in enumerate(lines):
            for name in SECTION_NAMES:
                pattern = re.sub(r"\s*/\s*", r"\\s*(?:/|and)?\\s*", re.escape(name))
                if re.search(pattern, line, re.IGNORECASE):
                    rows.append(
                        {
                            "section_name": name,
                            "page": page["page"],
                            "line": idx + 1,
                            "evidence_quote": snippet(line),
                        }
                    )
    rows.sort(key=lambda row: (row["page"], row["line"], row["section_name"]))
    return rows


def page_blocks(path: Path, pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blocks = []
    for page in pages:
        text = snippet(page["text"], 1000)
        if text:
            blocks.append(
                {
                    "source_file": str(path),
                    "page": page["page"],
                    "text_snippet": text,
                    "evidence_block_id": f"ev_{safe_id(path.stem)}_p{page['page']}",
                }
            )
    return blocks


def normalize_unit(raw_value: str, raw_unit: str) -> tuple[float | None, str | None, str | None]:
    value_text = raw_value.replace(",", "").strip()
    if re.search(r"(?:\d)\s*(?:-|–|—|to)\s*(?:\d)", value_text, re.IGNORECASE):
        return None, None, "ambiguous_range"
    try:
        value = float(value_text)
    except ValueError:
        return None, None, "invalid_numeric_value"
    unit_key = raw_unit.strip().replace("Ω", "ohm").replace("µ", "u").lower()
    factors = {
        "a": (1.0, "A"),
        "ma": (1e-3, "A"),
        "ua": (1e-6, "A"),
        "v": (1.0, "V"),
        "mv": (1e-3, "V"),
        "w": (1.0, "W"),
        "mw": (1e-3, "W"),
        "ohm": (1.0, "ohm"),
        "mohm": (1e-3, "ohm"),
        "kohm": (1e3, "ohm"),
        "f": (1.0, "F"),
        "uf": (1e-6, "F"),
        "nf": (1e-9, "F"),
        "pf": (1e-12, "F"),
    }
    if unit_key not in factors:
        return None, None, "unsupported_unit"
    factor, normalized_unit = factors[unit_key]
    normalized = value * factor
    return normalized if math.isfinite(normalized) else None, normalized_unit, None


def line_has_ambiguous_range(line: str) -> bool:
    return bool(re.search(r"\b\d+(?:\.\d+)?\s*(?:-|–|—|to)\s*\d+(?:\.\d+)?\s*(?:A|mA|uA|µA|V|mV|W|mW|ohm|mOhm|kOhm|F|uF|µF|nF|pF)\b", line, re.IGNORECASE))


def condition_from_line(line: str) -> str | None:
    pieces = []
    for pattern in [r"@\s*([^,;]+)", r"(?:at|with)\s+([^,;]+)", r"(?:Ta|TJ|Tc)\s*=\s*([^,;]+)"]:
        for match in re.finditer(pattern, line, re.IGNORECASE):
            value = match.group(1).strip()
            if value and value not in pieces:
                pieces.append(value)
    return "; ".join(pieces) if pieces else None


def parameter_for_line(line: str, unit: str) -> tuple[str, str | None]:
    lower = line.lower()
    normalized_unit = unit.replace("µ", "u").lower()
    if "hold current" in lower or re.search(r"\bi\s*h\b", lower):
        return "fuse_hold_current", "fuse"
    if "trip current" in lower or re.search(r"\bi\s*t\b", lower):
        return "fuse_trip_current", "fuse"
    if "connector" in lower and "current" in lower:
        return "connector_current_rating", "connector"
    if "output current" in lower:
        return "regulator_output_current", "regulator"
    if "current limit" in lower:
        return "load_switch_current_limit", "load_switch"
    if "saturation current" in lower or "isat" in lower:
        return "inductor_saturation_current", "inductor"
    if ("rds" in lower or "rds(on)" in lower) and normalized_unit in {"ohm", "mohm", "kohm"}:
        return "rds_on", "mosfet"
    if "esr" in lower:
        return "esr", "capacitor"
    if "capacitance" in lower or normalized_unit in {"f", "uf", "nf", "pf"}:
        return "capacitance", "capacitor"
    if "inductance" in lower:
        return "inductance", "inductor"
    if "thermal resistance" in lower or "θ" in lower or "theta" in lower:
        return "thermal_resistance_or_derating_note", None
    if "power" in lower or normalized_unit in {"w", "mw"}:
        return "power_rating", None
    if "voltage" in lower or normalized_unit in {"v", "mv"}:
        return "voltage_rating", None
    if "current" in lower or normalized_unit in {"a", "ma", "ua"}:
        return "current_rating", None
    if "resistance" in lower or normalized_unit in {"ohm", "mohm", "kohm"}:
        return "resistance", None
    return "candidate_value", None


def candidate_target(parameter_name: str, component_hint: str | None, bom_entry: dict[str, Any] | None) -> tuple[str | None, str | None]:
    target_type = component_hint
    if not target_type:
        if parameter_name.startswith("fuse_"):
            target_type = "fuse"
        elif parameter_name.startswith("connector_"):
            target_type = "connector"
        elif parameter_name.startswith("regulator_"):
            target_type = "regulator"
    refs = as_list(bom_entry.get("refdes") if bom_entry else None)
    target_identity = refs[0] if refs else (bom_entry.get("mpn") if bom_entry else None)
    return target_type, target_identity


def make_candidate(
    *,
    source_file: Path,
    page: int | None,
    line: str,
    raw_value: str,
    raw_unit: str,
    occurrence: int,
    bom_entry: dict[str, Any] | None,
    method: str,
) -> dict[str, Any]:
    parameter_name, component_hint = parameter_for_line(line, raw_unit)
    value_normalized, unit_normalized, normalize_warning = normalize_unit(raw_value, raw_unit)
    ambiguous = bool(normalize_warning) or line_has_ambiguous_range(line) or bool(re.search(r"\b(min|typ|max)\b.*\b(min|typ|max)\b", line, re.IGNORECASE))
    if parameter_name == "regulator_output_current" and re.search(r"\b(input|output)\b", line, re.IGNORECASE) is None:
        ambiguous = True
    if parameter_name == "current_rating" and "regulator" in line.lower() and "output" not in line.lower():
        ambiguous = True
    target_type, target_identity = candidate_target(parameter_name, component_hint, bom_entry)
    evidence = snippet(line)
    requires_review = ambiguous or not evidence
    usable_for_context = bool(evidence)
    basis = f"{source_file.name}|{page or 'unknown'}|{parameter_name}|{raw_value}|{raw_unit}|{occurrence}"
    return {
        "candidate_id": f"dei_{safe_id(basis)}",
        "target_type": target_type,
        "target_identity": target_identity,
        "parameter_name": parameter_name,
        "value_raw": raw_value,
        "value_normalized": None if requires_review else value_normalized,
        "unit_raw": raw_unit,
        "unit_normalized": None if requires_review else unit_normalized,
        "condition": condition_from_line(line),
        "source_file": str(source_file),
        "page": page,
        "evidence_quote": evidence,
        "extraction_method": method,
        "confidence": 0.55 if requires_review else 0.82,
        "requires_human_review": requires_review,
        "usable_for_ai_packet_context": usable_for_context,
        "usable_for_direct_ingestion": False,
        "warnings": [normalize_warning] if normalize_warning else [],
    }


def extract_candidates_for_pages(source_file: Path, pages: list[dict[str, Any]], bom_entry: dict[str, Any] | None) -> list[dict[str, Any]]:
    unit = r"(?:uA|µA|mA|A|mV|V|mW|W|mOhm|kOhm|ohm|Ω|uF|µF|nF|pF|F|°C/W|C/W)"
    value = r"(?:\d+(?:\.\d+)?(?:\s*(?:-|–|—|to)\s*\d+(?:\.\d+)?)?)"
    pattern = re.compile(rf"(?P<value>{value})\s*(?P<unit>{unit})\b", re.IGNORECASE)
    candidates: list[dict[str, Any]] = []
    occurrence = 0
    for page in pages:
        for raw_line in page["text"].splitlines():
            line = re.sub(r"\s+", " ", raw_line).strip()
            if not line:
                continue
            lower = line.lower()
            if not any(token in lower for token in ["current", "voltage", "power", "resistance", "rds", "capacitance", "esr", "inductance", "saturation", "hold", "trip", "connector", "thermal", "derating", "limit"]):
                continue
            for match in pattern.finditer(line):
                raw_unit = match.group("unit")
                if raw_unit.lower() in {"v", "mv"} and "=" in line[max(0, match.start() - 8) : match.start()] and "voltage" not in lower:
                    continue
                occurrence += 1
                candidates.append(
                    make_candidate(
                        source_file=source_file,
                        page=page.get("page"),
                        line=line,
                        raw_value=match.group("value"),
                        raw_unit=raw_unit,
                        occurrence=occurrence,
                        bom_entry=bom_entry,
                        method="regex_line_context_v1",
                    )
                )
    candidates.sort(key=lambda row: row["candidate_id"])
    return candidates


def collect_datasheet_files(datasheets_dir: Path) -> list[Path]:
    if not datasheets_dir.exists():
        return []
    return sorted(path for path in datasheets_dir.rglob("*") if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES.union(PDF_SUFFIXES))


def ensure_outputs_inside(out_dir: Path, paths: list[Path]) -> None:
    base = out_dir.resolve()
    for path in paths:
        path.resolve().relative_to(base)


def build_outputs(
    *,
    project: str,
    datasheets_dir: Path,
    bom_path: Path | None,
    part_info_index_path: Path | None,
    missing_data_manifest_path: Path | None,
    out_dir: Path,
    strict: bool,
    max_pages: int | None,
    include_tables: bool,
    text_only: bool,
) -> dict[str, dict[str, Any]]:
    warnings: list[str] = []
    blockers: list[dict[str, Any]] = []
    bom = bom_rows(bom_path)
    manifests = manifest_rows(datasheets_dir)
    part_info_index = load_json(part_info_index_path) if part_info_index_path and part_info_index_path.exists() else None
    missing_data_manifest = load_json(missing_data_manifest_path) if missing_data_manifest_path and missing_data_manifest_path.exists() else None
    if part_info_index_path and not part_info_index_path.exists():
        warnings.append(f"optional part-info-index missing: {part_info_index_path}")
    if missing_data_manifest_path and not missing_data_manifest_path.exists():
        warnings.append(f"optional missing-data-manifest missing: {missing_data_manifest_path}")

    files = collect_datasheet_files(datasheets_dir)
    if not files:
        blockers.append({"blocker_id": "missing_datasheets", "reason": "no supported local datasheet files found", "path": str(datasheets_dir)})

    documents: list[dict[str, Any]] = []
    all_candidates: list[dict[str, Any]] = []
    for path in files:
        before_stat = path.stat()
        pages, file_warnings, page_count = read_datasheet_text(path, max_pages)
        after_stat = path.stat()
        if (before_stat.st_mtime_ns, before_stat.st_size) != (after_stat.st_mtime_ns, after_stat.st_size):
            blockers.append({"blocker_id": f"source_mutated_{safe_id(path.name)}", "reason": "source datasheet changed during extraction", "path": str(path)})
        bom_entry = matching_bom_entry(path, pages, bom)
        manifest_entry = matching_manifest_entry(path, manifests)
        if manifest_entry and manifest_entry.get("selected_mpn"):
            normalized_manifest_mpn = norm(manifest_entry.get("selected_mpn"))
            bom_entry = next((row for row in bom if row.get("normalized_mpn") == normalized_manifest_mpn), bom_entry)
        blocks = page_blocks(path, pages)
        sections = section_candidates(pages)
        candidates = extract_candidates_for_pages(path, pages, bom_entry)
        all_candidates.extend(candidates)
        documents.append(
            {
                "document_id": f"doc_{safe_id(path.stem)}",
                "source_file": str(path),
                "filename": path.name,
                "file_sha256": sha256_file(path),
                "page_count": page_count,
                "extracted_text_available": bool(pages),
                "text_extraction_status": "extracted" if pages else "warning",
                "extracted_tables_available": False if not include_tables else False,
                "extraction_warnings": file_warnings,
                "page_evidence_blocks": blocks,
                "section_candidates": sections,
                "candidate_count": len(candidates),
                "matched_mpn": bom_entry.get("mpn") if bom_entry else manifest_entry.get("selected_mpn") if manifest_entry else None,
                "matched_manufacturer": bom_entry.get("manufacturer") if bom_entry else manifest_entry.get("selected_manufacturer") if manifest_entry else None,
                "matched_refdes": bom_entry.get("refdes") if bom_entry else manifest_entry.get("reference_designators") if manifest_entry else [],
                "matched_bom_entry": bom_entry,
                "matched_manifest_entry": manifest_entry,
            }
        )
        warnings.extend(f"{path.name}: {warning}" for warning in file_warnings)

    human_review = [row for row in all_candidates if row["requires_human_review"]]
    usable = [row for row in all_candidates if row["usable_for_ai_packet_context"]]
    now = utc_now()
    source_artifacts = {
        "datasheets_dir": str(datasheets_dir),
        "bom": str(bom_path) if bom_path else None,
        "part_info_index": str(part_info_index_path) if part_info_index_path else None,
        "missing_data_manifest": str(missing_data_manifest_path) if missing_data_manifest_path else None,
        "datasheet_manifest_jsonl": str(datasheets_dir / "datasheet_manifest.jsonl") if (datasheets_dir / "datasheet_manifest.jsonl").exists() else None,
    }
    index = {
        "artifact_type": "datasheet_evidence_index",
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": now,
        "project": project,
        "source_artifacts": source_artifacts,
        "documents": documents,
        "summary": {
            "datasheet_file_count": len(files),
            "documents_with_text": sum(1 for doc in documents if doc["extracted_text_available"]),
            "documents_with_tables": 0,
            "section_candidate_count": sum(len(doc["section_candidates"]) for doc in documents),
            "candidate_count": len(all_candidates),
            "warning_count": len(warnings),
            "blocker_count": len(blockers),
        },
        "warnings": warnings,
        "errors": [],
    }
    candidates = {
        "artifact_type": "datasheet_extraction_candidates",
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": now,
        "project": project,
        "source_artifacts": source_artifacts,
        "candidates": all_candidates,
        "summary": {
            "candidate_count": len(all_candidates),
            "requires_human_review_count": len(human_review),
            "usable_for_ai_packet_context_count": len(usable),
            "usable_for_direct_ingestion_count": 0,
        },
        "warnings": warnings,
        "errors": [],
    }
    status = {
        "artifact_type": "datasheet_extraction_status",
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": now,
        "project": project,
        "status": "blocked" if blockers else "passed",
        "strict": strict,
        "source_artifacts": source_artifacts,
        "summary": {
            "document_count": len(documents),
            "candidate_count": len(all_candidates),
            "human_review_candidate_count": len(human_review),
            "blocker_count": len(blockers),
            "warning_count": len(warnings),
            "part_info_index_loaded": isinstance(part_info_index, dict),
            "missing_data_manifest_loaded": isinstance(missing_data_manifest, dict),
            "datasheet_manifest_row_count": len(manifests),
        },
        "execution_pass": not blockers,
        "warnings": warnings,
        "errors": [],
    }
    blocker_artifact = {
        "artifact_type": "datasheet_extraction_blockers",
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": now,
        "project": project,
        "blockers": blockers,
        "summary": {
            "blocker_count": len(blockers),
        },
        "warnings": warnings,
        "errors": [],
    }
    review = {
        "artifact_type": "datasheet_extraction_review",
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": now,
        "project": project,
        "human_review_candidates": human_review,
        "review_notes": [
            "Candidates are evidence-only and are not directly applied to core artifacts.",
            "Ambiguous ranges, unclear min/typ/max values, and missing evidence require human review.",
        ],
        "summary": {
            "human_review_candidate_count": len(human_review),
            "direct_ingestion_candidate_count": 0,
        },
        "warnings": warnings,
        "errors": [],
    }
    return {
        "index": index,
        "candidates": candidates,
        "status": status,
        "blockers": blocker_artifact,
        "review": review,
    }


def walk_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            keys.add(str(key))
            keys.update(walk_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(walk_keys(child))
    return keys


def validate_no_forbidden_outputs(outputs: dict[str, dict[str, Any]]) -> None:
    for name, artifact in outputs.items():
        forbidden = {key for key in walk_keys(artifact) if key in FORBIDDEN_KEYS}
        if forbidden:
            raise ValueError(f"{name} artifact contains forbidden field(s): {', '.join(sorted(forbidden))}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build deterministic datasheet evidence index and extraction candidates.")
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--datasheets-dir", required=True)
    parser.add_argument("--bom")
    parser.add_argument("--part-info-index")
    parser.add_argument("--missing-data-manifest")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--max-pages", type=int)
    parser.add_argument("--include-tables", action="store_true")
    parser.add_argument("--text-only", action="store_true")
    parser.add_argument("--schema", default="schemas/datasheet_evidence_index_schema.json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    out_dir = Path(args.out_dir)
    output_paths = [out_dir / filename for filename in OUTPUT_FILES.values()]
    try:
        ensure_outputs_inside(out_dir, output_paths)
        outputs = build_outputs(
            project=args.project,
            datasheets_dir=Path(args.datasheets_dir),
            bom_path=Path(args.bom) if args.bom else None,
            part_info_index_path=Path(args.part_info_index) if args.part_info_index else None,
            missing_data_manifest_path=Path(args.missing_data_manifest) if args.missing_data_manifest else None,
            out_dir=out_dir,
            strict=args.strict,
            max_pages=args.max_pages,
            include_tables=args.include_tables,
            text_only=args.text_only,
        )
        validate_no_forbidden_outputs(outputs)
        for key, artifact in outputs.items():
            write_json(out_dir / OUTPUT_FILES[key], artifact)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    summary = outputs["status"]["summary"]
    print(
        "datasheet evidence index: "
        f"project={args.project} documents={summary['document_count']} "
        f"candidates={summary['candidate_count']} human_review={summary['human_review_candidate_count']} "
        f"out={out_dir}"
    )
    if args.strict and outputs["blockers"]["blockers"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
