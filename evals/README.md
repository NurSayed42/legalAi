# Retrieval evaluation

Measures how well each retrieval method finds the right judgements, on a fixed benchmark. It needs no API, LLM or network, and only reads the Chroma collections.

The generation, citation and abstention metrics from SPEC Phase 2 aren't built yet, and neither is the adversarial set. `run_eval.py` is the older API-based check.

## Run it

```bash
# Full run on v3, about 55 minutes on CPU. Stop the API first. Safe to re-run: reranker scores are cached.
python evals/retrieval_run.py --collection bangladesh_judgements_v3 --out evals/runs/v3.json

# Other collections or query forms, without the reranker
python evals/retrieval_run.py --collection bangladesh_judgements --systems dense bm25 hybrid hybrid_api --out evals/runs/v1.json
python evals/retrieval_run.py --collection bangladesh_judgements_v3 --systems dense bm25 hybrid --query-form precomposed --language bn --out evals/runs/v3_precomposed.json

# Re-score in seconds, for example after a label changes
python evals/retrieval_score.py --out evals/reports/retrieval_v3

# Sensitivity checks outside the declared comparisons, about 2 minutes
python evals/retrieval_explore.py

# After a person has filled in the review sheet
python evals/tools/human_review.py import

# Prove nothing changed
python ingestion/integrity.py verify-chroma --against data_quality/backups/chroma_phase2_baseline_exact.json
```

## Files

| File | What it is |
|---|---|
| `golden/retrieval_queries.jsonl` | The frozen queries |
| `golden/known_targets.json` | Correct case for known-item and Death Ref 58 queries |
| `golden/retrieval_qrels.jsonl` | Relevance grades, each positive grade with a verbatim quote from `text_v3/` |
| `golden/human_review.csv` | Sheet for a person to confirm or change the grades |
| `retrieval_config.json` | Every threshold and depth |
| `metrics/retrieval.py` | The metric and significance functions |
| `retrieval_run.py`, `retrieval_score.py` | Produce ranked lists, then score them |
| `tools/draft_candidates.py` | Seeded case sampling and whole-corpus term counts, with no retriever |
| `tools/build_pool.py`, `tools/apply_labels.py` | Blind judging cards, then grades into qrels |
| `tools/human_review.py` | Exports the review sheet and imports a person's grades |
| `retrieval_explore.py` | Exploratory checks, which support no claim |
| `reports/phase2_retrieval_comparison.md` | The results and recommendations |

## Systems

| System | Definition |
|---|---|
| `dense` | Vector search in Chroma (MiniLM, cosine) |
| `bm25` | BM25 over the same chunks, built in memory, using the API's tokenizer |
| `hybrid` | Reciprocal rank fusion of the top 100 from each, chunk by chunk |
| `hybrid_rerank` | `bge-reranker-v2-m3` over the top 50 `hybrid` chunks, with the API's quantization, `max_length` and text cut |
| `hybrid_api` | Diagnostic: fusion exactly as `api_v3._fuse_cases` does it, over its top 12 per method, adding up all chunks of a file and applying the precedent weight |

These use the raw query. The API also rewrites queries with an LLM and adds aliases, and it gives the reranker an English rewrite. **So these numbers describe the retrieval methods, not the API's end-to-end behaviour.**

## Unit of evaluation

Relevance belongs to a case, so results are compared by case. Each system's ranked chunks collapse to the best chunk per case, then the top 10 cases are scored. Otherwise one case filling all ten slots would count ten times. All three Death Ref 58 PDFs are one case. Domination is measured separately on the raw top 10 chunks, before collapsing.

## Grades

| Grade | Meaning |
|---|---|
| 2 | The judgement decides or substantially discusses the issue asked about |
| 1 | The same statute or topic is applied or discussed, but not this issue, or the issue is a minor part |
| 0 | Passing mention, citation only, or unrelated |

Grade 1 and above counts as relevant. The strict variants count grade 2 only.

## Metrics, all at k = 10

- **Precision@10:** relevant cases in the top 10, divided by 10. Empty slots count as misses.
- **Recall@10:** relevant cases in the top 10, divided by all known relevant cases. This is **pool recall**: it can only count cases found by a system or the term search. Queries with many relevant cases can't reach 1.0, so **capped recall** divides by `min(10, relevant)`.
- **MRR@10:** 1 / rank of the first relevant case, or 0 if none is in the top 10.
- **nDCG@10:** graded, with gain `2^grade - 1` and discount `log2(rank + 1)`, against the ideal order of the known grades. This is the primary metric.
- **Judged@10:** the share of the top 10 that was graded. Unjudged cases count as not relevant, so a low figure means that system's score is a lower bound.

No-answer queries have no relevant cases, so these metrics are undefined for them rather than zero. They're used for the false-similarity checks only.

## Claiming an improvement

A system counts as better than another only if both hold on nDCG@10 across queries with known relevant cases:

1. the 95% paired bootstrap interval of the difference excludes zero;
2. the sign-flip randomization p-value, Holm-adjusted across the declared comparisons, is below 0.05.

The two language versions of a question aren't independent, so tests resample by information need. Everything else is descriptive and supports no claim. The full protocol is D-201 in `docs/DECISIONS.md`.

## Who verified the labels

Claude graded the labels from excerpts, not a person and not a lawyer (D-202). `human_verified` stays `false` in the qrels until someone checks them in `golden/human_review.csv`. **The results are provisional until then.**
