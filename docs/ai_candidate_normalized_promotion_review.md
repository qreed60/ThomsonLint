# AI Candidate Normalized Promotion Review v0 (PR37)

## Overview

`ai_candidate_normalized_promotion_review.py` consumes PR36 candidate normalized outputs and creates a review-only comparison against optional existing core normalized outputs.

PR37 does not call AI. PR37 does not write core artifacts, does not write core normalized outputs, does not run ingestion, does not run allocation, does not run calculations, and does not merge addenda into authoritative topology data. PR37 also does not make pass/fail, compliance, or final recommendation judgments.

## Inputs

PR37 reads PR36 artifacts from `--candidate-ingested-dir`:

```bash
exports/TestProject/ai_candidate_core_ingested/
  ai-candidate-core-input-ingest-manifest.json
  ai-candidate-core-input-ingest-status.json
  ai-candidate-current-models-normalized.json
  ai-candidate-rating-models-normalized.json
  ai-candidate-core-input-ingest-review.json
  ai-candidate-core-input-ingest-blockers.json
  ai-candidate-addenda-ingest-index.json
```

Optional core comparison inputs:

```bash
--core-current-models-normalized exports/TestProject-current-models-normalized.json
--core-rating-models-normalized exports/TestProject-rating-models-normalized.json
```

If core normalized outputs are unavailable, `--allow-missing-core` keeps the review informational and classifies candidate records as add candidates with warnings. In strict mode without that flag, missing core inputs fail the review.

## Outputs

PR37 writes only under `--out-dir`:

```bash
exports/TestProject/ai_candidate_normalized_review/
  ai-candidate-normalized-promotion-review.json
  ai-candidate-normalized-promotion-status.json
  ai-candidate-current-normalized-diff.json
  ai-candidate-rating-normalized-diff.json
  ai-candidate-normalized-approval-readiness.json
  ai-candidate-normalized-review-blockers.json
```

These artifacts are review-only. Candidate normalized records remain inputs for future explicit stages only.

## Review Behavior

PR37 compares normalized current records by explicit normalized identity fields such as record type, target type, branch, rail, refdes, pin, and current type. It compares normalized rating records by explicit target and rating fields such as target type, normalized target type, refdes, pin, rail, branch, and normalized rating name.

Records are classified as:

- `add_candidate`
- `duplicate_existing`
- `conflict_with_core`
- `missing_identity`
- `missing_value`
- `missing_unit`
- `provenance_gap`
- `unsupported_record`

PR37 does not infer missing current values, does not treat unknown current as zero, does not infer missing ratings, does not expand connector-wide ratings to pins, and does not infer regulator input/output side.

Human review remains required for every item. A future core apply stage is required. `ready_for_core_apply` and `safe_for_core_apply` are always false in PR37, and `requires_future_core_apply_stage` is always true.

## Addenda

PR37 reads the PR36 addenda index only as review context. Addenda are not merged. Addenda with records remain blocked for a future merge validator.

## CLI

```bash
python scripts/ai_candidate_normalized_promotion_review.py \
  --project TestProject \
  --candidate-ingested-dir exports/TestProject/ai_candidate_core_ingested \
  --out-dir exports/TestProject/ai_candidate_normalized_review \
  --allow-missing-core
```

Optional:

```bash
--core-current-models-normalized exports/TestProject-current-models-normalized.json
--core-rating-models-normalized exports/TestProject-rating-models-normalized.json
--strict
--allow-missing-core
```

## Manual Validation Commands

```bash
python -m py_compile scripts/ai_candidate_normalized_promotion_review.py tests/test_ai_candidate_normalized_promotion_review.py
python -m pytest tests/test_ai_candidate_normalized_promotion_review.py -v
git diff --check
```

Manual artifact generation:

```bash
python scripts/ai_candidate_normalized_promotion_review.py \
  --project TestProject \
  --candidate-ingested-dir exports/TestProject/ai_candidate_core_ingested \
  --out-dir exports/TestProject/ai_candidate_normalized_review \
  --allow-missing-core
```

Manual artifact inspection:

```bash
python -m json.tool exports/TestProject/ai_candidate_normalized_review/ai-candidate-normalized-promotion-review.json | head -160
python -m json.tool exports/TestProject/ai_candidate_normalized_review/ai-candidate-normalized-promotion-status.json | head -160
python -m json.tool exports/TestProject/ai_candidate_normalized_review/ai-candidate-current-normalized-diff.json | head -160
python -m json.tool exports/TestProject/ai_candidate_normalized_review/ai-candidate-rating-normalized-diff.json | head -160
python -m json.tool exports/TestProject/ai_candidate_normalized_review/ai-candidate-normalized-approval-readiness.json | head -160
python -m json.tool exports/TestProject/ai_candidate_normalized_review/ai-candidate-normalized-review-blockers.json | head -160
```

## Future PRs

Future work remains explicit and separate:

1. Explicit core-input apply with opt-in flag.
2. Addenda merge validator.
3. Missing-data readiness rerun.
4. Allocation/calculation rerun only after explicit promotion.
5. Report integration and human review summary.
