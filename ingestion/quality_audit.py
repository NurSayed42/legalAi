"""
ingestion/quality_audit.py
==========================
Extracted judgement text এর quality audit (SPEC Phase 1)। প্রতিটা file এর metric:

  chars, words                 — আকার
  nonprintable_ratio           — control / U+FFFD / private-use character এর ভাগ
  bangla_ratio, latin_ratio    — অক্ষরের মধ্যে বাংলা (U+0980–09FF) আর ASCII অক্ষরের ভাগ
  legacy_font_ratio            — Bijoy/SutonnyMJ mojibake চিহ্ন (¡ ¢ £ Ñ − …) এর ভাগ
  avg_word_len                 — অক্ষর-token এর গড় দৈর্ঘ্য
  split_fragment_ratio         — ১-২ অক্ষরের token (সাধারণ শব্দ বাদে) — 'o f', 'appella nt' ধরনের ভাঙা শব্দ
  garbage_score                — অক্ষর/সংখ্যা/সাধারণ যতিচিহ্ন নয় এমন character এর ভাগ + legacy font ভাগ
  repeat_ratio                 — ১২-শব্দের shingle এর মধ্যে duplicate এর ভাগ (একই text layer ২-৪ বার)
  near_empty                   — শব্দ সংখ্যা min_words এর কম

নতুন check (docs/DECISIONS.md D-117, D-118) — পুরনো rule এর ফল bucket_old/reasons_old এ থাকে:

  corrupted_bangla             — বাংলা দেখতে Unicode, কিন্তু আসলে নষ্ট (bangla_signals দেখো)
  junk_pages                   — প্রায় পুরো page প্রতীকের junk ('52 ) - (/ 0 * + / 11 * %')
  line_retention               — OCR করা page এ লাইন কত থাকল; --pdf-dir দিলে। Saved output এ Tesseract
                                 কত লাইন দেখেছিল সেটা নেই, তাই হর হিসেবে PDF এর লাইন-কাঠামো (pdf_lines);
                                 flag সহ Tesseract এর 99.2% মেলে। per-page মান <out>_pages.csv এ।
  layer_new_ocr_pages          — নতুন rule এ যে page এ OCR বাড়বে, কিন্তু v2 তে হয়নি

  python ingestion/quality_audit.py                                   # judgements.json (v1)
  python ingestion/quality_audit.py --source text_v2 --pdf-dir downloaded_pdfs --out data_quality/audit_v2_strict.csv

Output: <out>.csv (প্রতি file এক সারি) আর <out>_summary.json (bucket অনুযায়ী count/percent)।
Bucket threshold গুলো নিচের THRESHOLDS এ — অনুমান থেকে বসানো, docs/DECISIONS.md এ লেখা।
"""

import argparse
import csv
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

THRESHOLDS = {
    # ৫০-১৪৯ শব্দের ৫০৪টা file এর sample সব আসল ছোট admission order ছিল — তাই ১৫০ না, ৫০
    "min_words": 50,
    "broken_legacy_font_ratio": 0.02,
    "broken_nonprintable_ratio": 0.01,
    "broken_garbage_score": 0.15,
    "broken_repeat_ratio": 0.3,
    "suspect_garbage_score": 0.05,
    "suspect_split_fragment_ratio": 0.06,
    "suspect_avg_word_len_min": 3.5,
    "suspect_avg_word_len_max": 9.0,
}

# সব মান পরিষ্কার text আর হাতে দেখা নষ্ট file মেপে বসানো; মাপগুলো docs/DECISIONS.md D-117 এ
BANGLA_CHECK = {
    "bad_token_ratio": 0.10,        # পরিষ্কার text এ <7%; 16,649 OCR page এর ৩টা 10–15%। হাতে দেখা নষ্ট file এ সর্বনিম্ন 13%
    "isolated_letter_ratio": 0.4,   # পরিষ্কার text (20+ token) এ সর্বোচ্চ 0.25, হাতে দেখা lossy file এ সর্বনিম্ন 0.50
    # text layer এর page: ভুল হলে শুধু একটা block OCR হয়, ক্ষতি সামান্য। অসম্ভব বানানের জন্য এক token ই যথেষ্ট,
    # কারণ অনেক file এ অল্প অল্প নষ্ট বাংলা কয়েক page এ ছড়ানো থাকে (S14: ৩ page এ ১৫ token)
    "min_tokens_layer_bad": 1,
    "min_tokens_layer_lossy": 4,
    "min_tokens_file": 10,          # saved text, পুরো file: এর নিচে OCR noise এই flag করে দেয়
    "min_tokens_page": 20,          # saved text, এক page: বড় OCR file এ অল্প নষ্ট page ধরার জন্য
    "broken_share_of_file": 0.2,    # file এর token এর এত ভাগ নষ্ট বাংলা হলে suspect না, broken
    "junk_ratio": 0.25,
    "junk_min_tokens": 40,
}
RETENTION_CHECK = {
    "min_line_retention": 0.95,     # এর নিচে file flag (ব্যবহারকারীর দেওয়া মান)
    "broken_line_retention": 0.70,  # এর নিচে suspect না, broken — অনুমান
    "line_min_chars": 3,            # এর কম অক্ষরের লাইন গোনা হয় না (page number, '|' জাতীয়)
    "baseline_gap_pt": 6,           # এর কম ফাঁকের span গুলো একই লাইন
}

# দুই ধরনের legacy encoding পাওয়া গেছে: 'h¡wm¡−cn' (¡ ¢ − Ñ …) আর SutonnyMJ/Bijoy 'evsjv‡`k'
# (‡ † ¨ © …) — দুটোই "বাংলাদেশ"। দ্বিতীয়টা দেখতে Latin অক্ষরের মতো, তাই আলাদা চিহ্ন লাগে।
LEGACY_CHARS = "¡¢£¤¥¦§©ª«¬®¯°±²³µ¶·¸¹º»¼½¾¿ÑÐ−" + "‡†ˆ‰Š‹ŒÖ¨"
LEGACY_RE = re.compile("[" + re.escape(LEGACY_CHARS) + "]")
COMMON_SHORT = {
    "a", "i", "of", "to", "in", "is", "it", "be", "by", "on", "or", "an", "as", "at", "no", "so",
    "up", "we", "he", "me", "my", "do", "if", "us", "am", "go", "mr", "ms", "dr", "vs", "sl", "md",
}
ALPHA_RE = re.compile(r"[A-Za-z]+")
TOKEN_RE = re.compile(r"\S+")
BANGLA_RE = re.compile(r"[ঀ-৿]")
LATIN_RE = re.compile(r"[A-Za-z]")
OTHER_LETTER_RE = re.compile(r"[^\W\d_A-Za-zঀ-৿]")
NONPRINT_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f�-]")
# অক্ষর, সংখ্যা, whitespace, বাংলা আর সাধারণ যতিচিহ্ন ছাড়া বাকি সব
ODD_RE = re.compile(r"[^\w\sঀ-৿‌‍.,;:!?()\[\]{}'\"\-–—/\\&%@#*+=<>|`~$’‘“”…।৷]")
SHINGLE = 12

# ─── নষ্ট বাংলা ────────────────────────────────────────────
# PDF এর glyph থেকে Unicode এ ফেরার সময় যা হয়: যুক্তাক্ষর Latin/IPA/Cyrillic অক্ষরে, স্বরচিহ্ন দেখার ক্রমে
# ('আদালেত'), কিছু অক্ষর Unicode এ না থাকা code point এ, কিছু হারিয়ে যায়। অক্ষরগুলো বাংলা block এ থাকে,
# তাই legacy-font check ধরে না। আসল বাংলা বানানে যা অসম্ভব, সেটাই মাপা হয়।
# ("\\u...." raw string না, re নিজেই পড়ে — ফলে source এ অদৃশ্য character থাকে না)
BN_LETTER_RE = re.compile("[\\u0985-\\u09b9\\u09ce\\u09dc-\\u09df\\u09f0\\u09f1]")
BN_SIGN_RE = re.compile("[\\u09be-\\u09c4\\u09c7\\u09c8\\u09cb\\u09cc\\u09cd\\u09d7]")
# বাংলা শব্দের ভেতরে থাকার কথা না: Latin Extended, IPA, combining mark, Greek, Cyrillic
BN_FOREIGN_RE = re.compile("[\\u0180-\\u036f\\u0370-\\u03ff\\u0400-\\u052f\\u1e00-\\u1eff]")
# বাংলা block এ Unicode যেগুলো ফাঁকা রেখেছে
BN_UNASSIGNED_RE = re.compile("[\\u0984\\u098d\\u098e\\u0991\\u0992\\u09a9\\u09b1\\u09b3\\u09b4\\u09b5\\u09ba\\u09bb"
                              "\\u09c5\\u09c6\\u09c9\\u09ca\\u09cf-\\u09d6\\u09d8-\\u09db\\u09de\\u09e4\\u09e5]")
# স্বরচিহ্ন বা হসন্ত ব্যঞ্জনের (বা নুকতার) পরে ছাড়া বসে না
BN_MISPLACED_SIGN_RE = re.compile("(?<![\\u0995-\\u09b9\\u09dc-\\u09df\\u09f0\\u09f1\\u09bc])"
                                  "[\\u09be-\\u09c4\\u09c7\\u09c8\\u09cb\\u09cc\\u09cd\\u09d7]")
BN_ZERO_WIDTH_RE = re.compile("[\\u200c\\u200d]")
# '(ক)', 'খ)' তালিকার চিহ্ন: পরিষ্কার text এও এক অক্ষরের, তাই হারানো অক্ষরের হিসাবে ধরা হয় না
BN_LIST_MARKER_RE = re.compile("^\\(?[\\u0985-\\u09b9]\\)[.,;:]?$")
BN_REAL_ONE_LETTER_WORDS = {"\u0993", "\u098f"}   # ও, এ
JUNK_SYMBOL_RE = re.compile(r"[!#$%&*+<=>?@\\^_`{|}~]")
WORD_SHAPE_RE = re.compile("^[(\\[\"'\\u201c\\u2018]*[A-Za-z\\u0980-\\u09ff][A-Za-z\\u0980-\\u09ff'\\u2019.\\-]*"
                           "[)\\]\"'\\u201d\\u2019.,;:!?\\u0964\\u09f7]*$")
NUMBER_SHAPE_RE = re.compile("^[(\\[]*[\\d\\u09e6-\\u09ef][\\d\\u09e6-\\u09ef.,/\\-:]*[)\\]]*[.,;:]*$")


def bangla_signals(text: str) -> dict:
    """বাংলা অক্ষর আছে এমন token এর মধ্যে কতগুলো বানানে অসম্ভব, আর কতগুলো একা একটা অক্ষর (বাকিটা হারিয়েছে)।
    PDF span শব্দের মাঝখানে ভাঙে, তাই লাইন জোড়া দিয়ে দিতে হবে; span ধরে মাপলে কিছুই ধরা পড়ে না।"""
    text = unicodedata.normalize("NFC", BN_ZERO_WIDTH_RE.sub("", text))
    tokens = [t for t in TOKEN_RE.findall(text) if BN_LETTER_RE.search(t) or BN_SIGN_RE.search(t)]
    foreign = unassigned = misplaced = bad = isolated = 0
    for token in tokens:
        has_foreign = bool(BN_FOREIGN_RE.search(token))
        has_unassigned = bool(BN_UNASSIGNED_RE.search(token))
        has_misplaced = bool(BN_MISPLACED_SIGN_RE.search(token))
        foreign += has_foreign
        unassigned += has_unassigned
        misplaced += has_misplaced
        bad += has_foreign or has_unassigned or has_misplaced
        letters = BN_LETTER_RE.findall(token)
        isolated += (len(letters) == 1 and not BN_SIGN_RE.search(token) and letters[0] not in BN_REAL_ONE_LETTER_WORDS
                     and not BN_LIST_MARKER_RE.match(token))
    return {
        "tokens": len(tokens), "bad": bad, "foreign": foreign, "unassigned": unassigned, "misplaced": misplaced,
        "isolated": isolated,
        "bad_ratio": bad / len(tokens) if tokens else 0.0,
        "isolated_ratio": isolated / len(tokens) if tokens else 0.0,
    }


def bangla_problem(signals: dict, min_tokens: int, min_tokens_lossy: int | None = None) -> str:
    """'' = ঠিক আছে বা বিচার করার মতো বাংলা নেই; নইলে কারণ। min_tokens_lossy না দিলে দুই route এ একই সীমা।"""
    if signals["tokens"] >= min_tokens and signals["bad_ratio"] >= BANGLA_CHECK["bad_token_ratio"]:
        return "impossible_spelling"
    lossy_floor = min_tokens if min_tokens_lossy is None else min_tokens_lossy
    if signals["tokens"] >= lossy_floor and signals["isolated_ratio"] >= BANGLA_CHECK["isolated_letter_ratio"]:
        return "isolated_letters"
    return ""


def junk_stats(text: str) -> tuple[int, int]:
    """(মোট token, শব্দ বা সংখ্যার আকার নেই এমন প্রতীক-ভরা token)"""
    tokens = TOKEN_RE.findall(text)
    junk = sum(1 for t in tokens if not WORD_SHAPE_RE.match(t) and not NUMBER_SHAPE_RE.match(t)
               and (JUNK_SYMBOL_RE.search(t) or len(t) <= 3))
    return len(tokens), junk


def is_junk_text(text: str) -> bool:
    total, junk = junk_stats(text)
    return total >= BANGLA_CHECK["junk_min_tokens"] and junk / total >= BANGLA_CHECK["junk_ratio"]


def text_layer_problem(text: str) -> str:
    """PDF text layer এর এক page বিশ্বাস করা যায় কিনা। extractor এর trigger; ভুলে flag হলে খরচ এক block OCR।"""
    problem = bangla_problem(bangla_signals(text), BANGLA_CHECK["min_tokens_layer_bad"], BANGLA_CHECK["min_tokens_layer_lossy"])
    if problem:
        return problem
    return "junk_tokens" if is_junk_text(text) else ""


def bangla_file_check(text: str, total_tokens: int) -> dict:
    """Saved text এর জন্য: file সব মিলিয়ে নষ্ট, অথবা বড় OCR file এর অল্প কিছু page নষ্ট।"""
    whole = bangla_signals(text)
    problem = bangla_problem(whole, BANGLA_CHECK["min_tokens_file"])
    bad_pages = junk_pages = bad_page_tokens = 0
    for page in text.split("\f"):
        signals = bangla_signals(page)
        page_problem = bangla_problem(signals, BANGLA_CHECK["min_tokens_page"])
        if page_problem:
            bad_pages += 1
            bad_page_tokens += signals["tokens"]
            problem = problem or page_problem
        junk_pages += is_junk_text(page)
    corrupted_tokens = whole["tokens"] if bangla_problem(whole, BANGLA_CHECK["min_tokens_file"]) else bad_page_tokens
    return {
        "bangla_tokens": whole["tokens"],
        "bangla_bad_ratio": round(whole["bad_ratio"], 4),
        "bangla_isolated_ratio": round(whole["isolated_ratio"], 4),
        "corrupted_bangla": problem,
        "corrupted_bangla_pages": bad_pages,
        "corrupted_bangla_share": round(corrupted_tokens / max(1, total_tokens), 4),
        "junk_pages": junk_pages,
    }


# ─── লাইন ধরে রাখা ─────────────────────────────────────────
OUTPUT_LINE_CHAR_RE = re.compile("[\\u0980-\\u09ffA-Za-z0-9]")


def output_lines(text: str) -> int:
    need = RETENTION_CHECK["line_min_chars"]
    return sum(1 for line in text.splitlines() if len(OUTPUT_LINE_CHAR_RE.findall(line)) >= need)


def pdf_lines(blocks: list) -> int:
    """PDF এর এক baseline এ যা আছে তা এক লাইন — mojibake হলেও লাইনের কাঠামো ঠিক থাকে।
    output_lines এর মতোই ছোট লাইন গোনা হয় না, নইলে হর বেশি হয়ে ভুল flag আসে।"""
    items = sorted(((s["bbox"][1] + s["bbox"][3]) / 2, len(s["text"].strip())) for b in blocks for line in b["lines"]
                   for s in line["spans"] if s["text"].strip())
    rows, last = [], None
    for y, chars in items:
        if last is None or y - last > RETENTION_CHECK["baseline_gap_pt"]:
            rows.append(0)
        rows[-1] += chars
        last = y
    return sum(1 for chars in rows if chars >= RETENTION_CHECK["line_min_chars"])


def retention(pdf_count: int, output_count: int) -> float:
    """বেশি লাইন এক page এ থাকলে অন্য page এর ক্ষতি ঢাকতে পারে না — তাই page প্রতি min(), তারপর যোগ।"""
    return min(pdf_count, output_count) / pdf_count if pdf_count else 1.0


def audit_retention(text_dir: Path, pdf_dir: Path, pages_out: Path | None = None, progress=None,
                    measure_new: bool = False) -> dict:
    """প্রতি file: v2 যে page OCR করেছিল সেগুলোতে লাইন কত থাকল, আর নতুন rule এ কোন page এ OCR বাড়বে। PDF
    শুধু পড়া হয়, OCR চলে না। Page ধরে মেলে না (ছড়ানো \\f, D-111) এমন file এ পুরো file মিলিয়ে হিসাব।
    measure_new: নতুন rule এ OCR হওয়া page ও মাপে — text_v3 এর জন্য। v2 এর সাথে একই page এ মেলাতে page CSV join করো।"""
    import pymupdf

    from ingestion.pdf_extract import page_text_blocks, plan_blocks, read_manifest
    manifest = read_manifest(text_dir)
    results = {}
    page_rows = []
    min_retention = RETENTION_CHECK["min_line_retention"]
    for done, name in enumerate(sorted(manifest), 1):
        path = text_dir / (Path(name).stem + ".txt")
        row = dict(ocr_pages=0, no_text_pages=0, pdf_lines=0, kept_lines=0, low_retention_pages=0,
                   retention_level="page", layer_new_ocr_pages=0, layer_new_full_pages=0, layer_new_ocr_blocks=0,
                   layer_reasons="", retention_error="")
        reasons = Counter()
        try:
            parts = path.read_text(encoding="utf-8").split("\f")
            doc = pymupdf.open(pdf_dir / name)
        except Exception as e:
            row["retention_error"] = f"{type(e).__name__}: {e}"[:200]
            results[name] = row
            continue
        try:
            aligned = len(parts) == doc.page_count
            all_pdf = 0
            for i, page in enumerate(doc):
                blocks = page_text_blocks(page)
                old, new = plan_blocks(blocks, check_layer=False), plan_blocks(blocks)
                if new.method != old.method or new.ocr_blocks != old.ocr_blocks:
                    row["layer_new_ocr_pages"] += 1
                    row["layer_new_full_pages"] += new.method.startswith("ocr_page")
                    row["layer_new_ocr_blocks"] += new.ocr_blocks - old.ocr_blocks if not new.method.startswith("ocr_page") else 0
                    reasons[new.layer_problem] += 1
                if not aligned:
                    all_pdf += pdf_lines(blocks)
                measured = new if measure_new else old
                if measured.method == "pymupdf":
                    continue
                if measured.method == "ocr_page_no_text":   # text layer নেই, তাই হরও নেই; আলাদা গোনা
                    row["no_text_pages"] += 1
                    continue
                row["ocr_pages"] += 1
                if not aligned:
                    continue
                n_pdf, n_out = pdf_lines(blocks), output_lines(parts[i])
                kept = min(n_pdf, n_out)
                row["pdf_lines"] += n_pdf
                row["kept_lines"] += kept
                page_retention = retention(n_pdf, n_out)
                row["low_retention_pages"] += page_retention < min_retention
                page_rows.append({"filename": name, "page": i, "method": measured.method, "pdf_lines": n_pdf,
                                  "output_lines": n_out, "line_retention": round(page_retention, 4)})
            if not aligned and (row["ocr_pages"] or row["no_text_pages"]):
                row["retention_level"] = "file"
                row["pdf_lines"] = all_pdf
                row["kept_lines"] = min(all_pdf, sum(output_lines(p) for p in parts))
        except Exception as e:
            row["retention_error"] = f"{type(e).__name__}: {e}"[:200]
        finally:
            doc.close()
        row["layer_reasons"] = ";".join(sorted(k for k in reasons if k))
        row["line_retention"] = round(row["kept_lines"] / row["pdf_lines"], 4) if row["pdf_lines"] else ""
        results[name] = row
        if progress and done % progress == 0:
            print(f"  retention: {done} of {len(manifest)} files", flush=True)
    if pages_out:
        pages_out.parent.mkdir(parents=True, exist_ok=True)
        with pages_out.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=["filename", "page", "method", "pdf_lines", "output_lines", "line_retention"])
            writer.writeheader()
            writer.writerows(page_rows)
    return results


def _repeat_ratio(tokens: list[str]) -> float:
    shingles = [" ".join(tokens[i:i + SHINGLE]) for i in range(0, max(0, len(tokens) - SHINGLE + 1), 4)]
    return 1 - len(set(shingles)) / len(shingles) if shingles else 0.0


def audit_text(text: str) -> dict:
    chars = len(text) or 1
    tokens = TOKEN_RE.findall(text)
    alpha_tokens = ALPHA_RE.findall(text)
    bangla = len(BANGLA_RE.findall(text))
    latin = len(LATIN_RE.findall(text))
    n_letters = (bangla + latin + len(OTHER_LETTER_RE.findall(text))) or 1
    legacy = len(LEGACY_RE.findall(text))
    fragments = sum(1 for t in alpha_tokens if len(t) <= 2 and t.lower() not in COMMON_SHORT)

    row = {
        "chars": len(text),
        "words": len(tokens),
        "nonprintable_ratio": round(len(NONPRINT_RE.findall(text)) / chars, 5),
        "bangla_ratio": round(bangla / n_letters, 4),
        "latin_ratio": round(latin / n_letters, 4),
        "legacy_font_ratio": round(legacy / n_letters, 4),
        "avg_word_len": round(sum(map(len, alpha_tokens)) / (len(alpha_tokens) or 1), 2),
        "split_fragment_ratio": round(fragments / (len(alpha_tokens) or 1), 4),
        "garbage_score": round(len(ODD_RE.findall(text)) / chars + legacy / n_letters, 4),
        "repeat_ratio": round(_repeat_ratio(tokens), 4),
        "near_empty": len(tokens) < THRESHOLDS["min_words"],
    }
    row.update(bangla_file_check(text, len(tokens)))
    row["bucket_old"], row["reasons_old"] = classify(row)
    row["bucket"], row["reasons"] = strict_classify(row)
    return row


def classify(row: dict) -> tuple[str, str]:
    t = THRESHOLDS
    broken = []
    if row["near_empty"]:
        broken.append("near_empty")
    if row["legacy_font_ratio"] > t["broken_legacy_font_ratio"]:
        broken.append("legacy_font")
    if row["nonprintable_ratio"] > t["broken_nonprintable_ratio"]:
        broken.append("nonprintable")
    if row["garbage_score"] > t["broken_garbage_score"]:
        broken.append("garbage")
    if row["repeat_ratio"] > t["broken_repeat_ratio"]:
        broken.append("repeated_text")
    if broken:
        return "broken", ";".join(broken)

    suspect = []
    if row["garbage_score"] > t["suspect_garbage_score"]:
        suspect.append("garbage")
    if row["split_fragment_ratio"] > t["suspect_split_fragment_ratio"]:
        suspect.append("split_words")
    if row["latin_ratio"] > 0.5 and not t["suspect_avg_word_len_min"] <= row["avg_word_len"] <= t["suspect_avg_word_len_max"]:
        suspect.append("word_length")
    if suspect:
        return "suspect", ";".join(suspect)
    return "good", ""


def strict_classify(row: dict) -> tuple[str, str]:
    """classify() এর উপরে নতুন check। পুরনো rule আলাদা রাখা হয়, যাতে আগের v1/v2 সংখ্যার সাথে মেলানো যায়।"""
    bucket, old_reasons = classify(row)
    broken, suspect = [], []
    if row.get("corrupted_bangla"):
        heavy = row.get("corrupted_bangla_share", 0) >= BANGLA_CHECK["broken_share_of_file"]
        (broken if heavy else suspect).append("corrupted_bangla")
    if row.get("junk_pages"):
        suspect.append("junk_text")
    kept = row.get("line_retention")
    if kept not in (None, "") and kept < RETENTION_CHECK["min_line_retention"]:
        (broken if kept < RETENTION_CHECK["broken_line_retention"] else suspect).append("low_line_retention")
    reasons = [r for r in old_reasons.split(";") if r] + broken + suspect
    if broken:
        bucket = "broken"
    elif suspect and bucket == "good":
        bucket = "suspect"
    return bucket, ";".join(reasons)


def load_texts(source: Path):
    """judgements.json (list of {filename, text}) অথবা .txt file এর folder।"""
    if source.is_dir():
        for path in sorted(source.glob("*.txt")):
            yield path.stem + ".pdf", path.read_text(encoding="utf-8", errors="replace")
    else:
        for item in json.loads(source.read_text(encoding="utf-8")):
            yield item["filename"], item["text"]


def summarize_retention(rows: list[dict]) -> dict:
    measured = [r for r in rows if r.get("line_retention") not in (None, "")]
    limit = RETENTION_CHECK["min_line_retention"]
    return {
        "files_measured": len(measured),
        "files_below": {str(t): sum(r["line_retention"] < t for r in measured) for t in (0.95, 0.9, 0.85, 0.7)},
        "ocr_pages": sum(r.get("ocr_pages", 0) for r in rows),
        "no_text_pages_no_baseline": sum(r.get("no_text_pages", 0) for r in rows),
        "low_retention_pages": sum(r.get("low_retention_pages", 0) for r in rows),
        "pdf_lines": sum(r.get("pdf_lines", 0) for r in measured),
        "kept_lines": sum(r.get("kept_lines", 0) for r in measured),
        "flagged_by_line_shortfall": dict(sorted(Counter(
            "1-2" if s <= 2 else "3-9" if s < 10 else "10-99" if s < 100 else "100+"
            for s in (r["pdf_lines"] - r["kept_lines"] for r in measured if r["line_retention"] < limit)).items())),
        "file_level_only": sum(r.get("retention_level") == "file" for r in rows),
        "errors": sum(bool(r.get("retention_error")) for r in rows),
        "layer_new_ocr_files": sum(r.get("layer_new_ocr_pages", 0) > 0 for r in rows),
        "layer_new_ocr_pages": sum(r.get("layer_new_ocr_pages", 0) for r in rows),
        "layer_new_full_pages": sum(r.get("layer_new_full_pages", 0) for r in rows),
        "layer_new_ocr_blocks": sum(r.get("layer_new_ocr_blocks", 0) for r in rows),
    }


def summarize(rows: list[dict]) -> dict:
    n = len(rows) or 1

    def count(key):
        c = Counter(r[key] for r in rows)
        return {b: {"count": c[b], "percent": round(c[b] / n * 100, 1)} for b in ("good", "suspect", "broken")}

    reasons = Counter(reason for r in rows for reason in r["reasons"].split(";") if reason)
    return {
        "files": len(rows),
        "buckets": count("bucket"),
        "buckets_old_rules": count("bucket_old"),
        "reasons": dict(reasons.most_common()),
        "corrupted_bangla_files": sum(bool(r["corrupted_bangla"]) for r in rows),
        "junk_text_files": sum(r["junk_pages"] > 0 for r in rows),
        "thresholds": THRESHOLDS,
        "bangla_check": BANGLA_CHECK,
        "retention_check": RETENTION_CHECK,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=str(ROOT / "judgements.json"), help="judgements.json বা .txt folder")
    ap.add_argument("--out", default=str(ROOT / "data_quality" / "audit.csv"))
    ap.add_argument("--pdf-dir", default=None, help="দিলে OCR করা page এ লাইন ধরে রাখার হিসাব করে (--source folder হতে হবে)")
    ap.add_argument("--measure-new-ocr-pages", action="store_true", help="নতুন rule এ OCR হওয়া page ও মাপো (text_v3)")
    args = ap.parse_args()

    out = Path(args.out)
    retention_rows = {}
    if args.pdf_dir:
        retention_rows = audit_retention(Path(args.source), Path(args.pdf_dir),
                                         out.with_name(out.stem + "_pages.csv"), progress=500,
                                         measure_new=args.measure_new_ocr_pages)
    rows = []
    for filename, text in load_texts(Path(args.source)):
        row = audit_text(text)
        if filename in retention_rows:
            row.update(retention_rows[filename])
            row["bucket"], row["reasons"] = strict_classify(row)
        rows.append({"filename": filename, **row})
    # retention কলাম সব সারিতে যেন থাকে: যে file manifest এ নেই সেটা খালি যাবে
    fieldnames = list(dict.fromkeys(k for r in rows for k in r))

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, restval="")
        writer.writeheader()
        writer.writerows(rows)

    summary = summarize(rows)
    summary["source"] = str(Path(args.source).name)
    if retention_rows:
        summary["retention"] = summarize_retention(rows)
    out.with_name(out.stem + "_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"{summary['files']} files ({summary['source']})")
    for bucket, v in summary["buckets"].items():
        old = summary["buckets_old_rules"][bucket]
        print(f"  {bucket:8} {v['count']:6}  {v['percent']:5}%   (old rules {old['count']}, {old['percent']}%)")
    print(f"  reasons: {summary['reasons']}")
    if retention_rows:
        print(f"  retention: {summary['retention']}")
    print(f"→ {out}, {out.with_name(out.stem + '_summary.json')}")


if __name__ == "__main__":
    main()
