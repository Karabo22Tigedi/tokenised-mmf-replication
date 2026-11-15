"""ADDITION (exploratory, cycle 4, X1): timing of dividend mints on non-business days.
Frozen inputs only: freeze_mint_labels.csv (TR-6 labels), ref_fedwire_holidays.csv (TR-4).
Window 2026-01-01 to 2026-09-30 ET (the frozen M4 window).
For each fund: dividend-mint days that are non-business days (ET date), split by whether every
dividend mint that day falls before 06:00 ET and the previous calendar day is a business day
(the pattern of a previous-business-day dividend minted the next morning), plus the ET hour mix.
Writes data/processed/addl_divtiming_summary.csv and addl_divtiming_days.csv."""
import csv
from collections import defaultdict, Counter
from datetime import datetime, date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
P = ROOT / "data/processed"
ET = ZoneInfo("America/New_York")
hol = set()
for r in csv.DictReader(open(P / "ref_fedwire_holidays.csv", encoding="utf-8")):
    if r["reserve_banks_closed_date"]:  # same column as scripts/rules.py TR-4
        hol.add(date.fromisoformat(r["reserve_banks_closed_date"]))

def business(d):
    return d.weekday() < 5 and d not in hol

days = defaultdict(list)  # (fund, et_date) -> list of et datetimes
for r in csv.DictReader(open(P / "freeze_mint_labels.csv", encoding="utf-8")):
    if r["label"] != "dividend":
        continue
    t = datetime.fromisoformat(r["ts_utc"].replace("Z", "+00:00")).astimezone(ET)
    if not (date(2026, 1, 1) <= t.date() <= date(2026, 9, 30)):
        continue
    days[(r["fund"], t.date())].append(t)

summary, rows = [], []
for fund in sorted({f for f, _ in days}):
    fd = {d: ts for (f, d), ts in days.items() if f == fund}
    nb = [d for d in fd if not business(d)]
    early = [d for d in nb if all(t.hour < 6 for t in fd[d]) and business(d - timedelta(days=1))]
    hours = Counter(t.hour for d in nb for t in fd[d])
    for d in sorted(nb):
        rows.append({"fund": fund, "et_date": d.isoformat(), "weekday": d.strftime("%a"),
                     "n_mints": len(fd[d]), "min_hour": min(t.hour for t in fd[d]),
                     "max_hour": max(t.hour for t in fd[d]),
                     "prev_day_business": business(d - timedelta(days=1)),
                     "all_before_06_after_business_day": d in early})
    summary.append({"fund": fund, "n_dividend_days": len(fd), "n_nonbusiness_dividend_days": len(nb),
                    "n_nonbusiness_days_early_after_business_day": len(early),
                    "n_nonbusiness_days_other": len(nb) - len(early),
                    "nonbusiness_mint_hours_et": ";".join(f"{h}:{n}" for h, n in sorted(hours.items()))})
for name, data in [("addl_divtiming_summary.csv", summary), ("addl_divtiming_days.csv", rows)]:
    with open(P / name, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(data[0])); w.writeheader(); w.writerows(data)
for s in summary:
    print(s)
