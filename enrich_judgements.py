"""
enrich_judgements.py
====================
judgements.json থেকে rich metadata extract করো,
তারপর ChromaDB তে re-index করো।

Metadata per judgement:
  filename, case_no, year, division, case_type,
  outcome, judges (partial), subject_keywords, word_count

    python enrich_judgements.py                                   # cleaned text/labels থাকলে সেগুলো
    python enrich_judgements.py --text raw --labels regex --allow-replace   # আসল v1 baseline index
    python enrich_judgements.py --text v2 --labels clean --collection bangladesh_judgements_v2
    python enrich_judgements.py --text v3 --labels clean --collection bangladesh_judgements_v3

--text v3 এ বিশাল মামলার chunk সীমা লাগে (ingestion/chunk_cap.py, docs/DECISIONS.md D-120); v1/v2 আগের মতোই।

Collection টা "<name>__building" এ তৈরি হয়, শেষ হলে তবেই পুরনোটার জায়গা নেয় — মাঝপথে বন্ধ
হলে চালু index নষ্ট হয় না, আবার চালালে যেখানে থেমেছিল সেখান থেকে চলে। আগে থেকে থাকা collection
--allow-replace ছাড়া বদলায় না (default নাম v1 এর, ভুল command এ baseline মুছে যেতে পারত)।
"""

import argparse
import csv
import json
import os
import re

import chromadb
from chromadb.utils import embedding_functions
from dotenv import load_dotenv
from tqdm import tqdm

from ingestion.chunk_cap import CAP_CONFIG, plan_index

load_dotenv()

# ─── CONFIG ───────────────────────────────────────────────
JUDGEMENTS_FILE = "./judgements.json"
# clean_metadata.py চালানো থাকলে এগুলো ব্যবহার হয় — OCR-fixed text আর header থেকে আসা
# case_no/case_type, LLM subject label, text_quality
CLEAN_FILE      = "./judgements_clean.json"
LABELS_FILE     = "./judgement_labels.json"
# ingestion/pdf_extract.py এর re-extracted text (Phase 1) আর তার audit
TEXT_DIRS       = {"v2": "./text_v2", "v3": "./text_v3"}
AUDIT_FILES     = {"v2": "./data_quality/audit_v2.csv", "v3": "./data_quality/audit_v3_strict.csv"}
CAP_REPORT      = "./data_quality/v3_index_plan.json"
CHROMA_DB_PATH  = os.getenv("CHROMA_DB_PATH", "./legal_chroma_db")   # api_v3.py এর সাথে একই path
COLLECTION_NAME = "bangladesh_judgements"
EMBED_MODEL     = "paraphrase-multilingual-MiniLM-L12-v2"
CHUNK_SIZE      = 600   # words per chunk (reduced for better precision)
CHUNK_OVERLAP   = 80
BATCH_SIZE      = 100
# ──────────────────────────────────────────────────────────


# ─── METADATA EXTRACTORS ──────────────────────────────────

def extract_year(filename: str, text: str) -> int:
    """Filename অথবা text থেকে year বের করো।"""
    # Filename থেকে (সবচেয়ে reliable)
    years = re.findall(r"[_\-\.](\d{4})[_\-\.]", filename)
    for y in years:
        y = int(y)
        if 1950 <= y <= 2025:
            return y

    # Text এর প্রথম 500 char থেকে
    m = re.search(r"\b(19[5-9]\d|20[0-2]\d)\b", text[:500])
    if m:
        return int(m.group(1))

    return 0


def extract_case_type(filename: str) -> str:
    fn = filename.upper()
    if "WRIT" in fn or "W.P" in fn or re.search(r"\bWP\b", fn):
        return "writ_petition"
    elif re.search(r"CRL[_\.]?APP|CRL[_\.]?A[_\.]", fn):
        return "criminal_appeal"
    elif re.search(r"CRL[_\.]?REV|CRL[_\.]?MISC|CRIM", fn):
        return "criminal_revision"
    elif "DEREF" in fn or "DEATH" in fn:
        return "death_reference"
    elif re.search(r"\bF\.?A[_\.]", fn):
        return "first_appeal"
    elif re.search(r"\bCR[_\.]|CIVIL[_\.]?REV|C[_\.]R[_\.]", fn):
        return "civil_revision"
    elif "SUOMOTO" in fn or "SUO" in fn:
        return "suo_moto"
    else:
        return "other"


def extract_outcome(filename: str, text: str) -> str:
    """
    Outcome hierarchy:
      filename (most reliable) → last 800 chars of text → unknown
    """
    fn = filename.upper()

    # Filename signals
    if "ABSOLUTE" in fn:
        return "rule_absolute"     # Petitioner জিতেছে
    if "DISCHARGED" in fn:
        return "rule_discharged"   # Petitioner হেরেছে
    if "DISMISSED" in fn:
        return "dismissed"
    if "DISPOSED" in fn:
        return "disposed"

    # Text tail
    tail = text[-800:].lower()
    if "rule is made absolute" in tail or "rule absolute" in tail:
        return "rule_absolute"
    if "rule is discharged" in tail or "rule discharged" in tail:
        return "rule_discharged"
    if "appeal is allowed" in tail or "revision is allowed" in tail or \
       "petition is allowed" in tail:
        return "allowed"
    if "appeal is dismissed" in tail or "revision is dismissed" in tail or \
       "petition is dismissed" in tail:
        return "dismissed"
    if "disposed of" in tail:
        return "disposed"

    return "unknown"


def extract_division(text: str) -> str:
    """High Court Division vs Appellate Division"""
    head = text[:400].upper()
    if "APPELLATE DIVISION" in head:
        return "AD"
    if "HIGH COURT DIVISION" in head:
        return "HCD"
    return "HCD"   # default — majority are HCD


def extract_judges(text: str) -> str:
    """Justice নামগুলো বের করো (first 600 chars)"""
    head = text[:600]
    judges = re.findall(r"Justice\s+([A-Z][a-z]+(?:\s+[A-Z][a-z\.]+){1,4})", head)
    return ", ".join(judges[:3]) if judges else ""


def extract_subject_keywords(text: str) -> str:
    """
    Subject matter keywords — LLM ছাড়াই rule-based।
    Legal domain specific।
    """
    text_lower = text.lower()
    subjects = []

    keyword_map = {
        "land_dispute":     ["land", "property", "khatian", "mutation", "deed", "plot"],
        "criminal":         ["murder", "assault", "robbery", "theft", "rape", "arms"],
        "contract":         ["contract", "agreement", "breach", "damages", "negotiable"],
        "family":           ["divorce", "dower", "maintenance", "custody", "marriage"],
        "writ":             ["fundamental right", "mandamus", "certiorari", "habeas corpus"],
        "service":          ["service", "appointment", "promotion", "dismissal", "government employee"],
        "tax":              ["tax", "revenue", "customs", "vat", "income tax"],
        "tenancy":          ["tenant", "landlord", "rent", "eviction", "lease"],
        "company":          ["company", "director", "shareholder", "winding up"],
        "cheque_dishonour": ["cheque", "dishonour", "negotiable instruments", "n.i. act"],
    }

    for subject, keywords in keyword_map.items():
        if any(k in text_lower for k in keywords):
            subjects.append(subject)

    return ",".join(subjects[:4]) if subjects else "general"


def extract_case_no(filename: str) -> str:
    """Human-readable case number।"""
    name = filename.replace(".pdf", "").replace("_", " ")
    # ID prefix সরাও (leading digits)
    name = re.sub(r"^\d+\s+", "", name)
    # Outcome suffix সরাও
    name = re.sub(r"\s+(ABSOLUTE|DISCHARGED|DISMISSED|DISPOSED OF|PENDING APPEAL)$",
                  "", name, flags=re.IGNORECASE)
    return name.strip()


# ─── CHUNKER ──────────────────────────────────────────────

def chunk_text(text: str, chunk_size: int = CHUNK_SIZE,
               overlap: int = CHUNK_OVERLAP):
    words = text.split()
    chunks = []
    start = 0
    idx = 0
    while start < len(words):
        end = start + chunk_size
        chunk = " ".join(words[start:end])
        chunks.append((idx, chunk))
        idx += 1
        start = end - overlap
    return chunks


# ─── MAIN ─────────────────────────────────────────────────

def write_cap_report(plan: dict, path: str = CAP_REPORT) -> None:
    report = {k: v for k, v in plan.items() if k != "keep"}
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
        f.write("\n")


def build_judgements_collection(text_source: str = "auto", label_source: str = "auto",
                                collection_name: str = COLLECTION_NAME, allow_replace: bool = False,
                                max_chunks_per_case: int | None = None):
    client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
    existing = [c if isinstance(c, str) else c.name for c in client.list_collections()]
    if collection_name in existing and not allow_replace:
        raise SystemExit(f"{collection_name} আগে থেকেই আছে — বদলাতে চাইলে --allow-replace দিন")

    if text_source == "auto":
        text_source = "clean" if os.path.exists(CLEAN_FILE) else "raw"
    if label_source == "auto":
        label_source = "clean" if os.path.exists(LABELS_FILE) else "regex"
    if text_source in TEXT_DIRS:
        text_dir = TEXT_DIRS[text_source]
        print(f"📂 {text_dir}/*.txt load করছি...")
        judgements = [
            {"filename": name[:-4] + ".pdf", "text": open(os.path.join(text_dir, name), encoding="utf-8").read()}
            for name in sorted(os.listdir(text_dir)) if name.endswith(".txt")
        ]
    else:
        source = CLEAN_FILE if text_source == "clean" else JUDGEMENTS_FILE
        print(f"📂 {source} load করছি...")
        with open(source, encoding="utf-8") as f:
            judgements = json.load(f)
    labels = {}
    if label_source == "clean":
        with open(LABELS_FILE, encoding="utf-8") as f:
            labels = json.load(f)
        print(f"🏷️  {LABELS_FILE} থেকে {len(labels)} টা judgement এর cleaned label")
    # v2/v3 text এর quality নিজের audit থেকে — v1 label এর 'legacy_bangla_font' OCR এর পরে আর সত্য না
    v2_quality = {}
    if text_source in AUDIT_FILES and os.path.exists(AUDIT_FILES[text_source]):
        with open(AUDIT_FILES[text_source], encoding="utf-8") as f:
            v2_quality = {row["filename"]: row["bucket"] for row in csv.DictReader(f)}
    cap_active = text_source == "v3"
    plan = None
    if cap_active:
        cap = CAP_CONFIG["max_chunks_per_case"] if max_chunks_per_case is None else max_chunks_per_case
        plan = plan_index({case["filename"]: len(chunk_text(case["text"])) for case in judgements}, cap)
        write_cap_report(plan)
        print(f"✂️  সীমা {cap} chunk/মামলা: text এ {plan['chunks_from_text']} chunk, index এ {plan['chunks_indexed']}; {plan['capped_cases']}")
    capped_files = {name for case in (plan["capped_cases"].values() if plan else []) for name in case["files"]}
    print(f"✅ {len(judgements)} টা judgement পেলাম → {collection_name} (text={text_source}, labels={label_source})\n")

    print("🤖 Embedding model load করছি...")
    embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=EMBED_MODEL
    )

    building_name = f"{collection_name}__building"
    collection = client.get_or_create_collection(
        name=building_name,
        embedding_function=embedding_fn,
        metadata={"hnsw:space": "cosine"},
    )

    all_docs, all_ids, all_metas = [], [], []

    print("⚙️  Metadata extraction + chunking শুরু করছি...")
    for case in tqdm(judgements, desc="Processing"):
        filename = case["filename"]
        text     = case["text"]

        # ── Extract metadata ──
        label    = labels.get(filename, {})
        year     = extract_year(filename, text)
        case_type = label.get("case_type") or extract_case_type(filename)
        outcome  = extract_outcome(filename, text)
        division = extract_division(text)
        judges   = extract_judges(text)
        subjects = ",".join(label["subjects"]) if label.get("subjects") else extract_subject_keywords(text)
        case_no  = label.get("case_no") or extract_case_no(filename)

        base_meta = {
            "filename":    filename,
            "case_no":     case_no,
            "year":        year,
            "division":    division,
            "case_type":   case_type,
            "outcome":     outcome,
            "judges":      judges,
            "subjects":    subjects,
            "text_quality": v2_quality.get(filename) or label.get("text_quality", "ok"),
            "source_type": "judgement",
        }

        # ── Chunk ──
        kept = set(plan["keep"][filename]) if plan else None
        for chunk_idx, chunk_text_str in chunk_text(text):
            if kept is not None and chunk_idx not in kept:
                continue
            doc_id = f"{filename}__chunk_{chunk_idx}"
            meta = {**base_meta, "chunk_index": chunk_idx}
            if cap_active:
                meta["case_capped"] = filename in capped_files

            all_docs.append(chunk_text_str)
            all_ids.append(doc_id)
            all_metas.append(meta)

    del judgements, labels, v2_quality   # 7.7 GB RAM এ embedding এর আগে পুরো corpus এর দ্বিতীয় copy ছেড়ে দাও
    print(f"\n📊 মোট {len(all_docs)} টা chunk তৈরি হলো")

    # আগের অসম্পূর্ণ run এর chunk গুলো আবার embed না করে সেখান থেকে চালাও (একই ক্রমে add হয়)
    resume_from = collection.count() // BATCH_SIZE * BATCH_SIZE
    if resume_from:
        # text বদলে গেলে ক্রমও বদলায়, তখন count দিয়ে resume করলে chunk বাদ পড়ত
        probe = all_ids[resume_from - 1] if resume_from <= len(all_ids) else None
        if probe is None or not collection.get(ids=[probe], include=[])["ids"]:
            raise SystemExit(f"{building_name} এর chunk এই text এর সাথে মেলে না — আগের build অন্য text থেকে; "
                             f"collection টা দেখে নিয়ে সিদ্ধান্ত নিন")
        print(f"↩️  {building_name} এ আগের run এর {resume_from} টা chunk আছে — সেখান থেকে চলছে")

    print("\n💾 ChromaDB তে store করছি...")
    for i in tqdm(range(resume_from, len(all_docs), BATCH_SIZE), desc="Storing"):
        collection.upsert(
            documents=all_docs[i:i + BATCH_SIZE],
            ids=all_ids[i:i + BATCH_SIZE],
            metadatas=all_metas[i:i + BATCH_SIZE],
        )

    # সম্পূর্ণ হলে তবেই পুরনো collection এর জায়গা নেয়
    try:
        client.delete_collection(collection_name)
        print(f"🗑️  পুরনো {collection_name} delete করলাম")
    except Exception:
        pass
    collection.modify(name=collection_name)
    print(f"\n✅ {collection_name} ready! {collection.count()} টা chunk stored।")

    # Quick stats
    print("\n📈 Outcome distribution:")
    outcomes = {}
    for m in all_metas[::10]:   # sample
        o = m["outcome"]
        outcomes[o] = outcomes.get(o, 0) + 1
    for k, v in sorted(outcomes.items(), key=lambda x: -x[1]):
        print(f"   {k}: ~{v*10}")

    return collection


def test_search(collection, query: str, filters: dict = None):
    print(f"\n🔍 Test: '{query}'")
    kwargs = {"query_texts": [query], "n_results": 3}
    if filters:
        kwargs["where"] = filters
    results = collection.query(**kwargs)

    for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
        print(f"\n  📄 {meta['case_no']}")
        print(f"     Year: {meta['year']} | Type: {meta['case_type']} | "
              f"Division: {meta['division']}")
        print(f"     Outcome: {meta['outcome']} | Subjects: {meta['subjects']}")
        print(f"     Preview: {doc[:200]}...")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", choices=["auto", "raw", "clean", "v2", "v3"], default="auto",
                    help="raw = judgements.json, clean = clean_metadata.py এর judgements_clean.json, "
                         "v2 = ingestion/pdf_extract.py এর text_v2/, v3 = ingestion/build_text_v3.py এর text_v3/")
    ap.add_argument("--max-chunks-per-case", type=int, default=None,
                    help="শুধু --text v3: ingestion/chunk_cap.py এর CAP_CONFIG এর সীমা বদলাতে")
    ap.add_argument("--labels", choices=["auto", "regex", "clean"], default="auto",
                    help="regex = এই file এর extractor, clean = judgement_labels.json")
    ap.add_argument("--collection", default=COLLECTION_NAME)
    ap.add_argument("--allow-replace", action="store_true", help="আগে থেকে থাকা collection প্রতিস্থাপন করতে দাও")
    ap.add_argument("--skip-tests", action="store_true")
    args = ap.parse_args()

    col = build_judgements_collection(args.text, args.labels, args.collection, args.allow_replace, args.max_chunks_per_case)
    if args.skip_tests:
        raise SystemExit(0)

    print("\n" + "=" * 60)
    print("🧪 Search tests:")
    test_search(col, "জমি দখল মামলায় বাদী কি পেয়েছে")
    test_search(col, "murder death sentence appeal",
                filters={"case_type": {"$eq": "criminal_appeal"}})
    test_search(col, "cheque dishonour NI Act conviction")
    test_search(col, "writ fundamental rights government service")
