"""22_addl2_cashlegs.py -- ADDITION 2 2026-10-18 (exploratory, post-freeze, CP65): decode cash legs

Reads (no fetch):
  data/raw/addition_2026-10-18/evm_receipts.jsonl   (21_addl2_fetch_receipts.py; parsed as JSON data only)
  data/raw/addition_2026-10-18/evm_tokencalls.jsonl (name/symbol/decimals eth_call responses)
  data/raw/addition_2026-10-18/labels/*.html        (Etherscan <title> public name tag, text only)
  data/processed/addl_swap_transfers.csv            (script 19 C1: 28 WTGXX swap-routed transfers)
  data/processed/addl_frax_transfers.csv            (script 19 C3: 22 WTGXX transfers with the Frax custodian)
  data/raw/issuer/2026-10-16_wisdomtree_wtgxx_fund_details.pdf via 03_onchain_pilot.issuer_addresses()
                                                    (WTGXX Ethereum contract, issuer-verified)
Writes (data/processed/):
  addl2_tokens.csv          one row per ERC-20 Transfer emitter: address, name(), symbol(), decimals(), Etherscan title
  addl2_legs.csv            one row per decoded ERC-20 Transfer log in every receipt (all tokens)
  addl2_tx_pairs.csv        one row per (route, tx, non-WTGXX token): direct and third-party flows, implied price
  addl2_tx.csv              one row per (route, tx): WTGXX leg, receipt match, pairing status
  addl2_summary.csv         one row per route x token (and route x ALL)
  addl2_summary_numbers_22.csv  key numbers (addl2_*), appended to key_numbers.csv by 07 (ADDITION 2 block)

Definitions
  WTGXX leg      the transfer listed in the script-19 CSV (frm F, to T, amount A). It is matched in the receipt
                 to a Transfer log of the issuer-verified WTGXX contract with the same from, to and amount
                 (raw integer / 10**decimals, |diff| <= 1e-9 * max(1, A)).
  ERC-20 log     topic0 = keccak("Transfer(address,address,uint256)") and exactly 3 topics (ERC-721 has 4);
                 amount = uint256 data / 10**decimals(), decimals from the logged decimals() eth_call.
  Direct pair    a non-WTGXX ERC-20 leg in the same tx that goes INTO F (including a mint from 0x0) or OUT OF T
                 (including a burn to 0x0). Per token: net_to_sender = (inflow to F) - (outflow from F);
                 net_from_recipient = (outflow from T) - (inflow to T).
  Direction      "sender_receives" if net_to_sender > 0 (WTGXX given for the token);
                 "recipient_pays" if not, and net_from_recipient > 0; "none" otherwise.
  Implied price  token units per WTGXX share: net_to_sender / A for "sender_receives",
                 net_from_recipient / A for "recipient_pays". gross_price_paid = net_from_recipient / A is
                 also reported when positive (it includes fee legs paid by T to third parties).
                 Prices are in TOKEN UNITS, not USD; no USD conversion is made.
  Third-party    non-WTGXX legs in the same tx that touch neither F nor T (0x0 counts as neither, so a mint to a
                 third party is third-party). Totals are reported; no price is computed for them.
  Atomic         every leg is in the same transaction receipt (status 0x1), so it settles in the same EVM
                 transaction or not at all. This says nothing about off-chain redemption of the paired token.

Run: python -I scripts/22_addl2_cashlegs.py, then python -I scripts/07_key_numbers.py
"""
import csv
import importlib.util
import json
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("pilot03", ROOT / "scripts" / "03_onchain_pilot.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)
PROC = ROOT / "data" / "processed"
RAW = ROOT / "data" / "raw" / "addition_2026-10-18"
TRANSFER = P.TRANSFER
ZERO = P.ZERO
CHAIN = "ethereum"
WINDOW = "2026-01-01:2026-09-30"
ASOF = f"{WINDOW} (receipts accessed 2026-10-18)"


def rd(name):
    with open(PROC / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write(rows, name, fields=None):
    fields = fields or list(dict.fromkeys(k for r in rows for k in r))
    with open(PROC / name, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {name}: {len(rows)} rows")


def abi_string(hexs):
    """Decode an ABI-encoded string return (offset, length, bytes). Falls back to bytes32."""
    b = bytes.fromhex(hexs[2:])
    if len(b) >= 64:
        off = int.from_bytes(b[:32], "big")
        if off + 32 <= len(b):
            n = int.from_bytes(b[off:off + 32], "big")
            return b[off + 32: off + 32 + n].decode("utf-8", errors="replace")
    return b.rstrip(b"\x00").decode("utf-8", errors="replace")


def addr(topic):
    return "0x" + topic[-40:].lower()


def main():
    # ---- issuer-verified WTGXX Ethereum contract
    wt = [t for t in P.issuer_addresses() if t["fund"] == "WTGXX" and t["chain"] == CHAIN]
    assert len(wt) == 1
    WT = wt[0]["address"].lower()
    print(f"WTGXX Ethereum contract (issuer PDF): {WT}")

    # ---- token metadata (logged eth_calls)
    calls = defaultdict(dict)
    for line in open(RAW / "evm_tokencalls.jsonl", encoding="utf-8"):
        d = json.loads(line)
        a = d["params"][1]
        res = d["response"].get("result") if isinstance(d["response"], dict) else None
        calls[a][d["method"].replace("eth_call_", "")] = res
    tokens = {}
    for a, c in sorted(calls.items()):
        title = ""
        lp = RAW / "labels" / f"{CHAIN}_{a}.html"
        title = __import__("source_facts").label_title(lp)
        tokens[a] = dict(address=a, name=abi_string(c["name"]) if c.get("name") else "",
                         symbol=abi_string(c["symbol"]) if c.get("symbol") else "",
                         decimals=int(c["decimals"], 16) if c.get("decimals") else "",
                         etherscan_title=title, is_issuer_verified_wtgxx=int(a == WT))
    assert WT in tokens and tokens[WT]["decimals"] == 18, "WTGXX decimals() must be 18 (data/README.md)"
    for t in tokens.values():
        print(f"  token {t['address']}: name={t['name']!r} symbol={t['symbol']!r} decimals={t['decimals']} "
              f"etherscan_title={t['etherscan_title']!r}")
    write(list(tokens.values()), "addl2_tokens.csv")
    sym = {a: t["symbol"] for a, t in tokens.items()}

    # ---- receipts
    rec = {}
    for line in open(RAW / "evm_receipts.jsonl", encoding="utf-8"):
        d = json.loads(line)
        rec[d["params"][1].lower()] = d["response"]["result"]
    print(f"receipts held: {len(rec)}")

    # ---- WTGXX legs per route (script-19 processed inputs)
    swap = rd("addl_swap_transfers.csv")
    frax = rd("addl_frax_transfers.csv")
    frax_w = [r for r in frax if r["chain"] == CHAIN and "2026-01-01" <= r["ts_et"][:10] <= "2026-09-30"]
    print(f"swap rows {len(swap)}; custodian rows {len(frax)} -> {len(frax_w)} after chain/window filter")
    legs_in = [dict(route="swap", tx=r["tx"].lower(), ts_utc=r["ts_utc"], frm=r["frm"].lower(), to=r["to"].lower(),
                    amount=float(r["amount"]), custodian_direction="") for r in swap]
    legs_in += [dict(route="custodian", tx=r["tx"].lower(), ts_utc=r["ts_utc"],
                     frm=(P_FRAX if r["direction"].startswith("out") else r["counterparty"].lower()),
                     to=(r["counterparty"].lower() if r["direction"].startswith("out") else P_FRAX),
                     amount=float(r["amount"]), custodian_direction=r["direction"]) for r in frax_w]
    overlap = {l["tx"] for l in legs_in if l["route"] == "swap"} & {l["tx"] for l in legs_in if l["route"] == "custodian"}
    print(f"tx in both routes: {len(overlap)}")
    c = Counter((l["route"], l["tx"]) for l in legs_in)
    assert max(c.values()) == 1, "one WTGXX leg per (route, tx) expected"

    # ---- every ERC-20 Transfer log
    all_legs = []
    other_topics = Counter()
    for h, r in sorted(rec.items()):
        for lg in r["logs"]:
            t = lg["topics"]
            if t and t[0].lower() == TRANSFER and len(t) == 3:
                a = lg["address"].lower()
                dec = tokens[a]["decimals"]
                raw = int(lg["data"], 16)
                all_legs.append(dict(tx=h, block=int(r["blockNumber"], 16), log_index=int(lg["logIndex"], 16),
                                     token=a, symbol=sym[a], frm=addr(t[1]), to=addr(t[2]), raw_amount=raw,
                                     amount=raw / 10 ** dec, is_wtgxx=int(a == WT),
                                     is_mint=int(addr(t[1]) == ZERO), is_burn=int(addr(t[2]) == ZERO)))
            else:
                other_topics[(lg["address"].lower(), t[0].lower() if t else "", len(t))] += 1
    print(f"decoded ERC-20 Transfer logs: {len(all_legs)}; other (undecoded) logs: {sum(other_topics.values())}")
    write(all_legs, "addl2_legs.csv")
    by_tx = defaultdict(list)
    for l in all_legs:
        by_tx[l["tx"]].append(l)

    # ---- per (route, tx)
    tx_rows, pair_rows = [], []
    for L in legs_in:
        h, F, T, A = L["tx"], L["frm"], L["to"], L["amount"]
        r = rec.get(h)
        logs = by_tx.get(h, [])
        match = [l for l in logs if l["is_wtgxx"] and l["frm"] == F and l["to"] == T
                 and abs(l["amount"] - A) <= 1e-9 * max(1.0, A)]
        other_wt = [l for l in logs if l["is_wtgxx"] and l not in match[:1]]
        non = [l for l in logs if not l["is_wtgxx"]]
        pairs_here = []
        for tok in sorted({l["token"] for l in non}):
            tl = [l for l in non if l["token"] == tok]
            in_F = sum(l["amount"] for l in tl if l["to"] == F)
            out_F = sum(l["amount"] for l in tl if l["frm"] == F)
            out_T = sum(l["amount"] for l in tl if l["frm"] == T)
            in_T = sum(l["amount"] for l in tl if l["to"] == T)
            third = [l for l in tl if l["frm"] not in (F, T) and l["to"] not in (F, T)]
            n_direct = sum(1 for l in tl if l["to"] == F or l["frm"] == T)
            nts, nfr = in_F - out_F, out_T - in_T
            if nts > 0:
                dirn, price = "sender_receives", nts / A
            elif nfr > 0:
                dirn, price = "recipient_pays", nfr / A
            else:
                dirn, price = "none", None
            pr = dict(route=L["route"], tx=h, token=tok, symbol=sym[tok], wtgxx_from=F, wtgxx_to=T, wtgxx_amount=A,
                      n_legs=len(tl), n_direct_legs=n_direct, n_third_party_legs=len(third),
                      n_mint_legs=sum(l["is_mint"] for l in tl), n_burn_legs=sum(l["is_burn"] for l in tl),
                      net_to_sender=round(nts, 12), net_from_recipient=round(nfr, 12),
                      third_party_amount=round(sum(l["amount"] for l in third), 12),
                      third_party_recipients=";".join(sorted({l["to"] for l in third})),
                      direct_counterparties=";".join(sorted({l["frm"] for l in tl if l["to"] == F} |
                                                            {l["to"] for l in tl if l["frm"] == T})),
                      direction=dirn, implied_price=round(price, 10) if price is not None else "",
                      gross_price_paid_by_recipient=round(nfr / A, 10) if nfr > 0 else "",
                      atomic_same_tx=int(dirn != "none" and r is not None and r.get("status") == "0x1"))
            pair_rows.append(pr)
            pairs_here.append(pr)
        direct = [p for p in pairs_here if p["direction"] != "none"]
        tx_rows.append(dict(route=L["route"], tx=h, ts_utc=L["ts_utc"], custodian_direction=L["custodian_direction"],
                            wtgxx_from=F, wtgxx_to=T, wtgxx_amount=A,
                            receipt_held=int(r is not None), receipt_status=(r or {}).get("status", ""),
                            tx_from=(r or {}).get("from", ""), tx_to=(r or {}).get("to", ""),
                            wtgxx_leg_matched=int(len(match) >= 1), n_other_wtgxx_legs=len(other_wt),
                            other_wtgxx_legs=";".join(f"{l['frm']}->{l['to']}:{l['amount']}" for l in other_wt),
                            n_non_wtgxx_legs=len(non), any_other_token=int(bool(non)),
                            direct_pair=int(bool(direct)),
                            direct_tokens=";".join(p["symbol"] for p in direct),
                            third_party_only_tokens=";".join(p["symbol"] for p in pairs_here if p["direction"] == "none"),
                            atomic_same_tx=int(any(p["atomic_same_tx"] for p in direct))))
    write(tx_rows, "addl2_tx.csv")
    write(pair_rows, "addl2_tx_pairs.csv")

    # ---- integrity prints
    print(f"\nreceipts held for {sum(t['receipt_held'] for t in tx_rows)}/{len(tx_rows)} legs; "
          f"status 0x1: {sum(t['receipt_status'] == '0x1' for t in tx_rows)}; "
          f"WTGXX leg matched in receipt: {sum(t['wtgxx_leg_matched'] for t in tx_rows)}")

    # ---- summary
    summ, KN = [], []

    def kn(name, value, unit, source):
        KN.append(dict(name=name, value=value, unit=unit, as_of=ASOF, source_file=f"data/processed/{source}"))

    for route in ("swap", "custodian"):
        tr = [t for t in tx_rows if t["route"] == route]
        n, nd, na = len(tr), sum(t["direct_pair"] for t in tr), sum(t["any_other_token"] for t in tr)
        summ.append(dict(route=route, token="ALL", symbol="ALL", n_tx=n, n_tx_direct_pair=nd, n_tx_any_other_token=na,
                         n_tx_no_other_token=n - na, n_tx_third_party_only=na - nd,
                         wtgxx_amount_all=round(sum(t["wtgxx_amount"] for t in tr), 12),
                         wtgxx_amount_direct=round(sum(t["wtgxx_amount"] for t in tr if t["direct_pair"]), 12),
                         n_tx_atomic=sum(t["atomic_same_tx"] for t in tr)))
        kn(f"addl2_{route}_n_tx", n, "count", "addl2_summary.csv")
        kn(f"addl2_{route}_n_tx_direct_pair", nd, "count", "addl2_summary.csv")
        kn(f"addl2_{route}_n_tx_any_other_token", na, "count", "addl2_summary.csv")
        kn(f"addl2_{route}_n_tx_no_other_token", n - na, "count", "addl2_summary.csv")
        kn(f"addl2_{route}_n_tx_third_party_only", na - nd, "count", "addl2_summary.csv")
        kn(f"addl2_{route}_n_tx_atomic_direct", sum(t["atomic_same_tx"] for t in tr), "count", "addl2_summary.csv")
        kn(f"addl2_{route}_wtgxx_amount_all", round(sum(t["wtgxx_amount"] for t in tr), 6), "WTGXX shares", "addl2_summary.csv")
        print(f"\n[{route}] tx {n}; direct pair {nd}; any other token {na}; no other token {n - na}; "
              f"third-party only {na - nd}")
        for tok in sorted({p["token"] for p in pair_rows if p["route"] == route}):
            pr = [p for p in pair_rows if p["route"] == route and p["token"] == tok]
            dp = [p for p in pr if p["direction"] != "none"]
            prices = [p["implied_price"] for p in dp]
            s = sym[tok]
            row = dict(route=route, token=tok, symbol=s, name=tokens[tok]["name"], n_tx=len(pr), n_tx_direct_pair=len(dp),
                       n_tx_sender_receives=sum(p["direction"] == "sender_receives" for p in dp),
                       n_tx_recipient_pays=sum(p["direction"] == "recipient_pays" for p in dp),
                       n_tx_third_party_only=len(pr) - len(dp),
                       token_amount_direct=round(sum(p["net_to_sender"] if p["direction"] == "sender_receives"
                                                     else p["net_from_recipient"] for p in dp), 12),
                       wtgxx_amount_direct=round(sum(p["wtgxx_amount"] for p in dp), 12),
                       token_amount_third_party=round(sum(p["third_party_amount"] for p in pr), 12),
                       price_min=min(prices) if prices else "", price_median=statistics.median(prices) if prices else "",
                       price_max=max(prices) if prices else "",
                       n_tx_atomic=sum(p["atomic_same_tx"] for p in dp))
            gross = [p["gross_price_paid_by_recipient"] for p in dp if p["gross_price_paid_by_recipient"] != ""]
            row.update(n_tx_gross_price=len(gross), gross_price_min=min(gross) if gross else "",
                       gross_price_median=statistics.median(gross) if gross else "", gross_price_max=max(gross) if gross else "")
            summ.append(row)
            k = f"addl2_{route}_{s}"
            kn(f"{k}_n_tx_direct_pair", row["n_tx_direct_pair"], "count", "addl2_summary.csv")
            kn(f"{k}_n_tx_sender_receives", row["n_tx_sender_receives"], "count", "addl2_summary.csv")
            kn(f"{k}_n_tx_recipient_pays", row["n_tx_recipient_pays"], "count", "addl2_summary.csv")
            kn(f"{k}_n_tx_third_party_only", row["n_tx_third_party_only"], "count", "addl2_summary.csv")
            kn(f"{k}_token_amount_direct", round(row["token_amount_direct"], 6), f"{s} token units", "addl2_summary.csv")
            kn(f"{k}_wtgxx_amount_direct", round(row["wtgxx_amount_direct"], 6), "WTGXX shares", "addl2_summary.csv")
            kn(f"{k}_token_amount_third_party", round(row["token_amount_third_party"], 6), f"{s} token units", "addl2_summary.csv")
            if prices:
                kn(f"{k}_price_min", round(row["price_min"], 6), f"{s} per WTGXX share", "addl2_summary.csv")
                kn(f"{k}_price_median", round(row["price_median"], 6), f"{s} per WTGXX share", "addl2_summary.csv")
                kn(f"{k}_price_max", round(row["price_max"], 6), f"{s} per WTGXX share", "addl2_summary.csv")
            kn(f"{k}_n_tx_atomic", row["n_tx_atomic"], "count", "addl2_summary.csv")
            if gross:   # fee-inclusive: what the WTGXX recipient paid in total, per WTGXX share
                kn(f"{k}_n_tx_gross_price", len(gross), "count", "addl2_summary.csv")
                kn(f"{k}_gross_price_min", round(min(gross), 6), f"{s} per WTGXX share", "addl2_summary.csv")
                kn(f"{k}_gross_price_median", round(statistics.median(gross), 6), f"{s} per WTGXX share", "addl2_summary.csv")
                kn(f"{k}_gross_price_max", round(max(gross), 6), f"{s} per WTGXX share", "addl2_summary.csv")
            print(f"  {s:7} ({tok[:10]}) tx {len(pr)}: direct {len(dp)} (sender_receives {row['n_tx_sender_receives']}, "
                  f"recipient_pays {row['n_tx_recipient_pays']}), third-party only {row['n_tx_third_party_only']}; "
                  f"direct {s} {row['token_amount_direct']:,.6f} for {row['wtgxx_amount_direct']:,.6f} WTGXX; "
                  f"third-party {s} {row['token_amount_third_party']:,.6f}; "
                  f"price min/med/max {row['price_min']}/{row['price_median']}/{row['price_max']}; atomic {row['n_tx_atomic']}")
    write(summ, "addl2_summary.csv")

    # ---- settlement patterns: group legs by (route, WTGXX from, WTGXX to, direct tokens, third-party-only tokens)
    pat = defaultdict(list)
    for t in tx_rows:
        pat[(t["route"], t["wtgxx_from"], t["wtgxx_to"], t["direct_tokens"], t["third_party_only_tokens"])].append(t)
    prow = []
    for (route, F, T, dt_, tp), ts in sorted(pat.items(), key=lambda x: (x[0][0], -len(x[1]))):
        hs = {t["tx"] for t in ts}
        pp = [p for p in pair_rows if p["tx"] in hs and p["route"] == route]
        prow.append(dict(route=route, wtgxx_from=F, wtgxx_to=T, direct_tokens=dt_, third_party_only_tokens=tp, n_tx=len(ts),
                         wtgxx_amount=round(sum(t["wtgxx_amount"] for t in ts), 12),
                         direct_token_amount=round(sum(p["net_to_sender"] if p["direction"] == "sender_receives" else
                                                       p["net_from_recipient"] if p["direction"] == "recipient_pays" else 0
                                                       for p in pp), 12),
                         direct_counterparties=";".join(sorted({c for p in pp for c in p["direct_counterparties"].split(";") if c})),
                         third_party_recipients=";".join(sorted({c for p in pp for c in p["third_party_recipients"].split(";") if c})),
                         third_party_amount=round(sum(p["third_party_amount"] for p in pp), 12),
                         other_wtgxx_hops_to=";".join(sorted({x.split("->")[1].split(":")[0] for t in ts
                                                              for x in t["other_wtgxx_legs"].split(";") if x})),
                         first_ts_utc=min(t["ts_utc"] for t in ts), last_ts_utc=max(t["ts_utc"] for t in ts)))
    write(prow, "addl2_patterns.csv")
    # derived numbers quoted in notes/addl_analysis.md D1 (so that none is hand-computed)
    sw = [t for t in tx_rows if t["route"] == "swap"]
    sw_usdc = [t for t in sw if t["direct_tokens"] == "USDC"]
    sw_tsy = [t for t in sw if t["direct_tokens"] == "TSY"]
    top = Counter(t["wtgxx_from"] for t in sw_usdc) + Counter(t["wtgxx_to"] for t in sw_usdc)
    w0 = top.most_common(1)[0][0]
    kn("addl2_swap_USDC_top_wallet_n_sells", sum(t["wtgxx_from"] == w0 for t in sw_usdc), f"count (wallet {w0})", "addl2_tx.csv")
    kn("addl2_swap_USDC_top_wallet_n_buys", sum(t["wtgxx_to"] == w0 for t in sw_usdc), f"count (wallet {w0})", "addl2_tx.csv")
    kn("addl2_swap_USDC_median_wtgxx_amount", statistics.median(t["wtgxx_amount"] for t in sw_usdc), "WTGXX shares", "addl2_tx.csv")
    kn("addl2_swap_TSY_share_of_route_wtgxx_pct",
       round(100 * sum(t["wtgxx_amount"] for t in sw_tsy) / sum(t["wtgxx_amount"] for t in sw), 4), "pct", "addl2_tx.csv")
    cu = [t for t in tx_rows if t["route"] == "custodian"]
    snd = [t for t in cu if t["wtgxx_to"] == P_FRAX and t["direct_tokens"] == "frxUSD"]
    kn("addl2_custodian_frxUSD_deposits_n_tx", len(snd), "count", "addl2_tx.csv")
    kn("addl2_custodian_frxUSD_deposits_wtgxx_amount", round(sum(t["wtgxx_amount"] for t in snd), 6), "WTGXX shares", "addl2_tx.csv")
    nol = [t for t in cu if not t["any_other_token"]]
    kn("addl2_custodian_no_token_leg_wtgxx_amount", round(sum(t["wtgxx_amount"] for t in nol), 6), "WTGXX shares", "addl2_tx.csv")
    print(f"  top USDC-swap wallet {w0}: sells {sum(t['wtgxx_from'] == w0 for t in sw_usdc)}, buys {sum(t['wtgxx_to'] == w0 for t in sw_usdc)}; "
          f"median USDC-swap size {statistics.median(t['wtgxx_amount'] for t in sw_usdc)}; "
          f"TSY share of swap WTGXX {100 * sum(t['wtgxx_amount'] for t in sw_tsy) / sum(t['wtgxx_amount'] for t in sw):.4f}%; "
          f"frxUSD deposits {len(snd)} tx {sum(t['wtgxx_amount'] for t in snd):,.6f}; no-token-leg WTGXX {sum(t['wtgxx_amount'] for t in nol):,.6f}")
    for r in prow:
        print(f"  pattern {r['route']:9} {r['wtgxx_from'][:10]}->{r['wtgxx_to'][:10]} direct={r['direct_tokens'] or '-'} "
              f"third={r['third_party_only_tokens'] or '-'} n={r['n_tx']} WTGXX={r['wtgxx_amount']:,.6f} "
              f"direct_amt={r['direct_token_amount']:,.6f} third_amt={r['third_party_amount']:,.6f} hops->{r['other_wtgxx_hops_to'] or '-'}")
    names = Counter(r["name"] for r in KN)
    assert max(names.values()) == 1, [n for n, c in names.items() if c > 1]
    write(KN, "addl2_summary_numbers_22.csv")
    print("\nundecoded log types (emitter, topic0, n_topics): count")
    for (a, t0, nt), cnt in sorted(other_topics.items(), key=lambda x: -x[1]):
        print(f"  {sym.get(a, a[:10])} {t0[:10]} {nt}: {cnt}")


P_FRAX = "0x860cc723935fc9a15ff8b1a94237a711dfef7857"

if __name__ == "__main__":
    main()
