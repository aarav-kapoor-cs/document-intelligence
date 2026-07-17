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


# One key-value pair found in the document, with Azure's confidence (0.0 - 1.0).
class KeyValue(BaseModel):
    key: str
    value: str
    confidence: float


# How many tokens the AI used.
class TokenUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


# What we send back for one file.
class FileResult(BaseModel):
    file_name: str
    model: str
    text: str = ""
    key_values: list[KeyValue] = []
    answer: str = ""
    tokens: TokenUsage | None = None


class AnalyzeResponse(BaseModel):
    results: list[FileResult] = []
