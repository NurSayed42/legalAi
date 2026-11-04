"""
Extraction crash-safety test — আসল PDF বানিয়ে, আসল worker process এ (Windows spawn সহ)। Tesseract
লাগে না: সব page এ text layer আছে।

    python -m pytest tests/test_pdf_extract.py -q
"""

import json
import os
import sys
from functools import partial
from pathlib import Path

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingestion import integrity, pdf_extract, run_phase1
from ingestion.pdf_extract import (
    MANIFEST_NAME,
    Unit,
    acquire_lock,
    atomic_write_text,
    inventory,
    load_shard,
    output_is_valid,
    process_all,
    read_manifest,
    repair_jsonl_tail,
    run_unit,
    run_units,
    shard_path,
    shard_ranges,
)


def make_pdf(path: Path, pages: int = 1, label: str = "doc") -> Path:
    doc = pymupdf.open()
    for i in range(pages):
        doc.new_page().insert_text((72, 72), f"{label} page {i} of this judgement, long enough to skip OCR.")
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def dirs(tmp_path):
    d = {name: tmp_path / name for name in ("pdfs", "out", "parts", "quarantine")}
    for p in d.values():
        p.mkdir()
    d["failures"] = tmp_path / "failures.jsonl"
    return d


def run_all(d, **kwargs):
    return process_all(d["pdfs"], d["out"], d["parts"], d["failures"], d["quarantine"], workers=2, **kwargs)


# ─── crash-injecting runners (top-level, কারণ worker process এ pickle হয়) ──

def _die_on_bad(unit, out_dir, parts_dir):
    if "bad" in unit.name:
        os._exit(7)
    return run_unit(unit, out_dir, parts_dir)


# ─── atomic write / manifest ──────────────────────────────

def test_atomic_write_keeps_old_file_on_failure(tmp_path, monkeypatch):
    target = tmp_path / "a.txt"
    atomic_write_text(target, "original")

    def boom(*_):
        raise OSError("disk full")

    monkeypatch.setattr(pdf_extract.os, "fsync", boom)
    with pytest.raises(OSError):
        atomic_write_text(target, "half written")
    assert target.read_text(encoding="utf-8") == "original"
    assert [p.name for p in tmp_path.iterdir()] == ["a.txt"]


def test_repair_tail_keeps_rows_after_torn_write(tmp_path):
    path = tmp_path / "m.jsonl"
    path.write_bytes(b'{"filename": "a.pdf"}\n{"filename": "b.pd')
    repair_jsonl_tail(path)
    pdf_extract.append_jsonl(path, {"filename": "c.pdf"})
    assert [r["filename"] for r in pdf_extract.read_jsonl(path)] == ["a.pdf", "c.pdf"]


def test_lock_blocks_second_run(tmp_path):
    first = acquire_lock(tmp_path / "x.lock")
    assert first is not None
    assert acquire_lock(tmp_path / "x.lock") is None
    first.close()
    second = acquire_lock(tmp_path / "x.lock")
    assert second is not None
    second.close()


# ─── resume validation ────────────────────────────────────

def test_output_validation_rejects_truncated_and_unrecorded(tmp_path):
    txt = tmp_path / "a.txt"
    txt.write_text("complete text", encoding="utf-8")
    assert output_is_valid(txt, {"ok": True, "chars": 13})[0]
    assert not output_is_valid(txt, None)[0]
    assert not output_is_valid(txt, {"ok": True, "chars": 500})[0]      # crash এ কাটা পড়া
    txt.write_text("  \n", encoding="utf-8")
    assert not output_is_valid(txt, {"ok": True, "chars": 3})[0]


def test_rerun_is_idempotent_and_leaves_outputs_untouched(dirs):
    make_pdf(dirs["pdfs"] / "one.pdf", 2)
    make_pdf(dirs["pdfs"] / "two.pdf", 3)
    assert run_all(dirs)["extracted"] == 2

    before = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in dirs["out"].glob("*.txt")}
    rows_before = (dirs["out"] / MANIFEST_NAME).read_text(encoding="utf-8")
    assert run_all(dirs) == {}
    assert {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in dirs["out"].glob("*.txt")} == before
    assert (dirs["out"] / MANIFEST_NAME).read_text(encoding="utf-8") == rows_before


def test_truncated_output_is_quarantined_not_deleted_then_redone(dirs):
    make_pdf(dirs["pdfs"] / "one.pdf", 2)
    make_pdf(dirs["pdfs"] / "good.pdf", 1)
    run_all(dirs)
    good_mtime = (dirs["out"] / "good.txt").stat().st_mtime_ns
    full = (dirs["out"] / "one.txt").read_text(encoding="utf-8")

    (dirs["out"] / "one.txt").write_text(full[:10], encoding="utf-8")   # crash এ কাটা পড়া file
    assert list(inventory(dirs["pdfs"], dirs["out"], dirs["failures"])["invalid"]) == ["one.pdf"]

    run_all(dirs)
    saved = list(dirs["quarantine"].glob("one.*.txt"))
    assert len(saved) == 1 and saved[0].read_text(encoding="utf-8") == full[:10]
    assert (dirs["out"] / "one.txt").read_text(encoding="utf-8") == full
    assert (dirs["out"] / "good.txt").stat().st_mtime_ns == good_mtime
    state = inventory(dirs["pdfs"], dirs["out"], dirs["failures"])
    assert sorted(state["valid"]) == ["good.pdf", "one.pdf"] and not state["invalid"]


def test_corrupt_pdf_is_recorded_and_others_continue(dirs):
    make_pdf(dirs["pdfs"] / "fine.pdf")
    (dirs["pdfs"] / "corrupt.pdf").write_bytes(b"not really a pdf")
    assert run_all(dirs) == {"extracted": 1, "failed": 1}
    rows = pdf_extract.read_jsonl(dirs["failures"])
    assert rows[0]["filename"] == "corrupt.pdf" and rows[0]["stage"] == "pdf_extract"

    state = inventory(dirs["pdfs"], dirs["out"], dirs["failures"])
    assert state["failed"] == ["corrupt.pdf"]
    assert run_all(dirs) == {}                              # আগে ব্যর্থ file skip
    assert run_all(dirs, retry_failed=True) == {"failed": 1}


# ─── worker crash isolation ───────────────────────────────

def test_worker_crash_does_not_stop_the_run(dirs):
    names = ["a.pdf", "bad.pdf", "c.pdf", "d.pdf", "e.pdf"]
    for n in names:
        make_pdf(dirs["pdfs"] / n)
    runner = partial(_die_on_bad, out_dir=str(dirs["out"]), parts_dir=str(dirs["parts"]))
    done, gave_up = [], []
    run_units([Unit(str(dirs["pdfs"] / n)) for n in names], 2, runner,
              lambda unit, row: done.append(unit.name), lambda unit: gave_up.append(unit.name), pool_units=5)
    assert sorted(done) == ["a.pdf", "c.pdf", "d.pdf", "e.pdf"]
    assert gave_up == ["bad.pdf"]


def test_process_all_records_crashing_pdf_and_finishes_rest(dirs):
    for n in ("a.pdf", "bad.pdf", "c.pdf"):
        make_pdf(dirs["pdfs"] / n)
    runner = partial(_die_on_bad, out_dir=str(dirs["out"]), parts_dir=str(dirs["parts"]))
    assert run_all(dirs, runner=runner) == {"extracted": 2, "failed": 1}
    rows = pdf_extract.read_jsonl(dirs["failures"])
    assert [(r["stage"], r["filename"]) for r in rows] == [("worker_crash", "bad.pdf")]
    assert (dirs["out"] / "a.txt").exists() and not (dirs["out"] / "bad.txt").exists()


# ─── page-range checkpoints ───────────────────────────────

def test_shard_ranges_cover_every_page_once():
    assert shard_ranges(12, 5) == [(0, 5), (5, 10), (10, 12)]
    assert shard_ranges(10, 5) == [(0, 5), (5, 10)]


def test_large_file_merges_in_page_order(dirs):
    make_pdf(dirs["pdfs"] / "big.pdf", 12, "big")
    assert run_all(dirs, shard_threshold=5, shard_size=5)["extracted"] == 1
    text = (dirs["out"] / "big.txt").read_text(encoding="utf-8")
    pages = text.split("\f")
    assert len(pages) == 12
    assert all(f"big page {i} of" in pages[i] for i in range(12))
    row = read_manifest(dirs["out"])["big.pdf"]
    assert row["pages"] == 12 and row["shards"] == 3 and row["chars"] == len(text)
    assert inventory(dirs["pdfs"], dirs["out"], dirs["failures"])["valid"] == ["big.pdf"]


def test_large_file_resumes_from_checkpoints(dirs):
    pdf = make_pdf(dirs["pdfs"] / "big.pdf", 12, "big")
    # আগের run এর checkpoint: প্রথম shard হয়ে গেছে, চিহ্ন দিয়ে বোঝা যাবে আবার চলেনি
    marker = {"start": 0, "end": 5, "pages": [f"CHECKPOINT {i}" for i in range(5)], "methods": {"pymupdf": 5},
              "failed_pages": [], "ocr_blocks": 0, "seconds": 1.0}
    target = shard_path(dirs["parts"], pdf.stem, 0, 5)
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(marker), encoding="utf-8")

    run_all(dirs, shard_threshold=5, shard_size=5)
    pages = (dirs["out"] / "big.txt").read_text(encoding="utf-8").split("\f")
    assert pages[0] == "CHECKPOINT 0" and pages[4] == "CHECKPOINT 4"
    assert "big page 5 of" in pages[5]


def test_damaged_checkpoint_is_not_trusted(tmp_path):
    path = tmp_path / "s.json"
    path.write_text('{"start": 0, "end": 5, "pages": ["a"', encoding="utf-8")
    assert load_shard(path, 0, 5) is None
    path.write_text(json.dumps({"start": 0, "end": 5, "pages": ["only one"]}), encoding="utf-8")
    assert load_shard(path, 0, 5) is None


# ─── orchestrator gate ────────────────────────────────────

def test_gate_refuses_incomplete_text():
    expected = ["a.pdf", "b.pdf"]
    assert run_phase1.extraction_gate(expected, {"valid": ["a.pdf", "b.pdf"], "todo": [], "invalid": {}, "failed": []})[0]
    assert not run_phase1.extraction_gate(expected, {"valid": ["a.pdf"], "todo": ["b.pdf"], "invalid": {}, "failed": []})[0]
    assert not run_phase1.extraction_gate(expected, {"valid": ["a.pdf", "b.pdf"], "todo": [], "invalid": {"c.pdf": "x"}, "failed": []})[0]
    failed = {"valid": ["a.pdf"], "todo": ["b.pdf"], "invalid": {}, "failed": ["b.pdf"]}
    assert not run_phase1.extraction_gate(expected, failed)[0]
    assert run_phase1.extraction_gate(expected, failed, allow_failed=True)[0]


def test_orchestrator_never_audits_or_indexes_after_incomplete_extraction(monkeypatch, tmp_path):
    """পুরনো run_phase1 return code উপেক্ষা করত: extraction ভেঙে গেলেও audit আর v2 build চলত।"""
    calls = []
    monkeypatch.setattr(run_phase1, "run", lambda args: calls.append(args[0]) or 1)
    monkeypatch.setattr(run_phase1, "acquire_lock", lambda path: object())
    monkeypatch.setattr(run_phase1, "keep_awake", lambda: None)
    monkeypatch.setattr(run_phase1, "run_extraction", lambda workers: calls.append("extract"))
    monkeypatch.setattr(run_phase1, "expected_files", lambda: ["a.pdf", "b.pdf"])
    monkeypatch.setattr(run_phase1, "PDF_DIR", tmp_path)
    (tmp_path / "a.pdf").touch()
    (tmp_path / "b.pdf").touch()
    monkeypatch.setattr(run_phase1, "inventory",
                        lambda *a, **k: {"valid": ["a.pdf"], "todo": ["b.pdf"], "invalid": {}, "failed": []})
    assert run_phase1.main([]) == 2
    assert calls == ["extract"]


def test_orchestrator_stops_if_v1_fingerprint_changes(monkeypatch, tmp_path):
    monkeypatch.setattr(run_phase1, "acquire_lock", lambda path: object())
    monkeypatch.setattr(run_phase1, "keep_awake", lambda: None)
    monkeypatch.setattr(run_phase1, "run_extraction", lambda workers: None)
    monkeypatch.setattr(run_phase1, "expected_files", lambda: [])
    monkeypatch.setattr(run_phase1, "inventory", lambda *a, **k: {"valid": [], "todo": [], "invalid": {}, "failed": []})
    monkeypatch.setattr(run_phase1, "collection_count", lambda name: 100)   # v2 আগে থেকেই আছে
    monkeypatch.setattr(run_phase1, "run", lambda args: 1 if "verify-chroma" in args else 0)
    assert run_phase1.main([]) == 4


# ─── integrity ────────────────────────────────────────────

def test_text_snapshot_detects_changed_and_missing_files(tmp_path):
    text_dir = tmp_path / "t"
    text_dir.mkdir()
    (text_dir / "a.txt").write_text("alpha", encoding="utf-8")
    (text_dir / "b.txt").write_text("beta", encoding="utf-8")
    snap = tmp_path / "snap.json"
    assert integrity.snapshot_text(text_dir, snap)["files"] == 2
    assert integrity.verify_text(text_dir, snap)["ok"]

    (text_dir / "new.txt").write_text("new files are fine", encoding="utf-8")
    assert integrity.verify_text(text_dir, snap)["ok"]

    (text_dir / "a.txt").write_text("alphX", encoding="utf-8")     # একই size, আলাদা content
    (text_dir / "b.txt").unlink()
    result = integrity.verify_text(text_dir, snap)
    assert not result["ok"] and result["changed"] == ["a.txt"] and result["missing"] == ["b.txt"]


def _small_collection(db_path: Path):
    import chromadb
    col = chromadb.PersistentClient(path=str(db_path)).get_or_create_collection("guarded", metadata={"hnsw:space": "cosine"})
    col.upsert(ids=[f"d{i}" for i in range(20)], documents=[f"text {i}" for i in range(20)],
               metadatas=[{"n": i} for i in range(20)], embeddings=[[float(i + 1), 1.0, 2.0, 3.0] for i in range(20)])
    return col


def test_chroma_verify_passes_when_untouched_and_catches_any_change(tmp_path):
    db = tmp_path / "db"
    col = _small_collection(db)
    snap = tmp_path / "snap.json"
    integrity.snapshot_chroma(db, snap, ["guarded"])
    assert integrity.verify_chroma(db, snap)["ok"]

    col.upsert(ids=["d3"], documents=["text 3"], metadatas=[{"n": 3}], embeddings=[[4.0, 1.0, 2.0, 3.5]])
    result = integrity.verify_chroma(db, snap)["collections"]["guarded"]
    assert result["vectors_sha256"] is False and result["content_sha256"] is True

    col.upsert(ids=["d3"], documents=["changed text"], metadatas=[{"n": 3}], embeddings=[[4.0, 1.0, 2.0, 3.0]])
    result = integrity.verify_chroma(db, snap)["collections"]["guarded"]
    assert result["content_sha256"] is False and result["vectors_sha256"] is True

    col.delete(ids=["d0"])
    assert not integrity.verify_chroma(db, snap)["ok"]


def test_chroma_verify_fails_on_old_baseline_without_vector_hash(tmp_path):
    """পুরনো baseline এ vector hash নেই — চুপচাপ পাস না করে fail করবে।"""
    db = tmp_path / "db"
    _small_collection(db)
    snap = tmp_path / "snap.json"
    integrity.snapshot_chroma(db, snap, ["guarded"])
    data = json.loads(snap.read_text(encoding="utf-8"))
    del data["guarded"]["vectors_sha256"]
    snap.write_text(json.dumps(data), encoding="utf-8")
    assert not integrity.verify_chroma(db, snap)["ok"]


def test_ocr_keeps_noise_like_rows(monkeypatch):
    """Default Tesseract বাংলার লাইন 'noise' ধরে ফেলে দেয়; flag দুইটা OCR call এ যাওয়া চাই।"""
    import pytesseract
    seen = {}

    def fake(img, lang, config, timeout):
        seen.update(lang=lang, config=config, timeout=timeout)
        return "text"

    monkeypatch.setattr(pytesseract, "image_to_string", fake)
    assert pdf_extract._ocr_image(object()) == "text"
    assert "textord_noise_rejrows=0" in seen["config"] and "textord_noise_rejwords=0" in seen["config"]
    assert seen["lang"] == "ben+eng" and seen["timeout"] == pdf_extract.CONFIG["ocr_timeout_s"]


def test_block_ocr_uses_border_and_single_block_layout_but_full_page_does_not(monkeypatch):
    """টাইট এক-লাইনের crop এ default layout কিছুই ফেরত দিতে পারে; দুইটা flag দুই জায়গাতেই থাকা চাই।"""
    import pytesseract
    calls = []

    def fake(img, lang, config, timeout):
        calls.append((img.size, config))
        return "text"

    monkeypatch.setattr(pytesseract, "image_to_string", fake)
    doc = pymupdf.open()
    page = doc.new_page()
    clip = pymupdf.Rect(72, 72, 272, 92)
    pdf_extract._ocr_pymupdf(page, clip=clip)
    pdf_extract._ocr_pymupdf(page)
    plain = page.get_pixmap(dpi=pdf_extract.CONFIG["ocr_dpi"], clip=clip)
    doc.close()
    (block_size, block_config), (_, page_config) = calls
    border = pdf_extract.CONFIG["ocr_block_border_px"]
    assert block_size == (plain.width + 2 * border, plain.height + 2 * border)
    assert "--psm 6" in block_config and "--psm" not in page_config
    assert all("textord_noise_rejrows=0" in c for _, c in calls)
