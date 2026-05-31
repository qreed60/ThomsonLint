#!/usr/bin/env python3
"""Import externally prepared raw AI packet responses.

This script does not call AI services and does not create response content. It
only matches existing JSON response files to PR26 packets, copies them into the
packet directory layout expected by PR27, and records provenance.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "1.0"
SAFETY_FLAGS = {
    "imported_only": True,
    "generated_ai_content": False,
    "called_ai_service": False,
    "fabricated_response": False,
    "applied_to_core": False,
    "safe_for_core_apply": False,
    "ready_for_core_apply": False,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def packet_rows(packet_dir: Path) -> list[dict[str, Any]]:
    queue_path = packet_dir / "packet_queue.json"
    if not queue_path.exists():
        raise FileNotFoundError(f"missing packet_queue.json: {queue_path}")
    queue = load_json(queue_path)
    if not isinstance(queue, dict):
        raise ValueError(f"packet_queue.json must be a JSON object: {queue_path}")
    rows = [row for row in queue.get("packets", []) if isinstance(row, dict) and row.get("packet_id")]
    return sorted(rows, key=lambda row: str(row["packet_id"]))


def packet_index(packets: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row["packet_id"]): row for row in packets}


def response_files(responses_dir: Path, response_glob: str) -> list[Path]:
    if not responses_dir.exists():
        raise FileNotFoundError(f"missing responses directory: {responses_dir}")
    return sorted(path for path in responses_dir.rglob("*") if path.is_file() and fnmatch.fnmatch(path.name, response_glob))


def match_from_name(path: Path, ordered_packet_ids: list[str]) -> str | None:
    stem = path.stem
    parent = path.parent.name
    if path.name == "raw_response.json" and parent in ordered_packet_ids:
        return parent
    if stem in ordered_packet_ids:
        return stem
    for packet_id in ordered_packet_ids:
        if stem == f"{packet_id}_raw_response":
            return packet_id
    match = re.fullmatch(r"packet_(\d+)_response", stem)
    if match:
        index = int(match.group(1)) - 1
        if 0 <= index < len(ordered_packet_ids):
            return ordered_packet_ids[index]
    return None


def match_response(path: Path, ordered_packet_ids: list[str]) -> tuple[str | None, bool, str, Any | None]:
    try:
        loaded = load_json(path)
    except Exception as exc:
        return None, False, f"invalid_json: {exc}", None
    name_match = match_from_name(path, ordered_packet_ids)
    payload_match = str(loaded.get("packet_id")) if isinstance(loaded, dict) and loaded.get("packet_id") else None
    matched = payload_match or name_match
    if payload_match and name_match and payload_match != name_match:
        return payload_match, True, f"packet_id_name_mismatch: filename maps to {name_match}", loaded
    if matched is None:
        return None, True, "could_not_match_packet_id", loaded
    return matched, True, "matched", loaded


def inside(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def import_responses(
    *,
    project: str,
    packet_dir: Path,
    responses_dir: Path,
    out_dir: Path,
    response_glob: str,
    allow_partial: bool,
    validate_json_only: bool,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], int]:
    packets = packet_rows(packet_dir)
    packets_by_id = packet_index(packets)
    ordered_packet_ids = list(packets_by_id)
    records: list[dict[str, Any]] = []
    review: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    imported_count = 0
    now = utc_now()
    matched_ids: set[str] = set()

    for source in response_files(responses_dir, response_glob):
        matched_packet, json_valid, reason, _payload = match_response(source, ordered_packet_ids)
        status = "rejected"
        destination: Path | None = None
        imported_sha: str | None = None
        if not json_valid:
            review.append({"source_response_file": str(source), "reason": reason, "status": "rejected"})
        elif matched_packet not in packets_by_id:
            review.append({"source_response_file": str(source), "packet_id": matched_packet, "reason": "unknown_packet_id", "status": "rejected"})
            reason = "unknown_packet_id"
        elif matched_packet in matched_ids:
            review.append({"source_response_file": str(source), "packet_id": matched_packet, "reason": "duplicate_response_for_packet", "status": "rejected"})
            reason = "duplicate_response_for_packet"
        else:
            destination = packet_dir / "packets" / str(matched_packet) / "raw_response.json"
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not inside(destination, packet_dir):
                raise ValueError(f"destination escapes packet directory: {destination}")
            shutil.copyfile(source, destination)
            imported_sha = sha256_file(destination)
            matched_ids.add(str(matched_packet))
            imported_count += 1
            status = "imported"
            reason = "matched_packet"
        records.append({
            "packet_id": matched_packet,
            "source_response_file": str(source),
            "destination_response_file": str(destination) if destination else None,
            "source_sha256": sha256_file(source),
            "imported_sha256": imported_sha,
            "json_valid": json_valid,
            "matched_packet": matched_packet in packets_by_id if matched_packet else False,
            "status": status,
            "reason": reason,
            "imported_at_utc": now if status == "imported" else None,
        })

    missing = [packet_id for packet_id in ordered_packet_ids if packet_id not in matched_ids]
    for packet_id in missing:
        row = {"packet_id": packet_id, "reason": "missing_response", "status": "missing"}
        review.append(row)
        if not allow_partial:
            blockers.append({"blocker_id": f"missing_response_{packet_id}", **row})

    if any(row["reason"] == "unknown_packet_id" for row in records):
        blockers.append({"blocker_id": "unknown_packet_response_file", "reason": "one or more response files did not match a packet"})
    if any(row["json_valid"] is False for row in records):
        blockers.append({"blocker_id": "invalid_response_json", "reason": "one or more response files were not valid JSON"})

    status_value = "blocked" if blockers else "passed"
    summary = {
        "packet_count": len(ordered_packet_ids),
        "source_response_file_count": len(records),
        "imported_response_count": imported_count,
        "missing_response_count": len(missing),
        "review_item_count": len(review),
        "blocker_count": len(blockers),
        "allow_partial": allow_partial,
        "validate_json_only": validate_json_only,
    }
    base = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": now,
        "project": project,
        **SAFETY_FLAGS,
    }
    manifest = {
        **base,
        "artifact_type": "ai_packet_response_import_manifest",
        "packet_dir": str(packet_dir),
        "responses_dir": str(responses_dir),
        "out_dir": str(out_dir),
        "response_glob": response_glob,
        "summary": summary,
        "imported_responses": records,
    }
    status = {
        **base,
        "artifact_type": "ai_packet_response_import_status",
        "status": status_value,
        "summary": summary,
        "reason": "responses imported" if not blockers else "response import blocked",
    }
    blocker_artifact = {
        **base,
        "artifact_type": "ai_packet_response_import_blockers",
        "blockers": blockers,
    }
    review_artifact = {
        **base,
        "artifact_type": "ai_packet_response_import_review",
        "review_items": review,
    }
    index = {
        **base,
        "artifact_type": "ai_packet_response_import_index",
        "responses": records,
    }
    return manifest, status, blocker_artifact, review_artifact, index, 0 if not blockers else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import externally prepared raw AI packet response files.")
    parser.add_argument("--project", required=True)
    parser.add_argument("--packet-dir", required=True)
    parser.add_argument("--responses-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--response-glob", default="*.json")
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--validate-json-only", action="store_true")
    parser.add_argument("--status-out")
    parser.add_argument("--manifest-out")
    parser.add_argument("--blockers-out")
    parser.add_argument("--review-out")
    parser.add_argument("--schema")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    out_dir = Path(args.out_dir)
    manifest_out = Path(args.manifest_out) if args.manifest_out else out_dir / "ai-packet-response-import-manifest.json"
    status_out = Path(args.status_out) if args.status_out else out_dir / "ai-packet-response-import-status.json"
    blockers_out = Path(args.blockers_out) if args.blockers_out else out_dir / "ai-packet-response-import-blockers.json"
    review_out = Path(args.review_out) if args.review_out else out_dir / "ai-packet-response-import-review.json"
    index_out = out_dir / "ai-packet-response-import-index.json"
    try:
        manifest, status, blockers, review, index, return_code = import_responses(
            project=args.project,
            packet_dir=Path(args.packet_dir),
            responses_dir=Path(args.responses_dir),
            out_dir=out_dir,
            response_glob=args.response_glob,
            allow_partial=args.allow_partial,
            validate_json_only=args.validate_json_only,
        )
        write_json(manifest_out, manifest)
        write_json(status_out, status)
        write_json(blockers_out, blockers)
        write_json(review_out, review)
        write_json(index_out, index)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    summary = status["summary"]
    print(
        "ai packet response import: "
        f"packets={summary['packet_count']} sources={summary['source_response_file_count']} "
        f"imported={summary['imported_response_count']} missing={summary['missing_response_count']} "
        f"blockers={summary['blocker_count']} out_dir={out_dir}"
    )
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
