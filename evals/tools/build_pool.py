"""
evals/tools/build_pool.py
=========================
প্রতিটা need এর জন্য বিচার করার মামলার তালিকা (pool) আর card (evals/judging/, gitignored)।

Pool = সব system এর top-10 মামলার মিলন + স্বাধীন arm: retriever ছাড়া পুরো text_v3 এ key term গুনে উপরের কয়েকটা। Arm
না থাকলে যে মামলা কোনো system খুঁজে পায়নি তা চিরকাল "অপ্রাসঙ্গিক" থেকে যেত।

Card এ কোন system এনেছে তা থাকে না, আর ক্রম seed দিয়ে এলোমেলো: বিচারের সময় পক্ষপাত এড়াতে।

    python evals/tools/build_pool.py --runs evals/runs/v3.json --arm 5
"""

import argparse
import json
import random
import re
import sys
import unicodedata
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from evals.retrieval_schema import BenchmarkQuery, grades_by_need, load_qrels, load_queries  # noqa: E402
from evals.tools.corpus import BN, doc_script, load_corpus, normalize, term_pattern  # noqa: E402
from retrieval.hybrid import RankedChunk, collapse_to_cases  # noqa: E402

JUDGING = ROOT / "evals" / "judging"
QUERIES = ROOT / "evals" / "golden" / "retrieval_queries.jsonl"
JUDGED_GROUPS = ("legal_term", "topic", "dr58", "lay")
SEED = 20261105


def squeeze(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split())


def run_pool(run: dict, query_ids: list[str], systems: list[str], k: int) -> set[str]:
    pool: set[str] = set()
    for qid in query_ids:
        for name in systems:
            ranked = [RankedChunk(chunk_id=c, filename=key, case_key=key, score=s) for c, key, s in run["results"][qid][name]["chunks"]]
            pool |= {c.case_key for c in collapse_to_cases(ranked)[:k]}
    return pool


def arm_cases(corpus_norm: dict[str, str], terms: list[str], n: int) -> list[str]:
    """যত বেশি আলাদা key term, তারপর যত বেশি বার প্রতি ১০০০ অক্ষরে।"""
    patterns = [term_pattern(t) for t in terms]
    scored = []
    for key, text in corpus_norm.items():
        counts = [len(p.findall(text)) for p in patterns]
        if any(counts):
            scored.append((-sum(c > 0 for c in counts), -sum(counts) / (len(text) / 1000 + 1), key))
    return [key for *_, key in sorted(scored)[:n]]


def passages(text: str, terms: list[str], width: int) -> tuple[list[str], dict[str, int]]:
    """সবচেয়ে বেশি আলাদা term যেখানে একসাথে, তার চারপাশের অংশ, আর সেখান থেকে দূরে আরেকটা। হুবহু text, প্রমাণ হিসেবে যাচাই হয়।"""
    hits: list[tuple[int, str]] = []
    counts: dict[str, int] = {}
    for term in terms:
        escaped = re.escape(unicodedata.normalize("NFC", term))
        pattern = re.compile(escaped if BN.search(term) else rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])", re.IGNORECASE)
        found = [m.start() for m in pattern.finditer(text)]
        if found:
            counts[term] = len(found)
            hits += [(p, term) for p in found]
    if not hits:
        return [], counts
    hits.sort()

    def around(center: int) -> tuple[int, int]:
        return max(0, center - width), min(len(text), center + width)

    def density(center: int) -> tuple[int, int]:
        low, high = around(center)
        inside = [t for p, t in hits if low <= p < high]
        return len(set(inside)), len(inside)

    step = max(1, len(hits) // 400)  # Death Ref 58 এ হাজার হাজার hit
    sampled = [p for p, _ in hits[::step]]
    first = max(sampled, key=lambda p: (density(p), -p))
    far = [p for p in sampled if abs(p - first) > 2 * width]
    chosen = [first] + ([max(far, key=lambda p: (density(p), -p))] if far else [])
    return [text[slice(*around(p))].strip() for p in chosen], counts


def write_need(need: str, queries: list[BenchmarkQuery], pool: list[str], corpus: dict[str, str], run: dict, width: int) -> int:
    terms = queries[0].key_terms
    rng = random.Random(f"{SEED}:{need}")
    pool = sorted(pool)
    rng.shuffle(pool)
    best_chunk: dict[str, str] = {}
    for q in queries:
        for row in run["results"][q.id].values():
            for cid, key, _ in row["chunks"][:60]:
                if cid in run["snippets"]:
                    best_chunk.setdefault(key, run["snippets"][cid])
    mapping, lines = {}, ["need " + need + " | " + " || ".join(q.query for q in queries), "terms: " + ", ".join(terms), ""]
    for i, case in enumerate(pool, 1):
        text = squeeze(corpus[case])
        found, counts = passages(text, terms, width)
        if not found:
            # key term নেই: system যে chunk এনেছিল সেটাই দেখা, নইলে শুরুটা
            source = squeeze(best_chunk.get(case, "")) or text
            found = [source[: 2 * width]]
        labels = "ab"
        mapping[str(i)] = {"case_key": case, "passages": dict(zip(labels, found))}
        stat = " ".join(f"{t}={n}" for t, n in counts.items()) or "no-terms"
        lines.append(f"#{i} {case[:34]} [{doc_script(corpus[case])}, {len(text) // 1000}k] {stat}")
        lines += [f"{label}: {p}" for label, p in zip(labels, found)]
    (JUDGING / f"{need}.txt").write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    (JUDGING / f"{need}.map.json").write_bytes(json.dumps(mapping, ensure_ascii=False).encode("utf-8"))
    return len(pool)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default=str(ROOT / "evals" / "runs" / "v3.json"))
    ap.add_argument("--arm", type=int, default=5)
    ap.add_argument("--width", type=int, default=100, help="key term এর দুই পাশে কত অক্ষর")
    ap.add_argument("--needs", nargs="*", default=None)
    ap.add_argument("--queries", default=str(QUERIES))
    ap.add_argument("--qrels", nargs="*", default=[], help="আগে বিচার করা label: সেই মামলার card আবার তৈরি হয় না")
    ap.add_argument("--count-only", action="store_true", help="কয়টা card লাগবে শুধু সেটা বলে, লেখা দেখায় না")
    ap.add_argument("--out-dir", default=str(JUDGING))
    args = ap.parse_args()

    run = json.loads(Path(args.runs).read_text(encoding="utf-8"))
    queries = load_queries(Path(args.queries))
    known = grades_by_need([r for path in args.qrels for r in load_qrels(Path(path))])
    if args.count_only:
        counts = {}
        k = run["config"]["k"]
        by_need_ids: dict[str, list[str]] = {}
        for q in queries:
            by_need_ids.setdefault(q.need_id, []).append(q.id)
        for need, ids in sorted(by_need_ids.items()):
            pool = run_pool(run, ids, run["systems"], k)
            counts[need] = {"pool": len(pool), "already_graded": len(pool & set(known.get(need, {}))), "to_grade": len(pool - set(known.get(need, {})))}
        print(json.dumps({"needs": counts, "total_to_grade": sum(c["to_grade"] for c in counts.values()), "total_pool": sum(c["pool"] for c in counts.values())}, indent=0))
        return
    corpus = load_corpus()
    corpus_norm = {k: normalize(v) for k, v in corpus.items()}
    JUDGING.mkdir(exist_ok=True)
    by_need: dict[str, list[BenchmarkQuery]] = {}
    for q in queries:
        if q.group in JUDGED_GROUPS and (not args.needs or q.need_id in args.needs):
            by_need.setdefault(q.need_id, []).append(q)
    total = 0
    k = run["config"]["k"]
    for need, qs in sorted(by_need.items()):
        pool = run_pool(run, [q.id for q in qs], run["systems"], k)
        arm = arm_cases(corpus_norm, qs[0].key_terms, args.arm)
        fresh = sorted((pool | set(arm)) - set(known.get(need, {})))
        count = write_need(need, qs, fresh, corpus, run, args.width)
        total += count
        print(f"{need}: pool {len(pool)} + arm-only {len(set(arm) - pool)} = {count}")
    print("total cards", total)


if __name__ == "__main__":
    main()
