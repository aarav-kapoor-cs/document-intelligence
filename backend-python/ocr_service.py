import os

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeDocumentRequest, DocumentAnalysisFeature
from azure.core.credentials import AzureKeyCredential

from models import KeyValue

# Reads text and key-value pairs from a file using Azure Document Intelligence.
# The model_id decides how the file is read:
#   prebuilt-layout    -> text + general key-value pairs
#   prebuilt-invoice   -> invoice fields (vendor, total, line items, ...)
#   prebuilt-receipt   -> receipt fields
#   prebuilt-idDocument-> ID fields (name, date of birth, ...)


def _leaf_value(field):
    """Return the best human-readable value for a leaf field.

    Azure usually fills in `content`; when it doesn't (some normalized fields such
    as totals or dates), fall back to the typed value so nothing is dropped.
    """
    content = getattr(field, "content", None)
    if content and content.strip():
        return content.strip()

    if getattr(field, "type", None) == "currency":
        currency = getattr(field, "value_currency", None)
        amount = getattr(currency, "amount", None) if currency else None
        if amount is not None:
            symbol = getattr(currency, "currency_symbol", "") or getattr(currency, "currency_code", "") or ""
            return (str(symbol) + str(amount)).strip()

    if getattr(field, "type", None) == "address":
        address = getattr(field, "value_address", None)
        if address is not None:
            parts = [getattr(address, part, None) for part in
                     ("street_address", "city", "state", "postal_code", "country_region")]
            joined = ", ".join(str(part) for part in parts if part)
            if joined:
                return joined

    for attr in ("value_string", "value_number", "value_integer", "value_date",
                 "value_time", "value_phone_number", "value_boolean",
                 "value_country_region", "value_selection_mark"):
        value = getattr(field, attr, None)
        if value is not None:
            return str(value)

    return ""


def _flatten_field(name, field, out):
    """Turn one (possibly nested) prebuilt field into flat KeyValue rows.

    Prebuilt models return fields that can nest: an invoice's "Items" is an array
    of objects (each line item), an address is an object, and so on. Walking that
    tree to the leaves means nothing is lost (e.g. line items) and containers are
    never dumped as one unreadable blob.
    """
    field_type = getattr(field, "type", None)

    if field_type == "array":
        for index, item in enumerate(field.value_array or [], start=1):
            _flatten_field(f"{name}[{index}]", item, out)
        return

    if field_type == "object":
        for sub_name, sub_field in (field.value_object or {}).items():
            _flatten_field(f"{name}.{sub_name}", sub_field, out)
        return

    # Leaf: show the value Azure extracted, with its confidence.
    value = _leaf_value(field)
    if value:
        confidence = float(getattr(field, "confidence", None) or 0.0)
        out.append(KeyValue(key=name, value=value, confidence=confidence))


def _dedupe(key_values):
    """Drop duplicate rows, keeping the first time each (key, value) is seen."""
    seen = set()
    unique = []
    for kv in key_values:
        signature = (kv.key, kv.value)
        if signature not in seen:
            seen.add(signature)
            unique.append(kv)
    return unique


# Give it the model id and the file bytes; get back (text, list of KeyValue).
def analyze(model_id, file_bytes):
    endpoint = os.getenv("DOC_INTELLIGENCE_ENDPOINT", "")
    key = os.getenv("DOC_INTELLIGENCE_KEY", "")

    # If OCR is not set up yet (endpoint is still a placeholder), return empty
    # results so the app still runs.
    if not endpoint.startswith("http"):
        print("OCR: not configured (endpoint is a placeholder) - returning empty text.")
        return "", []

    client = DocumentIntelligenceClient(endpoint=endpoint, credential=AzureKeyCredential(key))

    # "keyValuePairs" is an extra feature that only works with the layout model.
    features = [DocumentAnalysisFeature.KEY_VALUE_PAIRS] if model_id == "prebuilt-layout" else None

    print("OCR: reading the file with Azure Document Intelligence (model: " + model_id + ")...")
    poller = client.begin_analyze_document(
        model_id,
        AnalyzeDocumentRequest(bytes_source=file_bytes),
        features=features,
    )
    result = poller.result()

    text = result.content or ""
    key_values = []

    # Layout returns general key-value pairs (already a flat list).
    for pair in (result.key_value_pairs or []):
        pair_key = ((pair.key.content if pair.key else "") or "").strip()
        pair_value = ((pair.value.content if pair.value else "") or "").strip()
        # Skip blank rows so every displayed row is a real key -> value.
        if not pair_value:
            continue
        confidence = float(getattr(pair, "confidence", None) or 0.0)
        key_values.append(KeyValue(key=pair_key, value=pair_value, confidence=confidence))

    # Prebuilt models (invoice, receipt, id) return named fields that may be nested.
    for document in (result.documents or []):
        for field_name, field in (document.fields or {}).items():
            _flatten_field(field_name, field, key_values)

    key_values = _dedupe(key_values)
    print("OCR: done - " + str(len(text)) + " characters and " + str(len(key_values)) + " field(s).")
    return text, key_values
