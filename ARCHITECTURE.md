# Architecture — Questions & Answers

A short Q&A explaining how this project is built. Each answer points to the real file.

---

### 1. What does this project do?
You upload one or more PDFs or images and type a prompt. The app reads the text out of each file
(OCR), asks an AI your prompt about that text, shows the answer, and saves everything to a
database.

### 2. What is the overall architecture?
Three parts that talk over HTTP:

```
Browser (Angular)  ──►  .NET Web API  ──►  Azure OCR + Azure OpenAI
   localhost:4200         localhost:5011          │
        ▲                       │                 ▼
        └───────  JSON  ────────┴────────►  Database (SQLite file or SQL Server)
```

The **frontend** is a web page, the **backend** is the server that does the work, and the
**database** stores the results.

### 3. What are the main technologies?
- **Frontend:** Angular + TypeScript
- **Backend:** ASP.NET Core Web API in C# (.NET 10)
- **OCR:** Azure AI Document Intelligence (`prebuilt-read`)
- **AI:** Azure OpenAI
- **Database:** SQLite (a local file) by default, or Microsoft SQL Server

### 4. How does the frontend call the backend?
The Angular app uses `HttpClient` to send a POST request to `http://localhost:5011/api/analyze`
with JSON `{ prompt, files: [{ name, base64 }] }`. `HttpClient` is turned on by
`provideHttpClient()` in [app.config.ts](src/app/app.config.ts), and the browser is allowed to call
the backend by the **CORS** policy set in [Program.cs](backend/Program.cs).

### 5. What are the API routes, and how does routing work?
The backend uses **attribute routing**. On [AnalyzeController.cs](backend/Controllers/AnalyzeController.cs)
the class has `[Route("api")]` and the methods have `[HttpPost("analyze")]` and
`[HttpGet("analyses")]`, which creates two URLs:
- `POST /api/analyze` — analyze files and save them
- `GET /api/analyses` — return everything saved

`app.MapControllers()` in Program.cs wires these routes up.

### 6. What happens step-by-step when I click "Get AI response"?
1. `chooseFiles()` reads each file and turns it into base64 text — [app.ts](src/app/app.ts)
2. `getAnswer()` POSTs `{ prompt, files }` to `/api/analyze` — [app.ts](src/app/app.ts)
3. `AnalyzeController.Analyze` loops over each file — [AnalyzeController.cs](backend/Controllers/AnalyzeController.cs)
4. `OcrService.ReadTextAsync` reads the text with Azure OCR — [OcrService.cs](backend/Services/OcrService.cs)
5. `AiService.AnalyzeAsync` asks the AI the prompt about that text — [AiService.cs](backend/Services/AiService.cs)
6. `Database.Save` inserts a row — [Database.cs](backend/Data/Database.cs)
7. The backend returns JSON `{ results }`, and the page shows one card per file — [app.html](src/app/app.html)

### 7. Why is the file sent as base64?
JSON can only hold text, not raw file bytes. Base64 turns the file's bytes into text so it fits in
the JSON body. The backend turns it back into bytes with `Convert.FromBase64String` before sending
it to OCR (`toBase64` in [app.ts](src/app/app.ts); decode in the controller).

### 8. How does OCR (reading the text) work?
`OcrService` sends the file to Azure Document Intelligence's `prebuilt-read` model with the header
`Ocp-Apim-Subscription-Key`. Azure doesn't answer instantly — it returns an `Operation-Location`
link, so the code **polls** that link once a second until the status is `succeeded`, then reads all
the text from `analyzeResult.content`. See [OcrService.cs](backend/Services/OcrService.cs).

### 9. How does the AI produce the answer?
`AiService` POSTs to `{endpoint}/openai/deployments/{deployment}/chat/completions` with the
`api-key` header and two messages: your **prompt** as the `system` instruction and the
**document text** as the `user` message. It reads the reply from `choices[0].message.content`.
There is also a `mock` mode that returns a fake answer with no key. See
[AiService.cs](backend/Services/AiService.cs).

### 10. How does the database connect?
[Database.cs](backend/Data/Database.cs) uses plain **ADO.NET**. It opens either a
`SqliteConnection` (`Data Source=documents.db`, a local file) or a `SqlConnection` (using the
`ConnectionStrings:SqlServer` value), chosen by the `UseSqlServer` setting. It's registered once as
a **singleton** in Program.cs, and `EnsureTable()` runs at startup to create the table if needed.

### 11. What SQL does it run?
Just the fundamentals, hand-written:
- `EnsureTable()` → `CREATE TABLE Documents (...)`
- `Save()` → `INSERT INTO Documents (...) VALUES (@...)`
- `GetAll()` → `SELECT ... FROM Documents ORDER BY Id DESC`

The INSERT uses **parameters** (`@fileName`, etc.) instead of gluing strings together, which
protects against SQL injection.

### 12. Where are the keys and passwords kept?
In a git-ignored file, `backend/.env` (never pushed to GitHub). It's loaded at startup by
**DotNetEnv** (`Env.Load()` in Program.cs). The `__` in a name maps to a `:` setting — e.g.
`Azure__ApiKey` becomes `Azure:ApiKey`. `appsettings.json` holds only safe defaults (mock AI +
local SQLite).

### 13. How do you switch between mock/Azure and SQLite/SQL Server?
All in `backend/.env`, with **no code change**:
- `Ai__Provider=mock` or `azure`
- Local file (default) vs SQL Server: add `UseSqlServer=true` and `ConnectionStrings__SqlServer=...`

### 14. How are errors handled?
The controller wraps each file in a `try/catch`. If OCR or the AI fails (for example a wrong key
returns **401**), the reason is put into the answer text, so it shows in the result card **and** is
saved — instead of crashing the whole request. See [AnalyzeController.cs](backend/Controllers/AnalyzeController.cs).

### 15. Where is each essential file?

**Backend** (`backend/`)
| File | Job |
|---|---|
| [Program.cs](backend/Program.cs) | Startup: load `.env`, port 5011, CORS, register services, create table |
| [Controllers/AnalyzeController.cs](backend/Controllers/AnalyzeController.cs) | The API routes: `POST /api/analyze`, `GET /api/analyses` |
| [Services/OcrService.cs](backend/Services/OcrService.cs) | Reads file text with Azure OCR |
| [Services/AiService.cs](backend/Services/AiService.cs) | Calls Azure OpenAI (or mock) |
| [Data/Database.cs](backend/Data/Database.cs) | Plain SQL: CREATE / INSERT / SELECT |
| [Models/](backend/Models/) | The request/result/row classes |
| [appsettings.json](backend/appsettings.json) | Safe defaults (secrets live in `.env`) |

**Frontend** (`src/`)
| File | Job |
|---|---|
| [main.ts](src/main.ts) | Starts the Angular app |
| [app/app.config.ts](src/app/app.config.ts) | Turns on `HttpClient` |
| [app/app.ts](src/app/app.ts) | The screen's logic: read files → base64 → call backend |
| [app/app.html](src/app/app.html) | The screen: dropdown, file picker, prompt, result cards |
| [styles.css](src/styles.css) | Basic styling |

### 16. What are the data shapes?
- **Request** ([AnalyzeRequest.cs](backend/Models/AnalyzeRequest.cs)): `Prompt` + `Files[]`, each `{ Name, Base64 }`
- **Result** ([AnalyzeResult.cs](backend/Models/AnalyzeResult.cs)): `{ FileName, Answer }`
- **Saved row** ([DocumentRecord.cs](backend/Models/DocumentRecord.cs)) / `Documents` table columns:
  `Id, FileName, Prompt, DocumentText, Answer, CreatedAt`
