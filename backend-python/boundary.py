"""Where search stops and SQL starts.

Search retrieves. It does not aggregate, count, or compare. That is not a bug to
work around — it is the shape of the tool, and knowing which side of the line a
question falls on is most of what makes a retrieval layer useful.

This script asks the same questions two ways and prints both answers side by
side, so the boundary is something you have seen rather than been told.

    python boundary.py                    # the paired demonstrations
    python boundary.py --sql "SELECT ..." # ad-hoc SQL against the extraction log

It contacts nothing and costs nothing. The SQL runs against an in-memory SQLite
table loaded from the AI EXTRACTION LOG sheet, so both halves describe exactly
the same documents.

Why not query documents.db? Because it holds a different set of documents than
the workbook — the workbook is exported on the office laptop against SQL Server.
Comparing a search over 18 documents against a count over 3 different ones would
produce two true answers that appear to contradict each other, which is the
exact failure this script exists to prevent.
"""

import argparse
import sqlite3
import textwrap

# phase1.py owns reading the workbook, including stripping the trailing spaces
# off the two quirky column headers. Reuse it rather than repeating the fix.
from phase1 import COMP_SHEET, LOG_SHEET, VAL_SHEET, sheet

# The workbook's headers are not valid SQL identifiers, so they are renamed once,
# here, and every query below speaks these names.
COLUMNS = {
    "Number": "number",
    "Source_File": "source_file",
    "Type Of Document": "doc_type",
    "Field_Name(Keys from json)": "field_key",
    "Reference_Column(Keys from .net core)": "field_name",
    "OCR_Column (Extracted values)": "ocr_value",
    "Completeness_Flag": "completeness_flag",
    "Validity_Flag": "validity_flag",
}


def load_table():
    """The extraction log as a real SQL table, in memory."""
    frame = sheet(LOG_SHEET)[list(COLUMNS)].rename(columns=COLUMNS)
    # The same guard phase1.py uses: a document ingested twice would double every
    # count below without raising anything.
    frame = frame.drop_duplicates(subset=["number", "source_file", "field_key"])

    connection = sqlite3.connect(":memory:")
    frame.to_sql("extraction_log", connection, index=False)
    documents = connection.execute(
        "SELECT COUNT(DISTINCT number || '|' || source_file) FROM extraction_log"
    ).fetchone()[0]
    print(f"SQL: loaded {len(frame)} rows / {documents} documents into extraction_log")
    print(f"SQL: columns — {', '.join(COLUMNS.values())}\n")
    return connection


def run(connection, query):
    """Run one query and print it as a small table."""
    cursor = connection.execute(query)
    headers = [description[0] for description in cursor.description]
    rows = cursor.fetchall()
    widths = [max(len(str(h)), *(len(str(r[i])) for r in rows)) if rows else len(str(h))
              for i, h in enumerate(headers)]
    print("      " + "  ".join(str(h).ljust(w) for h, w in zip(headers, widths)))
    print("      " + "  ".join("-" * w for w in widths))
    for row in rows:
        print("      " + "  ".join(str(v).ljust(w) for v, w in zip(row, widths)))
    return rows


def check(connection):
    """Prove the extraction log and the two trend sheets tell the same story.

    The trend sheets are aggregates of the extraction log, so every count on them
    has to be reproducible by SQL over the log. If they ever disagree, the sheets
    were generated from different data and every chunk built from them is wrong.
    """
    completeness = sheet(COMP_SHEET).set_index("OCR_Field")
    validity = sheet(VAL_SHEET)
    validity = validity[validity["Pass_Count"].notna()].set_index("Field_Type")

    failures = []
    for field_name, nulls, fails, soft in connection.execute("""
        SELECT field_name,
               SUM(completeness_flag = 'N'),
               SUM(validity_flag = 'Fail'),
               SUM(completeness_flag = 'Y' AND validity_flag = 'Fail')
        FROM extraction_log GROUP BY field_name
    """):
        total = int(completeness.loc[field_name, "Total_Records"])
        expected_nulls = int(completeness.loc[field_name, "Null_Count"])
        expected_fails = int(validity.loc[field_name, "Fail_Count"])
        expected_soft = (total - expected_nulls) - int(validity.loc[field_name, "Pass_Count"])
        for label, got, want in (("Null_Count", nulls, expected_nulls),
                                 ("Fail_Count", fails, expected_fails),
                                 ("populated-but-invalid", soft, expected_soft)):
            if got != want:
                failures.append(f"{field_name}: {label} is {got} in the log, {want} in the trend sheet")

    fields = len(completeness)
    if failures:
        print(f"CHECK: {len(failures)} disagreement(s) across {fields} fields")
        for failure in failures:
            print(f"  {failure}")
        raise SystemExit("CHECK: the workbook is not internally consistent.")
    print(f"CHECK: all {fields} fields reconcile — Null_Count, Fail_Count and "
          f"populated-but-invalid all match the trend sheets.")


def demo(title, question, verdict, query, connection, search_says):
    print("=" * 78)
    print(f"{title}\n")
    print(f"  Question:  {question}")
    print(f"  Verdict:   {verdict}\n")
    if query:
        print("  SQL answers it exactly:")
        print(textwrap.indent(textwrap.dedent(query).strip(), "      "))
        print()
        run(connection, query)
        print()
    print(f"  Search:    {search_says}\n")


def main():
    parser = argparse.ArgumentParser(
        prog="boundary.py",
        description="Show which questions belong to search and which belong to SQL.")
    parser.add_argument("--sql", help="run one query against extraction_log and stop")
    parser.add_argument("--check", action="store_true",
                        help="verify the log and the trend sheets agree, and stop")
    args = parser.parse_args()

    connection = load_table()

    if args.sql:
        run(connection, args.sql)
        return

    if args.check:
        check(connection)
        return

    demo(
        "1. COUNTING — a SQL question",
        "How many documents are missing the vendor GSTIN?",
        "SQL. Search returns the matching chunks; it does not count them.",
        """
        SELECT COUNT(*) AS documents_missing_gstin
        FROM extraction_log
        WHERE field_name = 'Vendor GSTIN' AND completeness_flag = 'N'
        """,
        connection,
        "returns chunks that mention vendor GSTIN — and cannot tell you how many "
        "there were.\n             You count them, or a model counts them in a RAG step.",
    )

    demo(
        "2. RANKING BY VALUE — a SQL question",
        "Which document extracted worst?",
        "SQL. Search ranks by word overlap, not by magnitude — it cannot order "
        "58% against 55%.",
        """
        SELECT number, source_file,
               SUM(completeness_flag = 'Y') AS populated,
               COUNT(*)                     AS total,
               ROUND(100.0 * SUM(completeness_flag = 'Y') / COUNT(*), 1) AS pct
        FROM extraction_log
        GROUP BY number, source_file
        ORDER BY pct ASC
        LIMIT 5
        """,
        connection,
        "has no idea 39.4 is smaller than 42.4. Every one of these chunks says "
        "\"poor completeness\";\n             nothing in the text ranks them "
        "against each other.",
    )

    demo(
        "3. POPULATED BUT INVALID — a SQL question",
        "Which fields extracted successfully and then failed validation?",
        "SQL. This is a two-column comparison, and there is no phrase to match on.",
        """
        SELECT field_name,
               COUNT(*) AS extracted_but_invalid
        FROM extraction_log
        WHERE completeness_flag = 'Y' AND validity_flag = 'Fail'
        GROUP BY field_name
        ORDER BY extracted_but_invalid DESC
        """,
        connection,
        "can find these once the chunk text says so — which is exactly why the "
        "document chunks carry\n             an \"Extracted but failed validation:\" "
        "clause. Without that sentence, this question has no answer in the index.",
    )

    demo(
        "4. NO COLUMN EXISTS — a search question",
        "Which vendors keep sending documents where the GSTIN does not extract?",
        "Search. There is no WHERE clause for \"keep sending\".",
        None,
        connection,
        "this is the whole reason the index exists. \"keep sending\" is a pattern "
        "across documents, phrased\n             in words nobody put in a column. "
        "SQL can count GSTIN failures per vendor, but only once you have\n"
        "             already decided that is what the question meant.",
    )

    print("=" * 78)
    print("""
The rule: if the answer is a NUMBER or an ORDERING, it is SQL. If the answer is
"show me the ones that look like this", it is search.

MSSQL stays the system of record. Exact lookups — one invoice, one total — stay
in SQL and always will. The index earns its keep on the questions that have no
column behind them.
""")


if __name__ == "__main__":
    main()
