"""
Page-text index — a lightweight, vector-free store of parsed page content.

At ingest we parse every PDF (after normalizing pptx/docx -> PDF) and persist
each non-empty page's text + metadata to a single JSON file per project.
The lexical retriever reads from here; no embeddings are stored, so ingest is
fast and dependency-light.
"""

import json
from pathlib import Path
from typing import Optional

from .config import DATA_DIR, INDEX_DIR, REPORT_TYPES
from .converter import convert_folder, ConverterUnavailable
from .logger import get_logger
from .pdf_parser import PDFParser

logger = get_logger("page_index")


def collect_pdf_paths(base_dir: Path | None = None) -> list[Path]:
    """Walk <base_dir>/{CLINS,FE,CE}/, normalize pptx/docx -> PDF, collect PDFs.

    ``base_dir`` is the per-project dossier folder (PROJECT_ROOT / project_name).
    When omitted it falls back to the legacy global DATA_DIR.
    """
    base = Path(base_dir) if base_dir is not None else DATA_DIR
    pdfs: list[Path] = []
    for rt in REPORT_TYPES:
        rt_dir = base / rt
        if not rt_dir.exists():
            continue
        # Normalize pptx/docx -> PDF so downstream only sees PDFs.
        try:
            convert_folder(rt_dir)
        except ConverterUnavailable as e:
            logger.warning(
                f"Office conversion unavailable ({e}); only existing PDFs "
                f"in {rt}/ will be indexed."
            )
        pdfs.extend(sorted(rt_dir.glob("*.pdf")))

    # Also index top-level (UNKNOWN) PDFs — files the classifier could not
    # confidently assign to CLINS/FE/CE stay at the folder root. They still
    # carry a text layer (real, or OCR-synthesized) and must be indexed +
    # denoised. infer_report_type() resolves their type to "UNKNOWN" because
    # the parent folder is not a typed subfolder.
    try:
        convert_folder(base)
    except ConverterUnavailable as e:
        logger.warning(
            f"Office conversion unavailable ({e}); only existing PDFs "
            f"at the folder root will be indexed."
        )
    pdfs.extend(sorted(base.glob("*.pdf")))
    return pdfs


def _index_path(project_id: str) -> Path:
    return INDEX_DIR / f"{project_id}.json"


def build_index(project_id: str, base_dir: Path | None = None) -> int:
    """Parse all PDFs, extract page texts, and save the project index.

    Args:
        project_id: identifier for the project collection (also the index key).
        base_dir: per-project dossier folder to read classified PDFs from.
                   Falls back to the global DATA_DIR when omitted.

    Returns:
        Total number of (non-empty) pages indexed.
    """
    pdf_paths = collect_pdf_paths(base_dir)
    if not pdf_paths:
        logger.warning("No PDF files found in the project's type folders.")
        save_index(project_id, [])
        return 0

    pages: list[dict] = []
    for pdf_path in pdf_paths:
        logger.info(f"Indexing: {pdf_path}")
        try:
            with PDFParser(pdf_path) as parser:
                for p in parser.extract_all_pages():
                    pages.append({
                        "report_type": p["report_type"],
                        "filename": p["filename"],
                        "page_index": p["page_index"],
                        "page_label": p["page_label"],
                        "source_path": p["source_path"],
                        "text": p["text"],
                        "signals": p["signals"],
                    })
        except Exception as e:
            logger.warning(f"Failed to index {pdf_path}: {e}")

    save_index(project_id, pages)
    logger.info(
        f"Index built: {len(pages)} pages from {len(pdf_paths)} PDFs "
        f"for project '{project_id}'"
    )
    return len(pages)


def save_index(project_id: str, pages: list[dict]) -> None:
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    path = _index_path(project_id)
    path.write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")


def load_index(project_id: str) -> list[dict]:
    path = _index_path(project_id)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"Failed to load index {path}: {e}")
        return []


def index_exists(project_id: str) -> bool:
    return _index_path(project_id).exists()


def index_count(project_id: str) -> int:
    return len(load_index(project_id))


def delete_index(project_id: str) -> None:
    path = _index_path(project_id)
    if path.exists():
        try:
            path.unlink()
            logger.info(f"Deleted page index: {path}")
        except Exception as e:
            logger.warning(f"Failed to delete index {path}: {e}")
