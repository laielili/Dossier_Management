import sys, shutil
from pathlib import Path

ROOT = Path(".")
TMP = ROOT / "_tmp_cls"
TMP.mkdir(exist_ok=True)
# clean any prior run
for p in list(TMP.glob("*.pdf")):
    p.unlink()

SRC = ROOT / "_trash/retrieved_verify_leftover/TestProj/clinical_report.pdf"
shutil.copy2(SRC, TMP / "clinical_report.pdf")

sys.path.insert(0, "src")
import src.pdf_parser as pp
import src.classifier as cl

f = TMP / "clinical_report.pdf"
print("pdf_has_text(clinical_report.pdf) =", pp.pdf_has_text(f))
print("first-page text (first 300 chars):")
print("  ", pp.extract_first_page_text(f)[:300].replace("\n", " \\n "))

cls = cl.Classifier(base_dir=TMP)
out = cls.classify_inbox(auto_archive=False)
for r in out["results"]:
    print(f"\nFILE {r['filename']}:")
    print("  report_type =", r["report_type"])
    print("  confidence  =", r["confidence"])
    print("  low_conf    =", r["low_confidence"])
    print("  scores      =", r["scores"])

# cleanup
shutil.rmtree(TMP, ignore_errors=True)
