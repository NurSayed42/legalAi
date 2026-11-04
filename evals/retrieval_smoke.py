"""
evals/retrieval_smoke.py
========================
v1 আর v2 judgements collection এ একই প্রশ্ন, শুধু vector search (API, BM25, reranker বা LLM ছাড়া)। Read-only।

    python evals/retrieval_smoke.py
"""
import collections
import json
import re
import sys

import chromadb
from chromadb.utils import embedding_functions

sys.stdout.reconfigure(encoding="utf-8")
ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name="paraphrase-multilingual-MiniLM-L12-v2")
client = chromadb.PersistentClient(path="./legal_chroma_db")
cols = {name: client.get_collection(name, embedding_function=ef) for name in ("bangladesh_judgements", "bangladesh_judgements_v2")}

QUERIES = [
    "dishonour of cheque Negotiable Instruments Act section 138 conviction",
    "bail in a murder case section 302 Penal Code",
    "writ petition government service dismissal Article 102",
    "suit for recovery of possession of land Specific Relief Act section 9",
    "dower and maintenance after talaq Muslim Family Laws Ordinance",
    "চেক ডিসঅনার মামলায় দণ্ডের বিরুদ্ধে রিভিশন",
    "খুনের মামলায় আসামির জামিন আবেদন",
    "জমি দখল নিয়ে দেওয়ানী মামলা",
    "নারী ও শিশু নির্যাতন দমন আইনে জামিন",
    "পিলখানা বিদ্রোহ বিডিআর হত্যাকাণ্ড",
]
LEGACY = re.compile(r"[¡¢£¤¥¦§«¬®¯°±²³µ¶·¸¹º»¼½¾¿ÑÐ−‡†ˆ‰Š‹ŒÖ¨]")
BN = re.compile(r"[ঀ-৿]")
DEATHREF = "769454_"
summary = collections.defaultdict(lambda: collections.Counter())
for q in QUERIES:
    print(f"\n### {q}")
    is_bangla = bool(BN.search(q))
    for label, col in cols.items():
        r = col.query(query_texts=[q], n_results=10, include=["documents", "metadatas", "distances"])
        docs, metas, dists = r["documents"][0], r["metadatas"][0], r["distances"][0]
        readable = 0
        for d in docs:
            letters = sum(c.isalpha() for c in d) or 1
            if len(LEGACY.findall(d)) / letters < 0.02:
                readable += 1
        same_case = sum(m["filename"].startswith(DEATHREF) for m in metas)
        distinct = len({m["filename"] for m in metas})
        bangla_hits = sum(1 for d in docs if len(BN.findall(d)) > 50)
        short = "v1" if label == "bangladesh_judgements" else "v2"
        summary[short]["queries"] += 1
        summary[short]["readable"] += readable
        summary[short]["deathref"] += same_case
        summary[short]["distinct"] += distinct
        summary[short]["best_sim_x1000"] += int((1 - dists[0]) * 1000)
        if is_bangla:
            summary[short]["bn_queries"] += 1
            summary[short]["bn_hits_with_bangla_text"] += bangla_hits
        print(f" {short}: best sim {1 - dists[0]:.3f} | readable {readable}/10 | contains Bangla {bangla_hits}/10 | distinct docs {distinct} | Death Ref 58 chunks {same_case}/10")
        for m, d, dist in list(zip(metas, docs, dists))[:2]:
            snippet = " ".join(d.split())[:110]
            print(f"     {1 - dist:.3f} {m['case_no'][:38]:38} [{m['case_type']}] {snippet}")
print("\n=== totals over", len(QUERIES), "queries, top 10 each")
print(json.dumps({k: dict(v) for k, v in summary.items()}, ensure_ascii=False))
