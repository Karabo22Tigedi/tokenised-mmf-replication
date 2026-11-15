"""18_addl_oos_and_ordering.py -- ADDITION 2026-10-17 (exploratory, post-freeze; review cycle 3)

FROZEN inputs only (plus the logged addition outputs of scripts 13 and 15). No raw fetch. No freeze_* file is
written; no frozen measure, rule or threshold is changed. 05_measures.py is IMPORTED (its functions are called
in memory with the frozen MAIN specification); its main() is not run and it writes nothing.

Task 1  Out-of-calibration re-run of F1-F4 on 2026-01-01..2026-06-30 ET (the calibration pilot used Jul-Sep 2026).
        Spec "main_frozen_classification": TR-5 roles and TR-6 labels exactly as in the freeze (rules applied to
          every frozen event, as 05_measures.py did), measures restricted to the Jan-Jun window. Before that, the
          same code on the full window must reproduce freeze_m1/m2/m2_flow_match/m3/m4 exactly (else exit).
        Spec "info_horizon_2026-07-02": TR-5/TR-6 re-run on events up to 23:59:59 ET on 2026-07-02 only (the
          second ET business day after 30 June; 3 July 2026 is a Fedwire closure), so no role or label can draw
          on later data, while TR-2 (1 business day), TR-3 (1 business day) and TR-5 parking (2 business days)
          keep the look-ahead the frozen rules give them at the window edge.
        Stellar seeding (IMPL-1b): unchanged. Balances are seeded at 2026-01-01 00:00 ET by back-out, which does
          not depend on the window end. F3 is evaluated at the window-end quarter snapshot 2026-06-30 (the
          prereg fixes 30 Sep 2026 for the full window). F0/TR-3 are not part of this task.
        Verdict functions are verbatim copies of 07_key_numbers.py f1_eval/f2_eval/f3_eval/f4_eval and are
          checked against freeze_falsifier_verdicts.csv (main rows) before use.
Task 2  Ordering: on-chain issuance mints (burns) vs N-MFP3 daily gross subscriptions (redemptions), 2026-01-01..
        2026-09-30, same tolerance as the frozen M2 flow match: |nmfp - chain| <= max(1, 0.001 x nmfp).
Task 3  MultiliquidSwap cash legs: checks whether receipts/logs for the 28 WTGXX transactions are held in data/raw.
Task 4  WTGXX F1 count shares excluding transfers under 100 shares, with and without wallet 0x0c561ed8.

Outputs (data/processed): addl_oos_measures.csv, addl_oos_verdicts.csv, addl_oos_role_changes.csv, addl_ordering_days.csv,
  addl_ordering_summary.csv, addl_ordering_hours.csv, addl_mls_cashleg.csv, addl_f1_minsize.csv,
  addl_summary_numbers_18.csv (appended to key_numbers.csv by 07_key_numbers.py, ADDITION block).
Run: python -I scripts/18_addl_oos_and_ordering.py
"""
import argparse
import csv
import datetime as dt
import gzip
import importlib.util
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import rules as R  # noqa: E402

spec = importlib.util.spec_from_file_location("measures05", ROOT / "scripts" / "05_measures.py")
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)

PROC = ROOT / "data" / "processed"
RAW = ROOT / "data" / "raw"
STAG = "freeze_2026-10-16"
FUNDS = M.FUNDS
FULL = (dt.date(2026, 1, 1), dt.date(2026, 9, 30))
OOSW = (dt.date(2026, 1, 1), dt.date(2026, 6, 30))
HORIZON_DAY = dt.date(2026, 7, 2)
WALLET = "0x0c561ed86ef65dd681500d6266c99e03dec3c541"
WTGXX_ETH = "0x1fecf3d9d4fee7f2c02917a66028a48c6706c179"
STABLES = {"USDC": "a0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", "USDT": "dac17f958d2ee523a2206206994597c13d831ec7"}
SN = []   # summary numbers (all names addl_*)


def sn(name, value, unit, as_of, source):
    SN.append(dict(name=name, value=value, unit=unit, as_of=as_of, source_file=f"data/processed/{source}"))


def rd(name):
    with open(PROC / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write(rows, name):
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with open(PROC / name, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"  wrote {name}: {len(rows)} rows")


def fl(x):
    return float(x) if x not in ("", None) else None


def pct(a, b):
    return round(100 * a / b, 4) if b else ""


# ============================================================ verdict functions (verbatim from 07_key_numbers.py)
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


def f2_eval(r):
    s = fl(r["nonbusiness_share_pct"])
    if s is None:
        return "NOT TESTABLE", "no issuance or redemption value"
    return ("FAIL" if s >= 10 else "PASS-for-claim"), f"non-business share {s}%"


def f3_eval(r, fund):
    s = fl(r["nonta_outside_top10_share_pct"])
    return ("FAIL" if s >= 25 else "PASS-for-claim"), s


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


def s(rows):
    """Rows as written by csv (str of every value), for exact comparison with frozen CSVs."""
    return [{k: ("" if v is None else str(v)) for k, v in r.items()} for r in rows]


def verdicts_for(wm, c, qend):
    """F1-F4 verdicts from one window_measures result (pooled transfers_only for F1; F3 at qend)."""
    out = {}
    for fund in FUNDS:
        r1 = next(x for x in s(wm["m1"]) if x["fund"] == fund and x["chain_scope"] == "pooled" and x["basis"] == "transfers_only")
        out[("F1", fund)] = f1_eval(r1, fund) + (r1,)
        r2 = next(x for x in s(wm["m2"]) if x["fund"] == fund)
        out[("F2", fund)] = f2_eval(r2) + (r2,)
        r4 = next(x for x in s(wm["m4"]) if x["fund"] == fund)
        out[("F4", fund)] = f4_eval(r4) + (r4,)
        if fund in ("FOBXX", "WTGXX"):
            r3 = next(x for x in s(wm["m3"]) if x["fund"] == fund and x["snapshot"] == str(qend) and x["row"] == "M3")
            v, s3 = f3_eval(r3, fund)
            g = c.reg.get((fund, qend.isoformat()))
            sh = fl(g["class_shares_outstanding"])
            bal = fl(r3["balances_total"])
            non = bal - fl(r3["ta_share_pct"]) / 100 * bal
            unc = max(sh - bal, 0.0)
            worst = round(100 * (s3 / 100 * non + unc) / (non + unc), 4)
            r3 = dict(r3, nmfp_class_shares=sh, uncovered_shares=round(unc, 6), worst_case_outside_top10_pct=worst,
                      worst_case_verdict="FAIL" if worst >= 25 else "PASS-for-claim")
            out[("F3", fund)] = (v, f"outside top-10 share {s3}%", r3)
        else:
            out[("F3", fund)] = ("NOT TESTABLE", "prereg §4.5: F3 defined for FOBXX and WTGXX only", {})
    return out


# ============================================================ Task 1
def task1(c):
    print("\n=== Task 1: out-of-calibration re-run (Jan-Jun 2026) ===")
    p = dict(M.MAIN)
    roles, labels = M.classify(c, p)
    # (a) reproduction of the frozen full-window measures with the same in-memory code
    wm_full, _ = M.window_measures(c, p, roles, labels, FULL[0], FULL[1],
                                   [dt.date(2026, 3, 31), dt.date(2026, 6, 30), dt.date(2026, 9, 30)], f"{FULL[0]}:{FULL[1]}")
    for k, fn in [("m1", "freeze_m1_secondary.csv"), ("m2", "freeze_m2_calendar.csv"), ("m2f", "freeze_m2_flow_match.csv"),
                  ("m3", "freeze_m3_holding.csv"), ("m4", "freeze_m4_servicing.csv")]:
        mine, frz = s(wm_full[k]), rd(fn)
        same = len(mine) == len(frz) and all(all(a.get(f, "") == b[f] for f in b) for a, b in zip(mine, frz))
        print(f"  reproduction {fn}: {'IDENTICAL' if same else 'MISMATCH'} ({len(mine)} rows)")
        if not same:
            sys.exit(f"in-memory re-run does not reproduce {fn}; refusing to continue")
    # (b) verdict functions reproduce the frozen main verdicts
    frz_v = {(r["falsifier"], r["fund"]): r["verdict"] for r in rd("freeze_falsifier_verdicts.csv") if r["spec"] == "main"}
    vfull = verdicts_for(wm_full, c, dt.date(2026, 9, 30))
    for k, (v, _, _) in vfull.items():
        if frz_v.get(k) != v:
            sys.exit(f"verdict copy does not reproduce frozen {k}: {v} vs {frz_v.get(k)}")
    print("  verdict functions reproduce all frozen main F1-F4 verdicts")
    # (c) Jan-Jun, frozen classification
    q = [dt.date(2026, 3, 31), dt.date(2026, 6, 30)]
    wm_oos, _ = M.window_measures(c, p, roles, labels, OOSW[0], OOSW[1], q, f"{OOSW[0]}:{OOSW[1]}")
    v_oos = verdicts_for(wm_oos, c, OOSW[1])
    # (d) Jan-Jun, information horizon: TR-5/TR-6 on events <= 23:59:59 ET 2026-07-02 only
    H = R.end_of_day(HORIZON_DAY, "ET")
    full_ev = c.ev_by
    n_before = sum(len(v) for v in full_ev.values())
    c.ev_by = defaultdict(list, {k: [e for e in v if e["ts"] <= H] for k, v in full_ev.items()})
    n_after = sum(len(v) for v in c.ev_by.values())
    print(f"  info-horizon filter (events <= {H:%Y-%m-%dT%H:%M:%SZ}): rule events {n_before} -> {n_after}")
    M._label_cache.clear(); M._role_cache.clear(); M._accrual_cache.clear()
    roles_h, labels_h = M.classify(c, p)
    wm_h, _ = M.window_measures(c, p, roles_h, labels_h, OOSW[0], OOSW[1], q, f"{OOSW[0]}:{OOSW[1]} horizon {HORIZON_DAY}")
    v_h = verdicts_for(wm_h, c, OOSW[1])
    rc_rows = [dict(fund=k[0], chain=k[1], address=a, role_frozen=v["role"], reasons_frozen=";".join(v["reasons"]),
                    role_horizon=roles_h[k][a]["role"], reasons_horizon=";".join(roles_h[k][a]["reasons"]))
               for k in sorted(roles) for a, v in sorted(roles[k].items())
               if a in roles_h.get(k, {}) and roles_h[k][a]["role"] != v["role"]]
    role_changes = len(rc_rows)
    print(f"  addresses whose role differs between frozen and horizon classification: {role_changes}")
    for r_ in rc_rows:
        print(f"    {r_}")
    write(rc_rows or [dict(fund="", chain="", address="none")], "addl_oos_role_changes.csv")
    c.ev_by = full_ev
    M._label_cache.clear(); M._role_cache.clear(); M._accrual_cache.clear()

    meas, verd = [], []
    for specname, wm in [("full_window_reproduction", wm_full), ("main_frozen_classification", wm_oos),
                         ("info_horizon_2026-07-02", wm_h)]:
        for k in ("m1", "m2", "m2f", "m3", "m4"):
            for r in s(wm[k]):
                if k == "m1" and r["chain_scope"] != "pooled":
                    continue
                if k == "m3" and r["row"] != "M3":
                    continue
                meas.append(dict(spec=specname, measure=k.upper(), **r))
    for specname, vv in [("main_frozen_classification", v_oos), ("info_horizon_2026-07-02", v_h)]:
        for (fx, fund), (v, note, r) in sorted(vv.items()):
            fullv = vfull[(fx, fund)][0]
            row = dict(spec=specname, window=f"{OOSW[0]}:{OOSW[1]}", falsifier=fx, fund=fund, verdict=v,
                       full_window_verdict=fullv, matches_full_window="yes" if v == fullv else "no", note=note)
            if fx == "F1":
                row.update({k: r[k] for k in ("n_events", "lower_share_value_pct", "lower_share_count_pct",
                                              "main_share_value_pct", "main_share_count_pct",
                                              "upper_share_value_pct", "upper_share_count_pct")})
            elif fx == "F2":
                row.update({k: r[k] for k in ("issuance_value", "redemption_value", "nonbusiness_share_pct")})
            elif fx == "F3" and r:
                row.update(snapshot=r["snapshot"], outside_top10_pct=r["nonta_outside_top10_share_pct"],
                           n_nonta_wallets=r["n_nonta_wallets"], worst_case_outside_top10_pct=r["worst_case_outside_top10_pct"],
                           worst_case_verdict=r["worst_case_verdict"], full_window_snapshot="2026-09-30")
            elif fx == "F4":
                row.update({k: r[k] for k in ("n_dividend_days", "n_nonbusiness_dividend_days",
                                              "share_dividend_days_ta_batch_pct", "lag_status", "median_lag_bd_B",
                                              "share_delivered_on_chain_pct")})
            verd.append(row)
            tag = f"{fund}__{fx}__{'main' if specname.startswith('main') else 'horizon'}"
            sn(f"addl_oos_verdict__{tag}", v, "text", f"{OOSW[0]}:{OOSW[1]}", "addl_oos_verdicts.csv")
            sn(f"addl_oos_matches_full__{tag}", row["matches_full_window"], "text", f"{OOSW[0]}:{OOSW[1]}", "addl_oos_verdicts.csv")
            for k in ("n_events", "lower_share_value_pct", "lower_share_count_pct", "upper_share_value_pct",
                      "upper_share_count_pct", "nonbusiness_share_pct", "outside_top10_pct", "worst_case_outside_top10_pct",
                      "n_dividend_days", "n_nonbusiness_dividend_days", "share_dividend_days_ta_batch_pct", "median_lag_bd_B"):
                if k in row and row[k] not in ("", None):
                    sn(f"addl_oos_{k}__{tag}", row[k], "pct" if k.endswith("pct") else "count" if k.startswith("n_") else
                       "business days", f"{OOSW[0]}:{OOSW[1]}" if fx != "F3" else "2026-06-30", "addl_oos_verdicts.csv")
            print(f"  {specname:28} {fx} {fund:6} {v:45} full: {fullv:45} {note}")
    sn("addl_oos_role_changes_horizon_vs_frozen", role_changes, "count (addresses)", f"horizon {HORIZON_DAY}", "addl_oos_verdicts.csv")
    sn("addl_oos_rule_events_before_horizon_filter", n_before, "count", STAG, "addl_oos_verdicts.csv")
    sn("addl_oos_rule_events_after_horizon_filter", n_after, "count", f"<= {HORIZON_DAY} 23:59:59 ET", "addl_oos_verdicts.csv")
    write(meas, "addl_oos_measures.csv")
    write(verd, "addl_oos_verdicts.csv")
    return roles, labels


# ============================================================ Task 2
def nmfp_daily_both():
    cls = {r["ticker"]: r["class_id"] for r in rd("freeze_nmfp_filings.csv")}   # as 05_measures.load
    subs, reds = defaultdict(dict), defaultdict(dict)
    for r in rd("freeze_nmfp_daily.csv"):
        if r["governing"] != "1" or r["class_id"] != cls[r["ticker_file"]]:
            continue
        d = dt.date.fromisoformat(r["date"])
        if r["gross_subs"]:
            subs[r["ticker_file"]][d] = float(r["gross_subs"])
        if r["gross_reds"]:
            reds[r["ticker_file"]][d] = float(r["gross_reds"])
    return subs, reds


def tol_ok(nm, ch):
    return abs(nm - ch) <= max(1.0, 0.001 * nm)       # frozen M2 flow-match tolerance (05_measures.py)


def task2(c, roles, labels):
    print("\n=== Task 2: ordering (chain events vs N-MFP3 trade date) ===")
    subs, reds = nmfp_daily_both()
    # check: the subscription series equals the one 05 used
    assert all(subs[f] == dict(c.subs[f]) for f in FUNDS), "N-MFP3 subscription series differs from 05_measures.load"
    lo_t, hi_t = R.cutoff(FULL[0] - dt.timedelta(days=1)), R.cutoff(FULL[1])
    days_rows, summ, hours = [], [], []
    LAGS = [0, 1, -1, 2, -2, 3, -3]                     # calendar-day offsets, chain date minus N-MFP3 date
    for fund in FUNDS:
        chains = [ch for ch in sorted(c.chains_by_fund[fund]) if c.ev_by.get((fund, ch))]
        mints = []   # issuance mints (TR-6 issuance label), all dates
        burns_raw, burns_tr2 = [], []
        for ch in chains:
            lb = labels[(fund, ch)]
            evs = c.ev_by[(fund, ch)]
            parked = R.tr2_parked_lots(evs, M.burn_wallets(c, roles, fund, ch), c.cal) if fund in M.TR2_FUNDS else []
            pb = defaultdict(list)
            for p_ in parked:
                pb[p_["burn_ts"]].append(p_)
            for e in evs:
                if e["kind"] == "mint" and lb[e["id"]][0] == "issuance":
                    mints.append(dict(ts=e["ts"], amount=e["amount"], chain=ch))
                elif e["kind"] == "burn":
                    burns_raw.append(dict(ts=e["ts"], amount=e["amount"], chain=ch))
                    pieces = [(p_["arrival_ts"], p_["amount"]) for p_ in pb.get(e["ts"], [])]
                    rest = e["amount"] - sum(x for _, x in pieces)
                    if rest > 1e-9:
                        pieces.append((e["ts"], rest))
                    burns_tr2 += [dict(ts=t_, amount=a_, chain=ch, burn_ts=e["ts"]) for t_, a_ in pieces]
        for leg, nm, evlist in [("subscription_vs_issuance_mint", subs[fund], mints),
                                ("redemption_vs_burn_raw_date", reds[fund], burns_raw)] + \
                               ([("redemption_vs_burn_TR2_arrival_date", reds[fund], burns_tr2)] if fund in M.TR2_FUNDS else []):
            by_day = defaultdict(float)
            ev_by_day = defaultdict(list)
            for e in evlist:
                d = R.et_date(e["ts"])
                by_day[d] += e["amount"]
                ev_by_day[d].append(e)
            nm_days = sorted(d for d in nm if FULL[0] <= d <= FULL[1])
            # filter, printed: only N-MFP3 days with a positive gross flow carry a trade date to order
            pos = [d for d in nm_days if nm[d] > 0]
            print(f"  {fund} {leg}: N-MFP3 days in window {len(nm_days)}, with flow > 0: {len(pos)}")
            first_lag = Counter()
            for d in pos:
                hits = [k for k in LAGS if by_day.get(d + dt.timedelta(days=k), 0.0) > 0
                        and tol_ok(nm[d], by_day[d + dt.timedelta(days=k)])]
                fl_ = hits[0] if hits else None                 # lag 0 first, then smallest |k|, + before -
                first_lag["none" if fl_ is None else fl_] += 1
                days_rows.append(dict(fund=fund, leg=leg, nmfp_date=d, nmfp_amount=nm[d],
                                      chain_amount_same_day=round(by_day.get(d, 0.0), 6),
                                      matching_lags_calendar_days=";".join(str(k) for k in hits),
                                      first_match_lag=("" if fl_ is None else fl_),
                                      bd_lag_of_first_match=("" if fl_ is None else
                                                             c.cal.business_days_after(d, d + dt.timedelta(days=fl_)) if fl_ >= 0
                                                             else -c.cal.business_days_after(d + dt.timedelta(days=fl_), d)),
                                      n_chain_events_same_day=len(ev_by_day.get(d, []))))
                if fl_ == 0:
                    for e in ev_by_day[d]:
                        hours.append(dict(fund=fund, leg=leg, nmfp_date=d, chain=e["chain"],
                                          ts_utc=e["ts"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                                          ts_et=e["ts"].astimezone(R.ET).strftime("%Y-%m-%dT%H:%M:%S"),
                                          hour_et=R.et_hour(e["ts"]), amount=e["amount"],
                                          burn_ts_utc=e["burn_ts"].strftime("%Y-%m-%dT%H:%M:%SZ") if "burn_ts" in e else ""))
            n = len(pos)
            matched0 = first_lag[0]
            later = sum(v for k, v in first_lag.items() if k != "none" and k > 0)
            earlier = sum(v for k, v in first_lag.items() if k != "none" and k < 0)
            hh = [h for h in hours if h["fund"] == fund and h["leg"] == leg]
            hc = Counter(h["hour_et"] for h in hh)
            hv = defaultdict(float)
            for h in hh:
                hv[h["hour_et"]] += h["amount"]
            tot_v = sum(hv.values())
            bands = {"00-08": range(0, 9), "09-11": range(9, 12), "12-15": range(12, 16), "16-23": range(16, 24)}
            row = dict(fund=fund, leg=leg, window=f"{FULL[0]}:{FULL[1]}", nmfp_days_in_window=len(nm_days),
                       nmfp_days_flow_positive=n, matched_same_date=matched0, matched_same_date_pct=pct(matched0, n),
                       matched_chain_later_only=later, matched_chain_later_pct=pct(later, n),
                       matched_chain_earlier_only=earlier, matched_chain_earlier_pct=pct(earlier, n),
                       unmatched=first_lag["none"], unmatched_pct=pct(first_lag["none"], n),
                       first_match_lag_counts=";".join(f"{k}:{v}" for k, v in sorted(first_lag.items(), key=lambda kv: str(kv[0]))),
                       n_events_on_same_date_matches=len(hh),
                       hour_counts_et=";".join(f"{h}:{hc[h]}" for h in sorted(hc)),
                       modal_hour_et=(hc.most_common(1)[0][0] if hc else ""),
                       **{f"count_share_hours_{b}_pct": pct(sum(hc[h] for h in rg), len(hh)) for b, rg in bands.items()},
                       **{f"value_share_hours_{b}_pct": pct(sum(hv[h] for h in rg), tot_v) for b, rg in bands.items()})
            summ.append(row)
            print(f"    same date {matched0}/{n}; chain later only {later}; chain earlier only {earlier}; unmatched "
                  f"{first_lag['none']}; lags {row['first_match_lag_counts']}; hours {row['hour_counts_et']}")
            tag = f"{fund}__{leg}"
            for k in ("nmfp_days_flow_positive", "matched_same_date", "matched_same_date_pct", "matched_chain_later_only",
                      "matched_chain_earlier_only", "unmatched", "n_events_on_same_date_matches", "modal_hour_et",
                      "count_share_hours_00-08_pct", "count_share_hours_09-11_pct", "count_share_hours_12-15_pct",
                      "count_share_hours_16-23_pct", "value_share_hours_16-23_pct", "hour_counts_et"):
                sn(f"addl_ordering_{k}__{tag}", row[k], "pct" if k.endswith("pct") else "hour ET" if k == "modal_hour_et"
                   else "text (hour:count)" if k == "hour_counts_et" else "count", f"{FULL[0]}:{FULL[1]}", "addl_ordering_summary.csv")
    write(days_rows, "addl_ordering_days.csv")
    write(summ, "addl_ordering_summary.csv")
    write(hours, "addl_ordering_hours.csv")


# ============================================================ Task 3
def task3():
    print("\n=== Task 3: MultiliquidSwap cash legs ===")
    sf = rd("addl_signer_function.csv")
    mls = [r for r in sf if r["fund"] == "WTGXX" and "MultiliquidSwap" in (r["tx_to_contract"] or "")]
    hs = {r["tx"].lower() for r in mls}
    print(f"  WTGXX non-TA transfers routed via MultiliquidSwap: {len(mls)} rows, {len(hs)} distinct tx")
    # every raw file that could hold logs or receipts: frozen EVM transfer logs, frozen RPC call log, addition calls
    found = defaultdict(lambda: dict(logs_total=0, logs_non_wtgxx=0, receipts=0, tx_by_hash=0, calldata=""))
    files = sorted((RAW / "onchain" / STAG).glob("*.jsonl.gz")) + [RAW / "addition_2026-10-17" / "evm_calls.jsonl"]
    methods = Counter()
    for p in files:
        op = gzip.open if p.suffix == ".gz" else open
        with op(p, "rt", encoding="utf-8") as f:
            for line in f:
                low = line.lower()
                if not any(h in low for h in hs):
                    continue
                o = json.loads(line)
                m = o.get("method", "log" if "topics" in o else "?")
                methods[(p.name, m)] += 1
                h = next(h for h in hs if h in low)
                if "topics" in o:
                    found[h]["logs_total"] += 1
                    if o.get("address", "").lower() != WTGXX_ETH:
                        found[h]["logs_non_wtgxx"] += 1
                if "receipt" in m.lower():
                    found[h]["receipts"] += 1
                if m == "eth_getTransactionByHash":
                    found[h]["tx_by_hash"] += 1
                    resp = o.get("response") or o.get("result") or {}
                    res = resp.get("result", resp) if isinstance(resp, dict) else {}
                    found[h]["calldata"] = (res or {}).get("input", "") if isinstance(res, dict) else ""
    for k, v in sorted(methods.items()):
        print(f"  lines mentioning these tx: {k[0]} [{k[1]}]: {v}")
    rows = []
    for r in mls:
        h = r["tx"].lower()
        x = found[h]
        cd = x["calldata"].lower()
        rows.append(dict(tx=r["tx"], ts_utc=r["ts_utc"], frm=r["frm"], to=r["to"], wtgxx_amount=r["amount"], selector=r["selector"],
                         wtgxx_logs_held=x["logs_total"] - x["logs_non_wtgxx"], other_token_logs_held=x["logs_non_wtgxx"],
                         receipts_held=x["receipts"], tx_object_held=x["tx_by_hash"],
                         calldata_mentions_usdc=int(STABLES["USDC"] in cd), calldata_mentions_usdt=int(STABLES["USDT"] in cd),
                         cash_leg_status=("decoded" if x["logs_non_wtgxx"] or x["receipts"] else "not decoded; receipts not held")))
    write(rows, "addl_mls_cashleg.csv")
    n_cash = sum(1 for r in rows if r["cash_leg_status"] == "decoded")
    sn("addl_mls_n_transfers", len(rows), "count", "2026-01-01:2026-09-30", "addl_mls_cashleg.csv")
    sn("addl_mls_wtgxx_value_shares", round(sum(float(r["wtgxx_amount"]) for r in rows), 6), "shares", "2026-01-01:2026-09-30", "addl_mls_cashleg.csv")
    sn("addl_mls_n_with_receipt_or_other_token_log_held", n_cash, "count", STAG, "addl_mls_cashleg.csv")
    sn("addl_mls_cash_leg_status", "not decoded; receipts not held" if n_cash == 0 else f"{n_cash} decoded", "text", STAG, "addl_mls_cashleg.csv")
    sn("addl_mls_n_calldata_mentions_usdc", sum(r["calldata_mentions_usdc"] for r in rows), "count (tx; calldata word only, not a transfer)",
       STAG, "addl_mls_cashleg.csv")
    print(f"  cash legs decodable from held data: {n_cash} of {len(rows)}; calldata mentioning the USDC address: "
          f"{sum(r['calldata_mentions_usdc'] for r in rows)}; USDT: {sum(r['calldata_mentions_usdt'] for r in rows)}")


# ============================================================ Task 4
def task4():
    print("\n=== Task 4: WTGXX F1 count shares, minimum size 100 shares ===")
    ev = [r for r in rd("addl_f1_events.csv") if r["fund"] == "WTGXX"]
    # frozen F1 conventions (05_measures.window_measures, pooled, transfers_only): denominator = every transfer event in the
    # window on WTGXX event chains, whatever the roles; lower numerator = holder-holder; upper numerator = no TA-role party
    print(f"  WTGXX transfer events (frozen F1 denominator): {len(ev)}")
    rows = []

    def shares(sample, num_excl=lambda e: False):
        n = len(sample)
        v = sum(float(e["amount"]) for e in sample)
        lo = [e for e in sample if e["pair"] == "hh" and not num_excl(e)]
        up = [e for e in sample if e["nonta"] == "1" and not num_excl(e)]
        return dict(n_denominator=n, value_denominator=round(v, 6), n_lower=len(lo), n_upper=len(up),
                    lower_share_count_pct=pct(len(lo), n), upper_share_count_pct=pct(len(up), n),
                    lower_share_value_pct=pct(sum(float(e["amount"]) for e in lo), v),
                    upper_share_value_pct=pct(sum(float(e["amount"]) for e in up), v))
    wal = lambda e: WALLET in (e["frm"].lower(), e["to"].lower())
    big = [e for e in ev if float(e["amount"]) >= 100]
    print(f"  filter amount >= 100 shares (numerator and denominator): {len(ev)} -> {len(big)} events")
    specs = [("all_sizes", "frozen", ev, lambda e: False),
             ("all_sizes", "excl_wallet_sample", [e for e in ev if not wal(e)], lambda e: False),
             ("all_sizes", "excl_wallet_numerator", ev, wal),
             ("min_100_shares", "with_wallet", big, lambda e: False),
             ("min_100_shares", "excl_wallet_sample", [e for e in big if not wal(e)], lambda e: False),
             ("min_100_shares", "excl_wallet_numerator", big, wal)]
    for size, wmode, sample, ex in specs:
        r = dict(fund="WTGXX", size_filter=size, wallet_mode=wmode, **shares(sample, ex))
        rows.append(r)
        print(f"  {size:15} {wmode:22} {r}")
        for k in ("n_denominator", "n_lower", "n_upper", "lower_share_count_pct", "upper_share_count_pct",
                  "lower_share_value_pct", "upper_share_value_pct"):
            sn(f"addl_f1minsize_{k}__WTGXX__{size}__{wmode}", r[k], "pct" if k.endswith("pct") else "count",
               "2026-01-01:2026-09-30", "addl_f1_minsize.csv")
    chk = rows[0]
    frz = next(x for x in rd("freeze_m1_secondary.csv") if x["fund"] == "WTGXX" and x["chain_scope"] == "pooled" and x["basis"] == "transfers_only")
    assert str(chk["lower_share_count_pct"]) == frz["lower_share_count_pct"] and str(chk["upper_share_count_pct"]) == frz["upper_share_count_pct"]
    # size distribution of the lower-bound (hh) transfers without the wallet
    rest = sorted(float(e["amount"]) for e in ev if e["pair"] == "hh" and not wal(e))
    dist = dict(fund="WTGXX", size_filter="distribution", wallet_mode="hh_excl_wallet", n_lower=len(rest),
                n_under_1=sum(x < 1 for x in rest), n_under_20=sum(x < 20 for x in rest), n_under_100=sum(x < 100 for x in rest),
                n_ge_100=sum(x >= 100 for x in rest), total_shares=round(sum(rest), 6),
                median_shares=(rest[len(rest) // 2] if len(rest) % 2 else (rest[len(rest) // 2 - 1] + rest[len(rest) // 2]) / 2) if rest else "",
                max_shares=max(rest) if rest else "")
    rows.append(dist)
    print(f"  hh excl wallet: {dist}")
    for k in ("n_lower", "n_under_1", "n_under_20", "n_under_100", "n_ge_100", "total_shares", "median_shares", "max_shares"):
        sn(f"addl_f1minsize_hh_excl_wallet_{k}__WTGXX", dist[k], "shares" if "shares" in k else "count", "2026-01-01:2026-09-30",
           "addl_f1_minsize.csv")
    write(rows, "addl_f1_minsize.csv")


def main():
    task3()
    task4()
    a = argparse.Namespace(prefix="freeze", stellar_tag=STAG, stellar_rules_start="2026-01-01")
    c = M.load(a)
    roles, labels = task1(c)
    task2(c, roles, labels)
    names = Counter(r["name"] for r in SN)
    assert not [n for n, k in names.items() if k > 1], "duplicate names"
    assert all(r["name"].startswith("addl_") for r in SN)
    write(SN, "addl_summary_numbers_18.csv")


if __name__ == "__main__":
    main()
