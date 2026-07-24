import json
import os
import re

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


def parse_ai_response_to_json(answer_text: str) -> dict | None:
    """Try to parse the AI answer into a JSON-like dict.

    1) If the answer is valid JSON, return it.
    2) Else, look for simple key: value lines and build a dict.
    Returns None if parsing fails or no pairs found.
    """
    if not answer_text:
        return None
    # Try direct JSON
    try:
        parsed = json.loads(answer_text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    # Fallback: parse lines like "Key: Value"
    lines = [l.strip() for l in answer_text.splitlines() if l.strip()]
    kv = {}
    for line in lines:
        # Accept separators :, -, =
        m = re.split(r"\s*[:=\-]\s*", line, maxsplit=1)
        if len(m) == 2:
            raw_k, raw_v = m[0].strip(), m[1].strip()
            # Normalize key: remove non-alphanum, capitalize parts -> CamelCase
            parts = re.findall(r"[A-Za-z0-9]+", raw_k)
            if not parts:
                continue
            key = "".join(p.capitalize() for p in parts)
            kv[key] = raw_v

    return kv if kv else None


def generate_ai_answer_json(answer_text: str, prompt: str | None = None) -> dict | None:
    """Generate structured JSON from the AI answer based on the prompt.

    This function calls the AI again to structure the answer as JSON based on
    what the prompt asks for. For example:
    - Prompt: "Tell me vendor name, GST and items"
    - Answer: "The vendor is ABC Corp, GST is 18%, items are Item1, Item2"
    - Structured JSON: {"vendor_name": "ABC Corp", "gst": "18%", "items": ["Item1", "Item2"]}

    Returns a dict with structured data, or None if structuring fails.
    """
    if not answer_text or not prompt:
        return None

    try:
        # Ask the AI to structure the answer around what the prompt requested.
        structure_prompt = f"""Based on this question: "{prompt}"

And this answer: "{answer_text}"

Extract the specific information requested in the question and return it as a JSON object.
Use keys that match what was asked (convert to snake_case or camelCase).
If the question asks for multiple items (like 'items', 'products', 'lines'), put them in an array.
Return ONLY valid JSON, no other text."""

        response = get_client().chat.completions.create(
            model=get_deployment(),
            messages=[
                {"role": "system", "content": "You are a JSON extraction assistant. Return only valid JSON objects."},
                {"role": "user", "content": structure_prompt},
            ],
        )

        json_text = response.choices[0].message.content or ""
        try:
            structured = json.loads(json_text)
            if isinstance(structured, dict):
                return structured
        except json.JSONDecodeError:
            pass

        # Fallback: best-effort parsing of the original answer.
        parsed = parse_ai_response_to_json(answer_text)
        return parsed if parsed else {"answer": answer_text}

    except Exception as ex:
        print(f"WARNING: could not structure AI answer to JSON: {ex}")
        # Last resort: return the raw answer
        return {"answer": answer_text} if answer_text else None
