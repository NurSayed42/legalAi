"""
ingestion/integrity.py
======================
Recovery এর সময় "কিছু নষ্ট হয়নি" অনুমান না করে প্রমাণ করার জন্য:

  snapshot-text   text_v2/*.txt এর size + SHA-256, আর _manifest.jsonl এর copy
  verify-text     snapshot এর প্রতিটা file এখনো byte-identical কিনা (নতুন file থাকা ঠিক আছে)
  snapshot-chroma collection এর id, count, সব id/metadata/document এর hash, আর fixed vector এ
                  nearest-neighbour ফলাফল
  verify-chroma   উপরেরটার সাথে মেলানো

Chroma তে শুধু read-only SQLite দিয়ে পড়ে। HNSW file এর hash ইচ্ছা করে নেওয়া হয় না — Chroma
খোলার সময় WAL flush করে সেগুলো লিখতে পারে, content না বদলালেও। তাই vector অংশের প্রমাণ হলো
একই vector এ একই nearest neighbour।

    python ingestion/integrity.py snapshot-text --out data_quality/backups/text_v2_pre_resume.json
    python ingestion/integrity.py verify-chroma --against data_quality/backups/chroma_pre_v2.json
"""

import argparse
import hashlib
import json
import random
import shutil
import sqlite3
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_COLLECTIONS = ["bangladesh_judgements", "bangladesh_laws"]
PROBE_VECTORS = 5
EMBED_DIM = 384          # paraphrase-multilingual-MiniLM-L12-v2


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot_text(text_dir: Path, out: Path) -> dict:
    files = {p.name: [p.stat().st_size, _sha256(p)] for p in sorted(text_dir.glob("*.txt"))}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(files), encoding="utf-8")
    manifest = text_dir / "_manifest.jsonl"
    if manifest.exists():
        shutil.copy2(manifest, out.with_name(out.stem + "_manifest.jsonl"))
    return {"files": len(files), "bytes": sum(v[0] for v in files.values())}


def verify_text(text_dir: Path, snapshot: Path) -> dict:
    recorded = json.loads(snapshot.read_text(encoding="utf-8"))
    missing, changed = [], []
    for name, (size, digest) in recorded.items():
        path = text_dir / name
        if not path.exists():
            missing.append(name)
        elif path.stat().st_size != size or _sha256(path) != digest:
            changed.append(name)
    return {"checked": len(recorded), "identical": len(recorded) - len(missing) - len(changed),
            "missing": missing, "changed": changed, "ok": not missing and not changed}


def _collection_rows(db_path: Path, name: str) -> dict:
    con = sqlite3.connect(f"file:{db_path / 'chroma.sqlite3'}?mode=ro", uri=True)
    try:
        row = con.execute("select id from collections where name = ?", (name,)).fetchone()
        if row is None:
            return {"exists": False}
        collection_id = row[0]
        segment = con.execute("select id from segments where collection = ? and scope = 'METADATA'",
                              (collection_id,)).fetchone()[0]
        count = con.execute("select count(*) from embeddings where segment_id = ?", (segment,)).fetchone()[0]
        h = hashlib.sha256()
        cur = con.execute(
            "select e.embedding_id, m.key, m.string_value, m.int_value, m.float_value, m.bool_value "
            "from embedding_metadata m join embeddings e on e.id = m.id "
            "where e.segment_id = ? order by e.embedding_id, m.key", (segment,))
        while rows := cur.fetchmany(5000):
            for r in rows:
                h.update(repr(r).encode("utf-8"))
        return {"exists": True, "id": collection_id, "count": count, "content_sha256": h.hexdigest()}
    finally:
        con.close()


def _probe_neighbours(db_path: Path, name: str) -> list:
    import chromadb
    rng = random.Random(42)
    vectors = [[rng.gauss(0, 1) for _ in range(EMBED_DIM)] for _ in range(PROBE_VECTORS)]
    col = chromadb.PersistentClient(path=str(db_path)).get_collection(name)
    result = col.query(query_embeddings=vectors, n_results=10, include=["distances"])
    return [[(i, round(d, 4)) for i, d in zip(ids, dists)] for ids, dists in zip(result["ids"], result["distances"])]


def snapshot_chroma(db_path: Path, out: Path, names: list[str]) -> dict:
    data = {}
    for name in names:
        data[name] = _collection_rows(db_path, name)
        if data[name]["exists"]:
            data[name]["neighbours"] = _probe_neighbours(db_path, name)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data), encoding="utf-8")
    return {n: {k: v for k, v in d.items() if k != "neighbours"} for n, d in data.items()}


def verify_chroma(db_path: Path, snapshot: Path) -> dict:
    recorded = json.loads(snapshot.read_text(encoding="utf-8"))
    report = {"ok": True, "collections": {}}
    for name, before in recorded.items():
        now = _collection_rows(db_path, name)
        checks = {key: now.get(key) == before.get(key) for key in ("exists", "id", "count", "content_sha256")}
        if before.get("exists") and now.get("exists"):
            probe = json.loads(json.dumps(_probe_neighbours(db_path, name)))
            checks["neighbours"] = probe == before["neighbours"]
        report["collections"][name] = {"chunks": now.get("count"), **checks}
        report["ok"] = report["ok"] and all(checks.values())
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["snapshot-text", "verify-text", "snapshot-chroma", "verify-chroma"])
    ap.add_argument("--text-dir", default=str(ROOT / "text_v2"))
    ap.add_argument("--db", default=str(ROOT / "legal_chroma_db"))
    ap.add_argument("--out")
    ap.add_argument("--against")
    ap.add_argument("--collections", nargs="*", default=DEFAULT_COLLECTIONS)
    args = ap.parse_args()

    if args.command == "snapshot-text":
        result = snapshot_text(Path(args.text_dir), Path(args.out))
    elif args.command == "verify-text":
        result = verify_text(Path(args.text_dir), Path(args.against))
    elif args.command == "snapshot-chroma":
        result = snapshot_chroma(Path(args.db), Path(args.out), args.collections)
    else:
        result = verify_chroma(Path(args.db), Path(args.against))
    print(json.dumps(result, ensure_ascii=False, indent=1))
    if result.get("ok") is False:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
