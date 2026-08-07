# Training the anomaly model in Azure ML

The detector works without any of this — the rules run locally and score every
document. Azure ML adds one thing: a logistic regression that weighs all the
signals together, including the OCR-confidence collapse that no rule checks.

The model is **trained** in Azure ML and **applied** in the FastAPI backend. The
job exports the fitted regression as coefficients in `anomaly_model.json`, and
applying those is `sigmoid(z·w + b)` — a numpy dot product, the same arithmetic
scikit-learn does, to float precision. So there is no managed endpoint billing by
the hour, and `backend-python/requirements.txt` gains nothing at all.

---

## What it costs

The `cpu-cluster` created below has `min-instances 0`, so it costs **nothing
between runs** and scales to zero two minutes after a job finishes. A training
run on 600 records takes a few minutes on one `Standard_DS3_v2`.

This is the opposite of the Azure AI Search trap in the main README §7, where the
service bills hourly whether or not you query it. Nothing here bills while idle —
but do check the studio afterwards that the cluster really did scale down.

---

## One-time setup

**1. Install the Azure CLI and the ML extension**

```bash
brew install azure-cli          # macOS
# winget install Microsoft.AzureCLI   # Windows
az extension add -n ml
az login
```

**2. Put your workspace coordinates in `backend-python/.env`**

Open Azure ML studio, click the workspace name at the top right — the panel shows
all three:

```bash
AZURE_ML_SUBSCRIPTION_ID=<the subscription id>
AZURE_ML_RESOURCE_GROUP=<the resource group the workspace is in>
AZURE_ML_WORKSPACE=<the workspace name>
```

There is no key. Authentication is `az login`, so no Azure ML secret ever enters
a config file.

**3. Create the compute cluster and register the data**

```bash
bash azureml/setup.sh
```

Safe to re-run. It creates `cpu-cluster` only if missing, generates the synthetic
corpus if it is not there, and registers a new version of the `synthetic-invoices`
data asset — so every run records exactly which corpus it learned from.

If the cluster fails with a quota error, a fresh subscription often has zero quota
for the default family. Retry smaller:

```bash
AZURE_ML_VM_SIZE=Standard_DS2_v2 bash azureml/setup.sh
```

---

## Training

```bash
az ml job create -f azureml/job.yml --web
```

`--web` opens the run in the studio. Watch **Metrics** — the job logs four arms
scored on identical data:

| arm | what it answers |
|---|---|
| `logistic_regression` | the model being shipped |
| `isolation_forest` | would a real unsupervised model have done better? |
| `robust_z` | would median/MAD alone have done? |
| `rules_only` | **does the model beat the rules at all?** |

If `rules_only` matches the model, that is the finding — say so rather than
shipping a model that adds nothing. It is currently close: precision 0.936 for
the rules against 0.918 for the model.

The number to quote is **`holdout_types`**, not the headline precision. The job
holds two anomaly types out of training entirely, so that arm measures
generalisation instead of how well the model memorised your own injections.

---

## Installing the trained model

```bash
python -m pip install -r azureml/requirements-train.txt   # once, NOT in backend-python/.venv
python azureml/fetch_model.py
```

It downloads the newest registered version, **validates the feature list before
overwriting anything**, keeps the previous file as `anomaly_model.previous.json`,
and prints the metrics it just installed. The validation matters: the coefficients
are positional, so a model trained against a different `FEATURE_NAMES` would score
every document wrongly while looking perfectly healthy.

Then restart the backend and confirm:

```bash
curl -s localhost:8001/api/anomaly/status | python -m json.tool
```

`method` should read `logistic_regression`. Delete `anomaly_model.json` and it
degrades to `rules_only` rather than failing — that is intended.

**Commit `anomaly_model.json`.** It is 4 KB of coefficients fitted to synthetic
data — no vendor names, no GSTINs, no amounts — and the office laptop has git
blocked, so committing it is how the model gets there at all.

---

## Before you retrain: a real problem

The model currently shipped is **saturated on real invoices**:

| data | median score | scored ≥ 60 |
|---|---|---|
| clean synthetic | 2.30 | 2 / 120 |
| real invoices in `documents.db` | 98.34 | **17 / 17** |

Every real invoice lands in "Suspect", including eleven that fire no rule at all.
The model is not broken — it is close to perfect on its own distribution. The
generator is the problem: `synthetic_invoices.py` builds clean invoices carrying
all twelve core fields, and no real invoice ever does. Real ones routinely lack
Tax Details, Customer GSTIN and Vendor Address, so `core_missing_count = 3` on a
perfectly ordinary invoice looks identical to the injected `core_field_dropped`
anomaly.

Retraining without changing the generator reproduces the same saturated model.
The fix is in `synthetic_invoices.py`: make clean invoices omit core fields at the
rate real ones do, then regenerate, re-register and retrain.

---

## Safety

`job.yml` sets `code: ..` — the **repository root**, not this folder — because
`train.py` imports `anomaly_features` from `backend-python`. Training and serving
must compute features with the same code, or the exported coefficients end up
fitted to one definition and applied to another.

The price is that `az ml job create` uploads the whole repository to the workspace
blob store, honouring **`.amlignore`, not `.gitignore`**. `.amlignore` at the repo
root excludes `.env`, `*.db`, `*.xlsx` and `chunks.json`. `setup.sh` refuses to run
if it is missing. Do not remove it.

---

## Files

| file | what it is |
|---|---|
| `setup.sh` | creates the cluster, registers the data. Re-runnable. |
| `job.yml` | the training job: code, command, environment, compute, inputs |
| `environment.yml` | conda spec for the job. Pins numpy to the backend's version so float behaviour matches on both sides of the artifact. |
| `train.py` | fits the model, logs four arms to MLflow, exports the coefficients |
| `fetch_model.py` | registered model → `backend-python/anomaly_model.json` |
| `requirements-train.txt` | training-side deps. Never install into `backend-python/.venv`. |
| `data/` | generated corpus, git-ignored — reproducible from the seed |
