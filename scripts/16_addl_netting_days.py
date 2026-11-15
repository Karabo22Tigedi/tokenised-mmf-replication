"""ADDITION (exploratory, CP42; offset magnitude added cycle 2, D2): N-MFP3 business days with both gross subscriptions and
gross redemptions above zero, 2026-01-01 to 2026-09-30, token classes, governing rows only.
Reads data/processed/freeze_nmfp_daily.csv (frozen); writes data/processed/addl_nmfp_netting_days.csv."""
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data/processed/freeze_nmfp_daily.csv"
OUT = ROOT / "data/processed/addl_nmfp_netting_days.csv"
CLASSES = {"FOBXX": "C000215714", "WTGXX": "C000245468", "JLTXX": "C000272118", "FYOXX": "C000261149"}

rows = list(csv.DictReader(open(SRC, encoding="utf-8")))
out = []
for fund, cls in CLASSES.items():
    days = {}
    for r in rows:
        if r["ticker_file"] != fund or r["class_id"] != cls or r["governing"] != "1":
            continue
        if not ("2026-01-01" <= r["date"] <= "2026-09-30"):
            continue
        days[r["date"]] = (float(r["gross_subs"] or 0), float(r["gross_reds"] or 0))
    both = sum(1 for s, d in days.values() if s > 0 and d > 0)
    gross = sum(s + d for s, d in days.values())
    offset = sum(min(s, d) for s, d in days.values())
    out.append({"fund": fund, "class_id": cls, "window": "2026-01-01:2026-09-30",
                "n_days": len(days), "n_days_both_subs_and_reds_positive": both,
                "share_pct": round(100 * both / len(days), 4) if days else "",
                "gross_subs_plus_reds_usd": round(sum(s for s, _ in days.values()) + sum(d for _, d in days.values()), 2),
                "sum_daily_min_subs_reds_usd": round(offset, 2),
                # share of gross flow (subs + reds) removed by daily netting: the netted amount leaves both legs
                "netted_share_of_gross_flow_pct": round(100 * 2 * offset / gross, 4) if gross else ""})
with open(OUT, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(out[0]))
    w.writeheader(); w.writerows(out)
for r in out:
    print(r)
