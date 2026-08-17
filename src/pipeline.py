"""
Core pipeline — ingest & condense orchestration.

Ingest:    parse all PDFs in <project>/{CLINS,FE,CE}/ -> build a lightweight
           page-text index (no embeddings, no vector store).
Condense:  discover the denoised page set via the lexical retriever, then write
           a cleaned copy of every source dossier (noise pages removed) into the
           deliverable folder as faithful per-document vector PDFs. The merged
           single-file synthesis PDF was discontinued: the downstream client
           accepts multiple files but rejects any single file over 50 MB.
"""

import pymupdf as fitz  # PyMuPDF
import shutil
from pathlib import Path
from typing import Optional

from .config import (
    DEFAULT_QUERIES,
    QUERIES_DIR,
    REPORT_TYPES,
    SCREENSHOTS_DIR,
    DELETE_MIN_KEEP,
    project_data_dir,
)
from .logger import get_logger
from .page_index import (
    build_index,
    index_count,
    delete_index,
    collect_pdf_paths,
)
from .pdf_parser import infer_report_type
from .retriever import build_retriever, Retriever

logger = get_logger("pipeline")


# ---------------------------------------------------------------------------
# Query-file helpers (read / write the unified queries/query.txt lexicon)
# ---------------------------------------------------------------------------

QUERY_FILE = QUERIES_DIR / "query.txt"


def load_query_file() -> str:
    """Read the unified query lexicon from queries/query.txt.

    Falls back to the concatenated DEFAULT_QUERIES text if the file is missing.
    """
    if QUERY_FILE.exists():
        return QUERY_FILE.read_text(encoding="utf-8").strip()
    return "\n\n".join(DEFAULT_QUERIES[rt] for rt in REPORT_TYPES)


def save_query_file(text: str) -> None:
    """Write the unified query lexicon to queries/query.txt."""
    QUERIES_DIR.mkdir(parents=True, exist_ok=True)
    QUERY_FILE.write_text(text.strip() + "\n", encoding="utf-8")
    logger.info(f"Saved unified query file: {QUERY_FILE}")


def load_queries_from_files() -> dict[str, str]:
    """Read the unified queries/query.txt lexicon and replicate it for every
    report type (the retriever scores each type against the same lexicon)."""
    text = load_query_file()
    return {rt: text for rt in REPORT_TYPES}


def save_queries_to_files(queries: dict[str, str]) -> None:
    """Write the unified queries/query.txt lexicon.

    The dict may carry per-type keys; they are expected to be identical (the
    UI edits a single merged file), so the first non-empty value wins.
    """
    text = ""
    for rt in REPORT_TYPES:
        if queries.get(rt):
            text = queries[rt]
            break
    if not text:
        text = next(iter(queries.values()), "")
    save_query_file(text)


# ---------------------------------------------------------------------------
# Pipeline class
# ---------------------------------------------------------------------------

class DossierPipeline:
    """Orchestrates the pipeline for one project."""

    def __init__(self, project_id: str):
        self.project_id = project_id
        self.retriever: Optional[Retriever] = None

    def init(self):
        """Lazy-init: build the lexical retriever."""
        if self.retriever is not None:
            return
        self.retriever = build_retriever("lexical", self.project_id)

    @property
    def total_pages(self) -> int:
        if self.retriever is None:
            self.init()
        return self.retriever.count()

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------

    def ingest(self, base_dir: Optional[Path | str] = None) -> int:
        """Parse all PDFs and build the page-text index.

        Reads classified PDFs from the per-project folder
        ``base_dir/{CLINS,FE,CE}/``. ``base_dir`` defaults to
        ``project_data_dir(project_id)`` (the normal per-project folder) but
        may be overridden — the retrieval flow passes the
        ``retrieved/<name>/`` folder here so the pipeline runs on the cached
        copies rather than the live project tree.

        Returns:
            Total number of pages indexed.
        """
        self.init()
        effective_dir = Path(base_dir) if base_dir is not None else project_data_dir(self.project_id)
        total_pages = build_index(self.project_id, base_dir=effective_dir)

        if total_pages == 0:
            logger.warning(
                f"No PDF files found in {base_dir}/{{CLINS,FE,CE}}/."
            )
        return total_pages

    # ------------------------------------------------------------------
    # Condense (denoise each source dossier -> cleaned per-doc PDFs)
    # ------------------------------------------------------------------

    def condense(
        self,
        output_dir: Path | str,
        top_n: int | None = None,
        source_dir: Optional[Path | str] = None,
    ) -> dict:
        """Denoise every source dossier and write cleaned per-document PDFs.

        For each typed source PDF (CLINS / FE / CE) discovered at ingest, the
        pages the denoising retriever classifies as noise are dropped and the
        surviving pages are written to a faithful vector copy under
        ``output_dir/<report_type>/<filename>.pdf``.

        This replaces the old single merged synthesis PDF. The downstream
        client accepts many files but rejects any single file over 50 MB, so
        we keep the dossiers as separate, denoised documents instead of one
        giant combined file.

        Args:
            output_dir: base folder for the deliverable (typically
                <PROJECT_ROOT>/Dossier_condensed/<project>/).
            top_n: optional per-type page cap forwarded to the retriever
                (None = no ceiling).

        Returns:
            dict with keys: output_dir, files_written (list of
            "<report_type>/<filename>" strings), pages_dropped,
            sources_processed.
        """
        self.init()
        if self.retriever.count() == 0:
            # No text pages were indexed — typically a scanned / image-only PDF
            # with no extractable text layer (and no OCR configured). Lexical
            # noise detection is impossible, so pass the source PDFs through
            # unchanged into the deliverable folder rather than hard-failing.
            logger.warning(
                "condense: 0 text pages indexed — source appears to have no "
                "extractable text layer (scanned/image-only PDF, no OCR). "
                "Passing files through unchanged (no text-based noise removal "
                "is possible without OCR)."
            )
            return self._pass_through(output_dir, source_dir)

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Survivors returned by the denoising retriever, shaped
        # {"metadata": {report_type, filename, page_index, page_label,
        #  source_path, selected_by}, "document": "<page text>"}.
        survivors = self.retriever.discover(top_n=top_n)

        # Group survivors by source_path -> (sorted page indices, report_type).
        kept: dict[str, dict] = {}
        for item in survivors:
            meta = item.get("metadata", {})
            src = meta.get("source_path", "")
            if not src:
                continue
            rt = meta.get("report_type", "")
            entry = kept.setdefault(src, {"indices": set(), "report_type": rt})
            entry["indices"].add(meta.get("page_index", 0))

        files_written: list[str] = []
        pages_dropped = 0
        sources_processed = 0

        for src, info in kept.items():
            src_path = Path(src)
            if not src_path.exists():
                logger.warning(f"condense: source missing, skipped: {src}")
                continue

            rt = info["report_type"] or "UNKNOWN"
            dest_dir = output_dir / rt
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / src_path.name

            try:
                doc = fitz.open(str(src_path))
            except Exception as e:
                logger.warning(f"condense: cannot open {src}: {e}")
                continue

            try:
                total = doc.page_count
                indices = sorted(info["indices"])
                if not indices:
                    # Per-source safety net (mirrors the retriever's per-type
                    # safeguard): keep the first few pages rather than emit a
                    # broken empty PDF.
                    indices = list(range(min(DELETE_MIN_KEEP, total)))
                # Drop every page NOT in `indices`; kept pages stay in their
                # original order (indices are ascending).
                doc.select(indices)
                pages_dropped += (total - doc.page_count)
                # Re-compress on save: the default fitz save() leaves internal
                # streams uncompressed (garbage=0, deflate=0, use_objstms=0),
                # which expands a real (Office/Adobe-exported) PDF and can make
                # the output LARGER than the source despite fewer pages. Turning
                # on deflate* + use_objstms + garbage/clean re-packs the
                # surviving content losslessly — no pixel/vector change, so the
                # downstream AI client reads it identically. Combined with the
                # denoise page drop, the output should come out smaller.
                doc.save(
                    str(dest),
                    garbage=3,
                    clean=1,
                    deflate=1,
                    deflate_images=1,
                    deflate_fonts=1,
                    use_objstms=1,
                )
                files_written.append(f"{rt}/{src_path.name}")
                sources_processed += 1
                logger.info(
                    f"condense: {src_path.name} -> kept {doc.page_count}/"
                    f"{total} pages -> {dest}"
                )
            except Exception as e:
                logger.warning(f"condense: failed writing {dest}: {e}")
            finally:
                doc.close()

        logger.info(
            f"Condense complete: {sources_processed} source(s), "
            f"{len(files_written)} file(s) written to {output_dir}"
        )
        return {
            "output_dir": str(output_dir),
            "files_written": files_written,
            "pages_dropped": pages_dropped,
            "sources_processed": sources_processed,
        }

    # ------------------------------------------------------------------
    # Pass-through (no text layer -> no lexical denoise possible)
    # ------------------------------------------------------------------

    def _pass_through(
        self,
        output_dir: Path | str,
        source_dir: Optional[Path | str] = None,
    ) -> dict:
        """Copy source PDFs into the deliverable unchanged.

        Used when ``condense`` finds 0 indexed text pages (e.g. a scanned /
        image-only PDF with no text layer and no OCR). Lexical noise detection
        cannot run, so the files are written through verbatim, typed by their
        parent folder (or UNKNOWN for top-level files), so the downstream AI
        client still receives a complete, processed deliverable folder.
        """
        base = (
            Path(source_dir)
            if source_dir is not None
            else project_data_dir(self.project_id)
        )
        pdfs = collect_pdf_paths(base)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        files_written: list[str] = []
        for src in pdfs:
            rt = infer_report_type(src)
            dest_dir = output_dir / rt
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / src.name
            try:
                shutil.copy2(src, dest)
                files_written.append(f"{rt}/{src.name}")
                logger.info(f"condense(pass-through): {src.name} -> {dest}")
            except OSError as e:
                logger.warning(f"condense(pass-through): cannot copy {src.name}: {e}")

        logger.info(
            f"Pass-through complete: {len(files_written)} source(s) copied "
            f"unchanged to {output_dir}"
        )
        return {
            "output_dir": str(output_dir),
            "files_written": files_written,
            "pages_dropped": 0,
            "sources_processed": len(files_written),
        }

    # ------------------------------------------------------------------
    # Reset
    # ------------------------------------------------------------------

    def reset(self):
        """Delete the page index + screenshots."""
        if self.retriever is None:
            self.retriever = build_retriever("lexical", self.project_id)
        self.retriever.reset()

        # Clean screenshot cache. Delete files individually and tolerate
        # per-item errors: a bulk rmtree can be blocked by OS / sandbox
        # safe-delete policies that force files into a (unavailable) trash,
        # which would otherwise raise and 500 the reset endpoint.
        for rt in REPORT_TYPES:
            rt_dir = SCREENSHOTS_DIR / rt
            if not rt_dir.exists():
                continue
            for item in list(rt_dir.iterdir()):
                try:
                    if item.is_file() or item.is_symlink():
                        item.unlink()
                    elif item.is_dir():
                        shutil.rmtree(item)
                except OSError as e:
                    logger.warning(f"Could not remove {item}: {e}")
            try:
                rt_dir.rmdir()
            except OSError:
                pass

        logger.info(f"Project '{self.project_id}' reset complete.")


# ---------------------------------------------------------------------------
# Full-pipeline convenience
# ---------------------------------------------------------------------------

def run_full_pipeline(
    project_id: str,
    top_n: int | None = None,
    output_dir: str | None = None,
) -> dict:
    """One-shot: ingest + condense -> return the condense result dict.

    Args:
        project_id: identifier for the project collection
        top_n: optional per-type page cap forwarded to the retriever
        output_dir: deliverable folder; defaults to
            <PROJECT_ROOT>/Dossier_condensed/<project_id>/
    """
    pipeline = DossierPipeline(project_id)
    pipeline.init()

    n = pipeline.ingest()
    logger.info(f"Ingested {n} pages for project '{project_id}'")

    if output_dir is None:
        base = project_data_dir(project_id).parent
        output_dir = Path(base) / "Dossier_condensed" / project_id

    result = pipeline.condense(output_dir=output_dir, top_n=top_n)
    logger.info(f"Condense complete -> {result['output_dir']}")
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _collect_pdfs() -> list[Path]:
    """Deprecated: collection now lives in page_index.collect_pdf_paths.

    Kept as a thin alias for any external callers.
    """
    from .page_index import collect_pdf_paths
    return collect_pdf_paths()
