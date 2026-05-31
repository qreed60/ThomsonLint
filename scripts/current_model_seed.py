#!/usr/bin/env python3
"""Create a deterministic current model seed or manual-entry template.

This script prepares PR19 input without inferring current. If an explicit
current model is supplied it is copied into the run directory. Otherwise it
emits an empty current model plus manual-review placeholders that downstream
ingest ignores for allocation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "1.0"
CURRENT_ARRAYS = ("branch_currents", "rail_currents", "component_currents", "ratings")


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


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def safe_id(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")
    return text or "unknown"


def first_string(row: dict[str, Any], keys: list[str]) -> str | None:
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def collect_dicts(value: Any, limit: int = 400) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if len(found) >= limit:
            return
        if isinstance(node, dict):
            found.append(node)
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return found


def load_optional(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    loaded = load_json(path)
    return loaded if isinstance(loaded, dict) else None


def dedupe_placeholders(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (str(row.get("target_type")), str(row.get("target_identity")))
        by_key.setdefault(key, row)
    return [by_key[key] for key in sorted(by_key)]


def placeholder(
    *,
    project: str,
    target_type: str,
    target_identity: str,
    source_artifact: str,
    source_field: str,
    reason: str = "missing_explicit_current_model",
) -> dict[str, Any]:
    return {
        "placeholder_id": f"manual_current_{safe_id(target_type)}_{safe_id(target_identity)}",
        "project": project,
        "target_type": target_type,
        "target_identity": target_identity,
        "parameter_name": "current",
        "value_raw": None,
        "value_normalized": None,
        "unit_raw": None,
        "unit_normalized": None,
        "current_status": "unknown",
        "reason": reason,
        "source_artifact": source_artifact,
        "source_field": source_field,
        "requires_human_review": True,
        "usable_for_allocation": False,
        "usable_for_direct_ingestion": False,
        "unknown_current_treated_as_zero": False,
    }


def placeholders_from_context(
    *,
    project: str,
    branch_topology: dict[str, Any] | None,
    role_resolution: dict[str, Any] | None,
    rail_relationships: dict[str, Any] | None,
    manifest: dict[str, Any] | None,
    readiness: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if branch_topology:
        for item in collect_dicts(branch_topology):
            branch_id = first_string(item, ["branch_id", "id", "branch_key"])
            if branch_id:
                rows.append(placeholder(project=project, target_type="branch", target_identity=branch_id, source_artifact="branch_topology_enriched", source_field="branch_id"))
            rail_name = first_string(item, ["rail_name", "rail", "net_name", "power_net"])
            if rail_name:
                rows.append(placeholder(project=project, target_type="rail", target_identity=rail_name, source_artifact="branch_topology_enriched", source_field="rail_name"))
            refdes = first_string(item, ["refdes", "reference", "component_refdes"])
            if refdes:
                rows.append(placeholder(project=project, target_type="component", target_identity=refdes, source_artifact="branch_topology_enriched", source_field="refdes"))
    if rail_relationships:
        for item in collect_dicts(rail_relationships):
            rail_name = first_string(item, ["rail_name", "rail", "source_rail", "target_rail", "input_rail", "output_rail"])
            if rail_name:
                rows.append(placeholder(project=project, target_type="rail", target_identity=rail_name, source_artifact="rail_relationships", source_field="rail_name"))
    if role_resolution:
        for item in collect_dicts(role_resolution):
            refdes = first_string(item, ["refdes", "reference", "component_refdes"])
            if refdes:
                rows.append(placeholder(project=project, target_type="component", target_identity=refdes, source_artifact="role_resolution", source_field="refdes"))
    for artifact_name, artifact in (("missing_data_manifest", manifest), ("calculation_readiness", readiness)):
        if not artifact:
            continue
        for item in collect_dicts(artifact):
            category = str(item.get("category") or item.get("missing_data_type") or "")
            if "current" not in category.lower():
                continue
            target = first_string(item, ["target_id", "normalized_target", "branch_id", "rail_name", "refdes"])
            target_type = first_string(item, ["target_type"]) or "unknown"
            if target:
                rows.append(placeholder(project=project, target_type=target_type, target_identity=target, source_artifact=artifact_name, source_field="target_id"))
    return dedupe_placeholders(rows)


def explicit_numeric_record_count(model: dict[str, Any]) -> int:
    count = 0
    for key in CURRENT_ARRAYS:
        for row in as_list(model.get(key)):
            if not isinstance(row, dict):
                continue
            for value_key in ("branch_current_a", "rail_current_a", "typ_current_a", "max_current_a", "value", "current"):
                if is_number(row.get(value_key)):
                    count += 1
                    break
    return count


def ensure_current_arrays(model: dict[str, Any]) -> dict[str, Any]:
    copied = dict(model)
    for key in CURRENT_ARRAYS:
        if not isinstance(copied.get(key), list):
            copied[key] = []
    return copied


def build_template(project: str, placeholders: list[dict[str, Any]], reason: str) -> dict[str, Any]:
    return {
        "artifact_type": "current_model_template",
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": utc_now(),
        "project": project,
        "reason": reason,
        "instructions": "Fill explicit current arrays only from reviewed source evidence. Leave unknown currents blank; unknown is not zero.",
        "branch_currents": [],
        "rail_currents": [],
        "component_currents": [],
        "ratings": [],
        "manual_review_placeholders": placeholders,
        **safety_fields(placeholders=placeholders, explicit_numeric_count=0),
    }


def safety_fields(*, placeholders: list[dict[str, Any]], explicit_numeric_count: int) -> dict[str, Any]:
    return {
        "generated_current_values": False,
        "inferred_current_values": False,
        "unknown_current_treated_as_zero": False,
        "requires_human_review": bool(placeholders),
        "safe_for_allocation": explicit_numeric_count > 0,
        "safe_for_core_apply": False,
        "ready_for_core_apply": False,
    }


def build_seed(
    *,
    project: str,
    existing_current_model: Path | None,
    placeholders: list[dict[str, Any]],
) -> tuple[dict[str, Any], str, int]:
    now = utc_now()
    if existing_current_model is not None and existing_current_model.exists():
        loaded = load_json(existing_current_model)
        if not isinstance(loaded, dict):
            raise ValueError(f"existing current model must be a JSON object: {existing_current_model}")
        seed = ensure_current_arrays(loaded)
        numeric_count = explicit_numeric_record_count(seed)
        seed.setdefault("project", project)
        seed["current_model_seed_metadata"] = {
            "artifact_type": "current_model_seed",
            "schema_version": SCHEMA_VERSION,
            "generated_at_utc": now,
            "project": project,
            "source": "existing_current_model",
            "source_file": str(existing_current_model),
            "source_sha256": sha256_file(existing_current_model),
            "manual_review_placeholder_count": len(placeholders),
            **safety_fields(placeholders=placeholders, explicit_numeric_count=numeric_count),
        }
        return seed, "existing_current_model", numeric_count

    seed = {
        "artifact_type": "current_model_seed",
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": now,
        "project": project,
        "source": "manual_review_seed",
        "reason": "missing_explicit_current_model",
        "branch_currents": [],
        "rail_currents": [],
        "component_currents": [],
        "ratings": [],
        "manual_review_placeholders": placeholders,
        **safety_fields(placeholders=placeholders, explicit_numeric_count=0),
    }
    return seed, "missing_explicit_current_model", 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a current model seed/template without inferring current.")
    parser.add_argument("--project", required=True)
    parser.add_argument("--branch-topology-enriched", required=True)
    parser.add_argument("--role-resolution")
    parser.add_argument("--rail-relationships")
    parser.add_argument("--missing-data-manifest")
    parser.add_argument("--calculation-readiness")
    parser.add_argument("--existing-current-model")
    parser.add_argument("--out", required=True)
    parser.add_argument("--template-out")
    parser.add_argument("--status-out")
    parser.add_argument("--blockers-out")
    parser.add_argument("--review-out")
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    branch_path = Path(args.branch_topology_enriched)
    out = Path(args.out)
    template_out = Path(args.template_out) if args.template_out else out.with_name("current-model-template.json")
    status_out = Path(args.status_out) if args.status_out else out.with_name("current-model-seed-status.json")
    blockers_out = Path(args.blockers_out) if args.blockers_out else out.with_name("current-model-seed-blockers.json")
    review_out = Path(args.review_out) if args.review_out else out.with_name("current-model-seed-review.json")
    existing_path = Path(args.existing_current_model) if args.existing_current_model else None

    try:
        if not branch_path.exists():
            raise FileNotFoundError(f"missing branch topology enriched input: {branch_path}")
        branch = load_optional(branch_path)
        placeholders = placeholders_from_context(
            project=args.project,
            branch_topology=branch,
            role_resolution=load_optional(Path(args.role_resolution)) if args.role_resolution else None,
            rail_relationships=load_optional(Path(args.rail_relationships)) if args.rail_relationships else None,
            manifest=load_optional(Path(args.missing_data_manifest)) if args.missing_data_manifest else None,
            readiness=load_optional(Path(args.calculation_readiness)) if args.calculation_readiness else None,
        )
        seed, reason, numeric_count = build_seed(project=args.project, existing_current_model=existing_path, placeholders=placeholders)
        template = build_template(args.project, placeholders, reason)
        safety = safety_fields(placeholders=placeholders, explicit_numeric_count=numeric_count)
        blockers: list[dict[str, Any]] = []
        if args.strict and reason == "missing_explicit_current_model":
            blockers.append({
                "blocker_id": "current_model_seed_missing_explicit_current_model",
                "reason": "strict mode requires an explicit current model; the seed did not infer current",
            })
        status = {
            "artifact_type": "current_model_seed_status",
            "schema_version": SCHEMA_VERSION,
            "generated_at_utc": utc_now(),
            "project": args.project,
            "status": "blocked" if blockers else "passed",
            "reason": reason,
            "source": "existing_current_model" if reason == "existing_current_model" else "manual_review_seed",
            "explicit_numeric_record_count": numeric_count,
            "manual_review_placeholder_count": len(placeholders),
            **safety,
        }
        blocker_artifact = {
            "artifact_type": "current_model_seed_blockers",
            "schema_version": SCHEMA_VERSION,
            "generated_at_utc": utc_now(),
            "project": args.project,
            "blockers": blockers,
            **safety,
        }
        review = {
            "artifact_type": "current_model_seed_review",
            "schema_version": SCHEMA_VERSION,
            "generated_at_utc": utc_now(),
            "project": args.project,
            "review_items": placeholders,
            "review_item_count": len(placeholders),
            "reason": "manual current values must be supplied from explicit evidence before allocation can use them",
            **safety,
        }
        write_json(out, seed)
        write_json(template_out, template)
        write_json(status_out, status)
        write_json(blockers_out, blocker_artifact)
        write_json(review_out, review)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(
        "current model seed: "
        f"source={status['source']} explicit_numeric={numeric_count} "
        f"placeholders={len(placeholders)} safe_for_allocation={status['safe_for_allocation']} "
        f"out={out}"
    )
    return 1 if blockers else 0


if __name__ == "__main__":
    raise SystemExit(main())
