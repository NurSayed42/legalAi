"""retrieval/ এর হিসাব api_v3 এর সমান কিনা। api_v3 import করলে পুরো model আর DB load হয়, তাই source থেকে শুধু
সেই function গুলো বের করে চালানো হয়। api_v3 বদলালে এখানে ধরা পড়বে, baseline চুপচাপ সরে যাবে না।"""

import ast
import json
import re
from collections import defaultdict
from pathlib import Path

import pytest

from retrieval.hybrid import (
    TOKEN_RE,
    BM25Index,
    ChunkStore,
    RankedChunk,
    collapse_to_cases,
    precedent_weight,
    rrf_as_api,
    rrf_chunks,
    tokenize,
)
from retrieval.rerank import CrossEncoderReranker, pair_key

ROOT = Path(__file__).resolve().parent.parent
API_TREE = ast.parse((ROOT / "api_v3.py").read_text(encoding="utf-8"))


def api_const(name: str):
    for node in API_TREE.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise KeyError(name)


def api_function(name: str, namespace: dict):
    for node in API_TREE.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            exec(compile(ast.Module([node], []), "api_v3", "exec"), namespace)
            return namespace[name]
    raise KeyError(name)


def chunk(cid: str, filename: str) -> RankedChunk:
    return RankedChunk(chunk_id=cid, filename=filename, case_key=filename, score=0.0)


@pytest.fixture(scope="module")
def config() -> dict:
    return json.loads((ROOT / "evals" / "retrieval_config.json").read_text(encoding="utf-8"))


def test_config_matches_api_constants(config):
    assert config["embed_model"] == api_const("EMBED_MODEL")
    assert config["rerank_model"] == api_const("RERANK_MODEL")
    assert config["rerank_max_length"] == api_const("RERANK_MAX_LENGTH")
    assert config["rrf_k"] == api_const("RRF_K")
    assert config["api_retrieve_n"] == api_const("N_RETRIEVE")
    assert config["rerank_api_threshold"] == api_const("RERANK_THRESHOLD")


@pytest.mark.parametrize("text", [
    "দেনমোহর ও ভরণপোষণের দাবি।", "Section 302, Penal Code.", "ধারা ৩০২(ক) ZWNJ‌test", "W.P. No.12827/2023", "রায়ে বলা হয়েছে",
])
def test_tokenizer_matches_api(text):
    ns = {"re": re}
    node = next(n for n in API_TREE.body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "_TOKEN_RE")
    exec(compile(ast.Module([node], []), "api_v3", "exec"), ns)
    assert tokenize(text) == api_function("_tokenize", ns)(text)
    assert TOKEN_RE.pattern == ns["_TOKEN_RE"].pattern


@pytest.mark.parametrize("meta", [{"division": "AD", "year": 2020}, {"division": "HCD", "year": 1975}, {"division": "HCD"}, {"year": 2030}, {}])
def test_precedent_weight_matches_api(meta):
    assert precedent_weight(meta) == api_function("_precedent_weight", {})(meta)


def test_rrf_as_api_matches_api_fusion():
    # api_v3 এ item {"text","meta"}; ফল একই ক্রম আর score হওয়া চাই
    a, b, c = "a.pdf", "b.pdf", "c.pdf"
    years = {a: 2000, b: 2020, c: 1990}
    lists = [[(a, 0), (b, 0), (a, 1), (c, 0)], [(b, 0), (a, 2), (b, 1)]]
    ids = [f"{f}__chunk_{i}" for f in years for i in range(3)]
    store = ChunkStore(ids, ["x"] * len(ids), [{"filename": x.split("__")[0], "year": years[x.split("__")[0]]} for x in ids])
    mine = rrf_as_api([[chunk(f"{f}__chunk_{i}", f) for f, i in lst] for lst in lists], 60, store)

    ns = {"defaultdict": defaultdict, "RRF_K": 60}
    api_function("_precedent_weight", ns)
    api_function("_key_of", ns)
    api_function("_rrf", ns)
    fuse = api_function("_fuse_cases", ns)
    theirs = fuse([[{"text": "x", "meta": {"filename": f, "year": years[f]}} for f, _ in lst] for lst in lists])
    assert [x.filename for x in mine] == [t["meta"]["filename"] for t in theirs]
    assert [x.score for x in mine] == pytest.approx([t["score"] for t in theirs])


def test_rrf_chunks_gives_no_bonus_for_many_chunks():
    # এক মামলার ৫টা মাঝারি chunk আর এক মামলার একটা chunk যা দুই তালিকাতেই ১ নম্বরে
    many = [chunk(f"big#{i}", "big") for i in range(5)]
    lists = [[chunk("small#0", "small"), *many], [chunk("small#0", "small"), *many]]
    assert rrf_chunks(lists, 60)[0].case_key == "small"
    # api_v3 এর মতো file ধরে যোগ করলে উল্টো: বড় মামলা জিতে যায়
    ids = ["big#0", "big#1", "big#2", "big#3", "big#4", "small#0"]
    store = ChunkStore(ids, ["x"] * 6, [{"filename": x.split("#")[0], "year": 2000} for x in ids])
    assert rrf_as_api(lists, 60, store)[0].filename == "big"


def test_collapse_keeps_best_chunk_and_order():
    ranked = [chunk("a#3", "a"), chunk("b#1", "b"), chunk("a#1", "a"), chunk("c#0", "c")]
    assert [x.chunk_id for x in collapse_to_cases(ranked)] == ["a#3", "b#1", "c#0"]


def test_bm25_is_deterministic_and_drops_zero_scores():
    ids = [f"f{i}.pdf__chunk_0" for i in range(4)]
    docs = ["cheque dishonour section 138", "land possession suit", "bail murder section 302", "dower talaq maintenance"]
    index = BM25Index(ChunkStore(ids, docs, [{"filename": i.split("__")[0]} for i in ids]))
    got = index.search("cheque dishonour", 10)
    assert [x.filename for x in got] == ["f0.pdf"]
    assert index.search("nothing matches", 10) == []


def test_bangla_inflection_is_a_known_bm25_miss():
    """বর্তমান tokenizer stemming করে না। এটা baseline এর আচরণ লিখে রাখা, যাতে পাল্টালে চোখে পড়ে।"""
    ids = ["f0.pdf__chunk_0", "f1.pdf__chunk_0", "f2.pdf__chunk_0"]
    docs = ["দেনমোহরের দাবি", "অন্য কিছু লেখা", "আরও কিছু লেখা"]
    index = BM25Index(ChunkStore(ids, docs, [{"filename": i.split("__")[0]} for i in ids]))
    assert index.search("দেনমোহর", 10) == []


def test_reranker_cache_survives_restart_without_model(tmp_path):
    cache = tmp_path / "scores.jsonl"
    text = "x" * 50
    k = pair_key("m", 8, "q", text[:10])
    cache.write_text(json.dumps({"k": k, "s": 0.25}) + "\n", encoding="utf-8")
    reranker = CrossEncoderReranker("m", 8, 10, cache)
    # model load হলে এখানে exception হত: "m" নামের model নেই
    assert reranker.score("q", [text]) == [0.25]


def test_retrieval_code_never_writes_to_chroma():
    banned = re.compile(r"\.(add|upsert|update|delete|modify|create_collection|get_or_create_collection|delete_collection|reset)\(")
    paths = [*(ROOT / "retrieval").glob("*.py"), *(ROOT / "evals" / "metrics").glob("*.py"), *(ROOT / "evals" / "tools").glob("*.py"), *(ROOT / "evals").glob("retrieval_*.py")]
    assert len(paths) >= 4
    for path in paths:
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            # set.add() লেখা নয়; কেবল collection/client এর উপর call ধরা হয়
            if banned.search(line) and re.search(r"(col|collection|client|chroma)\w*\.", line, re.IGNORECASE):
                pytest.fail(f"{path.name}:{i}: {line.strip()}")
