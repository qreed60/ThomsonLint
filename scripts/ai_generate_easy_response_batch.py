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
                "field_name": "branch_current_a or current_max",
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
- items must be an object, not a list
- items must contain exactly these keys: {missing_ids}
- each item value must be an object, not a string
- status must be "extracted" or "unknown"
- if extracted: value, unit, source_file, and evidence_quote are required
- if unknown: value must be null and reason is required
- do not use semantic target_type words like connector, mosfet, capacitor, resistor, regulator, fuse, ic, diode
- do not claim datasheet ratings are board operating current
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

        if item_target_type in FORBIDDEN_TARGET_TYPES:
            raise ValueError(f"bad semantic target_type: {item_target_type}")

        if status == "extracted":
            for key in ["value", "unit", "source_file", "evidence_quote"]:
                if item.get(key) in [None, ""]:
                    raise ValueError(f"extracted item {mid} missing {key}")

            extracted_items.append({
                "missing_data_item_id": mid,
                "target_type": item_target_type,
                "target_refdes": target_refdes,
                "target_mpn": target_mpn,
                "field_name": field_name,
                "value": item.get("value"),
                "unit": item.get("unit"),
                "condition": item.get("condition"),
                "source_file": item.get("source_file"),
                "evidence_quote": item.get("evidence_quote"),
                "confidence": item.get("confidence", 0.6),
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
            raise SystemExit(f"FAILED {packet_id}: {last_error}")

if __name__ == "__main__":
    main()
