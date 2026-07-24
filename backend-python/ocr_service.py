"""Azure Document Intelligence OCR.

Models:
  prebuilt-invoice    -> invoice fields (vendor, totals, tax ids, line items)
  prebuilt-receipt    -> receipt fields
  prebuilt-idDocument -> ID fields
  prebuilt-layout     -> raw text + general key-value pairs
"""
import os
import re

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeDocumentRequest, DocumentAnalysisFeature
from azure.core.credentials import AzureKeyCredential

GSTIN_RE = re.compile(r"[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]")
PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
VENDOR_TAX_KEYS = ("VendorGSTIN", "VendorTaxId")


def _alnum(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())


def _leaf_text(field) -> str:
    """Page text first; fall back to Azure's typed value so nothing is dropped."""
    if field.content:
        return field.content.strip()

    currency = field.value_currency
    if currency is not None and currency.amount is not None:
        symbol = currency.currency_symbol or currency.currency_code or ""
        return f"{symbol}{currency.amount}".strip()

    address = field.value_address
    if address is not None:
        street = getattr(address, "street_address", None) or " ".join(
            str(part) for part in (
                getattr(address, "house_number", None),
                getattr(address, "road", None),
            ) if part
        )
        parts = (
            getattr(address, "po_box", None), street or None,
            getattr(address, "unit", None), getattr(address, "city_district", None),
            getattr(address, "city", None), getattr(address, "state", None),
            getattr(address, "postal_code", None), getattr(address, "country_region", None),
        )
        joined = ", ".join(str(part) for part in parts if part)
        if joined:
            return joined

    for value in (
        field.value_string, field.value_number, field.value_integer,
        field.value_date, field.value_time, field.value_phone_number,
        field.value_boolean, field.value_country_region, field.value_selection_mark,
    ):
        if value is not None:
            return str(value)
    return ""


def _field_to_json(field):
    if field.type == "array":
        value = [_field_to_json(item) for item in field.value_array or []]
    elif field.type == "object":
        value = {name: _field_to_json(sub) for name, sub in (field.value_object or {}).items()}
    else:
        value = _leaf_text(field)
    return {"value": value, "confidence": field.confidence}


def _fix_vendor_gstin(fields, document_text):
    """If Azure stored only a PAN in VendorTaxId, replace it with the matching GSTIN from OCR text.

    A GSTIN embeds its PAN at characters 3-12 (e.g. 27ABICX1218R1ZX). Only a GSTIN
    that embeds that exact PAN is accepted — never the customer's GSTIN.
    """
    for key in VENDOR_TAX_KEYS:
        field = fields.get(key)
        if not isinstance(field, dict):
            continue

        raw = _alnum(str(field.get("value") or ""))
        # Already a full GSTIN, or a GSTIN buried in the field text — keep/use it.
        embedded = GSTIN_RE.findall(raw)
        if embedded:
            fields[key] = {**field, "value": embedded[0]}
            return
        if not PAN_RE.fullmatch(raw):
            continue

        matches = {
            gstin for gstin in GSTIN_RE.findall(_alnum(document_text))
            if gstin[2:12] == raw
        }
        if len(matches) == 1:
            gstin = matches.pop()
            fields[key] = {**field, "value": gstin}
            print(f"OCR: upgraded {key} PAN {raw} -> GSTIN {gstin}")
        return


def analyze(model_id, file_bytes):
    """Run OCR. Returns (full_page_text, fields) where each field is {value, confidence}."""
    endpoint = os.getenv("DOC_INTELLIGENCE_ENDPOINT", "")
    key = os.getenv("DOC_INTELLIGENCE_KEY", "")
    if not endpoint.startswith("http"):
        print("OCR: not configured — returning empty result.")
        return "", {}

    client = DocumentIntelligenceClient(endpoint=endpoint, credential=AzureKeyCredential(key))
    features = [DocumentAnalysisFeature.OCR_HIGH_RESOLUTION]
    if model_id == "prebuilt-layout":
        features.append(DocumentAnalysisFeature.KEY_VALUE_PAIRS)

    print(f"OCR: analyzing with {model_id}...")
    result = client.begin_analyze_document(
        model_id,
        AnalyzeDocumentRequest(bytes_source=file_bytes),
        features=features,
    ).result()

    text = result.content or ""
    fields = {}

    # Layout key-value pairs (general documents).
    for pair in (result.key_value_pairs or []):
        pair_key = ((pair.key.content if pair.key else "") or "").strip()
        pair_value = ((pair.value.content if pair.value else "") or "").strip()
        if pair_key and pair_value and pair_key not in fields:
            fields[pair_key] = {"value": pair_value, "confidence": pair.confidence}

    # Structured fields from invoice / receipt / id models.
    for document in (result.documents or []):
        for name, field in (document.fields or {}).items():
            fields[name] = _field_to_json(field)

    if model_id == "prebuilt-invoice":
        _fix_vendor_gstin(fields, text)

    print(f"OCR: done — {len(text)} chars, {len(fields)} field(s).")
    return text, fields
