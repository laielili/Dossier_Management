"""
Dossier_Management — Pipeline Configuration
"""

import json
import os
from pathlib import Path

# --- Project root ---
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# --- Search target paths (Dossier retrieval feature) -------------------
# Persisted to search_paths.txt at the project root (one absolute path per
# line, most-recent first). This is a convenience history the search UI renders
# as a re-loadable / deletable list of folders to look inside for dossiers. The
# path is intentionally NOT written to logs.
SEARCH_PATHS_FILE = PROJECT_ROOT / "search_paths.txt"

# Folder that receives the files a user selects in the retrieval flow,
# structured as retrieved/<project_name>/ (one subfolder per retrieval run).
# Created on demand when a retrieval starts.
RETRIEVED_DIR = PROJECT_ROOT / "retrieved"

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
REPORT_TYPES = ["CLINS", "FE", "CE", "INSTRUMENTAL"]


def get_search_paths() -> list[str]:
    """Return the full ordered list of saved search target paths (history)."""
    if not SEARCH_PATHS_FILE.exists():
        return []
    out: list[str] = []
    seen: set[str] = set()
    for line in SEARCH_PATHS_FILE.read_text(encoding="utf-8").splitlines():
        p = line.strip()
        if not p:
            continue
        p = str(Path(p).expanduser())
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def set_search_path(path: str) -> None:
    """Add / activate a search target path in the history list.

    De-duplicates and moves the path to the front of the list, then persists.
    Re-saving the same path only re-orders it — it never creates a duplicate.
    """
    p = str(Path(path).expanduser()).strip()
    if not p:
        return
    folders = [f for f in get_search_paths() if f != p]
    folders.insert(0, p)
    SEARCH_PATHS_FILE.write_text("\n".join(folders) + "\n", encoding="utf-8")


def delete_search_path(path: str) -> bool:
    """Remove one saved search target path from the history list."""
    p = str(Path(path).expanduser()).strip()
    folders = get_search_paths()
    if p not in folders:
        return False
    folders = [f for f in folders if f != p]
    SEARCH_PATHS_FILE.write_text(
        ("\n".join(folders) + "\n") if folders else "", encoding="utf-8"
    )
    return True


def project_data_dir(project_name: str) -> Path:
    """Per-project dossier working folder.

    The project folder is <PROJECT_ROOT>/<project_name>/, and classified files
    go into <project_name>/{CLINS,FE,CE}/ beneath it. The project name doubles
    as the pipeline ``project_id`` (index key + output PDF name), so this
    single mapping drives the whole per-project flow.
    """
    return PROJECT_ROOT / project_name

# Friendly labels for the synthesis PDF annotation block (user-defined mapping:
# CLINS = clinical signal, FE = sensory signal, CE = consumer evaluation signal).
REPORT_TYPE_LABELS = {
    "CLINS": "Clinical",
    "FE": "Sensory",
    "CE": "Consumer Evaluation",
    "INSTRUMENTAL": "Instrumental",
}

# --- PDF parsing ---
SCREENSHOT_DPI = 300          # High resolution for crisp screenshots (~4x default 72 DPI)

# --- Page-selection: NOISE-based deletion ---
# We KEEP every page by default and delete ONLY pages we can PROVE are noise
# (see src/retriever.classify_noise + veto). A "veto" layer force-keeps any
# noise page whose text contains a high-value term (reused from
# queries/query.txt Title Anchors + Table Features), so genuinely
# evidence-bearing pages are never dropped. Survivors are emitted in the
# original report order.
#
# Safety net: if deletion would leave a type with ZERO pages (whole dossier is
# boilerplate), keep at least this many pages and warn.
DELETE_MIN_KEEP = 3

#
# Noise categories (each is dropped when detected AND not vetoed):
#   cover       title / section-divider page (large title, little text, no
#               table/list, few figures) — always dropped (veto-immune)
#   toc         table-of-contents / agenda page — always dropped (veto-immune)
#   boilerplate page whose text is almost entirely lines repeated across many
#               other pages of the same type (template / footer-only page)
#   blank       almost no text and no figure/table/list
#   closing     "thank you" / "work in progress" / "questions?" style page
# Thresholds are deliberately conservative — the design errs toward KEEPING.
NOISE_CATEGORY_LABELS = {
    "cover": "Cover / section divider",
    "toc": "Table of contents",
    "boilerplate": "Boilerplate / template",
    "blank": "Blank / near-empty",
    "closing": "Closing (thank-you, etc.)",
}

BLANK_MAX_CHARS = 30          # text shorter than this AND no figure/table/list => blank
BLANK_MAX_FONT = 18.0         # near-blank requires SMALL font; a large-font short page
                             # is a title/divider (cover), not blank
COVER_MIN_FONT = 20.0         # title this large (or larger) ...
COVER_MAX_CHARS = 60          # ... and this much text at most => cover
COVER_MAX_FIGURES = 3         # covers may carry a logo; more figures => content
CLOSING_MAX_CHARS = 120
BOILERPLATE_MIN_PAGES = 10    # a line seen on >= this many pages of a type is "common"
BOILERPLATE_MIN_UNIQUE_CHARS = 40  # page with fewer unique chars => boilerplate
VETO_MIN_UNIQUE_CHARS = 10    # pure-template boilerplate (fewer unique chars) NOT veto-rescued

# Veto terms are reused from queries/*.txt (Title Anchors + Table Features).
# This seed is only a fallback if those files are missing.
VETO_SEED_TERMS = ["conclusion", "results", "p-value", "significance"]

# --- User-tunable overrides (persisted to disk) --
# The frontend writes pipeline parameters here (e.g. the PPTX output folder).
# The noise-deletion policy itself is code-defined and not user-tunable.
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


# --- Deck output path bookmarks (svg2ppt saved-destination history) ---
DECK_OUTPUT_PATHS_KEY = "deck_output_paths"
_DECK_OUTPUT_PATHS_CAP = 20


def get_deck_output_paths() -> list[str]:
    """Saved deck-output folder bookmarks, most-recent first."""
    return list(get_config_overrides().get(DECK_OUTPUT_PATHS_KEY, []) or [])


def add_deck_output_path(path: str) -> list[str]:
    """Bookmark a deck-output folder (dedup, most-recent first, capped)."""
    p = str(path).strip()
    if not p:
        return get_deck_output_paths()
    cur = [x for x in get_deck_output_paths() if x != p]
    cur.insert(0, p)
    cur = cur[:_DECK_OUTPUT_PATHS_CAP]
    set_config_overrides({DECK_OUTPUT_PATHS_KEY: cur})
    return cur


def delete_deck_output_path(path: str) -> bool:
    """Remove a single bookmark. Returns False if it wasn't present."""
    p = str(path).strip()
    cur = get_deck_output_paths()
    nxt = [x for x in cur if x != p]
    if len(nxt) == len(cur):
        return False
    set_config_overrides({DECK_OUTPUT_PATHS_KEY: nxt})
    return True


# Optional MAX ceiling applied AFTER deletion. In delete mode this is OFF by
# default (top_n=None -> no ceiling) so we never re-introduce rank-based data
# loss. Pass --top-n N (or API top_n=N) to cap a type at N pages as a safety net.
# TOP_N_PER_TYPE is only used when an explicit top_n is provided.
TOP_N_PER_TYPE = 12            # optional ceiling; used ONLY when an explicit top_n is given

# Backward-compatible alias (older imports referenced LEXICAL_TOP_N_PER_TYPE).
LEXICAL_TOP_N_PER_TYPE = TOP_N_PER_TYPE

# --- Veto-term lexicon (queries/query.txt) ---
# The unified queries/query.txt is a SECTIONED lexicon. Each section is introduced
# by a "# <Dimension>" header line:/n#   # Title Anchors   - section/heading signals
#   # Metric Keywords - outcome/measurement terms
#   # Table Features  - statistical markers
#   # Other           - project-specific vocabulary
# Any other "#" line is a comment. One term/phrase per line; multi-word entries
# are matched as phrases. The retriever reuses the Title Anchors + Table Features
# sections as the VETO terms that rescue noise pages (see src/retriever.py). The
# classifier (classify/*.txt) is a separate, intentional match and is untouched.
LEXICON_DIMENSIONS = ["title_anchors", "metric_keywords", "table_features", "other"]
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
    "INSTRUMENTAL": (
            "instrumental measurement corneometer tewameter primos skin hydration "
            "transepidermal water loss barrier function roughness elasticity firmness "
            "biophysical skin properties objective assessment before after treatment "
            "角质层水分 经皮水分流失 皮肤屏障功能 粗糙度 弹性 坚韧度 生物物理 "
            "仪器测量 客观测量 皮肤生物物理参数 角质计 经表皮水分流失仪 皮肤成像分析"
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
    "INSTRUMENTAL": (
            "Instrumental report. First page identifies instrumental measurement, "
            "biophysical assessment, or skin property analysis. Contains terms: "
            "corneometer, tewameter, primos, skin hydration, transepidermal water loss, "
            "TEWL, barrier function, roughness, elasticity, firmness, biophysical, "
            "instrumental, objective measurement, skin properties. Measures skin "
            "biophysical parameters under controlled conditions. "
            "角质层水分, 经皮水分流失, 皮肤屏障功能, 粗糙度, 弹性, 坚韧度, 生物物理, "
            "仪器测量, 客观测量, 皮肤生物物理参数, 角质计, 经表皮水分流失仪, 皮肤成像分析"
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
