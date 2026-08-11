"""
Dossier_Management — Pipeline Configuration
"""

import json
import os
from pathlib import Path

# --- Project root ---
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# --- User-configured watch folder (base dir for projects) ---------------
# Persisted to listen_folder.txt at the project root. When set, per-project
# folders are resolved as <listen_folder>/<project_name>/ instead of
# PROJECT_ROOT/<project_name>/. Lets the user point the app at any folder
# (e.g. an OneDrive-synced dossier library) without moving files. The path
# is intentionally NOT written to logs.
LISTEN_FOLDER_FILE = PROJECT_ROOT / "listen_folder.txt"

# --- Data & output directories ---
DATA_DIR = PROJECT_ROOT / "data"
INDEX_DIR = PROJECT_ROOT / "index_projects"   # lightweight page-text index (no vectors)
SCREENSHOTS_DIR = PROJECT_ROOT / "screenshots"
OUTPUT_DIR = PROJECT_ROOT / "output"
LOG_DIR = PROJECT_ROOT / "logs"
QUERIES_DIR = PROJECT_ROOT / "queries"
CLASSIFY_DIR = DATA_DIR / "inbox"
CLASSIFY_PROFILE_DIR = PROJECT_ROOT / "classify"

# --- Report type subdirectories ---
REPORT_TYPES = ["CLINS", "FE", "CE"]


def _read_listen_folders() -> list[str]:
    """Read all saved listen folders as an ordered, de-duplicated list.

    Stored one absolute path per line in listen_folder.txt. The first entry
    is the *active* folder (used as the base dir for projects). The full
    ordered list is the user-visible history shown in the folder picker so
    the user can re-pick or delete past choices.
    """
    if not LISTEN_FOLDER_FILE.exists():
        return []
    out: list[str] = []
    seen: set[str] = set()
    for line in LISTEN_FOLDER_FILE.read_text(encoding="utf-8").splitlines():
        p = line.strip()
        if not p:
            continue
        p = str(Path(p).expanduser())
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def get_listen_folders() -> list[str]:
    """Return the full ordered list of saved listen folders (history)."""
    return _read_listen_folders()


def get_listen_folder() -> str | None:
    """Return the active (first) saved listen folder, or None if unset.

    The active folder is the base directory under which per-project folders
    (<project_name>/) live.
    """
    folders = _read_listen_folders()
    return folders[0] if folders else None


def set_listen_folder(path: str) -> None:
    """Add / activate a listen folder in the history list.

    De-duplicates and moves the path to the front of the list, then persists.
    The front entry becomes the active folder used for project resolution.
    Re-saving the same path only re-orders it — it never creates a duplicate.
    """
    p = str(Path(path).expanduser()).strip()
    if not p:
        return
    folders = [f for f in _read_listen_folders() if f != p]
    folders.insert(0, p)
    LISTEN_FOLDER_FILE.write_text("\n".join(folders) + "\n", encoding="utf-8")


def delete_listen_folder(path: str) -> bool:
    """Remove one saved folder from the history list. Returns True if removed."""
    p = str(Path(path).expanduser()).strip()
    folders = _read_listen_folders()
    if p not in folders:
        return False
    folders = [f for f in folders if f != p]
    LISTEN_FOLDER_FILE.write_text(
        ("\n".join(folders) + "\n") if folders else "", encoding="utf-8"
    )
    return True


def project_data_dir(project_name: str) -> Path:
    """Per-project dossier working folder.

    The base is the user-configured listen folder (listen_folder.txt) when
    set, otherwise the app root (PROJECT_ROOT). The project folder is
    <base>/<project_name>/, and classified files go into
    <project_name>/{CLINS,FE,CE}/ beneath it. The project name doubles as
    the pipeline ``project_id`` (index key + output PDF name), so this single
    mapping drives the whole per-project flow.
    """
    base = get_listen_folder()
    if base:
        return Path(base) / project_name
    return PROJECT_ROOT / project_name

# Friendly labels for the synthesis PDF annotation block (user-defined mapping:
# CLINS = clinical signal, FE = sensory signal, CE = consumer evaluation signal).
REPORT_TYPE_LABELS = {
    "CLINS": "Clinical",
    "FE": "Sensory",
    "CE": "Consumer Evaluation",
}

# --- PDF parsing ---
SCREENSHOT_DPI = 300          # High resolution for crisp screenshots (~4x default 72 DPI)

# --- Page-selection tuning: DELETE mode (replaces the old top-N "select") ----
# We no longer keep only the top-N *ranked* pages per type (that silently dropped
# mid-rank but still-valid pages). Instead we KEEP everything and DELETE pages
# whose information value falls below a single GLOBAL absolute score floor.
#
# Per-page value = max(_kw_score, _struct_score)   # union semantics:
#   a page survives if EITHER track signals value, so it is deleted only when
#   BOTH tracks are below the floor. A page with a relevant table but off-topic
#   text (or vice-versa) is therefore never falsely removed (avoids 误伤).
#     deleted  <=>  (_kw_score < DELETE_SCORE_FLOOR) AND (_struct_score < DELETE_SCORE_FLOOR)
# TOC / cover pages are zeroed on both tracks and are always deleted.
#
# DELETE_SCORE_FLOOR is an ABSOLUTE global threshold (NOT a percentile): the same
# value applies to every report type. Calibrated on the project's indexed
# dossiers (deletion % at various floors):
#   T=1.5 -> ~4-12%   T=2.5 -> ~9-12%   T=3.0 -> ~9-12%   T=4.0 -> ~14-31%
# The user's ~30% target corresponds to T~4, which also drops low-structure pages
# (single figure/table). Default is conservative: removes only clearly-low-info
# pages. Raise it if your real dossiers contain more boilerplate. Tune freely.
# Legacy: this used to be the single global score floor that gated page
# DELETION. Deletion is now NOISE-based (see src/retriever.classify_noise +
# veto). The constant is kept only so older callers / the /config/params
# fallback do not break; it no longer affects which pages are dropped.
DELETE_SCORE_FLOOR = 3.0
# Safety net: if deletion would leave a type with ZERO pages (whole dossier is
# boilerplate), keep at least this many highest-value pages and warn.
DELETE_MIN_KEEP = 3


# --- Page-selection: NOISE-based deletion (replaces score-floor deletion) ---
# We KEEP every page by default and delete ONLY pages we can PROVE are noise.
# A "veto" layer force-keeps any page whose text contains a high-value term
# (reused from queries/*.txt Title Anchors + Table Features), so genuinely
# evidence-bearing pages are never dropped. See src/retriever.py.
#
# Noise categories (each is dropped when detected AND not vetoed):
#   blank       almost no text and no figure/table/list
#   toc         table-of-contents / agenda page (never veto-rescued)
#   boilerplate page whose text is almost entirely lines repeated across many
#               other pages of the same type (template / footer-only page)
#   cover       title page (few text blocks, one large title, no table/list)
#   sectional   section divider (1-2 blocks, very short, large font)
#   closing     "thank you" / "work in progress" / "questions?" style page
#   decorative  a near-full-page image with no caption/text (OFF by default;
#               risky because it can also catch chart screenshots)
# Thresholds are deliberately conservative — the design errs toward KEEPING.
NOISE_CATEGORY_LABELS = {
    "blank": "Blank / near-empty",
    "toc": "Table of contents",
    "boilerplate": "Boilerplate / template",
    "cover": "Cover / title page",
    "sectional": "Section divider",
    "closing": "Closing (thank-you, etc.)",
    "decorative": "Decorative image",
}

BLANK_MAX_CHARS = 30          # text shorter than this AND no figure/table/list => blank
BLANK_MAX_FONT = 18.0          # near-blank requires SMALL font; a large-font short page
                              # is a title/divider (cover/sectional), not blank
COVER_MAX_BLOCKS = 3          # text-block count at/under this ...
COVER_MIN_FONT = 18.0         # ... with a title this large ...
COVER_MAX_CHARS = 200         # ... and this much text at most => cover
COVER_MAX_FIGURES = 3         # covers may carry a logo; more figures => content
SECTION_MAX_BLOCKS = 2
SECTION_MIN_FONT = 18.0
SECTION_MAX_CHARS = 60
CLOSING_MAX_CHARS = 120
BOILERPLATE_MIN_PAGES = 10    # a line seen on >= this many pages of a type is "common"
BOILERPLATE_MIN_UNIQUE_CHARS = 40  # page with fewer unique chars => boilerplate
VETO_MIN_UNIQUE_CHARS = 10    # pure-template boilerplate (fewer unique chars) NOT veto-rescued

# Decorative-image dropping is opt-in (can catch real chart screenshots).
DROP_DECORATIVE_IMAGE = False
DECORATIVE_MAX_CHARS = 10
DECORATIVE_IMG_RATIO = 0.85

# Veto terms are reused from queries/*.txt (Title Anchors + Table Features).
# This seed is only a fallback if those files are missing.
VETO_SEED_TERMS = ["conclusion", "results", "p-value", "significance"]

# --- User-tunable overrides (persisted to disk, editable from the frontend) --
# The frontend exposes "Deletion Floor" so the user can retune page deletion
# without editing code. The value is stored in config_overrides.json and read
# at run time by the retriever; DELETE_SCORE_FLOOR above remains the hard-coded
# default / fallback when no override is set (or it is cleared).
CONFIG_OVERRIDES_PATH = PROJECT_ROOT / "config_overrides.json"


def get_config_overrides() -> dict:
    """Load the user-editable config overrides, or {} if none saved yet."""
    if not CONFIG_OVERRIDES_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_OVERRIDES_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def set_config_overrides(overrides: dict) -> dict:
    """Persist the given override dict (merged onto the existing one).

    Explicit ``None`` values are dropped so the caller can "reset to default"
    by saving ``None`` for a key.
    """
    cur = get_config_overrides()
    cur.update(overrides)
    cur = {k: v for k, v in cur.items() if v is not None}
    CONFIG_OVERRIDES_PATH.write_text(
        json.dumps(cur, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return cur


def default_pptx_output_dir() -> str:
    """System Downloads folder — the default landing place for generated PPTX.

    Windows: %USERPROFILE%\\Downloads. Other OSes: ~/Downloads. Falls back to
    the home directory if no Downloads folder exists.
    """
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    downloads = home / "Downloads"
    return str(downloads if downloads.exists() else home)


def get_pptx_output_dir() -> str:
    """Effective HTML→PPTX output folder: user override, else Downloads."""
    ov = get_config_overrides().get("pptx_output_dir")
    if ov:
        return str(Path(str(ov)).expanduser())
    return default_pptx_output_dir()


def set_pptx_output_dir(path: str) -> str:
    """Persist the HTML→PPTX output folder override. Returns the stored value.

    The path is NOT created here — the API validates/creates it so the user
    gets an explicit error instead of a silently-made directory.
    """
    p = str(Path(str(path)).expanduser()).strip()
    if not p:
        raise ValueError("path is required")
    set_config_overrides({"pptx_output_dir": p})
    return p


def get_delete_floor() -> float:
    """Effective deletion floor: user override if set, else DELETE_SCORE_FLOOR."""
    ov = get_config_overrides().get("delete_floor")
    try:
        if ov is not None:
            return float(ov)
    except (TypeError, ValueError):
        pass
    return float(DELETE_SCORE_FLOOR)


def set_delete_floor(value: float) -> float:
    """Persist the deletion floor override. Returns the stored value.

    NOTE: delete_floor no longer gates deletion (deletion is noise-based). The
    setter is kept only for backward compatibility with older frontends.
    """
    v = float(value)
    set_config_overrides({"delete_floor": v})
    return v


def get_drop_decorative_image() -> bool:
    """Effective decorative-image drop flag: user override if set, else the
    hard-coded DROP_DECORATIVE_IMAGE default."""
    ov = get_config_overrides().get("drop_decorative_image")
    if ov is not None:
        return bool(ov)
    return bool(DROP_DECORATIVE_IMAGE)


def set_drop_decorative_image(value: bool) -> bool:
    """Persist the decorative-image drop override. Returns the stored value."""
    v = bool(value)
    set_config_overrides({"drop_decorative_image": v})
    return v

# Optional MAX ceiling applied AFTER deletion. In delete mode this is OFF by
# default (top_n=None -> no ceiling) so we never re-introduce rank-based data
# loss. Pass --top-n N (or API top_n=N) to cap a type at N pages as a safety net.
# TOP_N_PER_TYPE is only used when an explicit top_n is provided.
TOP_N_PER_TYPE = 12            # optional ceiling; used ONLY when an explicit top_n is given

# Backward-compatible alias (older imports referenced LEXICAL_TOP_N_PER_TYPE).
LEXICAL_TOP_N_PER_TYPE = TOP_N_PER_TYPE

# --- Page-selection TF-IDF lexicon (4-dimension weighted) ---------------
# queries/{CLINS,FE,CE}.txt are now SECTIONED lexicons. Each file has up to
# four sections introduced by a "# <Dimension>" header line:
#   # Title Anchors   — section/heading signals (high weight; positional boost)
#   # Metric Keywords — outcome/measurement terms (strong weight)
#   # Table Features  — statistical markers (synergy with detected tables)
#   # Other           — project-specific vocabulary (relevance booster / anti-miss)
# Any other "#" line is a comment. One term/phrase per line; multi-word entries
# are matched as phrases. The retriever computes a BM25-style IDF across the
# current project's pages (per report type) and scores each page as
#   score = Σ_t TF(t,page) · IDF(t) · W(dim(t)) · Pos(t)
# See src/retriever.py. The classifier (classify/*.txt) is intentionally
# untouched — this only affects page selection.
LEXICON_DIMENSIONS = ["title_anchors", "metric_keywords", "table_features", "other"]
DIM_WEIGHTS = {
    "title_anchors": 3.0,    # high-weight anchors (your "高权重锚点词")
    "metric_keywords": 2.5,  # strong indicator terms
    "table_features": 2.0,   # statistical markers
    "other": 0.5,            # project-specific vocabulary (relevance booster)
}
TF_SUBLINEAR = True          # use 1 + log(tf) instead of raw term frequency
TITLE_ANCHOR_TOP_REGION = 0.25   # fraction of page text (from top) = "heading zone"
TITLE_ANCHOR_BOOST = 2.0     # multiplier when a Title Anchor hits the heading zone
TABLE_FEATURE_SYNERGY = 1.5  # multiplier when a Table Feature co-occurs with a table
TOC_HEADERS = ("table of contents", "contents", "sommaire", "目录")

# --- Default per-type queries (fallback if queries/*.txt missing) ---
DEFAULT_QUERIES: dict[str, str] = {
    "CLINS": (
        "clinical study trial investigation efficacy safety dermatological "
        "tolerance adverse event endpoint instrumental measurement before after "
        "grader assessment clinical outcome claim substantiation"
    ),
    "FE": (
        "sensory evaluation panel analysis texture fragrance odor appearance "
        "feel sensory attributes descriptive analysis hedonic liking touch "
        "color sensory profiling results technical summary"
    ),
    "CE": (
        "consumer evaluation clinical study efficacy safety dermatological "
        "tolerance satisfaction self-assessment instrumental measurement "
        "statistical analysis primary endpoint results conclusion"
    ),
}

# --- Document-type classification (auto-sort uploaded PDFs) -----------
# A document's first-page text is scored (keyword/term-list) against each
# type's profile in classify/{TYPE}.txt. Highest score wins. Low confidence
# forces manual review. No embedding model is used.
CLASSIFY_MIN_SCORE = 1          # min lexical score for a type to be a candidate
CLASSIFY_CONFIDENCE_MARGIN = 1  # top1 - top2 gap below this => low confidence

DEFAULT_CLASSIFY_PROFILES: dict[str, str] = {
    "CLINS": (
        "Clinical report. First page identifies a clinical study, clinical trial, "
        "clinical evaluation, or clinical investigation. Contains terms: clinical, "
        "investigator, dermatological, efficacy, tolerance, safety, before/after, "
        "grader assessment, instrumental clinical measurement, endpoints, "
        "adverse event. Measured clinical outcomes under controlled conditions."
    ),
    "FE": (
        "Sensory report. First page identifies sensory evaluation, sensory analysis, "
        "sensory panel, or sensory assessment. Contains terms: sensory, panel, "
        "texture, fragrance, odor, appearance, feel, sensory attributes, "
        "descriptive analysis, hedonic, liking, touch, color. Product assessed "
        "through human sensory perception."
    ),
    "CE": (
        "Consumer Evaluation report. First page identifies a consumer test, "
        "consumer evaluation, consumer study, or consumer panel. Contains terms: "
        "consumer, evaluation, self-assessment, usage test, home-use test, "
        "questionnaire, satisfaction, perception, consumer feedback, claim "
        "substantiation, panelist. Measures consumer opinion and behavior at scale."
    ),
}

# --- PDF output ---
# A4 is used directly in pdf_generator.py via reportlab.lib.pagesizes

# --- Target-formula banner (Metadata Injection) -----------------------
# Baked into the cover of the synthesis-input PDF so the downstream
# multimodal LLM is anchored on the final target formula and treats
# data about other formulas as development reference only. {formula}
# is replaced by the user-supplied target formula string.
TARGET_FORMULA_BANNER_TEMPLATE = (
    "The final target formula for this Synthesis is: {formula}. "
    "Any data in the reports concerning other formulas is provided "
    "for development-reference purposes only."
)

# --- Logging ---
LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

# --- Ensure directories exist ---
for d in [
    DATA_DIR,
    INDEX_DIR,
    SCREENSHOTS_DIR,
    OUTPUT_DIR,
    LOG_DIR,
    QUERIES_DIR,
    CLASSIFY_DIR,
    CLASSIFY_PROFILE_DIR,
]:
    d.mkdir(parents=True, exist_ok=True)

for rt in REPORT_TYPES:
    (DATA_DIR / rt).mkdir(parents=True, exist_ok=True)
