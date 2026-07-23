"""Document-type checker backed by Azure OpenAI.

Classifies a document's extracted text into one of four types and confirms it
matches the Document Intelligence model the user selected.
"""

from typing import Optional, Tuple

from ai_service import get_client, get_deployment, is_configured

# The document type each Document Intelligence model expects. Layout only
# accepts general documents - an invoice, receipt, or ID uploaded under Layout
# is rejected so the user picks the model made for it.
MODEL_EXPECTED_TYPE = {
    "prebuilt-layout": "general_document",
    "prebuilt-invoice": "invoice",
    "prebuilt-receipt": "receipt",
    "prebuilt-idDocument": "identity_document",
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
    if not is_configured() or not document_text:
        print("CHECKER: skipped (Azure OpenAI not configured or no text).")
        return None

    print("CHECKER: calling Azure OpenAI to classify the document...")
    response = get_client().chat.completions.create(
        model=get_deployment(),
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
    expected = MODEL_EXPECTED_TYPE.get(model_id)
    if expected is None:
        return True, ""  # Unknown model: nothing to compare against.

    detected = classify(document_text)
    if detected is None or detected == expected:
        print("CHECKER: OK - document accepted for the '" + model_id + "' model.")
        return True, ""

    message = (
        "Document type mismatch: the selected model expects '" + expected
        + "', but this file appears to be '" + detected + "'. "
        + "Please choose the correct document type and try again."
    )
    print("CHECKER: MISMATCH -", message)
    return False, message
