# Requirements & Setup — EY office laptop (ZIP download, no git)

How to get the code as a **ZIP** (no `git` needed), run it in **VS Code**, and confirm your
**Azure OCR**, **Azure OpenAI**, and **SQL Server** all work. Everything runs on your machine.

The app has two parts you run: the **Python backend** (FastAPI, port 8000) and the **Angular
frontend** (port 4200).

---

## 1. Install these once
| Software | What for | Where |
|---|---|---|
| **Python 3.10 or newer** | Runs the backend API | https://www.python.org/downloads (tick **"Add python.exe to PATH"** in the installer) |
| **Node.js (LTS)** | Runs the Angular frontend | https://nodejs.org |
| **VS Code** | Editor + terminals | https://code.visualstudio.com |
| **ODBC Driver 18 for SQL Server** | Lets Python talk to SQL Server | https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server (a small, separate download from SSMS) |
| **VS Code extension: "SQL Server (mssql)"** (by Microsoft) | View the SQL Server data | VS Code → Extensions (Ctrl+Shift+X) → search **SQL Server (mssql)** |

> **No `git` needed on this laptop.** You are downloading a ZIP instead.
> You also do **not** hand-install the project's libraries — `pip install -r requirements.txt`
> (backend) and `npm install` (frontend) download everything for you.

## 2. Get the code (Download ZIP)
1. Open the repo in a browser: **https://github.com/aarav-kapoor-cs/document-intelligence**
2. Click the green **Code** button → **Download ZIP**.
3. Unzip it. The folder is named **`document-intelligence-main`**.
4. In VS Code: **File → Open Folder…** → select **`document-intelligence-main`**.

> The ZIP contains **source code only** — no `node_modules`, no `.venv`, and **no `.env`**.
> That's expected. The next steps create the `.env` and install the packages.

## 3. Set up SQL Server (once)
You said SQL Server / SSMS is already working on this laptop. Do these two things once so the app
can use it:
1. Make sure the **ODBC Driver 18 for SQL Server** is installed (from section 1). This is what
   Python uses to connect — SSMS uses its own driver, so having SSMS is not enough.
2. In **SQL Server Management Studio**, create the database once (the app creates the *table* for
   you, but not the *database*):
   ```sql
   CREATE DATABASE DocIntelligenceDb;
   ```

## 4. Add your secrets — create `backend-python/.env`
The app reads all your Azure keys from one private file, `backend-python/.env`. It is **not** in the
ZIP (secrets are never shared), so you create it once by copying the template that *is* in the ZIP.

1. In the VS Code Explorer, open the **`backend-python`** folder. You will see a file named
   **`.env.example`** — this is the template.
2. Copy it to a new file named exactly **`.env`** (in the same folder). Easiest way: open a terminal
   (**Terminal → New Terminal**) and run:
   ```bash
   cd backend-python
   copy .env.example .env
   ```
3. Open the new **`.env`** and fill in **your Azure values** (the parts in `<...>`):
   ```
   AZURE_OPENAI_ENDPOINT=https://<your-resource>.openai.azure.com
   AZURE_OPENAI_KEY=<your-azure-openai-key>
   AZURE_OPENAI_DEPLOYMENT=<your-deployment-name>
   DOC_INTELLIGENCE_ENDPOINT=https://<your-resource>.cognitiveservices.azure.com
   DOC_INTELLIGENCE_KEY=<your-ocr-key>
   ```
   > That's the **5 Azure values**: OpenAI (endpoint, key, deployment) and OCR (endpoint, key).
   > The api-versions are handled for you.
   >
   > **`AZURE_OPENAI_DEPLOYMENT` is NOT a secret key.** It's the *name* of your model in Azure
   > OpenAI. Find it in the Azure portal → your Azure OpenAI resource → **Model deployments** (or
   > Azure AI Foundry → **Deployments**) → the **Deployment name** column (e.g. `gpt-4o-mini`).
4. **SQL Server is already set up in the template** — these lines are already there:
   ```
   USE_SQL_SERVER=true
   SQL_CONNECTION_STRING=Driver={ODBC Driver 18 for SQL Server};Server=localhost;Database=DocIntelligenceDb;Trusted_Connection=yes;TrustServerCertificate=yes;
   ```
   - `Server=localhost` + `Trusted_Connection=yes` = "the SQL Server on this laptop, using my
     Windows login" (what SSMS connects to by default).
   - If your instance has a name, change it to e.g. `Server=localhost\SQLEXPRESS`.
   - To use a SQL **username/password** instead of your Windows login, use the alternative
     `SQL_CONNECTION_STRING` line shown (commented) in `.env.example`.

> `backend-python/.env` stays on this laptop and is **never committed or shared**. Keep the keys
> private.

## 5. Run it (two VS Code terminals)
Open a terminal in VS Code: **Terminal → New Terminal**. Then click the **split** icon (or the
**＋**) to open a second one, so the backend and frontend run side by side.

**Terminal 1 — backend:**
```bash
cd backend-python
python -m venv .venv
.venv\Scripts\activate          # you should see (.venv) at the start of the line
pip install -r requirements.txt
uvicorn main:app --port 8000
```
Wait until you see: `Uvicorn running on http://127.0.0.1:8000`

**Terminal 2 — frontend:**
```bash
npm install      # first time only
npm start        # then open http://localhost:4200
```

> After the first time, starting the backend is just: `cd backend-python`, `.venv\Scripts\activate`,
> `uvicorn main:app --port 8000` (no need to re-create the venv or re-install).

## 6. Check everything is working ✅
Go through these four checks in order — they confirm your credentials are correct.

| # | Check | What proves it works |
|---|---|---|
| 1 | **Backend is up** | Terminal 1 shows `Uvicorn running on http://127.0.0.1:8000` with **no** red error. (If it prints `WARNING: could not prepare the database...`, your SQL Server settings are off — see the table below.) |
| 2 | **Frontend is up** | http://localhost:4200 shows the page with the model dropdown and file picker. |
| 3 | **OCR + AI work** | On the page: choose a PDF **or image** → type a prompt → click **Get AI response** → an answer that is actually *about your document* appears, with key-value pairs and token counts. |
| 4 | **Data is saved to SQL Server** | In the mssql extension (or SSMS), run `SELECT * FROM Documents;` on `DocIntelligenceDb` → you see a new row (FileName, Model, Prompt, Answer, tokens, CreatedAt). |

**If something fails,** the answer text on the page (and Terminal 1) shows the exact reason:

| Symptom | Likely cause / fix |
|---|---|
| Answer shows a **401 / "invalid key"** | Wrong Azure key in `backend-python/.env` — fix `AZURE_OPENAI_KEY` (OpenAI) or `DOC_INTELLIGENCE_KEY` (OCR). |
| Answer says **`ERROR saving to the database: ...`** | The app reached Azure but couldn't save. Common causes: the `DocIntelligenceDb` database doesn't exist (section 3), the **ODBC Driver 18** isn't installed, or the `SQL_CONNECTION_STRING` server name is wrong. |
| Terminal 1 prints **`WARNING: could not prepare the database at startup`** | Same as above — the app still starts, but fix the SQL settings so saving works. |
| Answer **ignores the document** | OCR read nothing. Recheck `DOC_INTELLIGENCE_ENDPOINT` / `DOC_INTELLIGENCE_KEY`, and that the file has text or a clear image. |
| Page says **"Could not reach the backend"** | Terminal 1 (backend) isn't running. Start it, wait for the "Uvicorn running" line, then retry. |
| `'uvicorn' is not recognized` | The venv isn't active. Run `.venv\Scripts\activate` first (you should see `(.venv)`), then the `uvicorn` command. |
| Changed `.env` but nothing changed | Stop the backend (Ctrl+C in Terminal 1) and start `uvicorn` again — `.env` is read at startup. |

## 7. If your SQL login can't create the table
The app tries to create the `Documents` table automatically on startup. If your login lacks
permission, ask an admin to run this once (in the mssql extension or SSMS on `DocIntelligenceDb`),
then run the app:
```sql
CREATE TABLE Documents (
  Id INT IDENTITY(1,1) PRIMARY KEY,
  FileName NVARCHAR(400),
  Model NVARCHAR(100),
  Prompt NVARCHAR(MAX),
  DocumentText NVARCHAR(MAX),
  KeyValuesJson NVARCHAR(MAX),
  Answer NVARCHAR(MAX),
  PromptTokens INT,
  CompletionTokens INT,
  TotalTokens INT,
  CreatedAt NVARCHAR(40)
);
```

---

## Getting updates later (without git)
When you change the code on your **personal laptop** and push it to GitHub, get it here by simply
**re-downloading the ZIP**:

1. Download the ZIP again (section 2) and unzip it.
2. Copy the new files over your old folder — **but keep your existing `backend-python/.env`** (don't
   overwrite it; it holds your Azure keys and SQL settings).
3. If `requirements.txt` changed, re-run `pip install -r requirements.txt` (with the venv active).
   If `package.json` changed, run `npm install` again. Then run as usual (section 5).

> Work in one direction: **edit and `git push` on the personal laptop**; the **office laptop just
> re-downloads the ZIP**. That avoids needing git here at all.

> ⚠️ Confirm with your EY mentor that using a personal private GitHub for internship code is
> allowed. The repository is **private**.
