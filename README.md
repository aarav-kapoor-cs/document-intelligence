# Document Intelligence

Upload PDFs or images, pick an Azure Document Intelligence model, type a prompt, and get an AI
answer per file. The text and fields are read by Azure OCR, answered by Azure OpenAI, and saved to a
database. A second layer exports an **audit workbook** of extraction quality and makes it
searchable through **Azure AI Search** — keyword, vectors, hybrid, a semantic reranker, RAG, and a
Semantic Kernel agent.

- **Frontend** — Angular, http://localhost:4200
- **Backend** — Python / FastAPI, http://localhost:8001
- **OCR** — Azure AI Document Intelligence (Layout / Invoice / Receipt / ID Document)
- **AI** — Azure OpenAI (v1 endpoint, no api-version anywhere)
- **Database** — plain SQL: Microsoft SQL Server on the office laptop, or a local SQLite file
- **Search** — Azure AI Search over the audit workbook, at http://localhost:8001/search

---

## Contents

1. [Quick start](#1-quick-start)
2. [Settings — `.env`](#2-settings--env)
3. [Office laptop (Windows + SQL Server)](#3-office-laptop-windows--sql-server)
4. [Where the code is](#4-where-the-code-is)
5. [How it works](#5-how-it-works)
6. [The database and its stored procedures](#6-the-database-and-its-stored-procedures)
7. [Audit report search](#7-audit-report-search)
8. [Troubleshooting](#8-troubleshooting)
9. [Never commit](#9-never-commit)

---

## 1. Quick start

Two terminals. Python 3.10+ and Node.js required.

**Backend**
```bash
cd backend-python
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then fill in your Azure keys
uvicorn main:app --port 8001
```

**Frontend**
```bash
npm install      # first time only
npm start        # http://localhost:4200
```

Open http://localhost:4200 → choose a file → type a prompt → **Get AI response**.

The same backend also serves the search explorer at **http://localhost:8001/search** — no npm, no
build step. See [section 7](#7-audit-report-search).

---

## 2. Settings — `.env`

Every secret lives in one git-ignored file, `backend-python/.env`. It is never committed and never
in the ZIP, so you create it once per machine by copying the template:

```bash
cd backend-python
cp .env.example .env          # Windows: copy .env.example .env
```

The five values the app needs:

```
AZURE_OPENAI_BASE_URL=https://<your-resource>.services.ai.azure.com/openai/v1
AZURE_OPENAI_KEY=<your-azure-openai-key>
AZURE_OPENAI_DEPLOYMENT=<your-deployment-name>
DOC_INTELLIGENCE_ENDPOINT=https://<your-resource>.cognitiveservices.azure.com
DOC_INTELLIGENCE_KEY=<your-ocr-key>
```

The base URL is the **v1** endpoint — it ends with `/openai/v1`, so **no api-version is needed
anywhere**. `AZURE_OPENAI_DEPLOYMENT` is the *name* of your model in Azure AI Foundry →
**Deployments**, not a secret.

Search adds two more (only needed for [section 7](#7-audit-report-search)):

```
AZURE_SEARCH_ENDPOINT=https://<your-search-service>.search.windows.net
AZURE_SEARCH_KEY=<primary ADMIN key>          # query keys are read-only and 403 on upload
AZURE_OPENAI_EMBEDDING_DEPLOYMENT=text-embedding-3-small
```

Changing `.env` needs a backend restart — it is read once at startup.

**Database.** The template saves to a local SQLite file (`USE_SQL_SERVER=false`, no server needed).
On the office laptop set `USE_SQL_SERVER=true`; see the next section.

---

## 3. Office laptop (Windows + SQL Server)

`git` is blocked there, so the code arrives as a **ZIP download** from GitHub.

### Install once

| Software | What for |
|---|---|
| **Python 3.10+** | the backend — tick *"Add python.exe to PATH"* in the installer |
| **Node.js (LTS)** | the frontend |
| **VS Code** | editor + terminals |
| **ODBC Driver 18 for SQL Server** | lets Python talk to SQL Server — a separate download from SSMS, and having SSMS is *not* enough |
| VS Code extension **"SQL Server (mssql)"** | viewing the data |

Then create the database once in SSMS (the app creates the *table*, not the *database*):

```sql
CREATE DATABASE DocIntelligenceDb;
```

### Every new ZIP

1. GitHub → green **Code** button → **Download ZIP** → unzip → open the folder in VS Code.
2. Copy your existing `backend-python/.env` into the new folder. It is never inside the ZIP, so
   this saves refilling the keys each time.
3. Two terminals:

```bat
:: Terminal 1 - backend
cd backend-python
copy .env.example .env      :: only if you don't already have one; set USE_SQL_SERVER=true
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --port 8001
```

```bat
:: Terminal 2 - frontend, from the project root
npm install
npm start
```

PowerShell uses `.\.venv\Scripts\Activate.ps1` and `Copy-Item` instead.

If `requirements.txt` or `package.json` changed since the last ZIP, re-run `pip install -r
requirements.txt` / `npm install`.

### SQL Server settings in `.env`

```
USE_SQL_SERVER=true
SQL_CONNECTION_STRING=Driver={ODBC Driver 18 for SQL Server};Server=localhost;Database=DocIntelligenceDb;Trusted_Connection=yes;TrustServerCertificate=yes;
```

`Server=localhost` + `Trusted_Connection=yes` means "the SQL Server on this laptop, using my Windows
login" — what SSMS connects to by default. A named instance needs `Server=localhost\SQLEXPRESS`. To
use a SQL username/password instead, see the commented alternative in `.env.example`.

### Confirm it works

Run these in order. Each one proves the layer below it is fine, so the first
failure tells you exactly where to look.

| # | Check | What proves it |
|---|---|---|
| 1 | The files arrived | `python -m unittest test_backend` prints **`OK`** (36 tests). A `ModuleNotFoundError` here means a file is missing or still has an old name |
| 2 | Backend up | Terminal 1 shows `Uvicorn running on http://127.0.0.1:8001`, plus `DATABASE: using ...` and `SEARCH: explorer at ...`, with no red error |
| 3 | Database reachable | http://localhost:8001/api/health returns `{"database":"ok"}` |
| 4 | Frontend up | http://localhost:4200 shows the dropdown with **three** options: Document Intelligence, Excel report, AI Search |
| 5 | OCR + AI work | Analyze a file → an answer actually *about your document*, with fields and token counts |
| 6 | Saved to SQL Server | In SSMS: `SELECT * FROM Documents;` on `DocIntelligenceDb` shows a new row |
| 7 | Search is wired | Choose **Excel report** → pick dates → **Download Excel report**. You should get the file *and*, a few seconds later, `Search index updated - N chunks` |
| 8 | The chat box answers | Choose **AI Search** → ask *"which fields are extracting badly"* → the answer names real fields with percentages |

Check 7 is the one that proves the whole loop. If it says *"the search index was
not updated"*, the app still works — only the search half is unconfigured, and
the message says which part.

> **Leave `AUDIT_XLSX` blank on this laptop.** Set, it forces every re-index to
> overwrite that one file; blank, each export writes a fresh `Audit_<date>.xlsx`
> next to the code and the newest one is used automatically.

> Work in one direction: edit and push on the personal laptop, and let the office laptop
> re-download. That avoids needing git there at all.
>
> ⚠️ Confirm with your EY mentor that a personal private GitHub is allowed for internship code.

---

## 4. Where the code is

```
backend-python/
  main.py             FastAPI app + routes: /api/analyze, /api/analyses, /api/health, /api/export
  models.py           Pydantic request/response shapes
  ocr_service.py      Azure Document Intelligence - text + fields as structured JSON
  checker_service.py  confirms the file matches the chosen document type
  ai_service.py       Azure OpenAI - the answer + token counts
                      (ai_service also owns the shared OpenAI client - v1 endpoint,
                       no api-version - which the search modules reuse for RAG)
  database.py         plain SQL: SQLite file or SQL Server via stored procedures
  excel_service.py    builds the audit workbook (mirrors DriftTemplateInterns.xlsx exactly)

  --- the search layer (section 7) ---
  keyword_search.py       chunking + keyword search + semantic ranker (index: audit-keyword)
  hybrid_search.py        the same, plus embeddings and vectors (index: audit-hybrid)
  sql_comparison.py       the same questions asked by SQL and by search, side by side
  retrieval_metrics.py    recall@5 for all four retrieval methods, against SQL ground truth
  field_applicability.py  completeness split by whether the field applies at all
  agent_service.py        Semantic Kernel agent - picks its own tool
  search_api.py           HTTP wrapper for the Angular chat box and the explorer
  static/search.html      the explorer UI - one self-contained file, no CDN, no build

src/app/app.ts, app.html    the whole Angular screen
src/styles.css              styling
```

---

## 5. How it works

### The request path

```
Browser (Angular)  ──►  Python API (FastAPI)  ──►  Azure OCR + Azure OpenAI
   localhost:4200          localhost:8001              │
        ▲                       │                      ▼
        └───────  JSON  ────────┴────────►  Database (SQL Server or SQLite)
```

Angular's `HttpClient` POSTs `{ model, prompt, files: [{ name, base64 }] }` to
`/api/analyze`. Files are base64 because JSON holds text, not raw bytes; the backend decodes with
`base64.b64decode` before calling OCR. CORS middleware in `main.py` allows the call from port 4200.

### Step by step, on "Get AI response"

1. `chooseFiles()` turns each file into base64 — `app.ts`
2. `getAnswer()` POSTs to `/api/analyze` — `app.ts`
3. `analyze()` loops over the files — `main.py`
4. `ocr_service.analyze()` reads text + fields via Azure OCR
5. `checker_service.check()` confirms the document type — a wrong type stops the file here
6. `ai_service.answer()` asks the prompt about that text
7. `database.save()` inserts a row, fields as JSON
8. The page renders one card per file

### OCR

`ocr_service.analyze()` uses `DocumentIntelligenceClient.begin_analyze_document(...)`, then
`poller.result()`. Text comes from `result.content`; fields from `result.key_value_pairs` (Layout)
or the named fields in `result.documents` (Invoice / Receipt / ID). Every field — and every nested
level, each line item and each cell — is stored as `{ "value": ..., "confidence": ... }`, where
confidence is Azure's 0–1 score. Low-confidence values are highlighted per cell in the results
table. The high-resolution OCR add-on is on, for small print.

### The AI answer

`ai_service.answer()` calls `client.chat.completions.create(...)` with a fixed `system` instruction
and the document text + your question as the `user` message. The reply is
`choices[0].message.content`; token counts come from the response's `usage`.

### Errors

`_process_file()` wraps each step in its own `try/except`. A failed step stops that file and the
reason comes back in the result's `error` field, shown in red — nothing half-done is saved. The
database `save()` is wrapped separately, so an unreachable database still returns the answer.
`ensure_table()` at startup is guarded too: a bad connection prints a `WARNING` but the app starts.

### Data shapes (`models.py`)

- **Request** `AnalyzeRequest`: `model` + `prompt` + `files[]`, each `{ name, base64 }`
- **Result** `FileResult`: `{ file_name, model, text, fields, answer, error, tokens }`
- **Saved row**: `Id, FileName, Model, Prompt, DocumentText, KeyValuesJson, AiAnswerJson, Answer,
  PromptTokens, CompletionTokens, TotalTokens, CreatedAt`

---

## 6. The database and its stored procedures

`database.py` uses plain SQL. `_open()` returns a `pyodbc` connection or a `sqlite3` one, chosen by
`USE_SQL_SERVER`. Statements use `?` parameters rather than glued strings, which is what protects
against SQL injection; both drivers use `?`.

**On SQL Server every call goes through a stored procedure**, created at startup and called by name:

| Procedure | Caller | What it does |
|---|---|---|
| `SaveDocument` | `save()` | INSERT one result row |
| `GetDocuments` | `get_all()` | SELECT every row, newest first |
| `GetDocumentsFiltered` | `get_filtered()` | SELECT through the `DocumentsExport` view, filtered by date window + document type |

**SQLite keeps plain SQL** — it is a file, not a server, and has no stored procedures. Every SQL
Server-only statement sits inside `if _use_sql_server():`, so the Mac path is untouched.

Three rules the code follows for pyodbc + T-SQL:

1. `CREATE OR ALTER PROCEDURE` is **alone in its own `cursor.execute()`** — SQL Server requires it
   to be the only statement in a batch, and each `execute()` is one batch.
2. **No `GO`.** It is not SQL, only an SSMS separator. In Python a new `execute()` *is* the GO.
3. `SET NOCOUNT ON;` at the top of each procedure, so "(3 rows affected)" chatter cannot confuse
   pyodbc when it reads results.

**Schema catch-up.** The table has grown over time. `ensure_table()` runs
`IF COL_LENGTH('Documents','...') IS NULL ALTER TABLE ...` guards *before* creating the view and
procedures, because a view naming a missing column fails immediately. It also connects with
`autocommit=True`: with one big commit at the end, a single failing statement rolled back
everything, leaving old procedures in place while Python expected the new ones. `GET /api/health`
re-runs `ensure_table()`, so it doubles as a repair button.

**Why `GetDocumentsFiltered` takes DATETIME2.** `CreatedAt` is text (`NVARCHAR(40)`, local time).
The procedure does a real date comparison with `TRY_CAST(CreatedAt AS DATETIME2)` — `TRY_CAST`
returns NULL for a value it cannot convert, so one malformed row is skipped instead of crashing the
export. There is no timezone conversion anywhere: rows are written with `datetime.now()`, the user
picks local dates, and a document and the report it appears in are always on the same clock.

Check it in SSMS:

```sql
SELECT name, create_date FROM sys.procedures ORDER BY name;
-- expect: GetDocuments, GetDocumentsFiltered, SaveDocument
SELECT TOP 5 Id, FileName, Model, CreatedAt FROM Documents ORDER BY Id DESC;
```

If startup prints a permissions error, the API keeps running (database problems are non-fatal) but
saving fails until the procedures exist. Copy the `CREATE OR ALTER PROCEDURE` statements out of
`ensure_table()` and run them once in SSMS, or ask the DBA.

---

## 7. Audit report search

Makes the audit workbook searchable through Azure AI Search, so "which fields are extracting badly"
is answered by retrieval rather than by scrolling a spreadsheet.

**The workbook is the only knowledge base.** Nothing else is indexed — not `documents.db`, not blob
storage, not the raw OCR output.

### Where is Azure AI Search in all this? (no blob storage needed)

A fair question, because most tutorials start by uploading files to blob storage. Nothing here
does, and nothing is stored "in the website" either. What actually happens:

```
the workbook -> Python builds 84 JSON chunks -> uploaded over HTTPS -> Azure AI Search
                                                                       (your search service)
```

Azure AI Search **only ever stores JSON** — that is true of every project, including the ones
that appear to index PDFs. Those use an **indexer** plus a **skillset**: a robot Azure runs that
opens files in blob storage, extracts the text, chunks it and wraps it in JSON. The PDF never
reaches the index; only JSON does. That is the *pull* model, and blob storage is a requirement
of that model, not of Azure AI Search.

This project uses the *push* model instead: the Python builds the JSON and uploads it directly,
so there is no blob storage and no indexer to configure. That is a deliberate choice, not a
shortcut — the built-in text-split skill cuts by **character count**, which would slice through
the middle of a document's 33 rows and destroy the grain decision the chunking is built on.

To see for yourself that Azure really is doing the work:

```bash
python -c "
import hybrid_search
c = hybrid_search.search_client()
print(c.get_document_count())            # 84
print(c.get_document(key='comp-gst-amount'))   # the JSON, as Azure stores it
"
```

Or open the Azure portal → your Search service → **Search management → Indexes**, where
`audit-keyword` and `audit-hybrid` both show 84 documents. Every query the app runs is answered
there; `@search.score` and `@search.reranker_score` are Azure's numbers, computed on Azure.

### Asking questions from the Angular page

The main app has an **AI Search** option in the service dropdown. Type a question, and answer it
one of two ways:

| Mode | What it does | Best at |
|---|---|---|
| **Grounded answer (RAG)** | retrieves chunks, then the model writes an answer citing each `[chunk-id]` | open questions: "which fields are extracting badly" |
| **Agent picks a tool** | Semantic Kernel chooses between an exact SQL count, a document lookup, search, or an applicability check — and shows which | counting, and single named invoices |

RAG mode has a **grain** selector, and it matters more than it looks. Over *field* chunks the
answer leads with the real gaps (`GST Amount` 72.2%, `Tax Details` 83.3%). Over *document*
chunks the same question answers with optional fields no invoice ever carried. The agent picks
the grain itself, which is the honest argument for it.

### Keeping the index current

**Exporting the Excel report also rebuilds the search index.** That closes a loop that used to
need a terminal: export the workbook, copy it next to the module, run `build`. Now the export
button does all three, and the page reports both stages separately — a failed re-index does not
mean a failed download.

```
This is your Excel sheet downloaded.
Search index updated - 84 chunks (18 documents, 66 fields) from Audit_04082026.xlsx.
```

Behind it, `POST /api/search/reindex` rebuilds the workbook through the *same* two calls
`/api/export` uses, so the file you download and the file that gets indexed cannot disagree.

Two things worth knowing:

- **It writes to `AUDIT_XLSX` if that is set**, because that is where the chunker reads from —
  writing anywhere else would silently re-index the previous file. The workbook it replaces is
  kept once as `<name>.previous.xlsx`, since a fresh export only covers documents currently in
  the database and may well be smaller than what it replaced.
- **Chunks that disappear are deleted.** `upload_documents` only adds or overwrites, so without
  this a document that drops out of the date window would keep its chunk forever and search
  would go on returning an invoice the audit no longer covers.

### The explorer UI

```bash
uvicorn main:app --port 8001      # then open http://localhost:8001/search
```

| Tab | What it shows |
|---|---|
| **Chunks** | all 84 chunks, filterable by sheet and grain — what is actually in the index |
| **Compare** | one question run four ways side by side, so you can watch them disagree |
| **Ask (RAG)** | retrieved chunks + a grounded answer, every `[chunk-id]` clickable back to its source |
| **Agent** | the Semantic Kernel agent choosing a tool, with the call and arguments shown before the answer |

### From the terminal

```bash
python keyword_search.py chunks            # no Azure needed - prints what would be uploaded
python keyword_search.py build             # creates the index and uploads
python keyword_search.py build --recreate  # drops it first - needed after a schema change
python keyword_search.py ask "which fields are extracting badly" --grain field --top 30 --answer

python hybrid_search.py build           # the same, plus embeddings and hybrid search
python sql_comparison.py                 # SQL vs search, the same questions side by side
python sql_comparison.py --check         # the trend sheets must reconcile against the log
python retrieval_metrics.py                 # recall@5 for all four methods
python field_applicability.py --gaps     # only the genuine extraction gaps

python agent_service.py "how many documents are missing the vendor GSTIN"
```

Always run `chunks` first. It contacts nothing, costs nothing, and catches the problems that are
expensive to find after upload.

### Concepts

**Chunk** — one piece of text with an ID. This project produces 84. In Azure's vocabulary a
"document" is a chunk, not a Word file.

**Index vs indexer** — an index is the container. An *indexer* is a robot that fetches and converts
source files; this project does not use one.

**Keyword search (BM25)** — matches words, scoring rarer ones higher. Excellent at identifiers like
`MBS25/26/017`. Useless for "extracting badly" when the chunk says "poor quality".

**Embedding** — a sentence as ~1536 numbers. The numbers are a *position*, not a code: similar
meanings land near each other. Meaning becomes distance.

**Hybrid search** — keyword and vector together, merged by Reciprocal Rank Fusion, which uses only
each result's *position* because a BM25 score of 8.2 and a cosine of 0.79 are not comparable.

**Semantic ranker** — a second pass where a language model rereads the top ~50 candidates and
reorders them. Runs *after* retrieval, needs Basic tier or above. In Azure, "semantic" usually means
this ranker rather than vectors — worth asking which is meant.

**Semantic Kernel** — unrelated despite the name. An orchestration SDK that wraps functions as tools
an LLM can call. It does no retrieval and has no opinion about chunking.

### The data and the chunking

One workbook, six sheets, three indexed.

| Sheet | Rows | One chunk is | Chunks |
|---|---|---|---|
| AI EXTRACTION LOG | 594 | one **document** (33 rows grouped) | 18 |
| COMPLETENESS TREND | 33 | one row | 33 |
| VALIDITY TREND | 38 | one row | 33 |

Counts are from `Audit_31072026.xlsx` — 18 documents × 33 fields. **84 chunks**, 18 document-grain
and 66 field-grain.

The rule underneath all three: **a chunk should be one complete thought.** If understanding it
requires the chunk next door, the cut is in the wrong place.

A trend row is already complete — `Amount Due, 18 records, 16 null, 11.1%`. A log row is not:
`MBS25/26/017 | AmountDue | (blank) | N | Fail` tells you one field failed but not whether the other
32 were fine. Indexing row by row gives 594 chunks and **runs without error** — then search returns
three fields out of 33 as if they described the whole invoice. It fails silently, which is worse
than failing loudly. The fix is `groupby(["Number", "Source_File"])`; the key is both columns
because two documents can carry the same number, and the number can be blank when extraction
missed it.

**Both grains are indexed** because the questions need different cuts. Field chunks look *down a
column* — one field across every document. Document chunks look *across a row* — every field of one
document.

**Numbers are written out as words.** A chunk reads "Completeness 11.1% — poor completeness, bad
extraction quality, below the 90 percent threshold, needs attention", not just "11.1%". Search
matches text, and nothing connects "which fields extract badly" to a bare number; embeddings do not
help either, since a vector has no sense that 0.111 is small. This is **verbalization**, and it is
the difference between a retrieval layer that works and one that mysteriously does not.

**Line items are collapsed to a count.** One document's `Line Items` holds 26 products and 3284
characters — left whole, the document's embedding means "cake tins and frying pans" rather than
"extraction quality". Stored as `Line Items: 26 items`. If invoice contents ever need searching,
emit a *second* chunk with `grain='contents'`; do not widen the existing one.

### Gotchas in the actual file

- Two column names carry a **trailing space**: `Field_Name(Keys from json) ` and `Type `. Exact
  lookups fail with a `KeyError` that never mentions the space. Read every sheet through `sheet()`.
- The "TREND" sheets have **no date column** — they are not time series. Each row aggregates one
  field across all documents.
- The last five rows of VALIDITY TREND are a spacer plus a **threshold legend**, not data. Filter on
  `Pass_Count`, not on the text: `Recommended Threshold` is both a legend row and a column header.

### Why push, not an indexer

Azure AI Search only stores **JSON**. The reason people think it "supports PDFs" is that Azure
offers an indexer + skillset — a robot that opens files in blob storage, extracts text, chunks it
and wraps it in JSON. The PDF never reaches the index.

```
PDF   --[Azure's indexer + skillset]-->  JSON  --> index
Excel --[this project's Python]-------->  JSON  --> index
```

The built-in text-split skill cuts by **character count**. That is fine for prose with no natural
boundary, and wrong for a table where the boundary is obvious and meaningful — it would slice
through the middle of a document's 33 rows and the grain decision would stop existing. So the
conversion happens here and Azure receives finished chunks. **Do not use "Import data" in the portal
for this workbook.**

### Checkpoints

**After `chunks`** — expect **84**, split 18 / 66.

- 594 means the grouping broke.
- 38 validity chunks means the `Pass_Count` filter is missing and the legend got indexed.
- One short with no error means two chunks slugged to the same id; `make_chunks()` raises instead.
- A `KeyError` on a column means the names do not match the constants at the top of the file.

**After `build`** — portal → Search management → Indexes should read 84. A count of 0 with no errors
means the upload went to a different index name than the one being viewed.

**After `ask`** — read the chunks, not just the scores. "Amount Due" should return its completeness
and validity chunks, both at 11.1%.

### What search can and cannot do — measured

Search retrieves. It does not aggregate or compare.

`ask "which fields are extracting badly" --grain field` returns `comp-tax-details` (**83.3%**)
first, and the genuinely worst fields at **5.6%** do not appear at all. Nothing is broken:
`verdict()` did its job, every sub-90% chunk says "extracting badly" — but they all say it in the
same words, so BM25 cannot tell them apart and the reranker has no sense that 5.6 is smaller than
83.3. **Ranking is SQL's job.** `sql_comparison.py` answers it exactly.

What it is genuinely good at: **exact identifiers** (`ask "PSV/1650"` scores 2.58 with the
near-collision `BVN/1650` behind at 1.78), **filtering** (`--sheet`, `--grain`), and a **single
named field**.

The clear failure: **synonyms.** "documents where the tax number did not come through" returns 3
documents, only 1 of which is actually missing its GSTIN. The question says "tax number", the chunks
say "GSTIN" — no shared letters, no match.

Mean recall@5 over six queries (`retrieval_metrics.py`, ground truth from SQL):

| Method | recall@5 |
|---|---|
| Keyword (BM25) | **37%** |
| Vector only | 27% |
| Hybrid (RRF) | 34% |
| Hybrid + semantic reranker | 26% |

**Vectors do not beat keyword search on this data, and the reranker is the weakest of the four** —
the opposite of what was assumed before anyone measured. Two honest caveats:

1. **One query is not evidence.** The first synonym query gave vector 60% against keyword 40%, which
   looked like a clean win. Across six queries it reverses. That single result was noise, and it
   would have been written up as a finding if the harness had not been built.
2. **The questions are the problem, not the retrieval.** Every query in the set is "find the
   documents where field X is empty" — a `WHERE` clause wearing a sentence. Retrieval scoring 37%
   where SQL scores 100% is the boundary doing its job.

Why embeddings add so little here: the 18 document chunks have a mean pairwise cosine of **0.883**,
some pairs at **0.995**. They are all the same kind of thing described with the same 33 field names,
so there is very little for cosine distance to separate. Embedding a leaner string with the verdict
boilerplate stripped made it *worse* (0.883 → 0.878, closest pair 0.995 → 0.997).

At 84 chunks the whole set would fit in one prompt, and that would be more accurate than retrieval.
**At this size the honest recommendation is keyword plus RAG**; vectors are infrastructure for when
the corpus grows. The pipeline is the same fifteen-line change at 84 chunks or 84,000 — and at 84
you can still check every answer by hand, which is exactly how the above was found.

### RAG — where the model does the comparing

`--answer` sends the retrieved chunks to the chat deployment to write a grounded answer. This closes
the gap: retrieval cannot order 5.6% against 83.3%, but a model with both numbers in front of it
can. Measured against the same index that ranked `comp-tax-details` first:

```
1. Previous Unpaid Balance — 5.6%  [comp-previous-unpaid-balance]
2. Customer Id             — 5.6%  [comp-customer-id]
3. Remittance Address      — 5.6%  [comp-remittance-address]
...
```

That agrees with `sql_comparison.py`'s SQL. Three rules in `RAG_SYSTEM` make the difference between this
and a confident invention:

- **Only the chunks.** Asked for a bank account number that appears nowhere, it answers *"not
  present in the provided chunk(s)"* rather than producing a plausible one.
- **Cite every claim**, so any answer can be checked against the report in seconds.
- **Order, and show the number ordered by.** Without this the model returns a correct but unordered
  list — not an answer to "which are worst".

Retrieval still decides what the model can see. `--top` defaults to 5 for reading and 15 with
`--answer`; an aggregate question needs more (`--top 30`). **RAG does not make search complete; it
makes what search found usable.** For an exact count, SQL remains the honest answer.

### A note on MCP — currently unused

`requirements.txt` pins `mcp==1.29.0`, which is easy to mistake for "this project uses MCP".
**It does not.** Nothing here imports `mcp`, and Semantic Kernel only touches it if you import
`semantic_kernel.connectors.mcp`, which no code does. The agent runs fine either way; the pin is
insurance for work not yet done.

The obvious next step, if it is ever wanted, is to expose the four audit tools below as an MCP
**server**, so Claude Desktop or any other MCP client could query the audit workbook directly.
That is a separate piece of work, not something the pin alone provides.

### The agent

`agent_service.py` is a Semantic Kernel agent with four tools, and the interesting part is which one
it picks:

| Tool | Job |
|---|---|
| `count_documents` | exact counts — a field name from a fixed allow-list, substituted into a parameterised query. A model-authored SQL fragment never reaches the database |
| `search_chunks` | open-ended retrieval |
| `get_document` | one invoice's full chunk |
| `field_applicability` | is a low figure a real gap, or a field that never applied? |

```bash
python agent_service.py "how many documents are missing the vendor GSTIN"   # -> count_documents, exactly 5
python agent_service.py "what was on invoice PSV/1650"                      # -> get_document
python agent_service.py "is the vendor fax number extracting badly"         # -> field_applicability
```

A right answer reached by the wrong tool is a failure here: search cannot count, so
`count_documents` returning exactly 5 is the point.

It uses `OpenAIChatCompletion` with a plain `AsyncOpenAI` client pointed at the v1 endpoint —
**never `AzureChatCompletion`**, which demands `AsyncAzureOpenAI` and forces an api-version this
project avoids everywhere.

### Findings in the source data

All four are about the audit pipeline, not about search, and all are worth raising with whoever owns
it. The first two were found against an earlier export and have since been fixed upstream; the
guards written for them stayed in.

**1. A document was ingested twice — fixed.** `TABGV 2425-394` had 66 rows where every other
document had 33. It now has exactly 33 and there are no duplicate `Number + Source_File +
Field_Name` triples. The `drop_duplicates` call stays as a guard.

**2. The percentages were all slightly off — fixed.** COMPLETENESS TREND reported `Total_Records = 9`
for every field when there were 8 real documents, the denominator inflated by the duplicate. It now
reports 18.

**3. Completeness and validity restate each other — the apparent difference was a validator bug.**
This was got wrong twice, which is worth recording. Four date fields diverged sharply, which looked
like validity measuring something real. In fact sixteen populated dates were failing validation and
every one was a perfectly readable date that `DATE_FORMATS` had no pattern for — `30-Jul-2025` (the
table had `%d-%b-%y` but not `%d-%b-%Y`), `06 MARCH, 2004` (the comma), `12/15/2019` (month-first,
from a US invoice). The divergence was measuring the validator, not the data.

Four formats were added and all sixteen now parse, with no regression among the nine that already
passed. **Validity is now identical to completeness on all 33 fields**, so the original suspicion
was right: the column currently adds no information. Either it needs checks with teeth — a date in
the future, an amount that does not equal base plus tax, a GSTIN whose state code disagrees with the
vendor address — or it duplicates a column that already exists.

**4. The headline completeness figure counts fields that were never there.** Azure's
`prebuilt-invoice` schema is built around US invoices. `Vendor Fax Number` scores 5.6% because
seventeen invoices did not print one, not because OCR missed it seventeen times. The metric treats
"not applicable" and "OCR failed" identically.

| | Mean completeness |
|---|---|
| Headline, all 33 fields | 50.8% |
| **12 core fields** | **93.1%** — eight at 100% |
| 21 optional fields | 26.7% |

So the extraction is good, and the real audit finding is small and specific: `GST Amount` and
`Vendor GSTIN` each missing from **5 of 18** documents, `Tax Details` from 3, `Customer GSTIN` from
2. Everything else is schema noise.

This split deliberately lives **outside the workbook** — in `field_applicability.py` and in a sentence on
each field chunk — because the workbook has to keep mirroring `DriftTemplateInterns.xlsx` exactly,
verbatim headers and all. Nothing enforces that but discipline: an appended column would pass all 36
tests silently.

**Completeness is not accuracy.** The audit measures whether a field was *populated*, never whether
it was *right*, so a confidently wrong extraction scores the same as a correct one. Ten synthetic
invoices with a ground-truth manifest exist for this and are already in the export
(`IN-SYN-001.pdf` … `IN-SYN-010.pdf`, including the credit note `CN/ASE/25-26/007`), but the
**manifest is not in the repo**, so the join has not been made. Until it is, every number here
describes completeness, never correctness.

### Cost

Azure AI Search bills **hourly whether or not it is queried** — Basic is roughly USD 75/month. When
finished experimenting, delete the **service**, not just the index. Embedding 84 chunks costs a
fraction of a cent, so re-running `build` is effectively free. The expensive resource is the one
sitting idle.

---

## 8. Troubleshooting

### Setup and running

| Symptom | Fix |
|---|---|
| `'python' is not recognized` | Use `py -m venv .venv`, `py -m pip install ...`, or reinstall Python with *"Add python.exe to PATH"* ticked |
| PowerShell: `Activate.ps1 cannot be loaded` | `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`, reopen PowerShell — or use Command Prompt, which has no script policy |
| `'uvicorn' is not recognized` | The venv is not active. Activate it, or skip activation: `.venv\Scripts\python -m uvicorn main:app --port 8001` |
| PowerShell: `npm.ps1 cannot be loaded` | Use `npm.cmd install` / `npm.cmd start`, or Command Prompt |
| `pip install` fails with an SSL/proxy error | `pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org -r requirements.txt` |
| Changed `.env` but nothing changed | Restart the backend — `.env` is read once at startup |
| Page says "Could not reach the backend" | Terminal 1 is not running. Wait for the "Uvicorn running" line |

### Azure and the database

| Symptom | Fix |
|---|---|
| **401 / "invalid key"** in the answer | Wrong key in `.env` — `AZURE_OPENAI_KEY` or `DOC_INTELLIGENCE_KEY` |
| The answer **ignores the document** | OCR read nothing. Recheck `DOC_INTELLIGENCE_ENDPOINT` / `_KEY`, and that the file has text |
| `ERROR saving to the database` | `DocIntelligenceDb` does not exist, ODBC Driver 18 is missing, or the server name in `SQL_CONNECTION_STRING` is wrong |
| `WARNING: could not prepare the database at startup` | Same causes — the app still starts, but saving fails until fixed |
| pyodbc: `Can't open lib 'ODBC Driver 18 for SQL Server'` | Install Driver 18. If only 17 is available, change the name inside `SQL_CONNECTION_STRING` |
| Login error from SQL Server | Named instance? Use `Server=localhost\SQLEXPRESS`. Check `SELECT name FROM sys.databases;` |

### Search

| Symptom | Cause |
|---|---|
| `KeyError` on a column | Trailing space, or sheet name mismatch. Read through `sheet()`, not `pd.read_excel` directly |
| 594 chunks instead of 84 | `groupby` removed or the grouping key changed |
| 38 validity chunks instead of 33 | the `Pass_Count.notna()` filter is missing, so the threshold legend got indexed |
| 403 on upload | A query key used where an admin key is needed |
| `unknown_model` on the embedding call | The model exists in the region catalogue but is not **deployed**. Foundry → Deployments → Deploy model |
| 404 on the embedding call | Deployment name wrong — check the Deployments blade, not the model list |
| Every upload fails with a dimension error | `EMBED_DIMS` does not match the deployed model (`3-small` is 1536, `3-large` is 3072) |
| `ask` errors on `query_type="semantic"` | Free tier. Semantic ranking needs Basic or above |
| Index shows 0 documents | Indexing takes a few seconds — refresh. If still 0, the index name differs from the one being viewed |
| `CannotChangeExistingField` on build | Azure cannot alter a field's attributes in place. Re-run with `build --recreate` |
| Document chunks answer a question about fields | Expected. Use `--grain field` — a document chunk lists nineteen failing field names, so the reranker treats it as an answer |
| `ImportError: streamablehttp_client` from Semantic Kernel | Only reachable by importing `semantic_kernel.connectors.mcp`, which nothing here does — see the MCP note below. `requirements.txt` pins `mcp==1.29.0` because 2.x removed that symbol |

---

## 9. Never commit

- **`.env`** — holds the Azure keys and the search admin key
- **`*.xlsx`** — the audit workbooks hold real vendor names, GSTINs and invoice values
- **`chunks.json`** — the same values extracted into text
- **`*.db`** — the local SQLite database holds extracted document text

All four are in `.gitignore`. Verify with `git status` before every push: once something is in git
history, removing it properly is painful.
