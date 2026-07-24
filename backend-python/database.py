import os
import json
import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# A small database helper. By default it saves to a SQLite file (documents.db)
# next to this file, using plain SQL. If USE_SQL_SERVER=true in the .env, it
# uses Microsoft SQL Server instead - there every INSERT and SELECT lives in a
# stored procedure (created at startup), and Python calls them with EXEC.

# Keep the SQLite file next to this file, no matter where the app is started from.
DB_PATH = os.path.join(os.path.dirname(__file__), "documents.db")

# CreatedAt is stored in UTC, but users pick report dates in local time.
LOCAL_TZ = ZoneInfo("Asia/Kolkata")


def _use_sql_server():
    return os.getenv("USE_SQL_SERVER", "false").lower() == "true"


def _open(autocommit=False):
    # Open the right kind of database connection.
    if _use_sql_server():
        import pyodbc  # only needed (and imported) when using SQL Server
        return pyodbc.connect(os.getenv("SQL_CONNECTION_STRING", ""), autocommit=autocommit)
    return sqlite3.connect(DB_PATH)


def describe():
    """One short line saying which database this process is talking to."""
    if _use_sql_server():
        return "SQL Server (USE_SQL_SERVER=true)"
    return "SQLite file " + DB_PATH


def ensure_table():
    # Make the table the first time. Does nothing if it already exists.
    if _use_sql_server():
        # autocommit: each DDL statement sticks the moment it succeeds. With a
        # single end-of-function commit, one failing statement would roll back
        # every earlier one - leaving old stored procedures behind while the
        # Python code already expects the new ones.
        conn = _open(autocommit=True)
    else:
        conn = _open()
    try:
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
                "  AiAnswerJson NVARCHAR(MAX),"
                "  Answer NVARCHAR(MAX),"
                "  PromptTokens INT,"
                "  CompletionTokens INT,"
                "  TotalTokens INT,"
                "  CreatedAt NVARCHAR(40),"
                "  PromptId NVARCHAR(40)"
                ")"
            )
            # The table has grown over time (.NET era, then PromptId, then
            # AiAnswerJson). A database created by an older version is missing
            # the newer columns, so add whatever is absent - and do it BEFORE
            # the view below, because a view that names a missing column fails
            # immediately at CREATE time.
            for column, sql_type in (
                ("Model", "NVARCHAR(100)"),
                ("KeyValuesJson", "NVARCHAR(MAX)"),
                ("AiAnswerJson", "NVARCHAR(MAX)"),
                ("PromptTokens", "INT"),
                ("CompletionTokens", "INT"),
                ("TotalTokens", "INT"),
                ("CreatedAt", "NVARCHAR(40)"),
                ("PromptId", "NVARCHAR(40)"),
            ):
                cursor.execute(
                    f"IF COL_LENGTH('Documents', '{column}') IS NULL "
                    f"ALTER TABLE Documents ADD {column} {sql_type}"
                )
            # A view is a saved SELECT with a name. The Excel export reads through
            # it, so it only ever sees the columns listed here.
            # CREATE OR ALTER = make it, or replace it if it already exists, so
            # running this at every startup is safe. It must be alone in its own
            # execute() call - SQL Server wants it alone in a batch.
            cursor.execute(
                "CREATE OR ALTER VIEW DocumentsExport AS "
                "SELECT Id, FileName, Model, KeyValuesJson, AiAnswerJson, CreatedAt FROM Documents"
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
                "  SELECT Id, FileName, Model, KeyValuesJson, AiAnswerJson, CreatedAt "
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
                "  @DocumentText NVARCHAR(MAX), @KeyValuesJson NVARCHAR(MAX), @AiAnswerJson NVARCHAR(MAX), @Answer NVARCHAR(MAX), "
                "  @PromptTokens INT, @CompletionTokens INT, @TotalTokens INT, "
                "  @CreatedAt NVARCHAR(40), @PromptId NVARCHAR(40) "
                "AS BEGIN "
                "  SET NOCOUNT ON; "
                "  INSERT INTO Documents "
                "  (FileName, Model, Prompt, DocumentText, KeyValuesJson, AiAnswerJson, Answer, "
                "   PromptTokens, CompletionTokens, TotalTokens, CreatedAt, PromptId) "
                "  VALUES (@FileName, @Model, @Prompt, @DocumentText, @KeyValuesJson, @AiAnswerJson, @Answer, "
                "          @PromptTokens, @CompletionTokens, @TotalTokens, @CreatedAt, @PromptId) "
                "END"
            )
            # Stored procedure for the history page: the SELECT lives inside
            # SQL Server too.
            cursor.execute(
                "CREATE OR ALTER PROCEDURE GetDocuments "
                "AS BEGIN "
                "  SET NOCOUNT ON; "
                "  SELECT Id, FileName, Model, Prompt, DocumentText, KeyValuesJson, AiAnswerJson, Answer, "
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
                "  AiAnswerJson TEXT,"
                "  Answer TEXT,"
                "  PromptTokens INTEGER,"
                "  CompletionTokens INTEGER,"
                "  TotalTokens INTEGER,"
                "  CreatedAt TEXT,"
                "  PromptId TEXT"
                ")"
            )
            # Same catch-up as on SQL Server: an older documents.db file was
            # created before these columns existed, so add them once.
            columns = [row[1] for row in cursor.execute("PRAGMA table_info(Documents)")]
            if "PromptId" not in columns:
                cursor.execute("ALTER TABLE Documents ADD COLUMN PromptId TEXT")
            if "AiAnswerJson" not in columns:
                cursor.execute("ALTER TABLE Documents ADD COLUMN AiAnswerJson TEXT")
            # SQLite supports views too (but not stored procedures). Drop and
            # recreate mirrors what CREATE OR ALTER does on SQL Server.
            cursor.execute("DROP VIEW IF EXISTS DocumentsExport")
            cursor.execute(
                "CREATE VIEW DocumentsExport AS "
                "SELECT Id, FileName, Model, KeyValuesJson, AiAnswerJson, CreatedAt FROM Documents"
            )
            conn.commit()
    finally:
        conn.close()


# INSERT one row. On SQL Server this calls the SaveDocument stored procedure;
# SQLite has no procedures, so there it stays a plain INSERT. Both use "?"
# for parameters, and the value tuple is identical in both branches.
# prompt_id is the same for every file saved by one analyze request.
def save(file_name, model, prompt, document_text, key_values_json, ai_answer_json, answer,
         prompt_tokens, completion_tokens, total_tokens, created_at, prompt_id):
    conn = _open()
    cursor = conn.cursor()
    if _use_sql_server():
        cursor.execute(
            "EXEC SaveDocument ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?",
            (file_name, model, prompt, document_text, key_values_json, ai_answer_json, answer,
             prompt_tokens, completion_tokens, total_tokens, created_at, prompt_id),
        )
    else:
        cursor.execute(
            "INSERT INTO Documents "
            "(FileName, Model, Prompt, DocumentText, KeyValuesJson, AiAnswerJson, Answer, "
            " PromptTokens, CompletionTokens, TotalTokens, CreatedAt, PromptId) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (file_name, model, prompt, document_text, key_values_json, ai_answer_json, answer,
             prompt_tokens, completion_tokens, total_tokens, created_at, prompt_id),
        )
    conn.commit()
    conn.close()


def save_ai_answer_json(ai_json: dict | None) -> str | None:
    """Return a JSON string suitable for storing, or None when no JSON.

    Keeps None distinct so callers can store NULL in the DB when parsing
    failed or no structured content was created.
    """
    if ai_json is None:
        return None
    try:
        return json.dumps(ai_json, ensure_ascii=False)
    except Exception:
        return None


def _window_utc(from_date, to_date):
    """Turn the report's local dates into naive UTC datetimes.

    CreatedAt is stored in UTC, but the user picks local dates. Without this
    conversion, a document analysed early in the local morning (still the
    previous day in UTC) would silently fall out of a same-day report.
    """
    local_from = datetime.strptime(from_date + " 00:00:00", "%Y-%m-%d %H:%M:%S").replace(tzinfo=LOCAL_TZ)
    local_to = datetime.strptime(to_date + " 23:59:59", "%Y-%m-%d %H:%M:%S").replace(tzinfo=LOCAL_TZ)
    return (
        local_from.astimezone(timezone.utc).replace(tzinfo=None),
        local_to.astimezone(timezone.utc).replace(tzinfo=None),
    )


# SELECT the rows for the Excel export: a date window plus one document type.
# On SQL Server this calls the GetDocumentsFiltered stored procedure; SQLite
# has no procedures, so there it is a plain SELECT on the DocumentsExport view.
def get_filtered(from_date, to_date, model):
    from_dt, to_dt = _window_utc(from_date, to_date)
    conn = _open()
    cursor = conn.cursor()
    if _use_sql_server():
        # The procedure takes DATETIME2 parameters, so send real datetime
        # values - pyodbc passes them as proper dates, not text.
        cursor.execute("EXEC GetDocumentsFiltered ?, ?, ?", (from_dt, to_dt, model))
    else:
        cursor.execute(
            "SELECT Id, FileName, Model, KeyValuesJson, AiAnswerJson, CreatedAt "
            "FROM DocumentsExport "
            "WHERE CreatedAt >= ? AND CreatedAt <= ? AND Model = ? "
            "ORDER BY Id",
            (from_dt.strftime("%Y-%m-%d %H:%M:%S"), to_dt.strftime("%Y-%m-%d %H:%M:%S"), model),
        )
    rows = cursor.fetchall()
    conn.close()

    records = []
    for row in rows:
        # Columns: Id, FileName, Model, KeyValuesJson, AiAnswerJson, CreatedAt
        try:
            ai_json = json.loads(row[4]) if row[4] else None
        except Exception:
            ai_json = None
        records.append({
            "id": row[0],
            "fileName": row[1],
            "model": row[2],
            "fields": json.loads(row[3] or "{}"),
            "aiAnswerJson": ai_json,
            "createdAt": row[5],
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
            "SELECT Id, FileName, Model, Prompt, DocumentText, KeyValuesJson, AiAnswerJson, Answer, "
            "PromptTokens, CompletionTokens, TotalTokens, CreatedAt, PromptId "
            "FROM Documents ORDER BY Id DESC"
        )
    rows = cursor.fetchall()
    conn.close()

    records = []
    for row in rows:
        # Columns: Id, FileName, Model, Prompt, DocumentText, KeyValuesJson,
        # AiAnswerJson, Answer, PromptTokens, CompletionTokens, TotalTokens,
        # CreatedAt, PromptId
        try:
            ai_json = json.loads(row[6]) if row[6] else None
        except Exception:
            ai_json = None
        records.append({
            "id": row[0],
            "fileName": row[1],
            "model": row[2],
            "prompt": row[3],
            "documentText": row[4],
            "fields": json.loads(row[5] or "{}"),
            "aiAnswerJson": ai_json,
            "answer": row[7],
            "promptTokens": row[8],
            "completionTokens": row[9],
            "totalTokens": row[10],
            "createdAt": row[11],
            "promptId": row[12],
        })
    return records
