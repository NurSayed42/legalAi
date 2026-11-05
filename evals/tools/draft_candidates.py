"""
evals/tools/draft_candidates.py
===============================
Benchmark লেখার সাহায্য, কোনো retriever ছাড়া (SPEC Phase 2)। কিছুই label করে না।

    python evals/tools/draft_candidates.py sample --n 45      # seed দিয়ে এলোমেলো মামলা, script ভাগ করে
    python evals/tools/draft_candidates.py terms "bitcoin" "ক্রিপ্টোকারেন্সি"   # পুরো corpus এ কয়টা মামলায়, কতবার

sample: known-item প্রশ্নের লক্ষ্য হাতে বাছলে সহজ মামলা বেছে ফেলার ভয়, তাই আগে থেকে ঠিক করা ক্রমে নেওয়া হয়।
terms: কোনো বিষয়ের মামলা corpus এ আছে কিনা, retriever কে জিজ্ঞাসা না করেই।
"""

import argparse
import random
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from evals.tools.corpus import doc_script, load_corpus, normalize, term_counts, term_pattern  # noqa: E402

SEED = 20261105
# corpus এর ভাগের চেয়ে বাংলা বেশি নেওয়া, যাতে বাংলা মামলাও পরীক্ষায় থাকে
STRATA = {"en": 0.5, "mixed": 0.3, "bn": 0.2}


def sample(n: int, chars: int) -> None:
    corpus = load_corpus()
    by_script: dict[str, list[str]] = {s: [] for s in STRATA}
    for key in sorted(corpus):
        if key != "death_reference_58_of_2013":
            by_script[doc_script(corpus[key])].append(key)
    rng = random.Random(SEED)
    print({s: len(v) for s, v in by_script.items()})
    for script, share in STRATA.items():
        print(f"\n=== {script}")
        for i, key in enumerate(rng.sample(by_script[script], round(n * share)), 1):
            print(f"[{script}{i:02d}] {key}\n    {' '.join(corpus[key].split())[:chars]}")


def terms(wanted: list[str], show: int) -> None:
    corpus = {k: normalize(v) for k, v in load_corpus().items()}
    for term in wanted:
        pattern = {term: term_pattern(term)}
        hits = {k: term_counts(t, pattern)[term] for k, t in corpus.items()}
        hits = {k: c for k, c in hits.items() if c}
        top = sorted(hits, key=lambda k: (-hits[k], k))[:show]
        print(f"{term}: {len(hits)} cases, {sum(hits.values())} mentions | " + ", ".join(f"{k[:32]}={hits[k]}" for k in top))


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="command", required=True)
    s = sub.add_parser("sample")
    s.add_argument("--n", type=int, default=45)
    s.add_argument("--chars", type=int, default=420)
    t = sub.add_parser("terms")
    t.add_argument("terms", nargs="+")
    t.add_argument("--show", type=int, default=3)
    args = ap.parse_args()
    if args.command == "sample":
        sample(args.n, args.chars)
    else:
        terms(args.terms, args.show)


if __name__ == "__main__":
    main()
