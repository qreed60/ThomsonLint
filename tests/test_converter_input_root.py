from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from converter_input_root import resolve_converter_input_root  # noqa: E402


def test_converter_input_root_prefers_project_folder_with_sources(tmp_path: Path) -> None:
    project = tmp_path / "TestProject"
    project.mkdir()
    (project / "babel_bom.csv").write_text("Designator,Value\nU1,MCU\n", encoding="utf-8")
    (project / "Babel Fish.tgz").write_bytes(b"not a real odb archive")
    (project / "babel_ipc.xml").write_text("<IPC2581/>", encoding="utf-8")
    (tmp_path / "input").mkdir()

    selection = resolve_converter_input_root(tmp_path, "TestProject", "input")

    assert selection.selected_input_root == project.resolve()
    assert selection.discovered_bom_candidates == ["babel_bom.csv"]
    assert "Babel Fish.tgz" in selection.discovered_board_candidates
    assert "babel_ipc.xml" in selection.discovered_board_candidates


def test_converter_input_root_uses_legacy_input_when_project_absent(tmp_path: Path) -> None:
    legacy = tmp_path / "input"
    legacy.mkdir()
    (legacy / "legacy_bom.csv").write_text("Designator,Value\nU1,MCU\n", encoding="utf-8")

    selection = resolve_converter_input_root(tmp_path, "LegacyProject", "input")

    assert selection.selected_input_root == legacy.resolve()
    assert selection.discovered_bom_candidates == ["legacy_bom.csv"]


def test_converter_input_root_prefers_project_over_legacy_input(tmp_path: Path) -> None:
    project = tmp_path / "TestProject"
    legacy = tmp_path / "input"
    project.mkdir()
    legacy.mkdir()
    (project / "project_bom.csv").write_text("Designator,Value\nU1,MCU\n", encoding="utf-8")
    (legacy / "legacy_bom.csv").write_text("Designator,Value\nR1,10k\n", encoding="utf-8")

    selection = resolve_converter_input_root(tmp_path, "TestProject", "input")

    assert selection.selected_input_root == project.resolve()
    assert selection.discovered_bom_candidates == ["project_bom.csv"]


def test_converter_input_root_can_select_input_project_folder(tmp_path: Path) -> None:
    nested = tmp_path / "input" / "NestedProject"
    nested.mkdir(parents=True)
    (nested / "nested_ipc.xml").write_text("<IPC2581/>", encoding="utf-8")
    (tmp_path / "input" / "legacy_bom.csv").write_text("Designator,Value\nR1,10k\n", encoding="utf-8")

    selection = resolve_converter_input_root(tmp_path, "NestedProject", "input")

    assert selection.selected_input_root == nested.resolve()
    assert selection.discovered_board_candidates == ["nested_ipc.xml"]
