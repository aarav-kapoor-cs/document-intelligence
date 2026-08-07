"""Score documents for anomalies: rules, robust statistics, and the trained model.

Three layers, kept separate on purpose so each can be measured against the next
(anomaly_metrics.py does exactly that):

    L1  rules          deterministic, no training, works on the first upload
    L2  robust stats   median/MAD over the scan population
    L3  model          logistic regression trained in Azure ML

The reported score is 100 x P(anomaly) when the trained model is installed, and
the capped sum of rule weights when it is not. Those two are never blended: a
probability and a z-score are different quantities, and averaging them would
produce a number that means nothing in either scale. Every response says which
one it used, in `method`.

The model arrives as anomaly_model.json - coefficients, not a pickle. Logistic
regression inference is sigmoid(z.w + b) after standardization, so exporting the
scaler and the weights reproduces scikit-learn's predict_proba exactly while
letting this file score with a numpy dot product. That is why the backend needs
no scikit-learn: numpy was already pinned for the median/MAD work below.

It also means explanation is free. Each feature's contribution to the log-odds is
w_i * z_i, so the breakdown the API returns is the model's own arithmetic rather
than a separate explainer that might disagree with it.
"""

import argparse
import json
import logging
import math
import os
from collections import defaultdict

import numpy

import anomaly_features
import anomaly_rules
import database
import excel_service

logger = logging.getLogger(__name__)

# 0.6745 is the 75th percentile of the standard normal: dividing the MAD by it
# puts the scale on the same footing as a standard deviation, so a z of 3.5 here
# means what it usually means.
MAD_TO_SIGMA = 0.6745

# A vendor needs at least this many invoices before "normal for this vendor"
# is a real statement rather than a restatement of the one invoice we have.
MIN_VENDOR_HISTORY = 5

MODEL_FILENAME = "anomaly_model.json"


def model_path() -> str:
    """Where the exported coefficients are read from.

    ANOMALY_MODEL_JSON wins so a workspace can point at a model it fetched
    elsewhere; otherwise the file sits next to this module, which is where
    fetch_model.py puts it and where the office-laptop ZIP carries it.
    """
    configured = os.getenv("ANOMALY_MODEL_JSON", "").strip()
    if configured:
        return os.path.expanduser(configured)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), MODEL_FILENAME)


# --- layer 2: robust statistics ---------------------------------------------

def robust_z(values: list[float]) -> list | None:
    """Robust z-scores, or None when the population has no usable spread.

    Median and MAD rather than mean and standard deviation, because the outlier
    being hunted would otherwise inflate the very scale used to judge it.

    Returns None instead of infinity when the spread is zero - which happens
    constantly at small n, where several identical values are the norm rather
    than the exception. A caller that gets None should say so; faking a number
    here would put an unearned figure in an audit report.
    """
    if len(values) < 2:
        return None
    series = numpy.asarray(values, dtype=float)
    median = float(numpy.median(series))
    mad = float(numpy.median(numpy.abs(series - median)))
    if mad > 0:
        scale = mad / MAD_TO_SIGMA
    else:
        # Every value identical, or a tie-heavy population. The IQR is a coarser
        # but sometimes non-zero fallback; if it is also zero there is genuinely
        # no spread to measure against.
        q75, q25 = numpy.percentile(series, [75, 25])
        spread = float(q75 - q25)
        if spread <= 0:
            return None
        scale = spread / 1.349
    return [float((value - median) / scale) for value in series]


# --- layer 3: the trained model ---------------------------------------------

class ModelMismatch(Exception):
    """The artifact was trained against a different feature list."""


def load_model(path: str | None = None) -> dict | None:
    """The exported coefficients, or None when no usable artifact is installed.

    A missing model is a normal state, not an error: the rules work without it,
    and the office laptop may never have run a training job. A model whose
    feature list disagrees with anomaly_features.FEATURE_NAMES is different - the
    coefficients are positional, so applying them anyway would score every
    document against the wrong weights, silently and plausibly. That raises.
    """
    path = path or model_path()
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        model = json.load(handle)

    names = tuple(model.get("feature_names", ()))
    if names != anomaly_features.FEATURE_NAMES:
        raise ModelMismatch(
            f"{os.path.basename(path)} was trained on {len(names)} features that do "
            f"not match the {len(anomaly_features.FEATURE_NAMES)} this code builds. "
            "Retrain and re-export, or the coefficients apply to the wrong columns.")
    return model


def apply_model(model: dict, features: dict) -> tuple[float, list[dict]]:
    """(probability, per-feature contributions) - scikit-learn's own arithmetic."""
    raw = numpy.asarray(anomaly_features.vector(features), dtype=float)
    medians = numpy.asarray(model["feature_medians"], dtype=float)
    raw = numpy.where(numpy.isfinite(raw), raw, medians)

    mean = numpy.asarray(model["scaler_mean"], dtype=float)
    scale = numpy.asarray(model["scaler_scale"], dtype=float)
    # scikit-learn sets scale_ to 1.0 for zero-variance columns; mirror that
    # rather than dividing by zero.
    scale = numpy.where(scale == 0, 1.0, scale)
    standardized = (raw - mean) / scale

    weights = numpy.asarray(model["coef"], dtype=float)
    contributions = standardized * weights
    log_odds = float(contributions.sum()) + float(model["intercept"])
    probability = 1.0 / (1.0 + math.exp(-max(-500.0, min(500.0, log_odds))))

    signals = []
    for name, contribution, value in zip(
            anomaly_features.FEATURE_NAMES, contributions, raw):
        # Only the features actually pushing this document towards "anomalous"
        # are worth showing; the rest are the model confirming normality.
        if contribution > 0.05:
            signals.append({
                "name": name, "layer": "model", "value": float(value),
                "contribution": float(contribution),
                "detail": f"{name} = {value:g} raised the model's log-odds by "
                          f"{contribution:.2f}.",
            })
    signals.sort(key=lambda signal: signal["contribution"], reverse=True)
    return probability, signals


# --- putting it together ----------------------------------------------------

def _corpus_signals(scored: list[dict], notes: list[str]) -> None:
    """Add the two corpus rules in place: duplicates and vendor outliers."""
    if len(scored) < anomaly_rules.MIN_CORPUS:
        notes.append(
            f"Corpus checks (duplicate invoice, vendor amount outlier) need at least "
            f"{anomaly_rules.MIN_CORPUS} documents; this window has {len(scored)}. "
            "They were not computed rather than estimated from too little data.")
        return

    # -- duplicates. Identity is the whole tuple, not the number alone: the same
    # invoice number from a different vendor is a different invoice, and it is
    # the repeat of number + vendor + date + amount that means "processed twice".
    groups = defaultdict(list)
    for entry in scored:
        context = entry["context"]
        total = context.get("total")
        key = (context.get("invoice_number", ""), context.get("vendor_gstin", ""),
               context.get("invoice_date", ""),
               round(total, 2) if total is not None else None)
        if key[0]:
            groups[key].append(entry)
    for key, members in groups.items():
        if len(members) < 2:
            continue
        files = sorted({entry["source_file"] for entry in members})
        for entry in members:
            # layer "corpus", not "rule": anomaly_metrics.py separates the two so
            # that a hard negative flagged only because it has peers is not
            # counted as a rule false alarm.
            entry["signals"].append({
                "name": "duplicate_invoice", "layer": "corpus", "value": float(len(members)),
                "contribution": float(anomaly_rules.WEIGHTS["duplicate_invoice"]),
                "detail": f"Invoice {key[0]} appears {len(members)} times with the same "
                          f"vendor, date and amount, across: {', '.join(files)}.",
            })

    # -- vendor amount outliers, on log10 so the comparison is proportional:
    # a vendor whose invoices run in thousands and one that runs in lakhs should
    # be judged by how far off their own scale a document is.
    by_vendor = defaultdict(list)
    for entry in scored:
        gstin = entry["context"].get("vendor_gstin")
        total = entry["context"].get("total")
        if gstin and total is not None and total > 0:
            by_vendor[gstin].append(entry)

    for gstin, members in by_vendor.items():
        if len(members) < MIN_VENDOR_HISTORY:
            continue
        amounts = [math.log10(entry["context"]["total"]) for entry in members]
        scores = robust_z(amounts)
        if scores is None:
            notes.append(f"Vendor {gstin}: every invoice is the same amount, so there "
                         "is no spread to measure an outlier against.")
            continue
        for entry, z in zip(members, scores):
            entry["robust_z"] = z
            if abs(z) > anomaly_rules.VENDOR_OUTLIER_Z:
                entry["signals"].append({
                    "name": "vendor_amount_outlier", "layer": "robust_z", "value": float(z),
                    "contribution": float(anomaly_rules.WEIGHTS["vendor_amount_outlier"]),
                    "detail": f"At {entry['context']['total']:,.2f} this is {abs(z):.1f} "
                              f"robust deviations from what vendor {gstin} normally "
                              f"invoices across {len(members)} documents.",
                })


def scan(records: list[dict], model: dict | None = None) -> dict:
    """Score a population of documents. Records are database.get_filtered() rows."""
    notes = []
    if model is None:
        try:
            model = load_model()
        except ModelMismatch as mismatch:
            notes.append(str(mismatch))
            model = None
    if model is None:
        notes.append("No trained model installed - scoring with the deterministic "
                     "rules only. Run the Azure ML job and fetch_model.py to enable "
                     "the learned score.")

    scored = []
    for record in records:
        fields = excel_service.parse_key_values_json(record.get("fields", {}))
        features, context = anomaly_features.build(fields)
        scored.append({
            "document_id": record.get("id"),
            "source_file": record.get("fileName", ""),
            "features": features,
            "context": context,
            "signals": anomaly_rules.evaluate(features, context),
            "robust_z": None,
        })

    _corpus_signals(scored, notes)

    results = []
    for entry in scored:
        signals = entry["signals"]
        if model is not None:
            probability, model_signals = apply_model(model, entry["features"])
            score = 100.0 * probability
            method = "logistic_regression"
            signals = signals + model_signals
        else:
            score = min(100.0, sum(signal["contribution"] for signal in signals))
            method = "rules_only"
        results.append({
            "document_id": entry["document_id"],
            "source_file": entry["source_file"],
            "invoice_number": entry["context"].get("invoice_number", ""),
            "score": round(score, 2),
            "band": anomaly_rules.band(score),
            "method": method,
            "signals": signals,
            "features": entry["features"],
        })

    results.sort(key=lambda result: result["score"], reverse=True)
    # These key names are the AnomalyScanResponse contract in models.py. FastAPI
    # filters the response through that model, so a key spelled differently here
    # is not an error - it is silently dropped and the field falls back to its
    # default, which is how "corpus_checks_ran" could read false while the checks
    # had in fact run. Change both or neither.
    return {
        "documents": len(results),
        "corpus_n": len(results),
        "corpus_checks_ran": len(results) >= anomaly_rules.MIN_CORPUS,
        "method": "logistic_regression" if model is not None else "rules_only",
        "model_version": (model or {}).get("run_id", ""),
        "results": results,
        "notes": notes,
    }


def score_document(fields, model: dict | None = None) -> dict:
    """One document, scored on the layers that a single document supports.

    Corpus rules (duplicates, vendor outliers) are not applied here - they need
    peers, and scan() adds them. This is exactly why the model uses
    document-intrinsic features only: a document scored alone and the same
    document scored inside a batch produce the identical learned probability.
    """
    outcome = scan([{"id": None, "fileName": "", "fields": fields}], model=model)
    result = outcome["results"][0]
    result["notes"] = outcome["notes"]
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Score saved documents for anomalies (no Azure calls).")
    parser.add_argument("--from", dest="from_date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--to", dest="to_date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--doc-type", default="prebuilt-invoice")
    parser.add_argument("--all", action="store_true",
                        help="show every document, not just the ones that scored")
    args = parser.parse_args()

    records = database.get_filtered(args.from_date, args.to_date, args.doc_type)
    if not records:
        raise SystemExit("No documents were saved in that date range.")

    outcome = scan(records)
    print(f"{outcome['documents']} document(s), scored by {outcome['results'][0]['method']}"
          if outcome["results"] else "nothing to score")
    for note in outcome["notes"]:
        print(f"  note: {note}")
    print()

    for result in outcome["results"]:
        if not args.all and not result["signals"]:
            continue
        print(f"[{result['band']:<7} {result['score']:>6.2f}] "
              f"{result['invoice_number'] or '(no number)'}  {result['source_file']}")
        for signal in result["signals"]:
            print(f"    - {signal['name']}: {signal['detail']}")
        print()

    flagged = sum(1 for result in outcome["results"] if result["signals"])
    print(f"{flagged} of {outcome['documents']} document(s) raised at least one signal.")


if __name__ == "__main__":
    main()
