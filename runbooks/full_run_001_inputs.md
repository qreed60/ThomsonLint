# ThomsonLint Full Run 001 Inputs

## Control Rule

This input set is frozen for Full Run 001. Do not change input paths mid-run. If any input changes, stop and create a new runbook or a new run ID.

## Project

- Project name: TestProject
- Run ID: full_run_001
- Baseline tag: topology-ai-pr49-baseline

## Input Paths

- BOM JSON: TestProject/post_conversion/TestProject-bom.json
- Schematic export JSON: TestProject/post_conversion/TestProject-thomson-export-sch.json
- Board/stack/copper export root: TestProject/post_conversion
- Datasheets directory: .agents_tmp/pre_clean_phase6_run_20260525T104621Z/datasheets
- Datasheet manifest: none confirmed yet
- Datasheet evidence output root: exports/TestProject/datasheet_evidence_full_run_<UTC_STAMP>
- Topology AI phase-run output root: exports/TestProject/phase_runs/topology_ai

## Known Required Datasheet Matches

- FDS4435BZ -> expected file: 032_ONSEMI_FDS4435BZ.pdf
- S2B-XH-A (LF)(SN) -> expected JST connector datasheet
- GRM155R71H104KE14D -> expected Murata capacitor datasheet
- C2012X5R1H106K125AC -> expected TDK capacitor datasheet

## Scope for Full Run 001

Included:
- Deterministic topology workflow
- Missing-data manifest
- Datasheet evidence index
- PR26 packet generation
- External/import-only AI responses
- PR27 through PR37 candidate validation/review path

Excluded unless separately approved:
- Core artifact overwrite
- Automatic current allocation rerun
- Automatic copper/via/thermal calculation rerun
- Treating component/connector ratings as branch operating currents
- Final compliance/pass-fail claims from AI output

## Safety Expectations

Expected throughout Full Run 001:
- safe_for_core_apply: false
- ready_for_core_apply: false
- wrote_core_artifacts: false
- ran_post_promotion_allocation: false
- ran_post_promotion_calculations: false

## Notes

- Datasheet ratings are component or connector capabilities.
- Actual branch operating current remains unresolved unless explicit circuit/load evidence is provided.
- AI responses must use target_type exactly as provided in request.json.
- Generated exports and phase_runs are not committed.
