# Decisions and assumptions

Newest first within each section. Each entry says what was decided, why, and what would change it.

## Spec vs. actual state (found at the start of the SPEC.md work, 2026-11-03)

**D-001 — The corpus is High Court Division only.** SPEC §1 says "AD + HCD". `extract_division()` finds "APPELLATE DIVISION" in the first 400 characters of 0 of 8,615 judgements, and only 27 mention it in the first 2,000 (HCD judgements citing AD). `scraper.py` fetches `div_id=2`. The AD > HCD weight stays in code but has nothing to act on until AD judgements are added.

**D-002 — The pipeline had already changed before SPEC.md.** SPEC §1 lists `understanding → clarify → … → citation_verifier → finalize` and Groq Llama 3.3 70B. On this branch the graph is `understanding → query_expansion → retrieval → rerank → sufficiency_check → generation → verifier → finalize` (commit 98c6eeb, from an earlier explicit request: alias expansion, bge-reranker-v2-m3, framework-first prediction instead of a clarify-only reply). Groq retired `llama-3.3-70b-versatile`; the fallback is `openai/gpt-oss-120b`. The "baseline" in Phase 2 will be measured on this pipeline; `evals/reports/before.json` records the original pipeline on the original index for reference.

**D-003 — Backward compatibility: old field names restored as aliases.** The earlier rounds renamed `answer.summary` → `short_answer`, `sources.*.score` → `rerank_score`, and `/api/search` `judgements` → `cases`. SPEC §1 says never remove response fields, so the old names are returned alongside the new ones. The old answer's conditional keys (`irac_analysis`, `honest_assessment`, `strategy`, `outcome_prediction`, `action_checklist`) were only present for some intents and are not restored.

**D-004 — The v1 judgements index was rebuilt, not preserved.** An interrupted rebuild (2026-10-27) had deleted `bangladesh_judgements` and re-added only 6,900 of ~45K chunks. It is rebuilt from the unchanged `judgements.json` with the original regex metadata (`enrich_judgements.py --text raw --labels regex`), which is deterministic, so v1 = the original index. `enrich_judgements.py` now builds into `<name>__building` and swaps only when complete, so this can't recur.
The laws collection is not exactly the original: on 2026-10-27 `clean_metadata.py --apply` filled empty `section_no` (3,982) and `year` (4,230) for Bangla Acts in place. Documents and embeddings are unchanged.

**D-005 — Raw PDFs are not on this machine.** `downloaded_pdfs/` does not exist; only `judgements.json` (PyPDF2 text) is present. Phase 1 re-extraction needs the PDFs re-downloaded with `scraper.py` (8,615+ files from supremecourt.gov.bd, several hours). Not done without the owner's go-ahead.

**D-006 — Test/eval layout.** The earlier `eval/` folder was split into `evals/` (runner, reports) and `tests/` (unit + live regression tests) to match SPEC §3.

## Phase 1 — data quality

**D-101 — Audit buckets and thresholds** (`ingestion/quality_audit.py`, `THRESHOLDS`). All are guesses tuned on the 30-file sample below:
- broken: < 50 words, legacy-font characters > 2% of letters, non-printable > 1% of characters, garbage score > 0.15, or > 30% duplicated 12-word shingles;
- suspect: garbage score > 0.05, 1–2 letter fragments > 6% of Latin tokens (split words), or average word length outside 3.5–9 for Latin text;
- good: everything else.

**D-102 — Findings from the 30-file manual sample** (seed 42; 10 broken, 9 suspect, 8 good, plus 3 targeted).
1. *Legacy fonts — two encodings.* Bangla text typeset in legacy fonts comes out of PyPDF2 as ASCII mojibake. Two encodings appear: `h¡wm¡−cn p¤fË£j ®L¡VÑ` (¡ ¢ − Ñ …) and SutonnyMJ/Bijoy `evsjv‡`k miKvi` (‡ † ¨ …), both meaning "বাংলাদেশ…". The second looks like Latin letters, so the first audit version missed it; SutonnyMJ signature characters were added (379 files carry them). Consequence: a single Bijoy→Unicode converter would not fix the corpus — OCR is the general fix.
2. *Mixed files.* Many "broken" files are English judgements with a Bangla header or quoted Bangla passage in a legacy font (e.g. `642436_WP12889of2012`, `704153_WP1481of2014`). Most of their text is fine. → Extraction must decide per page, not per file.
3. *Duplicated text layers.* Some legacy-font files repeat each line 2–4 times without spaces (`p¤fË£j ®L¡VÑh¡wm¡−cn p¤fË£j ®L¡VÑ…`, e.g. `1327177_CriminalAppealNo.674of2018`). The 12-word shingle check catches only 9 files because the repeats are glued together; these files are broken by the legacy-font rule anyway.
4. *Near-empty files are real.* All sampled files under 150 words were genuine short admission orders ("This appeal will be heard. Call for the records…"), clean text. Threshold lowered to 50 words. No file has < 10 words — because `extract_text.py` silently dropped PDFs with no text layer (`if text:`). Image-only scans are therefore missing from the corpus entirely; their number is unknown until the PDFs are re-fetched.
5. *Split words are everywhere.* Even "good" files contain PyPDF2 spacing errors: `BANGLADE SH`, `Constitutio n`, `20 20`, `appella nt`, `Section s 83A`. "Suspect" files have them at > 6% of tokens (up to 48%).
6. *Unicode Bangla exists.* Some files have real Unicode Bangla (e.g. `2358026_C.R.326of2023`, Bangla share 32%) and extract fine. **Corrected by D-113:** this file's text layer is in fact corrupted. It looked fine because the letters are in the Bangla block.

**D-103 — OCR trigger is "page fails the quality check", not only "page has no text".** SPEC Phase 1.4 says OCR only for pages with no extractable text. Legacy-font pages *do* have extractable text — it's mojibake — so that rule would leave all ~2,800 legacy-font files broken. The cascade OCRs a page when its text is empty **or** the page fails the same legacy-font/garbage checks as the audit.

**D-119 — What gets re-OCRed (list built; the run needs approval).** `data_quality/reocr_list.csv`, from `ingestion/reocr_list.py`: **453 files, 25,753 pages**. A file can have several reasons: low retention 253, corrupted text layer 197, corrupted saved text 8, the earlier list of 24, Death Ref 58 parts 2 and 3. Files on the list keep 85.9% of their lines; files off it keep 99.0%. Estimated 5.8 h at 2 workers, from v2's recorded per-file seconds (4.4 h of that is Death Ref 58).

Left out on purpose:
- *3,789 other files with OCR* (12.4 CPU-hours, 6.2 h at 2 workers). In a sample of 40, re-extraction gained 1.0% more words. They can be added if a uniform v3 is worth the time.
- *Death Ref 58 part 1.* It keeps 99.2% of its lines.
- *251 no-text pages*, which have no line baseline. 210 hold under 10 saved words. Re-OCRing the other 41 gained 1.0% more words and 18 more lines of 960.

Two groups are on the list even though they probably don't need it, because they were requested or cost little: 5 of the earlier 24 are above 95% on the better baseline, and only 4 of the earlier 24 are among the 24 lowest now; the 8 "corrupted saved text" files are mostly OCR noise rather than corruption (D-117).

**D-118 — Line retention is in the audit, measured against a stand-in; block crops were also losing lines.** Saved outputs don't record what Tesseract detected, so `quality_audit.py --pdf-dir` compares output lines with the PDF's line structure (`pdf_lines`: spans on a baseline within 6 pt, with at least 3 non-space characters). Of three baselines tried on 139 pages, this one predicted real loss best: with the flags on, Tesseract matches 99.2% of it, and a page flag below 95% had 83.6% precision and 96.2% recall. Retention is the sum of min(PDF lines, output lines) over pages, divided by PDF lines, so extra lines on one page can't hide loss on another. Files below 95% are flagged (the threshold the project set); below 95% is `suspect` and below 70% is `broken` (a guess). `pdf_extract.plan_blocks` decides without running OCR, so the audit also counts exactly which pages the new rule would add.

File-level precision, estimated by re-extracting sampled files in memory ("true" = at least one page gains 8+ words and 5%+):

| Lines short | Flagged files | Sampled | True | Words gained |
|---|---|---|---|---|
| 1–2 | 42 | 25 | 48% | +4.5% |
| 3–9 | 147 | 25 | 72% | +6.7% |
| 10–99 | 55 | 25 | 72% | +11.2% |
| 100+ (excluding Death Ref 58) | 7 | 7 | 100% | +18.9% |

Overall about **69%** (an estimated 173 of 251). A flag on a file 1–2 lines short is close to a coin flip. A gain can include Latin junk as well as recovered text.

The check also under-reports. 5 of 40 sampled unflagged files had a page gaining 5%+ words, all of them cover pages of `1_TN_*` orders at or near 100% retention. The diffs show real form text coming back (`বনাম`, `প্রতিপক্ষ`, `আবেদনকারী`). Why retention misses these isn't established; form layouts may not line up one-to-one with baselines. There are 1,211 such files and 51 are on the list.

**Block crops.** Reading one page where the new output had fewer tokens than v2 showed that a tight one-line crop can come back from Tesseract **empty**, with no error: 4 of 12 flagged lines on that page, including a line of 12 tokens. On 116 real blocks, a 20 px white border with `--psm 6` took empty one-line results from 3/57 to 0/57 and agreement with full-page OCR from 87.2% to 93.2%, at the same speed. Padding the crop instead pulled in neighbouring lines (985 words against 907). Block OCR only; full-page OCR is unchanged. D-114's "40 OCR blocks: 100% kept" couldn't show this, because both sides of that comparison used the same crop.

**D-117 — Corrupted-Bangla detector; "62 files" was an undercount.** The D-113 rule (foreign letters over 5% of tokens) only sees one kind of corruption. A blind reading of 62 text-layer files found no clean one and found four kinds: foreign letters with visual-order vowels; visual order with dropped conjuncts; wrong letters, some with code points Unicode leaves unassigned; and lossy layers that keep only isolated letters.

**Likely cause:** rendering known-clean law text through a shaping PDF engine (MuPDF, Nirmala UI) produced the same corruption (`ব িѩ ব িদҝালয়`), because shaped glyphs don't map back to the original code points. This points to how the PDFs were made, not to bad typing. The court's own PDF toolchain hasn't been examined.

The detector (`quality_audit.py`; thresholds in `BANGLA_CHECK`) measures reassembled lines, because PDF spans split words:
- *Impossible spelling:* a foreign letter, an unassigned Bengali code point, or a vowel sign or virama that doesn't follow a consonant. Flags at 10% of Bangla tokens. In clean text with 20+ tokens the highest is 6.9% for law pieces and 5.8% for whole OCR files. Among 16,649 OCR pages, three are higher (10.5%, 13.7%, 15%), so the gap isn't perfect: the lowest hand-checked corrupted file is 13.3%.
- *Isolated letters:* single-letter tokens, not counting ও, এ or list markers like `(ক)`. Flags at 40%. Clean text with 20+ tokens peaks at 25%; the lowest hand-checked lossy file is 50%.
- *Symbol junk:* 25% of tokens with no word or number shape, on pages of 40+ tokens.

Rejected: vowel-sign density (it flagged clean phrases such as `অর্থ আইন, ১৯৯৩ (১৯৯৩ সনের ১৮ নং আইন)`) and vocabulary hit rate (clean OCR pages go as low as 25%, overlapping corrupted files, whose median is 31%).

On a text layer, one impossible token can flag a page, because some files scatter a few corrupted words across pages and a false flag only costs one block of OCR. Flagged blocks that contain Bangla go to OCR, or the whole page if they are over half of it. In saved text the bar is higher, because OCR noise on a handful of tokens would otherwise flag clean files: 10 tokens for a whole file, 20 for one page.

Results (`data_quality/bangla_detector_eval.json`, `corrupted_bangla_handcheck.csv`):

| Test | Result |
|---|---|
| Blind labels, 53 files | **53 of 53 flagged** (about 94% lower bound on recall) |
| All 62, including 9 labelled after unblinding | 61 of 62; the miss has no Bangla letters and the legacy rule handles it |
| Blind files whose output stays corrupted | v2: 48 of 53. New rule: **0** |
| Bad tokens surviving in those files | v2: 3,214. New rule: **1** |
| Whole laws (typed Unicode) | **0 of 594** flagged |
| Laws cut into 250 / 25 / 6-word pieces | 1 of 8,823 / 78 of 82,242 / 40 of 6,776; mostly odd encodings in the laws (`নিম্্নবর্ণিত`) |
| PDFs with a known-clean text layer (2 routes) | **0 of 18** pages flagged, no OCR run |
| OCR-only judgement files | 8 of 3,881 flagged (0.2%), 5 of them with under 50 Bangla tokens |
| Shaped PDFs with known source text | 6 of 6 flagged; word recall 29% in the layer, 92.6% through the extractor |

**What this doesn't show.** Labels are mine, not a human's, and the CSV says so. No file in the sample was labelled clean, so the sample can't measure precision; false positives come from the separate clean text above. I found no genuinely clean text-layer judgement in the corpus, so a real one has never been tested. The clean-PDF negatives are synthetic.

In the corpus: corrupted text survives in v2 in **197 files (540 pages)**. All of the original 62 are among them. In saved text the check flags 169 files, 161 of which the old rules rated "good". The 8 flagged only in saved text have no corrupted layer, so they are probably OCR noise.

**D-116 — What v2 is, and what's left open (2026-11-04).** `bangladesh_judgements_v2` = `text_v2/` + labels from `clean_metadata.py`. Those labels were derived from the v1 text; they weren't regenerated. One document (`2395499_706of2023`, 2 chunks) is wrongly tagged AD because two-column text put "Appellate Division" in the first 400 characters. Neither is corrected here. v2 was built before D-114 was found, so its OCR'd documents carry the line loss. The API still uses v1.

**D-115 — Death Ref 58 now swamps Bangla vector search (update to D-110).** In v1 this case's Bangla was mojibake, so Bangla queries rarely matched it. Now it's readable, and it's still 21% of chunks. On 10 test queries it went from 5 to 35 of the 100 top-10 results, and it filled all 10 slots for unrelated Bangla questions such as cheque dishonour. English queries were unaffected. Because fusion collapses chunks by file, a vector list full of one case also reduces candidates to one document. A per-document cap is needed before the API moves to v2; to be measured in Phase 3.

**D-114 — Tesseract silently drops Bangla lines; two flags fix it.** A page read by hand next to its output had lost 6 of 18 lines with no error, and every automated check had passed. Layout mode, DPI, language, script model and preprocessing made no difference. `-c textord_noise_rejrows=0 -c textord_noise_rejwords=0` did: 154 → 179 of 179 lines on 8 pages at essentially the same speed, word recall 64.9% → 95.4% on the hand-transcribed page, and no added junk on a 40-page sample. `tessdata_best` also keeps the lines but runs 2.4× slower. See D-118 for a separate loss in block crops.
Loss is concentrated in Death Ref 58 (part 2 keeps 83.0% of lines, part 3 85.0%, part 1 98.4%; about 54,000 of 317,000 lines missing in part 2). Other full-page OCR keeps 95.0% by the line-count metric, which moved only to 95.1% with the fix on a sample, so most of that shortfall is probably metric noise. Block OCR was unaffected. The flags are in `pdf_extract.py` from now on. **Existing outputs and v2 weren't regenerated**: that re-run is the owner's decision.
Lesson: mojibake and completeness are different properties. `ingestion/text_checks.py lines` now compares output to the PDF's line structure.

**D-113 — A third kind of broken Bangla.** Some text layers look like Unicode but hold visual-order vowels and Latin/IPA/Cyrillic letters in place of Bangla characters (`ǯদওয়ানী আদালেত ɛিতকার`). The legacy-character check can't see them. Found because "ground truth" from such files gave an impossible 27% OCR word recall. `text_checks.py bangla` flags a file when over 5% of its Bangla tokens contain foreign-script letters; the split is clean (3,058 files under 1%, 50 above 20%): **62 files, 0.43% of Bangla tokens**, median 9% Bangla, so mostly English judgements with Bangla quotations. **Superseded by D-117: this rule finds only one kind of corruption, and 197 files are affected.** Not re-extracted: `_is_legacy_span` doesn't yet treat these as unreadable. They rate "good" in the audit (61 of 62), and the audit buckets weren't changed, so v1/v2 stay comparable.

**D-112 — The v1 integrity check raised a false alarm; it now compares exact vectors.** After the v2 build, `verify-chroma` failed on `neighbours` for v1 and the pipeline stopped. Investigation: index files byte-identical to the pre-build backup; the same probe on the same unchanged DB gave 2 mismatches in about 10 runs; 0 of 45,571 raw float32 vectors and 0 documents differed from a copy of the backup (laws: 0 of 22,400). Approximate search isn't reliably repeatable, so it can't prove anything. Why it flips isn't established; replaying 572 WAL rows into the in-memory index on open is the likely reason. The check now hashes every vector by id, against a baseline taken from a copy of the backup (`chroma_pre_v2_exact.json`), so opening the backup itself was never needed. Tests show one changed vector, one changed document or one deleted id fails it. The pipeline stopping was the right behaviour; the check was wrong.

**D-107 — The `BrokenProcessPool` of 2026-11-04 04:39:37: what the evidence shows.** The full run stopped with 5,466 of 5,724 files done in its last pass. Evidence, from the Windows event logs and `phase1_run.log`:
- no Application Error, WER report or crash dump for `python.exe`/`tesseract.exe` in that window, so no native crash;
- no Resource-Exhaustion-Detector event in 30 days, and a worker peaks at ~106 MB, so memory exhaustion is unlikely (not strictly excluded: the pagefile's peak counter resets on reboot);
- `run_phase1.py` should have logged a line right after the failed pass and didn't, so it died at the same moment as the workers: a whole-tree termination, not one worker crashing;
- keyboard activity at 04:37:35, power source changes at 04:37:29 and 04:38:25, a user-mode `SetSuspendState` call at 04:39:40, three seconds after the traceback, then hibernation until 10:31.

Most likely the process tree was killed when the session closed, just before the laptop was put to sleep. What exactly closed it can't be proven from these logs. It doesn't change the fix: whatever the trigger, the code should have survived it.

**D-108 — Recovery design.** Defects found in the extraction code, and their fixes:
- one dead worker ended the whole pass → workers run in short-lived batches; unfinished units are retried alone; a unit that kills its worker twice alone goes to `failures.jsonl` and the run continues;
- no checkpoint inside a file → PDFs over 200 pages save every 50 pages to `text_v2_parts/`, atomically;
- non-atomic `.txt` writes and existence-only resume → temp + fsync + rename; an output counts only with a manifest row and a matching character count; anything else is moved to `data_quality/quarantine/`, never deleted;
- buffered manifest → each row is flushed and fsynced;
- no OCR timeout, PDFs never closed → 300 s per OCR call, 1 h stall detection, `doc.close()`;
- **`run_phase1.py` ignored return codes, so a failed extraction would have gone straight on to the audit and a v2 build from incomplete text** → a gate requires a validated output for all 8,615 PDFs. It reads the disk, not a progress counter.

`--force` was removed from `pdf_extract.py` (it overwrote outputs), and `enrich_judgements.py` now refuses to replace an existing collection without `--allow-replace`; its default target is v1. `ingestion/integrity.py` records SHA-256 of outputs and a logical fingerprint of the v1/laws collections (ids, metadata, documents, exact vectors; see D-112). HNSW file hashes are deliberately not compared, because Chroma can flush its WAL into them on open without any content change.

**D-109 — 2 workers.** RAM wasn't the cause of the crash, but only ~1 GB is typically available on this 7.7 GB laptop, and a worker plus its Tesseract process needs a few hundred MB. Below 1 GB available a batch runs with 1 worker; below 400 MB it waits first (`low_ram_mb`, `critical_ram_mb`). Measured on an idle machine: full-page OCR 1.33 s/page, against 3.78 s/page under the contention of the first run.

**D-110 — Death Reference 58/2013 dominates the corpus.** This judgement is split across PDFs of 11,407, 16,560 and 1,100 pages. Parts 1 and 2 alone were 9,996 of 45,571 v1 chunks (22%) and 24% of all words. They're extracted and indexed like everything else here, because dropping or capping them is a retrieval decision that needs numbers. To be measured in Phase 3: without a per-case cap, one case can fill the top-k.

**D-111 — The 8,357 existing outputs were kept as they are.** Two known small inconsistencies, not worth overwriting verified outputs for: 9 files have extra `\f` characters from the PDF text layer, so splitting them on `\f` gives more parts than pages (manifest page counts match the PDFs for all 8,357; new extractions strip these codes); and 7 of the original test sample predate the D-105 control-code rule (their audit shows no control-code problem).

**D-105 — Unreadable spans include fonts embedded without a character map.** Two sample files had Bangla in `Vrinda` (a Unicode font) embedded without ToUnicode, so the text layer returns glyph ids (`\x01\x02\x03`). A span counts as unreadable — and is OCR'd — if its font is a known legacy font, if > 30% of it is control codes, or if > 10% of its letters are legacy-font mojibake characters.

**D-106 — OCR settings.** 200 dpi, `ben+eng`, Tesseract `tessdata` (standard) models. 200 dpi was faster than 300 (3.7 s vs 6.3 s per page) and produced less junk on the probe page. Known issue: `ben+eng` inserts Latin junk into Bangla text; to be tested: `ben`-only OCR of the unreadable spans. See D-114 for the line-loss flags.

**D-104 — Tesseract.** Tesseract 5.5 is installed at `C:\Program Files\Tesseract-OCR` with `eng` only and is not on PATH. `ben` language data goes in a project-local `tessdata/` folder (gitignored) used through `TESSDATA_PREFIX`; the binary path is a config value.
