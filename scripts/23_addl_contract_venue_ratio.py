"""ADDITION (exploratory, cycle 5, harmoniser B open point 1): contract-venue value against N-MFP3 gross redemptions, WTGXX.
Venue value = swap-route transfers (addl_swap_summary.csv total_shares) + Frax custodian in+out
(addl_frax_custodian.csv value_in + value_out), 2026-01-01 to 2026-09-30.
Denominator = N-MFP3 gross redemptions, token class, governing rows (addl_turnover_vs_reds.csv nmfp_gross_reds_sum, WTGXX).
Atomic stablecoin-paired value from addl2_summary.csv is reported alongside where available.
Writes data/processed/addl_contract_venue_ratio.csv."""
import csv
from pathlib import Path

P = Path(__file__).resolve().parent.parent / "data/processed"
swap = next(csv.DictReader(open(P / "addl_swap_summary.csv", encoding="utf-8")))
frax = next(r for r in csv.DictReader(open(P / "addl_frax_custodian.csv", encoding="utf-8")) if r["fund"] == "WTGXX")
reds = next(r for r in csv.DictReader(open(P / "addl_turnover_vs_reds.csv", encoding="utf-8"))
            if r["fund"] == "WTGXX" and r["wallet_mode"] == "with_wallet")
swap_v = float(swap["total_shares"])
frax_v = float(frax["value_in"]) + float(frax["value_out"])
den = float(reds["nmfp_gross_reds_sum"])
row = {"fund": "WTGXX", "window": "2026-01-01:2026-09-30", "swap_route_shares": round(swap_v, 6),
       "custodian_route_shares": round(frax_v, 6), "venue_total_shares": round(swap_v + frax_v, 6),
       "nmfp_gross_reds_sum": den, "venue_share_of_gross_reds_pct": round(100 * (swap_v + frax_v) / den, 4),
       "swap_share_of_gross_reds_pct": round(100 * swap_v / den, 4),
       "custodian_share_of_gross_reds_pct": round(100 * frax_v / den, 4)}
with open(P / "addl_contract_venue_ratio.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(row)); w.writeheader(); w.writerow(row)
print(row)
