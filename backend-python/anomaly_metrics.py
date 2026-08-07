"""Measure the detector against the synthetic labels, locally and for free.

    python synthetic_invoices.py --count 600 --out /tmp/syn.jsonl
    python anomaly_metrics.py --data /tmp/syn.jsonl

This runs before the Azure ML job on purpose. If the rules alone already score
well, then a trained model is not obviously worth a compute cluster, and finding
that out costs nothing here and a queue wait plus a bill up there. The same
question is asked again inside train.py against the model, so the two answers can
be compared directly - rules-only is one of the arms in both places.

What is measured, per layer:

  rules_only     anomaly_rules, scored on whether any rule fired
  robust_z       the corpus layer, so the two corpus-only anomalies have somewhere
                 to be judged rather than counting as rule failures

And per anomaly type, because a single average hides everything useful: a
detector can look strong at 0.85 recall while missing one whole category
completely, and that category is what someone will ask about.

The honest caveat, repeated because it matters: these labels come from
synthetic_invoices.py, so what is measured is "can the rules find what this
generator injected". A real invoice fails in ways nobody wrote an injector for.
"""

import argparse
import json
from collections import defaultdict

import anomaly_detector
import anomaly_features
import anomaly_rules
import synthetic_invoices


def load(path) -> list:
    records = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _counts(predicted, actual) -> dict:
    true_positive = sum(1 for p, a in zip(predicted, actual) if p and a)
    false_positive = sum(1 for p, a in zip(predicted, actual) if p and not a)
    false_negative = sum(1 for p, a in zip(predicted, actual) if not p and a)
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1,
            "tp": true_positive, "fp": false_positive, "fn": false_negative}


def evaluate(records, model=None) -> dict:
    """Score every record and compare against its labels."""
    # The corpus layer needs the whole population at once, so everything goes
    # through scan() rather than being scored one at a time.
    as_rows = [{"id": index, "fileName": r["file_name"], "fields": r["fields"]}
               for index, r in enumerate(records)]
    report = anomaly_detector.scan(as_rows, model)
    by_id = {result["document_id"]: result for result in report["results"]}

    truth = [r["labels"] for r in records]
    fired_rule = []
    fired_corpus = []
    for index in range(len(records)):
        signals = by_id[index]["signals"]
        fired_rule.append(any(s["layer"] == "rule" for s in signals))
        fired_corpus.append(any(s["layer"] in ("corpus", "robust_z") for s in signals))

    intrinsic = [bool(t["is_intrinsic_anomaly"]) for t in truth]
    corpus_truth = [any(k in synthetic_invoices.CORPUS_ANOMALIES
                        for k in t["anomaly_types"]) for t in truth]
    anything = [bool(t["is_anomalous"]) for t in truth]
    fired_any = [r or c for r, c in zip(fired_rule, fired_corpus)]

    # The corpus layer is judged against ANY anomaly rather than only the two
    # corpus types, because an injection that multiplies the amounts - high_value
    # especially - genuinely does make that invoice an outlier for its vendor.
    # Scoring it against corpus types alone would count a correct flag as a miss.
    layers = {
        "rules_only (vs intrinsic anomalies)": _counts(fired_rule, intrinsic),
        "corpus layer (vs any anomaly)": _counts(fired_corpus, anything),
        "both layers (vs any anomaly)": _counts(fired_any, anything),
    }

    # Per type: of the records carrying this injected anomaly, how many were
    # flagged by anything at all.
    per_type = defaultdict(lambda: {"total": 0, "found": 0})
    for index, label in enumerate(truth):
        for kind in label["anomaly_types"]:
            per_type[kind]["total"] += 1
            if fired_any[index]:
                per_type[kind]["found"] += 1

    # Hard negatives are the precision test that matters: clean invoices built to
    # look alarming. Only the RULE layer is judged here. Two of the four hard
    # negatives (the credit note, the tiny courier invoice) are deliberately far
    # from their vendor's usual amount, so the corpus layer flagging them is
    # correct behaviour, not a false alarm - what would be wrong is an arithmetic
    # or format rule firing, and that count should be zero.
    hard = [index for index, t in enumerate(truth) if t["hard_negative"]]
    hard_flagged = sum(1 for index in hard if fired_rule[index])
    hard_corpus = sum(1 for index in hard if fired_corpus[index])

    return {
        "records": len(records),
        "layers": layers,
        "per_type": dict(per_type),
        "hard_negatives": {"total": len(hard), "false_alarms": hard_flagged,
                           "corpus_flags": hard_corpus},
        "notes": report["notes"],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Measure the anomaly rules against synthetic labels.")
    parser.add_argument("--data", default="synthetic_invoices.jsonl",
                        help="JSONL from synthetic_invoices.py")
    parser.add_argument("--model", default="",
                        help="an anomaly_model.json to include as a third arm")
    args = parser.parse_args(argv)

    records = load(args.data)
    model = anomaly_detector.load_model(args.model) if args.model else None
    report = evaluate(records, model)

    print(f"\n{report['records']} records from {args.data}")
    if model:
        print(f"Model: {model.get('run_id', '(unnamed)')}")
    for note in report["notes"]:
        print(f"  note: {note}")

    print(f"\n{'layer':40s} {'precision':>10s} {'recall':>8s} {'f1':>8s}   "
          f"{'tp':>5s} {'fp':>5s} {'fn':>5s}")
    print("  " + "-" * 84)
    for name, scores in report["layers"].items():
        print(f"{name:40s} {scores['precision']:10.3f} {scores['recall']:8.3f} "
              f"{scores['f1']:8.3f}   {scores['tp']:5d} {scores['fp']:5d} {scores['fn']:5d}")

    print(f"\n{'anomaly type':28s} {'injected':>9s} {'caught':>7s} {'recall':>8s}")
    print("  " + "-" * 55)
    # Anomalies nobody wrote a rule for. Low recall on these is expected and is
    # the argument for training a model, not a bug to go and fix.
    no_rule = {"confidence_collapse"}
    for kind, tally in sorted(report["per_type"].items(),
                              key=lambda item: item[1]["found"] / max(item[1]["total"], 1)):
        recall = tally["found"] / max(tally["total"], 1)
        if kind in no_rule:
            mark = "  (no rule - only the model can catch this)"
        elif recall < 0.5:
            # The rule and the injector disagree about what this anomaly IS,
            # which is a bug in one of them rather than a hard problem.
            mark = "  <-- check this rule"
        else:
            mark = ""
        print(f"{kind:28s} {tally['total']:9d} {tally['found']:7d} {recall:8.3f}{mark}")

    hard = report["hard_negatives"]
    rate = hard["false_alarms"] / max(hard["total"], 1)
    print(f"\nHard negatives (clean, but shaped like trouble): {hard['total']}")
    print(f"  falsely flagged by a rule: {hard['false_alarms']} ({rate:.1%}) "
          f"- this is the number that should be near zero")
    print(f"  flagged by the corpus layer: {hard['corpus_flags']} - expected, since "
          f"the credit note\n    and the tiny invoice really are far from their "
          f"vendor's usual amount")
    print("\nThese labels come from synthetic_invoices.py, so this measures how well "
          "the rules\nfind what that generator injects - not how they behave on real "
          "invoices.\n")


if __name__ == "__main__":
    main()
