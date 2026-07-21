import base64
import json
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

# Load the keys from the .env file (next to this file), before anything uses them.
load_dotenv(Path(__file__).with_name(".env"))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import database
import ocr_service
import ai_service
import checker_service
from models import AnalyzeRequest, AnalyzeResponse, FileResult

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


def _save(file_name, model, prompt, text, key_values, answer, tokens):
    """Insert one result row into the database."""
    print("DATABASE: saving the result for", file_name, "...")
    database.save(
        file_name,
        model,
        prompt,
        text,
        json.dumps([kv.model_dump() for kv in key_values]),
        answer,
        tokens.prompt_tokens if tokens else 0,
        tokens.completion_tokens if tokens else 0,
        tokens.total_tokens if tokens else 0,
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    )
    print("DATABASE: saved.")


def _process_file(file, model, prompt):
    """Run one file through OCR, the type checker, the AI, and the database."""
    print("PROCESSING FILE:", file.name, "| model:", model)

    text = ""
    key_values = []
    answer = ""
    tokens = None

    try:
        file_bytes = base64.b64decode(file.base64)
        text, key_values = ocr_service.analyze(model, file_bytes)

        # Confirm the file matches the chosen document type before going further.
        matches, message = checker_service.check(model, text)
        if not matches:
            # Wrong type: stop here - no AI answer, and nothing is saved.
            return FileResult(
                file_name=file.name,
                model=model,
                text=text,
                key_values=key_values,
                answer="ERROR: " + message,
            )

        answer, tokens = ai_service.answer(prompt, text)
    except Exception as ex:
        # OCR or the AI failed (e.g. a wrong key). Report the reason.
        print("ERROR while processing", file.name, "-", ex)
        answer = "ERROR: " + str(ex)

    # Save the result. A database problem is reported but never loses the answer.
    try:
        _save(file.name, model, prompt, text, key_values, answer, tokens)
    except Exception as ex:
        print("ERROR saving to the database:", ex)
        answer = (answer + "\n\n" if answer else "") + "ERROR saving to the database: " + str(ex)

    return FileResult(
        file_name=file.name,
        model=model,
        text=text,
        key_values=key_values,
        answer=answer,
        tokens=tokens,
    )


# POST /api/analyze - read each file, check its type, ask the AI, and save it.
@app.post("/api/analyze", response_model=AnalyzeResponse)
def analyze(request: AnalyzeRequest):
    results = [_process_file(file, request.model, request.prompt) for file in request.files]
    print("DONE - returning", len(results), "result(s).")
    return AnalyzeResponse(results=results)


# GET /api/analyses - everything saved so far (newest first).
@app.get("/api/analyses")
def get_analyses():
    return database.get_all()
