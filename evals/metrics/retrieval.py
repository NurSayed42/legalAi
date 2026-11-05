"""
evals/metrics/retrieval.py
==========================
Retrieval এর মাপ, শুধু হিসাব: model, collection বা disk এ হাত দেয় না।

`ranked` = মামলার তালিকা, ভালোটা আগে, একই মামলা একবারই। `grades` = {মামলা: 0|1|2}, যা বিচার করা হয়নি তা এখানে নেই
এবং অপ্রাসঙ্গিক হিসেবে গোনা হয়; কতটা বিচার হয়েছে তা judged_at_k বলে। সংজ্ঞা evals/README.md এ।
"""

import math
import random
from collections import Counter
from collections.abc import Sequence


def relevant_set(grades: dict[str, int], threshold: int = 1) -> set[str]:
    return {c for c, g in grades.items() if g >= threshold}


def precision_at_k(ranked: Sequence[str], grades: dict[str, int], k: int, threshold: int = 1) -> float:
    """হর সবসময় k: ফল কম এলে সেই ফাঁকা জায়গাও ক্ষতি।"""
    return sum(grades.get(c, 0) >= threshold for c in ranked[:k]) / k


def recall_at_k(ranked: Sequence[str], grades: dict[str, int], k: int, threshold: int = 1) -> float | None:
    """প্রাসঙ্গিক কিছু জানা না থাকলে None, শূন্য নয়। হর শুধু বিচার করা প্রাসঙ্গিক মামলা, তাই এটা pool recall।"""
    wanted = relevant_set(grades, threshold)
    if not wanted:
        return None
    return len(wanted & set(ranked[:k])) / len(wanted)


def capped_recall_at_k(ranked: Sequence[str], grades: dict[str, int], k: int, threshold: int = 1) -> float | None:
    """হর min(k, প্রাসঙ্গিক সংখ্যা): ১০০ টা প্রাসঙ্গিক মামলায় সাধারণ Recall@10 এর সর্বোচ্চ 0.1, এখানে 1।"""
    wanted = relevant_set(grades, threshold)
    if not wanted:
        return None
    return len(wanted & set(ranked[:k])) / min(k, len(wanted))


def rr_at_k(ranked: Sequence[str], grades: dict[str, int], k: int, threshold: int = 1) -> float | None:
    if not relevant_set(grades, threshold):
        return None
    for i, c in enumerate(ranked[:k]):
        if grades.get(c, 0) >= threshold:
            return 1 / (i + 1)
    return 0.0


def _dcg(gains: Sequence[int]) -> float:
    return sum((2**g - 1) / math.log2(i + 2) for i, g in enumerate(gains))


def ndcg_at_k(ranked: Sequence[str], grades: dict[str, int], k: int) -> float | None:
    ideal = _dcg(sorted(grades.values(), reverse=True)[:k])
    if ideal == 0:
        return None
    return _dcg([grades.get(c, 0) for c in ranked[:k]]) / ideal


def judged_at_k(ranked: Sequence[str], grades: dict[str, int], k: int) -> float:
    """Top-k এর কত ভাগ বিচার করা হয়েছে। কম হলে সেই system এর score বিশ্বাসযোগ্য নয়।"""
    top = list(ranked[:k])
    return sum(c in grades for c in top) / len(top) if top else 0.0


def domination(chunk_cases: Sequence[str], k: int) -> dict:
    """মামলায় নামানোর আগের top-k chunk এ একটা মামলা কতটা জায়গা নিচ্ছে।"""
    top = Counter(chunk_cases[:k])
    case, count = top.most_common(1)[0] if top else ("", 0)
    return {"max_slots_one_case": count, "top_case": case, "distinct_cases": len(top)}


def auc(positive: Sequence[float], negative: Sequence[float]) -> float | None:
    """একটা প্রাসঙ্গিক ফলের score একটা অপ্রাসঙ্গিকের চেয়ে বেশি হওয়ার সম্ভাবনা। 0.5 মানে score কিছুই বলে না।"""
    if not positive or not negative:
        return None
    pooled = sorted([(s, 1) for s in positive] + [(s, 0) for s in negative])
    rank_sum, i = 0.0, 0
    while i < len(pooled):
        j = i
        while j + 1 < len(pooled) and pooled[j + 1][0] == pooled[i][0]:
            j += 1
        mid = (i + j) / 2 + 1  # সমান score এর গড় rank
        rank_sum += mid * sum(pooled[m][1] for m in range(i, j + 1))
        i = j + 1
    n_pos, n_neg = len(positive), len(negative)
    return (rank_sum - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def _by_cluster(a: Sequence[float], b: Sequence[float], clusters: Sequence[str]) -> list[float]:
    """একই need এর বাংলা আর ইংরেজি প্রশ্ন স্বাধীন নয়, তাই need ধরে গড় করে তারপর পরীক্ষা।"""
    grouped: dict[str, list[float]] = {}
    for x, y, c in zip(a, b, clusters):
        grouped.setdefault(c, []).append(x - y)
    return [sum(v) / len(v) for v in grouped.values()]


def paired_bootstrap_ci(a: Sequence[float], b: Sequence[float], clusters: Sequence[str], n: int, seed: int, level: float = 0.95) -> tuple[float, float, float]:
    """a − b এর গড় আর তার percentile CI, need ধরে resample করে।"""
    diffs = _by_cluster(a, b, clusters)
    rng = random.Random(seed)
    means = sorted(sum(rng.choices(diffs, k=len(diffs))) / len(diffs) for _ in range(n))
    tail = (1 - level) / 2
    return sum(diffs) / len(diffs), means[int(tail * n)], means[min(n - 1, int((1 - tail) * n))]


def sign_flip_p(a: Sequence[float], b: Sequence[float], clusters: Sequence[str], n: int, seed: int) -> float:
    """দুই দিকের paired randomization test: পার্থক্যের চিহ্ন এলোমেলো করলে এত বড় গড় কতবার আসে।"""
    diffs = _by_cluster(a, b, clusters)
    observed = abs(sum(diffs))
    rng = random.Random(seed)
    extreme = sum(abs(sum(d if rng.random() < 0.5 else -d for d in diffs)) >= observed - 1e-12 for _ in range(n))
    return (extreme + 1) / (n + 1)


def holm_adjust(pvalues: dict[str, float]) -> dict[str, float]:
    """একসাথে অনেক তুলনা করলে কাকতালীয় 'জয়' ঠেকাতে Holm correction।"""
    order = sorted(pvalues, key=pvalues.__getitem__)
    out: dict[str, float] = {}
    running = 0.0
    for i, name in enumerate(order):
        running = max(running, min(1.0, (len(order) - i) * pvalues[name]))
        out[name] = running
    return out
