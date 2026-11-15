"""stellar_events.py -- normalise a 03b Horizon crawl into rules.py event records

Imported by 05_measures.py. Reads only data/raw/stellar/<TAG>/ (raw Horizon pages).

Mapping (IMPL-1): on Stellar the issuer account plays the role of the zero address:
  mint     = payment / path payment FROM the issuer (frm = issuer)
  mint     = create_claimable_balance by the issuer (to = 'CLAIMABLE:<op id>'; counted in supply,
             as Horizon's supply includes claimable_balances_amount)
  burn     = payment / path payment TO the issuer, or clawback (frm = the clawed-back account)
  transfer = payment / path payment of the asset between two non-issuer accounts
Path payments are converted only when the asset is the delivered asset and the source asset
(or the reverse leg when only the source asset is ours); legs that convert into another asset
(DEX) are counted in diagnostics, not converted. claim_claimable_balance,
clawback_claimable_balance, liquidity-pool and Soroban (invoke_host_function) ops that touch the
asset are not converted and are COUNTED in diagnostics, so any omission is visible.
Each op appears once (deduplicated by Horizon op id) even if crawled from several accounts.
Failed transactions are excluded (transaction_successful is false).
"""
import datetime as dt
import gzip
import json
from collections import Counter
from pathlib import Path


def read_pages(path):
    txt = gzip.open(path).read().decode("utf-8")
    dec, i, n = json.JSONDecoder(), 0, len(txt)
    while i < n:
        while i < n and txt[i].isspace():
            i += 1
        if i >= n:
            break
        obj, i = dec.raw_decode(txt, i)
        yield obj


def ts(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def is_asset(r, code, issuer, prefix=""):
    return r.get(f"{prefix}asset_code") == code and r.get(f"{prefix}asset_issuer") == issuer


def load(crawl_dir: Path, code: str, issuer: str):
    """Return (events, diag). events: rules.py records with extra 'op_type'."""
    seen, events, diag = set(), [], Counter()
    for f in sorted((crawl_dir / "ops").glob("*.jsonl.gz")):
        for pg in read_pages(f):
            for r in pg.get("_embedded", {}).get("records", []):
                oid = r["id"]
                if oid in seen:
                    continue
                t = r["type"]
                touches = (is_asset(r, code, issuer) or is_asset(r, code, issuer, "source_")
                           or str(r.get("asset", "")).startswith(f"{code}:{issuer}"))
                if not touches and t not in ("claim_claimable_balance", "clawback_claimable_balance",
                                             "invoke_host_function"):
                    continue
                seen.add(oid)
                if not r.get("transaction_successful", True):
                    diag[f"failed_tx:{t}"] += 1
                    continue
                base = dict(id=int(r["paging_token"]), ts=ts(r["created_at"]), tx=r["transaction_hash"], op_type=t)
                if t == "payment":
                    fr, to, amt = r["from"], r["to"], float(r["amount"])
                elif t.startswith("path_payment"):
                    if is_asset(r, code, issuer) and is_asset(r, code, issuer, "source_"):
                        fr, to, amt = r["from"], r["to"], float(r["amount"])
                    else:
                        diag[f"path_payment_cross_asset"] += 1
                        continue
                elif t == "clawback":
                    fr, to, amt = r["from"], issuer, float(r["amount"])
                elif t == "create_claimable_balance":
                    if r.get("source_account") == issuer:
                        fr, to, amt = issuer, f"CLAIMABLE:{oid}", float(r["amount"])
                    else:
                        diag["create_claimable_balance_by_holder"] += 1
                        continue
                else:
                    # not converted; asset-unknown types (claim, Soroban) counted separately
                    diag[f"not_converted:{t}{'' if touches else ' (asset not identifiable in op)'}"] += 1
                    continue
                if fr == issuer and to == issuer:
                    diag["issuer_self_payment"] += 1
                    continue
                kind = "mint" if fr == issuer else "burn" if to == issuer else "transfer"
                events.append(dict(base, frm=fr, to=to, amount=amt, kind=kind))
                diag[f"converted:{kind}"] += 1
    events.sort(key=lambda e: (e["ts"], e["id"]))
    return events, diag


def asset_snapshot(crawl_dir: Path, code: str):
    """Current supply (sum of Horizon /assets buckets) and the time the file was fetched."""
    rec = json.loads((crawl_dir / f"asset_{code}.json").read_bytes())["_embedded"]["records"][0]
    sup = sum(float(v) for v in rec["balances"].values()) + float(rec["claimable_balances_amount"]) + \
        float(rec["liquidity_pools_amount"]) + float(rec["contracts_amount"])
    t_q = None
    for line in open(crawl_dir / "manifest.jsonl"):
        m = json.loads(line)
        if m["path"].endswith(f"asset_{code}.json"):
            t_q = ts(m["accessed_utc"])
    return sup, t_q, rec


def trustline_balances(crawl_dir: Path, code: str, issuer: str):
    p = crawl_dir / f"trustlines_{code}.jsonl.gz"
    out = {}
    for pg in read_pages(p):
        for r in pg["_embedded"]["records"]:
            for b in r["balances"]:
                if b.get("asset_code") == code and b.get("asset_issuer") == issuer:
                    out[r["account_id"]] = float(b["balance"])
    t_q = None
    for line in open(crawl_dir / "manifest.jsonl"):
        m = json.loads(line)
        if m["path"].endswith(f"trustlines_{code}.jsonl.gz"):
            t_q = ts(m["accessed_utc"])
    return out, t_q


def supply_backout(events, sup_now, t_now, t_cut):
    """supply(T) = supply(now) - mints in (T, now] + burns in (T, now]."""
    m = sum(e["amount"] for e in events if e["kind"] == "mint" and t_cut < e["ts"] <= t_now)
    b = sum(e["amount"] for e in events if e["kind"] == "burn" and t_cut < e["ts"] <= t_now)
    return sup_now - m + b


def balances_backout(events, bal_now, t_now, t_cut, issuer):
    """Per-account balance at T = balance(now) - net inflow in (T, now]."""
    bal = dict(bal_now)
    for e in events:
        if not (t_cut < e["ts"] <= t_now):
            continue
        if e["to"] != issuer and not e["to"].startswith("CLAIMABLE:"):
            bal[e["to"]] = bal.get(e["to"], 0.0) - e["amount"]
        if e["frm"] != issuer:
            bal[e["frm"]] = bal.get(e["frm"], 0.0) + e["amount"]
    return bal


def crawl_start(crawl_dir: Path):
    """START of the crawl (00:00 ET on the first monthly issuer slice), as an aware UTC datetime.
    Events before it come only from holder pages that happened to reach further back, so they are
    incomplete and must not be used."""
    import json as _j
    from zoneinfo import ZoneInfo
    sl = _j.loads((crawl_dir / "issuer_slices.json").read_text())
    y, m = map(int, min(sl).split("-"))
    return dt.datetime(y, m, 1, tzinfo=ZoneInfo("America/New_York")).astimezone(dt.timezone.utc)
