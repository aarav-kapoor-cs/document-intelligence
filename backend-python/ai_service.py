import json
import os

from openai import OpenAI

from models import TokenUsage

# Sends the Document Intelligence results (text + extracted fields) and your
# question to Azure OpenAI, and returns the answer plus how many tokens were
# used (input, output, total).
#
# The client below is shared with the document-type checker. It talks to the
# v1 endpoint (AZURE_OPENAI_BASE_URL ends with /openai/v1), so no api-version
# is needed anywhere.


def is_configured():
    """True once AZURE_OPENAI_BASE_URL is filled in with a real URL."""
    return os.getenv("AZURE_OPENAI_BASE_URL", "").startswith("http")


def get_client():
    base_url = os.getenv("AZURE_OPENAI_BASE_URL", "").rstrip("/")
    if not base_url.endswith("/openai/v1"):
        base_url += "/openai/v1"
    return OpenAI(base_url=base_url, api_key=os.getenv("AZURE_OPENAI_KEY", ""))


def get_deployment():
    return os.getenv("AZURE_OPENAI_DEPLOYMENT", "")

def _values_only(node):
    """Strip the {"value", "confidence"} wrappers at every level (fields, line
    items, cells), leaving just the extracted values for the prompt."""
    if isinstance(node, dict) and "value" in node and "confidence" in node:
        node = node["value"]
    if isinstance(node, dict):
        return {name: _values_only(sub) for name, sub in node.items()}
    if isinstance(node, list):
        return [_values_only(item) for item in node]
    return node


_SYSTEM_PROMPT = (
    "You answer questions about a document. Your only source of information is "
    "the content extracted from it by Azure Document Intelligence: the document "
    "text and, when available, the extracted fields. Base every answer strictly "
    "on that content, preferring the extracted fields for values such as totals, "
    "dates, and names. If the answer is not in the extracted content, say so "
    "clearly - never guess or use outside knowledge."
)


# Returns (answer_text, TokenUsage or None).
def answer(prompt, document_text, fields=None):
    # No prompt typed -> nothing to answer.
    if not prompt:
        print("AI: skipped (no prompt was typed).")
        return "", None

    # A question was asked but the AI is not set up -> fail loudly instead of
    # silently showing nothing.
    if not is_configured():
        raise RuntimeError(
            "Azure OpenAI is not configured - fill in AZURE_OPENAI_BASE_URL, "
            "AZURE_OPENAI_KEY and AZURE_OPENAI_DEPLOYMENT in backend-python/.env."
        )

    # Give the AI everything Document Intelligence extracted: the full text,
    # plus the structured fields when a prebuilt model returned any. Only the
    # values go into the prompt - the confidence scores are for the UI.
    user_message = "Document text:\n" + document_text
    if fields:
        field_values = _values_only(fields)
        user_message += "\n\nExtracted fields (JSON):\n" + json.dumps(field_values, indent=2)
    user_message += "\n\nQuestion: " + prompt

    print("AI: calling Azure OpenAI to answer the prompt...")
    response = get_client().chat.completions.create(
        model=get_deployment(),
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
    )

    answer_text = response.choices[0].message.content or ""
    print("AI: done -", response.usage.total_tokens, "tokens used.")
    tokens = TokenUsage(
        prompt_tokens=response.usage.prompt_tokens,          # input tokens
        completion_tokens=response.usage.completion_tokens,  # output tokens
        total_tokens=response.usage.total_tokens,
    )
    return answer_text, tokens
