# Document Intelligence — Python backend (FastAPI + Pydantic)

A second backend, written in Python. You pick an Azure **Document Intelligence** model
(Read / Layout / Invoice / Receipt / ID Document); it extracts the text and **key-value pairs
with confidence scores**, optionally asks **Azure OpenAI** your prompt (and counts the input/output
**tokens**), and saves everything to a database.

> This is separate from the `.NET` backend in `backend/`. Both stay in the repo; the Angular app
> points at this one (port **8000**).

## Run it
```bash
cd backend-python
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --port 8000
```
Then start the Angular app from the project root (`npm start`) and open http://localhost:4200.

## Your secrets — create `backend-python/.env`
Create the file once (it is git-ignored, never pushed) and fill in your Azure values:
```
AZURE_OPENAI_ENDPOINT=https://<your-resource>.openai.azure.com
AZURE_OPENAI_KEY=<your-azure-openai-key>
AZURE_OPENAI_DEPLOYMENT=<your-deployment-name>
AZURE_OPENAI_API_VERSION=2024-10-21
DOC_INTELLIGENCE_ENDPOINT=https://<your-resource>.cognitiveservices.azure.com
DOC_INTELLIGENCE_KEY=<your-ocr-key>
USE_SQL_SERVER=false
```
- Saves to a local file `documents.db` by default (no database server needed).
- To use **SQL Server** instead: set `USE_SQL_SERVER=true` and
  `SQL_CONNECTION_STRING=Driver={ODBC Driver 18 for SQL Server};Server=...;Database=...;Uid=...;Pwd=...;TrustServerCertificate=yes;`
  (needs the Microsoft **ODBC Driver 18 for SQL Server** installed).

## The files
| File | Job |
|---|---|
| `main.py` | FastAPI app + routes: `GET /api/models`, `POST /api/analyze`, `GET /api/analyses` |
| `models.py` | Pydantic request/response shapes |
| `ocr_service.py` | Azure Document Intelligence — runs the chosen model, returns text + key-value pairs (+ confidence) |
| `ai_service.py` | Azure OpenAI — the answer + token counts (from the response's `usage`) |
| `database.py` | Plain SQL: SQLite (default) or SQL Server |

## What each model gives you
| Model | Output |
|---|---|
| Read | Full text (OCR) only |
| Layout | Text + general key-value pairs (with confidence) |
| Invoice / Receipt / ID Document | Named fields (e.g. Total, MerchantName) with confidence |
