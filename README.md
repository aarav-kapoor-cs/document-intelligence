# Document Intelligence Sample Project

Upload one or more PDFs, type a prompt, and get an AI answer for each file. Every result is
saved to a database.

- **Frontend** — Angular (http://localhost:4200)
- **Backend** — ASP.NET Core Web API, .NET 10 (http://localhost:5011)
- **Database** — plain SQL. A local **SQLite** file by default, or **Microsoft SQL Server**.
- **AI** — choose `mock` (no key), `openai`, or `azure`.

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
Open http://localhost:4200 → **Document Intelligence** → choose a PDF → type a prompt →
**Get AI response**.

## Settings — `backend/.env` (git-ignored)
Secrets and per-machine settings live in `backend/.env`. Copy the template and fill it in:
```bash
cp backend/.env.example backend/.env
```
It controls:
- `Ai__Provider` — `mock` | `openai` | `azure`
- `UseSqlServer` — `true` for SQL Server (also set `ConnectionStrings__SqlServer`), `false` for a local SQLite file
- the OpenAI / Azure keys

`appsettings.json` only holds safe defaults (mock + SQLite), so a fresh clone runs with no keys.

## Where the code is
```
backend/
  Program.cs                       startup (database, CORS, port)
  Controllers/AnalyzeController.cs  POST /api/analyze, GET /api/analyses
  Services/AiService.cs            calls mock / OpenAI / Azure
  Data/Database.cs                 plain SQL: CREATE TABLE / INSERT / SELECT
  Models/                          request, result, and DB-row classes
src/app/app.ts, src/app/app.html   the whole Angular screen
```

## View the saved data
- **SQLite** (default): open `backend/documents.db` with the VS Code **"SQLite Viewer"** extension.
- **SQL Server**: use the VS Code **"SQL Server (mssql)"** extension → `SELECT * FROM Documents;`

## Office laptop (EY SQL Server) + syncing two laptops via GitHub
See **[requirements/README.md](requirements/README.md)** — the install list, EY SQL Server setup,
and the personal↔office `git push` / `git pull` workflow.
