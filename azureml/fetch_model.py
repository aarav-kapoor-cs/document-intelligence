"""Download the registered model and install it next to the backend.

    python azureml/fetch_model.py                 # newest version
    python azureml/fetch_model.py --version 3

Reads the workspace coordinates from backend-python/.env (AZURE_ML_*) and
authenticates with DefaultAzureCredential, i.e. whoever `az login` says you are.
That is deliberate: there is no Azure ML key anywhere in this project, so the one
service that could hand out compute cannot be leaked through a config file.

The file it writes, backend-python/anomaly_model.json, is committed to the repo.
It is four kilobytes of coefficients fitted to synthetic data - no vendor names,
no GSTINs, no amounts - and the office laptop has git blocked, so committing it
is how the trained model gets there at all.
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv

BACKEND = Path(__file__).resolve().parent.parent / "backend-python"
load_dotenv(BACKEND / ".env")

sys.path.insert(0, str(BACKEND))
import anomaly_detector  # noqa: E402
import anomaly_features  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Install the registered anomaly model into backend-python/.")
    parser.add_argument("--version", default=os.getenv("AZURE_ML_MODEL_VERSION", ""),
                        help="model version (default: the newest)")
    parser.add_argument("--name", default=os.getenv("AZURE_ML_MODEL_NAME", "gst-anomaly-lr"))
    args = parser.parse_args(argv)

    subscription = os.getenv("AZURE_ML_SUBSCRIPTION_ID", "").strip()
    group = os.getenv("AZURE_ML_RESOURCE_GROUP", "").strip()
    workspace = os.getenv("AZURE_ML_WORKSPACE", "").strip()
    if not all((subscription, group, workspace)):
        sys.exit(
            "Set AZURE_ML_SUBSCRIPTION_ID, AZURE_ML_RESOURCE_GROUP and "
            "AZURE_ML_WORKSPACE in backend-python/.env first. They are in the "
            "Azure ML studio under the workspace name, top right.")

    try:
        from azure.ai.ml import MLClient
        from azure.identity import DefaultAzureCredential
    except ImportError:
        sys.exit("This script needs the training dependencies:\n"
                 "  python -m pip install -r azureml/requirements-train.txt")

    client = MLClient(DefaultAzureCredential(), subscription, group, workspace)
    if args.version:
        model = client.models.get(name=args.name, version=args.version)
    else:
        model = max(client.models.list(name=args.name), key=lambda m: int(m.version))
    print(f"Model {model.name} version {model.version}")

    with tempfile.TemporaryDirectory() as scratch:
        client.models.download(name=model.name, version=model.version, download_path=scratch)
        found = list(Path(scratch).rglob(anomaly_detector.MODEL_FILENAME))
        if not found:
            sys.exit(f"That model version has no {anomaly_detector.MODEL_FILENAME} in it.")

        # Validate BEFORE overwriting a working artifact: a model trained against
        # a different feature list is worse than none at all, because the
        # coefficients are positional and would score every document wrongly
        # while looking perfectly healthy.
        with open(found[0], encoding="utf-8") as handle:
            candidate = json.load(handle)
        if tuple(candidate.get("feature_names", ())) != anomaly_features.FEATURE_NAMES:
            sys.exit(
                f"Refusing to install it: that model was trained on "
                f"{len(candidate.get('feature_names', ()))} features and this code "
                f"builds {len(anomaly_features.FEATURE_NAMES)}. Retrain from the "
                f"current anomaly_features.py.")

        destination = BACKEND / anomaly_detector.MODEL_FILENAME
        if destination.exists():
            shutil.copy2(destination, destination.with_suffix(".previous.json"))
        shutil.copy2(found[0], destination)

    metrics = candidate.get("metrics", {}).get("logistic_regression", {})
    held = candidate.get("metrics", {}).get("holdout_types", {})
    print(f"Installed {destination}")
    print(f"  run {candidate.get('run_id') or '(local)'}, "
          f"threshold {candidate.get('threshold')}")
    print(f"  precision {metrics.get('precision', 0):.3f}, "
          f"recall {metrics.get('recall', 0):.3f}")
    if held.get("rows"):
        print(f"  held-out types {held['types']}: recall "
              f"{held.get('recall_at_threshold', 0):.3f} - quote this one")
    print("\nRestart uvicorn to pick it up, then check /api/anomaly/status.")
    print("Commit the file: the office laptop has no other way to get it.")


if __name__ == "__main__":
    main()
