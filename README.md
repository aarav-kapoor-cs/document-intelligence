# Document Intelligence Sample Project

Upload one or more PDFs or images, type a prompt, and get an AI answer for each file. The text is
read by Azure OCR, answered by Azure OpenAI, and every result is saved to a database.

- **Frontend** — Angular (http://localhost:4200)
- **Backend** — ASP.NET Core Web API, .NET 10 (http://localhost:5011)
- **OCR** — **Azure AI Document Intelligence** (`prebuilt-read`) reads the text from each file.
- **AI** — **Azure OpenAI** answers the prompt (or `mock` for a fake answer with no key).
- **Database** — plain SQL. A local **SQLite** file by default, or **Microsoft SQL Server**.

## Quick start (this laptop)
Two terminals:

**Backend**
```bash
cd backend
dotnet run       # http://localhost:5011
```
**Frontend**
```bash
npm install      # first time only
npm start        # http://localhost:4200
```
Open http://localhost:4200 → **Document Intelligence** → choose a PDF or image → type a prompt →
**Get AI response**.

## Settings — `backend/.env` (git-ignored)
All secrets live in one private file, `backend/.env` (never committed). Create it in the `backend`
folder and fill in your values:
```
Azure__Endpoint=...                   # Azure OpenAI
Azure__ApiKey=...
Azure__Deployment=...
DocumentIntelligence__Endpoint=...    # Azure OCR (Document Intelligence)
DocumentIntelligence__ApiKey=...
ConnectionStrings__SqlServer=...      # Microsoft SQL Server
Ai__Provider=azure
UseSqlServer=true
```
`appsettings.json` only holds safe defaults (mock AI + local SQLite), so the app still starts
before you add any keys. Full step-by-step: **[requirements/README.md](requirements/README.md)**.

## Where the code is
```
backend/
  Program.cs                       startup (database, CORS, port)
  Controllers/AnalyzeController.cs  POST /api/analyze, GET /api/analyses
  Services/OcrService.cs           reads file text via Azure Document Intelligence (OCR)
  Services/AiService.cs            calls mock / Azure OpenAI
  Data/Database.cs                 plain SQL: CREATE TABLE / INSERT / SELECT
  Models/                          request, result, and DB-row classes
src/app/app.ts, src/app/app.html   the whole Angular screen
```

## View the saved data
- **SQLite** (default): open `backend/documents.db` with the VS Code **"SQLite Viewer"** extension.
- **SQL Server**: use the VS Code **"SQL Server (mssql)"** extension → `SELECT * FROM Documents;`

## Office laptop (EY SQL Server) + syncing two laptops
See **[requirements/README.md](requirements/README.md)** — the install list, EY SQL Server setup,
and how to run it there. If `git` is blocked on the office laptop, that guide uses a **ZIP
download** from GitHub instead of `git clone` / `git pull`.
