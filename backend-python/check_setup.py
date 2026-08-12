"""Say what is wrong with this machine, in order, and stop at the first thing.

    cd backend-python
    .venv\\Scripts\\python check_setup.py      (Windows)
    .venv/bin/python check_setup.py           (Mac/Linux)

Every check prints either a tick or the exact command that fixes it. Nothing here
calls Azure, so it is safe to run at any time and costs nothing.
"""

import os
import sys
import traceback

# Running this from the repo root instead of backend-python is the single most
# common mistake, and the error it produces ("No module named database") sounds
# like a broken install rather than a wrong directory. Fix it rather than
# reporting it.
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
os.chdir(HERE)

FAIL = []


def ok(label, detail=""):
    print(f"  [ok]   {label}{'  ' + detail if detail else ''}")


def bad(label, fix):
    print(f"  [FAIL] {label}")
    print(f"         fix: {fix}")
    FAIL.append(label)


print("\nChecking this machine\n" + "-" * 60)

# 1. Python and the packages -------------------------------------------------
print(f"  [ok]   python {sys.version.split()[0]}  ({sys.executable})")
if ".venv" not in sys.executable:
    print("         note: this is NOT the .venv python. On Windows use")
    print("               .venv\\Scripts\\python check_setup.py")

for module in ("numpy", "openpyxl", "pandas", "dotenv", "fastapi", "uvicorn"):
    try:
        __import__(module)
        ok(f"package {module}")
    except ImportError:
        bad(f"package {module} is missing",
            "pip install -r requirements.txt   (with .venv activated)")

# 2. Configuration -----------------------------------------------------------
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(HERE, ".env"))
except ImportError:
    pass

if os.path.exists(os.path.join(HERE, ".env")):
    ok(".env found")
else:
    bad(".env is missing",
        "copy your backed-up .env into backend-python/ - it holds the Azure keys")

corpus = os.getenv("ANOMALY_MIN_CORPUS", "")
if corpus:
    ok(f"ANOMALY_MIN_CORPUS = {corpus}")
else:
    print("  [warn] ANOMALY_MIN_CORPUS is not set, so it defaults to 20.")
    print("         With fewer than 20 documents the duplicate and vendor-outlier")
    print("         checks stay silent. Add this line to .env:")
    print("               ANOMALY_MIN_CORPUS=10")

# 3. The database ------------------------------------------------------------
records = []
try:
    import database
    ok("database reachable", database.describe())
    records = database.get_filtered("2000-01-01", "2099-01-01", "prebuilt-invoice")
    if records:
        dates = sorted(str(r.get("createdAt", ""))[:10] for r in records if r.get("createdAt"))
        ok(f"{len(records)} invoice document(s) saved")
        if dates:
            print(f"         they run {dates[0]} to {dates[-1]}")
            print(f"         so use:  --from {dates[0]} --to {dates[-1]}")
    else:
        bad("no invoice documents in the database",
            "copy your backed-up documents.db into backend-python/, or upload a "
            "file through the app first")
except Exception:
    bad("the database could not be read", "see the traceback below")
    traceback.print_exc(limit=3)

# 4. The detector ------------------------------------------------------------
try:
    import anomaly_detector
    model = anomaly_detector.load_model()
    ok("anomaly detector imports")
    if model:
        ok("trained model installed", f"run {model.get('run_id') or '(local)'}")
    else:
        print("  [warn] no anomaly_model.json - scoring falls back to the rules,")
        print("         which is fine and still shows every finding.")
    if records:
        report = anomaly_detector.scan(records, model)
        bands = {}
        for result in report["results"]:
            bands[result["band"]] = bands.get(result["band"], 0) + 1
        ok(f"scan works: {report['documents']} scored by {report['method']}")
        print(f"         bands: {bands}")
        if len(bands) == 1 and "Suspect" in bands:
            print("  [warn] every document scored Suspect. That is the known model")
            print("         saturation - rename anomaly_model.json to demo on the")
            print("         rules alone, which separate the documents properly.")
except Exception:
    bad("the anomaly detector failed", "see the traceback below")
    traceback.print_exc()

# 5. Verdict -----------------------------------------------------------------
print("-" * 60)
if FAIL:
    print(f"\n{len(FAIL)} thing(s) to fix:")
    for item in FAIL:
        print(f"  - {item}")
    sys.exit(1)
print("\nEverything works. Run the detector with the dates printed above.\n")
