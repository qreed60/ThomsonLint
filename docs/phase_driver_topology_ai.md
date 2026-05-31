# Phase Driver Topology AI Workflow

PR38 adds a workflow-aware phase driver layer for the topology/current/rating/calculation and AI-assisted candidate workflow.

The original phase driver covered only the evidence_review workflow, using numeric phases 1-22 and OpenHands phase prompts. That workflow remains valid:

```bash
./scripts/run_phase_driver.sh TestProject 1 22
```

For PR16-PR37, use the explicit topology_ai workflow:

```bash
./scripts/run_phase_driver.sh TestProject --workflow topology_ai --start pr16 --end pr37 --dry-run
```

The topology_ai workflow writes run artifacts under:

```text
exports/TestProject/phase_runs/topology_ai/<run-id>/
```

Use `--out-dir` to choose a different workflow run directory. Use `--allow-existing-outputs` only when intentionally reusing a run directory.

## Workflow Boundary

The topology_ai deterministic stages are run directly by the phase driver. They are not routed through OpenHands and they do not reinterpret the old numeric evidence_review phases.

PR26 builds prompt-ready packet scaffolding only. It does not call AI services, does not invoke qwen_vision, and does not fabricate raw AI extraction responses. The driver records qwen_vision as configured only when the environment indicates a qwen vision model; `qwen_vision_invoked` remains false unless an implemented script actually invokes it.

After PR26, the driver checks for saved raw AI response artifacts. If they are missing, PR27 is marked `blocked_missing_input` and PR28-PR37 are skipped with a blocker reference. Existing or fixture AI artifacts are considered only when explicitly requested with `--fixtures-dir` or `--continue-with-existing-ai-artifacts`.

PR26-PR37 remain isolated and review-only. The driver does not write candidate outputs into authoritative core locations, does not apply promotions to core, does not merge addenda, and does not run post-promotion allocation or calculation reruns. Full core apply remains a future explicit stage.

## Stage Order

1. `pr16_calculation_readiness`
2. `pr17_schema_available`
3. `pr18_copper_calculation`
4. `pr19_current_model_ingest`
5. `pr20_current_allocation`
6. `pr21_copper_with_allocated_current`
7. `pr22_via_current_density`
8. `pr23_rating_model_ingest`
9. `pr24_fuse_margin`
10. `pr25_connector_pin_margin`
11. `pr26_ai_packet_build`
12. `pr27_ai_extraction_validate`
13. `pr28_ai_patch_build`
14. `pr29_ai_candidate_materialize`
15. `pr30_ai_candidate_adapter_outputs`
16. `pr31_ai_candidate_ingest`
17. `pr32_ai_promotion_plan`
18. `pr33_ai_approval_decisions`
19. `pr34_ai_promotion_apply_dry_run`
20. `pr35_ai_candidate_core_input_apply`
21. `pr36_ai_candidate_core_input_ingest`
22. `pr37_ai_candidate_normalized_review`

PR22 is copper/via current-density behavior, not margin behavior. PR23 rating ingestion occurs before PR24 and PR25 margins. PR30 creates adapter outputs/artifacts, not adapter scripts.

## Driver Artifacts

Each topology_ai run emits:

- `phase-driver-manifest.json`
- `phase-driver-status.json`
- `phase-driver-stage-results.json`
- `phase-driver-artifact-index.json`
- `phase-driver-blockers.json`
- `phase-driver-inspection-commands.md`

The JSON artifacts are validated by `schemas/phase_driver_run_schema.json`.

Required safety booleans are fixed as:

```json
{
  "workflow_run_only": true,
  "wrote_core_artifacts": false,
  "applied_promotions_to_core": false,
  "merged_addenda": false,
  "ran_post_promotion_allocation": false,
  "ran_post_promotion_calculations": false,
  "safe_for_core_apply": false,
  "ready_for_core_apply": false
}
```

## Guidance

Use the topology_ai driver for PR16-PR37 validation. Do not use the old numeric 1-22 evidence_review run for topology/AI validation.
