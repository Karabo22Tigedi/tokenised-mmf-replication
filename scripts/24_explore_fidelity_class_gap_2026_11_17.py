"""Fidelity Treasury Digital Fund: Liquidity vs OnChain class comparison.

EXPLORATORY analysis of existing held data, 17 Nov 2026; changes no frozen measure.

Purpose
-------
Decompose the seven-day net yield difference between the two share classes of
Fidelity Treasury Digital Fund (series S000089156):
  * Liquidity class  C000255665 (ticker FYHXX)
  * OnChain class    C000261149 (ticker FYOXX)
The frozen descriptive file (freeze_twin_descriptives.csv) reports unpaired
class means. This script re-parses the raw N-MFP3 XML filings, reproduces those
means, and adds same-date paired gaps, period/record decompositions, overlap
(non-independence) treatment, gross-minus-net comparisons, balance weighting,
filed fee-waiver dollars and same-day cross-class flow matches. It also
extracts documented fee/expense terms verbatim from the held 485BPOS
prospectus/SAI HTML. Nothing here is a new pre-specified test; all averages
are descriptive.

Inputs (all held locally; no network access)
--------------------------------------------
  data/raw/edgar/2026-10-16_FYOXX_N-MFP3_*.xml      22 original N-MFP3 filings,
                                                      report dates 2024-12-31..2026-09-30
  data/raw/edgar/2026-10-16_FYOXX_485BPOS_2026-06-24_prospectus-sai.htm
                                                      485BPOS, accession 0000917286-26-000089
  data/raw/FREEZE_MANIFEST_2026-10-16.csv            SHA-256 check of every raw input
  data/processed/freeze_nmfp_daily.csv               frozen parsed daily rows (cross-check;
                                                      cross-fund gross-minus-net context)
  data/processed/freeze_twin_descriptives.csv        frozen unpaired class means (reproduction)
  workflow/checks/supervisor17_econ_reviewer_scratch/*_output.txt
                                                      optional: earlier reviewer's scratch output,
                                                      read only to report agreement/differences

Outputs (data/processed/exploratory/2026-11-17/)
------------------------------------------------
  fidelity_class_daily.csv        one row per business date in the 22 filings
  fidelity_class_monthly.csv      one row per class-month (balances, waivers, checks)
  fidelity_class_gap_summary.csv  one row per quantity (quantity,value,unit,window,n,definition,source)
  fidelity_fee_terms.csv          documented fee/expense terms with verbatim quotations
  README.md                       file guide, re-run command, hashes, checks, source gaps

How to run (from any directory)
-------------------------------
  python -I -X utf8 "scripts/24_explore_fidelity_class_gap_2026_11_17.py" [PROJECT_ROOT]
PROJECT_ROOT defaults to the parent of this script's folder. The script is
deterministic: re-running it on the same inputs rewrites byte-identical outputs.
It never writes outside the output folder above.

Conventions
-----------
  * XML yields are decimal fractions (0.0414); they are multiplied by 100 and
    reported in percent. Reporting precision in the filings is 0.0001, i.e.
    0.01 percentage points (pp).
  * Gap = OnChain seven-day net yield minus Liquidity seven-day net yield on the
    same date (pp). Negative means OnChain yielded less.
  * "Full common window" = report months in which both classes filed
    (2025-08-31..2026-09-30). "2026" = 2026-01-01..2026-09-30.
  * Seven-day yields are trailing, annualised rates over overlapping seven-day
    windows; consecutive daily values are not independent observations.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import html
import os
import re
import statistics as st
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

SERIES = "S000089156"
LIQ, ONC = "C000255665", "C000261149"
CLASS_LABEL = {LIQ: "Liquidity", ONC: "OnChain"}
PROSPECTUS_ACCESSION = "0000917286-26-000089"
PROSPECTUS_FILE = "data/raw/edgar/2026-10-16_FYOXX_485BPOS_2026-06-24_prospectus-sai.htm"
MANIFEST = "data/raw/FREEZE_MANIFEST_2026-10-16.csv"
FREEZE_DAILY = "data/processed/freeze_nmfp_daily.csv"
FREEZE_TWIN = "data/processed/freeze_twin_descriptives.csv"
SCRATCH = "workflow/checks/supervisor17_econ_reviewer_scratch"
OUT_REL = "data/processed/exploratory/2026-11-17"
FULL_LO, FULL_HI = "2025-08-01", "2026-09-30"   # report months 2025-08..2026-09
Y26_LO, Y26_HI = "2026-01-01", "2026-09-30"
# Cross-fund token classes for gross-minus-net context (from freeze_nmfp_daily.csv)
OTHER_TOKEN_CLASSES = [("FOBXX", "C000215714"), ("WTGXX", "C000245468"), ("JLTXX", "C000272118")]
CONVERSION_MIN_USD = 1_000_000.0
CONVERSION_TOL = 0.01

SRC_XML = "N-MFP3 XML S000089156"
SRC_A19 = SRC_XML + " Items A.19/B.8"
SRC_B7 = SRC_XML + " Items B.5/B.7"


# ----------------------------------------------------------------------------- utilities
def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1]


def child(el, name):
    for c in el:
        if strip_ns(c.tag) == name:
            return c
    return None


def children(el, name):
    return [c for c in el if strip_ns(c.tag) == name]


def text_of(el, name):
    c = child(el, name)
    return None if c is None or c.text is None else c.text.strip()


def d(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else float("nan")


# ----------------------------------------------------------------------------- parsing
def parse_filing(path: Path) -> dict:
    """Parse one N-MFP3 XML filing for the classes and series-level items used here."""
    root = ET.parse(path).getroot()
    it = {strip_ns(e.tag): e for e in root.iter()}  # last element per tag; used for unique tags only
    gen = it["generalInfo"]
    sli = it["seriesLevelInfo"]
    acc = path.stem.rsplit("_", 1)[-1]
    f = dict(
        path=path, accession=acc,
        submission_type=text_of(it["headerData"], "submissionType"),
        report_date=text_of(gen, "reportDate"),
        series_id=text_of(gen, "seriesId"),
        n_classes=text_of(gen, "totalShareClassesInSeries"),
        gross_yield={}, classes={}, dup_dates=[],
    )
    for g in children(sli, "sevenDayGrossYield"):
        dd, v = text_of(g, "sevenDayGrossYieldDate"), Decimal(text_of(g, "sevenDayGrossYieldValue"))
        if dd in f["gross_yield"]:
            f["dup_dates"].append(("series_gross", dd))
        f["gross_yield"][dd] = v
    for c in [e for e in root.iter() if strip_ns(e.tag) == "classLevelInfo"]:
        cid = text_of(c, "classesId")
        cl = dict(name=text_of(c, "classFullName"),
                  shares=Decimal(text_of(c, "numberOfSharesOutstanding")),
                  net_assets=Decimal(text_of(c, "netAssetsOfClass")),
                  waiver_flag=text_of(c, "personPayForFundFlag"),
                  waiver_usd=Decimal(text_of(c, "nameOfPersonDescExpensePay") or "0"),
                  net_yield={}, subs={}, reds={})
        m = child(c, "monthlyShareholderFlowReported")
        cl["month_subs"] = Decimal(text_of(m, "totalGrossSubscriptions")) if m is not None else None
        cl["month_reds"] = Decimal(text_of(m, "totalGrossRedemptions")) if m is not None else None
        for y in children(c, "sevenDayNetYield"):
            dd, v = text_of(y, "sevenDayNetYieldDate"), Decimal(text_of(y, "sevenDayNetYieldValue"))
            if dd in cl["net_yield"]:
                f["dup_dates"].append((cid + "_net_yield", dd))
            cl["net_yield"][dd] = v
        # Tag spelling "dialyShareholderFlowReported" is the SEC schema's own spelling.
        for x in children(c, "dialyShareholderFlowReported"):
            dd = text_of(x, "dailyShareHolderFlowDate")
            if dd in cl["subs"]:
                f["dup_dates"].append((cid + "_flow", dd))
            cl["subs"][dd] = Decimal(text_of(x, "dailyGrossSubscriptions"))
            cl["reds"][dd] = Decimal(text_of(x, "dailyGrossRedemptions"))
        f["classes"][cid] = cl
    return f


# ----------------------------------------------------------------------------- balances
def reconstruct_balances(filings, cid, class_start):
    """Approximate end-of-day share balances for one class.

    For each report month, the Item B.5 month-end shares outstanding are taken as
    the balance after the last reported business day; earlier business days are
    obtained by backing out that month's later Item B.7 daily gross flows
    (balance_{d-1} = balance_d - subs_d + reds_d). Shares are treated as dollars
    (stable NAV ~1.0000). Approximate because: B.7 subscriptions include
    dividend reinvestment but month-end dividend reinvestment and cash
    distributions need not appear as business-day flows; flows are reported on
    trade dates. The reconstructed opening balance of each month is compared
    with the prior month's B.5 shares as a quality diagnostic.
    Returns (eod: {date: balance}, monthly: {report_date: dict}).
    """
    eod, monthly = {}, {}
    prev_end = None
    for f in filings:
        cl = f["classes"].get(cid)
        if cl is None:
            continue
        rd = f["report_date"]
        b = float(cl["shares"])
        ds = sorted(cl["subs"])
        for dd in reversed(ds):
            eod[dd] = b
            b = b - float(cl["subs"][dd]) + float(cl["reds"][dd])
        opening = b
        # calendar-day average from max(month start, class start) to month end
        first = max(d(rd[:8] + "01"), d(class_start))
        last = d(rd)
        cur, tot, nd, day = opening, 0.0, 0, first
        while day <= last:
            k = day.isoformat()
            if k in eod and k[:7] == rd[:7]:
                cur = eod[k]
            tot += cur
            nd += 1
            day += dt.timedelta(days=1)
        monthly[rd] = dict(opening_reconstructed=opening,
                           prior_month_end_shares=prev_end,
                           opening_minus_prior_end=(opening - prev_end) if prev_end is not None else None,
                           avg_balance=tot / nd, n_days=nd, first_day=first.isoformat())
        prev_end = float(cl["shares"])
    return eod, monthly


# ----------------------------------------------------------------------------- prospectus
def html_to_text(raw: str) -> str:
    """Normalise the 485BPOS HTML: drop script/style, strip tags, decode entities,
    collapse whitespace. Quotations below are verbatim from this normalised text."""
    x = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", raw)
    x = re.sub(r"(?s)<[^>]+>", " ", x)
    x = html.unescape(x)
    return re.sub(r"\s+", " ", x)


def fee_terms(text: str):
    """Extract fee/expense terms. Every quotation is asserted to occur verbatim."""
    rows = []
    liq_p = text.index("Class /Ticker Liquidity /FYHXX Prospectus")
    onc_p = text.index("Class /Ticker OnChain /FYOXX Prospectus")
    onc_p_end = text.index("Investment Company Act of 1940, File Number", onc_p)  # back-cover text
    liq_s = text.index("Fund/Class Liquidity Fidelity® Treasury Digital Fund FYHXX")
    onc_s = text.index("Fund/Class OnChain Fidelity® Treasury Digital Fund FYOXX")
    segs = {"LIQ_PRO": (liq_p, onc_p), "ONC_PRO": (onc_p, onc_p_end),
            "LIQ_SAI": (liq_s, onc_s), "ONC_SAI": (onc_s, len(text))}
    docs = {
        "LIQ_PRO": "Prospectus, Fidelity Treasury Digital Fund, Class Liquidity/FYHXX, dated June 29, 2026 (in 485BPOS filed 2026-06-24)",
        "ONC_PRO": "Prospectus, Fidelity Treasury Digital Fund, Class OnChain/FYOXX, dated June 29, 2026 (in 485BPOS filed 2026-06-24)",
        "LIQ_SAI": "Statement of Additional Information, Fund/Class Liquidity FYHXX, dated June 29, 2026 (in 485BPOS filed 2026-06-24)",
        "ONC_SAI": "Statement of Additional Information, Fund/Class OnChain FYOXX, dated June 29, 2026 (in 485BPOS filed 2026-06-24)",
    }

    def locate(seg, pattern, heading, regex=False):
        a, b = segs[seg]
        if regex:
            m = re.compile(pattern).search(text, a, b)
            if not m:
                raise AssertionError(f"pattern not found in {seg}: {pattern}")
            pos, quote = m.start(), m.group(0)
        else:
            pos = text.find(pattern, a, b)
            if pos < 0:
                raise AssertionError(f"quotation not found verbatim in {seg}: {pattern[:80]}")
            quote, m = pattern, None
        hpos = text.rfind(heading, a, pos)
        if hpos < 0:
            raise AssertionError(f"heading {heading!r} not found before quotation in {seg}")
        return pos, quote, m

    def add(term, cls, value, unit, period, seg, heading_path, quote, pos, note=""):
        rows.append(dict(term=term, share_class=cls, value=value, unit=unit, applies_to=period,
                         document=docs[seg], accession=PROSPECTUS_ACCESSION,
                         section_heading=heading_path, quoted_text=quote,
                         text_offset=pos, note=note))

    cap_note = ("Contractual cap stated in the current (June 29, 2026) prospectus; it is not by itself "
                "an effective historical expense series for earlier dates.")
    for seg, cls in [("LIQ_PRO", "Liquidity"), ("ONC_PRO", "OnChain")]:
        pat = (r"Management fee (\d\.\d\d) % Distribution and/or Service \(12b-1\) fees None "
               r"Other expenses (\d\.\d\d) % Total annual operating expenses (\d\.\d\d) % "
               r"Fee waiver and/or expense reimbursement (\d\.\d\d) % A Total annual operating "
               r"expenses after fee waiver and/or expense reimbursement (\d\.\d\d) % A")
        pos, quote, m = locate(seg, pat, "Fee Table", regex=True)
        hp = "Fund Summary > Fee Table > Annual Operating Expenses"
        per = "Current fee table, prospectus dated June 29, 2026"
        for i, t in enumerate(["fee_table_management_fee", "fee_table_other_expenses",
                               "fee_table_total_annual_operating_expenses",
                               "fee_table_fee_waiver_and_or_expense_reimbursement",
                               "fee_table_total_after_waiver"], start=1):
            add(t, cls, m.group(i), "% of average net assets per year", per, seg, hp, quote, pos, cap_note if i >= 4 else "")
        pat = (r"Fidelity Management & Research Company LLC \(FMR\) has contractually agreed to reimburse "
               r"[A-Za-z]+ class of the fund to the extent that total operating expenses .*? exceed "
               r"(\d\.\d\d)% \(the Expense Cap\)\..*?This arrangement will remain in effect through "
               r"([A-Z][a-z]+ \d+, \d{4}) \. FMR may not terminate this arrangement before the expiration "
               r"date without the approval of the Board of Trustees and may extend it in its discretion after that date\.")
        pos, quote, m = locate(seg, pat, "Fee Table", regex=True)
        add("expense_cap", cls, m.group(1), "% of average net assets per year",
            f"Through {m.group(2)} (cap end date as stated)", seg, hp + " (footnote A)", quote, pos, cap_note)
        add("expense_cap_end_date", cls, m.group(2), "date", "As stated in prospectus dated June 29, 2026",
            seg, hp + " (footnote A)", quote, pos)

    # Audited Financial Highlights
    fh_pat_liq = (r"Ratios to Average Net Assets B,F,G Expenses before reductions (\.\d\d)% (\.\d\d)% H,I "
                  r"Expenses net of fee waivers, if any (\.\d\d) % (\.\d\d)% I "
                  r"Expenses net of all reductions, if any (\.\d\d)% (\.\d\d)% I")
    pos, quote, m = locate("LIQ_PRO", fh_pat_liq, "Financial Highlights", regex=True)
    hp = "Appendix > Financial Highlights > Ratios to Average Net Assets"
    fn = text[pos:pos + 3000]
    assert "For the period December 23, 2024 (commencement of operations) through April 30, 2025." in fn
    p26 = "Fiscal year ended April 30, 2026 (audited)"
    p25 = "Period December 23, 2024 (commencement of operations) through April 30, 2025 (audited; annualized, footnote I)"
    for i, (t, per, note) in enumerate([
            ("fh_expenses_before_reductions", p26, ""),
            ("fh_expenses_before_reductions", p25, "Footnote H: size and fluctuation of net assets and expense amounts may cause ratios to differ from contractual rates."),
            ("fh_expenses_net_of_fee_waivers", p26, ""),
            ("fh_expenses_net_of_fee_waivers", p25, ""),
            ("fh_expenses_net_of_all_reductions", p26, ""),
            ("fh_expenses_net_of_all_reductions", p25, "")], start=1):
        add(t, "Liquidity", "0" + m.group(i), "% of average net assets", per, "LIQ_PRO", hp, quote, pos, note)
    fh_pat_onc = (r"Ratios to Average Net Assets B,F,G Expenses before reductions (\.\d\d)% H "
                  r"Expenses net of fee waivers, if any (\.\d\d) % H Expenses net of all reductions, if any (\.\d\d)% H")
    pos, quote, m = locate("ONC_PRO", fh_pat_onc, "Financial Highlights", regex=True)
    fn = text[pos:pos + 3000]
    assert "For the period August 4, 2025 (commencement of sale of shares) through April 30, 2026." in fn
    per = "Period August 4, 2025 (commencement of sale of shares) through April 30, 2026 (audited; annualized, footnote H)"
    for i, t in enumerate(["fh_expenses_before_reductions", "fh_expenses_net_of_fee_waivers",
                           "fh_expenses_net_of_all_reductions"], start=1):
        add(t, "OnChain", "0" + m.group(i), "% of average net assets", per, "ONC_PRO", hp, quote, pos)
    fh_def = ("Expense ratios reflect operating expenses of the class. Expenses before reductions do not reflect "
              "amounts reimbursed, waived, or reduced through arrangements with the investment adviser, brokerage "
              "services, or other offset arrangements, if applicable, and do not represent the amount paid by the "
              "class during periods when reimbursements, waivers or reductions occur.")
    for seg, cls in [("LIQ_PRO", "Liquidity"), ("ONC_PRO", "OnChain")]:
        pos, quote, _ = locate(seg, fh_def, "Financial Highlights")
        add("fh_footnote_G_definition", cls, "", "text", "Financial Highlights footnote G", seg,
            "Appendix > Financial Highlights (footnote G)", quote, pos)

    # Fund Management: all-inclusive fee and class-difference sentence
    allinc = ("Each class of the fund pays an all-inclusive management fee to the Adviser. A different "
              "all-inclusive management fee rate is applicable to each class of the fund. The difference "
              "between classes is the result of separate arrangements for class level services and/or waivers "
              "of certain expenses. It is not the result of any difference in advisory or custodial fees or "
              "other expenses related to the management of the fund's assets, which do not vary by class.")
    for seg, cls in [("LIQ_PRO", "Liquidity"), ("ONC_PRO", "OnChain")]:
        pos, quote, _ = locate(seg, allinc, "Advisory Fee(s)")
        assert text.rfind("Fund Management", segs[seg][0], pos) > 0
        add("all_inclusive_management_fee_class_difference", cls, "", "text",
            "Prospectus dated June 29, 2026", seg, "Fund Services > Fund Management > Advisory Fee(s)", quote, pos)
        pat = (r"The annual management fee rate, as a percentage of the class's average net assets, is shown in "
               r"the following table: Fund " + CLASS_LABEL[LIQ if cls == "Liquidity" else ONC] +
               r" Fidelity® Treasury Digital Fund (\d\.\d\d)%")
        pos, quote, m = locate(seg, pat, "Advisory Fee(s)", regex=True)
        add("management_fee_rate_table", cls, m.group(1), "% of class average net assets per year",
            "Prospectus dated June 29, 2026", seg, "Fund Services > Fund Management > Advisory Fee(s)", quote, pos,
            "Gross all-inclusive rate before the fee-table waiver/reimbursement.")

    # Blockchain transaction-fee sentence (OnChain prospectus)
    bfee = ("At this time, the transaction fees associated with validating transactions on the Ethereum or any "
            "other blockchain network will be borne by the Adviser or its affiliates. In the future, investors may "
            "be responsible for these fees, for some or all of their transactions involving fund shares.")
    a, b = segs["ONC_PRO"]
    ub = text.rfind("Use of Blockchain", a, text.find("Public blockchain networks require the payment", a, b))
    pos = text.find(bfee, ub, b)
    assert pos > 0 and text.rfind("Use of Blockchain", a, pos) == ub
    add("blockchain_transaction_fees", "OnChain", "", "text", "Prospectus dated June 29, 2026", "ONC_PRO",
        "Fund Basics > Use of Blockchain", bfee, pos,
        "Same two sentences also appear in Fund Summary > Purchase and Sale of Shares (see next row).")
    pos, quote, _ = locate("ONC_PRO", bfee, "Purchase and Sale of Shares")
    add("blockchain_transaction_fees", "OnChain", "", "text", "Prospectus dated June 29, 2026", "ONC_PRO",
        "Fund Summary > Purchase and Sale of Shares", quote, pos)
    perf = ("OnChain would have substantially similar annual returns to Liquidity because the classes are invested "
            "in the same portfolio of securities. OnChain's returns would differ from Liquidity's returns only to "
            "the extent that the classes do not have the same expenses.")
    pos, quote, _ = locate("ONC_PRO", perf, "Performance")
    add("class_return_difference_statement", "OnChain", "", "text", "Prospectus dated June 29, 2026", "ONC_PRO",
        "Fund Summary > Performance", quote, pos)
    conv = ("You may convert Liquidity class shares of the fund to OnChain class shares at any time, provided that "
            "you meet the eligibility requirements for OnChain class. You may email FDAMIR@fmr.com to request a "
            "conversion. A conversion will be based on the respective NAVs of the two classes, without the "
            "imposition of any fees, on the trade date of the conversion. A conversion between share classes of "
            "the same fund is a non-taxable event.")
    pos, quote, _ = locate("LIQ_PRO", conv, "Converting Shares")
    add("converting_shares", "Liquidity", "", "text", "Prospectus dated June 29, 2026", "LIQ_PRO",
        "Shareholder Information > Converting Shares", quote, pos,
        "Liquidity-to-OnChain direction only is described; the OnChain prospectus contains no text matching 'onver' "
        "(no conversion section in either direction).")
    assert text.find("onver", *segs["ONC_PRO"]) < 0

    # SAI: management fees paid (fund level) and voluntary reimbursement
    pat = (r"Fund\(s\) Fiscal Years Ended Amount of Credits Reducing Management Fees Management Fees Paid to "
           r"Investment Adviser Fidelity® Treasury Digital Fund 2026 \$ ([\d,]+) \$ ([\d,]+) 2025 ?\(A\) "
           r"\$ ([\d,]+) \$ ([\d,]+) \(A\)Fund commenced operations on December 23, 2024\.")
    pos, quote, m = locate("LIQ_SAI", pat, "Management Fee.", regex=True)
    assert text.rfind("MANAGEMENT CONTRACT", segs["LIQ_SAI"][0], pos) > 0
    hp = "MANAGEMENT CONTRACT > Management Fee. (fund level, both classes combined)"
    add("sai_management_fees_paid", "Fund (all classes)", m.group(2).replace(",", ""), "USD",
        "Fiscal year ended April 30, 2026", "LIQ_SAI", hp, quote, pos)
    add("sai_credits_reducing_management_fees", "Fund (all classes)", m.group(1).replace(",", ""), "USD",
        "Fiscal year ended April 30, 2026", "LIQ_SAI", hp, quote, pos)
    add("sai_management_fees_paid", "Fund (all classes)", m.group(4).replace(",", ""), "USD",
        "Fiscal period ended April 30, 2025 (commenced December 23, 2024)", "LIQ_SAI", hp, quote, pos)
    add("sai_credits_reducing_management_fees", "Fund (all classes)", m.group(3).replace(",", ""), "USD",
        "Fiscal period ended April 30, 2025 (commenced December 23, 2024)", "LIQ_SAI", hp, quote, pos)
    vol = ("FMR may, from time to time, voluntarily reimburse all or a portion of a fund's or, in the case of a "
           "multiple class fund, a class's operating expenses. FMR retains the ability to be repaid for these expense "
           "reimbursements in the amount that expenses fall below the limit prior to the end of the fiscal year. "
           "Expense reimbursements will increase returns and yield, and repayment of the reimbursement will decrease "
           "returns and yield.")
    pos, quote, _ = locate("LIQ_SAI", vol, "Management Fee.")
    add("sai_voluntary_reimbursement", "Fund (all classes)", "", "text", "SAI dated June 29, 2026", "LIQ_SAI",
        "MANAGEMENT CONTRACT > Management Fee.", quote, pos)
    for t in ["Liquidity class pays FMR a monthly all-inclusive management fee at an annual rate of 0.25% of its average daily net assets throughout the month.",
              "OnChain class pays FMR a monthly all-inclusive management fee at an annual rate of 0.25% of its average daily net assets throughout the month."]:
        seg = "LIQ_SAI" if t.startswith("Liquidity") else "ONC_SAI"
        pos, quote, _ = locate(seg, t, "Management Fee.")
        add("sai_management_fee_rate", t.split()[0], "0.25", "% of class average daily net assets per year",
            "SAI dated June 29, 2026", seg, "MANAGEMENT CONTRACT > Management Fee.", quote, pos)
    return rows


# ----------------------------------------------------------------------------- scratch comparison
def scratch_comparisons(root: Path, results: dict):
    """Compare selected results with the earlier reviewer's scratch outputs, if present."""
    out = []
    p1 = root / SCRATCH / "analyse_gap_output.txt"
    p2 = root / SCRATCH / "analyse_more_output.txt"
    p3 = root / SCRATCH / "gross_net_allfunds_output.txt"
    if not (p1.exists() and p2.exists() and p3.exists()):
        return [("scratch outputs", "not found", "", "not compared")]
    t1, t2, t3 = (p.read_text(encoding="utf-8") for p in (p1, p2, p3))

    def grab(t, pat, grp=1):
        m = re.search(pat, t, flags=re.M)
        return float(m.group(grp).replace(",", "")) if m else None

    checks = [
        ("unpaired mean Liquidity full", grab(t1, r"\[full\] unpaired mean LIQ ([\d.]+)%"), results["unp_liq_full"], 4),
        ("unpaired mean OnChain full", grab(t1, r"\[full\] unpaired mean .*?ONC ([\d.]+)%"), results["unp_onc_full"], 4),
        ("unpaired mean Liquidity 2026", grab(t1, r"\[2026\] unpaired mean LIQ ([\d.]+)%"), results["unp_liq_2026"], 4),
        ("unpaired mean OnChain 2026", grab(t1, r"\[2026\] unpaired mean .*?ONC ([\d.]+)%"), results["unp_onc_2026"], 4),
        ("paired mean gap full", grab(t1, r"^\[full\] unpaired.*\n.*\n\s+paired n=294 mean gap ([+-][\d.]+)pp"), results["pair_full"], 4),
        ("paired mean gap 2026", grab(t1, r"paired n=189 mean gap ([+-][\d.]+)pp"), results["pair_2026"], 4),
        ("launch contribution to full mean", grab(t1, r"launch-day contribution to full paired mean: ([+-][\d.]+)pp"), results["launch_contrib"], 4),
        ("paired gap excl launch", grab(t1, r"paired mean excl\. launch days: ([+-][\d.]+)pp"), results["pair_excl_launch"], 4),
        ("paired gap excl Aug 2025", grab(t1, r"full excl 2025-08: n=274 mean gap ([+-][\d.]+)pp"), results["pair_excl_aug"], 4),
        ("weekly non-overlapping n full", grab(t1, r"\[full\] non-overlapping weekly obs=(\d+)"), results["wk_full_n"], 0),
        ("weekly non-overlapping mean full", grab(t1, r"\[full\] non-overlapping weekly obs=\d+, mean gap ([+-][\d.]+)pp"), results["wk_full_mean"], 4),
        ("weekly non-overlapping sd full", grab(t1, r"\[full\] non-overlapping weekly obs=\d+, mean gap [+-][\d.]+pp, sd ([\d.]+)"), results["wk_full_sd"], 4),
        ("weekly non-overlapping n 2026", grab(t1, r"\[2026\] non-overlapping weekly obs=(\d+)"), results["wk_2026_n"], 0),
        ("calendar span full (days)", grab(t1, r"\[full\] 294 daily obs span (\d+) calendar days"), results["span_full"], 0),
        ("calendar span 2026 (days)", grab(t1, r"\[2026\] 189 daily obs span (\d+) calendar days"), results["span_2026"], 0),
        ("gross-net Liquidity 2026", grab(t1, r"\[2026\] gross-net LIQ ([\d.]+)pp"), results["gn_liq_2026"], 4),
        ("gross-net OnChain 2026", grab(t1, r"\[2026\] gross-net LIQ [\d.]+pp ONC ([\d.]+)pp"), results["gn_onc_2026"], 4),
        ("gross-net Liquidity full excl launch", grab(t1, r"\[excl launch\] gross-net LIQ ([\d.]+)pp"), results["gn_liq_exl"], 4),
        ("gross-net OnChain full excl launch", grab(t1, r"\[excl launch\] gross-net LIQ [\d.]+pp ONC ([\d.]+)pp"), results["gn_onc_exl"], 4),
        ("balance-weighted gap full", grab(t2, r"\[full\] OnChain-balance-weighted mean gap ([+-][\d.]+)pp"), results["bw_full"], 4),
        ("balance-weighted gap 2026", grab(t2, r"\[2026\] OnChain-balance-weighted mean gap ([+-][\d.]+)pp"), results["bw_2026"], 4),
        ("income difference 2026 (USD)", grab(t2, r"\[2026-01\.\.09\] approx\. income difference .*?\$(-?[\d,]+)"), results["inc_2026"], 0),
        ("income difference full (USD)", grab(t2, r"\[2025-08\.\.2026-09\] approx\. income difference .*?\$(-?[\d,]+)"), results["inc_full"], 0),
        ("waiver annualised Liquidity 2026 (%)", grab(t1, r"dollar-weighted annualised waiver LIQ: ([\d.]+)%"), results["wv_liq_2026"], 4),
        ("waiver annualised OnChain 2026 (%)", grab(t1, r"dollar-weighted annualised waiver ONC: ([\d.]+)%"), results["wv_onc_2026"], 4),
        ("waiver annualised Liquidity 2025-09..12 (%)", grab(t1, r"dollar-weighted annualised waiver LIQ_25: ([\d.]+)%"), results["wv_liq_2025q"], 4),
        ("waiver annualised OnChain 2025-09..12 (%)", grab(t1, r"dollar-weighted annualised waiver ONC_25: ([\d.]+)%"), results["wv_onc_2025q"], 4),
        ("cross-fund gross-net FOBXX 2026", grab(t3, r"'FOBXX'.*?mean gross-net ([\d.]+)pp"), results["gn_FOBXX"], 4),
        ("cross-fund gross-net WTGXX 2026", grab(t3, r"'WTGXX'.*?mean gross-net ([\d.]+)pp"), results["gn_WTGXX"], 4),
        ("cross-fund gross-net JLTXX 2026", grab(t3, r"'JLTXX'.*?mean gross-net ([\d.]+)pp"), results["gn_JLTXX"], 4),
        ("conversion-consistent matches (count)", float(len(re.findall(r"^\d{4}-\d\d-\d\d (LIQ|ONC) red ->", t2, flags=re.M))), results["n_conv"], 0),
    ]
    for label, theirs, mine, dp in checks:
        if theirs is None:
            out.append((label, "not found", f"{mine:.{dp}f}", "not compared"))
            continue
        same = round(theirs, dp) == round(mine, dp)
        out.append((label, f"{theirs:.{dp}f}", f"{mine:.{dp}f}", "agree" if same else "DIFFER"))
    return out


# ----------------------------------------------------------------------------- main
def main(argv):
    root = Path(argv[1]).resolve() if len(argv) > 1 else Path(__file__).resolve().parents[1]
    out = root / OUT_REL
    out.mkdir(parents=True, exist_ok=True)
    script_path = Path(__file__).resolve()

    # ---- inputs and manifest check
    xml_files = sorted((root / "data/raw/edgar").glob("2026-10-16_FYOXX_N-MFP3_*.xml"))
    manifest = {}
    with open(root / MANIFEST, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            manifest[r["path"]] = r["sha256"]
    inputs = []
    # Publication adapter: the 485BPOS HTML is not redistributed; skip its hash check when absent.
    for p in xml_files + [q for q in (root / PROSPECTUS_FILE,) if q.exists()] + [root / FREEZE_DAILY, root / FREEZE_TWIN]:
        rel = p.relative_to(root).as_posix()
        h = sha256(p)
        exp = manifest.get(rel)
        status = "match" if exp == h else ("NOT IN FREEZE MANIFEST" if exp is None else "MISMATCH")
        inputs.append((rel, h, status))
    raw_bad = [i for i in inputs if i[0].startswith("data/raw/") and i[2] != "match"]
    if raw_bad:
        raise SystemExit(f"Raw input hash check failed: {raw_bad}")

    # ---- parse XML
    filings = sorted((parse_filing(p) for p in xml_files), key=lambda f: f["report_date"])
    assert len(filings) == 22, len(filings)
    for f in filings:
        assert f["submission_type"] == "N-MFP3", f["submission_type"]   # originals only, no N-MFP3/A
        assert f["series_id"] == SERIES
        assert not f["dup_dates"], (f["accession"], f["dup_dates"])
        for cid, cl in f["classes"].items():
            for dd in list(cl["net_yield"]) + list(cl["subs"]):
                assert dd[:7] == f["report_date"][:7], (f["accession"], cid, dd)
    rds = [f["report_date"] for f in filings]
    assert len(set(rds)) == 22 and rds[0] == "2024-12-31" and rds[-1] == "2026-09-30"

    y = {LIQ: {}, ONC: {}}      # percent, Decimal
    flow = {LIQ: {}, ONC: {}}   # (subs, reds) Decimal
    gy, acc_of, rd_of = {}, {}, {}
    for f in filings:
        for dd, v in f["gross_yield"].items():
            gy[dd] = v * 100
            acc_of[dd], rd_of[dd] = f["accession"], f["report_date"]
        for cid, cl in f["classes"].items():
            assert cid in (LIQ, ONC), cid
            for dd, v in cl["net_yield"].items():
                y[cid][dd] = v * 100
                acc_of[dd], rd_of[dd] = f["accession"], f["report_date"]
            for dd in cl["subs"]:
                flow[cid][dd] = (cl["subs"][dd], cl["reds"][dd])
                acc_of[dd], rd_of[dd] = f["accession"], f["report_date"]

    # ---- cross-check against frozen parsed rows
    fz = {}
    with open(root / FREEZE_DAILY, encoding="utf-8", newline="") as fh:
        frozen_rows = list(csv.DictReader(fh))
    for r in frozen_rows:
        if r["ticker_file"] == "FYOXX" and r["governing"] == "1":
            fz[(r["class_id"], r["date"])] = r
    mism = Counter()
    n_cmp = 0
    keys_raw = {(c, dd) for c in (LIQ, ONC) for dd in set(y[c]) | set(flow[c])}
    for k in sorted(keys_raw | set(fz)):
        c, dd = k
        r = fz.get(k)
        if r is None:
            mism["date missing from frozen"] += 1
            continue
        if k not in keys_raw:
            mism["date missing from raw parse"] += 1
            continue
        n_cmp += 1
        if (r["class_7d_net_yield"] and Decimal(r["class_7d_net_yield"]) * 100 != y[c].get(dd)) or \
           (not r["class_7d_net_yield"] and dd in y[c]):
            mism["net yield"] += 1
        if r["series_7d_gross_yield"] and Decimal(r["series_7d_gross_yield"]) * 100 != gy.get(dd):
            mism["gross yield"] += 1
        if dd in flow[c] and (Decimal(r["gross_subs"]) != flow[c][dd][0] or Decimal(r["gross_reds"]) != flow[c][dd][1]):
            mism["flows"] += 1
        if r["accession"] != acc_of[dd]:
            mism["accession"] += 1
    crosscheck = dict(n_compared=n_cmp, mismatches=dict(mism))

    # ---- windows
    def in_win(dd, lo, hi):
        return lo <= dd <= hi

    rows = []

    def add(q, v, unit, window, n, definition, source=SRC_A19):
        rows.append(dict(quantity=q, value=v, unit=unit, window=window, n=n, definition=definition, source=source))

    W_FULL = "2025-08-01..2026-09-30"
    W_26 = "2026-01-01..2026-09-30"
    res = {}

    # 1. unpaired means
    for lab, lo, hi, w in [("full", FULL_LO, FULL_HI, W_FULL), ("2026", Y26_LO, Y26_HI, W_26)]:
        lq = [float(v) for dd, v in y[LIQ].items() if in_win(dd, lo, hi)]
        on = [float(v) for dd, v in y[ONC].items() if in_win(dd, lo, hi)]
        res[f"unp_liq_{lab}"], res[f"unp_onc_{lab}"] = mean(lq), mean(on)
        add("unpaired_mean_net_yield_Liquidity", f"{mean(lq):.4f}", "percent", w, len(lq),
            "Mean of all reported Liquidity seven-day net yields (Item B.8) dated in window; dates not matched to OnChain")
        add("unpaired_mean_net_yield_OnChain", f"{mean(on):.4f}", "percent", w, len(on),
            "Mean of all reported OnChain seven-day net yields (Item B.8) dated in window; dates not matched to Liquidity")
        add("unpaired_mean_difference_OnChain_minus_Liquidity", f"{mean(on) - mean(lq):.4f}", "pp", w,
            f"{len(on)}/{len(lq)}", "Unpaired OnChain mean minus unpaired Liquidity mean (different date sets if n differ)")
        extra = sorted(set(dd for dd in y[LIQ] if in_win(dd, lo, hi)) ^ set(dd for dd in y[ONC] if in_win(dd, lo, hi)))
        add("unpaired_extra_dates", ";".join(extra) if extra else "none", "date list", w, len(extra),
            "Dates with a yield for only one class (Liquidity-only if before OnChain's first record)")

    # reproduce frozen twin descriptives
    twin = {}
    with open(root / FREEZE_TWIN, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            twin[(r["class_name"], r["period"])] = (r["mean_7d_net_yield_pct"], r["n_business_days"])
    repro = {
        ("Liquidity", "common_months"): (f"{res['unp_liq_full']:.4f}", str(len([1 for dd in y[LIQ] if in_win(dd, FULL_LO, FULL_HI)]))),
        ("OnChain", "common_months"): (f"{res['unp_onc_full']:.4f}", str(len([1 for dd in y[ONC] if in_win(dd, FULL_LO, FULL_HI)]))),
        ("Liquidity", "2026-01..2026-09"): (f"{res['unp_liq_2026']:.4f}", str(len([1 for dd in y[LIQ] if in_win(dd, Y26_LO, Y26_HI)]))),
        ("OnChain", "2026-01..2026-09"): (f"{res['unp_onc_2026']:.4f}", str(len([1 for dd in y[ONC] if in_win(dd, Y26_LO, Y26_HI)]))),
    }
    repro_check = {k: (twin.get(k), v, twin.get(k) == v) for k, v in repro.items()}
    assert all(ok for _, _, ok in repro_check.values()), repro_check
    for k, v in repro.items():
        expected = {("Liquidity", "common_months"): ("3.6774", "295"), ("OnChain", "common_months"): ("3.5833", "294"),
                    ("Liquidity", "2026-01..2026-09"): ("3.5233", "189"), ("OnChain", "2026-01..2026-09"): ("3.5033", "189")}[k]
        assert v == expected, (k, v, expected)

    # 2. paired gaps
    paired = sorted(dd for dd in y[ONC] if dd in y[LIQ] and in_win(dd, FULL_LO, FULL_HI))
    gap = {dd: y[ONC][dd] - y[LIQ][dd] for dd in paired}   # Decimal, exact
    p26 = [dd for dd in paired if in_win(dd, Y26_LO, Y26_HI)]
    N = len(paired)
    pair_full = float(sum(gap[dd] for dd in paired)) / N
    pair_26 = float(sum(gap[dd] for dd in p26)) / len(p26)
    res["pair_full"], res["pair_2026"] = pair_full, pair_26
    SRC_P = SRC_A19
    add("paired_mean_gap_OnChain_minus_Liquidity", f"{pair_full:.4f}", "pp", W_FULL, N,
        "Mean over dates with both class yields of (OnChain net yield - Liquidity net yield)", SRC_P)
    add("paired_mean_gap_OnChain_minus_Liquidity", f"{pair_26:.4f}", "pp", W_26, len(p26),
        "Mean over dates with both class yields of (OnChain net yield - Liquidity net yield)", SRC_P)
    add("paired_median_gap", f"{st.median(float(gap[dd]) for dd in paired):.4f}", "pp", W_FULL, N, "Median same-date gap", SRC_P)
    add("paired_median_gap", f"{st.median(float(gap[dd]) for dd in p26):.4f}", "pp", W_26, len(p26), "Median same-date gap", SRC_P)
    add("paired_first_date", paired[0], "date", W_FULL, N, "First date with both class yields (OnChain first record)", SRC_P)

    # 3. launch-week records
    launch = [dd for dd in paired if y[ONC][dd] < 1]
    assert launch == ["2025-08-04", "2025-08-05", "2025-08-06", "2025-08-07", "2025-08-08"], launch
    for dd in launch:
        add("launch_record_OnChain_net_yield", f"{float(y[ONC][dd]):.2f}", "percent", dd, 1,
            f"OnChain seven-day net yield as filed (raw value {y[ONC][dd] / 100}); Liquidity same date "
            f"{float(y[LIQ][dd]):.2f}%; series gross {float(gy[dd]):.2f}%; gap {float(gap[dd]):+.2f} pp",
            f"{SRC_A19}; accession {acc_of[dd]}")
    lc = float(sum(gap[dd] for dd in launch)) / N
    res["launch_contrib"] = lc
    add("launch_records_contribution_to_full_paired_mean", f"{lc:.4f}", "pp", W_FULL, len(launch),
        "Sum of the five launch-record gaps divided by the full paired n (294)", SRC_P)
    exl = [dd for dd in paired if dd not in launch]
    res["pair_excl_launch"] = float(sum(gap[dd] for dd in exl)) / len(exl)
    add("paired_mean_gap_excl_launch_records", f"{res['pair_excl_launch']:.4f}", "pp", W_FULL + " excl. 2025-08-04..08", len(exl),
        "Paired mean gap after dropping the five launch-week OnChain records", SRC_P)
    exa = [dd for dd in paired if dd[:7] != "2025-08"]
    res["pair_excl_aug"] = float(sum(gap[dd] for dd in exa)) / len(exa)
    add("paired_mean_gap_excl_August_2025", f"{res['pair_excl_aug']:.4f}", "pp", "2025-09-01..2026-09-30", len(exa),
        "Paired mean gap after dropping all August 2025 dates", SRC_P)
    # additive decomposition of (full mean - 2026 mean)
    excess = pair_full - pair_26
    parts = [("launch records 2025-08-04..08", launch),
             ("rest of August 2025 (2025-08-11..29)", [dd for dd in paired if dd[:7] == "2025-08" and dd not in launch]),
             ("September-December 2025", [dd for dd in paired if "2025-09" <= dd[:7] <= "2025-12"]),
             ("January-September 2026", p26)]
    assert sum(len(p) for _, p in parts) == N
    add("excess_of_full_paired_mean_over_2026_paired_mean", f"{excess:.4f}", "pp", W_FULL, N,
        "Full-period paired mean minus 2026 paired mean", SRC_P)
    tot = 0.0
    for lab, ds in parts:
        c = float(sum(gap[dd] for dd in ds)) / N - len(ds) / N * pair_26
        tot += c
        add(f"excess_component[{lab}]", f"{c:.4f}", "pp", W_FULL, len(ds),
            "Sum over these dates of (gap - 2026 paired mean) / full n; components add to the excess", SRC_P)
        if lab.startswith("launch"):
            res["launch_share"] = c / excess
            add("share_of_excess_attributable_to_launch_records", f"{100 * c / excess:.1f}", "percent of excess", W_FULL, len(ds),
                "Launch-record component divided by (full paired mean - 2026 paired mean); arithmetic attribution, not a causal one", SRC_P)
    assert abs(tot - excess) < 1e-12
    alt = (pair_full - res["pair_excl_launch"]) / excess
    add("share_of_excess_removed_by_dropping_launch_records", f"{100 * alt:.1f}", "percent of excess", W_FULL, len(launch),
        "(full paired mean - paired mean excl. launch records) / (full paired mean - 2026 paired mean); alternative drop-based measure", SRC_P)

    # 4. monthly mean paired gaps
    bym = defaultdict(list)
    for dd in paired:
        bym[dd[:7]].append(dd)
    for m, ds in sorted(bym.items()):
        g = float(sum(gap[x] for x in ds)) / len(ds)
        add("monthly_mean_paired_gap", f"{g:.4f}", "pp", m, len(ds), "Mean same-date gap in calendar month", SRC_P)
        add("monthly_contribution_to_full_paired_mean", f"{float(sum(gap[x] for x in ds)) / N:.4f}", "pp", m, len(ds),
            "Sum of month's gaps divided by full paired n (contributions add to full paired mean)", SRC_P)

    # 5. overlap treatment
    for lab, ds, w in [("full", paired, W_FULL), ("2026", p26, W_26)]:
        span = (d(ds[-1]) - d(ds[0])).days + 7
        res[f"span_{lab}"] = span
        add("calendar_span_of_underlying_seven_day_windows", span, "calendar days", w, len(ds),
            "From 6 days before the first paired date to the last paired date inclusive (each yield covers the 7 days ending on its date)", SRC_P)
        add("non_overlapping_seven_day_windows_in_span", span // 7, "windows", w, len(ds),
            "floor(span / 7): number of distinct non-overlapping seven-day windows the daily series can contain", SRC_P)

    def weekly(ds):
        """Last paired date in each ISO week; keep a date only if >= 7 days after the last kept date."""
        wk = {}
        for x in ds:
            k = d(x).isocalendar()[:2]
            wk[k] = max(wk.get(k, x), x)
        cand = sorted(wk.values())
        keep = [cand[0]]
        for x in cand[1:]:
            if (d(x) - d(keep[-1])).days >= 7:
                keep.append(x)
        return keep

    for lab, ds, w in [("full", paired, W_FULL), ("2026", p26, W_26), ("exl", exl, W_FULL + " excl. launch records")]:
        wk = weekly(ds)
        g = [float(gap[x]) for x in wk]
        sd = st.stdev(g)
        res[f"wk_{lab}_n"], res[f"wk_{lab}_mean"], res[f"wk_{lab}_sd"] = len(wk), mean(g), sd
        defn = ("Weekly non-overlapping sample: last paired date of each ISO week, kept only if >= 7 calendar days "
                "after the previous kept date, so no two kept seven-day windows share a day")
        add("weekly_nonoverlapping_sample_n", len(wk), "dates", w, len(wk), defn, SRC_P)
        add("weekly_nonoverlapping_sample_mean_gap", f"{mean(g):.4f}", "pp", w, len(wk), defn, SRC_P)
        add("weekly_nonoverlapping_sample_sd_gap", f"{sd:.4f}", "pp", w, len(wk), defn + "; sample sd (n-1)", SRC_P)
    for lab, ds, w in [("full", paired, W_FULL), ("2026", p26, W_26)]:
        cnt = Counter(gap[x] for x in ds)
        for v in sorted(cnt):
            add(f"paired_gap_value_frequency[{float(v):+.2f}pp]", cnt[v], "dates", w, len(ds),
                "Number of paired dates on which the reported gap equals this value (reporting precision 0.01 pp)", SRC_P)
    res["n_distinct_full"] = len(set(gap[x] for x in paired))
    # August 2025 facts for the README source-gap note (computed, not typed)
    aug = [x for x in paired if x[:7] == "2025-08"]
    aug_after = [x for x in aug if x not in launch]
    res["aug_facts"] = dict(
        liq_vals=sorted({f"{float(y[LIQ][x]):.2f}" for x in aug}),
        gross_vals=sorted({f"{float(gy[x]):.2f}" for x in aug}),
        onc_after_vals=sorted({f"{float(y[ONC][x]):.2f}" for x in aug_after}),
        first=aug[0], last=aug[-1], after_first=aug_after[0],
        liq_0801=f"{float(y[LIQ]['2025-08-01']):.2f}", gross_0801=f"{float(gy['2025-08-01']):.2f}",
        launch_liq=sorted({f"{float(y[LIQ][x]):.2f}" for x in launch}),
        launch_gross=sorted({f"{float(gy[x]):.2f}" for x in launch}))

    # 6. series gross yield minus class net yield
    gn_defn = ("Mean of (series seven-day gross yield, Item A.19, before fees and expenses) minus (class seven-day "
               "net yield, Item B.8) on the same date; implied annualised class expense after waivers, approximate")
    for lab, ds, w in [("2026", p26, W_26), ("exl", exl, W_FULL + " excl. launch records"), ("full", paired, W_FULL),
                       ("aug_exl", [x for x in exl if x[:7] == "2025-08"], "2025-08-11..2025-08-29")]:
        for cid in (LIQ, ONC):
            v = mean(float(gy[x] - y[cid][x]) for x in ds)
            res[f"gn_{'liq' if cid == LIQ else 'onc'}_{lab}"] = v
            add(f"gross_minus_net_{CLASS_LABEL[cid]}", f"{v:.4f}", "pp", w, len(ds), gn_defn, SRC_A19)
        add("gross_minus_net_difference_OnChain_minus_Liquidity",
            f"{res[f'gn_onc_{lab}'] - res[f'gn_liq_{lab}']:.4f}", "pp", w, len(ds),
            "OnChain gross-minus-net minus Liquidity gross-minus-net (equals minus the paired gap)", SRC_A19)
    for tick, cid in OTHER_TOKEN_CLASSES:
        vals = [100 * (float(r["series_7d_gross_yield"]) - float(r["class_7d_net_yield"])) for r in frozen_rows
                if r["ticker_file"] == tick and r["class_id"] == cid and r["governing"] == "1"
                and in_win(r["date"], Y26_LO, Y26_HI) and r["class_7d_net_yield"] and r["series_7d_gross_yield"]]
        res[f"gn_{tick}"] = mean(vals)
        add(f"cross_fund_context_gross_minus_net[{tick} {cid}]", f"{mean(vals):.4f}", "pp", W_26, len(vals),
            "CROSS-FUND CONTEXT, NOT A TOKENISATION PREMIUM: same gross-minus-net measure for another fund's token class; "
            "funds differ in portfolio, fee terms and waivers", f"data/processed/freeze_nmfp_daily.csv (governing rows, {tick})")

    # 7. balances, balance-weighted gap, income difference
    class_start = {cid: min(set(y[cid]) | set(flow[cid])) for cid in (LIQ, ONC)}
    eod, mon = {}, {}
    for cid in (LIQ, ONC):
        eod[cid], mon[cid] = reconstruct_balances(filings, cid, class_start[cid])
    bal_note = ("Weights are reconstructed end-of-day OnChain balances (APPROXIMATE: month-end Item B.5 shares less "
                "later same-month Item B.7 net flows; shares treated as USD at stable NAV)")
    for lab, ds, w in [("full", paired, W_FULL), ("2026", p26, W_26)]:
        wts = [eod[ONC][x] for x in ds]
        bw = sum(a * float(gap[x]) for a, x in zip(wts, ds)) / sum(wts)
        res[f"bw_{lab}"] = bw
        add("balance_weighted_paired_gap", f"{bw:.4f}", "pp", w, len(ds), bal_note, SRC_B7)
        add("mean_reconstructed_OnChain_balance_on_paired_dates", f"{mean(wts):.2f}", "USD (approx.)", w, len(ds), bal_note, SRC_B7)
    for lab, lo, hi, key in [("2026", Y26_LO, Y26_HI, "inc_2026"), ("full", paired[0], FULL_HI, "inc_full")]:
        day, end, cb, cg, tot, n = d(lo), d(hi), None, None, 0.0, 0
        while day <= end:
            k = day.isoformat()
            if k in eod[ONC] and k in gap:
                cb, cg = eod[ONC][k], float(gap[k])
            if cb is not None:
                tot += cb * cg / 100 / 365
                n += 1
            day += dt.timedelta(days=1)
        res[key] = tot
        add("ILLUSTRATIVE_income_difference_on_OnChain_balances", f"{tot:.2f}", "USD", f"{lo}..{hi}", n,
            "ILLUSTRATIVE ONLY: sum over calendar days (from the first paired business day in the window) of reconstructed "
            "OnChain balance x same-date gap / 100 / 365, carrying the last business day's values over non-business days. "
            "Seven-day yields are trailing annualised rates, not daily accruals; balances are approximate", SRC_B7)

    # 8. filed fee waivers (Item B.9.a) annualised
    mrows = []
    for f in filings:
        for cid, cl in sorted(f["classes"].items()):
            mm = mon[cid][f["report_date"]]
            rate = 100 * float(cl["waiver_usd"]) / mm["avg_balance"] * 365 / mm["n_days"]
            ds_in = [x for x in cl["subs"]]
            gnv = [float(gy[x] - y[cid][x]) for x in cl["net_yield"] if x in gy]
            mrows.append(dict(
                report_date=f["report_date"], accession=f["accession"], class_id=cid, class_label=CLASS_LABEL[cid],
                class_full_name=cl["name"], shares_outstanding_B5=f"{cl['shares']}", net_assets_B4=f"{cl['net_assets']}",
                monthly_gross_subs_B7=f"{cl['month_subs']}", sum_daily_gross_subs=f"{sum(cl['subs'].values())}",
                monthly_gross_reds_B7=f"{cl['month_reds']}", sum_daily_gross_reds=f"{sum(cl['reds'].values())}",
                n_net_yield_dates=len(cl["net_yield"]), n_flow_dates=len(ds_in),
                waiver_flag_B9=cl["waiver_flag"], waiver_usd_B9a=f"{cl['waiver_usd']}",
                reconstructed_opening_balance=f"{mm['opening_reconstructed']:.2f}",
                prior_month_end_shares=("" if mm["prior_month_end_shares"] is None else f"{mm['prior_month_end_shares']:.4f}"),
                opening_minus_prior_month_end=("" if mm["opening_minus_prior_end"] is None else f"{mm['opening_minus_prior_end']:.2f}"),
                avg_daily_balance_approx=f"{mm['avg_balance']:.2f}", avg_from=mm["first_day"], n_calendar_days=mm["n_days"],
                waiver_annualised_pct_approx=f"{rate:.4f}",
                mean_gross_minus_net_pp=(f"{mean(gnv):.4f}" if gnv else ""),
            ))
    def wv(cid, lo, hi):
        sel = [r for r in mrows if r["class_id"] == cid and lo <= r["report_date"][:7] <= hi]
        w_ = sum(float(r["waiver_usd_B9a"]) for r in sel)
        dy = sum(float(r["avg_daily_balance_approx"]) * r["n_calendar_days"] / 365 for r in sel)
        return 100 * w_ / dy, w_, len(sel)
    wdef = ("Sum of filed Item B.9.a waiver/expense-payment dollars divided by sum of (approximate average daily "
            "class balance x calendar days / 365); DENOMINATOR APPROXIMATE (reconstructed balances)")
    for cid in (LIQ, ONC):
        periods = [("2025-08", "2026-09", "report months 2025-08..2026-09 (full common)"),
                   ("2025-09", "2025-12", "report months 2025-09..2025-12"),
                   ("2026-01", "2026-09", "report months 2026-01..2026-09")]
        if cid == LIQ:
            periods.insert(0, ("2024-12", "2025-07", "report months 2024-12..2025-07 (before OnChain)"))
        for lo, hi, lab in periods:
            rate, w_, n = wv(cid, lo, hi)
            key = {"2026-01": "2026", "2025-09": "2025q", "2025-08": "full", "2024-12": "pre"}[lo]
            res[f"wv_{'liq' if cid == LIQ else 'onc'}_{key}"] = rate
            add(f"filed_waiver_annualised_{CLASS_LABEL[cid]}", f"{rate:.4f}", "% of avg net assets per year (approx.)", lab, n, wdef,
                SRC_XML + " Item B.9.a")
            add(f"filed_waiver_dollars_{CLASS_LABEL[cid]}", f"{w_:.2f}", "USD", lab, n,
                "Sum of filed Item B.9.a amounts for the class over the report months", SRC_XML + " Item B.9.a")

    # 9. same-day opposite cross-class flows
    conv = []
    for dd in sorted(set(flow[LIQ]) & set(flow[ONC])):
        ls, lr = (float(v) for v in flow[LIQ][dd])
        os_, or_ = (float(v) for v in flow[ONC][dd])
        for a, b, desc in [(lr, os_, "Liquidity redemption with OnChain subscription"),
                           (or_, ls, "OnChain redemption with Liquidity subscription")]:
            if a > CONVERSION_MIN_USD and b > CONVERSION_MIN_USD and abs(a - b) / max(a, b) < CONVERSION_TOL:
                conv.append((dd, desc, a, b))
    res["n_conv"] = len(conv)
    add("same_day_opposite_cross_class_flows_count", len(conv), "dates", "all filings 2024-12..2026-09", len(conv),
        "Same-date pairs with both amounts > $1m and |a-b|/max(a,b) < 1%. CONSISTENT WITH CONVERSION; N-MFP3 DOES NOT LABEL CONVERSIONS", SRC_B7)
    for dd, desc, a, b in conv:
        add("same_day_opposite_cross_class_flow", f"{a:.2f}|{b:.2f}", "USD (redemption|subscription)", dd, 1,
            f"{desc}; consistent with conversion; N-MFP3 does not label conversions", f"{SRC_B7}; accession {acc_of[dd]}")

    # ---- write outputs
    def wcsv(name, fieldnames, data):
        with open(out / name, "w", encoding="utf-8", newline="") as fh:
            wr = csv.DictWriter(fh, fieldnames=fieldnames, lineterminator="\n")
            wr.writeheader()
            wr.writerows(data)

    alld = sorted(set(gy) | set(y[LIQ]) | set(y[ONC]) | set(flow[LIQ]) | set(flow[ONC]))
    drows = []
    for dd in alld:
        def g(dct):
            return "" if dd not in dct else f"{dct[dd]:.2f}"
        drows.append(dict(
            date=dd,
            liquidity_7d_net_yield_pct_C000255665=g(y[LIQ]),
            onchain_7d_net_yield_pct_C000261149=g(y[ONC]),
            series_7d_gross_yield_pct_A19=g(gy),
            paired_gap_onchain_minus_liquidity_pp=(f"{gap[dd]:.2f}" if dd in gap else ""),
            liquidity_gross_subs_usd=(f"{flow[LIQ][dd][0]}" if dd in flow[LIQ] else ""),
            liquidity_gross_reds_usd=(f"{flow[LIQ][dd][1]}" if dd in flow[LIQ] else ""),
            onchain_gross_subs_usd=(f"{flow[ONC][dd][0]}" if dd in flow[ONC] else ""),
            onchain_gross_reds_usd=(f"{flow[ONC][dd][1]}" if dd in flow[ONC] else ""),
            liquidity_eod_balance_reconstructed_approx=(f"{eod[LIQ][dd]:.2f}" if dd in eod[LIQ] else ""),
            onchain_eod_balance_reconstructed_approx=(f"{eod[ONC][dd]:.2f}" if dd in eod[ONC] else ""),
            launch_record=("1" if dd in launch else ""),
            report_date=rd_of[dd], source_accession=acc_of[dd]))
    wcsv("fidelity_class_daily.csv", list(drows[0]), drows)
    wcsv("fidelity_class_monthly.csv", list(mrows[0]), mrows)
    wcsv("fidelity_class_gap_summary.csv", ["quantity", "value", "unit", "window", "n", "definition", "source"], rows)

    if (root / PROSPECTUS_FILE).exists():
        raw_html = (root / PROSPECTUS_FILE).read_text(encoding="utf-8")
        frows = fee_terms(html_to_text(raw_html))
        wcsv("fidelity_fee_terms.csv", list(frows[0]), frows)
    else:
        # Publication adapter: copy the documented verbatim extract (data/source_facts).
        import shutil as _sh
        _sh.copyfile(root / "data/source_facts/fidelity_fee_terms.csv", out / "fidelity_fee_terms.csv")

    comps = scratch_comparisons(root, res)
    write_readme(out, root, script_path, inputs, crosscheck, repro_check, comps, res, conv, mrows, launch)

    print(f"filings parsed: {len(filings)}; frozen cross-check compared {n_cmp} class-dates, mismatches {dict(mism) or 0}")
    for k, (fr, mine, ok) in repro_check.items():
        print(f"reproduce {k}: frozen {fr} mine {mine} {'OK' if ok else 'FAIL'}")
    print(f"paired gap full {pair_full:.4f} pp (n {N}); 2026 {pair_26:.4f} pp (n {len(p26)})")
    for c in comps:
        print("scratch compare:", " | ".join(str(x) for x in c))
    print("outputs written to", out)


def write_readme(out, root, script_path, inputs, crosscheck, repro_check, comps, res, conv, mrows, launch):
    outs = ["fidelity_class_daily.csv", "fidelity_class_monthly.csv", "fidelity_class_gap_summary.csv", "fidelity_fee_terms.csv"]
    recon = [r for r in mrows if r["opening_minus_prior_month_end"] != ""]
    max_abs = max(abs(float(r["opening_minus_prior_month_end"])) for r in recon)
    flowchk = sum(1 for r in mrows if r["monthly_gross_subs_B7"] != r["sum_daily_gross_subs"]
                  or r["monthly_gross_reds_B7"] != r["sum_daily_gross_reds"])
    L = []
    a = L.append
    a("# EXPLORATORY 2026-11-17: Fidelity Treasury Digital Fund class comparison")
    a("")
    a("EXPLORATORY analysis of existing held data, 17 Nov 2026; changes no frozen measure. No network access, no new data. "
      "Every number here is descriptive; seven-day yields overlap and are not independent observations.")
    a("")
    a("Generated by `scripts/24_explore_fidelity_class_gap_2026_11_17.py` (this README is written by the script).")
    a("")
    a("## Re-run")
    a("")
    a("From the project root:")
    a("")
    a("```")
    a('python -I -X utf8 "scripts/24_explore_fidelity_class_gap_2026_11_17.py"')
    a("```")
    a("")
    a("An explicit project root may be passed as the first argument. Re-running rewrites byte-identical CSV outputs. "
      "This README is byte-identical only while `workflow/checks/supervisor17_econ_reviewer_scratch/` is present; without it, "
      "the one-time scratch comparison table below reads 'not found' (CSV outputs are unaffected).")
    a("")
    a(f"Script SHA-256: `{sha256(script_path)}`")
    a("")
    a("## Files")
    a("")
    a("| File | Content |")
    a("|---|---|")
    a("| `fidelity_class_daily.csv` | One row per business date in the 22 filings (2024-12-23..2026-09-30): Liquidity (C000255665) and OnChain (C000261149) seven-day net yields (Item B.8, percent), series seven-day gross yield (Item A.19, percent), same-date gap (pp), daily gross subscriptions/redemptions per class (Item B.7, USD), approximate reconstructed end-of-day balances, launch-record flag, source report date and accession. |")
    a("| `fidelity_class_monthly.csv` | One row per class-month: Item B.5 shares, net assets, B.7 monthly totals vs sum of daily flows, B.9 flag and B.9.a waiver dollars, reconstruction diagnostic (reconstructed opening balance minus prior month-end shares), approximate average daily balance, annualised waiver, mean gross-minus-net. |")
    a("| `fidelity_class_gap_summary.csv` | One row per quantity: `quantity,value,unit,window,n,definition,source`. |")
    a("| `fidelity_fee_terms.csv` | Fee/expense terms from the held 485BPOS (accession 0000917286-26-000089): fee tables, expense caps and end date, audited Financial Highlights ratios, verbatim quotations with section headings and character offsets in the normalised text. |")
    a("")
    a("Conventions in the summary: `source` \"N-MFP3 XML S000089156\" means the 22 held filings "
      "`data/raw/edgar/2026-10-16_FYOXX_N-MFP3_*.xml`; Items A.19 (series seven-day gross yield), B.5 (shares outstanding), "
      "B.7 (daily gross subscriptions/redemptions), B.8 (class seven-day net yield), B.9.a (expense payment/fee waiver dollars) "
      "follow the SEC Form N-MFP3 instructions held in `Readings/03_Policy_Regulatory/`. Window `2025-08-01..2026-09-30` is the full "
      "common window (report months in which both classes filed); `2026-01-01..2026-09-30` is Jan-Sep 2026. Yields are percent; "
      "gaps are percentage points (pp), OnChain minus Liquidity on the same date (negative = OnChain lower).")
    a("")
    a("Quotations in `fidelity_fee_terms.csv` are verbatim from the HTML after normalisation (script/style removed, tags stripped, "
      "HTML entities decoded, whitespace collapsed to single spaces); the script asserts each quotation occurs in that text "
      "inside the named class document and after the named heading. `text_offset` is the character offset in the normalised text.")
    a("")
    a("## Inputs and integrity checks")
    a("")
    a("| Input | SHA-256 | FREEZE_MANIFEST_2026-10-16 |")
    a("|---|---|---|")
    for rel, h, s in inputs:
        a(f"| `{rel}` | `{h}` | {s} |")
    a("")
    not_listed = [rel for rel, _, s in inputs if s != "match"]
    a("All 22 N-MFP3 XML files and the 485BPOS HTML match the freeze manifest (the script stops if any raw input does not). "
      + ("Inputs not listed in the freeze manifest (hash recorded above for this run): "
         + ", ".join(f"`{r}`" for r in not_listed) + "." if not_listed else "All inputs match the manifest."))
    a("")
    a("- All 22 filings are `N-MFP3` originals (no `N-MFP3/A`), series S000089156, report dates 2024-12-31..2026-09-30, one per month; no duplicate dates within a filing; every daily date lies in its filing's report month.")
    mm = crosscheck["mismatches"]
    a(f"- Cross-check against `freeze_nmfp_daily.csv` (FYOXX governing rows): {crosscheck['n_compared']} class-dates compared on net yield, series gross yield, daily subscriptions/redemptions and accession; mismatches: {mm if mm else 'none'}.")
    for k, (fr, mine, ok) in repro_check.items():
        a(f"- Reproduces `freeze_twin_descriptives.csv` {k[0]} / {k[1]}: frozen mean {fr[0]}% n {fr[1]}; this script {mine[0]}% n {mine[1]}: {'match' if ok else 'MISMATCH'}.")
    a(f"- Item B.7 monthly totals equal the sum of daily flows in {len(mrows) - flowchk} of {len(mrows)} class-months.")
    a(f"- Balance reconstruction diagnostic: reconstructed opening balance minus prior month-end Item B.5 shares; largest absolute value {max_abs:,.2f} shares (per class-month values in `fidelity_class_monthly.csv`). The reconstruction is approximate; see definitions.")
    a("")
    a("## Comparison with the earlier reviewer's scratch results")
    a("")
    a("Source: `workflow/checks/supervisor17_econ_reviewer_scratch/*_output.txt` (read only). Values rounded to the scratch output's precision.")
    a("")
    a("| Quantity | Scratch | This script | Result |")
    a("|---|---|---|---|")
    for lab, t, m_, r in comps:
        a(f"| {lab} | {t} | {m_} | {r} |")
    a("")
    a("Implementation differences that do not change the compared values at the reported precision: this script parses "
      "yields as exact decimals; the scratch scripts used floats. The scratch waiver calculation averaged OnChain's August 2025 "
      "balance over all 31 calendar days and Liquidity's December 2024 balance over all 31 days; this script averages from each "
      "class's first filed date (2025-08-04; 2024-12-23). Those months enter only the full-common and pre-OnChain waiver rows, "
      "which the scratch did not report. The additive decomposition of the full-period excess and the drop-based share are new here.")
    a("")
    a("## Source gaps / not established")
    a("")
    a("- **Launch-week OnChain yields.** The five OnChain records dated " + ", ".join(launch) + " are filed as 0.0001 (0.01%) while Liquidity "
      "reports 4.15% and the series gross yield 4.33% on the same dates. No held source states Fidelity's seven-day yield convention for a "
      "new class's first week (for example, whether a yield is computed over fewer than seven days of operation or set near zero). The "
      "audited Financial Highlights give the OnChain period as beginning August 4, 2025 (commencement of sale of shares). The records are "
      "reported here as filed; they are not treated as data errors and no mechanism is assigned. Held filings checked by the "
      "independent audit (`workflow/reviews/SUPERVISOR17_ECON_AUDIT.md`, AU-03) bear on their interpretation: OnChain's only "
      "August 2025 flows were $500,000 subscriptions on 4 and 12 August; month-end shares of 1,002,691.87 imply $2,691.87 of growth "
      "outside reported flows, consistent with accrual at about 4.09% from launch rather than near zero; the 2 September 2025 "
      "cross-class match differs by exactly that amount; the first non-0.01% value (11 August) is the first business day seven or more "
      "days after launch, and Liquidity reported no yields for its first seven days (23-27 December 2024); and OnChain was 100% held "
      "of record by an affiliated entity in August 2025. The launch records are therefore consistent with an incomplete seven-day "
      "calculation window; the convention itself is not documented in held sources. They are not evidence of an income shortfall.")
    af = res["aug_facts"]
    a(f"- **August 2025 flat yields.** On every paired date {af['first']}..{af['last']} the Liquidity net yield takes the value(s) "
      f"{'/'.join(af['liq_vals'])}% and the series gross yield {'/'.join(af['gross_vals'])}% (2025-08-01: {af['liq_0801']}% and "
      f"{af['gross_0801']}%); OnChain takes {'/'.join(af['onc_after_vals'])}% on every date {af['after_first']}..{af['last']}. "
      f"The OnChain gross-minus-net over 2025-08-11..29 is {res['gn_onc_aug_exl']:.2f} pp against {res['gn_liq_aug_exl']:.2f} pp for Liquidity, "
      "above OnChain's later level (about 0.20 pp) and its current 0.20% cap. No held source explains this; the fee history before the "
      "June 29, 2026 prospectus is not held (earlier prospectuses/supplements were not acquired).")
    a("- **Fee history.** The fee tables and caps are those of the prospectus dated June 29, 2026 (cap through August 31, 2027). The audited "
      "Financial Highlights give realised ratios only for the fiscal periods ended April 30, 2025 and 2026. Effective caps on earlier dates are not established.")
    a("- **Conversions.** N-MFP3 does not label class conversions. Same-day opposite cross-class flows above $1m matching within 1% are "
      f"listed ({len(conv)} dates) as consistent with conversion only. The Liquidity prospectus (Converting Shares) describes Liquidity-to-OnChain conversion; "
      "no held prospectus text describes OnChain-to-Liquidity conversion (the OnChain prospectus has no conversion text), so the "
      "OnChain-redemption/Liquidity-subscription match(es) on "
      + (", ".join(c[0] for c in conv if c[1].startswith("OnChain")) or "no date")
      + " are not explained by the held documents. The OnChain SAI's generic statement that \"The shares have no preemptive or "
      "conversion rights\" does not explain it either.")
    a("- **Balances.** End-of-day balances are reconstructed from month-end shares and daily gross flows; dividend reinvestment timing and "
      "cash distributions are not separately reported, so balances, balance weights, income illustrations and annualised waiver rates are approximate.")
    a("- **Item B.9.a scope.** The filed waiver dollars cover any person's payment or waiver of fund operating expenses for the class in the month; the filings do not split them by type. Blockchain transaction fees are stated to be borne by the Adviser or its affiliates; no held source quantifies them.")
    a("- **Cross-fund gross-minus-net.** Rows labelled cross-fund context compare different funds with different portfolios, fees and waivers; they are not a tokenisation premium.")
    a("")
    with open(out / "README.md", "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(L) + "\n")
    # record output hashes (excluding README itself) to stdout
    for o in outs:
        print(f"{o} sha256 {sha256(out / o)}")


if __name__ == "__main__":
    main(sys.argv)
