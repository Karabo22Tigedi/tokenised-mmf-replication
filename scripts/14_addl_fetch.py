"""14_addl_fetch.py -- ADDITION 2026-10-17 (exploratory, post-freeze, user-approved CP35): raw fetch

Fetches transaction-level data for the transfers rebuilt by 13_addl_f1_transfers.py and saves every
response unmodified under data/raw/addition_2026-10-17/. It does not touch any freeze_* file.

  evm_calls.jsonl         Etherscan V2: proxy eth_getTransactionByHash for every EVM transfer tx in the
                          2026 window (all funds; non-TA and TA-involving); contract getsourcecode
                          (verified source, ABI, proxy implementation) for every issuer-listed EVM token contract, each
                          implementation and each tx.to that is not the token; Safe getOwners()
                          (eth_call at the tx block) for Safes that are a party to or execute a non-TA
                          transfer; one line per call
                          {source, method, params, url (key masked), accessed_utc, response}.
  fourbyte_calls.jsonl    https://www.4byte.directory/api/v1/signatures/?hex_signature=<sel> for
                          selectors not found in a verified ABI.
  stellar_calls.jsonl     Horizon https://horizon.stellar.org: /transactions/<hash> and
                          /transactions/<hash>/operations for every Stellar transfer in the window;
                          /accounts/<issuer> for the BENJI and WTGX issuers; stellar.expert directory
                          https://api.stellar.expert/explorer/directory/<acct> for labelled accounts.
  labels/<chain>_<addr>.html  Etherscan / Arbiscan address pages (public name tag is in <title>) for the
                          largest non-TA counterparties, the primary-market wallets and non-holder signers.
  solana_calls.jsonl      public RPC getAccountInfo(jsonParsed) for the WTGXX and BENJI Solana mints
                          (Token-2022 extensions).
Solana transactions need no fetch: the frozen raw getTransaction(jsonParsed) responses already carry signers and
instructions (data/raw/onchain/freeze_2026-10-16/solana_*_transactions.jsonl.gz).

Idempotent: a call already present in the jsonl (same source/method/params) is not repeated.
After fetching, writes data/raw/ADDITION_MANIFEST_2026-10-17.csv (kind, path, bytes, sha256).
The Etherscan key comes from .env and is never printed or written.

Run: python -I scripts/14_addl_fetch.py
"""
import csv
import datetime as dt
import hashlib
import importlib.util
import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("pilot03", ROOT / "scripts" / "03_onchain_pilot.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)
PROC = ROOT / "data" / "processed"
RAW = ROOT / "data" / "raw" / "addition_2026-10-17"
MANIFEST = ROOT / "data" / "raw" / "ADDITION_MANIFEST_2026-10-17.csv"
HORIZON = "https://horizon.stellar.org"
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
EXPLORER = {"ethereum": "https://etherscan.io/address/", "arbitrum": "https://arbiscan.io/address/"}
TOP_N_LABELS = 10      # largest non-TA transfers per fund whose counterparties get a label lookup


def now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Log:
    """Append-only jsonl of raw responses with a (source, method, params) cache."""
    def __init__(self, name):
        self.path = RAW / name
        self.cache = {}
        if self.path.exists():
            for line in open(self.path, encoding="utf-8"):
                d = json.loads(line)
                self.cache[self.key(d["source"], d["method"], d["params"])] = d["response"]

    @staticmethod
    def key(source, method, params):
        return json.dumps([source, method, params], sort_keys=True)

    def get(self, source, method, params, fn, url_masked):
        k = self.key(source, method, params)
        if k in self.cache:
            return self.cache[k]
        resp = fn()
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(dict(source=source, method=method, params=params, url=url_masked,
                                    accessed_utc=now(), response=resp)) + "\n")
        self.cache[k] = resp
        return resp


def http_json(url, ua=P.UA, tries=4):
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return {"_http_status": 404}
            time.sleep(3 * (k + 1))
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(3 * (k + 1))
    return {"_error": f"failed after {tries} tries"}


def es_raw(chain, **params):
    """Etherscan V2 raw response (dict). Key masked in the logged url."""
    q = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{P.ETHERSCAN_V2}?chainid={P.CHAIN_IDS[chain]}&{q}&apikey={P.ETHERSCAN_KEY}"
    time.sleep(0.25)
    return http_json(url), P._mask(url)


def main():
    if not P.ETHERSCAN_KEY:
        sys.exit("no ETHERSCAN_API_KEY in .env")
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / "labels").mkdir(exist_ok=True)
    ev = list(csv.DictReader(open(PROC / "addl_f1_events.csv", newline="")))
    roles = {(r["fund"], r["chain"], r["address"]): r["role"]
             for r in csv.DictReader(open(PROC / "freeze_wallet_roles.csv", newline=""))}
    toks = {(t["fund"], t["chain"]): t["address"].lower() for t in P.issuer_addresses()}
    evm_log, fb_log, st_log = Log("evm_calls.jsonl"), Log("fourbyte_calls.jsonl"), Log("stellar_calls.jsonl")

    # ---- A. EVM transactions (every transfer tx in the window on EVM chains)
    evm_tx = sorted({(r["chain"], r["tx"]) for r in ev if r["chain"] in P.CHAIN_IDS})
    print(f"EVM transfer txs to fetch/cached: {len(evm_tx)}")
    txs = {}
    for ch, h in evm_tx:
        def f(ch=ch, h=h):
            d, _ = es_raw(ch, module="proxy", action="eth_getTransactionByHash", txhash=h)
            return d
        resp = evm_log.get("etherscan", "eth_getTransactionByHash", [ch, h], f,
                           f"{P.ETHERSCAN_V2}?chainid={P.CHAIN_IDS[ch]}&module=proxy&action=eth_getTransactionByHash&txhash={h}&apikey=***")
        txs[(ch, h)] = resp.get("result") or {}
    missing = [k for k, v in txs.items() if not v]
    print(f"  EVM txs with empty result: {len(missing)}")

    # ---- B. verified source / ABI: token contracts, implementations, tx.to contracts
    addrs = {(ch, a) for (f_, ch), a in toks.items() if ch in P.CHAIN_IDS}   # every issuer-listed EVM token
    addrs |= {(ch, t["to"].lower()) for (ch, _), t in txs.items() if t.get("to")}
    done = set()
    while addrs - done:
        ch, a = sorted(addrs - done)[0]
        done.add((ch, a))
        def f(ch=ch, a=a):
            d, _ = es_raw(ch, module="contract", action="getsourcecode", address=a)
            return d
        resp = evm_log.get("etherscan", "getsourcecode", [ch, a], f,
                           f"{P.ETHERSCAN_V2}?chainid={P.CHAIN_IDS[ch]}&module=contract&action=getsourcecode&address={a}&apikey=***")
        res = (resp.get("result") or [{}])
        res = res[0] if isinstance(res, list) and res else {}
        impl = (res.get("Implementation") or "").lower()
        if impl.startswith("0x") and len(impl) == 42:
            addrs.add((ch, impl))
        print(f"  getsourcecode {ch} {a[:10]}: name={res.get('ContractName')!r} proxy={res.get('Proxy')} impl={impl[:10]}")

    # ---- B2. Safe owners at the block of each tx that a Safe executed or that moved a Safe's tokens
    #      (eth_call getOwners() = 0xa0e67e2b, at the tx block; tests whether tx.from is an owner)
    safe_names = {"SafeProxy", "GnosisSafeProxy"}
    cname = {}
    for k, resp in evm_log.cache.items():
        src, meth, prm = json.loads(k)
        if meth == "getsourcecode":
            res = resp.get("result") or [{}]
            cname[tuple(prm)] = res[0].get("ContractName", "") if isinstance(res, list) and res else ""
    for r in ev:
        if r["chain"] not in P.CHAIN_IDS or r["nonta"] != "1":
            continue
        t = txs[(r["chain"], r["tx"])]
        for a in {r["frm"], r["to"], (t.get("to") or "").lower()}:
            if cname.get((r["chain"], a)) in safe_names:
                blk = t["blockNumber"]
                def f(ch=r["chain"], a=a, blk=blk):
                    d, _ = es_raw(ch, module="proxy", action="eth_call", to=a, data="0xa0e67e2b", tag=blk)
                    return d
                evm_log.get("etherscan", "eth_call_getOwners", [r["chain"], a, blk], f,
                            f"{P.ETHERSCAN_V2}?chainid={P.CHAIN_IDS[r['chain']]}&module=proxy&action=eth_call&to={a}&data=0xa0e67e2b&tag={blk}&apikey=***")

    # ---- C. 4byte for selectors not in any verified ABI
    known = set()
    for k, resp in evm_log.cache.items():
        if '"getsourcecode"' not in k:
            continue
        res = resp.get("result") or [{}]
        abi = res[0].get("ABI") if isinstance(res, list) and res else None
        if abi and abi.startswith("["):
            for it in json.loads(abi):
                if it.get("type") == "function":
                    sig = f"{it['name']}({','.join(P_type(i) for i in it.get('inputs', []))})"
                    known.add(keccak_selector(sig))
    sels = sorted({t["input"][:10] for t in txs.values() if t.get("input") and len(t["input"]) >= 10})
    for s in sels:
        if s in known:
            continue
        url = f"https://www.4byte.directory/api/v1/signatures/?hex_signature={s}"
        fb_log.get("4byte", "signatures", [s], lambda url=url: http_json(url), url)
        print(f"  4byte {s}")

    # ---- D. Stellar transactions and issuer accounts
    st_tx = sorted({r["tx"] for r in ev if r["chain"] == "stellar"})
    print(f"Stellar transfer txs: {len(st_tx)}")
    for h in st_tx:
        for path in (f"/transactions/{h}", f"/transactions/{h}/operations?limit=200"):
            url = HORIZON + path
            st_log.get("horizon", "get", [path], lambda url=url: http_json(url), url)
    for t in P.issuer_addresses():
        if t["chain"] == "stellar":
            path = f"/accounts/{t['address']}"
            st_log.get("horizon", "get", [path], lambda url=HORIZON + path: http_json(url), HORIZON + path)
            print(f"  issuer account {t['token']} {t['address'][:8]}")

    # ---- D2. Solana mint accounts (Token-2022 extensions: permanent delegate, pausable, default state)
    sol_log = Log("solana_calls.jsonl")
    for t in P.issuer_addresses():
        if t["chain"] == "solana":
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getAccountInfo",
                               "params": [t["address"], {"encoding": "jsonParsed"}]}).encode()
            def f(body=body):
                for k in range(5):
                    try:
                        req = urllib.request.Request("https://api.mainnet-beta.solana.com", data=body,
                                                     headers={"Content-Type": "application/json", "User-Agent": P.UA})
                        with urllib.request.urlopen(req, timeout=60) as r:
                            return json.loads(r.read())
                    except Exception:
                        time.sleep(5 * (k + 1))
                return {"_error": "failed"}
            sol_log.get("solana_rpc", "getAccountInfo", [t["address"]], f, "https://api.mainnet-beta.solana.com")
            print(f"  solana mint account {t['token']} {t['address'][:8]}")

    # ---- E. labels: counterparties of the largest non-TA transfers, primary-market wallets, signers
    big = defaultdict(list)
    for r in ev:
        if r["nonta"] == "1":
            big[r["fund"]].append(r)
    want = set()
    for fund, rows in big.items():
        rows.sort(key=lambda r: -float(r["amount"]))
        for r in rows[:TOP_N_LABELS]:
            want |= {(r["chain"], r["frm"]), (r["chain"], r["to"])}
    for (ch, h), t in txs.items():          # signers of non-TA transfers who are neither party
        for r in ev:
            if r["tx"] == h and r["nonta"] == "1" and t.get("from") and t["from"].lower() not in (r["frm"], r["to"]):
                want.add((ch, t["from"].lower()))
    want.add(("ethereum", "0x0c561ed86ef65dd681500d6266c99e03dec3c541"))   # critic's wallet
    # TA-role wallets that the critic's wallet's recipients deal with, and the TA burn-tx sender
    want |= {("ethereum", a) for a in ("0x894bb63432e8c5865a50d6bc495fa70a765cbc84", "0x63a8bb98d70d0ac73460d22b6756528a087ecbf8",
                                       "0x860cc723935fc9a15ff8b1a94237a711dfef7857", "0x726d3b3e18e301719b383fde4f99a5d31b9438e5")}
    for ch, a in sorted(want):
        if ch in EXPLORER:
            out = RAW / "labels" / f"{ch}_{a}.html"
            if out.exists():
                continue
            req = urllib.request.Request(EXPLORER[ch] + a, headers={"User-Agent": BROWSER_UA})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    out.write_bytes(r.read())
            except Exception as e:     # recorded, not hidden
                (RAW / "labels" / f"{ch}_{a}.error.txt").write_text(f"{now()} {EXPLORER[ch]}{a} {e}\n")
            time.sleep(2)
            print(f"  label page {ch} {a[:10]}")
        elif ch == "stellar":
            url = f"https://api.stellar.expert/explorer/directory/{a}"
            st_log.get("stellar.expert", "directory", [a], lambda url=url: http_json(url), url)
            time.sleep(0.5)
            print(f"  stellar.expert directory {a[:8]}")

    # ---- manifest
    rows = []
    for p in sorted(RAW.rglob("*")):
        if p.is_file():
            rows.append(dict(kind="raw_addition", path=p.relative_to(ROOT).as_posix(), bytes=p.stat().st_size,
                             sha256=hashlib.sha256(p.read_bytes()).hexdigest()))
    with open(MANIFEST, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["kind", "path", "bytes", "sha256"])
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {MANIFEST.relative_to(ROOT).as_posix()}: {len(rows)} files")


# ---------------------------------------------------------------- ABI helpers
def P_type(inp):
    """Canonical ABI type (tuples expanded)."""
    t = inp["type"]
    if t.startswith("tuple"):
        return "(" + ",".join(P_type(c) for c in inp.get("components", [])) + ")" + t[5:]
    return t


def keccak_selector(sig):
    return "0x" + keccak256(sig.encode()).hex()[:8]


def keccak256(data: bytes) -> bytes:
    """Pure-Python Keccak-256 (Ethereum variant, pad 0x01). Used only for 4-byte selectors."""
    RC = [0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000, 0x000000000000808B,
          0x0000000080000001, 0x8000000080008081, 0x8000000000008009, 0x000000000000008A, 0x0000000000000088,
          0x0000000080008009, 0x000000008000000A, 0x000000008000808B, 0x800000000000008B, 0x8000000000008089,
          0x8000000000008003, 0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
          0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008]
    ROT = [[0, 36, 3, 41, 18], [1, 44, 10, 45, 2], [62, 6, 43, 15, 61], [28, 55, 25, 21, 56], [27, 20, 39, 8, 14]]
    M = (1 << 64) - 1
    rate = 136
    msg = bytearray(data) + b"\x01"
    while len(msg) % rate:
        msg += b"\x00"
    msg[-1] |= 0x80
    S = [[0] * 5 for _ in range(5)]
    for off in range(0, len(msg), rate):
        for i in range(rate // 8):
            x, y = i % 5, i // 5
            S[x][y] ^= int.from_bytes(msg[off + 8 * i: off + 8 * i + 8], "little")
        for rnd in range(24):
            C = [S[x][0] ^ S[x][1] ^ S[x][2] ^ S[x][3] ^ S[x][4] for x in range(5)]
            D = [C[(x - 1) % 5] ^ (((C[(x + 1) % 5] << 1) | (C[(x + 1) % 5] >> 63)) & M) for x in range(5)]
            S = [[S[x][y] ^ D[x] for y in range(5)] for x in range(5)]
            B = [[0] * 5 for _ in range(5)]
            for x in range(5):
                for y in range(5):
                    r = ROT[x][y]
                    B[y][(2 * x + 3 * y) % 5] = ((S[x][y] << r) | (S[x][y] >> (64 - r))) & M if r else S[x][y]
            S = [[B[x][y] ^ ((~B[(x + 1) % 5][y]) & B[(x + 2) % 5][y]) for y in range(5)] for x in range(5)]
            S[0][0] ^= RC[rnd]
    out = b""
    for i in range(4):
        out += S[i % 5][i // 5].to_bytes(8, "little")
    return out


assert keccak_selector("transfer(address,uint256)") == "0xa9059cbb"
assert keccak_selector("transferFrom(address,address,uint256)") == "0x23b872dd"

if __name__ == "__main__":
    main()
