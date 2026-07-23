# Stored procedures on SQL Server — what is implemented and why

On SQL Server (the office laptop, `USE_SQL_SERVER=true`), **every database
call goes through a stored procedure** that lives inside the database.
`database.py` creates all three at startup and calls them by name with `EXEC`:

| Procedure | Who calls it | What it does |
|---|---|---|
| `SaveDocument` | `save()` after each analysis | The INSERT of one result row |
| `GetDocuments` | `get_all()` for the history page | SELECT every row, newest first |
| `GetDocumentsFiltered` | `get_filtered()` for the Excel export | SELECT through the `DocumentsExport` view, filtered by a date window + document type |

**SQLite keeps its plain SQL.** SQLite is a file, not a server, and has no
stored procedures. Every SQL Server-only statement sits in the
`if _use_sql_server():` branches, and the SQLite branches are untouched — the
Mac behaves exactly as before.

## The three rules the code follows (pyodbc + T-SQL)

1. `CREATE OR ALTER PROCEDURE` is **alone in its own `cursor.execute()`**
   call. SQL Server requires it to be the only statement in a batch, and each
   `execute()` is one batch.
2. No `GO` in Python code. `GO` is not SQL — it's a separator only SSMS
   understands. In Python, a new `cursor.execute()` *is* the "GO".
3. `SET NOCOUNT ON;` at the top of each procedure. It switches off the
   "(3 rows affected)" chatter that can confuse pyodbc when it reads results.

`CREATE OR ALTER` means: create it the first time, replace it if it already
exists — safe to run at every startup, same idea as the existing
`IF OBJECT_ID('Documents', 'U') IS NULL CREATE TABLE ...` line.

## Why `GetDocumentsFiltered` takes DATETIME2 parameters

`CreatedAt` is stored as text (`NVARCHAR(40)`, "YYYY-MM-DD HH:MM:SS" in UTC).
The old version compared that text against text parameters — it only worked
because of the fixed format. The procedure now does a **real date
comparison**:

- The parameters are `@FromDate DATETIME2, @ToDate DATETIME2`, and Python
  passes real `datetime` values (pyodbc sends them as dates, not strings).
- The column is converted with `TRY_CAST(CreatedAt AS DATETIME2)`. `TRY_CAST`
  returns NULL for a value it cannot convert, so one malformed row is simply
  skipped instead of crashing the whole export. The ISO "YYYY-MM-DD HH:MM:SS"
  format converts to DATETIME2 the same way on every SQL Server language
  setting, so the result never depends on the laptop's locale.
- The table itself is unchanged — rows already saved on the office laptop
  keep working.

## Checking it on the office laptop

1. Start the backend with `USE_SQL_SERVER=true` in the `.env`.
2. Watch the startup log. No warning means the table, the view, and all three
   procedures were created.
3. Analyze one document, then check in SSMS:

```sql
-- Are the procedures there?
SELECT name, create_date FROM sys.procedures ORDER BY name;
-- expect: GetDocuments, GetDocumentsFiltered, SaveDocument

-- Call them directly - the same thing Python does:
EXEC GetDocuments;
EXEC GetDocumentsFiltered '2026-07-01', '2026-07-31 23:59:59', 'prebuilt-invoice';
```

**If startup prints a permissions error** (corporate accounts sometimes lack
`CREATE PROCEDURE` rights): the API keeps running — `main.py` treats database
problems as non-fatal — but saving/reading will fail until the procedures
exist. The fallback is to copy the three `CREATE OR ALTER PROCEDURE ...`
statements out of `ensure_table()` in `database.py` and run them once in SSMS
(add line breaks freely; SQL doesn't care), or ask the DBA to run them.
