#!/usr/bin/env python3
import argparse
import json
import os
import re
import time
import urllib.request
from pathlib import Path

SYSTEM = """Return exactly one JSON object. No markdown. No commentary.
Use only the provided packet evidence.
Do not guess.
If evidence is insufficient, mark the item unknown.
Datasheet ratings are component capabilities, not board operating current.
"""

FORBIDDEN_TARGET_TYPES = {
    "connector", "mosfet", "capacitor", "resistor",
    "regulator", "fuse", "ic", "diode"
}

RATING_TARGET_TYPES = {
    "connector_rating",
    "connector_pin_rating",
    "fuse_rating",
    "regulator_rating",
    "load_switch_rating",
    "ferrite_rating",
}

IC_DESCRIPTION_TERMS = {
    "ic",
    "mux",
    "multiplexer",
    "repeater",
    "transceiver",
    "buffer",
    "regulator",
    "controller",
    "driver",
    "converter",
}

SUPPLY_CURRENT_TERMS = (
    "supply current",
    "positive supply current",
    "high-level supply current",
    "low-level supply current",
    "static current",
    "quiescent current",
    "standby current",
    "current consumption",
    "operating current",
    "icc",
    "icch",
    "iccl",
    "idd",
    "iq",
)

OPERATING_CURRENT_REJECT_TERMS = (
    "absolute maximum",
    "limiting values",
    "stresses beyond",
    "output current",
    "input/output current",
    "current limit",
    "peak current",
    "rated current",
    "load current",
    "short-circuit current",
    "short circuit current",
    "sink current",
    "source current",
    "drive current",
    "clamp current",
    "leakage current",
    "off-state leakage",
    "on-state leakage",
    "additional per input pin",
)

MICRO_UNITS = {"ua", "µa", "μa", "a"}
CURRENT_VALUE_RE = re.compile(
    r"(?P<value>[+-]?\d+(?:\.\d+)?)\s*(?P<unit>mA|uA|µA|μA|A|nA|amps?|amperes?|A)\b",
    re.I,
)
SUPPLY_SYMBOL_RE = re.compile(r"\b(?:I(?:CC|CCH|CCL|CCLC|DD|Q)|ICC|ICCH|ICCL|ICCLc|IDD|IQ)\b", re.I)


def normalize_text(text):
    return re.sub(r"\s+", " ", str(text or "").replace("\uf06d", "μ").replace("\uf057", "Ω")).strip()


def normalize_unit(unit):
    normalized_unit = str(unit).strip().lower()
    if normalized_unit in MICRO_UNITS:
        return "ua"
    if normalized_unit in {"amp", "amps", "ampere", "amperes"}:
        return "a"
    return normalized_unit

def read_json(path):
    return json.loads(path.read_text(encoding="utf-8", errors="replace"))

def chat(base_url, api_key, model, messages, max_tokens):
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0,
        "max_tokens": max_tokens,
        "stream": False,
    }

    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=900) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    return data["choices"][0]["message"]["content"]

def parse_json(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)

    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]

    return json.loads(text)

def get_missing_ids(req):
    return [str(x) for x in req.get("missing_data_item_ids", [])]


def safe_item_id_part(value):
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "")).strip("_")
    return text or "target"


def extracted_item_id(packet_id, target_type, field_name, target_refdes, index):
    return ":".join(
        [
            safe_item_id_part(packet_id),
            safe_item_id_part(target_type),
            safe_item_id_part(field_name),
            safe_item_id_part(target_refdes),
            f"{index:06d}",
        ]
    )


def make_extracted_item(packet_id, missing_id, target_type, target_refdes, target_mpn, field_name, value, unit, condition, source_file, evidence_quote, confidence, *, basis="datasheet_evidence", **extra):
    index = int(extra.pop("_index"))
    item = {
        "item_id": extracted_item_id(packet_id, target_type, field_name, target_refdes, index),
        "missing_data_item_ids": [str(missing_id)],
        "missing_data_item_id": str(missing_id),
        "target_type": target_type,
        "target_refdes": target_refdes,
        "target_mpn": target_mpn,
        "field_name": field_name,
        "value": value,
        "unit": unit,
        "condition": condition,
        "basis": basis,
        "source_file": source_file,
        "evidence_quote": evidence_quote,
        "confidence": confidence,
    }
    if extra.get("page") is not None:
        item["page"] = extra["page"]
        item["source_page"] = extra["page"]
    for key, value in extra.items():
        if key != "page" and value is not None:
            item[key] = value
    return item


def next_extracted_index(extracted_items):
    return len(extracted_items) + 1

def build_prompt(packet_dir):
    req = read_json(packet_dir / "request.json")
    ctx = read_json(packet_dir / "context.json")
    prompt_md = (packet_dir / "prompt.md").read_text(encoding="utf-8", errors="replace")

    packet_id = req["packet_id"]
    missing_ids = get_missing_ids(req)
    target_type = req.get("target_type") or req.get("expected_target_type")

    example = {
        "packet_id": packet_id,
        "items": {
            mid: {
                "status": "extracted or unknown",
                "target_type": target_type,
                "field_name": "branch_current_a",
                "value": None,
                "unit": None,
                "condition": None,
                "source_file": None,
                "evidence_quote": None,
                "reason": "required when status is unknown"
            }
            for mid in missing_ids
        }
    }

    return f"""
Generate EASY_JSON_V1 only.

Required shape:
{json.dumps(example, indent=2)}

Rules:
- packet_id must be exactly: {packet_id}
- IMPORTANT for connector ratings:
  If the packet is datasheet_current_extraction and the context contains text like "Current rating: 3 A AC/DC (AWG #22)", return an extracted rating candidate:
  {{
    "status": "extracted",
    "target_type": "connector_rating",
    "field_name": "current_max",
    "value": 3,
    "unit": "A",
    "condition": "AC/DC, AWG #22",
    "source_file": "the datasheet source_file from context",
    "evidence_quote": "Current rating: 3 A AC/DC（AWG #22）"
  }}
  This rating candidate does not resolve branch_current_a; the converter will preserve branch_current_a as unknown.
- items must be an object, not a list
- items must contain exactly these keys: {missing_ids}
- each item value must be an object, not a string
- status must be "extracted" or "unknown"
- if extracted: value, unit, source_file, and evidence_quote are required
- if unknown: value must be null and reason is required
- do not use semantic target_type words like connector, mosfet, capacitor, resistor, regulator, fuse, ic, diode
- do not claim datasheet ratings are board operating current
- for datasheet_current_extraction packets: if actual operating current is absent but a connector/fuse/regulator/load-switch/ferrite rating is present, return it as an extracted rating candidate using target_type connector_rating, connector_pin_rating, fuse_rating, regulator_rating, load_switch_rating, or ferrite_rating
- for connector current rating/capability evidence, use target_type connector_rating and field_name current_max unless the evidence is explicitly per-pin, then use connector_pin_rating and pin_current_max
- rating candidates do not resolve branch_current_a; branch operating current must remain unknown unless explicit board operating-current evidence is present
- no findings
- no pass/fail
- no compliance judgment

REQUEST_JSON:
{json.dumps(req, indent=2, ensure_ascii=False)}

CONTEXT_JSON:
{json.dumps(ctx, indent=2, ensure_ascii=False)}

PROMPT_MD:
{prompt_md}
"""


def looks_like_connector_target(req, ctx):
    refdes = str(req.get("target_refdes") or "")
    text = json.dumps({
        "target_value": req.get("target_value"),
        "target_description": req.get("target_description"),
        "bom": ctx.get("target_bom_evidence") or ctx.get("target_bom_data") or [],
        "datasheets": ctx.get("datasheet_references") or [],
    }, ensure_ascii=False).lower()

    return (
        refdes.startswith(("P", "J"))
        or "connector" in text
        or "con_hdr" in text
        or "wire-to-board" in text
        or "header" in text
    )


def target_context_text(req, ctx):
    return json.dumps({
        "target_refdes": req.get("target_refdes"),
        "target_value": req.get("target_value"),
        "target_description": req.get("target_description"),
        "target_mpn": req.get("target_mpn"),
        "bom": ctx.get("target_bom_evidence") or ctx.get("target_bom_data") or [],
    }, ensure_ascii=False).lower()


def classify_target_device(req, ctx):
    refdes = str(req.get("target_refdes") or "").upper()
    text = target_context_text(req, ctx)
    if refdes.startswith("Q") or any(term in text for term in ("mosfet", "fet", "transistor", "load switch")):
        return "mosfet"
    if refdes.startswith("U"):
        if "regulator" in text or "ldo" in text or "buck" in text or "boost" in text or "dc-dc" in text:
            return "regulator"
        return "ic"
    if looks_like_connector_target(req, ctx):
        return "connector"
    if "regulator" in text or "ldo" in text or "buck" in text or "boost" in text or "dc-dc" in text:
        return "regulator"
    if "fuse" in text or "ptc" in text or "efuse" in text:
        return "fuse"
    if refdes.startswith("FB") or "ferrite" in text or "inductor" in text:
        return "ferrite"
    if refdes.startswith(("D", "LED")) or any(term in text for term in ("diode", "tvs", "schottky", "rectifier", "led")):
        return "diode_led"
    if looks_like_ic_current_target(req, ctx):
        return "ic"
    return "unknown"


def quoted_candidate_ref_fields(ref):
    return {
        "source_file": ref.get("source_file") or ref.get("filename"),
        **({"page": ref.get("page")} if ref.get("page") is not None else {}),
        **({"evidence_block_id": ref.get("evidence_block_id")} if ref.get("evidence_block_id") is not None else {}),
    }


def rating_candidate(target_type, field_name, value, condition, ref, quote, confidence=0.75, human_review_needed=False):
    return {
        "target_type": target_type,
        "field_name": field_name,
        "value": value,
        "unit": "A",
        "condition": condition,
        "evidence_quote": quote.strip(),
        "confidence": confidence,
        "human_review_needed": human_review_needed,
        **quoted_candidate_ref_fields(ref),
    }


def connector_quote_is_rejected(quote):
    lower = quote.lower()
    return any(term in lower for term in (
        "test plug",
        "pogo pin",
        "not intended for continuous use",
        "continuity test",
        "contact resistance",
        "leakage current",
        "dielectric",
        "wire pullout",
        "insertion force",
        "packaging",
    ))


def connector_rating_quote_text(quote):
    text = normalize_text(quote)
    split = re.split(r"\bcurrent\s+for\s+test\s+plug\b", text, maxsplit=1, flags=re.I)
    return split[0].strip()


def extract_connector_rating_candidates(packet_dir, req):
    if req.get("packet_type") != "datasheet_current_extraction":
        return []

    ctx = read_json(packet_dir / "context.json")

    if not looks_like_connector_target(req, ctx):
        return []

    pattern = re.compile(
        r"(?i)current\s+rating\s*[:：]\s*"
        r"(?P<value>[+-]?\d+(?:\.\d+)?)\s*"
        r"(?P<unit>A|mA|uA|µA|μA|A|amps?|amperes?)"
        r"(?P<trailing>[^.。\n\r]{0,120})"
    )

    candidates = []
    for ref in ctx.get("datasheet_references", []):
        quote = connector_rating_quote_text(quote_text(ref))
        if connector_quote_is_rejected(quote) and not any(term in quote.lower() for term in ("current rating", "current derating", "rated current")):
            continue
        match = pattern.search(quote)
        if match:
            trailing = match.group("trailing") or ""
            condition_parts = []
            if re.search(r"AC\s*/\s*DC|AC/DC", trailing, re.I):
                condition_parts.append("AC/DC")

            awg_match = re.search(r"AWG\s*#?\s*(\d+)", trailing, re.I)
            if awg_match:
                condition_parts.append(f"AWG #{awg_match.group(1)}")

            start = max(0, match.start() - 20)
            end = min(len(quote), match.end() + 80)
            candidates.append(
                rating_candidate(
                    "connector_rating",
                    "current_max",
                    abs(current_value_to_a(match.group("value"), match.group("unit"))),
                    ", ".join(condition_parts) if condition_parts else "connector current rating",
                    ref,
                    quote[start:end],
                    confidence=0.85,
                    human_review_needed=False,
                )
            )

        lower = quote.lower()
        if "current derating" in lower and "applicable wires" in lower and "amp" in lower:
            matches = [
                candidate_match for candidate_match in CURRENT_VALUE_RE.finditer(quote)
                if normalize_unit(candidate_match.group("unit")) == "a"
            ]
            if matches:
                best = max(matches, key=lambda m: abs(current_value_to_a(m.group("value"), m.group("unit"))))
                awg_before = quote[max(0, best.start() - 160): best.start()]
                awg_match = re.search(r"(\d{2})\s*AWG", awg_before, re.I)
                condition = "current derating table; application dependent"
                if awg_match:
                    condition += f"; AWG {awg_match.group(1)}"
                candidates.append(
                    rating_candidate(
                        "connector_rating",
                        "current_max",
                        abs(current_value_to_a(best.group("value"), best.group("unit"))),
                        condition,
                        ref,
                        current_context_window(quote, best, before=260, after=180),
                        confidence=0.7,
                        human_review_needed=True,
                    )
                )
            else:
                row_candidates = []
                for row_match in re.finditer(r"\b(?P<awg>\d{2})\s*AWG(?P<row>.{0,140})", quote, re.I):
                    values = [
                        float(value)
                        for value in re.findall(r"(?<![#\w.])\d+(?:\.\d+)?(?!\s*(?:AWG|mm|inch|ckt|circuit)|[\w.])", row_match.group("row"))
                    ]
                    for value in values:
                        if 0 < value <= 100:
                            row_candidates.append((value, row_match.group("awg"), row_match.group(0)))
                if row_candidates:
                    value, awg, row_quote = max(row_candidates, key=lambda item: item[0])
                    candidates.append(
                        rating_candidate(
                            "connector_rating",
                            "current_max",
                            value,
                            f"current derating table; application dependent; AWG {awg}",
                            ref,
                            row_quote,
                            confidence=0.7,
                            human_review_needed=True,
                        )
                    )

    return sorted(candidates, key=lambda item: (item["confidence"], item["value"]), reverse=True)


def extract_connector_current_rating_from_context(packet_dir, req):
    candidates = extract_connector_rating_candidates(packet_dir, req)
    return candidates[0] if candidates else None


def quote_text(ref):
    for key in ("evidence_quote", "text_snippet", "text", "value_raw"):
        value = ref.get(key) if isinstance(ref, dict) else None
        if value not in (None, ""):
            return str(value)
    return ""


def looks_like_ic_current_target(req, ctx):
    refdes = str(req.get("target_refdes") or "").upper()
    if refdes.startswith("U"):
        return True
    text = json.dumps({
        "target_value": req.get("target_value"),
        "target_description": req.get("target_description"),
        "bom": ctx.get("target_bom_evidence") or ctx.get("target_bom_data") or [],
    }, ensure_ascii=False).lower()
    return any(term in text for term in IC_DESCRIPTION_TERMS)


def current_value_to_a(value, unit):
    normalized_unit = normalize_unit(unit)
    multipliers = {
        "a": 1.0,
        "ma": 0.001,
        "ua": 0.000001,
        "na": 0.000000001,
    }
    return round(float(value) * multipliers[normalized_unit], 12)


def usable_ic_supply_current_quote(text):
    lower = text.lower()
    if not any(term in lower for term in SUPPLY_CURRENT_TERMS):
        return False
    if any(term in lower for term in ("absolute maximum", "limiting values", "stresses beyond")):
        return False
    return bool(CURRENT_VALUE_RE.search(text))


def classify_current_field(text):
    lower = text.lower()
    has_max = bool(re.search(r"\b(max|maximum)\b", lower))
    has_typ = bool(re.search(r"\b(typ|typical)\b", lower))
    if has_typ and not has_max:
        return "typ_current_a"
    return "max_current_a"


def sentence_windows(text):
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if not normalized:
        return []
    pieces = re.split(r"(?<=[.;])\s+", normalized)
    return [piece.strip() for piece in pieces if piece.strip()]


def current_context_window(text, match, before=220, after=220):
    start = max(0, match.start() - before)
    end = min(len(text), match.end() + after)
    return text[start:end]


def supply_current_match_window(text, match):
    symbols = [symbol for symbol in SUPPLY_SYMBOL_RE.finditer(text[: match.start()]) if symbol.start() >= max(0, match.start() - 360)]
    if symbols:
        start = symbols[-1].start()
    else:
        start = max(0, match.start() - 120)
    end = min(len(text), match.end() + 80)
    tail = text[match.end() : end]
    next_delta = re.search(r"\s(?:ΔICC|δICC|delta\s+ICC|additional per input pin)\b", tail, re.I)
    if next_delta:
        end = match.end() + next_delta.start()
    return text[start:end]


def preceding_has_supply_current(text, match):
    before = text[max(0, match.start() - 360) : match.start()]
    return window_has_supply_current(before)


def window_has_supply_current(window):
    lower = window.lower()
    return SUPPLY_SYMBOL_RE.search(window) is not None or any(term in lower for term in SUPPLY_CURRENT_TERMS)


def window_is_rejected_operating_current(window):
    lower = window.lower()
    if any(term in lower for term in ("absolute maximum", "limiting values", "stresses beyond")):
        return True
    if any(term in lower for term in ("off-state leakage", "on-state leakage", "input leakage", "output leakage", "leakage current")):
        return True
    if any(term in lower for term in ("additional per input pin", "δicc", "Δicc".lower(), "delta icc")):
        return True
    if any(term in lower for term in ("output current", "maximum output current", "iout(max)", "current limit", "load current", "short-circuit current", "short circuit current", "clamp current", "sink current", "source current", "drive current", "rated current", "peak current")):
        return not window_has_supply_current(window)
    return False


def looks_graph_only_current_text(text):
    lower = text.lower()
    graph_terms = ("figure", " vs ", "graph", "curve", "typical characteristics", "performance characteristics")
    table_terms = ("max", "maximum", "electrical characteristics", "static characteristics", "supply characteristics")
    return any(term in lower for term in graph_terms) and not any(term in lower for term in table_terms)


def condition_from_supply_window(window):
    lower = window.lower()
    parts = []
    for token in ("normal mode", "standby", "static characteristics", "electrical characteristics", "supply characteristics", "positive supply current", "high-level supply current", "low-level supply current", "quiescent current"):
        if token in lower:
            parts.append(token)
    if re.search(r"\b(max|maximum)\b", lower):
        parts.append("maximum")
    elif re.search(r"\b(typ|typical)\b", lower):
        parts.append("typical")
    vcc = re.search(r"VCC\s*=\s*[^;,.)]{1,40}", window, re.I)
    if vcc:
        parts.append(vcc.group(0).strip())
    return ", ".join(dict.fromkeys(parts)) if parts else None


def supply_current_candidates_from_quote(quote):
    if not usable_ic_supply_current_quote(quote):
        return []
    if looks_graph_only_current_text(quote):
        return []

    candidates = []
    for match in CURRENT_VALUE_RE.finditer(quote):
        before_tail = quote[max(0, match.start() - 100) : match.start()].lower()
        if "dropout voltage" in before_tail or re.search(r"@\s*$|@\s*\d", before_tail[-16:]):
            continue
        if not preceding_has_supply_current(quote, match):
            continue
        window = supply_current_match_window(quote, match)
        if not window_has_supply_current(window):
            continue
        if window_is_rejected_operating_current(window):
            continue
        if looks_graph_only_current_text(window):
            continue
        field_name = classify_current_field(window)
        candidates.append(
            {
                "field_name": field_name,
                "value": current_value_to_a(match.group("value"), match.group("unit")),
                "unit": "A",
                "condition": condition_from_supply_window(window),
                "evidence_quote": window.strip(),
            }
        )
    return candidates


def best_supply_current_candidate(refs):
    best = None
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        quote = quote_text(ref)
        for candidate in supply_current_candidates_from_quote(quote):
            candidate = {
                **candidate,
                "source_file": ref.get("source_file") or ref.get("filename"),
                **({"page": ref.get("page")} if ref.get("page") is not None else {}),
                **({"evidence_block_id": ref.get("evidence_block_id")} if ref.get("evidence_block_id") is not None else {}),
            }
            if best is None:
                best = candidate
                continue
            if candidate["field_name"] == "max_current_a" and best["field_name"] != "max_current_a":
                best = candidate
            elif candidate["field_name"] == best["field_name"] and candidate["value"] > best["value"]:
                best = candidate
    return best


def regulator_rating_candidate_from_context(packet_dir, req):
    if req.get("packet_type") != "datasheet_current_extraction":
        return None
    ctx = read_json(packet_dir / "context.json")
    text = json.dumps({
        "target_value": req.get("target_value"),
        "target_description": req.get("target_description"),
        "bom": ctx.get("target_bom_evidence") or ctx.get("target_bom_data") or [],
    }, ensure_ascii=False).lower()
    if not (str(req.get("target_refdes") or "").upper().startswith("U") and ("regulator" in text or "ldo" in text)):
        return None
    for ref in ctx.get("datasheet_references", []):
        quote = quote_text(ref)
        lower = quote.lower()
        if not ("iout(max)" in lower or "maximum output current" in lower):
            continue
        if "absolute maximum" in lower:
            continue
        matches = list(CURRENT_VALUE_RE.finditer(quote))
        if not matches:
            continue
        # Ratings use the largest current-like value in the rating row/text.
        match = max(matches, key=lambda m: current_value_to_a(m.group("value"), m.group("unit")))
        return {
            "target_type": "regulator_rating",
            "field_name": "current_max",
            "value": current_value_to_a(match.group("value"), match.group("unit")),
            "unit": "A",
            "condition": "maximum output current",
            "source_file": ref.get("source_file") or ref.get("filename"),
            "evidence_quote": current_context_window(quote, match).strip(),
            "confidence": 0.75,
            **({"page": ref.get("page")} if ref.get("page") is not None else {}),
            **({"evidence_block_id": ref.get("evidence_block_id")} if ref.get("evidence_block_id") is not None else {}),
        }
    return None


def extract_regulator_rating_candidates(packet_dir, req):
    candidate = regulator_rating_candidate_from_context(packet_dir, req)
    return [candidate] if candidate else []


def mosfet_quote_is_rejected(quote):
    lower = quote.lower()
    return any(term in lower for term in (
        "idss",
        "igss",
        "leakage",
        "body diode",
    ))


def mosfet_quote_is_graph_like(quote):
    lower = quote.lower()
    return any(term in lower for term in (
        "typical characteristics",
        "figure",
        "graph",
        "on-region characteristics",
        "transfer characteristics",
        "normalized",
        "pulse duration",
        "duty cycle",
    ))


def mosfet_rating_evidence_score(quote):
    lower = quote.lower()
    score = 0
    for term in (
        "product summary",
        "maximum ratings",
        "absolute maximum ratings",
        "continuous drain current",
        "drain current -continuous",
        "drain current - continuous",
        "id max",
        "i d max",
    ):
        if term in lower:
            score += 20
    for term in (
        "typical characteristics",
        "figure",
        "graph",
        "on-region characteristics",
        "transfer characteristics",
        "normalized",
        "pulse duration",
        "duty cycle",
    ):
        if term in lower:
            score -= 30
    return score


def mosfet_continuous_current_matches(quote):
    patterns = [
        re.compile(r"\bI\s*D\b\s*max\b.{0,140}?" + CURRENT_VALUE_RE.pattern, re.I),
        re.compile(r"\bI\s*D\b\s*=\s*" + CURRENT_VALUE_RE.pattern, re.I),
        re.compile(r"drain\s+current.{0,80}?continuous.{0,120}?" + CURRENT_VALUE_RE.pattern, re.I),
    ]
    matches = []
    for pattern in patterns:
        for match in pattern.finditer(quote):
            window = current_context_window(quote, match, before=120, after=120)
            before_value = quote[max(0, match.start("value") - 48): match.start("value")].lower()
            if "pulsed" in before_value:
                continue
            if "pulsed" in window.lower() and "continuous" not in window.lower():
                continue
            matches.append(match)
    return matches


def extract_mosfet_rating_candidates(packet_dir, req):
    if req.get("packet_type") != "datasheet_current_extraction":
        return []
    ctx = read_json(packet_dir / "context.json")
    if classify_target_device(req, ctx) != "mosfet":
        return []

    candidates = []
    for ref in ctx.get("datasheet_references", []):
        quote = normalize_text(quote_text(ref))
        lower = quote.lower()
        if mosfet_quote_is_rejected(quote):
            continue
        if not any(term in lower for term in ("mosfet", "drain current", "id max", "product summary", "maximum ratings", "load switch")):
            continue
        evidence_score = mosfet_rating_evidence_score(quote)
        is_graph_like = mosfet_quote_is_graph_like(quote)
        for match in mosfet_continuous_current_matches(quote):
            value = abs(current_value_to_a(match.group("value"), match.group("unit")))
            if value <= 0:
                continue
            condition = "continuous drain current capability; board operating current not provided"
            if is_graph_like:
                condition += "; graph/ambiguous evidence, human review required"
            candidates.append(
                rating_candidate(
                    "load_switch_rating",
                    "current_max",
                    value,
                    condition,
                    ref,
                    current_context_window(quote, match, before=220, after=160),
                    confidence=0.82 if evidence_score > 0 else 0.45,
                    human_review_needed=True,
                )
            )
            candidates[-1]["evidence_score"] = evidence_score

    return sorted(candidates, key=lambda item: (item.get("evidence_score", 0), item.get("confidence", 0), item["value"]), reverse=True)


def extract_fuse_rating_candidates(packet_dir, req):
    if req.get("packet_type") != "datasheet_current_extraction":
        return []
    ctx = read_json(packet_dir / "context.json")
    if classify_target_device(req, ctx) != "fuse":
        return []
    candidates = []
    field_patterns = (
        ("hold_current", r"(?:hold current|ihold)\b"),
        ("trip_current", r"(?:trip current|itrip)\b"),
        ("current_max", r"(?:rated current|fuse rating|current rating)\b"),
    )
    for ref in ctx.get("datasheet_references", []):
        quote = normalize_text(quote_text(ref))
        lower = quote.lower()
        for field_name, field_pattern in field_patterns:
            if not re.search(field_pattern, lower, re.I):
                continue
            matches = list(CURRENT_VALUE_RE.finditer(quote))
            if not matches:
                continue
            match = max(matches, key=lambda m: abs(current_value_to_a(m.group("value"), m.group("unit"))))
            candidates.append(
                rating_candidate(
                    "fuse_rating",
                    field_name,
                    abs(current_value_to_a(match.group("value"), match.group("unit"))),
                    field_name.replace("_", " "),
                    ref,
                    current_context_window(quote, match),
                    confidence=0.72,
                    human_review_needed=True,
                )
            )
            break
    return candidates


def extract_ferrite_rating_candidates(packet_dir, req):
    if req.get("packet_type") != "datasheet_current_extraction":
        return []
    ctx = read_json(packet_dir / "context.json")
    if classify_target_device(req, ctx) != "ferrite":
        return []
    candidates = []
    for ref in ctx.get("datasheet_references", []):
        quote = normalize_text(quote_text(ref))
        lower = quote.lower()
        if not any(term in lower for term in ("rated current", "current rating", "allowable current", "saturation current", "irms", "isat")):
            continue
        matches = list(CURRENT_VALUE_RE.finditer(quote))
        if not matches:
            continue
        match = max(matches, key=lambda m: abs(current_value_to_a(m.group("value"), m.group("unit"))))
        candidates.append(
            rating_candidate(
                "ferrite_rating",
                "current_max",
                abs(current_value_to_a(match.group("value"), match.group("unit"))),
                "rated/saturation current capability",
                ref,
                current_context_window(quote, match),
                confidence=0.72,
                human_review_needed=True,
            )
        )
    return sorted(candidates, key=lambda item: item["value"], reverse=True)


def extract_rating_candidates(packet_dir, req):
    candidates = []
    candidates.extend(extract_connector_rating_candidates(packet_dir, req))
    candidates.extend(extract_mosfet_rating_candidates(packet_dir, req))
    candidates.extend(extract_regulator_rating_candidates(packet_dir, req))
    candidates.extend(extract_fuse_rating_candidates(packet_dir, req))
    candidates.extend(extract_ferrite_rating_candidates(packet_dir, req))
    return sorted(candidates, key=lambda item: (item.get("confidence", 0), item.get("value") or 0), reverse=True)


def extract_ic_supply_current_from_context(packet_dir, req):
    if req.get("packet_type") != "datasheet_current_extraction":
        return None
    if (req.get("target_type") or req.get("expected_target_type")) != "component_current_model":
        return None

    ctx = read_json(packet_dir / "context.json")
    if not looks_like_ic_current_target(req, ctx):
        return None

    current = best_supply_current_candidate(ctx.get("datasheet_references", []))
    if current is not None:
        return {
            "target_type": "component_current_model",
            "field_name": current["field_name"],
            "value": current["value"],
            "unit": current["unit"],
            "condition": current["condition"],
            "source_file": current["source_file"],
            "evidence_quote": current["evidence_quote"],
            "confidence": 0.75,
            **({"page": current["page"]} if "page" in current else {}),
            **({"evidence_block_id": current["evidence_block_id"]} if "evidence_block_id" in current else {}),
        }

    return None


def capacitor_default_from_context(packet_dir, req):
    if req.get("packet_type") != "datasheet_current_extraction":
        return None

    ctx = read_json(packet_dir / "context.json")
    refdes = str(req.get("target_refdes") or "")
    value = str(req.get("target_value") or "").lower()

    text = json.dumps({
        "request": req,
        "bom": ctx.get("target_bom_evidence") or ctx.get("target_bom_data") or [],
        "datasheets": ctx.get("datasheet_references") or [],
    }, ensure_ascii=False).lower()

    is_capacitor = (
        refdes.startswith("C")
        or "capacitor" in text
        or "cap_" in text
        or "cap cer" in text
        or "cap_ cer" in text
        or "uf" in value
        or "nf" in value
        or "pf" in value
    )

    if not is_capacitor:
        return None

    is_electrolytic = any(token in text for token in [
        "electrolytic",
        "aluminum electrolytic",
        "aluminium electrolytic",
        "tant",
        "tantalum"
    ])

    if is_electrolytic:
        return {
            "component_class": "electrolytic_capacitor",
            "field_name": "max_current_a",
            "value": 0.000005,
            "unit": "A",
            "condition": "Engineering default for electrolytic capacitor leakage/current draw",
            "source_file": "config/current_model_defaults.json",
            "evidence_quote": "Engineering default: electrolytic capacitor leakage/current draw = 5 µA.",
            "confidence": 0.5,
        }

    return {
        "component_class": "ceramic_capacitor",
        "field_name": "max_current_a",
        "value": 0.00000001,
        "unit": "A",
        "condition": "Engineering default for ceramic capacitor leakage/current draw",
        "source_file": "config/current_model_defaults.json",
        "evidence_quote": "Engineering default: ceramic capacitor leakage/current draw = 10 nA.",
        "confidence": 0.5,
    }


def append_capacitor_default_candidate(packet_dir, req, packet_id, missing_ids, target_type, target_refdes, target_mpn, extracted_items):
    if not missing_ids:
        return

    if target_type != "component_current_model":
        return

    if any(
        item.get("target_type") == "component_current_model"
        and item.get("field_name") in {"max_current_a", "typ_current_a"}
        for item in extracted_items
    ):
        return

    default = capacitor_default_from_context(packet_dir, req)
    if default is None:
        return

    extracted_items.append(make_extracted_item(
        packet_id,
        missing_ids[0],
        "component_current_model",
        target_refdes,
        target_mpn,
        default["field_name"],
        default["value"],
        default["unit"],
        default["condition"],
        default["source_file"],
        default["evidence_quote"],
        default["confidence"],
        basis="engineering_default_config",
        _index=next_extracted_index(extracted_items),
    ))


def append_context_rating_candidate(packet_dir, req, packet_id, missing_ids, target_type, target_refdes, target_mpn, extracted_items, unknown_items):
    if not missing_ids:
        return

    if any(
        item.get("target_type") in RATING_TARGET_TYPES
        and item.get("field_name") in {"current_max", "pin_current_max"}
        for item in extracted_items
    ):
        return

    candidates = extract_rating_candidates(packet_dir, req)
    if not candidates:
        return
    rating = candidates[0]

    extracted_items.append(make_extracted_item(
        packet_id,
        missing_ids[0],
        rating["target_type"],
        target_refdes,
        target_mpn,
        rating["field_name"],
        rating["value"],
        rating["unit"],
        rating["condition"],
        rating["source_file"],
        rating["evidence_quote"],
        rating["confidence"],
        basis="datasheet_rating_evidence",
        page=rating.get("page"),
        evidence_block_id=rating.get("evidence_block_id"),
        human_review_needed=rating.get("human_review_needed"),
        _index=next_extracted_index(extracted_items),
    ))
    if target_type != rating["target_type"]:
        unknown_items[:] = [
            item for item in unknown_items
            if not (
                item.get("missing_data_item_id") == missing_ids[0]
                and item.get("field_name") in {"max_current_a", "typ_current_a", "current_max", "pin_current_max"}
            )
        ]
        if not any(
            item.get("missing_data_item_id") == missing_ids[0]
            and item.get("field_name") == "branch_current_a"
            for item in unknown_items
        ):
            unknown_items.append({
                "missing_data_item_id": missing_ids[0],
                "target_type": target_type,
                "target_refdes": target_refdes,
                "target_mpn": target_mpn,
                "field_name": "branch_current_a",
                "reason_code": "requires_manual_requirement",
                "detail": "A datasheet rating/capability was extracted, but actual board branch operating current remains unknown.",
            })


def append_regulator_rating_candidate(packet_dir, req, packet_id, missing_ids, target_type, target_refdes, target_mpn, extracted_items, unknown_items):
    if not missing_ids:
        return

    if any(
        item.get("target_type") == "regulator_rating"
        and item.get("field_name") == "current_max"
        for item in extracted_items
    ):
        return

    rating = regulator_rating_candidate_from_context(packet_dir, req)
    if rating is None:
        return

    extracted_items.append(make_extracted_item(
        packet_id,
        missing_ids[0],
        "regulator_rating",
        target_refdes,
        target_mpn,
        "current_max",
        rating["value"],
        rating["unit"],
        rating["condition"],
        rating["source_file"],
        rating["evidence_quote"],
        rating["confidence"],
        basis="datasheet_rating_evidence",
        page=rating.get("page"),
        evidence_block_id=rating.get("evidence_block_id"),
        _index=next_extracted_index(extracted_items),
    ))
    unknown_items[:] = [
        item for item in unknown_items
        if not (
            item.get("missing_data_item_id") == missing_ids[0]
            and item.get("field_name") in {"max_current_a", "typ_current_a", "current_max", "pin_current_max"}
        )
    ]

    if target_type != "regulator_rating" and not any(
        item.get("missing_data_item_id") == missing_ids[0]
        and item.get("field_name") == "branch_current_a"
        for item in unknown_items
    ):
        unknown_items.append({
            "missing_data_item_id": missing_ids[0],
            "target_type": target_type,
            "target_refdes": target_refdes,
            "target_mpn": target_mpn,
            "field_name": "branch_current_a",
            "reason_code": "requires_manual_requirement",
            "detail": "A regulator output-current capability was extracted, but actual board branch operating current remains unknown.",
        })


def append_ic_current_candidate(packet_dir, req, packet_id, missing_ids, target_type, target_refdes, target_mpn, extracted_items, unknown_items):
    if not missing_ids:
        return
    if target_type != "component_current_model":
        return
    if any(
        item.get("target_type") == "component_current_model"
        and item.get("field_name") in {"max_current_a", "typ_current_a"}
        for item in extracted_items
    ):
        return

    current = extract_ic_supply_current_from_context(packet_dir, req)
    if current is None:
        return

    extracted_items.append(make_extracted_item(
        packet_id,
        missing_ids[0],
        current["target_type"],
        target_refdes,
        target_mpn,
        current["field_name"],
        current["value"],
        current["unit"],
        current["condition"],
        current["source_file"],
        current["evidence_quote"],
        current["confidence"],
        basis="datasheet_supply_current_evidence",
        page=current.get("page"),
        evidence_block_id=current.get("evidence_block_id"),
        _index=next_extracted_index(extracted_items),
    ))
    unknown_items[:] = [
        item for item in unknown_items
        if not (
            item.get("missing_data_item_id") == missing_ids[0]
            and item.get("target_type") == "component_current_model"
            and item.get("field_name") in {"max_current_a", "typ_current_a"}
        )
    ]
    if not any(
        item.get("missing_data_item_id") == missing_ids[0]
        and item.get("field_name") == "branch_current_a"
        for item in unknown_items
    ):
        unknown_items.append({
            "missing_data_item_id": missing_ids[0],
            "target_type": target_type,
            "target_refdes": target_refdes,
            "target_mpn": target_mpn,
            "field_name": "branch_current_a",
            "reason_code": "requires_manual_requirement",
            "detail": "Datasheet supply current was extracted, but it does not resolve branch_current_a for this board branch.",
        })


def context_text_for_unknown_reason(packet_dir):
    ctx = read_json(packet_dir / "context.json")
    return json.dumps(ctx.get("datasheet_references") or [], ensure_ascii=False).lower()


def precise_unknown_detail(packet_dir, field_name, extracted_items):
    if field_name == "branch_current_a":
        if any(item.get("target_type") == "component_current_model" for item in extracted_items):
            return "Datasheet supply current was extracted, but it does not resolve branch_current_a for this board branch."
        if any(item.get("target_type") in RATING_TARGET_TYPES for item in extracted_items):
            return "Provided datasheet evidence contains rating/capability current only, not operating supply current."
        return "No board operating current or branch allocation evidence was provided; branch_current_a remains unknown."
    if field_name in {"max_current_a", "typ_current_a"}:
        text = context_text_for_unknown_reason(packet_dir)
        if any(term in text for term in ("output current", "iout(max)", "current limit", "rated current")):
            return "Provided datasheet evidence contains rating/capability current only, not operating supply current."
        if any(term in text for term in ("figure", "graph", " vs ", "curve", "typical characteristics", "performance characteristics")):
            return "Current evidence was graph-only or ambiguous and was not converted to a deterministic value."
        return "No valid supply-current evidence was found in the packet datasheet context."
    return "No matching evidence was found in the packet context for this field."


def refine_unknown_items(packet_dir, unknown_items, extracted_items):
    for item in unknown_items:
        field_name = item.get("field_name")
        detail = str(item.get("detail") or "")
        if not detail or detail in {"required when status is unknown", "Model marked this item unknown.", "no matched datasheet evidence"}:
            item["detail"] = precise_unknown_detail(packet_dir, field_name, extracted_items)


def convert(packet_dir, easy):
    req = read_json(packet_dir / "request.json")

    packet_id = req["packet_id"]
    missing_ids = get_missing_ids(req)
    target_type = req.get("target_type") or req.get("expected_target_type")
    target_refdes = req.get("target_refdes")
    target_mpn = req.get("target_mpn")

    if easy.get("packet_id") != packet_id:
        raise ValueError(f"packet_id mismatch: {easy.get('packet_id')} != {packet_id}")

    items = easy.get("items")
    if not isinstance(items, dict):
        raise ValueError("items must be an object")

    if set(items.keys()) != set(missing_ids):
        raise ValueError(f"items keys mismatch: got {sorted(items.keys())}, expected {missing_ids}")

    extracted_items = []
    unknown_items = []

    for mid in missing_ids:
        item = items[mid]
        if not isinstance(item, dict):
            raise ValueError(f"items[{mid}] must be an object")

        status = item.get("status")
        item_target_type = item.get("target_type") or target_type
        field_name = item.get("field_name") or "branch_current_a"
        if (
            req.get("packet_type") == "datasheet_current_extraction"
            and status == "unknown"
            and field_name == "branch_current_a or current_max"
        ):
            field_name = "branch_current_a"

        if item_target_type in FORBIDDEN_TARGET_TYPES:
            raise ValueError(f"bad semantic target_type: {item_target_type}")

        if status == "extracted":
            for key in ["value", "unit", "source_file", "evidence_quote"]:
                if item.get(key) in [None, ""]:
                    raise ValueError(f"extracted item {mid} missing {key}")

            extracted_items.append(make_extracted_item(
                packet_id,
                mid,
                item_target_type,
                target_refdes,
                target_mpn,
                field_name,
                item.get("value"),
                item.get("unit"),
                item.get("condition"),
                item.get("source_file"),
                item.get("evidence_quote"),
                item.get("confidence", 0.6),
                basis=item.get("basis") or "model_extracted_from_packet_evidence",
                page=item.get("page") or item.get("source_page"),
                evidence_block_id=item.get("evidence_block_id"),
                human_review_needed=item.get("human_review_needed"),
                _index=next_extracted_index(extracted_items),
            ))

            if (
                req.get("packet_type") == "datasheet_current_extraction"
                and item_target_type in RATING_TARGET_TYPES
                and target_type != item_target_type
            ):
                unknown_items.append({
                    "missing_data_item_id": mid,
                    "target_type": target_type,
                    "target_refdes": target_refdes,
                    "target_mpn": target_mpn,
                    "field_name": "branch_current_a",
                    "reason_code": "requires_manual_requirement",
                    "detail": "A datasheet rating/capability was extracted, but actual board branch operating current remains unknown.",
                })

        elif status == "unknown":
            unknown_items.append({
                "missing_data_item_id": mid,
                "target_type": item_target_type,
                "target_refdes": target_refdes,
                "target_mpn": target_mpn,
                "field_name": field_name,
                "reason_code": "not_found_in_provided_context",
                "detail": item.get("reason") or "Model marked this item unknown.",
            })

        else:
            raise ValueError(f"bad status for {mid}: {status}")

    append_context_rating_candidate(
        packet_dir,
        req,
        packet_id,
        missing_ids,
        target_type,
        target_refdes,
        target_mpn,
        extracted_items,
        unknown_items,
    )

    append_regulator_rating_candidate(
        packet_dir,
        req,
        packet_id,
        missing_ids,
        target_type,
        target_refdes,
        target_mpn,
        extracted_items,
        unknown_items,
    )

    append_ic_current_candidate(
        packet_dir,
        req,
        packet_id,
        missing_ids,
        target_type,
        target_refdes,
        target_mpn,
        extracted_items,
        unknown_items,
    )

    append_capacitor_default_candidate(
        packet_dir,
        req,
        packet_id,
        missing_ids,
        target_type,
        target_refdes,
        target_mpn,
        extracted_items,
    )

    refine_unknown_items(packet_dir, unknown_items, extracted_items)

    return {
        "packet_id": packet_id,
        "schema_version": "ai_extraction_result_v1",
        "status": "completed",
        "notes": [],
        "warnings": [],
        "extracted_items": extracted_items,
        "unknown_items": unknown_items,
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--packet-list", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--raw-dir", required=True)
    ap.add_argument("--max-tokens", type=int, default=2048)
    args = ap.parse_args()

    base_url = os.environ["OPENAI_BASE_URL"]
    api_key = os.environ.get("OPENAI_API_KEY", "lm-studio")
    model = os.environ["MODEL"]

    out_dir = Path(args.out_dir)
    raw_dir = Path(args.raw_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    packet_dirs = [
        Path(x.strip())
        for x in Path(args.packet_list).read_text().splitlines()
        if x.strip()
    ]

    failed_packets = []

    for packet_dir in packet_dirs:
        req = read_json(packet_dir / "request.json")
        packet_id = req["packet_id"]

        print("GENERATING", packet_id, flush=True)

        messages = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": build_prompt(packet_dir)},
        ]

        last_error = None

        for attempt in range(1, 4):
            try:
                content = chat(base_url, api_key, model, messages, args.max_tokens)
                raw_attempt_path = raw_dir / f"{packet_id}.attempt{attempt}.raw.txt"
                raw_attempt_path.write_text(content or "", encoding="utf-8")
                print("  raw_len=", len(content or ""), "raw_head=", repr((content or "")[:300]), flush=True)

                easy = parse_json(content or "")
                converted = convert(packet_dir, easy)

                (raw_dir / f"{packet_id}.easy.json").write_text(
                    json.dumps(easy, indent=2, ensure_ascii=False) + "\n"
                )

                (out_dir / f"{packet_id}.json").write_text(
                    json.dumps(converted, indent=2, ensure_ascii=False) + "\n"
                )

                print(
                    "  wrote",
                    out_dir / f"{packet_id}.json",
                    "extracted=",
                    len(converted["extracted_items"]),
                    "unknown=",
                    len(converted["unknown_items"]),
                    flush=True,
                )
                break

            except Exception as e:
                last_error = e
                print(f"  attempt {attempt} failed: {e}", flush=True)
                messages.append({
                    "role": "user",
                    "content": f"Invalid EASY_JSON_V1: {e}. Return corrected JSON only."
                })
                time.sleep(1)
        else:
            failed_packets.append({"packet_id": packet_id, "error": str(last_error)})
            (raw_dir / f"{packet_id}.failed.json").write_text(
                json.dumps(
                    {
                        "packet_id": packet_id,
                        "status": "failed",
                        "error": str(last_error),
                        "attempts": 3,
                    },
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            print(f"  FAILED {packet_id}: {last_error}", flush=True)

    summary = {
        "packet_count": len(packet_dirs),
        "failed_packet_count": len(failed_packets),
        "failed_packets": failed_packets,
    }
    (raw_dir / "batch-summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if failed_packets:
        raise SystemExit(1)

if __name__ == "__main__":
    main()
