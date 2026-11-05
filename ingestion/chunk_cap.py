"""
ingestion/chunk_cap.py
======================
বিশাল মামলার chunk সীমা (docs/DECISIONS.md D-120)। Death Reference 58/2013 একাই v2 এর 21% chunk, তাই
বাংলা vector search এর top-10 ভরে ফেলে। সীমার বেশি chunk index এ যায় না, তবে পুরো text text_v3/ এ থাকে।

একই মামলার কয়েকটা PDF এক মামলা হিসেবে গোনা হয় (case_groups)। কোন chunk থাকবে তা শুধু text থেকেই বের হয়, তাই
verify করার সময় ঠিক একই id আবার হিসাব করা যায়:
  - প্রতিটা PDF এর প্রথম আর শেষ anchor_chunks_per_file টা chunk: header আর চূড়ান্ত আদেশ এখানে থাকে
  - বাকি জায়গা সমান ফাঁকে, যাতে পুরো মামলা থেকে কিছু না কিছু থাকে
"""

CAP_CONFIG = {
    # evals/cap_simulation.py এর মাপ, D-120: এর নিচে নামলে নিজের বিষয়ের প্রশ্নে আর লাভ হয় না, উপরে উঠলে
    # অন্য বিষয়ের top-10 আবার ভরে যায়। পরের বড় মামলা 354 chunk, তাই কার্যত শুধু Death Ref 58 এ লাগে।
    "max_chunks_per_case": 400,
    "anchor_chunks_per_file": 5,
    # filename prefix → মামলার নাম। এখানে না থাকলে প্রতিটা PDF নিজেই একটা মামলা। সংখ্যার prefix দিয়ে নিজে থেকে
    # জোড়া হয় না: '1_' দিয়ে শুরু হওয়া শত শত আলাদা file আছে।
    "case_groups": {"769454_DeathRef58of2013_": "death_reference_58_of_2013"},
}


def case_key(filename: str, groups: dict[str, str] | None = None) -> str:
    groups = CAP_CONFIG["case_groups"] if groups is None else groups
    for prefix, name in groups.items():
        if filename.startswith(prefix):
            return name
    return filename


def select_case_chunks(counts: dict[str, int], cap: int | None, anchors: int | None = None,
                       phase: float = 0.0) -> dict[str, list[int]]:
    """এক মামলার {file: chunk সংখ্যা} থেকে {file: যে chunk গুলো থাকবে}। সীমার মধ্যে থাকলে সব।
    phase (0–1) সমান ফাঁকের শুরু সরায়; শুধু simulation এ, কোন chunk পড়ল তার প্রভাব মাপতে।"""
    anchors = CAP_CONFIG["anchor_chunks_per_file"] if anchors is None else anchors
    total = sum(counts.values())
    if cap is None or total <= cap:
        return {name: list(range(n)) for name, n in counts.items()}
    if cap <= 0:
        raise ValueError(f"cap must be positive, got {cap}")

    keep: dict[str, set[int]] = {name: set() for name in counts}
    # anchor এ সীমা শেষ হয়ে গেলে anchor বাদ, শুধু সমান ফাঁকে
    if sum(min(n, 2 * anchors) for n in counts.values()) <= cap:
        for name, n in counts.items():
            keep[name].update(i for i in range(n) if i < anchors or i >= n - anchors)
    remaining = cap - sum(len(v) for v in keep.values())
    candidates = [(name, i) for name in sorted(counts) for i in range(counts[name]) if i not in keep[name]]
    if remaining > 0 and candidates:
        step = len(candidates) / remaining
        for k in range(remaining):
            name, i = candidates[int((k + phase) * step)]
            keep[name].add(i)
    return {name: sorted(keep[name]) for name in counts}


def plan_index(chunk_counts: dict[str, int], cap: int | None, groups: dict[str, str] | None = None,
               anchors: int | None = None, phase: float = 0.0) -> dict:
    """পুরো corpus এর {file: chunk সংখ্যা} থেকে কোন chunk index হবে, আর কতটা বাদ গেল তার হিসাব।"""
    cases: dict[str, dict[str, int]] = {}
    for name, n in chunk_counts.items():
        cases.setdefault(case_key(name, groups), {})[name] = n
    keep: dict[str, list[int]] = {}
    capped = {}
    for case, counts in cases.items():
        chosen = select_case_chunks(counts, cap, anchors, phase)
        keep.update(chosen)
        total = sum(counts.values())
        kept = sum(len(v) for v in chosen.values())
        if kept < total:
            capped[case] = {"files": sorted(counts), "chunks_from_text": total, "chunks_indexed": kept}
    return {"keep": keep, "capped_cases": capped, "chunks_from_text": sum(chunk_counts.values()),
            "chunks_indexed": sum(len(v) for v in keep.values()), "max_chunks_per_case": cap}
