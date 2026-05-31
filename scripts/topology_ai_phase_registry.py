#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable


CommandBuilder = Callable[["TopologyPaths"], list[str] | None]


@dataclass(frozen=True)
class TopologyPaths:
    project: str
    repo_root: Path
    run_dir: Path
    python_executable: str
    strict: bool
    dry_run: bool
    fixtures_dir: Path | None = None

    def core(self, suffix: str) -> Path:
        return self.repo_root / "exports" / f"{self.project}-{suffix}"

    def stage_dir(self, phase_id: str) -> Path:
        return self.run_dir / phase_id

    def stage_file(self, phase_id: str, filename: str) -> Path:
        return self.stage_dir(phase_id) / filename


@dataclass(frozen=True)
class PhaseSpec:
    phase_id: str
    pr_number: int
    title: str
    script: str | None
    input_paths: Callable[[TopologyPaths], list[Path]]
    output_paths: Callable[[TopologyPaths], list[Path]]
    command: CommandBuilder
    reason: str
    stage_kind: str = "deterministic"


def _py(paths: TopologyPaths, script: str) -> list[str]:
    return [paths.python_executable, str(paths.repo_root / "scripts" / script)]


def _strict(paths: TopologyPaths) -> list[str]:
    return ["--strict"] if paths.strict else []


def _core_current(paths: TopologyPaths) -> Path:
    return paths.stage_file("pr19_current_model_ingest", "current-models-normalized.json")


def _current_allocation(paths: TopologyPaths) -> Path:
    return paths.stage_file("pr20_current_allocation", "topology-current-allocation.json")


def _rating_models(paths: TopologyPaths) -> Path:
    return paths.stage_file("pr23_rating_model_ingest", "rating-models-normalized.json")


def _readiness(paths: TopologyPaths) -> Path:
    return paths.stage_file("pr16_calculation_readiness", "calculation-readiness-inventory.json")


def _missing_manifest(paths: TopologyPaths) -> Path:
    fixture = paths.fixtures_dir / "missing-data-manifest.json" if paths.fixtures_dir else None
    return fixture if fixture and fixture.exists() else paths.core("missing-data-manifest.json")


def _packet_dir(paths: TopologyPaths) -> Path:
    return paths.stage_dir("pr26_ai_packet_build")


def _validation(paths: TopologyPaths) -> Path:
    return paths.stage_file("pr27_ai_extraction_validate", "ai-extraction-validation.json")


def _patch_bundle(paths: TopologyPaths) -> Path:
    return paths.stage_file("pr28_ai_patch_build", "ai-patch-bundle.json")


def _candidate_dir(paths: TopologyPaths) -> Path:
    return paths.stage_dir("pr29_ai_candidate_materialize")


def _adapter_dir(paths: TopologyPaths) -> Path:
    return paths.stage_dir("pr30_ai_candidate_adapter_outputs")


def _ai_ingested_dir(paths: TopologyPaths) -> Path:
    return paths.stage_dir("pr31_ai_candidate_ingest")


def _promotion_dir(paths: TopologyPaths) -> Path:
    return paths.stage_dir("pr32_ai_promotion_plan")


def _approval_decisions(paths: TopologyPaths) -> Path:
    return paths.stage_file("pr33_ai_approval_decisions", "ai-approval-decisions.json")


def _approval_validation(paths: TopologyPaths) -> Path:
    return paths.stage_file("pr33_ai_approval_decisions", "ai-approval-decision-validation.json")


def _dry_apply_dir(paths: TopologyPaths) -> Path:
    return paths.stage_dir("pr34_ai_promotion_apply_dry_run")


def _candidate_core_input_dir(paths: TopologyPaths) -> Path:
    return paths.stage_dir("pr35_ai_candidate_core_input_apply")


def _candidate_core_ingested_dir(paths: TopologyPaths) -> Path:
    return paths.stage_dir("pr36_ai_candidate_core_input_ingest")


def _cmd_pr16(paths: TopologyPaths) -> list[str]:
    return [
        *_py(paths, "calculation_readiness_inventory.py"),
        "--project",
        paths.project,
        "--branch-topology-enriched",
        str(paths.core("branch-topology-enriched.json")),
        "--role-resolution",
        str(paths.core("topology-roles.json")),
        "--rail-relationships",
        str(paths.core("rail-relationships.json")),
        "--geometry-review",
        str(paths.core("topology-geometry-review.json")),
        "--out",
        str(_readiness(paths)),
    ]


def _cmd_pr18(paths: TopologyPaths) -> list[str]:
    return [
        *_py(paths, "topology_copper_calculate.py"),
        "--project",
        paths.project,
        "--geometry-review",
        str(paths.core("topology-geometry-review.json")),
        "--calculation-readiness",
        str(_readiness(paths)),
        "--missing-data-manifest",
        str(_missing_manifest(paths)),
        "--out",
        str(paths.stage_file("pr18_copper_calculation", "topology-copper-calculations.json")),
    ]


def _cmd_pr19(paths: TopologyPaths) -> list[str]:
    return [
        *_py(paths, "current_model_ingest.py"),
        "--project",
        paths.project,
        "--current-model",
        str(paths.core("current-model.json")),
        "--missing-data-manifest",
        str(_missing_manifest(paths)),
        "--branch-topology-enriched",
        str(paths.core("branch-topology-enriched.json")),
        "--rail-relationships",
        str(paths.core("rail-relationships.json")),
        "--role-resolution",
        str(paths.core("topology-roles.json")),
        "--out",
        str(_core_current(paths)),
    ]


def _cmd_pr20(paths: TopologyPaths) -> list[str]:
    return [
        *_py(paths, "topology_current_allocate.py"),
        "--project",
        paths.project,
        "--current-models-normalized",
        str(_core_current(paths)),
        "--branch-topology-enriched",
        str(paths.core("branch-topology-enriched.json")),
        "--rail-relationships",
        str(paths.core("rail-relationships.json")),
        "--role-resolution",
        str(paths.core("topology-roles.json")),
        "--missing-data-manifest",
        str(_missing_manifest(paths)),
        "--calculation-readiness",
        str(_readiness(paths)),
        "--out",
        str(_current_allocation(paths)),
    ]


def _cmd_pr21(paths: TopologyPaths) -> list[str]:
    return [
        *_py(paths, "topology_copper_calculate.py"),
        "--project",
        paths.project,
        "--geometry-review",
        str(paths.core("topology-geometry-review.json")),
        "--calculation-readiness",
        str(_readiness(paths)),
        "--missing-data-manifest",
        str(_missing_manifest(paths)),
        "--current-allocation",
        str(_current_allocation(paths)),
        "--out",
        str(paths.stage_file("pr21_copper_with_allocated_current", "topology-copper-with-allocated-current.json")),
    ]


def _cmd_pr22(paths: TopologyPaths) -> list[str]:
    return [
        *_py(paths, "topology_copper_calculate.py"),
        "--project",
        paths.project,
        "--geometry-review",
        str(paths.core("topology-geometry-review.json")),
        "--calculation-readiness",
        str(_readiness(paths)),
        "--missing-data-manifest",
        str(_missing_manifest(paths)),
        "--current-allocation",
        str(_current_allocation(paths)),
        "--out",
        str(paths.stage_file("pr22_via_current_density", "topology-via-current-density.json")),
    ]


def _cmd_pr23(paths: TopologyPaths) -> list[str]:
    return [
        *_py(paths, "rating_model_ingest.py"),
        "--project",
        paths.project,
        "--current-models-normalized",
        str(_core_current(paths)),
        "--missing-data-manifest",
        str(_missing_manifest(paths)),
        "--branch-topology-enriched",
        str(paths.core("branch-topology-enriched.json")),
        "--rail-relationships",
        str(paths.core("rail-relationships.json")),
        "--role-resolution",
        str(paths.core("topology-roles.json")),
        "--out",
        str(_rating_models(paths)),
    ]


def _cmd_margin(paths: TopologyPaths, phase_id: str, filename: str) -> list[str]:
    return [
        *_py(paths, "topology_margin_calculate.py"),
        "--project",
        paths.project,
        "--current-allocation",
        str(_current_allocation(paths)),
        "--rating-models-normalized",
        str(_rating_models(paths)),
        "--missing-data-manifest",
        str(_missing_manifest(paths)),
        "--branch-topology-enriched",
        str(paths.core("branch-topology-enriched.json")),
        "--rail-relationships",
        str(paths.core("rail-relationships.json")),
        "--role-resolution",
        str(paths.core("topology-roles.json")),
        "--out",
        str(paths.stage_file(phase_id, filename)),
    ]


def _cmd_pr26(paths: TopologyPaths) -> list[str]:
    return [
        *_py(paths, "ai_packet_phase_build.py"),
        "--project",
        paths.project,
        "--missing-data-manifest",
        str(_missing_manifest(paths)),
        "--out-dir",
        str(_packet_dir(paths)),
        "--phase-id",
        "pr26",
        "--phase-name",
        "AI packet generation",
        "--branch-topology-enriched",
        str(paths.core("branch-topology-enriched.json")),
        "--rail-relationships",
        str(paths.core("rail-relationships.json")),
        "--role-resolution",
        str(paths.core("topology-roles.json")),
    ]


def _cmd_pr27(paths: TopologyPaths) -> list[str]:
    return [
        *_py(paths, "ai_extraction_validate.py"),
        "--project",
        paths.project,
        "--packet-dir",
        str(_packet_dir(paths)),
        "--out",
        str(_validation(paths)),
        *_strict(paths),
    ]


def _cmd_pr28(paths: TopologyPaths) -> list[str]:
    return [
        *_py(paths, "ai_patch_build.py"),
        "--project",
        paths.project,
        "--validation",
        str(_validation(paths)),
        "--out",
        str(_patch_bundle(paths)),
        "--out-dir",
        str(paths.stage_dir("pr28_ai_patch_build")),
        *_strict(paths),
    ]


def _cmd_pr29(paths: TopologyPaths) -> list[str]:
    return [
        *_py(paths, "ai_candidate_materialize.py"),
        "--project",
        paths.project,
        "--patch-bundle",
        str(_patch_bundle(paths)),
        "--out-dir",
        str(_candidate_dir(paths)),
        *_strict(paths),
    ]


def _cmd_pr30(paths: TopologyPaths) -> list[str]:
    return [
        *_py(paths, "ai_candidate_adapter_build.py"),
        "--project",
        paths.project,
        "--candidate-dir",
        str(_candidate_dir(paths)),
        "--out-dir",
        str(_adapter_dir(paths)),
        *_strict(paths),
    ]


def _cmd_pr31(paths: TopologyPaths) -> list[str]:
    return [
        *_py(paths, "ai_candidate_ingest_workflow.py"),
        "--project",
        paths.project,
        "--adapter-dir",
        str(_adapter_dir(paths)),
        "--out-dir",
        str(_ai_ingested_dir(paths)),
        *_strict(paths),
    ]


def _cmd_pr32(paths: TopologyPaths) -> list[str]:
    return [
        *_py(paths, "ai_candidate_promotion_plan.py"),
        "--project",
        paths.project,
        "--ai-ingested-dir",
        str(_ai_ingested_dir(paths)),
        "--out-dir",
        str(_promotion_dir(paths)),
        "--core-current-models-normalized",
        str(_core_current(paths)),
        "--core-rating-models-normalized",
        str(_rating_models(paths)),
        "--missing-data-manifest",
        str(_missing_manifest(paths)),
        "--allow-core-missing",
        *_strict(paths),
    ]


def _cmd_pr33(paths: TopologyPaths) -> list[str]:
    return [
        *_py(paths, "ai_approval_decision_edit.py"),
        "--project",
        paths.project,
        "--promotion-dir",
        str(_promotion_dir(paths)),
        "--out",
        str(_approval_decisions(paths)),
        "--validate-out",
        str(_approval_validation(paths)),
        "--decision-template",
        *_strict(paths),
    ]


def _cmd_pr34(paths: TopologyPaths) -> list[str]:
    return [
        *_py(paths, "ai_promotion_apply_dry_run.py"),
        "--project",
        paths.project,
        "--promotion-dir",
        str(_promotion_dir(paths)),
        "--decisions",
        str(_approval_decisions(paths)),
        "--decision-validation",
        str(_approval_validation(paths)),
        "--out-dir",
        str(_dry_apply_dir(paths)),
        "--core-current-models-normalized",
        str(_core_current(paths)),
        "--core-rating-models-normalized",
        str(_rating_models(paths)),
        *_strict(paths),
    ]


def _cmd_pr35(paths: TopologyPaths) -> list[str]:
    return [
        *_py(paths, "ai_candidate_core_input_apply.py"),
        "--project",
        paths.project,
        "--dry-run-dir",
        str(_dry_apply_dir(paths)),
        "--out-dir",
        str(_candidate_core_input_dir(paths)),
        *_strict(paths),
    ]


def _cmd_pr36(paths: TopologyPaths) -> list[str]:
    return [
        *_py(paths, "ai_candidate_core_input_ingest_workflow.py"),
        "--project",
        paths.project,
        "--candidate-input-dir",
        str(_candidate_core_input_dir(paths)),
        "--out-dir",
        str(_candidate_core_ingested_dir(paths)),
        *_strict(paths),
    ]


def _cmd_pr37(paths: TopologyPaths) -> list[str]:
    return [
        *_py(paths, "ai_candidate_normalized_promotion_review.py"),
        "--project",
        paths.project,
        "--candidate-ingested-dir",
        str(_candidate_core_ingested_dir(paths)),
        "--out-dir",
        str(paths.stage_dir("pr37_ai_candidate_normalized_review")),
        "--core-current-models-normalized",
        str(_core_current(paths)),
        "--core-rating-models-normalized",
        str(_rating_models(paths)),
        "--allow-missing-core",
        *_strict(paths),
    ]


TOPOLOGY_AI_PHASES: tuple[PhaseSpec, ...] = (
    PhaseSpec("pr16_calculation_readiness", 16, "calculation readiness / missing-data inventory", "calculation_readiness_inventory.py", lambda p: [p.core("branch-topology-enriched.json")], lambda p: [_readiness(p)], _cmd_pr16, "PR16 calculation readiness and missing-data inventory."),
    PhaseSpec("pr17_schema_available", 17, "schema availability check", None, lambda p: [p.repo_root / "schemas" / "calculation_readiness_schema.json", p.repo_root / "schemas" / "calculation_result_schema.json"], lambda p: [], lambda p: None, "PR17 checks schema availability only; it does not execute a command.", "schema_check"),
    PhaseSpec("pr18_copper_calculation", 18, "copper calculations", "topology_copper_calculate.py", lambda p: [p.core("topology-geometry-review.json"), _readiness(p), _missing_manifest(p)], lambda p: [p.stage_file("pr18_copper_calculation", "topology-copper-calculations.json")], _cmd_pr18, "PR18 deterministic copper calculations."),
    PhaseSpec("pr19_current_model_ingest", 19, "current model ingestion", "current_model_ingest.py", lambda p: [p.core("current-model.json"), _missing_manifest(p)], lambda p: [_core_current(p)], _cmd_pr19, "PR19 normalizes explicit current models."),
    PhaseSpec("pr20_current_allocation", 20, "current allocation", "topology_current_allocate.py", lambda p: [_core_current(p), p.core("branch-topology-enriched.json"), _missing_manifest(p), _readiness(p)], lambda p: [_current_allocation(p)], _cmd_pr20, "PR20 allocates explicit normalized current to topology branches."),
    PhaseSpec("pr21_copper_with_allocated_current", 21, "copper path using allocated currents", "topology_copper_calculate.py", lambda p: [p.core("topology-geometry-review.json"), _readiness(p), _missing_manifest(p), _current_allocation(p)], lambda p: [p.stage_file("pr21_copper_with_allocated_current", "topology-copper-with-allocated-current.json")], _cmd_pr21, "PR21 reruns copper calculation behavior with allocated currents."),
    PhaseSpec("pr22_via_current_density", 22, "via current density", "topology_copper_calculate.py", lambda p: [p.core("topology-geometry-review.json"), _readiness(p), _missing_manifest(p), _current_allocation(p)], lambda p: [p.stage_file("pr22_via_current_density", "topology-via-current-density.json")], _cmd_pr22, "PR22 is copper/via current-density behavior, not margin behavior.", "copper_via"),
    PhaseSpec("pr23_rating_model_ingest", 23, "rating model ingestion", "rating_model_ingest.py", lambda p: [_core_current(p), _missing_manifest(p)], lambda p: [_rating_models(p)], _cmd_pr23, "PR23 normalizes explicit rating models before margin stages."),
    PhaseSpec("pr24_fuse_margin", 24, "fuse margin", "topology_margin_calculate.py", lambda p: [_current_allocation(p), _rating_models(p), _missing_manifest(p)], lambda p: [p.stage_file("pr24_fuse_margin", "topology-fuse-margin.json")], lambda p: _cmd_margin(p, "pr24_fuse_margin", "topology-fuse-margin.json"), "PR24 calculates margins using current allocation and rating models."),
    PhaseSpec("pr25_connector_pin_margin", 25, "connector pin current margin", "topology_margin_calculate.py", lambda p: [_current_allocation(p), _rating_models(p), _missing_manifest(p)], lambda p: [p.stage_file("pr25_connector_pin_margin", "topology-connector-pin-current-margin.json")], lambda p: _cmd_margin(p, "pr25_connector_pin_margin", "topology-connector-pin-current-margin.json"), "PR25 calculates connector pin current margins."),
    PhaseSpec("pr26_ai_packet_build", 26, "AI packet generation", "ai_packet_phase_build.py", lambda p: [_missing_manifest(p)], lambda p: [p.stage_file("pr26_ai_packet_build", "packet_queue.json"), p.stage_file("pr26_ai_packet_build", "phase_status.json")], _cmd_pr26, "PR26 creates prompt-ready packets only and does not call AI.", "ai_packet"),
    PhaseSpec("pr27_ai_extraction_validate", 27, "AI extraction validation", "ai_extraction_validate.py", lambda p: [p.stage_file("pr26_ai_packet_build", "packet_queue.json")], lambda p: [_validation(p)], _cmd_pr27, "PR27 validates saved raw AI responses or existing validated extraction artifacts.", "ai_response_required"),
    PhaseSpec("pr28_ai_patch_build", 28, "patch bundle build", "ai_patch_build.py", lambda p: [_validation(p)], lambda p: [_patch_bundle(p)], _cmd_pr28, "PR28 builds deterministic patch bundles from validated extraction artifacts."),
    PhaseSpec("pr29_ai_candidate_materialize", 29, "candidate materialization", "ai_candidate_materialize.py", lambda p: [_patch_bundle(p)], lambda p: [p.stage_file("pr29_ai_candidate_materialize", "ai-candidate-inputs.json")], _cmd_pr29, "PR29 materializes isolated candidate inputs."),
    PhaseSpec("pr30_ai_candidate_adapter_outputs", 30, "candidate adapter outputs/artifacts", "ai_candidate_adapter_build.py", lambda p: [p.stage_file("pr29_ai_candidate_materialize", "ai-candidate-inputs.json")], lambda p: [p.stage_file("pr30_ai_candidate_adapter_outputs", "ai-adapter-manifest.json")], _cmd_pr30, "PR30 creates adapter outputs/artifacts, not adapter scripts."),
    PhaseSpec("pr31_ai_candidate_ingest", 31, "candidate ingestion workflow", "ai_candidate_ingest_workflow.py", lambda p: [p.stage_file("pr30_ai_candidate_adapter_outputs", "ai-adapter-manifest.json")], lambda p: [p.stage_file("pr31_ai_candidate_ingest", "ai-candidate-ingestion-manifest.json")], _cmd_pr31, "PR31 ingests candidate adapter outputs in isolation."),
    PhaseSpec("pr32_ai_promotion_plan", 32, "promotion plan / approval queue", "ai_candidate_promotion_plan.py", lambda p: [p.stage_file("pr31_ai_candidate_ingest", "ai-candidate-ingestion-manifest.json")], lambda p: [p.stage_file("pr32_ai_promotion_plan", "ai-candidate-promotion-plan.json"), p.stage_file("pr32_ai_promotion_plan", "ai-candidate-approval-queue.json")], _cmd_pr32, "PR32 builds a review-only promotion plan and approval queue."),
    PhaseSpec("pr33_ai_approval_decisions", 33, "approval decision artifact generation/validation", "ai_approval_decision_edit.py", lambda p: [p.stage_file("pr32_ai_promotion_plan", "ai-candidate-approval-queue.json")], lambda p: [_approval_decisions(p), _approval_validation(p)], _cmd_pr33, "PR33 generates and validates human decision artifacts with safe_to_apply false."),
    PhaseSpec("pr34_ai_promotion_apply_dry_run", 34, "approved-only dry run", "ai_promotion_apply_dry_run.py", lambda p: [p.stage_file("pr32_ai_promotion_plan", "ai-candidate-promotion-plan.json"), _approval_decisions(p), _approval_validation(p)], lambda p: [p.stage_file("pr34_ai_promotion_apply_dry_run", "ai-approved-promotion-apply-dry-run.json")], _cmd_pr34, "PR34 produces an approved-only dry-run plan without applying promotions.", "dry_apply"),
    PhaseSpec("pr35_ai_candidate_core_input_apply", 35, "candidate core-input apply to isolated files", "ai_candidate_core_input_apply.py", lambda p: [p.stage_file("pr34_ai_promotion_apply_dry_run", "ai-approved-promotion-apply-dry-run.json")], lambda p: [p.stage_file("pr35_ai_candidate_core_input_apply", "ai-candidate-core-input-apply-manifest.json")], _cmd_pr35, "PR35 applies approved operations only to isolated candidate input files.", "isolated_candidate_apply"),
    PhaseSpec("pr36_ai_candidate_core_input_ingest", 36, "candidate core-input ingestion workflow", "ai_candidate_core_input_ingest_workflow.py", lambda p: [p.stage_file("pr35_ai_candidate_core_input_apply", "ai-candidate-core-input-apply-manifest.json")], lambda p: [p.stage_file("pr36_ai_candidate_core_input_ingest", "ai-candidate-core-input-ingest-manifest.json")], _cmd_pr36, "PR36 ingests isolated candidate core inputs only."),
    PhaseSpec("pr37_ai_candidate_normalized_review", 37, "candidate normalized promotion review", "ai_candidate_normalized_promotion_review.py", lambda p: [p.stage_file("pr36_ai_candidate_core_input_ingest", "ai-candidate-core-input-ingest-manifest.json")], lambda p: [p.stage_file("pr37_ai_candidate_normalized_review", "ai-candidate-normalized-promotion-review.json")], _cmd_pr37, "PR37 reviews candidate normalized outputs without core apply."),
)


PHASE_BY_ID = {phase.phase_id: phase for phase in TOPOLOGY_AI_PHASES}
PHASE_BY_PR = {f"pr{phase.pr_number}": phase for phase in TOPOLOGY_AI_PHASES}


def resolve_phase_id(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in PHASE_BY_PR:
        return PHASE_BY_PR[normalized].phase_id
    if normalized in PHASE_BY_ID:
        return normalized
    raise ValueError(f"unknown topology_ai phase id: {value}")


def selected_phases(start: str, end: str) -> list[PhaseSpec]:
    start_id = resolve_phase_id(start)
    end_id = resolve_phase_id(end)
    ids = [phase.phase_id for phase in TOPOLOGY_AI_PHASES]
    start_index = ids.index(start_id)
    end_index = ids.index(end_id)
    if end_index < start_index:
        raise ValueError(f"end phase {end} comes before start phase {start}")
    return list(TOPOLOGY_AI_PHASES[start_index : end_index + 1])
