from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


BOM_EXTENSIONS = {".csv"}
BOARD_EXTENSIONS = {".tgz", ".zip", ".xml", ".ipc2581", ".ipc", ".cvg", ".net", ".asc", ".pads", ".stackup"}
PDF_EXTENSIONS = {".pdf"}
DESIGN_SOURCE_EXTENSIONS = BOM_EXTENSIONS | BOARD_EXTENSIONS | PDF_EXTENSIONS


@dataclass(frozen=True)
class InputRootSelection:
    selected_input_root: Path
    input_root_selection_reason: str
    discovered_bom_candidates: list[str]
    discovered_board_candidates: list[str]
    discovered_pdf_candidates: list[str]
    usable_candidate_count: int

    @property
    def has_usable_candidates(self) -> bool:
        return self.usable_candidate_count > 0


def display_path(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _candidate_paths(root: Path) -> tuple[list[str], list[str], list[str]]:
    bom: list[str] = []
    board: list[str] = []
    pdf: list[str] = []
    if not root.exists() or not root.is_dir():
        return bom, board, pdf
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix in BOM_EXTENSIONS:
            bom.append(path.relative_to(root).as_posix())
        if suffix in BOARD_EXTENSIONS or path.name.lower().endswith(".tar.gz"):
            board.append(path.relative_to(root).as_posix())
        if suffix in PDF_EXTENSIONS:
            pdf.append(path.relative_to(root).as_posix())
    return bom, board, pdf


def inspect_input_root(root: Path, repo_root: Path) -> InputRootSelection:
    bom, board, pdf = _candidate_paths(root)
    usable_count = len(bom) + len(board) + len(pdf)
    root_display = display_path(root, repo_root)
    reason = (
        f"selected {root_display}: discovered {usable_count} usable design-source candidate(s)"
        if usable_count
        else f"selected {root_display}: no usable design-source candidates discovered"
    )
    return InputRootSelection(
        selected_input_root=root,
        input_root_selection_reason=reason,
        discovered_bom_candidates=bom,
        discovered_board_candidates=board,
        discovered_pdf_candidates=pdf,
        usable_candidate_count=usable_count,
    )


def resolve_converter_input_root(repo_root: Path, project_name: str, requested_input_root: Path | str) -> InputRootSelection:
    requested = Path(requested_input_root)
    if not requested.is_absolute():
        requested = repo_root / requested
    requested = requested.resolve()
    repo_root = repo_root.resolve()

    default_input = (repo_root / "input").resolve()
    default_project_input = (default_input / project_name).resolve()
    project_root = (repo_root / project_name).resolve()

    if requested == default_input:
        candidates = [project_root, default_project_input, default_input]
    else:
        candidates = [requested, project_root, default_project_input, default_input]

    seen: set[Path] = set()
    inspected: list[InputRootSelection] = []
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.exists() and candidate.is_dir():
            selection = inspect_input_root(candidate, repo_root)
            if selection.has_usable_candidates:
                return selection
            inspected.append(selection)

    if inspected:
        fallback = inspected[0]
        return InputRootSelection(
            selected_input_root=fallback.selected_input_root,
            input_root_selection_reason=(
                "no usable design-source candidates discovered in any candidate input root; "
                f"falling back to {display_path(fallback.selected_input_root, repo_root)}"
            ),
            discovered_bom_candidates=fallback.discovered_bom_candidates,
            discovered_board_candidates=fallback.discovered_board_candidates,
            discovered_pdf_candidates=fallback.discovered_pdf_candidates,
            usable_candidate_count=fallback.usable_candidate_count,
        )

    return InputRootSelection(
        selected_input_root=requested,
        input_root_selection_reason=(
            "no candidate input roots exist; selected requested path "
            f"{display_path(requested, repo_root)}"
        ),
        discovered_bom_candidates=[],
        discovered_board_candidates=[],
        discovered_pdf_candidates=[],
        usable_candidate_count=0,
    )
