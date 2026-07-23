import io
import json
import re
from datetime import date, datetime

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

# Builds the audit workbook (same 6 sheets as the mentor's template,
# DriftTemplateInterns.xlsx) from the rows database.get_filtered() returns.

# The template hard-codes this value on the AUDIT Period sheet.
DATA_SOURCE = "Invoice_DataBatch"

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
    "CustomerTaxId": "Customer GSTIN",
    "Items": "Line Items",
}

# Excel refuses cells longer than 32,767 characters; stay safely below that.
MAX_CELL_LENGTH = 32000


def reference_name(raw):
    """Turn a JSON field name into the audit's reference name."""
    if raw in REFERENCE_NAMES:
        return REFERENCE_NAMES[raw]
    if re.fullmatch(r"[A-Za-z0-9]+", raw):
        # Azure names like "DocumentNumber" -> "Document Number".
        return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", raw)
    # Layout fields are literal text read off the page, e.g. "Student Name :"
    # or "Units\nPassed" - tidy the whitespace and drop a trailing colon.
    return re.sub(r"\s+", " ", raw).strip().rstrip(":").strip()


def _plain(node):
    # Every extracted value is wrapped as {value, confidence}, at every level.
    # This strips the wrappers and returns plain values/lists/dicts.
    if isinstance(node, dict) and "value" in node and "confidence" in node:
        return _plain(node["value"])
    if isinstance(node, list):
        return [_plain(item) for item in node]
    if isinstance(node, dict):
        return {key: _plain(value) for key, value in node.items()}
    return node


def _cell_text(value):
    """One field's value as text for a cell ('' when there is nothing)."""
    plain = _plain(value)
    if plain is None or plain == "" or plain == [] or plain == {}:
        return ""
    if isinstance(plain, (list, dict)):
        # Line items and other nested data go in as JSON, like the template.
        text = json.dumps(plain, ensure_ascii=False)
    else:
        text = str(plain)
    text = ILLEGAL_CHARACTERS_RE.sub("", text)
    if len(text) > MAX_CELL_LENGTH:
        text = text[:MAX_CELL_LENGTH] + " ...[truncated]"
    return text


def _quote(text):
    # A text value inside an Excel formula: quotes are doubled to escape them.
    return '"' + text.replace('"', '""') + '"'


def _add_header(sheet, headers):
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True)


def _fit_columns(sheet):
    # Make every column wide enough to read (capped so JSON blobs don't
    # stretch the sheet forever).
    for column in sheet.columns:
        longest = max((len(str(c.value)) for c in column if c.value is not None), default=0)
        sheet.column_dimensions[column[0].column_letter].width = min(longest + 2, 60)


def build_workbook(rows, from_date, to_date, doc_type):
    """Build the whole audit workbook and return it as bytes."""
    label = DOC_TYPE_LABELS[doc_type]
    today = date.today()
    workbook = Workbook()

    # ---- Sheet 1: AUDIT Period - one row describing this audit run. ----
    sheet = workbook.active
    sheet.title = "AUDIT Period"
    _add_header(sheet, ["Audit_Run_ID", "Audit_Date", "Start_Date", "End_Date",
                        "Total_Records", "Data_Source", "Model_Version"])
    sheet.append([
        "Audit_" + today.strftime("%d%m%Y"),
        today,
        datetime.strptime(from_date, "%Y-%m-%d").date(),
        datetime.strptime(to_date, "%Y-%m-%d").date(),
        len(rows),
        DATA_SOURCE,
        "Azure OpenAI GPT-5 Mini + Azure Document Intelligence ("
        + MODEL_VERSION_BRACKET[doc_type] + ")",
    ])
    for cell in ("B2", "C2", "D2"):
        sheet[cell].number_format = "DD/MM/YYYY"

    # ---- Sheet 2: AI EXTRACTION LOG - one row per field per document. ----
    # While writing it we also count per-field totals for sheets 3 and 4.
    log = workbook.create_sheet("AI EXTRACTION LOG")
    _add_header(log, ["Number", "Source_File", "Type Of Document",
                      "Field_Name(Keys from json) ",
                      "Reference_Column(Keys from .net core)",
                      "OCR_Column (Extracted values)",
                      "UserEdit_Column(Leave this blank)",
                      "Completeness_Flag", "Validity_Flag"])
    stats = {}  # field name -> {"ref", "total", "null"}, in first-seen order
    line = 1
    for record in rows:
        fields = record["fields"]
        # The document's own number: invoices have InvoiceId, ID cards have
        # DocumentNumber; other types leave the column blank.
        number = _cell_text(fields.get("InvoiceId") or fields.get("DocumentNumber"))
        for name, value in fields.items():
            line += 1
            text = _cell_text(value)
            complete = text != ""
            # The Validity_Flag is a live formula: blank until the auditor
            # types into UserEdit (column G), then it compares G with the
            # OCR value in column F by itself.
            log.append([
                number,
                record["fileName"],
                label,
                name,
                reference_name(name),
                text,
                "",
                "Y" if complete else "N",
                '=IF(G{0}="","",IF(EXACT(F{0},G{0}),"Pass","Fail"))'.format(line),
            ])
            entry = stats.setdefault(name, {"ref": reference_name(name), "total": 0, "null": 0})
            entry["total"] += 1
            if not complete:
                entry["null"] += 1

    # ---- Sheet 3: COMPLETENESS TREND - how often OCR found each field. ----
    trend = workbook.create_sheet("COMPLETENESS TREND")
    _add_header(trend, ["OCR_Field", "Document Type", "Total_Records",
                        "Null_Count", "UserEdit_count(Blank)", "Completeness_Percent"])
    row_number = 1
    for name, entry in stats.items():
        row_number += 1
        trend.append([
            "OCR " + entry["ref"],
            label,
            entry["total"],
            entry["null"],
            # Live count of how many rows of this field the auditor has
            # filled in on the log sheet ("<>" means "not blank").
            "=COUNTIFS('AI EXTRACTION LOG'!D:D,{0},'AI EXTRACTION LOG'!G:G,\"<>\")".format(_quote(name)),
            "=(C{0}-D{0})/C{0}*100".format(row_number),
        ])
        trend.cell(row=row_number, column=6).number_format = "0.00"

    # ---- Sheet 4: VALIDITY TREND - when OCR found it, was it right? ----
    validity = workbook.create_sheet("VALIDITY TREND")
    _add_header(validity, ["Field_Type", "Type ", "Pass_Count", "Fail_Count",
                           "Validity_Percent"])
    row_number = 1
    for name, entry in stats.items():
        row_number += 1
        # Pass/Fail are counted live off the log sheet, so they update as
        # the auditor fills the UserEdit column.
        validity.append([
            entry["ref"],
            label,
            "=COUNTIFS('AI EXTRACTION LOG'!D:D,{0},'AI EXTRACTION LOG'!I:I,\"Pass\")".format(_quote(name)),
            "=COUNTIFS('AI EXTRACTION LOG'!D:D,{0},'AI EXTRACTION LOG'!I:I,\"Fail\")".format(_quote(name)),
            '=IF(C{0}+D{0}=0,"",C{0}/(C{0}+D{0})*100)'.format(row_number),
        ])
        validity.cell(row=row_number, column=5).number_format = "0.00"
    # The template's grading legend, below the table.
    validity.append([])
    validity.append(["Recommended Threshold"])
    validity.cell(row=validity.max_row, column=1).font = Font(bold=True)
    validity.append(["✅ Excellent: ≥ 95%"])
    validity.append(["\U0001f7e1 Acceptable: 90% – 94.99%"])
    validity.append(["\U0001f534 Needs Attention: < 90%"])

    # ---- Sheet 5: Invoice Volume - documents grouped by their own date. ----
    # Invoices carry InvoiceDate, receipts TransactionDate; documents without
    # either (layout, ID) are grouped under "Could not fetch".
    volume = workbook.create_sheet("Invoice Volume")
    _add_header(volume, ["Invoice Date", "Invoice_Count"])
    date_counts = {}
    for record in rows:
        fields = record["fields"]
        doc_date = _cell_text(fields.get("InvoiceDate") or fields.get("TransactionDate"))
        if doc_date == "":
            doc_date = "Could not fetch"
        date_counts[doc_date] = date_counts.get(doc_date, 0) + 1
    for doc_date, count in date_counts.items():
        volume.append([doc_date, count])

    # ---- Sheet 6: VENDOR MIX - who sent us these documents. ----
    # Invoices carry Vendor GSTIN (VendorTaxId), receipts the merchant name.
    vendors = workbook.create_sheet("VENDOR MIX")
    _add_header(vendors, ["Vendor_GSTIN", "Document Type", "Invoice_Count",
                          "Vendor_Percentage"])
    vendor_counts = {}
    for record in rows:
        fields = record["fields"]
        vendor = _cell_text(fields.get("VendorTaxId") or fields.get("MerchantName"))
        if vendor == "":
            vendor = "Could not fetch"
        vendor_counts[vendor] = vendor_counts.get(vendor, 0) + 1
    for vendor, count in vendor_counts.items():
        vendors.append([vendor, label, count, round(count / len(rows) * 100, 2)])

    for sheet in workbook.worksheets:
        _fit_columns(sheet)

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
