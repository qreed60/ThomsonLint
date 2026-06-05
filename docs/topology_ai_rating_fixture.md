# Topology AI Rating Fixture

PR45 adds a deterministic offline rating-model fixture path for the topology_ai
workflow:

```bash
./scripts/run_phase_driver.sh TestProject \
  --workflow topology_ai \
  --start pre01 \
  --end pr37 \
  --allow-existing-outputs \
  --responses-dir tests/fixtures/topology_ai_rating_non_empty/responses
```

This fixture is separate from `tests/fixtures/topology_ai_non_empty/`, which
exercises the current-model path. Both fixture sets are static, hand-authored
JSON files under `tests/fixtures`; neither is live AI output, and neither causes
the driver to call an LLM or qwen_vision.

The rating fixture contains one accepted `fuse_rating` extraction for `R50`
`current_max`. It is explicit by target type and refdes. It does not expand
connector-wide ratings to pins and does not infer regulator input/output side.

By default, PR33 writes pending approval decisions only. The driver never
auto-approves candidates. To exercise the approved PR34 dry-run path, provide
the explicit approval-decision fixture:

```bash
./scripts/run_phase_driver.sh TestProject \
  --workflow topology_ai \
  --start pre01 \
  --end pr37 \
  --allow-existing-outputs \
  --responses-dir tests/fixtures/topology_ai_rating_non_empty/responses \
  --approval-decisions tests/fixtures/topology_ai_rating_non_empty/approval-decisions.json
```

Inspect the key artifacts:

```bash
python -m json.tool exports/TestProject/phase_runs/topology_ai/<run-id>/pr27_ai_extraction_validate/ai-extraction-validation.json
python -m json.tool exports/TestProject/phase_runs/topology_ai/<run-id>/pr31_ai_candidate_ingest/ai-rating-models-normalized.json
python -m json.tool exports/TestProject/phase_runs/topology_ai/<run-id>/pr32_ai_promotion_plan/ai-candidate-approval-queue.json
python -m json.tool exports/TestProject/phase_runs/topology_ai/<run-id>/pr32_ai_promotion_plan/ai-approval-decisions.json
python -m json.tool exports/TestProject/phase_runs/topology_ai/<run-id>/pr34_ai_promotion_apply_dry_run/ai-approved-promotion-apply-dry-run.json
python -m json.tool exports/TestProject/phase_runs/topology_ai/<run-id>/pr37_ai_candidate_normalized_review/ai-candidate-normalized-promotion-review.json
```

PR34 and later stages remain dry-run/candidate-only. They do not write core
artifacts, merge addenda, apply promotions to core, rerun allocation or
calculations, or set `safe_for_core_apply` / `ready_for_core_apply` true. Core
apply remains future explicit work.
