"""Generate labelled invoice records to train and measure the anomaly detector.

The real database holds 27 rows but only four distinct invoices, all analysed in
one afternoon. Nothing can be trained or honestly measured on that, so this
module fabricates a corpus in the same shape Azure Document Intelligence produces
and writes down exactly which anomalies it injected into which record.

    python synthetic_invoices.py --count 600 --anomaly-rate 0.30 \
        --seed 20260807 --out ../azureml/data/synthetic_invoices.jsonl

Three things in here exist to stop the model learning something true about this
generator instead of something true about invoices.

1. Benign OCR noise is applied to CLEAN records too, and not labelled. All of it
   is copied from the real data: a date split across a newline ("16-\\nApr-25"),
   a value the OCR read twice ("5,142.60\\n5,142.60"), a rupee sign on its own
   line, a quantity written "480 PCS", a line item with no Quantity at all, and
   line amounts that are tax-INCLUSIVE. Without this the model learns "contains a
   newline" and reports 0.99 precision on data no real invoice resembles.

2. The JSON wrapper shape is sampled from what the database actually contains -
   about 30% bare scalars with no confidence (the oldest rows), about 65% with
   top-level confidence but bare dicts inside arrays, about 5% fully nested. If
   every synthetic record used one shape, the shape would BE the label.

3. Hard negatives: clean invoices that look alarming. A mixed 12%/18% invoice, a
   credit note with negative amounts, a tiny invoice among large ones, a document
   with no due date. These are labelled 0, and they are why the measured precision
   means something.

Labels distinguish intrinsic anomalies from corpus ones. duplicate_invoice and
vendor_amount_outlier are properties of a GROUP - the record itself looks
perfectly ordinary - so training a per-document model on them would be teaching
it to guess. is_intrinsic_anomaly is what the model learns; is_anomalous is what
the rules as a whole are measured against.

This module writes JSONL and nothing else. It deliberately does not import
database: synthetic invoices must never reach documents.db, where they would flow
into the audit workbook and be indistinguishable from real extractions.
"""

import argparse
import json
import random
from datetime import date, timedelta

import anomaly_features
import excel_service

# Indian GST slab rates. Real invoices sit on these; off-slab is a later feature.
GST_SLABS = (5.0, 12.0, 18.0, 28.0)

# Which injected anomalies a single document could in principle reveal on its own.
# The other two need peers, so a per-document model trained on them would be
# fitting noise - see the module docstring.
INTRINSIC_ANOMALIES = (
    "core_field_dropped",
    "gstin_checksum_bad",
    "pan_instead_of_gstin",
    "high_value",
    "total_mismatch",
    "item_sum_mismatch",
    # Visible from one document via conf_core_min / conf_below_080_frac. Note the
    # rules have no confidence check - this is a type only the model can catch,
    # which is part of what makes training worth doing.
    "confidence_collapse",
)
CORPUS_ANOMALIES = ("duplicate_invoice", "vendor_amount_outlier")

STATE_CODES = ("07", "27", "29", "06", "19", "33", "24", "36")
GOODS = ("Mugs", "Printer Cartridge", "A4 Paper Ream", "Desk Organiser", "USB Cable",
         "Whiteboard Marker", "Conference Chair", "Laptop Stand", "Toner Refill",
         "Notebook Pack", "HDMI Adapter", "Filing Cabinet")
VENDOR_WORDS = ("MODRN", "ASENT", "KLR", "BVN", "PSV", "TABGV", "VERTEX", "QUANTA",
                "NEXUS", "RIDGE", "ORBIT", "SUMMIT")


def _letters(rng, count):
    return "".join(rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(count))


def _digits(rng, count):
    return "".join(rng.choice("0123456789") for _ in range(count))


def make_pan(rng) -> str:
    """A structurally valid PAN: 5 letters, 4 digits, 1 letter."""
    return _letters(rng, 5) + _digits(rng, 4) + _letters(rng, 1)


def make_gstin(rng, pan=None, state=None) -> str:
    """A GSTIN whose check digit is correct, so a wrong one is a real signal.

    Layout is state code, the 10-character PAN, an entity code, a literal Z, and
    the mod-36 check digit computed from the first fourteen.
    """
    pan = pan or make_pan(rng)
    state = state or rng.choice(STATE_CODES)
    body = state + pan + rng.choice("123456789") + "Z"
    return body + anomaly_features.gstin_check_digit(body)


def make_vendors(rng, count=25) -> list:
    """A stable pool of vendors, each with its own typical invoice size.

    Per-vendor scale is what makes the vendor-outlier rule measurable: without
    it every invoice is drawn from the same distribution and no amount is
    unusual *for its vendor*.
    """
    vendors = []
    for index in range(count):
        pan = make_pan(rng)
        state = rng.choice(STATE_CODES)
        vendors.append({
            "name": f"{rng.choice(VENDOR_WORDS)} {rng.choice(('SYSTEMS', 'ENTERPRISES', 'TRADERS', 'SUPPLIES', 'INDUSTRIES'))} PVT LTD",
            "gstin": make_gstin(rng, pan, state),
            "pan": pan,
            "state": state,
            "address": f"{rng.randint(1, 400)}, {rng.choice(('MG ROAD', 'PARK STREET', 'SECTOR 18', 'INDUSTRIAL AREA'))}",
            # Typical invoice magnitude, so "far from usual" means something.
            "scale": rng.choice((3_000, 8_000, 25_000, 60_000, 150_000)),
        })
    return vendors


def _money(value) -> str:
    return f"{value:,.2f}"


def _clean_invoice(rng, vendor, customer, index) -> dict:
    """One arithmetically correct invoice, before noise or injection."""
    line_count = rng.randint(1, 5)
    # One rate for most invoices, two for some - a mixed-rate invoice is legal and
    # is exactly the shape that trips a naive "effective rate must be a slab" check.
    rates = [rng.choice(GST_SLABS)]
    if rng.random() < 0.25:
        rates.append(rng.choice([r for r in GST_SLABS if r != rates[0]]))

    items = []
    for _ in range(line_count):
        quantity = rng.randint(1, 500)
        unit_price = round(vendor["scale"] / max(quantity, 1) * rng.uniform(0.4, 1.6), 2)
        amount = round(quantity * unit_price, 2)
        items.append({
            "Description": f"{rng.choice(GOODS)} -{_digits(rng, 4)}",
            "Quantity": str(quantity),
            "Unit": "PCS",
            "UnitPrice": _money(unit_price),
            "Amount": _money(amount),
            "TaxRate": f"{rng.choice(rates):.0f} %",
            "_amount": amount,
            "_rate": rng.choice(rates),
        })

    subtotal = round(sum(item["_amount"] for item in items), 2)
    tax = round(sum(item["_amount"] * item["_rate"] / 100.0 for item in items), 2)
    total = round(subtotal + tax, 2)
    # Real invoices round the printed total to the nearest rupee, which is why
    # the real corpus is off by 0.10 and 0.40. Reproduce that so the model learns
    # a few paise is normal and does not treat every rounding as a mismatch.
    if rng.random() < 0.5:
        total = round(total + rng.choice((-0.4, -0.1, 0.05, 0.25)), 2)

    invoice_date = date(2025, 4, 1) + timedelta(days=rng.randint(0, 500))
    due_date = invoice_date + timedelta(days=rng.choice((15, 30, 45, 60)))

    fields = {
        "InvoiceId": f"{rng.choice(('INV', 'GEN', 'TQS', 'NX'))}/{invoice_date.year % 100}-{(invoice_date.year + 1) % 100}/{index:04d}",
        "InvoiceDate": invoice_date.strftime("%d-%b-%y"),
        "DueDate": due_date.strftime("%d-%b-%y"),
        "VendorName": vendor["name"],
        "VendorAddress": vendor["address"],
        "VendorTaxId": vendor["gstin"],
        "CustomerName": customer["name"],
        "CustomerTaxId": customer["gstin"],
        "SubTotal": _money(subtotal),
        "TotalTax": _money(tax),
        "InvoiceTotal": "₹" + _money(total),
        "Items": [
            {key: value for key, value in item.items() if not key.startswith("_")}
            for item in items
        ],
        "TaxDetails": [
            {"Rate": f"{rate:.0f} %",
             "Amount": _money(round(sum(i["_amount"] for i in items if i["_rate"] == rate)
                                    * rate / 100.0, 2))}
            for rate in sorted(set(item["_rate"] for item in items))
        ],
    }

    # A correct invoice is NOT a complete one, and getting this wrong poisoned the
    # first trained model. Every clean invoice used to carry all twelve core
    # fields, so "a core field is missing" only ever occurred when
    # core_field_dropped had been injected - and the model duly learned that a
    # single absence means anomaly with near-certainty. Applied to real invoices,
    # which routinely print no separate tax breakdown, it scored all seventeen at
    # 100 and was useless.
    #
    # Rates measured from the real corpus: Tax Details appeared on 1 of 4 unique
    # invoices, Customer GSTIN on 3 of 4. Kept slightly milder than measured
    # because four invoices is a thin sample to extrapolate from.
    #
    # None of these four is in _inject's droppable list, so the injected anomaly
    # stays a distinct thing rather than blurring into normal absence.
    for key, omit_rate in (("TaxDetails", 0.55), ("CustomerTaxId", 0.25),
                           ("VendorAddress", 0.12), ("CustomerName", 0.08)):
        if rng.random() < omit_rate:
            fields.pop(key, None)

    # The other half of the same mistake, and the larger one. Azure's
    # prebuilt-invoice schema is far wider than the fields above: it returns
    # address recipients, separate billing and shipping blocks, emails, a purchase
    # order. Real extractions carry 16 to 25 fields; this generator produced about
    # twelve. field_count is in the feature vector, so every real invoice sat SIX
    # standard deviations from the training data on that one number, which alone
    # was worth enough log-odds to score it 100 whatever else it looked like.
    #
    # These are padding in the honest sense - the detector has no rule about any of
    # them - but their presence is what a real document looks like.
    for key, value, keep_rate in (
            ("VendorAddressRecipient", vendor["name"], 0.95),
            ("BillingAddress", customer["address"], 0.75),
            ("BillingAddressRecipient", customer["name"], 0.75),
            ("ShippingAddress", customer["address"], 0.60),
            ("ShippingAddressRecipient", customer["name"], 0.60),
            ("CustomerAddress", customer["address"], 0.45),
            ("CustomerAddressRecipient", customer["name"], 0.45),
            ("AmountDue", "₹" + _money(total), 0.35),
            ("PurchaseOrder", "PO-" + _digits(rng, 5), 0.30),
            ("CustomerId", _digits(rng, 6), 0.25),
            ("VendorEmail", f"accounts@{vendor['name'].split()[0].lower()}.co.in", 0.25),
            ("CustomerEmail", f"payables@{customer['name'].split()[0].lower()}.co.in", 0.25),
    ):
        if rng.random() < keep_rate:
            fields[key] = value

    return {"fields": fields, "items": items, "subtotal": subtotal, "tax": tax,
            "total": total, "vendor": vendor, "invoice_date": invoice_date}


# --- benign noise: applied to clean records too, never labelled -----------------

def _apply_ocr_noise(rng, record, touch_amounts=True):
    """Cosmetic OCR damage: everything here survives the existing parsers.

    A newline inside a date, a figure read twice, a stray rupee sign - all of
    these change the TEXT without changing the number excel_service reads back
    out, which is precisely why they must not be labelled as anomalies.

    The one exception is the tax-inclusive rewrite, which does change values, so
    callers that have already arranged the amounts deliberately (hard negatives)
    pass touch_amounts=False rather than having their work undone.
    """
    # Every access is guarded because this runs AFTER injection, and
    # core_field_dropped may have removed any of these outright.
    fields = record["fields"]
    if rng.random() < 0.25 and "InvoiceDate" in fields:
        # "16-\nApr-25" - the date wrapped mid-value on the page.
        fields["InvoiceDate"] = fields["InvoiceDate"].replace("-", "-\n", 1)
    if rng.random() < 0.20 and "TotalTax" in fields:
        # "5,142.60\n5,142.60" - OCR read the same figure on two lines.
        fields["TotalTax"] = fields["TotalTax"] + "\n" + fields["TotalTax"]
    if rng.random() < 0.15 and "InvoiceTotal" in fields:
        fields["InvoiceTotal"] = "₹\n" + fields["InvoiceTotal"].lstrip("₹")
    if rng.random() < 0.30:
        for item in fields["Items"]:
            # A hard negative may already have removed Quantity entirely.
            if "Quantity" in item:
                item["Quantity"] = item["Quantity"] + " PCS"
    if rng.random() < 0.20:
        # Real invoices sometimes carry no Quantity at all, which makes the
        # quantity-times-price check uncomputable rather than wrong.
        for item in fields["Items"]:
            item.pop("Quantity", None)
    if touch_amounts and rng.random() < 0.25:
        # Tax-INCLUSIVE line amounts: the items then sum to InvoiceTotal instead
        # of SubTotal. Both conventions appear in the real data.
        for item, source in zip(fields["Items"], record["items"]):
            item["Amount"] = _money(round(source["_amount"] * (1 + source["_rate"] / 100.0), 2))
    if rng.random() < 0.10:
        # An optional field Azure did not find. Not a core field, so not an anomaly.
        fields.pop("DueDate", None)
    return record


# --- hard negatives: clean, but shaped like trouble -----------------------------

def _make_hard_negative(rng, record):
    """Clean invoices that a careless rule would flag. Labelled 0 on purpose.

    These have to stay genuinely clean, or they are not hard negatives - they are
    mislabelled anomalies, and they teach the model the opposite of the intended
    lesson. Two mistakes are easy to make here and both were made first time
    round: negating the header amounts for a credit note without negating the
    line items (which then reconciles against nothing), and removing Tax Details
    as though it were optional when it is one of the twelve core fields.
    """
    fields = record["fields"]
    choice = rng.randint(0, 3)
    if choice == 0:
        # A credit note: every amount negative, header AND lines, so it still
        # reconciles. Accountants write negatives in parentheses.
        for key in ("SubTotal", "TotalTax"):
            fields[key] = "(" + fields[key] + ")"
        fields["InvoiceTotal"] = "(" + fields["InvoiceTotal"].lstrip("₹") + ")"
        for item in fields["Items"]:
            item["Amount"] = "(" + item["Amount"] + ")"
        fields["InvoiceId"] = "CN/" + fields["InvoiceId"]
    elif choice == 1:
        # A tiny invoice in a corpus of large ones. Rewritten end to end so the
        # arithmetic, the lines and the tax block all still agree.
        fields["SubTotal"] = _money(85.0)
        fields["TotalTax"] = _money(15.3)
        fields["InvoiceTotal"] = _money(100.3)
        fields["Items"] = [{"Description": "Courier charge", "Quantity": "1",
                            "Unit": "PCS", "UnitPrice": "85.00", "Amount": "85.00",
                            "TaxRate": "18 %"}]
        fields["TaxDetails"] = [{"Rate": "18 %", "Amount": _money(15.3)}]
    elif choice == 2:
        # Every line amount tax-inclusive AND no quantities - the combination that
        # makes both cross-checks uncomputable at once.
        for item, source in zip(fields["Items"], record["items"]):
            item["Amount"] = _money(round(source["_amount"] * (1 + source["_rate"] / 100.0), 2))
            item.pop("Quantity", None)
    else:
        # DueDate only. Tax Details is a core field, so dropping it would BE
        # anomaly 1 rather than a hard negative.
        fields.pop("DueDate", None)
    return record


# --- anomaly injectors: one per rule, each records its own label -----------------

def _scale_amounts(fields, factor) -> bool:
    """Multiply the three header amounts, reading them the way the parser does.

    Uses excel_service._numeric_value rather than float(), because by this point
    a value may carry a currency symbol, a thousands separator or an embedded
    newline. A plain float() silently fails on those, which would mean an
    injection that never happened but was still labelled.
    """
    scaled = {}
    for key in ("SubTotal", "TotalTax", "InvoiceTotal"):
        if key not in fields:
            continue
        try:
            scaled[key] = _money(round(excel_service._numeric_value(fields[key]) * factor, 2))
        except (TypeError, ValueError):
            return False
    if not scaled:
        return False
    fields.update(scaled)
    return True


def _inject(rng, record, kind):
    fields = record["fields"]

    if kind == "core_field_dropped":
        droppable = [k for k in ("VendorTaxId", "InvoiceId", "InvoiceDate",
                                 "InvoiceTotal", "TotalTax", "VendorName")
                     if k in fields]
        if not droppable:
            return False
        fields.pop(rng.choice(droppable))
        return True

    if kind == "gstin_checksum_bad":
        gstin = fields.get("VendorTaxId", "")
        if len(gstin) != 15:
            return False
        # Change only the check digit, so the SHAPE still passes. This is exactly
        # what the workbook's validity column cannot see.
        alphabet = anomaly_features.GSTIN_ALPHABET
        wrong = rng.choice([c for c in alphabet if c != gstin[14]])
        fields["VendorTaxId"] = gstin[:14] + wrong
        return True

    if kind == "pan_instead_of_gstin":
        gstin = fields.get("VendorTaxId", "")
        if len(gstin) != 15:
            return False
        fields["VendorTaxId"] = gstin[2:12]  # the embedded PAN, exactly as OCR does
        return True

    if kind == "high_value":
        return _scale_amounts(fields, rng.uniform(12.0, 60.0))

    if kind == "total_mismatch":
        if "InvoiceTotal" not in fields:
            return False
        drift = record["total"] * rng.uniform(0.005, 0.15) * rng.choice((-1, 1))
        # At least a few rupees, or it hides under the rounding tolerance and the
        # label would be a lie.
        if abs(drift) < 5:
            drift = 5 * (1 if drift >= 0 else -1)
        fields["InvoiceTotal"] = _money(round(record["total"] + drift, 2))
        return True

    if kind == "item_sum_mismatch":
        if not fields.get("Items"):
            return False
        item = fields["Items"][0]
        try:
            amount = float(item["Amount"].replace(",", ""))
        except (KeyError, ValueError):
            return False
        item["Amount"] = _money(round(amount * rng.uniform(1.3, 2.5), 2))
        return True

    if kind == "vendor_amount_outlier":
        return _scale_amounts(fields, 50.0)

    return False


# --- wrapper shapes -------------------------------------------------------------

def _confidence(rng):
    """A plausible Azure confidence.

    Deliberately bimodal, which a single Beta cannot reproduce. Azure returns most
    fields high but a handful genuinely low - the real corpus has 21 of 258 values
    under 0.80, and per-invoice minimums of 0.296, 0.414, 0.453 and 0.555.

    The old single Beta(5, 0.7) put the MINIMUM confidence across an invoice near
    0.72 in training against 0.41 in reality. conf_min is in the feature vector
    with a large negative coefficient, so that gap alone was reading as an anomaly
    on every real document.
    """
    if rng.random() < 0.10:
        return round(rng.uniform(0.28, 0.68), 3)
    return round(min(0.999, max(0.05, rng.betavariate(6.0, 0.6))), 3)


def _wrap(rng, fields, shape, weak_fields=()):
    """Re-shape a plain field dict into one of the three real storage shapes."""
    if shape == "bare":
        return fields  # the oldest rows: scalars, no confidence anywhere

    wrapped = {}
    for key, value in fields.items():
        score = _confidence(rng)
        if key in weak_fields:
            score = round(rng.uniform(0.30, 0.65), 3)
        if isinstance(value, list):
            if shape == "nested":
                items = [{"value": {k: {"value": v, "confidence": _confidence(rng)}
                                    for k, v in item.items()},
                          "confidence": _confidence(rng)} for item in value]
            else:
                items = value  # top-wrapped: arrays hold bare dicts
            wrapped[key] = {"value": items, "confidence": None}
        else:
            wrapped[key] = {"value": value, "confidence": score}
    return wrapped


def generate(count=600, anomaly_rate=0.30, seed=20260807,
             hard_negative_rate=0.12) -> list:
    """The corpus, as a list of records ready to be written as JSONL."""
    rng = random.Random(seed)
    vendors = make_vendors(rng)
    customers = make_vendors(rng, count=8)

    records = []
    for index in range(count):
        vendor = rng.choice(vendors)
        record = _clean_invoice(rng, vendor, rng.choice(customers), index)

        injected = []
        hard_negative = rng.random() < hard_negative_rate
        if hard_negative:
            _make_hard_negative(rng, record)
        elif rng.random() < anomaly_rate:
            # One or two, because real documents fail in more than one way at once.
            wanted = rng.sample(list(INTRINSIC_ANOMALIES) + ["vendor_amount_outlier"],
                                rng.randint(1, 2))
            for kind in wanted:
                if _inject(rng, record, kind):
                    injected.append(kind)

        # Hard negatives have already set their amounts deliberately, so the one
        # value-changing piece of noise is held back for them.
        _apply_ocr_noise(rng, record, touch_amounts=not hard_negative)

        # Low confidence is its own signal and is applied to the wrapper, not the
        # values, so it can coexist with any of the injections above.
        weak = ()
        if not hard_negative and rng.random() < 0.08:
            weak = ("VendorTaxId", "InvoiceTotal", "VendorName")
            injected.append("confidence_collapse")

        shape = rng.choices(("bare", "top", "nested"), weights=(30, 65, 5))[0]
        fields = _wrap(rng, record["fields"], shape, weak)

        records.append({
            "doc_id": f"SYN-{index:06d}",
            "file_name": f"SYN-{index:06d}.pdf",
            "model": "prebuilt-invoice",
            "created_at": record["invoice_date"].isoformat(),
            "shape": shape,
            "fields": fields,
            "labels": {
                "is_anomalous": 1 if injected else 0,
                "is_intrinsic_anomaly": 1 if any(
                    k in INTRINSIC_ANOMALIES for k in injected) else 0,
                "anomaly_types": sorted(injected),
                "hard_negative": bool(hard_negative),
            },
        })

    # Duplicates are made by re-emitting an existing record, because that is what
    # the anomaly IS - the same invoice processed twice, not a document that looks
    # odd on its own. Both copies are labelled: the rule flags the whole group.
    duplicate_count = max(1, int(count * 0.03))
    for _ in range(duplicate_count):
        source = rng.choice(records[:len(records)])
        if "duplicate_invoice" in source["labels"]["anomaly_types"]:
            continue
        copy = json.loads(json.dumps(source))
        copy["doc_id"] = source["doc_id"] + "-DUP"
        copy["file_name"] = source["file_name"].replace(".pdf", " (2).pdf")
        for row in (source, copy):
            row["labels"]["is_anomalous"] = 1
            row["labels"]["anomaly_types"] = sorted(
                set(row["labels"]["anomaly_types"]) | {"duplicate_invoice"})
        records.append(copy)

    return records


def write_jsonl(records, path):
    with open(path, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate labelled synthetic invoices for the anomaly detector.")
    parser.add_argument("--count", type=int, default=600)
    parser.add_argument("--anomaly-rate", type=float, default=0.30)
    parser.add_argument("--hard-negative-rate", type=float, default=0.12)
    parser.add_argument("--seed", type=int, default=20260807,
                        help="same seed, same corpus - keep it in the run record")
    parser.add_argument("--out", default="synthetic_invoices.jsonl")
    args = parser.parse_args(argv)

    records = generate(args.count, args.anomaly_rate, args.seed, args.hard_negative_rate)
    write_jsonl(records, args.out)

    counts = {}
    for record in records:
        for kind in record["labels"]["anomaly_types"]:
            counts[kind] = counts.get(kind, 0) + 1
    anomalous = sum(r["labels"]["is_anomalous"] for r in records)
    intrinsic = sum(r["labels"]["is_intrinsic_anomaly"] for r in records)
    negatives = sum(r["labels"]["hard_negative"] for r in records)

    print(f"\n{len(records)} records -> {args.out}")
    print(f"  {anomalous} anomalous ({anomalous / len(records):.1%}), "
          f"{intrinsic} of them detectable from the document alone")
    print(f"  {negatives} hard negatives (clean, but shaped like trouble)")
    print("  by type:")
    for kind, total in sorted(counts.items(), key=lambda item: -item[1]):
        scope = "intrinsic" if kind in INTRINSIC_ANOMALIES else "needs peers"
        print(f"    {kind:24s} {total:5d}   ({scope})")
    print("\n  Measured on this data, precision and recall are a statement about "
          "this generator,\n  not about real invoices. Quote the held-out-type "
          "numbers, not these.\n")


if __name__ == "__main__":
    main()
