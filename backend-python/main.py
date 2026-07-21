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
from models import AnalyzeRequest, AnalyzeResponse, FileResult

app = FastAPI()

# Allow the Angular app (http://localhost:4200) to call this API from the browser.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# The Document Intelligence models the dropdown can choose from.
MODELS = [
    {"id": "prebuilt-read", "label": "Read (OCR text)"},
    {"id": "prebuilt-layout", "label": "Layout (text + tables + key-value pairs)"},
    {"id": "prebuilt-invoice", "label": "Invoice"},
    {"id": "prebuilt-receipt", "label": "Receipt"},
    {"id": "prebuilt-idDocument", "label": "ID Document"},
]

# Make the database table when the app starts. If the database can't be reached
# yet (e.g. SQL Server isn't running or the connection string is wrong), don't
# crash — print a clear note and keep running so /api/models still works and the
# analyze step can report the exact problem.
try:
    database.ensure_table()
except Exception as ex:
    print("WARNING: could not prepare the database at startup:", ex)


# GET /api/models - the list for the dropdown.
@app.get("/api/models")
def get_models():
    return MODELS


# POST /api/analyze - read each file, ask the AI, save it, return the answers.
@app.post("/api/analyze", response_model=AnalyzeResponse)
def analyze(request: AnalyzeRequest):
    results = []
    for file in request.files:
        text = ""
        key_values = []
        answer_text = ""
        tokens = None

        try:
            # 1. Turn the base64 text back into the real file bytes.
            file_bytes = base64.b64decode(file.base64)
            # 2. Read the file with the chosen Document Intelligence model.
            text, key_values = ocr_service.analyze(request.model, file_bytes)
            # 3. If a prompt was given, ask the AI (and get the token counts).
            answer_text, tokens = ai_service.answer(request.prompt, text)
        except Exception as ex:
            # If OCR or the AI failed (e.g. a wrong key), show the reason.
            answer_text = "ERROR: " + str(ex)

        # 4. Save everything to the database. If the database can't be reached
        # (e.g. SQL Server isn't running or the connection string is wrong),
        # show that clearly instead of failing the whole request.
        try:
            database.save(
                file.name,
                request.model,
                request.prompt,
                text,
                json.dumps([kv.model_dump() for kv in key_values]),
                answer_text,
                tokens.prompt_tokens if tokens else 0,
                tokens.completion_tokens if tokens else 0,
                tokens.total_tokens if tokens else 0,
                datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            )
        except Exception as ex:
            answer_text = (answer_text + "\n\n" if answer_text else "") + \
                "ERROR saving to the database: " + str(ex)

        results.append(FileResult(
            file_name=file.name,
            model=request.model,
            text=text,
            key_values=key_values,
            answer=answer_text,
            tokens=tokens,
        ))

    return AnalyzeResponse(results=results)


# GET /api/analyses - everything saved so far (newest first).
@app.get("/api/analyses")
def get_analyses():
    return database.get_all()
