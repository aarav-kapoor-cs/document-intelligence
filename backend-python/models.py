from pydantic import BaseModel

# These are the "shapes" of the data, using Pydantic. FastAPI checks the incoming
# request against them and turns our responses into JSON automatically.


# One uploaded file: its name and its bytes turned into base64 text.
class FileInput(BaseModel):
    name: str
    base64: str


# What the Angular app sends us.
class AnalyzeRequest(BaseModel):
    model: str = "prebuilt-layout"  # which Document Intelligence model to run
    prompt: str = ""                # optional prompt for the AI
    files: list[FileInput] = []


# How many tokens the AI used.
class TokenUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


# What we send back for one file. `fields` holds the extracted data as
# structured JSON - it is saved to the database, not shown on screen. `error`
# is filled in when a step failed (wrong document type, AI not set up, ...).
class FileResult(BaseModel):
    file_name: str
    model: str
    text: str = ""
    fields: dict = {}
    answer: str = ""
    ai_answer_json: dict | None = None
    error: str = ""
    tokens: TokenUsage | None = None


class AnalyzeResponse(BaseModel):
    results: list[FileResult] = []


# What the Excel report page sends us: a date window and one document type.
class ExportRequest(BaseModel):
    from_date: str = ""  # "YYYY-MM-DD"
    to_date: str = ""    # "YYYY-MM-DD"
    doc_type: str = "prebuilt-invoice"


# --- the search explorer at /search ---

# One question, run through the retrieval pipeline. Empty grain or sheet means
# no filter. `answer` turns on the RAG step, which costs an extra model call.
# `top` is left unset on purpose: RAG needs far more context than plain
# retrieval does, so the endpoint fills it in the same way the CLI does.
class SearchRequest(BaseModel):
    question: str = ""
    grain: str = ""   # "document", "field", or "" for both
    sheet: str = ""   # a sheet name, or "" for all three
    top: int | None = None
    answer: bool = False


# The same question run four ways, so the methods can be compared side by side.
class CompareRequest(BaseModel):
    question: str = ""
    grain: str = "document"
    top: int = 5


# A question for the Semantic Kernel agent, which picks its own tools.
class AgentRequest(BaseModel):
    question: str = ""


# --- the anomaly detector ---

# One document's extracted fields, scored without saving anything. This is what
# the Document Intelligence page sends straight after an upload, so a file can be
# checked before it is ever exported. `fields` is a raw KeyValuesJson dict.
class AnomalyScoreRequest(BaseModel):
    fields: dict = {}
    doc_type: str = "prebuilt-invoice"


# One reason a document scored the way it did. `layer` says which of the three
# produced it - "rule" (deterministic), "robust_z" (median/MAD across the scan)
# or "model" (a feature's contribution to the trained probability) - because the
# three carry very different weight and should not read as interchangeable.
class AnomalySignal(BaseModel):
    name: str
    layer: str
    value: float = 0.0
    contribution: float = 0.0
    detail: str = ""


# One scored document. `features` is included so any number in `detail` can be
# checked against what it was computed from.
class AnomalyResult(BaseModel):
    document_id: int | None = None
    source_file: str = ""
    invoice_number: str = ""
    score: float = 0.0
    band: str = "Clean"          # Clean | Review | Suspect
    method: str = "rules_only"   # rules_only | logistic_regression
    signals: list[AnomalySignal] = []
    features: dict = {}


# A whole window, scored. `notes` carries the honest degradations - no model
# installed, too few documents for the corpus checks - in the same way
# /api/search/status does, so the page can say what it could not do.
class AnomalyScanResponse(BaseModel):
    from_date: str = ""
    to_date: str = ""
    doc_type: str = ""
    documents: int = 0
    corpus_n: int = 0
    corpus_checks_ran: bool = False
    method: str = "rules_only"
    model_version: str = ""
    results: list[AnomalyResult] = []
    notes: list[str] = []
