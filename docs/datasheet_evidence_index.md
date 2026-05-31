# Datasheet Evidence Index

`scripts/datasheet_evidence_index.py` builds deterministic, evidence-only datasheet artifacts for PR39. It parses local datasheet text, attempts local PDF text extraction when available, records page evidence blocks, and emits bounded candidate facts for later review and PR26 packet context.

It does not call AI, does not require qwen_vision, does not fetch network content, and does not directly apply candidates to core topology/current/rating artifacts.

## Inputs

```bash
python scripts/datasheet_evidence_index.py \
  --project TestProject \
  --datasheets-dir exports/datasheets \
  --bom exports/TestProject-bom.json \
  --part-info-index exports/TestProject-part-info-index.json \
  --missing-data-manifest exports/TestProject-missing-data-manifest.json \
  --out-dir exports/TestProject/datasheet_evidence_index
```

Supported flags:

- `--datasheets-dir`: required local datasheet directory.
- `--bom`: optional BOM context for refdes/MPN targeting.
- `--part-info-index`: optional existing part info index context.
- `--missing-data-manifest`: optional missing-data context.
- `--strict`: return nonzero when blockers are present.
- `--max-pages`: limit extracted pages.
- `--include-tables`: reserved for table extraction metadata; PR39 records table extraction as unavailable.
- `--text-only`: skip PDF extraction attempts.
- `--schema`: schema path for callers that validate outputs.

## Outputs

The script writes only to `--out-dir`:

- `datasheet-evidence-index.json`
- `datasheet-extraction-candidates.json`
- `datasheet-extraction-status.json`
- `datasheet-extraction-blockers.json`
- `datasheet-extraction-review.json`

The schema is `schemas/datasheet_evidence_index_schema.json`.

## Extraction Limits

Extraction is regex and local-text based. It is intentionally conservative:

- Every candidate includes `source_file`, `page`, `evidence_quote`, `extraction_method`, and `confidence`.
- Deterministic unit normalization is limited to A/mA/uA, V/mV, W/mW, ohm/mOhm/kOhm, and F/uF/nF/pF.
- Ambiguous ranges, unclear min/typ/max context, footnote-dependent values, and unsupported units route to human review.
- Missing evidence blocks candidate usability.
- Connector-wide current ratings are not expanded to pins.
- Regulator input/output side is not inferred unless the evidence explicitly says output current.
- Missing or unknown current is never treated as zero.

## PR26 Context

`ai_packet_phase_build.py` accepts optional `--datasheet-evidence-index PATH`. When provided, bounded candidate snippets are included in packet `context.json` under `datasheet_references`. This improves packet quality by placing local evidence next to the missing-data item, while preserving packet bounds.

The PR26 hook remains review-only: it does not call AI, does not validate candidates as accepted facts, and does not apply anything to core artifacts.

## Inspection

```bash
python -m json.tool exports/TestProject/datasheet_evidence_index/datasheet-evidence-index.json | head -80
python -m json.tool exports/TestProject/datasheet_evidence_index/datasheet-extraction-candidates.json | head -120
python -m json.tool exports/TestProject/datasheet_evidence_index/datasheet-extraction-review.json | head -120
```

Treat candidates as evidence-backed review inputs, not authoritative engineering conclusions.
