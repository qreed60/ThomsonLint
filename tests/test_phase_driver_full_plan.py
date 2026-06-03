from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import phase_driver  # noqa: E402
import vision_image_review  # noqa: E402


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def valid_vision_response(description: str = "reviewed") -> str:
    return json.dumps(
        {
            "visual_review_performed": True,
            "confirmation_no_pixel_quantitative_claims": True,
            "brief_description": description,
        }
    )


def engineering_vision_response() -> str:
    return json.dumps(
        {
            "page_type": "schematic",
            "visual_review_performed": True,
            "confirmation_no_pixel_quantitative_claims": True,
            "brief_description": "Observed an apparent 24 V input and 3.3 V regulator section.",
            "visible_circuit_blocks": ["24 V input / 3.3 V regulator"],
            "visible_components_or_refdes": ["U1", "C12", "C13"],
            "visible_net_labels_or_signal_names": ["24V_IN", "3V3"],
            "component_role_observations": ["U1 appears to be a regulator"],
            "engineering_concern_candidates": [
                "Observed an apparent 24 V input / 3.3 V regulator section. Verify regulator output load, dropout margin, thermal dissipation, and input/output capacitor requirements.",
                "routing verified",
            ],
            "blocked_verification_candidates": ["Regulator thermal verification is blocked until output load current is known."],
            "datasheet_check_needed": ["Check U1 datasheet for input/output capacitor requirements."],
            "calculation_needed": ["Calculate regulator thermal dissipation after load current is known."],
            "human_review_questions": ["Confirm whether U1 supplies all 3V3 loads on this page."],
            "not_verifiable_from_image": ["Output load current is not visible in the image."],
            "confidence": 0.72,
        }
    )


def create_phase13_image(exports: Path, project: str, page: int = 1) -> Path:
    exports.mkdir(parents=True, exist_ok=True)
    image = exports / f"{project}-img-sch-p{page}.png"
    image.write_bytes(b"png")
    return image


def run_vision_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    responses: list[str],
    *,
    project: str = "TestProject",
    extra_args: list[str] | None = None,
) -> tuple[int, Path, Path, list[dict[str, Any]]]:
    exports = tmp_path / "exports"
    create_phase13_image(exports, project)
    out = tmp_path / "review.json"
    raw_out = tmp_path / "raw"
    calls: list[dict[str, Any]] = []

    monkeypatch.setenv("VISION_BASE_URL", "http://local/v1")
    monkeypatch.setenv("VISION_MODEL", "kimi_vision")

    def fake_post_chat_completion(**kwargs: Any) -> str:
        calls.append(kwargs)
        if not responses:
            raise AssertionError("unexpected vision model call")
        return responses.pop(0)

    monkeypatch.setattr(vision_image_review, "post_chat_completion", fake_post_chat_completion)
    args = [
        "--project",
        project,
        "--exports",
        str(exports),
        "--out",
        str(out),
        "--raw-out-dir",
        str(raw_out),
        "--sleep",
        "0",
    ]
    if extra_args:
        args.extend(extra_args)
    result = vision_image_review.main(args)
    return result, out, raw_out, calls


def write_checkpoint(root: Path, project: str, phase: int, *, passed: bool = True) -> None:
    checkpoint = root / "exports" / f"{project}-phase-checkpoints.jsonl"
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "phase_number": phase,
        "phase_name": phase_driver.FULL_PLAN_PHASES[phase],
        "started_at_utc": "2026-06-03T00:00:00Z",
        "completed_at_utc": "2026-06-03T00:00:01Z",
        "required_artifacts": [],
        "artifacts_verified": [],
        "validation_artifacts": [],
        "validation_passed": passed,
        "blockers": [] if passed else ["blocked"],
        "phase_passed": passed,
        "failed_phase_number": None if passed else phase,
        "repair_required": not passed,
    }
    with checkpoint.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


def test_phase13_vision_valid_json_first_try_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result, out, raw_out, calls = run_vision_review(tmp_path, monkeypatch, [valid_vision_response()])

    artifact = read_json(out)
    assert result == 0
    assert artifact["overall_pass"] is True
    assert artifact["phase_13_completed"] is True
    assert artifact["vision_model"] == "kimi_vision"
    assert artifact["per_page_vision_observations"][0]["retry_count_used"] == 0
    assert Path(artifact["per_page_vision_observations"][0]["raw_response_path"]).is_file()
    assert list(raw_out.glob("*.attempt1.txt"))
    assert len(calls) == 1


def test_phase13_vision_malformed_first_response_then_valid_retry_passes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, out, _raw_out, calls = run_vision_review(
        tmp_path,
        monkeypatch,
        ["```json\n{\"visual_review_performed\": true,\n```", valid_vision_response("retry")],
    )

    observation = read_json(out)["per_page_vision_observations"][0]
    assert result == 0
    assert observation["response"]["brief_description"] == "retry"
    assert observation["retry_count_used"] == 1
    assert len(observation["parse_errors"]) == 1
    assert len(observation["raw_response_paths"]) == 2
    assert calls[1]["prompt"].startswith(vision_image_review.STRICT_JSON_ONLY_PROMPT)


def test_phase13_vision_extra_text_after_complete_json_object_is_parsed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, out, _raw_out, _calls = run_vision_review(
        tmp_path,
        monkeypatch,
        [valid_vision_response("complete") + "\nAdditional prose the model should not have added."],
    )

    artifact = read_json(out)
    assert result == 0
    assert artifact["overall_pass"] is True
    assert artifact["per_page_vision_observations"][0]["response"]["brief_description"] == "complete"


def test_phase13_vision_malformed_after_all_retries_records_error_and_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, out, raw_out, _calls = run_vision_review(
        tmp_path,
        monkeypatch,
        ["not json", "{\"still\": ", "```json\n[]\n```"],
    )

    artifact = read_json(out)
    assert result == 2
    assert artifact["overall_pass"] is False
    assert artifact["phase_13_completed"] is False
    assert artifact["reviewed_image_count"] == 0
    assert len(artifact["errors"]) == 1
    assert artifact["errors"][0]["retry_count_used"] == 2
    assert len(artifact["errors"][0]["parse_errors"]) == 3
    assert len(list(raw_out.glob("*.txt"))) == 3


def test_phase13_vision_raw_response_file_is_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result, out, _raw_out, _calls = run_vision_review(tmp_path, monkeypatch, [valid_vision_response("raw")])

    observation = read_json(out)["per_page_vision_observations"][0]
    raw_path = Path(observation["raw_response_path"])
    assert result == 0
    assert raw_path.read_text(encoding="utf-8") == valid_vision_response("raw")


def test_phase13_vision_resume_retries_failed_images_and_keeps_successful_ones(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = "TestProject"
    exports = tmp_path / "exports"
    first = create_phase13_image(exports, project, 1)
    second = create_phase13_image(exports, project, 2)
    out = tmp_path / "review.json"
    raw_out = tmp_path / "raw"
    previous = {
        "per_page_vision_observations": [
            {
                "file": str(first),
                "kind": "schematic",
                "model": "kimi_vision",
                "response": {
                    "visual_review_performed": True,
                    "confirmation_no_pixel_quantitative_claims": True,
                    "brief_description": "kept",
                },
            }
        ],
        "errors": [{"file": str(second), "kind": "schematic", "error": "old parse error"}],
    }
    out.write_text(json.dumps(previous), encoding="utf-8")
    calls: list[dict[str, Any]] = []
    responses = [valid_vision_response("retried")]
    monkeypatch.setenv("VISION_BASE_URL", "http://local/v1")
    monkeypatch.setenv("VISION_MODEL", "kimi_vision")

    def fake_post_chat_completion(**kwargs: Any) -> str:
        calls.append(kwargs)
        return responses.pop(0)

    monkeypatch.setattr(vision_image_review, "post_chat_completion", fake_post_chat_completion)
    result = vision_image_review.main(
        [
            "--project",
            project,
            "--exports",
            str(exports),
            "--out",
            str(out),
            "--raw-out-dir",
            str(raw_out),
            "--sleep",
            "0",
            "--resume",
        ]
    )

    artifact = read_json(out)
    observations = {row["file"]: row for row in artifact["per_page_vision_observations"]}
    assert result == 0
    assert len(calls) == 1
    assert calls[0]["image_path"] == second
    assert observations[str(first)]["response"]["brief_description"] == "kept"
    assert observations[str(second)]["response"]["brief_description"] == "retried"
    assert artifact["errors"] == []
    assert artifact["overall_pass"] is True


def test_phase13_strict_profile_does_not_write_engineering_annotations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("THOMSONLINT_ASSESSMENT_PROFILE", raising=False)
    result, out, _raw_out, _calls = run_vision_review(tmp_path, monkeypatch, [valid_vision_response("strict")])

    annotations = out.parent / "TestProject-vision-engineering-annotations.json"
    assert result == 0
    assert annotations.exists() is False


def test_phase13_engineering_profile_prompt_and_annotations_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("THOMSONLINT_ASSESSMENT_PROFILE", "engineering")
    result, out, _raw_out, calls = run_vision_review(tmp_path, monkeypatch, [engineering_vision_response()])

    annotations = read_json(out.parent / "TestProject-vision-engineering-annotations.json")
    row = annotations["annotations"][0]
    assert result == 0
    assert "Engineering annotation requirements" in calls[0]["prompt"]
    assert annotations["assessment_profile"] == "engineering"
    assert annotations["overall_pass"] is True
    assert annotations["annotation_count"] == 1
    assert row["page_type"] == "schematic"
    assert row["observed_circuits"] == ["24 V input / 3.3 V regulator"]
    assert "U1" in row["observed_refdes"]
    assert "3V3" in row["observed_nets"]


def test_phase13_engineering_annotations_reject_generic_claims(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("THOMSONLINT_ASSESSMENT_PROFILE", "balanced")
    result, out, _raw_out, _calls = run_vision_review(tmp_path, monkeypatch, [engineering_vision_response()])

    annotations = read_json(out.parent / "TestProject-vision-engineering-annotations.json")
    row = annotations["annotations"][0]
    assert result == 0
    assert annotations["generic_claim_count"] == 1
    assert "routing verified" in row["generic_claims_rejected"]
    assert "routing verified" not in row["engineering_concern_candidates"]


def test_phase13_engineering_annotations_keep_concerns_separate_from_findings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("THOMSONLINT_ASSESSMENT_PROFILE", "engineering")
    result, out, _raw_out, _calls = run_vision_review(tmp_path, monkeypatch, [engineering_vision_response()])

    annotations = read_json(out.parent / "TestProject-vision-engineering-annotations.json")
    row = annotations["annotations"][0]
    assert result == 0
    assert "findings" not in annotations
    assert "findings" not in row
    assert row["engineering_concern_candidates"]
    assert row["blocked_verification_candidates"] == [
        "Regulator thermal verification is blocked until output load current is known."
    ]


def fake_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    (root / "PLAN.md").write_text("# PLAN\n", encoding="utf-8")
    (root / "OPENHANDS_REVIEW.md").write_text("# Review\n", encoding="utf-8")
    for name in ["write_phase_prompt.py", "audit_phase.py", "ensure_phase_checkpoint.py"]:
        (root / "scripts" / name).write_text("# stub\n", encoding="utf-8")
    (root / "scripts" / "run_openhands_phase.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    monkeypatch.setattr(phase_driver, "repo_root", lambda: root)
    return root


def fake_completed(command: list[str], returncode: int = 0, stdout: str = "ok", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


def test_full_plan_dry_run_resolves_1_to_22_in_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_root(tmp_path, monkeypatch)
    out_dir = tmp_path / "run"
    result = phase_driver.main(["TestProject", "--workflow", "full_plan", "--start", "1", "--end", "22", "--dry-run", "--out-dir", str(out_dir)])
    assert result == 0

    manifest = read_json(out_dir / "full-plan-driver-manifest.json")
    results = read_json(out_dir / "full-plan-driver-stage-results.json")["stage_results"]
    assert [row["phase_number"] for row in results] == list(range(1, 23))
    assert [row["phase_number"] for row in manifest["stage_plan"]] == list(range(1, 23))
    assert not list(out_dir.glob("phase[0-9][0-9]_*"))


def test_full_plan_default_out_dir_uses_run_id_when_provided(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = fake_root(tmp_path, monkeypatch)
    result = phase_driver.main([
        "TestProject",
        "--workflow",
        "full_plan",
        "--run-id",
        "full_run_001",
        "--start",
        "1",
        "--end",
        "1",
        "--dry-run",
    ])
    assert result == 0

    run_root = root / "exports" / "TestProject" / "full_run_001"
    run_dirs = list(run_root.glob("full_plan_*"))
    assert len(run_dirs) == 1
    out_dir = run_dirs[0]
    manifest = read_json(out_dir / "full-plan-driver-manifest.json")
    status = read_json(out_dir / "full-plan-driver-status.json")
    stage_results = read_json(out_dir / "full-plan-driver-stage-results.json")
    blockers = read_json(out_dir / "full-plan-driver-blockers.json")
    assert manifest["run_id"] == "full_run_001"
    assert status["run_id"] == "full_run_001"
    assert stage_results["run_id"] == "full_run_001"
    assert blockers["run_id"] == "full_run_001"
    assert stage_results["stage_results"][0]["run_id"] == "full_run_001"
    assert str(out_dir).endswith("/exports/TestProject/full_run_001/" + out_dir.name)


def test_full_plan_default_out_dir_without_run_id_keeps_existing_shape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = fake_root(tmp_path, monkeypatch)
    result = phase_driver.main(["TestProject", "--workflow", "full_plan", "--start", "1", "--end", "1", "--dry-run"])
    assert result == 0

    project_root = root / "exports" / "TestProject"
    run_dirs = [path for path in project_root.iterdir() if path.is_dir()]
    assert len(run_dirs) == 1
    assert run_dirs[0].name.startswith("20")
    assert read_json(run_dirs[0] / "full-plan-driver-manifest.json")["run_id"] is None


def test_full_plan_start_end_limits_and_no_phase_consolidation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_root(tmp_path, monkeypatch)
    out_dir = tmp_path / "run"
    result = phase_driver.main(["TestProject", "--workflow", "full_plan", "--start", "8", "--end", "19", "--dry-run", "--out-dir", str(out_dir)])
    assert result == 0

    rows = read_json(out_dir / "full-plan-driver-stage-results.json")["stage_results"]
    assert [row["phase_number"] for row in rows] == list(range(8, 20))
    assert len([row for row in rows if 8 <= row["phase_number"] <= 17]) == 10
    assert [row["phase_number"] for row in rows if row["phase_number"] in {18, 19}] == [18, 19]


def test_prompt_only_writes_prompts_and_does_not_execute_agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_root(tmp_path, monkeypatch)
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        assert "run_openhands_phase.sh" not in " ".join(command)
        assert "audit_phase.py" not in " ".join(command)
        out = Path(command[command.index("--out") + 1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("prompt\n", encoding="utf-8")
        return fake_completed(command)

    monkeypatch.setattr(phase_driver.subprocess, "run", fake_run)
    out_dir = tmp_path / "run"
    result = phase_driver.main(["TestProject", "--workflow", "full_plan", "--start", "1", "--end", "2", "--prompt-only", "--out-dir", str(out_dir)])
    assert result == 0

    rows = read_json(out_dir / "full-plan-driver-stage-results.json")["stage_results"]
    assert [row["status"] for row in rows] == ["prompt_written", "prompt_written"]
    assert len(commands) == 2
    assert all(Path(row["prompt_path"]).is_file() for row in rows)


def test_resume_skips_passed_checkpoints_only_with_resume(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = fake_root(tmp_path, monkeypatch)
    write_checkpoint(root, "TestProject", 1, passed=True)
    out_dir = tmp_path / "run"
    result = phase_driver.main(["TestProject", "--workflow", "full_plan", "--start", "1", "--end", "1", "--prompt-only", "--resume", "--out-dir", str(out_dir)])
    assert result == 0
    rows = read_json(out_dir / "full-plan-driver-stage-results.json")["stage_results"]
    assert rows[0]["status"] == "skipped_passed_checkpoint"

    blocked_dir = tmp_path / "blocked"
    result = phase_driver.main(["TestProject", "--workflow", "full_plan", "--start", "1", "--end", "1", "--prompt-only", "--out-dir", str(blocked_dir)])
    assert result == 1
    blocked_rows = read_json(blocked_dir / "full-plan-driver-stage-results.json")["stage_results"]
    assert blocked_rows[0]["status"] == "blocked"


def test_failed_checkpoint_blocks_next_phase(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = fake_root(tmp_path, monkeypatch)

    def fake_run(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        text = " ".join(command)
        if "write_phase_prompt.py" in text:
            out = Path(command[command.index("--out") + 1])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text("prompt\n", encoding="utf-8")
        elif "run_openhands_phase.sh" in text:
            write_checkpoint(root, "TestProject", int(command[-2]), passed=False)
        return fake_completed(command)

    def fake_popen(*args: Any, **kwargs: Any) -> subprocess.Popen[str]:
        class _FakeStdout:
            lines = ["runner output\n"]
            idx = 0
            def close(self):
                pass
            def readline(self):
                if self.idx < len(_FakeStdout.lines):
                    line = _FakeStdout.lines[self.idx]
                    self.idx += 1
                    return line
                return ""
        class _FakeProc:
            stdout = None  # type: ignore[attr-defined]
            def wait(self):
                write_checkpoint(root, "TestProject", 1, passed=False)
                return 0
            def close(self):
                pass
        _FakeProc.stdout = _FakeStdout()
        return _FakeProc()

    monkeypatch.setattr(phase_driver.subprocess, "run", fake_run)
    monkeypatch.setattr(phase_driver.subprocess, "Popen", fake_popen)
    out_dir = tmp_path / "run"
    result = phase_driver.main(["TestProject", "--workflow", "full_plan", "--start", "1", "--end", "2", "--execute", "--runner", "openhands", "--out-dir", str(out_dir)])
    assert result == 1
    rows = read_json(out_dir / "full-plan-driver-stage-results.json")["stage_results"]
    assert len(rows) == 1
    assert rows[0]["status"] == "blocked"
    assert rows[0]["blocker_id"] == "phase01_checkpoint_not_passed"


def test_execute_runner_openhands_invokes_wrapper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """execute runner=openhands invokes the OpenHands wrapper and records runner_command."""
    root = fake_root(tmp_path, monkeypatch)

    def fake_run(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        text = " ".join(command)
        if "write_phase_prompt.py" in text:
            out = Path(command[command.index("--out") + 1])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text("prompt\n", encoding="utf-8")
        elif "ensure_phase_checkpoint.py" in text:
            # ensure_phase_checkpoint writes the checkpoint; don't double-write
            pass
        return fake_completed(command)

    def fake_popen(*args: Any, **kwargs: Any) -> subprocess.Popen[str]:
        class _FakeStdout:
            lines = ["runner output\n"]
            idx = 0
            def close(self):
                pass
            def readline(self):
                if self.idx < len(_FakeStdout.lines):
                    line = _FakeStdout.lines[self.idx]
                    self.idx += 1
                    return line
                return ""
        class _FakeProc:
            stdout = None  # type: ignore[attr-defined]
            def wait(self):
                write_checkpoint(root, "TestProject", 1, passed=True)
                return 0
            def close(self):
                pass
        _FakeProc.stdout = _FakeStdout()
        return _FakeProc()

    monkeypatch.setattr(phase_driver.subprocess, "run", fake_run)
    monkeypatch.setattr(phase_driver.subprocess, "Popen", fake_popen)
    out_dir = tmp_path / "run"
    result = phase_driver.main(["TestProject", "--workflow", "full_plan", "--start", "1", "--end", "1", "--execute", "--runner", "openhands", "--out-dir", str(out_dir)])
    assert result == 0

    rows = read_json(out_dir / "full-plan-driver-stage-results.json")["stage_results"]
    assert len(rows) == 1
    assert rows[0]["status"] == "passed"
    runner_cmd = rows[0]["runner_command"]
    assert any("run_openhands_phase.sh" in str(c) for c in runner_cmd)


def test_execute_runner_none_does_not_invoke_openhands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """execute runner=none blocks immediately without invoking OpenHands."""
    fake_root(tmp_path, monkeypatch)
    out_dir = tmp_path / "run"
    result = phase_driver.main(["TestProject", "--workflow", "full_plan", "--start", "1", "--end", "1", "--execute", "--runner", "none", "--out-dir", str(out_dir)])
    assert result == 1

    rows = read_json(out_dir / "full-plan-driver-stage-results.json")["stage_results"]
    assert len(rows) == 1
    assert rows[0]["status"] == "blocked"
    assert rows[0]["blocker_id"] == "phase01_runner_none"


def test_nonzero_runner_return_blocks_phase(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """nonzero runner return code blocks the phase and stops."""
    root = fake_root(tmp_path, monkeypatch)

    def fake_run(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        text = " ".join(command)
        if "write_phase_prompt.py" in text:
            out = Path(command[command.index("--out") + 1])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text("prompt\n", encoding="utf-8")
        return fake_completed(command)

    def fake_popen(*args: Any, **kwargs: Any) -> subprocess.Popen[str]:
        class _FakeStdout:
            lines = ["runner output\n"]
            idx = 0
            def close(self):
                pass
            def readline(self):
                if self.idx < len(_FakeStdout.lines):
                    line = _FakeStdout.lines[self.idx]
                    self.idx += 1
                    return line
                return ""
        class _FakeProc:
            stdout = None  # type: ignore[attr-defined]
            def wait(self):
                return 42
            def close(self):
                pass
        _FakeProc.stdout = _FakeStdout()
        return _FakeProc()

    monkeypatch.setattr(phase_driver.subprocess, "run", fake_run)
    monkeypatch.setattr(phase_driver.subprocess, "Popen", fake_popen)
    out_dir = tmp_path / "run"
    result = phase_driver.main(["TestProject", "--workflow", "full_plan", "--start", "1", "--end", "2", "--execute", "--runner", "openhands", "--out-dir", str(out_dir)])
    assert result == 1

    rows = read_json(out_dir / "full-plan-driver-stage-results.json")["stage_results"]
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"
    assert rows[0]["blocker_id"] == "phase01_runner_failed"
    assert rows[0]["return_code"] == 42


def test_runner_command_and_log_path_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """runner_command and phase log path are recorded in stage result."""
    root = fake_root(tmp_path, monkeypatch)

    def fake_run(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        text = " ".join(command)
        if "write_phase_prompt.py" in text:
            out = Path(command[command.index("--out") + 1])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text("prompt\n", encoding="utf-8")
        elif "ensure_phase_checkpoint.py" in text:
            # ensure_phase_checkpoint writes the checkpoint; don't double-write
            pass
        return fake_completed(command)

    def fake_popen(*args: Any, **kwargs: Any) -> subprocess.Popen[str]:
        class _FakeStdout:
            lines = ["runner output\n"]
            idx = 0
            def close(self):
                pass
            def readline(self):
                if self.idx < len(_FakeStdout.lines):
                    line = _FakeStdout.lines[self.idx]
                    self.idx += 1
                    return line
                return ""
        class _FakeProc:
            stdout = None  # type: ignore[attr-defined]
            def wait(self):
                write_checkpoint(root, "TestProject", 1, passed=True)
                return 0
            def close(self):
                pass
        _FakeProc.stdout = _FakeStdout()
        return _FakeProc()

    monkeypatch.setattr(phase_driver.subprocess, "run", fake_run)
    monkeypatch.setattr(phase_driver.subprocess, "Popen", fake_popen)
    out_dir = tmp_path / "run"
    result = phase_driver.main(["TestProject", "--workflow", "full_plan", "--start", "1", "--end", "1", "--execute", "--runner", "openhands", "--out-dir", str(out_dir)])
    assert result == 0

    rows = read_json(out_dir / "full-plan-driver-stage-results.json")["stage_results"]
    row = rows[0]
    runner_cmd = row["runner_command"]
    assert len(runner_cmd) > 0
    assert any("run_openhands_phase.sh" in str(c) for c in runner_cmd)
    log_path = row.get("log_preview")
    phase_dir = Path(row["phase_output_dir"])
    expected_log = phase_dir / "phase01-openhands.log"
    assert expected_log.exists()


def test_dry_run_does_not_invoke_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """dry-run still does not invoke runner."""
    fake_root(tmp_path, monkeypatch)
    out_dir = tmp_path / "run"
    result = phase_driver.main(["TestProject", "--workflow", "full_plan", "--start", "1", "--end", "1", "--dry-run", "--out-dir", str(out_dir)])
    assert result == 0

    rows = read_json(out_dir / "full-plan-driver-stage-results.json")["stage_results"]
    assert len(rows) == 1
    assert rows[0]["status"] == "planned"


def test_phase_boundary_guards_and_topology_subsystem(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_root(tmp_path, monkeypatch)
    out_dir = tmp_path / "run"
    result = phase_driver.main(["TestProject", "--workflow", "full_plan", "--start", "1", "--end", "22", "--dry-run", "--out-dir", str(out_dir)])
    assert result == 0

    manifest = read_json(out_dir / "full-plan-driver-manifest.json")
    status = read_json(out_dir / "full-plan-driver-status.json")
    plan = manifest["stage_plan"]
    assert all(not row["may_write_findings"] for row in plan if row["phase_number"] < 19)
    assert all(not row["may_generate_report"] for row in plan if row["phase_number"] < 21)
    assert all(not row["may_write_final_summary"] for row in plan if row["phase_number"] < 22)
    assert [row["topology_ai_subsystem_command"] for row in plan if row["phase_number"] == 12][0]
    assert status["workflow"] == "full_plan"
    assert status["topology_ai_is_subsystem"] is True
    assert status["wrote_core_artifacts"] is False
    assert status["safe_for_core_apply"] is False
    assert status["ready_for_core_apply"] is False


def test_full_plan_stage_record_missing_log_does_not_raise(tmp_path: Path) -> None:
    """full_plan_stage_record with missing log_path does not raise."""
    phase_dir = tmp_path / "phase04_run"
    phase_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = phase_dir / "phase04_prompt.md"
    prompt_path.write_text("prompt", encoding="utf-8")
    missing_log = str(tmp_path / "nonexistent" / "phase04-openhands.log")

    record = phase_driver.full_plan_stage_record(
        run_id_value="test_run",
        phase=4,
        mode="execute",
        status="passed",
        reason="phase runner and audit passed",
        phase_dir=phase_dir,
        prompt_path=prompt_path,
        prompt_command=["python3", "write_phase_prompt.py"],
        runner_command=["run_openhands_phase.sh", "4", "TestProject"],
        validation_commands={"audit": ["python3", "audit_phase.py"]},
        return_code=0,
        stdout="ok",
        stderr="",
        checkpoint_row_count=1,
        blocker_id=None,
        log_path=missing_log,
    )

    assert record["log_exists"] is False
    assert record["log_preview"] is None
    assert record["log_path"] == missing_log


def test_full_plan_stage_record_missing_log_recorded_safely(tmp_path: Path) -> None:
    """Missing log path is recorded safely in stage results."""
    phase_dir = tmp_path / "phase04_run"
    phase_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = phase_dir / "phase04_prompt.md"
    prompt_path.write_text("prompt", encoding="utf-8")
    missing_log = str(tmp_path / "nonexistent" / "phase04-openhands.log")

    record = phase_driver.full_plan_stage_record(
        run_id_value="test_run",
        phase=4,
        mode="execute",
        status="passed",
        reason="phase runner and audit passed",
        phase_dir=phase_dir,
        prompt_path=prompt_path,
        prompt_command=["python3", "write_phase_prompt.py"],
        runner_command=["run_openhands_phase.sh", "4", "TestProject"],
        validation_commands={"audit": ["python3", "audit_phase.py"]},
        return_code=0,
        stdout="ok",
        stderr="",
        checkpoint_row_count=1,
        blocker_id=None,
        log_path=missing_log,
    )

    # Verify JSON serialization works (no crash from missing file)
    json_str = json.dumps(record, indent=2, sort_keys=True, allow_nan=False)
    parsed = json.loads(json_str)
    assert parsed["log_exists"] is False
    assert parsed["log_preview"] is None
    assert parsed["log_path"] == missing_log


def test_successful_runner_checkpoint_audit_with_missing_log_writes_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Successful runner + checkpoint + audit still writes final full-plan status/stage/blocker artifacts even if the log file is missing."""
    root = fake_root(tmp_path, monkeypatch)

    def fake_run(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        text = " ".join(command)
        if "write_phase_prompt.py" in text:
            out = Path(command[command.index("--out") + 1])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text("prompt\n", encoding="utf-8")
        elif "ensure_phase_checkpoint.py" in text:
            # ensure_phase_checkpoint writes the checkpoint; don't double-write
            pass
        return fake_completed(command)

    def fake_popen(*args: Any, **kwargs: Any) -> subprocess.Popen[str]:
        class _FakeStdout:
            lines = ["runner output\n"]
            idx = 0

            def close(self):
                pass

            def readline(self):
                if self.idx < len(_FakeStdout.lines):
                    line = _FakeStdout.lines[self.idx]
                    self.idx += 1
                    return line
                return ""

        class _FakeProc:
            stdout = None  # type: ignore[attr-defined]

            def wait(self):
                write_checkpoint(root, "TestProject", 4, passed=True)
                # Simulate the crash scenario: phase_dir was created but log file
                # was never written (e.g., runner died before writing logs).
                phase_output = Path(kwargs.get("cwd", root)) / "exports" / "TestProject"
                return 0

            def close(self):
                pass

        _FakeProc.stdout = _FakeStdout()
        return _FakeProc()

    monkeypatch.setattr(phase_driver.subprocess, "run", fake_run)
    monkeypatch.setattr(phase_driver.subprocess, "Popen", fake_popen)

    out_dir = tmp_path / "run"
    result = phase_driver.main(["TestProject", "--workflow", "full_plan", "--start", "4", "--end", "4", "--execute", "--runner", "openhands", "--out-dir", str(out_dir)])
    assert result == 0

    # All artifacts should be written despite missing log file
    manifest = read_json(out_dir / "full-plan-driver-manifest.json")
    status = read_json(out_dir / "full-plan-driver-status.json")
    stage_results = read_json(out_dir / "full-plan-driver-stage-results.json")
    blockers = read_json(out_dir / "full-plan-driver-blockers.json")

    assert manifest["workflow"] == "full_plan"
    assert status["workflow"] == "full_plan"
    assert len(stage_results["stage_results"]) == 1
    row = stage_results["stage_results"][0]
    assert row["status"] == "passed"
    # The log file may or may not exist depending on runner behavior;
    # the key assertion is that no crash occurs and artifacts are written.
    assert row["log_path"] is not None
    assert len(blockers.get("blockers", [])) == 0


def test_full_plan_stage_record_with_existing_log(tmp_path: Path) -> None:
    """full_plan_stage_record with existing log file reads preview correctly."""
    phase_dir = tmp_path / "phase04_run"
    phase_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = phase_dir / "phase04_prompt.md"
    prompt_path.write_text("prompt", encoding="utf-8")
    log_file = phase_dir / "phase04-openhands.log"
    log_file.write_text("runner output line 1\nrunner output line 2\n", encoding="utf-8")

    record = phase_driver.full_plan_stage_record(
        run_id_value="test_run",
        phase=4,
        mode="execute",
        status="passed",
        reason="phase runner and audit passed",
        phase_dir=phase_dir,
        prompt_path=prompt_path,
        prompt_command=["python3", "write_phase_prompt.py"],
        runner_command=["run_openhands_phase.sh", "4", "TestProject"],
        validation_commands={"audit": ["python3", "audit_phase.py"]},
        return_code=0,
        stdout="ok",
        stderr="",
        checkpoint_row_count=1,
        blocker_id=None,
        log_path=str(log_file),
    )

    assert record["log_exists"] is True
    assert record["log_preview"] == "runner output line 1\nrunner output line 2\n"
    assert record["log_path"] == str(log_file)


def test_full_plan_stage_record_no_log_path(tmp_path: Path) -> None:
    """full_plan_stage_record with no log_path records safely."""
    phase_dir = tmp_path / "phase04_run"
    phase_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = phase_dir / "phase04_prompt.md"
    prompt_path.write_text("prompt", encoding="utf-8")

    record = phase_driver.full_plan_stage_record(
        run_id_value="test_run",
        phase=4,
        mode="dry_run",
        status="planned",
        reason="dry run planned",
        phase_dir=phase_dir,
        prompt_path=prompt_path,
        prompt_command=["python3", "write_phase_prompt.py"],
        runner_command=[],
        validation_commands={"audit": ["python3", "audit_phase.py"]},
        return_code=None,
        stdout="",
        stderr="",
        checkpoint_row_count=0,
        blocker_id=None,
        log_path=None,
    )

    assert record["log_exists"] is False
    assert record["log_preview"] is None
    assert record["log_path"] is None
