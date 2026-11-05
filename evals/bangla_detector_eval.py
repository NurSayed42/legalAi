"""
evals/bangla_detector_eval.py
=============================
নষ্ট বাংলা detector এর precision/recall (docs/DECISIONS.md D-117)। Repo থেকে যা পড়ে:
data_quality/corrupted_bangla_handcheck.csv, downloaded_pdfs/, text_v2/, bangladesh_laws.json।
লেখে শুধু --out; PDF বা text_v2 ছোঁয় না। OCR চলে শুধু নিজের বানানো PDF এ।

    python evals/bangla_detector_eval.py --out data_quality/bangla_detector_eval.json

যা মাপে, আর কেন:
  sample     হাতে দেখা নমুনায় recall। নমুনায় একটাও পরিষ্কার file পাওয়া যায়নি, তাই precision এখান থেকে
             মাপা যায় না; flag হওয়া সবই "corrupted" label এর, সেটা তাই precision এর প্রমাণ না।
  laws       বাংলা আইনের text, টাইপ করা Unicode: 6/25/250 শব্দের টুকরো, আর পুরো আইন।
  ocr_only   যে judgement এর text layer এ বাংলা নেই, বাংলা পুরোটাই OCR থেকে এসেছে।
  clean_pdf  আসল PDF এ পরিষ্কার text layer: (ক) shaping ছাড়া লেখা, (খ) Tesseract এর searchable PDF।
  shaped_pdf জানা পরিষ্কার text কে shaping engine দিয়ে PDF করলে text layer নষ্ট হয় — উৎস জানা ধনাত্মক
             নমুনা। আসল extractor দিয়ে চালিয়ে দেখে OCR কতটা উদ্ধার করে।
"""

import argparse
import collections
import csv
import io
import json
import os
import random
import re
import shutil
import sys
import tempfile
from pathlib import Path

import pymupdf

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ingestion.pdf_extract import _init_worker, extract_page_pymupdf, page_text_blocks, plan_blocks  # noqa: E402
from ingestion.quality_audit import (  # noqa: E402
    BANGLA_CHECK,
    BN_LETTER_RE,
    bangla_file_check,
    bangla_problem,
    bangla_signals,
)

NIRMALA = Path(r"C:\Windows\Fonts\Nirmala.ttc")
BN_WORD_RE = re.compile("[\\u0985-\\u09e3\\u09f0\\u09f1]{2,}")
SEED = 2026


def _surviving(plan) -> str:
    """OCR এ যাবে না এমন block এর text: এটাই output এ text layer থেকে হুবহু থেকে যায়।"""
    return "\n".join(text for _, text, needs_ocr in plan.blocks if not needs_ocr)


def file_outcome(pdf: Path) -> dict:
    """প্রতি page এ আসল extractor এর সিদ্ধান্ত। PDF এ OCR চলে না। Detector একা কী ধরে, আর extraction
    এর পর নষ্ট বাংলা কতটা থেকে যায় — দুটো আলাদা, কারণ legacy rule আগে থেকেই অনেক block OCR করে দেয়।"""
    doc = pymupdf.open(pdf)
    reasons = collections.Counter()
    layer, old_left, new_left = [], [], []
    try:
        for page in doc:
            blocks = page_text_blocks(page)
            plan = plan_blocks(blocks)
            if plan.layer_problem:
                reasons[plan.layer_problem] += 1
            layer.extend(text for b in blocks for text in ["\n".join("".join(s["text"] for s in li["spans"]) for li in b["lines"])])
            old_left.append(_surviving(plan_blocks(blocks, check_layer=False)))
            new_left.append(_surviving(plan))
    finally:
        doc.close()
    whole, old, new = (bangla_signals("\n".join(x)) for x in (layer, old_left, new_left))
    floor_bad, floor_lossy = BANGLA_CHECK["min_tokens_layer_bad"], BANGLA_CHECK["min_tokens_layer_lossy"]
    return {
        "detector_says_corrupted": bool(bangla_problem(whole, floor_bad, floor_lossy)),
        "flagged_pages": dict(reasons),
        "layer_tokens": whole["tokens"],
        "v2_surviving_tokens": old["tokens"], "v2_surviving_bad": old["bad"],
        "v2_surviving_corrupted": bool(bangla_problem(old, floor_bad, floor_lossy)),
        "new_surviving_tokens": new["tokens"], "new_surviving_bad": new["bad"],
        "new_surviving_corrupted": bool(bangla_problem(new, floor_bad, floor_lossy)),
    }


def eval_sample(csv_path: Path, pdf_dir: Path) -> dict:
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    results = [{**r, **file_outcome(pdf_dir / r["filename"])} for r in rows]

    def score(sub):
        n = len(sub)
        hit = sum(x["detector_says_corrupted"] for x in sub)
        return {
            "files": n,
            "detector_recall": {"flagged": hit, "recall": round(hit / n, 4) if n else None},
            "v2_output_still_corrupted": sum(x["v2_surviving_corrupted"] for x in sub),
            "new_output_still_corrupted": sum(x["new_surviving_corrupted"] for x in sub),
            "surviving_bangla_tokens": {"v2": sum(x["v2_surviving_tokens"] for x in sub), "new": sum(x["new_surviving_tokens"] for x in sub)},
            "surviving_bad_tokens": {"v2": sum(x["v2_surviving_bad"] for x in sub), "new": sum(x["new_surviving_bad"] for x in sub)},
        }

    classes = sorted({x["class"] for x in results})
    return {
        "note": "Labels by claude-opus-5-5, not a human. 0 of 62 were labelled clean, so these rows cannot measure "
                "precision: every flag lands on a 'corrupted' label by construction. Use the laws, ocr_only and pdfs "
                "sections for false positives.",
        "blind_rows": score([x for x in results if x["labelled"] == "blind"]),
        "blind_rows_without_uncertain": score([x for x in results if x["labelled"] == "blind" and not x["uncertain"]]),
        "all_rows": score(results),
        "labelled_clean": sum(x["label"] != "corrupted" for x in results),
        "per_class": {c: score([x for x in results if x["class"] == c]) for c in classes},
        "detector_missed": [{k: x[k] for k in ("sample_id", "filename", "class", "labelled", "layer_tokens", "new_surviving_tokens")}
                            for x in results if not x["detector_says_corrupted"]],
        "still_corrupted_after_new_rule": [{k: x[k] for k in ("sample_id", "filename", "class", "new_surviving_tokens", "new_surviving_bad")}
                                           for x in results if x["new_surviving_corrupted"]],
    }


def law_texts() -> list[str]:
    out = []
    for law in json.loads((ROOT / "bangladesh_laws.json").read_text(encoding="utf-8")):
        text = " ".join(s.get("text", "") for s in law.get("sections", []))
        if len(BN_LETTER_RE.findall(text)) > 200:
            out.append(text)
    return out


def eval_laws(texts: list[str]) -> dict:
    rng = random.Random(SEED)
    out = {}
    for size, keep in ((6, 0.02), (25, 1.0), (250, 1.0)):
        judged = flagged = 0
        reasons = collections.Counter()
        for text in texts:
            tokens = text.split()
            for i in range(0, len(tokens), size):
                if keep < 1.0 and rng.random() > keep:
                    continue
                signals = bangla_signals(" ".join(tokens[i:i + size]))
                if not signals["tokens"]:
                    continue
                judged += 1
                problem = bangla_problem(signals, BANGLA_CHECK["min_tokens_layer_bad"], BANGLA_CHECK["min_tokens_layer_lossy"])
                flagged += bool(problem)
                reasons[problem] += bool(problem)
        out[f"{size}_word_pieces"] = {"judged": judged, "flagged": flagged, "rate": round(flagged / max(1, judged), 5),
                                      "reasons": {k: v for k, v in reasons.items() if v}}
    whole = [bangla_file_check(t, len(t.split())) for t in texts]
    out["whole_laws_file_rule"] = {"laws": len(whole), "flagged": sum(bool(r["corrupted_bangla"]) for r in whole)}
    total = collections.Counter()
    for text in texts:
        s = bangla_signals(text)
        for key in ("tokens", "foreign", "unassigned", "misplaced"):
            total[key] += s[key]
    out["token_counts"] = dict(total)
    return out


def layer_bangla_files(pdf_dir: Path) -> set[str]:
    """OCR-only দল থেকে বাদ দিতে: text layer এ বাংলা অক্ষর আছে যেসব file। সব page পড়া হয়।"""
    found = set()
    for pdf in sorted(pdf_dir.glob("*.pdf")):
        doc = pymupdf.open(pdf)
        try:
            if any(BN_LETTER_RE.search(page.get_text("text")) for page in doc):
                found.add(pdf.name)
        finally:
            doc.close()
    return found


def eval_ocr_only(text_dir: Path, pdf_dir: Path) -> dict:
    layered = layer_bangla_files(pdf_dir)
    by_size = collections.defaultdict(lambda: [0, 0])
    flagged_files = []
    pages_judged = pages_flagged = files = 0
    for path in sorted(text_dir.glob("*.txt")):
        name = path.stem + ".pdf"
        if name in layered:
            continue
        text = path.read_text(encoding="utf-8")
        if not BN_LETTER_RE.search(text):
            continue
        files += 1
        row = bangla_file_check(text, len(text.split()))
        n = row["bangla_tokens"]
        size = "<10 (not judged whole)" if n < 10 else "10-49" if n < 50 else "50-199" if n < 200 else "200+"
        by_size[size][1] += 1
        by_size[size][0] += bool(row["corrupted_bangla"])
        if row["corrupted_bangla"]:
            flagged_files.append({"file": name, "tokens": n, "bad_ratio": row["bangla_bad_ratio"],
                                  "isolated_ratio": row["bangla_isolated_ratio"], "pages": row["corrupted_bangla_pages"]})
        for page in text.split("\f"):
            signals = bangla_signals(page)
            if signals["tokens"] >= BANGLA_CHECK["min_tokens_page"]:
                pages_judged += 1
                pages_flagged += bool(bangla_problem(signals, BANGLA_CHECK["min_tokens_page"]))
    return {
        "files_with_bangla_and_no_layer_bangla": files,
        "files_flagged": len(flagged_files),
        "by_bangla_tokens_flagged_of_total": dict(sorted(by_size.items())),
        "pages_20plus_tokens": {"judged": pages_judged, "flagged": pages_flagged},
        "flagged": flagged_files[:40],
    }


def _law_samples(texts: list[str], n: int, words: int) -> list[str]:
    out = [" ".join(t.split()[:words]) for t in texts if len(BN_LETTER_RE.findall(t)) > 3000][:n]
    return out


def _lines(page) -> str:
    return "\n".join("".join(s["text"] for s in line["spans"]) for b in page_text_blocks(page) for line in b["lines"])


def _word_recall(truth: str, got: str) -> float:
    want = collections.Counter(BN_WORD_RE.findall(bangla_clean(truth)))
    have = collections.Counter(BN_WORD_RE.findall(bangla_clean(got)))
    return sum((want & have).values()) / max(1, sum(want.values()))


def bangla_clean(text: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFC", text.replace("\u200c", "").replace("\u200d", ""))


def eval_pdfs(texts: list[str], tmp: Path) -> dict:
    if not NIRMALA.exists():
        return {"skipped": "Nirmala UI font not found"}
    _init_worker()
    samples = _law_samples(texts, 12, 300)
    font = pymupdf.Font(fontfile=str(NIRMALA))

    doc = pymupdf.open()
    for text in samples:
        page = doc.new_page()
        writer = pymupdf.TextWriter(page.rect)
        writer.fill_textbox(page.rect + (40, 40, -40, -40), text, font=font, fontsize=10)
        writer.write_text(page)
    doc.save(tmp / "exact_codepoints.pdf")
    doc.close()

    css = "@font-face {font-family: nm; src: url(" + NIRMALA.as_posix() + ");} body {font-family: nm; font-size: 12pt;}"
    archive = pymupdf.Archive()
    doc = pymupdf.open()
    shaped_texts = [t[:1500] for t in samples[:6]]
    for text in shaped_texts:
        page = doc.new_page()
        page.insert_htmlbox(page.rect + (40, 40, -40, -40), f"<p>{text}</p>", css=css, archive=archive)
    doc.save(tmp / "shaped.pdf")
    doc.close()

    out = {}
    doc = pymupdf.open(tmp / "exact_codepoints.pdf")
    plans = [plan_blocks(page_text_blocks(p)) for p in doc]
    recalls = [_word_recall(samples[i], _lines(p)) for i, p in enumerate(doc)]
    doc.close()
    out["clean_layer_exact_codepoints"] = {
        "pages": len(plans), "flagged": sum(bool(p.layer_problem) for p in plans), "methods": dict(collections.Counter(p.method for p in plans)),
        "word_recall_of_layer_vs_source_min": round(min(recalls), 3)}

    # Tesseract এর PDF renderer পরিষ্কার Unicode লেখে, তবে pdf.ttf আর configs/pdf লাগে; সাময়িক tessdata folder এ রাখা
    ttf = Path(r"C:\Program Files\Tesseract-OCR\tessdata\pdf.ttf")
    tessdata = ROOT / "tessdata"
    if ttf.exists():
        import pytesseract
        from PIL import Image
        scratch = tmp / "tessdata"
        scratch.mkdir(exist_ok=True)
        shutil.copy(ttf, scratch / "pdf.ttf")
        shutil.copytree(ttf.parent / "configs", scratch / "configs", dirs_exist_ok=True)
        for lang in ("ben", "eng", "osd"):
            shutil.copy(tessdata / f"{lang}.traineddata", scratch / f"{lang}.traineddata")
        previous = os.environ.get("TESSDATA_PREFIX")
        os.environ["TESSDATA_PREFIX"] = str(scratch)
        shaped = pymupdf.open(tmp / "shaped.pdf")
        plans = []
        try:
            for page in shaped:
                image = Image.open(io.BytesIO(page.get_pixmap(dpi=200).tobytes("png")))
                data = pytesseract.image_to_pdf_or_hocr(image, lang="ben+eng", extension="pdf",
                                                        config="-c textord_noise_rejrows=0 -c textord_noise_rejwords=0")
                searchable = pymupdf.open(stream=data, filetype="pdf")
                plans.append(plan_blocks(page_text_blocks(searchable[0])))
                searchable.close()
        finally:
            shaped.close()
            if previous is None:
                os.environ.pop("TESSDATA_PREFIX", None)
            else:
                os.environ["TESSDATA_PREFIX"] = previous
        out["clean_layer_tesseract_searchable_pdf"] = {
            "pages": len(plans), "flagged": sum(bool(p.layer_problem) for p in plans), "methods": dict(collections.Counter(p.method for p in plans))}
    else:
        out["clean_layer_tesseract_searchable_pdf"] = {"skipped": "pdf.ttf not found"}

    # উৎস জানা ধনাত্মক নমুনা, আসল extractor দিয়ে
    doc = pymupdf.open(tmp / "shaped.pdf")
    layer_recall, extracted_recall, plans, methods = [], [], [], collections.Counter()
    for i, page in enumerate(doc):
        plans.append(plan_blocks(page_text_blocks(page)))
        layer_recall.append(_word_recall(shaped_texts[i], _lines(page)))
        text, method, _ = extract_page_pymupdf(page)
        methods[method] += 1
        extracted_recall.append(_word_recall(shaped_texts[i], text))
    doc.close()
    out["shaped_pdf_known_source"] = {
        "pages": len(plans), "flagged": sum(bool(p.layer_problem) for p in plans), "methods": dict(methods),
        "word_recall_text_layer": round(sum(layer_recall) / len(layer_recall), 3),
        "word_recall_after_extractor": round(sum(extracted_recall) / len(extracted_recall), 3)}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", default=str(ROOT / "data_quality" / "corrupted_bangla_handcheck.csv"))
    ap.add_argument("--pdf-dir", default=str(ROOT / "downloaded_pdfs"))
    ap.add_argument("--text-dir", default=str(ROOT / "text_v2"))
    ap.add_argument("--out", default=str(ROOT / "data_quality" / "bangla_detector_eval.json"))
    args = ap.parse_args()

    labels, pdf_dir, text_dir = Path(args.labels), Path(args.pdf_dir), Path(args.text_dir)
    texts = law_texts()
    report = {"thresholds": BANGLA_CHECK, "sample": eval_sample(labels, pdf_dir)}
    print(json.dumps(report["sample"], ensure_ascii=False, indent=1), flush=True)
    report["laws"] = eval_laws(texts)
    print(json.dumps(report["laws"], ensure_ascii=False, indent=1), flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        report["pdfs"] = eval_pdfs(texts, Path(tmp))
    print(json.dumps(report["pdfs"], ensure_ascii=False, indent=1), flush=True)
    report["ocr_only"] = eval_ocr_only(text_dir, pdf_dir)
    print(json.dumps(report["ocr_only"], ensure_ascii=False, indent=1), flush=True)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"→ {args.out}")


if __name__ == "__main__":
    main()
