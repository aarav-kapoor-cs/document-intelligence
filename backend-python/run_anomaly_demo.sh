#!/bin/bash
# Run the anomaly detector end to end, with no Azure calls and no cost.
#
#   cd backend-python
#   bash run_anomaly_demo.sh
#
# Nothing here uploads a file or calls Azure. The detector reads documents that
# are already in documents.db, so no PDFs and no API keys are needed. Only the
# last step (the web UI) needs the server running.

set -e
PY=.venv/bin/python
[ -x "$PY" ] || PY=python3

echo
echo "=== 1. The rules, and what each one checks =========================="
# Every threshold in the detector, printed from the same table the API serves.
$PY -c "
import anomaly_rules
for r in anomaly_rules.catalogue():
    print(f\"  {r['layer']:8s} {r['name']:22s} weight={r['weight']:<3} {r['threshold']}\")"

echo
echo "=== 2. Score the real invoices in documents.db ======================"
# --all shows every document; drop it to see only the ones with findings.
$PY anomaly_detector.py --from 2026-07-01 --to 2026-08-31

echo
echo "=== 3. Make 600 synthetic invoices with known anomalies ============="
# Reproducible from the seed, so this file never needs committing.
$PY synthetic_invoices.py --count 600 --out /tmp/syn.jsonl

echo
echo "=== 4. Measure the rules against those known labels ================"
# The number that matters is "falsely flagged by a rule" - it should be 0.
$PY anomaly_metrics.py --data /tmp/syn.jsonl

echo
echo "=== 5. The web UI =================================================="
echo "  Terminal 1:  cd backend-python && .venv/bin/python -m uvicorn main:app --port 8001"
echo "  Terminal 2:  npm start"
echo "  Browser:     http://localhost:4200  ->  pick 'Anomaly detector'"
echo
