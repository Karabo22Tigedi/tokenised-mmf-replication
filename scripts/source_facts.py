"""Publication adapters for factual fields extracted from held third-party pages.

The scientific algorithms remain in the original analysis modules. These readers
substitute explicitly identified CSV extracts for whole issuer PDFs/web pages.
Missing extracts fail rather than changing the available evidence silently.
"""
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FACTS = ROOT / "data/source_facts"


def issuer_addresses():
    with (FACTS / "issuer_contracts.csv").open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if len(rows) != 18:
        raise ValueError("Expected 18 held-vintage issuer/address records")
    return rows


def franklin_modules():
    from collections import defaultdict
    modules = defaultdict(set)
    with (FACTS / "franklin_modules.csv").open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            modules[row["chain"]].add(row["address"])
    return modules


def label_record(path):
    name = Path(path).resolve().relative_to(ROOT.resolve()).as_posix()
    with (FACTS / "label_titles.csv").open(encoding="utf-8", newline="") as f:
        return next((r for r in csv.DictReader(f) if r["source_path"] == name), None)


def label_title(path):
    row = label_record(path)
    return row["title"] if row else ""


def label_exists(path):
    return label_record(path) is not None
