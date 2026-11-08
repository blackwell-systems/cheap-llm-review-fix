#!/usr/bin/env python3
"""Merge several structured reviews of the same code into one table.

Each input is a JSON review written by `ask_model.py --json-schema
review_schema.json`. Findings are grouped by file and symbol (and a
similar title within the same symbol), so agreement between models shows
at a glance. Every finding's `evidence` is checked against the actual file
in the repository: evidence that isn't found in the file (after whitespace
normalisation) is flagged, which usually means the model reconstructed or
invented the code it is talking about. Each finding's `symbol` is also
located (a function, method, class or constant of that name) and given its line range,
flagged when the symbol doesn't exist or the evidence sits outside it (a
finding filed under the wrong function). A `test_file` that doesn't exist is
flagged in the same column ("test file X not found") and keeps the finding
out of the auto-confirmed batch. The lead still verifies every claim;
this only orders the work and catches the obvious misses.

Symbols are found for Python, GDScript and common brace languages (see
symbols.py); for any other language pass --no-symbols to skip that check.

With --batch-out, the confirmed findings are written for fix_batch.py: two or
more models on the same symbol with every check passing, plus any ids the
lead passes with --confirm.

  merge_reviews.py review_a.json review_b.json --out merged.md
"""
import argparse
import json
import os
import re
import sys

from symbols import symbol_range  # noqa: F401  (re-exported)

SEVERITY = {"high": 0, "medium": 1, "low": 2}


def squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def evidence_found(root: str, path: str, evidence: str) -> str:
    full = os.path.join(root, path)
    if not os.path.isfile(full):
        return "file not found"
    with open(full, encoding="utf-8", errors="replace") as handle:
        source = squash(handle.read())
    lines = [squash(line) for line in evidence.splitlines() if squash(line)]
    if not lines:
        return "no evidence"
    hits = sum(1 for line in lines if line in source)
    if hits == len(lines):
        return "ok"
    return "partial" if hits else "NOT FOUND"


def symbol_check(root: str, path: str, symbol: str, evidence: str, skip: bool = False) -> str:
    """Where `symbol` is and whether the evidence quote sits inside it:
    `L120-L148`, `L120-L148, evidence outside`, `L120-L148, evidence partly
    outside` or `symbol not found`. A model that files a finding under the
    wrong function, or quotes code from elsewhere, shows up here. The symbol
    finder knows Python, GDScript and the brace languages listed in symbols.py;
    any other file gives `symbol not found` (use `skip` / `--no-symbols`)."""
    if skip:
        return "not checked"
    full = os.path.join(root, path)
    if not os.path.isfile(full):
        return "file not found"
    with open(full, encoding="utf-8", errors="replace") as handle:
        lines = handle.read().splitlines()
    span = symbol_range(lines, symbol, path)
    if span is None:
        return "symbol not found"
    start, end = span
    where = f"L{start}-L{end}"
    quotes = [squash(line) for line in evidence.splitlines() if squash(line)]
    found = [[number for number, line in enumerate(lines, 1) if quote in squash(line)] for quote in quotes]
    found = [numbers for numbers in found if numbers]
    if not found:
        return where
    inside = sum(1 for numbers in found if any(start <= n <= end for n in numbers))
    if inside == len(found):
        return where
    return f"{where}, evidence {'partly ' if inside else ''}outside"


def symbol_column(root: str, finding: dict, skip: bool = False) -> str:
    """The symbol check for a finding, plus `; test file X not found` when its
    test_file doesn't exist (a fixer given a missing file can only fail)."""
    result = symbol_check(root, finding.get("file", ""), finding.get("symbol", ""),
                          finding.get("evidence", ""), skip=skip)
    test_file = finding.get("test_file", "")
    if test_file and not os.path.isfile(os.path.join(root, test_file)):
        result += f"; test file {test_file} not found"
    return result


FIELDS = ("title", "severity", "confidence", "kind", "file", "symbol", "evidence", "situation", "fix",
          "test_file", "related")


def passes_checks(finding: dict) -> bool:
    """Evidence found in the file, the test file exists, and (unless symbols
    weren't checked) the symbol exists and holds the evidence."""
    return finding["evidence_check"] == "ok" and not any(
        word in finding["symbol_check"] for word in ("not found", "outside"))


def write_batch(path: str, groups: list[dict], confirmed: set[str]) -> None:
    """Writes the findings ready for fixing as one review-format file. A
    finding goes in when the lead confirmed it (its id or its group number),
    or when it passes the evidence and symbol checks and two or more models
    reported problems in the same function. Two models' versions of one
    issue both go in: the second fixer sees the fixed code and can answer
    'not a bug'."""
    chosen: list[dict] = []
    for index, group in enumerate(groups, 1):
        agreed = len({f["model"] for f in group["findings"]}) >= 2
        for finding in group["findings"]:
            if str(index) in confirmed or finding["id"] in confirmed or (agreed and passes_checks(finding)):
                entry = {key: finding[key] for key in FIELDS if key in finding}
                entry.setdefault("related", [])
                entry["_id"] = finding["id"]
                entry["_model"] = finding["model"]
                chosen.append(entry)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"summary": f"Fix batch: {len(chosen)} findings.", "findings": chosen, "checked_ok": []},
                  handle, indent=1, ensure_ascii=False)
    missing = sum(1 for entry in chosen if not entry.get("test_file"))
    print(f"{len(chosen)} findings in the batch -> {path}" + (f" ({missing} have no test_file)" if missing else ""))


def label(review_path: str, review: dict) -> str:
    model = (review.get("_meta") or {}).get("model") or os.path.basename(review_path)
    return model.split("/")[-1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("reviews", nargs="+", help="structured review JSON files")
    parser.add_argument("--root", default=".", help="repository root for evidence checks")
    parser.add_argument("--out", help="write the merged Markdown here (default: stdout)")
    parser.add_argument("--batch-out", help="also write a fix batch (review-format JSON for "
                        "fix_batch.py): findings that pass both checks in groups two or more models "
                        "reported, plus any named in --confirm")
    parser.add_argument("--no-symbols", action="store_true", help="skip the symbol check (for languages "
                        "the symbol finder doesn't know); a finding then needs only its evidence to be "
                        "found for the batch's auto-confirm")
    parser.add_argument("--confirm", default="", help="ids the lead checked and confirmed, comma "
                        "separated: a group number (all its findings, for example 3) or one finding (3b)")
    args = parser.parse_args()

    groups: list[dict] = []
    for path in args.reviews:
        with open(path, encoding="utf-8") as handle:
            review = json.load(handle)
        who = label(path, review)
        for finding in review.get("findings", []):
            file, evidence = finding.get("file", ""), finding.get("evidence", "")
            finding = dict(finding, model=who, evidence_check=evidence_found(args.root, file, evidence),
                           symbol_check=symbol_column(args.root, finding, skip=args.no_symbols))
            key = (finding.get("file", ""), finding.get("symbol", ""))
            # One group per file and symbol: findings about the same function are
            # listed together, so overlapping and conflicting claims sit side by side.
            match = next((group for group in groups if group["key"] == key), None)
            if match is None:
                match = {"key": key, "title": finding["title"], "findings": []}
                groups.append(match)
            match["findings"].append(finding)

    groups.sort(key=lambda g: (min(SEVERITY.get(f["severity"], 3) for f in g["findings"]),
                               -len({f["model"] for f in g["findings"]})))
    out = ["# Merged review", "",
           "| # | File / symbol | Title | Models | Severity | Confidence | Evidence | Symbol |",
           "|---|---|---|---|---|---|---|---|"]
    for index, group in enumerate(groups, 1):
        models = sorted({f["model"] for f in group["findings"]})
        sev = min((f["severity"] for f in group["findings"]), key=lambda s: SEVERITY.get(s, 3))
        conf = ", ".join(f"{f['model']}: {f['confidence']}" for f in group["findings"])
        checks = ", ".join(f"{f['model']}: {f['evidence_check']}" for f in group["findings"])
        symbols = "; ".join(sorted({f["symbol_check"] for f in group["findings"]}))
        out.append(f"| {index} | `{group['key'][0]}` {group['key'][1]} | {group['title']} | "
                   f"{', '.join(models)} | {sev} | {conf} | {checks} | {symbols} |")
    out += ["", "## Details", ""]
    for index, group in enumerate(groups, 1):
        out.append(f"### {index}. {group['title']}")
        for letter, finding in zip("abcdefghijklmnopqrstuvwxyz", group["findings"]):
            finding["id"] = f"{index}{letter}"
            out += [f"- **{finding['id']} {finding['model']}** ({finding['severity']}, {finding['confidence']}, {finding['kind']}; "
                    f"evidence {finding['evidence_check']}; `{finding['file']}` {finding['symbol_check']}): "
                    f"{finding['situation']}",
                    f"  - Fix: {finding['fix']}",
                    f"  - Evidence: `{squash(finding['evidence'])[:300]}`"]
        out.append("")
    text = "\n".join(out) + "\n"
    if args.batch_out:
        write_batch(args.batch_out, groups, {item.strip() for item in args.confirm.split(",") if item.strip()})
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text)
        print(f"{len(groups)} merged findings from {len(args.reviews)} reviews -> {args.out}")
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
