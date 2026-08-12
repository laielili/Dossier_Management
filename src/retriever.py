"""
Retriever — drop noise pages so the downstream AI has less to read.

This module is the DENOISING stage of the pipeline (classification and the
final merge/integration live elsewhere). Principle: KEEP every page by default
and delete ONLY pages we can prove are noise, so genuinely evidence-bearing
pages (e.g. a pure-text conclusion) are never dropped.

Deletion is driven by ``classify_noise`` (cover / toc / boilerplate / blank /
closing) plus a cross-page boilerplate pass. A veto layer force-keeps any noise
page whose text contains a high-value term reused from queries/query.txt
(Title Anchors + Table Features). The veto does NOT apply to cover / toc pages
(veto-immune) — those are pure navigation and the package footer already carries
the report title + type. An optional MAX ceiling (``top_n`` > 0) truncates a type
after deletion as a safety net. Survivors are emitted in the original report
order.

Zero extra dependencies, deterministic, explainable.

All implementations return a list of items shaped like:
    {"metadata": {report_type, filename, page_index, page_label, source_path,
                  selected_by},
     "document": "<page text>"}
"""

import re
from abc import ABC, abstractmethod
from collections import Counter

from .config import (
    REPORT_TYPES,
    TOP_N_PER_TYPE,
    DELETE_MIN_KEEP,
    LEXICON_DIMENSIONS,
    TOC_HEADERS,
    QUERIES_DIR,
    BLANK_MAX_CHARS,
    BLANK_MAX_FONT,
    COVER_MIN_FONT,
    COVER_MAX_CHARS,
    COVER_MAX_FIGURES,
    CLOSING_MAX_CHARS,
    BOILERPLATE_MIN_PAGES,
    BOILERPLATE_MIN_UNIQUE_CHARS,
    VETO_MIN_UNIQUE_CHARS,
    VETO_SEED_TERMS,
)
from .logger import get_logger
from .page_index import load_index, index_count, delete_index

logger = get_logger("retriever")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_retriever(
    mode: str | None = None,
    project_id: str = "default",
) -> "Retriever":
    """Construct the retriever for a project.

    Only the lexical retriever is supported (no vector DB / embedding model).
    The ``mode`` argument is accepted for backward compatibility but ignored.
    """
    return LexicalRetriever(project_id)


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class Retriever(ABC):
    """Discovers relevant pages for one project given per-type query text."""

    def __init__(self, project_id: str):
        self.project_id = project_id

    @abstractmethod
    def discover(self, top_n: int | None = None) -> list[dict]:
        """Return page items ({"metadata", "document"}) for merging."""
        ...

    @abstractmethod
    def count(self) -> int:
        """Number of indexable pages available for this project."""
        ...

    def reset(self) -> None:
        """Drop any persisted state for this project."""
        delete_index(self.project_id)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _to_items(pages: list[dict]) -> list[dict]:
    """Convert stored page records into pipeline item shape."""
    return [{
        "metadata": {
            "report_type": p["report_type"],
            "filename": p["filename"],
            "page_index": p["page_index"],
            "page_label": p["page_label"],
            "source_path": p["source_path"],
            "selected_by": p.get("selected_by", []),
        },
        "document": p["text"],
    } for p in pages]


def _token_weights(query: str) -> Counter:
    """Term -> weight (occurrences in the query). Longer terms weigh more
    because the user repeated/emphasized them."""
    toks = re.findall(r"[a-z0-9]+", query.lower())
    return Counter(t for t in toks if len(t) > 2)


def _lexical_score(text: str, query: str, weights: Counter):
    """Weighted count of matched query terms + a phrase-match bonus.

    Returns (score, matched_terms) where matched_terms is the list of query
    terms found in the page text (used for the PDF annotation block).
    """
    lowered = text.lower()
    score = 0.0
    matched: list[str] = []
    for term, w in weights.items():
        if term in lowered:
            score += w
            matched.append(term)
    # Bonus for whole query lines appearing verbatim (e.g. a key phrase).
    for phrase in query.lower().split("\n"):
        phrase = phrase.strip()
        if len(phrase) > 4 and phrase in lowered:
            score += 2.0
    return score, matched


def _is_toc_page(page: dict) -> bool:
    """Detect a Table-of-Contents page.

    A TOC page is recognised when one of TOC_HEADERS appears in the first few
    normalised lines of the page text.
    """
    text = page.get("text", "") or page.get("_norm_text", "")
    if not text:
        return False
    head = [normalize(line) for line in text.splitlines()[:5]]
    for line in head:
        for hdr in TOC_HEADERS:
            if hdr in line:
                return True
    return False


# ---------------------------------------------------------------------------
# Noise classification + veto (page DELETION is noise-based)
# ---------------------------------------------------------------------------

# Closing-page marker phrases (case-insensitive word boundaries).
_CLOSING_RE = re.compile(
    r"\b(thank you|thanks|work in progress|questions?|contact us|"
    r"appendix|end of (report|section|document))\b",
    re.IGNORECASE,
)

_VETO_RE = None  # compiled lazily by get_veto_terms()

# Noise types that are NEVER rescued by the veto layer — they are structural
# navigation / decoration, not content, so even a high-value term on them is
# not worth keeping (the footer already carries the report title + type).
_VETO_IMMUNE_NOISE = ("toc", "cover")


def list_veto_terms() -> list[str]:
    """Return the veto-term list, reused from the unified queries/query.txt
    lexicon (Title Anchors + Table Features sections). Falls back to
    VETO_SEED_TERMS if the file is missing."""
    terms: set[str] = set()
    qf = QUERIES_DIR / "query.txt"
    if qf.exists():
        dims = parse_lexicon(qf.read_text(encoding="utf-8"))
        terms.update(dims.get("title_anchors", []))
        terms.update(dims.get("table_features", []))
    terms.update(VETO_SEED_TERMS)
    return sorted(terms)


def reset_veto_terms() -> None:
    """Drop the cached compiled veto regex so a freshly saved query.txt takes
    effect on the next package run without a server restart."""
    global _VETO_RE
    _VETO_RE = None


def get_veto_terms() -> re.Pattern:
    """Compiled, cached regex of the veto terms (word-boundary, case-insensitive,
    multi-word phrases supported). A page whose text matches is force-kept."""
    global _VETO_RE
    if _VETO_RE is not None:
        return _VETO_RE
    escaped = [re.escape(t.strip()) for t in list_veto_terms() if t.strip()]
    escaped.sort(key=len, reverse=True)  # longer phrases win over single words
    _VETO_RE = re.compile(r"(?i)\b(" + "|".join(escaped) + r")\b")
    return _VETO_RE


def _content_lines(page: dict) -> list[str]:
    """Non-trivial text lines of a page (used for cross-page boilerplate
    detection). Drops very short fragments that are usually footers/numbers."""
    text = page.get("text", "") or ""
    return [ln.strip() for ln in text.splitlines() if len(ln.strip()) >= 4]


def classify_noise(page: dict) -> str | None:
    """Return a noise category if the page is provably noise, else None.

    Conservative by design: only returns a category on strong structural
    evidence, so genuine content pages (including pure-text conclusions) are
    never flagged. The caller applies the veto layer on top, but cover / toc
    pages are veto-immune (deleted regardless of any high-value term), since
    the package footer already carries the report title + type.
    """
    sig = page.get("signals") or {}
    text = page.get("text", "") or ""
    n_chars = len(text.strip())
    max_font = sig.get("max_font", 0.0) or 0.0
    img_ratio = sig.get("img_area_ratio", 0.0) or 0.0
    figures = sig.get("figures", 0) or 0
    has_table = bool(sig.get("table"))
    has_bullets = bool(sig.get("bullets"))

    if _is_toc_page(page):
        return "toc"
    # Closing / thank-you pages (linguistic marker). Checked before the generic
    # blank so a short "Thank you" page is labelled correctly (not "blank").
    if n_chars < CLOSING_MAX_CHARS and _CLOSING_RE.search(text):
        return "closing"
    # Cover / section-divider pages: low content, large title font, no
    # table/list, few figures. Checked before blank so a large-font short title
    # is a cover, not a near-blank page.
    if (not has_table and not has_bullets and figures < COVER_MAX_FIGURES
            and max_font >= COVER_MIN_FONT and n_chars < COVER_MAX_CHARS):
        return "cover"
    # Generic near-blank: little text, no figure/table/list, AND small font.
    if (n_chars < BLANK_MAX_CHARS and figures == 0 and not has_table
            and not has_bullets and max_font < BLANK_MAX_FONT):
        return "blank"
    return None


def _detect_boilerplate(candidates: list[dict]) -> None:
    """Cross-page pass: flag pages whose text is almost entirely lines repeated
    across many other pages of the same type (template / footer-only pages).
    Sets ``_noise = "boilerplate"`` and ``_boiler_unique`` (unique-char count) on
    matching pages. Skips pages already flagged as another noise type."""
    line_counter: Counter = Counter()
    for p in candidates:
        for line in _content_lines(p):
            line_counter[line] += 1
    for p in candidates:
        if p.get("_noise"):
            continue
        uniq = [ln for ln in _content_lines(p) if line_counter[ln] < BOILERPLATE_MIN_PAGES]
        uniq_text = " ".join(uniq).strip()
        if len(uniq_text) < BOILERPLATE_MIN_UNIQUE_CHARS:
            p["_noise"] = "boilerplate"
            p["_boiler_unique"] = len(uniq_text)


# ---------------------------------------------------------------------------
# Shared text + lexicon helpers (used by the veto layer)
# ---------------------------------------------------------------------------

def normalize(text: str) -> str:
    """Lowercase, strip punctuation (keep alphanumerics + spaces), collapse
    whitespace. Makes token and phrase matching punctuation-agnostic so e.g.
    'Skin-elasticity' and 'Skin elasticity' normalise identically, and avoids
    the old naive substring bug ('log' matching 'biology').
    """
    s = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    return re.sub(r"\s+", " ", s).strip()


SECTION_ALIASES = {
    "title anchors": "title_anchors",
    "title anchor": "title_anchors",
    "metric keywords": "metric_keywords",
    "metric keyword": "metric_keywords",
    "table features": "table_features",
    "table feature": "table_features",
    "other": "other",
}


def parse_lexicon(raw: str) -> dict[str, list[str]]:
    """Parse a sectioned queries/{type}.txt into {dimension: [terms]}.

    Sections are introduced by a '# <Dimension>' header line. Any other '#'
    line is a comment. One term/phrase per line; multi-word entries are kept
    intact as phrases. A file with no section headers (legacy flat format)
    falls back to the 'metric_keywords' dimension so callers stay compatible.
    """
    dims: dict[str, list[str]] = {d: [] for d in LEXICON_DIMENSIONS}
    current: str | None = None
    found_section = False
    for line in raw.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            header = s[1:].strip().lower()
            mapped = None
            for alias, dim in SECTION_ALIASES.items():
                if alias == header or header.startswith(alias) or header.endswith(alias):
                    mapped = dim
                    break
            if mapped:
                current = mapped
                found_section = True
            else:
                current = None  # comment line
            continue
        if current is None:
            if not found_section:
                current = "metric_keywords"  # legacy flat fallback
            else:
                continue  # comment-only region between sections
        dims[current].append(s)
    return dims




# Lexical (default & only retriever)
# ---------------------------------------------------------------------------

class LexicalRetriever(Retriever):
    """Keyword/term-list scoring — no embeddings, no vector store."""

    def count(self) -> int:
        return index_count(self.project_id)

    def discover(
        self,
        top_n: int | None = None,
    ) -> list[dict]:
        """Select pages for the synthesis PDF using a NOISE-BASED deletion
        policy (keep-all, drop provably-noise pages).

        Every page is KEPT by default. A page is deleted only when it is
        classified as noise AND not rescued by the veto layer::

            keep  <=>  (noise is None)  OR  (veto AND noise not in {toc, cover, boilerplate[pure]})

        Deletion is driven by ``classify_noise`` (cover / toc / boilerplate /
        blank / closing) — NOT by a score. A cross-page boilerplate pass flags
        template/footer-only pages. The veto layer force-keeps any noise page
        whose text contains a high-value term reused from queries/query.txt
        (Title Anchors + Table Features), so genuinely evidence-bearing pages
        (e.g. a pure-text conclusion) are never dropped.

        Survivors are returned in the original report order. An optional MAX
        ceiling (``top_n`` > 0) is applied AFTER deletion as a safety net;
        ``None`` / ``-1`` means no ceiling.
        """
        pages = load_index(self.project_id)
        if not pages:
            return []

        if top_n is None:
            cap = None            # no ceiling by default
        elif top_n == -1:
            cap = None            # sentinel: no per-type cap (All)
        elif isinstance(top_n, int) and top_n > 0:
            cap = top_n           # explicit ceiling requested
        else:
            cap = None

        results: list[dict] = []
        for rt in REPORT_TYPES:
            candidates = [p for p in pages if p["report_type"] == rt]
            if not candidates:
                continue

            # ---- NOISE-BASED deletion (keep-all, drop provable noise) ----
            veto_re = get_veto_terms()
            for p in candidates:
                p["_noise"] = classify_noise(p)
            # Cross-page boilerplate pass (needs all candidate pages).
            _detect_boilerplate(candidates)
            # Veto + final keep decision.
            for p in candidates:
                noise = p.get("_noise")
                if noise is None:
                    keep = True
                else:
                    veto = bool(veto_re.search(p.get("text", "") or ""))
                    p["_veto"] = veto
                    pure_template = (
                        noise == "boilerplate"
                        and p.get("_boiler_unique", 999) < VETO_MIN_UNIQUE_CHARS
                    )
                    if noise in _VETO_IMMUNE_NOISE or pure_template:
                        keep = False          # navigation/decoration/pure-template: never rescued
                    else:
                        keep = veto           # any other noise kept ONLY if vetoed
                p["_keep"] = keep

            survivor_keys = {
                (p["source_path"], p["page_index"])
                for p in candidates if p.get("_keep")
            }
            if not survivor_keys:
                # Safety net: whole type classified as noise -> keep first N
                # pages by original order and warn.
                logger.warning(
                    f"[{rt}] noise deletion left 0 pages (entire type is noise); "
                    f"keeping first {DELETE_MIN_KEEP} by original order as a safeguard."
                )
                survivors = sorted(
                    candidates,
                    key=lambda p: (p["source_path"], p["page_index"]),
                )[:DELETE_MIN_KEEP]
                survivor_keys = {
                    (p["source_path"], p["page_index"]) for p in survivors
                }

            survivors = [
                p for p in candidates
                if (p["source_path"], p["page_index"]) in survivor_keys
            ]
            deleted = [
                p for p in candidates
                if (p["source_path"], p["page_index"]) not in survivor_keys
            ]

            # Tag survivors: veto-kept vs normal content.
            for p in survivors:
                p["selected_by"] = ["veto"] if p.get("_veto") else ["content"]

            # Order survivors by original report order.
            ordered = sorted(
                survivors,
                key=lambda p: (p["source_path"], p["page_index"]),
            )

            # Optional MAX ceiling (only when an explicit top_n > 0 was given).
            if cap is not None:
                ordered = ordered[:cap]

            noise_counts = Counter(
                p["_noise"] for p in deleted if p.get("_noise")
            )
            veto_kept = sum(1 for p in survivors if p.get("_veto"))
            cap_label = "no ceiling" if cap is None else cap
            logger.info(
                f"[{rt}] kept {len(ordered)} / {len(candidates)} "
                f"(veto-kept={veto_kept}, deleted_by_noise={dict(noise_counts)}, "
                f"ceiling={cap_label})"
            )
            results.extend(_to_items(ordered))

        return results
