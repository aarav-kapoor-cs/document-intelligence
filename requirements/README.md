# Requirements & Setup — EY office laptop

How to install, run, and view the data for this project on a fresh machine (e.g. the EY
office laptop), and how to keep it in sync with your personal laptop through GitHub.

---

## 1. Install these once
| Software | What for | Where |
|---|---|---|
| **.NET 10 SDK** | Runs the backend API | https://dotnet.microsoft.com/download |
| **Node.js (LTS)** | Runs the Angular frontend | https://nodejs.org |
| **Git** | Get the code / sync changes | https://git-scm.com/downloads |
| **VS Code** | Editor | https://code.visualstudio.com |
| **VS Code extension: "SQL Server (mssql)"** (by Microsoft) | View the SQL Server data | VS Code → Extensions → search "SQL Server (mssql)" |

> You do **not** hand-install the project's libraries. `npm install` (frontend) and
> `dotnet restore` (runs automatically on `dotnet run`) download everything from
> `package.json` and the `.csproj`.

## 2. Get the code
```bash
git clone <your-repo-url>
cd sample
```

## 3. Set up your secrets (per machine, never committed)
```bash
cp backend/.env.example backend/.env
```
Open `backend/.env` and fill in **your EY values**:
```
Ai__Provider=azure
UseSqlServer=true
ConnectionStrings__SqlServer=Server=<EY-SQL-SERVER>;Database=DocIntelligenceDb;User Id=<user>;Password=<password>;TrustServerCertificate=True;
Azure__Endpoint=https://<your-resource>.openai.azure.com
Azure__ApiKey=<your-azure-key>
Azure__Deployment=<your-deployment-name>
Azure__ApiVersion=2024-10-21
```
`backend/.env` is git-ignored — it stays on this laptop and is never pushed.

## 4. Run it (two terminals)
**Backend:**
```bash
cd backend
dotnet run          # wait for: Now listening on: http://localhost:5011
```
**Frontend:**
```bash
npm install         # first time only
npm start           # then open http://localhost:4200
```
Open http://localhost:4200 → Document Intelligence → choose a PDF → prompt → **Get AI response**.

## 5. View the data in SQL Server (VS Code mssql extension)
1. Click the **SQL Server** icon in the VS Code sidebar → **Add Connection**.
2. Server = your EY server, SQL Login, your username/password, Database = `DocIntelligenceDb`,
   Trust server certificate = **Yes**.
3. Expand **DocIntelligenceDb → Tables → dbo.Documents** → right-click → **Select Top 1000**,
   or run `SELECT * FROM Documents;`.

## 6. If your EY SQL login can't create tables
The app tries to create the `Documents` table automatically on startup. If your login lacks
permission, ask an admin to run this once (in SSMS or the mssql extension), then run the app:
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

## Keeping both laptops in sync (GitHub)
- **Personal laptop** (make changes): `git add -A && git commit -m "..." && git push`
- **Office laptop** (get the changes): `git pull`  ← changes appear *after* you pull, not live.
- Each laptop keeps its **own** `backend/.env` (personal = local Docker SQL Server,
  office = EY SQL Server). Secrets are never pushed.

> ⚠️ Confirm with your EY mentor that pushing internship code to a personal GitHub is allowed,
> and keep the repository **private**.
