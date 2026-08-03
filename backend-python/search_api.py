"""HTTP access to the search pipeline, for the explorer page at /search.

Everything here is a thin wrapper over pipeline.py — the chunking, the four
retrieval methods, the RAG step and the agent all already exist and are reused
rather than reimplemented. What this module adds is the three things a web
request needs and a CLI does not:

  * a cache, because make_chunks() re-reads the workbook about six times and
    that is fine once per command but not once per keystroke,
  * a guard, because pipeline.py signals every misconfiguration with SystemExit,
    which inside a request handler would stop the worker rather than return an
    error to the browser,
  * a status endpoint, so the page can say "the embedding model is not deployed"
    instead of failing on the first query.
"""

import asyncio

from fastapi import APIRouter, HTTPException

import agent_service
import evaluate
import phase1
import pipeline
from models import AgentRequest, CompareRequest, SearchRequest

router = APIRouter(prefix="/api/search", tags=["search"])

_chunks_cache = None


def guard(call, *args, **kwargs):
    """Run a pipeline function, turning its SystemExit into an HTTP error.

    pipeline.py is a CLI first: it reports a missing key or an undeployed model
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
        _chunks_cache = guard(pipeline.make_chunks)
    return _chunks_cache


@router.get("/status")
def status():
    """What is configured and what is not — so the page can degrade honestly."""
    state = {
        "workbook": None, "chunks": 0, "search_configured": False,
        "embeddings_configured": False, "indexes": {}, "notes": [],
    }
    try:
        state["workbook"] = pipeline.workbook_path().rsplit("/", 1)[-1]
        state["chunks"] = len(cached_chunks())
    except HTTPException as error:
        state["notes"].append(error.detail)

    for module, label in ((phase1, "keyword + semantic ranker"),
                          (pipeline, "hybrid + vectors")):
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
        pipeline.embed(["ping"])
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

    # Same rule as the CLI (pipeline.py): retrieval alone shows 5 hits, but a
    # grounded answer needs RAG_CONTEXT chunks or the model tallies whatever
    # narrow slice it was handed and calls that the whole picture.
    top = request.top if request.top is not None else (
        pipeline.RAG_CONTEXT if request.answer else 5)

    def run():
        return list(pipeline.search_client().search(
            search_text=request.question,
            filter=pipeline.odata_filter(request.sheet or None, request.grain or None),
            query_type="semantic",
            semantic_configuration_name=pipeline.SEMANTIC_CONFIG,
            select=["id", "sheet", "grain", "entity", "content"],
            top=max(1, min(top, 84)),
        ))

    results = guard(run)
    payload = {"question": request.question, "hits": _hits(results), "answer": None}
    if request.answer and results:
        payload["answer"] = guard(pipeline.answer_from, request.question, results)
    return payload


@router.post("/compare")
def compare(request: CompareRequest):
    """One question, four retrieval methods, so the differences are visible.

    The variants come from evaluate.py, which is where they are already defined
    and already used to measure recall — one definition, not two.
    """
    if not request.question.strip():
        raise HTTPException(400, "A question is required.")

    top = max(1, min(request.top, 20))
    variants = guard(evaluate.search_variants, request.question, top)
    client = guard(pipeline.search_client)
    filters = pipeline.odata_filter(None, request.grain or None)

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
