"""
evals/tools/build_embedding_index.py
====================================
Phase 3 এ অন্য embedding model পরীক্ষার জন্য v3 এর chunk এর vector বানায়, আলাদা folder এ numpy file হিসেবে (D-209)। ChromaDB
তে কিছু লেখে না, আর production DB এর ভেতরে লিখতে দেয় না। থেমে গেলে আবার চালালে আগের batch আর হিসাব করে না।

ব্যয়বহুল: model download আর CPU তে কয়েক ঘণ্টা। তাই --run ছাড়া শুধু পরিকল্পনা দেখায়, আর --run চলে labels প্রস্তুত হলে
(evals/run_experiment.py --status)।

    python evals/tools/build_embedding_index.py --model bge-m3            # শুধু পরিকল্পনা
    python evals/tools/build_embedding_index.py --model bge-m3 --limit 200 --run   # সময় মাপা
"""

import argparse
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

PRODUCTION_DB = ROOT / "legal_chroma_db"
OUT_ROOT = ROOT / "evals" / "cache" / "embeddings"
# Prefix model এর নিয়ম: e5 "query: " আর "passage: " চায়
MODELS = {
    "bge-m3": {"hf": "BAAI/bge-m3", "query_prefix": "", "passage_prefix": "", "max_seq_length": 512},
    "e5-large": {"hf": "intfloat/multilingual-e5-large", "query_prefix": "query: ", "passage_prefix": "passage: ", "max_seq_length": 512},
    "minilm": {"hf": "paraphrase-multilingual-MiniLM-L12-v2", "query_prefix": "", "passage_prefix": "", "max_seq_length": 128},
}


def guard_out(out: Path) -> Path:
    resolved = out.resolve()
    prod = PRODUCTION_DB.resolve()
    if resolved == prod or prod in resolved.parents:
        raise PermissionError(f"refusing to write inside the production database: {resolved}")
    return resolved


def build_vectors(encode: Callable[[list[str]], "object"], ids: list[str], docs: list[str], out: Path, prefix: str = "", batch_size: int = 64) -> dict:
    """প্রতিটা batch আলাদা file এ। যে batch আছে তা বাদ, তাই মাঝপথে থামলে ক্ষতি নেই।"""
    import numpy as np

    out = guard_out(out)
    out.mkdir(parents=True, exist_ok=True)
    manifest = out / "ids.json"
    if manifest.exists() and json.loads(manifest.read_text(encoding="utf-8")) != ids:
        raise ValueError(f"{out} holds vectors for different chunks. Use a new folder.")
    manifest.write_text(json.dumps(ids), encoding="utf-8")
    done = encoded = 0
    for number, start in enumerate(range(0, len(ids), batch_size)):
        path = out / f"part_{number:06d}.npy"
        if path.exists():
            done += 1
            continue
        vectors = np.asarray(encode([prefix + d for d in docs[start : start + batch_size]]), dtype="float32")
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        tmp = path.with_suffix(".tmp.npy")
        np.save(tmp, vectors / np.maximum(norms, 1e-12))
        tmp.replace(path)
        encoded += 1
    return {"batches_encoded": encoded, "batches_already_done": done, "out": str(out)}


class VectorIndex:
    """Cosine দিয়ে পুরো খোঁজা। ৩৪ হাজার chunk এ যথেষ্ট দ্রুত, আর approximate search এর ভুল এড়ায়।"""

    def __init__(self, folder: Path):
        import numpy as np

        self.ids = json.loads((folder / "ids.json").read_text(encoding="utf-8"))
        parts = sorted(folder.glob("part_*.npy"))
        self.matrix = np.concatenate([np.load(p) for p in parts]) if parts else np.empty((0, 0))
        if len(self.matrix) != len(self.ids):
            raise ValueError(f"{folder} is incomplete: {len(self.matrix)} vectors for {len(self.ids)} chunks")

    def search(self, query_vector, n: int) -> list[tuple[str, float]]:
        import numpy as np

        q = np.asarray(query_vector, dtype="float32")
        q = q / max(float(np.linalg.norm(q)), 1e-12)
        scores = self.matrix @ q
        order = np.argsort(-scores)[:n]
        return [(self.ids[i], float(scores[i])) for i in order]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=sorted(MODELS), required=True)
    ap.add_argument("--limit", type=int, default=None, help="প্রথম এতগুলো chunk, সময় মাপার জন্য")
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--db", default=str(ROOT / "evals" / "cache" / "chroma_eval_copy"))
    args = ap.parse_args()
    spec = MODELS[args.model]
    out = OUT_ROOT / (args.model + (f"_probe{args.limit}" if args.limit else ""))
    guard_out(out)
    if not args.run:
        print(json.dumps({"model": spec["hf"], "writes_to": str(out), "chunks": args.limit or 34217, "max_seq_length": spec["max_seq_length"], "note": "Nothing was run. Add --run after evals/run_experiment.py --status says ready."}, indent=1))
        return
    from evals.experiment_gate import readiness

    gate = readiness()
    if not gate["ready"]:
        raise SystemExit(f"not ready, so this expensive step won't run: {gate['unmet']}")
    import chromadb
    from sentence_transformers import SentenceTransformer

    from evals.pipeline_env import guard_db_path
    from retrieval.hybrid import ChunkStore

    store = ChunkStore.from_collection(chromadb.PersistentClient(path=str(guard_db_path(Path(args.db)))).get_collection("bangladesh_judgements_v3"))
    ids, docs = store.ids[: args.limit], store.docs[: args.limit]
    model = SentenceTransformer(spec["hf"])
    started = time.time()
    report = build_vectors(lambda texts: model.encode(texts, batch_size=args.batch_size, show_progress_bar=False), ids, docs, out, spec["passage_prefix"], args.batch_size)
    print(json.dumps({**report, "chunks_per_second": round(len(ids) / max(time.time() - started, 0.001), 2)}, indent=1))


if __name__ == "__main__":
    main()
