"""
enrich_judgements.py
====================
judgements.json থেকে rich metadata extract করো,
তারপর ChromaDB তে re-index করো।

Metadata per judgement:
  filename, case_no, year, division, case_type,
  outcome, judges (partial), subject_keywords, word_count
"""

import json
import re
from tqdm import tqdm
import chromadb
from chromadb.utils import embedding_functions

# ─── CONFIG ───────────────────────────────────────────────
JUDGEMENTS_FILE = "./judgements.json"
CHROMA_DB_PATH  = "./legal_chroma_db"
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

def build_judgements_collection():
    print("📂 judgements.json load করছি...")
    with open(JUDGEMENTS_FILE, encoding="utf-8") as f:
        judgements = json.load(f)
    print(f"✅ {len(judgements)} টা judgement পেলাম\n")

    print("🤖 Embedding model load করছি...")
    embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=EMBED_MODEL
    )

    client = chromadb.PersistentClient(path=CHROMA_DB_PATH)

    try:
        client.delete_collection(COLLECTION_NAME)
        print("🗑️  পুরনো judgements collection delete করলাম")
    except Exception:
        pass

    collection = client.create_collection(
        name=COLLECTION_NAME,
        embedding_function=embedding_fn,
        metadata={"hnsw:space": "cosine"},
    )

    all_docs, all_ids, all_metas = [], [], []

    print("⚙️  Metadata extraction + chunking শুরু করছি...")
    for case in tqdm(judgements, desc="Processing"):
        filename = case["filename"]
        text     = case["text"]

        # ── Extract metadata ──
        year     = extract_year(filename, text)
        case_type = extract_case_type(filename)
        outcome  = extract_outcome(filename, text)
        division = extract_division(text)
        judges   = extract_judges(text)
        subjects = extract_subject_keywords(text)
        case_no  = extract_case_no(filename)

        base_meta = {
            "filename":    filename,
            "case_no":     case_no,
            "year":        year,
            "division":    division,
            "case_type":   case_type,
            "outcome":     outcome,
            "judges":      judges,
            "subjects":    subjects,
            "source_type": "judgement",
        }

        # ── Chunk ──
        for chunk_idx, chunk_text_str in chunk_text(text):
            doc_id = f"{filename}__chunk_{chunk_idx}"
            meta = {**base_meta, "chunk_index": chunk_idx}

            all_docs.append(chunk_text_str)
            all_ids.append(doc_id)
            all_metas.append(meta)

    print(f"\n📊 মোট {len(all_docs)} টা chunk তৈরি হলো")

    print("\n💾 ChromaDB তে store করছি...")
    for i in tqdm(range(0, len(all_docs), BATCH_SIZE), desc="Storing"):
        collection.add(
            documents=all_docs[i:i + BATCH_SIZE],
            ids=all_ids[i:i + BATCH_SIZE],
            metadatas=all_metas[i:i + BATCH_SIZE],
        )

    print(f"\n✅ Judgements collection ready! {collection.count()} টা chunk stored।")

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
    col = build_judgements_collection()

    print("\n" + "=" * 60)
    print("🧪 Search tests:")
    test_search(col, "জমি দখল মামলায় বাদী কি পেয়েছে")
    test_search(col, "murder death sentence appeal",
                filters={"case_type": {"$eq": "criminal_appeal"}})
    test_search(col, "cheque dishonour NI Act conviction")
    test_search(col, "writ fundamental rights government service")
