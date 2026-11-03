"""
clean_metadata.py
=================
enrich_judgements.py এর regex label গুলো পরিষ্কার করে:

  1. PDF extraction এ ভাঙা ইংরেজি শব্দ জোড়া লাগায় ('o f' → 'of', 'appella nt' → 'appellant',
     'V E R S U S' → 'VERSUS') — corpus এর নিজস্ব শব্দ-গণনা দিয়ে, hard-coded list ছাড়া।
  2. Judgement এর header থেকে আসল case number বের করে ('Criminal Appeal No. 6777 of 2023')
     আর সেটা থেকে case_type — filename এ প্রায়ই শুধু internal code থাকে ('TN 72663 2023')।
  3. Legacy Bijoy/SutonnyMJ font এ লেখা বাংলা judgement চিহ্নিত করে (PyPDF2 এ এগুলো
     'q¡C−L¡VÑ' ধরনের mojibake হয়ে যায়) — convert করে না, শুধু text_quality flag দেয়।
  4. --subjects: LLM দিয়ে subject label, keyword match এর বদলে।
  5. --apply: বাংলা আইনের section_no/year ('১৷' → '1') laws collection এ update করে।

    python clean_metadata.py                 # 1-3 + report (LLM call ছাড়া; আগের subject cache থাকলে সেটা)
    python clean_metadata.py --subjects --model gemini-3.1-flash-lite
                                             # + বাকি judgement এর LLM subject label (cache থেকে resume)
    python clean_metadata.py --apply         # + laws metadata Chroma তে লেখে (server বন্ধ রেখে)
    python enrich_judgements.py              # তারপর judgements collection নতুন text/labels দিয়ে rebuild

Output: judgements_clean.json, judgement_labels.json, evals/reports/metadata_report.json
"""

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

from enrich_judgements import extract_case_no, extract_case_type, extract_subject_keywords

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()

# ─── CONFIG ───────────────────────────────────────────────
JUDGEMENTS_FILE = "./judgements.json"
CLEAN_FILE      = "./judgements_clean.json"
LABELS_FILE     = "./judgement_labels.json"
SUBJECT_CACHE   = "./judgement_subjects.jsonl"
REPORT_FILE     = "./evals/reports/metadata_report.json"
CHROMA_DB_PATH  = os.getenv("CHROMA_DB_PATH", "./legal_chroma_db")
GEMINI_MODEL    = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
SUBJECT_BATCH   = 20
# Gemini free tier: প্রতি model এ 15 request/min — তার নিচে থাকতে একটা worker, call এর মাঝে বিরতি।
# Groq fallback নেই: free tier এ 20-judgement batch "Request too large" (413) দেয়।
SUBJECT_MIN_INTERVAL_S = 5.5
# ──────────────────────────────────────────────────────────


# ─── 1. OCR SPLIT WORDS ───────────────────────────────────

WORD_RE = re.compile(r"[A-Za-z]+")
LETTER_SPACED_RE = re.compile(r"\b(?:[A-Za-z] ){3,}[A-Za-z]\b")
MIN_JOINED_COUNT = 20     # জোড়া লাগানো শব্দটা corpus এ অন্তত এতবার পুরো আকারে থাকতে হবে
JOIN_RATIO       = 5      # আর ভাঙা জোড়ার চেয়ে অন্তত এত গুণ বেশি
COMMON_WORD      = 2000   # দুই অংশই এর বেশি বার আলাদা শব্দ হিসেবে থাকলে জোড়া লাগাই না ('a cross')


def build_vocab(texts) -> Counter:
    vocab = Counter()
    for t in texts:
        vocab.update(w.lower() for w in WORD_RE.findall(t))
    return vocab


def _adjacent_pairs(text):
    words = list(WORD_RE.finditer(text))
    for a, b in zip(words, words[1:]):
        if b.start() - a.end() == 1 and text[a.end()] == " ":
            yield a, b


def find_joinable_pairs(texts, vocab: Counter) -> set:
    """কোন (a, b) জোড়া আসলে একটা ভাঙা শব্দ — joined রূপটা corpus এ অনেক বেশি থাকলে,
    আর দুটো অংশই আলাদা করে সাধারণ শব্দ না হলে।"""
    pair_counts = Counter()
    for t in texts:
        for a, b in _adjacent_pairs(t):
            joined = (a.group() + b.group()).lower()
            if vocab[joined] >= MIN_JOINED_COUNT:
                pair_counts[(a.group().lower(), b.group().lower())] += 1

    def is_word(w):
        return w in ("a", "i") or (len(w) > 1 and vocab[w] >= COMMON_WORD)

    return {
        (a, b) for (a, b), n in pair_counts.items()
        if len(a + b) >= 3 and vocab[a + b] >= JOIN_RATIO * n and not (is_word(a) and is_word(b))
    }


def fix_ocr_text(text: str, vocab: Counter, joins: set) -> tuple[str, int]:
    fixes = 0

    def unspace(m):
        nonlocal fixes
        joined = m.group().replace(" ", "")
        if vocab[joined.lower()] >= MIN_JOINED_COUNT:
            fixes += 1
            return joined
        return m.group()

    text = LETTER_SPACED_RE.sub(unspace, text)

    out, last, skip_until = [], 0, -1
    for a, b in _adjacent_pairs(text):
        if a.start() < skip_until:
            continue
        if (a.group().lower(), b.group().lower()) in joins:
            out.append(text[last:a.end()])
            last = b.start()          # মাঝের space টা বাদ
            skip_until = b.end()
            fixes += 1
    out.append(text[last:])
    return "".join(out), fixes


# ─── 2. CASE NUMBER / CASE TYPE ───────────────────────────

CASE_NO_RE = re.compile(
    r"((?:Criminal|Crl|Cril|Civil|Writ|First|Second|Company|Admiralty|Contempt|Death|Jail|"
    r"Income\s*Tax|Customs|Arbitration|Suo|Review|Transfer|Election)"
    r"[A-Za-z.\s]{0,45}?(?:No|Nos)\.?\s*[\d][\d,\s\-–&and]*\s*of\s*\d{2,4})",
    re.IGNORECASE,
)

# ক্রম গুরুত্বপূর্ণ — বেশি নির্দিষ্টটা আগে (death reference কে criminal appeal ধরা যাবে না)।
# শুধু case number label এ চালানো হয় (_norm_label এর পরে) — ছোট abbreviation ('c r', 'a s')
# পুরো header এ চালালে এলোমেলো text এর সাথে মিলে যায়।
CASE_TYPE_PATTERNS = [
    ("death_reference",      r"death\s*ref|\bderef|\bd\s*r\b"),
    ("jail_appeal",          r"jail\s*app"),
    ("criminal_appeal",      r"\b(?:criminal|crl|cril|cr)\s*(?:appeal|apl|appl|app|a)\b"),
    ("criminal_revision",    r"\b(?:criminal|crl|cril|cr)\s*rev"),
    ("criminal_misc",        r"\b(?:criminal|crl|cril|cr)\s*mis|\b561\s*a\b|\b498\b"),
    ("contempt_petition",    r"contempt"),
    ("review_petition",      r"review"),
    ("writ_petition",        r"writ|\bw\s*p\b|\bwps\b"),
    ("civil_revision",       r"civil\s*rev|\bc\s*r\b"),
    ("civil_rule",           r"civil\s*rule|\bc\s*rule"),
    ("first_misc_appeal",    r"first\s*misc|\bf\s*m\s*a\b"),
    ("first_appeal",         r"first\s*app|\bf\s*a\b"),
    ("second_appeal",        r"second\s*app|\bs\s*a\b"),
    ("civil_misc",           r"civil\s*misc"),
    ("company_matter",       r"company\s*matter|\bc\s*m\b"),
    ("admiralty_suit",       r"admiralty|\ba\s*s\b"),
    ("income_tax_reference", r"income\s*tax|\bi\s*t\s*r\b"),
    ("customs_vat_appeal",   r"customs|\bvat\b|excise"),
    ("arbitration",          r"arbitration"),
    ("election_petition",    r"election"),
    ("trademark",            r"trade\s*mark"),
    ("suo_moto",             r"suo\s*mo"),
]
CASE_TYPE_RES = [(t, re.compile(p, re.IGNORECASE)) for t, p in CASE_TYPE_PATTERNS]

# case number না মিললে header text এ — শুধু পুরো শব্দ। Legacy-font bail/561A order গুলোর
# header এ file path থাকে ('Rule_&_Bail_2024', '498_Motion', '561A_&_etc_Matter')।
HEAD_TYPE_PATTERNS = [
    ("death_reference",   r"death\s*reference"),
    ("criminal_misc",     r"criminal\s*misc|bail|498|561\s*a"),
    ("criminal_appeal",   r"criminal\s*appeal"),
    ("criminal_revision", r"criminal\s*revision"),
    ("writ_petition",     r"writ\s*petition"),
    ("civil_revision",    r"civil\s*revision"),
    ("first_appeal",      r"first\s*appeal"),
    ("company_matter",    r"company\s*matter"),
    ("admiralty_suit",    r"admiralty\s*suit"),
]
HEAD_TYPE_RES = [(t, re.compile(p, re.IGNORECASE)) for t, p in HEAD_TYPE_PATTERNS]

# তাও না মিললে header এর jurisdiction লাইন থেকে
JURISDICTION_TYPES = [
    ("criminal_misc",     r"CRIMINAL\s+MISC"),
    ("criminal_revision", r"CRIMINAL\s+REVISIONAL"),
    ("criminal_appeal",   r"CRIMINAL\s+APPELLATE"),
    ("civil_revision",    r"CIVIL\s+REVISIONAL"),
    ("first_appeal",      r"CIVIL\s+APPELLATE"),
    ("writ_petition",     r"SPECIAL\s+ORIGINAL"),
    ("company_matter",    r"STATUTORY\s+ORIGINAL"),
    ("admiralty_suit",    r"ADMIRALTY"),
]
JURISDICTION_RES = [(t, re.compile(p)) for t, p in JURISDICTION_TYPES]


def _norm_label(label: str) -> str:
    """'CriminalAppealNo.8095of2019' → 'criminal appeal no 8095 of 2019', 'fano26' → 'fa no 26'।"""
    s = re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", label)   # 'VATAppeal' → 'VAT Appeal'
    s = re.sub(r"[_.\-,()]", " ", s.lower())
    s = re.sub(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])", " ", s)
    s = re.sub(r"(?<=[a-z])no\b", " no", s)
    return re.sub(r"\s+", " ", s).strip()


def _first_match(text: str, patterns) -> str | None:
    for case_type, rx in patterns:
        if rx.search(text):
            return case_type
    return None


def derive_case(filename: str, text: str) -> tuple[str, str, str]:
    """(case_no, case_type, source) — header এর case number সবচেয়ে বিশ্বাসযোগ্য,
    তারপর filename, তারপর header এর পুরো-শব্দ match, তারপর jurisdiction লাইন।"""
    head = text[:1500]
    m = CASE_NO_RE.search(head)
    if m:
        case_no = re.sub(r"\s+", " ", m.group(1)).strip(" .,")
        case_type = _first_match(_norm_label(case_no), CASE_TYPE_RES)
        if case_type:
            return case_no, case_type, "header"

    file_case_no = extract_case_no(filename)
    case_type = _first_match(_norm_label(file_case_no), CASE_TYPE_RES)
    if case_type:
        return file_case_no, case_type, "filename"
    case_type = _first_match(head, HEAD_TYPE_RES) or _first_match(head.upper(), JURISDICTION_RES)
    if case_type:
        return file_case_no, case_type, "header_text"
    return file_case_no, "other", "none"


# ─── 3. LEGACY BANGLA FONT ────────────────────────────────

LEGACY_CHARS = set("¡¢£¤¥¦§©ª«¬®¯°±²³µ¶·¸¹º»¼½¾¿ÑÐ−")


def text_quality(text: str) -> str:
    sample = text[:3000]
    letters = sum(c.isalpha() for c in sample) or 1
    legacy = sum(c in LEGACY_CHARS for c in sample)
    return "legacy_bangla_font" if legacy / letters > 0.02 else "ok"


# ─── 4. LLM SUBJECT LABELS ────────────────────────────────

SUBJECT_LABELS = [
    "land_property", "inheritance_succession", "family_matrimonial", "tenancy_rent",
    "contract_commercial", "cheque_dishonour", "banking_loan_recovery", "company_corporate",
    "tax_customs_vat", "service_employment", "labour", "constitutional_writ", "election",
    "murder_homicide", "violence_women_children", "narcotics", "arms_explosives",
    "corruption_money_laundering", "fraud_cheating_theft", "bail", "quashing_proceedings",
    "criminal_procedure_other", "civil_procedure", "admiralty_shipping", "intellectual_property",
    "environment", "other",
]

SUBJECT_PROMPT = f"""You label Bangladesh High Court Division judgements by legal subject.
Allowed labels: {", ".join(SUBJECT_LABELS)}
For each judgement return 1-3 labels from the allowed list, most specific first.
Some excerpts contain garbled legacy-font Bangla — use the readable English parts
(section numbers, Act names) to decide. Use "other" only if nothing else fits.
Return ONLY a JSON object mapping every id to its label list, e.g. {{"0": ["bail", "murder_homicide"]}}."""

_gemini = None


def _call_llm(user_content: str, model: str) -> str:
    global _gemini
    from google import genai
    from google.genai import types
    _gemini = _gemini or genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    r = _gemini.models.generate_content(
        model=model, contents=user_content,
        config=types.GenerateContentConfig(
            system_instruction=SUBJECT_PROMPT, response_mime_type="application/json", temperature=0,
        ),
    )
    return r.text


_WS_RE = re.compile(r"\s+")


def _classify_batch(batch: list[dict], model: str) -> dict:
    content = "\n\n".join(
        f"[{i}] {d['case_no']} ({d['case_type']})\n{_WS_RE.sub(' ', d['text'][:1800])}"
        for i, d in enumerate(batch)
    )
    for attempt in range(4):
        try:
            raw = json.loads(_call_llm(content, model))
            out = {}
            for i, d in enumerate(batch):
                labels = [l for l in (raw.get(str(i)) or []) if l in SUBJECT_LABELS][:3]
                if labels:
                    out[d["filename"]] = labels
            return out
        except Exception as e:
            wait = 30 * (attempt + 1)   # 429 per-minute quota মিনিট শেষে ছাড়ে
            print(f"   ⚠️ batch error ({str(e)[:100]}), {wait}s পরে আবার")
            time.sleep(wait)
    return {}


def load_subject_cache() -> dict:
    cache = {}
    if Path(SUBJECT_CACHE).exists():
        for line in Path(SUBJECT_CACHE).read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            cache[row["filename"]] = row["subjects"]
    return cache


def label_subjects(docs: list[dict], model: str) -> dict:
    cache = load_subject_cache()
    todo = [d for d in docs if d["filename"] not in cache]
    batches = [todo[i:i + SUBJECT_BATCH] for i in range(0, len(todo), SUBJECT_BATCH)]
    print(f"🏷️  Subjects ({model}): {len(cache)} cached, {len(todo)} বাকি — {len(batches)} batch")

    with open(SUBJECT_CACHE, "a", encoding="utf-8") as f:
        for done, batch in enumerate(batches, 1):
            started = time.time()
            for filename, labels in _classify_batch(batch, model).items():
                cache[filename] = labels
                f.write(json.dumps({"filename": filename, "subjects": labels}, ensure_ascii=False) + "\n")
            f.flush()
            if done % 20 == 0 or done == len(batches):
                print(f"   {done}/{len(batches)} batches, {len(cache)} labelled", flush=True)
            time.sleep(max(0.0, SUBJECT_MIN_INTERVAL_S - (time.time() - started)))
    return cache


# ─── 5. LAWS METADATA (বাংলা আইন) ─────────────────────────

BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
LAW_SECTION_NO_RE = re.compile(r"^\s*([0-9০-৯]+[A-Za-z]?)\s*[.।৷]")
YEAR_RE = re.compile(r"\b(1[5-9]\d{2}|20\d{2})\b")


def fix_laws_metadata(apply: bool) -> dict:
    import chromadb
    col = chromadb.PersistentClient(path=CHROMA_DB_PATH).get_collection("bangladesh_laws")
    total, ids, metas = col.count(), [], []
    section_fixed = year_fixed = 0
    for offset in range(0, total, 500):
        batch = col.get(limit=500, offset=offset, include=["documents", "metadatas"])
        for doc_id, doc, meta in zip(batch["ids"], batch["documents"], batch["metadatas"]):
            new = dict(meta)
            if not meta.get("section_no"):
                body = doc.split("\n", 1)[1] if "\n" in doc else doc
                m = LAW_SECTION_NO_RE.match(body)
                if m:
                    new["section_no"] = m.group(1).translate(BN_DIGITS)
                    section_fixed += 1
            if not meta.get("year"):
                m = YEAR_RE.search((meta.get("pub_date") or "").translate(BN_DIGITS))
                if m:
                    new["year"] = int(m.group(1))
                    year_fixed += 1
            if new != meta:
                ids.append(doc_id)
                metas.append(new)
    if apply:
        for i in range(0, len(ids), 500):
            col.update(ids=ids[i:i + 500], metadatas=metas[i:i + 500])
    print(f"📜 Laws: section_no ঠিক হলো {section_fixed}, year ঠিক হলো {year_fixed}"
          f"{' (Chroma তে লেখা হয়েছে)' if apply else ' (dry run — --apply দিলে লেখা হবে)'}")
    return {"section_no_fixed": section_fixed, "year_fixed": year_fixed, "applied": apply}


# ─── MAIN ─────────────────────────────────────────────────

def _dist(counter: Counter) -> dict:
    return dict(counter.most_common())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--subjects", action="store_true", help="বাকি judgement গুলো LLM দিয়ে label (না দিলে শুধু cache)")
    ap.add_argument("--model", default=GEMINI_MODEL,
                    help="subject labeling এর Gemini model — API যে model চালায় তার থেকে আলাদা দিলে quota ভাগ হয় না")
    ap.add_argument("--apply", action="store_true", help="laws metadata Chroma তে লেখো")
    args = ap.parse_args()

    print("📂 judgements.json load করছি...")
    judgements = json.loads(Path(JUDGEMENTS_FILE).read_text(encoding="utf-8"))
    texts = [j["text"] for j in judgements]

    # legacy-font judgement এর 'ইংরেজি' আসলে mojibake — ওগুলো vocabulary আর fix দুটো থেকেই বাদ
    quality = [text_quality(t) for t in texts]
    ok_texts = [t for t, q in zip(texts, quality) if q == "ok"]
    print("🔤 OCR split words: vocabulary + জোড়া খুঁজছি...")
    vocab = build_vocab(ok_texts)
    joins = find_joinable_pairs(ok_texts, vocab)
    print(f"   {len(joins)} ধরনের ভাঙা জোড়া পাওয়া গেছে")

    docs, join_examples = [], Counter()
    total_fixes = docs_fixed = 0
    for j, q in zip(judgements, quality):
        fixed, n = fix_ocr_text(j["text"], vocab, joins) if q == "ok" else (j["text"], 0)
        total_fixes += n
        docs_fixed += bool(n)
        case_no, case_type, source = derive_case(j["filename"], fixed)
        docs.append({
            "filename": j["filename"], "text": fixed, "case_no": case_no, "case_type": case_type,
            "case_source": source, "text_quality": q,
            "case_type_before": extract_case_type(j["filename"]),
            "subjects_before": extract_subject_keywords(j["text"]).split(","),
        })
    for t in ok_texts[:500]:
        for a, b in _adjacent_pairs(t):
            if (a.group().lower(), b.group().lower()) in joins:
                join_examples[f"{a.group()} {b.group()}"] += 1

    subjects = label_subjects(docs, args.model) if args.subjects else load_subject_cache()

    labels = {
        d["filename"]: {
            "case_no": d["case_no"], "case_type": d["case_type"], "text_quality": d["text_quality"],
            **({"subjects": subjects[d["filename"]]} if d["filename"] in subjects else {}),
        }
        for d in docs
    }
    Path(LABELS_FILE).write_text(json.dumps(labels, ensure_ascii=False), encoding="utf-8")
    Path(CLEAN_FILE).write_text(
        json.dumps([{"filename": d["filename"], "text": d["text"], "word_count": len(d["text"].split())}
                    for d in docs], ensure_ascii=False),
        encoding="utf-8",
    )

    report = {
        "judgements": len(docs),
        "ocr": {"docs_fixed": docs_fixed, "total_fixes": total_fixes, "distinct_joins": len(joins),
                "examples": [k for k, _ in join_examples.most_common(25)]},
        "text_quality": _dist(Counter(d["text_quality"] for d in docs)),
        "case_no_source": _dist(Counter(d["case_source"] for d in docs)),
        "case_type_before": _dist(Counter(d["case_type_before"] for d in docs)),
        "case_type_after": _dist(Counter(d["case_type"] for d in docs)),
        "subjects_before": _dist(Counter(s for d in docs for s in d["subjects_before"])),
        "subjects_after": _dist(Counter(s for labels_ in subjects.values() for s in labels_)),
        "subjects_labelled": len(subjects),
        "laws": fix_laws_metadata(args.apply),
    }
    Path(REPORT_FILE).parent.mkdir(parents=True, exist_ok=True)
    Path(REPORT_FILE).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n✅ OCR: {docs_fixed} টা judgement এ {total_fixes} টা fix — যেমন {report['ocr']['examples'][:10]}")
    print(f"   text_quality: {report['text_quality']}")
    print(f"   case_no source: {report['case_no_source']}")
    for key in ("case_type", "subjects"):
        before, after = report[f"{key}_before"], report[f"{key}_after"]
        print(f"\n{key:24} {'before':>8} {'after':>8}")
        for label in sorted(set(before) | set(after), key=lambda k: -(after.get(k, 0) or before.get(k, 0))):
            print(f"  {label:22} {before.get(label, 0):>8} {after.get(label, 0):>8}")
    print(f"\n💾 {LABELS_FILE}, {CLEAN_FILE}, {REPORT_FILE}")


if __name__ == "__main__":
    main()
