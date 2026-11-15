"""10_diag_stellar_claimable.py: read-only diagnostic of unconverted Stellar claimable balances.

stellar_events.py converts a create_claimable_balance only when the issuer creates it (a mint).
Holder-created claimable balances (FOBXX: 134 in the crawl) and every claim_claimable_balance
are counted in diagnostics but not converted, so they are absent from M1. This script asks how
many of the holder-created balances, and what value, could have moved shares from one holder to
another (the F1 numerator), using only the frozen raw Horizon pages.

Classification of each holder-created balance by its claimant list (source = creating account):
  self_and_issuer_only   every claimant is the source or the issuer: the shares can only return
                         to the source or go to the issuer (a burn); never holder-to-holder
  other_holder_possible  at least one claimant is another non-issuer, non-TA account: a
                         holder-to-holder transfer would follow if that claimant claimed
  ta_side                another claimant exists and either it or the source is a TA-role account
                         (TA to holder, or holder to TA): not secondary under M1
Direction of the bias on the main F1 basis (transfers_only, kind == transfer): a conversion
can add an event to the denominator only, or to both numerator and denominator. Adding to
both raises the share; adding to the denominator only (a ta_side balance) would lower it.
So the omission understates the FOBXX secondary share unless ta_side balances exist.
self_and_issuer_only balances end as a return to the source (no event) or an issuer claim (a
burn), which is outside the transfers_only basis; on the all_events basis a burn would enter
the denominator only.

Inputs (read-only): data/raw/stellar/freeze_2026-10-16/ (ops pages, asset_*.json),
data/processed/freeze_wallet_roles.csv, data/processed/freeze_m1_secondary.csv.
Output: data/processed/freeze_diag_stellar_claimable.csv (fund, scope, test, value, note).
No rule, measure or verdict is changed.

Run: python -I scripts/10_diag_stellar_claimable.py
"""
import csv
import datetime as dt
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import rules as R  # noqa: E402
import stellar_events as SE  # noqa: E402

PROC = ROOT / "data" / "processed"
CRAWL = ROOT / "data" / "raw" / "stellar" / "freeze_2026-10-16"
ASSETS = {"FOBXX": "BENJI", "WTGXX": "WTGX"}
W0, W1 = dt.date(2026, 1, 1), dt.date(2026, 9, 30)


def main():
    roles = defaultdict(dict)
    with open(PROC / "freeze_wallet_roles.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["chain"] == "stellar":
                roles[r["fund"]][r["address"]] = r["role"]
    m1 = {}
    with open(PROC / "freeze_m1_secondary.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["chain_scope"] == "pooled" and r["basis"] == "transfers_only":
                m1[r["fund"]] = r

    issuers = {}
    for fund, code in ASSETS.items():
        rec = json.loads((CRAWL / f"asset_{code}.json").read_bytes())["_embedded"]["records"][0]
        issuers[fund] = rec["asset_issuer"]

    seen = set()
    created = defaultdict(list)       # fund -> list of dicts
    claims_by = Counter()             # claimant role buckets for claim ops (asset unknown)
    for fpath in sorted((CRAWL / "ops").glob("*.jsonl.gz")):
        for pg in SE.read_pages(fpath):
            for r in pg.get("_embedded", {}).get("records", []):
                if r["id"] in seen:
                    continue
                t = r["type"]
                if t == "claim_claimable_balance":
                    seen.add(r["id"])
                    if r.get("transaction_successful", True):
                        cl = r.get("claimant")
                        b = "issuer_FOBXX" if cl == issuers["FOBXX"] else "issuer_WTGXX" if cl == issuers["WTGXX"] else "other"
                        claims_by[b] += 1
                    continue
                if t != "create_claimable_balance":
                    continue
                for fund, code in ASSETS.items():
                    iss = issuers[fund]
                    if str(r.get("asset", "")).startswith(f"{code}:{iss}") and r.get("source_account") != iss:
                        seen.add(r["id"])
                        if not r.get("transaction_successful", True):
                            continue
                        src = r["source_account"]
                        dests = [c["destination"] for c in r.get("claimants", [])]
                        others = [d for d in dests if d not in (src, iss)]
                        ta = [d for d in others if roles[fund].get(d) == "TA"]
                        if others and (ta or roles[fund].get(src) == "TA"):
                            cls = "ta_side"
                        elif others:
                            cls = "other_holder_possible"
                        else:
                            cls = "self_and_issuer_only"
                        ts = SE.ts(r["created_at"])
                        created[fund].append(dict(id=r["id"], ts=ts, d=R.et_date(ts), src=src, amt=float(r["amount"]),
                                                  cls=cls, n_claimants=len(dests),
                                                  issuer_claimant=iss in dests,
                                                  issuer_pred=json.dumps(next((c["predicate"] for c in r["claimants"]
                                                                               if c["destination"] == iss), None)),
                                                  src_role=roles[fund].get(src, "holder")))

    out = []

    def add(fund, scope, test, val, note=""):
        out.append(dict(fund=fund, scope=scope, test=test, value=val, note=note))

    for fund in ASSETS:
        rows = created[fund]
        for scope, sel in (("crawl_2025-09-01_to_freeze", rows),
                           ("window_2026-01-01_to_2026-09-30_ET", [x for x in rows if W0 <= x["d"] <= W1])):
            add(fund, scope, "holder_created_n", len(sel), "deduplicated by Horizon op id; successful tx only")
            add(fund, scope, "holder_created_value", round(sum(x["amt"] for x in sel), 7), "shares")
            for c in ("self_and_issuer_only", "other_holder_possible", "ta_side"):
                s = [x for x in sel if x["cls"] == c]
                add(fund, scope, f"{c}_n", len(s), "")
                add(fund, scope, f"{c}_value", round(sum(x["amt"] for x in s), 7), "shares")
            add(fund, scope, "with_issuer_as_claimant_n", sum(1 for x in sel if x["issuer_claimant"]), "")
            add(fund, scope, "distinct_source_accounts", len({x["src"] for x in sel}), "")
            add(fund, scope, "source_role_counts", ";".join(f"{k}:{v}" for k, v in sorted(Counter(x["src_role"] for x in sel).items())), "")
            add(fund, scope, "claimant_count_distribution", ";".join(f"{k}:{v}" for k, v in sorted(Counter(x["n_claimants"] for x in sel).items())), "")
            preds = Counter("not_abs_before" if '"not"' in x["issuer_pred"] and "abs_before" in x["issuer_pred"] else x["issuer_pred"][:40]
                            for x in sel if x["issuer_claimant"])
            add(fund, scope, "issuer_claimant_predicate_types", ";".join(f"{k}:{v}" for k, v in preds.items()), "")
            if sel:
                add(fund, scope, "first_et_date", min(x["d"] for x in sel).isoformat(), "")
                add(fund, scope, "last_et_date", max(x["d"] for x in sel).isoformat(), "")
        # bound on F1 if every window 'other_holder_possible' balance were a holder-to-holder transfer
        win = [x for x in rows if W0 <= x["d"] <= W1 and x["cls"] == "other_holder_possible"]
        if fund in m1:
            n, v = int(m1[fund]["n_events"]), float(m1[fund]["value"])
            un, uv = float(m1[fund]["upper_share_count_pct"]), float(m1[fund]["upper_share_value_pct"])
            hn, hv = len(win), sum(x["amt"] for x in win)
            add(fund, "window_2026-01-01_to_2026-09-30_ET", "f1_upper_value_pct_frozen", uv, "freeze_m1_secondary pooled transfers_only")
            add(fund, "window_2026-01-01_to_2026-09-30_ET", "f1_upper_value_pct_if_all_other_holder_possible_converted",
                round(100 * (uv / 100 * v + hv) / (v + hv), 4) if v + hv else "", "descriptive bound only; F1 measure unchanged")
            add(fund, "window_2026-01-01_to_2026-09-30_ET", "f1_upper_count_pct_frozen", un, "")
            add(fund, "window_2026-01-01_to_2026-09-30_ET", "f1_upper_count_pct_if_all_other_holder_possible_converted",
                round(100 * (un / 100 * n + hn) / (n + hn), 4) if n + hn else "", "descriptive bound only; F1 measure unchanged")
    for k, v in sorted(claims_by.items()):
        add("both", "crawl_2025-09-01_to_freeze", f"claim_ops_claimant_{k}", v,
            "claim_claimable_balance ops in the crawl (asset not identifiable in the op)")

    with open(PROC / "freeze_diag_stellar_claimable.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["fund", "scope", "test", "value", "note"])
        w.writeheader()
        w.writerows(out)
    for r in out:
        print(r["fund"], r["scope"], r["test"], r["value"], r["note"], sep=" | ")


if __name__ == "__main__":
    main()
