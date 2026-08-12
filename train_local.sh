#!/bin/bash
# Train the anomaly model on this machine. No Azure, no cloud, no cost.
#
#   bash train_local.sh
#
# Generates a fresh synthetic corpus, fits the model, compares it against the
# rules and against an isolation forest, and installs the winner into
# backend-python/anomaly_model.json.
#
# scikit-learn goes into a SEPARATE .venv-train, never into backend-python/.venv.
# That is not fussiness: the backend applies the model as a numpy dot product over
# exported coefficients, so it genuinely needs no scikit-learn, and keeping the
# two environments apart is what keeps that claim true and testable.

set -e
cd "$(dirname "$0")"

TRAIN_VENV=.venv-train
DATA=azureml/data/synthetic_invoices.jsonl
OUT=azureml/data/model_out
BACKEND_PY=backend-python/.venv/bin/python
[ -x "$BACKEND_PY" ] || BACKEND_PY=backend-python/.venv/Scripts/python.exe
[ -x "$BACKEND_PY" ] || { echo "Set up backend-python/.venv first (see README)."; exit 1; }

# --- 1. the training environment, created once ------------------------------
if [ ! -x "$TRAIN_VENV/bin/python" ] && [ ! -x "$TRAIN_VENV/Scripts/python.exe" ]; then
  echo "Creating $TRAIN_VENV (one time, ~1 minute)..."
  python3 -m venv "$TRAIN_VENV"
  TRAIN_PY="$TRAIN_VENV/bin/python"; [ -x "$TRAIN_PY" ] || TRAIN_PY="$TRAIN_VENV/Scripts/python.exe"
  # numpy pinned to the backend's version so float behaviour is identical on both
  # sides of the exported coefficients. The rest is what train.py imports through
  # anomaly_features - the same module the backend scores with, so that training
  # and serving can never compute a feature differently.
  "$TRAIN_PY" -m pip -q install --upgrade pip
  "$TRAIN_PY" -m pip -q install "scikit-learn==1.6.1" "numpy==2.5.1" "pandas==2.3.3" \
      "openpyxl==3.1.5" "azure-ai-documentintelligence==1.0.2" \
      "azure-search-documents==12.0.0" "openai==2.46.0" "python-dotenv==1.2.2"
fi
TRAIN_PY="$TRAIN_VENV/bin/python"; [ -x "$TRAIN_PY" ] || TRAIN_PY="$TRAIN_VENV/Scripts/python.exe"

# --- 2. a fresh corpus -------------------------------------------------------
echo
echo "Generating 600 synthetic invoices with injected anomalies..."
mkdir -p azureml/data
(cd backend-python && "../$BACKEND_PY" synthetic_invoices.py --count 600 \
    --out "../$DATA" | tail -6)

# --- 3. train ----------------------------------------------------------------
echo
echo "Training..."
PYTHONPATH=backend-python "$TRAIN_PY" azureml/train.py \
    --data "$DATA" --out "$OUT" \
    --seed 20260807 --target-precision 0.90 \
    --holdout-types gstin_checksum_bad,item_sum_mismatch 2>&1 \
  | grep -vE "OptimizeWarning|opt_res|warnings.warn"

# --- 4. install it -----------------------------------------------------------
# Keep the previous model. A retrain on a changed generator can be worse, and
# without a copy there is no way back to the one that was working.
if [ -f backend-python/anomaly_model.json ]; then
  cp backend-python/anomaly_model.json backend-python/anomaly_model.previous.json
fi
cp "$OUT/anomaly_model.json" backend-python/anomaly_model.json

echo
echo "Installed backend-python/anomaly_model.json (previous kept as .previous.json)"
echo "Restart uvicorn to pick it up, then check /api/anomaly/status."
echo
echo "See how it scores the real documents:"
echo "  cd backend-python && .venv/bin/python anomaly_detector.py --from 2026-07-01 --to 2026-08-31"
