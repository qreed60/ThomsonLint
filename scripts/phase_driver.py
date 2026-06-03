#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ensure_phase_checkpoint import PHASE_ARTIFACTS as FULL_PLAN_PHASE_ARTIFACTS
from ensure_phase_checkpoint import PHASES as FULL_PLAN_PHASES
from topology_ai_phase_registry import PhaseSpec, TopologyPaths, _missing_manifest, selected_phases


WORKFLOW = "topology_ai"
FULL_PLAN_WORKFLOW = "full_plan"
SAFETY_FLAGS = {
    "workflow_run_only": True,
    "wrote_core_artifacts": False,
    "applied_promotions_to_core": False,
    "merged_addenda": False,
    "ran_post_promotion_allocation": False,
    "ran_post_promotion_calculations": False,
    "safe_for_core_apply": False,
    "ready_for_core_apply": False,
}
FULL_PLAN_SAFETY_FLAGS = {
    "workflow_run_only": True,
    "wrote_core_artifacts": False,
    "safe_for_core_apply": False,
    "ready_for_core_apply": False,
    "topology_ai_is_subsystem": True,
    "ran_topology_ai_subsystem": False,
    "ran_allocation": False,
    "ran_copper": False,
    "ran_voltage_drop": False,
    "ran_thermal": False,
    "ran_margin": False,
    "ran_calculations": False,
}
REQUIRED_OUTPUTS = (
    "phase-driver-manifest.json",
    "phase-driver-status.json",
    "phase-driver-stage-results.json",
    "phase-driver-artifact-index.json",
    "phase-driver-blockers.json",
    "phase-driver-inspection-commands.md",
)
FULL_PLAN_REQUIRED_OUTPUTS = (
    "full-plan-driver-manifest.json",
    "full-plan-driver-status.json",
    "full-plan-driver-stage-results.json",
    "full-plan-driver-blockers.json",
    "full-plan-driver-inspection-commands.md",
)
ISOLATED_OUTPUT_PRS = set(range(26, 38))


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def preview(text: str, limit: int = 2000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[truncated]"


def rel(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or "phase"


def command_text(command: list[str]) -> str:
    return " ".join(str(part) for part in command)


def full_phase_number(value: str | int) -> int:
    try:
        phase = int(str(value).removeprefix("phase"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"full_plan phase must be numeric 1-22: {value}") from exc
    if phase not in FULL_PLAN_PHASES:
        raise argparse.ArgumentTypeError(f"full_plan phase must be in 1-22: {value}")
    return phase


def env_value(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None or value == "":
        return None
    if "KEY" in name or "TOKEN" in name or "SECRET" in name:
        return "[redacted]"
    return value


def model_routing_summary() -> dict[str, Any]:
    vision_model = os.environ.get("VISION_MODEL") or os.environ.get("THOMSONLINT_VISION_MODEL")
    qwen_configured = bool(os.environ.get("QWEN_VISION_MODEL"))
    if not qwen_configured:
        for env_name in ("VISION_MODEL", "THOMSONLINT_VISION_MODEL"):
            val = os.environ.get(env_name)
            if val and "qwen" in val.lower():
                qwen_configured = True
                break
    configured = bool(vision_model and "qwen" in vision_model.lower()) or qwen_configured
    return {
        "LLM_BASE_URL": env_value("LLM_BASE_URL"),
        "LLM_MODEL": env_value("LLM_MODEL"),
        "THOMSONLINT_TEXT_MODEL": env_value("THOMSONLINT_TEXT_MODEL"),
        "VISION_MODEL": env_value("VISION_MODEL"),
        "THOMSONLINT_VISION_MODEL": env_value("THOMSONLINT_VISION_MODEL"),
        "qwen_vision_configured": configured,
        "qwen_vision_invoked": False,
    }


def blocker(blocker_id: str, phase: PhaseSpec, reason: str, paths: list[Path]) -> dict[str, Any]:
    return {
        "blocker_id": blocker_id,
        "workflow": WORKFLOW,
        "phase_id": phase.phase_id,
        "pr_number": phase.pr_number,
        "reason": reason,
        "missing_paths": [str(path) for path in paths],
    }


def stage_record(
    phase: PhaseSpec,
    *,
    command: list[str] | None,
    status: str,
    return_code: int | None,
    stdout: str = "",
    stderr: str = "",
    input_paths: list[Path] | None = None,
    output_paths: list[Path] | None = None,
    reason: str,
    blocker_id: str | None = None,
) -> dict[str, Any]:
    return {
        "workflow": WORKFLOW,
        "phase_id": phase.phase_id,
        "pr_number": phase.pr_number,
        "script": phase.script,
        "command": command or [],
        "status": status,
        "return_code": return_code,
        "stdout_preview": preview(stdout),
        "stderr_preview": preview(stderr),
        "input_paths": [str(path) for path in input_paths or []],
        "output_paths": [str(path) for path in output_paths or []],
        "reason": reason,
        "blocker_id": blocker_id,
        "stage_kind": phase.stage_kind,
    }


def packet_has_raw_responses(packet_dir: Path) -> bool:
    queue_path = packet_dir / "packet_queue.json"
    if not queue_path.exists():
        return False
    return any(packet_dir.glob("packets/*/raw_response.json"))


def existing_validation_artifacts(paths: TopologyPaths) -> bool:
    if paths.fixtures_dir and (paths.fixtures_dir / "ai-extraction-validation.json").exists():
        return True
    return (paths.run_dir / "pr27_ai_extraction_validate" / "ai-extraction-validation.json").exists()


def path_inside(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def assert_outputs_in_run_dir(results: list[dict[str, Any]], run_dir: Path) -> None:
    for result in results:
        for value in result["output_paths"]:
            if value and not path_inside(Path(value), run_dir):
                raise ValueError(f"{result['phase_id']} output escapes workflow run directory: {value}")


def write_post_conversion_index(paths: TopologyPaths, inputs: list[Path], output: Path) -> tuple[str, list[dict[str, Any]]]:
    optional = [
        paths.post_conversion() / f"{paths.project}-thomson-export-stack.json",
        paths.post_conversion() / f"{paths.project}-conversion-report.json",
    ]
    image_count = len(list(paths.post_conversion().glob(f"{paths.project}-img-*.png"))) if paths.post_conversion().exists() else 0
    warnings = [
        {"path": str(path), "reason": "optional post-conversion input missing"}
        for path in optional
        if not path.exists()
    ]
    if image_count == 0:
        warnings.append({"path": str(paths.post_conversion() / f"{paths.project}-img-*.png"), "reason": "optional image inputs missing"})
    artifact = {
        "artifact_type": "post_conversion_source_index",
        "schema_version": "1.0",
        "generated_at_utc": utc_now(),
        "project": paths.project,
        "post_conversion_dir": str(paths.post_conversion()),
        "required_inputs": [{"path": str(path), "exists": path.exists()} for path in inputs],
        "optional_inputs": [{"path": str(path), "exists": path.exists()} for path in optional],
        "image_count": image_count,
        "warnings": warnings,
        "errors": [],
    }
    write_json(output, artifact)
    reason = f"located post-conversion exports; optional_warning_count={len(warnings)}"
    return reason, warnings


def execute_topology_ai(args: argparse.Namespace) -> int:
    root = repo_root()
    selected = selected_phases(args.start, args.end)
    out_dir = Path(args.out_dir) if args.out_dir else root / "exports" / args.project / "phase_runs" / WORKFLOW / run_id()
    if not out_dir.is_absolute():
        out_dir = root / out_dir
    if out_dir.exists() and any(out_dir.iterdir()) and not args.allow_existing_outputs:
        print(f"ERROR: output directory already exists and is not empty: {out_dir}", file=sys.stderr)
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)

    paths = TopologyPaths(
        project=args.project,
        repo_root=root,
        run_dir=out_dir,
        python_executable=sys.executable,
        strict=args.strict,
        dry_run=args.dry_run,
        fixtures_dir=Path(args.fixtures_dir).resolve() if args.fixtures_dir else None,
        responses_dir=Path(args.responses_dir).resolve() if args.responses_dir else None,
        approval_decisions=Path(args.approval_decisions).resolve() if args.approval_decisions else None,
        allow_partial_responses=args.allow_partial_responses,
        bom_path=Path(args.bom).resolve() if args.bom else None,
        schematic_export_path=Path(args.schematic_export).resolve() if args.schematic_export else None,
        datasheets_dir=Path(args.datasheets_dir).resolve() if args.datasheets_dir else None,
        datasheet_manifest_path=Path(args.datasheet_manifest).resolve() if args.datasheet_manifest else None,
        datasheet_index_path=Path(args.datasheet_index).resolve() if args.datasheet_index else None,
        datasheet_evidence_index_path=Path(args.datasheet_evidence_index).resolve() if args.datasheet_evidence_index else None,
        part_info_index_path=Path(args.part_info_index).resolve() if args.part_info_index else None,
    )
    blockers: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    cascade_blocker_id: str | None = None
    cascade_reason: str | None = None

    for phase in selected:
        inputs = phase.input_paths(paths)
        outputs = phase.output_paths(paths)
        command = phase.command(paths)

        if args.dry_run:
            status = "not_applicable" if phase.stage_kind == "schema_check" else "skipped"
            results.append(
                stage_record(
                    phase,
                    command=command,
                    status=status,
                    return_code=None,
                    input_paths=inputs,
                    output_paths=outputs,
                    reason=f"dry-run: stage command was not executed; {phase.reason}" if command else phase.reason,
                )
            )
            continue

        if phase.stage_kind == "schema_check":
            missing = [path for path in inputs if not path.exists()]
            if missing:
                blocker_id = f"{phase.phase_id}_missing_schema"
                blockers.append(blocker(blocker_id, phase, "schema_not_available", missing))
                results.append(
                    stage_record(
                        phase,
                        command=[],
                        status="not_applicable",
                        return_code=None,
                        input_paths=inputs,
                        output_paths=outputs,
                        reason="schema_not_available",
                        blocker_id=blocker_id,
                    )
                )
            else:
                results.append(
                    stage_record(
                        phase,
                        command=[],
                        status="passed",
                        return_code=None,
                        input_paths=inputs,
                        output_paths=outputs,
                        reason="schema_available; no command executed",
                    )
                )
            continue

        if phase.stage_kind == "ai_response_import" and command is None:
            results.append(
                stage_record(
                    phase,
                    command=[],
                    status="not_applicable",
                    return_code=None,
                    input_paths=inputs,
                    output_paths=outputs,
                    reason="no --responses-dir or fixture response directory provided; default PR27 missing-response block remains enabled",
                )
            )
            continue

        if cascade_blocker_id and phase.stage_kind not in {"ai_packet", "ai_response_import", "ai_response_required"}:
            results.append(
                stage_record(
                    phase,
                    command=command,
                    status="skipped",
                    return_code=None,
                    input_paths=inputs,
                    output_paths=outputs,
                    reason=f"skipped because {cascade_reason}",
                    blocker_id=cascade_blocker_id,
                )
            )
            continue

        if phase.stage_kind == "ai_response_required" and not args.continue_with_existing_ai_artifacts:
            if not packet_has_raw_responses(paths.stage_dir("pr26_ai_packet_build")):
                blocker_id = "pr27_missing_raw_ai_response"
                reason = "missing raw AI response artifacts after PR26; driver will not fabricate responses"
                blockers.append(blocker(blocker_id, phase, reason, [paths.stage_dir("pr26_ai_packet_build") / "packets"]))
                results.append(
                    stage_record(
                        phase,
                        command=command,
                        status="blocked_missing_input",
                        return_code=None,
                        input_paths=inputs,
                        output_paths=outputs,
                        reason=reason,
                        blocker_id=blocker_id,
                    )
                )
                cascade_blocker_id = blocker_id
                cascade_reason = reason
                if args.stop_at_missing_ai:
                    break
                continue

        if phase.stage_kind == "ai_response_required" and args.continue_with_existing_ai_artifacts and existing_validation_artifacts(paths):
            results.append(
                stage_record(
                    phase,
                    command=command,
                    status="skipped",
                    return_code=None,
                    input_paths=inputs,
                    output_paths=outputs,
                    reason="continue-with-existing-ai-artifacts: existing validation artifact will be used",
                )
            )
            continue

        missing_inputs = [path for path in inputs if not path.exists()]
        if missing_inputs:
            blocker_id = f"{phase.phase_id}_missing_input"
            reason = "missing required input artifact(s)"
            blockers.append(blocker(blocker_id, phase, reason, missing_inputs))
            results.append(
                stage_record(
                    phase,
                    command=command,
                    status="blocked_missing_input",
                    return_code=None,
                    input_paths=inputs,
                    output_paths=outputs,
                    reason=reason,
                    blocker_id=blocker_id,
                )
            )
            cascade_blocker_id = blocker_id
            cascade_reason = reason
            continue

        if phase.stage_kind == "locate_post_conversion":
            reason, _warnings = write_post_conversion_index(paths, inputs, outputs[0])
            results.append(
                stage_record(
                    phase,
                    command=[],
                    status="passed",
                    return_code=None,
                    input_paths=inputs,
                    output_paths=outputs,
                    reason=reason,
                )
            )
            continue

        # Pre-stage: auto-generate datasheet evidence index before PR26 if datasheets_dir is provided.
        if phase.phase_id == "pr26_ai_packet_build" and paths.datasheets_dir and paths.datasheets_dir.exists():
            evidence_out = paths.run_dir / "pre26_datasheet_evidence"
            evidence_index_path = evidence_out / "datasheet-evidence-index.json"
            if not evidence_index_path.exists() and (paths.datasheet_evidence_index_path is None or not Path(paths.datasheet_evidence_index_path).exists()):
                evidence_cmd = [
                    paths.python_executable,
                    str(paths.repo_root / "scripts" / "datasheet_evidence_index.py"),
                    "--project", paths.project,
                    "--datasheets-dir", str(paths.datasheets_dir),
                    "--out-dir", str(evidence_out),
                ]
                if paths.part_info_index_path and Path(paths.part_info_index_path).exists():
                    evidence_cmd.extend(["--part-info-index", str(paths.part_info_index_path)])
                if _missing_manifest(paths).exists():
                    evidence_cmd.extend(["--missing-data-manifest", str(_missing_manifest(paths))])
                evidence_out.mkdir(parents=True, exist_ok=True)
                completed_evidence = subprocess.run(evidence_cmd, cwd=root, text=True, capture_output=True)
                if completed_evidence.returncode != 0:
                    print(
                        f"WARNING: datasheet evidence index generation failed for {paths.project}: "
                        f"{completed_evidence.stderr.strip() or 'unknown error'}",
                        file=sys.stderr,
                    )
                else:
                    results.append(
                        stage_record(
                            PhaseSpec("pre26_datasheet_evidence_index", 0, "datasheet evidence index generation", None, lambda p: [], lambda p: [evidence_index_path], lambda p: [], "auto-generated datasheet evidence index from --datasheets-dir"),
                            command=evidence_cmd,
                            status="passed" if completed_evidence.returncode == 0 else "failed",
                            return_code=completed_evidence.returncode,
                            input_paths=[paths.datasheets_dir],
                            output_paths=[evidence_index_path],
                            reason=f"auto-generated datasheet evidence index; {len(list(paths.datasheets_dir.glob('*.pdf')))} PDFs found" if paths.datasheets_dir.exists() else "datasheets_dir not available",
                        )
                    )

        for output in outputs:
            output.parent.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(command or [], cwd=root, text=True, capture_output=True)
        if completed.returncode == 0:
            status = "passed"
            reason = phase.reason
        elif phase.stage_kind == "ai_response_import":
            status = "blocked_missing_input"
            reason = f"response import blocked; command returned {completed.returncode}"
        else:
            status = "failed"
            reason = f"command returned {completed.returncode}"
        results.append(
            stage_record(
                phase,
                command=command,
                status=status,
                return_code=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
                input_paths=inputs,
                output_paths=outputs,
                reason=reason,
            )
        )
        if status == "failed":
            cascade_blocker_id = f"{phase.phase_id}_failed"
            cascade_reason = reason
            blockers.append(blocker(cascade_blocker_id, phase, reason, []))
        elif status == "blocked_missing_input":
            cascade_blocker_id = f"{phase.phase_id}_blocked"
            cascade_reason = reason
            blockers.append(blocker(cascade_blocker_id, phase, reason, []))
        elif phase.stage_kind == "ai_response_required":
            cascade_blocker_id = None
            cascade_reason = None

    assert_outputs_in_run_dir(results, out_dir)
    artifacts = write_run_artifacts(args, out_dir, selected, results, blockers)
    print(f"phase driver topology_ai: project={args.project} run_dir={out_dir}")
    for name in REQUIRED_OUTPUTS:
        print(f"wrote {artifacts[name]}")
    return 0 if not any(row["status"] == "failed" for row in results) else 1


def write_run_artifacts(
    args: argparse.Namespace,
    out_dir: Path,
    phases: list[PhaseSpec],
    results: list[dict[str, Any]],
    blockers: list[dict[str, Any]],
) -> dict[str, Path]:
    now = utc_now()
    manifest = {
        "artifact_type": "phase_driver_manifest",
        "schema_version": "1.0",
        "generated_at_utc": now,
        "project": args.project,
        "workflow": WORKFLOW,
        "workflow_run_dir": str(out_dir),
        "phase_range": {"start": phases[0].phase_id if phases else None, "end": phases[-1].phase_id if phases else None},
        "dry_run": args.dry_run,
        "flags": {
            "allow_existing_outputs": args.allow_existing_outputs,
            "fixtures_dir": args.fixtures_dir,
            "responses_dir": args.responses_dir,
            "approval_decisions": args.approval_decisions,
            "allow_partial_responses": args.allow_partial_responses,
            "continue_with_existing_ai_artifacts": args.continue_with_existing_ai_artifacts,
            "stop_at_missing_ai": args.stop_at_missing_ai,
            "strict": args.strict,
        },
        "stage_plan": [{"phase_id": p.phase_id, "pr_number": p.pr_number, "title": p.title, "script": p.script, "stage_kind": p.stage_kind} for p in phases],
        "model_routing_summary": model_routing_summary(),
        **SAFETY_FLAGS,
    }
    status_counts = {status: sum(1 for row in results if row["status"] == status) for status in ["passed", "failed", "skipped", "blocked_missing_input", "not_applicable"]}
    status = {
        "artifact_type": "phase_driver_status",
        "schema_version": "1.0",
        "generated_at_utc": now,
        "project": args.project,
        "workflow": WORKFLOW,
        "workflow_run_dir": str(out_dir),
        "overall_status": "failed" if status_counts["failed"] else ("blocked" if status_counts["blocked_missing_input"] else "passed"),
        "stage_counts": status_counts,
        "blocker_count": len(blockers),
        **SAFETY_FLAGS,
    }
    stage_results = {
        "artifact_type": "phase_driver_stage_results",
        "schema_version": "1.0",
        "generated_at_utc": now,
        "project": args.project,
        "workflow": WORKFLOW,
        "stage_results": results,
        **SAFETY_FLAGS,
    }
    artifact_index = {
        "artifact_type": "phase_driver_artifact_index",
        "schema_version": "1.0",
        "generated_at_utc": now,
        "project": args.project,
        "workflow": WORKFLOW,
        "workflow_run_dir": str(out_dir),
        "artifacts": [
            {
                "phase_id": row["phase_id"],
                "pr_number": row["pr_number"],
                "path": path,
                "exists": Path(path).exists(),
                "inside_workflow_run_dir": path_inside(Path(path), out_dir),
                "is_prerequisite_output": row["phase_id"].startswith("pre"),
            }
            for row in results
            for path in row["output_paths"]
        ],
        **SAFETY_FLAGS,
    }
    blocker_artifact = {
        "artifact_type": "phase_driver_blockers",
        "schema_version": "1.0",
        "generated_at_utc": now,
        "project": args.project,
        "workflow": WORKFLOW,
        "blockers": blockers,
        **SAFETY_FLAGS,
    }

    artifacts = {
        "phase-driver-manifest.json": out_dir / "phase-driver-manifest.json",
        "phase-driver-status.json": out_dir / "phase-driver-status.json",
        "phase-driver-stage-results.json": out_dir / "phase-driver-stage-results.json",
        "phase-driver-artifact-index.json": out_dir / "phase-driver-artifact-index.json",
        "phase-driver-blockers.json": out_dir / "phase-driver-blockers.json",
        "phase-driver-inspection-commands.md": out_dir / "phase-driver-inspection-commands.md",
    }
    write_json(artifacts["phase-driver-manifest.json"], manifest)
    write_json(artifacts["phase-driver-status.json"], status)
    write_json(artifacts["phase-driver-stage-results.json"], stage_results)
    write_json(artifacts["phase-driver-artifact-index.json"], artifact_index)
    write_json(artifacts["phase-driver-blockers.json"], blocker_artifact)
    write_inspection_commands(artifacts["phase-driver-inspection-commands.md"], args.project, out_dir, results)
    return artifacts


def write_inspection_commands(path: Path, project: str, out_dir: Path, results: list[dict[str, Any]]) -> None:
    lines = [
        "# Phase Driver Inspection Commands",
        "",
        f"Project: `{project}`",
        f"Workflow run directory: `{out_dir}`",
        "",
        "```bash",
        f"python -m json.tool {out_dir / 'phase-driver-manifest.json'}",
        f"python -m json.tool {out_dir / 'phase-driver-status.json'}",
        f"python -m json.tool {out_dir / 'phase-driver-stage-results.json'}",
        f"python -m json.tool {out_dir / 'phase-driver-artifact-index.json'}",
        f"python -m json.tool {out_dir / 'phase-driver-blockers.json'}",
        "```",
        "",
        "## Stage Commands",
        "",
    ]
    for row in results:
        command = " ".join(row["command"]) if row["command"] else "(no command)"
        lines.extend([f"### {row['phase_id']}", "", "```bash", command, "```", ""])
        for output in row["output_paths"]:
            lines.extend(["```bash", f"python -m json.tool {output} | head -80", "```", ""])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def full_plan_doc_status(root: Path) -> dict[str, Any]:
    docs = {
        "PLAN.md": root / "PLAN.md",
        "OPENHANDS_REVIEW.md": root / "OPENHANDS_REVIEW.md",
    }
    return {
        name: {
            "path": str(path),
            "exists": path.is_file(),
            "byte_count": path.stat().st_size if path.is_file() else 0,
        }
        for name, path in docs.items()
    }


def selected_full_plan_phases(start: int, end: int) -> list[int]:
    if end < start:
        raise ValueError("full_plan --end must be greater than or equal to --start")
    return list(range(start, end + 1))


def checkpoint_file(root: Path, project: str) -> Path:
    return root / "exports" / f"{project}-phase-checkpoints.jsonl"


def load_checkpoint_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        text = line.strip()
        if not text:
            continue
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            rows.append({"phase_number": None, "phase_passed": False, "_invalid_json_line": line_number})
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def checkpoint_rows_for_phase(rows: list[dict[str, Any]], phase: int) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("phase_number") == phase]


def exactly_one_passed_checkpoint(rows: list[dict[str, Any]], phase: int) -> bool:
    phase_rows = checkpoint_rows_for_phase(rows, phase)
    return len(phase_rows) == 1 and phase_rows[0].get("phase_passed") is True


def full_plan_phase_dir(out_dir: Path, phase: int, stamp: str) -> Path:
    return out_dir / f"phase{phase:02d}_{slugify(FULL_PLAN_PHASES[phase])}_{stamp}"


def full_plan_prompt_command(root: Path, project: str, phase: int, prompt_path: Path) -> list[str]:
    return [
        sys.executable,
        str(root / "scripts" / "write_phase_prompt.py"),
        "--project",
        project,
        "--phase",
        str(phase),
        "--out",
        str(prompt_path),
    ]


def full_plan_runner_command(root: Path, project: str, phase: int, runner: str) -> list[str]:
    if runner == "openhands":
        return [str(root / "scripts" / "run_openhands_phase.sh"), str(phase), project]
    if runner == "codex":
        return ["codex", "exec", "--project", project, "--phase", str(phase)]
    return []


def full_plan_validation_commands(root: Path, project: str, phase: int) -> dict[str, list[str]]:
    exports_arg = str(root / "exports")
    return {
        "checkpoint_inspection": [
            sys.executable,
            str(root / "scripts" / "ensure_phase_checkpoint.py"),
            "--project",
            project,
            "--phase",
            str(phase),
            "--exports",
            exports_arg,
        ],
        "audit": [
            sys.executable,
            str(root / "scripts" / "audit_phase.py"),
            "--project",
            project,
            "--phase",
            str(phase),
            "--exports",
            exports_arg,
        ],
    }


def full_plan_topology_ai_subsystem_command(root: Path, project: str, phase: int, out_dir: Path) -> list[str]:
    if phase != 12:
        return []
    return [
        sys.executable,
        str(root / "scripts" / "phase_driver.py"),
        project,
        "--workflow",
        WORKFLOW,
        "--out-dir",
        str(out_dir / "topology_ai_subsystem"),
        "--dry-run",
    ]


def full_plan_stage_plan(root: Path, project: str, run_id_value: str | None, out_dir: Path, phases: list[int], stamp: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for phase in phases:
        phase_dir = full_plan_phase_dir(out_dir, phase, stamp)
        prompt_path = phase_dir / f"phase{phase:02d}_prompt.md"
        validation = full_plan_validation_commands(root, project, phase)
        rows.append(
            {
                "workflow": FULL_PLAN_WORKFLOW,
                "run_id": run_id_value,
                "phase_number": phase,
                "phase_name": FULL_PLAN_PHASES[phase],
                "phase_output_dir": str(phase_dir),
                "prompt_path": str(prompt_path),
                "prompt_command": full_plan_prompt_command(root, project, phase, prompt_path),
                "runner_command": [],
                "validation_commands": validation,
                "required_artifacts": [
                    artifact.format(project=project) for artifact in FULL_PLAN_PHASE_ARTIFACTS.get(phase, [])
                ],
                "may_write_findings": phase >= 19,
                "may_generate_report": phase >= 21,
                "may_write_final_summary": phase == 22,
                "topology_ai_subsystem_command": full_plan_topology_ai_subsystem_command(root, project, phase, out_dir),
            }
        )
    return rows


def full_plan_blocker(blocker_id: str, phase: int | None, reason: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "blocker_id": blocker_id,
        "workflow": FULL_PLAN_WORKFLOW,
        "phase_number": phase,
        "phase_name": FULL_PLAN_PHASES.get(phase) if phase else None,
        "reason": reason,
        "details": details or {},
    }


def full_plan_stage_record(
    *,
    run_id_value: str | None,
    phase: int,
    mode: str,
    status: str,
    reason: str,
    phase_dir: Path,
    prompt_path: Path,
    prompt_command: list[str],
    runner_command: list[str],
    validation_commands: dict[str, list[str]],
    return_code: int | None = None,
    stdout: str = "",
    stderr: str = "",
    checkpoint_row_count: int = 0,
    blocker_id: str | None = None,
    log_path: str | None = None,
) -> dict[str, Any]:
    _log_exists = Path(log_path).exists() if log_path else False
    return {
        "workflow": FULL_PLAN_WORKFLOW,
        "run_id": run_id_value,
        "phase_number": phase,
        "phase_name": FULL_PLAN_PHASES[phase],
        "mode": mode,
        "status": status,
        "reason": reason,
        "return_code": return_code,
        "stdout_preview": preview(stdout),
        "stderr_preview": preview(stderr),
        "log_path": log_path,
        "log_exists": _log_exists,
        "log_preview": preview(Path(log_path).read_text(encoding="utf-8")) if (_log_exists and log_path) else None,
        "phase_output_dir": str(phase_dir),
        "prompt_path": str(prompt_path),
        "prompt_command": prompt_command,
        "runner_command": runner_command,
        "validation_commands": validation_commands,
        "checkpoint_row_count": checkpoint_row_count,
        "blocker_id": blocker_id,
        "may_write_findings": phase >= 19,
        "may_generate_report": phase >= 21,
        "may_write_final_summary": phase == 22,
        "topology_ai_subsystem": phase == 12,
    }


def full_plan_mode(args: argparse.Namespace) -> str:
    if args.prompt_only:
        return "prompt_only"
    if args.execute:
        return "execute"
    return "dry_run"


def out_dir_for_full_plan(args: argparse.Namespace, root: Path) -> Path:
    if args.out_dir:
        out_dir = Path(args.out_dir)
        return out_dir if out_dir.is_absolute() else root / out_dir
    stamp = run_id()
    if args.run_id:
        return root / "exports" / args.project / args.run_id / f"full_plan_{stamp}"
    return root / "exports" / args.project / stamp


def write_full_plan_inspection_commands(path: Path, project: str, out_dir: Path, results: list[dict[str, Any]]) -> None:
    lines = [
        "# Full Plan Driver Inspection Commands",
        "",
        f"Project: `{project}`",
        f"Workflow run directory: `{out_dir}`",
        "",
        "```bash",
        f"python3 -m json.tool {out_dir / 'full-plan-driver-manifest.json'}",
        f"python3 -m json.tool {out_dir / 'full-plan-driver-status.json'}",
        f"python3 -m json.tool {out_dir / 'full-plan-driver-stage-results.json'}",
        f"python3 -m json.tool {out_dir / 'full-plan-driver-blockers.json'}",
        "```",
        "",
        "## Phase Commands",
        "",
    ]
    for row in results:
        lines.extend([f"### Phase {row['phase_number']}: {row['phase_name']}", ""])
        lines.extend(["Prompt:", "```bash", command_text(row["prompt_command"]), "```", ""])
        if row["runner_command"]:
            lines.extend(["Runner:", "```bash", command_text(row["runner_command"]), "```", ""])
        lines.extend(["Audit:", "```bash", command_text(row["validation_commands"]["audit"]), "```", ""])
        if row.get("topology_ai_subsystem"):
            lines.extend(["Topology AI subsystem remains separate from the full-plan driver.", ""])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_full_plan_artifacts(
    args: argparse.Namespace,
    out_dir: Path,
    phases: list[int],
    stage_plan: list[dict[str, Any]],
    results: list[dict[str, Any]],
    blockers: list[dict[str, Any]],
    mode: str,
) -> dict[str, Path]:
    now = utc_now()
    status_counts = {
        name: sum(1 for row in results if row["status"] == name)
        for name in ["planned", "prompt_written", "passed", "failed", "blocked", "skipped_passed_checkpoint"]
    }
    overall_status = "failed" if status_counts["failed"] else ("blocked" if status_counts["blocked"] or blockers else "passed")
    flags = {**FULL_PLAN_SAFETY_FLAGS}
    if args.enable_topology_ai_subsystem and any(row.get("topology_ai_subsystem") and row["status"] == "passed" for row in results):
        flags["ran_topology_ai_subsystem"] = True
    manifest = {
        "artifact_type": "full_plan_driver_manifest",
        "schema_version": "1.0",
        "generated_at_utc": now,
        "project": args.project,
        "run_id": args.run_id,
        "workflow": FULL_PLAN_WORKFLOW,
        "workflow_run_dir": str(out_dir),
        "phase_range": {"start": phases[0] if phases else None, "end": phases[-1] if phases else None},
        "mode": mode,
        "runner": args.runner,
        "resume": args.resume,
        "allow_existing_outputs": args.allow_existing_outputs,
        "docs": full_plan_doc_status(repo_root()),
        "stage_plan": stage_plan,
        **flags,
    }
    status = {
        "artifact_type": "full_plan_driver_status",
        "schema_version": "1.0",
        "generated_at_utc": now,
        "project": args.project,
        "run_id": args.run_id,
        "workflow": FULL_PLAN_WORKFLOW,
        "workflow_run_dir": str(out_dir),
        "overall_status": overall_status,
        "mode": mode,
        "runner": args.runner,
        "phase_count": len(phases),
        "stage_counts": status_counts,
        "blocker_count": len(blockers),
        "findings_forbidden_before_phase_19": True,
        "report_forbidden_before_phase_21": True,
        "final_summary_forbidden_before_phase_22": True,
        **flags,
    }
    stage_results = {
        "artifact_type": "full_plan_driver_stage_results",
        "schema_version": "1.0",
        "generated_at_utc": now,
        "project": args.project,
        "run_id": args.run_id,
        "workflow": FULL_PLAN_WORKFLOW,
        "stage_results": results,
        **flags,
    }
    blocker_artifact = {
        "artifact_type": "full_plan_driver_blockers",
        "schema_version": "1.0",
        "generated_at_utc": now,
        "project": args.project,
        "run_id": args.run_id,
        "workflow": FULL_PLAN_WORKFLOW,
        "blockers": blockers,
        **flags,
    }
    artifacts = {
        "full-plan-driver-manifest.json": out_dir / "full-plan-driver-manifest.json",
        "full-plan-driver-status.json": out_dir / "full-plan-driver-status.json",
        "full-plan-driver-stage-results.json": out_dir / "full-plan-driver-stage-results.json",
        "full-plan-driver-blockers.json": out_dir / "full-plan-driver-blockers.json",
        "full-plan-driver-inspection-commands.md": out_dir / "full-plan-driver-inspection-commands.md",
    }
    write_json(artifacts["full-plan-driver-manifest.json"], manifest)
    write_json(artifacts["full-plan-driver-status.json"], status)
    write_json(artifacts["full-plan-driver-stage-results.json"], stage_results)
    write_json(artifacts["full-plan-driver-blockers.json"], blocker_artifact)
    write_full_plan_inspection_commands(artifacts["full-plan-driver-inspection-commands.md"], args.project, out_dir, results)
    return artifacts


def execute_full_plan(args: argparse.Namespace) -> int:
    root = repo_root()
    docs = full_plan_doc_status(root)
    missing_docs = [name for name, info in docs.items() if not info["exists"]]
    out_dir = out_dir_for_full_plan(args, root)
    mode = full_plan_mode(args)
    start = full_phase_number(args.start)
    end = full_phase_number(args.end)
    phases = selected_full_plan_phases(start, end)
    stamp = run_id()
    stage_plan = full_plan_stage_plan(root, args.project, args.run_id, out_dir, phases, stamp)
    blockers: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []

    if missing_docs:
        blockers.append(full_plan_blocker("missing_full_plan_docs", None, "required full-plan docs are missing", {"missing": missing_docs}))

    if out_dir.exists() and any(out_dir.iterdir()) and not args.allow_existing_outputs:
        blockers.append(full_plan_blocker("existing_full_plan_output_dir", None, "output directory already exists and is not empty", {"out_dir": str(out_dir)}))

    out_dir.mkdir(parents=True, exist_ok=True)

    if blockers:
        write_full_plan_artifacts(args, out_dir, phases, stage_plan, results, blockers, mode)
        print(f"ERROR: full_plan driver blocked before phase execution: {blockers[0]['reason']}", file=sys.stderr)
        return 2

    checkpoint_path = checkpoint_file(root, args.project)
    checkpoint_rows = load_checkpoint_rows(checkpoint_path)

    for planned in stage_plan:
        phase = int(planned["phase_number"])
        phase_dir = Path(planned["phase_output_dir"])
        prompt_path = Path(planned["prompt_path"])
        prompt_command = planned["prompt_command"]
        validation_commands = planned["validation_commands"]
        runner_command = full_plan_runner_command(root, args.project, phase, args.runner)
        if args.enable_topology_ai_subsystem and phase == 12:
            runner_command = planned["topology_ai_subsystem_command"] or runner_command
        phase_rows = checkpoint_rows_for_phase(checkpoint_rows, phase)

        if args.resume and exactly_one_passed_checkpoint(checkpoint_rows, phase):
            results.append(
                full_plan_stage_record(
                    run_id_value=args.run_id,
                    phase=phase,
                    mode=mode,
                    status="skipped_passed_checkpoint",
                    reason="resume: exactly one passed checkpoint row already exists",
                    phase_dir=phase_dir,
                    prompt_path=prompt_path,
                    prompt_command=prompt_command,
                    runner_command=runner_command,
                    validation_commands=validation_commands,
                    checkpoint_row_count=len(phase_rows),
                )
            )
            continue

        if phase_rows and not args.allow_existing_outputs and mode != "dry_run":
            blocker_id = f"phase{phase:02d}_checkpoint_exists_without_resume"
            blockers.append(
                full_plan_blocker(
                    blocker_id,
                    phase,
                    "checkpoint rows already exist; use --resume to skip passed phases or --allow-existing-outputs to continue",
                    {"checkpoint_row_count": len(phase_rows), "checkpoint_path": str(checkpoint_path)},
                )
            )
            results.append(
                full_plan_stage_record(
                    run_id_value=args.run_id,
                    phase=phase,
                    mode=mode,
                    status="blocked",
                    reason="checkpoint exists without --resume",
                    phase_dir=phase_dir,
                    prompt_path=prompt_path,
                    prompt_command=prompt_command,
                    runner_command=runner_command,
                    validation_commands=validation_commands,
                    checkpoint_row_count=len(phase_rows),
                    blocker_id=blocker_id,
                )
            )
            break

        if mode == "dry_run":
            results.append(
                full_plan_stage_record(
                    run_id_value=args.run_id,
                    phase=phase,
                    mode=mode,
                    status="planned",
                    reason="dry-run: prompt, runner, and validation commands were not executed",
                    phase_dir=phase_dir,
                    prompt_path=prompt_path,
                    prompt_command=prompt_command,
                    runner_command=runner_command,
                    validation_commands=validation_commands,
                    checkpoint_row_count=len(phase_rows),
                )
            )
            continue

        phase_dir.mkdir(parents=True, exist_ok=True)
        completed_prompt = subprocess.run(prompt_command, cwd=root, text=True, capture_output=True)
        if completed_prompt.returncode != 0:
            blocker_id = f"phase{phase:02d}_prompt_failed"
            blockers.append(full_plan_blocker(blocker_id, phase, "phase prompt generation failed", {"return_code": completed_prompt.returncode}))
            results.append(
                full_plan_stage_record(
                    run_id_value=args.run_id,
                    phase=phase,
                    mode=mode,
                    status="failed",
                    reason="phase prompt generation failed",
                    phase_dir=phase_dir,
                    prompt_path=prompt_path,
                    prompt_command=prompt_command,
                    runner_command=runner_command,
                    validation_commands=validation_commands,
                    return_code=completed_prompt.returncode,
                    stdout=completed_prompt.stdout,
                    stderr=completed_prompt.stderr,
                    checkpoint_row_count=len(phase_rows),
                    blocker_id=blocker_id,
                )
            )
            break

        if mode == "prompt_only":
            results.append(
                full_plan_stage_record(
                    run_id_value=args.run_id,
                    phase=phase,
                    mode=mode,
                    status="prompt_written",
                    reason="prompt-only: runner and validation commands were not executed",
                    phase_dir=phase_dir,
                    prompt_path=prompt_path,
                    prompt_command=prompt_command,
                    runner_command=runner_command,
                    validation_commands=validation_commands,
                    return_code=completed_prompt.returncode,
                    stdout=completed_prompt.stdout,
                    stderr=completed_prompt.stderr,
                    checkpoint_row_count=len(phase_rows),
                )
            )
            continue

        subsystem_runner_selected = args.enable_topology_ai_subsystem and phase == 12 and bool(planned["topology_ai_subsystem_command"])
        if args.runner == "none" and not subsystem_runner_selected:
            blocker_id = f"phase{phase:02d}_runner_none"
            blockers.append(full_plan_blocker(blocker_id, phase, "--execute requires --runner openhands or an enabled subsystem runner"))
            results.append(
                full_plan_stage_record(
                    run_id_value=args.run_id,
                    phase=phase,
                    mode=mode,
                    status="blocked",
                    reason="execute requested with runner=none",
                    phase_dir=phase_dir,
                    prompt_path=prompt_path,
                    prompt_command=prompt_command,
                    runner_command=runner_command,
                    validation_commands=validation_commands,
                    checkpoint_row_count=len(phase_rows),
                    blocker_id=blocker_id,
                )
            )
            break
        if args.runner == "codex" and not subsystem_runner_selected:
            blocker_id = f"phase{phase:02d}_runner_codex_unimplemented"
            blockers.append(full_plan_blocker(blocker_id, phase, "codex runner integration is not implemented yet"))
            results.append(
                full_plan_stage_record(
                    run_id_value=args.run_id,
                    phase=phase,
                    mode=mode,
                    status="blocked",
                    reason="codex runner integration is not implemented yet",
                    phase_dir=phase_dir,
                    prompt_path=prompt_path,
                    prompt_command=prompt_command,
                    runner_command=runner_command,
                    validation_commands=validation_commands,
                    checkpoint_row_count=len(phase_rows),
                    blocker_id=blocker_id,
                )
            )
            break

        # Stream runner output live and save per-phase log
        phase_log_path = str(phase_dir / f"phase{phase:02d}-openhands.log")
        log_fh = open(phase_log_path, "w", encoding="utf-8")
        try:
            proc = subprocess.Popen(
                runner_command,
                cwd=root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
        except Exception as exc:
            blocker_id = f"phase{phase:02d}_runner_failed"
            blockers.append(full_plan_blocker(blocker_id, phase, "phase runner failed", {"reason": str(exc)}))
            results.append(
                full_plan_stage_record(
                    run_id_value=args.run_id,
                    phase=phase,
                    mode=mode,
                    status="failed",
                    reason="phase runner launch failed",
                    phase_dir=phase_dir,
                    prompt_path=prompt_path,
                    prompt_command=prompt_command,
                    runner_command=runner_command,
                    validation_commands=validation_commands,
                    return_code=None,
                    stdout="",
                    stderr=str(exc),
                    checkpoint_row_count=len(phase_rows),
                    blocker_id=blocker_id,
                    log_path=phase_log_path,
                )
            )
            break

        runner_stdout_parts: list[str] = []
        for line in iter(proc.stdout.readline, ""):
            sys.stdout.write(line)
            sys.stdout.flush()
            log_fh.write(line)
            runner_stdout_parts.append(line)
        proc.stdout.close()
        completed_runner = subprocess.CompletedProcess(
            runner_command,
            proc.wait(),
            "".join(runner_stdout_parts),
            "",
        )
        log_fh.close()
        log_fh = None

        checkpoint_rows = load_checkpoint_rows(checkpoint_path)
        phase_rows = checkpoint_rows_for_phase(checkpoint_rows, phase)
        if completed_runner.returncode != 0:
            blocker_id = f"phase{phase:02d}_runner_failed"
            blockers.append(full_plan_blocker(blocker_id, phase, "phase runner failed", {"return_code": completed_runner.returncode}))
            results.append(
                full_plan_stage_record(
                    run_id_value=args.run_id,
                    phase=phase,
                    mode=mode,
                    status="failed",
                    reason="phase runner failed",
                    phase_dir=phase_dir,
                    prompt_path=prompt_path,
                    prompt_command=prompt_command,
                    runner_command=runner_command,
                    validation_commands=validation_commands,
                    return_code=completed_runner.returncode,
                    stdout=completed_runner.stdout,
                    stderr="",
                    checkpoint_row_count=len(phase_rows),
                    blocker_id=blocker_id,
                    log_path=phase_log_path,
                )
            )
            break

        # Post-runner validation: ensure_phase_checkpoint.py then audit_phase.py
        ensure_cmd = [
            sys.executable or "python3",
            str(root / "scripts" / "ensure_phase_checkpoint.py"),
            "--project", args.project,
            "--phase", str(phase),
            "--exports", str(root / "exports"),
            "--mode", "replace",
        ]
        completed_ensure = subprocess.run(ensure_cmd, cwd=root, text=True, capture_output=True)
        if completed_ensure.returncode != 0:
            blocker_id = f"phase{phase:02d}_checkpoint_not_passed"
            blockers.append(
                full_plan_blocker(
                    blocker_id,
                    phase,
                    "ensure_phase_checkpoint failed",
                    {"return_code": completed_ensure.returncode},
                )
            )
            results.append(
                full_plan_stage_record(
                    run_id_value=args.run_id,
                    phase=phase,
                    mode=mode,
                    status="blocked",
                    reason="ensure_phase_checkpoint failed",
                    phase_dir=phase_dir,
                    prompt_path=prompt_path,
                    prompt_command=prompt_command,
                    runner_command=runner_command,
                    validation_commands=validation_commands,
                    return_code=completed_ensure.returncode,
                    stdout=completed_ensure.stdout,
                    stderr=completed_ensure.stderr,
                    checkpoint_row_count=len(phase_rows),
                    blocker_id=blocker_id,
                    log_path=phase_log_path,
                )
            )
            break

        # Re-checkpoint after ensure_phase_checkpoint
        checkpoint_rows = load_checkpoint_rows(checkpoint_path)
        phase_rows = checkpoint_rows_for_phase(checkpoint_rows, phase)
        if not exactly_one_passed_checkpoint(checkpoint_rows, phase):
            blocker_id = f"phase{phase:02d}_checkpoint_not_passed"
            blockers.append(
                full_plan_blocker(
                    blocker_id,
                    phase,
                    "phase must produce exactly one checkpoint row with phase_passed=true",
                    {"checkpoint_row_count": len(phase_rows), "checkpoint_path": str(checkpoint_path)},
                )
            )
            results.append(
                full_plan_stage_record(
                    run_id_value=args.run_id,
                    phase=phase,
                    mode=mode,
                    status="blocked",
                    reason="missing, duplicate, or failed phase checkpoint",
                    phase_dir=phase_dir,
                    prompt_path=prompt_path,
                    prompt_command=prompt_command,
                    runner_command=runner_command,
                    validation_commands=validation_commands,
                    return_code=completed_ensure.returncode,
                    stdout=completed_ensure.stdout,
                    stderr=completed_ensure.stderr,
                    checkpoint_row_count=len(phase_rows),
                    blocker_id=blocker_id,
                    log_path=phase_log_path,
                )
            )
            break

        completed_audit = subprocess.run(validation_commands["audit"], cwd=root, text=True, capture_output=True)
        status = "passed" if completed_audit.returncode == 0 else "failed"
        blocker_id = None
        reason = "phase runner and audit passed"
        if completed_audit.returncode != 0:
            blocker_id = f"phase{phase:02d}_audit_failed"
            reason = "phase audit failed"
            blockers.append(full_plan_blocker(blocker_id, phase, reason, {"return_code": completed_audit.returncode}))
        results.append(
            full_plan_stage_record(
                run_id_value=args.run_id,
                phase=phase,
                mode=mode,
                status=status,
                reason=reason,
                phase_dir=phase_dir,
                prompt_path=prompt_path,
                prompt_command=prompt_command,
                runner_command=runner_command,
                validation_commands=validation_commands,
                return_code=completed_audit.returncode,
                stdout=completed_audit.stdout,
                stderr=completed_audit.stderr,
                checkpoint_row_count=len(phase_rows),
                blocker_id=blocker_id,
                log_path=phase_log_path,
            )
        )
        if completed_audit.returncode != 0:
            break

    artifacts = write_full_plan_artifacts(args, out_dir, phases, stage_plan, results, blockers, mode)
    print(f"phase driver full_plan: project={args.project} phases={start}-{end} mode={mode} run_dir={out_dir}")
    for name in FULL_PLAN_REQUIRED_OUTPUTS:
        print(f"wrote {artifacts[name]}")
    return 0 if not blockers else 1


def execute_evidence_review_dry_run(args: argparse.Namespace) -> int:
    root = repo_root()
    start = int(args.legacy_start)
    end = int(args.legacy_end)
    if start < 1 or end > 22 or end < start:
        print("ERROR: evidence_review phases must be in the original 1-22 range", file=sys.stderr)
        return 2
    out_dir = root / "exports" / args.project / "phase_runs" / "evidence_review" / run_id()
    out_dir.mkdir(parents=True, exist_ok=True)
    plan = {
        "artifact_type": "phase_driver_manifest",
        "schema_version": "1.0",
        "generated_at_utc": utc_now(),
        "project": args.project,
        "workflow": "evidence_review",
        "workflow_run_dir": str(out_dir),
        "dry_run": True,
        "stage_plan": [{"phase_number": n, "runner": "scripts/run_openhands_phase.sh"} for n in range(start, end + 1)],
        "reason": "legacy compatibility dry-run only; numeric phases are not renumbered or reinterpreted",
    }
    write_json(out_dir / "phase-driver-manifest.json", plan)
    print(f"phase driver evidence_review dry-run: project={args.project} phases={start}-{end} run_dir={out_dir}")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Workflow-aware ThomsonLint phase driver.")
    parser.add_argument("project")
    parser.add_argument("legacy_start", nargs="?")
    parser.add_argument("legacy_end", nargs="?")
    parser.add_argument("--workflow", choices=["evidence_review", WORKFLOW, FULL_PLAN_WORKFLOW], default="evidence_review")
    parser.add_argument("--start", default="pr16")
    parser.add_argument("--end", default="pr37")
    parser.add_argument("--run-id")
    parser.add_argument("--out-dir")
    parser.add_argument("--allow-existing-outputs", action="store_true")
    parser.add_argument("--fixtures-dir")
    parser.add_argument("--responses-dir")
    parser.add_argument("--approval-decisions")
    parser.add_argument("--allow-partial-responses", action="store_true")
    parser.add_argument("--continue-with-existing-ai-artifacts", action="store_true")
    parser.add_argument("--stop-at-missing-ai", action="store_true")
    parser.add_argument("--strict", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--prompt-only", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--runner", choices=["openhands", "codex", "none"], default="none")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--enable-topology-ai-subsystem", action="store_true")
    # Optional source artifact overrides for PR26
    parser.add_argument("--bom")
    parser.add_argument("--schematic-export")
    parser.add_argument("--datasheets-dir")
    parser.add_argument("--datasheet-manifest")
    parser.add_argument("--datasheet-index")
    parser.add_argument("--datasheet-evidence-index")
    parser.add_argument("--part-info-index")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.workflow == FULL_PLAN_WORKFLOW:
        if args.start == "pr16":
            args.start = args.legacy_start or "1"
        if args.end == "pr37":
            args.end = args.legacy_end or "22"
        if not (args.dry_run or args.prompt_only or args.execute):
            args.dry_run = True
        return execute_full_plan(args)
    if args.workflow == WORKFLOW:
        return execute_topology_ai(args)
    if args.dry_run:
        if args.legacy_start is None or args.legacy_end is None:
            args.legacy_start = args.legacy_start or "1"
            args.legacy_end = args.legacy_end or "22"
        return execute_evidence_review_dry_run(args)
    print("ERROR: phase_driver.py only executes topology_ai or evidence_review --dry-run; use scripts/run_phase_driver.sh for legacy execution", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
