"""Train the anomaly model in Azure ML and export it as plain coefficients.

Run in the cloud:
    az ml job create -f azureml/job.yml

Run locally first, which is the sane order - debugging a stack trace in a queue
is miserable:
    cd backend-python
    python synthetic_invoices.py --count 600 --out ../azureml/data/syn.jsonl
    PYTHONPATH=. python ../azureml/train.py --data ../azureml/data/syn.jsonl --out /tmp/model

Two decisions shape this file.

**It imports anomaly_features from backend-python rather than owning a copy.**
That is why job.yml uploads the repo root instead of just this folder. A vendored
copy would drift, and the day it drifted the exported coefficients would be fitted
to one definition of "conf_core_min" and applied to another, with nothing failing
loudly.

**It exports JSON, not a pickle.** Applying a logistic regression to standardized
features is sigmoid(z . w + b) - so mean, scale, coefficients and intercept are a
complete description of the fitted model, reproducible to float precision with a
numpy dot product. The backend therefore needs no scikit-learn, no mlflow and no
azure-ai-ml; nothing is added to requirements.txt at all. It also means the
artifact is four kilobytes of readable numbers that can be committed and can
travel in a ZIP to a laptop where pip installs are awkward.

Four arms are scored on identical data, and the interesting comparison is not
"did the model work" but "did it beat the rules". If it did not, that is the
finding worth reporting, not a failure to hide.
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

import numpy as np

# backend-python holds anomaly_features/rules/detector. job.yml puts it on
# PYTHONPATH; this fallback makes a local run work without ceremony.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "backend-python"))

import anomaly_detector          # noqa: E402
import anomaly_features          # noqa: E402
import anomaly_rules             # noqa: E402
import synthetic_invoices        # noqa: E402

from sklearn.ensemble import IsolationForest                     # noqa: E402
from sklearn.linear_model import LogisticRegression              # noqa: E402
from sklearn.metrics import (average_precision_score, f1_score,  # noqa: E402
                             precision_score, recall_score, roc_auc_score)
from sklearn.model_selection import GridSearchCV, train_test_split  # noqa: E402
from sklearn.preprocessing import StandardScaler                 # noqa: E402

try:
    import mlflow
except ImportError:  # a local run without mlflow installed should still work
    mlflow = None


def log_metric(name, value):
    if mlflow:
        mlflow.log_metric(name, float(value))


def log_param(name, value):
    if mlflow:
        mlflow.log_param(name, value)


def load_jsonl(path):
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def build_matrix(records):
    """Records -> (X, y, types). X is n x len(FEATURE_NAMES), all floats.

    The label is is_intrinsic_anomaly, NOT is_anomalous. A duplicated invoice and
    a vendor outlier are properties of a group: the document itself looks
    completely ordinary, so training a per-document model on those labels would
    be asking it to guess, and it would learn to guess from whatever spurious
    thing correlates. Those two are the corpus layer's job and are measured there.
    """
    features, labels, types = [], [], []
    for record in records:
        built, _context = anomaly_features.build(record["fields"])
        features.append(anomaly_features.vector(built))
        labels.append(int(record["labels"]["is_intrinsic_anomaly"]))
        types.append(record["labels"]["anomaly_types"])
    return np.asarray(features, dtype=float), np.asarray(labels, dtype=int), types


def rules_predictions(records):
    """The rules arm: did any deterministic rule fire, per record."""
    fired = []
    for record in records:
        result = anomaly_detector.score_document(record["fields"])
        fired.append(int(any(s["layer"] == "rule" for s in result["signals"])))
    return np.asarray(fired, dtype=int)


def score_arm(name, truth, predicted, scores=None) -> dict:
    """Precision/recall/f1 (+ ranking metrics when a continuous score exists)."""
    metrics = {
        "precision": float(precision_score(truth, predicted, zero_division=0)),
        "recall": float(recall_score(truth, predicted, zero_division=0)),
        "f1": float(f1_score(truth, predicted, zero_division=0)),
    }
    if scores is not None and len(set(truth)) > 1:
        metrics["average_precision"] = float(average_precision_score(truth, scores))
        metrics["roc_auc"] = float(roc_auc_score(truth, scores))
    for key, value in metrics.items():
        log_metric(f"{name}_{key}", value)
    return metrics


def pick_threshold(truth, scores, target_precision=0.90) -> float:
    """The lowest cutoff that still reaches the target precision.

    Not max-F1, which is the usual default. An audit false alarm costs a person
    the time to open a document and find nothing wrong, so precision is worth
    more here than the extra recall a balanced threshold would buy. If no cutoff
    reaches the target, fall back to the best F1 rather than returning something
    that flags everything.
    """
    best_f1, best_cut, chosen = -1.0, 0.5, None
    for cut in np.unique(np.round(scores, 3)):
        predicted = (scores >= cut).astype(int)
        if predicted.sum() == 0:
            continue
        precision = precision_score(truth, predicted, zero_division=0)
        f1 = f1_score(truth, predicted, zero_division=0)
        if f1 > best_f1:
            best_f1, best_cut = f1, float(cut)
        if precision >= target_precision and chosen is None:
            chosen = float(cut)
    return chosen if chosen is not None else best_cut


def main(argv=None):
    parser = argparse.ArgumentParser(description="Train the invoice anomaly model.")
    parser.add_argument("--data", required=True, help="JSONL from synthetic_invoices.py")
    parser.add_argument("--out", required=True, help="folder to write anomaly_model.json into")
    parser.add_argument("--seed", type=int, default=20260807)
    parser.add_argument("--test-size", type=float, default=0.3)
    parser.add_argument("--target-precision", type=float, default=0.90)
    parser.add_argument(
        "--holdout-types", default="gstin_checksum_bad,item_sum_mismatch",
        help="anomaly types kept OUT of training, to measure generalisation")
    args = parser.parse_args(argv)

    records = load_jsonl(args.data)
    X, y, types = build_matrix(records)
    names = anomaly_features.FEATURE_NAMES

    log_param("seed", args.seed)
    log_param("rows", len(records))
    log_param("feature_count", len(names))
    # A hash of the feature list, so a run can be matched to the code that made
    # it even if someone edits FEATURE_NAMES later.
    log_param("feature_hash",
              hashlib.blake2s(",".join(names).encode(), digest_size=8).hexdigest())

    # Impute before scaling, and keep the medians: serving has to fill gaps the
    # same way or the same document scores differently in the two places.
    medians = np.nanmedian(np.where(np.isfinite(X), X, np.nan), axis=0)
    medians = np.where(np.isfinite(medians), medians, 0.0)
    X = np.where(np.isfinite(X), X, medians)

    # --- split (a): ordinary stratified holdout ------------------------------
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=args.test_size, random_state=args.seed, stratify=y)

    scaler = StandardScaler().fit(X_train)
    search = GridSearchCV(
        LogisticRegression(class_weight="balanced", max_iter=2000),
        {"C": [0.01, 0.1, 1.0, 10.0]},
        scoring="average_precision", cv=5)
    search.fit(scaler.transform(X_train), y_train)
    model = search.best_estimator_
    log_param("C", search.best_params_["C"])

    probabilities = model.predict_proba(scaler.transform(X_test))[:, 1]
    threshold = pick_threshold(y_test, probabilities, args.target_precision)
    log_param("threshold", threshold)

    metrics = {"logistic_regression": score_arm(
        "lr", y_test, (probabilities >= threshold).astype(int), probabilities)}

    # --- comparison arms, same test rows -------------------------------------
    # IsolationForest is fitted on clean rows only, which is the correct
    # unsupervised setup: learn what normal looks like, then score everything.
    # It is logged and never exported - it answers "would an unsupervised model
    # have done better", and shipping it would mean a scikit-learn dependency in
    # the backend for a model trained on a few hundred synthetic rows.
    forest = IsolationForest(random_state=args.seed).fit(X_train[y_train == 0])
    forest_scores = -forest.score_samples(X_test)
    metrics["isolation_forest"] = score_arm(
        "iforest", y_test, (forest.predict(X_test) == -1).astype(int), forest_scores)

    test_indices = train_test_split(
        np.arange(len(y)), test_size=args.test_size,
        random_state=args.seed, stratify=y)[1]
    rules_fired = rules_predictions([records[i] for i in test_indices])
    metrics["rules_only"] = score_arm("rules", y_test, rules_fired)

    # --- split (b): held-out anomaly TYPES ------------------------------------
    # The headline number. Split (a) measures how well the model recognises
    # anomalies it has already seen examples of; since every one of those was
    # written by synthetic_invoices.py, a high score there partly measures the
    # generator. Holding whole types out asks the harder and more honest question:
    # does it generalise to a kind of wrongness it was never shown?
    holdout = {t.strip() for t in args.holdout_types.split(",") if t.strip()}
    in_holdout = np.asarray(
        [any(kind in holdout for kind in row) for row in types], dtype=bool)
    if in_holdout.any() and (~in_holdout).sum() > 20:
        scaler_b = StandardScaler().fit(X[~in_holdout])
        model_b = LogisticRegression(
            class_weight="balanced", max_iter=2000, C=search.best_params_["C"]
        ).fit(scaler_b.transform(X[~in_holdout]), y[~in_holdout])
        held_scores = model_b.predict_proba(scaler_b.transform(X[in_holdout]))[:, 1]
        metrics["holdout_types"] = {
            "types": sorted(holdout),
            "rows": int(in_holdout.sum()),
            "recall_at_threshold": float(np.mean(held_scores >= threshold)),
            "mean_probability": float(np.mean(held_scores)),
        }
        log_metric("holdout_recall", metrics["holdout_types"]["recall_at_threshold"])
    else:
        metrics["holdout_types"] = {"types": sorted(holdout), "rows": 0,
                                    "note": "not enough rows to measure"}

    # --- per anomaly type ------------------------------------------------------
    predicted_all = (model.predict_proba(scaler.transform(X))[:, 1] >= threshold)
    by_type = {}
    for kind in sorted({k for row in types for k in row}):
        rows = [i for i, row in enumerate(types) if kind in row]
        if not rows:
            continue
        recall = float(np.mean(predicted_all[rows]))
        by_type[kind] = {"rows": len(rows), "recall": recall}
        log_metric(f"recall_{kind}", recall)
    metrics["by_type"] = by_type

    # --- export ----------------------------------------------------------------
    run_id = ""
    if mlflow and mlflow.active_run():
        run_id = mlflow.active_run().info.run_id

    artifact = {
        "schema_version": 1,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run_id": run_id,
        "feature_names": list(names),
        "feature_medians": [float(v) for v in medians],
        "scaler_mean": [float(v) for v in scaler.mean_],
        "scaler_scale": [float(v) for v in scaler.scale_],
        "coef": [float(v) for v in model.coef_[0]],
        "intercept": float(model.intercept_[0]),
        "threshold": float(threshold),
        "metrics": metrics,
        "training": {
            "rows": len(records),
            "seed": args.seed,
            "C": search.best_params_["C"],
            "target_precision": args.target_precision,
            "holdout_types": sorted(holdout),
            "label": "is_intrinsic_anomaly",
            "source": os.path.basename(args.data),
            "caveat": ("Labels are injected by synthetic_invoices.py, so these "
                       "numbers describe how well the model finds what that "
                       "generator produces - not how it behaves on real invoices. "
                       "Quote metrics.holdout_types, which at least measures "
                       "generalisation to unseen kinds of wrongness."),
        },
    }

    os.makedirs(args.out, exist_ok=True)
    destination = os.path.join(args.out, anomaly_detector.MODEL_FILENAME)
    with open(destination, "w", encoding="utf-8") as handle:
        json.dump(artifact, handle, indent=2)

    if mlflow:
        mlflow.log_artifact(destination)

    print(f"\nWrote {destination}")
    print(f"  {len(records)} rows, {len(names)} features, C={search.best_params_['C']}, "
          f"threshold={threshold:.3f}")
    print(f"\n  {'arm':22s} {'precision':>10s} {'recall':>8s} {'f1':>8s}")
    for arm in ("logistic_regression", "isolation_forest", "rules_only"):
        scores = metrics[arm]
        print(f"  {arm:22s} {scores['precision']:10.3f} {scores['recall']:8.3f} "
              f"{scores['f1']:8.3f}")
    held = metrics["holdout_types"]
    if held.get("rows"):
        print(f"\n  Held-out types {held['types']}: recall "
              f"{held['recall_at_threshold']:.3f} on {held['rows']} rows "
              f"(this is the number worth quoting)")
    if metrics["rules_only"]["f1"] >= metrics["logistic_regression"]["f1"]:
        print("\n  NOTE: the rules match or beat the model on this data. That is a "
              "\n  result, not a bug - report it rather than shipping a model that "
              "\n  adds nothing.")
    print()


if __name__ == "__main__":
    main()
