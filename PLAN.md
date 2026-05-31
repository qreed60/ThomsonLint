1. OBJECTIVE
Create a linear, deep, and checkpointed ThomsonLint execution plan that follows `OPENHANDS_REVIEW.md` as source of truth and prevents shortcutting across evidence classes.

2. CONTEXT SUMMARY
This plan enforces a strict order: inspect inputs first, then setup/tool preflight, then converter execution, then framework inspection, then datasheet retrieval as a normal numbered phase, followed by separate evidence-class reviews (schematic, board/layout, stackup, BOM, images, datasheets), then cross-source consistency, candidate development, findings JSON, validation, report generation, and final summary.

3. APPROACH OVERVIEW

**UNVERIFIABLE RULES DIRECTIVE**:
Rules flagged as [UNVERIFIABLE] or [PARTIALLY VERIFIABLE] due to lack of 3D thermal simulation, physical printing requirements, subjective intent, or missing manual metadata (e.g., DFM_LIB_001, DFT_BUILD_001, DFT_MEAS_001, DFT_PROD_001, SCH_IC_001, SCH_OPT_001, THM_RISE_001, THM_HEAT_001, THM_COOL_001, AERO_VIB_001) must not be guessed or hallucinated. The agent must immediately output them as "Skipped: Unverifiable by AI / Requires Physical Testing / Requires 3D CAD" (or similar explicit limitation) in the final findings JSON or evidence inventory rather than attempting to forge a pass/fail condition.

Artifact-Based Phase Completion Rule:
A phase may not be marked complete based only on narrative text. If the phase defines a required artifact or validation JSON, that artifact must exist on disk, parse successfully if JSON, and contain the required pass field set to true before the phase can be marked complete. Verbal claims such as "phase complete", "gate passed", or "reviewed" are invalid unless backed by the required artifact.
Universal phase checkpoint artifact: `exports/<project>-phase-checkpoints.jsonl`. Every phase must append exactly one JSONL checkpoint row before moving to the next phase, and each row must include `phase_number`, `phase_name`, `started_at_utc`, `completed_at_utc`, `required_artifacts`, `artifacts_verified`, `validation_artifacts`, `validation_passed`, `blockers`, `phase_passed`. A phase is not complete unless its checkpoint row exists and `phase_passed=true`. If a phase has no separate artifact, the checkpoint row itself is the required artifact. Narrative text is not a checkpoint.
`exports/<project>-phase-checkpoints.jsonl` must record the phase-local gate result for each phase. The checkpoint row must include `repair_required=true` when a phase-local gate fails (i.e., when `phase_passed=false`).

Phase-Local Gate Enforcement Rule:
Each phase owns its own gate. A phase must not be marked complete until its required artifacts exist, parse successfully if JSON, and pass that phase’s validation criteria.
If a phase-local gate fails: stay in the same phase; repair only that phase artifact/work product; re-run that phase validation; repeat until pass or blocker; do not advance; do not defer failure to Phase 15, Phase 20, or final audit.

No Phase Consolidation:
Phases must be executed one at a time. Phases 8 through 17 must not be consolidated. Phases 18 and 19 must not be consolidated. Each phase must print its phase name, produce its required checkpoint artifacts, verify those artifacts, and only then proceed to the next phase.
Checkpoint-row enforcement: phases 8 through 17 must each append a distinct row in `exports/<project>-phase-checkpoints.jsonl`; phases 18 and 19 must each append a distinct row; one shared row must not mark multiple phases complete.

Phase 1 — Ingest ThomsonLint Workflow
Phase 2 — Inspect Inputs and Datasheets
Phase 3 — Setup and Tool Preflight
Phase 4 — Run Integrated Converter
Phase 5 — Inspect Findings Framework
Phase 6 — Full BOM Datasheet Retrieval
Phase 7 — Enforce Image Review Gate
Phase 8 — Review Schematic Evidence FULL
Phase 9 — Full Board/Layout JSON Evaluation
Phase 10 — Review Stackup and Manufacturing Evidence FULL
Phase 11 — Review DFM and Manufacturing Specifications
Phase 12 — Review BOM and Component Evidence FULL
Phase 13 — Review Image Evidence FULL
Phase 14 — Review Datasheet Evidence FULL
Phase 15 — Review Aerospace and Process Metadata
Phase 16 — Cross-Source Consistency Review
Phase 17 — Pre-Findings Gate Check
Phase 18 — Candidate Finding Development
Phase 19 — Write Findings JSON
Phase 20 — Validate and Repair Findings
Phase 21 — Generate Report
Phase 22 — Final Summary

Phase 12 AI Data Completion packet model:
**[Superseded by PR26–PR37]** The conceptual packet model described below was the pre-implementation design for AI-assisted data completion. It has been superseded by the implemented topology/current/rating/calculation-readiness plus AI-assisted candidate completion and review baseline (PR16–PR37). See the "AI-Assisted Candidate Completion and Review Baseline (PR26–PR37)" section below for the actual implemented workflow.

When the deterministic topology/missing-data pipeline needs AI-assisted data completion, it must be represented as one formal phase: `Phase 12: AI Data Completion`. Inside that phase, work is split into bounded stages and packets rather than a giant board-wide AI prompt. Stage examples include `12A` datasheet role/pin extraction, `12B` datasheet current model extraction, `12C` datasheet rating extraction, and `12D` passive/support component extraction. Each packet owns one task, prompt, bounded context, required output schema, validation result, checkpoint/status file, and accepted/rejected/human-review lifecycle state.

The packet execution loop is deterministic: build `packet_queue.json`, write per-packet `request.json`, `context.json`, `prompt.md`, and `status.json`, structurally validate the generated artifacts, and checkpoint phase status before any later runner can execute prompts. AI packet construction and future AI responses must not directly mutate core topology artifacts, current/copper/margin artifacts, or findings. Any future data fill must be validated and applied through an explicit patch step before calculations are rerun.

4. IMPLEMENTATION STEPS

## Phase 1 — Ingest ThomsonLint Workflow
- **Purpose**: Map `OPENHANDS_REVIEW.md` workflows to concrete actions and confirm phase order matches the numbered workflow order.
- **Files/tools to inspect/use**: `OPENHANDS_REVIEW.md`, `docs/REVIEWER_INSTRUCTIONS.md` (if present), `README.md` (if present).
- **Expected evidence/output**: Workflow-to-phase mapping table and explicit confirmation of the 1→22 linear sequence.
- **Validation/checkpoint before moving to next phase**: Mapping covers all workflows and preserves order without lettered side phases.
- **Risks or ways the agent could go wrong**: Inventing alternate sequence, skipping required workflows, or reintroducing side phases.

## Phase 2 — Inspect Inputs and Datasheets
- **Purpose**: Inspect `input/` and `datasheets/`, and record missing datasheets as evidence gaps rather than guessed values.
- **Files/tools to inspect/use**: `input/`, `datasheets/` (if present), explicit stackup inputs (`input/stackup.csv`, `input/stackup.json`, fab drawing PDFs, ODB++ archive/folder, IPC-2581 with cross-section, EDA stackup reports). Aerospace and process inputs when present: fab work order or board-build specification (for solder alloy and IPC class), conformal coating specification (for masking requirements), environmental or vibration profile document (for vibration retention assessment), chassis-mounting drawing (for chassis-ground keepout identification).
- **Expected evidence/output**: Complete file inventory and datasheet availability status. Explicitly record presence or absence of each aerospace/process input type; if absent, document as missing evidence for Phase 15.
- **Validation/checkpoint before moving to next phase**: At least one raw design input exists; datasheet gaps are explicitly recorded as missing evidence; aerospace/process input availability is explicitly documented (present or absent, not skipped).
- **Risks or ways the agent could go wrong**: Guessing datasheet parameters, ignoring missing-evidence tracking, or failing to record absence of aerospace/process documentation.

## Phase 3 — Setup and Tool Preflight
- **Purpose**: Ensure required local tools are available before any converter execution.
- **Files/tools to inspect/use**: `python3`, `pdftoppm`, `pdfinfo`, Ubuntu/Debian package `poppler-utils`.
- **Expected evidence/output**: Tool availability results and installation attempt status, plus `exports/tool-preflight-status.json`.
- **Validation/checkpoint before moving to next phase**:
  - Check `which python3`, `which pdftoppm`, `which pdfinfo`.
  - If `pdftoppm` or `pdfinfo` missing, attempt `apt-get update && apt-get install -y poppler-utils`.
  - If `sudo` is required and available, attempt `sudo apt-get update && sudo apt-get install -y poppler-utils`.
  - Verify post-install with `which pdftoppm`, `which pdfinfo`, `pdftoppm -v`, `pdfinfo -v`.
  - If install fails or tools remain unavailable when PDFs are present, stop and report blocker; no silent JSON-only fallback unless user explicitly approves fallback.
- **Required artifact fields**: `python3_available`, `pdftoppm_available`, `pdfinfo_available`, `install_attempted`, `install_command`, `install_succeeded`, `pdfs_present`, `fallback_used`, `user_approved_fallback`, `approval_source`, `json_only_review_approved`, `json_only_approval_source`, `overall_pass`.
- **Pass logic**: If PDFs are present, pass only when (`pdftoppm_available=true` and `pdfinfo_available=true`) or (image-render fallback is used and `user_approved_fallback=true`) or (JSON-only fallback is explicitly approved with `json_only_review_approved=true`). Do not treat image-render fallback approval as JSON-only approval.
- **Phase-local failure loop**: If `exports/tool-preflight-status.json` is missing or `overall_pass=false`, remain in Setup and Tool Preflight. Do not run the converter. Repair tool installation or stop for explicit user approval.
- **Risks or ways the agent could go wrong**: Running converter before tool preflight, skipping install attempt, or proceeding after failed preflight.

## Phase 4 — Run Integrated Converter
- **Purpose**: Generate review artifacts before evidence analysis.
- **Files/tools to inspect/use**: `python3 tools/run_converter_pipeline.py input --project-name example --clean`, `exports/` outputs.
- **Expected evidence/output**: `exports/` created, JSON exports loadable, PNG renders present when PDFs exist, conversion report files inspected.
- **Validation/checkpoint before moving to next phase**: JSON parsing succeeds; report artifacts reviewed; converter warnings captured as evidence-quality notes.
- **Risks or ways the agent could go wrong**: Reviewing before conversion, stale exports, or treating converter warnings as automatic findings.

---

## MILESTONE: Data Extraction & Converter Pipeline ✅ COMPLETE
**Status**: The integrated converter (`converter/ipc2581_to_json/thomson_bundle_converter.py`) is fully operational and produces enriched, cross-referenced JSON exports including:
- BOM parsing with aerospace/hi-rel fields (mass, lead finish, solder alloy)
- PADS schematic netlist extraction with component/net/pin relationships
- IPC-2581 board geometry with routes, pads, holes, polygons, and bounding boxes
- Cross-extraction pipeline merging BOM, PADS, and IPC-2581 data
- DFM analysis functions (annular rings, edge clearances, fiducials, thermal pads, NPTH keepouts)

---

## MILESTONE: Geometry Helper Library ✅ COMPLETE
**Status**: Mathematical analysis layer implemented in `scripts/geometry_helpers.py`.

**Capabilities**:
- **Trace Width Analysis** (`get_net_segments`): Extract segment-level width data with min/max/avg/nominal statistics per net
- **Net Clearance Math** (`calculate_min_clearance`): Bounding-box filtering + point-to-segment distance for precise edge-to-edge clearance
- **Differential Pair Analysis** (`analyze_differential_pair`): Coupling distance calculation, length mismatch detection, uncoupled section identification
- **NPTH Safety Verification** (`check_npth_clearance`): Copper keepout zone analysis for mounting holes (chassis ground safety per Appendix K.6)
- **Ampacity Verification** (`verify_trace_ampacity`): IPC-2221 current capacity estimation for power nets

**Usage**:
```bash
py -3 scripts/geometry_helpers.py exports/<project>-thomson-export-brd.json --net VCC --json
py -3 scripts/geometry_helpers.py exports/<project>-thomson-export-brd.json --clearance NET_A NET_B
py -3 scripts/geometry_helpers.py exports/<project>-thomson-export-brd.json --diff-pairs
py -3 scripts/geometry_helpers.py exports/<project>-thomson-export-brd.json --npth --npth-radius 4.0
py -3 scripts/geometry_helpers.py exports/<project>-thomson-export-brd.json --ampacity VCC 2.0
```

**Integration**: Phase 9 (Full Board/Layout JSON Evaluation) MUST use these helpers for quantitative geometry analysis.

---

## Phase 5 — Inspect Findings Framework
- **Purpose**: Determine valid finding structure, issue fields, evidence row format, `verified_checks`, `cross_checks`, severity, domains, and rule IDs.
- **Files/tools to inspect/use**: `tests/findings_schema.json`, `tests/sample_findings.json`, `ontology/ontology.json`, `examples/examples.json`, `tools/validate_findings.py`, `tools/gen_report.py`.
- **Expected evidence/output**: Definitive schema/validator constraints and allowed ontology values.
- **Validation/checkpoint before moving to next phase**: Required/optional fields and accepted enumerations documented and consistent across schema + validator + ontology.
- **Risks or ways the agent could go wrong**: Using invalid severities/domains/rule IDs, missing required fields, or inventing schema fields.

## Phase 6 — Full BOM Datasheet Retrieval
- **BOM line item definition**: Every raw BOM CSV row is a BOM line item, including labels, documents, generic passives, connectors, test points, mechanical rows, rows without MPN, and rows with MPN=`?`.
- **Coverage rule**: Every raw BOM row must produce exactly one manifest row unless a grouped row explicitly lists all included raw BOM row indexes. Coverage is invalid if manifest row count plus grouped covered row indexes does not cover all raw BOM row indexes.
- **Representation rule**: Rows lacking an applicable unique datasheet must still be represented (typically `not_applicable_generic` with reason).
- **Purpose**: Build a datasheet manifest for every BOM line item and attempt full BOM datasheet coverage.
- **Files/tools to inspect/use**: `exports/<project>-bom.json`, schematic/component exports for context, local `datasheets/`, SearXNG MCP (if available), `exports/datasheets/`.
- **Expected evidence/output**: `exports/datasheets/datasheet_manifest.jsonl` with one manifest row for every BOM line item (or clearly grouped equivalent row), plus locally saved datasheets for `local`/`found` items.
- **Validation/checkpoint before moving to next phase**:
  - Manifest coverage checkpoint: every BOM line item is represented.
  - Retrieved evidence checkpoint: all `found` datasheets are saved under `exports/datasheets/`.
  - Status values limited to `local`, `found`, `ambiguous`, `missing`, `not_applicable_generic`.
  - No browser Google search; no Tavily unless user explicitly requests it.
  - Search snippets are not evidence.
  - If first URL fails, do not give up; attempt additional candidate URLs with bounded multi-attempt logic (for example up to 3–5 candidates).
- **Risks or ways the agent could go wrong**: Incomplete BOM accounting, premature `missing` labels, untrusted source selection, or unbounded URL retry loops.


- **Hard definitions**:
  - `discovered_url`: A candidate datasheet URL found by SearXNG MCP or another approved discovery path. This is not evidence and does not count as found.
  - `downloaded_datasheet`: A datasheet file saved locally under `exports/datasheets/`. Only this can satisfy `status=found` or be cited as datasheet evidence.
- **Hard rules**:
  - A BOM row must not be marked status=found unless local_saved_filename is populated and that file exists under exports/datasheets/.
  - If candidate URLs are discovered but no local file is saved, the manifest row status must be ambiguous or missing, not found.
  - Do not write "datasheets available online" as equivalent to found; this is discovered_url only.
  - If environment download is unavailable, set status to ambiguous/missing and include status_note: "download unavailable in environment".
  - The agent must not continue past Full BOM Datasheet Retrieval while any status=found row points to a missing local file.
- **Required manifest validation** (must run before evidence review):
  - load `exports/datasheets/datasheet_manifest.jsonl`
  - count BOM rows from `exports/<project>-bom.json`
  - verify every BOM row has a manifest row or clearly grouped equivalent row
  - verify every status is one of `local`, `found`, `ambiguous`, `missing`, `not_applicable_generic`
  - verify every status=local row has an existing local_saved_filename
  - verify every status=found row has an existing local_saved_filename under `exports/datasheets/`
  - verify candidate_urls are recorded for web-discovered rows
  - verify failed candidate URLs are recorded when download attempts fail
  - fail if any status=found row lacks a local file
- **Required artifact**: `exports/datasheets/datasheet_manifest_validation.json` with:
  `bom_csv_path`, `bom_raw_row_count`, `manifest_path`, `manifest_row_count`, `covered_bom_row_indexes`, `uncovered_bom_row_indexes`, `status_counts`, `found_rows_missing_local_files`, `local_file_validation_pass`, `coverage_pass`, `overall_pass`.
- **Pass criteria**: Full BOM Datasheet Retrieval passes only if `coverage_pass=true`, `local_file_validation_pass=true`, and `overall_pass=true`.
- **Phase-local failure loop**: If `exports/datasheets/datasheet_manifest_validation.json` is missing or `overall_pass=false`, remain in Full BOM Datasheet Retrieval. Repair manifest and/or datasheet files, regenerate the validation artifact, and do not proceed to image gate or evidence review until Phase 6 passes.
- **Blocker**: If manifest validation fails, stop and repair manifest and/or downloads before Review Datasheet Evidence, Cross-Source Consistency Review, Candidate Finding Development, or Findings JSON.
- **Coverage strictness**: Full BOM retrieval means every BOM line item is represented. It is not complete if only ICs/easy URLs are covered, if generic parts are omitted instead of `not_applicable_generic`, or if test points/connectors/mechanical parts are omitted instead of classified.

## Phase 7 — Enforce Image Review Gate
- **Purpose**: Enforce PNG evidence readiness for deep review runs.
- **Files/tools to inspect/use**: `exports/` PNG artifacts; `pdftoppm`/`pdfinfo` when relevant. Read `json_only_review_approved` from `exports/tool-preflight-status.json` (written in Phase 3). Do not re-request user approval for JSON-only fallback if already recorded there; read it from `json_only_approval_source` in that file.
- **Expected evidence/output**: Verified schematic and layout/Gerber/PCB PNG presence when PDFs are present and `exports/<project>-image-evidence-inventory.json`.
- **Required artifact fields**: `pdf_sources`, `conversion_tool`, `fallback_used`, `user_approved_fallback`, `total_pages_expected`, `total_pages_rendered`, `output_files`, `schematic_pngs`, `layout_pngs`, `pages_inspected`, `page_roles_or_labels_if_identifiable`, `visual_context_notes`, `limitations`, `confirmation_no_pixel_quantitative_claims`, `overall_pass`.
- **Fallback distinctions**: image-render fallback means alternate rendering (for example PyMuPDF) to produce PNG evidence; JSON-only fallback means proceeding without image evidence.
- **Approval rules**: these require separate approval. `user_approved_fallback=true` is sufficient only for image-render fallback. JSON-only review requires `json_only_review_approved=true` and explicit user approval in a new message after the blocker is reported.
- **Validation/checkpoint before moving to next phase**: If PDFs exist but PNGs are missing or cannot be produced, stop unless `json_only_review_approved=true` as recorded in `exports/tool-preflight-status.json`.
- **Phase-local failure loop**: If `exports/<project>-image-evidence-inventory.json` is missing or `overall_pass=false` when PDFs/images are required, remain in Phase 7. Repair image rendering or stop for explicit fallback approval. Do not proceed to schematic/board evidence review or candidate findings.
- **Risks or ways the agent could go wrong**: Quietly skipping image gate, claiming image review without real renders, or re-requesting approval already recorded in Phase 3 artifact.

## Phase 8 — Review Schematic Evidence FULL
⚠️ No Phase Consolidation: This phase must produce its checkpoint row and complete independently before the next phase begins.
- **Purpose**: Perform a structured multi-pass schematic evidence review covering all KB Appendix H check families and Cluster 1 Rules.
- **Files/tools to inspect/use**: Generated schematic JSON, schematic PNG context if relevant.
- **REQUIRED TOOL**: `scripts/schematic_helpers.py` (graph-based analysis)

  **Command:**
  ```bash
  python scripts/schematic_helpers.py exports/<project>-thomson-export-sch.json --analyze-all --json
  ```

  **What this analyzes:**
  - `--analyze-all` runs all deterministic graph-based checks:
    * Single-pin nets (SCH_NET_002)
    * UART TX/RX crossover (SCH_UART_001)
    * FET gate termination (SCH_FET_001)
    * Floating inputs (SCH_FLOAT_001)
    * I2C pull-ups (MS_I2C_001)
    * I2C address conflicts (SCH_I2C_002)
    * Op-amp tie-off (SCH_PULLUP_001)

  **Output:** LLM-optimized JSON with precise paths (refdes, pin_number, pin_name, net_name, rule_id).

  **Individual check flags (optional):**
  - `--single-pins` - Run only SCH_NET_002
  - `--uart-check` - Run only SCH_UART_001
  - `--fet-check` - Run only SCH_FET_001
  - `--floating-check` - Run only SCH_FLOAT_001
  - `--i2c-check` - Run only MS_I2C_001, SCH_I2C_002
  - `--opamp-check` - Run only SCH_PULLUP_001

- **Expected evidence/output**:
  - **Net Integrity**: Single-pin nets (SCH_NET_002), cross-sheet labels (SCH_NET_001), consistent naming (SCH_NET_003), duplicate powers (SCH_NET_004).
  - **Component Application**: UART crossover (SCH_UART_001), floating inputs (SCH_FLOAT_001), FET pull-down (SCH_FET_001), op-amp tie-off (SCH_PULLUP_001), op-amp capacitive load (AN_OPAMP_002, AN_OPAMP_003), ADC filter/protection (AN_ADC_001, AN_ADC_003), SMPS comp (PWR_COMP_001).
  - **Value, Rating & Metadata**: Polar caps (SCH_POL_001), DNP flags (SCH_DNP_001). Flag [UNVERIFIABLE] subjective rules (SCH_OPT_001, SCH_IC_001).
  - **Protection and Safety**: Flyback diodes (PWR_RELAY_001), fuses (PWR_FUSE_001), TVS placement (AERO_TVS_001), reverse-polarity topology (AERO_RPP_001).
  - **I2C & Mixed Signal**: Address annotation/conflicts (SCH_I2C_001, SCH_I2C_002), bus pull-ups (MS_I2C_001), reset states (MS_RST_001), isolated power (MX_PWR_001).
  - **Test & Debug**: JTAG/SWD test points (DFT_JTAG_001, DFT_SWD_001).
- **Required artifact**: `exports/<project>-schematic-evidence-inventory.json`
- **Required validation artifact**: `exports/<project>-schematic-evidence-inventory-validation.json`
- **Validation/checkpoint before moving to next phase**:
  - schematic_helpers.py MUST be executed and results recorded
  - Both artifacts must exist, parse successfully, and `overall_pass=true`.
  - Findings/checks/limitations include schematic JSON citations with file/path/field/value where practical.
  - Phase 8 is not complete unless both artifacts exist and pass validation.
- **Phase-local failure loop**: If either artifact is missing or `overall_pass=false`, remain in Phase 8. Repair the inventory and validation artifacts.
- **Risks or ways the agent could go wrong**: Vague schematic claims, quantitative claims from PNG-only evidence, skipping KB Appendix H check families, or failing to run schematic_helpers.py for graph-based analysis.

## Phase 9 — Full Board/Layout JSON Evaluation
⚠️ No Phase Consolidation: This phase must produce its checkpoint row and complete independently before the next phase begins.
- **Purpose**: Perform full board/layout JSON evaluation (Cluster 2 Rules) using complete logical content, not summary-only extraction.
- **Files/tools to inspect/use**: `exports/<project>-thomson-export-brd.json`
- **REQUIRED TOOL**: `scripts/geometry_helpers.py` (quantitative geometry analysis)

  **Available Commands:**

  **Basic Geometry Analysis:**
  ```bash
  # Get segment statistics for a specific net
  python scripts/geometry_helpers.py exports/<project>-thomson-export-brd.json --net <NET_NAME> --json

  # Calculate clearance between two nets
  python scripts/geometry_helpers.py exports/<project>-thomson-export-brd.json --clearance NET_A NET_B

  # Analyze all differential pairs (auto-detected)
  python scripts/geometry_helpers.py exports/<project>-thomson-export-brd.json --diff-pairs --json
  ```

  **DFM Checks (REQUIRED):**
  ```bash
  # Via annular ring check (DFM_VIA_001, DFM_VIA_003, DFM_VIA_004)
  python scripts/geometry_helpers.py exports/<project>-thomson-export-brd.json --check-annular-ring --json

  # Acid trap detection (DFM_ACID_001)
  python scripts/geometry_helpers.py exports/<project>-thomson-export-brd.json --detect-acid-traps --json

  # Board edge clearance (DFM_EDGE_001, net-type-aware: GND=25mil, PWR/SIG=50mil)
  python scripts/geometry_helpers.py exports/<project>-thomson-export-brd.json --board-edge-clearance --json

  # Copper balance check (DFM_COPPER_001)
  python scripts/geometry_helpers.py exports/<project>-thomson-export-brd.json --copper-balance --json

  # NPTH keepout check (Appendix K.6)
  python scripts/geometry_helpers.py exports/<project>-thomson-export-brd.json --npth --npth-radius 4.0 --json
  ```

  **Physical-Math Verification (REQUIRED if stackup available):**

  These use `scripts/saturn_engine.py` (integrated into geometry_helpers.py):

  ```bash
  # Impedance verification (HS_MAT_001) - Wheeler/Wadell formulas
  # Requires stackup data, verifies single-ended and differential impedance
  python scripts/geometry_helpers.py exports/<project>-thomson-export-brd.json --verify-impedance --target-ohms 100 --json

  # Trace temperature/ampacity (PWR_TRACE_002) - IPC-2152 formulas
  # Requires stackup data, verifies thermal rise for power nets
  python scripts/geometry_helpers.py exports/<project>-thomson-export-brd.json --verify-trace-temp --current-a 3.0 --max-temp-rise 10.0 --json

  # Voltage clearance (DFM_TRACE_004) - IPC-2221B tables
  # Requires schematic with voltage annotations
  python scripts/geometry_helpers.py exports/<project>-thomson-export-brd.json --check-voltage-clearance --json
  ```

  **Trace ampacity check (utility):**
  ```bash
  # Check if a net can carry required current
  python scripts/geometry_helpers.py exports/<project>-thomson-export-brd.json --ampacity VCC 2.0
  ```


  **Rule-to-Tool Mapping (Which tool verifies which rule):**
  - PWR_TRACE_002 (thermal): `--verify-trace-temp` (REQUIRED if stackup available)
  - HS_MAT_001 (impedance): `--verify-impedance` (REQUIRED if stackup available)
  - DFM_TRACE_004 (voltage spacing): `--check-voltage-clearance` (REQUIRED if voltages annotated)
  - DFM_VIA_001/003/004 (annular rings): `--check-annular-ring` (REQUIRED)
  - DFM_ACID_001 (acid traps): `--detect-acid-traps` (REQUIRED)
  - DFM_EDGE_001/PANEL_001 (edge clearance): `--board-edge-clearance` (REQUIRED)
  - DFM_COPPER_001 (copper balance): `--copper-balance` (REQUIRED)
  - Appendix K.6 (NPTH keepout): `--npth --npth-radius 4.0` (REQUIRED)
  - HS_DIFF_001-006 (diff pairs): `--diff-pairs` (REQUIRED)
  - PWR_RES_001, DFM_TRACE_005 (net segments): `--net <NET_NAME>` (as needed)

  **If stackup unavailable:** Mark impedance/thermal checks as `[STACKUP_DATA_REQUIRED]` in findings but STILL run all DFM checks.

- **Expected evidence/output**:
  Execute physical-math verification using `scripts/saturn_engine.py` (integrated into `geometry_helpers.py`) to verify electrical constraints:
  1. **Impedance Verification**: Verify microstrip/stripline impedances against target specifications using Wheeler equations.
     ```bash
     py -3 scripts/geometry_helpers.py exports/<project>-thomson-export-brd.json --verify-impedance --target-ohms 50 --json
     ```
  2. **Thermal/Current Capacity**: Verify trace ampacity and via temperature rise using IPC-2152 formulas.
     ```bash
     py -3 scripts/geometry_helpers.py exports/<project>-thomson-export-brd.json --verify-trace-temp --current-a 3.0 --max-temp-rise 10.0 --json
     ```
  3. **Voltage Spacing**: Verify IPC-2221B electrical clearance on high-voltage nets.
     ```bash
     py -3 scripts/geometry_helpers.py exports/<project>-thomson-export-brd.json --check-voltage-clearance --json
     ```
  Note: Physical-math verification requires stackup data and voltage annotations. If unavailable, mark as `[STACKUP_DATA_REQUIRED]`.
- **Required Checkpoint Gate**: `geometry_helper_analysis` must contain verified results for `impedance_verification` and `ampacity_verification` (when stackup available), with zero critical errors before advancing.
- **Expected evidence/output**: `exports/<project>-board-evidence-inventory.json`.Must evaluate: DDR length matching (HS_DDR_001/002), Clocks & Crystals (HS_CLK_001/002, HS_XTAL_001 to 006), Power & SMPS Hot Loops (PWR_DECPL_001 to 005, PWR_BUCK_001 to 006), EMC & Planes (EMC_ESD_001 to 006, EMC_PATH_001, EMC_PLANE_002, EMC_AGG_001, EMC_STITCH_001/002, EMC_VIA_003), Analog Isolation (AN_ADC_004 to 007, AN_SENSOR_001), Thermal Areas (THM_PWR_001/002, THM_VIA_001/004/005, THM_SPREAD_001). Flag subjective rules as partially verifiable (HS_CRIT_001, HS_CROSS_001, HS_SENS_001, HS_SHORT_001).
- **Validation/checkpoint**: Artifacts exist, pass geometry checks, and overall_pass=true.
- **Validation/checkpoint before moving to next phase**:
  - Full Board/Layout JSON Evaluation is not complete unless both `exports/<project>-board-evidence-inventory.json` and `exports/<project>-board-evidence-inventory-validation.json` exist and validation `overall_pass=true`.
  - Record the exact board evidence inventory path.
  - A printed summary table is not sufficient evidence of full board JSON evaluation. The required inventory JSON artifact must exist and pass validation.
  - Board evidence inventory must contain required fields: `source_board_json`, `generated_timestamp`, `board_json_loaded`, `inspected_sections`, `unavailable_sections`, `object_counts`, `layer_count`, `net_count`, `route_count`, `via_count`, `hole_count`, `component_count_if_available`, `route_width_summary`, `route_length_summary`, `candidate_differential_or_paired_nets`, `candidate_power_nets`, `candidate_connector_or_interface_nets`, `candidate_test_or_debug_features`, `npth_holes_with_copper_keepout`, `thermal_pad_components`, `smps_candidates`, `ground_plane_slot_indicators`, `conversion_limitations`, `missing_or_unsupported_fields`, `evidence_paths_used`, `geometry_helper_analysis` (must include differential_pairs, npth_clearance, trace_widths, annular_ring, acid_traps, board_edge_clearance, copper_balance, and physical_math_verification when stackup available).
  - Required validation artifact: `exports/<project>-board-evidence-inventory-validation.json` with `inventory_exists`, `required_fields_present`, `board_json_loaded`, `required_categories_inspected_or_marked_unavailable`, `geometry_helpers_executed`, `dfm_geometry_checks_executed`, `physical_math_checks_attempted_or_marked_unavailable`, `overall_pass`.
  - If board evidence inventory validation fails, stop and repair before Candidate Finding Development or Findings JSON.
  - If `exports/<project>-board-evidence-inventory.json` or `exports/<project>-board-evidence-inventory-validation.json` is missing, invalid, or `overall_pass=false`, remain in Phase 9. Repair the inventory and validation artifacts. Do not proceed to stackup review, DFM review, cross-source review, or candidate findings.
  - Board JSON loaded successfully.
  - Required categories inspected or explicitly marked unavailable: metadata, layers, units/coords, outline, components/footprints/packages, pads, vias/holes, plated vs non-plated, nets/net classes, routes, route width by net, route length by net/layer, polygons/copper areas, pour indicators, non-copper geometry, silkscreen/mechanical, test/debug features, connector/interface context, differential/paired candidates, power-net routing evidence, NPTH mounting holes and copper keepout zones on all layers, thermal pad footprints and via array sufficiency, trace-spacing candidates for 3W rule near high-speed nets, via stub presence for signals above 3 Gbps, serpentine routing geometry (bend angles and intra-segment spacing), SMPS hot-loop evidence (inductor keepout zones, SW node copper area, cap proximity), ground plane slot and via-wall analysis, conversion limitations/missing fields.
  - Board evidence inventory created with counts/summaries/candidate groups/missing fields/evidence paths.
  - No findings written before board evidence inventory exists.
  - Board JSON is geometry/routing evidence, not true DRC; do not claim impedance verification, thermal capacity verification, or manufacturing signoff without explicit physical-math tool evidence and stackup data. Mark as `[STACKUP_DATA_REQUIRED]` if unavailable.
- **Risks or ways the agent could go wrong**: Summary-only review, missing large-file sections, over-claiming DRC/manufacturing conclusions, skipping new thermal/NPTH/SMPS extraction targets, not running geometry helpers, skipping DFM geometry checks, skipping physical-math checks when stackup available.

## Phase 10 — Review Stackup and Manufacturing Evidence FULL
⚠️ No Phase Consolidation: This phase must produce its checkpoint row and complete independently before the next phase begins.
- **Purpose**: Review stackup facts and manufacturing evidence limits with explicit source and fallback reporting. Note: DFM manufacturing rule compliance (trace widths, via sizes, solder mask, panelization, fiducials, copper-to-edge clearances) is covered separately in Phase 11. This phase focuses on stackup material data, impedance evidence, and layer structure.
- **Files/tools to inspect/use**: Generated stack JSON, `input/stackup.csv`, `input/stackup.json`, `input/*.tcfx` (Cadence Allegro/OrCAD technology files), fabrication drawing PDFs with stackup tables, ODB++ archive/folder when present, IPC-2581 stackup/cross-section content when present, and EDA/fab stackup reports (Allegro/OrCAD, Altium, PADS, KiCad) when present, `scripts/stackup_helpers.py` (REQUIRED when stackup CSV/JSON exists), `converter/ipc2581_to_json/parse_tcfx_stackup.py` (for manual merging if needed).
- **Note on TCFX Auto-Merge**: The `thomson_bundle_converter.py` now automatically searches for `.tcfx` files and merges stackup data during conversion. Manual merging is only needed to update existing stackup JSON files:
  ```bash
  py -3 converter/ipc2581_to_json/parse_tcfx_stackup.py input/<project>.tcfx exports/<project>-thomson-export-stack.json
  ```
- **Expected evidence/output**: Reported stackup source used, whether generated stack JSON exists, whether explicit `stackup.csv` or `stackup.json` exists, whether ODB++ exists, whether EDA/fab stackup report exists, stackup completeness status, missing stackup fields, impedance-evidence availability, stackup helper analysis results, and stackup limitations.
- **Validation/checkpoint before moving to next phase**:
  - Stackup completeness status must be one of `complete_explicit`, `partial_explicit`, `layer_order_only`, `missing`.
  - If explicit stackup source exists, stackup_helpers.py must be executed and results recorded.
  - If no explicit stackup source exists, mark stackup as missing evidence.
  - Do not claim impedance verification, stackup verification, or manufacturing signoff without explicit stackup/material/impedance evidence.
  - Layer names/order/files alone are insufficient for dielectric thickness, copper weight, Dk/Df, controlled impedance, finished thickness, or manufacturing signoff.
- **Risks or ways the agent could go wrong**: Inferring stackup from naming conventions, overstating impedance/manufacturing confidence, omitting limitations, skipping stackup_helpers.py when input exists.

## Phase 11 — Review DFM and Manufacturing Specifications
⚠️ No Phase Consolidation: This phase must produce its checkpoint row and complete independently before the next phase begins.
- **Purpose**: Review design-for-manufacturing compliance against KB Appendix G and Cluster 3 Rules.
- **Files/tools to inspect/use**: `exports/<project>-board-evidence-inventory.json`, `exports/<project>-thomson-export-brd.json`

- **REQUIRED TOOL**: `scripts/geometry_helpers.py` (DFM checks from Phase 9)

  **DFM geometry checks (should already be run in Phase 9, if not, run now):**
  ```bash
  python scripts/geometry_helpers.py exports/<project>-thomson-export-brd.json --check-annular-ring --json
  python scripts/geometry_helpers.py exports/<project>-thomson-export-brd.json --detect-acid-traps --json
  python scripts/geometry_helpers.py exports/<project>-thomson-export-brd.json --board-edge-clearance --json
  python scripts/geometry_helpers.py exports/<project>-thomson-export-brd.json --copper-balance --json
  ```

  **Physical-Math Verification (REQUIRED when stackup available):**
  ```bash
  # IPC-2221B voltage spacing lookup for high-voltage nets
  python scripts/geometry_helpers.py exports/<project>-thomson-export-brd.json --check-voltage-clearance --json
  ```

  **What this verifies:**
  - DFM_VIA_001/003/004: Via annular rings vs manufacturing minimums
  - DFM_ACID_001: Acute-angle copper features (acid traps)
  - DFM_EDGE_001/PANEL_001: Board edge clearances (GND=25mil, PWR/SIG=50mil)
  - DFM_COPPER_001: Layer-by-layer copper balance (warpage prevention)
  - DFM_TRACE_004: IPC-2221B electrical clearance for voltage spacing

  **Rule-to-Tool Mapping:**
  - Annular rings: `--check-annular-ring`
  - Acid traps: `--detect-acid-traps`
  - Edge clearance: `--board-edge-clearance`
  - Copper balance: `--copper-balance`
  - Voltage spacing: `--check-voltage-clearance` (requires schematic with voltages)

- **Required Checkpoint Gate**: All geometry-based DFM checks must be executed and results recorded in `exports/<project>-dfm-evidence-inventory.json`.

- **Expected evidence/output**: Assessment against manufacturing minimums:
  - **Manufacturing**: Trace/Space (DFM_TRACE_001/004), Via/Annular ring (DFM_VIA_001/003/004), Slivers (DFM_SLIVER_001), Acid traps (DFM_ACID_001), Thermal vias (THM_VIA_002/003).
  - **Mask & Paste**: (DFM_MASK_001, DFM_PASTE_001).
  - **Silkscreen**: Clearances/legibility (DFM_SILK_001), Text markers (DFT_SILK_001/002, DFM_LABEL_001, DFT_POL_001, DFT_CONN_LABEL_001, DFT_TP_004, DFM_LAYER_001).
  - **Panelization**: Edges/Courtyards (DFM_EDGE_001, DFM_COMP_EDGE_001, DFM_PANEL_001, DFM_COURT_001).
  - **Assembly & DFT**: Fiducials (DFM_FID_001/002, DFT_FID_001), Copper balance (DFM_COPPER_001), Test points (DFT_TP_001/002/003, DFT_GND_003).
  - **Mechanical**: Mark [UNVERIFIABLE] if 3D/STEP is absent (MEC_CONN_001, MEC_MOUNT_001, MEC_HEATSINK_001, MEC_UI_001, MEC_HEIGHT_001).
- **Required artifacts**: `exports/<project>-dfm-evidence-inventory.json` and validation. Must include `geometry_helpers_dfm_results` section with annular_ring, acid_traps, board_edge_clearance, copper_balance check outputs.

## Phase 12 — Review BOM and Component Evidence FULL
⚠️ No Phase Consolidation: This phase must produce its checkpoint row and complete independently before the next phase begins.
- **Purpose**: Review BOM quality, component metadata completeness, and identify components requiring datasheet verification.
- **Files/tools to inspect/use**: Generated BOM JSON (`exports/<project>-bom.json`)

- **REQUIRED TOOL**: `scripts/bom_helpers.py` (deterministic component analysis)

  **Command:**
  ```bash
  python scripts/bom_helpers.py exports/<project>-bom.json --audit-components --json
  ```

  **What this analyzes:**
  - `--audit-components` runs all deterministic component checks:
    * Heavy components >3g (AERO_VIB_001)
    * Capacitor dielectrics (COMP_CAP_001)
    * Incomplete MPNs (DFM_BOM_001)
    * Lead finish assessment (AERO_SLD_001)
    * Polarized capacitors (SCH_POL_001)

  **Individual check flags (optional):**
  ```bash
  # Heavy component check (adjustable threshold)
  python scripts/bom_helpers.py exports/<project>-bom.json --heavy-threshold 3.0 --json

  # Capacitor dielectric check (X5R, X7R vs Y5V, Z5U)
  python scripts/bom_helpers.py exports/<project>-bom.json --check-dielectrics --json

  # MPN completeness audit
  python scripts/bom_helpers.py exports/<project>-bom.json --audit-mpns --json

  # Lead finish check (Sn vs SnPb)
  python scripts/bom_helpers.py exports/<project>-bom.json --check-lead-finish --json

  # Polarized component check
  python scripts/bom_helpers.py exports/<project>-bom.json --polarized --json
  ```

  **Output:** LLM-optimized JSON with precise paths (refdes, mpn, description, rule_id).

  **Rule-to-Tool Mapping:**
  - AERO_VIB_001 (heavy components): `--heavy-threshold`
  - COMP_CAP_001 (dielectrics): `--check-dielectrics`
  - DFM_BOM_001 (MPN completeness): `--audit-mpns`
  - AERO_SLD_001 (lead finish): `--check-lead-finish`
  - SCH_POL_001 (polarized): `--polarized`

- **Expected evidence/output**: Refdes coverage, MPN/LCSC/manufacturer fields, quantity consistency, package metadata, DNP flags, components needing datasheet evidence, and aerospace-relevant metadata (component mass candidates > 1 g, lead-finish designator fields).
- **Required artifact**: `exports/<project>-bom-evidence-inventory.json` with fields:
  `bom_row_count`, `refdes_coverage`, `mpn_present_count`, `mpn_missing_count`, `lcsc_present_count`, `manufacturer_present_count`, `quantity_consistency_checked`, `package_metadata_coverage`, `dnp_components_flagged`, `missing_metadata_rows`, `aero_lead_finish_candidates`, `component_mass_candidates`, `datasheet_needed_components`, `bom_schematic_consistency_candidates`, `evidence_paths_used`.
- **Required validation artifact**: `exports/<project>-bom-evidence-inventory-validation.json` with fields:
  `inventory_exists`, `required_fields_present`, `bom_loaded`, `overall_pass`.
- **Pass logic**: Phase 12 passes when both artifacts exist, parse successfully, and `overall_pass=true`.
- **Validation/checkpoint before moving to next phase**: BOM inconsistencies and metadata gaps documented with citations.
- **Phase-local failure loop**: If either artifact is missing or `overall_pass=false`, remain in Phase 12. Repair the inventory and validation artifacts.
- **Risks or ways the agent could go wrong**: Inferring electrical limits from package/vendor text alone; failing to flag aerospace-relevant fields for Phase 15; failing to run bom_helpers.py for deterministic analysis.

## Phase 13 — Review Image Evidence FULL
⚠️ No Phase Consolidation: This phase must produce its checkpoint row and complete independently before the next phase begins.
- **Purpose**: Review PNG evidence as visual/context support. This phase performs the actual image inspection; Phase 7 only verified that PNG files exist. Phase 13 must produce a distinct artifact proving inspection occurred.
- **Files/tools to inspect/use**: Generated schematic and layout/Gerber/PCB PNG files. The Phase 7 artifact (`exports/<project>-image-evidence-inventory.json`) confirms files exist; this phase must open and inspect each PNG individually.
- **Expected evidence/output**: Recorded image pages actually opened, visual/context observations per page, schematic labels, connector labels, and power/interface labels identified.
- **Required artifact**: `exports/<project>-image-evidence-review.json` (distinct from the Phase 7 image-evidence-inventory) with fields:
  `pages_actually_opened`, `schematic_page_observations`, `layout_page_observations`, `visual_concerns`, `schematic_labels_identified`, `power_interface_labels_identified`, `connector_labels_identified`, `limitations`, `phase_13_completed`.
- **Required validation artifact**: `exports/<project>-image-evidence-review-validation.json` with fields:
  `inventory_exists`, `required_fields_present`, `pages_actually_opened_count`, `phase_13_completed`, `overall_pass`.
- **Validation/checkpoint before moving to next phase**:
  - Both artifacts must exist, parse successfully, and `overall_pass=true`.
  - `phase_13_completed=true` must be set by actual image inspection, not carried over from Phase 7.
  - Only qualitative/context conclusions from PNGs; no quantitative pixel-derived metrics.
- **Phase-local failure loop**: If either artifact is missing or `overall_pass=false` when PDFs/images are required, remain in Phase 13. Repair image inspection or stop for explicit fallback approval. Do not proceed to datasheet review or candidate findings.
- **Risks or ways the agent could go wrong**: Reusing Phase 7 artifact without performing actual inspection; deriving dimensions/clearance/width from pixel measurements.

## Phase 14 — Review Datasheet Evidence FULL
⚠️ No Phase Consolidation: This phase must produce its checkpoint row and complete independently before the next phase begins.
- **Purpose**: Review available local datasheet evidence against Cluster 4 Rules.
- **Files/tools to inspect/use**: Local datasheets, manifest from Phase 6.
- **Expected evidence/output**: Datasheet-backed checks/findings with citations. Must evaluate:
  - **Capacitors**: Dielectric/DC bias (COMP_CAP_001/002), Leakage (COMP_CAP_003), Voltage margin (COMP_CAP_004, SCH_POL_001), Ripple/ESR/ESL (COMP_CAP_005/006).
  - **Inductors**: Tolerance/Isat/SRF/Q-factor (COMP_IND_001 to 004, PWR_RATING_001).
  - **Resistors**: Film type/noise/power/inductance (COMP_RES_001 to 004).
  - **Semiconductors**: Voltage margins (PWR_RATING_002), Op-Amp CM (AN_OPAMP_001), ADC driver (AN_ADC_002).
  - **Metadata**: Text sanity (SCH_VAL_001), MPN verify (DFM_BOM_001).
- **Required artifacts**: `exports/<project>-datasheet-evidence-review.json` and validation.

## Phase 15 — Review Aerospace and Process Metadata
⚠️ No Phase Consolidation: This phase must produce its checkpoint row and complete independently before the next phase begins.
- **Purpose**: Inspect aerospace certification and process metadata (Cluster 5 Rules). Document absences as missing evidence or [UNVERIFIABLE].
- **Files/tools to inspect/use**: Fab work orders, conformal specs, BOM mass records.
- **Expected evidence/output**: Assessment of:
  - **Materials & Process**: Stackup dielectric (HS_MAT_001), Solder alloy/Class (AERO_SLD_001), Conformal masks (AERO_TERM_001), Lead finish (AERO_SLD_001).
  - **Vibration & Mass**: Component mass > 3g (AERO_VIB_001).
  - **Logs**: DRC/ERC verification (DFT_DRC_001/002, DFT_CONN_001).
  - **[UNVERIFIABLE] Flagging**: Immediately flag THM_DISS_001, THM_RISE_001, THM_HEAT_001, THM_COOL_001, DFT_BUILD_001, DFT_MEAS_001, DFT_PROD_001, and SCH_IC_001 as "Skipped: Requires Physical Prototype/Testing/3D Simulation".
- **Required artifacts**: `exports/<project>-aerospace-evidence-inventory.json` and validation.

## Phase 16 — Cross-Source Consistency Review
⚠️ No Phase Consolidation: This phase must produce its checkpoint row and complete independently before the next phase begins.
- **Purpose**: Cross-check schematic, board, stack, BOM, and datasheets against Cluster 6 Rules.
- **Files/tools to inspect/use**: All generated JSON artifacts, datasheets.

- **REQUIRED TOOL**: `scripts/cross_check_helpers.py` (tripartite set operations and topology mapping)

  **Command:**
  ```bash
  python scripts/cross_check_helpers.py \
    --bom exports/<project>-bom.json \
    --sch exports/<project>-thomson-export-sch.json \
    --brd exports/<project>-thomson-export-brd.json \
    --json
  ```

  **What this analyzes (all checks run by default):**
  - RefDes reconciliation (tripartite set matching)
  - Package mismatches (DFM_LIB_002)
  - Netlist topology verification (SCH_NET_001)
  - Voltage derating margins (SCH_POL_001, COMP_CAP_002)

  **Individual check flags (optional):**
  ```bash
  # RefDes tripartite matching (BOM ∩ SCH ∩ BRD)
  python scripts/cross_check_helpers.py --bom <bom> --sch <sch> --brd <brd> --run-reconciliation --json

  # Package mismatch detection (DFM_LIB_002)
  python scripts/cross_check_helpers.py --bom <bom> --brd <brd> --check-packages --json

  # Netlist topology verification (SCH_NET_001)
  python scripts/cross_check_helpers.py --sch <sch> --brd <brd> --verify-netlist --json

  # Voltage derating validation (SCH_POL_001, COMP_CAP_002)
  python scripts/cross_check_helpers.py --bom <bom> --sch <sch> --verify-derating --json
  ```

  **Output:** LLM-optimized JSON with precise discrepancy reporting:
  - RefDes in BOM but not in SCH/BRD (and vice versa)
  - Package name mismatches (BOM vs BRD footprint)
  - Net connectivity differences (SCH pins vs BRD pads)
  - Insufficient voltage margins (rated vs applied voltage)

  **Rule-to-Tool Mapping:**
  - RefDes consistency: `--run-reconciliation`
  - DFM_LIB_002 (package mismatch): `--check-packages`
  - SCH_NET_001 (netlist sync): `--verify-netlist`
  - SCH_POL_001, COMP_CAP_002 (derating): `--verify-derating`

- **Expected evidence/output**: Cross-check coverage for:
  - **RefDes Reconciliation**: S\B, B\S, S\L, L\S set differences flagged
  - **Pin & Pad Mapping**: Symbol pins vs PDF (SCH_SYMBOL_001), Layout footprint vs PDF drawing (DFM_LIB_002).
  - **Package Mismatches**: BOM MPN package vs Board footprint size
  - **Netlist Topology**: Schematic net pins vs Board net pins
  - **Voltage Derating**: Operating voltage vs rated voltage margins (SCH_POL_001, COMP_CAP_002)
  - **Orientation**: Connector Pin 1 vs cable specs (DFM_LIB_003).
  - **Physical Print**: Flag DFM_LIB_001 (1:1 Print) as [UNVERIFIABLE].
  - **Routing vs Ratings**: Inductor Isat vs peak current (PWR_RATING_001), Capacitor bias vs supply voltage (COMP_CAP_001), Stackup vs high-speed presence (HS_MAT_001).
- **Required artifacts**: `exports/<project>-cross-source-review.json` and validation.

## Phase 17 — Pre-Findings Gate Check
- **Purpose**: Execute Workflow 16: Pre-Findings Gate Check and block findings work until all required artifacts and hard gates pass.
- **Validation/checkpoint before moving to next phase**:
  - required artifact `exports/<project>-pre-findings-gate.json` exists
  - converter_completed=true
  - json_exports_loadable=true
  - png_image_gate_passed=true
  - datasheet_manifest_exists=true
  - datasheet_manifest_validation_pass=true (`datasheet_manifest_validation_pass` must be set by reading `overall_pass` from `exports/datasheets/datasheet_manifest_validation.json`; do not invent this value or infer it from any other source)
  - schematic_evidence_inventory_exists=true
  - schematic_evidence_inventory_validation_pass=true
  - board_evidence_inventory_exists=true
  - board_evidence_inventory_validation_pass=true
  - dfm_evidence_inventory_exists=true
  - dfm_evidence_inventory_validation_pass=true
  - bom_evidence_inventory_exists=true
  - bom_evidence_inventory_validation_pass=true
  - image_evidence_inventory_exists=true when images are required (Phase 7 artifact)
  - image_evidence_review_exists=true when images are required (Phase 13 artifact)
  - image_evidence_review_phase_13_completed=true when images are required
  - datasheet_evidence_review_exists=true
  - datasheet_evidence_review_validation_pass=true
  - aerospace_evidence_inventory_exists=true
  - aerospace_evidence_inventory_validation_pass=true
  - cross_source_review_exists=true
  - cross_source_review_validation_pass=true
  - stackup_completeness_recorded=true
  - framework_inspection_completed=true
  - no hard blocker remains and `overall_gate_pass=true`
  - this phase must not require findings validation or report generation artifacts
- **Phase-local failure loop**: If `exports/<project>-pre-findings-gate.json` is missing or `overall_gate_pass=false`, remain in Pre-Findings Gate Check. Do not create candidate findings or findings JSON. The gate must identify which earlier phase failed and list the failed phase number requiring repair.
- **Blocker rule**: If any item fails, stop before Candidate Finding Development. The agent must not proceed to candidate findings without board evidence inventory, and must not proceed without image evidence inventory and image evidence review when images are required.

## Phase 18 — Candidate Finding Development
- **Purpose**: Develop candidate findings before writing final JSON.
- **Files/tools to inspect/use**: Phase 8–16 evidence plus `ontology/ontology.json` and `examples/examples.json` (style/rule mapping only).
- **Expected evidence/output**: Evidence-cited candidate set mapped to rule/domain/severity where possible.
- **Validation/checkpoint before moving to next phase**: Unsupported or vague candidates rejected; concrete citations required.
- **Risks or ways the agent could go wrong**: Promoting weak/vague ideas to issues.

## Phase 19 — Write Findings JSON
- **Purpose**: Create findings JSON using schema-allowed fields only.
- **Files/tools to inspect/use**: Findings schema, validator expectations, generated evidence sources.
- **Expected evidence/output**: Findings JSON with `issues`, `evidence`, `recommended_actions`, and `verified_checks`/`cross_checks`/`source_documents` as supported.
- **Validation/checkpoint before moving to next phase**: Findings writing occurs after all evidence-review phases; `issues[]` must provide full evidence-backed coverage. Do not apply arbitrary count caps. Include every concrete, non-duplicative, evidence-supported issue that satisfies schema and validation requirements.
- **Risks or ways the agent could go wrong**: Writing findings too early or violating schema.

## Phase 20 — Validate and Repair Findings
- **Purpose**: Validate findings and repair only findings JSON until pass.
- **Files/tools to inspect/use**: `python3 tools/validate_findings.py exports/example-findings.json` (or matching project prefix).
- **Expected evidence/output**: Validation pass output.
- **Validation/checkpoint before moving to next phase**: Validator succeeds with no bypass.
- **Phase-local failure loop**: If findings validation fails, remain in Validate and Repair Findings. Repair only findings JSON. Do not modify schema, validator, ontology, examples, source evidence, or generated converter outputs.
- **Risks or ways the agent could go wrong**: Editing framework files instead of findings JSON.

## Phase 21 — Generate Report
- **Purpose**: Generate report only after findings validation passes and enforce report-generation gates.
- **Files/tools to inspect/use**: `python3 tools/gen_report.py exports/example-findings.json --output exports` (or matching project prefix).
- **Expected evidence/output**: HTML report path under `exports/` plus `exports/<project>-report-generation-validation.json`.
- **Validation/checkpoint before moving to next phase**:
  - Must run `tools/gen_report.py` after findings validation passes.
  - Must produce an HTML report under `exports/`.
  - Must verify the HTML report exists.
  - Must record the exact HTML report path.
  - Markdown-only report output does not satisfy the phase; markdown report alone is not sufficient.
  - If no HTML report exists, stop before Phase 20 Final Summary.
  - Required report validation artifact: `exports/<project>-report-generation-validation.json` including:
    - `findings_json_path`
    - `validation_passed_before_report`
    - `report_command`
    - `html_report_path`
    - `html_report_exists`
    - `markdown_report_only_detected`
    - `overall_pass`
  - Report generation passes only if:
    - `validation_passed_before_report=true`
    - `html_report_exists=true`
    - `markdown_report_only_detected=false`
    - `overall_pass=true`
  - Do not mark the review complete if only `exports/review_report.md` or `exports/example-review-report.md` exists.
  - Do not substitute markdown output for the required HTML report.
- **Phase-local failure loop**: If `exports/<project>-review.html` is missing or `exports/<project>-report-generation-validation.json` is missing or `overall_pass=false`, remain in Generate Report. Re-run `tools/gen_report.py` or repair report generation. Do not proceed to Final Summary.
- **Risks or ways the agent could go wrong**: Running report generation pre-validation or treating markdown-only output as complete.

## Phase 22 — Final Summary
- **Purpose**: Provide final operational summary and required metrics.
- **Files/tools to inspect/use**: Converter logs/report, framework inspection notes, evidence inspection notes, findings/validation/report outputs.
- **Expected evidence/output**: Final summary including datasheet retrieval totals (BOM line items, manifest rows, local/found/ambiguous/missing/not_applicable_generic counts, manifest path, cited datasheets, candidate URL failure summary) plus stackup source/completeness/limitations and per-evidence-class inspection summaries.
- **Validation/checkpoint before completion**: Final summary may be produced only if `exports/<project>-phase-checkpoints.jsonl` exists, all required phase rows exist, all phase rows have `phase_passed=true`, `exports/tool-preflight-status.json overall_pass=true`, `exports/datasheets/datasheet_manifest_validation.json overall_pass=true`, `exports/<project>-schematic-evidence-inventory-validation.json overall_pass=true`, `exports/<project>-board-evidence-inventory-validation.json overall_pass=true`, `exports/<project>-dfm-evidence-inventory-validation.json overall_pass=true`, `exports/<project>-bom-evidence-inventory-validation.json overall_pass=true`, `exports/<project>-image-evidence-inventory.json overall_pass=true` when PDFs/images are required, `exports/<project>-image-evidence-review-validation.json overall_pass=true` when PDFs/images are required, `exports/<project>-datasheet-evidence-review-validation.json overall_pass=true`, `exports/<project>-aerospace-evidence-inventory-validation.json overall_pass=true`, `exports/<project>-cross-source-review-validation.json overall_pass=true`, `exports/<project>-pre-findings-gate.json overall_gate_pass=true`, findings JSON exists and validator passed, `exports/<project>-report-generation-validation.json overall_pass=true`, and `exports/<project>-review.html` exists. If any gate fails, write `INVALID RUN SUMMARY` instead of a completion summary.
- **Risks or ways the agent could go wrong**: Omitting datasheet metrics or evidence-class coverage details.


## Required Final Response Format Additions

Datasheets:
- datasheet manifest validation path
- datasheet manifest validation overall_pass
- status=found rows with existing local file count
- status=found rows missing local file count
- discovered URL only count
- download unavailable count

Schematic:
- schematic evidence inventory validation path
- schematic evidence inventory validation overall_pass

Board:
- board evidence inventory validation path
- board evidence inventory validation overall_pass

DFM:
- DFM evidence inventory validation path
- DFM evidence inventory validation overall_pass

BOM:
- BOM evidence inventory validation path
- BOM evidence inventory validation overall_pass

Images:
- image evidence inventory path
- image evidence inventory created: yes/no
- image evidence review path
- image evidence review phase_13_completed: yes/no
- image pages actually inspected count

Datasheet Evidence Review:
- datasheet evidence review validation path
- datasheet evidence review validation overall_pass

Aerospace:
- aerospace evidence inventory validation path
- aerospace evidence inventory validation overall_pass
- aerospace documentation present: yes/no
- aerospace evidence gaps documented: yes/no

Cross-Source:
- cross-source review validation path
- cross-source review validation overall_pass

Pre-findings gate:
- pre-findings gate passed: yes/no
- any blockers remaining before findings: yes/no

Report:
- report generation validation path
- report generation validation overall_pass
- HTML report path
- HTML report exists: yes/no
- markdown-only report detected: yes/no


## Topology and Calculation Foundation (PR16–PR25)

The following deterministic scripts form the topology/current/rating/calculation-readiness foundation. Each PR produces authoritative artifacts that are **not** modified by later AI-assisted candidate stages (PR26–PR37).

### PR16 — Missing Data Manifest
- **Script**: `scripts/missing_data_manifest.py`
- **Purpose**: Inventory missing datasheets, evidence gaps, and readiness state.
- **Output artifacts**: `exports/<project>-missing-data-manifest.json`, manifest validation JSON.
- **Validation**: Manifest parses successfully; status counts (found/missing/not_applicable_generic) are consistent.

### PR17 — Calculation Schemas and Examples
- **Docs**: `docs/calculation_schemas.md`
- **Purpose**: Define calculation input/result schemas with worked examples.
- **Output artifacts**: Schema files under `schemas/`, example JSON under `examples/calculation_examples/`.
- **Validation**: Schema files parse as valid JSON; examples conform to schema structure.

### PR18 — Basic Copper Calculations v0
- **Script**: `scripts/topology_copper_calculate.py`
- **Purpose**: Compute copper layer resistance, trace width, and current-carrying capacity from topology data.
- **Output artifacts**: Copper calculation results under `exports/`.
- **Validation**: Output JSON conforms to calculation result schema; values are internally consistent.

### PR19 — Current Model Ingestion v0
- **Script**: `scripts/current_model_ingest.py`
- **Purpose**: Ingest current model data from schematic/netlist sources into structured format.
- **Output artifacts**: Current model JSON under `exports/`.
- **Validation**: Output parses; net-to-current mappings are complete for available data.

### PR20 — Topology Current Allocation v0
- **Script**: `scripts/topology_current_allocate.py`
- **Purpose**: Allocate currents across topology branches and nets.
- **Output artifacts**: Allocated current results under `exports/`.
- **Validation**: Sum of allocated currents is consistent with source model; no orphaned nets.

### PR21 — Wire Allocated Currents into Copper Calculations
- **Integration**: Connects PR20 allocation outputs to PR18 copper calculation inputs.
- **Purpose**: Feed branch-level current allocations into copper layer calculations.
- **Output artifacts**: Extended copper calculation results incorporating wire-level currents.
- **Validation**: Copper calculations reference valid allocated-current sources; no missing references.

### PR22 — Via Current Density v0
- **Integration**: Part of topology margin calculation pipeline.
- **Purpose**: Compute via current density from allocated currents and via geometry.
- **Output artifacts**: Via current density results under `exports/`.
- **Validation**: Density values are non-negative; via-to-current mappings are complete for available data.

### PR23 — Rating Model Ingestion v0
- **Script**: `scripts/rating_model_ingest.py`
- **Purpose**: Ingest component rating models from datasheets and BOM sources.
- **Output artifacts**: Rating model JSON under `exports/`.
- **Validation**: Output parses; part-to-rating mappings are complete for available data.

### PR24 — Fuse Margin v0
- **Integration**: Part of topology margin calculation pipeline.
- **Purpose**: Compute fuse current margins from rating models and allocated currents.
- **Output artifacts**: Fuse margin results under `exports/`.
- **Validation**: Margins are computed for all available fuse entries; no negative or NaN values.

### PR25 — Connector Pin Current Margin v0
- **Integration**: Part of topology margin calculation pipeline.
- **Purpose**: Compute connector pin current margins from rating models and allocated currents.
- **Output artifacts**: Connector pin margin results under `exports/`.
- **Validation**: Margins are computed for all available connector pins; no negative or NaN values.


## AI-Assisted Candidate Completion and Review Baseline (PR26–PR37)

The following deterministic scripts implement the AI-assisted candidate completion and review baseline. These scripts:

- **Never call AI models directly.** They generate, validate, materialize, and review artifacts around AI-assisted results.
- **Do not modify core topology/current/copper/margin artifacts.** All outputs are isolated candidate or review-only files.
- **Produce findings/pass-fail/compliance judgments.** These stages produce review artifacts only; no pass/fail/compliance fields are emitted.
- **Keep `safe_for_core_apply` and `ready_for_core_apply` as `false`.** No stage through PR37 sets these to true.

### PR26 — AI Packet Phase Driver v0
- **Script**: `scripts/ai_packet_phase_build.py`
- **Purpose**: Generate packetized AI extraction request files from missing-data/readiness inputs.
- **Output artifacts**: `packet_queue.json`, per-packet `request.json`, `context.json`, `prompt.md`, `status.json`.
- **Validation**: Packet queue parses; all required per-packet fields present; status files initialized.

### PR27 — AI Extraction Result Schema and Validator v0
- **Script**: `scripts/ai_extraction_validate.py`
- **Purpose**: Define extraction result schema and structurally validate AI-generated extraction outputs.
- **Output artifacts**: Validation JSON with pass/fail per packet (validation only, not design compliance).
- **Validation**: All validated packets have required fields; structural errors are reported.

### PR28 — AI Patch Builder v0
- **Script**: `scripts/ai_patch_build.py`
- **Purpose**: Create patch bundles from validated extraction results for candidate data fill.
- **Output artifacts**: Patch bundle files under `exports/`.
- **Validation**: Patch bundles parse; all referenced source fields exist in target artifacts.

### PR29 — AI Candidate Input Materialization v0
- **Script**: `scripts/ai_candidate_materialize.py`
- **Purpose**: Materialize candidate input files from patch bundles for downstream processing.
- **Output artifacts**: Candidate input JSON files under `exports/`.
- **Validation**: Materialized inputs parse; all required fields present; no references to missing sources.

### PR30 — AI Candidate Ingestion Adapters v0
- **Script**: `scripts/ai_candidate_adapter_build.py`
- **Purpose**: Generate adapter artifacts/files from candidate input materialization for ingestion.
- **Output artifacts**: Adapter outputs under `exports/`.
- **Validation**: Adapter files parse; all required adapter fields present; no references to missing sources.

### PR31 — AI Candidate Ingestion Workflow Wrapper v0
- **Script**: `scripts/ai_candidate_ingest_workflow.py`
- **Purpose**: Wrap existing ingestion scripts for isolated candidate ingestion (outputs not written to core).
- **Output artifacts**: Isolated candidate ingestion outputs under `exports/candidate/`.
- **Validation**: Candidate outputs are isolated; no writes to authoritative topology/current/rating files.

### PR32 — AI Candidate Promotion Plan / Approval Queue v0
- **Script**: `scripts/ai_candidate_promotion_plan.py`
- **Purpose**: Generate promotion plan and approval queue from candidate ingestion results.
- **Output artifacts**: Promotion plan JSON, approval queue under `exports/`.
- **Validation**: Promotion plan parses; all candidates have corresponding queue entries; source references are valid.

### PR33 — AI Approval Queue Editor v0
- **Script**: `scripts/ai_approval_decision_edit.py`
- **Purpose**: Produce human approval decisions from the PR32 approval queue (a source artifact).
- **Output artifacts**: `ai-approval-decisions.json`, `ai-approval-decision-validation.json`.
- **Validation**: Decisions parse; validation JSON confirms all required fields present. The PR32 approval queue remains a source artifact and is not modified by this stage.

### PR34 — Approved-Only Promotion Apply Dry Run v0
- **Script**: `scripts/ai_promotion_apply_dry_run.py`
- **Purpose**: Execute approved-only promotion as a dry run (no writes to core or candidate files).
- **Output artifacts**: Dry-run output JSON under `exports/`.
- **Validation**: Dry-run output parses; no file system modifications occurred; all approved candidates accounted for.

### PR35 — Approved Promotion Apply to Candidate Core Inputs v0
- **Script**: `scripts/ai_candidate_core_input_apply.py`
- **Purpose**: Apply approved promotion decisions to candidate core input files (not authoritative topology).
- **Output artifacts**: Candidate core input files under `exports/candidate-core/`.
- **Validation**: Applied inputs parse; all changes are within candidate file scope only.

### PR36 — Candidate Core Input Ingestion Workflow v0
- **Script**: `scripts/ai_candidate_core_input_ingest_workflow.py`
- **Purpose**: Ingest candidate core input files through isolated ingestion workflow (not core).
- **Output artifacts**: Isolated candidate ingestion outputs under `exports/candidate-ingest/`.
- **Validation**: Candidate outputs are isolated; no writes to authoritative topology/current/rating files.

### PR37 — Candidate Normalized Promotion Review v0
- **Script**: `scripts/ai_candidate_normalized_promotion_review.py`
- **Purpose**: Produce normalized review output from candidate ingestion results for human review.
- **Output artifacts**: Normalized promotion review JSON under `exports/review/`.
- **Validation**: Review output parses; all required fields present; no pass/fail/compliance judgments emitted.


## Artifact Flow

The complete artifact flow through PR16–PR37:

1. **Missing-data/readiness inputs** → `scripts/missing_data_manifest.py`, `scripts/calculation_readiness_inventory.py`
2. **Packetized AI extraction request generation** → `scripts/ai_packet_phase_build.py` → `packet_queue.json`, per-packet `request.json`/`context.json`/`prompt.md`/`status.json`
3. **AI extraction validation** → `scripts/ai_extraction_validate.py` → validated extraction results with structural pass/fail
4. **Patch bundle creation** → `scripts/ai_patch_build.py` → patch bundles referencing validated extractions
5. **Candidate materialization** → `scripts/ai_candidate_materialize.py` → candidate input files from patch bundles
6. **Candidate adapter generation** → `scripts/ai_candidate_adapter_build.py` → adapter artifacts/files for ingestion
7. **Isolated candidate ingestion** → `scripts/ai_candidate_ingest_workflow.py` + `scripts/ai_candidate_core_input_ingest_workflow.py` → isolated candidate outputs (not core)
8. **Promotion planning and approval queue** → `scripts/ai_candidate_promotion_plan.py` → promotion plan, PR32 approval queue (source artifact)
9. **Human approval decisions** → `scripts/ai_approval_decision_edit.py` → `ai-approval-decisions.json`, `ai-approval-decision-validation.json`. The PR32 approval queue remains a source artifact and is not modified by this stage.
10. **Approved-only dry-run operations** → `scripts/ai_promotion_apply_dry_run.py` → dry-run output (no writes to core)
11. **Candidate core-input apply** → `scripts/ai_candidate_core_input_apply.py` → candidate core input files (not authoritative topology)
12. **Candidate core-input ingestion** → `scripts/ai_candidate_core_input_ingest_workflow.py` → isolated candidate ingestion outputs
13. **Candidate normalized promotion review** → `scripts/ai_candidate_normalized_promotion_review.py` → normalized review output for human review
14. **Future explicit core apply stage** — Not yet implemented. Core artifacts remain unmodified through PR37.


## Model-Role Clarification

### OpenHands Controller Model
- **Model**: `qwen3.6_35b_a3b_openhands` (or configured equivalent)
- **Purpose**: Used to edit docs/code, reason about repo changes, and execute shell commands. This is the agent running this workflow.

### ThomsonLint Text/Reasoning Model
- **Model**: `qwen3.6_35b_a3b_openhands` or the configured local text model
- **Purpose**: Used for text packet review, datasheet extraction, candidate reasoning, and workflow assistance when AI packets are actually executed outside deterministic scripts (PR26–PR37).

### ThomsonLint Vision Model
- **Model**: `qwen_vision`
- **Purpose**: Used for schematic/layout/PNG/board-image review packets and multimodal extraction. This is **not** the OpenHands controller model. The deterministic PR26–PR37 scripts do not directly call `qwen_vision`; they generate/validate/materialize/review artifacts around AI-assisted results. Deeper targeted `qwen_vision` review remains a future priority: targeted circuit/region vision review with extraction, calculations, and cross-checks against schematic JSON, BOM, datasheets, and board JSON.


## Runbook Environment Variables

These are runbook/manual orchestration variables, not working CLI flags. No implemented script consumes them directly as CLI arguments.

| Variable | Purpose |
|----------|---------|
| `LLM_BASE_URL` | OpenHands controller LLM base URL |
| `LLM_MODEL` | OpenHands controller model name (e.g., `qwen3.6_35b_a3b_openhands`) |
| `THOMSONLINT_TEXT_MODEL` | ThomsonLint text/reasoning model for packet execution |
| `VISION_MODEL` or `THOMSONLINT_VISION_MODEL` | ThomsonLint vision model (`qwen_vision`) for multimodal extraction |


## Key Constraints and Boundaries

- **Core artifacts are not modified by PR26–PR37.** All AI-assisted candidate outputs are isolated under `exports/` subdirectories.
- **AI is never called by the deterministic scripts themselves.** PR26–PR37 scripts generate, validate, materialize, review, and plan around AI-assisted results without invoking any model.
- **Existing ingestion scripts may be invoked only by wrapper stages** that write isolated candidate outputs (PR31, PR36). They do not write to authoritative topology/current/rating files.
- **Allocation/calculation reruns are NOT performed after AI candidate promotion** until a future explicit stage exists.
- **Addenda remain review-only until a future merge validator exists.** No stage through PR37 merges addenda into authoritative topology.
- **Findings/pass-fail/compliance judgments are NOT produced by these stages.** PR26–PR37 produce review artifacts only.
- **`safe_for_core_apply` and `ready_for_core_apply` remain `false`** through PR37. No stage sets either to true.


## TestProject Manual Runbook

### Setup
```bash
# Verify Python 3 is available
which python3

# Install poppler-utils if PDFs are present in input/
apt-get update && apt-get install -y poppler-utils

# Verify tools
which pdftoppm
which pdfinfo
pdftoppm -v
pdfinfo -v
```

### PR16–PR25: Topology/Calculation Artifact Generation
```bash
# PR16 — Missing Data Manifest
python3 scripts/missing_data_manifest.py --input input/ --output exports/

# Verify manifest
python3 -m json.tool exports/<project>-missing-data-manifest.json | head -50

# PR17 — Calculation Schemas (docs only; schemas already in repo)
# No script execution required; see docs/calculation_schemas.md and schemas/

# PR18 — Basic Copper Calculations v0
python3 scripts/topology_copper_calculate.py --topology exports/<project>-topology.json --output exports/

# Verify copper calculations
python3 -m json.tool exports/<project>-copper-calculations.json | head -50

# PR19 — Current Model Ingestion v0
python3 scripts/current_model_ingest.py --input input/ --output exports/

# Verify current model
python3 -m json.tool exports/<project>-current-model.json | head -50

# PR20 — Topology Current Allocation v0
python3 scripts/topology_current_allocate.py --current-model exports/<project>-current-model.json --output exports/

# Verify allocation
python3 -m json.tool exports/<project>-current-allocation.json | head -50

# PR21 — Wire Allocated Currents into Copper Calculations (integration step)
# Handled by copper calculation pipeline; verify extended outputs above.

# PR22–PR25 — Via Current Density, Fuse Margin, Connector Pin Margin (margin calculation pipeline)
python3 scripts/topology_margin_calculate.py --topology exports/<project>-topology.json --current-model exports/<project>-current-model.json --output exports/

# Verify margin calculations
python3 -m json.tool exports/<project>-via-current-density.json | head -50
python3 -m json.tool exports/<project>-fuse-margin.json | head -50
python3 -m json.tool exports/<project>-connector-pin-margin.json | head -50

# PR23 — Rating Model Ingestion v0 (standalone)
python3 scripts/rating_model_ingest.py --input input/ --output exports/

# Verify rating model
python3 -m json.tool exports/<project>-rating-model.json | head -50
```

### PR26–PR37: AI/Candidate/Promotion/Review Commands
```bash
# PR26 — AI Packet Phase Driver v0
python3 scripts/ai_packet_phase_build.py --input exports/ --output exports/packets/

# Verify packet queue
python3 -m json.tool exports/packets/packet_queue.json | head -50

# PR27 — AI Extraction Result Schema and Validator v0
python3 scripts/ai_extraction_validate.py --packets exports/packets/ --output exports/validation/

# Verify validation results
python3 -m json.tool exports/validation/extraction-validation.json | head -50

# PR28 — AI Patch Builder v0
python3 scripts/ai_patch_build.py --validations exports/validation/ --output exports/patches/

# Verify patch bundles
python3 -m json.tool exports/patches/patch-bundle.json | head -50

# PR29 — AI Candidate Input Materialization v0
python3 scripts/ai_candidate_materialize.py --patches exports/patches/ --output exports/candidate-inputs/

# Verify materialized inputs
python3 -m json.tool exports/candidate-inputs/candidate-input.json | head -50

# PR30 — AI Candidate Ingestion Adapters v0
python3 scripts/ai_candidate_adapter_build.py --inputs exports/candidate-inputs/ --output exports/adapters/

# Verify adapter artifacts
ls -la exports/adapters/

# PR31 — AI Candidate Ingestion Workflow Wrapper v0
python3 scripts/ai_candidate_ingest_workflow.py --adapters exports/adapters/ --output exports/candidate/

# Verify isolated candidate outputs (not core)
ls -la exports/candidate/

# PR32 — AI Candidate Promotion Plan / Approval Queue v0
python3 scripts/ai_candidate_promotion_plan.py --candidate-outputs exports/candidate/ --output exports/promotion/

# Verify promotion plan and approval queue (source artifact)
python3 -m json.tool exports/promotion/promotion-plan.json | head -50
python3 -m json.tool exports/promotion/approval-queue.json | head -50

# PR33 — AI Approval Queue Editor v0
python3 scripts/ai_approval_decision_edit.py --approval-queue exports/promotion/approval-queue.json --output exports/approvals/

# Verify approval decisions (PR32 queue remains source artifact)
python3 -m json.tool exports/approvals/ai-approval-decisions.json | head -50
python3 -m json.tool exports/approvals/ai-approval-decision-validation.json | head -50

# PR34 — Approved-Only Promotion Apply Dry Run v0
python3 scripts/ai_promotion_apply_dry_run.py --approvals exports/approvals/ --output exports/dry-run/

# Verify dry-run output (no file system modifications)
python3 -m json.tool exports/dry-run/dry-run-result.json | head -50

# PR35 — Approved Promotion Apply to Candidate Core Inputs v0
python3 scripts/ai_candidate_core_input_apply.py --approvals exports/approvals/ --output exports/candidate-core/

# Verify candidate core inputs (not authoritative topology)
ls -la exports/candidate-core/

# PR37 — Candidate Normalized Promotion Review v0
python3 scripts/ai_candidate_normalized_promotion_review.py --candidate-ingest outputs from PR36 --output exports/review/

# Verify normalized review output
python3 -m json.tool exports/review/promotion-review.json | head -50
```

### Expected Artifact Directories
| Directory | Contents |
|-----------|----------|
| `exports/` | Topology/current/copper/margin/rating authoritative artifacts (PR16–PR25) |
| `exports/packets/` | AI packet request files (PR26) |
| `exports/validation/` | Extraction validation results (PR27) |
| `exports/patches/` | Patch bundles (PR28) |
| `exports/candidate-inputs/` | Materialized candidate inputs (PR29) |
| `exports/adapters/` | Adapter artifacts/files (PR30) |
| `exports/candidate/` | Isolated candidate ingestion outputs (PR31) |
| `exports/promotion/` | Promotion plan, approval queue source artifact (PR32) |
| `exports/approvals/` | AI approval decisions and validation (PR33) |
| `exports/dry-run/` | Dry-run promotion output (PR34) |
| `exports/candidate-core/` | Candidate core input files (PR35) |
| `exports/review/` | Normalized promotion review output (PR37) |

### Expected Safe Booleans and Flags
- All PR26–PR37 outputs have `safe_for_core_apply: false` or equivalent review-only flag.
- No PR26–PR37 stage sets `ready_for_core_apply: true`.
- Candidate outputs are isolated under `exports/candidate/`, `exports/candidate-core/`, and `exports/review/`.
- Authoritative topology/current/copper/margin files from PR16–PR25 remain unmodified.


## Required Next Steps

After PR37, the next step is:

1. **Update PLAN.md and OPENHANDS_REVIEW.md** — This documentation task (completed).
2. **Run a full TestProject pipeline through PR16–PR37** — Execute all deterministic scripts end-to-end with real TestProject data.
3. **Inspect artifacts manually** — Verify artifact flow, isolation boundaries, and review-only flags using `python -m json.tool` and `head`.
4. **Decide PR38 scope** — Based on manual inspection results, determine whether PR38 should be:
   - Explicit core-input apply with opt-in flag, or
   - Addenda merge validator, or
   - Missing-data readiness rerun after approved promotion, or
   - Dedicated full-pipeline smoke/documentation alignment PR.


## Proposed/Future PRs

The following PRs are **proposed/future** and not yet implemented:

| PR | Title | Status |
|----|-------|--------|
| PR38 | Full TestProject Pipeline Smoke / Documentation Alignment, or Explicit Core-Input Apply with Opt-In Flag | Proposed/Future |
| PR39 | Addenda Merge Validator | Proposed/Future |
| PR40 | Missing-Data Readiness Rerun After Approved Promotion | Proposed/Future |
| PR41 | Allocation/Calculation Rerun After Explicit Promotion | Proposed/Future |
| PR42 | Report Integration / Human Review Summary | Proposed/Future |

These stages are bounded: none may modify authoritative topology/current/copper/margin artifacts without an explicit opt-in mechanism, and none may merge addenda before a merge validator (PR39) exists.

## PR38 — Phase Driver Topology/AI Workflow Integration v0

PR38 adds a workflow-aware driver layer while preserving the original evidence_review phase driver. The legacy command remains valid and continues to mean the original OpenHands-backed numeric phases 1-22:

```bash
./scripts/run_phase_driver.sh TestProject 1 22
```

The topology/current/rating/calculation and AI-assisted candidate workflow now uses an explicit profile:

```bash
./scripts/run_phase_driver.sh TestProject --workflow topology_ai --start pr16 --end pr37 --dry-run
```

The topology_ai profile runs deterministic PR16-PR25 stages directly and keeps PR26-PR37 isolated under `exports/<project>/phase_runs/topology_ai/<run-id>/`. It does not route these stages through OpenHands, does not call AI services, and does not reinterpret the original evidence_review phases.

PR26 builds prompt-ready packets only. Missing raw AI responses block PR27 and cause PR28-PR37 to be skipped or blocked with a clear blocker reference unless fixture or existing-artifact mode is explicitly requested. The driver reports qwen_vision configuration from environment routing, but `qwen_vision_invoked` remains false unless an implemented stage actually invokes it.

PR26-PR37 remain review-only: no authoritative core outputs are written, no promotions are applied to core, no addenda are merged, no post-promotion allocation or calculation reruns are performed, and `safe_for_core_apply` / `ready_for_core_apply` remain false. Full core apply remains a future explicit stage.

Use the topology_ai driver for PR16-PR37 validation. Do not use the old numeric 1-22 evidence_review run for topology/AI validation.
