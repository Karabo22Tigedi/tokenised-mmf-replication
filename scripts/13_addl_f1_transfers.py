"""13_addl_f1_transfers.py -- ADDITION 2026-10-17 (exploratory, post-freeze, user-approved CP35)

Rebuilds the list of events behind the pre-registered F1 measure (M1, pooled, transfers_only basis,
window 2026-01-01..2026-09-30 ET) from FROZEN inputs only, and checks that the rebuilt shares equal
data/processed/freeze_m1_secondary.csv exactly (to the 4 decimals written there). Nothing frozen is
changed. The list is the input to 14_addl_fetch.py (signer / function fetch) and 15_addl_analysis.py.

Inputs (frozen): freeze_evm_events.csv, freeze_solana_events.csv, freeze_wallet_roles.csv,
                 freeze_m1_secondary.csv, data/raw/stellar/freeze_2026-10-16/ (via stellar_events.py)
Output: data/processed/addl_f1_events.csv -- one row per transfer event in the window, every fund and
        chain with events, with role_from, role_to, pair (hh/ch/cc/..T..) and nonta (1 if no TA wallet
        takes part, i.e. counted in the F1 upper-bound numerator; hh = lower bound).

Run: python -I scripts/13_addl_f1_transfers.py
"""
import csv
import datetime as dt
import importlib.util
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import rules as R            # noqa: E402
import stellar_events as SE  # noqa: E402

spec = importlib.util.spec_from_file_location("pilot03", ROOT / "scripts" / "03_onchain_pilot.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)
PROC = ROOT / "data" / "processed"
STAG = ROOT / "data" / "raw" / "stellar" / "freeze_2026-10-16"
W0, W1 = dt.date(2026, 1, 1), dt.date(2026, 9, 30)
LO, HI = R.cutoff(W0 - dt.timedelta(days=1)), R.cutoff(W1)      # same as 05_measures.window_measures


def rd(name):
    with open(PROC / name, newline="") as f:
        return list(csv.DictReader(f))


def load_events():
    """All frozen events (EVM, Solana full history; Stellar from the 2026-01-01 ET rules start)."""
    evs = []
    for name in ("freeze_evm_events.csv", "freeze_solana_events.csv"):
        for r in rd(name):
            evs.append(dict(fund=r["fund"], chain=r["chain"], token=r["token"], id=r["id"], ts=SE.ts(r["ts_utc"]),
                            frm=r["frm"], to=r["to"], amount=float(r["amount"]), kind=r["kind"], tx=r["tx"],
                            op_type=""))
    for t in P.issuer_addresses():
        if t["chain"] != "stellar":
            continue
        se, _ = SE.load(STAG, t["token"], t["address"])
        start = max(SE.crawl_start(STAG), dt.datetime.combine(W0, dt.time(0), R.ET).astimezone(R.UTC))
        for e in se:
            if e["ts"] >= start:     # rules start 2026-01-01 ET, as 05_measures (IMPL-1b)
                evs.append(dict(e, fund=t["fund"], chain="stellar", token=t["token"], id=str(e["id"])))
    return evs


def main():
    evs = load_events()
    print(f"events loaded (all kinds, all dates): {len(evs)}")
    win = [e for e in evs if LO < e["ts"] <= HI]
    print(f"in window {W0}..{W1} ET: {len(win)}")
    tr = [e for e in win if e["kind"] == "transfer"]
    print(f"transfers in window (transfers_only basis): {len(tr)}")

    roles = {(r["fund"], r["chain"], r["address"]): r["role"] for r in rd("freeze_wallet_roles.csv")}
    out, agg = [], Counter()
    for e in sorted(tr, key=lambda e: (e["fund"], e["chain"], e["ts"], e["id"])):
        ra = roles.get((e["fund"], e["chain"], e["frm"]), "holder")
        rb = roles.get((e["fund"], e["chain"], e["to"]), "holder")
        pair = "".join(sorted([ra[0], rb[0]]))
        nonta = "T" not in pair
        out.append(dict(fund=e["fund"], chain=e["chain"], token=e["token"], id=e["id"],
                        ts_utc=e["ts"].strftime("%Y-%m-%dT%H:%M:%SZ"), frm=e["frm"], to=e["to"],
                        amount=e["amount"], tx=e["tx"], op_type=e.get("op_type", ""),
                        role_from=ra, role_to=rb, pair=pair, nonta=int(nonta)))
        k = e["fund"]
        agg[(k, "n")] += 1
        agg[(k, "v")] += e["amount"]
        if pair == "hh":
            agg[(k, "lo_n")] += 1
            agg[(k, "lo_v")] += e["amount"]
        if nonta:
            agg[(k, "up_n")] += 1
            agg[(k, "up_v")] += e["amount"]

    # reproduction check against the frozen M1 (pooled, transfers_only)
    frozen = {r["fund"]: r for r in rd("freeze_m1_secondary.csv")
              if r["chain_scope"] == "pooled" and r["basis"] == "transfers_only"}
    pct = lambda a, b: round(100 * a / b, 4) if b else ""
    ok = True
    for fund, fr in sorted(frozen.items()):
        mine = dict(n_events=agg[(fund, "n")],
                    lower_share_value_pct=pct(agg[(fund, "lo_v")], agg[(fund, "v")]),
                    lower_share_count_pct=pct(agg[(fund, "lo_n")], agg[(fund, "n")]),
                    upper_share_value_pct=pct(agg[(fund, "up_v")], agg[(fund, "v")]),
                    upper_share_count_pct=pct(agg[(fund, "up_n")], agg[(fund, "n")]))
        for k, v in mine.items():
            fv = fr[k]
            same = (str(v) == fv) if k == "n_events" else (fv == "" and v == "") or (fv != "" and v != "" and abs(float(fv) - v) < 1e-9)
            if not same:
                ok = False
            print(f"  {fund} {k}: rebuilt {v} frozen {fv} {'OK' if same else 'MISMATCH'}")
    if not ok:
        sys.exit("rebuild does not reproduce freeze_m1_secondary.csv; refusing to write")
    with open(PROC / "addl_f1_events.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)
    print(f"wrote addl_f1_events.csv: {len(out)} rows, of which non-TA (F1 upper numerator) "
          f"{sum(r['nonta'] for r in out)}")
    for (fund, ch), n in sorted(Counter((r["fund"], r["chain"]) for r in out if r["nonta"]).items()):
        print(f"  non-TA transfers {fund} {ch}: {n}")


if __name__ == "__main__":
    main()
