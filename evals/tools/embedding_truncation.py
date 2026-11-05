"""
evals/tools/embedding_truncation.py
===================================
Embedding model প্রতিটা chunk এর কতটা পড়ে? MiniLM ১২৮ token এ কাটে, chunk ৬০০ শব্দের। কোনো model চালায় না, শুধু tokenizer
দিয়ে গোনে, তাই খরচ নেই। ফল evals/reports/embedding_truncation.json এ। শুধু পড়ে।

    python evals/tools/embedding_truncation.py --db evals/cache/chroma_eval_copy
"""

import argparse
import json
import random
import statistics
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
LIMIT = 128
SPECIAL_TOKENS = 2  # শুরু আর শেষের চিহ্ন


def words_read(offsets: list[tuple[int, int]], text: str, limit: int) -> int:
    """কাটার আগে কত শব্দ model পায়।"""
    kept = [o for o in offsets if o[1] > o[0]][: limit - SPECIAL_TOKENS]
    return len(text[: kept[-1][1]].split()) if kept else 0


def summarize(rows: list[dict]) -> dict:
    return {"chunks": len(rows), "median_tokens": statistics.median(r["tokens"] for r in rows), "median_words": statistics.median(r["words"] for r in rows), "median_words_read": statistics.median(r["words_read"] for r in rows),
            "share_truncated": round(sum(r["tokens"] > LIMIT for r in rows) / len(rows), 3), "median_share_of_words_read": round(statistics.median(r["words_read"] / max(r["words"], 1) for r in rows), 3)}


def main() -> None:
    import chromadb
    from transformers import AutoTokenizer

    from evals.pipeline_env import guard_db_path
    from evals.tools.corpus import doc_script
    from retrieval.hybrid import ChunkStore

    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "evals" / "cache" / "chroma_eval_copy"))
    ap.add_argument("--sample", type=int, default=1500)
    ap.add_argument("--out", default=str(ROOT / "evals" / "reports" / "embedding_truncation.json"))
    args = ap.parse_args()
    tokenizer = AutoTokenizer.from_pretrained(MODEL, local_files_only=True)
    store = ChunkStore.from_collection(chromadb.PersistentClient(path=str(guard_db_path(Path(args.db)))).get_collection("bangladesh_judgements_v3"))
    picked = random.Random(20261105).sample(range(len(store.docs)), args.sample)
    rows = []
    for i in picked:
        text = store.docs[i]
        encoded = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True, truncation=False, verbose=False)
        rows.append({"script": doc_script(text), "tokens": len(encoded["input_ids"]), "words": len(text.split()), "words_read": words_read(encoded["offset_mapping"], text, LIMIT)})
    report = {"model": MODEL, "max_seq_length": LIMIT, "seed": 20261105, "all": summarize(rows)}
    for script in ("en", "mixed", "bn"):
        sub = [r for r in rows if r["script"] == script]
        if sub:
            report[script] = summarize(sub)
    Path(args.out).write_bytes((json.dumps(report, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
