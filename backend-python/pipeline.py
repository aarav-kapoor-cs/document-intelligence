"""Phase 2 — the same thing, with vectors.

Reads the six-sheet audit workbook, turns three of its sheets into chunks of
plain English, and pushes them into Azure AI Search. Retrieval here is hybrid:
BM25 keyword matching AND vector similarity, merged by Reciprocal Rank Fusion,
then reordered by the semantic ranker. Needs an Azure OpenAI embedding
deployment as well as an Azure AI Search key.

    python pipeline.py chunks     # no Azure needed, prints what would be uploaded
    python pipeline.py build      # embeds, creates the index and uploads
    python pipeline.py ask "which fields are extracting badly" --answer

Generated from phase1.py. `diff phase1.py pipeline.py` is the entire vector
feature and nothing else — the chunking, the RAG step and the CLI are identical.
"""

import argparse
import glob
import hashlib
import json
import os
import re
import time
from pathlib import Path

import pandas as pd
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchableField,
    SearchFieldDataType,
    SearchIndex,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    SimpleField,
    # --- the vector half ---
    HnswAlgorithmConfiguration,
    HnswParameters,
    SearchField,
    VectorSearch,
    VectorSearchAlgorithmMetric,
    VectorSearchProfile,
)
from azure.search.documents.models import VectorizedQuery
from dotenv import load_dotenv
from openai import OpenAI, OpenAIError

import ai_service

# Only main.py loads the .env file, and this script does not import main.py, so
# it has to load the keys itself before anything reads them.
load_dotenv(Path(__file__).with_name(".env"))

# Sheet names, exactly as excel_service.py writes them.
PERIOD_SHEET = "AUDIT Period"
LOG_SHEET = "AI EXTRACTION LOG"
COMP_SHEET = "COMPLETENESS TREND"
VAL_SHEET = "VALIDITY TREND"

# Column names AFTER sheet() has stripped them. Two headers in the workbook carry
# a trailing space on purpose — "Field_Name(Keys from json) " and "Type " are
# copied verbatim from the mentor's template. Read every sheet through sheet()
# and the space stops mattering; read one with pd.read_excel directly and you get
# a KeyError that never mentions whitespace.
NUMBER = "Number"
SOURCE_FILE = "Source_File"
DOC_TYPE = "Type Of Document"
FIELD_RAW = "Field_Name(Keys from json)"
FIELD_PRETTY = "Reference_Column(Keys from .net core)"
OCR_VALUE = "OCR_Column (Extracted values)"
COMPLETE_FLAG = "Completeness_Flag"
VALID_FLAG = "Validity_Flag"

# The trend sheets name the same field differently, and both use the pretty name
# that FIELD_PRETTY holds — not the raw JSON key in FIELD_RAW.
COMP_FIELD = "OCR_Field"
VAL_FIELD = "Field_Type"

# Fields whose VALUE goes into a document chunk. Everything else contributes its
# name only, which keeps addresses out of the text without losing the fact that
# they extracted.
KEY_FIELDS = (
    "Vendor Name", "Vendor GSTIN", "Customer Name", "Customer GSTIN",
    "Invoice Date", "Due Date", "Total Invoice Amount", "Base Amount",
    "GST Amount", "Line Items",
)

# The fields a GST invoice cannot legally be without. Everything else in Azure's
# prebuilt-invoice schema is optional, and most of it — vendor fax number,
# service period dates, remittance address, previous unpaid balance — simply does
# not appear on an Indian invoice. Without this split the report reads "39.4%
# complete" and sounds like a broken extractor, when the core fields average
# 93.1% and the rest are mostly measuring the absence of things that were never
# there. A hand-written list on purpose: deriving it from whatever is rare in the
# data would quietly reclassify a genuinely broken field as "not applicable".
CORE_FIELDS = (
    "Invoice Id", "Invoice Date", "Vendor Name", "Vendor Address", "Vendor GSTIN",
    "Customer Name", "Customer GSTIN", "Total Invoice Amount", "Base Amount",
    "GST Amount", "Tax Details", "Line Items",
)

INDEX_NAME = os.getenv("AUDIT_PIPELINE_INDEX", "audit-pipeline")
SEMANTIC_CONFIG = "audit-semantic"

# How many chunks --answer puts in front of the model when no --top is given.
# Higher than the 5 a human wants to read, because the model has to compare
# across chunks to answer anything aggregate.
RAG_CONTEXT = 15

# The rules that separate a RAG answer from a plausible-sounding invention. The
# fourth is the one that earns its keep: retrieval cannot rank 5.6% against
# 83.3%, but a model reading both numbers can.
RAG_SYSTEM = """You answer questions about an invoice extraction audit using ONLY
the numbered chunks provided in the user message.

- Open with a direct one-sentence answer to the question asked. No preamble.
- Use only what the chunks say. Never invent a number, a field name, or a
  document number. If a chunk does not say it, you do not know it.
- Cite the chunk id in square brackets after each claim, like [comp-amount-due].
- Whenever you list more than one thing, ORDER IT by the number involved — worst
  first for a question about problems. Never leave it in the order the chunks
  happened to arrive in. Ordering is the whole reason you are being asked.
- Show the number you ordered by next to each item, so the answer can be checked
  against the report. One line per item.
- Give at most 8 items, then say how many others there were.
- The chunks are a retrieved subset, not the whole report. If answering properly
  would need information that is not in them, say so plainly.
- Completeness means a field was populated. It does NOT mean the value was
  correct. Never describe a field as accurate or correct.
- Before calling any low figure a failure, check whether its chunk says the field
  is optional or often absent from these invoices. If it does, lead with that: a
  field that is not on the document was not extracted badly, it was not there.
- Asked which fields extract badly or worst: answer with the CORE fields that
  fall short, ranked worst first, even when optional fields have lower numbers.
  A core field at 72% is a real problem; an optional field at 5% usually means
  the invoice never had one. Mention optional fields afterwards, separately and
  labelled as probably-not-applicable, or leave them out."""

# The embedding model turns chunk text into ~1536 numbers. The numbers are a
# position, not a code: sentences that mean similar things land near each other,
# which is how meaning becomes distance. Use the DEPLOYMENT name from the
# Deployments blade — a model that is merely available in the region gives an
# "unknown_model" error that explains nothing.
EMBED_DEPLOYMENT = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "")
# Must match the deployed model: text-embedding-3-small is 1536,
# text-embedding-3-large is 3072. A mismatch fails on every single upload.
EMBED_DIMS = int(os.getenv("AUDIT_EMBED_DIMS", "1536"))


# --------------------------------------------------------------------------
# Reading the workbook
# --------------------------------------------------------------------------

def workbook_path():
    """The audit workbook: AUDIT_XLSX if set, else the newest one next to this file."""
    configured = os.getenv("AUDIT_XLSX", "").strip()
    if configured:
        path = os.path.expanduser(configured)
        if not os.path.exists(path):
            raise SystemExit(f"CHUNKS: AUDIT_XLSX points at {path}, which does not exist.")
        return path

    here = os.path.dirname(os.path.abspath(__file__))
    found = glob.glob(os.path.join(here, "Audit_*.xlsx"))
    if not found:
        raise SystemExit(
            "CHUNKS: no workbook found. Put an Audit_*.xlsx next to pipeline.py, or "
            "point AUDIT_XLSX at one in .env."
        )
    # Newest by modification time — the filenames are DDMMYYYY, so sorting them
    # as text puts 01082026 before 31072026.
    return max(found, key=os.path.getmtime)


def sheet(name):
    """Read one sheet, stripping the trailing spaces off its column names."""
    frame = pd.read_excel(workbook_path(), sheet_name=name)
    frame.columns = [str(column).strip() for column in frame.columns]
    return frame


def audit_period():
    """Run id, the period it covers, and the record count — stamped onto every chunk."""
    row = sheet(PERIOD_SHEET).iloc[0]
    start = pd.to_datetime(row["Start_Date"]).date()
    end = pd.to_datetime(row["End_Date"]).date()
    return str(row["Audit_Run_ID"]), f"{start} to {end}", int(row["Total_Records"])


# --------------------------------------------------------------------------
# Turning numbers into words
# --------------------------------------------------------------------------

def band(pct):
    """Which threshold band a 0.0-1.0 ratio falls in. Bands come from the legend
    at the bottom of the VALIDITY TREND sheet."""
    percent = pct * 100
    if percent >= 95:
        return "Excellent"
    if percent >= 90:
        return "Acceptable"
    return "Needs Attention"


def verdict(pct, kind):
    """Turn 0.111 into words someone would actually type into a search box.

    Search matches text. A chunk holding only "11.1%" cannot be found by anyone
    asking which fields extract badly, because no word connects the question to
    the number. Embeddings do not rescue it either — a vector has no sense that
    0.111 is small. Writing the number out is the difference between a retrieval
    layer that works and one that mysteriously does not.

    Only the band that applies is emitted. Spelling out the whole legend here
    would put "needs attention" into all 66 field chunks and destroy exactly the
    discrimination this function exists to create.
    """
    percent = pct * 100
    if kind == "completeness":
        if percent >= 95:
            return (f"{percent:.1f} percent complete - excellent completeness, good "
                    f"extraction quality, reliable field, almost always populated, at "
                    f"or above the 95 percent threshold, no action needed")
        if percent >= 90:
            return (f"{percent:.1f} percent complete - acceptable completeness, adequate "
                    f"extraction quality, borderline, occasionally blank, between the 90 "
                    f"and 95 percent thresholds, worth watching")
        return (f"{percent:.1f} percent complete - poor completeness, bad extraction "
                f"quality, unreliable field, extracting badly, frequently blank or "
                f"missing, below the 90 percent threshold, needs attention")

    if kind == "document":
        # Deliberately says "document" and never "field". A document chunk that
        # described itself as an "unreliable field" outranked the actual field
        # chunks on "which fields are extracting badly" — the two grains ended up
        # answering each other's questions.
        if percent >= 95:
            return (f"{percent:.1f} percent populated - excellent extraction, good "
                    f"extraction quality, reliable document, nearly complete, at or "
                    f"above the 95 percent threshold, no action needed")
        if percent >= 90:
            return (f"{percent:.1f} percent populated - acceptable extraction, adequate "
                    f"extraction quality, borderline document, a few values missing, "
                    f"between the 90 and 95 percent thresholds, worth watching")
        return (f"{percent:.1f} percent populated - poor extraction, bad extraction "
                f"quality, unreliable document, this document extracted badly, much of "
                f"it blank or missing, below the 90 percent threshold, needs attention")

    if percent >= 95:
        return (f"{percent:.1f} percent valid - excellent validity, good extraction "
                f"quality, reliable field, values in the expected format, at or above "
                f"the 95 percent threshold, no action needed")
    if percent >= 90:
        return (f"{percent:.1f} percent valid - acceptable validity, adequate extraction "
                f"quality, borderline, a few malformed values, between the 90 and 95 "
                f"percent thresholds, worth watching")
    return (f"{percent:.1f} percent valid - poor validity, bad extraction quality, "
            f"unreliable field, extracting badly, values malformed or failing "
            f"validation, below the 90 percent threshold, needs attention")


def slug(text):
    """Lowercase and URL-safe. Azure document keys allow only letters, digits, _ - ="""
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(text).strip().lower())
    return re.sub(r"-+", "-", cleaned).strip("-") or "x"


def short_hash(*parts):
    """Six hex characters over the grouping key, so slug collisions cannot happen.

    Invoice numbers slug lossily: INV/100 and INV-100 both become inv-100. A key
    collision in Azure is a silent overwrite, not an error — you would get one
    document fewer, no exception, and a count that looks almost right.
    """
    return hashlib.blake2s("|".join(parts).encode("utf-8"), digest_size=3).hexdigest()


def applicability(entity, pct):
    """One sentence saying whether this field's figure is actually a problem.

    Without it a search for badly-extracting fields returns Vendor Fax Number at
    5.6% alongside Vendor GSTIN at 72.2%, as though they were the same kind of
    failure. One is a field nobody prints any more; the other is a compliance gap.

    The percentage matters as well as the field: telling a chunk that sits at
    100% that "a low figure here is a genuine gap" is a sentence about a
    situation that is not happening, and the reranker reads it anyway.
    """
    if entity not in CORE_FIELDS:
        return ("This field is optional and is often absent from Indian GST invoices, "
                "so a low figure here usually means the field does not apply to these "
                "documents rather than that extraction failed.")
    if pct >= 1.0:
        return ("This is a core field that every GST invoice must carry, and it was "
                "present on every document — nothing to chase here.")
    return ("This is a core field that every GST invoice must carry, so this "
            "shortfall is a genuine extraction gap worth chasing.")


def clean_value(value, limit=120):
    """Collapse whitespace and truncate, so one long field cannot dominate a chunk."""
    text = " ".join(str(value).split())
    return text[: limit - 3] + "..." if len(text) > limit else text


# --------------------------------------------------------------------------
# Chunking — three sheets, three rules
# --------------------------------------------------------------------------

def document_chunks(run_id, period, expected_documents):
    """One chunk per document: all of its field rows folded into one paragraph.

    A single row of the extraction log is one field of one document, and on its
    own it is useless — you learn that AmountDue was blank, but not whether the
    other 32 fields were fine or the whole document collapsed. Indexing row by
    row runs without error and returns results, which is what makes it dangerous:
    a search then hands back three fields out of 33 as if they described the whole
    invoice. Grouping is what makes a chunk one complete thought.
    """
    log = sheet(LOG_SHEET)
    print(f"CHUNKS: read {len(log)} rows from {LOG_SHEET}")

    # A guard, not a fix. A document ingested twice would otherwise double every
    # count in its chunk.
    log = log.drop_duplicates(subset=[NUMBER, SOURCE_FILE, FIELD_RAW])
    if len(log) != len(sheet(LOG_SHEET)):
        print(f"CHUNKS: WARN dropped {len(sheet(LOG_SHEET)) - len(log)} duplicate row(s)")

    chunks = []
    populated_cells = 0
    # Group on Number AND Source_File: two documents can carry the same number,
    # and the number can be blank when extraction missed it entirely.
    for (number, source_file), group in log.groupby([NUMBER, SOURCE_FILE], dropna=False):
        values, filled, failed, soft_failed = {}, [], [], []
        for _, row in group.iterrows():
            name = str(row[FIELD_PRETTY])
            complete = str(row[COMPLETE_FLAG]).strip() == "Y"
            valid = str(row[VALID_FLAG]).strip() == "Pass"
            if complete:
                filled.append(name)
                values[name] = row[OCR_VALUE]
            if not complete or not valid:
                failed.append(name)
            if complete and not valid:
                soft_failed.append(name)

        total = len(group)
        populated_cells += len(filled)
        doc_type = str(group.iloc[0][DOC_TYPE])

        shown = []
        for name in KEY_FIELDS:
            if name not in values:
                continue
            if name == "Line Items":
                # One document's line items run to 26 products. Left whole they are
                # longer than the rest of the chunk, and the chunk ends up meaning
                # "cake tins and frying pans" rather than "extraction quality". If
                # invoice contents ever need searching, emit a SECOND chunk with
                # grain='contents' — do not widen this one.
                items = str(values[name]).count(";") + 1
                shown.append(f"Line Items: {items} items")
            else:
                shown.append(f"{name}: {clean_value(values[name])}")
        other = sorted(name for name in filled if name not in KEY_FIELDS)

        content = (
            f"Document {number} ({doc_type}) from source file {source_file}, "
            f"audit run {run_id} covering {period}. "
            # "field" is kept out of these two sentences on purpose. Saying it here
            # made every document chunk a strong match for questions about fields.
            f"{len(filled)} of {total} populated, {total - len(filled)} empty; "
            f"{len(failed)} of {total} failed validation. "
            f"Overall extraction quality for this document: "
            f"{verdict(len(filled) / total, 'document')}. "
            # This phrase is load-bearing. "vendor GSTIN" matches whether the field
            # extracted or not, and what tells the two apart is which side of this
            # sentence the match came from. The semantic ranker reads that context;
            # plain keyword search cannot.
            f"Fields empty or failed: {', '.join(sorted(failed)) if failed else 'none'}. "
        )
        if soft_failed:
            content += (f"Extracted but failed validation: "
                        f"{', '.join(sorted(soft_failed))}. ")
        content += (
            f"Extracted values: {'; '.join(shown) if shown else 'none'}. "
            f"Also populated: {', '.join(other) if other else 'none'}."
        )

        chunks.append({
            "id": f"doc-{slug(number)}-{short_hash(str(number), str(source_file))}",
            "sheet": LOG_SHEET,
            "grain": "document",
            "entity": str(number),
            "content": content,
        })

    print(f"CHUNKS: {len(chunks)} document(s), {populated_cells} of {len(log)} cells populated")
    if len(chunks) != expected_documents:
        print(f"CHUNKS: WARN {PERIOD_SHEET} says {expected_documents} records but the "
              f"log groups into {len(chunks)} document(s)")
    return chunks, populated_cells


def completeness_chunks(run_id, period):
    """One chunk per row: a trend row is already a complete fact on its own."""
    chunks = []
    for _, row in sheet(COMP_SHEET).iterrows():
        entity = str(row[COMP_FIELD])
        total = int(row["Total_Records"])
        nulls = int(row["Null_Count"])
        pct = float(row["Completeness_Percent"])
        chunks.append({
            "id": f"comp-{slug(entity)}",
            "sheet": COMP_SHEET,
            "grain": "field",
            "entity": entity,
            "content": (
                f"Completeness of the field {entity} on {row['Document Type']} documents, "
                f"audit run {run_id} covering {period}. "
                f"The field was populated in {total - nulls} of {total} documents and "
                f"empty in {nulls}. "
                f"Completeness {verdict(pct, 'completeness')}. "
                f"Rated {band(pct)} against the recommended threshold. "
                f"{applicability(entity, pct)} "
                f"This measures how often {entity} was extracted at all, not whether "
                f"the value extracted was correct."
            ),
        })
    print(f"CHUNKS: {len(chunks)} completeness field(s)")
    return chunks


def validity_chunks(run_id, period):
    """One chunk per row, minus the threshold legend at the bottom of the sheet."""
    frame = sheet(VAL_SHEET)
    # The last five rows are a blank spacer and the four-line legend explaining the
    # ratings ("Excellent: >= 95%"). They have no Pass_Count. Indexed, they surface
    # as a meaningless fragment whenever anyone asks about quality thresholds.
    # Filter on Pass_Count, not on the text: "Recommended Threshold" is both a
    # legend row and a column header, so matching the string catches the wrong things.
    data = frame[frame["Pass_Count"].notna()]
    if len(frame) - len(data) != 5:
        print(f"CHUNKS: WARN dropped {len(frame) - len(data)} legend row(s) from "
              f"{VAL_SHEET}, expected 5")

    chunks = []
    for _, row in data.iterrows():
        entity = str(row[VAL_FIELD])
        passed = int(row["Pass_Count"])
        failed = int(row["Fail_Count"])
        pct = float(row["Validity_Percent"])
        rating = str(row["Recommended Threshold"]).strip()
        # The sheet rates each row itself. Recomputing keeps one source of truth
        # across all three chunk types, but if the two ever disagree the report
        # changed underneath us and the chunks are wrong.
        if rating != band(pct):
            print(f"CHUNKS: WARN {entity} is rated {rating!r} in the sheet but "
                  f"computes as {band(pct)!r}")
        chunks.append({
            "id": f"val-{slug(entity)}",
            "sheet": VAL_SHEET,
            "grain": "field",
            "entity": entity,
            "content": (
                f"Validity of the field {entity} on {row['Type']} documents, audit run "
                f"{run_id} covering {period}. "
                f"{passed} of {passed + failed} documents passed validation for this "
                f"field and {failed} failed. "
                f"Validity {verdict(pct, 'validity')}. "
                f"Rated {rating} against the recommended threshold. "
                f"{applicability(entity, pct)} "
                f"Validity checks the format of what was extracted, so a field can be "
                f"populated and still fail here."
            ),
        })
    print(f"CHUNKS: {len(chunks)} validity field(s)")
    return chunks


def make_chunks():
    """Three sheets, three rules. Returns the full list of chunks to upload."""
    run_id, period, expected_documents = audit_period()
    print(f"CHUNKS: {os.path.basename(workbook_path())} - run {run_id}, {period}")

    documents, populated_cells = document_chunks(run_id, period, expected_documents)
    completeness = completeness_chunks(run_id, period)
    validity = validity_chunks(run_id, period)
    chunks = documents + completeness + validity

    # The two sheets are generated from the same rows, so their null counts have to
    # agree. If they do not, they came from different data and nothing below is
    # trustworthy.
    nulls = int(sheet(COMP_SHEET)["Null_Count"].sum())
    expected_populated = len(documents) * len(completeness) - nulls
    if populated_cells != expected_populated:
        print(f"CHUNKS: WARN {populated_cells} populated cells in the log but "
              f"{COMP_SHEET} implies {expected_populated}")

    # A duplicate id is the one failure that Azure accepts silently — the second
    # upload overwrites the first, and the count comes back one short.
    ids = [chunk["id"] for chunk in chunks]
    if len(set(ids)) != len(ids):
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        raise SystemExit(f"CHUNKS: duplicate ids would silently overwrite: {duplicates}")

    print(f"CHUNKS: {len(chunks)} chunks - {len(documents)} document, "
          f"{len(completeness)} completeness, {len(validity)} validity")
    return chunks


# --------------------------------------------------------------------------
# Azure AI Search
# --------------------------------------------------------------------------

# A note on what is embedded, because the obvious optimisation does not work.
#
# The chunks share a lot of text: two document chunks have 73% of their
# vocabulary in common, mean pairwise cosine 0.883, some pairs as high as 0.995.
# The tempting conclusion is that verdict()'s pile of synonyms is flooding the
# vector, and that embedding a leaner string would separate them. It was tried
# and measured: mean cosine moved 0.883 -> 0.878 and the closest pair got *worse*
# (0.995 -> 0.997). The similarity is not boilerplate — it is that all eighteen
# documents genuinely are the same kind of thing, described with the same
# thirty-three field names.
#
# High absolute similarity turns out not to matter much anyway. Ranking depends
# on relative order, and vector search still beats keyword search on synonym
# queries here. So `content` is embedded as-is.
def embedding_client():
    """The client for the embedding model.

    The embedding model does not have to live in the same Azure OpenAI resource
    as the chat model — here it does not. When AZURE_OPENAI_EMBEDDING_BASE_URL is
    set, embeddings use their own endpoint and key; otherwise they fall back to
    the chat resource via ai_service. Either way it is the plain OpenAI client
    against a /openai/v1 base URL, never AzureOpenAI and never an api-version.
    """
    base_url = os.getenv("AZURE_OPENAI_EMBEDDING_BASE_URL", "").rstrip("/")
    if not base_url:
        return ai_service.get_client()
    if not base_url.endswith("/openai/v1"):
        base_url += "/openai/v1"
    return OpenAI(base_url=base_url, api_key=os.getenv("AZURE_OPENAI_EMBEDDING_KEY", ""))


def embed(texts):
    """Embed a list of strings. Order is preserved, so results zip back onto the input."""
    configured = os.getenv("AZURE_OPENAI_EMBEDDING_BASE_URL", "").startswith("http")
    if not EMBED_DEPLOYMENT or not (configured or ai_service.is_configured()):
        raise SystemExit("The embedding model is not configured — set "
                         "AZURE_OPENAI_EMBEDDING_DEPLOYMENT, and either "
                         "AZURE_OPENAI_EMBEDDING_BASE_URL/_KEY or the main "
                         "AZURE_OPENAI_BASE_URL/_KEY, in backend-python/.env.")
    print(f"EMBED: {len(texts)} text(s) via {EMBED_DEPLOYMENT}...")
    try:
        response = embedding_client().embeddings.create(
            model=EMBED_DEPLOYMENT, input=texts)
    except OpenAIError as error:
        # The name being set is not the same as the model being deployed. Every
        # model in the region shows up in the catalogue; only the ones on the
        # Deployments blade can actually be called.
        if "unknown_model" in str(error) or "404" in str(error):
            raise SystemExit(
                f"EMBED: {EMBED_DEPLOYMENT!r} is not a deployment on this Azure "
                f"OpenAI resource. Deploy an embedding model first — portal → "
                f"Foundry → Deployments → Deploy model → text-embedding-3-small — "
                f"then put the DEPLOYMENT name in AZURE_OPENAI_EMBEDDING_DEPLOYMENT. "
                f"Being listed in the model catalogue is not the same as being "
                f"deployed. phase1.py works without this."
            ) from error
        raise
    vectors = [item.embedding for item in response.data]
    # Checked here rather than at upload, so a wrong deployment fails once with a
    # clear message instead of 84 times with a dimension error.
    if vectors and len(vectors[0]) != EMBED_DIMS:
        raise SystemExit(f"EMBED: {EMBED_DEPLOYMENT} returned {len(vectors[0])} "
                         f"dimensions but the index expects {EMBED_DIMS}. Fix "
                         f"AUDIT_EMBED_DIMS or point at the right deployment.")
    return vectors


def index_schema():
    """The index definition. SearchableField means keyword search looks inside the
    field; SimpleField means stored and filterable but never searched."""
    fields = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True),
        # Searchable as well as filterable because any field named in a semantic
        # configuration must be searchable and retrievable. Filtering still uses
        # the exact stored string, which is what --sheet relies on.
        SearchableField(name="sheet", type=SearchFieldDataType.String,
                        filterable=True, facetable=True),
        # Searchable, not simple. The obvious reading is that nobody types the word
        # "document" or "field" into a search box — but the semantic ranker needs
        # the signal. Without it, a document chunk listing 19 failing field names
        # outranks the actual field chunks on "which fields are extracting badly",
        # and the two grains answer each other's questions.
        SearchableField(name="grain", type=SearchFieldDataType.String,
                        filterable=True, facetable=True),
        SearchableField(name="entity", type=SearchFieldDataType.String,
                        filterable=True, sortable=True),
        # No custom analyzer on purpose. en.microsoft would stem "extracting" to
        # "extract" and make the demo query work for the wrong reason, hiding the
        # fact that verdict() is what makes these chunks findable.
        SearchableField(name="content", type=SearchFieldDataType.String),
        # hidden=True keeps the vector out of query results. Retrieved, it would
        # add 1536 floats of noise to every hit.
        SearchField(name="content_vector",
                    type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                    searchable=True, hidden=True,
                    vector_search_dimensions=EMBED_DIMS,
                    vector_search_profile_name="hnsw-profile"),
    ]
    semantic = SemanticSearch(
        default_configuration_name=SEMANTIC_CONFIG,
        configurations=[SemanticConfiguration(
            name=SEMANTIC_CONFIG,
            prioritized_fields=SemanticPrioritizedFields(
                title_field=SemanticField(field_name="entity"),
                content_fields=[SemanticField(field_name="content")],
                keywords_fields=[SemanticField(field_name="grain"),
                                 SemanticField(field_name="sheet")],
            ),
        )],
    )
    vectors = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(
            name="hnsw-config",
            parameters=HnswParameters(m=4, ef_construction=400, ef_search=500,
                                      metric=VectorSearchAlgorithmMetric.COSINE))],
        profiles=[VectorSearchProfile(name="hnsw-profile",
                                      algorithm_configuration_name="hnsw-config")],
    )
    return SearchIndex(name=INDEX_NAME, fields=fields, semantic_search=semantic,
                       vector_search=vectors)


def search_credentials():
    """Endpoint and admin key, or a readable exit if search is not configured."""
    endpoint = os.getenv("AZURE_SEARCH_ENDPOINT", "")
    key = os.getenv("AZURE_SEARCH_KEY", "")
    if not endpoint.startswith("http") or "YOUR-SEARCH-SERVICE" in endpoint:
        raise SystemExit("Azure AI Search is not configured — set AZURE_SEARCH_ENDPOINT "
                         "and AZURE_SEARCH_KEY in backend-python/.env.")
    if not key or key.startswith("PASTE_") or key.startswith("YOUR_"):
        raise SystemExit("AZURE_SEARCH_KEY is not filled in — portal → your search "
                         "service → Settings → Keys → Primary admin key. Admin, not "
                         "query: query keys are read-only and cannot upload.")
    return endpoint, key


def index_client():
    endpoint, key = search_credentials()
    return SearchIndexClient(endpoint=endpoint, credential=AzureKeyCredential(key))


def search_client():
    endpoint, key = search_credentials()
    return SearchClient(endpoint=endpoint, index_name=INDEX_NAME,
                        credential=AzureKeyCredential(key))


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def cmd_chunks(args):
    """Build the chunks and print them. Contacts nothing, costs nothing.

    Run this first, always. It catches the problems that are expensive to find
    after upload, and it works without any Azure credentials at all — so the
    chunking can be checked before the service exists.
    """
    chunks = make_chunks()
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(chunks, handle, indent=2, ensure_ascii=False)
    print(f"CHUNKS: wrote {args.out}")

    # Read these. If they do not read like something a person could understand on
    # their own, search will not work either — every result depends on this text.
    for grain in ("document", "field"):
        sample = next(chunk for chunk in chunks if chunk["grain"] == grain)
        print(f"\n--- sample {grain} chunk: {sample['id']} ---\n{sample['content']}")


def cmd_build(args):
    """Create or update the index, then upload every chunk."""
    chunks = make_chunks()
    endpoint, _ = search_credentials()
    client = index_client()

    for chunk, vector in zip(chunks, embed([chunk["content"] for chunk in chunks])):
        chunk["content_vector"] = vector

    # Azure refuses to change an existing field's attributes — making a field
    # searchable, or changing an analyzer or vector dimensions, all fail with
    # CannotChangeExistingField. The only way through is to drop the index and
    # rebuild it, which is cheap here because the chunks are rebuilt from the
    # workbook every run anyway.
    if args.recreate:
        try:
            client.delete_index(INDEX_NAME)
            print(f"BUILD: deleted the existing {INDEX_NAME} index")
        except ResourceNotFoundError:
            pass

    try:
        client.create_or_update_index(index_schema())
    except HttpResponseError as error:
        if "CannotChangeExistingField" in str(error):
            raise SystemExit(
                f"BUILD: the schema changed in a way Azure cannot apply in place. "
                f"Re-run with --recreate to drop and rebuild {INDEX_NAME}."
            ) from error
        raise
    print(f"BUILD: index {INDEX_NAME} created/updated on {endpoint}")

    client = search_client()
    results = client.upload_documents(documents=chunks)
    failures = [r for r in results if not r.succeeded]
    for failure in failures:
        print(f"BUILD: FAILED {failure.key} — {failure.error_message}")
    print(f"BUILD: uploaded {len(results) - len(failures)}/{len(chunks)} chunks")
    if failures:
        raise SystemExit("BUILD: some chunks did not upload.")

    # Indexing is not instant; a count of 0 straight after upload is normal.
    time.sleep(3)
    print(f"BUILD: index now reports {client.get_document_count()} documents")


def answer_from(question, hits):
    """Write a prose answer grounded in the retrieved chunks — the RAG step.

    This is where the counting and ranking that search cannot do gets done. The
    model can read "5.6 percent" and "83.3 percent" and order them; BM25 and the
    reranker cannot. What it must not do is add anything the chunks do not say,
    which is what the system prompt spends most of its words on.
    """
    if not ai_service.is_configured():
        raise SystemExit("Azure OpenAI is not configured — set AZURE_OPENAI_BASE_URL, "
                         "AZURE_OPENAI_KEY and AZURE_OPENAI_DEPLOYMENT in .env.")

    context = "\n\n".join(f"[{hit['id']}] ({hit['sheet']}, {hit['grain']})\n{hit['content']}"
                          for hit in hits)
    print(f"RAG: asking {ai_service.get_deployment()} over {len(hits)} chunk(s)...")
    response = ai_service.get_client().chat.completions.create(
        model=ai_service.get_deployment(),
        messages=[
            {"role": "system", "content": RAG_SYSTEM},
            {"role": "user", "content": f"Chunks:\n\n{context}\n\nQuestion: {question}"},
        ],
    )
    print(f"RAG: done — {response.usage.total_tokens} tokens.\n")
    return response.choices[0].message.content or ""


def odata_filter(sheet_value, grain_value):
    """An OData filter for --sheet / --grain. Single quotes are escaped by doubling."""
    parts = []
    if sheet_value:
        parts.append("sheet eq '{}'".format(sheet_value.replace("'", "''")))
    if grain_value:
        parts.append("grain eq '{}'".format(grain_value.replace("'", "''")))
    return " and ".join(parts) or None


def cmd_ask(args):
    """Keyword search, then the semantic ranker rereads and reorders the shortlist."""
    top = args.top if args.top is not None else (RAG_CONTEXT if args.answer else 5)
    try:
        results = search_client().search(
            search_text=args.question,
            filter=odata_filter(args.sheet, args.grain),
            # Keyword and vector run together and the two result lists are merged
            # by Reciprocal Rank Fusion, which throws away the raw scores and uses
            # only each result's position — a BM25 score of 8.2 and a cosine
            # similarity of 0.79 are not comparable numbers. Worth doing because
            # the two methods fail in exactly opposite places.
            vector_queries=[VectorizedQuery(vector=embed([args.question])[0],
                                            k_nearest_neighbors=50,
                                            fields="content_vector")],
            query_type="semantic",
            semantic_configuration_name=SEMANTIC_CONFIG,
            select=["id", "sheet", "grain", "entity", "content"],
            top=top,
        )
        results = list(results)
    except HttpResponseError as error:
        if "semantic" in str(error).lower():
            raise SystemExit(
                "ASK: the semantic ranker is not available. It needs Basic tier or "
                "above, and must be switched on: portal → your search service → "
                "Settings → Semantic ranker."
            ) from error
        raise

    print(f"ASK: {len(results)} result(s) for {args.question!r}\n")
    for rank, hit in enumerate(results, start=1):
        score = hit.get("@search.reranker_score") or hit["@search.score"]
        print(f"{rank}. {hit['id']}  [{hit['sheet']} / {hit['grain']}]  score {score:.2f}")
        # When the model is about to read these, printing every chunk in full
        # buries the answer. Print the text only when a person is the reader.
        if not args.answer:
            print(f"   {hit['content']}\n")

    # Read the chunks that come back, not just the scores. Ask something you
    # already know the answer to and check the text says what you expect.

    if args.answer:
        if not results:
            raise SystemExit("\nASK: nothing was retrieved, so there is nothing to "
                             "answer from. Widen the question or drop the filter.")
        print()
        print(answer_from(args.question, results))


def main():
    parser = argparse.ArgumentParser(
        prog="pipeline.py",
        description="Index the invoice audit workbook into Azure AI Search and query it.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    chunks = subparsers.add_parser("chunks", help="build and print the chunks; no Azure needed")
    chunks.add_argument("--out", default="chunks.json")

    build = subparsers.add_parser("build", help="create the index and upload every chunk")
    build.add_argument("--recreate", action="store_true",
                       help="drop the index first — needed after any schema change")

    ask = subparsers.add_parser("ask", help="keyword search plus the semantic ranker")
    ask.add_argument("question")
    ask.add_argument("--sheet", choices=[LOG_SHEET, COMP_SHEET, VAL_SHEET])
    ask.add_argument("--grain", choices=["document", "field"])
    ask.add_argument("--top", type=int, default=None,
                     help=f"chunks to retrieve (default 5, or {RAG_CONTEXT} with --answer)")
    ask.add_argument("--answer", action="store_true",
                     help="RAG: have the model write an answer from the retrieved chunks")

    args = parser.parse_args()
    {"chunks": cmd_chunks, "build": cmd_build, "ask": cmd_ask}[args.command](args)


if __name__ == "__main__":
    main()
