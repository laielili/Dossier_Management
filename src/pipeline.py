"""
Core pipeline — ingest & package orchestration.

Ingest:   parse all PDFs in /data/{CLINS,FE,CE}/ -> build a lightweight page-text
          index (no embeddings, no vector store).
Package:  discover relevant pages via the lexical retriever
          -> screenshot -> merge PDF.
"""

import shutil
from pathlib import Path
from typing import Optional

from .config import (
    DEFAULT_QUERIES,
    QUERIES_DIR,
    REPORT_TYPES,
    SCREENSHOTS_DIR,
    project_data_dir,
)
from .logger import get_logger
from .pdf_generator import PDFGenerator
from .pdf_parser import PDFParser
from .page_index import (
    build_index,
    load_index,
    index_count,
    delete_index,
)
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

    def ingest(self) -> int:
        """Parse all PDFs and build the page-text index.

        Reads classified PDFs from the per-project folder
        PROJECT_ROOT/<project_id>/{CLINS,FE,CE}/.

        Returns:
            Total number of pages indexed.
        """
        self.init()
        base_dir = project_data_dir(self.project_id)
        total_pages = build_index(self.project_id, base_dir=base_dir)

        if total_pages == 0:
            logger.warning(
                f"No PDF files found in {base_dir}/{{CLINS,FE,CE}}/."
            )
        return total_pages

    # ------------------------------------------------------------------
    # Package
    # ------------------------------------------------------------------

    def package(
        self,
        queries: dict[str, str] | None = None,
        top_n: int | None = None,
        project_owner: str = "",
        target_formula: str = "",
        delete_floor: float | None = None,
    ) -> Path:
        """Run the full package pipeline and produce a synthesis PDF.

        Args:
            queries: optional per-type query overrides.
                     If omitted, reads from queries/*.txt files.
            top_n: optional cap of pages kept PER report type (overrides the
                   config default TOP_N_PER_TYPE). Configurable from the
                   frontend or the CLI --top-n flag.
            target_formula: user-supplied final target formula string. When
                   set, it is baked into the cover of the synthesis PDF
                   (metadata injection) to anchor the downstream AI.
            delete_floor: optional override of the deletion score floor used by
                   the retriever's DELETE policy. When omitted, the retriever
                   reads the persisted frontend override (or config default).

        Returns:
            Path to the generated PDF.
        """
        self.init()

        if self.retriever.count() == 0:
            raise RuntimeError(
                "No pages ingested. Run ingest first."
            )

        # 1. Discover relevant pages via the lexical retriever
        summary_pages = self._discover_summary_pages(
            queries, top_n=top_n, delete_floor=delete_floor
        )

        # 2. Take high-resolution screenshots for each discovered page
        summary_pages = self._enrich_with_screenshots(summary_pages)

        # 3. Generate synthesis PDF (with the target-formula banner on the cover)
        generator = PDFGenerator()
        output_path = generator.generate(
            self.project_id, summary_pages,
            project_owner=project_owner, target_formula=target_formula,
        )

        return output_path

    # ------------------------------------------------------------------
    # Per-type page discovery (delegated to the retriever)
    # ------------------------------------------------------------------

    def _discover_summary_pages(
        self,
        queries: dict[str, str] | None = None,
        top_n: int | None = None,
        delete_floor: float | None = None,
    ) -> list[dict]:
        """Discover relevant pages via the lexical retriever.

        Args:
            queries: per-type query overrides (or read from files if None).
            top_n: optional per-type page cap forwarded to the retriever.
            delete_floor: optional deletion-floor override forwarded to the
                   retriever's DELETE policy.

        Returns items shaped {"metadata", "document"}, then deduplicates by
        (filename, page_index) and sorts by (report_type, filename, page).
        """
        query_map = queries or load_queries_from_files()
        raw = self.retriever.discover(
            query_map, top_n=top_n, delete_floor=delete_floor
        )

        # Deduplicate: one entry per (filename, page_index)
        seen: set[tuple[str, int]] = set()
        deduped: list[dict] = []
        for r in raw:
            key = (
                r["metadata"].get("filename", ""),
                r["metadata"].get("page_index", -1),
            )
            if key not in seen:
                seen.add(key)
                deduped.append(r)

        # Sort: report_type -> filename -> page_index
        deduped.sort(key=lambda r: (
            r["metadata"].get("report_type", ""),
            r["metadata"].get("filename", "").lower(),
            r["metadata"].get("page_index", 0),
        ))

        logger.info(
            f"Discovered {len(deduped)} unique pages across all types "
            f"(after deduplication)"
        )
        return deduped

    def _enrich_with_screenshots(
        self,
        summary_pages: list[dict],
    ) -> list[dict]:
        """Open each source PDF, take a high-res screenshot of the page,
        and add 'screenshot' and 'text' fields to every item."""
        enriched: list[dict] = []
        for item in summary_pages:
            source = item["metadata"].get("source_path", "")
            page_idx = item["metadata"].get("page_index", 0)

            text = item.get("document", "")
            screenshot_path = None

            if source and Path(source).exists():
                try:
                    with PDFParser(Path(source)) as parser:
                        screenshot_path = parser.screenshot_page(page_idx)
                        # Re-extract text from the actual page for accuracy
                        page_text = parser.extract_text(page_idx)
                        if page_text.strip():
                            text = page_text.strip()
                except Exception as e:
                    logger.warning(
                        f"Failed to screenshot {source} page {page_idx}: {e}"
                    )

            enriched.append({
                "text": text,
                "matched_terms": item["metadata"].get("matched_terms", []),
                "screenshot": str(screenshot_path) if screenshot_path else None,
                "filename": item["metadata"].get("filename", ""),
                "report_type": item["metadata"].get("report_type", ""),
                "page_label": item["metadata"].get("page_label", 1),
                "source_path": source,
            })

        return enriched

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
    queries: dict[str, str] | None = None,
    top_n: int | None = None,
    project_owner: str = "",
    target_formula: str = "",
    delete_floor: float | None = None,
) -> Path:
    """One-shot: ingest + package → return output PDF path.

    Args:
        project_id: identifier for the project collection
        queries: optional per-type query overrides
        top_n: optional cap of pages kept PER report type (overrides config default)
        target_formula: user-supplied final target formula (baked into cover)
        delete_floor: optional deletion-floor override (see DossierPipeline.package)
    """
    pipeline = DossierPipeline(project_id)
    pipeline.init()

    n = pipeline.ingest()
    logger.info(f"Ingested {n} pages for project '{project_id}'")

    output_path = pipeline.package(
        queries, top_n=top_n,
        project_owner=project_owner, target_formula=target_formula,
        delete_floor=delete_floor,
    )
    logger.info(f"Package complete → {output_path}")
    return output_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _collect_pdfs() -> list[Path]:
    """Deprecated: collection now lives in page_index.collect_pdf_paths.

    Kept as a thin alias for any external callers.
    """
    from .page_index import collect_pdf_paths
    return collect_pdf_paths()
