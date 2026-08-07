"""HTTP access to the anomaly detector, for the Angular page and for curl.

A thin wrapper over anomaly_detector.py, in the same spirit as search_api.py: the
scoring, the rules and the model already exist and are reused rather than
reimplemented. What a web request needs and a CLI does not is a cached model (the
artifact is small, but re-reading and re-validating it per keystroke is silly) and
a status endpoint that says what is missing instead of failing on the first scan.

One deliberate difference from search_api.py, so its absence does not read as an
oversight: there is no guard() here. That helper exists because hybrid_search.py
reports a missing Azure key by exiting the process, which inside a request handler
would kill the worker. This module talks to no Azure service at all. Its one
degradation - no anomaly_model.json yet - is not an error and must not become a
503: the rules layer needs no model, so the scan still runs, still scores, and
says method="rules_only" so the caller knows which number it is holding.

The exception is a model whose feature list disagrees with the code. That is
refused loudly (500), because applying positional coefficients to a reordered
vector would look perfectly healthy while scoring every document wrongly.
"""

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException

import anomaly_detector
import anomaly_rules
import database
import excel_service
# ExportRequest is reused for /scan: it is exactly the date-window + doc-type
# contract /api/export and /api/search/reindex already take, so all three pages
# send the same shape.
from models import AnomalyScanResponse, AnomalyScoreRequest, ExportRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/anomaly", tags=["anomaly"])

# The content checks - do the amounts reconcile, is the GSTIN real - only mean
# something for invoices. Receipts and ID documents have no base-plus-tax to
# check, so scoring them would produce confident nonsense.
SUPPORTED_DOC_TYPE = "prebuilt-invoice"

_model_cache = None
_model_loaded = False


def cached_model(refresh=False):
    """The exported coefficients, read once.

    A missing file is cached as None so a scan does not stat the disk per
    document. Call with refresh=True after installing a new artifact.
    """
    global _model_cache, _model_loaded
    if refresh or not _model_loaded:
        _model_cache = anomaly_detector.load_model()
        _model_loaded = True
    return _model_cache


def _require_invoices(doc_type: str):
    if doc_type != SUPPORTED_DOC_TYPE:
        label = excel_service.DOC_TYPE_LABELS.get(doc_type, doc_type)
        raise HTTPException(
            400, f"The anomaly checks are arithmetic and format checks over invoice "
                 f"fields, so they only mean something for invoices - not {label}.")


@router.get("/status")
def status():
    """What is loaded and what is not, so the page can degrade honestly."""
    state = {
        "model_installed": False,
        "model_version": "",
        "model_path": anomaly_detector.model_path(),
        "method": "rules_only",
        "metrics": {},
        "trained_on": {},
        "rules": len(anomaly_rules.RULES) + 2,  # + the two corpus rules
        "min_corpus": anomaly_rules.MIN_CORPUS,
        "notes": [],
    }
    try:
        model = cached_model()
    except ValueError as error:
        state["notes"].append(str(error))
        return state

    if model:
        state.update({
            "model_installed": True,
            "model_version": model.get("run_id", ""),
            "method": "logistic_regression",
            "metrics": model.get("metrics", {}),
            "trained_on": model.get("training", {}),
        })
    else:
        state["notes"].append(
            "No trained model is installed, so documents are scored by the "
            "deterministic rules alone. That is a working detector, not a broken "
            "one - the rules need neither training data nor Azure.")
    return state


@router.get("/rules")
def rules():
    """The rule catalogue and every threshold behind it.

    Served as data rather than written up separately, so what the page explains
    and what the scan applies cannot drift apart.
    """
    return anomaly_rules.catalogue()


@router.post("/scan", response_model=AnomalyScanResponse)
def scan(request: ExportRequest):
    """Score every saved document in a date window."""
    # The same validation /api/export does - a clear 400 beats a confusing crash.
    for value in (request.from_date, request.to_date):
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(400, "Both dates are needed, like 2026-07-22.")
    if request.from_date > request.to_date:
        raise HTTPException(400, "The from date must not be after the to date.")
    _require_invoices(request.doc_type)

    try:
        records = database.get_filtered(request.from_date, request.to_date, request.doc_type)
    except Exception as error:
        raise HTTPException(500, "The database could not be read: " + str(error))
    if not records:
        raise HTTPException(400, "No documents were saved in that date range, so there "
                                 "is nothing to score. Analyze a file first.")

    try:
        model = cached_model()
    except ValueError as error:
        raise HTTPException(500, str(error))

    report = anomaly_detector.scan(records, model)
    report.update({
        "from_date": request.from_date,
        "to_date": request.to_date,
        "doc_type": request.doc_type,
    })
    return report


@router.post("/score")
def score(request: AnomalyScoreRequest):
    """Score one set of extracted fields without saving anything.

    For checking a document straight after upload, before it has been exported or
    has any peers. The two corpus rules cannot run on a single document, so they
    are absent by construction rather than silently returning "clean".
    """
    if not request.fields:
        raise HTTPException(400, "No extracted fields were sent, so there is nothing "
                                 "to score.")
    _require_invoices(request.doc_type)

    try:
        model = cached_model()
    except ValueError as error:
        raise HTTPException(500, str(error))

    result = anomaly_detector.score_document(request.fields, model)
    result["notes"] = [
        "Duplicate and vendor-outlier checks need other documents to compare "
        "against, so they did not run here. Use /api/anomaly/scan for those."
    ]
    return result
