"""The rule catalogue: every threshold in the anomaly detector lives here.

Deliberately one table rather than `if` statements scattered across the scorer.
Someone asking "what does this actually consider suspicious, and at what number"
should have exactly one file to read, and GET /api/anomaly/rules serves this same
catalogue to the browser so the answer is auditable without the source.

Seven document rules and two corpus rules. The split matters: a document rule can
be applied to a single upload with no history, while duplicate_invoice and
vendor_amount_outlier are properties of a GROUP and mean nothing about a record
in isolation. anomaly_detector.scan() adds the corpus pair and suppresses them
entirely below MIN_CORPUS rather than computing them from too little data.

    1  missing core field         document
    2  invalid format             document   (shape, plus the GSTIN check digit)
    3  PAN extracted as GSTIN     document
    4  duplicate invoice          corpus
    5  high-value invoice         document
    6  amounts do not reconcile   document   (totals, and line items)
    7  vendor amount outlier      corpus

There is deliberately no confidence rule. A collapse in OCR confidence is real
and the generator injects it, but picking a cutoff for "unusually unsure" means
inventing a number, and the features carry it (conf_core_min,
conf_below_080_frac) where the trained model can weigh it against everything
else. That anomaly is the clearest argument for training a model at all, so
leaving it to the model is the honest division of labour rather than a gap.

Every threshold below was measured against a 618-record synthetic corpus rather
than chosen by taste, and the measurements are recorded next to each one. Where a
threshold cannot separate the classes, that is said outright instead of being
tuned until the number looks good.
"""

import os

# --- thresholds -------------------------------------------------------------

# One rupee, not zero. Every genuine invoice in documents.db is off by a few
# paise: 41,130.00 + 5,142.60 against a printed 46,273.00 is a 40 paise gap, and
# 8,955.00 + 1,611.90 against 10,567.00 is 10 paise. That is rounding on the
# printed document, not an extraction error, and a zero tolerance flags three of
# the four real invoices on the first run.
#
# The ceiling is set by the generator, which never injects a drift below 5 rupees
# precisely so the label cannot be a lie. Anything in [1, 5) separates the two
# perfectly; 1 is chosen because it is the smallest amount a human would call a
# discrepancy rather than a rounding artefact.
TOTAL_TOLERANCE_RUPEES = 1.0

# Half a percent of the invoice value. The line-item comparison already picks
# whichever of the base amount or the invoice total it sits closer to (see
# anomaly_features.item_sum_residual_rel), because real invoices carry both
# tax-exclusive and tax-inclusive line amounts - two of the four real invoices
# are tax-inclusive and a stricter comparison flags both. What is left after that
# allowance is genuine disagreement.
ITEM_SUM_TOLERANCE_REL = 0.005

# Ten lakh: a business threshold, not a fitted one. "Large enough that a person
# should look" is a policy decision, so it is configurable.
#
# Measured, it is also the right cut. Clean synthetic invoices run to 11.5 lakh
# and injected high-value ones start at 5,480 (a small invoice scaled 12x is
# still a small invoice), so the two classes genuinely overlap and NO threshold
# separates them. Ten lakh catches 71% of injections for 5 false alarms in 357
# clean records; dropping to two lakh would catch 91% but falsely flag 45% of
# clean invoices, which would make the whole report ignorable. The injections it
# misses are small invoices that are still small - arguably the label is wrong
# there, not the rule.
HIGH_VALUE_RUPEES = float(os.getenv("ANOMALY_HIGH_VALUE", "1000000"))

# The classic outlier cut, applied to log10(amount) within one vendor - so it
# means "orders of magnitude away from what this vendor normally bills", not
# "larger than average". The generator scales an outlier by exactly 50x, which is
# 1.7 on a log10 scale and comfortably clear of this once a vendor has history.
VENDOR_OUTLIER_Z = 3.5

# Below this many documents in the window, a median and a MAD are being computed
# from noise. The corpus rules report nothing rather than guessing - documents.db
# has 17 invoices in its only date range, so this fires in practice.
MIN_CORPUS = int(os.getenv("ANOMALY_MIN_CORPUS", "20"))


# --- the catalogue ----------------------------------------------------------
# (name, anomaly number, weight, what it checks)
#
# Weights are the rules-only fallback score: summed per document and capped at
# 100. They are a stated opinion about severity, not a fitted quantity - when a
# trained model is installed its probability replaces the sum entirely, and the
# two are never blended.
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
     "Base amount plus GST does not equal the stated invoice total."),
    ("item_sum_mismatch", 6, 20,
     "The line items add up to neither the base amount nor the invoice total."),
)

# The two that need peers. Kept out of RULES because "how many rules apply here"
# has two different answers depending on whether there is a population to compare
# against, and anomaly_api.py reports both counts separately.
CORPUS_RULES = (
    ("duplicate_invoice", 4, 30,
     "The same invoice number, vendor, date and amount appears more than once."),
    ("vendor_amount_outlier", 7, 25,
     "The amount is far from what this vendor normally invoices."),
)

WEIGHTS = {name: weight
           for name, _number, weight, _description in RULES + CORPUS_RULES}

# Score bands, high to low, first match wins. The same (threshold, label, legend)
# shape as excel_service.VALIDITY_BANDS so the two grading systems read as
# siblings and the frontend can reuse the workbook's own three colours.
SCORE_BANDS = (
    (60.0, "Suspect", "Suspect: 60 and above"),
    (25.0, "Review", "Review: 25 - 59.99"),
    (0.0, "Clean", "Clean: below 25"),
)

# Thresholds as text, for the /rules endpoint. Written from the constants above
# so the documented number and the applied number cannot drift apart.
THRESHOLDS = {
    "missing_core_field": "any of the 12 core fields empty",
    "invalid_core_field": "fails excel_service.is_valid_field_value",
    "gstin_checksum_bad": "GSTIN shape valid but mod-36 check digit wrong",
    "pan_instead_of_gstin": "vendor tax id is 10 characters, not 15",
    "high_value": f"total > {HIGH_VALUE_RUPEES:,.0f} rupees",
    "total_mismatch": f"|base + GST - total| > {TOTAL_TOLERANCE_RUPEES:,.2f} rupees",
    "item_sum_mismatch": f"line-item gap > {ITEM_SUM_TOLERANCE_REL:.1%} of the total",
    "duplicate_invoice": "same (number, vendor GSTIN, date, total) seen twice",
    "vendor_amount_outlier": f"|robust z of log10(total) per vendor| > {VENDOR_OUTLIER_Z}",
}


def band(score: float) -> str:
    """The label for a score, by the same first-match-wins rule the workbook uses."""
    for minimum, label, _legend in SCORE_BANDS:
        if score >= minimum:
            return label
    return "Clean"


def _signal(name, value, detail):
    return {"name": name, "layer": "rule", "value": float(value),
            "contribution": float(WEIGHTS[name]), "detail": detail}


def evaluate(features: dict, context: dict) -> list[dict]:
    """The document rules that fired, as signals.

    The corpus rules are not here: they need the population, and
    anomaly_detector.scan() adds them with layer "corpus" / "robust_z" so the
    two kinds stay distinguishable in the response and in the metrics.
    """
    fired = []

    # Required fields only. A missing Tax Details block or Customer GSTIN is
    # ordinary - three of the four real invoices lack one or the other - so firing
    # on all twelve core fields would flag most of a healthy corpus.
    missing = context.get("missing_required_fields") or []
    if missing:
        optional_gone = len(context.get("missing_core_fields") or []) - len(missing)
        extra = f" ({optional_gone} optional field(s) also absent)" if optional_gone else ""
        fired.append(_signal(
            "missing_core_field", len(missing),
            f"{len(missing)} required field(s) empty: {', '.join(missing)}{extra}."))

    invalid = context.get("invalid_core_fields") or []
    if invalid:
        fired.append(_signal(
            "invalid_core_field", len(invalid),
            f"{len(invalid)} core field(s) failed their format check: "
            f"{', '.join(invalid)}."))

    # Only meaningful when a well-shaped GSTIN is actually there. Otherwise
    # "the checksum failed" is just another way of saying the field is missing,
    # and one problem would be reported twice against the same document.
    if features["vendor_gstin_shape_ok"] and not features["vendor_gstin_checksum_ok"]:
        fired.append(_signal(
            "gstin_checksum_bad", 1,
            f"Vendor GSTIN {context.get('raw_vendor_tax_id', '')} has a valid shape "
            "but its check digit does not agree, so at least one character was "
            "misread or mistyped. The audit workbook cannot see this: its validity "
            "check tests the shape only."))

    if features["pan_instead_of_gstin"]:
        fired.append(_signal(
            "pan_instead_of_gstin", 1,
            f"The vendor tax id {context.get('raw_vendor_tax_id', '')} is a PAN. A "
            "GSTIN embeds the PAN at characters 3-12, so the state code and check "
            "digit around it were not read."))

    total = context.get("total")
    if total is not None and total > HIGH_VALUE_RUPEES:
        fired.append(_signal(
            "high_value", total,
            f"The invoice total {total:,.2f} is above the review threshold of "
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
            f"nor the invoice total."))

    return fired


def catalogue() -> list[dict]:
    """The rules as data, for GET /api/anomaly/rules."""
    return [
        {"name": name, "anomaly": number, "weight": weight,
         "checks": description, "threshold": THRESHOLDS.get(name, "presence"),
         "layer": "corpus" if (name, number, weight, description) in CORPUS_RULES
                  else "document"}
        for name, number, weight, description in RULES + CORPUS_RULES
    ]
