# AI Packet Response Import

PR42 adds an offline import step for externally prepared raw AI packet responses. It lets PR27 and later review stages be tested without calling a local model.

The importer does not call AI, does not fetch network content, does not generate response text, and does not alter PR26 packet prompts or context.

## CLI

```bash
python scripts/ai_packet_response_import.py \
  --project TestProject \
  --packet-dir exports/TestProject/phase_runs/topology_ai/<run-id>/pr26_ai_packet_build \
  --responses-dir path/to/manual/responses \
  --out-dir exports/TestProject/phase_runs/topology_ai/<run-id>/pr26_ai_packet_response_import
```

Optional flags:

- `--response-glob "*.json"`
- `--allow-partial`
- `--validate-json-only`
- `--strict`
- `--manifest-out PATH`
- `--status-out PATH`
- `--blockers-out PATH`
- `--review-out PATH`

## Response File Layout

The source response directory may contain JSON files named:

- `<packet_id>.json`
- `<packet_id>_raw_response.json`
- `packet_<n>_response.json`, where `<n>` maps to packet queue order starting at 1
- `<packet_id>/raw_response.json`

The response payload may also carry `packet_id`. Every response must match an actual packet in `packet_queue.json`. Unknown packet IDs are rejected and routed to review/blockers rather than silently imported.

## Outputs

The importer writes:

- `ai-packet-response-import-manifest.json`
- `ai-packet-response-import-status.json`
- `ai-packet-response-import-blockers.json`
- `ai-packet-response-import-review.json`
- `ai-packet-response-import-index.json`

Each imported record includes packet ID, source path, destination path, source/imported SHA-256 hashes, JSON validity, match status, reason, and import timestamp.

Imported responses are copied to:

```text
<packet-dir>/packets/<packet_id>/raw_response.json
```

That is the layout consumed by `scripts/ai_extraction_validate.py`.

## Partial Responses

By default, missing packet responses block the import. With `--allow-partial`, available responses are imported and missing packet IDs are listed in the review artifact. PR27 can then validate available responses while reporting pending packets according to its existing validation rules.

## Safety

The importer is an offline file import tool. It does not execute an AI model and does not fabricate missing responses.

Required safety fields remain:

```json
{
  "imported_only": true,
  "generated_ai_content": false,
  "called_ai_service": false,
  "fabricated_response": false,
  "applied_to_core": false,
  "safe_for_core_apply": false,
  "ready_for_core_apply": false
}
```

## Driver Use

Default topology_ai behavior is unchanged:

```bash
./scripts/run_phase_driver.sh TestProject --workflow topology_ai --start pre01 --end pr37
```

Without responses, PR27 blocks with the expected missing raw response reason.

To import externally prepared responses before PR27:

```bash
./scripts/run_phase_driver.sh TestProject \
  --workflow topology_ai \
  --start pre01 \
  --end pr37 \
  --responses-dir path/to/manual/responses \
  --allow-existing-outputs
```

Use `--allow-partial-responses` when intentionally importing only a subset of packet responses.

## Inspection

```bash
python -m json.tool exports/TestProject/phase_runs/topology_ai/<run-id>/pr26_ai_packet_response_import/ai-packet-response-import-status.json
python -m json.tool exports/TestProject/phase_runs/topology_ai/<run-id>/pr26_ai_packet_response_import/ai-packet-response-import-index.json
python -m json.tool exports/TestProject/phase_runs/topology_ai/<run-id>/pr27_ai_extraction_validate/ai-extraction-validation.json
```
