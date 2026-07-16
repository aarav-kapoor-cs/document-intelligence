# Requirements & Setup — EY office laptop (ZIP download, no git)

How to get the code as a **ZIP** (no `git` needed), run it in **VS Code**, and confirm your
**Azure OCR**, **Azure OpenAI**, and **SQL Server** all work. Everything runs on your machine.

---

## 1. Install these once
| Software | What for | Where |
|---|---|---|
| **.NET 10 SDK** | Runs the backend API | https://dotnet.microsoft.com/download |
| **Node.js (LTS)** | Runs the Angular frontend | https://nodejs.org |
| **VS Code** | Editor + terminals | https://code.visualstudio.com |
| **VS Code extension: "SQL Server (mssql)"** (by Microsoft) | View the SQL Server data | VS Code → Extensions (Ctrl+Shift+X) → search **SQL Server (mssql)** |

> **No `git` needed on this laptop.** You are downloading a ZIP instead.
> You also do **not** hand-install the project's libraries — `npm install` (frontend) and
> `dotnet run` (backend, which runs `dotnet restore` automatically) download everything for you.

## 2. Get the code (Download ZIP)
1. Open the repo in a browser: **https://github.com/aarav-kapoor-cs/document-intelligence**
2. Click the green **Code** button → **Download ZIP**.
3. Unzip it. The folder is named **`document-intelligence-main`**.
4. In VS Code: **File → Open Folder…** → select **`document-intelligence-main`**.

> The ZIP contains **source code only** — no `node_modules`, no build output, and **no `.env`**.
> That's expected. The next steps create the `.env` and install the packages.

## 3. Add your secrets — create `backend/.env`
The app reads all your keys and passwords from one private file, `backend/.env`. It is **not** in
the ZIP (secrets are never shared), so you create it once on this laptop.

1. In the VS Code Explorer, right-click the **`backend`** folder → **New File** → name it exactly
   **`.env`** (just `.env` — nothing before the dot).
2. Paste this in, then fill in **your EY values** (the parts in `<...>`):
```
# Azure OpenAI — the AI that answers your prompt
Azure__Endpoint=https://<your-resource>.openai.azure.com
Azure__ApiKey=<your-azure-openai-key>
Azure__Deployment=<your-deployment-name>

# Azure Document Intelligence — the OCR that reads text from your files
DocumentIntelligence__Endpoint=https://<your-resource>.cognitiveservices.azure.com
DocumentIntelligence__ApiKey=<your-ocr-key>

# Microsoft SQL Server — where the answers are saved
ConnectionStrings__SqlServer=Server=<EY-SQL-SERVER>;Database=DocIntelligenceDb;User Id=<user>;Password=<password>;TrustServerCertificate=True;

# Leave these two as-is
Ai__Provider=azure
UseSqlServer=true
```
> That's **5 values** you paste in: Azure OpenAI (endpoint, key, deployment), Azure OCR
> (endpoint, key), plus the SQL Server connection string. The two api-versions are handled for you.
>
> `backend/.env` stays on this laptop and is **never committed or shared**. Keep the keys private.

## 4. Run it (two VS Code terminals)
Open a terminal in VS Code: **Terminal → New Terminal**. Then click the **split** icon (or the
**＋**) to open a second one, so the backend and frontend run side by side.

**Terminal 1 — backend:**
```bash
cd backend
dotnet run
```
Wait until you see: `Now listening on: http://localhost:5011`

**Terminal 2 — frontend:**
```bash
npm install      # first time only
npm start        # then open http://localhost:4200
```

## 5. Check everything is working ✅
Go through these four checks in order — they confirm your credentials are correct.

| # | Check | What proves it works |
|---|---|---|
| 1 | **Backend is up** | Terminal 1 shows `Now listening on: http://localhost:5011` with **no** red SQL error. → Your **SQL Server** connection string is correct and the `Documents` table was created. |
| 2 | **Frontend is up** | http://localhost:4200 shows the **"Document Intelligence Sample Project"** page. |
| 3 | **OCR + AI work** | On the page: choose a PDF **or image** → type a prompt → click **Get AI response** → an answer that is actually *about your document* appears. → Azure **OCR** read the text **and** Azure **OpenAI** answered. |
| 4 | **Data is saved** | In the mssql extension run `SELECT * FROM Documents;` → you see a new row (FileName, Prompt, Answer, CreatedAt). → **SQL Server storage** works end to end. |

**If something fails** (the backend terminal, Terminal 1, prints the exact error):

| Symptom | Likely cause / fix |
|---|---|
| Answer shows a **401 / "invalid key"** error | Wrong Azure key in `backend/.env` — fix `Azure__ApiKey` (OpenAI) or `DocumentIntelligence__ApiKey` (OCR). Terminal 1 shows which call failed. |
| Answer **ignores the document** / says it can't see any text | OCR read nothing. Recheck `DocumentIntelligence__Endpoint` and `DocumentIntelligence__ApiKey`, and that the file really has text or a clear image. |
| Backend **crashes on startup** with a SQL error | Wrong connection string, wrong password, or the EY server isn't reachable from this laptop. Recheck `ConnectionStrings__SqlServer`. |
| Page says **"Could not reach the backend"** | Terminal 1 (backend) isn't running. Start it, wait for the "Now listening" line, then retry. |
| Backend error says it **can't create the table** | Your SQL login lacks permission — see section 7 to create the table once. |
| Changed `.env` but nothing changed | Stop the backend (Ctrl+C in Terminal 1) and run `dotnet run` again — `.env` is read at startup. |

## 6. View the data in SQL Server (VS Code mssql extension)
1. Click the **SQL Server** icon in the VS Code sidebar → **Add Connection**.
2. Server = your **EY server**, authentication = **SQL Login**, your username/password,
   Database = `DocIntelligenceDb`, **Trust server certificate = Yes**.
3. Expand **DocIntelligenceDb → Tables → dbo.Documents** → right-click → **Select Top 1000**,
   or run `SELECT * FROM Documents;`.

## 7. If your EY SQL login can't create tables
The app tries to create the `Documents` table automatically on startup. If your login lacks
permission, ask an admin to run this once (in the mssql extension or SSMS), then run the app:
```sql
CREATE TABLE Documents (
  Id INT IDENTITY(1,1) PRIMARY KEY,
  FileName NVARCHAR(400),
  Prompt NVARCHAR(MAX),
  DocumentText NVARCHAR(MAX),
  Answer NVARCHAR(MAX),
  CreatedAt NVARCHAR(40)
);
```

---

## Getting updates later (without git)
When you change the code on your **personal laptop** and push it to GitHub, get it here by simply
**re-downloading the ZIP**:

1. Download the ZIP again (section 2) and unzip it.
2. Copy the new files over your old folder — **but keep your existing `backend/.env`** (don't
   overwrite it; it holds your EY secrets).
3. If `package.json` changed, run `npm install` again. Then run as usual (section 4).

> Work in one direction: **edit and `git push` on the personal laptop**; the **office laptop just
> re-downloads the ZIP**. That avoids needing git here at all.

> ⚠️ Confirm with your EY mentor that using a personal private GitHub for internship code is
> allowed. The repository is **private**.
