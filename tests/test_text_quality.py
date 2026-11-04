"""
নষ্ট বাংলা detector আর লাইন-retention check এর test (docs/DECISIONS.md D-117, D-118)। OCR, DB, server লাগে না।

    python -m pytest tests/test_text_quality.py -q
"""

import sys
from pathlib import Path

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingestion import pdf_extract  # noqa: E402
from ingestion.pdf_extract import PagePlan, extract_page_pymupdf, page_text_blocks, plan_blocks  # noqa: E402
from ingestion.quality_audit import (  # noqa: E402
    BANGLA_CHECK,
    RETENTION_CHECK,
    audit_retention,
    audit_text,
    bangla_file_check,
    bangla_problem,
    bangla_signals,
    is_junk_text,
    output_lines,
    pdf_lines,
    retention,
    strict_classify,
    text_layer_problem,
)

CLEAN = ("বাংলাদেশের সর্বোচ্চ আদালত এই মামলায় রায় প্রদান করিয়াছেন এবং আপীল বিভাগ দেওয়ানী কার্যবিধির "
         "ধারা অনুযায়ী আদেশ দিয়াছেন। উক্ত আইনের বিধান মোতাবেক বিচারক সিদ্ধান্ত গ্রহণ করিলেন ও আবেদন মঞ্জুর হইল।")
# PDF text layer থেকে হুবহু নেওয়া নমুনা, প্রতিটা আলাদা ধরনের নষ্ট
FOREIGN_VISUAL = "ǯদওয়ানী আদালেত ɛিতকার পাওয়ার িবষয়িট ǯকান আইেনর অধীেন িবচাযȟ"
VISUAL_ONLY = "িনধর্ািরত সমেয়র মেধ্য আেবদন িবেবচনা কিরয়া িসদ্ধান্ত িদেত হইেব েকােনা আদালত"
UNASSIGNED = "প঩঱঳ক঵ র঴ম঑ন঳ আ঱঩ল ক঳ঝ঵ দ঱঩ত ম঵঳নর ব঱঩ঝ঵ ন঵঳ প঱঳র঵"
LOSSY = "২৯৷ (১) এই ই উপ প প ক এ ই ৫(১)/২০১৯-৮৫২ ও ক ল জ ই জ এ ই জ ল এ ই"
GARBAGE = ('52 ) - (/ 0 * + / 11 * % - ; 2 - - 3 2 - 4, -# ; 5 , 2 ( 4 # 2 ) + (6 +! - ( "8 + ( # ; 2 9 << * ) = % % ) ) '
           "*-4 % + 1 @ @ + ( - * - 1 ( 2 0% 1 A (/ * 3 % + 7 # (")
ENGLISH = ("The appellant filed this petition under Article 102 of the Constitution of the People's Republic of "
           "Bangladesh, and the Rule was issued on 12.03.2019. We have heard the learned Advocates for both sides "
           "and perused the materials on record (Annexure-A). The impugned order dated 05.04.2018 is set aside.")

# পুনরাবৃত্তি ছাড়া লম্বা text, নইলে পুরনো repeated_text rule নিজেই bucket পাল্টে দেয়
LONG_ENGLISH = ENGLISH + " " + " ".join(f"witness number {i} stated before court {i * 7} regarding exhibit {i * 3}." for i in range(90))


@pytest.mark.parametrize("text", [FOREIGN_VISUAL, VISUAL_ONLY, UNASSIGNED])
def test_impossible_spelling_is_flagged(text):
    assert bangla_problem(bangla_signals(text), BANGLA_CHECK["min_tokens_layer_bad"]) == "impossible_spelling"


def test_lossy_layer_is_flagged_by_isolated_letters():
    signals = bangla_signals(LOSSY)
    assert signals["bad"] == 0, "এই ধরনে অসম্ভব বানান নেই, তাই আলাদা signal লাগে"
    assert bangla_problem(signals, BANGLA_CHECK["min_tokens_layer_lossy"]) == "isolated_letters"


def test_clean_words_without_vowel_signs_are_not_lossy():
    """আইনের আসল text এ পাওয়া: স্বরচিহ্ন কম, কিন্তু পরিষ্কার। স্বরচিহ্নের ঘনত্ব দিয়ে মাপলে এগুলো ভুল flag হত।"""
    assert text_layer_problem(f"{ENGLISH} অর্থ আইন, ১৯৯৩ (১৯৯৩ সনের ১৮ নং আইন)") == ""


def test_list_markers_are_not_lost_letters():
    assert text_layer_problem("Annexures (ক) (খ) (গ) (ঘ) ঙ) and (চ).") == ""


def test_clean_bangla_is_not_flagged():
    assert text_layer_problem(CLEAN) == ""
    assert bangla_signals(CLEAN)["bad"] == 0


def test_clean_bangla_with_english_digits_and_danda_is_not_flagged():
    assert text_layer_problem(f"{ENGLISH} {CLEAN} ১২৩/২০১৯ ।") == ""


def test_english_only_and_digits_only_are_not_judged():
    assert bangla_signals(ENGLISH)["tokens"] == 0
    assert bangla_signals("২০১৯ ১২৩ ৪৫৬")["tokens"] == 0
    assert text_layer_problem(ENGLISH) == ""


def test_a_few_clean_letters_are_not_called_lossy():
    assert text_layer_problem("Annexure ক and খ") == ""


def test_one_impossible_token_flags_a_thin_layer():
    """অনেক file এ অল্প নষ্ট বাংলা কয়েক page এ ছড়ানো; page প্রতি token গুনে এগুলো ছাড়া যায় না।"""
    assert text_layer_problem(ENGLISH + " Memo No. ারকনং-গৃঃগ।মা/") == "impossible_spelling"
    assert text_layer_problem(ENGLISH + " ʅাȭ") == "impossible_spelling"


def test_conjuncts_and_nukta_are_valid_spelling():
    # ব্যঞ্জন + হসন্ত + ব্যঞ্জন, ড় ঢ় য়, দুই রকম encoding এর য়, ZWNJ সহ র‍্য — কোনোটাই অসম্ভব না
    text = "বিশ্ববিদ্যালয় পাহাড় আষাঢ় নিয়ম নিয়ম র\u200d্যাব কর্ম\u200cচারী সুপ্রিম"
    assert bangla_signals(text)["bad"] == 0


def test_one_typo_in_clean_text_does_not_flag():
    assert text_layer_problem(CLEAN + " িভুল") == ""


def test_junk_layer_is_flagged_but_english_is_not():
    assert is_junk_text(GARBAGE) and text_layer_problem(GARBAGE) == "junk_tokens"
    assert not is_junk_text(ENGLISH * 3)


def test_file_check_finds_one_bad_page_in_a_large_clean_file():
    pages = [CLEAN] * 40 + [(FOREIGN_VISUAL + " ") * 3]
    text = "\f".join(pages)
    row = bangla_file_check(text, len(text.split()))
    assert row["corrupted_bangla"] and row["corrupted_bangla_pages"] == 1
    assert row["bangla_bad_ratio"] < BANGLA_CHECK["bad_token_ratio"], "পুরো file এ গড় করলে এটা ঢাকা পড়ে"


def test_file_check_ignores_clean_text():
    row = bangla_file_check(CLEAN + "\f" + CLEAN, 60)
    assert row["corrupted_bangla"] == "" and row["corrupted_bangla_pages"] == 0


def test_corrupted_share_decides_suspect_or_broken():
    small = audit_text(LONG_ENGLISH + " " + " ".join([FOREIGN_VISUAL] * 3))
    assert small["corrupted_bangla"] and small["bucket"] == "suspect" and small["bucket_old"] == "good"
    heavy = audit_text(" ".join([FOREIGN_VISUAL] * 15))
    assert heavy["bucket"] == "broken" and "corrupted_bangla" in heavy["reasons"]


def test_old_rule_columns_are_unchanged_by_strict_checks():
    row = audit_text(LONG_ENGLISH)
    assert row["bucket"] == row["bucket_old"] == "good" and row["reasons"] == ""


# ─── extractor এর সিদ্ধান্ত ────────────────────────────────

def block(text: str, y: float = 100.0) -> dict:
    lines = [{"spans": [{"text": line, "font": "Times", "bbox": (72, y + i * 14, 400, y + i * 14 + 10)}]}
             for i, line in enumerate(text.split("\n"))]
    return {"type": 0, "bbox": (72, y, 400, y + 14 * len(lines)), "lines": lines}


def test_plan_keeps_clean_layer_and_runs_no_ocr():
    plan = plan_blocks([block(ENGLISH), block(CLEAN, 200)])
    assert plan.method == "pymupdf" and plan.ocr_blocks == 0


def test_plan_ocrs_only_the_corrupted_block():
    plan = plan_blocks([block(ENGLISH), block(ENGLISH, 150), block(FOREIGN_VISUAL, 200)])
    assert plan.method == "pymupdf+ocr_bangla" and plan.ocr_blocks == 1
    assert [needs_ocr for _, _, needs_ocr in plan.blocks] == [False, False, True]


def test_plan_uses_full_page_when_corruption_dominates():
    plan = plan_blocks([block("Page 3"), block((FOREIGN_VISUAL + " ") * 4, 200)])
    assert plan.method == "ocr_page_corrupted" and plan.layer_problem == "impossible_spelling"


def test_plan_full_page_for_junk_layer():
    plan = plan_blocks([block(GARBAGE)])
    assert plan.method == "ocr_page_corrupted" and plan.layer_problem == "junk_tokens"


def test_plan_without_layer_check_reproduces_v2_decision():
    blocks = [block(ENGLISH), block(FOREIGN_VISUAL, 200)]
    assert plan_blocks(blocks, check_layer=False).method == "pymupdf"


def test_plan_reassembles_spans_split_inside_words():
    """Span ধরে মাপলে একটা অক্ষরের span এ অন্য লিপি দেখা যায় না; এই ফাঁদে আগের census আটকেছিল।"""
    split = {"type": 0, "bbox": (72, 100, 400, 114), "lines": [{"spans": [
        {"text": part, "font": "Nikosh", "bbox": (72, 100, 400, 110)}
        for part in ("ǯ", "দওয়ানী আদা", "লেত ɛ", "িতকার প", "াওয়ার িব", "ষয়িট ǯকান আই", "েনর")]}]}
    plan = plan_blocks([split, block(ENGLISH, 200)])
    assert plan.method == "pymupdf+ocr_bangla" and plan.ocr_blocks == 1


class FakePage:
    def __init__(self, blocks):
        self.blocks = blocks
        self.rect = pymupdf.Rect(0, 0, 595, 842)

    def get_text(self, kind, sort=True):
        return {"blocks": self.blocks}


def test_extract_page_replaces_only_the_flagged_block(monkeypatch):
    clips = []

    def fake_ocr(page, clip=None):
        clips.append(clip)
        return "OCR_OUTPUT"

    monkeypatch.setattr(pdf_extract, "_ocr_pymupdf", fake_ocr)
    page = FakePage([block(ENGLISH), block(ENGLISH, 150), block(FOREIGN_VISUAL, 200)])
    text, method, n_ocr = extract_page_pymupdf(page)
    assert method == "pymupdf+ocr_bangla" and n_ocr == 1 and len(clips) == 1 and clips[0] is not None
    assert "OCR_OUTPUT" in text and ENGLISH in text and "ɛ" not in text


def test_extract_page_never_ocrs_a_clean_layer(monkeypatch):
    def fail(*a, **k):
        raise AssertionError("clean layer এ OCR চলা উচিত না")

    monkeypatch.setattr(pdf_extract, "_ocr_pymupdf", fail)
    text, method, n_ocr = extract_page_pymupdf(FakePage([block(ENGLISH), block(CLEAN, 200)]))
    assert method == "pymupdf" and n_ocr == 0 and CLEAN in text


NIRMALA = Path(r"C:\Windows\Fonts\Nirmala.ttc")


@pytest.mark.skipif(not NIRMALA.exists(), reason="Nirmala UI font লাগে")
def test_real_pdf_with_exact_codepoints_is_not_flagged(tmp_path):
    """Shaping ছাড়া লিখলে text layer এ হুবহু code point থাকে: আসল পরিষ্কার layer, flag হওয়া চলবে না।"""
    doc = pymupdf.open()
    page = doc.new_page()
    writer = pymupdf.TextWriter(page.rect)
    writer.fill_textbox(page.rect + (40, 40, -40, -40), CLEAN, font=pymupdf.Font(fontfile=str(NIRMALA)), fontsize=11)
    writer.write_text(page)
    doc.save(tmp_path / "clean.pdf")
    doc.close()
    doc = pymupdf.open(tmp_path / "clean.pdf")
    plan: PagePlan = plan_blocks(page_text_blocks(doc[0]))
    doc.close()
    assert plan.method == "pymupdf" and plan.layer_problem == ""


@pytest.mark.skipif(not NIRMALA.exists(), reason="Nirmala UI font লাগে")
def test_real_pdf_from_shaping_engine_is_flagged(tmp_path):
    """পরিষ্কার text ও shaping engine দিয়ে PDF হলে text layer নষ্ট হয় — এই corpus এর সমস্যার উৎস।"""
    doc = pymupdf.open()
    page = doc.new_page()
    css = "@font-face {font-family: nm; src: url(" + NIRMALA.as_posix() + ");} body {font-family: nm; font-size: 12pt;}"
    page.insert_htmlbox(page.rect + (40, 40, -40, -40), f"<p>{CLEAN}</p>", css=css, archive=pymupdf.Archive())
    doc.save(tmp_path / "shaped.pdf")
    doc.close()
    doc = pymupdf.open(tmp_path / "shaped.pdf")
    plan = plan_blocks(page_text_blocks(doc[0]))
    doc.close()
    assert plan.layer_problem == "impossible_spelling"


# ─── লাইন ধরে রাখা ────────────────────────────────────────

def test_output_lines_ignores_short_noise():
    assert output_lines("এই একটি লাইন\n|\n12\n১২৩\nabc") == 3


def test_pdf_lines_groups_spans_on_a_baseline_and_ignores_stubs():
    blocks = [block("first line of text\nsecond line of text\n3")]
    blocks[0]["lines"][0]["spans"].append({"text": "same baseline", "font": "Times", "bbox": (300, 100, 400, 110)})
    assert pdf_lines(blocks) == 2


def test_retention_cannot_exceed_one_or_hide_loss_across_pages():
    assert retention(10, 20) == 1.0
    assert retention(10, 8) == 0.8
    assert retention(0, 5) == 1.0


def test_low_retention_changes_the_bucket():
    row = audit_text(LONG_ENGLISH)
    row["line_retention"] = RETENTION_CHECK["min_line_retention"] - 0.01
    assert strict_classify(row)[0] == "suspect" and "low_line_retention" in strict_classify(row)[1]
    row["line_retention"] = RETENTION_CHECK["broken_line_retention"] - 0.01
    assert strict_classify(row)[0] == "broken"
    row["line_retention"] = ""
    assert strict_classify(row)[0] == "good"


def _legacy_pdf(path: Path, lines_per_page: list[int]) -> None:
    """legacy-font mojibake এর page: v2 এগুলো OCR করত, তাই retention মাপা হবে।"""
    doc = pymupdf.open()
    for count in lines_per_page:
        page = doc.new_page()
        for i in range(count):
            page.insert_text((72, 72 + i * 20), "h¡wm¡−cn fË¡¢çl ¢hQ¡l¢f¢a" * 2, fontsize=9)
    doc.save(path)
    doc.close()


def test_audit_retention_per_page_and_file(tmp_path):
    pdfs, text = tmp_path / "pdfs", tmp_path / "text"
    pdfs.mkdir()
    text.mkdir()
    _legacy_pdf(pdfs / "a.pdf", [10, 10])
    # page 1 এ 10 এর 10, page 2 এ 10 এর 7
    (text / "a.txt").write_text("\n".join(["বাংলা লাইন"] * 10) + "\f" + "\n".join(["বাংলা লাইন"] * 7), encoding="utf-8")
    (text / "_manifest.jsonl").write_text('{"filename": "a.pdf", "ok": true, "pages": 2, "chars": 1}\n', encoding="utf-8")
    out = audit_retention(text, pdfs, tmp_path / "pages.csv")
    row = out["a.pdf"]
    assert row["ocr_pages"] == 2 and row["pdf_lines"] == 20 and row["kept_lines"] == 17
    assert row["line_retention"] == 0.85 and row["low_retention_pages"] == 1 and row["retention_level"] == "page"
    assert (tmp_path / "pages.csv").read_text(encoding="utf-8").count("\n") == 3


def test_audit_retention_extra_lines_on_one_page_do_not_hide_loss(tmp_path):
    pdfs, text = tmp_path / "pdfs", tmp_path / "text"
    pdfs.mkdir()
    text.mkdir()
    _legacy_pdf(pdfs / "a.pdf", [10, 10])
    (text / "a.txt").write_text("\n".join(["বাংলা লাইন"] * 20) + "\f" + "\n".join(["বাংলা লাইন"] * 5), encoding="utf-8")
    (text / "_manifest.jsonl").write_text('{"filename": "a.pdf", "ok": true, "pages": 2, "chars": 1}\n', encoding="utf-8")
    assert audit_retention(text, pdfs)["a.pdf"]["line_retention"] == 0.75


def test_audit_retention_falls_back_to_file_level_on_stray_page_breaks(tmp_path):
    pdfs, text = tmp_path / "pdfs", tmp_path / "text"
    pdfs.mkdir()
    text.mkdir()
    _legacy_pdf(pdfs / "a.pdf", [10, 10])
    (text / "a.txt").write_text("\f".join(["\n".join(["বাংলা লাইন"] * 6)] * 3), encoding="utf-8")
    (text / "_manifest.jsonl").write_text('{"filename": "a.pdf", "ok": true, "pages": 2, "chars": 1}\n', encoding="utf-8")
    row = audit_retention(text, pdfs)["a.pdf"]
    assert row["retention_level"] == "file" and row["line_retention"] == 0.9 and row["low_retention_pages"] == 0


# ─── re-OCR তালিকা ─────────────────────────────────────────

def _audit_row(name: str, **values) -> dict:
    return {"filename": name, "line_retention": "", "corrupted_bangla": "", "layer_new_ocr_pages": "0",
            "layer_new_full_pages": "0", "layer_new_ocr_blocks": "0", "ocr_pages": "0", "no_text_pages": "0",
            "low_retention_pages": "0", **values}


def test_reocr_reasons_cover_each_source_and_skip_good_files():
    from ingestion.reocr_list import build, summarize
    rows = [
        _audit_row("huge.pdf", line_retention="0.99"),
        _audit_row("old.pdf", line_retention="0.99"),
        _audit_row("low.pdf", line_retention="0.90", ocr_pages="4"),
        _audit_row("layer.pdf", layer_new_ocr_pages="2", layer_new_full_pages="1", layer_new_ocr_blocks="2", corrupted_bangla="impossible_spelling"),
        _audit_row("saved.pdf", corrupted_bangla="isolated_letters"),
        _audit_row("good.pdf", line_retention="0.99"),
        _audit_row("exact.pdf", line_retention="0.95"),
    ]
    manifest = {r["filename"]: {"seconds": 10.0, "pages": 5} for r in rows}
    out = {r["filename"]: r for r in build(rows, manifest, {"huge.pdf"}, {"old.pdf"}, 0.95)}
    assert set(out) == {"huge.pdf", "old.pdf", "low.pdf", "layer.pdf", "saved.pdf"}, "ঠিক ৯৫% flag না; পরিষ্কার file বাদ"
    assert out["huge.pdf"]["reasons"] == "death_ref_58_parts_2_3" and out["old.pdf"]["reasons"] == "earlier_24"
    assert out["low.pdf"]["reasons"] == "low_line_retention"
    assert out["layer.pdf"]["reasons"] == "corrupted_text_layer", "layer এ ধরা পড়লে saved-text কারণ আলাদা গোনা হয় না"
    assert out["saved.pdf"]["reasons"] == "corrupted_saved_text"
    assert out["layer.pdf"]["est_seconds_one_worker"] > out["saved.pdf"]["est_seconds_one_worker"], "নতুন OCR এর সময় যোগ হয়"
    assert summarize(list(out.values()))["files"] == 5


def test_audit_cli_runs_as_a_script_and_keeps_old_buckets(tmp_path):
    """Script হিসেবে চালালে import path আলাদা — unit test এ এই ভুল ধরা পড়ে না।"""
    import csv
    import json
    import subprocess

    pdfs, text = tmp_path / "pdfs", tmp_path / "text"
    pdfs.mkdir()
    text.mkdir()
    _legacy_pdf(pdfs / "a.pdf", [10, 10])
    (text / "a.txt").write_text("\n".join(["বাংলা লাইন"] * 10) + "\f" + "\n".join(["বাংলা লাইন"] * 7), encoding="utf-8")
    (text / "_manifest.jsonl").write_text('{"filename": "a.pdf", "ok": true, "pages": 2, "chars": 1}\n', encoding="utf-8")
    out = tmp_path / "audit.csv"
    root = Path(__file__).resolve().parent.parent
    done = subprocess.run([sys.executable, str(root / "ingestion" / "quality_audit.py"), "--source", str(text),
                           "--pdf-dir", str(pdfs), "--out", str(out)], capture_output=True, text=True,
                          encoding="utf-8", env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"}, cwd=tmp_path)
    assert done.returncode == 0, done.stderr
    row = next(csv.DictReader(out.open(encoding="utf-8")))
    assert row["line_retention"] == "0.85" and "low_line_retention" in row["reasons"] and row["bucket_old"]
    summary = json.loads((tmp_path / "audit_summary.json").read_text(encoding="utf-8"))
    assert summary["retention"]["files_below"]["0.95"] == 1 and "buckets_old_rules" in summary
    assert (tmp_path / "audit_pages.csv").exists()
