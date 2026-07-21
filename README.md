# Document Intelligence Sample Project

Upload one or more PDFs or images, type a prompt, and get an AI answer for each file. The text is
read by Azure OCR, answered by Azure OpenAI, and every result is saved to a database.

- **Frontend** — Angular (http://localhost:4200)
- **Backend** — Python, FastAPI (http://localhost:8000) — see [backend-python/](backend-python/)
- **OCR** — **Azure AI Document Intelligence** reads the text (+ key-value pairs) from each file.
- **AI** — **Azure OpenAI** answers the prompt and reports the token counts.
- **Database** — plain SQL. **Microsoft SQL Server** on the office laptop, or a local **SQLite** file.

## Quick start (this laptop)
Two terminals:

**Backend** (needs Python 3.10+)
```bash
cd backend-python
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then fill in your Azure keys (Windows: copy .env.example .env)
uvicorn main:app --port 8000
```
**Frontend**
```bash
npm install      # first time only
npm start        # http://localhost:4200
```
Open http://localhost:4200 → choose a PDF or image → type a prompt → **Get AI response**.

## Settings — `backend-python/.env` (git-ignored)
All secrets live in one private file, `backend-python/.env` (never committed, never in the ZIP).
Create it by copying the template — `cp .env.example .env` — then fill in your 5 Azure values:
```
AZURE_OPENAI_ENDPOINT=...          # Azure OpenAI
AZURE_OPENAI_KEY=...
AZURE_OPENAI_DEPLOYMENT=...
DOC_INTELLIGENCE_ENDPOINT=...      # Azure OCR (Document Intelligence)
DOC_INTELLIGENCE_KEY=...
```
The template is set up to save to **Microsoft SQL Server** (`USE_SQL_SERVER=true`). To save to a
**local file** instead (no SQL Server needed), set `USE_SQL_SERVER=false`.
Full step-by-step: **[requirements/README.md](requirements/README.md)** and
**[backend-python/README.md](backend-python/README.md)**.

## Where the code is
```
backend-python/
  main.py            FastAPI app + routes: /api/models, /api/analyze, /api/analyses
  ocr_service.py     reads file text + key-value pairs via Azure Document Intelligence
  ai_service.py      calls Azure OpenAI and returns the answer + token counts
  database.py        plain SQL: CREATE TABLE / INSERT / SELECT (SQL Server or SQLite)
  models.py          Pydantic request/response shapes
src/app/app.ts, src/app/app.html   the whole Angular screen
```

## View the saved data
- **SQL Server** (office laptop): use SSMS or the VS Code **"SQL Server (mssql)"** extension →
  `SELECT * FROM Documents;`
- **SQLite** (when `USE_SQL_SERVER=false`): open `backend-python/documents.db` with the VS Code
  **"SQLite Viewer"** extension.

## Office laptop (EY SQL Server) + syncing two laptops
See **[requirements/README.md](requirements/README.md)** — the install list, EY SQL Server setup,
and how to run it there. If `git` is blocked on the office laptop, that guide uses a **ZIP
download** from GitHub instead of `git clone` / `git pull`.
