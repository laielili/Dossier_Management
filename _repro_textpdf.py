import sys, shutil
from pathlib import Path

ROOT = Path(".")
TMP = ROOT / "_tmp_cls"
TMP.mkdir(exist_ok=True)
for p in list(TMP.glob("*.pdf")):
    p.unlink()

import fitz
# Build a REAL text-layer PDF whose first page is clearly CLINS.
doc = fitz.open()
page = doc.new_page()
lines = [
    "Clinical Investigation Report",
    "A controlled clinical study assessing the efficacy and tolerance of the",
    "test product under dermatological investigation. The investigator recorded",
    "clinical outcomes at baseline and after treatment using instrumental",
    "measurement and grader assessment. No serious adverse event was reported;",
    "safety and clinical endpoints were met with statistical significance.",
]
y = 72
for ln in lines:
    page.insert_text((50, y), ln, fontsize=11, fontname="helv")
    y += 16
doc.save(str(TMP / "demo_clins.pdf"), garbage=3, deflate=True)
doc.close()

sys.path.insert(0, "src")
import src.pdf_parser as pp
import src.classifier as cl

f = TMP / "demo_clins.pdf"
print("pdf_has_text(demo_clins.pdf) =", pp.pdf_has_text(f))
cls = cl.Classifier(base_dir=TMP)
out = cls.classify_inbox(auto_archive=False)
for r in out["results"]:
    print(f"report_type={r['report_type']} conf={r['confidence']} "
          f"low={r['low_confidence']} scores={r['scores']}")

shutil.rmtree(TMP, ignore_errors=True)
