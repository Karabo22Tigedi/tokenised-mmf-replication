"""07_key_numbers.py -- key numbers and falsifier verdicts for the frozen vintage (freeze_2026-10-16)

  python -I scripts/07_key_numbers.py

Reads only data/processed/freeze_*.csv (written by 05_measures.py --prefix freeze --reveal-oos
--sensitivities, 02_parse_nmfp.py and 06's input matrix) and writes:
  data/processed/key_numbers.csv               name, value, unit, as_of, source_file, script
  data/processed/freeze_falsifier_verdicts.csv one row per falsifier x fund x specification
  data/processed/freeze_twin_descriptives.csv  Fidelity OnChain vs Liquidity (descriptive only)
Every number in notes/freeze_results.md is read from these files. Nothing is typed by hand.
ADDITION 2026-10-17 (exploratory, post-freeze, CP35/CP36): rows from data/processed/addl_summary_numbers.csv
(scripts/15_addl_analysis.py) are appended after the frozen rows, all named addl_*. Later ADDITION blocks append
addl_summary_numbers_18.csv (script 18, cycle 3) and addl_summary_numbers_19.csv (script 19, cycle 4) in that order;
ADDITION 2 (2026-10-18) appends addl2_summary_numbers_22.csv (script 22, receipts cash legs) last.

Falsifier scoring follows notes/preregistration.md §4.5 as implemented in
notes/preregistration_implementation_notes.md (STATUS: APPROVED; "Final implementation after audit"):
  F0  per fund: rules.f0_count on OOS rows, ET clock only; > 2 exceedances -> FAIL. Coverage-incomplete
      months count only through the one-sided test (CP11). A fund whose OOS months cannot reach 3
      complete-or-one-sided exceedances is NOT TESTABLE (no power). TR-3 scope 'all', UTC clock and
      every 05 sensitivity (sens_m0, sample=oos) are scored the same way.
  F1  per fund: pooled, basis=transfers_only, upper bound: value >= 5% or count >= 10% -> FAIL.
      JLTXX (and FOBXX on EVM) structurally precluded (prereg: 'no power'). 0 transfers -> the share is
      undefined; reported as NOT TESTABLE on the main basis.
  F2  per fund: non-business share of issuance + redemption value >= 10% -> FAIL.
  F3  FOBXX, WTGXX: non-TA supply outside the top-10 wallets at 2026-09-30 >= 25% -> FAIL. A worst-case
      bound adds the N-MFP3 shares not covered by event-chain balances to the outside-top-10 pool.
  F4  per fund: lag identified -> FAIL if (a) 0 non-business dividend days, (b) 100% TA-batch days and
      (c) median B lag >= 1; lag not identifiable -> partial verdict, FAIL if (a) and (b).
"""
import csv
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import rules as R  # noqa: E402

PROC = ROOT / "data" / "processed"
SCRIPT = "scripts/07_key_numbers.py"
VINTAGE = "freeze_2026-10-16"
WINDOW = "2026-01-01:2026-09-30"
FUNDS = ["FOBXX", "WTGXX", "JLTXX", "FYOXX"]
TWIN = {"OnChain": "C000261149", "Liquidity": "C000255665"}   # Fidelity series S000089156
KN = []


def rd(name):
    with open(PROC / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def kn(name, value, unit, as_of, source):
    KN.append(dict(name=name, value=value, unit=unit, as_of=as_of, source_file=f"data/processed/{source}", script=SCRIPT))


def fl(x):
    return float(x) if x not in ("", None) else None


# ------------------------------------------------------------------ inputs
m0 = rd("freeze_m0_reconciliation.csv")
oos = rd("freeze_m0_oos_codepath.csv")
m1 = rd("freeze_m1_secondary.csv")
m2 = rd("freeze_m2_calendar.csv")
m2f = rd("freeze_m2_flow_match.csv")
m3 = rd("freeze_m3_holding.csv")
m4 = rd("freeze_m4_servicing.csv")
roles = rd("freeze_wallet_roles.csv")
diag = rd("freeze_diagnostics.csv")
summ = [r for r in rd("freeze_nmfp_summary.csv") if r["governing"] == "1"]
daily = [r for r in rd("freeze_nmfp_daily.csv") if r["governing"] == "1"]
perm = rd("prep_permissions_matrix.csv")
sens = {k: rd(f"freeze_sens_{k}.csv") for k in ("m0", "m1", "m2", "m3", "m4", "labels", "roles")}
snaps = rd("freeze_supply_snapshots_evm.csv")
VARIANTS = list(dict.fromkeys(r["variant"] for r in sens["m4"]))

# ------------------------------------------------------------------ M0 / F0
for r in m0:
    tag = f"{r['fund']}__{r['report_date']}__{r['clock']}"
    for k, u in [("nmfp_class_shares", "shares"), ("onchain_supply_raw", "shares"), ("tr2_parked_subtracted", "shares"),
                 ("tr3_dividends_added", "shares"), ("onchain_supply_adjusted", "shares"), ("gap_shares", "shares"),
                 ("gap_pct", "pct of class shares"), ("gap_pct_tr3_all", "pct of class shares"),
                 ("gap_raw_pct", "pct of class shares")]:
        kn(f"m0_insample_{k}__{tag}", r[k], u, r["report_date"], "freeze_m0_reconciliation.csv")
    kn(f"m0_insample_coverage__{tag}", r["coverage"], "text", r["report_date"], "freeze_m0_reconciliation.csv")
    kn(f"m0_insample_chains_missing__{tag}", r["chains_missing"] or "none", "text", r["report_date"], "freeze_m0_reconciliation.csv")
for r in oos:
    if r["register_row"] != "True":
        continue
    tag = f"{r['fund']}__{r['report_date']}__{r['clock']}"
    for k in ("gap_pct", "gap_pct_tr3_all", "gap_raw_pct"):
        kn(f"m0_oos_{k}__{tag}", r[k], "pct of class shares", r["report_date"], "freeze_m0_oos_codepath.csv")
    for k in ("coverage", "chains_missing", "f0_month_status", "f0_month_status_tr3_all"):
        kn(f"m0_oos_{k}__{tag}", r[k] or "none", "text", r["report_date"], "freeze_m0_oos_codepath.csv")

# FYOXX diagnostic (descriptive, post hoc): OOS residual in shares vs the N-MFP3 gross subscription on the
# first ET business day after the report date (where FDIT's monthly dividend is recorded in Jul-Sep 2026)
cal = R.BusinessCalendar.from_csv(outside="weekends")
shares_by = {(r["ticker_file"], r["class_id"], r["report_date"]): fl(r["class_shares_outstanding"]) for r in summ}
subs_by = {(r["class_id"], r["date"]): fl(r["gross_subs"]) or 0.0 for r in daily}
fdit_diag = []
import datetime as dt  # noqa: E402
for r in oos:
    if r["fund"] == "FYOXX" and r["clock"] == "ET" and r["gap_pct"] not in ("", None):
        d = dt.date.fromisoformat(r["report_date"])
        sh = shares_by[("FYOXX", TWIN["OnChain"], r["report_date"])]
        gap_sh = fl(r["gap_pct"]) / 100 * sh
        nb = cal.next_business(d)
        sub1 = subs_by.get((TWIN["OnChain"], nb.isoformat()))
        fdit_diag.append(dict(report_date=r["report_date"], gap_shares=round(gap_sh, 2), first_bd=nb.isoformat(),
                              nmfp_subs_first_bd=sub1, residual_plus_sub=round(gap_sh + sub1, 2) if sub1 is not None else ""))
        kn(f"fdit_oos_gap_shares__{r['report_date']}", round(gap_sh, 2), "shares (gap_pct x class shares)", r["report_date"],
           "freeze_m0_oos_codepath.csv")
        kn(f"fdit_oos_nmfp_subs_first_bd_after__{r['report_date']}", sub1, "USD (= shares at $1 NAV)", nb.isoformat(),
           "freeze_nmfp_daily.csv")

close = [x for x in fdit_diag if x["residual_plus_sub"] != "" and abs(x["residual_plus_sub"]) <= 100]
kn("fdit_oos_months_residual_within_100_shares_of_first_bd_subs", len(close), "count (months)", VINTAGE,
   "freeze_fdit_oos_residual_diagnostic.csv")
kn("fdit_oos_months_in_diagnostic", len(fdit_diag), "count (months)", VINTAGE, "freeze_fdit_oos_residual_diagnostic.csv")
kn("fdit_oos_max_abs_residual_plus_sub_among_close", max((abs(x["residual_plus_sub"]) for x in close), default=""), "shares",
   VINTAGE, "freeze_fdit_oos_residual_diagnostic.csv")


def f0_score(rows, clock="ET", status_key="f0_month_status"):
    """F0 for one fund from OOS rows; returns verdict, counts."""
    gk = "gap_pct_tr3_all" if status_key.endswith("tr3_all") else "gap_pct"
    rs = [dict(r, f0_month_status=r[status_key], gap_pct=r.get(gk, "")) for r in rows if r.get("register_row") in ("True", True)]
    et = [r for r in rs if r["clock"] == clock]
    n_exc = R.f0_count(rs, clock=clock)
    st = Counter(r["f0_month_status"] for r in et)
    n_complete = st["complete_within"] + st["complete_exceeds"]
    n_undet = st["incomplete_undetermined"]
    n_onesided = st["incomplete_exceeds_onesided"]
    if n_exc > 2:
        v = "FAIL"
    elif n_complete < 3:      # fewer than 3 complete months and no one-sided fails: F0 could not fail
        v = "NOT TESTABLE"
    else:
        v = "PASS-for-claim"
    return v, dict(n_oos_months=len(et), n_complete=n_complete, n_complete_exceeds=st["complete_exceeds"],
                   n_incomplete_onesided_exceeds=n_onesided, n_incomplete_undetermined=n_undet, f0_count=n_exc,
                   max_abs_gap_complete=max((abs(fl(r["gap_pct"])) for r in et if r["f0_month_status"].startswith("complete")
                                             and r.get("gap_pct") not in ("", None)), default=""))


def f0_reason(v, c):
    if v == "FAIL":
        return f"{c['f0_count']} exceedances > 2"
    if c["n_complete"] == 0:
        return (f"no complete OOS month; {c['n_incomplete_undetermined']} coverage-incomplete months, none exceeds "
                f"+0.10% one-sided")
    if c["n_complete"] < 3:
        return f"only {c['n_complete']} complete OOS months: more than 2 exceedances impossible (no power)"
    return f"{c['f0_count']} exceedances in {c['n_complete']} complete months (<= 2); {c['n_incomplete_undetermined']} months undetermined"


VERD = []


def verdict(fx, fund, spec, variant, verdict_, metrics, threshold, note=""):
    VERD.append(dict(falsifier=fx, fund=fund, spec=spec, variant=variant, verdict=verdict_,
                     metrics="; ".join(f"{k}={v}" for k, v in metrics.items()), threshold=threshold, note=note))


F0_THR = "fails if > 2 OOS fund-months (ET) exceed |gap| > 0.10% (complete) or gap > +0.10% (incomplete, one-sided)"
for fund in FUNDS:
    rows = [r for r in oos if r["fund"] == fund]
    for spec, kw in [("main", {}), ("tr3_scope_all", dict(status_key="f0_month_status_tr3_all")),
                     ("clock_UTC", dict(clock="UTC"))]:
        v, c = f0_score(rows, **kw)
        verdict("F0", fund, "main" if spec == "main" else "sensitivity", spec, v, c, F0_THR, f0_reason(v, c))
        if spec == "main":
            for k, x in c.items():
                kn(f"f0_{k}__{fund}", x, "count" if k != "max_abs_gap_complete" else "pct", VINTAGE,
                   "freeze_m0_oos_codepath.csv")
            kn(f"f0_verdict__{fund}", v, "text", VINTAGE, "freeze_m0_oos_codepath.csv")
    for var in VARIANTS:
        if var == "evm_full_history":
            continue
        srows = [r for r in sens["m0"] if r["variant"] == var and r["sample"] == "oos" and r["fund"] == fund]
        if srows:
            v, c = f0_score(srows)
            verdict("F0", fund, "sensitivity", var, v, c, F0_THR, f0_reason(v, c))

# ------------------------------------------------------------------ M1 / F1
for r in m1:
    tag = f"{r['fund']}__{r['chain_scope']}__{r['basis']}"
    kn(f"m1_n_events__{tag}", r["n_events"], "count", WINDOW, "freeze_m1_secondary.csv")
    kn(f"m1_value__{tag}", r["value"], "shares", WINDOW, "freeze_m1_secondary.csv")
    for b in ("lower", "main", "upper"):
        for m in ("value", "count"):
            kn(f"m1_{b}_share_{m}_pct__{tag}", r[f"{b}_share_{m}_pct"], "pct", WINDOW, "freeze_m1_secondary.csv")

F1_THR = "fails if upper-bound secondary share >= 5% of non-dividend transfer value or >= 10% of count"
PRECLUDED = {"JLTXX": "structurally precluded (prereg §4.5: no power for JLTXX, 3-4 holders)"}


def f1_eval(row, fund, scope="pooled"):
    if fund in PRECLUDED:
        return "NOT TESTABLE", PRECLUDED[fund]
    if fund == "FOBXX" and scope not in ("pooled", "stellar"):
        return "NOT TESTABLE", "BENJI on EVM: structurally precluded (prereg §4.5)"
    if int(row["n_events"]) == 0:
        return "NOT TESTABLE", "no non-dividend transfers in the window: share undefined"
    fv, fc = fl(row["upper_share_value_pct"]), fl(row["upper_share_count_pct"])
    if fv >= 5 or fc >= 10:
        return "FAIL", f"upper value {fv}% / count {fc}%"
    return "PASS-for-claim", f"upper value {fv}% / count {fc}%"


def m1_metrics(r):
    return dict(n_transfers=r["n_events"], upper_value_pct=r["upper_share_value_pct"], upper_count_pct=r["upper_share_count_pct"],
                main_value_pct=r["main_share_value_pct"], main_count_pct=r["main_share_count_pct"],
                lower_value_pct=r["lower_share_value_pct"], lower_count_pct=r["lower_share_count_pct"])


for fund in FUNDS:
    for r in m1:
        if r["fund"] != fund:
            continue
        if r["chain_scope"] == "pooled" and r["basis"] == "transfers_only":
            v, n = f1_eval(r, fund)
            verdict("F1", fund, "main", "main", v, m1_metrics(r), F1_THR, n)
            kn(f"f1_verdict__{fund}", v, "text", WINDOW, "freeze_m1_secondary.csv")
        elif r["chain_scope"] == "pooled" and r["basis"] == "all_events":
            v, n = f1_eval(r, fund)
            verdict("F1", fund, "sensitivity", "basis_all_events", v, m1_metrics(r), F1_THR, n)
        elif r["basis"] == "transfers_only":
            v, n = f1_eval(r, fund, r["chain_scope"])
            verdict("F1", fund, "per-chain", f"chain_{r['chain_scope']}", v, m1_metrics(r), F1_THR, n)
    for var in VARIANTS:
        for r in sens["m1"]:
            if r["variant"] == var and r["fund"] == fund and r["chain_scope"] == "pooled" and r["basis"] == "transfers_only":
                v, n = f1_eval(r, fund)
                verdict("F1", fund, "sensitivity", var, v, m1_metrics(r), F1_THR, n)

# ------------------------------------------------------------------ M2 / F2
F2_THR = "fails if >= 10% of issuance + redemption value (ex-dividend) is on non-business days (ET)"
for r in m2:
    for k, u in [("issuance_value", "shares"), ("redemption_value", "shares"), ("nonbusiness_issuance_value", "shares"),
                 ("nonbusiness_redemption_value", "shares"), ("nonbusiness_share_pct", "pct"), ("modal_burn_hour_et", "hour ET")]:
        kn(f"m2_{k}__{r['fund']}", r[k], u, WINDOW, "freeze_m2_calendar.csv")
# burn-hour distribution (ET), parsed from burn_hour_counts ("hour:count;...")
for r in m2:
    hc = {int(h): int(c) for h, c in (p_.split(":") for p_ in (r["burn_hour_counts"] or "").split(";") if p_)}
    if not hc:
        continue
    f_ = r["fund"]
    modal = max(sorted(hc), key=lambda h: hc[h])
    pair = max(range(24), key=lambda h: (hc.get(h, 0) + hc.get((h + 1) % 24, 0), -h))
    kn(f"m2_burn_total__{f_}", sum(hc.values()), "count (burns)", WINDOW, "freeze_m2_calendar.csv")
    kn(f"m2_burn_modal_hour_count__{f_}", hc[modal], "count (burns in modal ET hour)", WINDOW, "freeze_m2_calendar.csv")
    kn(f"m2_burn_top2_consecutive_hours_start__{f_}", pair, "hour ET (first of two consecutive hours)", WINDOW, "freeze_m2_calendar.csv")
    kn(f"m2_burn_top2_consecutive_hours_count__{f_}", hc.get(pair, 0) + hc.get((pair + 1) % 24, 0),
       "count (burns in the two consecutive ET hours with most burns)", WINDOW, "freeze_m2_calendar.csv")
    for h in sorted(hc):
        kn(f"m2_burn_hour_count__{f_}__{h:02d}", hc[h], "count (burns in ET hour)", WINDOW, "freeze_m2_calendar.csv")
# M2 flow-match diagnostic with accrued dividend mints added (09_diag_fobxx_sameday.py; descriptive, not a measure).
# 09 reads f3_uncovered_shares__FOBXX from key_numbers.csv, so these rows are read only if 09 has already run.
if (PROC / "freeze_diag_fobxx_sameday.csv").exists():
    for r in rd("freeze_diag_fobxx_sameday.csv"):
        if r["test"] == "b2_match_subs_eq_issuance_plus_div_since_prev_nmfp_day_pct":
            kn(f"diag_m2_flowmatch_div_added_match_share_pct__{r['fund']}", r["value"], "pct", WINDOW,
               "freeze_diag_fobxx_sameday.csv")
            a, _, b = r["note"].split(" ")[:3]
            kn(f"diag_m2_flowmatch_div_added_match_days__{r['fund']}", int(a), "count (days)", WINDOW, "freeze_diag_fobxx_sameday.csv")
            kn(f"diag_m2_flowmatch_div_added_nmfp_days__{r['fund']}", int(b), "count (days)", WINDOW, "freeze_diag_fobxx_sameday.csv")
for r in m2f:
    for k, u in [("nmfp_business_days", "count"), ("days_same_day_match", "count"), ("match_share_pct", "pct"),
                 ("sum_nmfp_gross_subs", "USD"), ("sum_issuance_mints_on_those_days", "shares")]:
        kn(f"m2_flowmatch_{k}__{r['fund']}", r[k], u, WINDOW, "freeze_m2_flow_match.csv")


def f2_eval(r):
    s = fl(r["nonbusiness_share_pct"])
    if s is None:
        return "NOT TESTABLE", "no issuance or redemption value"
    return ("FAIL" if s >= 10 else "PASS-for-claim"), f"non-business share {s}%"


for fund in FUNDS:
    for r in m2:
        if r["fund"] == fund:
            v, n = f2_eval(r)
            verdict("F2", fund, "main", "main", v, dict(nonbusiness_share_pct=r["nonbusiness_share_pct"],
                                                        issuance_value=r["issuance_value"], redemption_value=r["redemption_value"]), F2_THR, n)
            kn(f"f2_verdict__{fund}", v, "text", WINDOW, "freeze_m2_calendar.csv")
    for r in sens["m2"]:
        if r["fund"] == fund:
            v, n = f2_eval(r)
            verdict("F2", fund, "sensitivity", r["variant"], v, dict(nonbusiness_share_pct=r["nonbusiness_share_pct"]), F2_THR, n)

# ------------------------------------------------------------------ M3 / F3, owner categories, holder counts
for r in m3:
    if r["row"] == "M3":
        tag = f"{r['fund']}__{r['snapshot']}"
        for k, u in [("balances_total", "shares"), ("ta_share_pct", "pct"), ("n_nonta_wallets", "count (non-TA wallets with positive balance, chain-address)"),
                     ("nonta_outside_top10_share_pct", "pct")]:
            if r.get(k, "") != "":
                kn(f"m3_{k}__{tag}", r[k], u, r["snapshot"], "freeze_m3_holding.csv")
        kn(f"m3_chains_without_balances__{tag}", r.get("chains_without_events") or "none", "text", r["snapshot"], "freeze_m3_holding.csv")
        for i, part in enumerate(p_ for p_ in (r["nmfp_owner_categories"] or "").split(";") if p_):
            if part:
                cat, rec, ben = part.split("|")
                kn(f"nmfp_owner_category_rec_pct__{tag}__{i + 1}_{cat}", round(100 * float(rec.split('=')[1]), 2), "pct of shares (of record)",
                   r["snapshot"], "freeze_m3_holding.csv")
    else:
        kn(f"m3_negative_balances__{r['fund']}__{r['snapshot']}__{r['chain']}", r["negative_balances"], "count", r["snapshot"],
           "freeze_m3_holding.csv")
# owner categories for every fund and class at every governing report date (from N-MFP3 directly)
for r in summ:
    for i, part in enumerate(p_ for p_ in (r["beneficial_record_owner_categories"] or "").split(";") if p_):
        if part:
            cat, rec, ben = part.split("|")
            kn(f"nmfp_owner_category_rec_pct__{r['ticker_file']}__{r['class_id']}__{r['report_date']}__{i + 1}_{cat}",
               round(100 * float(rec.split('=')[1]), 2), "pct of shares (of record)", r["report_date"], "freeze_nmfp_summary.csv")
# role counts (addresses ever seen in the rules window, per fund-chain)
rc = Counter((r["fund"], r["chain"], r["role"]) for r in roles)
for (f_, ch, role), n in sorted(rc.items()):
    kn(f"wallet_roles_n_addresses__{f_}__{ch}__{role}", n, "count (addresses seen in events)", WINDOW, "freeze_wallet_roles.csv")

# F3 worst-case bound: N-MFP3 class shares not covered by event-chain balances at 2026-09-30
F3_THR = "fails if wallets outside the top 10 (excl. TA set) hold >= 25% of on-chain non-TA supply at 2026-09-30"
q = "2026-09-30"


def f3_eval(r, fund):
    s = fl(r["nonta_outside_top10_share_pct"])
    return ("FAIL" if s >= 25 else "PASS-for-claim"), s


for fund in ("FOBXX", "WTGXX"):
    r = next(x for x in m3 if x["fund"] == fund and x["snapshot"] == q and x["row"] == "M3")
    v, s = f3_eval(r, fund)
    sh = next(fl(x["class_shares_outstanding"]) for x in summ if x["ticker_file"] == fund and x["report_date"] == q)
    bal = fl(r["balances_total"])
    ta = fl(r["ta_share_pct"]) / 100 * bal
    non = bal - ta
    out10 = s / 100 * non
    uncovered = max(sh - bal, 0.0)
    worst = round(100 * (out10 + uncovered) / (non + uncovered), 4)
    # archive supply on chains without events (known part of the uncovered amount)
    arch = sum(fl(x["supply_call"] or x["supply_events"]) or 0 for x in snaps
               if x["fund"] == fund and x["report_date"] == q and x["clock"] == "ET" and x["chain"] in (r["chains_without_events"] or "").split(";"))
    kn(f"f3_uncovered_shares__{fund}", round(uncovered, 6), "shares (N-MFP3 class shares - event-chain balances)", q, "freeze_m3_holding.csv")
    kn(f"f3_archive_supply_chains_without_events__{fund}", round(arch, 6), "shares", q, "freeze_supply_snapshots_evm.csv")
    kn(f"f3_worst_case_outside_top10_pct__{fund}", worst, "pct", q, "freeze_m3_holding.csv")
    wv = "FAIL" if worst >= 25 else "PASS-for-claim"
    verdict("F3", fund, "main", "main", v, dict(outside_top10_pct=s, n_nonta_wallets=r["n_nonta_wallets"],
                                                chains_without_balances=r["chains_without_events"]), F3_THR,
            f"outside top-10 share {s}%")
    verdict("F3", fund, "bound", "worst_case_uncovered_supply_outside_top10", wv,
            dict(worst_case_pct=worst, uncovered_shares=round(uncovered, 2), archive_supply_no_event_chains=round(arch, 2)),
            F3_THR, "all N-MFP3 shares not covered by event-chain balances assumed held outside the top 10")
    kn(f"f3_verdict__{fund}", v, "text", q, "freeze_m3_holding.csv")
    for x in sens["m3"]:
        if x["fund"] == fund and x["snapshot"] == q and x["row"] == "M3":
            vv, ss = f3_eval(x, fund)
            verdict("F3", fund, "sensitivity", x["variant"], vv, dict(outside_top10_pct=ss), F3_THR, "")
for fund in ("JLTXX", "FYOXX"):
    verdict("F3", fund, "main", "main", "NOT TESTABLE", {}, F3_THR, "prereg §4.5: F3 defined for FOBXX and WTGXX only (the low-minimum funds; JLTXX and FYOXX are the $1m-minimum funds)")

# ------------------------------------------------------------------ M4 / F4
F4_THR = ("fails if dividend mints occur only on business days (a), in TA batches (b) and, where the lag is identified, "
          "with median lag >= 1 business day (c); lag not identifiable -> partial verdict on (a) and (b)")
for r in m4:
    for k, u in [("n_dividend_mints", "count"), ("dividend_value", "shares"), ("est_accrual_on_event_chains", "shares"),
                 ("share_delivered_on_chain_pct", "pct"), ("n_dividend_days", "count"), ("n_nonbusiness_dividend_days", "count"),
                 ("n_dividend_days_ta_batch", "count"), ("share_dividend_days_ta_batch_pct", "pct"), ("median_lag_bd_B", "business days"),
                 ("median_lag_bd_variantA", "business days"), ("lag_status", "text"), ("rules_used", "text")]:
        kn(f"m4_{k}__{r['fund']}", r[k], u, WINDOW, "freeze_m4_servicing.csv")


def f4_eval(r):
    nd = int(r["n_dividend_days"] or 0)
    if nd == 0:
        return "NOT TESTABLE", "no dividend mints in the window"
    a = int(r["n_nonbusiness_dividend_days"]) == 0
    b = fl(r["share_dividend_days_ta_batch_pct"]) == 100.0
    ident = r["lag_status"].startswith("identified")
    if ident:
        c = fl(r["median_lag_bd_B"]) >= 1
        return ("FAIL" if (a and b and c) else "PASS-for-claim"), f"(a) {a}, (b) {b}, (c) {c}; lag identified"
    return ("FAIL (partial: lag not identifiable)" if (a and b) else "PASS-for-claim (partial: lag not identifiable)"), \
        f"(a) {a}, (b) {b}; lag not identifiable"


def m4_metrics(r):
    return dict(n_dividend_days=r["n_dividend_days"], n_nonbusiness_dividend_days=r["n_nonbusiness_dividend_days"],
                ta_batch_pct=r["share_dividend_days_ta_batch_pct"], median_lag_bd_B=r["median_lag_bd_B"] or "n/a",
                delivered_pct=r["share_delivered_on_chain_pct"])


for fund in FUNDS:
    r = next(x for x in m4 if x["fund"] == fund)
    v, n = f4_eval(r)
    verdict("F4", fund, "main", "main", v, m4_metrics(r), F4_THR, n)
    kn(f"f4_verdict__{fund}", v, "text", WINDOW, "freeze_m4_servicing.csv")
    for x in sens["m4"]:
        if x["fund"] == fund:
            vv, nn = f4_eval(x)
            verdict("F4", fund, "sensitivity", x["variant"], vv, m4_metrics(x), F4_THR, nn)

for fund in FUNDS:
    verdict("F-SA", fund, "legal", "-", "NOT SCORED HERE", {}, "legal falsifier", "see notes/sa_three_regimes.md")

# ------------------------------------------------------------------ robustness summary per falsifier x fund
for fx in ("F0", "F1", "F2", "F3", "F4"):
    for fund in FUNDS:
        rows = [x for x in VERD if x["falsifier"] == fx and x["fund"] == fund]
        mainv = next((x["verdict"] for x in rows if x["spec"] == "main"), None)
        if mainv is None:
            continue
        others = [x for x in rows if x["spec"] in ("sensitivity", "bound")]
        flips = sorted({x["variant"] for x in others if x["verdict"].split(" ")[0] != mainv.split(" ")[0]})
        verdict(fx, fund, "robustness", "summary", "robust" if not flips else "NOT robust",
                dict(n_variants=len(others), n_flip=len(flips)), "", ("flips under: " + ", ".join(flips)) if flips else "")
        kn(f"{fx.lower()}_robust__{fund}", "yes" if not flips else "no; flips under " + ", ".join(flips), "text", VINTAGE,
           "freeze_falsifier_verdicts.csv")

# ------------------------------------------------------------------ coverage, diagnostics, labels
for fund in FUNDS:
    et = [r for r in oos if r["fund"] == fund and r["clock"] == "ET" and r["register_row"] == "True"]
    cv = Counter(r["coverage"] for r in et)
    kn(f"coverage_oos_months_with_register_row__{fund}", len(et), "count", VINTAGE, "freeze_m0_oos_codepath.csv")
    kn(f"coverage_oos_months_complete__{fund}", cv["complete"], "count", VINTAGE, "freeze_m0_oos_codepath.csv")
    kn(f"coverage_oos_months_incomplete__{fund}", cv["coverage-incomplete"], "count", VINTAGE, "freeze_m0_oos_codepath.csv")
    mc = Counter(r["chains_missing"] for r in et if r["chains_missing"])
    kn(f"coverage_oos_missing_chain_sets__{fund}", "; ".join(f"{k}:{v}" for k, v in sorted(mc.items())) or "none", "text (chains:months)",
       VINTAGE, "freeze_m0_oos_codepath.csv")
    if et:
        kn(f"coverage_oos_first_report_date__{fund}", min(r["report_date"] for r in et), "date", VINTAGE, "freeze_m0_oos_codepath.csv")
        kn(f"coverage_oos_last_report_date__{fund}", max(r["report_date"] for r in et), "date", VINTAGE, "freeze_m0_oos_codepath.csv")
for r in diag:
    nm = f"diag_{r['item']}__{r['fund'] or 'all'}__{r['chain'] or 'all'}".replace(" ", "_")
    kn(nm, r["value"], "count" if "sum" not in r["item"] else "shares", VINTAGE, "freeze_diagnostics.csv")
    if r.get("note"):
        kn(nm + "__note", r["note"], "text", VINTAGE, "freeze_diagnostics.csv")

for r in sens["labels"]:
    if r["variant"] in ("stellar_unseeded", "rate_cap_k3", "rate_cap_off", "iii_off", "iii_monthly_accrual"):
        for k, v in r.items():
            if k.startswith(("dividend_", "issuance_")) and v:
                kn(f"labels_{r['variant']}__{r['fund']}__{r['chain']}__{k}", v, "count (mints)", VINTAGE, "freeze_sens_labels.csv")
ml = Counter()
with open(PROC / "freeze_mint_labels.csv", newline="") as f:
    for r in csv.DictReader(f):
        ml[(r["fund"], r["chain"], r["label"], r["rule"] or "none")] += 1
for (f_, ch, l_, ru), n in sorted(ml.items()):
    kn(f"labels_main__{f_}__{ch}__{l_}_{ru}", n, "count (mints, all event dates)", VINTAGE, "freeze_mint_labels.csv")

# ------------------------------------------------------------------ M5 permissions summary
ITEM_FOR = {"M1": "i", "M2": "iii", "M3": "v", "M4": "iv", "K1": "ii"}
seen = Counter()
for r in perm:
    seen[(r["item_code"], r["ticker"])] += 1
    sfx = "" if seen[(r["item_code"], r["ticker"])] == 1 else f"__row{seen[(r['item_code'], r['ticker'])]}"
    kn(f"permission_{r['item_code']}__{r['ticker']}{sfx}", r["coding"], "text (yes/no/unclear)", "2026-10-16", "prep_permissions_matrix.csv")
pc = Counter((r["ticker"], r["coding"]) for r in perm)
for (t, c_), n in sorted(pc.items()):
    kn(f"permission_coding_count__{t}__{c_}", n, "count (matrix rows)", "2026-10-16", "prep_permissions_matrix.csv")

# ------------------------------------------------------------------ Fidelity twin (descriptive only)
twin_rows = []
months_by = {k: sorted(r["report_date"] for r in summ if r["class_id"] == v) for k, v in TWIN.items()}
common = sorted(set(months_by["OnChain"]) & set(months_by["Liquidity"]))
for period, months in [("common_months", common), ("2026-01..2026-09", [m for m in common if m >= "2026-01"])]:
    for k, cid in TWIN.items():
        dd = [r for r in daily if r["class_id"] == cid and r["report_date"] in months]
        subs = [fl(r["gross_subs"]) or 0.0 for r in dd]
        reds = [fl(r["gross_reds"]) or 0.0 for r in dd]
        ys = [fl(r["class_7d_net_yield"]) for r in dd if r["class_7d_net_yield"]]
        nz_s, nz_r = [x for x in subs if x > 0], [x for x in reds if x > 0]
        last = max((r for r in summ if r["class_id"] == cid), key=lambda r: r["report_date"])
        row = dict(class_name=k, class_id=cid, period=period, months=f"{months[0]}..{months[-1]}" if months else "",
                   n_months=len(months), n_business_days=len(dd),
                   days_with_subscription=len(nz_s), days_with_subscription_pct=round(100 * len(nz_s) / len(dd), 2) if dd else "",
                   days_with_redemption=len(nz_r), days_with_redemption_pct=round(100 * len(nz_r) / len(dd), 2) if dd else "",
                   median_subscription_usd=round(statistics.median(nz_s), 2) if nz_s else "",
                   mean_subscription_usd=round(statistics.mean(nz_s), 2) if nz_s else "",
                   median_redemption_usd=round(statistics.median(nz_r), 2) if nz_r else "",
                   mean_redemption_usd=round(statistics.mean(nz_r), 2) if nz_r else "",
                   total_subscriptions_usd=round(sum(subs), 2), total_redemptions_usd=round(sum(reds), 2),
                   mean_7d_net_yield_pct=round(100 * statistics.mean(ys), 4) if ys else "",
                   min_initial_investment_latest=last["min_initial_investment"], latest_report_date=last["report_date"],
                   class_shares_outstanding_latest=last["class_shares_outstanding"],
                   owner_categories_latest=last["beneficial_record_owner_categories"],
                   n_owner_categories_latest=len([p for p in last["beneficial_record_owner_categories"].split(";") if p]))
        twin_rows.append(row)
        for kk, vv in row.items():
            if kk not in ("class_name", "class_id", "period"):
                kn(f"twin_{kk}__{k}__{period}", vv, "text" if isinstance(vv, str) else "see name", period, "freeze_twin_descriptives.csv")
# same-day yield difference (OnChain - Liquidity), days present in both
yd = defaultdict(dict)
for r in daily:
    for k, cid in TWIN.items():
        if r["class_id"] == cid and r["class_7d_net_yield"]:
            yd[r["date"]][k] = float(r["class_7d_net_yield"])
diffs = [100 * (v["OnChain"] - v["Liquidity"]) for v in yd.values() if len(v) == 2]
kn("twin_7d_net_yield_diff_onchain_minus_liquidity_mean_pctpts", round(statistics.mean(diffs), 4) if diffs else "", "pct points",
   "common days", "freeze_nmfp_daily.csv")
kn("twin_7d_net_yield_diff_n_days", len(diffs), "count", "common days", "freeze_nmfp_daily.csv")

# ------------------------------------------------------------------ ADDITION 2026-10-17 (exploratory, CP35/CP36)
# Post-freeze, user-approved exploratory analyses (scripts/13-15). Appended AFTER every frozen row; names
# must start with addl_. Nothing above is read from these files, so no pre-registered number changes.
_addl = PROC / "addl_summary_numbers.csv"
if _addl.exists():
    for r in rd("addl_summary_numbers.csv"):
        if not r["name"].startswith("addl_"):
            raise SystemExit(f"addition row without addl_ prefix: {r['name']}")
        KN.append(dict(name=r["name"], value=r["value"], unit=r["unit"], as_of=r["as_of"], source_file=r["source_file"],
                       script="scripts/15_addl_analysis.py via scripts/07_key_numbers.py (ADDITION, exploratory)"))
# ADDITION cycle 3 (2026-10-17, exploratory): scripts/18_addl_oos_and_ordering.py. Appended after the script-15 rows.
_addl18 = PROC / "addl_summary_numbers_18.csv"
if _addl18.exists():
    for r in rd("addl_summary_numbers_18.csv"):
        if not r["name"].startswith("addl_"):
            raise SystemExit(f"addition row without addl_ prefix: {r['name']}")
        KN.append(dict(name=r["name"], value=r["value"], unit=r["unit"], as_of=r["as_of"], source_file=r["source_file"],
                       script="scripts/18_addl_oos_and_ordering.py via scripts/07_key_numbers.py (ADDITION, exploratory)"))
# ADDITION cycle 4 (2026-10-17, exploratory): scripts/19_addl_swap_and_owner.py. Appended after the script-18 rows.
_addl19 = PROC / "addl_summary_numbers_19.csv"
if _addl19.exists():
    for r in rd("addl_summary_numbers_19.csv"):
        if not r["name"].startswith("addl_"):
            raise SystemExit(f"addition row without addl_ prefix: {r['name']}")
        KN.append(dict(name=r["name"], value=r["value"], unit=r["unit"], as_of=r["as_of"], source_file=r["source_file"],
                       script="scripts/19_addl_swap_and_owner.py via scripts/07_key_numbers.py (ADDITION, exploratory)"))
# ADDITION 2 (2026-10-18, exploratory, CP65): scripts/22_addl2_cashlegs.py decodes the cash legs from the receipts
# fetched by scripts/21_addl2_fetch_receipts.py (data/raw/addition_2026-10-18/). Appended after the script-19 rows;
# names must start with addl2_.
_addl22 = PROC / "addl2_summary_numbers_22.csv"
if _addl22.exists():
    for r in rd("addl2_summary_numbers_22.csv"):
        if not r["name"].startswith("addl2_"):
            raise SystemExit(f"addition-2 row without addl2_ prefix: {r['name']}")
        KN.append(dict(name=r["name"], value=r["value"], unit=r["unit"], as_of=r["as_of"], source_file=r["source_file"],
                       script="scripts/22_addl2_cashlegs.py via scripts/07_key_numbers.py (ADDITION 2, exploratory)"))


# ------------------------------------------------------------------ write
def write(rows, name):
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with open(PROC / name, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {name}: {len(rows)} rows")


names = Counter(r["name"] for r in KN)
dup = [n for n, c in names.items() if c > 1]
if dup:
    raise SystemExit(f"duplicate key-number names: {dup[:5]}")
write(KN, "key_numbers.csv")
write(VERD, "freeze_falsifier_verdicts.csv")
write(twin_rows, "freeze_twin_descriptives.csv")
write(fdit_diag, "freeze_fdit_oos_residual_diagnostic.csv")
print("\nVerdicts (main):")
for x in VERD:
    if x["spec"] in ("main", "robustness"):
        print(f"  {x['falsifier']:4} {x['fund']:6} {x['variant']:8} {x['verdict']:45} {x['note']}")
