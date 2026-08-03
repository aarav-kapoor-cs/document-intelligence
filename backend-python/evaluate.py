"""Measure the retrieval methods against each other, instead of guessing.

Every claim about search — "hybrid is better", "the reranker fixes it" — is
testable here, because the audit workbook contains its own ground truth. If a
question is "which documents are missing the vendor GSTIN", SQL over the
extraction log knows the exact answer, so recall can be counted rather than
eyeballed.

    python evaluate.py            # all four methods, mean recall@5
    python evaluate.py --top 10   # same at a wider cutoff

Needs the vector index built:  python pipeline.py build

Written after a single query suggested vector search beat keyword search 3/5 to
2/5. Across five queries it does not, and that first result was noise. One query
is never enough to conclude anything.
"""

import argparse

from azure.search.documents.models import VectorizedQuery

import boundary
import pipeline

# Questions phrased the way a person would ask them, deliberately avoiding the
# wording the chunks use. Each pairs with the field whose absence defines the
# right answer, so SQL can supply the gold set.
QUERIES = (
    ("documents where the tax number did not come through", "Vendor GSTIN"),
    ("invoices missing the buyer's tax registration", "Customer GSTIN"),
    ("bills where the sales tax figure is absent", "GST Amount"),
    ("paperwork with no breakdown of taxes", "Tax Details"),
    ("invoices with no payment terms stated", "Payment Term"),
    ("which invoices never stated when payment was due", "Due Date"),
)

METHODS = ("keyword", "vector", "hybrid", "hybrid+rerank")


def gold_set(connection, field):
    """The documents where this field is empty — the exact answer, from SQL."""
    return {row[0] for row in connection.execute(
        "SELECT number FROM extraction_log WHERE field_name = ? AND completeness_flag = 'N'",
        (field,))}


def search_variants(question, top):
    """The same question run four ways."""
    vector = [VectorizedQuery(vector=pipeline.embed([question])[0],
                              k_nearest_neighbors=50, fields="content_vector")]
    return {
        "keyword": dict(search_text=question),
        "vector": dict(search_text=None, vector_queries=vector),
        "hybrid": dict(search_text=question, vector_queries=vector),
        "hybrid+rerank": dict(search_text=question, vector_queries=vector,
                              query_type="semantic",
                              semantic_configuration_name=pipeline.SEMANTIC_CONFIG),
    }


def main():
    parser = argparse.ArgumentParser(
        prog="evaluate.py", description="Compare retrieval methods against SQL ground truth.")
    parser.add_argument("--top", type=int, default=5, help="cutoff for recall@k (default 5)")
    args = parser.parse_args()

    connection = boundary.load_table()
    client = pipeline.search_client()
    totals = dict.fromkeys(METHODS, 0.0)

    # Kept under 80 columns on purpose — wider than that and the row wraps in a
    # standard terminal, which makes two adjacent percentages read as one number.
    header = f"{'question':<40} {'gold':>4}  " + " ".join(f"{m[:6]:>7}" for m in METHODS)
    print(header)
    print("-" * len(header))

    for question, field in QUERIES:
        gold = gold_set(connection, field)
        # Recall is capped at the cutoff: 8 right answers cannot all fit in a
        # top-5, and scoring that as 5/8 would punish a perfect result.
        reachable = min(args.top, len(gold))
        scores = {}
        for method, arguments in search_variants(question, args.top).items():
            hits = client.search(filter="grain eq 'document'", select=["entity"],
                                 top=args.top, **arguments)
            found = sum(1 for hit in hits if hit["entity"] in gold)
            scores[method] = found / reachable if reachable else 0.0
            totals[method] += scores[method]
        print(f"{question[:40]:<40} {len(gold):>4}  "
              + " ".join(f"{scores[m]:>7.0%}" for m in METHODS))

    print("-" * len(header))
    print(f"{'MEAN recall@' + str(args.top):<40} {'':>4}  "
          + " ".join(f"{totals[m] / len(QUERIES):>7.0%}" for m in METHODS))
    print("""
Read this before concluding anything about the methods: every one of these
questions is "find the documents where field X is empty", which is a WHERE
clause wearing a sentence. SQL answers all of them exactly and instantly —
see boundary.py. Weak numbers here are the retrieval layer being asked the
wrong kind of question, not the retrieval layer being broken.""")


if __name__ == "__main__":
    main()
