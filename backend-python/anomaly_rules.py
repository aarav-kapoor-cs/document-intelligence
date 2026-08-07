"""The rule catalogue: every threshold in the anomaly detector lives here.

Deliberately one table rather than scattered `if` statements. A reader who wants
to know "what does this product actually consider suspicious, and at what
number" should have exactly one file to read, and /api/anomaly/rules serves this
same catalogue to the browser so the answer is auditable without the source.

Seven anomalies, in the order they were scoped. Five are document-intrinsic -
computable from a single invoice, so they work on one upload with no history.
Two are corpus-relative and need a population, so they are computed by
anomaly_detector.py over a scan and are suppressed rather than guessed when the
corpus is too small.

    1  missing core field         intrinsic
    2  invalid format             intrinsic   (shape + GSTIN check digit)
    3  PAN extracted as GSTIN     intrinsic
    4  duplicate invoice          corpus
    5  high-value invoice         intrinsic
    6  amounts do not reconcile   intrinsic   (totals + line items)
    7  vendor amount outlier      corpus

Three of the thresholds below exist because the naive version fired on real,
correct invoices in documents.db. Those are marked. They are not arbitrary
softening - each one has a specific invoice behind it.
"""

import os

# --- thresholds -------------------------------------------------------------

# One rupee, not zero. Every genuine invoice in the database is off by a few
# paise: 41,130.00 + 5,142.60 vs a printed 46,273.00 is a 40 paise gap, and
# 8,955.00 + 1,611.90 vs 10,567.00 is 10 paise. Those are rounding on the
# printed document, not extraction errors. A zero tolerance flags them all.
TOTAL_TOLERANCE_RUPEES = 1.0

# Half a percent of the invoice value. The line-item comparison is already
# generous about WHICH base it compares to (see anomaly_features), so what is
# left is genuine disagreement.
ITEM_SUM_TOLERANCE_REL = 0.005

# Business threshold, not a learned one - "large enough that a human should look"
# is a policy decision, so it is configurable and defaults to ten lakh.
HIGH_VALUE_RUPEES = float(os.getenv("ANOMALY_HIGH_VALUE", "1000000"))

# A robust z of 3.5 is roughly the classic outlier cut. Applied to
# log10(amount) within one vendor, so it means "orders of magnitude off this
# vendor's normal", not "bigger than average".
VENDOR_OUTLIER_Z = 3.5

# Below this many documents in the scan window, median/MAD is being computed
# from noise and the corpus rules report nothing instead of guessing.
MIN_CORPUS = int(os.getenv("ANOMALY_MIN_CORPUS", "20"))


# --- the catalogue ----------------------------------------------------------
# (name, anomaly number, weight, one-line description of what it checks)
#
# Weights are the rules-only fallback score, summed and capped at 100. They are
# a stated opinion about severity, not a fitted quantity - when the trained model
# is installed its probability replaces this sum entirely.
RULES = (
    ("missing_core_field", 1, 20,
     "A field the audit treats as core is empty."),
    ("invalid_core_field", 2, 15,
     "A populated core field does not match its expected format."),
    ("gstin_checksum_bad", 2, 8,
     "The vendor GSTIN is correctly shaped but its check digit disagrees."),
    ("pan_instead_of_gstin", 3, 25,
     "The vendor tax id is a 10-character PAN where a 15-character GSTIN belongs."),
    ("high_value", 5, 15,
     f"The invoice total is above the review threshold of {HIGH_VALUE_RUPEES:,.0f}."),
    ("total_mismatch", 6, 30,
     "Base amount plus GST does not equal the invoice total."),
    ("item_sum_mismatch", 6, 20,
     "The line items do not add up to the base amount or the invoice total."),
)

# The two that need peers. Kept separate from RULES because a single uploaded
# document can be scored against every rule above and against neither of these,
# so "how many rules apply here" has two different answers depending on whether
# there is a population to compare with.
CORPUS_RULES = (
    ("duplicate_invoice", 4, 30,
     "The same invoice number, vendor, date and amount appears more than once."),
    ("vendor_amount_outlier", 7, 25,
     "The amount is far from what this vendor normally invoices."),
)

WEIGHTS = {name: weight
           for name, _number, weight, _description in RULES + CORPUS_RULES}

# Score bands, ordered high to low, first match wins. The same (threshold, label,
# legend) shape as excel_service.VALIDITY_BANDS so the two grading systems read
# as siblings and the frontend can reuse the workbook's own three colours.
SCORE_BANDS = (
    (60.0, "Suspect", "Suspect: 60 and above"),
    (25.0, "Review", "Review: 25 - 59.99"),
    (0.0, "Clean", "Clean: below 25"),
)


def band(score: float) -> str:
    for minimum, label, _legend in SCORE_BANDS:
        if score >= minimum:
            return label
    return "Clean"


def _signal(name, value, detail):
    return {"name": name, "layer": "rule", "value": float(value),
            "contribution": float(WEIGHTS[name]), "detail": detail}


def evaluate(features: dict, context: dict) -> list[dict]:
    """The document-intrinsic rules that fired, as signals.

    Corpus rules (duplicate_invoice, vendor_amount_outlier) are not here: they
    need the population and are added by anomaly_detector.py.
    """
    fired = []

    missing = context.get("missing_core_fields") or []
    if missing:
        fired.append(_signal(
            "missing_core_field", len(missing),
            f"{len(missing)} core field(s) empty: {', '.join(missing)}."))

    invalid = context.get("invalid_core_fields") or []
    if invalid:
        fired.append(_signal(
            "invalid_core_field", len(invalid),
            f"{len(invalid)} core field(s) failed their format check: "
            f"{', '.join(invalid)}."))

    # Only meaningful when a well-shaped GSTIN is actually present - otherwise
    # "checksum failed" would just be another way of saying the field is missing,
    # and the same document would be reported twice for one problem.
    if features["vendor_gstin_shape_ok"] and not features["vendor_gstin_checksum_ok"]:
        fired.append(_signal(
            "gstin_checksum_bad", 1,
            f"Vendor GSTIN {context.get('raw_vendor_tax_id', '')} has a valid shape "
            "but its check digit does not agree, so at least one character is wrong."))

    if features["pan_instead_of_gstin"]:
        fired.append(_signal(
            "pan_instead_of_gstin", 1,
            f"The vendor tax id {context.get('raw_vendor_tax_id', '')} is a PAN. "
            "A GSTIN embeds the PAN at characters 3-12, so the surrounding state "
            "code and check digit were not read."))

    total = context.get("total")
    if total is not None and total > HIGH_VALUE_RUPEES:
        fired.append(_signal(
            "high_value", total,
            f"The invoice total {total:,.2f} is above the review threshold "
            f"{HIGH_VALUE_RUPEES:,.0f}."))

    if features["total_residual_abs"] > TOTAL_TOLERANCE_RUPEES:
        fired.append(_signal(
            "total_mismatch", features["total_residual_abs"],
            f"Base {context.get('subtotal'):,.2f} plus GST {context.get('tax'):,.2f} "
            f"is {features['total_residual_abs']:,.2f} away from the stated total "
            f"{total:,.2f}."))

    if features["item_sum_residual_rel"] > ITEM_SUM_TOLERANCE_REL:
        fired.append(_signal(
            "item_sum_mismatch", features["item_sum_residual_rel"],
            f"The {int(features['items_count'])} line items sum to "
            f"{context.get('item_sum'):,.2f}, which matches neither the base amount "
            "nor the invoice total."))

    return fired


def catalogue() -> list[dict]:
    """The rules as data, for GET /api/anomaly/rules."""
    thresholds = {
        "total_mismatch": f"> {TOTAL_TOLERANCE_RUPEES:,.2f} rupees",
        "item_sum_mismatch": f"> {ITEM_SUM_TOLERANCE_REL:.1%} of the invoice total",
        "high_value": f"> {HIGH_VALUE_RUPEES:,.0f} rupees",
        "vendor_amount_outlier": f"|robust z| > {VENDOR_OUTLIER_Z}",
        "duplicate_invoice": "same (number, vendor GSTIN, date, total)",
    }
    return [
        {"name": name, "anomaly": number, "weight": weight,
         "checks": description, "threshold": thresholds.get(name, "presence"),
         "layer": "corpus" if name in ("duplicate_invoice", "vendor_amount_outlier")
                  else "document"}
        for name, number, weight, description in RULES + CORPUS_RULES
    ]
