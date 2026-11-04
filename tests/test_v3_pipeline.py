"""
text_v3 আর v3 index এর pipeline test (docs/DECISIONS.md D-119, D-120)। OCR, server, আসল DB লাগে না।

    python -m pytest tests/test_v3_pipeline.py -q
"""

import csv
import json
import sys
from pathlib import Path

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingestion import build_text_v3, run_v3, verify_v3  # noqa: E402
from ingestion.build_text_v3 import SOURCE_COPY, SOURCE_REOCR, copy_unlisted, read_list  # noqa: E402
from ingestion.chunk_cap import plan_index  # noqa: E402
from ingestion.pdf_extract import inventory, process_all, read_manifest  # noqa: E402

# ─── index এর chunk ─────────────────────────────────────

def test_real_chunker_ids_match_plan_after_cap(tmp_path):
    from enrich_judgements import chunk_text
    (tmp_path / "a.txt").write_text(" ".join(f"w{i}" for i in range(5000)), encoding="utf-8")
    (tmp_path / "b.txt").write_text(" ".join(f"w{i}" for i in range(100)), encoding="utf-8")
    plan, texts = verify_v3.expected_chunks(tmp_path, chunk_text, 3)
    assert plan["chunks_from_text"] == len(chunk_text(texts["a.pdf"])) + 1
    assert len(plan["keep"]["a.pdf"]) == 3 and plan["keep"]["b.pdf"] == [0]


def test_compare_ids_catches_missing_and_extra_chunks():
    plan = plan_index({"a.pdf": 3}, None)
    assert verify_v3.compare_ids(plan, {"a.pdf__chunk_0", "a.pdf__chunk_1", "a.pdf__chunk_2"})["ok"]
    gone = verify_v3.compare_ids(plan, {"a.pdf__chunk_0", "a.pdf__chunk_1"})
    assert not gone["ok"] and gone["missing"] == ["a.pdf__chunk_2"]
    more = verify_v3.compare_ids(plan, {"a.pdf__chunk_0", "a.pdf__chunk_1", "a.pdf__chunk_2", "b.pdf__chunk_0"})
    assert not more["ok"] and more["extra"] == ["b.pdf__chunk_0"]


# ─── text_v3 ───────────────────────────────────────────────

def make_pdf(path: Path, pages: int = 1, label: str = "doc") -> None:
    doc = pymupdf.open()
    for i in range(pages):
        doc.new_page().insert_text((72, 72), f"{label} page {i} of this judgement, long enough to skip OCR.")
    doc.save(path)
    doc.close()


@pytest.fixture
def corpus(tmp_path):
    """text_v2 সহ তিনটা PDF, তার একটা তালিকায়।"""
    d = {name: tmp_path / name for name in ("pdfs", "v2", "v3", "parts", "quarantine")}
    for p in d.values():
        p.mkdir()
    d["failures"] = tmp_path / "failures.jsonl"
    rows = []
    for name, text in (("listed", "OLD v2 TEXT"), ("keep1", "keep one\r\nwith crlf"), ("keep2", "page 0\fpage 1")):
        make_pdf(d["pdfs"] / f"{name}.pdf", pages=2 if name == "keep2" else 1, label=name)
        (d["v2"] / f"{name}.txt").write_bytes(text.encode("utf-8"))
        chars = len((d["v2"] / f"{name}.txt").read_text(encoding="utf-8"))
        rows.append({"filename": f"{name}.pdf", "ok": True, "pages": 2 if name == "keep2" else 1, "methods": {"pymupdf": 1}, "chars": chars})
    (d["v2"] / "_manifest.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return d


def test_copy_is_byte_identical_and_records_its_source(corpus):
    counts = copy_unlisted(corpus["v2"], corpus["v3"], {"listed.pdf"})
    assert counts == {"copied": 2, "already_ok": 0, "skipped_listed": 1}
    assert (corpus["v3"] / "keep1.txt").read_bytes() == (corpus["v2"] / "keep1.txt").read_bytes(), "newline বদলানো চলবে না"
    assert not (corpus["v3"] / "listed.txt").exists()
    row = read_manifest(corpus["v3"])["keep1.pdf"]
    assert row["source_version"] == SOURCE_COPY and len(row["sha256"]) == 64


def test_copy_is_idempotent_and_never_touches_the_source(corpus):
    before = {p.name: p.read_bytes() for p in corpus["v2"].iterdir()}
    copy_unlisted(corpus["v2"], corpus["v3"], {"listed.pdf"})
    manifest = (corpus["v3"] / "_manifest.jsonl").read_bytes()
    counts = copy_unlisted(corpus["v2"], corpus["v3"], {"listed.pdf"})
    assert counts["copied"] == 0 and counts["already_ok"] == 2
    assert (corpus["v3"] / "_manifest.jsonl").read_bytes() == manifest
    assert {p.name: p.read_bytes() for p in corpus["v2"].iterdir()} == before


def test_copy_repairs_a_damaged_copy(corpus):
    copy_unlisted(corpus["v2"], corpus["v3"], {"listed.pdf"})
    (corpus["v3"] / "keep1.txt").write_bytes(b"damaged")
    assert copy_unlisted(corpus["v2"], corpus["v3"], {"listed.pdf"})["copied"] == 1
    assert (corpus["v3"] / "keep1.txt").read_bytes() == (corpus["v2"] / "keep1.txt").read_bytes()


def test_copy_refuses_to_write_into_its_own_source(corpus):
    with pytest.raises(ValueError):
        copy_unlisted(corpus["v2"], corpus["v2"], set())


def test_reocr_runs_only_listed_files_and_records_why(corpus):
    process_all(corpus["pdfs"], corpus["v3"], corpus["parts"], corpus["failures"], corpus["quarantine"], workers=1,
                only={"listed.pdf"}, manifest_extra=lambda name: {"source_version": SOURCE_REOCR, "reocr_reasons": "because"})
    assert sorted(p.name for p in corpus["v3"].glob("*.txt")) == ["listed.txt"]
    row = read_manifest(corpus["v3"])["listed.pdf"]
    assert row["source_version"] == SOURCE_REOCR and row["reocr_reasons"] == "because"
    assert "listed page 0" in (corpus["v3"] / "listed.txt").read_text(encoding="utf-8")
    assert (corpus["v2"] / "listed.txt").read_text(encoding="utf-8") == "OLD v2 TEXT"


def test_inventory_only_limits_the_view(corpus):
    state = inventory(corpus["pdfs"], corpus["v3"], corpus["failures"], only={"keep1.pdf"})
    assert state["todo"] == ["keep1.pdf"]
    assert len(inventory(corpus["pdfs"], corpus["v3"], corpus["failures"])["todo"]) == 3


def test_large_listed_file_records_its_source_after_merge(corpus):
    make_pdf(corpus["pdfs"] / "huge.pdf", pages=7, label="huge")
    process_all(corpus["pdfs"], corpus["v3"], corpus["parts"], corpus["failures"], corpus["quarantine"], workers=1,
                only={"huge.pdf"}, shard_threshold=3, shard_size=3,
                manifest_extra=lambda name: {"source_version": SOURCE_REOCR, "reocr_reasons": "big"})
    row = read_manifest(corpus["v3"])["huge.pdf"]
    assert row["source_version"] == SOURCE_REOCR and row["pages"] == 7 and row["shards"] == 3


def _full_v3(corpus):
    process_all(corpus["pdfs"], corpus["v3"], corpus["parts"], corpus["failures"], corpus["quarantine"], workers=1,
                only={"listed.pdf"}, manifest_extra=lambda name: {"source_version": SOURCE_REOCR, "reocr_reasons": "x"})
    copy_unlisted(corpus["v2"], corpus["v3"], {"listed.pdf"})


def test_verify_passes_a_complete_corpus(corpus):
    _full_v3(corpus)
    report = verify_v3.verify_outputs(corpus["v3"], corpus["pdfs"], corpus["v2"], {"listed.pdf"})
    assert report["ok"], report
    assert report["by_source"] == {SOURCE_REOCR: 1, SOURCE_COPY: 2} and report["text_files"] == 3


def test_verify_catches_a_missing_output(corpus):
    _full_v3(corpus)
    (corpus["v3"] / "keep2.txt").unlink()
    report = verify_v3.verify_outputs(corpus["v3"], corpus["pdfs"], corpus["v2"], {"listed.pdf"})
    assert not report["ok"] and report["missing_outputs"] == ["keep2.pdf"]


def test_verify_catches_a_copy_that_differs_from_v2(corpus):
    _full_v3(corpus)
    original = (corpus["v3"] / "keep1.txt").read_text(encoding="utf-8")
    (corpus["v3"] / "keep1.txt").write_text(original.upper(), encoding="utf-8", newline="")
    report = verify_v3.verify_outputs(corpus["v3"], corpus["pdfs"], corpus["v2"], {"listed.pdf"})
    assert not report["ok"] and report["copy_differs_from_v2"] == ["keep1.pdf"]


def test_verify_catches_a_listed_file_that_was_only_copied(corpus):
    copy_unlisted(corpus["v2"], corpus["v3"], set())
    report = verify_v3.verify_outputs(corpus["v3"], corpus["pdfs"], corpus["v2"], {"listed.pdf"})
    assert not report["ok"] and "listed.pdf" in report["wrong_source"]


def test_verify_catches_a_wrong_page_count(corpus):
    _full_v3(corpus)
    make_pdf(corpus["pdfs"] / "keep1.pdf", pages=3, label="keep1")
    report = verify_v3.verify_outputs(corpus["v3"], corpus["pdfs"], corpus["v2"], {"listed.pdf"})
    assert not report["ok"] and report["page_count_mismatch"]["keep1.pdf"] == {"pdf": 3, "manifest": 1}


def test_read_list_returns_the_reasons(tmp_path):
    path = tmp_path / "list.csv"
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["filename", "reasons"])
        writer.writeheader()
        writer.writerow({"filename": "a.pdf", "reasons": "x;y"})
    assert read_list(path) == {"a.pdf": "x;y"}


# ─── orchestrator ──────────────────────────────────────────

@pytest.fixture
def orchestrator(monkeypatch, tmp_path):
    """কিছু চালায় না, শুধু কোন command কোন ক্রমে চলত সেটা লেখে।"""
    calls = []
    state = {"valid": ["a.pdf"], "todo": [], "invalid": {}, "failed": []}
    monkeypatch.setattr(run_v3, "run", lambda args: calls.append(args[0]) or 0)
    monkeypatch.setattr(run_v3, "inventory", lambda *a, **k: state)
    monkeypatch.setattr(run_v3, "expected_files", lambda: ["a.pdf"])
    monkeypatch.setattr(run_v3, "read_list", lambda: {"a.pdf": "x"})
    monkeypatch.setattr(run_v3, "api_is_running", lambda: False)
    monkeypatch.setattr(run_v3, "keep_awake", lambda: None)
    monkeypatch.setattr(run_v3, "backup_chroma", lambda target: calls.append("backup"))
    monkeypatch.setattr(run_v3, "collection_count", lambda name: None)
    monkeypatch.setattr(run_v3, "acquire_lock", lambda path: object())
    monkeypatch.setattr(run_v3, "TEXT_BASELINE", tmp_path / "text.json")
    monkeypatch.setattr(run_v3, "CHROMA_BASELINE", tmp_path / "chroma.json")
    monkeypatch.setattr(run_v3.time, "sleep", lambda s: None)
    return calls, state


def test_pipeline_runs_in_order_with_baselines_first(orchestrator):
    calls, _ = orchestrator
    assert run_v3.main(["--workers", "2"]) == 0
    assert calls == ["ingestion/integrity.py", "ingestion/integrity.py", "ingestion/build_text_v3.py", "ingestion/build_text_v3.py",
                     "ingestion/quality_audit.py", "backup", "enrich_judgements.py", "ingestion/verify_v3.py"]


def test_pipeline_never_audits_or_indexes_incomplete_text(orchestrator):
    calls, state = orchestrator
    state["valid"], state["todo"] = [], ["a.pdf"]
    assert run_v3.main(["--workers", "2"]) == 2
    assert "ingestion/quality_audit.py" not in calls and "enrich_judgements.py" not in calls


def test_pipeline_never_indexes_after_a_failed_file(orchestrator):
    calls, state = orchestrator
    state["valid"], state["todo"], state["failed"] = [], ["a.pdf"], ["a.pdf"]
    assert run_v3.main(["--workers", "2"]) == 2
    assert "enrich_judgements.py" not in calls


def test_pipeline_refuses_to_start_with_the_api_running(orchestrator, monkeypatch):
    calls, _ = orchestrator
    monkeypatch.setattr(run_v3, "api_is_running", lambda: True)
    assert run_v3.main(["--workers", "2"]) == 2
    assert calls == []


def test_pipeline_does_not_rebuild_an_existing_collection_but_still_verifies(orchestrator, monkeypatch):
    calls, _ = orchestrator
    monkeypatch.setattr(run_v3, "collection_count", lambda name: 123)
    assert run_v3.main(["--workers", "2"]) == 0
    assert "enrich_judgements.py" not in calls and "backup" not in calls and calls[-1] == "ingestion/verify_v3.py"


def test_pipeline_alarms_when_verification_fails(orchestrator, monkeypatch):
    calls, _ = orchestrator
    monkeypatch.setattr(run_v3, "run", lambda args: calls.append(args[0]) or (1 if args[0] == "ingestion/verify_v3.py" else 0))
    assert run_v3.main(["--workers", "2"]) == 4


def test_existing_baselines_are_not_retaken(orchestrator, tmp_path):
    calls, _ = orchestrator
    (tmp_path / "text.json").write_text("{}")
    (tmp_path / "chroma.json").write_text("{}")
    assert run_v3.main(["--workers", "2"]) == 0
    assert "ingestion/integrity.py" not in calls, "মাঝপথের অবস্থা baseline হয়ে গেলে প্রমাণের মূল্য থাকে না"


def test_paths_never_point_the_new_build_at_v2():
    assert build_text_v3.PATHS["out_dir"].name == "text_v3" and build_text_v3.PATHS["parts_dir"].name == "text_v3_parts"
    assert build_text_v3.PATHS["source_dir"].name == "text_v2"
    assert verify_v3.V3_COLLECTION == "bangladesh_judgements_v3"


# ─── আসল Chroma দিয়ে index ─────────────────────────────────

class FakeEmbedding:
    """Model load না করে নির্দিষ্ট vector; Chroma, chunking, সীমা আর swap সবই আসল।"""

    def __init__(self, *a, **k):
        pass

    def __call__(self, input):
        return [[float(len(t) % 7), float(len(t) % 11), 1.0] for t in input]

    @staticmethod
    def name():
        return "fake-for-tests"

    def embed_documents(self, input):
        return self(input)

    def embed_query(self, input):
        return self(input)

    def is_legacy(self):
        return True

    def get_config(self):
        return {}

    @staticmethod
    def build_from_config(config):
        return FakeEmbedding()


def _words(n: int, label: str) -> str:
    return " ".join(f"{label}{i}" for i in range(n))


def test_v3_build_through_real_chroma_caps_and_verifies(tmp_path, monkeypatch):
    """Death Ref এর তিন PDF + একটা সাধারণ file: সীমা, metadata, swap, আর verify এর হিসাব সব মেলে কিনা। ৬ ঘণ্টা OCR এর
    পরে এই ধাপে bug ধরা পড়লে অনেক দেরি।"""
    import chromadb

    import enrich_judgements as ej

    text_dir, db = tmp_path / "text_v3", tmp_path / "db"
    text_dir.mkdir()
    for name, words in (("769454_DeathRef58of2013_1", 12000), ("769454_DeathRef58of2013_2", 14000),
                        ("769454_DeathRef58of2013_3", 2000), ("ordinary", 3000)):
        (text_dir / f"{name}.txt").write_text(_words(words, name[:3]), encoding="utf-8")
    report = tmp_path / "plan.json"
    monkeypatch.setattr(ej, "TEXT_DIRS", {"v3": str(text_dir)})
    monkeypatch.setattr(ej, "AUDIT_FILES", {"v3": str(tmp_path / "no_audit.csv")})
    monkeypatch.setattr(ej, "CHROMA_DB_PATH", str(db))
    monkeypatch.setattr(ej, "write_cap_report", lambda plan: report.write_text(json.dumps({k: v for k, v in plan.items() if k != "keep"})))
    monkeypatch.setattr(ej.embedding_functions, "SentenceTransformerEmbeddingFunction", FakeEmbedding)

    ej.build_judgements_collection("v3", "regex", "bangladesh_judgements_v3", max_chunks_per_case=40)

    client = chromadb.PersistentClient(path=str(db))
    names = [c if isinstance(c, str) else c.name for c in client.list_collections()]
    assert names == ["bangladesh_judgements_v3"], "__building থেকে যাওয়া চলবে না"
    plan, texts = verify_v3.expected_chunks(text_dir, ej.chunk_text, 40)
    ids = verify_v3.collection_ids(db, "bangladesh_judgements_v3")
    assert verify_v3.compare_ids(plan, ids)["ok"]
    assert sum(1 for i in ids if i.startswith("769454_")) == 40, "তিনটা PDF মিলে এক সীমা"
    assert sum(1 for i in ids if i.startswith("ordinary")) == len(ej.chunk_text(texts["ordinary.pdf"])), "সাধারণ file কাটা পড়বে না"
    assert verify_v3.verify_sample_documents(db, "bangladesh_judgements_v3", ids, texts, ej.chunk_text)["ok"]
    col = client.get_collection("bangladesh_judgements_v3")
    metas = col.get(limit=1000, include=["metadatas"])["metadatas"]
    assert all(m["case_capped"] == m["filename"].startswith("769454_") for m in metas)
    assert json.loads(report.read_text())["capped_cases"]["death_reference_58_of_2013"]["chunks_indexed"] == 40
    with pytest.raises(SystemExit):
        ej.build_judgements_collection("v3", "regex", "bangladesh_judgements_v3", max_chunks_per_case=40)


def test_cap_does_not_touch_v1_or_v2_builds(tmp_path, monkeypatch):
    import chromadb

    import enrich_judgements as ej

    text_dir, db = tmp_path / "text_v2", tmp_path / "db"
    text_dir.mkdir()
    (text_dir / "769454_DeathRef58of2013_1.txt").write_text(_words(9000, "a"), encoding="utf-8")
    monkeypatch.setattr(ej, "TEXT_DIRS", {"v2": str(text_dir)})
    monkeypatch.setattr(ej, "AUDIT_FILES", {"v2": str(tmp_path / "no_audit.csv")})
    monkeypatch.setattr(ej, "CHROMA_DB_PATH", str(db))
    monkeypatch.setattr(ej.embedding_functions, "SentenceTransformerEmbeddingFunction", FakeEmbedding)
    ej.build_judgements_collection("v2", "regex", "some_v2")
    col = chromadb.PersistentClient(path=str(db)).get_collection("some_v2")
    assert col.count() == len(ej.chunk_text(_words(9000, "a")))
    assert "case_capped" not in col.get(limit=1, include=["metadatas"])["metadatas"][0]
