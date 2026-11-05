# Retrieval evaluation

Measures how well each retrieval method finds the right judgements, on a fixed benchmark. It needs no API, LLM or network, and only reads the Chroma collections.

Generation, citation and abstention are covered further down, under "Pipeline evaluation". `run_eval.py` without `--system` is the older API-based check.

## Run it

```bash
# Full run on v3, about 55 minutes on CPU, on the evaluation copy of the database (see below). Safe to re-run: reranker scores are cached.
python evals/retrieval_run.py --collection bangladesh_judgements_v3 --out evals/runs/v3.json

# Other collections or query forms, without the reranker
python evals/retrieval_run.py --collection bangladesh_judgements --systems dense bm25 hybrid hybrid_api --out evals/runs/v1.json
python evals/retrieval_run.py --collection bangladesh_judgements_v3 --systems dense bm25 hybrid --query-form precomposed --language bn --out evals/runs/v3_precomposed.json

# Re-score in seconds, for example after a label changes
python evals/retrieval_score.py --out evals/reports/retrieval_v3

# Sensitivity checks outside the declared comparisons, about 2 minutes
python evals/retrieval_explore.py

# After a person has reviewed the labels (see "Human review")
python evals/tools/human_review.py import --from human_review_decisions.csv
python evals/retrieval_score.py --with-corrections --out evals/reports/retrieval_v3_reviewed

# Prove production wasn't touched. This reads the files and never opens ChromaDB.
python ingestion/integrity.py verify-files --against data_quality/backups/chroma_phase2_files.json

# Prove the copy's contents still match, by exact vectors and documents
python ingestion/integrity.py verify-chroma --db evals/cache/chroma_eval_copy --against data_quality/backups/chroma_phase2_baseline_exact.json
```

## Files

Everything under `golden/` and `reports/` quotes judgements or names real people, so those folders are gitignored and aren't in the public repository (D-211). The commands below need them on your machine.

| File | What it is |
|---|---|
| `golden/retrieval_queries.jsonl` | The frozen queries |
| `golden/known_targets.json` | Correct case for known-item and Death Ref 58 queries |
| `golden/retrieval_qrels.jsonl` | Relevance grades, each positive grade with a verbatim quote from `text_v3/` |
| `golden/human_review.csv` | The 564 positive labels, prepared for review |
| `golden/human_corrections.jsonl` | Reviewer decisions, append-only, created at the first import |
| `golden/lay_queries.jsonl`, `golden/lay_qrels.jsonl` | Lay-language queries and the expected judgements known so far |
| `golden/golden_set.jsonl`, `golden/adversarial_set.jsonl` | Pipeline evaluation questions |
| `retrieval_config.json` | Every threshold and depth |
| `metrics/retrieval.py` | The metric and significance functions |
| `retrieval_run.py`, `retrieval_score.py` | Produce ranked lists, then score them |
| `tools/draft_candidates.py` | Seeded case sampling and whole-corpus term counts, with no retriever |
| `tools/build_pool.py`, `tools/apply_labels.py` | Blind judging cards, then grades into qrels |
| `tools/human_review.py` | Builds the review sheet and page, and imports a person's decisions |
| `pipeline_env.py`, `pipeline_eval.py`, `report_eval.py` | Isolated pipeline runs and their reports |
| `metrics/generation.py`, `metrics/citation.py`, `metrics/abstention.py` | Pipeline metrics |
| `experiments/phase3_plan.json`, `run_experiment.py`, `experiment_gate.py` | Phase 3 plan, runner and readiness gate |
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

Claude graded the labels from excerpts, not a person and not a lawyer (D-202). **The results are provisional until a person reviews them.**

## Human review

Nothing a reviewer does changes `retrieval_qrels.jsonl` (D-207).

1. Run `python evals/tools/human_review.py export` if `evals/review/review.html` is missing.
2. Open `evals/review/review.html` in a browser. It works offline and sends nothing anywhere. Or use `golden/human_review.csv` in Excel, filling in `decision` (`agree`, `change` or `unsure`), `human_grade` and `reviewer_notes`.
3. Enter your name, and for each label agree, change the grade or mark it unsure. Decisions stay in that browser, so use **Export decisions** regularly. **Restore an export** brings them back.
4. Import the file with the command above, then check progress with `python evals/tools/human_review.py status`.

A grade is about the whole judgement, so open the PDF when the two passages don't settle it. The PDF link needs `downloaded_pdfs/` beside the repo's `evals/` folder, and browsers vary in honouring the page number.

## Lay-language queries

`golden/lay_queries.jsonl` asks 18 of the benchmark's needs and 4 new ones in everyday wording (D-206). Grading isn't finished, so no lay result can be claimed yet.

```bash
python evals/retrieval_run.py --collection bangladesh_judgements_v3 --queries evals/golden/lay_queries.jsonl --db evals/cache/chroma_eval_copy --out evals/runs/lay_v3.json
python evals/tools/build_pool.py --runs evals/runs/lay_v3.json --queries evals/golden/lay_queries.jsonl --qrels evals/golden/retrieval_qrels.jsonl evals/golden/lay_qrels.jsonl --out-dir evals/judging
python evals/retrieval_score.py --runs evals/runs/lay_v3.json --queries evals/golden/lay_queries.jsonl --extra-qrels evals/golden/lay_qrels.jsonl --out evals/reports/retrieval_lay_v3
```

## Pipeline evaluation

Runs the real LangGraph pipeline without starting a server, touching port 8000 or opening the production database (D-208). Opening ChromaDB changes its SQLite file even for reads, so every evaluation tool here uses the copy and refuses the production path.

```bash
python evals/tools/make_eval_db.py                     # once: a verified copy of the database, about 3.8 GB, gitignored
python evals/run_eval.py --system pipeline --collection v1 --limit 3    # fake LLM: tests the environment only
python evals/tools/check_llm.py                        # one trivial message to each provider
python evals/run_eval.py --system pipeline --collection v1 --llm real --judge --dataset both --out evals/reports/pipeline_v1.json
python evals/report_eval.py --run evals/reports/pipeline_v1.json --out evals/reports/pipeline_v1.md
```

`--llm` defaults to `fake`, which can't become a baseline. Stop the API first or check free RAM: this loads a second full copy of the pipeline, about 1.8 GB at peak on this machine. It also takes about 2.5 minutes to load.

### Metrics

| Metric | Definition | Needs a judge |
|---|---|---|
| Must-mention | Share of `must_mention` phrases found in the answer. `a\|b` means either. Compared after NFC, case folding and Bangla digits to ASCII | No |
| Must-not-claim | Questions whose answer contains a `must_not_claim` phrase | No |
| Faithfulness | Share of the answer's claims the judge marks supported by the retrieved sources | Yes |
| Correctness | Correct 1, partial 0.5, wrong 0, against `reference_answer`. Only for questions that have one | Yes |
| Citation precision | Cited items that were retrieved, over all cited | No |
| Citation recall | Expected sources cited. With `expected_match: any`, one is enough, because many cases may be relevant | No |
| Fabricated rate | Cited items in neither the retrieved set nor the index, including ones the pipeline later removed | No |
| Cited but not retrieved | Items in the index that weren't in this question's sources | No |
| No-answer abstention | `no_answer` questions where the pipeline stopped or said nothing relevant was found, with no citations | No |
| False abstention | The same on answerable questions | No |
| False premise corrected | No missing must-mention, no violation, no abstention | No |
| Injection resisted | Canary absent, system prompt not leaked, no violation. Planted text enters the context in memory only | No |
| Vague prediction clarified | Asks for details, promises no outcome | No |
| Latency, tokens, cost | p50 and p95 seconds, per question. Cost is "not measured" if any model's price in `llm/pricing.json` is unknown | No |

Word lists and limits are in `generation_config.json`. They're checks on wording, so a real abstention phrased differently can be missed.

### The judge

Prompts are versioned in `llm/prompts/judge_faithfulness_v1.md` and `judge_correctness_v1.md`. The judge runs at temperature 0 through `llm/client.py`, which caches by prompt hash, and on a different provider from the generator when one is available. Each row records whether it was independent. The question, answer and sources are passed inside tags as untrusted data. A failed judge gives no score rather than zero, and failures are counted.

## See it live

`python evals/serve_live.py` serves the UI (`frontend/dist` if it's built, otherwise `legal_ui_v2.html`; `--ui legacy` forces the old one) and the real pipeline from one process at `http://localhost:8001`, on the evaluation copy, listening only on this computer (D-210). It refuses port 8000. Questions in the UI make real LLM calls, about 2–3 per question. Search doesn't.

## Phase 3 preparation

```bash
python evals/run_experiment.py --list
python evals/run_experiment.py --status     # what's still needed
python evals/run_experiment.py --plan E3    # hypothesis, evidence, cost
```

`--run` refuses until the gate opens (D-209). New embeddings go to `evals/cache/embeddings`, never into ChromaDB.
