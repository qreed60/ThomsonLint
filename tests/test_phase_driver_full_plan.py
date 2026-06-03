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


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


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

    monkeypatch.setattr(phase_driver.subprocess, "run", fake_run)
    out_dir = tmp_path / "run"
    result = phase_driver.main(["TestProject", "--workflow", "full_plan", "--start", "1", "--end", "2", "--execute", "--runner", "openhands", "--out-dir", str(out_dir)])
    assert result == 1
    rows = read_json(out_dir / "full-plan-driver-stage-results.json")["stage_results"]
    assert len(rows) == 1
    assert rows[0]["status"] == "blocked"
    assert rows[0]["blocker_id"] == "phase01_checkpoint_not_passed"


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
