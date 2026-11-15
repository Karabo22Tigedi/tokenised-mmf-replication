"""ADDITION (exploratory, cycle 2 D9): holder-to-holder turnover, 2026-01-01 to 2026-09-30.
turnover_pct = lower-bound (holder-to-holder) transfer value over the window / non-TA on-chain supply at 2026-09-30.
Inputs (frozen or logged-addition outputs): addl_concentration.csv (lower-bound value), freeze_m3_holding.csv
(balances_total, ta_share_pct at 2026-09-30), key_numbers.csv (wallet 0x0c561ed8 share of WTGXX lower bound).
Writes data/processed/addl_turnover.csv."""
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
P = ROOT / "data/processed"
conc = {(r["fund"], r["bound"]): r for r in csv.DictReader(open(P / "addl_concentration.csv", encoding="utf-8"))}
m3 = {r["fund"]: r for r in csv.DictReader(open(P / "freeze_m3_holding.csv", encoding="utf-8"))
      if r["snapshot"] == "2026-09-30" and r["row"] == "M3"}
kn = {r["name"] if "name" in r else list(r.values())[0]: r for r in csv.DictReader(open(P / "key_numbers.csv", encoding="utf-8"))}
key = "addl_pmwallet_f1_lower_value_touching_share_of_lower_pct"
row = next(r for k, r in kn.items() if k == key)
wallet_share = float(list(row.values())[1])

out = []
for fund, bound in [("WTGXX", "lower (hh)"), ("FOBXX", "lower = upper")]:
    c = conc.get((fund, bound)) or next(v for (f, b), v in conc.items() if f == fund and b.startswith("lower"))
    value = float(c["value"])
    supply = float(m3[fund]["balances_total"]) * (1 - float(m3[fund]["ta_share_pct"]) / 100)
    rec = {"fund": fund, "window": "2026-01-01:2026-09-30", "hh_value_shares": round(value, 2),
           "nonta_supply_2026-09-30": round(supply, 2), "turnover_pct": round(100 * value / supply, 4)}
    if fund == "WTGXX":
        v2 = value * (1 - wallet_share / 100)
        rec["hh_value_excl_wallet_0x0c561ed8"] = round(v2, 2)
        rec["turnover_excl_wallet_pct"] = round(100 * v2 / supply, 4)
    out.append(rec)
fields = sorted({k for r in out for k in r}, key=lambda k: list(out[0]).index(k) if k in out[0] else 99)
with open(P / "addl_turnover.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(out)
for r in out:
    print(r)
