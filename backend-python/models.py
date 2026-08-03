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
