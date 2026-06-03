# Phase Driver Topology AI Workflow

PR38 added a workflow-aware phase driver layer for the topology/current/rating/calculation and AI-assisted candidate workflow. PR40 adds deterministic prerequisite generation/location before PR16. PR41 adds a run-local current model seed/template before PR19.

The original phase driver covered only the evidence_review workflow, using numeric phases 1-22 and OpenHands phase prompts. That workflow remains valid:

```bash
./scripts/run_phase_driver.sh TestProject 1 22
```

## Full-Plan Assessment Profiles

Numeric full-plan prompt generation supports `THOMSONLINT_ASSESSMENT_PROFILE`.
The default, unset value is equivalent to `strict` and preserves the existing
phase prompts.

- `strict`: existing behavior; intermediate phases keep the current constrained
  evidence-review wording.
- `balanced`: phases 13-19 may record separately classified engineering concern
  candidates, blocked verifications, datasheet checks, human review questions,
  and calculation-needed items when they are evidence-linked and avoid final
  pass/fail language.
- `engineering`: same safety gates as `balanced`, intended for deeper hardware
  engineering assessment before final findings are written.

Engineering assessment output is intermediate review material only. Final
findings remain gated by Phase 19 and the findings validator: only verified,
evidence-backed items may become final findings, and no AI output may mutate
core artifacts.

For Phase 13, the standard image evidence review remains
`exports/<project>-image-evidence-review.json`. When the profile is `balanced`
or `engineering`, Phase 13 also writes
`exports/<project>-vision-engineering-annotations.json`. That annotation
artifact records page-level engineering observations, concern candidates,
blocked verifications, datasheet checks, calculations needed, and human-review
questions. These annotations are not verified findings; they are review
candidates for later concern generation and must remain separate until a later
verified-finding gate proves them.

For Phase 18, the canonical candidate artifact remains
`exports/<project>-candidate-findings.json`. In `balanced` or `engineering`
profiles, that artifact separates `verified_finding_candidates` from
`engineering_concern_candidates`, `blocked_verification_candidates`,
`datasheet_check_candidates`, `calculation_candidates`,
`human_review_candidates`, and `rejected_or_unsupported_candidates`. These
categories are pre-final-review classifications only. Engineering concerns and
blocked verifications are not final findings, and final gates remain strict.

For PR16-PR37, use the explicit topology_ai workflow. PR40 allows `pre01` starts from post-conversion exports:

```bash
./scripts/run_phase_driver.sh TestProject --workflow topology_ai --start pre01 --end pr37 --dry-run
```

The topology_ai workflow writes run artifacts under:

```text
exports/TestProject/phase_runs/topology_ai/<run-id>/
```

Use `--out-dir` to choose a different workflow run directory. Use `--allow-existing-outputs` only when intentionally reusing a run directory.

## Workflow Boundary

The topology_ai deterministic stages are run directly by the phase driver. They are not routed through OpenHands and they do not reinterpret the old numeric evidence_review phases.

PR40 consumes post-conversion exports when available, especially:

- `TestProject/post_conversion/TestProject-bom.json`
- `TestProject/post_conversion/TestProject-thomson-export-sch.json`
- `TestProject/post_conversion/TestProject-thomson-export-brd.json`
- `TestProject/post_conversion/TestProject-thomson-export-stack.json`

PR16 no longer assumes `exports/TestProject-branch-topology-enriched.json` already exists. It consumes the branch topology enriched artifact generated or located in the current workflow run directory.

PR41 means the workflow no longer requires `exports/TestProject-current-model.json` to exist before PR19. `pre10_current_model_seed` copies an explicit current model into the run directory when one exists; otherwise it creates an empty/manual-review seed. PR19 consumes that run-local seed. Unknown current remains unknown, not zero, and current allocation may still block or produce no allocations when there are no usable numeric current records.

PR26 packet generation can run from the missing-data manifest even if current/rating allocation or margin stages are blocked. It remains prompt scaffolding only.

PR26 builds prompt-ready packet scaffolding only. It does not call AI services, does not invoke qwen_vision, and does not fabricate raw AI extraction responses. The driver records qwen_vision as configured only when the environment indicates a qwen vision model; `qwen_vision_invoked` remains false unless an implemented script actually invokes it.

PR42 adds an offline response import stage between PR26 and PR27. By default it is `not_applicable`, and the PR27 missing-response block is still expected when no raw responses exist. When `--responses-dir PATH` is supplied, the driver imports externally prepared response JSON into `pr26_ai_packet_build/packets/<packet_id>/raw_response.json` before PR27 validation. Fixture response directories are also recognized under `--fixtures-dir` as `responses/` or `ai_responses/`.

PR44 adds explicit approval-decision input for fixture and manual review workflows. By default PR33 still writes a pending decision template and the driver never auto-approves candidates. When `--approval-decisions PATH` is supplied, PR33 validates that human-authored decision artifact against the run-local PR32 approval queue, copies it to the run-local PR32 promotion directory, writes validation beside it, and PR34 consumes those recorded paths.

After PR26 and optional response import, the driver checks for saved raw AI response artifacts. If they are missing, PR27 is marked `blocked_missing_input` and PR28-PR37 are skipped with a blocker reference. Existing or fixture AI artifacts are considered only when explicitly requested with `--fixtures-dir`, `--responses-dir`, or `--continue-with-existing-ai-artifacts`.

PR26-PR37 remain isolated and review-only. The driver does not write candidate outputs into authoritative core locations, does not apply promotions to core, does not merge addenda, and does not run post-promotion allocation or calculation reruns. Full core apply remains a future explicit stage.

PR33 uses the approval decision editor's existing output safety contract. Its decision and validation artifacts are written inside the same run-local PR32 promotion directory:

```text
exports/TestProject/phase_runs/topology_ai/<run-id>/pr32_ai_promotion_plan/
  ai-approval-decisions.json
  ai-approval-decision-validation.json
```

This is isolated same-run promotion directory mutation, not core mutation. PR34 consumes these exact recorded paths.

Run with response fixtures and explicit approval decisions:

```bash
./scripts/run_phase_driver.sh TestProject \
  --workflow topology_ai \
  --start pre01 \
  --end pr37 \
  --allow-existing-outputs \
  --responses-dir tests/fixtures/topology_ai_non_empty/responses \
  --approval-decisions tests/fixtures/topology_ai_non_empty/approval-decisions.json
```

The current-model fixture and rating-model fixture are intentionally separate.
For rating candidate coverage, use:

```bash
./scripts/run_phase_driver.sh TestProject \
  --workflow topology_ai \
  --start pre01 \
  --end pr37 \
  --allow-existing-outputs \
  --responses-dir tests/fixtures/topology_ai_rating_non_empty/responses \
  --approval-decisions tests/fixtures/topology_ai_rating_non_empty/approval-decisions.json
```

Both fixture sets are static offline JSON fixtures, not live AI output. The
rating fixture uses an explicit fuse current rating and must not infer
connector pins or regulator input/output side. Without `--approval-decisions`,
PR33 writes pending decisions and PR34 has zero approved operations.

PR34 and later stages remain dry-run/candidate-only. The driver does not apply candidates to core, merge addenda, rerun allocation/calculations, or mark core apply readiness true.

## Stage Order

1. `pre01_locate_post_conversion_exports`
2. `pre02_topology_map`
3. `pre03_topology_role_resolution`
4. `pre04_rail_relationships`
5. `pre05_copper_net_association`
6. `pre06_branch_topology`
7. `pre07_topology_geometry_review`
8. `pre08_branch_topology_enrichment`
9. `pr16_calculation_readiness`
10. `pre09_missing_data_manifest_or_readiness_seed`
11. `pre10_current_model_seed`
12. `pr17_schema_available`
13. `pr18_copper_calculation`
14. `pr19_current_model_ingest`
15. `pr20_current_allocation`
16. `pr21_copper_with_allocated_current`
17. `pr22_via_current_density`
18. `pr23_rating_model_ingest`
19. `pr24_fuse_margin`
20. `pr25_connector_pin_margin`
21. `pr26_ai_packet_build`
22. `pr26_ai_packet_response_import`
23. `pr27_ai_extraction_validate`
24. `pr28_ai_patch_build`
25. `pr29_ai_candidate_materialize`
26. `pr30_ai_candidate_adapter_outputs`
27. `pr31_ai_candidate_ingest`
28. `pr32_ai_promotion_plan`
29. `pr33_ai_approval_decisions`
30. `pr34_ai_promotion_apply_dry_run`
31. `pr35_ai_candidate_core_input_apply`
32. `pr36_ai_candidate_core_input_ingest`
33. `pr37_ai_candidate_normalized_review`

PR22 is copper/via current-density behavior, not margin behavior. PR23 rating ingestion occurs before PR24 and PR25 margins. PR30 creates adapter outputs/artifacts, not adapter scripts.

Prerequisite outputs are isolated under the workflow run directory. They are not written to the normal `exports/` root unless an existing script is invoked outside the driver.

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

Use the topology_ai driver for PR16-PR37 validation. Do not use the old numeric 1-22 evidence_review run for topology/AI validation. The prerequisite and current seed integrations do not call AI or qwen_vision, do not infer current, and do not apply AI candidates or write core promotion outputs.

For offline PR27 testing, provide response files with:

```bash
./scripts/run_phase_driver.sh TestProject \
  --workflow topology_ai \
  --start pre01 \
  --end pr37 \
  --responses-dir path/to/manual/responses \
  --allow-existing-outputs
```

Use `--allow-partial-responses` only when intentionally validating a subset of packet responses.
