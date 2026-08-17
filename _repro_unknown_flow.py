import sys, shutil, json
from pathlib import Path

ROOT = Path(".")
TMP = ROOT / "_tmp_unk"
OUT = ROOT / "_tmp_unk_out"
if TMP.exists():
    shutil.rmtree(TMP)
if OUT.exists():
    shutil.rmtree(OUT)
TMP.mkdir()

import fitz
doc = fitz.open()
# Page 1: generic content (no CLINS/FE/CE keywords) -> should be UNKNOWN
p = doc.new_page()
for i, ln in enumerate([
    "Internal Meeting Notes",
    "This document records discussion points from the weekly sync.",
    "Action items were reviewed and owners assigned for follow-up.",
    "No clinical or sensory claims are made in this section.",
]):
    p.insert_text((50, 70 + i*16), ln, fontsize=11, fontname="helv")
# Page 2: a TOC page -> noise, should be dropped by denoise
p = doc.new_page()
for i, ln in enumerate([
    "Table of Contents",
    "1. Introduction",
    "2. Background",
    "3. Discussion",
    "4. Next Steps",
]):
    p.insert_text((50, 70 + i*16), ln, fontsize=11, fontname="helv")
# Page 3-4: more generic content
for k in range(2):
    p = doc.new_page()
    for i, ln in enumerate([
        "Follow-up summary",
        "The team agreed to revisit the timeline next quarter.",
        "Budget notes were deferred to the finance review.",
        "These pages contain no dossier-type keywords.",
    ]):
        p.insert_text((50, 70 + i*16), ln, fontsize=11, fontname="helv")
doc.save(str(TMP / "meeting_notes.pdf"), garbage=3, deflate=True)
doc.close()

sys.path.insert(0, "src")
from src.orchestrator import run_project_pipeline

try:
    res = run_project_pipeline("unktest", folder=TMP, condense_dir=OUT)
    print("=== PIPELINE RESULT ===")
    print(json.dumps(res, indent=2, default=str))
except Exception as e:
    print("PIPELINE RAISED:", type(e).__name__, e)
    raise

# Verify the denoised UNKNOWN PDF exists and has pages.
unk_out = OUT / "UNKNOWN" / "meeting_notes.pdf"
if unk_out.exists():
    d = fitz.open(str(unk_out))
    print(f"\nDENOISED UNKNOWN PDF: {unk_out}")
    print(f"  pages = {d.page_count} (expected 3: TOC dropped)")
    d.close()
else:
    print("ERROR: UNKNOWN output PDF not written!")

# Cleanup
shutil.rmtree(TMP, ignore_errors=True)
shutil.rmtree(OUT, ignore_errors=True)
idx = ROOT / "index_projects" / "unktest.json"
if idx.exists():
    idx.unlink()
print("\nCLEANUP DONE")
