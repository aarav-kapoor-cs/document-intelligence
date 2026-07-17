import os
import json
import sqlite3

# A small database helper that uses plain SQL: CREATE TABLE, INSERT, SELECT.
# By default it saves to a SQLite file (documents.db) next to this file.
# If USE_SQL_SERVER=true in the .env, it uses Microsoft SQL Server instead.

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
            "  CreatedAt NVARCHAR(40)"
            ")"
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
            "  CreatedAt TEXT"
            ")"
        )
    conn.commit()
    conn.close()


# INSERT one row. SQLite and pyodbc both use "?" for parameters.
def save(file_name, model, prompt, document_text, key_values_json, answer,
         prompt_tokens, completion_tokens, total_tokens, created_at):
    conn = _open()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO Documents "
        "(FileName, Model, Prompt, DocumentText, KeyValuesJson, Answer, "
        " PromptTokens, CompletionTokens, TotalTokens, CreatedAt) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (file_name, model, prompt, document_text, key_values_json, answer,
         prompt_tokens, completion_tokens, total_tokens, created_at),
    )
    conn.commit()
    conn.close()


# SELECT every row, newest first.
def get_all():
    conn = _open()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT Id, FileName, Model, Prompt, DocumentText, KeyValuesJson, Answer, "
        "PromptTokens, CompletionTokens, TotalTokens, CreatedAt "
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
            "keyValues": json.loads(row[5] or "[]"),
            "answer": row[6],
            "promptTokens": row[7],
            "completionTokens": row[8],
            "totalTokens": row[9],
            "createdAt": row[10],
        })
    return records
