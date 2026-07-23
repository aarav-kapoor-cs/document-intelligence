import os

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeDocumentRequest, DocumentAnalysisFeature
from azure.core.credentials import AzureKeyCredential

# Reads text and fields from a file using Azure Document Intelligence.
# The model_id decides how the file is read:
#   prebuilt-layout    -> text + general key-value pairs
#   prebuilt-invoice   -> invoice fields (vendor, total, line items, ...)
#   prebuilt-receipt   -> receipt fields
#   prebuilt-idDocument-> ID fields (name, date of birth, ...)


def _field_to_json(field):
    """Convert one Azure field into plain JSON: objects become dicts, arrays
    become lists, and everything else becomes its text value. Every level -
    the field itself, each line item, and each cell inside it - is wrapped as
    {"value": ..., "confidence": ...}, so the app can show how sure Azure was
    about every single value, not just the field as a whole."""
    if field.type == "array":
        value = [_field_to_json(item) for item in field.value_array or []]
    elif field.type == "object":
        value = {name: _field_to_json(sub) for name, sub in (field.value_object or {}).items()}
    else:
        value = _leaf_text(field)
    return {"value": value, "confidence": field.confidence}


def _leaf_text(field):
    """The text read from the page - or, when Azure normalizes a value (totals,
    dates, addresses, ...) without page text, the typed value, so nothing is lost."""
    if field.content:
        return field.content.strip()

    currency = field.value_currency
    if currency is not None and currency.amount is not None:
        symbol = currency.currency_symbol or currency.currency_code or ""
        return (str(symbol) + str(currency.amount)).strip()

    address = field.value_address
    if address is not None:
        parts = (address.street_address, address.city, address.state,
                 address.postal_code, address.country_region)
        return ", ".join(str(part) for part in parts if part)

    for value in (field.value_string, field.value_number, field.value_integer,
                  field.value_date, field.value_time, field.value_phone_number,
                  field.value_boolean, field.value_country_region,
                  field.value_selection_mark):
        if value is not None:
            return str(value)

    return ""


# Give it the model id and the file bytes; get back (text, fields), where fields
# maps each field name to {"value": ..., "confidence": ...} - the value is
# JSON-ready and the confidence is Azure's 0-1 score (or None if not given).
# Nested values (e.g. each invoice line item and each cell in it) carry their
# own {"value", "confidence"} wrapper too.
def analyze(model_id, file_bytes):
    endpoint = os.getenv("DOC_INTELLIGENCE_ENDPOINT", "")
    key = os.getenv("DOC_INTELLIGENCE_KEY", "")

    # If OCR is not set up yet (endpoint is still a placeholder), return empty
    # results so the app still runs.
    if not endpoint.startswith("http"):
        print("OCR: not configured (endpoint is a placeholder) - returning empty text.")
        return "", {}

    client = DocumentIntelligenceClient(endpoint=endpoint, credential=AzureKeyCredential(key))

    # Extra features: high-resolution OCR reads small or dense print more
    # accurately (better values and confidence, at a small extra cost per
    # page), and "keyValuePairs" only works with the layout model.
    features = [DocumentAnalysisFeature.OCR_HIGH_RESOLUTION]
    if model_id == "prebuilt-layout":
        features.append(DocumentAnalysisFeature.KEY_VALUE_PAIRS)

    print("OCR: reading the file with Azure Document Intelligence (model: " + model_id + ")...")
    poller = client.begin_analyze_document(
        model_id,
        AnalyzeDocumentRequest(bytes_source=file_bytes),
        features=features,
    )
    result = poller.result()

    text = result.content or ""
    fields = {}

    # Layout returns general key-value pairs; keep the first value seen per key.
    # The confidence says how sure Azure is that this key and value belong together.
    for pair in (result.key_value_pairs or []):
        pair_key = ((pair.key.content if pair.key else "") or "").strip()
        pair_value = ((pair.value.content if pair.value else "") or "").strip()
        if pair_key and pair_value and pair_key not in fields:
            fields[pair_key] = {"value": pair_value, "confidence": pair.confidence}

    # Prebuilt models (invoice, receipt, id) return named fields that may nest
    # (e.g. an invoice's line items) - keep that structure as nested JSON.
    # The confidence says how sure Azure is that the field was found and read right.
    for document in (result.documents or []):
        for name, field in (document.fields or {}).items():
            fields[name] = _field_to_json(field)

    print("OCR: done - " + str(len(text)) + " characters and " + str(len(fields)) + " field(s).")
    return text, fields
