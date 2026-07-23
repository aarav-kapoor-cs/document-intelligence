import base64
import json
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

# Load the keys from the .env file (next to this file), before anything uses them.
load_dotenv(Path(__file__).with_name(".env"))

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware

import database
import ocr_service
import ai_service
import checker_service
import excel_service
from models import AnalyzeRequest, AnalyzeResponse, ExportRequest, FileResult

app = FastAPI()

# Allow the Angular app (http://localhost:4200) to call this API from the browser.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create the database table on startup. If the database can't be reached yet
# (e.g. SQL Server is down or the connection string is wrong), log it and keep
# running so the API still works and each request can report the exact problem.
try:
    database.ensure_table()
except Exception as ex:
    print("WARNING: could not prepare the database at startup:", ex)


def _save(file_name, model, prompt, text, fields, answer, tokens, prompt_id):
    """Insert one result row into the database (fields stored as JSON)."""
    print("DATABASE: saving the result for", file_name, "...")
    database.save(
        file_name,
        model,
        prompt,
        text,
        json.dumps(fields),
        answer,
        tokens.prompt_tokens if tokens else 0,
        tokens.completion_tokens if tokens else 0,
        tokens.total_tokens if tokens else 0,
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        prompt_id,
    )
    print("DATABASE: saved.")


def _process_file(file, model, prompt, prompt_id):
    """Run one file through OCR, the type checker, the AI, and the database.

    Each step reports its own error, and a failed step stops the file right
    there - nothing half-done is saved.
    """
    print("PROCESSING FILE:", file.name, "| model:", model)

    def failed(step, ex):
        print("ERROR while processing", file.name, "-", ex)
        return FileResult(file_name=file.name, model=model, error=step + ": " + str(ex))

    try:
        text, fields = ocr_service.analyze(model, base64.b64decode(file.base64))
    except Exception as ex:
        return failed("Could not read the file", ex)

    # Confirm the file matches the chosen document type before going further.
    try:
        matches, message = checker_service.check(model, text)
    except Exception as ex:
        return failed("The document-type check failed", ex)
    if not matches:
        # Wrong document type: stop here - no AI answer, and nothing is saved.
        return FileResult(file_name=file.name, model=model, error=message)

    try:
        answer, tokens = ai_service.answer(prompt, text, fields)
    except Exception as ex:
        return failed("The AI could not answer", ex)

    # Save the result. A database problem is reported but never loses the answer.
    error = ""
    try:
        _save(file.name, model, prompt, text, fields, answer, tokens, prompt_id)
    except Exception as ex:
        print("ERROR saving to the database:", ex)
        error = "The result could not be saved to the database: " + str(ex)

    return FileResult(
        file_name=file.name,
        model=model,
        text=text,
        fields=fields,
        answer=answer,
        error=error,
        tokens=tokens,
    )


# POST /api/analyze - read each file, check its type, ask the AI, and save it.
@app.post("/api/analyze", response_model=AnalyzeResponse)
def analyze(request: AnalyzeRequest):
    # One analyze call = one conversation: every file in this request is saved
    # with the same PromptId, so its rows can be found together later.
    prompt_id = uuid.uuid4().hex
    results = [_process_file(file, request.model, request.prompt, prompt_id) for file in request.files]
    print("DONE - returning", len(results), "result(s).")
    return AnalyzeResponse(results=results)


# GET /api/analyses - everything saved so far (newest first).
@app.get("/api/analyses")
def get_analyses():
    return database.get_all()


# POST /api/export - build the audit workbook for a date window and one
# document type, and send it back as a downloadable .xlsx file.
@app.post("/api/export")
def export(request: ExportRequest):
    # Check the inputs first; a clear 400 beats a confusing crash later.
    for value in (request.from_date, request.to_date):
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(400, "Both dates are needed, like 2026-07-22.")
    if request.from_date > request.to_date:
        raise HTTPException(400, "The from date must not be after the to date.")
    if request.doc_type not in excel_service.DOC_TYPE_LABELS:
        raise HTTPException(400, "Unknown document type: " + request.doc_type)

    print("EXPORT:", request.from_date, "to", request.to_date, "|", request.doc_type)
    try:
        rows = database.get_filtered(request.from_date, request.to_date, request.doc_type)
    except Exception as ex:
        raise HTTPException(500, "The database could not be read: " + str(ex))

    # An empty window still returns a valid workbook (Total_Records = 0).
    content = excel_service.build_workbook(
        rows, request.from_date, request.to_date, request.doc_type
    )
    filename = "Audit_" + date.today().strftime("%d%m%Y") + ".xlsx"
    print("EXPORT: sending", filename, "with", len(rows), "record(s).")
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="' + filename + '"'},
    )
