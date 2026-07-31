"""
build_laws_vectordb.py
======================
bangladesh_laws.json থেকে ChromaDB তে laws index করো।
Section-level granularity — প্রতিটা section আলাদা document।
"""

import json
import re
import sys
from tqdm import tqdm
import chromadb
from chromadb.utils import embedding_functions

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# ─── CONFIG ───────────────────────────────────────────────
LAWS_FILE        = "./bangladesh_laws.json"
CHROMA_DB_PATH   = "./legal_chroma_db"
COLLECTION_NAME  = "bangladesh_laws"
EMBED_MODEL      = "paraphrase-multilingual-MiniLM-L12-v2"
BATCH_SIZE       = 100
MIN_TEXT_LEN     = 50   # এর চেয়ে ছোট section skip করো
# ──────────────────────────────────────────────────────────


def extract_year(pub_date: str) -> int | None:
    """'11th September, 1836' → 1836"""
    m = re.search(r"\b(1[5-9]\d{2}|20\d{2})\b", pub_date)
    return int(m.group(1)) if m else None


SECTION_NO_RE = re.compile(r"^\s*(\d+[A-Za-z]?)\.")


def extract_section_no(sec_text: str) -> str:
    """scraper section title তে number থাকে না (শুধু heading text, যেমন
    'Punishment for murder') — আসল number টা body text এর শুরুতে থাকে
    ('302. Whoever commits murder...')। Exact-section-lookup এর জন্য এইটা
    আলাদা metadata হিসেবে বের করে রাখা দরকার।"""
    m = SECTION_NO_RE.match(sec_text)
    return m.group(1) if m else ""


def classify_act(title: str) -> str:
    """আইনের ধরন বের করো title থেকে।"""
    t = title.lower()
    if any(k in t for k in ["penal", "criminal", "offence", "punishment"]):
        return "criminal"
    elif any(k in t for k in ["land", "property", "tenant", "rent", "registration"]):
        return "property"
    elif any(k in t for k in ["family", "marriage", "divorce", "succession", "inheritance"]):
        return "family"
    elif any(k in t for k in ["contract", "sale", "trade", "commerce", "company"]):
        return "commercial"
    elif any(k in t for k in ["civil procedure", "evidence", "court", "limitation"]):
        return "procedural"
    elif any(k in t for k in ["tax", "revenue", "customs", "excise", "vat"]):
        return "tax"
    elif any(k in t for k in ["labour", "worker", "employment", "factory"]):
        return "labour"
    elif any(k in t for k in ["constitution", "election", "government", "administration"]):
        return "constitutional"
    else:
        return "general"


def build_laws_collection():
    print("📂 bangladesh_laws.json load করছি...")
    with open(LAWS_FILE, encoding="utf-8") as f:
        laws = json.load(f)
    print(f"✅ {len(laws)} টা আইন পেলাম\n")

    print("🤖 Embedding model load করছি...")
    embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=EMBED_MODEL
    )

    client = chromadb.PersistentClient(path=CHROMA_DB_PATH)

    # পুরনো collection থাকলে delete করো
    try:
        client.delete_collection(COLLECTION_NAME)
        print("🗑️  পুরনো laws collection delete করলাম")
    except Exception:
        pass

    collection = client.create_collection(
        name=COLLECTION_NAME,
        embedding_function=embedding_fn,
        metadata={"hnsw:space": "cosine"},
    )

    all_docs, all_ids, all_metas = [], [], []
    skipped = 0
    doc_counter = 0

    for law in laws:
        act_id    = law["id"]
        title     = law["title"]
        url       = law["url"]
        pub_date  = law.get("pub_date", "")
        year      = extract_year(pub_date)
        act_type  = classify_act(title)
        sections  = law.get("sections", [])

        # Section নেই → full text fallback
        if not sections:
            sections = [{"section": "Full Text", "text": " ".join(
                law.get("footnotes", [])
            )}]

        for sec in sections:
            sec_title = sec.get("section", "").strip()
            sec_text  = sec.get("text", "").strip()

            if len(sec_text) < MIN_TEXT_LEN:
                skipped += 1
                continue

            # Embedding text — title + section name + content
            embed_text = f"{title} | {sec_title}\n{sec_text}"

            doc_id = f"law_{act_id}_sec_{doc_counter}"
            doc_counter += 1

            all_docs.append(embed_text)
            all_ids.append(doc_id)
            all_metas.append({
                "act_id":       act_id,
                "title":        title,
                "section":      sec_title,
                "section_no":   extract_section_no(sec_text),
                "url":          url,
                "pub_date":     pub_date,
                "year":         year or 0,
                "act_type":     act_type,
                "source_type":  "legislation",
            })

    print(f"\n📊 মোট {len(all_docs)} টা section chunk তৈরি হলো")
    print(f"   ⏭️  Skip হলো: {skipped} (too short)")

    # Batch করে store করো
    print("\n⚙️  ChromaDB তে store করছি...")
    for i in tqdm(range(0, len(all_docs), BATCH_SIZE), desc="Laws indexing"):
        batch_end = i + BATCH_SIZE
        collection.add(
            documents=all_docs[i:batch_end],
            ids=all_ids[i:batch_end],
            metadatas=all_metas[i:batch_end],
        )

    print(f"\n✅ Laws collection ready! {collection.count()} টা section stored।")
    print(f"💾 Path: {CHROMA_DB_PATH}/{COLLECTION_NAME}")
    return collection


def test_laws_search(collection, query: str):
    print(f"\n🔍 Test: '{query}'")
    results = collection.query(
        query_texts=[query],
        n_results=3,
    )
    for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
        print(f"\n  📜 {meta['title']} — {meta['section']}")
        print(f"     Type: {meta['act_type']} | Year: {meta['year']}")
        print(f"     URL:  {meta['url']}")
        print(f"     Preview: {doc[:200]}...")


if __name__ == "__main__":
    col = build_laws_collection()

    print("\n" + "=" * 60)
    print("🧪 Search tests:")
    test_laws_search(col, "murder punishment death penalty")
    test_laws_search(col, "জমি নিবন্ধন বিক্রয়")
    test_laws_search(col, "contract breach damages")
    test_laws_search(col, "divorce khula marriage dissolution")
