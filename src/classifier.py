"""
Document-type classification — lexical (keyword) scoring against editable
text profiles. Fully offline, no embedding model.

Each report type (CLINS/FE/CE) has a profile text in classify/{TYPE}.txt
describing what its first page looks like. A document's first-page text is
scored (term-list match) against the three profiles. The highest score wins.

Flow (per product decision):
  - High-confidence match  -> auto-archive (move) into data/{type}/
  - Low-confidence / UNKNOWN -> leave in inbox, force manual review
"""

import shutil
from pathlib import Path
from typing import Optional

from .config import (
    CLASSIFY_CONFIDENCE_MARGIN,
    CLASSIFY_MIN_SCORE,
    CLASSIFY_PROFILE_DIR,
    DATA_DIR,
    DEFAULT_CLASSIFY_PROFILES,
    REPORT_TYPES,
)
from .logger import get_logger
from .pdf_parser import extract_first_page_text
from .converter import (
    convert_folder,
    ConverterUnavailable,
    CONVERTIBLE_EXTS,
    _is_junk_filename,
)
from .retriever import _token_weights, _lexical_score

logger = get_logger("classifier")

UNKNOWN = "UNKNOWN"


# ---------------------------------------------------------------------------
# Profile file helpers (read / write classify/{CLINS,FE,CE}.txt)
# ---------------------------------------------------------------------------

def load_profiles_from_files() -> dict[str, str]:
    """Read per-type classification profiles from classify/{CLINS,FE,CE}.txt.

    Falls back to DEFAULT_CLASSIFY_PROFILES for any missing file and writes
    the default back so the user can edit it.
    """
    profiles: dict[str, str] = {}
    for rt in REPORT_TYPES:
        pf = CLASSIFY_PROFILE_DIR / f"{rt}.txt"
        if pf.exists():
            profiles[rt] = pf.read_text(encoding="utf-8").strip()
        else:
            text = DEFAULT_CLASSIFY_PROFILES.get(rt, "")
            pf.write_text(text.strip() + "\n", encoding="utf-8")
            profiles[rt] = text
            logger.info(f"No classify profile for {rt}, wrote default")
    return profiles


def save_profiles_to_files(profiles: dict[str, str]) -> None:
    """Write per-type profiles back to classify/{CLINS,FE,CE}.txt."""
    CLASSIFY_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    for rt in REPORT_TYPES:
        text = profiles.get(rt, DEFAULT_CLASSIFY_PROFILES.get(rt, ""))
        pf = CLASSIFY_PROFILE_DIR / f"{rt}.txt"
        pf.write_text(text.strip() + "\n", encoding="utf-8")
        logger.info(f"Saved classify profile: {pf}")


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

class Classifier:
    """Lexical classifier: scores first-page text against type profiles.

    ``base_dir`` is the per-project dossier folder (PROJECT_ROOT / project_name).
    The inbox = ``base_dir`` (top-level dossiers); classified files are moved
    into ``base_dir/{CLINS,FE,CE}/``. Falls back to the global DATA_DIR when
    ``base_dir`` is not supplied.
    """

    def __init__(self, base_dir: Path | None = None):
        self._profiles = load_profiles_from_files()
        self.base_dir = Path(base_dir) if base_dir is not None else DATA_DIR

    # ------------------------------------------------------------------
    # Core classification
    # ------------------------------------------------------------------

    def classify_text(self, text: str) -> dict:
        """Classify a single text snippet against the type profiles.

        Returns:
            {
                "report_type": "CLINS" | "FE" | "CE" | "UNKNOWN",
                "confidence": float,           # top-1 lexical score
                "scores": {type: score, ...},  # all three scores
                "low_confidence": bool,        # needs manual review
            }
        """
        if not text.strip():
            return {
                "report_type": UNKNOWN,
                "confidence": 0.0,
                "scores": {rt: 0.0 for rt in self._profiles},
                "low_confidence": True,
            }

        scores: dict[str, float] = {}
        for rt, profile in self._profiles.items():
            weights = _token_weights(profile)
            # _lexical_score returns (score, matched_terms); take the score.
            scores[rt] = round(_lexical_score(text, profile, weights)[0], 2)

        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        top1_type, top1 = ranked[0]
        top2 = ranked[1][1] if len(ranked) > 1 else 0.0
        margin = top1 - top2

        low = (
            top1 < CLASSIFY_MIN_SCORE
            or margin < CLASSIFY_CONFIDENCE_MARGIN
        )
        report_type = UNKNOWN if top1 < CLASSIFY_MIN_SCORE else top1_type
        if report_type == UNKNOWN:
            low = True

        return {
            "report_type": report_type,
            "confidence": round(top1, 2),
            "scores": scores,
            "low_confidence": low,
        }

    # ------------------------------------------------------------------
    # Explicit path list (retrieval flow - no source copies, no archiving)
    # ------------------------------------------------------------------

    def classify_paths(self, paths):
        # Classify an explicit list of PDF files without touching the
        # filesystem layout (no moves, no auto-archive).
        results = []
        unprocessed = []

        # Normalize any pptx/docx in each parent folder so PDF siblings exist.
        seen_parents = set()
        for p in paths:
            parent = p.parent
            if parent in seen_parents:
                continue
            seen_parents.add(parent)
            try:
                convert_folder(parent)
            except ConverterUnavailable as e:
                logger.warning(
                    "Office conversion unavailable (" + str(e) + "); non-PDF sources "
                    "in " + str(parent) + " cannot be normalized."
                )

        for raw in paths:
            p = Path(raw)
            # Reject anything not on disk first.
            if not p.exists() or not p.is_file():
                if p.suffix.lower() in CONVERTIBLE_EXTS:
                    unprocessed.append({
                        "filename": p.name,
                        "reason": "failed Office conversion (pptx/docx -> PDF)",
                    })
                elif _is_junk_filename(p.name):
                    unprocessed.append({
                        "filename": p.name,
                        "reason": "Office lock / OS cruft file",
                    })
                else:
                    unprocessed.append({
                        "filename": p.name,
                        "reason": "missing file (no longer on disk)",
                    })
                continue

            pdf = p.with_suffix(".pdf") if p.suffix.lower() != ".pdf" else p
            if pdf != p and not pdf.exists():
                if p.suffix.lower() in CONVERTIBLE_EXTS:
                    unprocessed.append({
                        "filename": p.name,
                        "reason": "failed Office conversion (pptx/docx -> PDF)",
                    })
                elif _is_junk_filename(p.name):
                    unprocessed.append({
                        "filename": p.name,
                        "reason": "Office lock / OS cruft file",
                    })
                else:
                    unprocessed.append({
                        "filename": p.name,
                        "reason": "unsupported file type (not PDF/pptx/docx)",
                    })
                continue

            score_target = pdf if pdf != p and pdf.exists() else p
            text = extract_first_page_text(score_target)
            res = self.classify_text(text)
            res["filename"] = score_target.name
            res["path"] = str(score_target)
            res["archived"] = False
            res["current_path"] = str(score_target)
            results.append(res)
            logger.info(
                score_target.name + ": type=" + res["report_type"] + " "
                "conf=" + str(res["confidence"]) + " low=" + str(res["low_confidence"])
            )

        return {"results": results, "unprocessed": unprocessed}

    # ------------------------------------------------------------------
    # Inbox scan + auto-archive
    # ------------------------------------------------------------------

    def classify_inbox(

        self,
        inbox_dir: Path | None = None,
        auto_archive: bool = True,
    ) -> list[dict]:
        """Classify every PDF in the project's dossier folder.

        ``inbox_dir`` defaults to ``self.base_dir`` (the project folder's
        top-level). High-confidence matches are auto-archived (moved) into
        their type subfolder. Low-confidence / UNKNOWN files stay in the
        folder flagged for manual review.

        Returns a list of result dicts (one per PDF).
        """
        inbox_dir = Path(inbox_dir or self.base_dir)
        results: list[dict] = []
        if not inbox_dir.exists():
            return {"results": results, "unprocessed": []}

        # Normalize pptx/docx -> PDF so downstream only ever sees PDFs.
        try:
            convert_folder(inbox_dir)
        except ConverterUnavailable as e:
            logger.warning(
                f"Office conversion unavailable ({e}); only existing PDFs "
                f"will be classified."
            )

        # Any file still NOT a PDF after the conversion attempt is a silent
        # drop risk: a pptx/docx that failed COM conversion, an Office lock
        # file that slipped through, or some other unsupported leftover.
        # Surface it so "uploaded N" can never silently diverge from
        # "classified N" without an explanation the user can see.
        unprocessed = []
        for p in sorted(inbox_dir.iterdir()):
            if not p.is_file() or p.suffix.lower() == ".pdf":
                continue
            if p.suffix.lower() in CONVERTIBLE_EXTS:
                # A convertible source that already produced a sibling PDF
                # was converted SUCCESSFULLY — the converter normally
                # deletes the consumed source, but if that deletion was
                # skipped/failed the source can still linger here. Do NOT
                # report it as a failure just because it is still present;
                # only a convertible with NO sibling PDF actually failed.
                if p.with_suffix(".pdf").exists():
                    continue
                reason = "failed Office conversion (pptx/docx -> PDF)"
            elif _is_junk_filename(p.name):
                reason = "Office lock / OS cruft file"
            else:
                reason = "unsupported file type (not PDF/pptx/docx)"
            unprocessed.append({"filename": p.name, "reason": reason})

        pdfs = sorted(inbox_dir.glob("*.pdf"))
        if not pdfs:
            logger.info(f"Inbox empty: {inbox_dir}")
            return {"results": results, "unprocessed": unprocessed}

        for pdf in pdfs:
            text = extract_first_page_text(pdf)
            res = self.classify_text(text)
            res["filename"] = pdf.name
            res["path"] = str(pdf)

            if (
                auto_archive
                and not res["low_confidence"]
                and res["report_type"] in REPORT_TYPES
            ):
                dest = self.archive(pdf, res["report_type"])
                res["archived"] = True
                res["current_path"] = str(dest)
            else:
                res["archived"] = False
                res["current_path"] = str(pdf)

            results.append(res)
            logger.info(
                f"{pdf.name}: type={res['report_type']} "
                f"conf={res['confidence']} "
                f"low={res['low_confidence']} "
                f"archived={res['archived']}"
            )
        return {"results": results, "unprocessed": unprocessed}

    # ------------------------------------------------------------------
    # File movement
    # ------------------------------------------------------------------

    def archive(self, src_path: Path, report_type: str) -> Path:
        """Move a single file into <base_dir>/{report_type}/ (avoids name clashes)."""
        src_path = Path(src_path)
        dest_dir = self.base_dir / report_type
        dest_dir.mkdir(parents=True, exist_ok=True)

        dest = dest_dir / src_path.name
        if dest.exists() and dest.resolve() != src_path.resolve():
            stem = src_path.stem
            suffix = 1
            while (dest_dir / f"{stem}_{suffix}{src_path.suffix}").exists():
                suffix += 1
            dest = dest_dir / f"{stem}_{suffix}{src_path.suffix}"

        shutil.move(str(src_path), str(dest))
        logger.info(f"Archived {src_path.name} -> {report_type}/")
        return dest

    def apply_decisions(self, decisions: list[dict]) -> list[str]:
        """Apply user decisions: move each file to its chosen type folder.

        ``decisions`` is a list of {"filename": str, "report_type": str}.
        A file may currently be in the inbox or in a (wrong) subfolder;
        it is moved to data/{report_type}/{filename}. Already-correct
        files are skipped.
        """
        moved: list[str] = []
        for d in decisions:
            rt = str(d.get("report_type", "")).upper()
            fname = d.get("filename", "")
            if rt not in REPORT_TYPES or not fname:
                continue

            src = self._find_file(fname)
            if src is None:
                logger.warning(f"File not found for decision: {fname}")
                continue

            dest_dir = self.base_dir / rt
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / fname
            if src.resolve() == dest.resolve():
                continue  # already in the right place

            if dest.exists() and dest.resolve() != src.resolve():
                stem = src.stem
                suffix = 1
                while (dest_dir / f"{stem}_{suffix}{src.suffix}").exists():
                    suffix += 1
                dest = dest_dir / f"{stem}_{suffix}{src.suffix}"

            shutil.move(str(src), str(dest))
            moved.append(fname)
            logger.info(f"Moved {fname} -> {rt}/")
        return moved

    def _find_file(self, filename: str) -> Path | None:
        """Locate a file by name across the project folder + its type subfolders."""
        candidates = [self.base_dir / filename]
        for rt in REPORT_TYPES:
            candidates.append(self.base_dir / rt / filename)
        for c in candidates:
            if c.exists():
                return c
        return None
