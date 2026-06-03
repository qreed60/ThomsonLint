#!/usr/bin/env bash
set -euo pipefail

PROJECT="${PROJECT:-TestProject}"
RUN_ID="${RUN_ID:-full_run_001}"
MODEL="${MODEL:-qwen35_2b}"
OPENAI_BASE_URL="${OPENAI_BASE_URL:-http://192.168.5.5:1234/v1}"
OPENAI_API_KEY="${OPENAI_API_KEY:-lm-studio}"
MAX_TOKENS="${MAX_TOKENS:-8192}"

EVIDENCE_INDEX="${EVIDENCE_INDEX:-exports/TestProject/full_run_001/phase03_datasheet_evidence_reviewed_clean_20260601T185417Z/datasheet_evidence/datasheet-evidence-index.json}"

export OPENAI_BASE_URL
export OPENAI_API_KEY
export MODEL

if [ ! -f "$EVIDENCE_INDEX" ]; then
  echo "ERROR: missing evidence index: $EVIDENCE_INDEX"
  exit 1
fi

PACKET_ROOT="${PACKET_ROOT:-$(find "exports/$PROJECT/phase_runs/topology_ai" \
  -path "*/pr26_ai_packet_build/packets" \
  -type d \
  -printf '%T@ %p\n' \
  | sort -nr \
  | head -1 \
  | cut -d' ' -f2-)}"

if [ -z "$PACKET_ROOT" ] || [ ! -d "$PACKET_ROOT" ]; then
  echo "ERROR: could not locate PR26 packet root"
  exit 1
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
MODEL_SAFE="$(printf '%s' "$MODEL" | tr -cs 'A-Za-z0-9_-' '_')"

PHASE8_DIR="exports/$PROJECT/$RUN_ID/phase08_ai_response_dry_run_${MODEL_SAFE}_${STAMP}"
LIST_DIR="$PHASE8_DIR/packet_lists"
RESP_DIR="$PHASE8_DIR/responses_by_stage"
RAW_DIR="$PHASE8_DIR/raw_by_stage"
FINAL_RESPONSES="$PHASE8_DIR/final_responses"
LOG_DIR="$PHASE8_DIR/logs"

mkdir -p "$LIST_DIR" "$RESP_DIR" "$RAW_DIR" "$FINAL_RESPONSES" "$LOG_DIR"

LOG="$LOG_DIR/phase8.log"

echo "PHASE8_DIR=$PHASE8_DIR" | tee -a "$LOG"
echo "PACKET_ROOT=$PACKET_ROOT" | tee -a "$LOG"
echo "EVIDENCE_INDEX=$EVIDENCE_INDEX" | tee -a "$LOG"
echo "MODEL=$MODEL" | tee -a "$LOG"

PACKET_COUNT="$(find "$PACKET_ROOT" -mindepth 2 -maxdepth 2 -name request.json | wc -l)"
echo "packet_count=$PACKET_COUNT" | tee -a "$LOG"

for STAGE in 12A 12B 12H; do
  LIST="$LIST_DIR/${STAGE}_packet_dirs.txt"

  find "$PACKET_ROOT" -mindepth 1 -maxdepth 1 -type d -name "${STAGE}-*" \
    | sort > "$LIST"

  COUNT="$(wc -l < "$LIST")"
  echo "stage=$STAGE count=$COUNT" | tee -a "$LOG"

  if [ "$COUNT" -eq 0 ]; then
    continue
  fi

  mkdir -p "$RESP_DIR/$STAGE" "$RAW_DIR/$STAGE"

  python3 scripts/ai_generate_easy_response_batch.py \
    --packet-list "$LIST" \
    --out-dir "$RESP_DIR/$STAGE" \
    --raw-dir "$RAW_DIR/$STAGE" \
    --max-tokens "$MAX_TOKENS" \
    2>&1 | tee -a "$LOG"

  python3 scripts/ai_response_preflight_validate.py \
    --responses-dir "$RESP_DIR/$STAGE" \
    --packet-list "$LIST" \
    2>&1 | tee -a "$LOG"
done

find "$RESP_DIR" -mindepth 2 -maxdepth 2 -type f -name '*.json' \
  -exec cp {} "$FINAL_RESPONSES"/ \;

FINAL_COUNT="$(find "$FINAL_RESPONSES" -maxdepth 1 -type f -name '*.json' | wc -l)"
echo "final_response_count=$FINAL_COUNT" | tee -a "$LOG"

if [ "$FINAL_COUNT" -ne "$PACKET_COUNT" ]; then
  echo "ERROR: response count mismatch: final=$FINAL_COUNT packets=$PACKET_COUNT"
  exit 1
fi

./scripts/run_phase_driver.sh "$PROJECT" \
  --workflow topology_ai \
  --start pre01 \
  --end pr37 \
  --allow-existing-outputs \
  --responses-dir "$FINAL_RESPONSES" \
  --datasheet-evidence-index "$EVIDENCE_INDEX" \
  2>&1 | tee -a "$LOG"

RUN_DIR="$(ls -td exports/$PROJECT/phase_runs/topology_ai/* | head -1)"
echo "VALIDATION_RUN_DIR=$RUN_DIR" | tee -a "$LOG"

python3 - "$PHASE8_DIR" "$RUN_DIR" "$FINAL_RESPONSES" "$PACKET_COUNT" <<'PY'
import json
import sys
from pathlib import Path
from collections import Counter

phase8_dir = Path(sys.argv[1])
run_dir = Path(sys.argv[2])
final_responses = Path(sys.argv[3])
packet_count = int(sys.argv[4])

status = json.loads((run_dir / "phase-driver-status.json").read_text())
blockers = json.loads((run_dir / "phase-driver-blockers.json").read_text())
val = json.loads((run_dir / "pr27_ai_extraction_validate" / "ai-extraction-validation.json").read_text())

response_files = sorted(final_responses.glob("*.json"))

stage_counts = Counter()
response_status_counts = Counter()
total_extracted = 0
total_unknown = 0

for path in response_files:
    d = json.loads(path.read_text())
    stage_counts[path.name.split("-")[0]] += 1
    response_status_counts[d.get("status")] += 1
    total_extracted += len(d.get("extracted_items", []))
    total_unknown += len(d.get("unknown_items", []))

pr27_status_counts = Counter(row.get("status") for row in val["packet_results"])

gates = {
    "response_count_matches_packet_count": len(response_files) == packet_count,
    "pr27_all_accepted": dict(pr27_status_counts) == {"accepted": packet_count},
    "driver_passed": status.get("overall_status") == "passed",
    "no_blockers": status.get("blocker_count") == 0,
    "not_safe_for_core_apply": status.get("safe_for_core_apply") is False,
    "not_ready_for_core_apply": status.get("ready_for_core_apply") is False,
    "no_core_writes": status.get("wrote_core_artifacts") is False,
    "no_allocation_rerun": status.get("ran_post_promotion_allocation") is False,
    "no_calculation_rerun": status.get("ran_post_promotion_calculations") is False,
}

summary = {
    "phase": 8,
    "phase_name": "one_command_ai_response_dry_run",
    "phase8_dir": str(phase8_dir),
    "validation_run_dir": str(run_dir),
    "final_responses": str(final_responses),
    "packet_count": packet_count,
    "final_response_count": len(response_files),
    "response_stage_counts": dict(stage_counts),
    "response_status_counts": dict(response_status_counts),
    "total_extracted_items": total_extracted,
    "total_unknown_items": total_unknown,
    "driver_overall_status": status.get("overall_status"),
    "driver_stage_counts": status.get("stage_counts"),
    "blocker_count": status.get("blocker_count"),
    "blockers": blockers.get("blockers"),
    "safe_for_core_apply": status.get("safe_for_core_apply"),
    "ready_for_core_apply": status.get("ready_for_core_apply"),
    "wrote_core_artifacts": status.get("wrote_core_artifacts"),
    "ran_post_promotion_allocation": status.get("ran_post_promotion_allocation"),
    "ran_post_promotion_calculations": status.get("ran_post_promotion_calculations"),
    "pr27_status_counts": dict(pr27_status_counts),
    "gates": gates,
    "phase8_automation_pass": all(gates.values()),
}

(phase8_dir / "phase8-summary.json").write_text(json.dumps(summary, indent=2) + "\n")

md = f"""# Phase 8 AI Response Dry Run

Validation run: {run_dir}

- Packet count: {packet_count}
- Final response count: {len(response_files)}
- Response stage counts: {dict(stage_counts)}
- PR27 status counts: {dict(pr27_status_counts)}
- Driver overall status: {status.get("overall_status")}
- Blocker count: {status.get("blocker_count")}
- safe_for_core_apply: {status.get("safe_for_core_apply")}
- ready_for_core_apply: {status.get("ready_for_core_apply")}
- wrote_core_artifacts: {status.get("wrote_core_artifacts")}
- ran_post_promotion_allocation: {status.get("ran_post_promotion_allocation")}
- ran_post_promotion_calculations: {status.get("ran_post_promotion_calculations")}
- Extracted items: {total_extracted}
- Unknown items: {total_unknown}

phase8_automation_pass: {summary["phase8_automation_pass"]}
"""

(phase8_dir / "PHASE8_AI_RESPONSE_DRY_RUN.md").write_text(md)
Path("runbooks/full_run_001_phase8_automation.md").write_text(md)

print(json.dumps(summary, indent=2))
print()
print("PHASE8_DIR", phase8_dir)
print("phase8_automation_pass", summary["phase8_automation_pass"])

raise SystemExit(0 if summary["phase8_automation_pass"] else 1)
PY
