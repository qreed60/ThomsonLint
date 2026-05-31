# Topology AI Non-Empty Fixture

PR44 adds a deterministic offline fixture path for the topology_ai workflow:

```bash
./scripts/run_phase_driver.sh TestProject \
  --workflow topology_ai \
  --start pre01 \
  --end pr37 \
  --allow-existing-outputs \
  --responses-dir tests/fixtures/topology_ai_non_empty/responses
```

The fixture responses are static, hand-authored test files. They are not live AI output. The driver imports them with PR42 response import and does not call an LLM or qwen_vision.

By default, PR33 creates pending approval decisions only. The driver never auto-approves candidates. To exercise the approved dry-run path, provide an explicit human approval decision artifact:

```bash
./scripts/run_phase_driver.sh TestProject \
  --workflow topology_ai \
  --start pre01 \
  --end pr37 \
  --allow-existing-outputs \
  --responses-dir tests/fixtures/topology_ai_non_empty/responses \
  --approval-decisions tests/fixtures/topology_ai_non_empty/approval-decisions.json
```

Inspect the key artifacts:

```bash
python -m json.tool exports/TestProject/phase_runs/topology_ai/<run-id>/pr27_ai_extraction_validate/ai-extraction-validation.json
python -m json.tool exports/TestProject/phase_runs/topology_ai/<run-id>/pr32_ai_promotion_plan/ai-candidate-approval-queue.json
python -m json.tool exports/TestProject/phase_runs/topology_ai/<run-id>/pr32_ai_promotion_plan/ai-approval-decisions.json
python -m json.tool exports/TestProject/phase_runs/topology_ai/<run-id>/pr34_ai_promotion_apply_dry_run/ai-approved-promotion-apply-dry-run.json
python -m json.tool exports/TestProject/phase_runs/topology_ai/<run-id>/pr37_ai_candidate_normalized_review/ai-candidate-normalized-promotion-review.json
```

PR34 and later stages remain dry-run/candidate-only. They do not write core artifacts, merge addenda, apply promotions to core, rerun allocation/calculations, or set `safe_for_core_apply` / `ready_for_core_apply` true. Core apply remains future explicit work.
