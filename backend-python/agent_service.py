"""A Semantic Kernel agent that decides which tool answers the question.

Search cannot count and cannot rank by value; SQL cannot answer "which vendors
keep sending bad documents". Until now a person had to know which of those a
question was and run the right script. This agent makes that choice itself, and
shows its working.

    python agent_service.py "how many documents are missing the vendor GSTIN"
    python agent_service.py "what was on invoice PSV/1650"

The interesting output is not the answer but the trace: which tool it picked and
what it passed. A right answer reached through the wrong tool is still a bug —
if it estimates a count from search hits instead of calling count_documents, the
number is a guess that happens to be close.

Semantic Kernel is used here rather than LangChain because it is already the
Microsoft-native fit for this Azure stack, and it is what requirements.txt pins.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from openai import AsyncOpenAI
from semantic_kernel import Kernel
from semantic_kernel.connectors.ai import FunctionChoiceBehavior
from semantic_kernel.connectors.ai.open_ai import (
    OpenAIChatCompletion,
    OpenAIChatPromptExecutionSettings,
)
from semantic_kernel.contents import (
    ChatHistory,
    FunctionCallContent,
    FunctionResultContent,
)
from semantic_kernel.functions import kernel_function

import sql_comparison
import keyword_search
import hybrid_search

load_dotenv(Path(__file__).with_name(".env"))

SERVICE_ID = "audit-chat"
PLUGIN_NAME = "audit"

# Upper bound on any model-chosen result count. Deliberately far above the 84
# chunks in the index, so it never quietly truncates a growing workbook - it is
# only here to stop an absurd value reaching Azure.
MAX_TOP = 1000

# Everything the model may count. A field name from this list is substituted into
# a parameterised query; a model-authored SQL fragment never reaches the database.
COUNTABLE_FIELDS = tuple(sorted(set(keyword_search.CORE_FIELDS) | {
    "Amount Due", "Due Date", "Payment Term", "Purchase Order", "Customer Id",
    "Billing Address", "Shipping Address", "Remittance Address", "Service Address",
    "Previous Unpaid Balance", "Vendor Fax Number", "Vendor Phone Number",
}))

# The routing rules. Most of this is the same hard-won guidance the RAG prompt
# carries, because the agent can reach the same wrong answers in the same ways.
AGENT_SYSTEM = f"""You answer questions about an invoice extraction audit by
choosing the right tool. You have no knowledge of this audit beyond what the
tools return.

Choosing:
- "How many" / "how often" / anything wanting an exact number -> count_documents.
  NEVER count search results yourself and never estimate; search returns a
  handful of matching chunks, not the whole population.
- A specific named invoice -> get_document.
- Open-ended, comparative, or "which ones look like X" -> search_chunks.
- Before calling any field's low percentage a failure -> field_applicability.

Answering:
- Say which tool you used and what it returned.
- Never invent a number, a field name or a document number.
- A core field falling short is a real extraction gap. An optional field with a
  low figure usually means the invoice never had that field — say so rather than
  reporting it as bad extraction.
- Completeness means a field was populated. It never means the value was
  correct. Never describe an extraction as accurate or correct.
- Be brief. No preamble.

Countable field names: {", ".join(COUNTABLE_FIELDS)}."""


class AuditTools:
    """The four things the agent can actually do."""

    @kernel_function(
        name="count_documents",
        description="Exact count of documents where a named field is empty. Use "
                    "for any question asking how many. Returns a precise number.")
    async def count_documents(
        self,
        field: Annotated[str, "Exact field name, e.g. 'Vendor GSTIN'."],
    ) -> Annotated[str, "JSON with the count and the document numbers."]:
        if field not in COUNTABLE_FIELDS:
            return json.dumps({"error": f"{field!r} is not a countable field.",
                               "countable_fields": list(COUNTABLE_FIELDS)})

        def run():
            connection = sql_comparison.load_table()
            rows = connection.execute(
                "SELECT number FROM extraction_log "
                "WHERE field_name = ? AND completeness_flag = 'N' ORDER BY number",
                (field,)).fetchall()
            total = connection.execute(
                "SELECT COUNT(DISTINCT number || '|' || source_file) FROM extraction_log"
            ).fetchone()[0]
            return {"field": field, "missing_from": len(rows), "of_documents": total,
                    "documents": [r[0] for r in rows]}

        return json.dumps(await asyncio.to_thread(run))

    @kernel_function(
        name="search_chunks",
        description="Search the audit index for chunks matching a question. Use "
                    "for open-ended or comparative questions. Does not count.")
    async def search_chunks(
        self,
        query: Annotated[str, "What to search for, in natural language."],
        grain: Annotated[str, "'document', 'field', or '' for both."] = "",
        top: Annotated[int, "How many chunks to return. Ask for more than you "
                            "think you need: a question about fields in general "
                            "needs most of them, not the closest few."]
             = hybrid_search.RAG_CONTEXT,
    ) -> Annotated[str, "JSON list of {id, entity, sheet, content}."]:
        # The model picks this number, so it has to survive a bad pick. Azure
        # answers top=0 with an empty list rather than an error, which would
        # read back as "the audit found nothing", and rejects a negative
        # outright.
        top = max(1, min(top, MAX_TOP))

        def run():
            results = hybrid_search.search_client().search(
                search_text=query,
                filter=hybrid_search.odata_filter(None, grain or None),
                query_type="semantic",
                semantic_configuration_name=hybrid_search.SEMANTIC_CONFIG,
                select=["id", "sheet", "grain", "entity", "content"],
                top=top,
            )
            return [{"id": hit["id"], "entity": hit["entity"], "sheet": hit["sheet"],
                     "content": hit["content"]} for hit in results]

        return json.dumps(await asyncio.to_thread(run))

    @kernel_function(
        name="get_document",
        description="Everything the audit knows about one invoice, by its number.")
    async def get_document(
        self,
        number: Annotated[str, "The invoice number, e.g. 'PSV/1650'."],
    ) -> Annotated[str, "JSON for that one document, or a not-found note."]:
        def run():
            results = list(hybrid_search.search_client().search(
                search_text=number, filter="grain eq 'document'",
                select=["id", "entity", "content"], top=5))
            for hit in results:
                if str(hit["entity"]).strip().casefold() == number.strip().casefold():
                    return {"id": hit["id"], "number": hit["entity"],
                            "content": hit["content"]}
            return {"error": f"No document numbered {number!r}.",
                    "closest": [hit["entity"] for hit in results]}

        return json.dumps(await asyncio.to_thread(run))

    @kernel_function(
        name="field_applicability",
        description="Whether a field is core to every GST invoice or optional. A "
                    "low figure on an optional field usually means the invoice "
                    "never had one, not that extraction failed.")
    async def field_applicability(
        self,
        field: Annotated[str, "Exact field name, e.g. 'Vendor Fax Number'."],
    ) -> Annotated[str, "JSON saying core or optional, and what that implies."]:
        core = field in keyword_search.CORE_FIELDS
        return json.dumps({
            "field": field,
            "classification": "core" if core else "optional",
            "meaning": ("Every GST invoice must carry this, so a shortfall is a "
                        "genuine extraction gap.") if core else
                       ("Often absent from Indian GST invoices by design. A low "
                        "figure usually means the field does not apply rather "
                        "than that extraction failed."),
        })


def build_kernel():
    """Kernel wired to Azure OpenAI through the plain v1 endpoint.

    OpenAIChatCompletion with a supplied async_client, never AzureChatCompletion:
    that class demands an AsyncAzureOpenAI and forces an api-version, which is
    exactly what this project avoids everywhere else. ai_model_id is required
    even when the client is supplied, and for the v1 endpoint it is the
    deployment name.
    """
    base_url = os.getenv("AZURE_OPENAI_BASE_URL", "").rstrip("/")
    if not base_url.startswith("http"):
        raise SystemExit("Azure OpenAI is not configured — set AZURE_OPENAI_BASE_URL "
                         "and AZURE_OPENAI_KEY in backend-python/.env.")
    if not base_url.endswith("/openai/v1"):
        base_url += "/openai/v1"

    kernel = Kernel()
    kernel.add_service(OpenAIChatCompletion(
        ai_model_id=os.getenv("AZURE_OPENAI_DEPLOYMENT", ""),
        service_id=SERVICE_ID,
        async_client=AsyncOpenAI(base_url=base_url,
                                 api_key=os.getenv("AZURE_OPENAI_KEY", "")),
    ))
    kernel.add_plugin(AuditTools(), plugin_name=PLUGIN_NAME)
    return kernel


def trace_from(history):
    """Pull the tool calls out of the chat history.

    Semantic Kernel mutates the ChatHistory it is given: the assistant's tool
    calls and their results are appended as content items. Reading them back is
    what lets the UI show the routing decision rather than just the answer.
    """
    steps = []
    for message in history.messages:
        for item in message.items:
            if isinstance(item, FunctionCallContent):
                steps.append({"type": "call", "call_id": item.id,
                              "tool": item.function_name,
                              "arguments": item.parse_arguments() or {}})
            elif isinstance(item, FunctionResultContent):
                steps.append({"type": "result", "call_id": item.id,
                              "tool": item.function_name,
                              "result": str(item.result)})
    return steps


async def ask(question):
    """Answer one question. Returns the answer and the tools it took to get there."""
    kernel = build_kernel()
    settings = OpenAIChatPromptExecutionSettings(
        service_id=SERVICE_ID,
        function_choice_behavior=FunctionChoiceBehavior.Auto(
            filters={"included_plugins": [PLUGIN_NAME]}),
    )

    history = ChatHistory()
    history.add_system_message(AGENT_SYSTEM)
    history.add_user_message(question)

    answer = await kernel.get_service(SERVICE_ID).get_chat_message_content(
        chat_history=history, settings=settings, kernel=kernel)
    return {"question": question, "answer": str(answer), "trace": trace_from(history)}


def main():
    parser = argparse.ArgumentParser(
        prog="agent_service.py",
        description="Ask the audit a question and let the agent pick the tool.")
    parser.add_argument("question")
    parser.add_argument("--json", action="store_true", help="print raw JSON")
    args = parser.parse_args()

    result = asyncio.run(ask(args.question))
    if args.json:
        print(json.dumps(result, indent=2))
        return

    print(f"AGENT: {args.question!r}\n")
    for step in result["trace"]:
        if step["type"] == "call":
            arguments = ", ".join(f"{k}={v!r}" for k, v in step["arguments"].items())
            print(f"  -> {step['tool']}({arguments})")
        else:
            summary = " ".join(step["result"].split())
            print(f"     {summary[:160]}{'...' if len(summary) > 160 else ''}\n")
    if not result["trace"]:
        print("  (no tool was called — the model answered from the prompt alone)\n")
    print(result["answer"])


if __name__ == "__main__":
    main()
