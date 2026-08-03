"""Completeness, split by whether the field could have been there at all.

The audit reports one completeness figure per field and treats them all alike.
That makes the report read worse than the extraction actually is: `Vendor Fax
Number` scores 5.6% not because OCR missed it seventeen times but because
seventeen invoices did not print one. Azure's prebuilt-invoice schema is built
around US-style invoices and carries fields — service period dates, remittance
address, previous unpaid balance, customer id — that an Indian GST invoice
simply does not have.

Separating the two turns "39.4% complete, extraction is failing" into "93% on
the fields that matter, and five invoices are genuinely missing a vendor GSTIN",
which is the finding worth taking to whoever owns the audit.

    python applicability.py          # the full breakdown
    python applicability.py --gaps   # only the fields that are real problems

Contacts nothing and costs nothing. Deliberately does NOT write to the workbook:
that has to keep mirroring the mentor's DriftTemplateInterns.xlsx exactly, so
this stays a separate report.
"""

import argparse

from phase1 import COMP_SHEET, CORE_FIELDS, sheet


def rows():
    """Every field with its completeness, tagged core or optional."""
    frame = sheet(COMP_SHEET)
    return [
        {
            "field": str(row["OCR_Field"]),
            "percent": float(row["Completeness_Percent"]) * 100,
            "populated": int(row["Total_Records"]) - int(row["Null_Count"]),
            "total": int(row["Total_Records"]),
            "core": str(row["OCR_Field"]) in CORE_FIELDS,
        }
        for _, row in frame.iterrows()
    ]


def show(title, group):
    print(f"\n{title}")
    for item in sorted(group, key=lambda i: -i["percent"]):
        print(f"    {item['field']:<28} {item['percent']:6.1f}%   "
              f"{item['populated']:>2} of {item['total']}")
    if group:
        mean = sum(i["percent"] for i in group) / len(group)
        print(f"    {'-- average --':<28} {mean:6.1f}%")
    return sum(i["percent"] for i in group) / len(group) if group else 0.0


def main():
    parser = argparse.ArgumentParser(
        prog="applicability.py",
        description="Split completeness by whether a field applies to these invoices.")
    parser.add_argument("--gaps", action="store_true",
                        help="only the core fields that are below 100%%")
    args = parser.parse_args()

    everything = rows()
    core = [item for item in everything if item["core"]]
    optional = [item for item in everything if not item["core"]]
    gaps = sorted((item for item in core if item["percent"] < 100),
                  key=lambda i: i["percent"])

    if args.gaps:
        print("Core fields below 100% — these are the real extraction gaps:")
        for item in gaps:
            missing = item["total"] - item["populated"]
            print(f"    {item['field']:<28} {item['percent']:6.1f}%   "
                  f"missing from {missing} of {item['total']} documents")
        if not gaps:
            print("    none — every core field extracted from every document.")
        return

    core_mean = show("CORE — a GST invoice cannot be without these:", core)
    other_mean = show("OPTIONAL — mostly absent from Indian GST invoices "
                      "by design, not by failure:", optional)
    overall = sum(i["percent"] for i in everything) / len(everything)

    print(f"\n{'=' * 62}")
    print(f"  Headline reads       {overall:5.1f}%   (all {len(everything)} fields, "
          f"the number the report shows)")
    print(f"  Fields that matter   {core_mean:5.1f}%   ({len(core)} core fields)")
    print(f"  Everything else      {other_mean:5.1f}%   ({len(optional)} optional fields)")
    print("=" * 62)

    if gaps:
        print("\nThe genuine gaps — core fields that did not always extract:")
        for item in gaps:
            missing = item["total"] - item["populated"]
            print(f"    {item['field']:<28} missing from {missing} of "
                  f"{item['total']} documents")
        print("\nThat is the audit finding. Everything in the OPTIONAL group above "
              "\nis schema noise and should not be reported as an extraction failure.")

    print("\nCompleteness still only means the field was populated — never that the "
          "\nvalue was right. See the ground-truth manifest note in the README.")


if __name__ == "__main__":
    main()
