import os

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeDocumentRequest, DocumentAnalysisFeature
from azure.core.credentials import AzureKeyCredential

from models import KeyValue

# Reads text and key-value pairs from a file using Azure Document Intelligence.
# The model_id decides how the file is read:
#   prebuilt-read      -> just the text (OCR)
#   prebuilt-layout    -> text + general key-value pairs
#   prebuilt-invoice   -> invoice fields (vendor, total, ...)
#   prebuilt-receipt   -> receipt fields
#   prebuilt-idDocument-> ID fields (name, date of birth, ...)


# Give it the model id and the file bytes; get back (text, list of KeyValue).
def analyze(model_id, file_bytes):
    endpoint = os.getenv("DOC_INTELLIGENCE_ENDPOINT", "")
    key = os.getenv("DOC_INTELLIGENCE_KEY", "")

    # If OCR is not set up yet (endpoint is still a placeholder), return empty results
    # so the app still runs.
    if not endpoint.startswith("http"):
        return "", []

    client = DocumentIntelligenceClient(endpoint=endpoint, credential=AzureKeyCredential(key))

    # "keyValuePairs" is an extra feature that only works with the layout model.
    features = [DocumentAnalysisFeature.KEY_VALUE_PAIRS] if model_id == "prebuilt-layout" else None

    poller = client.begin_analyze_document(
        model_id,
        AnalyzeDocumentRequest(bytes_source=file_bytes),
        features=features,
    )
    result = poller.result()

    text = result.content or ""
    key_values = []

    # Layout returns general key-value pairs.
    for pair in (result.key_value_pairs or []):
        pair_key = pair.key.content if pair.key else ""
        pair_value = pair.value.content if pair.value else ""
        confidence = getattr(pair, "confidence", None) or 0.0
        key_values.append(KeyValue(key=pair_key, value=pair_value, confidence=float(confidence)))

    # Prebuilt models (invoice, receipt, id) return named fields.
    for document in (result.documents or []):
        for field_name, field in (document.fields or {}).items():
            field_value = getattr(field, "content", None) or ""
            confidence = getattr(field, "confidence", None) or 0.0
            key_values.append(KeyValue(key=field_name, value=str(field_value), confidence=float(confidence)))

    return text, key_values
