"""05_measures.py -- pre-registered measures M0-M4 (notes/preregistration.md §4.4)

Inputs (all produced by scripts 00-03e with the same --prefix / tags):
  data/processed/ref_fedwire_holidays.csv                     (00, TR-4)
  data/processed/<prefix>_nmfp_summary.csv, _nmfp_daily.csv   (01, 02; governing versions only)
  data/processed/<prefix>_evm_events.csv, _supply_snapshots_evm.csv, _evm_codes.csv,
                 _evm_burn_senders.csv                         (03a)
  data/processed/<prefix>_solana_events.csv (03c), _aptos_events.csv (03e; optional: without it,
                 every FOBXX month is coverage-incomplete, IMPL-18)
  data/raw/stellar/<stellar-tag>/                             (03b, raw Horizon pages)
  data/raw/issuer/ (addresses and Franklin module contracts, parsed as in 03)
Rules: scripts/rules.py (TR-1..TR-6). Parameters: the final implementation in
       notes/preregistration_implementation_notes.md, section "Final implementation after audit
       (approved by user 2026-10-16, CP11)". MAIN below is that specification; SENSITIVITIES are
       the variants required by reviews/impl_audit.md (summary table and C1/C2).

Outputs (data/processed/<prefix>_*.csv, or --out-dir):
  m0_reconciliation   in-sample fund-months (the calibration window), values shown
  m0_oos_codepath     out-of-sample months: computed yes/no and coverage status ONLY, unless
                      --reveal-oos (refused unless --prefix freeze). With --reveal-oos the gap under
                      both TR-3 scopes and the F0 month status (rules.f0_month_status, incl. the
                      one-sided test for coverage-incomplete months, CP11) are written. Every M0 row
                      carries f0_counting_row: F0 is counted on ET rows only (rules.f0_count);
                      UTC rows are the TR-1 sensitivity.
  m1_secondary (pooled rows + one row per chain: audit F1 pooling / IMPL-1 Solana), m2_calendar,
  m2_flow_match, m3_holding, m4_servicing (IMPL-14 B lag + TA-batch share; variant A lag as a
  sensitivity column), wallet_roles, mint_labels, measures_status, diagnostics
  with --sensitivities: sens_m0, sens_m1, sens_m2, sens_m3, sens_m4, sens_labels, sens_roles
  (one block of rows per variant; OOS M0 values only under --reveal-oos).
No falsifier verdicts are computed here.

Stellar: the crawl may start before the analysis window (raw freeze crawls as far back as Horizon
serves). Events from the crawl START feed the supply back-out (M0) only. TR-5 roles and TR-6 labels
use Stellar events from --stellar-rules-start (default 2026-01-01, IMPL-1b/IMPL-21), with balances
seeded at that instant by back-out.

Run (prep code-path test on the pilot window):
  python -I scripts/05_measures.py --prefix prep --stellar-tag prep_2026-10-16 \
      --window 2026-07-01:2026-09-30 --insample 2026-07-31,2026-08-31,2026-09-30 --oos 2026-06-30 \
      [--sensitivities] [--out-dir <dir>]
"""
import argparse
import csv
import datetime as dt
import importlib.util
import re
import statistics
import sys
from bisect import bisect_right
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import rules as R            # noqa: E402
import stellar_events as SE  # noqa: E402

spec = importlib.util.spec_from_file_location("pilot03", ROOT / "scripts" / "03_onchain_pilot.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)
PROC = ROOT / "data" / "processed"
OUT = PROC
TR2_FUNDS = {"JLTXX", "FYOXX"}          # TR-2: burn wallet holds no persistent balance
LOW_MIN = {"FOBXX", "WTGXX"}            # M3c low-minimum funds
SINGLE_CHAIN = {"JLTXX", "FYOXX"}       # IMPL-10: TR-6(iii) only where N-MFP3 flows = one chain
FUNDS = ["FOBXX", "WTGXX", "JLTXX", "FYOXX"]
NON_EVM = ("stellar", "solana", "aptos")

# Final specification (audit fixes approved by the user 2026-10-16, CP11)
MAIN = dict(burn_origin="tx_sender",        # IMPL-5
            tr3_scope="iii",                # IMPL-4b
            hop_trigger="issuance",         # audit C1: only issuance mints trigger the hop leg
            hop_excl=True,                  # IMPL-7b
            rate_cap_k=5.0, rate_cap_mode="flat",   # IMPL-8c, k=5 (audit)
            outlier_k=None,                 # IMPL-8b dropped (audit)
            iii=True, iii_mode="first_bd_of_month", sub_tol=0.01,   # IMPL-10
            stellar_burn_leg=False,         # IMPL-5 (leg off on Stellar)
            stellar_seed=True,              # IMPL-1b
            ta_batch_span_s=None)           # IMPL-14 TA batch = TA mint source only (verification 4c; CP15)
# (name, overrides, audit item). Every variant changes ONE parameter against MAIN.
SENSITIVITIES = [
    ("burn_origin_transfer_from", dict(burn_origin="transfer_from"), "IMPL-5 (M1, M3a, M3c)"),
    ("stellar_burner_leg_on", dict(stellar_burn_leg=True), "IMPL-5 Stellar literal (diagnostic)"),
    ("hop_incl_redemptions", dict(hop_excl=False), "IMPL-7b"),
    ("hop_trigger_all_mints", dict(hop_trigger="all"), "C1 pre-audit reading"),
    ("hop_leg_off", dict(hop_trigger="off"), "C1 hop leg off"),
    ("outlier_k3", dict(outlier_k=3.0), "IMPL-8b robustness"),
    ("rate_cap_k3", dict(rate_cap_k=3.0), "IMPL-8c k=3"),
    ("rate_cap_k10", dict(rate_cap_k=10.0), "IMPL-8c k=10"),
    ("rate_cap_off", dict(rate_cap_k=None), "IMPL-8c no cap"),
    ("rate_cap_day_aware", dict(rate_cap_mode="day_aware"), "IMPL-8c day-aware cap (1.25)"),
    ("iii_off", dict(iii=False), "IMPL-10 rule (iii) off (FDIT)"),
    ("iii_sub_tol_1", dict(sub_tol=1.0), "IMPL-10 tolerance 1 share"),
    ("iii_sub_tol_100", dict(sub_tol=100.0), "IMPL-10 tolerance 100 shares"),
    ("iii_monthly_accrual", dict(iii_mode="monthly_accrual"), "IMPL-10 monthly-accrual variant"),
    ("stellar_unseeded", dict(stellar_seed=False), "IMPL-1b unseeded (diagnostic)"),
    ("ta_batch_span_1h", dict(ta_batch_span_s=3600), "IMPL-14 TA batch: TA source and 1-hour span (pre-CP15 rule)"),
    ("ta_batch_span_3h", dict(ta_batch_span_s=10800), "IMPL-14 TA batch: TA source and 3-hour span"),
]


def rd(name):
    with open(PROC / name, newline="") as f:
        return list(csv.DictReader(f))


def write(rows, name):
    if not rows:
        print(f"  (no rows for {name})")
        return
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with open(OUT / name, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"  wrote {name}: {len(rows)} rows")


def ts(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def franklin_modules():
    import source_facts
    return source_facts.franklin_modules()


def _original_franklin_modules():
    """Module contracts per chain from the raw Franklin contracts page (TR-5 'module' leg)."""
    html = (ROOT / "data/raw/issuer/2026-10-16_franklin_benji_contracts.html").read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"<[^>]+>", "\n", re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.S))
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    mods, chain = defaultdict(set), None
    for i, l in enumerate(lines):
        m = re.fullmatch(r"BENJI \(([^)]+)\)", l)
        if m:
            chain = m.group(1).lower()
            continue
        if re.fullmatch(r"[a-zA-Z]+BENJI \(([^)]+)\)", l):
            chain = None            # other products' blocks
            continue
        if chain and l.endswith("Module") and i + 1 < len(lines) and lines[i + 1].startswith("0x"):
            mods[chain].add(lines[i + 1].lower())
    return mods


def pct(a, b):
    return round(100 * a / b, 4) if b else ""


# ====================================================================== inputs
def load(a):
    c = argparse.Namespace()
    c.cal = R.BusinessCalendar.from_csv(outside="weekends")      # audit TR-4 guard (dates < 2024)
    toks = P.issuer_addresses()
    filings = rd(f"{a.prefix}_nmfp_filings.csv")
    cls = {r["ticker"]: r["class_id"] for r in filings}
    summ = [r for r in rd(f"{a.prefix}_nmfp_summary.csv") if r["governing"] == "1"]
    c.reg = {(r["ticker_file"], r["report_date"]): r for r in summ if r["class_id"] == cls[r["ticker_file"]]}
    c.report_dates = defaultdict(list)
    for (f_, d_) in c.reg:
        c.report_dates[f_].append(dt.date.fromisoformat(d_))
    daily = [r for r in rd(f"{a.prefix}_nmfp_daily.csv") if r["governing"] == "1"]
    c.yields, c.subs = defaultdict(dict), defaultdict(dict)
    for r in daily:
        if r["class_id"] != cls[r["ticker_file"]]:
            continue
        d = dt.date.fromisoformat(r["date"])
        if r["class_7d_net_yield"]:
            c.yields[r["ticker_file"]][d] = float(r["class_7d_net_yield"])
        if r["gross_subs"]:
            c.subs[r["ticker_file"]][d] = float(r["gross_subs"])

    c.ev_by = defaultdict(list)           # (fund, chain) -> events used by TR-5/TR-6 and M1-M4
    ev_rows = rd(f"{a.prefix}_evm_events.csv")
    c.full_history_chains = set()         # chains whose events start at creation (supply = mints - burns)
    for ch_ in ("solana", "aptos"):       # 03c and 03e: full history from creation, same columns
        if (PROC / f"{a.prefix}_{ch_}_events.csv").exists():
            rows_ = rd(f"{a.prefix}_{ch_}_events.csv")
            ev_rows += rows_
            c.full_history_chains |= {(r["fund"], ch_) for r in rows_}
        else:
            print(f"  no {a.prefix}_{ch_}_events.csv: {ch_} months stay coverage-incomplete (IMPL-18)")
    for r in ev_rows:
        c.ev_by[(r["fund"], r["chain"])].append(dict(id=r["id"], ts=ts(r["ts_utc"]), frm=r["frm"], to=r["to"],
                                                     amount=float(r["amount"]), kind=r["kind"], tx=r["tx"]))
    c.codes = {(r["chain"], r["address"]): r["has_code"] == "1" for r in rd(f"{a.prefix}_evm_codes.csv")}
    c.senders = {r["tx"]: r["tx_from"] for r in rd(f"{a.prefix}_evm_burn_senders.csv") if r["tx_from"]}
    c.snaps = defaultdict(dict)           # (fund, date, clock) -> chain -> (value, source)
    for r in rd(f"{a.prefix}_supply_snapshots_evm.csv"):
        v = r["supply_call"] or r["supply_events"]
        if v:
            c.snaps[(r["fund"], r["report_date"], r["clock"])][r["chain"]] = (
                float(v), "totalSupply@block" if r["supply_call"] else "events")
    c.diag = []
    c.stellar = {}
    if a.stellar_tag:
        sdir = ROOT / "data" / "raw" / "stellar" / a.stellar_tag
        rules_start_cfg = dt.datetime.combine(dt.date.fromisoformat(a.stellar_rules_start), dt.time(0),
                                              R.ET).astimezone(R.UTC)
        for t in toks:
            if t["chain"] != "stellar":
                continue
            evs, sdiag = SE.load(sdir, t["token"], t["address"])
            start = SE.crawl_start(sdir)
            n0 = len(evs)
            evs = [e for e in evs if e["ts"] >= start]   # pre-START events are partial (holder pages only)
            rstart = max(start, rules_start_cfg)
            rule_evs = [e for e in evs if e["ts"] >= rstart]
            sup_now, t_now, _ = SE.asset_snapshot(sdir, t["token"])
            bal_now, t_bal = SE.trustline_balances(sdir, t["token"], t["address"])
            init = SE.balances_backout(evs, bal_now, t_bal, rstart - dt.timedelta(seconds=1), t["address"])
            neg = {k: v for k, v in init.items() if v < -1e-7}
            c.stellar[t["fund"]] = dict(issuer=t["address"], events=evs, sup_now=sup_now, t_now=t_now,
                                        bal_now=bal_now, t_bal=t_bal, earliest=start,
                                        init_bal={k: v for k, v in init.items() if v > 0})
            c.ev_by[(t["fund"], "stellar")] = rule_evs
            print(f"Stellar {t['token']}: crawl START {start:%Y-%m-%dT%H:%M:%SZ}; rules start {rstart:%Y-%m-%dT%H:%M:%SZ}; "
                  f"{len(evs)} events (supply back-out), {len(rule_evs)} for TR-5/TR-6; diag {dict(sdiag)}")
            for k, v in [("stellar_events_dropped_before_crawl_start", n0 - len(evs)),
                         ("stellar_events_supply_only_before_rules_start", len(evs) - len(rule_evs)),
                         ("stellar_negative_backout_balances_at_rules_start", len(neg)),
                         ("stellar_negative_backout_balance_sum", round(sum(neg.values()), 7))] + \
                    [(f"stellar_ops:{k_}", v_) for k_, v_ in sorted(sdiag.items())]:
                c.diag.append(dict(item=k, fund=t["fund"], chain="stellar", value=v,
                                   note=f"crawl START {start:%Y-%m-%d}; rules start {rstart:%Y-%m-%d}"))
    c.chains_by_fund = defaultdict(set)
    for t in toks:
        c.chains_by_fund[t["fund"]].add(t["chain"])
    c.mods = franklin_modules()
    for (fund, ch), evs in sorted(c.ev_by.items()):
        if ch in NON_EVM:
            continue
        burns = [e for e in evs if e["kind"] == "burn"]
        c.diag.append(dict(item="evm_burns", fund=fund, chain=ch, value=len(burns), note=""))
        c.diag.append(dict(item="evm_burns_without_recorded_tx_sender", fund=fund, chain=ch,
                           value=sum(1 for e in burns if e["tx"] not in c.senders),
                           note="IMPL-5: no burner added for these under tx_sender"))
    return c


# ====================================================================== TR-6 then TR-5 (audit C1 order)
_label_cache, _role_cache = {}, {}


def classify(c, p):
    labels, roles = {}, {}
    for (fund, chain), evs in sorted(c.ev_by.items()):
        if not evs:
            continue
        is_st = chain == "stellar"
        src = c.stellar[fund]["issuer"] if is_st else R.ZERO
        lkey = (fund, chain, p["rate_cap_k"], p["rate_cap_mode"], p["outlier_k"], p["iii"], p["iii_mode"],
                p["sub_tol"], p["stellar_seed"] if is_st else None)
        if lkey not in _label_cache:
            _label_cache[lkey] = R.tr6_classify(
                evs, R.YieldSeries(c.yields[fund]), mint_src=src, burn_dst=src, cal=c.cal,
                initial_bal=c.stellar[fund]["init_bal"] if (is_st and p["stellar_seed"]) else None,
                subs_by_date=c.subs[fund] if (fund in SINGLE_CHAIN and p["iii"]) else None,
                iii_mode=p["iii_mode"], sub_tol=p["sub_tol"], outlier_k=p["outlier_k"],
                rate_cap_k=p["rate_cap_k"], rate_cap_mode=p["rate_cap_mode"])
        labels[(fund, chain)] = lb = _label_cache[lkey]
        leg = chain not in NON_EVM or (is_st and p["stellar_burn_leg"])   # IMPL-5 / IMPL-1; Aptos as Solana
        origin = "transfer_from" if is_st else p["burn_origin"]           # Stellar literal = the payer
        rkey = (fund, chain, origin, leg, p["hop_trigger"], p["hop_excl"], lkey if p["hop_trigger"] == "issuance" else None)
        if rkey not in _role_cache:
            _role_cache[rkey] = R.tr5_roles(
                evs, c.cal, mint_src=src, burn_dst=src,
                module_contracts=c.mods.get(chain, set()) if fund == "FOBXX" else set(),
                has_code=(lambda ad: ad.startswith("C")) if is_st else (lambda ad, ch=chain: c.codes.get((ch, ad), False)),
                burn_origin=origin, tx_sender=c.senders, burn_origin_leg=leg,
                hop_exclude_redemptions=p["hop_excl"], mint_labels=lb, hop_trigger=p["hop_trigger"])
        roles[(fund, chain)] = _role_cache[rkey]
    return roles, labels


def divmints(c, labels, fund):
    out = []
    for ch in c.chains_by_fund[fund]:
        for e in c.ev_by.get((fund, ch), []):
            if e["kind"] == "mint" and labels[(fund, ch)][e["id"]][0] == "dividend":
                out.append(dict(e, rule=labels[(fund, ch)][e["id"]][1], chain=ch))
    return out


def burn_wallets(c, roles, fund, ch):
    return [ad for ad in {e["frm"] for e in c.ev_by[(fund, ch)] if e["kind"] == "burn"}
            if roles[(fund, ch)].get(ad, {}).get("role") == "TA" and ad != R.ZERO]


# ====================================================================== M0
_raw_cache = {}


def raw_supply(c, fund, d, clock):
    """Supply per chain at the TR-1 cutoff (parameter-independent; cached)."""
    key = (fund, d, clock)
    if key not in _raw_cache:
        cut = R.cutoff(d, clock)
        vals = dict(c.snaps.get((fund, d.isoformat(), clock), {}))
        for (f_, ch_) in c.full_history_chains:
            if f_ == fund and ch_ not in vals:
                vals[ch_] = (R.supply_at(c.ev_by[(fund, ch_)], cut), "events_full_history")
        if fund in c.stellar:
            st = c.stellar[fund]
            if st["earliest"] <= cut <= st["t_now"]:
                vals["stellar"] = (SE.supply_backout(st["events"], st["sup_now"], st["t_now"], cut), "horizon_backout")
        _raw_cache[key] = vals
    return _raw_cache[key]


def m0_rows(c, a, p, roles, labels, insample, oos):
    m0, m0oos = [], []
    for fund in FUNDS:
        parked = []
        if fund in TR2_FUNDS:
            for ch in c.chains_by_fund[fund]:
                if (fund, ch) in roles:
                    parked += R.tr2_parked_lots(c.ev_by[(fund, ch)], burn_wallets(c, roles, fund, ch), c.cal)
        dms = divmints(c, labels, fund)
        for d in insample + oos:
            g = c.reg.get((fund, d.isoformat()))
            sh = float(g["class_shares_outstanding"]) if g and g["class_shares_outstanding"] else None
            for clock in ("ET", "UTC"):
                cut = R.cutoff(d, clock)
                vals = raw_supply(c, fund, d, clock)
                missing = sorted(c.chains_by_fund[fund] - set(vals))
                computed = g is not None and bool(vals)
                complete = computed and not missing
                raw = sum(v for v, _ in vals.values())
                tr2 = R.tr2_adjustment(parked, cut) if fund in TR2_FUNDS else 0.0
                tr3_all = R.tr3_adjustment(dms, d, c.cal, cut)
                tr3_iii = R.tr3_adjustment(dms, d, c.cal, cut, rules_in={"iii"})
                tr3 = tr3_all if p["tr3_scope"] == "all" else tr3_iii      # IMPL-4b
                adj = raw - tr2 + tr3
                gap = pct(adj - sh, sh) if sh else ""
                gap_all = pct(raw - tr2 + tr3_all - sh, sh) if sh else ""
                cov = "complete" if complete else "coverage-incomplete"
                # verification item 6: F0 is counted on ET rows only (rules.f0_count); UTC = TR-1 sensitivity
                f0_row = "yes" if clock == R.F0_CLOCK else "no (TR-1 UTC sensitivity)"
                if d in insample:
                    no_event_chains = sorted(ch for ch in vals if (fund, ch) not in c.ev_by)
                    m0.append(dict(fund=fund, report_date=d, clock=clock, cutoff_utc=cut.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                   K_label="K1", nmfp_class_shares=sh, onchain_supply_raw=round(raw, 6),
                                   tr2_parked_subtracted=round(tr2, 6), tr3_dividends_added=round(tr3, 6),
                                   tr3_scope=p["tr3_scope"], tr3_if_all_dividends=round(tr3_all, 6),
                                   tr3_if_iii_only=round(tr3_iii, 6), gap_pct_tr3_all=gap_all,
                                   gap_pct_tr3_iii=pct(raw - tr2 + tr3_iii - sh, sh) if sh else "",
                                   onchain_supply_adjusted=round(adj, 6),
                                   gap_shares=round(adj - sh, 6) if sh else "", gap_pct=gap,
                                   gap_raw_pct=pct(raw - sh, sh) if sh else "", coverage=cov,
                                   f0_month_status=R.f0_month_status(gap, complete) if computed else "not_computed",
                                   f0_counting_row=f0_row,
                                   chains_missing=";".join(missing),
                                   chains_without_events_TR3_not_applied=";".join(no_event_chains),
                                   chains_included=";".join(f"{ch}[{s}]" for ch, (v, s) in sorted(vals.items()))))
                else:
                    row = dict(fund=fund, report_date=d, clock=clock, register_row=g is not None,
                               computed="yes" if computed else "no", coverage=cov, chains_missing=";".join(missing),
                               f0_counting_row=f0_row)
                    if a.reveal_oos:
                        row.update(gap_pct=gap, gap_pct_tr3_all=gap_all,
                                   gap_pct_tr3_iii=pct(raw - tr2 + tr3_iii - sh, sh) if sh else "",
                                   gap_raw_pct=pct(raw - sh, sh) if sh else "",
                                   f0_month_status=R.f0_month_status(gap, complete) if computed else "not_computed",
                                   f0_month_status_tr3_all=R.f0_month_status(gap_all, complete) if computed else "not_computed")
                    m0oos.append(row)
    return m0, m0oos


# ====================================================================== M1 .. M4
_accrual_cache = {}


def accrual(c, fund, chains, w0, w1):
    """Estimated accrual = sum over days of on-chain supply (event chains) x 7-day net yield / 365."""
    key = (fund, tuple(chains), w0, w1)
    if key not in _accrual_cache:
        y = R.YieldSeries(c.yields[fund])
        tot, d = 0.0, w0
        st = c.stellar.get(fund)
        if st and "stellar" in chains:
            # same quantity as SE.supply_backout (supply now minus net mints in (cut, now]),
            # computed with suffix sums so the daily loop is not quadratic
            sev = sorted((e["ts"], e["amount"] if e["kind"] == "mint" else -e["amount"] if e["kind"] == "burn" else 0.0)
                         for e in st["events"] if e["ts"] <= st["t_now"])
            st_ts = [t_ for t_, _ in sev]
            suffix = [0.0] * (len(sev) + 1)
            for i in range(len(sev) - 1, -1, -1):
                suffix[i] = suffix[i + 1] + sev[i][1]
        while d <= w1:
            cut = R.cutoff(d)
            sup_d = 0.0
            for ch in chains:
                if ch == "stellar":
                    sup_d += st["sup_now"] - suffix[bisect_right(st_ts, cut)]
                else:
                    sup_d += R.supply_at(c.ev_by[(fund, ch)], cut)
            tot += sup_d * (y.at(d) or 0) / 365
            d += dt.timedelta(days=1)
        _accrual_cache[key] = tot
    return _accrual_cache[key]


def window_measures(c, p, roles, labels, w0, w1, qends, window_label, chain_filter=None, do_m3=True):
    lo_t, hi_t = R.cutoff(w0 - dt.timedelta(days=1)), R.cutoff(w1)   # ET days w0..w1
    m1, m2, m2f, m3, m4, status = [], [], [], [], [], []
    for fund in FUNDS:
        chains = sorted(ch for ch in c.chains_by_fund[fund] if chain_filter is None or chain_filter(ch))
        ev_chains = [ch for ch in chains if c.ev_by.get((fund, ch))]
        no_ev = sorted(set(chains) - set(ev_chains))
        # ---- M1 (pooled, and per chain: audit F1 pooling and IMPL-1 Solana contribution)
        agg = Counter()
        for ch in ev_chains:
            rl, lb = roles[(fund, ch)], labels[(fund, ch)]
            for e in c.ev_by[(fund, ch)]:
                if not (lo_t < e["ts"] <= hi_t):
                    continue
                if e["kind"] == "mint" and lb[e["id"]][0] == "dividend":
                    continue
                ra, rb = rl.get(e["frm"], {}).get("role", "holder"), rl.get(e["to"], {}).get("role", "holder")
                pair = "".join(sorted([ra[0], rb[0]]))   # h, c, T
                for basis in ("all_events", "transfers_only"):
                    if basis == "transfers_only" and e["kind"] != "transfer":
                        continue
                    for scope in ("pooled", ch):
                        agg[(scope, basis, "n")] += 1
                        agg[(scope, basis, "v")] += e["amount"]
                        if "T" not in pair:
                            lvl = {"hh": "lower", "ch": "main", "cc": "upper_only"}[pair]
                            agg[(scope, basis, lvl, "n")] += 1
                            agg[(scope, basis, lvl, "v")] += e["amount"]
        for scope in ["pooled"] + ev_chains:
            for basis in ("all_events", "transfers_only"):
                n, v = agg[(scope, basis, "n")], agg[(scope, basis, "v")]
                lower = (agg[(scope, basis, "lower", "n")], agg[(scope, basis, "lower", "v")])
                main_ = (lower[0] + agg[(scope, basis, "main", "n")], lower[1] + agg[(scope, basis, "main", "v")])
                upper = (main_[0] + agg[(scope, basis, "upper_only", "n")], main_[1] + agg[(scope, basis, "upper_only", "v")])
                m1.append(dict(fund=fund, K_label="K2", window=window_label, chain_scope=scope, basis=basis,
                               main_test="yes" if (scope == "pooled" and basis == "transfers_only") else "no",
                               chains_with_events=";".join(ev_chains), chains_without_events=";".join(no_ev),
                               n_events=n, value=round(v, 6),
                               lower_share_value_pct=pct(lower[1], v), lower_share_count_pct=pct(lower[0], n),
                               main_share_value_pct=pct(main_[1], v), main_share_count_pct=pct(main_[0], n),
                               upper_share_value_pct=pct(upper[1], v), upper_share_count_pct=pct(upper[0], n)))
        status.append(dict(measure="M1", fund=fund, computes="yes" if agg[("pooled", "all_events", "n")] else "no",
                           note=f"event chains {ev_chains}; no events on {no_ev}"))
        # ---- M2
        iss_v, red_v, iss_nb, red_nb = 0.0, 0.0, 0.0, 0.0
        burn_hours = Counter()
        iss_by_day = defaultdict(float)
        for ch in ev_chains:
            lb = labels[(fund, ch)]
            parked = R.tr2_parked_lots(c.ev_by[(fund, ch)], burn_wallets(c, roles, fund, ch), c.cal) \
                if fund in TR2_FUNDS else []
            parked_by_burn = defaultdict(list)
            for p_ in parked:
                parked_by_burn[p_["burn_ts"]].append(p_)
            for e in c.ev_by[(fund, ch)]:
                if e["kind"] == "mint" and lb[e["id"]][0] == "issuance" and lo_t < e["ts"] <= hi_t:
                    iss_v += e["amount"]
                    d = R.et_date(e["ts"])
                    iss_by_day[d] += e["amount"]
                    if not c.cal.is_business(d):
                        iss_nb += e["amount"]
                if e["kind"] == "burn":
                    # TR-2 funds: the qualifying parked part is dated at arrival (IMPL-11)
                    pieces = [(p_["arrival_ts"], p_["amount"]) for p_ in parked_by_burn.get(e["ts"], [])]
                    rest = e["amount"] - sum(x for _, x in pieces)
                    if rest > 1e-9:
                        pieces.append((e["ts"], rest))
                    for t_, amt in pieces:
                        if lo_t < t_ <= hi_t:
                            red_v += amt
                            if not c.cal.is_business(R.et_date(t_)):
                                red_nb += amt
                    if lo_t < e["ts"] <= hi_t:
                        burn_hours[R.et_hour(e["ts"])] += 1
        tot = iss_v + red_v
        m2.append(dict(fund=fund, K_label="K3", window=window_label, chains_with_events=";".join(ev_chains),
                       chains_without_events=";".join(no_ev), issuance_value=round(iss_v, 6),
                       redemption_value=round(red_v, 6), nonbusiness_issuance_value=round(iss_nb, 6),
                       nonbusiness_redemption_value=round(red_nb, 6), nonbusiness_share_pct=pct(iss_nb + red_nb, tot),
                       modal_burn_hour_et=burn_hours.most_common(1)[0][0] if burn_hours else "",
                       burn_hour_counts=";".join(f"{h}:{n}" for h, n in sorted(burn_hours.items()))))
        status.append(dict(measure="M2", fund=fund, computes="yes" if tot else "no",
                           note=f"issuance+redemption events found={tot > 0}"))
        days = [d for d in c.subs[fund] if w0 <= d <= w1]
        exact = sum(1 for d in days if abs(c.subs[fund][d] - iss_by_day.get(d, 0.0)) <= max(1.0, 0.001 * c.subs[fund][d]))
        m2f.append(dict(fund=fund, K_label="K3", window=window_label, nmfp_business_days=len(days),
                        days_same_day_match=exact, match_share_pct=pct(exact, len(days)),
                        sum_nmfp_gross_subs=round(sum(c.subs[fund][d] for d in days), 2),
                        sum_issuance_mints_on_those_days=round(sum(iss_by_day.get(d, 0.0) for d in days), 6),
                        note="N-MFP3 gross subscriptions are not ex-dividend (IMPL-13); issuance mints exclude TR-6 dividends; "
                             f"chains without events not counted: {no_ev}"))
        # ---- M3 (quarter-end snapshots)
        for q in (qends if do_m3 else []):
            cut = R.cutoff(q)
            ta_bal, tot_bal, nonta = 0.0, 0.0, []
            for ch in ev_chains:
                rl = roles[(fund, ch)]
                if ch == "stellar":
                    st = c.stellar[fund]
                    bal = SE.balances_backout(st["events"], st["bal_now"], st["t_bal"], cut, st["issuer"])
                    sup_q = SE.supply_backout(st["events"], st["sup_now"], st["t_now"], cut)
                    neg = sum(1 for v in bal.values() if v < -1e-7)
                else:
                    bal = R.balances_at(c.ev_by[(fund, ch)], cut)
                    sup_q = R.supply_at(c.ev_by[(fund, ch)], cut)
                    neg = sum(1 for v in bal.values() if v < -1e-6)   # float residues ~1e-8 ignored
                for ad, v in bal.items():
                    if v <= 1e-9:
                        continue
                    tot_bal += v
                    if rl.get(ad, {}).get("role") == "TA":
                        ta_bal += v
                    else:
                        nonta.append((v, ch, ad))
                m3.append(dict(fund=fund, snapshot=q, chain=ch, row="chain_check",
                               balances_sum=round(sum(v for v in bal.values() if v > 1e-9), 6),
                               supply=round(sup_q, 6), negative_balances=neg))
            nonta.sort(reverse=True)
            s_non = sum(v for v, _, _ in nonta)
            out10 = sum(v for v, _, _ in nonta[10:])
            g = c.reg.get((fund, q.isoformat()))
            m3.append(dict(fund=fund, snapshot=q, chain=";".join(ev_chains), row="M3", K_label="K4",
                           chains_without_events=";".join(no_ev), balances_total=round(tot_bal, 6),
                           ta_share_pct=pct(ta_bal, tot_bal), n_nonta_wallets=len(nonta),
                           nonta_outside_top10_share_pct=pct(out10, s_non) if fund in LOW_MIN else "",
                           nmfp_owner_categories=g["beneficial_record_owner_categories"] if g else ""))
        if do_m3:
            status.append(dict(measure="M3", fund=fund, computes="yes" if qends and ev_chains else "no",
                               note=f"snapshots {qends}; chains without balances {no_ev}"))
        # ---- M4
        dm = [dict(e, chain=ch, rule=labels[(fund, ch)][e["id"]][1]) for ch in ev_chains for e in c.ev_by[(fund, ch)]
              if e["kind"] == "mint" and labels[(fund, ch)][e["id"]][0] == "dividend" and lo_t < e["ts"] <= hi_t]
        div_days = sorted({R.et_date(e["ts"]) for e in dm})
        acc = accrual(c, fund, ev_chains, w0, w1)
        # IMPL-14 B (audit C2): lag only where N-MFP3 dates the accrual (rule-iii mints); the accrual
        # is dated at the N-MFP3 report date d (as TR-3); lag = ET business days in (d, mint date]
        lag_b = [R.m4_lag_from_report_date(R.et_date(e["ts"]), c.report_dates[fund], c.cal) for e in dm if e["rule"] == "iii"]
        lag_b = [x for x in lag_b if x is not None]
        # Variant A (sensitivity): accrual = calendar day before the mint date
        lags_a = [c.cal.business_days_after(R.et_date(e["ts"]) - dt.timedelta(days=1), R.et_date(e["ts"])) for e in dm]
        # TA batch (verification 4c; CP15): main = every chain's dividend mints come from that chain's
        # TA mint source (zero address / Stellar issuer); span variants are sensitivities
        msrc = {ch: (c.stellar[fund]["issuer"] if ch == "stellar" else R.ZERO) for ch in ev_chains}
        tab = R.ta_batch_days(dm, msrc, max_span_s=p.get("ta_batch_span_s"))
        m4.append(dict(fund=fund, K_label="K5", window=window_label, chains_with_events=";".join(ev_chains),
                       chains_without_events=";".join(no_ev), n_dividend_mints=len(dm),
                       dividend_value=round(sum(e["amount"] for e in dm), 6),
                       est_accrual_on_event_chains=round(acc, 6),
                       share_delivered_on_chain_pct=pct(sum(e["amount"] for e in dm), acc),
                       n_dividend_days=len(div_days),
                       n_nonbusiness_dividend_days=sum(1 for x in div_days if not c.cal.is_business(x)),
                       n_dividend_days_ta_batch=sum(1 for x in div_days if tab.get(x)),
                       share_dividend_days_ta_batch_pct=pct(sum(1 for x in div_days if tab.get(x)), len(div_days)),
                       ta_batch_definition=("ta_mint_source" if p.get("ta_batch_span_s") is None
                                            else f"ta_mint_source_and_span_le_{p['ta_batch_span_s']}s"),
                       lag_status="identified (N-MFP3 report date to rule-iii mint)" if lag_b else "not identifiable",
                       n_lag_mints_B=len(lag_b), median_lag_bd_B=statistics.median(lag_b) if lag_b else "",
                       lags_bd_B=";".join(f"{k}:{v}" for k, v in sorted(Counter(lag_b).items())) if lag_b else "",
                       median_lag_bd_variantA=statistics.median(lags_a) if lags_a else "",
                       rules_used=";".join(f"{k}:{v}" for k, v in sorted(Counter(e["rule"] for e in dm).items()))))
        status.append(dict(measure="M4", fund=fund, computes="yes" if ev_chains else "no",
                           note=f"{len(dm)} dividend mints in window; lag {'identified' if lag_b else 'not identifiable'}"))
    return dict(m1=m1, m2=m2, m2f=m2f, m3=m3, m4=m4), status


def main():
    global OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", required=True)
    ap.add_argument("--stellar-tag", default=None)
    ap.add_argument("--stellar-rules-start", default="2026-01-01",
                    help="ET date from which Stellar events feed TR-5/TR-6 (IMPL-1b seed instant; IMPL-21)")
    ap.add_argument("--window", required=True, help="ET dates start:end for M1/M2/M4")
    ap.add_argument("--insample", required=True, help="report dates reconciled with values shown")
    ap.add_argument("--oos", default="", help="report dates run for code path only (values hidden)")
    ap.add_argument("--reveal-oos", action="store_true")
    ap.add_argument("--sensitivities", action="store_true", help="also compute every audit sensitivity")
    ap.add_argument("--burn-origin", default=MAIN["burn_origin"], choices=["tx_sender", "transfer_from"])
    ap.add_argument("--tr3-scope", default=MAIN["tr3_scope"], choices=["iii", "all"])
    ap.add_argument("--quarter-ends", default=None, help="M3 snapshot dates (default: quarter-ends in window)")
    ap.add_argument("--out-dir", default=None, help="write outputs here instead of data/processed (code-path tests)")
    a = ap.parse_args()
    if a.reveal_oos and a.prefix != "freeze":
        raise SystemExit("--reveal-oos is only allowed on the frozen vintage (--prefix freeze)")
    if a.out_dir:
        OUT = Path(a.out_dir)
        OUT.mkdir(parents=True, exist_ok=True)
    w0, w1 = [dt.date.fromisoformat(x) for x in a.window.split(":")]
    insample = [dt.date.fromisoformat(x) for x in a.insample.split(",") if x]
    oos = [dt.date.fromisoformat(x) for x in a.oos.split(",") if x]
    qends = [dt.date.fromisoformat(x) for x in a.quarter_ends.split(",")] if a.quarter_ends else \
        [d for d in insample if d.month in (3, 6, 9, 12) and w0 <= d <= w1]
    c = load(a)
    main_p = dict(MAIN, burn_origin=a.burn_origin, tr3_scope=a.tr3_scope)

    # ------------------------------------------------------------------ main specification
    roles, labels = classify(c, main_p)
    role_rows, label_rows = [], []
    for (fund, chain), rl in sorted(roles.items()):
        lb = labels[(fund, chain)]
        for ad, v in rl.items():
            role_rows.append(dict(fund=fund, chain=chain, address=ad, role=v["role"], reasons=";".join(v["reasons"])))
        for e in c.ev_by[(fund, chain)]:
            if e["kind"] == "mint":
                l = lb[e["id"]]
                label_rows.append(dict(fund=fund, chain=chain, id=e["id"], ts_utc=e["ts"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                                       to=e["to"], amount=e["amount"], label=l[0], rule=l[1], detail=l[2]))
        print(f"[{fund} {chain}] roles {dict(Counter(v['role'] for v in rl.values()))} "
              f"TA reasons {dict(Counter(r for v in rl.values() for r in v['reasons']))}; "
              f"mint labels {dict(Counter((lb[e['id']][0], lb[e['id']][1]) for e in c.ev_by[(fund, chain)] if e['kind'] == 'mint'))}")
    write(role_rows, f"{a.prefix}_wallet_roles.csv")
    # verification report (pre-2024 calendar fallback): mints dated before the fund's first N-MFP3
    # 7-day yield have no yield, so TR-6 8c and rule (ii) are off and rule (i) runs uncapped
    # (e.g. FOBXX EVM mints Oct 2023 to about mid-2024). Count them per fund-chain and label.
    for (fund, chain), lb in sorted(labels.items()):
        ys = R.YieldSeries(c.yields[fund])
        ny = [e for e in c.ev_by[(fund, chain)] if e["kind"] == "mint" and ys.at(R.et_date(e["ts"])) is None]
        if ny:
            cnt = Counter(lb[e["id"]][:2] for e in ny)
            ds = sorted(R.et_date(e["ts"]) for e in ny)
            first_y = min(c.yields[fund]) if c.yields[fund] else "none"
            c.diag.append(dict(item="mints_labelled_with_no_yield_available", fund=fund, chain=chain, value=len(ny),
                               note=f"{ds[0]}..{ds[-1]} (first N-MFP3 yield date {first_y}); labels "
                                    + ";".join(f"{lab}_{rule or 'none'}={n}" for (lab, rule), n in sorted(cnt.items()))
                                    + "; 8c and rule (ii) off, rule (i) uncapped"))
    write(label_rows, f"{a.prefix}_mint_labels.csv")
    m0, m0oos = m0_rows(c, a, main_p, roles, labels, insample, oos)
    write(m0, f"{a.prefix}_m0_reconciliation.csv")
    write(m0oos, f"{a.prefix}_m0_oos_codepath.csv")
    wm, status = window_measures(c, main_p, roles, labels, w0, w1, qends, a.window)
    for fund in FUNDS:
        rows = [r for r in m0 if r["fund"] == fund]
        status.insert(0, dict(measure="M0", fund=fund,
                              computes="yes" if rows and all(r["nmfp_class_shares"] for r in rows) else "no",
                              note=f"coverage {sorted({r['coverage'] for r in rows})}; missing {sorted({r['chains_missing'] for r in rows})}"))
        ro = [r for r in m0oos if r["fund"] == fund and r["clock"] == "ET"]
        if ro:
            cnt = Counter(r["coverage"] for r in ro if r["register_row"])
            status.insert(1, dict(measure="M0-OOS code path", fund=fund,
                                  computes=",".join(sorted({r["computed"] for r in ro})),
                                  note=f"OOS fund-months with a register row: {cnt.get('complete', 0)} complete, "
                                       f"{cnt.get('coverage-incomplete', 0)} coverage-incomplete; missing chains "
                                       f"{sorted({r['chains_missing'] for r in ro if r['chains_missing']})} (values hidden unless --reveal-oos); "
                                       f"F0 counts ET rows only (rules.f0_count; UTC = sensitivity)"))
    write(wm["m1"], f"{a.prefix}_m1_secondary.csv")
    write(wm["m2"], f"{a.prefix}_m2_calendar.csv")
    write(wm["m2f"], f"{a.prefix}_m2_flow_match.csv")
    write(wm["m3"], f"{a.prefix}_m3_holding.csv")
    write(wm["m4"], f"{a.prefix}_m4_servicing.csv")
    write(status, f"{a.prefix}_measures_status.csv")

    # ------------------------------------------------------------------ sensitivities (audit table)
    if a.sensitivities:
        sens = defaultdict(list)
        runs = [(n, dict(main_p, **o), item) for n, o, item in SENSITIVITIES]
        for name, p, item in runs:
            print(f"sensitivity {name} ({item})", flush=True)
            r_, l_ = classify(c, p)
            s0, s0oos = m0_rows(c, a, p, r_, l_, insample, oos)
            for sample, rows_ in (("in", s0), ("oos", s0oos)):   # OOS values only under --reveal-oos
                sens["m0"] += [dict(variant=name, audit_item=item, sample=sample, **row) for row in rows_]
            swm, _ = window_measures(c, p, r_, l_, w0, w1, qends, a.window)
            for k in ("m1", "m2", "m3", "m4"):
                sens[k] += [dict(variant=name, audit_item=item, **row) for row in swm[k]]
            for (fund, ch), lb in sorted(l_.items()):
                cnt = Counter(v[:2] for v in lb.values())
                sens["labels"].append(dict(variant=name, fund=fund, chain=ch,
                                           **{f"{lab}_{rule or 'none'}": n for (lab, rule), n in sorted(cnt.items())}))
                cr = Counter(rr for v in r_[(fund, ch)].values() for rr in v["reasons"])
                sens["roles"].append(dict(variant=name, fund=fund, chain=ch,
                                          n_TA=sum(1 for v in r_[(fund, ch)].values() if v["role"] == "TA"),
                                          **{f"reason_{k_}": v_ for k_, v_ in sorted(cr.items())}))
        # IMPL-21: EVM-only, full history (from 1 Jan 2024, the start of TR-4 calendar coverage)
        print("sensitivity evm_full_history (IMPL-21)", flush=True)
        ew0 = max(dt.date(2024, 1, 1), min((R.et_date(e["ts"]) for (f_, ch), evs in c.ev_by.items()
                                             if ch not in NON_EVM for e in evs), default=w0))
        swm, _ = window_measures(c, main_p, roles, labels, ew0, w1, qends, f"{ew0}:{w1} EVM only",
                                 chain_filter=lambda ch: ch not in NON_EVM, do_m3=False)
        for k in ("m1", "m2", "m4"):
            sens[k] += [dict(variant="evm_full_history", audit_item="IMPL-21 EVM full history", **row) for row in swm[k]]
        for k in ("m0", "m1", "m2", "m3", "m4", "labels", "roles"):
            write(sens[k], f"{a.prefix}_sens_{k}.csv")

    if c.cal.outside_dates:
        od = sorted(c.cal.outside_dates)
        c.diag.append(dict(item="calendar_lookups_outside_TR4_coverage", fund="", chain="", value=len(od),
                           note=f"{od[0]}..{od[-1]}; weekend rule only (audit TR-4 guard)"))
    write(c.diag, f"{a.prefix}_diagnostics.csv")
    print("\nMeasure status (computes yes/no):")
    for s in status:
        print(f"  {s['measure']:18} {s['fund']:6} {s['computes']:6} {s['note']}")


if __name__ == "__main__":
    main()
