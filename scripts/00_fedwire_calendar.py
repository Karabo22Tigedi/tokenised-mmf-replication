"""00_fedwire_calendar.py  -- TR-4 business calendar (Federal Reserve Bank holidays 2024-2026)

Source: Federal Reserve Board, "Federal Reserve Holidays" (page k8.htm), which lists the days the
Federal Reserve Banks (and so Fedwire / FRBNY) are closed:
  * current page (lists 2026-2030):  https://www.federalreserve.gov/aboutthefed/k8.htm
      raw: data/raw/frb/2026-10-16_federalreserve_k8_holidays.htm
  * the same page as archived on 2024-06-14 (lists 2024-2028), via the Internet Archive:
      https://web.archive.org/web/20240614134448id_/https://www.federalreserve.gov/aboutthefed/k8.htm
      raw: data/raw/frb/2026-10-16_wayback_k8_2024.htm
Observance rules (footnotes on both pages, and the FRB Services holiday page
https://www.frbservices.org/about/holiday-schedules, raw: data/raw/frb/2026-10-16_frbservices_holiday_schedules.html):
  * holiday on a Saturday: "the Federal Reserve Banks are open" (the preceding Friday is a
    business day for the Reserve Banks; only the Board closes)  -> no closure added;
  * holiday on a Sunday: Reserve Banks closed the following Monday -> Monday is the closure.
Ad hoc closure check: 9 Jan 2025 (National Day of Mourning) is NOT a closure for Fedwire. NY Fed
operating policy of 2 Jan 2025 says "The Fedwire Securities Service will operate during its regular
hours on that date" (raw: data/raw/frb/2026-10-16_nyfed_operating_policy_250102a.html,
https://www.newyorkfed.org/markets/opolicy/operating_policy_250102a).

Output: data/processed/ref_fedwire_holidays.csv  (holiday, year, listed_date, weekday,
        reserve_banks_closed_date [empty if no closure], source_file)
The two sources overlap for 2026-2028; the script asserts that they agree.
Run: python -I scripts/00_fedwire_calendar.py
"""
import csv
import datetime as dt
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "frb"
OUT = ROOT / "data" / "processed" / "ref_fedwire_holidays.csv"
FILES = ["2026-10-16_wayback_k8_2024.htm", "2026-10-16_federalreserve_k8_holidays.htm"]
MONTHS = {m: i for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July", "August",
                                      "September", "October", "November", "December"], 1)}
YEARS = (2024, 2025, 2026)


def parse(fname):
    t = (RAW / fname).read_text(encoding="utf-8", errors="replace")
    i = t.find("<table")
    tbl = t[i:t.find("</table>", i)]
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", tbl, flags=re.S)
    cells = [[re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c)).replace("&nbsp;", "").strip()
              for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", r, flags=re.S)] for r in rows]
    years = [int(y) for y in cells[0][1:]]
    out = {}
    for c in cells[1:]:
        name = c[0].replace("’", "'")
        for y, v in zip(years, c[1:]):
            m = re.match(r"([A-Z][a-z]+) (\d+)", v)
            out[(name, y)] = dt.date(y, MONTHS[m.group(1)], int(m.group(2)))
    return out


def main():
    allh = {}
    for f in FILES:
        h = parse(f)
        print(f"{f}: {len(h)} holiday-years, years {sorted({y for _, y in h})}")
        for k, d in h.items():
            if k in allh:
                assert allh[k][0] == d, f"sources disagree on {k}: {allh[k][0]} vs {d}"
            allh[k] = (d, f)
    rows = []
    for (name, y), (d, f) in sorted(allh.items(), key=lambda x: x[1][0]):
        if y not in YEARS:
            continue
        wd = d.weekday()
        if wd == 5:        # Saturday: Reserve Banks open (Board closed the Friday) -> no closure
            closed = ""
        elif wd == 6:      # Sunday: Reserve Banks closed the following Monday
            closed = (d + dt.timedelta(days=1)).isoformat()
        else:
            closed = d.isoformat()
        rows.append(dict(holiday=name, year=y, listed_date=d.isoformat(), weekday=d.strftime("%a"),
                         reserve_banks_closed_date=closed, source_file=f))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {OUT.name}: {len(rows)} rows ({sum(1 for r in rows if r['reserve_banks_closed_date'])} closures)")
    for r in rows:
        print(f"  {r['listed_date']} {r['weekday']} {r['holiday']:40} closed: {r['reserve_banks_closed_date'] or '(none: Saturday)'}")


if __name__ == "__main__":
    main()
