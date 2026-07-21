"""Document-type checker backed by Azure OpenAI.

Classifies a document's extracted text into one of four types and confirms it
matches the Document Intelligence model the user selected.
"""

import os
from typing import Optional, Tuple

from openai import AzureOpenAI

# The document type each Document Intelligence model expects.
# "Layout" is general-purpose, so it maps to the "general_document" type.
MODEL_EXPECTED_TYPE = {
    "prebuilt-invoice": "invoice",
    "prebuilt-receipt": "receipt",
    "prebuilt-idDocument": "identity_document",
    "prebuilt-layout": "general_document",
}

VALID_TYPES = ("invoice", "receipt", "identity_document", "general_document")

_SYSTEM_PROMPT = (
    "You are a document classification assistant.\n\n"
    "Analyze the uploaded document using its OCR text, headings, fields, tables, "
    "and overall purpose.\n\n"
    "Classify it into exactly one category:\n\n"
    "invoice: A document requesting payment, usually containing invoice number, seller "
    "and buyer details, items, taxes, due date, and amount due.\n"
    "receipt: Proof that payment or a transaction has already been completed, usually "
    "containing merchant details, purchased items, transaction date, payment method, "
    "and amount paid.\n"
    "identity_document: A document used to verify a person's identity, such as a passport, "
    "Aadhaar card, PAN card, driving licence, employee ID, or student ID.\n"
    "general_document: Any document that does not clearly fit the above categories, such "
    "as letters, reports, contracts, forms, or notes.\n\n"
    "Reply with exactly one category name: invoice, receipt, identity_document, or general_document."
)


def classify(document_text: str) -> Optional[str]:
    """Return the detected document type, or None if the check cannot run."""
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    key = os.getenv("AZURE_OPENAI_KEY", "")
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "")

    # Skip the check when Azure OpenAI is not configured or there is no text.
    if not endpoint.startswith("http") or not document_text:
        print("CHECKER: skipped (Azure OpenAI not configured or no text).")
        return None

    print("CHECKER: calling Azure OpenAI to classify the document...")
    # The Azure OpenAI Python SDK needs a version string; default it via the SDK's
    # own env var so you only ever set endpoint, key, and deployment.
    os.environ.setdefault("OPENAI_API_VERSION", "2024-10-21")
    client = AzureOpenAI(azure_endpoint=endpoint, api_key=key)
    response = client.chat.completions.create(
        model=deployment,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": document_text},
        ],
    )

    raw = (response.choices[0].message.content or "").strip().lower()
    # The model should reply with one category word, but be forgiving if it adds
    # punctuation or wraps it in a short sentence.
    detected = raw if raw in VALID_TYPES else next(
        (category for category in VALID_TYPES if category in raw), "general_document"
    )
    print("CHECKER: detected document type =", detected, "(raw reply:", repr(raw) + ")")
    return detected


def check(model_id: str, document_text: str) -> Tuple[bool, str]:
    """Confirm the document matches the chosen model.

    Returns (True, "") when it matches or cannot be checked, and
    (False, message) when the user selected the wrong document type.
    """
    detected = classify(document_text)
    if detected is None:
        return True, ""

    expected = MODEL_EXPECTED_TYPE.get(model_id, "general_document")
    if detected == expected:
        print("CHECKER: OK - document matches the '" + expected + "' model.")
        return True, ""

    message = (
        "Document type mismatch: the selected model expects '" + expected
        + "', but this file appears to be '" + detected + "'. "
        + "Please choose the correct document type and try again."
    )
    print("CHECKER: MISMATCH -", message)
    return False, message
