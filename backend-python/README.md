# Document Intelligence — Python backend (FastAPI + Pydantic)

This is the backend the Angular app talks to (port **8000**). You pick an Azure **Document
Intelligence** model (Layout / Invoice / Receipt / ID Document); it extracts the text and **fields
(saved as structured JSON)**, confirms the file matches the chosen document type, optionally asks
**Azure OpenAI** your prompt (and counts the input/output **tokens**), and saves everything to a
database.

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
AZURE_OPENAI_BASE_URL=https://<your-resource>.services.ai.azure.com/openai/v1
AZURE_OPENAI_KEY=<your-azure-openai-key>
AZURE_OPENAI_DEPLOYMENT=<your-deployment-name>
DOC_INTELLIGENCE_ENDPOINT=https://<your-resource>.cognitiveservices.azure.com
DOC_INTELLIGENCE_KEY=<your-ocr-key>
```
That's the five Azure values: OpenAI (base URL, key, deployment) and Document Intelligence
(endpoint, key). The base URL is the **v1** endpoint — it ends with `/openai/v1`, so **no
api-version is needed anywhere**. `AZURE_OPENAI_DEPLOYMENT` is just the **name** of your model in
Azure OpenAI, not a secret.

## Database — a local file (default) or SQL Server (office laptop)
The template (`.env.example`) saves to a **local SQLite file** (`USE_SQL_SERVER=false`) — the file
is `backend-python/documents.db`, created automatically, no server needed. On the office laptop,
switch to **Microsoft SQL Server**:
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

On SQL Server the app also creates three **stored procedures** on startup — `SaveDocument`,
`GetDocuments`, `GetDocumentsFiltered` — and all reads/writes go through them. Details and SSMS
checks: [STORED_PROCEDURES.md](STORED_PROCEDURES.md).

## Office laptop (Windows) — full steps, start to finish

**One-time setup** (only the first time on the laptop):
1. Install **Python 3.10+** (tick *"Add python.exe to PATH"*) and **Node.js**.
2. Install the Microsoft **ODBC Driver 18 for SQL Server**.
3. In SSMS run: `CREATE DATABASE DocIntelligenceDb;`

**Every new ZIP** (git is blocked, so on GitHub use *Code → Download ZIP*, then extract):

Using **Command Prompt (cmd)**:
```bat
cd backend-python
copy .env.example .env
:: open .env in Notepad: fill the Azure keys and set USE_SQL_SERVER=true
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --port 8000
```

Using **PowerShell** (same steps, different syntax):
```powershell
cd backend-python
Copy-Item .env.example .env
# open .env in Notepad: fill the Azure keys and set USE_SQL_SERVER=true
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --port 8000
```

Then the Angular app, in a **second** window from the **project root**:
```bat
npm install
npm start
```
and open http://localhost:4200.

Tip: instead of filling `.env` again each time, copy the `.env` file from the previous extracted
folder into the new `backend-python` folder — it is never inside the ZIP.

### If a command fails — the usual fixes

| Error | Fix |
|---|---|
| `'python' is not recognized` | Use the Windows launcher instead: `py -m venv .venv`, `py -m pip install -r requirements.txt` — or reinstall Python with *"Add python.exe to PATH"* ticked. |
| PowerShell: `Activate.ps1 cannot be loaded because running scripts is disabled` | Run `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`, close and reopen PowerShell — or simply use Command Prompt, which has no script policy. |
| `'uvicorn' is not recognized` | The venv is not active. Activate it first, or skip activation entirely: `.venv\Scripts\python -m uvicorn main:app --port 8000` |
| `pip install` fails with an SSL / certificate / proxy error (corporate network) | `pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org -r requirements.txt` |
| pyodbc: `Can't open lib 'ODBC Driver 18 for SQL Server'` | The ODBC driver is missing — install Driver 18. If only Driver 17 is available on the laptop, change the name inside `SQL_CONNECTION_STRING` in `.env` to `{ODBC Driver 17 for SQL Server}`. |
| Login/connection error from SQL Server | Named instance? Use `Server=localhost\SQLEXPRESS` in `.env`. Check the database exists: `SELECT name FROM sys.databases;` in SSMS. |
| PowerShell: `npm.ps1 cannot be loaded` | Run `npm.cmd install` / `npm.cmd start`, or use Command Prompt. |

**Verify the database side in SSMS** after analyzing one document:
```sql
SELECT name, create_date FROM sys.procedures ORDER BY name;
-- expect: GetDocuments, GetDocumentsFiltered, SaveDocument
SELECT TOP 5 Id, FileName, Model, CreatedAt FROM Documents ORDER BY Id DESC;
```

## The files
| File | Job |
|---|---|
| `main.py` | FastAPI app + routes: `POST /api/analyze`, `GET /api/analyses` |
| `models.py` | Pydantic request/response shapes |
| `ocr_service.py` | Azure Document Intelligence — runs the chosen model, returns text + fields (structured JSON) |
| `checker_service.py` | Confirms the file matches the chosen document type (via Azure OpenAI) |
| `ai_service.py` | Azure OpenAI — the answer + token counts (from the response's `usage`) |
| `openai_client.py` | Shared Azure OpenAI client (v1 endpoint — no api-version) |
| `database.py` | Plain SQL: a local SQLite file (default) or SQL Server |

## What each model gives you
| Model | Output |
|---|---|
| Layout | Text + general key-value pairs |
| Invoice / Receipt / ID Document | Named fields (e.g. Total, MerchantName), nested JSON for line items |
