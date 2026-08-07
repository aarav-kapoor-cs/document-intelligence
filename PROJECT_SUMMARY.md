# Project Summary — Document Intelligence & Audit Search

**Period:** 17 July 2026 → 4 August 2026 (~3 weeks)
**Repository:** `sample` — Angular 20 frontend, Python (FastAPI) backend, Azure services
**Scale:** 26 commits, ~4,800 lines of Python across 15 modules, plus an Angular single-page app

> This is a record of what was built and what was learned. For setup and
> operating instructions see [README.md](README.md); this document is the
> narrative behind it.

---

## 1. What the project does

Two products sharing one backend.

**A — Document reader.** Upload a PDF → Azure Document Intelligence extracts text and
labelled fields → an LLM classifies the document type and answers a question about it →
the result is saved to a database.

**B — Audit searcher.** Export the saved documents into a 6-sheet Excel audit report →
turn that workbook into searchable text chunks → ask natural-language questions about the
whole batch ("which fields are extracting badly?") and get an answer with citations.

The join between them is the Excel workbook. Product B reads the workbook, never the
database — deliberately, because the two hold different document sets.

```
PDF → OCR → type check → LLM answer → database
                                          ↓ export
                              6-sheet Excel workbook
                                          ↓ chunk
                              84 self-contained paragraphs
                                          ↓ embed
                              Azure AI Search index
                                          ↓ retrieve 15 → LLM
                              grounded answer with [chunk-id] citations
```

---

## 2. Timeline

### Phase 1 — Foundation (17 July)
- Initial Angular + backend scaffold
- Added Azure Document Intelligence OCR; committed to an Azure-only stack
- Wrote a ZIP-download setup guide because **git is blocked on the office laptop**
- Made error messages name *which* Azure service failed (OCR vs OpenAI) instead of a generic 401
- Wrote `ARCHITECTURE.md` as a Q&A walkthrough for the mentor meeting

### Phase 2 — Backend rebuild (18–22 July)
- **Dropped the .NET backend entirely** and rebuilt on FastAPI + Pydantic
- Added a Document Intelligence model picker (invoice / receipt / ID / layout)
- Made the backend speak both SQLite (local) and SQL Server (office laptop), with all
  MSSQL access going through stored procedures
- Added a **document-type checker** — an LLM classifies the upload and rejects it if it
  doesn't match the selected model, *before* anything is saved
- Made database failures non-fatal so the API still runs and reports the exact problem

### Phase 3 — The Excel audit report (22–31 July)
- Built the **6-sheet workbook** from `KeyValuesJson`, mirroring the mentor's
  `DriftTemplateInterns.xlsx` verbatim — including its quirky headers
- Added `PromptId` so one analyze call's files can be found together
- **GSTIN/PAN repair:** Azure sometimes returns a 10-character PAN where a 15-character
  GSTIN belongs. Implemented recovery at both OCR time and export time, matching only a
  GSTIN whose embedded PAN (characters 3–12) is an exact match, and only when exactly one
  candidate exists — so the customer's GSTIN can never be picked up by mistake
- Assembled full addresses from parts when `street_address` came back empty
- Fixed Invoice Volume to date by `CreatedAt` (real processing day) rather than export day
- Added per-row validity ratings against the recommended threshold, with the bands and the
  legend generated from one source so they cannot drift apart

### Phase 4 — AI Search and RAG (3–4 August)
- Designed the **chunking strategy** — three sheets, three rules, 84 chunks
- Built **two parallel implementations**: `keyword_search.py` (BM25 + semantic ranker) and
  `hybrid_search.py` (adds embeddings and vector search), so the diff between them *is*
  the vector feature
- Wired up Azure AI Search: index schema, HNSW vector profile, semantic configuration
- Added **RAG** — retrieve chunks, have the model write a grounded answer with citations
- Built a **Semantic Kernel agent** with four tools that routes questions to SQL or search
- Wrote `retrieval_metrics.py` to measure the four retrieval methods against SQL ground truth
- Wrote `sql_comparison.py` to demonstrate where search stops and SQL starts
- Built a standalone explorer UI at `/search` and integrated search into the Angular page
- Made the Excel export automatically refresh the search index
- Consolidated all documentation into one README

---

## 3. What was built

### Backend modules

| Module | Lines | Responsibility |
|---|---:|---|
| `hybrid_search.py` | 891 | Chunking, embeddings, vector + hybrid search, RAG |
| `keyword_search.py` | 778 | Same pipeline without vectors — the comparison baseline |
| `excel_service.py` | 726 | The 6-sheet audit workbook |
| `database.py` | 313 | SQLite / SQL Server, stored procedures |
| `agent_service.py` | 287 | Semantic Kernel agent, four tools |
| `search_api.py` | 271 | HTTP layer over the search pipeline |
| `sql_comparison.py` | 226 | Search-vs-SQL boundary, workbook consistency check |
| `main.py` | 205 | FastAPI app, analyze and export endpoints |
| `ai_service.py` | 182 | Azure OpenAI chat |
| `ocr_service.py` | 143 | Document Intelligence, GSTIN repair |
| `retrieval_metrics.py` | 105 | Recall@k across four methods |
| `field_applicability.py` | 104 | Core vs optional field split |
| `checker_service.py` | 87 | Document-type classification |
| `models.py` | 75 | Pydantic request/response shapes |
| `test_backend.py` | 393 | 36 tests |

### The chunking design

`make_chunks()` reads three of the six sheets with a different rule each:

| Sheet | Rule | Chunks |
|---|---|---:|
| AI EXTRACTION LOG | Group 594 rows by `(Number, Source_File)` — one paragraph per document | 18 |
| COMPLETENESS TREND | One chunk per row | 33 |
| VALIDITY TREND | One chunk per row, minus 5 legend rows | 33 |
| | **Total** | **84** |

Two grains, deliberately: `document` chunks answer "how did *this invoice* do", `field`
chunks answer "how did *this field* do". Rows vs columns of the same table.

---

## 4. Engineering decisions worth defending

**Numbers are written out as words.** A chunk containing `11.1%` cannot be found by anyone
asking which fields extract badly — no word connects the question to the number, and
embeddings don't help because a vector has no sense that 0.111 is small. `verdict()`
expands every percentage into searchable prose, and emits **only the band that applies** —
spelling out all three would put "needs attention" into all 66 field chunks and destroy the
discrimination the function exists to create.

**Documents are grouped, not indexed row by row.** A single log row is one field of one
document and is useless alone. Indexing row-by-row runs without error and returns results,
which is what makes it dangerous — search then hands back three fields out of 33 as if they
described the whole invoice.

**Chunk ids carry a hash.** `INV/100` and `INV-100` both slug to `inv-100`, and a key
collision in Azure is a silent overwrite, not an error. A 6-character blake2s hash over the
grouping key makes collisions impossible.

**Stale chunks are deleted explicitly.** `upload_documents` only ever adds or overwrites.
Because chunk ids are deterministic, "no longer produced" is simply "not in the set we just
uploaded" — `delete_orphans()` handles it.

**The agent never writes SQL.** It picks a field *name*, checked against a fixed allowlist
and passed as a query parameter. Model-authored SQL never reaches the database, and a wrong
guess returns the valid list so it can retry.

**The workbook mirrors the template exactly.** The core/optional field split lives *outside*
it — in `field_applicability.py` and in a sentence on each field chunk — because the report
has to keep matching `DriftTemplateInterns.xlsx` verbatim.

---

## 5. What was measured

Rather than assuming, `retrieval_metrics.py` scores all four methods against ground truth
from SQL. Mean recall@5 over six queries:

| Method | recall@5 |
|---|---:|
| Keyword (BM25) | **37%** |
| Hybrid (RRF) | 34% |
| Vector only | 27% |
| Hybrid + semantic reranker | 26% |

**Vectors do not beat keyword search on this data, and the reranker is the weakest of the
four** — the opposite of what was assumed before measuring.

Two honest caveats recorded with the result:

1. **One query is not evidence.** The first synonym query gave vector 60% vs keyword 40%,
   which looked like a clean win. Across six queries it reverses. That single result was
   noise, and would have been written up as a finding if the harness hadn't been built.
2. **The questions are the problem, not the retrieval.** Every query is "find documents
   where field X is empty" — a `WHERE` clause wearing a sentence. Retrieval scoring 37%
   where SQL scores 100% is the boundary doing its job.

**Why embeddings add little here:** the 18 document chunks have a mean pairwise cosine of
**0.883**, some pairs at **0.995**. They are all the same kind of thing described with the
same 33 field names. Embedding a leaner string with the verdict boilerplate stripped made
it *worse* (0.883 → 0.878; closest pair 0.995 → 0.997).

**Conclusion recorded:** at 84 chunks the honest recommendation is keyword plus RAG.
Vectors are infrastructure for when the corpus grows — and the pipeline is the same
fifteen-line change at 84 chunks or 84,000.

---

## 6. Findings raised about the source data

1. **A document was ingested twice — fixed.** `TABGV 2425-394` had 66 rows where every
   other document had 33. The `drop_duplicates` guard stayed in.
2. **Percentages were inflated by that duplicate — fixed.** `Total_Records` read 9 for
   every field when there were 8 real documents; now correctly 18.
3. **Validity was measuring the validator, not the data.** Four date fields diverged
   sharply from completeness, which looked meaningful. In fact sixteen populated dates were
   failing validation and every one was a perfectly readable date the format table had no
   pattern for — `30-Jul-2025`, `06 MARCH, 2004`, `12/15/2019`. Four formats were added;
   all sixteen now parse with no regression. **Validity is now identical to completeness on
   all 33 fields** — so the column currently adds no information and either needs checks
   with teeth or is redundant.
4. **The headline completeness figure counts fields that were never there.**

   | | Mean completeness |
   |---|---:|
   | Headline, all 33 fields | 50.8% |
   | **12 core fields** | **93.1%** — eight at 100% |
   | 21 optional fields | 26.7% |

   `Vendor Fax Number` scores 5.6% because seventeen invoices didn't print one, not because
   OCR missed it. **The real audit finding is small and specific:** `GST Amount` and
   `Vendor GSTIN` each missing from 5 of 18 documents, `Tax Details` from 3,
   `Customer GSTIN` from 2. Everything else is schema noise.

**Completeness is not accuracy.** The audit measures whether a field was *populated*, never
whether it was *right*. Ten synthetic invoices with a ground-truth manifest exist for this,
but the manifest is not in the repo, so the join has not been made.

---

## 7. Concepts learned

| Concept | Where it lives |
|---|---|
| OCR and prebuilt document models | `ocr_service.py` |
| LLM prompting, system vs user messages, tokens | `ai_service.py` |
| Zero-shot classification | `checker_service.py` |
| Chunking strategy and grain | `make_chunks()` and its three helpers |
| Text embeddings, cosine similarity, HNSW | `embed()`, `index_schema()` |
| BM25 keyword search | every `search_text=` call |
| Hybrid retrieval and Reciprocal Rank Fusion | `search_variants()` |
| Semantic reranking (cross-encoder) | `query_type="semantic"` |
| RAG and grounded citation | `answer_from()`, `RAG_SYSTEM` |
| Tool-calling agents | `agent_service.py`, Semantic Kernel |
| Retrieval evaluation, recall@k | `retrieval_metrics.py` |
| Knowing when *not* to use search | `sql_comparison.py` |

**The single most useful rule learned:** if the answer is a **number** or an **ordering**,
it's SQL. If it's "show me the ones that look like this", it's search.

---

## 8. Known gaps

- **The web Ask endpoint doesn't send vectors.** `search_api.py:136-144` passes
  `search_text` and `query_type="semantic"` but no `vector_queries`, so the Angular page
  runs keyword + reranker. Vectors run only in the CLI and the Compare tab.
- **Hardcoded chunk cap.** `search_api.py:143` uses `min(top, 84)`, which will silently
  truncate once the workbook exceeds 84 chunks.
- **`keyword_search.py` is 92% duplicate** of `hybrid_search.py` (141 differing lines of
  1,669). Deliberate — the diff *is* the teaching material — but every fix must be made twice.
- **`field_applicability.py` is imported by nothing.** It's a standalone CLI report; the
  agent has its own separate core/optional lookup.
- **Three LLM calls per uploaded file** (classify, answer, re-format as JSON). The third
  could be removed by requesting structured output in the first.
- **No auto-refresh on upload.** The workbook and index only refresh when Download Excel is
  clicked, not when new records reach the database.
- **The ground-truth manifest is missing,** so every number describes completeness, never
  correctness.

---

## 9. Cost note

Azure AI Search bills **hourly whether or not it is queried** — Basic is roughly USD 75/month.
When finished experimenting, delete the **service**, not just the index. Embedding 84 chunks
costs a fraction of a cent, so rebuilding is effectively free. The expensive resource is the
one sitting idle.
