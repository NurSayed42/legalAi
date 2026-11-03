"""
ingestion/pdf_extract.py
========================
Judgement PDF → text, extraction cascade (SPEC Phase 1.4)। PyPDF2 এর তিনটা সমস্যা ঠিক করে:
ভাঙা শব্দ ('DIVISIO N'), legacy-font বাংলা ('q¡C−L¡VÑ' = 'হাইকোর্ট'), আর text layer ছাড়া scan
(extract_text.py এগুলো চুপচাপ বাদ দিত)।

প্রতি page এ:
  1. PyMuPDF text (blocks, reading order)।
  2. PyMuPDF খুলতে/পড়তে না পারলে pdfplumber।
  3. OCR (Tesseract ben+eng):
       - page এ কোনো text নেই                         → পুরো page
       - legacy-font text page এর অর্ধেকের বেশি         → পুরো page (একটা call, কম overhead)
       - কিছু block legacy font এ                       → শুধু ওই block গুলোর অংশ
     Legacy span চেনা হয় font নাম (AdarshaLipi*, SutonnyMJ …) অথবা mojibake character দিয়ে।
     SPEC এ "OCR only for pages with no extractable text" — কিন্তু legacy-font page এ text আছে,
     সেটাই mojibake; তাই trigger টা "page/block quality check fail" (docs/DECISIONS.md D-103)।

    python ingestion/pdf_extract.py                          # downloaded_pdfs/ → text_v2/
    python ingestion/pdf_extract.py --workers 4 --limit 50

Resumable: text_v2/<name>.txt থাকলে skip। প্রতি file এর method/সময় text_v2/_manifest.jsonl এ,
ব্যর্থ file data_quality/failures.jsonl এ। Page গুলো form feed (\\f) দিয়ে আলাদা।
"""

import argparse
import io
import json
import os
import re
import sys
import time
import traceback
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ingestion.quality_audit import LEGACY_RE  # noqa: E402

CONFIG = {
    "tesseract_cmd": os.getenv("TESSERACT_CMD", r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
    "tessdata_dir": os.getenv("TESSDATA_PREFIX", str(ROOT / "tessdata")),
    "ocr_lang": "ben+eng",
    "ocr_dpi": 200,                 # probe এ 300 dpi র চেয়ে দ্রুত (3.7s vs 6.3s/page) আর কম junk
    "min_page_chars": 20,           # এর কম text থাকলে page "text ছাড়া" ধরা হয়
    "full_page_ocr_share": 0.5,     # legacy-font character এর ভাগ এর বেশি হলে পুরো page OCR
    "span_legacy_char_ratio": 0.1,  # font নাম না মিললেও span এ এত mojibake থাকলে legacy
    "span_control_char_ratio": 0.3, # এর বেশি control code = character map ছাড়া embed করা font
}
LEGACY_FONT_RE = re.compile(r"AdarshaLipi|SutonnyMJ|MJ\b|Bijoy|Sulekha|Lekhoni|Boishakhi|ChandrabatiMJ|Kalpurush ANSI",
                            re.IGNORECASE)
CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f]")
PAGE_SEP = "\f"


def _is_legacy_span(span: dict) -> bool:
    """Text layer থেকে পড়া যায় না এমন span — legacy-font mojibake, অথবা ToUnicode map ছাড়া
    embed করা font (sample এ Vrinda: বাংলা অক্ষরের বদলে '\\x01\\x02\\x03' glyph id আসে)।"""
    text = span.get("text", "")
    if LEGACY_FONT_RE.search(span.get("font", "")):
        return bool(text.strip())
    if text.strip() and len(CONTROL_RE.findall(text)) / len(text) > CONFIG["span_control_char_ratio"]:
        return True
    letters = sum(c.isalpha() for c in text) or 1
    return len(text) >= 5 and len(LEGACY_RE.findall(text)) / letters > CONFIG["span_legacy_char_ratio"]


def _ocr_image(img) -> str:
    import pytesseract
    return pytesseract.image_to_string(img, lang=CONFIG["ocr_lang"])


def _ocr_pymupdf(page, clip=None) -> str:
    from PIL import Image
    pix = page.get_pixmap(dpi=CONFIG["ocr_dpi"], clip=clip)
    return _ocr_image(Image.open(io.BytesIO(pix.tobytes("png"))))


def extract_page_pymupdf(page) -> tuple[str, str, int]:
    """(text, method, ocr_blocks)"""
    import pymupdf
    blocks = [b for b in page.get_text("dict", sort=True)["blocks"] if b.get("type") == 0]
    total = legacy = 0
    parsed = []
    for b in blocks:
        spans = [s for line in b["lines"] for s in line["spans"]]
        n = sum(len(s["text"]) for s in spans)
        n_legacy = sum(len(s["text"]) for s in spans if _is_legacy_span(s))
        total += n
        legacy += n_legacy
        text = "\n".join("".join(s["text"] for s in line["spans"]) for line in b["lines"])
        parsed.append((b, text, n_legacy > 0))

    if total < CONFIG["min_page_chars"]:
        return _ocr_pymupdf(page), "ocr_page_no_text", 0
    if legacy / total > CONFIG["full_page_ocr_share"]:
        return _ocr_pymupdf(page), "ocr_page_legacy", 0
    if legacy == 0:
        return "\n".join(t for _, t, _ in parsed), "pymupdf", 0

    out, ocr_blocks = [], 0
    for b, text, has_legacy in parsed:
        if has_legacy:
            out.append(_ocr_pymupdf(page, clip=pymupdf.Rect(b["bbox"])).strip())
            ocr_blocks += 1
        else:
            out.append(text)
    return "\n".join(out), "pymupdf+ocr_blocks", ocr_blocks


def extract_page_pdfplumber(pdf_path: Path, page_no: int) -> tuple[str, str]:
    import pdfplumber
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[page_no]
        text = page.extract_text() or ""
        if len(text.strip()) >= CONFIG["min_page_chars"] and not LEGACY_RE.search(text):
            return text, "pdfplumber"
        return _ocr_image(page.to_image(resolution=CONFIG["ocr_dpi"]).original), "ocr_page_pdfplumber"


def extract_pdf(pdf_path: Path) -> dict:
    import pymupdf
    started = time.time()
    pages, methods, ocr_blocks = [], Counter(), 0
    try:
        doc = pymupdf.open(pdf_path)
        n_pages = len(doc)
    except Exception:
        doc = None
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            n_pages = len(pdf.pages)

    for i in range(n_pages):
        try:
            if doc is None:
                raise RuntimeError("pymupdf could not open the file")
            text, method, n_ocr = extract_page_pymupdf(doc[i])
            ocr_blocks += n_ocr
        except Exception:
            text, method = extract_page_pdfplumber(pdf_path, i)
        pages.append(text.strip())
        methods[method] += 1
    return {"text": PAGE_SEP.join(pages), "pages": n_pages, "methods": dict(methods),
            "ocr_blocks": ocr_blocks, "seconds": round(time.time() - started, 1)}


def _init_worker():
    import pytesseract
    pytesseract.pytesseract.tesseract_cmd = CONFIG["tesseract_cmd"]
    os.environ["TESSDATA_PREFIX"] = CONFIG["tessdata_dir"]
    os.environ["OMP_THREAD_LIMIT"] = "1"   # parallel process এর মধ্যে Tesseract thread কাড়াকাড়ি না করে


def _process(pdf_path: str, out_dir: str) -> dict:
    path = Path(pdf_path)
    try:
        result = extract_pdf(path)
        (Path(out_dir) / f"{path.stem}.txt").write_text(result["text"], encoding="utf-8")
        return {"filename": path.name, "ok": True, **{k: v for k, v in result.items() if k != "text"},
                "chars": len(result["text"])}
    except Exception as e:
        return {"filename": path.name, "ok": False, "error": f"{type(e).__name__}: {e}",
                "trace": traceback.format_exc(limit=3)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf-dir", default=str(ROOT / "downloaded_pdfs"))
    ap.add_argument("--out", default=str(ROOT / "text_v2"))
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--force", action="store_true", help="আগে থেকে থাকা .txt আবার তৈরি করো")
    ap.add_argument("--retry-failed", action="store_true", help="failures.jsonl এ থাকা file আবার চেষ্টা করো")
    ap.add_argument("--settle-seconds", type=int, default=30,
                    help="এর চেয়ে নতুন PDF বাদ — download চলাকালীন আধা-লেখা file না পড়তে")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    failures_path = ROOT / "data_quality" / "failures.jsonl"
    failures_path.parent.mkdir(parents=True, exist_ok=True)
    failed_before = set()
    if failures_path.exists() and not args.retry_failed:
        failed_before = {json.loads(line)["filename"] for line in failures_path.read_text(encoding="utf-8").splitlines()
                         if line.strip()}

    cutoff = time.time() - args.settle_seconds
    pdfs = sorted(p for p in Path(args.pdf_dir).glob("*.pdf") if p.stat().st_mtime < cutoff)
    todo = [p for p in pdfs if (args.force or not (out_dir / f"{p.stem}.txt").exists()) and p.name not in failed_before]
    if args.limit:
        todo = todo[:args.limit]
    print(f"{len(pdfs)} PDF, {len(pdfs) - len(todo)} আগেই হয়েছে, {len(todo)} বাকি — {args.workers} worker")

    done = failed = 0
    methods = Counter()
    started = time.time()
    with ProcessPoolExecutor(args.workers, initializer=_init_worker) as pool, \
            open(out_dir / "_manifest.jsonl", "a", encoding="utf-8") as manifest, \
            open(failures_path, "a", encoding="utf-8") as failures:
        futures = [pool.submit(_process, str(p), str(out_dir)) for p in todo]
        for fut in as_completed(futures):
            row = fut.result()
            if row["ok"]:
                done += 1
                methods.update(row["methods"])
                manifest.write(json.dumps(row, ensure_ascii=False) + "\n")
            else:
                failed += 1
                failures.write(json.dumps({"stage": "pdf_extract", **row}, ensure_ascii=False) + "\n")
            if (done + failed) % 50 == 0 or done + failed == len(todo):
                rate = (time.time() - started) / max(1, done + failed)
                print(f"   {done + failed}/{len(todo)} ({failed} failed, {rate:.1f}s/file) pages by method: {dict(methods)}",
                      flush=True)
    print(f"✅ {done} extracted, {failed} failed → {out_dir}")


if __name__ == "__main__":
    main()
