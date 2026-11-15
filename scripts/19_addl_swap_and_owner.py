"""19_addl_swap_and_owner.py -- ADDITION 2026-10-17 (exploratory, post-freeze; review cycle 4)

FROZEN inputs only (plus the logged addition output addl_signer_function.csv of script 15). No raw fetch. No
freeze_* file is written; no frozen measure, rule or threshold is changed. 05_measures.py is IMPORTED (load() and
classify() are called in memory with the frozen MAIN specification); its main() is not run and it writes nothing.

Task C1  WisdomTree (WTGXX) transfers routed through ERC1967Proxy->MultiliquidSwap (the 28 rows of
         addl_signer_function.csv with fund WTGXX and tx_to_contract containing "MultiliquidSwap"):
         hour (ET) and TR-4 business-day distribution against the WTGXX TA schedule computed from the frozen
         events in the same window; size distribution and total against frozen WTGXX redemption value
         (freeze_m2_calendar.csv); counterparty primary-market flags (issuance/dividend mints received, burns
         sent as Transfer `from` or as recorded burn tx sender, full frozen history); same-ET-day burns or
         mints by the same wallet.
Task C2  Dominant N-MFP3 record owner (30 Sep 2026, freeze_m3_holding.csv / governing N-MFP3 register) vs the
         largest non-TA wallet balances at the 2026-09-30 ET cutoff, as a share of N-MFP3 class shares and of
         on-chain balances. The in-memory snapshot must reproduce the frozen M3 row for 2026-09-30 (else exit).

Outputs (data/processed): addl_swap_transfers.csv, addl_swap_hours.csv, addl_swap_counterparties.csv,
  addl_swap_summary.csv, addl_ownermatch_wallets.csv, addl_ownermatch_summary.csv,
  addl_summary_numbers_19.csv (appended to key_numbers.csv by 07_key_numbers.py, ADDITION block).
Run: python -I scripts/19_addl_swap_and_owner.py, then python -I scripts/07_key_numbers.py
"""
import argparse
import bisect
import csv
import datetime as dt
import importlib.util
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import rules as R            # noqa: E402
import stellar_events as SE  # noqa: E402

spec = importlib.util.spec_from_file_location("measures05", ROOT / "scripts" / "05_measures.py")
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)

PROC = ROOT / "data" / "processed"
STAG = "freeze_2026-10-16"
FUNDS = M.FUNDS
WIN = (dt.date(2026, 1, 1), dt.date(2026, 9, 30))
WINS = f"{WIN[0]}:{WIN[1]}"
Q = dt.date(2026, 9, 30)
WALLET = "0x0c561ed86ef65dd681500d6266c99e03dec3c541"
MINT_BAND = range(16, 24)    # TA issuance-mint band 16:00-23:59 ET (cycle-3 B2 band; its share is re-computed and printed)
MATCH_PP = 1.0               # "match" tolerance, percentage points of class shares
SN = []


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


def pct(a, b):
    return round(100 * a / b, 4) if b else ""


def med(xs):
    return statistics.median(xs) if xs else ""


def ts(s):
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


def check_classification(c, roles, labels):
    """The in-memory TR-5/TR-6 output must equal the frozen wallet roles and mint labels.
    Labels are compared row by row in 05_measures.main() write order (one Solana id occurs twice in the freeze)."""
    fr = {(r["fund"], r["chain"], r["address"]): r["role"] for r in rd("freeze_wallet_roles.csv")}
    mine = {(f, ch, ad): v["role"] for (f, ch), rl in roles.items() for ad, v in rl.items()}
    fl = [(r["fund"], r["chain"], r["id"], r["label"]) for r in rd("freeze_mint_labels.csv")]
    ml = [(f, ch, str(e["id"]), labels[(f, ch)][e["id"]][0]) for (f, ch) in sorted(roles)
          for e in c.ev_by[(f, ch)] if e["kind"] == "mint"]
    ok_r, ok_l = fr == mine, fl == ml
    print(f"  roles vs freeze_wallet_roles.csv: {'IDENTICAL' if ok_r else 'MISMATCH'} ({len(fr)} rows); "
          f"mint labels vs freeze_mint_labels.csv: {'IDENTICAL' if ok_l else 'MISMATCH'} ({len(fl)} rows)")
    if not (ok_r and ok_l):
        sys.exit("in-memory classification does not reproduce the freeze; refusing to continue")


# ============================================================ C1 swap-routed transfers
def task_c1(c, roles, labels):
    print("\n=== C1: WTGXX transfers routed via ERC1967Proxy->MultiliquidSwap ===")
    sf = rd("addl_signer_function.csv")
    wt = [r for r in sf if r["fund"] == "WTGXX"]
    # filter (printed): WTGXX rows whose transaction target resolves to the MultiliquidSwap implementation
    sw = [r for r in wt if "MultiliquidSwap" in (r["tx_to_contract"] or "")]
    print(f"  addl_signer_function.csv WTGXX non-TA transfer rows: {len(wt)} -> routed via MultiliquidSwap: {len(sw)}")
    swap_targets = sorted({r["tx_to"].lower() for r in sw})
    chains = sorted({r["chain"] for r in sw})
    print(f"  tx_to (proxy) addresses: {swap_targets}; chains: {chains}")
    lo_t, hi_t = R.cutoff(WIN[0] - dt.timedelta(days=1)), R.cutoff(WIN[1])

    # ---- WTGXX TA schedule from the frozen events (same window, all event chains)
    ta_rows, ta_hours = [], {"issuance_mint": Counter(), "burn": Counter()}
    eth_hours = {"issuance_mint": Counter(), "burn": Counter()}   # Ethereum only (the swap-routed transfers are on Ethereum)
    burns_by = defaultdict(list)      # (chain, address) -> burn events (as Transfer from or tx sender)
    mints_by = defaultdict(list)      # (chain, address) -> mint events received
    for ch in sorted(c.chains_by_fund["WTGXX"]):
        lb = labels.get(("WTGXX", ch), {})
        for e in c.ev_by.get(("WTGXX", ch), []):
            if e["kind"] == "burn":
                burns_by[(ch, e["frm"].lower())].append(e)
                snd = c.senders.get(e["tx"])
                if snd and snd.lower() != e["frm"].lower():
                    burns_by[(ch, snd.lower())].append(dict(e, via="tx_sender"))
                if lo_t < e["ts"] <= hi_t:
                    ta_hours["burn"][R.et_hour(e["ts"])] += 1
                    if ch == "ethereum":
                        eth_hours["burn"][R.et_hour(e["ts"])] += 1
            elif e["kind"] == "mint":
                mints_by[(ch, e["to"].lower())].append(dict(e, label=lb[e["id"]][0]))
                if lb[e["id"]][0] == "issuance" and lo_t < e["ts"] <= hi_t:
                    ta_hours["issuance_mint"][R.et_hour(e["ts"])] += 1
                    if ch == "ethereum":
                        eth_hours["issuance_mint"][R.et_hour(e["ts"])] += 1
    burn_modal = ta_hours["burn"].most_common(1)[0][0]
    nb, nm = sum(ta_hours["burn"].values()), sum(ta_hours["issuance_mint"].values())
    mint_band_share = pct(sum(ta_hours["issuance_mint"][h] for h in MINT_BAND), nm)
    burn_modal_share = pct(ta_hours["burn"][burn_modal], nb)
    eth_mint_band_share = pct(sum(eth_hours["issuance_mint"][h] for h in MINT_BAND), sum(eth_hours["issuance_mint"].values()))
    eth_burn_modal_share = pct(eth_hours["burn"][burn_modal], sum(eth_hours["burn"].values()))
    ta_any_hours = {h for k in ta_hours for h, n in ta_hours[k].items() if n}
    frz_modal = next(r for r in rd("freeze_m2_calendar.csv") if r["fund"] == "WTGXX")["modal_burn_hour_et"]
    assert str(burn_modal) == frz_modal, "modal burn hour differs from freeze_m2_calendar.csv"
    print(f"  WTGXX TA schedule {WINS}: burns {nb} (modal hour {burn_modal}, {burn_modal_share}% of count); "
          f"issuance mints {nm} ({mint_band_share}% in 16:00-23:59); Ethereum only: issuance mints "
          f"{sum(eth_hours['issuance_mint'].values())} ({eth_mint_band_share}% in 16:00-23:59), burns "
          f"{sum(eth_hours['burn'].values())} ({eth_burn_modal_share}% in hour {burn_modal})")
    for k in ta_hours:
        for h in range(24):
            ta_rows.append(dict(series=f"WTGXX TA {k}", hour_et=h, count=ta_hours[k][h]))
            ta_rows.append(dict(series=f"WTGXX TA {k} (ethereum)", hour_et=h, count=eth_hours[k][h]))

    # ---- per-transfer rows
    rows = []
    for r in sorted(sw, key=lambda x: x["ts_utc"]):
        t = ts(r["ts_utc"])
        d, h = R.et_date(t), R.et_hour(t)
        amt = float(r["amount"])
        same_day = {}
        for side in ("frm", "to"):
            ad = r[side].lower()
            b = [e for e in burns_by.get((r["chain"], ad), []) if R.et_date(e["ts"]) == d]
            m = [e for e in mints_by.get((r["chain"], ad), []) if R.et_date(e["ts"]) == d]
            same_day[side] = (b, m)
        rows.append(dict(tx=r["tx"], chain=r["chain"], ts_utc=r["ts_utc"],
                         ts_et=t.astimezone(R.ET).strftime("%Y-%m-%dT%H:%M:%S"), date_et=d, weekday=d.strftime("%a"),
                         hour_et=h, business_day_tr4=int(c.cal.is_business(d)), frm=r["frm"], to=r["to"], amount=amt,
                         selector=r["selector"], function_class=r["function_class"], tx_to=r["tx_to"],
                         in_modal_burn_hour=int(h == burn_modal), in_mint_band_16_23=int(h in MINT_BAND),
                         on_ta_schedule=int(c.cal.is_business(d) and (h == burn_modal or h in MINT_BAND)),
                         in_any_hour_with_ta_event=int(h in ta_any_hours),
                         sameday_burns_by_frm=len(same_day["frm"][0]), sameday_burns_by_to=len(same_day["to"][0]),
                         sameday_mints_to_frm=len(same_day["frm"][1]), sameday_mints_to_to=len(same_day["to"][1]),
                         sameday_issuance_mints_same_wallet=sum(1 for s_ in same_day.values() for e in s_[1] if e["label"] == "issuance"),
                         sameday_burn_value_same_wallet=round(sum(e["amount"] for s_ in same_day.values() for e in s_[0]), 6)))
    n = len(rows)
    amts = sorted(x["amount"] for x in rows)
    hc = Counter(x["hour_et"] for x in rows)
    n_nb = sum(1 for x in rows if not x["business_day_tr4"])
    n_on = sum(x["on_ta_schedule"] for x in rows)
    n_out = n - n_on
    n_anyta = sum(x["in_any_hour_with_ta_event"] for x in rows)
    n_samedayburn = sum(1 for x in rows if x["sameday_burns_by_frm"] or x["sameday_burns_by_to"])
    n_samedaymint = sum(1 for x in rows if x["sameday_mints_to_frm"] or x["sameday_mints_to_to"])
    n_samedayiss = sum(1 for x in rows if x["sameday_issuance_mints_same_wallet"])
    tot = sum(amts)
    m2 = next(r for r in rd("freeze_m2_calendar.csv") if r["fund"] == "WTGXX")
    red_v, iss_v = float(m2["redemption_value"]), float(m2["issuance_value"])
    by_month = defaultdict(float)
    for x in rows:
        by_month[str(x["date_et"])[:7]] += x["amount"]
    # monthly WTGXX burn value (same window, frozen events) for the per-month comparison
    burn_month = defaultdict(float)
    for ch in sorted(c.chains_by_fund["WTGXX"]):
        for e in c.ev_by.get(("WTGXX", ch), []):
            if e["kind"] == "burn" and lo_t < e["ts"] <= hi_t:
                burn_month[R.et_date(e["ts"]).isoformat()[:7]] += e["amount"]
    burn_sizes = sorted(e["amount"] for ch in c.chains_by_fund["WTGXX"] for e in c.ev_by.get(("WTGXX", ch), [])
                        if e["kind"] == "burn" and lo_t < e["ts"] <= hi_t)
    print(f"  transfers {n}; hours ET {dict(sorted(hc.items()))}; non-business days {n_nb}; off TA schedule {n_out}; "
          f"in an hour with any WTGXX TA event {n_anyta}")
    print(f"  size: total {tot:.6f}; median {med(amts)}; max {max(amts)}; under 20 shares {sum(a < 20 for a in amts)}; "
          f">= 100 {sum(a >= 100 for a in amts)}; vs redemption value {red_v} -> {pct(tot, red_v)}%")
    print(f"  WTGXX burns in window: n {len(burn_sizes)}, median {med(burn_sizes)}")
    print(f"  same-ET-day burn by the same wallet: {n_samedayburn}; same-day mint to the same wallet: {n_samedaymint} "
          f"(of which issuance-labelled: {n_samedayiss})")
    for x in rows:
        x["share_of_wtgxx_redemption_value_pct"] = pct(x["amount"], red_v)

    # ---- counterparties
    cps = []
    parties = Counter()
    for x in rows:
        parties[x["frm"].lower()] += 1
        parties[x["to"].lower()] += 1
    for ad in swap_targets:
        parties.setdefault(ad, 0)
    for ad, k in sorted(parties.items(), key=lambda kv: -kv[1]):
        ch = chains[0]
        rl = roles.get(("WTGXX", ch), {}).get(ad, {})
        sent = sum(x["amount"] for x in rows if x["frm"].lower() == ad)
        recv = sum(x["amount"] for x in rows if x["to"].lower() == ad)
        mts = mints_by.get((ch, ad), [])
        bns = burns_by.get((ch, ad), [])
        evs_any = sum(1 for e in c.ev_by.get(("WTGXX", ch), []) if ad in (e["frm"].lower(), e["to"].lower()))
        bal = R.balances_at(c.ev_by[("WTGXX", ch)], R.cutoff(Q)).get(next((a for a in R.balances_at(
            c.ev_by[("WTGXX", ch)], R.cutoff(Q)) if a.lower() == ad), ad), 0.0)
        flags = [f for f, cond in (("issuance_mint_recipient", any(e["label"] == "issuance" for e in mts)),
                                   ("dividend_mint_recipient", any(e["label"] == "dividend" for e in mts)),
                                   ("burn_from", any(e.get("via") != "tx_sender" for e in bns)),
                                   ("burn_tx_sender", any(e.get("via") == "tx_sender" for e in bns))) if cond]
        cps.append(dict(address=ad, chain=ch, is_swap_proxy_tx_to=int(ad in swap_targets),
                        freeze_role=rl.get("role", "(not in role table)"), freeze_role_reasons=";".join(rl.get("reasons", [])),
                        has_code=(int(c.codes[(ch, ad)]) if (ch, ad) in c.codes else "(not in freeze_evm_codes.csv)"),
                        n_swap_transfers=k,
                        swap_sent_shares=round(sent, 6), swap_received_shares=round(recv, 6),
                        n_wtgxx_events_any_side_full_history=evs_any,
                        mints_received_n=len(mts), issuance_mints_n=sum(e["label"] == "issuance" for e in mts),
                        issuance_mints_value=round(sum(e["amount"] for e in mts if e["label"] == "issuance"), 6),
                        dividend_mints_n=sum(e["label"] == "dividend" for e in mts),
                        burns_from_n=sum(1 for e in bns if e.get("via") != "tx_sender"),
                        burns_tx_sender_n=sum(1 for e in bns if e.get("via") == "tx_sender"),
                        burns_value=round(sum(e["amount"] for e in bns), 6),
                        balance_at_2026_09_30_et=round(bal, 6),
                        primary_market_flags=";".join(flags) or "none"))
    for x in cps:
        print(f"  {x['address']} role {x['freeze_role']} code {x['has_code']} swaps {x['n_swap_transfers']} "
              f"sent {x['swap_sent_shares']} recv {x['swap_received_shares']} events {x['n_wtgxx_events_any_side_full_history']} "
              f"flags {x['primary_market_flags']} bal@Q {x['balance_at_2026_09_30_et']}")
    n_pm = sum(1 for x in cps if x["primary_market_flags"] != "none")
    proxy_in_events = sum(x["n_wtgxx_events_any_side_full_history"] for x in cps if x["is_swap_proxy_tx_to"])

    hrows = [dict(series="swap-routed transfers", hour_et=h, count=hc[h]) for h in range(24)] + ta_rows
    summ = dict(window=WINS, n_transfers=n, n_distinct_tx=len({x["tx"] for x in rows}), chain=";".join(chains),
                swap_proxy_tx_to=";".join(swap_targets), first_ts_utc=rows[0]["ts_utc"], last_ts_utc=rows[-1]["ts_utc"],
                hour_counts_et=";".join(f"{h}:{hc[h]}" for h in sorted(hc)), modal_hour_et=hc.most_common(1)[0][0],
                n_nonbusiness_day_tr4=n_nb, ta_modal_burn_hour_et=burn_modal, ta_modal_burn_hour_share_pct=burn_modal_share,
                ta_issuance_mint_share_16_23_pct=mint_band_share, ta_issuance_mint_share_16_23_ethereum_pct=eth_mint_band_share,
                ta_burn_modal_hour_share_ethereum_pct=eth_burn_modal_share, n_on_ta_schedule=n_on, n_off_ta_schedule=n_out,
                n_off_ta_schedule_pct=pct(n_out, n), n_in_any_hour_with_ta_event=n_anyta,
                total_shares=round(tot, 6), median_shares=med(amts), max_shares=max(amts), min_shares=min(amts),
                n_under_20_shares=sum(a < 20 for a in amts), n_ge_100_shares=sum(a >= 100 for a in amts),
                largest4_share_of_total_pct=pct(sum(amts[-4:]), tot),
                wtgxx_redemption_value_window=red_v, wtgxx_issuance_value_window=iss_v,
                total_share_of_redemption_value_pct=pct(tot, red_v), total_share_of_issuance_value_pct=pct(tot, iss_v),
                n_wtgxx_burns_window=len(burn_sizes), median_wtgxx_burn_shares=med(burn_sizes),
                max_month_swap_share_of_month_burn_value_pct=max((pct(v, burn_month[mo]) for mo, v in by_month.items()
                                                                  if burn_month[mo]), default=""),
                swap_value_by_month=";".join(f"{mo}:{round(v, 6)}" for mo, v in sorted(by_month.items())),
                n_counterparties=len([x for x in cps if not x["is_swap_proxy_tx_to"]]),
                n_counterparties_with_primary_market_flag=n_pm,
                swap_proxy_wtgxx_events_full_history=proxy_in_events,
                n_transfers_sameday_burn_same_wallet=n_samedayburn, n_transfers_sameday_mint_same_wallet=n_samedaymint,
                n_transfers_sameday_issuance_mint_same_wallet=n_samedayiss)
    print(f"  max month swap/burn value share {summ['max_month_swap_share_of_month_burn_value_pct']}%; "
          f"counterparties {summ['n_counterparties']}, with PM flag {n_pm}; proxy in WTGXX events {proxy_in_events}")
    write(rows, "addl_swap_transfers.csv")
    write(hrows, "addl_swap_hours.csv")
    write(cps, "addl_swap_counterparties.csv")
    write([summ], "addl_swap_summary.csv")
    units = dict(n_transfers="count", n_distinct_tx="count", modal_hour_et="hour ET", hour_counts_et="text (hour:count)",
                 n_nonbusiness_day_tr4="count", ta_modal_burn_hour_et="hour ET", ta_modal_burn_hour_share_pct="pct",
                 ta_issuance_mint_share_16_23_pct="pct", ta_issuance_mint_share_16_23_ethereum_pct="pct",
                 ta_burn_modal_hour_share_ethereum_pct="pct", n_on_ta_schedule="count", n_off_ta_schedule="count",
                 n_off_ta_schedule_pct="pct", n_in_any_hour_with_ta_event="count", total_shares="shares",
                 median_shares="shares", max_shares="shares", min_shares="shares", n_under_20_shares="count",
                 n_ge_100_shares="count", largest4_share_of_total_pct="pct", wtgxx_redemption_value_window="shares",
                 total_share_of_redemption_value_pct="pct", total_share_of_issuance_value_pct="pct",
                 n_wtgxx_burns_window="count", median_wtgxx_burn_shares="shares",
                 max_month_swap_share_of_month_burn_value_pct="pct", n_counterparties="count",
                 n_counterparties_with_primary_market_flag="count", swap_proxy_wtgxx_events_full_history="count",
                 n_transfers_sameday_burn_same_wallet="count", n_transfers_sameday_mint_same_wallet="count",
                 n_transfers_sameday_issuance_mint_same_wallet="count")
    for k, u in units.items():
        sn(f"addl_swap_{k}__WTGXX", summ[k], u, WINS, "addl_swap_summary.csv")
    for x in cps:
        if x["is_swap_proxy_tx_to"]:
            continue
        sn(f"addl_swap_cp_flags__WTGXX__{x['address'][:10]}", x["primary_market_flags"], "text",
           "full frozen history to 2026-10-16", "addl_swap_counterparties.csv")


# ============================================================ C2 owner match
def parse_owners(s):
    out = []
    for part in (s or "").split(";"):
        if "|rec=" in part:
            cat, rest = part.split("|rec=")
            out.append((cat, float(rest.split("|")[0]) * 100))
    return out


def task_c2(c, roles, labels):
    print("\n=== C2: dominant N-MFP3 record owner vs largest wallets, 2026-09-30 ===")
    cut = R.cutoff(Q)
    frz = {r["fund"]: r for r in rd("freeze_m3_holding.csv") if r["snapshot"] == str(Q) and r["row"] == "M3"}
    wrows, summ = [], []
    for fund in FUNDS:
        ev_chains = [ch for ch in sorted(c.chains_by_fund[fund]) if c.ev_by.get((fund, ch))]
        pm_ads = defaultdict(set)    # chain -> addresses with an issuance mint received or a burn sent, up to the cutoff
        pm_broad = defaultdict(set)  # chain -> addresses that sent to / received from a TA-role wallet, up to the cutoff
        allw, ta_bal, tot_bal, sup = [], 0.0, 0.0, 0.0
        for ch in ev_chains:
            rl, lb = roles[(fund, ch)], labels[(fund, ch)]
            if ch == "stellar":   # as 05_measures.window_measures M3
                st = c.stellar[fund]
                bal = SE.balances_backout(st["events"], st["bal_now"], st["t_bal"], cut, st["issuer"])
                sup += SE.supply_backout(st["events"], st["sup_now"], st["t_now"], cut)
            else:
                bal = R.balances_at(c.ev_by[(fund, ch)], cut)
                sup += R.supply_at(c.ev_by[(fund, ch)], cut)
            for e in c.ev_by[(fund, ch)]:
                if e["ts"] > cut:
                    continue
                if e["kind"] == "mint" and lb[e["id"]][0] == "issuance":
                    pm_ads[ch].add(e["to"])
                if e["kind"] == "burn":
                    pm_ads[ch].add(e["frm"])
                    if e["tx"] in c.senders:
                        pm_ads[ch].add(c.senders[e["tx"]])
                # broad definition: a transfer to or from a TA-role wallet (TR-2 redemption deposit, TA delivery)
                if e["kind"] == "transfer":
                    ra, rb = rl.get(e["frm"], {}).get("role"), rl.get(e["to"], {}).get("role")
                    if rb == "TA" and ra != "TA":
                        pm_broad[ch].add(e["frm"])
                    if ra == "TA" and rb != "TA":
                        pm_broad[ch].add(e["to"])
            for ad, v in bal.items():
                if v <= 1e-9:      # as frozen M3: zero / float-residue balances are not wallets
                    continue
                tot_bal += v
                role = rl.get(ad, {}).get("role", "holder")
                if role == "TA":
                    ta_bal += v
                else:
                    allw.append(dict(chain=ch, address=ad, role=role, balance=v, pm=int(ad in pm_ads[ch]),
                                     pm_broad=int(ad in pm_ads[ch] or ad in pm_broad[ch])))
        allw.sort(key=lambda x: -x["balance"])
        # reproduction check against the frozen M3 row
        f = frz[fund]
        out10 = sum(x["balance"] for x in allw[10:])
        s_non = sum(x["balance"] for x in allw)
        same = (str(round(tot_bal, 6)) == f["balances_total"] and str(pct(ta_bal, tot_bal)) == f["ta_share_pct"]
                and str(len(allw)) == f["n_nonta_wallets"]
                and (f["nonta_outside_top10_share_pct"] == "" or str(pct(out10, s_non)) == f["nonta_outside_top10_share_pct"]))
        print(f"  {fund}: reproduction of freeze_m3_holding.csv 2026-09-30 M3 row: {'IDENTICAL' if same else 'MISMATCH'}")
        if not same:
            sys.exit(f"{fund}: in-memory snapshot does not reproduce the frozen M3 row; refusing to continue")
        g = c.reg[(fund, Q.isoformat())]
        cls_sh = float(g["class_shares_outstanding"])
        owners = parse_owners(g["beneficial_record_owner_categories"])
        dom_cat, dom = max(owners, key=lambda x: x[1])
        assert g["beneficial_record_owner_categories"] == f["nmfp_owner_categories"]
        for i, x in enumerate(allw[:10], 1):
            near = min(owners, key=lambda o: abs(o[1] - pct(x["balance"], cls_sh)))
            wrows.append(dict(fund=fund, snapshot=Q, rank=i, chain=x["chain"], address=x["address"], freeze_role=x["role"],
                              primary_market_wallet=x["pm"], primary_market_wallet_broad=x["pm_broad"], balance=round(x["balance"], 6),
                              share_of_class_shares_pct=pct(x["balance"], cls_sh),
                              share_of_onchain_balances_pct=pct(x["balance"], tot_bal),
                              share_of_onchain_supply_pct=pct(x["balance"], sup),
                              nearest_nmfp_owner_category=near[0], nearest_nmfp_owner_rec_pct=round(near[1], 4),
                              diff_to_nearest_owner_pp=round(pct(x["balance"], cls_sh) - near[1], 4),
                              diff_to_dominant_owner_pp=round(pct(x["balance"], cls_sh) - dom, 4)))
        pmw = [x for x in allw if x["pm"]]
        pm_sum = sum(x["balance"] for x in pmw)
        pmb = [x for x in allw if x["pm_broad"]]
        pmb_sum = sum(x["balance"] for x in pmb)
        topk = {k: sum(x["balance"] for x in allw[:k]) for k in (1, 2, 3)}
        # candidates for a match with the dominant record owner (|share - dominant| <= MATCH_PP)
        cands = [("largest wallet", topk[1]), ("largest two wallets", topk[2]), ("largest three wallets", topk[3]),
                 ("primary-market cluster (all non-TA PM wallets)", pm_sum),
                 ("primary-market cluster, broad (incl. transfers with TA-role wallets)", pmb_sum), ("all non-TA wallets", s_non)]
        matches = [nm_ for nm_, v in cands if abs(pct(v, cls_sh) - dom) <= MATCH_PP]
        any_single = [x["address"] for x in allw if abs(pct(x["balance"], cls_sh) - dom) <= MATCH_PP]
        w_rank = next((i for i, x in enumerate(allw, 1) if x["address"].lower() == WALLET), "")
        ent = []      # nearest of the 50 largest non-TA wallets to every N-MFP3 owner entry
        for cat, rec in owners:
            nw = min(allw[:50], key=lambda x: abs(pct(x["balance"], cls_sh) - rec))
            ent.append(f"{cat} {round(rec, 2)}% -> {nw['address'][:10]} {pct(nw['balance'], cls_sh)}% "
                       f"(diff {round(pct(nw['balance'], cls_sh) - rec, 4)} pp)")
        row = dict(fund=fund, snapshot=Q, nmfp_class_shares=cls_sh, onchain_balances_total=round(tot_bal, 6),
                   onchain_supply_total=round(sup, 6), chains_with_events=";".join(ev_chains),
                   chains_without_events=f["chains_without_events"],
                   onchain_balances_share_of_class_shares_pct=pct(tot_bal, cls_sh),
                   nmfp_owner_entries=";".join(f"{a}:{round(b, 2)}" for a, b in owners), nmfp_n_owner_entries=len(owners),
                   dominant_owner_category=dom_cat, dominant_record_share_pct=round(dom, 4),
                   ta_balance_share_of_class_shares_pct=pct(ta_bal, cls_sh), n_nonta_wallets=len(allw),
                   top1_address=allw[0]["address"] if allw else "", top1_chain=allw[0]["chain"] if allw else "",
                   top1_pm=allw[0]["pm"] if allw else "",
                   **{f"top{k}_share_of_class_shares_pct": pct(v, cls_sh) for k, v in topk.items()},
                   **{f"top{k}_share_of_onchain_supply_pct": pct(v, sup) for k, v in topk.items()},
                   **{f"top{k}_minus_dominant_pp": round(pct(v, cls_sh) - dom, 4) for k, v in topk.items()},
                   n_pm_wallets_with_balance=len(pmw), pm_cluster_share_of_class_shares_pct=pct(pm_sum, cls_sh),
                   pm_cluster_minus_dominant_pp=round(pct(pm_sum, cls_sh) - dom, 4),
                   n_pm_broad_wallets_with_balance=len(pmb), pm_broad_cluster_share_of_class_shares_pct=pct(pmb_sum, cls_sh),
                   pm_broad_cluster_minus_dominant_pp=round(pct(pmb_sum, cls_sh) - dom, 4),
                   top1_pm_broad=allw[0]["pm_broad"] if allw else "",
                   nonta_share_of_class_shares_pct=pct(s_non, cls_sh),
                   n_single_wallets_within_1pp=len(any_single), single_wallets_within_1pp=";".join(any_single),
                   aggregates_within_1pp=";".join(matches) or "none",
                   nearest_wallet_per_owner_entry=" | ".join(ent),
                   wallet_0x0c561ed8_rank=w_rank,
                   wallet_0x0c561ed8_share_of_class_shares_pct=(pct(allw[w_rank - 1]["balance"], cls_sh) if w_rank else ""))
        summ.append(row)
        print(f"    owner entries vs nearest wallet (top 50): {row['nearest_wallet_per_owner_entry']}")
        print(f"  {fund}: class shares {cls_sh}; on-chain {round(tot_bal, 2)} ({row['onchain_balances_share_of_class_shares_pct']}%); "
              f"dominant {dom_cat} {round(dom, 4)}%; top1/2/3 {row['top1_share_of_class_shares_pct']}/"
              f"{row['top2_share_of_class_shares_pct']}/{row['top3_share_of_class_shares_pct']}% of class shares; "
              f"PM cluster {row['pm_cluster_share_of_class_shares_pct']}% ({len(pmw)} wallets); within 1pp: single "
              f"{len(any_single)}, aggregates {row['aggregates_within_1pp']}; top1 {row['top1_address']} ({row['top1_chain']}, pm={row['top1_pm']})"
              + (f"; 0x0c561ed8 rank {w_rank} share {row['wallet_0x0c561ed8_share_of_class_shares_pct']}%" if fund == "WTGXX" else ""))
        for k in ("dominant_record_share_pct", "onchain_balances_share_of_class_shares_pct", "top1_share_of_class_shares_pct",
                  "top2_share_of_class_shares_pct", "top3_share_of_class_shares_pct", "top1_share_of_onchain_supply_pct",
                  "top2_share_of_onchain_supply_pct", "top3_share_of_onchain_supply_pct", "top1_minus_dominant_pp",
                  "pm_cluster_share_of_class_shares_pct", "pm_cluster_minus_dominant_pp", "n_pm_wallets_with_balance",
                  "pm_broad_cluster_share_of_class_shares_pct", "pm_broad_cluster_minus_dominant_pp", "n_pm_broad_wallets_with_balance",
                  "top1_pm_broad",
                  "n_single_wallets_within_1pp", "aggregates_within_1pp", "top1_address", "top1_pm", "n_nonta_wallets",
                  "ta_balance_share_of_class_shares_pct", "nearest_wallet_per_owner_entry"):
            u = ("pct" if k.endswith("pct") else "pct points" if k.endswith("_pp") else
                 "text" if k in ("aggregates_within_1pp", "top1_address", "nearest_wallet_per_owner_entry") else "count")
            sn(f"addl_ownermatch_{k}__{fund}", row[k], u, str(Q), "addl_ownermatch_summary.csv")
        if fund == "WTGXX":
            sn("addl_ownermatch_wallet_0x0c561ed8_rank__WTGXX", w_rank, "rank (non-TA wallets)", str(Q), "addl_ownermatch_summary.csv")
            sn("addl_ownermatch_wallet_0x0c561ed8_share_of_class_shares_pct__WTGXX",
               row["wallet_0x0c561ed8_share_of_class_shares_pct"], "pct", str(Q), "addl_ownermatch_summary.csv")
    write(wrows, "addl_ownermatch_wallets.csv")
    write(summ, "addl_ownermatch_summary.csv")


# ============================================================ shared: M1 (pooled, transfers_only) as 05_measures.window_measures
def m1_pooled(c, roles, fund, num_excl=lambda e: False, sample_excl=lambda e: False):
    """Pooled transfers-only M1 for fund in WIN, with roles[(fund, chain)] -> {address: {"role": ...}}.
    Pair letters as 05: h(older), c(ontract), T(A). lower = hh; upper = no T. num_excl drops events from the
    numerators only; sample_excl drops them from numerator and denominator."""
    lo_t, hi_t = R.cutoff(WIN[0] - dt.timedelta(days=1)), R.cutoff(WIN[1])
    n = v = nl = vl = nu = vu = 0
    for ch in sorted(c.chains_by_fund[fund]):
        rl = roles.get((fund, ch), {})
        for e in c.ev_by.get((fund, ch), []):
            if e["kind"] != "transfer" or not (lo_t < e["ts"] <= hi_t) or sample_excl(e):
                continue
            n += 1
            v += e["amount"]
            ra, rb = rl.get(e["frm"], {}).get("role", "holder"), rl.get(e["to"], {}).get("role", "holder")
            pair = "".join(sorted([ra[0], rb[0]]))
            if "T" in pair or num_excl(e):
                continue
            nu += 1
            vu += e["amount"]
            if pair == "hh":
                nl += 1
                vl += e["amount"]
    return dict(n_events=n, value=round(v, 6), n_lower=nl, value_lower=round(vl, 6), n_upper=nu, value_upper=round(vu, 6),
                lower_share_value_pct=pct(vl, v), lower_share_count_pct=pct(nl, n),
                upper_share_value_pct=pct(vu, v), upper_share_count_pct=pct(nu, n))


def wal(e):
    return WALLET in (e["frm"].lower(), e["to"].lower())


def load_sourcecode():
    """Contract names from the logged addition getsourcecode responses (parsed as JSON data, never executed)."""
    src = {}
    with open(ROOT / "data" / "raw" / "addition_2026-10-17" / "evm_calls.jsonl", encoding="utf-8") as f:
        for line in f:
            o = json.loads(line)
            if o.get("method") == "getsourcecode":
                res = (o.get("response") or {}).get("result") or [{}]
                src[tuple(o["params"])] = res[0] if isinstance(res, list) and res and isinstance(res[0], dict) else {}
    return src


def cname(src, chain, addr):
    """As scripts/15_addl_analysis.cname: ContractName, plus the implementation's name for a proxy."""
    s_ = src.get((chain, addr), {})
    n_ = s_.get("ContractName", "")
    impl = (s_.get("Implementation") or "").lower()
    if impl and src.get((chain, impl), {}).get("ContractName"):
        n_ = f"{n_}->{src[(chain, impl)]['ContractName']}"
    return n_


# ============================================================ C3 third-party contracts out of the TA set
def pair_returns(evs, venue, label, fund, pairs):
    """Mechanical in/out pairing (no decoding of what the venue paid out in other tokens): each transfer from `venue`
    back to wallet w is paired with the unpaired earlier transfer from w to `venue` in the previous 24 hours whose
    ratio returned/deposited is closest to 1. evs: dicts with ts, frm, to, amount."""
    open_ = defaultdict(list)
    for e in sorted(evs, key=lambda e: e["ts"]):
        if e["to"] == venue:
            open_[e["frm"]].append(e)
        elif e["frm"] == venue:
            cand = [d for d in open_[e["to"]] if (e["ts"] - d["ts"]).total_seconds() <= 86400]
            if cand:
                d = min(cand, key=lambda d: abs(e["amount"] / d["amount"] - 1))
                open_[e["to"]].remove(d)
                pairs.append(dict(venue=label, fund=fund, venue_address=venue, wallet=e["to"],
                                  deposit_ts_utc=d["ts"].strftime("%Y-%m-%dT%H:%M:%SZ"), deposit=d["amount"],
                                  return_ts_utc=e["ts"].strftime("%Y-%m-%dT%H:%M:%SZ"), returned=e["amount"],
                                  hours_between=round((e["ts"] - d["ts"]).total_seconds() / 3600, 3),
                                  ratio_returned_to_deposit=round(e["amount"] / d["amount"], 6)))


def task_c3(c, roles):
    print("\n=== C3: hop/parking TA addresses; WTGXX M1 with third-party contracts in the contract class ===")
    src = load_sourcecode()
    codes = {(r["chain"], r["address"]): r["has_code"] for r in rd("freeze_evm_codes.csv")}
    lo_t, hi_t = R.cutoff(WIN[0] - dt.timedelta(days=1)), R.cutoff(WIN[1])
    ta = [r for r in rd("freeze_wallet_roles.csv") if r["role"] == "TA" and r["fund"] in ("WTGXX", "FOBXX")]
    # filter (printed): TA only through the hop or parking legs (no zero/mint-source, module or burner reason)
    hp = [r for r in ta if r["reasons"] and set(r["reasons"].split(";")) <= {"hop", "parking"}]
    print(f"  WTGXX+FOBXX TA rows {len(ta)} -> TA by hop/parking only: {len(hp)}")
    fund_tv = {f: sum(e["amount"] for ch in c.chains_by_fund[f] for e in c.ev_by.get((f, ch), [])
                      if e["kind"] == "transfer" and lo_t < e["ts"] <= hi_t) for f in ("WTGXX", "FOBXX")}
    rows = []
    for r in hp:
        evs = [e for e in c.ev_by.get((r["fund"], r["chain"]), []) if r["address"] in (e["frm"], e["to"])]
        win = [e for e in evs if lo_t < e["ts"] <= hi_t and e["kind"] == "transfer"]
        hc = codes.get((r["chain"], r["address"]))
        if r["chain"] == "stellar":
            hc = "1" if r["address"].startswith("C") else "0"     # as 05_measures.classify has_code for Stellar
        tv = sum(e["amount"] for e in win)
        ft = fund_tv[r["fund"]]
        rows.append(dict(fund=r["fund"], chain=r["chain"], address=r["address"], reasons=r["reasons"],
                         has_code=hc if hc is not None else "(not in freeze_evm_codes.csv)",
                         contract_name_addition=cname(src, r["chain"], r["address"]) or "(none held)",
                         n_events_full_history=len(evs), n_transfers_window=len(win), transfer_value_window=round(tv, 6),
                         share_of_fund_transfer_value_window_pct=pct(tv, ft),
                         material_ge_0_1pct=int(bool(ft) and 100 * tv / ft >= 0.1)))   # materiality: >= 0.1% of fund transfer value
    rows.sort(key=lambda x: (x["fund"], -x["transfer_value_window"]))
    for x in rows:
        print(f"  {x['fund']} {x['chain']:8} {x['address'][:12]} {x['reasons']:12} code {x['has_code']} "
              f"{x['contract_name_addition'][:58]:58} n {x['n_transfers_window']:4} value {x['transfer_value_window']} "
              f"({x['share_of_fund_transfer_value_window_pct']}%)")
    write(rows, "addl_ta_hop_parking.csv")
    # reclassification (exploratory): hop/parking TA addresses with code -> contract class; EOAs stay TA
    moved = {(x["fund"], x["chain"], x["address"]) for x in rows if x["has_code"] == "1"}
    print(f"  moved to contract class (hop/parking TA with code): {len(moved)} "
          f"(WTGXX {sum(1 for m in moved if m[0] == 'WTGXX')}, FOBXX {sum(1 for m in moved if m[0] == 'FOBXX')})")
    roles2 = {k: {ad: (dict(v, role="contract") if (k[0], k[1], ad) in moved else v) for ad, v in rl.items()}
              for k, rl in roles.items()}
    # outer bound (exploratory): EVERY hop/parking-only TA address leaves the TA set (code -> contract, EOA -> holder)
    allhp = {(x["fund"], x["chain"], x["address"]): ("contract" if x["has_code"] == "1" else "holder") for x in rows}
    roles3 = {k: {ad: (dict(v, role=allhp[(k[0], k[1], ad)]) if (k[0], k[1], ad) in allhp else v) for ad, v in rl.items()}
              for k, rl in roles.items()}
    frow = next(x for x in rd("freeze_m1_secondary.csv") if x["fund"] == "WTGXX" and x["chain_scope"] == "pooled"
                and x["basis"] == "transfers_only")
    base = m1_pooled(c, roles, "WTGXX")
    for k in ("lower_share_value_pct", "lower_share_count_pct", "upper_share_value_pct", "upper_share_count_pct"):
        if str(base[k]) != frow[k]:
            sys.exit(f"M1 replication differs on {k}: {base[k]} vs {frow[k]}")
    print("  M1 replication (frozen roles) vs freeze_m1_secondary.csv WTGXX pooled transfers_only: IDENTICAL")
    out = []
    for cls_, rl in (("frozen roles", roles), ("third-party contracts -> contract class", roles2),
                     ("all hop/parking TA -> non-TA (outer bound)", roles3)):
        for wm, kw in (("with_wallet", {}), ("excl_wallet_numerator", dict(num_excl=wal)),
                       ("excl_wallet_sample", dict(sample_excl=wal))):
            r_ = dict(fund="WTGXX", classification=cls_, wallet_mode=wm, window=WINS, **m1_pooled(c, rl, "WTGXX", **kw))
            out.append(r_)
            print(f"  {cls_:40} {wm:22} lower v/c {r_['lower_share_value_pct']}/{r_['lower_share_count_pct']}  "
                  f"upper v/c {r_['upper_share_value_pct']}/{r_['upper_share_count_pct']}  (n {r_['n_events']})")
            tag = "reclass" if cls_.startswith("third") else "allhp" if cls_.startswith("all") else "frozen"
            for k in ("lower_share_value_pct", "lower_share_count_pct", "upper_share_value_pct", "upper_share_count_pct",
                      "n_events", "n_lower", "n_upper"):
                sn(f"addl_reclass_m1_{k}__WTGXX__{tag}__{wm}", r_[k], "pct" if k.endswith("pct") else "count",
                   WINS, "addl_reclass_m1.csv")
    write(out, "addl_reclass_m1.csv")
    for f in ("WTGXX", "FOBXX"):
        sn(f"addl_reclass_n_hop_parking_ta__{f}", sum(1 for x in rows if x["fund"] == f), "count", STAG, "addl_ta_hop_parking.csv")
        sn(f"addl_reclass_n_material__{f}", sum(x["material_ge_0_1pct"] for x in rows if x["fund"] == f), "count",
           WINS, "addl_ta_hop_parking.csv")
        sn(f"addl_reclass_n_moved__{f}", sum(1 for m in moved if m[0] == f), "count", STAG, "addl_ta_hop_parking.csv")

    # ---- Frax custodian (name from the addition getsourcecode data) and MultiliquidSwap counterparties: in/out ratios
    frax = [x for x in rows if "FrxUSDCustodian" in x["contract_name_addition"]]
    print(f"  hop/parking TA addresses whose addition contract name contains FrxUSDCustodian: "
          f"{[(x['fund'], x['address']) for x in frax]}")
    fx_rows, pairs, fx_list = [], [], []
    for x in frax:
        ad, ch, fund = x["address"], x["chain"], x["fund"]
        evs = [e for e in c.ev_by[(fund, ch)] if e["kind"] == "transfer" and lo_t < e["ts"] <= hi_t and ad in (e["frm"], e["to"])]
        ins = [e for e in evs if e["to"] == ad]
        outs = [e for e in evs if e["frm"] == ad]
        pair_returns(evs, ad, "FrxUSDCustodian", fund, pairs)
        rl = roles[(fund, ch)]
        for e in sorted(evs, key=lambda e: e["ts"]):
            cp = e["frm"] if e["to"] == ad else e["to"]
            fx_list.append(dict(fund=fund, chain=ch, ts_utc=e["ts"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                                ts_et=e["ts"].astimezone(R.ET).strftime("%Y-%m-%dT%H:%M:%S"),
                                direction="in (to custodian)" if e["to"] == ad else "out (from custodian)",
                                counterparty=cp, counterparty_role=rl.get(cp, {}).get("role", "holder"),
                                amount=e["amount"], tx=e["tx"]))
        fx_rows.append(dict(fund=fund, chain=ch, address=ad, contract_name=x["contract_name_addition"], window=WINS,
                            n_transfers=len(evs), n_in=len(ins), value_in=round(sum(e["amount"] for e in ins), 6),
                            n_out=len(outs), value_out=round(sum(e["amount"] for e in outs), 6),
                            n_distinct_counterparties=len({e["frm"] for e in ins} | {e["to"] for e in outs}),
                            share_of_fund_transfer_value_pct=x["share_of_fund_transfer_value_window_pct"]))
    # venues for the same pairing: the material hop/parking EOAs (e.g. 0x894bb634, where 10 -> 9.99 round trips occur) and
    # the code-bearing counterparties of the MultiliquidSwap-routed transfers (addl_swap_counterparties.csv, has_code 1)
    for x in rows:
        if x["fund"] == "WTGXX" and x["material_ge_0_1pct"] and not x in frax:
            ad, ch = x["address"], x["chain"]
            evs = [e for e in c.ev_by[("WTGXX", ch)] if e["kind"] == "transfer" and lo_t < e["ts"] <= hi_t and ad in (e["frm"], e["to"])]
            pair_returns(evs, ad, f"hop_parking_{ad[:10]}", "WTGXX", pairs)
    sw = [dict(ts=ts(r["ts_utc"]), frm=r["frm"], to=r["to"], amount=float(r["amount"])) for r in rd("addl_swap_transfers.csv")]
    for venue in sorted(r["address"] for r in rd("addl_swap_counterparties.csv") if r["has_code"] == "1"):
        pair_returns(sw, venue, f"MultiliquidSwap_routed_{venue[:10]}", "WTGXX", pairs)
    for x in fx_rows:
        print(f"  Frax custodian {x['fund']} {x['address']}: {x['n_transfers']} transfers; in {x['n_in']} ({x['value_in']}), "
              f"out {x['n_out']} ({x['value_out']}); counterparties {x['n_distinct_counterparties']}")
        for k in ("n_transfers", "n_in", "value_in", "n_out", "value_out", "n_distinct_counterparties",
                  "share_of_fund_transfer_value_pct"):
            sn(f"addl_frax_{k}__{x['fund']}", x[k], "shares" if k.startswith("value") else "pct" if k.endswith("pct") else "count",
               WINS, "addl_frax_custodian.csv")
    rc = Counter((p_["venue"], p_["ratio_returned_to_deposit"]) for p_ in pairs)
    for (vn, ra), k in sorted(rc.items()):
        print(f"  pair ratio {vn}: {ra} x{k}")
    for vn in sorted({p_["venue"] for p_ in pairs}):
        rs = [p_["ratio_returned_to_deposit"] for p_ in pairs if p_["venue"] == vn]
        sn(f"addl_pairs_n__{vn}", len(rs), "count", WINS, "addl_frax_pairs.csv")
        sn(f"addl_pairs_ratio_counts__{vn}", ";".join(f"{ra}:{k}" for (v_, ra), k in sorted(rc.items()) if v_ == vn),
           "text (ratio:count)", WINS, "addl_frax_pairs.csv")
        sn(f"addl_pairs_median_ratio__{vn}", med(rs), "ratio", WINS, "addl_frax_pairs.csv")
        sn(f"addl_pairs_n_ratio_0999__{vn}", sum(1 for x in rs if x == 0.999), "count (ratio exactly 0.999 at 6 dp)", WINS,
           "addl_frax_pairs.csv")
        sn(f"addl_pairs_n_ratio_1__{vn}", sum(1 for x in rs if x == 1.0), "count (ratio exactly 1)", WINS, "addl_frax_pairs.csv")
    write(fx_rows, "addl_frax_custodian.csv")
    write(fx_list, "addl_frax_transfers.csv")
    write(pairs, "addl_frax_pairs.csv")


# ============================================================ C4 turnover vs redemptions; C5 netting buffer
def nmfp_daily_both():
    cls = {r["ticker"]: r["class_id"] for r in rd("freeze_nmfp_filings.csv")}   # as 05_measures.load (token class)
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


def task_c4_c5(c, roles):
    print("\n=== C4: holder-to-holder value vs N-MFP3 gross redemptions; C5: netting buffer ===")
    subs, reds = nmfp_daily_both()
    out4, out5 = [], []
    for fund in ("WTGXX", "FOBXX"):
        sred = sum(v for d, v in reds[fund].items() if WIN[0] <= d <= WIN[1])
        for wm, kw in (("with_wallet", {}), ("excl_wallet_numerator", dict(num_excl=wal))):
            if fund == "FOBXX" and wm != "with_wallet":
                continue
            m = m1_pooled(c, roles, fund, **kw)
            r_ = dict(fund=fund, window=WINS, wallet_mode=wm, hh_value_shares=m["value_lower"],
                      nmfp_gross_reds_sum=round(sred, 2),
                      n_nmfp_days=sum(1 for d in reds[fund] if WIN[0] <= d <= WIN[1]),
                      hh_value_share_of_gross_reds_pct=pct(m["value_lower"], sred),
                      upper_nonta_value_shares=m["value_upper"], upper_value_share_of_gross_reds_pct=pct(m["value_upper"], sred))
            out4.append(r_)
            print(f"  {fund} {wm}: hh value {r_['hh_value_shares']} / N-MFP3 gross reds {r_['nmfp_gross_reds_sum']} "
                  f"= {r_['hh_value_share_of_gross_reds_pct']}% (upper {r_['upper_value_share_of_gross_reds_pct']}%)")
            for k in ("hh_value_shares", "nmfp_gross_reds_sum", "hh_value_share_of_gross_reds_pct",
                      "upper_nonta_value_shares", "upper_value_share_of_gross_reds_pct"):
                sn(f"addl_turnred_{k}__{fund}__{wm}", r_[k], "pct" if k.endswith("pct") else
                   ("USD (N-MFP3)" if k.startswith("nmfp") else "shares"), WINS, "addl_turnover_vs_reds.csv")
    for fund in FUNDS:
        days = sorted(d for d in set(subs[fund]) | set(reds[fund]) if WIN[0] <= d <= WIN[1])
        both = [d for d in days if d in subs[fund] and d in reds[fund]]
        # convention (printed): a day with one leg not reported contributes min = 0 (nothing can net)
        ss = sum(subs[fund].get(d, 0.0) for d in days)
        rr = sum(reds[fund].get(d, 0.0) for d in days)
        mn = sum(min(subs[fund].get(d, 0.0), reds[fund].get(d, 0.0)) for d in days)
        r_ = dict(fund=fund, window=WINS, n_days=len(days), n_days_both_legs_reported=len(both),
                  sum_subs=round(ss, 2), sum_reds=round(rr, 2), sum_daily_min=round(mn, 2),
                  min_share_of_reds_pct=pct(mn, rr), min_share_of_subs_plus_reds_pct=pct(mn, ss + rr),
                  note=("N-MFP3 gross subscriptions include reinvested dividends (IMPL-13)" if fund in ("FOBXX", "JLTXX") else ""))
        out5.append(r_)
        print(f"  {fund}: days {len(days)} (both legs reported {len(both)}); sum min {r_['sum_daily_min']} / reds {r_['sum_reds']} = "
              f"{r_['min_share_of_reds_pct']}%; / (subs+reds) = {r_['min_share_of_subs_plus_reds_pct']}%")
        for k in ("min_share_of_reds_pct", "min_share_of_subs_plus_reds_pct", "sum_daily_min", "sum_reds", "sum_subs", "n_days"):
            sn(f"addl_netbuf_{k}__{fund}", r_[k], "pct" if k.endswith("pct") else "count" if k == "n_days" else "USD (N-MFP3)",
               WINS, "addl_netting_buffer.csv")
    chk = {r["fund"]: r for r in rd("addl_nmfp_netting_days.csv")}
    for r_ in out5:
        if r_["fund"] in chk:
            print(f"  cross-check {r_['fund']}: addl_nmfp_netting_days.csv netted_share_of_gross_flow_pct "
                  f"{chk[r_['fund']]['netted_share_of_gross_flow_pct']} (this script: {r_['min_share_of_subs_plus_reds_pct']})")
    write(out4, "addl_turnover_vs_reds.csv")
    write(out5, "addl_netting_buffer.csv")


# ============================================================ C6 cross-chain burn/mint pairs
def prev_bd(cal, d):
    d = d - dt.timedelta(days=1)
    while not cal.is_business(d):
        d -= dt.timedelta(days=1)
    return d


def task_c6(c, roles, labels):
    print("\n=== C6: same-amount burn on one chain and mint on another within 1 ET business day ===")
    lo_t, hi_t = R.cutoff(WIN[0] - dt.timedelta(days=1)), R.cutoff(WIN[1])
    TOL = 0.01      # shares
    pair_rows, summ = [], []
    for fund in ("FOBXX", "WTGXX"):
        chains = [ch for ch in sorted(c.chains_by_fund[fund]) if c.ev_by.get((fund, ch))]
        burns, mints = [], []
        for ch in chains:
            lb = labels[(fund, ch)]
            for i, e in enumerate(c.ev_by[(fund, ch)]):
                if e["kind"] == "burn" and lo_t < e["ts"] <= hi_t:
                    burns.append(dict(e, chain=ch, key=(ch, i)))
                elif e["kind"] == "mint":     # partner mints may fall just outside the window (edge days)
                    mints.append(dict(e, chain=ch, key=(ch, i), label=lb[e["id"]][0]))
        mints.sort(key=lambda e: e["amount"])
        amts = [e["amount"] for e in mints]

        def match(shift_days):
            """One-to-one greedy matching in burn-time order: partner = unused mint on another chain, amount within TOL,
            ET date from the previous to the next business day around the (shifted) burn date, nearest in time."""
            used, out = set(), []
            for b in sorted(burns, key=lambda e: e["ts"]):
                d = R.et_date(b["ts"]) + dt.timedelta(days=shift_days)
                lo_d, hi_d = prev_bd(c.cal, d), c.cal.next_business(d)
                i0, i1 = bisect.bisect_left(amts, b["amount"] - TOL), bisect.bisect_right(amts, b["amount"] + TOL)
                cand = [m for m in mints[i0:i1] if m["chain"] != b["chain"] and m["key"] not in used
                        and lo_d <= R.et_date(m["ts"]) <= hi_d]
                if cand:
                    m = min(cand, key=lambda m: abs((m["ts"] - b["ts"]).total_seconds() - shift_days * 86400))
                    used.add(m["key"])
                    out.append((b, m))
            return out
        real = match(0)
        placebo = match(10)     # chance baseline: the same test with each burn date moved 10 calendar days later
        for b, m in real:
            pair_rows.append(dict(fund=fund, burn_chain=b["chain"], burn_ts_utc=b["ts"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                                  burn_amount=b["amount"], burn_tx=b["tx"], mint_chain=m["chain"],
                                  mint_ts_utc=m["ts"].strftime("%Y-%m-%dT%H:%M:%SZ"), mint_amount=m["amount"], mint_tx=m["tx"],
                                  mint_label=m["label"], hours_mint_minus_burn=round((m["ts"] - b["ts"]).total_seconds() / 3600, 3),
                                  amount_band=("<1" if b["amount"] < 1 else "1-<100" if b["amount"] < 100 else
                                               "100-<10k" if b["amount"] < 1e4 else ">=10k")))

        def m2(excl_b, excl_m):
            """M2 as 05_measures (FOBXX and WTGXX are not TR-2 funds: burns dated at burn time)."""
            iv = rv = inb = rnb = 0.0
            for ch in chains:
                lb = labels[(fund, ch)]
                for i, e in enumerate(c.ev_by[(fund, ch)]):
                    if not (lo_t < e["ts"] <= hi_t):
                        continue
                    nb = not c.cal.is_business(R.et_date(e["ts"]))
                    if e["kind"] == "mint" and lb[e["id"]][0] == "issuance" and (ch, i) not in excl_m:
                        iv += e["amount"]
                        inb += e["amount"] if nb else 0.0
                    elif e["kind"] == "burn" and (ch, i) not in excl_b:
                        rv += e["amount"]
                        rnb += e["amount"] if nb else 0.0
            return dict(issuance_value=round(iv, 6), redemption_value=round(rv, 6), nonbusiness_issuance_value=round(inb, 6),
                        nonbusiness_redemption_value=round(rnb, 6), nonbusiness_share_pct=pct(inb + rnb, iv + rv))
        m2_base = m2(set(), set())
        f = next(r for r in rd("freeze_m2_calendar.csv") if r["fund"] == fund)
        for k in ("issuance_value", "redemption_value", "nonbusiness_share_pct"):
            if str(m2_base[k]) != f[k]:
                sys.exit(f"{fund} M2 replication differs on {k}: {m2_base[k]} vs {f[k]}")
        print(f"  {fund}: M2 replication vs freeze_m2_calendar.csv: IDENTICAL")
        # exclusion: the paired burns, and the paired mints where TR-6 labels them issuance (only those enter M2)
        m2_ex = m2({b["key"] for b, _ in real}, {m["key"] for _, m in real if m["label"] == "issuance"})
        bands = Counter(r["amount_band"] for r in pair_rows if r["fund"] == fund)
        bv = sum(b["amount"] for b in burns)
        row = dict(fund=fund, window=WINS, tolerance_shares=TOL, chains=";".join(chains), n_burns_window=len(burns),
                   value_burns_window=round(bv, 6), n_pairs=len(real),
                   value_pairs_burn_side=round(sum(b["amount"] for b, _ in real), 6),
                   pairs_share_of_burn_value_pct=pct(sum(b["amount"] for b, _ in real), bv),
                   n_pairs_mint_issuance=sum(1 for _, m in real if m["label"] == "issuance"),
                   n_pairs_mint_dividend=sum(1 for _, m in real if m["label"] == "dividend"),
                   pair_amount_bands=";".join(f"{k}:{v}" for k, v in sorted(bands.items())),
                   n_pairs_ge_100_shares=sum(1 for b, _ in real if b["amount"] >= 100),
                   value_pairs_ge_100_shares=round(sum(b["amount"] for b, _ in real if b["amount"] >= 100), 6),
                   chain_pairs=";".join(f"{a}->{b_}:{n_}" for (a, b_), n_ in
                                        sorted(Counter((b["chain"], m["chain"]) for b, m in real).items())),
                   placebo_shift10d_n_pairs=len(placebo),
                   placebo_shift10d_n_pairs_ge_100_shares=sum(1 for b, _ in placebo if b["amount"] >= 100),
                   placebo_shift10d_value=round(sum(b["amount"] for b, _ in placebo), 6),
                   **{f"m2_frozen_{k}": v for k, v in m2_base.items()},
                   **{f"m2_excl_pairs_{k}": v for k, v in m2_ex.items()})
        summ.append(row)
        print(f"  {fund}: burns {len(burns)}; pairs {len(real)} (value {row['value_pairs_burn_side']}, "
              f"{row['pairs_share_of_burn_value_pct']}% of burn value; >=100 shares {row['n_pairs_ge_100_shares']}, "
              f"value {row['value_pairs_ge_100_shares']}); bands {row['pair_amount_bands']}; chain pairs {row['chain_pairs']}; "
              f"placebo {len(placebo)} (>=100: {row['placebo_shift10d_n_pairs_ge_100_shares']}, value {row['placebo_shift10d_value']})")
        print(f"    M2 non-business share frozen {m2_base['nonbusiness_share_pct']}% -> excluding pairs "
              f"{m2_ex['nonbusiness_share_pct']}%; issuance {m2_base['issuance_value']} -> {m2_ex['issuance_value']}; "
              f"redemption {m2_base['redemption_value']} -> {m2_ex['redemption_value']}")
        for k in ("n_burns_window", "n_pairs", "value_pairs_burn_side", "pairs_share_of_burn_value_pct", "n_pairs_mint_issuance",
                  "n_pairs_mint_dividend", "n_pairs_ge_100_shares", "value_pairs_ge_100_shares", "placebo_shift10d_n_pairs",
                  "placebo_shift10d_n_pairs_ge_100_shares", "placebo_shift10d_value", "chain_pairs", "pair_amount_bands",
                  "m2_frozen_nonbusiness_share_pct", "m2_excl_pairs_nonbusiness_share_pct", "m2_excl_pairs_issuance_value",
                  "m2_excl_pairs_redemption_value"):
            u = ("pct" if k.endswith("pct") else "text" if k in ("chain_pairs", "pair_amount_bands") else
                 "shares" if "value" in k else "count")
            sn(f"addl_xchain_{k}__{fund}", row[k], u, WINS, "addl_xchain_summary.csv")
    write(pair_rows, "addl_xchain_pairs.csv")
    write(summ, "addl_xchain_summary.csv")


def main():
    a = argparse.Namespace(prefix="freeze", stellar_tag=STAG, stellar_rules_start="2026-01-01")
    c = M.load(a)
    roles, labels = M.classify(c, dict(M.MAIN))
    check_classification(c, roles, labels)
    task_c1(c, roles, labels)
    task_c2(c, roles, labels)
    task_c3(c, roles)
    task_c4_c5(c, roles)
    task_c6(c, roles, labels)
    names = Counter(r["name"] for r in SN)
    assert not [n for n, k in names.items() if k > 1], "duplicate names"
    assert all(r["name"].startswith("addl_") for r in SN)
    write(SN, "addl_summary_numbers_19.csv")


if __name__ == "__main__":
    main()
