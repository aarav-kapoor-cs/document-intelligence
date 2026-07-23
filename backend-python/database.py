import os
import json
import sqlite3
from datetime import datetime

# A small database helper. By default it saves to a SQLite file (documents.db)
# next to this file, using plain SQL. If USE_SQL_SERVER=true in the .env, it
# uses Microsoft SQL Server instead - there every INSERT and SELECT lives in a
# stored procedure (created at startup), and Python calls them with EXEC.

# Keep the SQLite file next to this file, no matter where the app is started from.
DB_PATH = os.path.join(os.path.dirname(__file__), "documents.db")


def _use_sql_server():
    return os.getenv("USE_SQL_SERVER", "false").lower() == "true"


def _open():
    # Open the right kind of database connection.
    if _use_sql_server():
        import pyodbc  # only needed (and imported) when using SQL Server
        return pyodbc.connect(os.getenv("SQL_CONNECTION_STRING", ""))
    return sqlite3.connect(DB_PATH)


def ensure_table():
    # Make the table the first time. Does nothing if it already exists.
    conn = _open()
    cursor = conn.cursor()
    if _use_sql_server():
        cursor.execute(
            "IF OBJECT_ID('Documents', 'U') IS NULL "
            "CREATE TABLE Documents ("
            "  Id INT IDENTITY(1,1) PRIMARY KEY,"
            "  FileName NVARCHAR(400),"
            "  Model NVARCHAR(100),"
            "  Prompt NVARCHAR(MAX),"
            "  DocumentText NVARCHAR(MAX),"
            "  KeyValuesJson NVARCHAR(MAX),"
            "  Answer NVARCHAR(MAX),"
            "  PromptTokens INT,"
            "  CompletionTokens INT,"
            "  TotalTokens INT,"
            "  CreatedAt NVARCHAR(40),"
            "  PromptId NVARCHAR(40)"
            ")"
        )
        # PromptId groups rows saved by the same analyze request (one prompt =
        # one conversation). Databases created before the column existed get
        # it added here once; new databases already have it from CREATE TABLE.
        cursor.execute(
            "IF COL_LENGTH('Documents', 'PromptId') IS NULL "
            "ALTER TABLE Documents ADD PromptId NVARCHAR(40)"
        )
        # A view is a saved SELECT with a name. The Excel export reads through
        # it, so it only ever sees the columns listed here.
        # CREATE OR ALTER = make it, or replace it if it already exists, so
        # running this at every startup is safe. It must be alone in its own
        # execute() call - SQL Server wants it alone in a batch.
        cursor.execute(
            "CREATE OR ALTER VIEW DocumentsExport AS "
            "SELECT Id, FileName, Model, KeyValuesJson, CreatedAt FROM Documents"
        )
        # Stored procedure for the Excel export: filters the view by a date
        # window and the document type. The dates are real DATETIME2 values,
        # not text: TRY_CAST turns the stored "YYYY-MM-DD HH:MM:SS" text into
        # a date for a proper date comparison (a malformed row becomes NULL
        # and is skipped, instead of crashing the whole export).
        cursor.execute(
            "CREATE OR ALTER PROCEDURE GetDocumentsFiltered "
            "  @FromDate DATETIME2, @ToDate DATETIME2, @Model NVARCHAR(100) "
            "AS BEGIN "
            "  SET NOCOUNT ON; "
            "  SELECT Id, FileName, Model, KeyValuesJson, CreatedAt "
            "  FROM DocumentsExport "
            "  WHERE TRY_CAST(CreatedAt AS DATETIME2) BETWEEN @FromDate AND @ToDate "
            "    AND Model = @Model "
            "  ORDER BY Id "
            "END"
        )
        # Stored procedure for saving: the INSERT lives inside SQL Server.
        cursor.execute(
            "CREATE OR ALTER PROCEDURE SaveDocument "
            "  @FileName NVARCHAR(400), @Model NVARCHAR(100), @Prompt NVARCHAR(MAX), "
            "  @DocumentText NVARCHAR(MAX), @KeyValuesJson NVARCHAR(MAX), @Answer NVARCHAR(MAX), "
            "  @PromptTokens INT, @CompletionTokens INT, @TotalTokens INT, "
            "  @CreatedAt NVARCHAR(40), @PromptId NVARCHAR(40) "
            "AS BEGIN "
            "  SET NOCOUNT ON; "
            "  INSERT INTO Documents "
            "  (FileName, Model, Prompt, DocumentText, KeyValuesJson, Answer, "
            "   PromptTokens, CompletionTokens, TotalTokens, CreatedAt, PromptId) "
            "  VALUES (@FileName, @Model, @Prompt, @DocumentText, @KeyValuesJson, @Answer, "
            "          @PromptTokens, @CompletionTokens, @TotalTokens, @CreatedAt, @PromptId) "
            "END"
        )
        # Stored procedure for the history page: the SELECT lives inside
        # SQL Server too.
        cursor.execute(
            "CREATE OR ALTER PROCEDURE GetDocuments "
            "AS BEGIN "
            "  SET NOCOUNT ON; "
            "  SELECT Id, FileName, Model, Prompt, DocumentText, KeyValuesJson, Answer, "
            "         PromptTokens, CompletionTokens, TotalTokens, CreatedAt, PromptId "
            "  FROM Documents ORDER BY Id DESC "
            "END"
        )
    else:
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS Documents ("
            "  Id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  FileName TEXT,"
            "  Model TEXT,"
            "  Prompt TEXT,"
            "  DocumentText TEXT,"
            "  KeyValuesJson TEXT,"
            "  Answer TEXT,"
            "  PromptTokens INTEGER,"
            "  CompletionTokens INTEGER,"
            "  TotalTokens INTEGER,"
            "  CreatedAt TEXT,"
            "  PromptId TEXT"
            ")"
        )
        # Same PromptId catch-up as on SQL Server: an older documents.db file
        # was created without the column, so add it once if it is missing.
        columns = [row[1] for row in cursor.execute("PRAGMA table_info(Documents)")]
        if "PromptId" not in columns:
            cursor.execute("ALTER TABLE Documents ADD COLUMN PromptId TEXT")
        # SQLite supports views too (but not stored procedures). Drop and
        # recreate mirrors what CREATE OR ALTER does on SQL Server.
        cursor.execute("DROP VIEW IF EXISTS DocumentsExport")
        cursor.execute(
            "CREATE VIEW DocumentsExport AS "
            "SELECT Id, FileName, Model, KeyValuesJson, CreatedAt FROM Documents"
        )
    conn.commit()
    conn.close()


# INSERT one row. On SQL Server this calls the SaveDocument stored procedure;
# SQLite has no procedures, so there it stays a plain INSERT. Both use "?"
# for parameters, and the value tuple is identical in both branches.
# prompt_id is the same for every file saved by one analyze request.
def save(file_name, model, prompt, document_text, key_values_json, answer,
         prompt_tokens, completion_tokens, total_tokens, created_at, prompt_id):
    conn = _open()
    cursor = conn.cursor()
    if _use_sql_server():
        cursor.execute(
            "EXEC SaveDocument ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?",
            (file_name, model, prompt, document_text, key_values_json, answer,
             prompt_tokens, completion_tokens, total_tokens, created_at, prompt_id),
        )
    else:
        cursor.execute(
            "INSERT INTO Documents "
            "(FileName, Model, Prompt, DocumentText, KeyValuesJson, Answer, "
            " PromptTokens, CompletionTokens, TotalTokens, CreatedAt, PromptId) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (file_name, model, prompt, document_text, key_values_json, answer,
             prompt_tokens, completion_tokens, total_tokens, created_at, prompt_id),
        )
    conn.commit()
    conn.close()


# SELECT the rows for the Excel export: a date window plus one document type.
# On SQL Server this calls the GetDocumentsFiltered stored procedure; SQLite
# has no procedures, so there it is a plain SELECT on the DocumentsExport view.
def get_filtered(from_date, to_date, model):
    # CreatedAt is stored as "YYYY-MM-DD HH:MM:SS" (UTC). Adding the first and
    # last second of the day makes both edge days fully included.
    conn = _open()
    cursor = conn.cursor()
    if _use_sql_server():
        # The procedure takes DATETIME2 parameters, so send real datetime
        # values - pyodbc passes them as proper dates, not text.
        from_dt = datetime.strptime(from_date + " 00:00:00", "%Y-%m-%d %H:%M:%S")
        to_dt = datetime.strptime(to_date + " 23:59:59", "%Y-%m-%d %H:%M:%S")
        cursor.execute("EXEC GetDocumentsFiltered ?, ?, ?", (from_dt, to_dt, model))
    else:
        cursor.execute(
            "SELECT Id, FileName, Model, KeyValuesJson, CreatedAt "
            "FROM DocumentsExport "
            "WHERE CreatedAt >= ? AND CreatedAt <= ? AND Model = ? "
            "ORDER BY Id",
            (from_date + " 00:00:00", to_date + " 23:59:59", model),
        )
    rows = cursor.fetchall()
    conn.close()

    records = []
    for row in rows:
        records.append({
            "id": row[0],
            "fileName": row[1],
            "model": row[2],
            "fields": json.loads(row[3] or "{}"),
            "createdAt": row[4],
        })
    return records


# SELECT every row, newest first. On SQL Server this calls the GetDocuments
# stored procedure; on SQLite it is a plain SELECT. Both return the columns
# in the same order, so the loop below works for either.
def get_all():
    conn = _open()
    cursor = conn.cursor()
    if _use_sql_server():
        cursor.execute("EXEC GetDocuments")
    else:
        cursor.execute(
            "SELECT Id, FileName, Model, Prompt, DocumentText, KeyValuesJson, Answer, "
            "PromptTokens, CompletionTokens, TotalTokens, CreatedAt, PromptId "
            "FROM Documents ORDER BY Id DESC"
        )
    rows = cursor.fetchall()
    conn.close()

    records = []
    for row in rows:
        records.append({
            "id": row[0],
            "fileName": row[1],
            "model": row[2],
            "prompt": row[3],
            "documentText": row[4],
            "fields": json.loads(row[5] or "{}"),
            "answer": row[6],
            "promptTokens": row[7],
            "completionTokens": row[8],
            "totalTokens": row[9],
            "createdAt": row[10],
            "promptId": row[11],
        })
    return records
