"""Do everything the az CLI would, using the Python SDK instead.

    python -m pip install -r azureml/requirements-train.txt
    python azureml/submit.py

Written because `az` is a separate install that frequently is not on PATH, is
awkward on a locked-down work laptop, and is not actually required: azure-ai-ml
talks to the same service. This creates the compute cluster, registers the
training data, submits the job, waits for it, and registers the result - the
whole of azureml/setup.sh plus `az ml job create` plus the registration step.

Authentication tries DefaultAzureCredential first (which picks up `az login` if
the CLI happens to be there) and falls back to opening a browser. So there is
still no Azure ML key in .env, and still nothing to leak.

Run it from the repository root, not from this folder:

    cd /path/to/sample
    python azureml/submit.py
"""

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend-python"
load_dotenv(BACKEND / ".env")

CLUSTER = "cpu-cluster"
DATA_NAME = "synthetic-invoices"
MODEL_NAME = os.getenv("AZURE_ML_MODEL_NAME", "gst-anomaly-lr")
DATA_FILE = ROOT / "azureml" / "data" / "synthetic_invoices.jsonl"


def credential():
    """`az login` if it exists, a browser window if it does not."""
    from azure.identity import DefaultAzureCredential, InteractiveBrowserCredential
    try:
        cred = DefaultAzureCredential(exclude_interactive_browser_credential=True)
        cred.get_token("https://management.azure.com/.default")
        print("  signed in via DefaultAzureCredential")
        return cred
    except Exception:
        print("  no cached login found - opening a browser to sign in")
        return InteractiveBrowserCredential()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Create the cluster, register the data, and run the training job.")
    parser.add_argument("--vm-size", default=os.getenv("AZURE_ML_VM_SIZE", "Standard_DS3_v2"),
                        help="try Standard_DS2_v2 if the default is refused for quota")
    parser.add_argument("--count", type=int, default=600,
                        help="synthetic invoices to generate if the file is missing")
    parser.add_argument("--no-wait", action="store_true",
                        help="submit and exit instead of waiting for the run")
    args = parser.parse_args(argv)

    # --- the same three values fetch_model.py reads --------------------------
    subscription = os.getenv("AZURE_ML_SUBSCRIPTION_ID", "").strip()
    group = os.getenv("AZURE_ML_RESOURCE_GROUP", "").strip()
    workspace = os.getenv("AZURE_ML_WORKSPACE", "").strip()
    if not all((subscription, group, workspace)):
        sys.exit("Set AZURE_ML_SUBSCRIPTION_ID, AZURE_ML_RESOURCE_GROUP and "
                 "AZURE_ML_WORKSPACE in backend-python/.env first.\n"
                 "All three are in Azure ML studio: click the workspace name, top right.")

    # `code=ROOT` uploads the whole repository, and Azure ML honours .amlignore
    # rather than .gitignore. Without it this would send backend-python/.env and
    # documents.db to the workspace blob store.
    if not (ROOT / ".amlignore").exists():
        sys.exit("STOP: .amlignore is missing from the repository root. It is what "
                 "keeps .env and documents.db out of the upload.")

    try:
        from azure.ai.ml import Input, MLClient, Output, command
        from azure.ai.ml.constants import AssetTypes
        from azure.ai.ml.entities import AmlCompute, Data, Environment, Model
    except ImportError:
        sys.exit("Install the training dependencies first:\n"
                 "  python -m pip install -r azureml/requirements-train.txt")

    print(f"\nWorkspace {workspace} (resource group {group})")
    client = MLClient(credential(), subscription, group, workspace)

    # --- 1. compute ----------------------------------------------------------
    try:
        client.compute.get(CLUSTER)
        print(f"  compute '{CLUSTER}' already exists")
    except Exception:
        print(f"  creating compute '{CLUSTER}' ({args.vm_size}, scales to zero)...")
        # min_instances=0 is the entire cost argument: nothing is billed between
        # runs, which is why this project trains in the cloud but scores in-process
        # instead of paying for an always-on endpoint.
        client.begin_create_or_update(AmlCompute(
            name=CLUSTER, type="amlcompute", size=args.vm_size,
            min_instances=0, max_instances=1, idle_time_before_scale_down=120,
        )).result()
        print("  created")

    # --- 2. training data ----------------------------------------------------
    if not DATA_FILE.exists():
        print(f"  generating {args.count} synthetic invoices...")
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        sys.path.insert(0, str(BACKEND))
        import synthetic_invoices
        synthetic_invoices.write_jsonl(
            synthetic_invoices.generate(count=args.count), str(DATA_FILE))

    data = client.data.create_or_update(Data(
        name=DATA_NAME, path=str(DATA_FILE), type=AssetTypes.URI_FILE,
        description="Synthetic GST invoices with injected anomalies and labels."))
    print(f"  registered data '{DATA_NAME}' version {data.version}")

    # --- 3. the job ----------------------------------------------------------
    # PYTHONPATH=backend-python because train.py imports anomaly_features - the
    # SAME module the backend serves with. A vendored copy would drift and the
    # exported coefficients would end up fitted to one definition of a feature and
    # applied to another.
    job = command(
        code=str(ROOT),
        command=("PYTHONPATH=backend-python python azureml/train.py "
                 "--data ${{inputs.data}} --out ${{outputs.model_dir}} "
                 "--seed 20260807 --target-precision 0.90 "
                 "--holdout-types gstin_checksum_bad,item_sum_mismatch"),
        inputs={"data": Input(type=AssetTypes.URI_FILE, path=f"{DATA_NAME}@latest")},
        outputs={"model_dir": Output(type=AssetTypes.URI_FOLDER)},
        environment=Environment(
            image="mcr.microsoft.com/azureml/openmpi4.1.0-ubuntu22.04",
            conda_file=str(ROOT / "azureml" / "environment.yml"),
            name="gst-anomaly-train"),
        compute=CLUSTER,
        experiment_name="gst-anomaly",
        display_name="gst-anomaly-lr",
    )
    run = client.jobs.create_or_update(job)
    print(f"\n  submitted: {run.name}")
    print(f"  watch it:  {run.studio_url}\n")

    if args.no_wait:
        print("Not waiting. Register the model yourself once it finishes, or re-run "
              "this without --no-wait.")
        return

    print("Waiting for the run (the first one is slow - it builds the conda image)...")
    client.jobs.stream(run.name)

    finished = client.jobs.get(run.name)
    if finished.status != "Completed":
        sys.exit(f"The run ended as {finished.status}. Open the studio link above, "
                 f"check 'Outputs + logs' -> user_logs/std_log.txt for the reason.")

    # --- 4. register the result ---------------------------------------------
    model = client.models.create_or_update(Model(
        path=f"azureml://jobs/{run.name}/outputs/model_dir",
        name=MODEL_NAME, type=AssetTypes.CUSTOM_MODEL,
        description="Logistic regression exported as coefficients for in-process scoring."))
    print(f"\n  registered model {model.name} version {model.version}")
    print("\nNow install it into the backend:")
    print("  python azureml/fetch_model.py")


if __name__ == "__main__":
    main()
