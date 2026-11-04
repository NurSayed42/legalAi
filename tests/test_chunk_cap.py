"""
বিশাল মামলার chunk সীমার test (docs/DECISIONS.md D-120)। কিছু লাগে না।

    python -m pytest tests/test_chunk_cap.py -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingestion.chunk_cap import CAP_CONFIG, case_key, plan_index, select_case_chunks  # noqa: E402


def test_under_the_cap_keeps_everything():
    assert select_case_chunks({"a.pdf": 5}, 10) == {"a.pdf": [0, 1, 2, 3, 4]}
    assert select_case_chunks({"a.pdf": 10}, 10)["a.pdf"] == list(range(10))
    assert select_case_chunks({"a.pdf": 500}, None)["a.pdf"] == list(range(500))


def test_over_the_cap_keeps_exactly_the_cap_with_no_repeats():
    for total, cap in ((1000, 100), (8988, 400), (401, 400), (7, 3)):
        kept = select_case_chunks({"a.pdf": total}, cap, anchors=1)["a.pdf"]
        assert len(kept) == cap and len(set(kept)) == cap
        assert all(0 <= i < total for i in kept)


def test_cap_keeps_first_and_last_chunks_of_every_part():
    counts = {"p1.pdf": 4000, "p2.pdf": 4500, "p3.pdf": 300}
    kept = select_case_chunks(counts, 400, anchors=5)
    assert sum(len(v) for v in kept.values()) == 400
    for name, n in counts.items():
        assert set(range(5)) <= set(kept[name]), "header থাকতে হবে"
        assert set(range(n - 5, n)) <= set(kept[name]), "চূড়ান্ত আদেশ থাকতে হবে"


def test_cap_spreads_across_the_whole_case():
    kept = select_case_chunks({"a.pdf": 10000}, 100, anchors=0)["a.pdf"]
    gaps = [b - a for a, b in zip(kept, kept[1:])]
    assert max(gaps) <= 101 and min(gaps) >= 99


def test_cap_smaller_than_the_anchors_still_respects_the_cap():
    kept = select_case_chunks({"a.pdf": 100, "b.pdf": 100}, 5, anchors=5)
    assert sum(len(v) for v in kept.values()) == 5


def test_selection_is_deterministic_so_verify_can_recompute_it():
    counts = {"p1.pdf": 4138, "p2.pdf": 4551, "p3.pdf": 299}
    assert select_case_chunks(counts, 400) == select_case_chunks(dict(reversed(list(counts.items()))), 400)


def test_parts_of_one_case_share_one_cap_but_other_files_do_not():
    groups = {"769454_": "death_ref"}
    assert case_key("769454_a_1.pdf", groups) == case_key("769454_a_2.pdf", groups) == "death_ref"
    counts = {"769454_a_1.pdf": 300, "769454_a_2.pdf": 300, "big.pdf": 350, "small.pdf": 3}
    plan = plan_index(counts, 400, groups=groups, anchors=1)
    assert len(plan["keep"]["769454_a_1.pdf"]) + len(plan["keep"]["769454_a_2.pdf"]) == 400
    assert len(plan["keep"]["big.pdf"]) == 350 and len(plan["keep"]["small.pdf"]) == 3
    assert plan["chunks_from_text"] == 953 and plan["chunks_indexed"] == 753
    assert set(plan["capped_cases"]) == {"death_ref"}


def test_real_death_ref_filenames_are_grouped():
    assert {case_key(f"769454_DeathRef58of2013_{i}.pdf") for i in (1, 2, 3)} == {"death_reference_58_of_2013"}
    assert case_key("1_TN_11240_2024_RS.pdf") == "1_TN_11240_2024_RS.pdf"


def test_configured_cap_is_above_the_next_largest_case():
    """সীমাটা Death Ref 58 এর জন্য; পরের বড় মামলা 354 chunk (v2)। এর নিচে নামালে সাধারণ judgement ও কাটা পড়বে।"""
    assert CAP_CONFIG["max_chunks_per_case"] > 354
