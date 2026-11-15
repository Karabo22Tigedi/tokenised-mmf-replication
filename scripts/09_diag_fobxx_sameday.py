"""09_diag_fobxx_sameday.py: read-only diagnostic of the M2 same-day subscription match.

Why is the FOBXX same-ET-day match of N-MFP3 gross subscriptions to issuance mints 0%
(WTGXX 75%, FYOXX 91%)? Candidate explanations tested, from frozen outputs only:
  (a) granularity: N-MFP3 flows are fund-wide, mints are counted on event chains only
      (FOBXX: Aptos, Avalanche, Base have no events);
  (b) N-MFP3 gross subscriptions include reinvested dividends (IMPL-13: not ex-dividend),
      while issuance mints exclude TR-6 dividend mints; FOBXX pays dividends daily;
  (c) timing: issuance recorded on chain the next ET day (T+1);
  (d) code: an independent recomputation of the 05_measures.py match from the frozen
      label file must reproduce freeze_m2_flow_match.csv exactly.

Inputs (read-only): data/processed/freeze_nmfp_daily.csv, freeze_nmfp_filings.csv,
freeze_mint_labels.csv, freeze_m2_flow_match.csv, freeze_supply_snapshots_evm.csv.
Output: data/processed/freeze_diag_fobxx_sameday.csv (long format: fund, test, value, note).
No rule, measure or verdict is changed; the M2 match row stays as computed.

Run: python -I scripts/09_diag_fobxx_sameday.py
"""
import csv
import datetime as dt
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import rules as R  # noqa: E402

PROC = ROOT / "data" / "processed"
FUNDS = ["FOBXX", "WTGXX", "JLTXX", "FYOXX"]
W0, W1 = dt.date(2026, 1, 1), dt.date(2026, 9, 30)


def rd(name):
    with open(PROC / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def tol(x):                       # identical to 05_measures.py line 484
    return max(1.0, 0.001 * x)


def main():
    cls = {r["ticker"]: r["class_id"] for r in rd("freeze_nmfp_filings.csv")}
    subs = defaultdict(dict)
    for r in rd("freeze_nmfp_daily.csv"):
        if r["governing"] == "1" and r["class_id"] == cls[r["ticker_file"]] and r["gross_subs"]:
            subs[r["ticker_file"]][dt.date.fromisoformat(r["date"])] = float(r["gross_subs"])

    iss = defaultdict(lambda: defaultdict(float))
    div = defaultdict(lambda: defaultdict(float))
    iss_chain = defaultdict(lambda: defaultdict(float))
    for r in rd("freeze_mint_labels.csv"):
        ts = dt.datetime.fromisoformat(r["ts_utc"].replace("Z", "+00:00"))
        d = R.et_date(ts)
        if not (W0 - dt.timedelta(days=3) <= d <= W1 + dt.timedelta(days=3)):
            continue
        a = float(r["amount"])
        if r["label"] == "issuance":
            iss[r["fund"]][d] += a
            iss_chain[(r["fund"], r["chain"])][d] += a
        else:
            div[r["fund"]][d] += a

    m2f = {r["fund"]: r for r in rd("freeze_m2_flow_match.csv")}
    out = []

    def add(f, test, val, note=""):
        out.append(dict(fund=f, test=test, value=val, note=note))

    for f in FUNDS:
        days = sorted(d for d in subs[f] if W0 <= d <= W1)
        n = len(days)
        S = {d: subs[f][d] for d in days}
        I, D = iss[f], div[f]
        nxt = lambda d: d + dt.timedelta(days=1)  # noqa: E731

        def share(pred):
            k = sum(1 for d in days if pred(d))
            return k, round(100.0 * k / n, 4) if n else ""

        # (d) recomputation
        k, p = share(lambda d: abs(S[d] - I.get(d, 0.0)) <= tol(S[d]))
        add(f, "d_recomputed_same_day_match_days", k,
            f"05_measures.py value {m2f[f]['days_same_day_match']} of {m2f[f]['nmfp_business_days']}; recomputed of {n}")
        add(f, "d_recomputed_same_day_match_pct", p, f"05_measures.py value {m2f[f]['match_share_pct']}")
        add(f, "d_recomputed_equals_measure", int(str(k) == m2f[f]["days_same_day_match"] and str(n) == m2f[f]["nmfp_business_days"]), "1 = identical")

        # days with any dividend mint / any issuance
        add(f, "nmfp_days_with_dividend_mints_same_day", share(lambda d: D.get(d, 0.0) > 0)[0], f"of {n}")
        add(f, "nmfp_days_with_issuance_mints_same_day", share(lambda d: I.get(d, 0.0) > 0)[0], f"of {n}")
        add(f, "nmfp_days_with_zero_subs", share(lambda d: S[d] == 0)[0], f"of {n}")

        # sign of residual N-MFP3 subs - issuance
        res = [S[d] - I.get(d, 0.0) for d in days]
        add(f, "residual_subs_minus_issuance_positive_days", sum(1 for x in res if x > tol(1.0)), f"of {n}; residual > 1 share")
        add(f, "residual_subs_minus_issuance_negative_days", sum(1 for x in res if x < -1.0), f"of {n}")
        add(f, "residual_median", round(statistics.median(res), 4) if res else "", "shares")

        # (b) dividends: subs vs issuance + same-day dividend mints, and vs prior-day dividend mints
        k, p = share(lambda d: abs(S[d] - I.get(d, 0.0) - D.get(d, 0.0)) <= tol(S[d]))
        add(f, "b_match_subs_eq_issuance_plus_div_same_day_pct", p, f"{k} of {n} days")
        prv = lambda d: d - dt.timedelta(days=1)  # noqa: E731
        k, p = share(lambda d: abs(S[d] - I.get(d, 0.0) - D.get(prv(d), 0.0)) <= tol(S[d]))
        add(f, "b_match_subs_eq_issuance_plus_div_prev_cal_day_pct", p, f"{k} of {n} days")
        # residual relative to the dividend amount on days with zero issuance mints
        rat = [(S[d] - I.get(d, 0.0)) / D[d] for d in days if D.get(d, 0.0) > 0 and I.get(d, 0.0) == 0]
        add(f, "b_ratio_residual_to_same_day_div_median_on_no_issuance_days",
            round(statistics.median(rat), 4) if rat else "", f"n={len(rat)} days")
        # on days without dividend mints, how often does the plain rule match?
        nd = [d for d in days if D.get(d, 0.0) == 0]
        k2 = sum(1 for d in nd if abs(S[d] - I.get(d, 0.0)) <= tol(S[d]))
        add(f, "b_same_day_match_pct_on_days_without_dividend_mints",
            round(100.0 * k2 / len(nd), 4) if nd else "", f"{k2} of {len(nd)} days")
        wd = [d for d in days if D.get(d, 0.0) > 0]
        k3 = sum(1 for d in wd if abs(S[d] - I.get(d, 0.0)) <= tol(S[d]))
        add(f, "b_same_day_match_pct_on_days_with_dividend_mints",
            round(100.0 * k3 / len(wd), 4) if wd else "", f"{k3} of {len(wd)} days")

        # (b') dividends accrued since the previous N-MFP3 day (weekend/holiday dividends
        # are credited on chain daily but reinvested in N-MFP3 on a business day)
        prev_nd = {d: (days[i - 1] if i else d - dt.timedelta(days=1)) for i, d in enumerate(days)}

        def div_acc(d):
            x, s = prev_nd[d] + dt.timedelta(days=1), 0.0
            while x <= d:
                s += D.get(x, 0.0)
                x += dt.timedelta(days=1)
            return s
        k, p = share(lambda d: abs(S[d] - I.get(d, 0.0) - div_acc(d)) <= tol(S[d]))
        add(f, "b2_match_subs_eq_issuance_plus_div_since_prev_nmfp_day_pct", p, f"{k} of {n} days")
        for thr in (0.01, 0.05):
            k, p = share(lambda d, t=thr: abs(S[d] - I.get(d, 0.0) - div_acc(d)) <= max(1.0, t * S[d]))
            add(f, f"b2_within_{int(thr * 100)}pct_share", p, f"{k} of {n} days")
        r2 = [S[d] - I.get(d, 0.0) - div_acc(d) for d in days]
        add(f, "b2_residual_after_div_sum", round(sum(r2), 2), "N-MFP3 subs - issuance - accrued dividend mints (event chains)")
        add(f, "b2_residual_after_div_positive_days", sum(1 for x in r2 if x > 1.0), f"of {n}")
        add(f, "b2_residual_after_div_median", round(statistics.median(r2), 4) if r2 else "", "shares")

        # (c) timing: subs vs next-ET-day issuance (and next business day)
        k, p = share(lambda d: abs(S[d] - I.get(nxt(d), 0.0)) <= tol(S[d]))
        add(f, "c_match_subs_eq_next_cal_day_issuance_pct", p, f"{k} of {n} days")
        k, p = share(lambda d: abs(S[d] - I.get(nbd(d), 0.0)) <= tol(S[d]))
        add(f, "c_match_subs_eq_next_business_day_issuance_pct", p, f"{k} of {n} days")
        k, p = share(lambda d: abs(S[d] - I.get(nbd(d), 0.0) - D.get(nbd(d), 0.0)) <= tol(S[d]))
        add(f, "bc_match_subs_eq_next_bd_issuance_plus_div_pct", p, f"{k} of {n} days")

        # (a) granularity: window sums and issuance by chain
        add(f, "a_sum_nmfp_subs", round(sum(S.values()), 2), "window business days")
        add(f, "a_sum_issuance_same_day", round(sum(I.get(d, 0.0) for d in days), 6), "event chains only")
        add(f, "a_sum_div_mints_same_day", round(sum(D.get(d, 0.0) for d in days), 6), "event chains only")
        add(f, "a_sum_div_mints_all_window_days", round(sum(v for d, v in D.items() if W0 <= d <= W1), 6),
            "incl. weekends/holidays; event chains only")
        for (ff, ch), byd in sorted(iss_chain.items()):
            if ff == f:
                add(f, f"a_issuance_window_chain_{ch}", round(sum(v for d, v in byd.items() if W0 <= d <= W1), 6), "")
        # closeness: share of days within 1% / 5% (descriptive)
        for thr in (0.01, 0.05):
            k, p = share(lambda d, t=thr: abs(S[d] - I.get(d, 0.0)) <= max(1.0, t * S[d]))
            add(f, f"same_day_within_{int(thr * 100)}pct_share", p, f"{k} of {n} days")

    # (a) uncovered-chain supply share for FOBXX (archive totalSupply, ET cutoff)
    snaps = defaultdict(dict)
    for r in rd("freeze_supply_snapshots_evm.csv"):
        if r["fund"] == "FOBXX" and r["clock"] == "ET" and r["status"] == "ok" and r["supply_call"]:
            snaps[r["report_date"]][r["chain"]] = float(r["supply_call"])
    for rdte in ("2026-03-31", "2026-06-30", "2026-09-30"):
        if rdte in snaps:
            unc = sum(v for ch, v in snaps[rdte].items() if ch in ("avalanche", "base"))
            add("FOBXX", f"a_uncovered_evm_supply_avalanche_base_{rdte}", round(unc, 6),
                "archive totalSupply; Aptos not observed (no key)")

    # (a) scale check: daily dividend that the uncovered FOBXX supply would earn, vs the
    # median residual after event-chain dividends (both in shares per N-MFP3 day)
    kn = {r["name"]: r["value"] for r in rd("key_numbers.csv")}
    unc_all = float(kn["f3_uncovered_shares__FOBXX"])
    ylds = [float(r["class_7d_net_yield"]) for r in rd("freeze_nmfp_daily.csv")
            if r["ticker_file"] == "FOBXX" and r["governing"] == "1" and r["class_id"] == cls["FOBXX"]
            and r["class_7d_net_yield"] and W0 <= dt.date.fromisoformat(r["date"]) <= W1]
    y = statistics.mean(ylds)
    add("FOBXX", "a_uncovered_shares_2026-09-30_all_chains", unc_all, "key_numbers f3_uncovered_shares__FOBXX (Aptos, Avalanche, Base)")
    add("FOBXX", "a_mean_7d_net_yield_window", round(y, 6), "N-MFP3 class 7-day net yield, decimal")
    add("FOBXX", "a_implied_daily_dividend_on_uncovered_shares", round(unc_all * y / 365, 2),
        "shares per calendar day; compare b2_residual_after_div_median")
    b2med = next(r["value"] for r in out if r["fund"] == "FOBXX" and r["test"] == "b2_residual_after_div_median")
    add("FOBXX", "a_implied_annual_rate_of_b2_median_on_uncovered_shares", round(float(b2med) / unc_all * 365, 6),
        "b2 median residual / uncovered shares x 365; compare mean 7-day yield")

    with open(PROC / "freeze_diag_fobxx_sameday.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["fund", "test", "value", "note"])
        w.writeheader()
        w.writerows(out)
    for r in out:
        print(r["fund"], r["test"], r["value"], r["note"], sep=" | ")


CAL = None


def nbd(d):
    x = d + dt.timedelta(days=1)
    while not CAL.is_business(x):
        x += dt.timedelta(days=1)
    return x


if __name__ == "__main__":
    CAL = R.BusinessCalendar.from_csv(PROC / "ref_fedwire_holidays.csv")
    main()
