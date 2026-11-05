"""
ingestion/integrity.py
======================
Recovery এর সময় "কিছু নষ্ট হয়নি" অনুমান না করে প্রমাণ করার জন্য:

  snapshot-text   text_v2/*.txt এর size + SHA-256, আর _manifest.jsonl এর copy
  verify-text     snapshot এর প্রতিটা file এখনো byte-identical কিনা (নতুন file থাকা ঠিক আছে)
  snapshot-chroma collection এর id, count, সব id/metadata/document এর hash, আর সব vector এর
                  exact hash (id অনুযায়ী)
  verify-chroma   উপরেরটার সাথে মেলানো
  snapshot-files  DB folder এর প্রতিটা file এর size + SHA-256, Chroma না খুলে
  verify-files    উপরেরটার সাথে মেলানো, Chroma না খুলে

Chroma খুললেই chroma.sqlite3 বদলায়, শুধু পড়লেও: header এর দুই byte আর একটা page (D-208)। Content একই থাকে, কিন্তু file
আর আগের মতো থাকে না। তাই production DB যাচাই করতে verify-files, আর content প্রমাণ করতে verify-chroma চালাও কপির উপর।

HNSW file এর hash নেওয়া হয় না — Chroma খোলার সময় WAL flush করে সেগুলো লিখতে পারে, content না
বদলালেও। Nearest-neighbour মিলিয়েও দেখা হয় না: approximate search একই DB তে কখনো কখনো ভিন্ন
ফল দেয়, তাতে মিথ্যা alarm বাজে (docs/DECISIONS.md D-112)। তাই প্রমাণ হলো প্রতিটা vector নিজে।

    python ingestion/integrity.py snapshot-text --out data_quality/backups/text_v2_pre_resume.json
    python ingestion/integrity.py verify-chroma --against data_quality/backups/chroma_pre_v2.json
"""

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_COLLECTIONS = ["bangladesh_judgements", "bangladesh_laws"]
VECTOR_BATCH = 2000
CHECKED_FIELDS = ("exists", "id", "count", "content_sha256", "vectors_sha256")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _db_files(db_path: Path) -> dict[str, list]:
    return {p.relative_to(db_path).as_posix(): [p.stat().st_size, _sha256(p)] for p in sorted(db_path.rglob("*")) if p.is_file()}


def snapshot_files(db_path: Path, out: Path) -> dict:
    files = _db_files(db_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(files), encoding="utf-8")
    return {"files": len(files), "bytes": sum(v[0] for v in files.values())}


def verify_files(db_path: Path, snapshot: Path) -> dict:
    """কোনো database library ছাড়া, শুধু file পড়ে। নতুন file ও পরিবর্তন হিসেবে গোনা হয়।"""
    recorded = json.loads(snapshot.read_text(encoding="utf-8"))
    current = _db_files(db_path)
    changed = sorted(k for k in recorded.keys() & current.keys() if recorded[k] != current[k])
    missing, added = sorted(recorded.keys() - current.keys()), sorted(current.keys() - recorded.keys())
    return {"checked": len(recorded), "changed": changed, "missing": missing, "added": added, "ok": not (changed or missing or added)}


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


def _vectors_sha256(db_path: Path, name: str) -> str:
    """সব vector এর exact hash, id এর ক্রমে — retrieval এর ক্রম বা WAL flush এ বদলায় না।"""
    import chromadb
    import numpy as np
    col = chromadb.PersistentClient(path=str(db_path)).get_collection(name)
    digests = {}
    for offset in range(0, col.count(), VECTOR_BATCH):
        batch = col.get(limit=VECTOR_BATCH, offset=offset, include=["embeddings"])
        for doc_id, vector in zip(batch["ids"], batch["embeddings"]):
            digests[doc_id] = hashlib.sha256(np.asarray(vector, dtype="float32").tobytes()).digest()
    h = hashlib.sha256()
    for doc_id in sorted(digests):
        h.update(doc_id.encode("utf-8"))
        h.update(digests[doc_id])
    return h.hexdigest()


def snapshot_chroma(db_path: Path, out: Path, names: list[str]) -> dict:
    data = {}
    for name in names:
        data[name] = _collection_rows(db_path, name)
        if data[name]["exists"]:
            data[name]["vectors_sha256"] = _vectors_sha256(db_path, name)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data), encoding="utf-8")
    return data


def verify_chroma(db_path: Path, snapshot: Path) -> dict:
    recorded = json.loads(snapshot.read_text(encoding="utf-8"))
    report = {"ok": True, "collections": {}}
    for name, before in recorded.items():
        now = _collection_rows(db_path, name)
        if now.get("exists"):
            now["vectors_sha256"] = _vectors_sha256(db_path, name)
        checks = {key: now.get(key) == before.get(key) for key in CHECKED_FIELDS}
        report["collections"][name] = {"chunks": now.get("count"), **checks}
        report["ok"] = report["ok"] and all(checks.values())
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["snapshot-text", "verify-text", "snapshot-chroma", "verify-chroma", "snapshot-files", "verify-files"])
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
    elif args.command == "snapshot-files":
        result = snapshot_files(Path(args.db), Path(args.out))
    elif args.command == "verify-files":
        result = verify_files(Path(args.db), Path(args.against))
    elif args.command == "snapshot-chroma":
        result = snapshot_chroma(Path(args.db), Path(args.out), args.collections)
    else:
        result = verify_chroma(Path(args.db), Path(args.against))
    print(json.dumps(result, ensure_ascii=False, indent=1))
    if result.get("ok") is False:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
