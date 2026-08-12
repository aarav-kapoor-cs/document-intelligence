"""Build Project_Report.xlsx - what this project is, what went into it, and when.

    python make_project_report.py

A record of the work for a review or a presentation: the technology used, every
commit in order, what each backend module does, and the numbers that were
actually measured.

Nothing here touches the audit workbook. That one mirrors the mentor's
DriftTemplateInterns.xlsx verbatim and must never gain a sheet; this is a
separate document about the project rather than about the invoices.

The timeline is read from `git log`, so it stays true as the project moves. The
rest is written down here because it is judgement, not data - which decision
mattered and why it was taken.
"""

import subprocess
import sys

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
HEADER_FONT = Font(bold=True, color="FFFFFF")
PHASE_FILL = PatternFill("solid", fgColor="DDEBF7")

# Commit ranges per phase, oldest first. A commit is placed in the last phase
# whose start date it is on or after.
PHASES = (
    ("2026-07-17", "1. Foundation", "Angular shell, Azure OCR, first FastAPI backend"),
    ("2026-07-21", "2. Backend rebuild", "Python backend, SQL Server, document-type checker"),
    ("2026-07-23", "3. Excel audit report", "The 6-sheet workbook, GSTIN repair, validity bands"),
    ("2026-08-03", "4. AI Search and RAG", "Chunking, two indexes, RAG answers, the agent"),
    ("2026-08-07", "5. Anomaly detection", "Rules, robust statistics, logistic regression trained locally"),
)


def phase_for(date_text):
    label = PHASES[0][1]
    for start, name, _detail in PHASES:
        if date_text >= start:
            label = name
    return label


def sheet(book, title, headers, rows, widths):
    grid = book.create_sheet(title)
    grid.append(headers)
    for column, width in enumerate(widths, start=1):
        grid.column_dimensions[get_column_letter(column)].width = width
    for cell in grid[1]:
        cell.fill, cell.font = HEADER_FILL, HEADER_FONT
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    for row in rows:
        grid.append(row)
    for row in grid.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    grid.freeze_panes = "A2"
    grid.auto_filter.ref = grid.dimensions
    return grid


def git_timeline():
    """Every commit, oldest first, with the date and the files it touched."""
    try:
        raw = subprocess.run(
            ["git", "log", "--reverse", "--date=format:%Y-%m-%d %H:%M",
             "--pretty=format:%h|%ad|%s", "--shortstat"],
            capture_output=True, text=True, check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []

    rows, pending = [], None
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if "|" in line and line.count("|") == 2:
            if pending:
                rows.append(pending + [""])
            commit, stamp, subject = line.split("|", 2)
            date_text = stamp.split(" ")[0]
            pending = [len(rows) + 1, date_text, stamp.split(" ")[1],
                       phase_for(date_text), commit, subject]
        elif pending and "changed" in line:
            rows.append(pending + [line])
            pending = None
    if pending:
        rows.append(pending + [""])
    return rows


book = Workbook()
book.remove(book.active)

# --- 1. Overview -------------------------------------------------------------
sheet(book, "1 Overview",
      ["Item", "Detail"],
      [
          ["Project", "Document Intelligence & Audit Search"],
          ["Repository", "github.com/aarav-kapoor-cs/document-intelligence"],
          ["What it does",
           "Reads invoices and other documents with Azure OCR, answers questions "
           "about them with an LLM, exports a 6-sheet audit workbook, makes that "
           "workbook searchable in natural language, and scores every invoice for "
           "anomalies."],
          ["Product A - Document reader",
           "Upload a PDF -> Azure Document Intelligence extracts text and labelled "
           "fields -> an LLM classifies the document type and rejects a mismatch -> "
           "a second LLM answers the user's prompt -> the result is saved."],
          ["Product B - Audit searcher",
           "Export the saved documents to the audit workbook -> chunk it into 84 "
           "self-contained paragraphs -> index in Azure AI Search -> answer questions "
           "with citations, or route to SQL through a Semantic Kernel agent."],
          ["Product C - Anomaly detector",
           "Score every invoice 0-100 across seven anomalies, using deterministic "
           "rules, median/MAD statistics, and a logistic regression fitted on "
           "synthetic invoices with injected anomalies."],
          ["Where the model is trained",
           "Locally - `bash train_local.sh`. No cloud, no cost, about two minutes. "
           "The Azure ML job (azureml/) is written and verified but was not used, "
           "because training on 600 rows does not need a compute cluster."],
          ["The join between A and B",
           "The Excel workbook, deliberately - not the database. The two hold "
           "different document sets, so comparing them would give two true answers "
           "that look contradictory."],
          ["Domain", "OCR drift auditing for Indian GST invoices"],
          ["Completeness", "Was the field populated. Explicitly NOT whether it is correct."],
          ["Validity", "Did the populated value pass a format check (GSTIN, date, number)."],
          ["Total commits", str(len(git_timeline()))],
          ["Backend Python", "6,862 lines across 21 modules"],
          ["Tests", "36, all passing, no Azure calls needed"],
      ],
      [26, 100])

# --- 2. Timeline -------------------------------------------------------------
timeline = git_timeline()
grid = sheet(book, "2 Timeline",
             ["#", "Date", "Time", "Phase", "Commit", "What changed", "Files / lines"],
             timeline, [5, 12, 8, 22, 11, 62, 34])
for row in grid.iter_rows(min_row=2, min_col=4, max_col=4):
    row[0].fill = PHASE_FILL

# --- 3. Phases ---------------------------------------------------------------
sheet(book, "3 Phases",
      ["Phase", "Started", "What was built"],
      [[name, start, detail] for start, name, detail in PHASES],
      [24, 14, 74])

# --- 4. Technology -----------------------------------------------------------
sheet(book, "4 Tech stack",
      ["Layer", "Technology", "Version", "Why it is here"],
      [
          ["Frontend", "Angular", "22.0", "Single standalone component, no router - three modes chosen by a dropdown"],
          ["Frontend", "TypeScript", "6.0", "Language for the Angular app"],
          ["Frontend", "RxJS", "7.8", "HttpClient returns observables"],
          ["Frontend", "zone.js", "0.16", "Angular change detection"],
          ["Frontend", "Vitest / Playwright", "4.0 / 1.61", "Configured, not yet used - no .spec.ts in the repo"],
          ["Backend", "Python", "3.12", "Runtime"],
          ["Backend", "FastAPI", "0.139.2", "The HTTP API, on port 8001"],
          ["Backend", "Pydantic", "(with FastAPI)", "Request and response shapes in models.py"],
          ["Backend", "Uvicorn", "0.51.0", "ASGI server"],
          ["Backend", "openpyxl", "3.1.5", "Writes the 6-sheet audit workbook"],
          ["Backend", "pandas", "2.3.3", "Reads the workbook back for chunking. Pinned to 2.x: 3.x changes blank-cell handling the chunker relies on"],
          ["Backend", "numpy", "2.5.1", "Median/MAD statistics and the logistic-regression dot product"],
          ["Backend", "pyodbc", "5.3.0", "SQL Server on the office laptop"],
          ["Backend", "python-dotenv", "1.2.2", "Loads .env"],
          ["Database", "SQLite", "built in", "Local default - documents.db, no server needed"],
          ["Database", "SQL Server", "ODBC 18", "Office laptop, through stored procedures"],
          ["AI", "Azure Document Intelligence", "1.0.2", "OCR: prebuilt-invoice, -receipt, -idDocument, -layout"],
          ["AI", "Azure OpenAI", "openai 2.46.0", "Chat and embeddings, via the /openai/v1 endpoint with the plain OpenAI client"],
          ["AI", "Azure AI Search", "12.0.0", "Two indexes: audit-keyword (BM25) and audit-hybrid (vectors + RRF)"],
          ["AI", "Semantic Kernel", "1.44.0", "The agent, with four kernel functions"],
          ["AI", "MCP", "1.29.0", "Pinned but unused - semantic-kernel 1.44 needs mcp 1.x or its import breaks"],
          ["ML", "scikit-learn", "1.6.1", "TRAINING ONLY, in a separate .venv-train - never installed in the backend, which applies the model as a numpy dot product"],
          ["ML", "Logistic regression", "-", "The shipped model. Exported as coefficients, not a pickle"],
          ["ML", "Isolation forest", "-", "Trained only to answer 'would unsupervised have done better'. It did not (0.576 vs 0.902). Never shipped"],
          ["ML", "Azure Machine Learning", "not used", "The job, environment and submit script are written and verified, but training 600 rows locally takes two minutes and needs no cluster"],
      ],
      [14, 32, 18, 74])

# --- 5. Azure services -------------------------------------------------------
sheet(book, "5 Azure services",
      ["Service", "Used for", "Where in the code", "Cost note"],
      [
          ["Azure Document Intelligence", "OCR - text and labelled fields from PDFs",
           "ocr_service.py", "Per page analysed"],
          ["Azure OpenAI (chat)", "Document-type check, the answer, and the JSON re-format",
           "ai_service.py, checker_service.py", "Three calls per uploaded file"],
          ["Azure OpenAI (embeddings)", "text-embedding-3-small, 1536 dims, for the vector index",
           "hybrid_search.py", "Per chunk embedded"],
          ["Azure AI Search", "Keyword and hybrid retrieval over the audit workbook",
           "keyword_search.py, hybrid_search.py",
           "BILLS HOURLY whether queried or not - delete the service, not just the index"],
          ["Azure Machine Learning", "NOT USED. The job is written and verified; training runs locally instead",
           "azureml/ (train.py, job.yml, submit.py)",
           "Nothing spent. 600 rows fits on a laptop in two minutes"],
      ],
      [30, 44, 34, 46])

# --- 6. Backend modules ------------------------------------------------------
sheet(book, "6 Backend modules",
      ["Module", "Lines", "What it does"],
      [
          ["hybrid_search.py", 891, "Vector + hybrid index: chunking, embeddings, RRF, semantic ranking, the RAG answer"],
          ["keyword_search.py", 778, "The keyword-only index. ~92% the same as hybrid_search on purpose - the diff IS the vector feature"],
          ["excel_service.py", 726, "Builds the 6-sheet audit workbook, verbatim to the mentor's template"],
          ["synthetic_invoices.py", 576, "Generates labelled fake invoices with injected anomalies, for training"],
          ["test_backend.py", 393, "36 tests, no Azure calls"],
          ["anomaly_features.py", 372, "One document's fields -> 24 numbers. Also the GSTIN check digit"],
          ["anomaly_detector.py", 350, "The three scoring layers, model loading, the CLI"],
          ["database.py", 313, "SQLite and SQL Server behind one interface, plus stored procedures"],
          ["agent_service.py", 287, "Semantic Kernel agent with four tools and a call trace"],
          ["search_api.py", 271, "HTTP routes for search: status, chunks, ask, compare, reindex, agent"],
          ["anomaly_rules.py", 235, "The seven rules and EVERY threshold in the detector"],
          ["sql_comparison.py", 226, "Loads the extraction log into SQLite; shows what belongs to SQL vs search"],
          ["main.py", 218, "FastAPI app, CORS, the analyze and export routes, optional router mounting"],
          ["ai_service.py", 182, "The Azure OpenAI client, the answer, and the JSON re-format"],
          ["anomaly_metrics.py", 178, "Measures each layer against the synthetic labels, locally and free"],
          ["anomaly_api.py", 174, "HTTP routes for anomalies: status, scan, score, rules"],
          ["ocr_service.py", 143, "Azure Document Intelligence, plus PAN -> GSTIN repair"],
          ["check_setup.py", 127, "Says what is wrong with a machine instead of raising"],
          ["models.py", 126, "Every Pydantic request and response shape"],
          ["retrieval_metrics.py", 105, "recall@k for the four retrieval methods"],
          ["field_applicability.py", 104, "Splits completeness into core vs optional fields"],
      ],
      [26, 9, 88])

# --- 7. Anomaly rules --------------------------------------------------------
sheet(book, "7 Anomaly rules",
      ["#", "Anomaly", "Layer", "What it checks", "Weight"],
      [
          [1, "Missing core field", "document", "A required field is empty. Required is a subset of the 12 core fields, because Tax Details and Customer GSTIN are legitimately absent from real invoices", 20],
          [2, "Invalid format", "document", "Fails the format check, plus a GSTIN mod-36 check digit the audit workbook structurally cannot see", "15 / 8"],
          [3, "PAN instead of GSTIN", "document", "The vendor tax id is a 10-character PAN where a 15-character GSTIN belongs", 25],
          [4, "Duplicate invoice", "corpus", "Same invoice number, vendor, date and amount seen more than once", 30],
          [5, "High-value invoice", "document", "Total above the review threshold, default 10,00,000 rupees", 15],
          [6, "Amounts do not reconcile", "document", "Base + GST differs from the total by more than 1 rupee; line items match neither base nor total", "30 / 20"],
          [7, "Vendor amount outlier", "corpus", "Robust z of log10(total) within that vendor's own history exceeds 3.5", 25],
      ],
      [5, 26, 12, 82, 10])

# --- 8. Measured results -----------------------------------------------------
sheet(book, "8 Measured results",
      ["What was measured", "Result", "What it means"],
      [
          ["Retrieval: BM25 keyword", "37% mean recall@5", "The best of the four methods on this corpus"],
          ["Retrieval: hybrid RRF", "34%", "Vectors did not help here"],
          ["Retrieval: vector only", "27%", ""],
          ["Retrieval: hybrid + semantic reranker", "26%", "The most expensive option scored lowest"],
          ["Why vectors lose", "mean pairwise cosine 0.883", "The chunks are all the same kind of thing, so embeddings cannot separate them"],
          ["Anomaly: logistic regression", "precision 0.902, recall 0.725", "Trained locally on 618 synthetic invoices in about two minutes"],
          ["Anomaly: rules only", "precision 0.905, recall 0.745", "The rules BEAT the model - a result worth reporting, not a bug"],
          ["Anomaly: isolation forest", "precision 0.576", "Unsupervised did clearly worse; logged for comparison, never shipped"],
          ["Anomaly: held-out anomaly types", "recall 0.623 on 53 rows", "THE NUMBER TO QUOTE - measures generalisation, not memorisation"],
          ["Model on real invoices, before the fix", "17 of 17 scored Suspect", "field_count was 6 sigma from training data; the generator was wrong, not the model"],
          ["Model on real invoices, after the fix", "median 9.8, 6 of 17 Suspect", "The invoice with no findings went from 98.3 to 9.8 - Clean"],
          ["Core vs optional fields", "50.8% overall vs 93.1% core", "Azure's US-shaped invoice schema carries fields an Indian invoice never prints"],
          ["GSTIN check digit", "27ABICX1218R1ZX fails", "Valid shape, so the workbook says Pass - a real defect the audit cannot see"],
      ],
      [40, 32, 74])

# --- 9. Decisions ------------------------------------------------------------
sheet(book, "9 Key decisions",
      ["Decision", "Why"],
      [
          ["Export the model as coefficients, not a pickle",
           "Logistic regression inference is sigmoid(z.w + b), so the scaler and weights reproduce scikit-learn to float precision. The backend needs no scikit-learn at all, the artifact is 4 KB of readable JSON that can be committed, and the per-feature contribution w_i * z_i IS the explanation - no separate explainer that could disagree with the model."],
          ["Train locally rather than in Azure ML",
           "600 rows and 24 features fit on a laptop in two minutes. A compute cluster would add queue time, an image build and a bill for no measurable gain. The Azure ML job is written and verified so it is there when the corpus outgrows a laptop."],
          ["scikit-learn lives in a separate .venv-train",
           "Keeping it out of backend-python/.venv is what makes 'the backend needs no ML library' a testable claim rather than a slogan."],
          ["Never blend a probability with a z-score",
           "They are different kinds of number. The score is 100 x P(anomaly) with a model and a capped sum of rule weights without one, and `method` always says which."],
          ["The workbook mirrors the template verbatim",
           "Including two headers with deliberate trailing spaces. Nothing enforces it but discipline - an appended column would pass all 36 tests silently."],
          ["The GSTIN checksum lives only in the anomaly module",
           "Changing excel_service.is_valid_gstin would change the Validity_Flag column, and therefore the workbook."],
          ["Search reads the workbook, never the database",
           "The two hold different document sets. Cross-checking would produce two true answers that look contradictory."],
          ["Percentages are expanded into prose before indexing",
           "'11.1%' cannot be retrieved by 'which fields extract badly', so the verdict is written out in words."],
          ["Two near-duplicate search modules",
           "keyword_search and hybrid_search differ by 141 lines of 1,669. The diff IS the vector feature - but every fix must be made twice."],
          ["Tolerance of 1 rupee on the totals check",
           "Real invoices are off by 10 and 40 paise from printed rounding. Zero tolerance flagged three of the four real invoices."],
          ["Corpus checks suppress themselves below 20 documents",
           "Median/MAD over four vendors is noise. Reporting nothing is better than reporting a number computed from too little data."],
          ["Model uses document-intrinsic features only",
           "Corpus z-scores depend on which slice was scanned, so including them would make one document score differently alone than inside a batch."],
      ],
      [46, 96])

book.save("Project_Report.xlsx")
print("Wrote Project_Report.xlsx")
print(f"  {len(book.sheetnames)} sheets: {', '.join(book.sheetnames)}")
print(f"  timeline: {len(timeline)} commits")
