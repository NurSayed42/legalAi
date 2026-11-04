"""
evals/cap_simulation.py
=======================
বিশাল মামলার সীমা কত হবে, প্রতিবার ৭০ মিনিটের build ছাড়া (docs/DECISIONS.md D-120)। v2 এর আসল vector পড়ে
নিয়ে, প্রতিটা সীমায় যে chunk গুলো index এ থাকত শুধু সেগুলোর উপর exact cosine search করে। DB তে কিছু লেখে না।

    python evals/cap_simulation.py --out data_quality/cap_simulation.json

দুই দিকেই মাপে: অন্য বিষয়ের প্রশ্নে এই মামলা কতটা ভরে ফেলে, আর এই মামলার নিজের বিষয়ের প্রশ্নে এটা এখনো পাওয়া যায় কিনা।
সমান ফাঁকের শুরু (phase) বদলে কয়েকবার চালায়, কারণ কোন chunk পড়ল তার উপর ফল অনেকটা নির্ভর করে।

সীমাবদ্ধতা: এগুলো v2 এর vector। v3 এ Death Ref 58 এর text বেড়েছে, তাই বাছাই করা সীমা আসল v3 এও মাপতে হবে।
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evals"))

from retrieval_smoke import EMBED_MODEL, QUERIES_BN, QUERIES_EN, score_results, totals  # noqa: E402

from ingestion.chunk_cap import plan_index  # noqa: E402

CAPS = [None, 2000, 1500, 1000, 750, 500, 400, 300, 200, 100]
PHASES = [0.0, 0.2, 0.4, 0.6, 0.8]
# Death Ref 58 এর নিজের বিষয়: সীমা বাছাইয়ের ফল দেখার আগে ঠিক করা
OWN_TOPIC = [
    "পিলখানা বিদ্রোহ বিডিআর হত্যাকাণ্ড",
    "বিডিআর সদর দপ্তরে সেনা কর্মকর্তাদের হত্যা",
    "দরবার হলে বিডিআর জওয়ানদের বিদ্রোহ",
    "২৫ ফেব্রুয়ারি ২০০৯ পিলখানা হত্যা মামলার রায়",
    "Pilkhana BDR mutiny killing of army officers death reference",
    "BDR carnage at Darbar Hall Pilkhana 2009",
]


def load_collection(db: str, name: str) -> tuple[list[str], list[str], list[str], np.ndarray]:
    import chromadb
    col = chromadb.PersistentClient(path=db).get_collection(name)
    ids, docs, files, vectors = [], [], [], []
    for offset in range(0, col.count(), 2000):
        batch = col.get(limit=2000, offset=offset, include=["embeddings", "documents", "metadatas"])
        ids += batch["ids"]
        docs += batch["documents"]
        files += [m["filename"] for m in batch["metadatas"]]
        vectors.append(np.asarray(batch["embeddings"], dtype="float32"))
    matrix = np.vstack(vectors)
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
    return ids, docs, files, matrix


def simulate(ids, docs, files, matrix, queries: list[str], vectors: np.ndarray, cap: int | None, phase: float = 0.0) -> list[dict]:
    counts: dict[str, int] = {}
    for f in files:
        counts[f] = counts.get(f, 0) + 1
    keep = plan_index(counts, cap, phase=phase)["keep"]
    keep_ids = {f"{f}__chunk_{i}" for f, idx in keep.items() for i in idx}
    mask = np.fromiter((x in keep_ids for x in ids), dtype=bool, count=len(ids))
    positions = np.flatnonzero(mask)
    sims = matrix[positions] @ vectors.T
    rows = []
    for j, q in enumerate(queries):
        top = positions[np.argsort(-sims[:, j])[:10]]
        row = score_results([docs[i] for i in top], [files[i] for i in top])
        row["query"] = q
        rows.append(row)
    return rows


def main() -> None:
    from sentence_transformers import SentenceTransformer

    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "legal_chroma_db"))
    ap.add_argument("--collection", default="bangladesh_judgements_v2")
    ap.add_argument("--out", default=str(ROOT / "data_quality" / "cap_simulation.json"))
    args = ap.parse_args()

    ids, docs, files, matrix = load_collection(args.db, args.collection)
    print(f"{args.collection}: {len(ids)} vectors", flush=True)
    model = SentenceTransformer(EMBED_MODEL)
    report = {"collection": args.collection, "phases": PHASES, "own_topic_queries": OWN_TOPIC,
              "note": "exact cosine search on v2 vectors with chunks removed; nothing written to the DB", "caps": {}}
    swamp_queries = [q for q in QUERIES_BN if q not in OWN_TOPIC]
    groups = {"bangla_other_topics": swamp_queries, "english": QUERIES_EN, "own_topic": OWN_TOPIC}
    vectors = {name: model.encode(queries, normalize_embeddings=True) for name, queries in groups.items()}
    print(f"{'cap':>5} | other Bangla topics: Death Ref slots of {len(swamp_queries) * 10}, worst one case, distinct docs | own topic: queries finding it of {len(OWN_TOPIC)}, slots | English: Death Ref slots")
    for cap in CAPS:
        entry = report["caps"].setdefault(str(cap), {"runs": []})
        for phase in ([0.0] if cap is None else PHASES):
            run = {"phase": phase}
            for name, queries in groups.items():
                rows = simulate(ids, docs, files, matrix, queries, vectors[name], cap, phase)
                run[name] = {**totals(rows), "found_by": sum(r["death_ref_58_chunks"] > 0 for r in rows),
                             "per_query": [r["death_ref_58_chunks"] for r in rows]}
            entry["runs"].append(run)

        def span(group: str, key: str) -> str:
            values = [r[group][key] for r in entry["runs"]]
            return f"{min(values)}–{max(values)}" if min(values) != max(values) else str(values[0])

        print(f"{str(cap):>5} | slots {span('bangla_other_topics', 'death_ref_58_slots'):>7}, worst {span('bangla_other_topics', 'worst_single_case'):>5}, distinct {span('bangla_other_topics', 'distinct_documents'):>7} "
              f"| found by {span('own_topic', 'found_by'):>5}, slots {span('own_topic', 'death_ref_58_slots'):>7} | English slots {span('english', 'death_ref_58_slots'):>4}", flush=True)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
