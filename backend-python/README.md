# Document Intelligence — Python backend (FastAPI + Pydantic)

This is the backend the Angular app talks to (port **8000**). You pick an Azure **Document
Intelligence** model (Read / Layout / Invoice / Receipt / ID Document); it extracts the text and
**key-value pairs with confidence scores**, optionally asks **Azure OpenAI** your prompt (and counts
the input/output **tokens**), and saves everything to a database.

> There is also an older `.NET` backend in `backend/`. It stays in the repo but is **not** used by
> the app anymore — everything below is what you run.

## Run it (needs **Python 3.10 or newer**)
```bash
cd backend-python
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --port 8000
```
Then start the Angular app from the project root (`npm start`) and open http://localhost:4200.

## Your secrets — create `backend-python/.env`
The `.env` file holds your Azure keys. It is **git-ignored** (never pushed, never in the ZIP), so you
create it once on each machine by copying the template:
```bash
cp .env.example .env          # Windows: copy .env.example .env
```
Then open `.env` and fill in your Azure values:
```
AZURE_OPENAI_ENDPOINT=https://<your-resource>.openai.azure.com
AZURE_OPENAI_KEY=<your-azure-openai-key>
AZURE_OPENAI_DEPLOYMENT=<your-deployment-name>
DOC_INTELLIGENCE_ENDPOINT=https://<your-resource>.cognitiveservices.azure.com
DOC_INTELLIGENCE_KEY=<your-ocr-key>
```
That's the five Azure values: OpenAI (endpoint, key, deployment) and Document Intelligence
(endpoint, key). `AZURE_OPENAI_DEPLOYMENT` is just the **name** of your model in Azure OpenAI, not a
secret.

## Database — SQL Server (office laptop) or a local file
The template (`.env.example`) is set up for **Microsoft SQL Server** on the office laptop:
```
USE_SQL_SERVER=true
SQL_CONNECTION_STRING=Driver={ODBC Driver 18 for SQL Server};Server=localhost;Database=DocIntelligenceDb;Trusted_Connection=yes;TrustServerCertificate=yes;
```
For this to work on the office laptop:
1. Install the Microsoft **ODBC Driver 18 for SQL Server** (a small, separate download from SSMS).
2. In SQL Server Management Studio, create the database once: `CREATE DATABASE DocIntelligenceDb;`
   (the app makes the `Documents` **table** for you on startup).
3. `Server=localhost` + `Trusted_Connection=yes` means "the SQL Server on this laptop, using my
   Windows login" — the same thing SSMS connects to by default. If your instance has a name, use
   e.g. `Server=localhost\SQLEXPRESS`. To use a SQL username/password instead of your Windows login,
   see the alternative line in `.env.example`.

**No SQL Server?** Set `USE_SQL_SERVER=false` and it saves to a local file (`documents.db`) instead —
handy for testing on a machine without SQL Server.

## The files
| File | Job |
|---|---|
| `main.py` | FastAPI app + routes: `GET /api/models`, `POST /api/analyze`, `GET /api/analyses` |
| `models.py` | Pydantic request/response shapes |
| `ocr_service.py` | Azure Document Intelligence — runs the chosen model, returns text + key-value pairs (+ confidence) |
| `ai_service.py` | Azure OpenAI — the answer + token counts (from the response's `usage`) |
| `database.py` | Plain SQL: SQL Server (default) or a local SQLite file |

## What each model gives you
| Model | Output |
|---|---|
| Read | Full text (OCR) only |
| Layout | Text + general key-value pairs (with confidence) |
| Invoice / Receipt / ID Document | Named fields (e.g. Total, MerchantName) with confidence |
