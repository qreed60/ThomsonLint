#!/usr/bin/env python3
from __future__ import annotations

import argparse
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

    for packet_dir in packet_dirs:
        req = json.loads((packet_dir / "request.json").read_text())
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

        for section_name, items in (("extracted_items", extracted), ("unknown_items", unknown)):
            if not isinstance(items, list):
                continue

            for i, item in enumerate(items):
                if not isinstance(item, dict):
                    print("ERROR", packet_id, section_name, i, "not object")
                    failed = True
                    continue

                target_type = item.get("target_type")
                if target_type != req.get("target_type"):
                    print("ERROR", packet_id, section_name, i, "target_type mismatch", target_type)
                    failed = True

                if target_type in SEMANTIC_TARGET_TYPES:
                    print("ERROR", packet_id, section_name, i, "semantic target_type", target_type)
                    failed = True

                if section_name == "unknown_items":
                    rc = item.get("reason_code")
                    if rc not in ALLOWED_REASON_CODES:
                        print("ERROR", packet_id, section_name, i, "bad reason_code", rc)
                        failed = True

    response_files = sorted(p.stem for p in responses_dir.glob("*.json"))
    extra = sorted(set(response_files) - set(expected_ids))
    missing = sorted(set(expected_ids) - set(response_files))

    print()
    print("expected_packet_count", len(expected_ids))
    print("response_count", len(response_files))
    print("missing_response_ids", missing)
    print("extra_response_files", extra)
    print("response_preflight_pass", not failed and not missing and not extra)

    return 1 if failed or missing or extra else 0

if __name__ == "__main__":
    raise SystemExit(main())
