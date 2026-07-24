import io
import json
import logging
import os
import re
from collections import defaultdict
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font

# Builds the audit workbook (same 6 sheets as the mentor's template,
# DriftTemplateInterns.xlsx) from the rows database.get_filtered() returns.
# KeyValuesJson (each record's "fields" dict) is the source of truth: every
# sheet is calculated from it - nothing is hardcoded or invented.

logger = logging.getLogger(__name__)

# The template hard-codes this value on the AUDIT Period sheet.
DATA_SOURCE = "Invoice_DataBatch"

# Excel refuses cells longer than 32,767 characters; stay safely below that.
MAX_CELL_LENGTH = 32000

# CreatedAt is stored in UTC; the report groups days in local time.
LOCAL_TZ = ZoneInfo("Asia/Kolkata")

# Friendly names per Document Intelligence model id.
DOC_TYPE_LABELS = {
    "prebuilt-invoice": "Invoice",
    "prebuilt-receipt": "Receipt",
    "prebuilt-layout": "General layout",
    "prebuilt-idDocument": "ID Document",
}

# The bracket part of the Model_Version cell, e.g. "(Prebuilt-Invoice)".
MODEL_VERSION_BRACKET = {
    "prebuilt-invoice": "Prebuilt-Invoice",
    "prebuilt-receipt": "Prebuilt-Receipt",
    "prebuilt-layout": "Prebuilt-Layout",
    "prebuilt-idDocument": "Prebuilt-IdDocument",
}

# JSON field name -> the reference name the audit uses (the template calls
# this the "Reference_Column"). Anything not listed here falls back to
# reference_name() below, which splits PascalCase into words.
REFERENCE_NAMES = {
    "InvoiceId": "Invoice Id",
    "InvoiceDate": "Invoice Date",
    "InvoiceTotal": "Total Invoice Amount",
    "SubTotal": "Base Amount",
    "TotalTax": "GST Amount",
    "VendorTaxId": "Vendor GSTIN",
    "VendorGSTIN": "Vendor GSTIN",
    "CustomerTaxId": "Customer GSTIN",
    "CustomerGSTIN": "Customer GSTIN",
    "Items": "Line Items",
}

# A GSTIN is 15 characters: state code, PAN, entity code, "Z", checksum.
# A PAN alone (10 characters) must never be shown as a Vendor GSTIN.
GSTIN_PATTERN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
GSTIN_SEARCH = re.compile(r"[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]")
PAN_PATTERN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")

# Where to look for the vendor's GSTIN, in priority order.
GSTIN_KEYS = (
    "VendorGSTIN",
    "Vendor GSTIN",
    "VendorTaxId",
    "Vendor Tax Id",
    "SupplierGSTIN",
    "Supplier GSTIN",
    "GSTIN",
    "GSTIN/UIN",
    "TaxRegistrationNumber",
)

# Where to look for the document number shown in the "Number" column.
DOCUMENT_NUMBER_KEYS = (
    "InvoiceId",
    "Invoice ID",
    "Invoice Id",
    "DocumentNumber",
    "Document Number",
    "ReferenceNumber",
    "Reference Number",
    "TransactionId",
    "Transaction ID",
)

DATE_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d %b %Y",
    "%d %B %Y",
    "%d %b' %Y",
    "%d-%b-%y",
    "%d%b-%y",
)


def is_blank(value) -> bool:
    if value is None:
        return True
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return str(value).strip().lower() in {"", "none", "null", "[]", "{}"}


def normalize_flag(value) -> str:
    return "N" if is_blank(value) else "Y"


def _plain(node):
    # Unwrap only the OCR wrappers ocr_service produces ({"value", "confidence"}),
    # so real extracted data that merely contains a "value" key stays intact.
    if isinstance(node, dict) and "value" in node and "confidence" in node:
        return _plain(node["value"])
    if isinstance(node, list):
        return [_plain(item) for item in node]
    if isinstance(node, dict):
        return {key: _plain(value) for key, value in node.items()}
    return node


def _clean_scalar(value) -> str:
    # OCR frequently returns a value split across lines ("16-\nApr-25") or the
    # same value repeated on two lines ("5,142.60\n5,142.60"). Collapse the
    # whitespace so each cell shows one clean, readable value instead of the
    # raw multi-line blob it read off the page.
    text = re.sub(r"\s+", " ", str(value)).strip()
    halves = text.split(" ")
    if len(halves) == 2 and halves[0] == halves[1]:
        text = halves[0]
    return text


def _readable(plain) -> str:
    # Render a value as plain readable text rather than raw JSON, so line items
    # and tax blocks read like "Amount: 37,680.00, Description: Mugs" instead of
    # {"Amount": "37,680.00", ...} - the values Azure extracted, just legible.
    if is_blank(plain):
        return ""
    if isinstance(plain, list):
        return "; ".join(part for part in (_readable(item) for item in plain) if part)
    if isinstance(plain, dict):
        return ", ".join(f"{key}: {_readable(val)}"
                         for key, val in plain.items() if not is_blank(val))
    return _clean_scalar(plain)


def _cell_text(value) -> str:
    text = _readable(_plain(value))
    text = ILLEGAL_CHARACTERS_RE.sub("", text)
    return text if len(text) <= MAX_CELL_LENGTH else text[:MAX_CELL_LENGTH] + " ...[truncated]"


def reference_name(raw: str) -> str:
    if raw in REFERENCE_NAMES:
        return REFERENCE_NAMES[raw]
    if re.fullmatch(r"[A-Za-z0-9_]+", raw):
        # Azure names like "DocumentNumber" -> "Document Number".
        raw = raw.replace("_", " ")
        return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", raw).strip()
    # Layout fields are literal text read off the page, e.g. "Student Name :"
    # - tidy the whitespace and drop a trailing colon.
    return re.sub(r"\s+", " ", raw).strip().rstrip(":").strip()


def _normalize_tax_id(value) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value).upper())


def is_valid_gstin(value: str) -> bool:
    return bool(GSTIN_PATTERN.fullmatch(_normalize_tax_id(value)))


def is_pan(value: str) -> bool:
    return bool(PAN_PATTERN.fullmatch(_normalize_tax_id(value)))


def parse_key_values_json(value) -> dict:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to parse KeyValuesJson")
        return {}


def _find_case_insensitive(data: dict, wanted_key: str):
    wanted = wanted_key.casefold()
    for actual_key, value in data.items():
        if str(actual_key).casefold() == wanted:
            return value
    return None


def _gstin_candidates(value):
    return GSTIN_SEARCH.findall(_cell_text(value).upper())


def get_vendor_gstin_from_key_values_json(key_values_json: dict) -> str:
    if not isinstance(key_values_json, dict):
        return ""

    # First inspect vendor/supplier-specific keys in strict priority order.
    for key in GSTIN_KEYS:
        value = _find_case_insensitive(key_values_json, key)
        if value is None:
            continue
        for candidate in _gstin_candidates(value):
            if is_valid_gstin(candidate):
                logger.debug("Vendor GSTIN selected from key %s: %s", key, candidate)
                return _normalize_tax_id(candidate)
        plain = _normalize_tax_id(_cell_text(value))
        if is_pan(plain):
            logger.debug("Rejected PAN found under key %s: %s", key, plain)

    # Last JSON-only fallback: inspect key-value pairs whose key names mention GSTIN.
    # Raw OCR document text is deliberately not used here.
    for key, value in key_values_json.items():
        key_text = str(key).casefold()
        if "gstin" not in key_text and "gst" not in key_text:
            continue
        for candidate in _gstin_candidates(value):
            if is_valid_gstin(candidate):
                logger.debug("Vendor GSTIN selected from JSON key %s: %s", key, candidate)
                return _normalize_tax_id(candidate)

    logger.debug("No valid Vendor GSTIN found in KeyValuesJson")
    return ""


def _pan_of_gstin(gstin: str) -> str:
    # A 15-character GSTIN embeds the 10-character PAN at positions 3-12,
    # e.g. 27<ABICX1218R>1ZX. Returns "" for anything that is not a GSTIN.
    normalized = _normalize_tax_id(gstin)
    return normalized[2:12] if is_valid_gstin(normalized) else ""


def _pan_candidate_from_key_values_json(key_values_json: dict) -> str:
    # The bare PAN sitting in a vendor tax-id key, when no full GSTIN is present
    # (Azure sometimes reads only the PAN portion of a GSTIN on one invoice).
    if not isinstance(key_values_json, dict):
        return ""
    for key in GSTIN_KEYS:
        value = _find_case_insensitive(key_values_json, key)
        if value is None:
            continue
        plain = _normalize_tax_id(_cell_text(value))
        if is_pan(plain):
            return plain
    return ""


def build_gstin_recovery_map(records) -> dict:
    """Map a bare PAN to the full GSTIN of the same vendor within this export.

    When one invoice's tax id was read as only the PAN (ABICX1218R) but another
    invoice from the same vendor carries the full GSTIN (27ABICX1218R1ZX, which
    embeds that PAN), the PAN can be resolved back to the real GSTIN. The GSTIN
    still comes straight from KeyValuesJson - nothing is invented.
    """
    mapping = {}
    for record in records:
        fields = parse_key_values_json(record.get("fields", {}))
        pan = _pan_of_gstin(get_vendor_gstin_from_key_values_json(fields))
        if pan:
            mapping.setdefault(pan, get_vendor_gstin_from_key_values_json(fields))
    return mapping


def resolve_vendor_gstin(key_values_json: dict, recovery_map: dict | None = None) -> str:
    """The vendor GSTIN for one record, recovering it from a sibling record's
    GSTIN when only a PAN was extracted here."""
    gstin = get_vendor_gstin_from_key_values_json(key_values_json)
    if gstin:
        return gstin
    if recovery_map:
        pan = _pan_candidate_from_key_values_json(key_values_json)
        if pan in recovery_map:
            logger.info("Recovered Vendor GSTIN %s from PAN %s via a sibling record",
                        recovery_map[pan], pan)
            return recovery_map[pan]
    return ""


def get_document_number_from_key_values_json(key_values_json: dict) -> str:
    for key in DOCUMENT_NUMBER_KEYS:
        value = _find_case_insensitive(key_values_json, key)
        text = _cell_text(value).strip() if value is not None else ""
        if text:
            return text
    return ""


def parse_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value:
        return None
    text = re.sub(r"\s+", " ", str(value).strip())
    try:
        return datetime.fromisoformat(text[:10]).date()
    except ValueError:
        pass
    # OCR can leave a stray space beside a separator ("16- Apr-25"); tightening
    # it lets an otherwise valid date parse (and so pass the validity check).
    candidates = [text]
    tightened = re.sub(r"\s*([/-])\s*", r"\1", text)
    if tightened != text:
        candidates.append(tightened)
    for candidate in candidates:
        for fmt in DATE_FORMATS:
            try:
                return datetime.strptime(candidate, fmt).date()
            except ValueError:
                continue
    return None


def extraction_date_local(created_at):
    """The local calendar day a document was extracted on.

    CreatedAt is stored as "YYYY-MM-DD HH:MM:SS" in UTC; a document analysed
    late in the local evening would land on the wrong day without converting.
    Returns None when the value cannot be read as a date at all.
    """
    if isinstance(created_at, datetime):
        moment = created_at
    else:
        try:
            moment = datetime.strptime(str(created_at).strip(), "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            return parse_date(created_at)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(LOCAL_TZ).date()


def _numeric_value(value):
    # Take the first number in the text, so a value OCR duplicated across two
    # lines ("5,142.60 5,142.60") or prefixed with a currency symbol ("₹46,273.00")
    # still reads as a single amount instead of failing to parse.
    match = re.search(r"\(?-?[0-9][0-9,]*\.?[0-9]*\)?", str(value))
    if not match:
        raise ValueError(f"no number in {value!r}")
    cleaned = match.group().replace(",", "")
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-" + cleaned[1:-1]
    return float(cleaned)


def is_valid_field_value(field_name: str, field_value: str) -> bool:
    if is_blank(field_value):
        return False
    name = field_name.casefold()
    if "gstin" in name or "taxid" in name:
        return is_valid_gstin(field_value)
    if "date" in name:
        return parse_date(field_value) is not None
    if any(token in name for token in ("amount", "total", "price", "subtotal", "tax")):
        try:
            _numeric_value(field_value)
            return True
        except (TypeError, ValueError):
            return False
    return True


def build_extraction_log_rows(records, doc_type: str) -> list:
    label = DOC_TYPE_LABELS.get(doc_type, doc_type)
    parsed_records = []
    all_fields = set()

    for record in records:
        fields = parse_key_values_json(record.get("fields", {}))
        source_file = record.get("fileName", "")
        if fields:
            logger.info("Processing %s: %d field(s) %s", source_file, len(fields), sorted(fields))
        else:
            logger.warning("No extracted fields (empty KeyValuesJson) for %s", source_file)
        parsed_records.append((record, fields))
        all_fields.update(fields.keys())

    rows = []
    sorted_fields = sorted(all_fields)
    for record, fields in parsed_records:
        source_file = record.get("fileName", "")
        invoice_number = get_document_number_from_key_values_json(fields)
        vendor_gstin = get_vendor_gstin_from_key_values_json(fields)
        if fields:
            logger.info("Vendor GSTIN for %s: %s", source_file, vendor_gstin or "none found")

        # Create a row for every field seen in the report population. Missing
        # fields stay blank (Completeness_Flag = N), which is what makes the
        # Null_Count on the COMPLETENESS TREND sheet meaningful.
        for field_name in sorted_fields:
            field_value = fields.get(field_name)
            ocr_value = _cell_text(field_value)

            # Show the validated GSTIN for Vendor GSTIN fields when one was
            # found. When none was found, keep the raw extracted value: a PAN
            # then stays visible with Completeness Y and Validity Fail,
            # instead of being blanked into a fake "missing" cell.
            if reference_name(field_name) == "Vendor GSTIN" and vendor_gstin:
                ocr_value = vendor_gstin

            rows.append({
                "invoice_number": invoice_number,
                "source_file": source_file,
                "doc_type": label,
                "field_name": field_name,
                "reference_column": reference_name(field_name),
                "ocr_value": ocr_value,
                "user_edit": "",
                "completeness_flag": normalize_flag(ocr_value),
                "validity_flag": "Pass" if is_valid_field_value(field_name, ocr_value) else "Fail",
            })
    return rows


def calculate_completeness_trend(extraction_rows: list) -> list:
    stats_by_field = defaultdict(lambda: {"total": 0, "null": 0, "doc_type": ""})
    for row in extraction_rows:
        stats = stats_by_field[row["field_name"]]
        stats["doc_type"] = row["doc_type"]
        stats["total"] += 1
        stats["null"] += row["completeness_flag"] == "N"

    result = []
    for field_name in sorted(stats_by_field):
        stats = stats_by_field[field_name]
        total = stats["total"]
        # Stored as a decimal (1.0 = 100%); the cell's "0.00%" format displays it.
        completeness = ((total - stats["null"]) / total) if total else 0.0
        result.append({
            "ocr_field": reference_name(field_name),
            "doc_type": stats["doc_type"],
            "total_records": total,
            "null_count": stats["null"],
            # The UserEdit column is intentionally left blank - there are no user
            # edits to count, so the cell stays empty rather than showing a number.
            "user_edit_count": "",
            "completeness_percent": completeness,
        })
    return result


def calculate_validity_trend(extraction_rows: list) -> list:
    stats_by_field = defaultdict(lambda: {"pass": 0, "fail": 0, "doc_type": ""})
    for row in extraction_rows:
        stats = stats_by_field[row["field_name"]]
        stats["doc_type"] = row["doc_type"]
        stats["pass" if row["validity_flag"] == "Pass" else "fail"] += 1

    result = []
    for field_name in sorted(stats_by_field):
        stats = stats_by_field[field_name]
        total = stats["pass"] + stats["fail"]
        # Stored as a decimal, same as completeness above.
        validity = (stats["pass"] / total) if total else 0.0
        result.append({
            "field_type": reference_name(field_name),
            "type": stats["doc_type"],
            "pass_count": stats["pass"],
            "fail_count": stats["fail"],
            "validity_percent": validity,
        })
    return result


def calculate_invoice_volume(records) -> list:
    grouped = defaultdict(set)
    for index, record in enumerate(records):
        created_at = record.get("createdAt")
        extraction_date = extraction_date_local(created_at)
        if extraction_date is None:
            logger.warning("Skipping record with unreadable CreatedAt: %r", created_at)
            continue
        grouped[extraction_date].add(str(record.get("id", index)))
    return [
        {"extraction_date": day, "invoice_count": len(grouped[day])}
        for day in sorted(grouped)
    ]


def calculate_vendor_mix(records) -> list:
    vendor_counts = defaultdict(int)
    labels = {}
    total = len(records)
    recovery_map = build_gstin_recovery_map(records)

    for record in records:
        fields = parse_key_values_json(record.get("fields", {}))
        gstin = resolve_vendor_gstin(fields, recovery_map) or "Unknown"
        model = record.get("model", "")
        vendor_counts[gstin] += 1
        labels[gstin] = DOC_TYPE_LABELS.get(model, model or "Unknown")

    result = []
    for gstin in sorted(vendor_counts):
        count = vendor_counts[gstin]
        result.append({
            "vendor_gstin": gstin,
            "doc_type": labels[gstin],
            "invoice_count": count,
            # Stored as a decimal (2 of 4 -> 0.5); shown as 50.00% by the format.
            "vendor_percentage": count / total if total else 0.0,
        })
    return result


def _add_header(sheet, headers):
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    sheet.freeze_panes = "A2"


def _fit_columns(sheet):
    for column in sheet.columns:
        longest = max((len(str(cell.value)) for cell in column if cell.value is not None), default=0)
        sheet.column_dimensions[column[0].column_letter].width = min(longest + 2, 60)
    for row in sheet.iter_rows():
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")


def build_workbook(rows, from_date, to_date, doc_type):
    logger.info("Export: %d record(s) received for %s between %s and %s",
                len(rows), doc_type, from_date, to_date)
    today = date.today()
    workbook = Workbook()

    audit = workbook.active
    audit.title = "AUDIT Period"
    _add_header(audit, [
        "Audit_Run_ID", "Audit_Date", "Start_Date", "End_Date",
        "Total_Records", "Data_Source", "Model_Version",
    ])
    start_date = datetime.strptime(from_date, "%Y-%m-%d").date()
    end_date = datetime.strptime(to_date, "%Y-%m-%d").date()
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "").strip()
    if "azure openai" not in deployment.lower():
        deployment = ("Azure OpenAI " + deployment).strip()
    audit.append([
        "Audit_" + today.strftime("%d%m%Y"), today, start_date, end_date,
        len(rows), DATA_SOURCE,
        f"{deployment} + Azure Document Intelligence ({MODEL_VERSION_BRACKET.get(doc_type, doc_type)})",
    ])
    for cell in (audit["B2"], audit["C2"], audit["D2"]):
        cell.number_format = "DD/MM/YYYY"

    extraction_rows = build_extraction_log_rows(rows, doc_type)
    logger.info("Export: %d extraction row(s) generated", len(extraction_rows))
    # The two header quirks below (trailing spaces on "...json) " and "Type ")
    # are copied verbatim from the mentor's template - do not tidy them.
    log = workbook.create_sheet("AI EXTRACTION LOG")
    _add_header(log, [
        "Number", "Source_File", "Type Of Document", "Field_Name(Keys from json) ",
        "Reference_Column(Keys from .net core)", "OCR_Column (Extracted values)",
        "UserEdit_Column(Leave this blank)", "Completeness_Flag", "Validity_Flag",
    ])
    for row in extraction_rows:
        log.append([
            row["invoice_number"], row["source_file"], row["doc_type"], row["field_name"],
            row["reference_column"], row["ocr_value"], row["user_edit"],
            row["completeness_flag"], row["validity_flag"],
        ])

    completeness_rows = calculate_completeness_trend(extraction_rows)
    logger.info("Export: %d completeness row(s) generated", len(completeness_rows))
    completeness = workbook.create_sheet("COMPLETENESS TREND")
    _add_header(completeness, [
        "OCR_Field", "Document Type", "Total_Records", "Null_Count",
        "UserEdit_count(Blank)", "Completeness_Percent",
    ])
    for item in completeness_rows:
        completeness.append([
            item["ocr_field"], item["doc_type"], item["total_records"], item["null_count"],
            item["user_edit_count"], item["completeness_percent"],
        ])
        completeness.cell(completeness.max_row, 6).number_format = "0.00%"

    validity_rows = calculate_validity_trend(extraction_rows)
    logger.info("Export: %d validity row(s) generated", len(validity_rows))
    validity = workbook.create_sheet("VALIDITY TREND")
    _add_header(validity, ["Field_Type", "Type ", "Pass_Count", "Fail_Count", "Validity_Percent"])
    for item in validity_rows:
        validity.append([
            item["field_type"], item["type"], item["pass_count"], item["fail_count"],
            item["validity_percent"],
        ])
        validity.cell(validity.max_row, 5).number_format = "0.00%"
    validity.append([])
    validity.append(["Recommended Threshold"])
    validity[validity.max_row][0].font = Font(bold=True)
    validity.append(["Excellent: >= 95%"])
    validity.append(["Acceptable: 90% - 94.99%"])
    validity.append(["Needs Attention: < 90%"])

    volume = workbook.create_sheet("Invoice Volume")
    _add_header(volume, ["Extraction_Date", "Invoice_Count"])
    for item in calculate_invoice_volume(rows):
        volume.append([item["extraction_date"], item["invoice_count"]])
        volume.cell(volume.max_row, 1).number_format = "DD/MM/YYYY"

    vendors = workbook.create_sheet("VENDOR MIX")
    _add_header(vendors, ["Vendor_GSTIN", "Document Type", "Invoice_Count", "Vendor_Percentage"])
    for item in calculate_vendor_mix(rows):
        vendors.append([
            item["vendor_gstin"], item["doc_type"], item["invoice_count"], item["vendor_percentage"],
        ])
        vendors.cell(vendors.max_row, 4).number_format = "0.00%"

    for sheet in workbook.worksheets:
        _fit_columns(sheet)

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
