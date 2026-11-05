"""আলাদা পরিবেশের নিরাপত্তা: production DB খোলা যায় না, লেখা যায় না, fake run baseline হয় না। api_v3 import করে না, তাই দ্রুত।"""

import ast
import json
from pathlib import Path

import pytest

from evals.fake_llm import fake_call_llm
from evals.pipeline_env import PRODUCTION_DB, ReadOnlyClient, ReadOnlyCollection, guard_db_path
from evals.pipeline_eval import RecordingLLM, extract_citations, percentile, planted_context, summarize
from evals.report_eval import render

ROOT = Path(__file__).resolve().parent.parent
API = (ROOT / "api_v3.py").read_text(encoding="utf-8")
TREE = ast.parse(API)


def test_production_db_and_anything_inside_it_is_refused(tmp_path):
    with pytest.raises(PermissionError):
        guard_db_path(PRODUCTION_DB)
    with pytest.raises(PermissionError):
        guard_db_path(PRODUCTION_DB / "sub")
    with pytest.raises(FileNotFoundError):
        guard_db_path(tmp_path / "missing")
    assert guard_db_path(tmp_path) == tmp_path.resolve()


def test_guard_sees_through_a_relative_path(tmp_path, monkeypatch):
    prod = tmp_path / "db"
    prod.mkdir()
    monkeypatch.chdir(tmp_path)
    with pytest.raises(PermissionError):
        guard_db_path(Path("db"), production=prod)


class Inner:
    name = "x"

    def count(self):
        return 7

    def query(self, **kw):
        return "q"

    def get(self, **kw):
        return "g"

    def add(self, **kw):
        raise AssertionError("write reached the database")

    upsert = update = delete = modify = add


@pytest.mark.parametrize("method", ["add", "upsert", "update", "delete", "modify"])
def test_collection_writes_are_blocked(method):
    with pytest.raises(PermissionError):
        getattr(ReadOnlyCollection(Inner()), method)


def test_collection_reads_work_and_attributes_cant_be_set():
    c = ReadOnlyCollection(Inner())
    assert (c.count(), c.query(), c.get(), c.name) == (7, "q", "g", "x")
    with pytest.raises(PermissionError):
        c.anything = 1


class InnerClient:
    def __init__(self):
        self.asked = []

    def get_collection(self, name, embedding_function=None):
        self.asked.append(name)
        return Inner()

    def delete_collection(self, name):
        raise AssertionError("write reached the database")

    create_collection = get_or_create_collection = reset = delete_collection


def test_client_redirects_only_the_judgements_name():
    inner = InnerClient()
    client = ReadOnlyClient(inner, "bangladesh_judgements_v3")
    assert isinstance(client.get_collection(name="bangladesh_judgements"), ReadOnlyCollection)
    client.get_collection(name="bangladesh_laws")
    assert inner.asked == ["bangladesh_judgements_v3", "bangladesh_laws"]


@pytest.mark.parametrize("method", ["delete_collection", "create_collection", "get_or_create_collection", "reset"])
def test_client_writes_are_blocked(method):
    with pytest.raises(PermissionError):
        getattr(ReadOnlyClient(InnerClient(), "x"), method)


def test_api_still_has_what_the_harness_relies_on():
    """api_v3 এ এগুলো বদলালে harness চুপচাপ ভুল কিছু মাপত, তাই আগেই ধরা পড়ে।"""
    functions = {n.name for n in TREE.body if isinstance(n, ast.FunctionDef)}
    assert {"call_llm", "build_sources_block", "run_pipeline", "build_graph"} <= functions
    names = {t.id for n in TREE.body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}
    assert {"legal_graph", "judgements_col", "laws_col", "judgements_bm25", "laws_bm25", "CHROMA_DB_PATH"} <= names
    assert 'os.getenv("CHROMA_DB_PATH"' in API
    assert 'get_collection(name="bangladesh_judgements"' in API
    assert "chromadb.PersistentClient(path=CHROMA_DB_PATH)" in API
    call_llm = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == "call_llm")
    assert [a.arg for a in call_llm.args.args] == ["system_prompt", "user_content", "json_mode", "schema"]


def test_file_check_catches_changes_without_any_database_library(tmp_path):
    from ingestion.integrity import snapshot_files, verify_files

    db = tmp_path / "db"
    (db / "seg").mkdir(parents=True)
    (db / "chroma.sqlite3").write_bytes(b"abc")
    (db / "seg" / "data.bin").write_bytes(b"123")
    snap = tmp_path / "snap.json"
    assert snapshot_files(db, snap)["files"] == 2
    assert verify_files(db, snap)["ok"]
    (db / "chroma.sqlite3").write_bytes(b"abd")  # একই size, ভিন্ন byte: Chroma খুললে ঠিক এমনই হয়
    got = verify_files(db, snap)
    assert not got["ok"] and got["changed"] == ["chroma.sqlite3"]
    (db / "new.bin").write_bytes(b"x")
    (db / "seg" / "data.bin").unlink()
    got = verify_files(db, snap)
    assert got["added"] == ["new.bin"] and got["missing"] == ["seg/data.bin"]


@pytest.mark.parametrize("script", ["retrieval_run.py", "retrieval_explore.py", "run_experiment.py", "tools/embedding_truncation.py", "tools/build_embedding_index.py"])
def test_evaluation_tools_refuse_the_production_database(script):
    source = (ROOT / "evals" / script).read_text(encoding="utf-8")
    assert "guard_db_path" in source
    assert "PersistentClient(path=str(guard_db_path(" in source


def test_loader_doesnt_start_a_server_or_touch_port_8000():
    source = (ROOT / "evals" / "pipeline_env.py").read_text(encoding="utf-8") + (ROOT / "evals" / "pipeline_eval.py").read_text(encoding="utf-8")
    assert "uvicorn.run" not in source
    assert "taskkill" not in source and "Stop-Process" not in source


def test_fake_llm_router_and_answer_shapes():
    router = json.loads(fake_call_llm("তুমি query router", "history\n\nবর্তমান প্রশ্ন: test?"))
    assert router["rewritten_query"] == "test?" and router["intent"] == "general"
    sources = "### Act: The Penal Code, 1860\nSection: 302\nYear: 1860\ntext\n### Case: Civil Revision No. 1 of 2020\nYear: 2020\n"
    answer = json.loads(fake_call_llm("answer", sources))
    assert answer["applicable_laws"][0]["act"] == "The Penal Code, 1860"
    assert answer["relevant_cases"][0]["case_no"] == "Civil Revision No. 1 of 2020"


def test_recording_llm_counts_calls_and_clears():
    llm = RecordingLLM(fake=lambda *a: "x")
    assert llm("s", "u", True, None) == "x"
    assert [c["provider"] for c in llm.take()] == ["fake"]
    assert llm.take() == []


def test_planted_snippet_is_only_for_that_question_and_only_in_memory():
    class Api:
        @staticmethod
        def build_sources_block(retrieved):
            return "real"

    api = Api()
    original = api.build_sources_block
    with planted_context(api, "PLANT"):
        assert "PLANT" in api.build_sources_block({})
    assert api.build_sources_block is original
    with planted_context(api, ""):
        assert api.build_sources_block({}) == "real"


def test_citations_include_those_the_pipeline_later_removed():
    state = {"structured_answer": {"relevant_cases": [{"case_no": "A"}, {"case_no": "A"}], "applicable_laws": [{"act": "X", "section": "1"}]},
             "citation_issues": {"bad_cases": [{"case_no": "B"}], "bad_laws": [{"act": "X", "section": "1"}, {"act": "Y", "section": "2"}]}}
    cases, laws = extract_citations(state)
    assert cases == ["A", "B"]
    assert [(x["act"], x["section"]) for x in laws] == [("X", "1"), ("Y", "2")]


def _row(status="ok", latency=1.0, cost=None):
    return {"status": status, "latency_s": latency, "must_mention": {"score": 1.0, "missing": []}, "must_not_claim_violations": [],
            "citations": {"cited": 1, "in_retrieved": 1, "unsupported": 0, "fabricated": 0, "recall": 1.0, "precision": 1.0, "fabricated_rate": 0.0},
            "behavior": {"expected_behavior": "answer", "abstained": False, "behaved": True}, "llm_calls": [{"provider": "fake", "input_tokens": None, "output_tokens": None, "cost_usd": cost}]}


def test_summary_reports_errors_and_unknown_cost_as_unknown():
    got = summarize([_row(latency=1.0), _row(latency=3.0), {"status": "error", "llm_calls": []}])
    assert (got["questions"], got["errors"], got["ops"]["latency_p50_s"]) == (3, 1, 2.0)
    assert got["ops"]["cost_usd_per_question"] is None
    assert got["generation"]["faithfulness"] is None


def test_known_cost_is_reported_only_when_every_call_has_one():
    assert summarize([_row(cost=0.01), _row(cost=0.03)])["ops"]["cost_usd_per_question"] == pytest.approx(0.02)
    assert summarize([_row(cost=0.01), _row(cost=None)])["ops"]["cost_usd_per_question"] is None


def test_percentile():
    assert percentile([], 0.95) is None
    assert percentile([1.0, 2.0, 3.0], 0.5) == 2.0


def _report(llm="fake", candidate=False):
    return {"collection": "v1", "dataset": "both", "llm": llm, "is_baseline_candidate": candidate, "judge_used": False, "load_seconds": 1, "peak_memory_mb": 1, "summary": summarize([_row()])}


def test_fake_run_report_says_it_isnt_about_quality():
    assert "Environment test only" in render(_report())
    assert "not measured" in render(_report())


def test_real_partial_run_is_labelled_partial():
    assert "Partial run" in render(_report("real", False))
    assert "Environment" not in render(_report("real", True))


def test_report_compares_with_a_baseline():
    md = render(_report("real", True), _report("real", True))
    assert "Baseline" in md and "+0.000" in md


def test_fake_run_cant_be_a_baseline(tmp_path, monkeypatch):
    import sys

    from evals import report_eval

    run, base = tmp_path / "r.json", tmp_path / "b.json"
    run.write_text(json.dumps(_report("real", True)), encoding="utf-8")
    base.write_text(json.dumps(_report("fake", False)), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["x", "--run", str(run), "--baseline", str(base), "--out", str(tmp_path / "o.md")])
    with pytest.raises(SystemExit, match="can't be a baseline"):
        report_eval.main()
