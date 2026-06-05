# Current Model Seed

PR41 adds `scripts/current_model_seed.py`, a deterministic helper that prepares PR19 current-model input without inventing current values.

## Purpose

The topology AI workflow needs a current model artifact before `current_model_ingest.py` can run. Many projects do not yet have explicit current values. The seed stage creates a schema-valid input artifact that either copies an explicit current model into the workflow run directory or creates an empty manual-review seed/template.

Unknown current is not zero. The seed/template is not a calculation result and is not a core apply artifact.

## CLI

```bash
python scripts/current_model_seed.py \
  --project TestProject \
  --branch-topology-enriched exports/TestProject/phase_runs/topology_ai/<run-id>/pre08_branch_topology_enrichment/branch-topology-enriched.json \
  --role-resolution exports/TestProject/phase_runs/topology_ai/<run-id>/pre03_topology_role_resolution/topology-roles.json \
  --rail-relationships exports/TestProject/phase_runs/topology_ai/<run-id>/pre04_rail_relationships/rail-relationships.json \
  --missing-data-manifest exports/TestProject/phase_runs/topology_ai/<run-id>/pre09_missing_data_manifest_or_readiness_seed/missing-data-manifest.json \
  --calculation-readiness exports/TestProject/phase_runs/topology_ai/<run-id>/pr16_calculation_readiness/calculation-readiness-inventory.json \
  --existing-current-model exports/TestProject-current-model.json \
  --out exports/TestProject/phase_runs/topology_ai/<run-id>/pre10_current_model_seed/current-model-seed.json
```

Optional outputs are `--template-out`, `--status-out`, `--blockers-out`, and `--review-out`. `--strict` records a blocker when no explicit current model is available.

## Outputs

The script writes:

- `current-model-seed.json`
- `current-model-template.json`
- `current-model-seed-status.json`
- `current-model-seed-blockers.json`
- `current-model-seed-review.json`

When `--existing-current-model` points to a real file, the explicit current arrays are copied into `current-model-seed.json` without mutating the source file. When it is missing, all explicit current arrays are empty and manual-review placeholders are emitted separately under `manual_review_placeholders`.

## Manual Review

Placeholders include branch, rail, or component identities where deterministic topology context exposes them. They use null values and units, `requires_human_review: true`, and `usable_for_allocation: false`.

To supply current later, add explicit records to `branch_currents`, `rail_currents`, or `component_currents` from reviewed evidence. Do not move placeholder null values into explicit current arrays.

## Safety

The seed stage does not call AI, does not require qwen_vision, does not infer current, and does not write core artifacts. Required safety fields stay false:

- `generated_current_values`
- `inferred_current_values`
- `unknown_current_treated_as_zero`
- `safe_for_core_apply`
- `ready_for_core_apply`

`safe_for_allocation` is true only when explicit numeric current records exist.

## Topology AI Use

`topology_ai` runs `pre10_current_model_seed` before `pr19_current_model_ingest`. PR19 consumes the run-local `current-model-seed.json` instead of requiring `exports/TestProject-current-model.json` to exist before the workflow starts.
