# Architecture — Questions & Answers

A short Q&A explaining how this project is built. Each answer points to the real file.

---

### 1. What does this project do?
You upload one or more PDFs or images, pick a Document Intelligence model, and type a prompt. The app
reads the text (and key-value pairs) out of each file with OCR, asks an AI your prompt about that
text, shows the answer and the token counts, and saves everything to a database.

### 2. What is the overall architecture?
Three parts that talk over HTTP:

```
Browser (Angular)  ──►  Python API (FastAPI)  ──►  Azure OCR + Azure OpenAI
   localhost:4200          localhost:8000              │
        ▲                       │                      ▼
        └───────  JSON  ────────┴────────►  Database (SQL Server or SQLite file)
```

The **frontend** is a web page, the **backend** is the server that does the work, and the
**database** stores the results.

### 3. What are the main technologies?
- **Frontend:** Angular + TypeScript
- **Backend:** Python with **FastAPI** + **Pydantic**
- **OCR:** Azure AI Document Intelligence (Read / Layout / Invoice / Receipt / ID Document)
- **AI:** Azure OpenAI
- **Database:** Microsoft SQL Server, or SQLite (a local file) for testing

### 4. How does the frontend call the backend?
The Angular app uses `HttpClient` to send a POST request to `http://localhost:8000/api/analyze`
with JSON `{ model, prompt, files: [{ name, base64 }] }`. `HttpClient` is turned on by
`provideHttpClient()` in [app.config.ts](src/app/app.config.ts), and the browser is allowed to call
the backend by the **CORS** middleware set in [main.py](backend-python/main.py).

### 5. What are the API routes, and how does routing work?
FastAPI uses **decorators** on plain functions in [main.py](backend-python/main.py):
- `@app.get("/api/models")` — the list of Document Intelligence models for the dropdown
- `@app.post("/api/analyze")` — analyze files and save them
- `@app.get("/api/analyses")` — return everything saved

The function right under each decorator handles that URL. FastAPI reads the type hints (Pydantic
models) to validate the request and to turn the return value into JSON automatically.

### 6. What happens step-by-step when I click "Get AI response"?
1. `chooseFiles()` reads each file and turns it into base64 text — [app.ts](src/app/app.ts)
2. `getAnswer()` POSTs `{ model, prompt, files }` to `/api/analyze` — [app.ts](src/app/app.ts)
3. `analyze()` loops over each file — [main.py](backend-python/main.py)
4. `ocr_service.analyze()` reads the text + key-value pairs with Azure OCR — [ocr_service.py](backend-python/ocr_service.py)
5. `ai_service.answer()` asks the AI the prompt about that text — [ai_service.py](backend-python/ai_service.py)
6. `database.save()` inserts a row — [database.py](backend-python/database.py)
7. The backend returns JSON `{ results }`, and the page shows one card per file — [app.html](src/app/app.html)

### 7. Why is the file sent as base64?
JSON can only hold text, not raw file bytes. Base64 turns the file's bytes into text so it fits in
the JSON body. The backend turns it back into bytes with `base64.b64decode` before sending it to OCR
(`toBase64` in [app.ts](src/app/app.ts); decode in [main.py](backend-python/main.py)).

### 8. How does OCR (reading the text) work?
`ocr_service.analyze()` uses the **Azure SDK** `DocumentIntelligenceClient`. It calls
`begin_analyze_document(model_id, ...)`, then `poller.result()` waits for Azure to finish. The text
comes from `result.content`; key-value pairs come from `result.key_value_pairs` (Layout model) or
from the named fields in `result.documents` (Invoice / Receipt / ID). Each pair keeps Azure's
**confidence** score. See [ocr_service.py](backend-python/ocr_service.py).

### 9. How does the AI produce the answer?
`ai_service.answer()` uses the **Azure OpenAI SDK** client and calls
`client.chat.completions.create(...)` with two messages: your **prompt** as the `system` message and
the **document text** as the `user` message. It reads the reply from `choices[0].message.content`,
and reads the token counts from the response's `usage` (input, output, total). See
[ai_service.py](backend-python/ai_service.py).

### 10. How does the database connect?
[database.py](backend-python/database.py) uses plain SQL. `_open()` returns either a
`pyodbc` connection (using the `SQL_CONNECTION_STRING`) or a `sqlite3` connection (a local file),
chosen by the `USE_SQL_SERVER` setting. `ensure_table()` runs at startup to create the table if
needed.

### 11. What SQL does it run?
Just the fundamentals, hand-written:
- `ensure_table()` → `CREATE TABLE Documents (...)`
- `save()` → `INSERT INTO Documents (...) VALUES (?, ?, ...)`
- `get_all()` → `SELECT ... FROM Documents ORDER BY Id DESC`

The INSERT uses **parameters** (`?`) instead of gluing strings together, which protects against SQL
injection. Both SQLite and pyodbc (SQL Server) use `?` for parameters.

### 12. Where are the keys and passwords kept?
In a git-ignored file, `backend-python/.env` (never pushed to GitHub, never in the ZIP). It's loaded
at startup by **python-dotenv** (`load_dotenv(...)` at the top of [main.py](backend-python/main.py),
before anything reads the keys). A committed `backend-python/.env.example` shows what to fill in.

### 13. How do you switch between Azure services and SQLite/SQL Server?
All in `backend-python/.env`, with **no code change**:
- Azure OpenAI: `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_KEY`, `AZURE_OPENAI_DEPLOYMENT`
- Azure OCR: `DOC_INTELLIGENCE_ENDPOINT`, `DOC_INTELLIGENCE_KEY`
- Database: `USE_SQL_SERVER=true` (with `SQL_CONNECTION_STRING`) for SQL Server, or `false` for a
  local SQLite file.

If an Azure endpoint is left as a placeholder, that step is simply skipped, so the app still runs.

### 14. How are errors handled?
`analyze()` wraps each file's OCR + AI in a `try/except`. If either fails (for example a wrong key
returns **401**), the reason is put into the answer text, so it shows in the result card. The
database `save()` is wrapped separately, so if SQL Server is unreachable you see
`ERROR saving to the database: ...` instead of the whole request crashing. `ensure_table()` at
startup is also guarded, so a bad SQL connection prints a `WARNING` but the app still starts. See
[main.py](backend-python/main.py).

### 15. Where is each essential file?

**Backend** (`backend-python/`)
| File | Job |
|---|---|
| [main.py](backend-python/main.py) | Startup: load `.env`, CORS, create table; the routes `/api/models`, `/api/analyze`, `/api/analyses` |
| [ocr_service.py](backend-python/ocr_service.py) | Reads file text + key-value pairs with Azure Document Intelligence |
| [ai_service.py](backend-python/ai_service.py) | Calls Azure OpenAI and returns the answer + token counts |
| [database.py](backend-python/database.py) | Plain SQL: CREATE / INSERT / SELECT (SQL Server or SQLite) |
| [models.py](backend-python/models.py) | The Pydantic request/result shapes |
| [.env.example](backend-python/.env.example) | Template for the private `.env` (safe defaults, no secrets) |

**Frontend** (`src/`)
| File | Job |
|---|---|
| [main.ts](src/main.ts) | Starts the Angular app |
| [app/app.config.ts](src/app/app.config.ts) | Turns on `HttpClient` |
| [app/app.ts](src/app/app.ts) | The screen's logic: read files → base64 → call backend |
| [app/app.html](src/app/app.html) | The screen: dropdown, file picker, prompt, result cards |
| [styles.css](src/styles.css) | Basic styling |

### 16. What are the data shapes?
Defined with Pydantic in [models.py](backend-python/models.py):
- **Request** (`AnalyzeRequest`): `model` + `prompt` + `files[]`, each `{ name, base64 }`
- **Result** (`FileResult`): `{ file_name, model, text, key_values[], answer, tokens }`, where each
  `KeyValue` is `{ key, value, confidence }` and `tokens` is `{ prompt_tokens, completion_tokens,
  total_tokens }`
- **Saved row** (`Documents` table columns): `Id, FileName, Model, Prompt, DocumentText,
  KeyValuesJson, Answer, PromptTokens, CompletionTokens, TotalTokens, CreatedAt`
