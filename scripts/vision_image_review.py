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
) -> str:
    url = base_url.rstrip("/") + "/chat/completions"

    payload = {
        "model": model,
        "temperature": 0.1,
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


def _ensure_review_record_fields(response: dict[str, Any], source_file: str, kind: str) -> dict[str, Any]:
    """Deterministically repair missing validation fields from runtime facts."""
    repaired = False
    if response.get("page_actually_opened") is None and response.get("actual_image_review_performed") is None:
        # If the model confirmed visual review was performed, treat it as actually opened.
        if response.get("visual_review_performed") is True:
            response["page_actually_opened"] = True
            response["actual_image_review_performed"] = True
            repaired = True
        else:
            response["page_actually_opened"] = False
            response["actual_image_review_performed"] = False
            repaired = True

    if "page_type" not in response or not response.get("page_type"):
        response["page_type"] = kind if kind in {"schematic", "layout", "gerber"} else "unknown"
        repaired = True

    if "image_id" not in response or not response.get("image_id"):
        response["image_id"] = Path(source_file).name
        repaired = True

    for field in ("errors",):
        val = response.get(field)
        if not isinstance(val, list):
            response[field] = []
            repaired = True

    for field in ("warnings",):
        val = response.get(field)
        if not isinstance(val, list):
            response[field] = []
            repaired = True

    return response, repaired


def observation_successful(observation: dict[str, Any]) -> bool:
    """Check that the persisted review record is valid."""
    response = observation.get("response")
    if not isinstance(response, dict):
        return False
    # Must have explicit page_actually_opened or actual_image_review_performed
    opened = response.get("page_actually_opened") or response.get("actual_image_review_performed")
    if opened is not True:
        return False
    return (
        response.get("visual_review_performed") is True
        and response.get("confirmation_no_pixel_quantitative_claims") is True
    )


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
    observations = base_artifact.get("per_page_vision_observations", [])
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
) -> dict[str, Any]:
    by_file: dict[str, dict[str, Any]] = {}
    ordered_files: list[str] = []
    for observation in observations:
        file_name = str(observation.get("file") or "")
        if not file_name:
            continue
        if file_name not in by_file:
            ordered_files.append(file_name)
        by_file[file_name] = observation

    deduped_observations = [by_file[file_name] for file_name in ordered_files]

    # Count pages_actually_opened from explicit per-record fields
    pages_actually_opened_count = 0
    reviewed_image_count = 0
    failed_or_missing_ids: list[str] = []

    for obs in deduped_observations:
        resp = obs.get("response")
        if not isinstance(resp, dict):
            continue
        img_id = str(resp.get("image_id", "")) or Path(str(obs.get("file", ""))).name
        opened = (resp.get("page_actually_opened") is True) or (resp.get("actual_image_review_performed") is True)
        if opened:
            pages_actually_opened_count += 1
        # Check if observation_successful (valid review record)
        if observation_successful(obs):
            reviewed_image_count += 1
        else:
            failed_or_missing_ids.append(img_id)

    all_reviewed = expected > 0 and reviewed_image_count == expected and not errors
    all_opened = expected > 0 and pages_actually_opened_count == expected
    all_no_pixel_geometry = (
        len(deduped_observations) == reviewed_image_count
        and all(bool(obs.get("response", {}).get("confirmation_no_pixel_quantitative_claims")) for obs in deduped_observations if isinstance(obs.get("response"), dict))
    )

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
        "expected_image_count": expected,
        "reviewed_image_count": reviewed_image_count,
        "pages_actually_opened_count": pages_actually_opened_count,
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
) -> dict[str, Any]:
    artifact = artifact_for(
        project=project,
        base_url=base_url,
        model=model,
        expected=expected,
        observations=observations,
        errors=errors,
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

    if not base_url or not model:
        print("ERROR: set VISION_BASE_URL and VISION_MODEL, or LLM_BASE_URL and LLM_MODEL", file=sys.stderr)
        return 2

    images = list_images(exports, args.project)
    if args.limit and args.limit > 0:
        images = images[: args.limit]

    target_files = {str(path) for _, path in images}
    target_order = [str(path) for _, path in images]
    observations: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    if args.resume and not args.force:
        previous_observations, previous_errors = load_previous_artifact(out)
        observations = [
            obs
            for obs in previous_observations
            if str(obs.get("file") or "") in target_files and observation_successful(obs)
        ]
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

    # Determine expected count from canonical inventory when available
    canonical_ids = expected_image_ids_from_inventory(exports, args.project)
    images_for_processing = expected_image_paths(exports, args.project, canonical_ids) if canonical_ids else images
    effective_expected = len(images_for_processing) or len(images)

    artifact = write_artifacts(
        out=out,
        project=args.project,
        base_url=base_url,
        model=model,
        expected=effective_expected,
        observations=observations,
        errors=errors,
    )

    # Build set of images that have both valid review AND (in balanced/engineering mode) valid annotation
    completed_image_ids: set[str] = set()
    for obs in observations:
        if not isinstance(obs, dict):
            continue
        resp = obs.get("response")
        if not isinstance(resp, dict):
            continue
        img_id = str(resp.get("image_id", "")) or Path(str(obs.get("file", ""))).name
        if img_id and observation_successful(obs):
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
                retries=args.retries,
                raw_out_dir=raw_out_dir,
            )

            # Ensure the response has required validation fields
            repaired_resp, was_repaired = _ensure_review_record_fields(parsed, path_key, kind)
            parsed = repaired_resp

            observation = {
                "file": path_key,
                "kind": kind,
                "model": model,
                **diagnostics,
                "response": parsed,
                "repair_applied": was_repaired,
            }
            by_file[path_key] = observation
            observations = [by_file[file_name] for file_name in target_order if file_name in by_file]

            # Check persisted validity before printing PASS
            is_valid_review = observation_successful(observation)
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
        expected=len(images),
        observations=observations,
        errors=errors,
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
