#!/bin/bash
# Create the two things job.yml expects to already exist: the compute cluster it
# runs on, and the registered data asset it trains from.
#
#   bash azureml/setup.sh
#
# Safe to re-run. The cluster is created only if it is missing; the data asset
# gets a new version each time, which is the point - every run records exactly
# which corpus it learned from.

set -e
cd "$(dirname "$0")/.."          # repo root, so paths below are stable

CLUSTER=cpu-cluster
DATA_NAME=synthetic-invoices
DATA_FILE=azureml/data/synthetic_invoices.jsonl
# Standard_DS3_v2 is 4 cores and is usually available on a fresh subscription.
# If quota is refused, Standard_DS2_v2 is smaller and almost always allowed.
VM_SIZE=${AZURE_ML_VM_SIZE:-Standard_DS3_v2}

# --- checks that fail early with a fixable message ---------------------------

command -v az >/dev/null || {
  echo "The Azure CLI is not installed."
  echo "  macOS:   brew install azure-cli"
  echo "  Windows: winget install Microsoft.AzureCLI"
  exit 1
}

az extension show -n ml >/dev/null 2>&1 || {
  echo "Adding the 'ml' extension (one time)..."
  az extension add -n ml
}

az account show >/dev/null 2>&1 || { echo "Run 'az login' first."; exit 1; }

# The workspace coordinates live in backend-python/.env, the same file the
# backend reads - one place to configure Azure, not two.
set -a; [ -f backend-python/.env ] && . backend-python/.env; set +a
: "${AZURE_ML_RESOURCE_GROUP:?Set AZURE_ML_RESOURCE_GROUP in backend-python/.env}"
: "${AZURE_ML_WORKSPACE:?Set AZURE_ML_WORKSPACE in backend-python/.env}"
[ -n "$AZURE_ML_SUBSCRIPTION_ID" ] && az account set -s "$AZURE_ML_SUBSCRIPTION_ID"

# Stops every later command needing -g and -w spelled out.
az configure --defaults group="$AZURE_ML_RESOURCE_GROUP" workspace="$AZURE_ML_WORKSPACE"
echo "Workspace: $AZURE_ML_WORKSPACE (resource group $AZURE_ML_RESOURCE_GROUP)"

# Refuse to upload anything before the ignore file exists. `code: ..` in job.yml
# sends the whole repository to the workspace blob store and Azure ML honours
# .amlignore, NOT .gitignore - without it this uploads backend-python/.env and
# documents.db.
[ -f .amlignore ] || { echo "STOP: .amlignore is missing from the repo root."; exit 1; }

# --- the compute cluster -----------------------------------------------------

if az ml compute show -n "$CLUSTER" >/dev/null 2>&1; then
  echo "Compute '$CLUSTER' already exists."
else
  echo "Creating compute '$CLUSTER' ($VM_SIZE, scales to zero)..."
  # min-instances 0 is the whole cost argument: the cluster bills nothing
  # between runs, which is why this project trains in Azure ML but scores
  # in-process rather than paying for an always-on endpoint.
  az ml compute create -n "$CLUSTER" --type amlcompute \
      --min-instances 0 --max-instances 1 --size "$VM_SIZE" \
      --idle-time-before-scale-down 120
fi

# --- the training data -------------------------------------------------------

if [ ! -f "$DATA_FILE" ]; then
  echo "Generating the synthetic corpus..."
  mkdir -p azureml/data
  PY=backend-python/.venv/bin/python; [ -x "$PY" ] || PY=python3
  (cd backend-python && ../$PY synthetic_invoices.py --count 600 \
      --out ../"$DATA_FILE")
fi

echo "Registering '$DATA_NAME' (a new version each run)..."
az ml data create -n "$DATA_NAME" -p "$DATA_FILE" --type uri_file

echo
echo "Ready. Submit the training job with:"
echo "  az ml job create -f azureml/job.yml --web"
