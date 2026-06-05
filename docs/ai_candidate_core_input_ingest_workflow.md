# AI Candidate Core Input Ingest Workflow v0 (PR36)

## Overview

`ai_candidate_core_input_ingest_workflow.py` consumes PR35 candidate core-input files and runs existing ingestion scripts only into isolated candidate output paths.

PR36 does not call AI. PR36 does not write core normalized outputs, does not overwrite canonical current/rating/topology/copper/margin artifacts, does not run allocation, does not run calculations, and does not merge addenda into authoritative topology data.

## Inputs

PR36 reads PR35 artifacts from `--candidate-input-dir`:

```bash
exports/TestProject/ai_candidate_core_inputs/
  ai-candidate-core-input-apply-manifest.json
  ai-candidate-core-input-apply-status.json
  ai-candidate-current-model-input.json
  ai-candidate-rating-model-input.json
  ai-candidate-role-addenda.json
  ai-candidate-pin-role-addenda.json
  ai-candidate-rail-relationship-hints.json
  ai-candidate-passive-support-inputs.json
```

## Outputs

PR36 writes only under `--out-dir`:

```bash
exports/TestProject/ai_candidate_core_ingested/
  ai-candidate-core-input-ingest-manifest.json
  ai-candidate-core-input-ingest-status.json
  ai-candidate-current-model-ingest-input.json
  ai-candidate-rating-current-model-ingest-input.json
  ai-candidate-current-models-normalized.json
  ai-candidate-rating-current-models-normalized.json
  ai-candidate-rating-models-normalized.json
  ai-candidate-addenda-ingest-index.json
  ai-candidate-core-input-ingest-review.json
  ai-candidate-core-input-ingest-blockers.json
```

These candidate normalized files are not core outputs. They are inputs for future explicit stages only.

## Ingestion Behavior

PR36 may run:

- `current_model_ingest.py` against PR36-local `ai-candidate-current-model-ingest-input.json`
- `current_model_ingest.py` against PR36-local `ai-candidate-rating-current-model-ingest-input.json`
- `rating_model_ingest.py` against `ai-candidate-rating-current-models-normalized.json`

The two ingest-input files are adapters generated from PR35 candidate records into the exact shape expected by the existing ingestion scripts. They are written only under `--out-dir` and are not canonical inputs.

Each subprocess is invoked with an argument array, never `shell=True`. Step records include script, arguments, input path, output path, return code, status, stdout preview, and stderr preview.

PR36 does not run `topology_current_allocate.py`, `topology_copper_calculate.py`, or `topology_margin_calculate.py`.

## Addenda

PR36 indexes candidate role, pin-role, rail relationship, and passive support addenda. It always marks:

- `safe_to_merge_automatically: false`
- `merged_addenda: false`

Addenda require a future merge validator and are not merged in PR36.

## Provenance

PR35 approval and operation provenance may not be preserved by the existing ingestion scripts. PR36 records known provenance gaps in the review and blockers artifacts instead of altering ingestion behavior or silently hiding the gap.

## CLI

```bash
python scripts/ai_candidate_core_input_ingest_workflow.py \
  --project TestProject \
  --candidate-input-dir exports/TestProject/ai_candidate_core_inputs \
  --out-dir exports/TestProject/ai_candidate_core_ingested
```

Optional:

```bash
--current-model-ingest-script scripts/current_model_ingest.py
--rating-model-ingest-script scripts/rating_model_ingest.py
--skip-current
--skip-rating
--strict
```

## Manual Validation Commands

```bash
python -m py_compile scripts/ai_candidate_core_input_ingest_workflow.py tests/test_ai_candidate_core_input_ingest_workflow.py
python -m pytest tests/test_ai_candidate_core_input_ingest_workflow.py -v
git diff --check
```

Manual artifact inspection:

```bash
python -m json.tool exports/TestProject/ai_candidate_core_ingested/ai-candidate-core-input-ingest-manifest.json | head -160
python -m json.tool exports/TestProject/ai_candidate_core_ingested/ai-candidate-core-input-ingest-status.json | head -160
python -m json.tool exports/TestProject/ai_candidate_core_ingested/ai-candidate-current-model-ingest-input.json | head -160
python -m json.tool exports/TestProject/ai_candidate_core_ingested/ai-candidate-rating-current-model-ingest-input.json | head -160
python -m json.tool exports/TestProject/ai_candidate_core_ingested/ai-candidate-current-models-normalized.json | head -160
python -m json.tool exports/TestProject/ai_candidate_core_ingested/ai-candidate-rating-current-models-normalized.json | head -160
python -m json.tool exports/TestProject/ai_candidate_core_ingested/ai-candidate-rating-models-normalized.json | head -160
python -m json.tool exports/TestProject/ai_candidate_core_ingested/ai-candidate-core-input-ingest-review.json | head -160
python -m json.tool exports/TestProject/ai_candidate_core_ingested/ai-candidate-core-input-ingest-blockers.json | head -160
```

## Future PRs

Future work remains explicit and separate:

1. Explicit candidate normalized promotion review.
2. Explicit core-input apply with opt-in flag.
3. Addenda merge validator.
4. Missing-data readiness rerun.
5. Allocation/calculation rerun only after explicit promotion.
