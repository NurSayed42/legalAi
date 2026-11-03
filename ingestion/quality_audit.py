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

  python ingestion/quality_audit.py                                   # judgements.json (v1)
  python ingestion/quality_audit.py --source text_v2 --out data_quality/audit_v2.csv

Output: <out>.csv (প্রতি file এক সারি) আর <out>_summary.json (bucket অনুযায়ী count/percent)।
Bucket threshold গুলো নিচের THRESHOLDS এ — অনুমান থেকে বসানো, docs/DECISIONS.md এ লেখা।
"""

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent

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
    row["bucket"], row["reasons"] = classify(row)
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


def load_texts(source: Path):
    """judgements.json (list of {filename, text}) অথবা .txt file এর folder।"""
    if source.is_dir():
        for path in sorted(source.glob("*.txt")):
            yield path.stem + ".pdf", path.read_text(encoding="utf-8", errors="replace")
    else:
        for item in json.loads(source.read_text(encoding="utf-8")):
            yield item["filename"], item["text"]


def summarize(rows: list[dict]) -> dict:
    n = len(rows) or 1
    buckets = Counter(r["bucket"] for r in rows)
    reasons = Counter(reason for r in rows for reason in r["reasons"].split(";") if reason)
    return {
        "files": len(rows),
        "buckets": {b: {"count": buckets[b], "percent": round(buckets[b] / n * 100, 1)}
                    for b in ("good", "suspect", "broken")},
        "reasons": dict(reasons.most_common()),
        "thresholds": THRESHOLDS,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=str(ROOT / "judgements.json"), help="judgements.json বা .txt folder")
    ap.add_argument("--out", default=str(ROOT / "data_quality" / "audit.csv"))
    args = ap.parse_args()

    rows = []
    for filename, text in load_texts(Path(args.source)):
        rows.append({"filename": filename, **audit_text(text)})

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    summary = summarize(rows)
    summary["source"] = str(Path(args.source).name)
    out.with_name(out.stem + "_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"{summary['files']} files ({summary['source']})")
    for bucket, v in summary["buckets"].items():
        print(f"  {bucket:8} {v['count']:6}  {v['percent']:5}%")
    print(f"  reasons: {summary['reasons']}")
    print(f"→ {out}, {out.with_name(out.stem + '_summary.json')}")


if __name__ == "__main__":
    main()
