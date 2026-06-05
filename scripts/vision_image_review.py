#!/usr/bin/env python3
"""
Vision-based PNG review for ThomsonLint Phase 13.

Uses an OpenAI-compatible multimodal /v1/chat/completions endpoint.
Requires a vision-capable model. Text-only models should fail/block rather
than pretending metadata/pixel sampling is vision.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import mimetypes
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

STRICT_JSON_ONLY_PROMPT = (
    "Return only minified valid JSON. No markdown. No prose. No comments. "
    "No trailing commas. Escape quotes inside strings. Output exactly one JSON object."
)
DEFAULT_VISION_TEMPERATURE = 0.1
ASSESSMENT_PROFILES = {"strict", "balanced", "engineering"}
ASSESSMENT_ENABLED_PROFILES = {"balanced", "engineering"}
GENERIC_VISION_CLAIMS = {
    "routing verified",
    "connectivity verified",
    "power distribution verified",
    "layer inspected",
    "visual inspection passed",
    "component placement verified",
}
FINAL_STYLE_CLAIM_PATTERNS = [
    re.compile(r"\bfails?\b", re.IGNORECASE),
    re.compile(r"\bincorrectly designed\b", re.IGNORECASE),
    re.compile(r"\bviolates?\b", re.IGNORECASE),
    re.compile(r"\bviolation found\b", re.IGNORECASE),
]


def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def assessment_profile_from_env() -> str:
    value = env("THOMSONLINT_ASSESSMENT_PROFILE", "strict")
    profile = (value or "strict").strip().lower()
    if profile not in ASSESSMENT_PROFILES:
        raise SystemExit(
            "Invalid THOMSONLINT_ASSESSMENT_PROFILE: "
            f"{profile!r}. Expected one of: balanced, engineering, strict."
        )
    return profile


def assessment_enabled(profile: str) -> bool:
    return profile in ASSESSMENT_ENABLED_PROFILES


def vision_temperature_from_env() -> float:
    value = env("VISION_TEMPERATURE")
    if value is None:
        return DEFAULT_VISION_TEMPERATURE
    try:
        temperature = float(value)
    except ValueError as exc:
        raise SystemExit("Invalid VISION_TEMPERATURE: expected numeric value from 0.0 to 2.0") from exc
    if not math.isfinite(temperature) or temperature < 0.0 or temperature > 2.0:
        raise SystemExit("Invalid VISION_TEMPERATURE: expected numeric value from 0.0 to 2.0")
    return temperature


def data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def post_chat_completion(
    base_url: str,
    api_key: str,
    model: str,
    image_path: Path,
    prompt: str,
    timeout: int,
    max_tokens: int,
    temperature: float = DEFAULT_VISION_TEMPERATURE,
) -> str:
    url = base_url.rstrip("/") + "/chat/completions"

    payload = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are reviewing electronics schematic/layout PNG evidence. "
                    "Use actual image content. Do not claim pixel-derived trace widths, "
                    "clearances, pad sizes, or dimensions. Return compact valid JSON only."
                ),
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": data_uri(image_path)},
                    },
                ],
            },
        ],
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} from vision endpoint: {body[:1000]}") from e
    except Exception as e:
        raise RuntimeError(f"vision endpoint call failed: {e}") from e

    try:
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        raise RuntimeError(f"unexpected response shape: {json.dumps(data)[:1000]}") from e


def strip_markdown_fences(text: str) -> str:
    cleaned = text.strip()
    if not cleaned.startswith("```"):
        return cleaned

    lines = cleaned.splitlines()
    if not lines:
        return cleaned
    if not lines[0].strip().startswith("```"):
        return cleaned

    lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def first_complete_json_object(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None

    depth = 0
    in_string = False
    escape = False
    for idx in range(start, len(text)):
        char = text[idx]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : idx + 1]

    return None


def extract_json(text: str) -> dict[str, Any]:
    cleaned = strip_markdown_fences(text)

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    object_text = first_complete_json_object(cleaned)
    if object_text:
        parsed = json.loads(object_text)
        if isinstance(parsed, dict):
            return parsed

    raise ValueError(f"model did not return valid JSON: {text[:500]}")


def engineering_annotation_prompt(path: Path, kind: str) -> str:
    common = f"""

Engineering annotation requirements:
- Return valid JSON only.
- Extract engineering observations, not final findings.
- Label hypotheses, engineering concerns, blocked verifications, datasheet checks,
  calculations needed, and human-review questions separately from verified facts.
- State what cannot be verified from image alone.
- Do not invent numeric values.
- Do not make exact geometry claims from screenshots/Gerber images unless tied to board-coordinate evidence.
- Do not report "verified" unless a concrete page-specific observation is included.
- Reject generic visual claims such as "routing verified", "connectivity verified",
  "power distribution verified", "layer inspected", "visual inspection passed",
  or "component placement verified" unless accompanied by concrete page-specific observations.
- Do not create final findings.
- Do not use final-style language such as "Regulator fails thermal check",
  "PMOS is incorrectly designed", "This trace violates current density", or
  "Impedance violation found" unless later deterministic final-finding gates prove it.

Add these keys to the returned JSON:
{{
  "image_id": "{path.name}",
  "source_file": "{path}",
  "page_number": null,
  "observed_circuits": [],
  "observed_refdes": [],
  "observed_nets": [],
  "likely_circuit_purpose": [],
  "component_role_observations": [],
  "engineering_concern_candidates": [],
  "blocked_verification_candidates": [],
  "datasheet_check_needed": [],
  "calculation_needed": [],
  "human_review_questions": [],
  "not_verifiable_from_image": [],
  "confidence": 0.0
}}
"""
    if kind == "schematic":
        return common + """
For schematic pages, identify visible functional blocks, important refdes and net names,
likely circuit purpose, and component roles suggested by the image.
Safe examples:
- "Observed an apparent I2C buffer/pullup section. Verify pullup sizing, bus capacitance, voltage domains, and enable pin bias."
- "Observed an apparent 24 V input / 3.3 V regulator section. Verify regulator output load, dropout margin, thermal dissipation, and input/output capacitor requirements."
- "Observed apparent high-side PMOS/load-switching path. Verify Vds, Vgs, gate pull network, transient exposure, and load current."
"""
    return common + """
For layout/Gerber pages, identify layer/page type, visible routing/plane/features,
possible layout or manufacturing concerns, whether coordinate/board-data is required
before geometry claims, and what cannot be concluded from the image alone.
"""


def prompt_for(path: Path, kind: str, assessment_profile: str = "strict") -> str:
    key_scope = "at least" if assessment_enabled(assessment_profile) else "exactly"
    if kind == "schematic":
        prompt = f"""
Review this schematic PNG page: {path.name}

Return JSON with {key_scope} these keys:
{{
  "page_type": "schematic",
  "visual_review_performed": true,
  "brief_description": "...",
  "visible_circuit_blocks": ["..."],
  "visible_components_or_refdes": ["..."],
  "visible_net_labels_or_signal_names": ["..."],
  "visible_component_values_or_ratings": ["..."],
  "possible_electrical_calculations_from_visible_values": ["..."],
  "possible_concerns_or_followups": ["..."],
  "limitations": ["..."],
  "confirmation_no_pixel_quantitative_claims": true
}}

Rules:
- Use what is visible in the image.
- It is allowed to read visible resistor/capacitor/voltage/current values.
- It is allowed to suggest electrical calculations from visible schematic values.
- Do not invent unreadable text.
- Do not measure physical layout geometry from pixels.
- Do not create final findings.
"""
        if assessment_enabled(assessment_profile):
            prompt += engineering_annotation_prompt(path, kind)
        return prompt
    prompt = f"""
Review this PCB layout/Gerber PNG page: {path.name}

Return JSON with {key_scope} these keys:
{{
  "page_type": "layout",
  "visual_review_performed": true,
  "brief_description": "...",
  "visible_layer_or_drawing_role": "...",
  "visible_board_features": ["..."],
  "visible_text_or_labels": ["..."],
  "possible_concerns_or_followups": ["..."],
  "limitations": ["..."],
  "confirmation_no_pixel_quantitative_claims": true
}}

Rules:
- Use what is visible in the image.
- Do not infer trace width, clearance, creepage, pad size, hole size, or board dimensions from pixels.
- For physical geometry, defer to board JSON / IPC-2581 evidence.
- Do not create final findings.
"""
    if assessment_enabled(assessment_profile):
        prompt += engineering_annotation_prompt(path, kind)
    return prompt


def list_images(exports: Path, project: str) -> list[tuple[str, Path]]:
    """Return (kind, path) for every expected image found on disk.

    Uses deterministic glob patterns so the set of images is reproducible
    across runs as long as the same files exist.
    """
    schematic = sorted(exports.glob(f"{project}-img-sch-p*.png"))
    layout = sorted(exports.glob(f"{project}-img-layout-p*.png"))
    return [("schematic", p) for p in schematic] + [("layout", p) for p in layout]


def expected_image_ids_from_inventory(exports: Path, project: str) -> list[str]:
    """Extract canonical image IDs from the Phase 7 inventory artifact.

    Reads exports/<project>-image-evidence-inventory.json and returns a
    deduplicated list of file basenames (Path(...).name) derived from:
      - schematic_pngs entries
      - layout_pngs entries
      - output_files entries (if present as dicts with a 'file' or 'path' key)

    This is the source-of-truth for how many images Phase 13 must review.
    """
    inv_path = exports / f"{project}-image-evidence-inventory.json"
    if not inv_path.exists():
        return []

    try:
        data = json.loads(inv_path.read_text(encoding="utf-8"))
    except Exception:
        return []

    ids: list[str] = []
    seen: set[str] = set()

    def _add(raw: str) -> None:
        name = Path(raw).name
        if name and name not in seen:
            seen.add(name)
            ids.append(name)

    for key in ("schematic_pngs", "layout_pngs"):
        entries = data.get(key, [])
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, str):
                    _add(entry)
                elif isinstance(entry, dict):
                    path_val = entry.get("file") or entry.get("path") or entry.get("image_path", "")
                    if isinstance(path_val, str):
                        _add(path_val)

    output_files = data.get("output_files", [])
    if isinstance(output_files, list):
        for entry in output_files:
            if isinstance(entry, dict):
                path_val = entry.get("file") or entry.get("path") or entry.get("image_path", "")
                if isinstance(path_val, str) and Path(path_val).name:
                    _add(path_val)

    return ids


def expected_image_paths(exports: Path, project: str, expected_ids: list[str]) -> list[tuple[str, Path]]:
    """Build (kind, path) pairs for every canonical image ID that exists on disk."""
    id_set = set(expected_ids)
    if not id_set:
        # Fallback to glob-based discovery when inventory is unavailable
        return list_images(exports, project)

    results: list[tuple[str, Path]] = []
    seen_files: set[str] = set()

    for kind_pattern in [("schematic", f"{project}-img-sch-p*.png"), ("layout", f"{project}-img-layout-p*.png")]:
        kind, pattern = kind_pattern
        for p in sorted(exports.glob(pattern)):
            if p.name in id_set and str(p) not in seen_files:
                seen_files.add(str(p))
                results.append((kind, p))

    return results


def _as_bool(value: Any) -> bool | None:
    if value is True or value is False:
        return value
    return None


def _list_or_empty(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


SELF_ATTESTATION_VALIDATION_ERRORS = {
    "response.visual_review_performed is not true",
    "response.confirmation_no_pixel_quantitative_claims is not true",
}
PIXEL_QUANTITATIVE_PATTERNS = [
    re.compile(
        r"\b(?:trace\s+widths?|clearance|creepage|pad\s+(?:sizes?|diameters?)|hole\s+sizes?|via\s+(?:diameters?|sizes?)|"
        r"component\s+spacing|spacing|board\s+(?:width|height|dimensions?)|layer\s+thickness)\b"
        r"[^.]{0,80}\b\d+(?:\.\d+)?\s*(?:mil|mils|mm|um|µm|micron|microns|inch|inches|in)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b\d+(?:\.\d+)?\s*(?:mil|mils|mm|um|µm|micron|microns|inch|inches|in)\b"
        r"[^.]{0,80}\b(?:trace\s+widths?|clearance|creepage|pad\s+(?:sizes?|diameters?)|hole\s+sizes?|via\s+(?:diameters?|sizes?)|"
        r"component\s+spacing|spacing|board\s+(?:width|height|dimensions?)|layer\s+thickness)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:measured|estimated|inferred|calculated)\b[^.]{0,80}\b(?:from|using|by)\b[^.]{0,40}\bpixels?\b",
        re.IGNORECASE,
    ),
]
PIXEL_MEASUREMENT_LIMITATION_PATTERNS = [
    re.compile(
        r"\b(?:trace\s+widths?|clearance|creepage|pad\s+(?:sizes?|diameters?)|hole\s+sizes?|via\s+(?:diameters?|sizes?)|"
        r"component\s+spacing|spacing|board\s+(?:width|height|dimensions?)|layer\s+thickness)\b"
        r"[^.]{0,100}\b(?:cannot|can't|can\s+not|not|without|unable|do\s+not)\b"
        r"[^.]{0,100}\b(?:infer(?:red)?|determine(?:d)?|verif(?:y|iable)|measure(?:d)?|trust(?:ed)?|derive(?:d)?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:cannot|can't|can\s+not|not|without|unable|do\s+not)\b"
        r"[^.]{0,100}\b(?:infer(?:red)?|determine(?:d)?|verif(?:y|iable)|measure(?:d)?|trust(?:ed)?|derive(?:d)?)\b"
        r"[^.]{0,100}\b(?:trace\s+widths?|clearance|creepage|pad\s+(?:sizes?|diameters?)|hole\s+sizes?|via\s+(?:diameters?|sizes?)|"
        r"component\s+spacing|spacing|board\s+(?:width|height|dimensions?)|layer\s+thickness)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bno\s+pixel-derived\s+quantitative\s+claim\s+is\s+made\b", re.IGNORECASE),
]


def _response_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        strings: list[str] = []
        for nested in value.values():
            strings.extend(_response_strings(nested))
        return strings
    if isinstance(value, list):
        strings: list[str] = []
        for nested in value:
            strings.extend(_response_strings(nested))
        return strings
    return []


def pixel_quantitative_claim_scan(response: dict[str, Any]) -> dict[str, list[str]]:
    forbidden: list[str] = []
    allowed_limitations: list[str] = []
    for text in _response_strings(response):
        normalized = re.sub(r"\s+", " ", text.strip())
        if not normalized:
            continue
        is_limitation = any(pattern.search(normalized) for pattern in PIXEL_MEASUREMENT_LIMITATION_PATTERNS)
        is_measurement_claim = any(pattern.search(normalized) for pattern in PIXEL_QUANTITATIVE_PATTERNS)
        if is_measurement_claim and not is_limitation:
            forbidden.append(normalized)
        elif is_limitation:
            allowed_limitations.append(normalized)
    return {
        "forbidden_pixel_measurement_claims": unique_strings(forbidden),
        "allowed_pixel_measurement_limitations": unique_strings(allowed_limitations),
    }


def unsupported_pixel_quantitative_claims(response: dict[str, Any]) -> list[str]:
    return pixel_quantitative_claim_scan(response)["forbidden_pixel_measurement_claims"]


def canonical_validation_errors(values: list[Any]) -> list[str]:
    return [
        str(value)
        for value in values
        if str(value) and str(value) not in SELF_ATTESTATION_VALIDATION_ERRORS
    ]


def review_row_validation_errors(row: dict[str, Any], expected_image_ids: set[str] | None = None) -> tuple[list[str], list[str]]:
    required = [
        "image_id",
        "file",
        "model",
        "raw_response_path",
        "retry_count",
        "repair_applied",
        "parse_status",
        "validation_status",
        "page_actually_opened",
        "actual_image_review_performed",
        "validation_errors",
        "validation_missing_fields",
        "errors",
        "warnings",
        "response",
    ]
    missing = [
        field
        for field in required
        if field not in row or row.get(field) is None or (field in {"image_id", "file", "model", "raw_response_path"} and row.get(field) == "")
    ]
    errors: list[str] = []

    if row.get("page_actually_opened") is not True:
        errors.append("page_actually_opened is not true")
    if row.get("actual_image_review_performed") is not True:
        errors.append("actual_image_review_performed is not true")
    if row.get("parse_status") not in {"parsed", "repaired"}:
        errors.append("parse_status is not parsed or repaired")
    if row.get("validation_status") != "passed":
        errors.append("validation_status is not passed")

    response = row.get("response")
    if not isinstance(response, dict):
        errors.append("response is not an object")
    else:
        for claim in unsupported_pixel_quantitative_claims(response):
            errors.append(f"unsupported pixel-derived quantitative claim detected: {claim}")

    image_id = str(row.get("image_id") or "")
    if expected_image_ids is not None and image_id not in expected_image_ids:
        errors.append("image_id does not match expected inventory image ID")

    return errors, missing


def normalize_review_observation(
    *,
    image_path: str | Path | None = None,
    kind: str | None = None,
    model: str | None = None,
    response: dict[str, Any] | None = None,
    raw_response_path: str | None = None,
    raw_response_paths: list[str] | None = None,
    retry_count: int | None = None,
    repair_applied: bool = False,
    parse_status: str | None = None,
    runtime_review_performed: bool = False,
    existing: dict[str, Any] | None = None,
    expected_image_ids: set[str] | None = None,
    validation_errors: list[str] | None = None,
) -> dict[str, Any]:
    """Return the stable Phase 13 persisted review-row schema."""
    existing = existing or {}
    response = response if isinstance(response, dict) else existing.get("response")
    if not isinstance(response, dict):
        response = {}

    file_value = str(
        image_path
        or existing.get("file")
        or existing.get("source_file")
        or existing.get("image_path")
        or response.get("source_file")
        or response.get("file")
        or ""
    )
    image_id = str(existing.get("image_id") or response.get("image_id") or Path(file_value).name or "")
    page_type = str(existing.get("page_type") or existing.get("kind") or response.get("page_type") or kind or "unknown")
    row_kind = str(kind or existing.get("kind") or page_type or "unknown")

    page_number = existing.get("page_number", response.get("page_number"))
    if not isinstance(page_number, int):
        page_number = page_number_from_path(file_value or image_id)

    paths = raw_response_paths or existing.get("raw_response_paths")
    if not isinstance(paths, list):
        paths = []
    paths = [str(path) for path in paths if path]
    raw_path = str(raw_response_path or existing.get("raw_response_path") or (paths[-1] if paths else ""))
    if raw_path and raw_path not in paths:
        paths.append(raw_path)

    retry_value = retry_count
    if retry_value is None:
        retry_value = existing.get("retry_count", existing.get("retry_count_used"))
    if not isinstance(retry_value, int):
        retry_value = 0

    repair_value = bool(existing.get("repair_applied", False) or repair_applied)
    opened = _as_bool(existing.get("page_actually_opened"))
    if opened is None:
        opened = _as_bool(response.get("page_actually_opened"))
    performed = _as_bool(existing.get("actual_image_review_performed"))
    if performed is None:
        performed = _as_bool(response.get("actual_image_review_performed"))
    if runtime_review_performed:
        opened = True
        performed = True
    elif raw_path and response and (opened is None or performed is None):
        if response.get("visual_review_performed") is True:
            opened = True if opened is None else opened
            performed = True if performed is None else performed
            repair_value = True

    if opened is None:
        opened = False
    if performed is None:
        performed = False

    status = parse_status or existing.get("parse_status")
    if status not in {"parsed", "repaired", "failed"}:
        status = "parsed" if response else "failed"
    if repair_value and status == "parsed":
        status = "repaired"

    errors = _list_or_empty(existing.get("errors")) + _list_or_empty(response.get("errors"))
    warnings = _list_or_empty(existing.get("warnings")) + _list_or_empty(response.get("warnings"))
    provided_validation_errors = canonical_validation_errors(
        _list_or_empty(existing.get("validation_errors")) + _list_or_empty(validation_errors)
    )
    provided_missing = _list_or_empty(existing.get("validation_missing_fields"))

    row = {
        "image_id": image_id,
        "file": file_value,
        "source_file": str(existing.get("source_file") or response.get("source_file") or file_value),
        "page_number": page_number,
        "page_type": page_type,
        "kind": row_kind,
        "model": str(model or existing.get("model") or ""),
        "raw_response_path": raw_path,
        "raw_response_paths": paths,
        "retry_count": retry_value,
        "retry_count_used": retry_value,
        "repair_applied": repair_value,
        "parse_status": status,
        "validation_status": "failed" if status == "failed" else "passed",
        "page_actually_opened": opened,
        "actual_image_review_performed": performed,
        "validation_errors": [],
        "validation_missing_fields": [],
        "errors": errors,
        "warnings": warnings,
        "response": response,
    }
    if isinstance(existing.get("parse_errors"), list):
        row["parse_errors"] = existing["parse_errors"]

    if row["image_id"] and not response.get("image_id"):
        response["image_id"] = row["image_id"]
    if row["source_file"] and not response.get("source_file"):
        response["source_file"] = row["source_file"]
    if row["page_number"] is not None and not isinstance(response.get("page_number"), int):
        response["page_number"] = row["page_number"]
    if row["page_type"] and not response.get("page_type"):
        response["page_type"] = row["page_type"]
    if not isinstance(response.get("errors"), list):
        response["errors"] = []
    if not isinstance(response.get("warnings"), list):
        response["warnings"] = []

    row_errors, missing = review_row_validation_errors(row, expected_image_ids)
    row["validation_errors"] = unique_strings([str(err) for err in provided_validation_errors + row_errors])
    row["validation_missing_fields"] = unique_strings([str(field) for field in provided_missing + missing])
    if row["validation_errors"] or row["validation_missing_fields"] or status == "failed":
        row["validation_status"] = "failed"
        if status == "failed":
            row["parse_status"] = "failed"
    else:
        row["validation_status"] = "passed"

    return row


def observation_successful(observation: dict[str, Any], expected_image_ids: set[str] | None = None) -> bool:
    """Check that the persisted review record is valid."""
    if not isinstance(observation, dict):
        return False
    if observation.get("page_actually_opened") is not True:
        return False
    if observation.get("actual_image_review_performed") is not True:
        return False
    if observation.get("parse_status") not in {"parsed", "repaired"}:
        return False
    if observation.get("validation_status") != "passed":
        return False
    errors, missing = review_row_validation_errors(observation, expected_image_ids)
    return not errors and not missing


def as_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str):
            text = item.strip()
            if text:
                out.append(text)
    return out


def unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def normalized_claim_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower().rstrip(".:;!"))


def is_generic_vision_claim(value: str) -> bool:
    text = normalized_claim_text(value)
    return text in GENERIC_VISION_CLAIMS


def is_final_style_claim(value: str) -> bool:
    return any(pattern.search(value) for pattern in FINAL_STYLE_CLAIM_PATTERNS)


def filter_annotation_claims(values: list[str]) -> tuple[list[str], list[str]]:
    kept: list[str] = []
    rejected: list[str] = []
    for value in unique_strings(values):
        if is_generic_vision_claim(value) or is_final_style_claim(value):
            rejected.append(value)
        else:
            kept.append(value)
    return kept, rejected


def downgrade_low_confidence_annotation(annotation: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """If confidence is 0.0, reject specific engineering claims without concrete evidence."""
    warnings_list: list[str] = []
    if annotation.get("confidence", 1.0) == 0.0:
        # Move specific concerns to rejected unless they have concrete refdes/net/page refs
        specific_concerns = annotation.get("engineering_concern_candidates", [])
        valid_concerns: list[str] = []
        for concern in specific_concerns:
            text_lower = normalized_claim_text(concern)
            # Accept only if it contains concrete evidence references (refdes, nets, page numbers)
            has_evidence = any(
                kw in text_lower
                for kw in ["u[0-9]", "r[0-9]", "c[0-9]", "l[0-9]", "net", "page", "pin", "v[0-9]", "j[0-9]"]
            ) or re.search(r"[A-Z][0-9]+", concern) is not None
            if has_evidence:
                valid_concerns.append(concern)
            else:
                warnings_list.append(f"rejected_generic_concern: {concern}")

        annotation["engineering_concern_candidates"] = valid_concerns

        # Reject specific calculation_needed without evidence
        calculations = annotation.get("calculation_needed", [])
        valid_calcs: list[str] = []
        for calc in calculations:
            text_lower = normalized_claim_text(calc)
            has_evidence = re.search(r"[A-Z][0-9]+", calc) is not None
            if has_evidence:
                valid_calcs.append(calc)
            else:
                warnings_list.append(f"rejected_generic_calculation: {calc}")

        annotation["calculation_needed"] = valid_calcs

        # Reject specific observed_circuits without concrete refdes/net/page evidence
        circuits = annotation.get("observed_circuits", [])
        generic_circuit_terms = [
            "power distribution network",
            "signal routing paths",
            "logic circuitry",
            "voltage regulation sections",
        ]
        valid_circuits: list[str] = []
        for circ in circuits:
            text_lower = normalized_claim_text(circ)
            if any(term in text_lower for term in generic_circuit_terms):
                # Accept only if accompanied by concrete refdes/net/page evidence.
                # A generic circuit term alone is never sufficient evidence.
                has_refdes = bool(re.search(r"[A-Z][0-9]+", circ))
                # Require concrete net references: "net <NAME>" where NAME starts with letter/underscore,
                # or explicit "label <NAME>", or a signal reference that looks like an actual signal name
                # (starts with uppercase letter, not just any word).
                has_nets = bool(
                    re.search(r"\bnet\s+[A-Za-z_]\w*", text_lower)
                    or re.search(r"label\s+[A-Za-z_]\w*", text_lower)
                    or re.search(r"signal\s+[A-Z][A-Za-z0-9_]*", circ)
                )
                has_page = "page" in text_lower
                if not (has_refdes or has_nets or has_page):
                    warnings_list.append(f"rejected_generic_circuit: {circ}")
                    continue
            valid_circuits.append(circ)

        annotation["observed_circuits"] = valid_circuits

    return annotation, warnings_list


def reject_final_style_claims(annotation: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Reject final-style claims that should not pass as engineering annotations."""
    warnings_list: list[str] = []
    for field in ["engineering_concern_candidates", "blocked_verification_candidates"]:
        values = annotation.get(field, [])
        valid_values: list[str] = []
        for value in values:
            if is_final_style_claim(value):
                warnings_list.append(f"rejected_final_style_claim: {value}")
            else:
                valid_values.append(value)
        annotation[field] = valid_values

    # Reject "routing verified", "connectivity verified", etc. from any string field
    for field in ["observed_circuits", "component_role_observations"]:
        values = annotation.get(field, [])
        valid_values: list[str] = []
        for value in values:
            if is_generic_vision_claim(value):
                warnings_list.append(f"rejected_generic_claim: {value}")
            else:
                valid_values.append(value)
        annotation[field] = valid_values

    return annotation, warnings_list


def create_minimal_annotation(image_id: str, source_file: str, page_number: int | None, page_type: str) -> dict[str, Any]:
    """Create a minimal annotation record when rich content is unavailable."""
    return {
        "image_id": image_id,
        "source_file": source_file,
        "page_number": page_number,
        "page_type": page_type if page_type in {"schematic", "layout", "gerber"} else "unknown",
        "observed_circuits": [],
        "observed_refdes": [],
        "observed_nets": [],
        "component_role_observations": [],
        "engineering_concern_candidates": [],
        "blocked_verification_candidates": ["Engineering annotation unavailable or incomplete for this image"],
        "datasheet_check_needed": [],
        "calculation_needed": [],
        "human_review_questions": [],
        "not_verifiable_from_image": ["No concrete engineering annotation was extracted from this image"],
        "confidence": 0.0,
        "validation_status": "repaired_minimal",
        "generic_claims_rejected": [],
        "evidence_references": [
            {
                "source_file": source_file,
                "image_id": image_id,
                "page_number": page_number,
            }
        ],
    }


def page_number_from_path(path_text: str) -> int | None:
    match = re.search(r"-p(\d+)\.png$", Path(path_text).name)
    if not match:
        return None
    return int(match.group(1))


def annotation_for_observation(observation: dict[str, Any]) -> dict[str, Any]:
    response = observation.get("response")
    if not isinstance(response, dict):
        response = {}

    source_file = str(observation.get("file") or response.get("source_file") or "")
    kind = str(observation.get("kind") or response.get("page_type") or "unknown")
    page_type = str(response.get("page_type") or kind or "unknown")
    page_number = response.get("page_number")
    if not isinstance(page_number, int):
        page_number = page_number_from_path(source_file)

    observed_circuits = unique_strings(
        as_string_list(response.get("observed_circuits"))
        + as_string_list(response.get("visible_circuit_blocks"))
        + as_string_list(response.get("visible_board_features"))
    )
    observed_refdes = unique_strings(
        as_string_list(response.get("observed_refdes"))
        + as_string_list(response.get("visible_components_or_refdes"))
    )
    observed_nets = unique_strings(
        as_string_list(response.get("observed_nets"))
        + as_string_list(response.get("visible_net_labels_or_signal_names"))
        + as_string_list(response.get("visible_text_or_labels"))
    )
    role_observations = unique_strings(
        as_string_list(response.get("component_role_observations"))
        + as_string_list(response.get("likely_circuit_purpose"))
    )

    concern_values = (
        as_string_list(response.get("engineering_concern_candidates"))
        + as_string_list(response.get("possible_concerns_or_followups"))
    )
    blocked_values = as_string_list(response.get("blocked_verification_candidates"))
    datasheet_values = as_string_list(response.get("datasheet_check_needed"))
    calculation_values = (
        as_string_list(response.get("calculation_needed"))
        + as_string_list(response.get("possible_electrical_calculations_from_visible_values"))
    )
    question_values = as_string_list(response.get("human_review_questions"))

    engineering_concerns, rejected_concerns = filter_annotation_claims(concern_values)
    blocked_verifications, rejected_blocked = filter_annotation_claims(blocked_values)
    datasheet_checks, rejected_datasheets = filter_annotation_claims(datasheet_values)
    calculations, rejected_calculations = filter_annotation_claims(calculation_values)
    questions, rejected_questions = filter_annotation_claims(question_values)
    generic_from_description = [
        value
        for value in as_string_list(response.get("brief_description"))
        if is_generic_vision_claim(value) or is_final_style_claim(value)
    ]

    confidence = response.get("confidence")
    if not isinstance(confidence, (int, float)):
        confidence = 0.0
    confidence = max(0.0, min(1.0, float(confidence)))

    # Apply low-confidence downgrade: reject specific claims when confidence is 0.0
    annotation_result = {
        "image_id": str(response.get("image_id") or Path(source_file).name),
        "source_file": source_file,
        "page_number": page_number,
        "page_type": page_type if page_type in {"schematic", "layout", "gerber", "unknown"} else kind,
        "observed_circuits": observed_circuits,
        "observed_refdes": observed_refdes,
        "observed_nets": observed_nets,
        "component_role_observations": role_observations,
        "engineering_concern_candidates": engineering_concerns,
        "blocked_verification_candidates": blocked_verifications,
        "datasheet_check_needed": datasheet_checks,
        "calculation_needed": calculations,
        "human_review_questions": questions,
        "not_verifiable_from_image": unique_strings(
            as_string_list(response.get("not_verifiable_from_image"))
            + as_string_list(response.get("limitations"))
        ),
        "generic_claims_rejected": unique_strings(
            rejected_concerns
            + rejected_blocked
            + rejected_datasheets
            + rejected_calculations
            + rejected_questions
            + generic_from_description
        ),
        "confidence": confidence,
        "evidence_references": [
            {
                "source_file": source_file,
                "image_id": str(response.get("image_id") or Path(source_file).name),
                "page_number": page_number,
            }
        ],
    }

    # Downgrade low-confidence annotations: reject specific claims without concrete evidence
    annotation_result, confidence_warnings = downgrade_low_confidence_annotation(annotation_result)
    # Reject final-style and generic vision claims from remaining fields
    annotation_result, style_warnings = reject_final_style_claims(annotation_result)

    all_warnings = list(confidence_warnings) + list(style_warnings)
    if all_warnings:
        annotation_result["validation_warnings"] = unique_strings(all_warnings)
        annotation_result["validation_status"] = "filtered"

    return annotation_result


def annotations_artifact_for(
    *,
    project: str,
    assessment_profile: str,
    base_artifact: dict[str, Any],
    expected_image_ids: list[str] | None = None,
) -> dict[str, Any]:
    observations = [
        normalize_review_observation(existing=obs, expected_image_ids=set(expected_image_ids) if expected_image_ids else None)
        for obs in base_artifact.get("per_page_vision_observations", [])
        if isinstance(obs, dict)
    ]
    expected_count = base_artifact.get("expected_image_count", 0)

    # Build annotations from successful observations
    annotations_map: dict[str, dict[str, Any]] = {}
    for observation in observations:
        if not isinstance(observation, dict) or not observation_successful(observation):
            continue
        ann = annotation_for_observation(observation)
        img_id = ann.get("image_id", "")
        if img_id:
            annotations_map[img_id] = ann

    # In balanced/engineering mode, ensure one annotation per expected image
    if assessment_profile in ("balanced", "engineering"):
        missing_ids: list[str] = []
        for obs in observations:
            if not isinstance(obs, dict):
                continue
            resp = obs.get("response")
            if not isinstance(resp, dict):
                continue
            img_id = str(resp.get("image_id", "")) or Path(str(obs.get("file", ""))).name
            if img_id and img_id not in annotations_map:
                missing_ids.append(img_id)

        # Also check against canonical expected image IDs if provided
        if expected_image_ids:
            for eid in expected_image_ids:
                if eid not in annotations_map and eid not in missing_ids:
                    missing_ids.append(eid)

        for img_id in missing_ids:
            source_file = ""
            page_number: int | None = None
            page_type = "unknown"
            # Try to find the observation to extract metadata
            for obs2 in observations:
                if not isinstance(obs2, dict):
                    continue
                resp2 = obs2.get("response")
                if not isinstance(resp2, dict):
                    continue
                img_id2 = str(resp2.get("image_id", "")) or Path(str(obs2.get("file", ""))).name
                if img_id2 == img_id:
                    source_file = str(obs2.get("file", "") or resp2.get("source_file", ""))
                    page_number = resp2.get("page_number")
                    if not isinstance(page_number, int):
                        page_number = page_number_from_path(source_file)
                    page_type = str(resp2.get("page_type", "unknown"))
                    break

            minimal = create_minimal_annotation(img_id, source_file or img_id, page_number, page_type)
            annotations_map[img_id] = minimal

    # Sort annotations by image_id for deterministic output
    sorted_ids = sorted(annotations_map.keys())
    annotations = [annotations_map[iid] for iid in sorted_ids]

    generic_claim_count = sum(len(row.get("generic_claims_rejected", [])) for row in annotations)
    blockers: list[str] = []
    if base_artifact.get("overall_pass") is not True:
        blockers.append("image evidence review did not pass")
    if expected_count and len(annotations) != expected_count:
        missing_from_annotations = [iid for iid in sorted_ids if annotations_map[iid].get("validation_status") == "repaired_minimal"]
        if missing_from_annotations:
            blockers.append(f"annotation count does not match reviewed image count ({len(annotations)}/{expected_count}); {len(missing_from_annotations)} minimal repaired records written")
        else:
            blockers.append(f"annotation count does not match reviewed image count ({len(annotations)}/{expected_count})")

    warnings: list[str] = []
    if generic_claim_count:
        warnings.append("generic or final-style vision claims were rejected from engineering annotations")

    # Check for minimal repaired records (engineering/balanced mode indicator)
    minimal_count = sum(1 for a in annotations if a.get("validation_status") == "repaired_minimal")
    if minimal_count:
        warnings.append(f"{minimal_count} annotation(s) have minimal/repaired content; not verified engineering findings")

    return {
        "project": project,
        "phase": 13,
        "assessment_profile": assessment_profile,
        "vision_temperature": base_artifact.get("vision_temperature"),
        "overall_pass": not blockers,
        "annotation_count": len(annotations),
        "expected_image_count": expected_count,
        "generic_claim_count": generic_claim_count,
        "minimal_repaired_count": minimal_count,
        "annotations": annotations,
        "blockers": blockers,
        "warnings": warnings,
    }


def write_annotations_artifact(
    *,
    annotations_out: Path,
    project: str,
    assessment_profile: str,
    base_artifact: dict[str, Any],
) -> dict[str, Any]:
    artifact = annotations_artifact_for(
        project=project,
        assessment_profile=assessment_profile,
        base_artifact=base_artifact,
    )
    annotations_out.parent.mkdir(parents=True, exist_ok=True)
    annotations_out.write_text(json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8")
    return artifact


def load_previous_artifact(out: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not out.exists():
        return [], []

    try:
        data = json.loads(out.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"WARNING: could not load existing artifact {out}: {exc}", file=sys.stderr)
        return [], []

    observations = data.get("per_page_vision_observations", [])
    errors = data.get("errors", [])
    if not isinstance(observations, list):
        observations = []
    if not isinstance(errors, list):
        errors = []

    return (
        [obs for obs in observations if isinstance(obs, dict)],
        [err for err in errors if isinstance(err, dict)],
    )


def safe_stem(path: Path) -> str:
    return "".join(char if char.isalnum() or char in ("-", "_", ".") else "_" for char in path.name)


def raw_response_path(raw_out_dir: Path, image_path: Path, attempt: int) -> Path:
    return raw_out_dir / f"{safe_stem(image_path)}.attempt{attempt}.txt"


def write_raw_response(raw_out_dir: Path, image_path: Path, attempt: int, content: str) -> Path:
    path = raw_response_path(raw_out_dir, image_path, attempt)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def review_image_with_retries(
    *,
    base_url: str,
    api_key: str,
    model: str,
    image_path: Path,
    prompt: str,
    timeout: int,
    max_tokens: int,
    temperature: float,
    retries: int,
    raw_out_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    parse_errors: list[dict[str, Any]] = []
    raw_paths: list[str] = []
    attempts = max(0, retries) + 1

    for attempt in range(1, attempts + 1):
        attempt_prompt = prompt if attempt == 1 else f"{STRICT_JSON_ONLY_PROMPT}\n\n{prompt}"
        content = post_chat_completion(
            base_url=base_url,
            api_key=api_key,
            model=model,
            image_path=image_path,
            prompt=attempt_prompt,
            timeout=timeout,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        raw_path = write_raw_response(raw_out_dir, image_path, attempt, content)
        raw_paths.append(str(raw_path))
        try:
            parsed = extract_json(content)
            diagnostics = {
                "raw_response_path": str(raw_path),
                "raw_response_paths": raw_paths,
                "parse_errors": parse_errors,
                "retry_count_used": attempt - 1,
            }
            return parsed, diagnostics
        except Exception as exc:
            parse_errors.append(
                {
                    "attempt": attempt,
                    "raw_response_path": str(raw_path),
                    "error": str(exc),
                }
            )

    raise ValueError(
        json.dumps(
            {
                "message": "model did not return valid JSON after retries",
                "retry_count_used": attempts - 1,
                "raw_response_paths": raw_paths,
                "parse_errors": parse_errors,
            },
            ensure_ascii=False,
        )
    )


def artifact_for(
    *,
    project: str,
    base_url: str,
    model: str,
    expected: int,
    observations: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    temperature: float = DEFAULT_VISION_TEMPERATURE,
    expected_image_ids: list[str] | None = None,
) -> dict[str, Any]:
    expected_id_set = set(expected_image_ids or [])
    if not expected_id_set:
        expected_id_set = None

    by_file: dict[str, dict[str, Any]] = {}
    ordered_files: list[str] = []
    for observation in observations:
        normalized = normalize_review_observation(existing=observation, model=model, expected_image_ids=expected_id_set)
        file_name = str(normalized.get("file") or "")
        if not file_name:
            continue
        if file_name not in by_file:
            ordered_files.append(file_name)
        by_file[file_name] = normalized

    deduped_observations = [by_file[file_name] for file_name in ordered_files]

    # Count pages_actually_opened from explicit per-record fields
    pages_actually_opened_count = 0
    reviewed_image_count = 0
    valid_image_ids: list[str] = []
    invalid_image_ids: list[str] = []
    invalid_records: list[dict[str, Any]] = []

    for obs in deduped_observations:
        img_id = str(obs.get("image_id") or Path(str(obs.get("file", ""))).name)
        if obs.get("page_actually_opened") is True:
            pages_actually_opened_count += 1
        if observation_successful(obs, expected_id_set):
            reviewed_image_count += 1
            valid_image_ids.append(img_id)
        else:
            invalid_image_ids.append(img_id)
            missing_fields = [str(field) for field in obs.get("validation_missing_fields", []) if field]
            reasons = [str(reason) for reason in obs.get("validation_errors", []) if reason]
            if not reasons and obs.get("validation_status") != "passed":
                reasons.append(f"validation_status={obs.get('validation_status')}")
            invalid_records.append(
                {
                    "image_id": img_id,
                    "reason": "; ".join(reasons) if reasons else "record failed top-level validation",
                    "missing_fields": missing_fields,
                    "raw_response_path": str(obs.get("raw_response_path") or ""),
                }
            )

    observed_ids = {str(obs.get("image_id") or Path(str(obs.get("file", ""))).name) for obs in deduped_observations}
    if expected_image_ids:
        missing_image_ids = [image_id for image_id in expected_image_ids if image_id not in observed_ids]
    else:
        missing_count = max(0, expected - len(deduped_observations))
        missing_image_ids = [f"<missing-image-{idx}>" for idx in range(1, missing_count + 1)]
    failed_or_missing_ids = invalid_image_ids + missing_image_ids

    all_reviewed = expected > 0 and reviewed_image_count == expected and not errors
    all_opened = expected > 0 and pages_actually_opened_count == expected
    pixel_claim_errors = [
        error
        for obs in deduped_observations
        for error in obs.get("validation_errors", [])
        if isinstance(error, str) and error.startswith("unsupported pixel-derived quantitative claim detected:")
    ]
    all_no_pixel_geometry = len(deduped_observations) == reviewed_image_count and not pixel_claim_errors

    # phase_13_completed is true only when every expected image has a valid review record
    phase_13_completed = bool(all_reviewed and all_opened and all_no_pixel_geometry)

    # overall_pass requires: inventory exists, required fields present, counts match, errors empty/nonfatal
    blockers: list[str] = []
    if not all_reviewed:
        blockers.append(f"reviewed_image_count ({reviewed_image_count}) != expected_image_count ({expected})")
    if not all_opened and all_reviewed:
        blockers.append(f"pages_actually_opened_count ({pages_actually_opened_count}) != reviewed_image_count ({reviewed_image_count}); {len(failed_or_missing_ids)} image(s) did not have page_actually_opened=true")
    elif not all_opened:
        blockers.append(f"pages_actually_opened_count ({pages_actually_opened_count}) != expected_image_count ({expected})")
    if errors and any(e.get("severity", "info") in ("error", "fatal") for e in errors):
        blockers.append(f"{len([e for e in errors if e.get('severity', 'info') in ('error', 'fatal')])} error(s) present")

    overall_pass = phase_13_completed and not blockers

    return {
        "phase": 13,
        "phase_name": "Review Image Evidence FULL",
        "project": project,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "vision_base_url": base_url,
        "vision_model": model,
        "vision_temperature": temperature,
        "expected_image_count": expected,
        "reviewed_image_count": reviewed_image_count,
        "pages_actually_opened_count": pages_actually_opened_count,
        "valid_image_ids": valid_image_ids,
        "invalid_image_ids": invalid_image_ids,
        "missing_image_ids": missing_image_ids,
        "invalid_records": invalid_records,
        "failed_or_missing_ids": failed_or_missing_ids,
        "vision_review_performed": all_reviewed,
        "metadata_only_review": False,
        "actual_multimodal_endpoint_used": True,
        "per_page_vision_observations": deduped_observations,
        "errors": errors,
        "confirmation_no_pixel_quantitative_claims": all_no_pixel_geometry,
        "allowed_quantitative_scope": (
            "Electrical calculations may be derived from visibly readable schematic values. "
            "Physical/layout geometry measurements must not be derived from raster pixels unless calibrated."
        ),
        "overall_pass": overall_pass,
        "phase_13_completed": phase_13_completed,
    }


def write_artifacts(
    *,
    out: Path,
    project: str,
    base_url: str,
    model: str,
    expected: int,
    observations: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    temperature: float = DEFAULT_VISION_TEMPERATURE,
    expected_image_ids: list[str] | None = None,
) -> dict[str, Any]:
    artifact = artifact_for(
        project=project,
        base_url=base_url,
        model=model,
        expected=expected,
        observations=observations,
        errors=errors,
        temperature=temperature,
        expected_image_ids=expected_image_ids,
    )

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8")

    validation_artifact = {
        "phase": 13,
        "inventory_exists": True,
        "required_fields_present": True,
        "expected_image_count": artifact["expected_image_count"],
        "reviewed_image_count": artifact["reviewed_image_count"],
        "pages_actually_opened_count": artifact["pages_actually_opened_count"],
        "valid_image_ids": artifact.get("valid_image_ids", []),
        "invalid_image_ids": artifact.get("invalid_image_ids", []),
        "missing_image_ids": artifact.get("missing_image_ids", []),
        "invalid_records": artifact.get("invalid_records", []),
        "failed_or_missing_ids": artifact.get("failed_or_missing_ids", []),
        "phase_13_completed": artifact["phase_13_completed"],
        "overall_pass": artifact["overall_pass"],
    }

    val_out = out.parent / f"{project}-image-evidence-review-validation.json"
    val_out.write_text(json.dumps(validation_artifact, indent=2, ensure_ascii=False), encoding="utf-8")
    return artifact


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="example")
    ap.add_argument("--exports", default="exports")
    ap.add_argument("--out", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--max-tokens", type=int, default=1200)
    ap.add_argument("--sleep", type=float, default=0.2)
    ap.add_argument("--retries", type=int, default=2)
    ap.add_argument("--raw-out-dir", default=None)
    ap.add_argument("--annotations-out", default=None)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)

    exports = Path(args.exports)
    out = Path(args.out) if args.out else exports / f"{args.project}-image-evidence-review.json"
    raw_out_dir = Path(args.raw_out_dir) if args.raw_out_dir else exports / "vision_raw_responses" / args.project
    annotations_out = (
        Path(args.annotations_out)
        if args.annotations_out
        else out.parent / f"{args.project}-vision-engineering-annotations.json"
    )
    assessment_profile = assessment_profile_from_env()

    base_url = env("VISION_BASE_URL", env("LLM_BASE_URL"))
    model = env("VISION_MODEL", env("LLM_MODEL"))
    api_key = env("VISION_API_KEY", env("LLM_API_KEY", "local"))
    temperature = vision_temperature_from_env()

    if not base_url or not model:
        print("ERROR: set VISION_BASE_URL and VISION_MODEL, or LLM_BASE_URL and LLM_MODEL", file=sys.stderr)
        return 2

    print(f"VISION_TEMPERATURE={temperature:g}")

    images = list_images(exports, args.project)
    if args.limit and args.limit > 0:
        images = images[: args.limit]

    # Determine expected count from canonical inventory when available
    canonical_ids = expected_image_ids_from_inventory(exports, args.project)
    images_for_processing = expected_image_paths(exports, args.project, canonical_ids) if canonical_ids else images
    effective_expected = len(images_for_processing) or len(images)
    target_files = {str(path) for _, path in (images_for_processing or images)}
    target_order = [str(path) for _, path in (images_for_processing or images)]
    observations: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    if args.resume and not args.force:
        previous_observations, previous_errors = load_previous_artifact(out)
        expected_id_set = set(canonical_ids) if canonical_ids else None
        observations = []
        for obs in previous_observations:
            normalized = normalize_review_observation(existing=obs, model=model, expected_image_ids=expected_id_set)
            if str(normalized.get("file") or "") in target_files and observation_successful(normalized, expected_id_set):
                observations.append(normalized)
        completed = {str(obs.get("file") or "") for obs in observations}
        errors = [
            err
            for err in previous_errors
            if str(err.get("file") or "") in target_files and str(err.get("file") or "") not in completed
        ]
        print(f"Resume loaded: {len(observations)} successful observations, {len(errors)} prior errors")
    elif args.force:
        print("Force enabled: ignoring previous observations")

    by_file = {str(obs.get("file") or ""): obs for obs in observations}
    errors = [err for err in errors if str(err.get("file") or "") not in by_file]

    artifact = write_artifacts(
        out=out,
        project=args.project,
        base_url=base_url,
        model=model,
        expected=effective_expected,
        observations=observations,
        errors=errors,
        temperature=temperature,
        expected_image_ids=canonical_ids or None,
    )

    # Build set of images that have both valid review AND (in balanced/engineering mode) valid annotation
    completed_image_ids: set[str] = set()
    for obs in observations:
        if not isinstance(obs, dict):
            continue
        img_id = str(obs.get("image_id") or Path(str(obs.get("file", ""))).name)
        if img_id and observation_successful(obs, set(canonical_ids) if canonical_ids else None):
            completed_image_ids.add(img_id)

    # In balanced/engineering mode, also check annotation validity
    annotations_validated: set[str] = set()
    if assessment_profile in ("balanced", "engineering"):
        ann_artifact_path = annotations_out if args.annotations_out else out.parent / f"{args.project}-vision-engineering-annotations.json"
        if ann_artifact_path.exists():
            try:
                ann_data = json.loads(ann_artifact_path.read_text(encoding="utf-8"))
                for ann in ann_data.get("annotations", []):
                    a_id = ann.get("image_id", "")
                    status = ann.get("validation_status", "valid")
                    if a_id and status != "repaired_minimal":
                        annotations_validated.add(a_id)
            except Exception:
                pass

    for kind, path in images_for_processing or images:
        img_id = Path(path).name
        path_key = str(path)

        # Resume: skip only if both review and annotation are valid (completeness-aware)
        if not args.force and img_id in completed_image_ids:
            if assessment_profile in ("balanced", "engineering"):
                if img_id in annotations_validated:
                    print(f"SKIP existing vision review + annotation: {path.name}")
                    continue
                else:
                    print(f"WARN missing annotation for {path.name}; will retry or repair")
            else:
                print(f"SKIP existing vision review: {path.name}")
                continue

        errors = [err for err in errors if str(err.get("file") or "") != path_key]

        try:
            parsed, diagnostics = review_image_with_retries(
                base_url=base_url,
                api_key=api_key or "local",
                model=model,
                image_path=path,
                prompt=prompt_for(path, kind, assessment_profile),
                timeout=args.timeout,
                max_tokens=args.max_tokens,
                temperature=temperature,
                retries=args.retries,
                raw_out_dir=raw_out_dir,
            )

            observation = normalize_review_observation(
                image_path=path,
                kind=kind,
                model=model,
                response=parsed,
                raw_response_path=diagnostics.get("raw_response_path"),
                raw_response_paths=diagnostics.get("raw_response_paths"),
                retry_count=diagnostics.get("retry_count_used"),
                repair_applied=False,
                parse_status="parsed",
                runtime_review_performed=True,
                existing={"parse_errors": diagnostics.get("parse_errors", [])},
                expected_image_ids=set(canonical_ids) if canonical_ids else None,
            )
            observation["parse_errors"] = diagnostics.get("parse_errors", [])
            by_file[path_key] = observation
            observations = [by_file[file_name] for file_name in target_order if file_name in by_file]

            # Check persisted validity before printing PASS
            is_valid_review = observation_successful(observation, set(canonical_ids) if canonical_ids else None)
            annotation_ok = True
            if assessment_profile in ("balanced", "engineering"):
                ann_artifact_path = annotations_out if args.annotations_out else out.parent / f"{args.project}-vision-engineering-annotations.json"
                if ann_artifact_path.exists():
                    try:
                        ann_data = json.loads(ann_artifact_path.read_text(encoding="utf-8"))
                        found_ann = False
                        for ann in ann_data.get("annotations", []):
                            a_id = ann.get("image_id", "")
                            if a_id == img_id:
                                status = ann.get("validation_status", "valid")
                                if status != "repaired_minimal":
                                    found_ann = True
                                break
                        annotation_ok = found_ann
                    except Exception:
                        pass

            if is_valid_review and annotation_ok:
                print(f"PASS vision review + annotation: {path.name}")
            elif is_valid_review and not annotation_ok:
                print(f"WARN valid review but missing/invalid annotation for {path.name}: will repair")
            else:
                print(f"FAIL vision review (valid={is_valid_review}, annotation_ok={annotation_ok}): {path.name}")

        except Exception as e:
            error: dict[str, Any] = {"file": path_key, "kind": kind, "model": model, "error": str(e)}
            try:
                details = json.loads(str(e))
                if isinstance(details, dict):
                    error.update(details)
            except Exception:
                pass
            failed_observation = normalize_review_observation(
                image_path=path,
                kind=kind,
                model=model,
                response={},
                raw_response_path=error.get("raw_response_path"),
                raw_response_paths=error.get("raw_response_paths"),
                retry_count=error.get("retry_count_used"),
                repair_applied=False,
                parse_status="failed",
                runtime_review_performed=False,
                validation_errors=[str(error.get("message") or error.get("error") or e)],
                expected_image_ids=set(canonical_ids) if canonical_ids else None,
            )
            failed_observation["parse_errors"] = error.get("parse_errors", [])
            by_file[path_key] = failed_observation
            observations = [by_file[file_name] for file_name in target_order if file_name in by_file]
            errors.append(error)
            print(f"FAIL vision review: {path.name}: {e}", file=sys.stderr)

        artifact = write_artifacts(
            out=out,
            project=args.project,
            base_url=base_url,
            model=model,
            expected=effective_expected,
            observations=observations,
            errors=errors,
            temperature=temperature,
            expected_image_ids=canonical_ids or None,
        )
        print(
            f"Progress written: {artifact['reviewed_image_count']}/{artifact['expected_image_count']} "
            f"reviewed, errors={len(artifact['errors'])}, overall_pass={artifact['overall_pass']}"
        )
        if assessment_enabled(assessment_profile):
            annotations = write_annotations_artifact(
                annotations_out=annotations_out,
                project=args.project,
                assessment_profile=assessment_profile,
                base_artifact=artifact,
            )
            print(
                f"Annotations written: {annotations['annotation_count']} annotations, "
                f"generic_claim_count={annotations['generic_claim_count']}, "
                f"overall_pass={annotations['overall_pass']}"
            )
        time.sleep(args.sleep)

    artifact = write_artifacts(
        out=out,
        project=args.project,
        base_url=base_url,
        model=model,
        expected=effective_expected,
        observations=observations,
        errors=errors,
        temperature=temperature,
        expected_image_ids=canonical_ids or None,
    )
    print(f"\nWrote {out}")
    print("overall_pass:", artifact["overall_pass"])
    print("reviewed:", artifact["reviewed_image_count"], "/", artifact["expected_image_count"])
    print("errors:", len(errors))
    print(f"Wrote {out.parent / f'{args.project}-image-evidence-review-validation.json'}")
    if assessment_enabled(assessment_profile):
        annotations = write_annotations_artifact(
            annotations_out=annotations_out,
            project=args.project,
            assessment_profile=assessment_profile,
            base_artifact=artifact,
        )
        print(f"Wrote {annotations_out}")
        print("annotations_overall_pass:", annotations["overall_pass"])

    return 0 if artifact["overall_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
