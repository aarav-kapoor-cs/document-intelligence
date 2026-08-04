"""HTTP access to the search pipeline, for the explorer page at /search.

Everything here is a thin wrapper over hybrid_search.py — the chunking, the four
retrieval methods, the RAG step and the agent all already exist and are reused
rather than reimplemented. What this module adds is the three things a web
request needs and a CLI does not:

  * a cache, because make_chunks() re-reads the workbook about six times and
    that is fine once per command but not once per keystroke,
  * a guard, because hybrid_search.py signals every misconfiguration with SystemExit,
    which inside a request handler would stop the worker rather than return an
    error to the browser,
  * a status endpoint, so the page can say "the embedding model is not deployed"
    instead of failing on the first query.
"""

import asyncio
import os
import shutil
from datetime import date, datetime

from fastapi import APIRouter, HTTPException

import agent_service
import database
import excel_service
import retrieval_metrics
import keyword_search
import hybrid_search
from models import AgentRequest, CompareRequest, ExportRequest, SearchRequest

router = APIRouter(prefix="/api/search", tags=["search"])

_chunks_cache = None


def guard(call, *args, **kwargs):
    """Run a pipeline function, turning its SystemExit into an HTTP error.

    hybrid_search.py is a CLI first: it reports a missing key or an undeployed model
    by exiting with a readable message. That message is worth keeping — it names
    the exact portal blade to visit — so it becomes the HTTP detail rather than
    being swallowed.
    """
    try:
        return call(*args, **kwargs)
    except SystemExit as stop:
        raise HTTPException(status_code=503, detail=str(stop)) from stop


def cached_chunks(refresh=False):
    """The 84 chunks, built once. Pass refresh=True after a new export."""
    global _chunks_cache
    if _chunks_cache is None or refresh:
        _chunks_cache = guard(hybrid_search.make_chunks)
    return _chunks_cache


@router.get("/status")
def status():
    """What is configured and what is not — so the page can degrade honestly."""
    state = {
        "workbook": None, "chunks": 0, "search_configured": False,
        "embeddings_configured": False, "indexes": {}, "notes": [],
    }
    try:
        state["workbook"] = hybrid_search.workbook_path().rsplit("/", 1)[-1]
        state["chunks"] = len(cached_chunks())
    except HTTPException as error:
        state["notes"].append(error.detail)

    for module, label in ((keyword_search, "keyword + semantic ranker"),
                          (hybrid_search, "hybrid + vectors")):
        try:
            state["indexes"][module.INDEX_NAME] = {
                "documents": module.search_client().get_document_count(),
                "kind": label,
            }
            state["search_configured"] = True
        except SystemExit as stop:
            state["notes"].append(str(stop))
        except Exception as error:  # index missing, network, bad key
            state["indexes"][module.INDEX_NAME] = {"error": str(error)[:200], "kind": label}

    try:
        hybrid_search.embed(["ping"])
        state["embeddings_configured"] = True
    except SystemExit as stop:
        state["notes"].append(str(stop))
    except Exception as error:
        state["notes"].append(str(error)[:200])
    return state


@router.get("/chunks")
def chunks(refresh: bool = False):
    """Every chunk, with a per-sheet tally. No Azure call — this reads the workbook."""
    everything = cached_chunks(refresh=refresh)
    counts = {}
    for chunk in everything:
        counts[chunk["sheet"]] = counts.get(chunk["sheet"], 0) + 1
    return {"total": len(everything), "by_sheet": counts, "chunks": everything}


def _hits(results):
    """Flatten Azure's result objects into JSON the page can render."""
    out = []
    for rank, hit in enumerate(results, start=1):
        out.append({
            "rank": rank,
            "id": hit["id"],
            "sheet": hit["sheet"],
            "grain": hit["grain"],
            "entity": hit["entity"],
            "content": hit["content"],
            # The reranker score replaces the BM25 one when semantic ranking ran,
            # and the two are not comparable, so which is which is worth saying.
            "score": hit.get("@search.reranker_score") or hit["@search.score"],
            "scored_by": "reranker" if hit.get("@search.reranker_score") else "bm25/rrf",
        })
    return out


@router.post("/ask")
def ask(request: SearchRequest):
    """Retrieve, and optionally have the model write a grounded answer."""
    if not request.question.strip():
        raise HTTPException(400, "A question is required.")

    # Same rule as the CLI (hybrid_search.py): retrieval alone shows 5 hits, but a
    # grounded answer needs RAG_CONTEXT chunks or the model tallies whatever
    # narrow slice it was handed and calls that the whole picture.
    top = request.top if request.top is not None else (
        hybrid_search.RAG_CONTEXT if request.answer else 5)

    def run():
        return list(hybrid_search.search_client().search(
            search_text=request.question,
            filter=hybrid_search.odata_filter(request.sheet or None, request.grain or None),
            query_type="semantic",
            semantic_configuration_name=hybrid_search.SEMANTIC_CONFIG,
            select=["id", "sheet", "grain", "entity", "content"],
            top=max(1, min(top, 84)),
        ))

    results = guard(run)
    payload = {"question": request.question, "hits": _hits(results), "answer": None}
    if request.answer and results:
        payload["answer"] = guard(hybrid_search.answer_from, request.question, results)
    return payload


@router.post("/compare")
def compare(request: CompareRequest):
    """One question, four retrieval methods, so the differences are visible.

    The variants come from retrieval_metrics.py, which is where they are already defined
    and already used to measure recall — one definition, not two.
    """
    if not request.question.strip():
        raise HTTPException(400, "A question is required.")

    top = max(1, min(request.top, 20))
    variants = guard(retrieval_metrics.search_variants, request.question, top)
    client = guard(hybrid_search.search_client)
    filters = hybrid_search.odata_filter(None, request.grain or None)

    methods = {}
    for name, arguments in variants.items():
        results = guard(lambda a=arguments: list(client.search(
            filter=filters, select=["id", "sheet", "grain", "entity", "content"],
            top=top, **a)))
        methods[name] = _hits(results)

    # What actually differs between the four is the ORDER, and that is easy to
    # miss when reading four lists. Say it outright.
    orders = {name: [hit["id"] for hit in hits] for name, hits in methods.items()}
    baseline = orders.get("keyword", [])
    return {
        "question": request.question,
        "methods": methods,
        "same_as_keyword": {name: order == baseline for name, order in orders.items()},
    }


@router.post("/reindex")
def reindex(request: ExportRequest):
    """Rebuild the workbook from the database, then rebuild the search index.

    This is the step that used to be done by hand: export the report, copy the
    file next to the module, run `python hybrid_search.py build`. Doing all three
    in one call is what keeps the index describing the same documents the app has
    actually analysed, instead of whichever workbook was last copied in.
    """
    # The same validation /api/export does — a clear 400 beats a confusing crash.
    for value in (request.from_date, request.to_date):
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(400, "Both dates are needed, like 2026-07-22.")
    if request.from_date > request.to_date:
        raise HTTPException(400, "The from date must not be after the to date.")
    if request.doc_type not in excel_service.DOC_TYPE_LABELS:
        raise HTTPException(400, "Unknown document type: " + request.doc_type)

    # 1. Build the workbook through the very same two calls /api/export uses, so
    #    the file that gets indexed and the file the user downloads cannot differ.
    try:
        rows = database.get_filtered(request.from_date, request.to_date, request.doc_type)
    except Exception as error:
        raise HTTPException(500, "The database could not be read: " + str(error))
    if not rows:
        raise HTTPException(400, "No documents were saved in that date range, so "
                                 "there is nothing to index. Analyze a file first.")
    content = excel_service.build_workbook(
        rows, request.from_date, request.to_date, request.doc_type)

    # 2. Write it where the chunker will actually look. workbook_path() prefers
    #    AUDIT_XLSX and otherwise takes the newest Audit_*.xlsx next to the module,
    #    so writing anywhere else would silently re-index the previous file.
    configured = os.getenv("AUDIT_XLSX", "").strip()
    target = os.path.expanduser(configured) if configured else os.path.join(
        os.path.dirname(os.path.abspath(hybrid_search.__file__)),
        "Audit_" + date.today().strftime("%d%m%Y") + ".xlsx")

    # Keep one copy of whatever was there. A fresh export only covers documents
    # currently in the database, so re-indexing against a narrower one silently
    # replaces a workbook that may have taken real work to produce.
    replaced = None
    if os.path.exists(target):
        replaced = os.path.splitext(target)[0] + ".previous.xlsx"
        shutil.copy2(target, replaced)

    with open(target, "wb") as handle:
        handle.write(content)

    # 3. Drop the explorer's cache, or the page keeps serving the old chunks.
    cached_chunks(refresh=True)

    # 4. Rebuild both indexes, so /status can never show one current and one stale.
    #    Each build re-chunks the workbook we just wrote and deletes the chunks it
    #    no longer produces.
    indexes = {}
    for module in (hybrid_search, keyword_search):
        summary = guard(module.build_index)
        indexes[summary["index"]] = summary

    first = next(iter(indexes.values()))
    return {
        "workbook": os.path.basename(target),
        "workbook_path": target,
        "replaced": os.path.basename(replaced) if replaced else None,
        "records": len(rows),
        "chunks": first["chunks"],
        "documents": first["documents"],
        "fields": first["fields"],
        "indexes": indexes,
    }


@router.post("/agent")
async def agent(request: AgentRequest):
    """Let the Semantic Kernel agent choose its own tools, and show the trace."""
    if not request.question.strip():
        raise HTTPException(400, "A question is required.")
    try:
        return await agent_service.ask(request.question)
    except SystemExit as stop:
        raise HTTPException(status_code=503, detail=str(stop)) from stop
    except Exception as error:
        raise HTTPException(status_code=502, detail=str(error)[:400]) from error
