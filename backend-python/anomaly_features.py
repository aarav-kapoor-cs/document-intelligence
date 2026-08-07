"""Turn one document's KeyValuesJson into a fixed vector of numbers.

This module is deliberately the boring one: it computes, it does not judge.
Every threshold in the anomaly detector lives in anomaly_rules.py, so that
tuning "how big a mismatch counts" never means editing the thing that feeds the
trained model. What is produced here has to mean exactly the same on the office
laptop as it did inside the Azure ML training job, or the exported coefficients
are being applied to a different feature than the one they were fitted to.

Two facts about the source data shape everything below.

First, confidence dies early everywhere else. Azure Document Intelligence
returns a confidence for every field, and it is stored - but excel_service._plain()
unwraps {"value", "confidence"} down to the bare value, so the extraction log,
the workbook and the search chunks never see it. It is the single richest signal
the pipeline already collects and throws away, so _confidences() below reads the
wrappers BEFORE _plain gets to them.

Second, there are three wrapper shapes in the real database, not one:

    Ids 1-8     bare scalars, no confidence at all      "InvoiceTotal": "$93.50"
    Ids 9-27    top-level fields wrapped, but arrays    {"value": "...", "confidence": 0.94}
                hold bare dicts inside                  {"value": [ {...} ], "confidence": null}
    Id 24 only  fully nested, per-row and per-cell

Anything reading confidence has to survive all three, and legacy rows must
report "no confidence recorded" rather than a confident-looking zero - hence the
conf_present flag rather than silently imputing 0.0.

Values are read through the existing excel_service helpers so that a number here
and the same number in the audit workbook can never disagree.
"""

import math
from datetime import date

import excel_service
import keyword_search

# The feature order IS the contract. anomaly_model.json stores these names and
# refuses to load against a different list, because the exported coefficients are
# positional: inserting a feature here without retraining would silently score
# every document against the wrong weights.
FEATURE_NAMES = (
    # -- completeness and extraction confidence --
    "core_missing_count",
    "core_invalid_count",
    "field_count",
    "conf_present",
    "conf_min",
    "conf_mean",
    "conf_core_min",
    "conf_below_080_frac",
    # -- amounts that should reconcile --
    "totals_known",
    "total_residual_abs",
    "total_residual_rel",
    "total_residual_over_rupee",
    "item_sum_residual_rel",
    "items_count",
    "total_magnitude_log",
    # -- vendor and customer identity --
    "vendor_gstin_present",
    "vendor_gstin_shape_ok",
    "vendor_gstin_checksum_ok",
    "pan_instead_of_gstin",
    "customer_gstin_present",
    "self_billing",
    # -- dates --
    "invoice_date_parsed",
    "invoice_date_future_days",
    "due_before_invoice_days",
)

# The same 0.80 the Angular app already highlights at (app.ts: isLowConfidence).
# One number, one meaning - a second threshold here would mean the table and the
# anomaly score disagreed about which fields were weak.
LOW_CONFIDENCE = 0.80

# GSTIN check digit alphabet: 0-9 then A-Z, valued 0-35.
GSTIN_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# Where the totals live. Receipts name them differently from invoices, and the
# feature builder should not care which model produced the record.
SUBTOTAL_KEYS = ("SubTotal", "Subtotal", "Sub Total", "BaseAmount", "TaxableValue")
TAX_KEYS = ("TotalTax", "TaxAmount", "GSTAmount", "GST Amount")
TOTAL_KEYS = ("InvoiceTotal", "Total", "GrandTotal", "AmountDue", "TotalAmount")
ITEMS_KEYS = ("Items", "LineItems", "Line Items")
INVOICE_DATE_KEYS = ("InvoiceDate", "Invoice Date", "TransactionDate", "Date")
DUE_DATE_KEYS = ("DueDate", "Due Date")


def gstin_check_digit(gstin: str) -> str:
    """The 15th character a GSTIN should carry, by the official mod-36 rule.

    Each of the first 14 characters is valued 0-35, multiplied by 1 or 2
    alternately, and the quotient and remainder of that product over 36 are both
    added to a running total. The check digit makes the total a multiple of 36.

    This is a real check the audit workbook cannot do: excel_service.is_valid_gstin
    tests the SHAPE only, so a GSTIN with one character mistyped or misread still
    passes and shows Validity_Flag = Pass. Note that this function is used only
    here - changing is_valid_gstin would change the workbook, which must stay a
    verbatim mirror of the mentor's template.
    """
    total = 0
    for position, character in enumerate(gstin[:14]):
        product = GSTIN_ALPHABET.index(character) * (2 if position % 2 else 1)
        total += product // 36 + product % 36
    return GSTIN_ALPHABET[(36 - total % 36) % 36]


def gstin_checksum_ok(value: str) -> bool:
    """True when the value is a well-shaped GSTIN whose check digit also agrees."""
    normalized = excel_service._normalize_tax_id(value)
    if not excel_service.is_valid_gstin(normalized):
        return False
    return gstin_check_digit(normalized) == normalized[14]


def _confidences(fields: dict) -> dict:
    """Top-level field name -> confidence, for the fields that recorded one.

    Deliberately top-level only. Per-line-item confidence exists on exactly one
    of the 27 real records, so a feature built on it would be missing almost
    everywhere and would mostly measure which OCR run produced the row.

    Fields with no confidence are absent from the result rather than present with
    a zero, so "Azure was unsure" stays distinguishable from "this row predates
    confidence being stored".
    """
    found = {}
    if not isinstance(fields, dict):
        return found
    for key, node in fields.items():
        if isinstance(node, dict) and "confidence" in node:
            score = node.get("confidence")
            if isinstance(score, (int, float)):
                found[key] = float(score)
    return found


def _first(fields: dict, keys) -> str:
    """The readable text of the first of these keys that holds anything."""
    for key in keys:
        value = excel_service._find_case_insensitive(fields, key)
        if value is None:
            continue
        text = excel_service._cell_text(value).strip()
        if text:
            return text
    return ""


def _number(fields: dict, keys):
    """The first of these keys that parses as a number, or None."""
    text = _first(fields, keys)
    if not text:
        return None
    try:
        return excel_service._numeric_value(text)
    except (TypeError, ValueError):
        return None


def _raw_vendor_tax_id(fields: dict) -> str:
    """The vendor tax id exactly as extracted, before any PAN->GSTIN recovery.

    resolve_vendor_gstin() repairs a PAN back into the full GSTIN using a sibling
    record, which is right for the workbook and wrong here: the repair is
    precisely the anomaly worth reporting, so it has to stay visible.
    """
    for key in excel_service.GSTIN_KEYS:
        value = excel_service._find_case_insensitive(fields, key)
        if value is None:
            continue
        text = excel_service._normalize_tax_id(excel_service._cell_text(value))
        if text:
            return text
    return ""


def _core_field_values(fields: dict) -> dict:
    """Core field reference name -> (raw key, text), for the ones present.

    keyword_search.CORE_FIELDS is written in reference-name space ("Vendor GSTIN")
    while KeyValuesJson uses Azure's raw keys ("VendorTaxId"), so the mapping goes
    through excel_service.reference_name - the same translation the extraction log
    performs, so "missing" means the same thing in both places.
    """
    present = {}
    for raw_key, node in fields.items():
        name = excel_service.reference_name(str(raw_key))
        if name in keyword_search.CORE_FIELDS:
            present.setdefault(name, (str(raw_key), excel_service._cell_text(node)))
    return present


def _items(fields: dict) -> list:
    """The line items as plain dicts, whichever wrapper shape they arrived in."""
    for key in ITEMS_KEYS:
        value = excel_service._find_case_insensitive(fields, key)
        if value is None:
            continue
        plain = excel_service._plain(value)
        if isinstance(plain, list):
            return [item for item in plain if isinstance(item, dict)]
    return []


def _item_amount_sum(items: list):
    """Sum of the line-item amounts, or None when none of them parse."""
    total = 0.0
    parsed = 0
    for item in items:
        for key in ("Amount", "TotalPrice", "LineTotal"):
            if key not in item:
                continue
            try:
                total += excel_service._numeric_value(item[key])
                parsed += 1
            except (TypeError, ValueError):
                pass
            break
    return total if parsed else None


def build(fields) -> tuple[dict, dict]:
    """One document's KeyValuesJson -> (features, context).

    `features` is exactly FEATURE_NAMES, all floats, ready for the model.
    `context` carries the raw values the rules need to write a human sentence
    (and to compare against a configurable threshold) - keeping them out of the
    feature dict is what lets this module stay free of thresholds.
    """
    fields = excel_service.parse_key_values_json(fields)
    confidences = _confidences(fields)
    core_present = _core_field_values(fields)

    # -- completeness and confidence ------------------------------------------
    core_missing = [name for name in keyword_search.CORE_FIELDS
                    if name not in core_present
                    or excel_service.is_blank(core_present[name][1])]
    core_invalid = [
        name for name, (raw_key, text) in core_present.items()
        if not excel_service.is_blank(text)
        and not excel_service.is_valid_field_value(raw_key, text)
    ]

    scores = list(confidences.values())
    core_scores = [confidences[raw_key] for _name, (raw_key, _text) in core_present.items()
                   if raw_key in confidences]

    # -- amounts ---------------------------------------------------------------
    subtotal = _number(fields, SUBTOTAL_KEYS)
    tax = _number(fields, TAX_KEYS)
    total = _number(fields, TOTAL_KEYS)
    totals_known = None not in (subtotal, tax, total)

    residual = abs(subtotal + tax - total) if totals_known else 0.0
    # Relative, because the corpus spans a 93 rupee invoice and a 46,273 rupee
    # one: an absolute gap means something different at each end.
    residual_rel = residual / max(abs(total), 1.0) if totals_known else 0.0

    items = _items(fields)
    item_sum = _item_amount_sum(items)
    if item_sum is not None and total is not None:
        # Compared against BOTH bases on purpose. On real invoices the line
        # Amount is sometimes tax-exclusive (sums to SubTotal) and sometimes
        # tax-inclusive (sums to InvoiceTotal); insisting on SubTotal alone flags
        # perfectly good invoices, so the nearer of the two is what counts.
        against_subtotal = abs(item_sum - subtotal) if subtotal is not None else None
        against_total = abs(item_sum - total)
        gap = min(x for x in (against_subtotal, against_total) if x is not None)
        item_sum_rel = gap / max(abs(total), 1.0)
    else:
        item_sum_rel = 0.0

    # -- identity --------------------------------------------------------------
    raw_tax_id = _raw_vendor_tax_id(fields)
    vendor_gstin = excel_service.get_vendor_gstin_from_key_values_json(fields)
    customer_gstin = excel_service.get_customer_gstin_from_key_values_json(fields)
    pan_instead = bool(raw_tax_id) and excel_service.is_pan(raw_tax_id) \
        and not excel_service.is_valid_gstin(raw_tax_id)

    # -- dates -----------------------------------------------------------------
    invoice_date = excel_service.parse_date(_first(fields, INVOICE_DATE_KEYS))
    due_date = excel_service.parse_date(_first(fields, DUE_DATE_KEYS))
    today = date.today()
    future_days = max(0, (invoice_date - today).days) if invoice_date else 0
    due_gap = 0
    if invoice_date and due_date:
        due_gap = max(0, (invoice_date - due_date).days)

    features = {
        "core_missing_count": float(len(core_missing)),
        "core_invalid_count": float(len(core_invalid)),
        "field_count": float(len(fields)),
        "conf_present": 1.0 if scores else 0.0,
        # Imputed to 1.0 (i.e. "nothing to worry about") when absent, so a legacy
        # row without confidence does not read as a low-confidence one. conf_present
        # is what tells the model to discount these three features entirely.
        "conf_min": float(min(scores)) if scores else 1.0,
        "conf_mean": float(sum(scores) / len(scores)) if scores else 1.0,
        # Restricted to core fields because 0.296 on CustomerEmail is noise while
        # 0.414 on VendorName is the extraction going wrong.
        "conf_core_min": float(min(core_scores)) if core_scores else 1.0,
        "conf_below_080_frac": (
            float(sum(1 for s in scores if s < LOW_CONFIDENCE)) / len(scores)
            if scores else 0.0),
        "totals_known": 1.0 if totals_known else 0.0,
        "total_residual_abs": float(residual),
        "total_residual_rel": float(residual_rel),
        # The crisp version the report quotes. One rupee, not zero: every real
        # invoice in the database is off by a few paise from rounding.
        "total_residual_over_rupee": 1.0 if residual > 1.0 else 0.0,
        "item_sum_residual_rel": float(item_sum_rel),
        "items_count": float(len(items)),
        # Not an anomaly by itself - it lets the model learn that tolerances and
        # scrutiny both scale with the size of the invoice.
        "total_magnitude_log": math.log10(max(total, 1.0)) if total is not None else 0.0,
        "vendor_gstin_present": 1.0 if vendor_gstin else 0.0,
        "vendor_gstin_shape_ok": 1.0 if excel_service.is_valid_gstin(raw_tax_id) else 0.0,
        "vendor_gstin_checksum_ok": 1.0 if gstin_checksum_ok(raw_tax_id) else 0.0,
        "pan_instead_of_gstin": 1.0 if pan_instead else 0.0,
        "customer_gstin_present": 1.0 if customer_gstin else 0.0,
        "self_billing": 1.0 if vendor_gstin and vendor_gstin == customer_gstin else 0.0,
        "invoice_date_parsed": 1.0 if invoice_date else 0.0,
        "invoice_date_future_days": float(future_days),
        "due_before_invoice_days": float(due_gap),
    }

    context = {
        "invoice_number": excel_service.get_document_number_from_key_values_json(fields),
        "vendor_gstin": vendor_gstin,
        "raw_vendor_tax_id": raw_tax_id,
        "customer_gstin": customer_gstin,
        "invoice_date": invoice_date.isoformat() if invoice_date else "",
        "subtotal": subtotal,
        "tax": tax,
        "total": total,
        "item_sum": item_sum,
        "missing_core_fields": core_missing,
        "invalid_core_fields": sorted(core_invalid),
    }
    return features, context


def vector(features: dict) -> list[float]:
    """The feature dict as a positional list, in FEATURE_NAMES order."""
    return [float(features[name]) for name in FEATURE_NAMES]
