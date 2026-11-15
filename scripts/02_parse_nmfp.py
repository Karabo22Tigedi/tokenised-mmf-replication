"""02_parse_nmfp.py  (PILOT, not the frozen G2 vintage)

Parses every N-MFP3 primary_doc.xml in data/raw/edgar/ (downloaded by 01_edgar_nmfp_pull.py)
and writes:
  data/processed/pilot_nmfp_summary.csv  one row per filing x share class
  data/processed/pilot_nmfp_daily.csv    one row per filing x share class x day
                                          (daily NAV/share, gross subs, gross reds, 7d net yield)

All share classes in a series are kept (Fidelity's series has the OnChain class FYOXX and
the conventional Liquidity class FYHXX, the twin). Nothing is dropped or imputed; missing
XML fields are written as empty.

N-MFP3 field names used (Form N-MFP3 XML, tags as they appear, namespaces stripped):
  series:  netAssetOfSeries, numberOfSharesOutstanding, stablePricePerShare,
           sevenDayGrossYield/{sevenDayGrossYieldValue,Date},
           dailyNetAssetValuePerShareSeries/{...Series, ...DateSeries},
           liquidAssetsDetails/percentage{Daily,Weekly}LiquidAssets
  class:   classFullName, classesId, minInitialInvestment, netAssetsOfClass,
           numberOfSharesOutstanding, dailyNetAssetValuePerShareClass/{...},
           dialyShareholderFlowReported/{dailyGrossSubscriptions, dailyGrossRedemptions,
           dailyShareHolderFlowDate}   (sic: SEC tag is spelled 'dialy'),
           monthlyShareholderFlowReported/{totalGrossSubscriptions,totalGrossRedemptions},
           sevenDayNetYield/{...}, beneficialRecordOwnerCategory/{...},
           shareCancellationReportingPeriod
Run: python -I scripts/02_parse_nmfp.py
Prep/freeze mode (added 2026-10-16):
  python -I scripts/02_parse_nmfp.py --prefix prep [--max-period 2026-09-30]
  parses every N-MFP3 original and amendment and writes <prefix>_nmfp_summary.csv / _daily.csv
  with extra columns is_amendment and governing (IMPL-12: for each ticker x period the most
  recently filed version governs; the others are kept with governing=0). With the default
  --prefix pilot only the three pilot periods (originals) are parsed, as in the pilot run.
"""
import argparse
import csv
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "edgar"
PROC = ROOT / "data" / "processed"
FNAME = re.compile(r"(\d{4}-\d{2}-\d{2})_([A-Z]+)_(N-MFP\d(?:-A)?)_(\d{4}-\d{2}-\d{2})_([\d-]+)\.xml$")


def strip_ns(root):
    for e in root.iter():
        if "}" in e.tag:
            e.tag = e.tag.split("}", 1)[1]
    return root


def txt(e, path):
    x = e.find(path)
    return x.text.strip() if x is not None and x.text else ""


def fnum(s):
    return float(s) if s not in ("", None) else None


def parse(path: Path):
    m = FNAME.search(path.name)
    access_date, ticker, form, period, acc = m.groups()
    root = strip_ns(ET.parse(path).getroot())
    fd = root.find("formData")
    gi, sl = fd.find("generalInfo"), fd.find("seriesLevelInfo")
    # series-level daily NAV and series 7-day gross yield (keyed by date)
    s_nav = {txt(x, "dailyNetAssetValuePerShareDateSeries"): txt(x, "dailyNetAssetValuePerShareSeries")
             for x in sl.findall("dailyNetAssetValuePerShareSeries")}
    s_gy = {txt(x, "sevenDayGrossYieldDate"): txt(x, "sevenDayGrossYieldValue")
            for x in sl.findall("sevenDayGrossYield")}
    series = dict(
        ticker_file=ticker, form=form, accession=acc, raw_file=path.name,
        report_date=txt(gi, "reportDate"), registrant=txt(gi, "registrantFullName"),
        cik=txt(gi, "cik"), series_id=txt(gi, "seriesId"), series_name=txt(gi, "nameOfSeries"),
        n_classes=txt(gi, "totalShareClassesInSeries"),
        mmf_category=txt(sl, "moneyMarketFundCategory"),
        retail_flag=txt(sl, "fundRetailMoneyMarketFlag"),
        series_net_assets=txt(sl, "netAssetOfSeries"),
        series_shares_outstanding=txt(sl, "numberOfSharesOutstanding"),
        stable_price_per_share=txt(sl, "stablePricePerShare"),
        wam_days=txt(sl, "averagePortfolioMaturity"), wal_days=txt(sl, "averageLifeMaturity"),
        pct_daily_liquid=txt(sl, "liquidAssetsDetails/percentageDailyLiquidAssets"),
        pct_weekly_liquid=txt(sl, "liquidAssetsDetails/percentageWeeklyLiquidAssets"),
        series_nav_per_share_last=s_nav[max(s_nav)] if s_nav else "",
        series_nav_last_date=max(s_nav) if s_nav else "",
        series_7d_gross_yield_last=s_gy[max(s_gy)] if s_gy else "",
    )
    summary, daily = [], []
    for c in fd.findall("classLevelInfo"):
        cid = txt(c, "classesId")
        nav = {txt(x, "dailyNetAssetValuePerShareDateClass"): txt(x, "dailyNetAssetValuePerShareClass")
               for x in c.findall("dailyNetAssetValuePerShareClass")}
        ny = {txt(x, "sevenDayNetYieldDate"): txt(x, "sevenDayNetYieldValue")
              for x in c.findall("sevenDayNetYield")}
        flows = defaultdict(dict)
        for x in c.findall("dialyShareholderFlowReported"):
            d = txt(x, "dailyShareHolderFlowDate")
            flows[d] = dict(subs=txt(x, "dailyGrossSubscriptions"), reds=txt(x, "dailyGrossRedemptions"))
        owners = []
        for x in c.findall("beneficialRecordOwnerCategory"):
            cat = txt(x, "beneficialRecordOwnerCategoryType")
            if cat == "Other":
                cat = "Other:" + txt(x, "otherInvestorCategory")
            owners.append(f"{cat}|rec={txt(x, 'percentOutstandingSharesRecord')}"
                          f"|ben={txt(x, 'percentOutstandingSharesBeneficial')}")
        sum_subs = sum(fnum(v["subs"]) or 0 for v in flows.values())
        sum_reds = sum(fnum(v["reds"]) or 0 for v in flows.values())
        summary.append(dict(
            series,
            class_id=cid, class_name=txt(c, "classFullName"),
            min_initial_investment=txt(c, "minInitialInvestment"),
            class_net_assets=txt(c, "netAssetsOfClass"),
            class_shares_outstanding=txt(c, "numberOfSharesOutstanding"),
            class_nav_per_share_last=nav[max(nav)] if nav else "",
            class_nav_last_date=max(nav) if nav else "",
            class_nav_min=min(fnum(v) for v in nav.values()) if nav else "",
            class_nav_max=max(fnum(v) for v in nav.values()) if nav else "",
            monthly_gross_subs=txt(c, "monthlyShareholderFlowReported/totalGrossSubscriptions"),
            monthly_gross_reds=txt(c, "monthlyShareholderFlowReported/totalGrossRedemptions"),
            n_daily_flow_rows=len(flows),
            sum_daily_gross_subs=round(sum_subs, 2), sum_daily_gross_reds=round(sum_reds, 2),
            class_7d_net_yield_last=ny[max(ny)] if ny else "",
            beneficial_record_owner_categories=";".join(owners),
            share_cancellation_flag=txt(c, "shareCancellationReportingPeriod"),
        ))
        for d in sorted(set(nav) | set(flows) | set(ny)):
            daily.append(dict(ticker_file=ticker, report_date=series["report_date"], series_id=series["series_id"],
                              class_id=cid, class_name=txt(c, "classFullName"), date=d,
                              class_nav_per_share=nav.get(d, ""), series_nav_per_share=s_nav.get(d, ""),
                              gross_subs=flows.get(d, {}).get("subs", ""),
                              gross_reds=flows.get(d, {}).get("reds", ""),
                              class_7d_net_yield=ny.get(d, ""), series_7d_gross_yield=s_gy.get(d, ""),
                              accession=acc))
    return summary, daily


def write(rows, name):
    with open(PROC / name, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {name}: {len(rows)} rows")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", default="pilot")
    ap.add_argument("--max-period", default="2026-09-30")
    a = ap.parse_args()
    files = sorted(p for p in RAW.glob("*_N-MFP*.xml") if FNAME.search(p.name))
    if a.prefix == "pilot":   # reproduce the pilot: originals for Jul-Sep 2026 only
        files = [p for p in files if FNAME.search(p.name).group(4) in ("2026-07-31", "2026-08-31", "2026-09-30")
                 and not FNAME.search(p.name).group(3).endswith("-A")]
    files = [p for p in files if FNAME.search(p.name).group(4) <= a.max_period]
    # if the same accession was downloaded on several days, keep the latest download only
    latest = {}
    for p in files:
        latest[FNAME.search(p.name).group(5)] = p
    files = sorted(latest.values())
    print(f"{len(files)} N-MFP XML files found")
    S, D = [], []
    for p in files:
        s, d = parse(p)
        S += s
        D += d
    # governing version per ticker x report period x class: latest filing (accession order is
    # filer-agent specific, so use the filing date from the filings list when available)
    import csv as _csv
    fl = PROC / f"{a.prefix}_nmfp_filings.csv"
    fdate = {}
    if fl.exists():
        with open(fl, newline="") as fh:
            fdate = {r["accession"]: r["filing_date"] for r in _csv.DictReader(fh)}
    for r in S:
        r["is_amendment"] = int(r["form"].endswith("-A"))
        r["filing_date"] = fdate.get(r["accession"], "")
    best = {}
    for r in S:
        k = (r["ticker_file"], r["report_date"], r["class_id"])
        if k not in best or (r["filing_date"], r["is_amendment"]) > (best[k]["filing_date"], best[k]["is_amendment"]):
            best[k] = r
    gov_acc = {(k, r["accession"]) for k, r in best.items()}
    for r in S:
        r["governing"] = int(((r["ticker_file"], r["report_date"], r["class_id"]), r["accession"]) in gov_acc)
    for r in D:
        r["governing"] = int(((r["ticker_file"], r["report_date"], r["class_id"]), r["accession"]) in gov_acc)
    S.sort(key=lambda r: (r["ticker_file"], r["report_date"], r["class_id"], r["filing_date"]))
    write(S, f"{a.prefix}_nmfp_summary.csv")
    write(D, f"{a.prefix}_nmfp_daily.csv")
    if a.prefix != "pilot":
        print(f"{len(S)} filing-class rows; amendments {sum(r['is_amendment'] for r in S)}; "
              f"governing {sum(r['governing'] for r in S)}")
        return
    print("\nticker  class                                          period      class_net_assets  "
          "class_shares      nav_last  m_subs        m_reds        daily_rows 7d_net")
    for r in S:
        print(f"{r['ticker_file']:6}  {r['class_name'][:45]:45}  {r['report_date']}  "
              f"{r['class_net_assets']:>16}  {r['class_shares_outstanding']:>16}  {r['class_nav_per_share_last']:>7}  "
              f"{r['monthly_gross_subs']:>12}  {r['monthly_gross_reds']:>12}  {r['n_daily_flow_rows']:>3}  "
              f"{r['class_7d_net_yield_last']}")
        # consistency check: sum of daily flows vs reported monthly totals
        for k in ("subs", "reds"):
            mt, sd = fnum(r[f"monthly_gross_{k}"]), r[f"sum_daily_gross_{k}"]
            if mt is not None and abs(mt - sd) > 1:
                print(f"     note: sum daily gross {k} {sd:,.2f} != monthly total {mt:,.2f}")
        print(f"     owners: {r['beneficial_record_owner_categories']}")


if __name__ == "__main__":
    main()
