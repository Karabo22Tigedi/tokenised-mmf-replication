"""Exploratory check (18 November 2026): were Franklin's signing keys issuer-account signers
at the moment they signed?

Reads the frozen Stellar operation crawl (data/raw/stellar/freeze_2026-10-16/ops/) for every
set_options operation sourced by the BENJI issuer account, and the 44 FOBXX holder-to-holder rows
of data/processed/addl_signer_function.csv. For each signature key on each transfer it records the
key's weight on the issuer account at the transaction time (from the last earlier set_options
operation for that key). No network access; no frozen file or frozen measure is changed.

Outputs (data/processed/exploratory/2026-11-18/):
- franklin_signer_timing.csv: one row per (transfer, key) with the issuer-account status at signing.
- franklin_signer_timing_summary.csv: per-key added/removed times, first/last use and uses.

Usage: python -I -B scripts/25_check_franklin_signer_timing_2026_11_18.py
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import stellar_events as SE  # noqa: E402

ISSUER = "GBHNGLLIE3KWGKCHIKMHJ5HVZHYIK7WTBE4QF5PLAKL4CJGSEU7HZIW5"  # BENJI issuer (FOBXX)
OPS = ROOT / "data/raw/stellar/freeze_2026-10-16/ops"
SIGNERS = ROOT / "data/processed/addl_signer_function.csv"
OUT = ROOT / "data/processed/exploratory/2026-11-18"


def main() -> None:
    history: dict[str, set[tuple[str, int]]] = {}
    earliest = None
    for f in sorted(OPS.glob("*.jsonl.gz")):
        for page in SE.read_pages(f):
            for r in page.get("_embedded", {}).get("records", []):
                t = r["created_at"]
                earliest = t if earliest is None or t < earliest else earliest
                if (r["type"] == "set_options" and r.get("source_account") == ISSUER
                        and str(r.get("signer_key", "")).startswith("G")):
                    history.setdefault(r["signer_key"], set()).add((t, int(r.get("signer_weight", 0))))
    hist = {k: sorted(v) for k, v in history.items()}  # pages overlap: de-duplicate, then order by time

    with SIGNERS.open(encoding="utf-8", newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if r["fund"] == "FOBXX" and r["pair"] == "hh"]
    rows.sort(key=lambda r: (r["ts_utc"], r["tx"]))

    uses = []
    for r in rows:
        for key in sorted(k for k in r["signer"].split(";") if k):
            h = hist.get(key, [])
            before = [w for t, w in h if t <= r["ts_utc"]]
            if before:
                status = "issuer signer" if before[-1] > 0 else "removed before signing"
            elif h:
                status = "added after signing"
            else:
                status = "no issuer set_options in crawl"
            uses.append(dict(ts_utc=r["ts_utc"], tx=r["tx"], key=key,
                             issuer_weight_at_signing=before[-1] if before else "",
                             status_at_signing=status))

    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "franklin_signer_timing.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(uses[0]))
        w.writeheader()
        w.writerows(uses)

    summary = []
    for key in sorted({u["key"] for u in uses}):
        h = hist.get(key, [])
        used = sorted(u["ts_utc"] for u in uses if u["key"] == key)
        summary.append(dict(key=key,
                            added_utc=";".join(t for t, w in h if w > 0),
                            removed_utc=";".join(t for t, w in h if w == 0),
                            first_use_utc=used[0], last_use_utc=used[-1], uses=len(used),
                            uses_not_issuer_signer=sum(1 for u in uses if u["key"] == key
                                                       and u["status_at_signing"] != "issuer signer")))
    with (OUT / "franklin_signer_timing_summary.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(summary[0]))
        w.writeheader()
        w.writerows(summary)

    bad = sum(s["uses_not_issuer_signer"] for s in summary)
    print(f"ops crawl earliest record: {earliest}")
    print(f"transfers: {len(rows)}; key uses: {len(uses)}; distinct keys: {len(summary)}; "
          f"key uses not by a then-current issuer signer: {bad}")


if __name__ == "__main__":
    main()
