"""
ingestion/text_checks.py
========================
quality_audit.py যা ধরে না তার read-only পরীক্ষা (docs/DECISIONS.md D-113):

  bangla    বাংলা অক্ষর দেখা গেলেই text ঠিক না। কিছু PDF এর text layer এ বাংলার সাথে Latin/IPA/
            Cyrillic অক্ষর মেশানো, কার visual ক্রমে ('ǯদওয়ানী আদালেত ɛিতকার')। প্রতিটা file এ:
              mixed_token_ratio  বাংলা token এর মধ্যে অন্য লিপির অক্ষর আছে এমন token এর ভাগ
              vocab_hit          বাংলা token এর কত ভাগ বাংলা আইনের শব্দভাণ্ডারে আছে
            পুরনো rule (D-113, "62 file"), অনেক file বাদ পড়ে। নতুন rule quality_audit.py এ (D-117);
            এটা শুধু পুরনো সংখ্যা মেলানোর জন্য।
  scanned   ছোট text কিন্তু page জুড়ে image — অল্প junk text এর কারণে OCR বাদ পড়া scan হতে পারে।
            কতগুলো page, আর একটা sample OCR করলে কত text বাড়ে।
  ocr       OCR accuracy, আসল ground truth দিয়ে: জানা পরিষ্কার বাংলা (আইনের text) render করে OCR
            করে মেলানো। পরিষ্কার font এ best case — legacy-font scan এর accuracy এর উপরের সীমা।
  lines     OCR করা page এ লাইন হারিয়েছে কিনা: PDF এর text layer এ অক্ষর mojibake হলেও লাইনের সংখ্যা
            ঠিক থাকে, সেটা সংরক্ষিত output এর সাথে মেলানো হয়। OCR চলে না।

    python ingestion/text_checks.py bangla scanned ocr lines --out data_quality/text_checks.json

শুধু --out আর data_quality/bangla_layer_check.csv লেখে; text_v2, PDF, Chroma ছোঁয় না।
"""

import argparse
import collections
import csv
import difflib
import io
import json
import random
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BANGLA_TOKEN_RE = re.compile(r"[\u0980-\u09ff]{2,}")
ANY_TOKEN_RE = re.compile(r"\S+")
HAS_BANGLA_RE = re.compile(r"[\u0980-\u09ff]")
# বাংলা শব্দের ভেতরে এগুলো থাকার কথা না: Latin Extended, IPA, combining mark, Greek, Cyrillic
FOREIGN_RE = re.compile(r"[\u0180-\u036f\u0370-\u03ff\u0400-\u052f\u1e00-\u1eff]")

CONFIG = {
    "min_bangla_tokens": 50,        # এর কম বাংলা token হলে file টা বিচার হয় না
    "corrupt_mixed_ratio": 0.05,    # sample দেখে বসানো; docs/DECISIONS.md D-113
    "short_page_chars": 300,
    "image_coverage": 0.5,
    "ocr_sample_pages": 25,
    "seed": 13,
}


def bangla_vocab(min_count: int = 3) -> set[str]:
    counts = collections.Counter()
    for law in json.loads((ROOT / "bangladesh_laws.json").read_text(encoding="utf-8")):
        for section in law.get("sections", []):
            counts.update(BANGLA_TOKEN_RE.findall(section.get("text", "")))
    return {w for w, n in counts.items() if n >= min_count}


def check_bangla_text(text: str, vocab: set[str]) -> dict:
    tokens = [t for t in ANY_TOKEN_RE.findall(text) if HAS_BANGLA_RE.search(t)]
    mixed = sum(1 for t in tokens if FOREIGN_RE.search(t))
    words = BANGLA_TOKEN_RE.findall(text)
    hit = sum(1 for w in words if w in vocab)
    return {"bangla_tokens": len(tokens), "mixed_tokens": mixed,
            "mixed_token_ratio": round(mixed / len(tokens), 4) if tokens else 0.0,
            "vocab_hit": round(hit / len(words), 4) if words else 0.0}


def run_bangla(text_dir: Path, csv_out: Path) -> dict:
    vocab = bangla_vocab()
    rows = []
    for path in sorted(text_dir.glob("*.txt")):
        row = check_bangla_text(path.read_text(encoding="utf-8"), vocab)
        row["filename"] = path.stem + ".pdf"
        rows.append(row)
    judged = [r for r in rows if r["bangla_tokens"] >= CONFIG["min_bangla_tokens"]]
    for row in rows:
        row["layer"] = ("too_little_bangla" if row["bangla_tokens"] < CONFIG["min_bangla_tokens"] else
                        "corrupted" if row["mixed_token_ratio"] > CONFIG["corrupt_mixed_ratio"] else "ok")
    with csv_out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["filename", "layer", "bangla_tokens", "mixed_tokens", "mixed_token_ratio", "vocab_hit"])
        writer.writeheader()
        writer.writerows(rows)
    corrupted = [r for r in judged if r["layer"] == "corrupted"]
    ok = [r for r in judged if r["layer"] == "ok"]

    def median(values):
        values = sorted(values)
        return values[len(values) // 2] if values else None

    total = sum(r["bangla_tokens"] for r in judged) or 1
    return {
        "vocabulary_size": len(vocab),
        "files_with_bangla_judged": len(judged),
        "corrupted_files": len(corrupted),
        "corrupted_share_of_bangla_tokens": round(sum(r["bangla_tokens"] for r in corrupted) / total, 4),
        "median_vocab_hit": {"ok": median([r["vocab_hit"] for r in ok]), "corrupted": median([r["vocab_hit"] for r in corrupted])},
        "mixed_ratio_histogram": dict(sorted(collections.Counter(
            "<1%" if r["mixed_token_ratio"] < 0.01 else "1-5%" if r["mixed_token_ratio"] <= 0.05 else
            "5-20%" if r["mixed_token_ratio"] <= 0.2 else ">20%" for r in judged).items())),
        "largest_corrupted": [(r["filename"], r["bangla_tokens"], r["mixed_token_ratio"], r["vocab_hit"])
                              for r in sorted(corrupted, key=lambda r: -r["bangla_tokens"])[:8]],
    }


def _ocr_page(page, dpi: int) -> str:
    from PIL import Image

    from ingestion.pdf_extract import _ocr_image
    return _ocr_image(Image.open(io.BytesIO(page.get_pixmap(dpi=dpi).tobytes("png"))))


def run_scanned(text_dir: Path, pdf_dir: Path) -> dict:
    import pymupdf

    from ingestion.pdf_extract import CONFIG as EXTRACT
    from ingestion.pdf_extract import _init_worker
    _init_worker()
    suspects, checked_pages, skipped_docs = [], 0, 0
    manifest = {}
    for line in (text_dir / "_manifest.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            manifest[row["filename"]] = row
    for path in sorted(text_dir.glob("*.txt")):
        name = path.stem + ".pdf"
        parts = path.read_text(encoding="utf-8").split("\f")
        if len(parts) != manifest[name]["pages"]:
            skipped_docs += 1     # stray \f, page ধরে মেলানো যায় না
            continue
        short = [i for i, p in enumerate(parts) if len(p.strip()) < CONFIG["short_page_chars"]]
        checked_pages += len(parts)
        if not short:
            continue
        doc = pymupdf.open(pdf_dir / name)
        for i in short:
            page = doc[i]
            area = abs(page.rect) or 1
            covered = sum(abs(pymupdf.Rect(info["bbox"]) & page.rect) for info in page.get_image_info())
            if covered / area > CONFIG["image_coverage"]:
                suspects.append((name, i, len(parts[i].strip())))
        doc.close()

    random.seed(CONFIG["seed"])
    sample = random.sample(suspects, min(CONFIG["ocr_sample_pages"], len(suspects)))
    gains = []
    for name, i, chars in sample:
        doc = pymupdf.open(pdf_dir / name)
        ocr_chars = len(" ".join(_ocr_page(doc[i], EXTRACT["ocr_dpi"]).split()))
        doc.close()
        gains.append({"file": name, "page": i, "extracted_chars": chars, "ocr_chars": ocr_chars})
    missed = [g for g in gains if g["ocr_chars"] > max(300, 3 * g["extracted_chars"])]
    return {
        "pages_checked": checked_pages, "docs_skipped_stray_ff": skipped_docs,
        "short_pages_mostly_image": len(suspects), "files_affected": len({s[0] for s in suspects}),
        "sampled": len(gains), "sampled_with_much_more_text_when_ocrd": len(missed),
        "sample_rows": sorted(gains, key=lambda g: -g["ocr_chars"])[:8],
    }


def run_ocr_accuracy() -> dict:
    """জানা পরিষ্কার বাংলা → Nirmala UI তে render → OCR → মূল text এর সাথে তুলনা।"""
    import pymupdf

    from ingestion.pdf_extract import CONFIG as EXTRACT
    from ingestion.pdf_extract import _init_worker
    _init_worker()
    sections = []
    for law in json.loads((ROOT / "bangladesh_laws.json").read_text(encoding="utf-8")):
        for section in law.get("sections", []):
            text = " ".join(section.get("text", "").split())
            if 900 <= len(text) and len(HAS_BANGLA_RE.findall(text)) / len(text) > 0.75:
                sections.append(text[:1400])
    random.seed(CONFIG["seed"])
    sample = random.sample(sections, min(20, len(sections)))
    archive = pymupdf.Archive("C:/Windows/Fonts")
    css = '@font-face {font-family: bn; src: url(Nirmala.ttc);} body {font-family: bn; font-size: 13pt; line-height: 1.5;}'
    rows = []
    for truth in sample:
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_htmlbox(pymupdf.Rect(60, 60, 540, 780), f"<p>{truth}</p>", css=css, archive=archive)
        ocr = " ".join(_ocr_page(page, EXTRACT["ocr_dpi"]).split())
        doc.close()
        char_sim = difflib.SequenceMatcher(None, truth, ocr, autojunk=False).ratio()
        want = collections.Counter(BANGLA_TOKEN_RE.findall(truth))
        got = collections.Counter(BANGLA_TOKEN_RE.findall(ocr))
        recall = sum((want & got).values()) / (sum(want.values()) or 1)
        rows.append({"char_similarity": round(char_sim, 3), "word_recall": round(recall, 3),
                     "latin_words_added": len(re.findall(r"\b[A-Za-z]{2,}\b", ocr)) - len(re.findall(r"\b[A-Za-z]{2,}\b", truth))})

    def stats(key):
        values = sorted(r[key] for r in rows)
        return {"median": values[len(values) // 2], "min": values[0], "max": values[-1]}

    return {"pages": len(rows), "char_similarity": stats("char_similarity"), "word_recall": stats("word_recall"),
            "latin_words_added": stats("latin_words_added")}


def _visual_lines(blocks: list) -> int:
    """একই baseline এর span গুলো এক লাইন, PDF যেভাবেই ভাগ করুক।"""
    ys = sorted(round((s["bbox"][1] + s["bbox"][3]) / 2) for b in blocks for line in b["lines"]
                for s in line["spans"] if s["text"].strip())
    count, last = 0, None
    for y in ys:
        if last is None or y - last > 6:
            count += 1
        last = y
    return count


def _output_lines(text: str) -> int:
    return sum(1 for x in text.splitlines() if len(re.findall(r"[\u0980-\u09ffA-Za-z0-9]", x)) >= 3)


def run_lines(text_dir: Path, pdf_dir: Path, sample_large: int = 800) -> dict:
    import pymupdf

    from ingestion.pdf_extract import CONFIG as EXTRACT
    from ingestion.pdf_extract import _is_legacy_span
    manifest = {}
    for line in (text_dir / "_manifest.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            manifest[row["filename"]] = row
    random.seed(5)
    # pages, pdf lines, kept lines, pages with any shortfall, pages losing >10%
    groups = {"full_page_ocr": [0, 0, 0, 0, 0], "block_ocr": [0, 0, 0, 0, 0]}
    per_file = {}
    skipped = 0
    for name, row in manifest.items():
        if set(row["methods"]) == {"pymupdf"}:
            continue
        parts = (text_dir / (name[:-4] + ".txt")).read_text(encoding="utf-8").split("\f")
        if len(parts) != row["pages"]:
            skipped += 1
            continue
        pages = sorted(random.sample(range(row["pages"]), sample_large)) if row["pages"] > sample_large else range(row["pages"])
        doc = pymupdf.open(pdf_dir / name)
        for i in pages:
            blocks = [b for b in doc[i].get_text("dict", sort=True)["blocks"] if b.get("type") == 0]
            total = legacy = 0
            for b in blocks:
                for line in b["lines"]:
                    for s in line["spans"]:
                        total += len(s["text"])
                        legacy += len(s["text"]) if _is_legacy_span(s) else 0
            if total < EXTRACT["min_page_chars"] or not legacy:
                continue
            key = "full_page_ocr" if legacy / total > EXTRACT["full_page_ocr_share"] else "block_ocr"
            want = _visual_lines(blocks)
            if want < 5:
                continue
            got = min(want, _output_lines(parts[i]))
            g = groups[key]
            g[0] += 1
            g[1] += want
            g[2] += got
            g[3] += got < want
            g[4] += got < 0.9 * want
            if row["pages"] > sample_large and key == "full_page_ocr":
                f = per_file.setdefault(name, [0, 0])
                f[0] += want
                f[1] += got
        doc.close()
    return {
        "note": "PDF line counts vs saved output; files over sample_large pages are sampled",
        "docs_skipped_stray_ff": skipped, "sample_large": sample_large,
        **{k: {"pages": g[0], "pdf_lines": g[1], "output_lines": g[2], "kept_pct": round(g[2] / max(1, g[1]) * 100, 1),
               "pages_with_any_shortfall_pct": round(g[3] / max(1, g[0]) * 100, 1),
               "pages_losing_over_10pct": round(g[4] / max(1, g[0]) * 100, 1)} for k, g in groups.items()},
        "large_files_full_page_kept_pct": {k: round(v[1] / max(1, v[0]) * 100, 1) for k, v in per_file.items()},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("checks", nargs="+", choices=["bangla", "scanned", "ocr", "lines"])
    ap.add_argument("--text-dir", default=str(ROOT / "text_v2"))
    ap.add_argument("--pdf-dir", default=str(ROOT / "downloaded_pdfs"))
    ap.add_argument("--csv", default=str(ROOT / "data_quality" / "bangla_layer_check.csv"))
    ap.add_argument("--out", default=str(ROOT / "data_quality" / "text_checks.json"))
    args = ap.parse_args()

    out = Path(args.out)
    results = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    if "bangla" in args.checks:
        results["bangla"] = run_bangla(Path(args.text_dir), Path(args.csv))
    if "scanned" in args.checks:
        results["scanned"] = run_scanned(Path(args.text_dir), Path(args.pdf_dir))
    if "ocr" in args.checks:
        results["ocr_accuracy_clean_font"] = run_ocr_accuracy()
    if "lines" in args.checks:
        results["lines"] = run_lines(Path(args.text_dir), Path(args.pdf_dir))
    results["config"] = CONFIG
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in results.items() if k != "config"}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
