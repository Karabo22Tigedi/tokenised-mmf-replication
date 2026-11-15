"""03_onchain_pilot.py  (PILOT, not the frozen G2 vintage)

On-chain leg for the four tokenised government MMFs. No API keys used.

Token addresses are NOT typed by hand where an issuer source exists: they are parsed from the
raw issuer files saved in data/raw/issuer/ (see notes/token_contracts.md):
  * Franklin BENJI: 2026-10-16_franklin_benji_contracts.html (digitalassets.franklintempleton.com)
                    and 2026-10-16_franklin_stellar.toml (franklintempleton.com/.well-known)
  * WisdomTree WTGXX: 2026-10-16_wisdomtree_wtgxx_fund_details.pdf (dataspanapi.wisdomtree.com)
  * JPMorgan JLTXX: 2026-10-16_jpm_jltxx_press_release.html (am.jpmorgan.com)
  * Fidelity FDIT (OnChain class): NO issuer source found. The address below is taken from
    Etherscan's token page label "Fidelity Digital Interest Token" and is UNVERIFIED; the
    contract's own name() is checked at run time.

What it does
  A. Ethereum, full event history (eth_getLogs, ERC-20 Transfer topic, 10,000-block chunks,
     free public RPCs eth.drpc.org / rpc.mevblocker.io) for JLTXX, WTGXX, FDIT and BENJI:
       mint = Transfer from 0x0; burn = Transfer to 0x0; other = any other Transfer.
     Supply from events (mints - burns) is compared with totalSupply() at the head block.
     Holder count = addresses with positive event-derived balance (0x0 excluded).
     Month-end supply/holders are recomputed from events at the last block <= month-end.
  B. Other EVM chains (WTGXX: Arbitrum, Avalanche, Base, Optimism, Plume; BENJI: Polygon,
     Arbitrum, Avalanche, Base): totalSupply() at head and at month-end blocks (needs an
     archive-capable RPC; failures are recorded, not filled).
  C. Solana (WTGXX, BENJI): getTokenSupply at head only (public RPC has no historical state).
  D. Stellar (BENJI, WTGX) via Horizon: current supply from /assets; issuer-account operations
     are paged backwards to BACK_TO, and month-end supply is backed out as
       supply(T) = supply(now) - sum(mints after T) + sum(burns after T)
     mint = payment/path payment from issuer, or claimable balance created by issuer;
     burn = payment to issuer, or clawback (claimable-balance clawbacks counted as burn).
     Supply = authorized + authorized_to_maintain_liabilities + unauthorized balances
              + claimable balances + liquidity pools + contracts (Horizon /assets fields).
  E. Aptos (BENJI): not attempted in this pilot (recorded as such).

Month-end cut-off: 23:59:59 UTC on the N-MFP3 report date (= 19:59:59 New York time,
i.e. after the 4pm fund close). Also reported at +1 day as a timing sensitivity.

Outputs
  data/raw/onchain/<TODAY>_*.jsonl.gz   raw RPC/Horizon responses (unmodified, gzipped)
  data/processed/pilot_onchain_summary.csv     one row per token x chain x snapshot
Etherscan V2: if <project>/.env contains ETHERSCAN_API_KEY (git-ignored; never printed, masked
in logged URLs), Ethereum logs are pulled with module=logs&action=getLogs (paged) and the
Etherscan proxy (eth_call/eth_getBlockByNumber, chainid per chain) is used as a last-resort
fallback when public RPCs fail. Without a key the script runs keyless on public RPCs.
Run: python -I scripts/03_onchain_pilot.py
"""
import csv
import datetime as dt
import gzip
import json
import re
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
import sys
sys.path.insert(0, str(ROOT / "scripts"))
RAWI = ROOT / "data" / "raw" / "issuer"
RAWO = ROOT / "data" / "raw" / "onchain"
PROC = ROOT / "data" / "processed"
TODAY = dt.date.today().isoformat()
UA = "Karabo Tigedi research research-contact-omitted"

MONTH_ENDS = ["2026-07-31", "2026-08-31", "2026-09-30"]  # N-MFP3 report dates pulled in 01
BACK_TO = "2026-07-31T23:59:59Z"                           # Stellar back-out horizon
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ZERO = "0x" + "0" * 40
FDIT_UNVERIFIED = "0x48aB4e39AC59F4E88974804B04A991b3a402717f"  # Etherscan label only, see header

RPCS = {
    "ethereum": ["https://eth.drpc.org", "https://rpc.mevblocker.io", "https://ethereum-rpc.publicnode.com"],
    # eth_getLogs: drpc free plan rejects historical ranges ("ranges over 10000 blocks are not
    # supported on free plan" even for 9,000), so mevblocker (10k-block ranges) goes first
    "ethereum_logs": ["https://rpc.mevblocker.io", "https://eth.drpc.org"],
    "arbitrum": ["https://arb1.arbitrum.io/rpc", "https://arbitrum.api.onfinality.io/public", "https://arbitrum.drpc.org"],
    "avalanche": ["https://api.avax.network/ext/bc/C/rpc", "https://avalanche.drpc.org"],
    "base": ["https://mainnet.base.org", "https://base.api.onfinality.io/public", "https://base.drpc.org"],
    "optimism": ["https://mainnet.optimism.io", "https://optimism.api.onfinality.io/public", "https://optimism.drpc.org"],
    "polygon": ["https://polygon.api.onfinality.io/public", "https://rpc-mainnet.matic.quiknode.pro", "https://1rpc.io/matic"],
    "plume": ["https://rpc.plume.org"],
}
SOLANA = "https://api.mainnet-beta.solana.com"
HORIZON = "https://horizon.stellar.org"


# ---------------------------------------------------------------- address discovery (from raw files)
def issuer_addresses():
    # Publication adapter: held factual extract, not a new address discovery.
    import source_facts
    return source_facts.issuer_addresses()


def _original_issuer_addresses():
    toks = []  # dicts: fund, token, chain, address, status, source_file
    # Franklin contracts page: "BENJI (<Chain>)" heading followed by "Fund Token" then address
    html = (RAWI / "2026-10-16_franklin_benji_contracts.html").read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"<[^>]+>", "\n", re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.S))
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    for i, l in enumerate(lines):
        m = re.fullmatch(r"BENJI \(([^)]+)\)", l)  # only plain BENJI (= FOBXX); iBENJI/gBENJI etc excluded
        if m and lines[i + 1] == "Fund Token":
            toks.append(dict(fund="FOBXX", token="BENJI", chain=m.group(1).lower(), address=lines[i + 2],
                             status="verified (issuer contracts page)",
                             source_file="2026-10-16_franklin_benji_contracts.html"))
    toml = (RAWI / "2026-10-16_franklin_stellar.toml").read_text(encoding="utf-8")
    m = re.search(r'code="BENJI"\s*\nissuer="(G[A-Z2-7]{55})"', toml)
    for t in toks:  # cross-check Stellar issuer between the two Franklin sources
        if t["chain"] == "stellar":
            assert m and m.group(1) == t["address"], "Franklin stellar.toml and contracts page disagree"
            t["status"] += " + stellar.toml"
    # WisdomTree fund details PDF: "Fund Token Contract Addresses" block, "<Chain> <address>"
    from pypdf import PdfReader
    pdf = "\n".join(p.extract_text() for p in PdfReader(RAWI / "2026-10-16_wisdomtree_wtgxx_fund_details.pdf").pages)
    block = pdf.split("Fund Token Contract Addresses", 1)[1].split("Smart contract audit", 1)[0]
    for chain, addr in re.findall(r"^(Arbitrum|Avalanche|Base|Ethereum|Optimism|Plume|Solana|Stellar) (\S+)$", block, re.M):
        toks.append(dict(fund="WTGXX", token="WTGXX" if chain != "Stellar" else "WTGX", chain=chain.lower(),
                         address=addr, status="verified (issuer fund-details PDF, data as of 09/30/2026)",
                         source_file="2026-10-16_wisdomtree_wtgxx_fund_details.pdf"))
    # JPMorgan press release
    pr = (RAWI / "2026-10-16_jpm_jltxx_press_release.html").read_text(encoding="utf-8", errors="replace")
    a = re.search(r"JLTXX blockchain token address is (0x[0-9a-fA-F]{40})", pr).group(1)
    toks.append(dict(fund="JLTXX", token="JLTXX", chain="ethereum", address=a,
                     status="verified (issuer press release 13 May 2026)",
                     source_file="2026-10-16_jpm_jltxx_press_release.html"))
    toks.append(dict(fund="FYOXX", token="FDIT", chain="ethereum", address=FDIT_UNVERIFIED,
                     status="UNVERIFIED (Etherscan label only; no issuer source found)", source_file=""))
    return toks


# ---------------------------------------------------------------- Etherscan V2 (optional key in .env)
def _load_env_key(name="ETHERSCAN_API_KEY"):
    """Read a key from <project>/.env (git-ignored). The value is never printed or written."""
    envp = ROOT / ".env"
    if not envp.exists():
        return None
    for line in envp.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith(name + "="):
            return line.split("=", 1)[1].strip().strip('"').strip("'") or None
    return None


ETHERSCAN_KEY = _load_env_key()
ETHERSCAN_V2 = "https://api.etherscan.io/v2/api"
CHAIN_IDS = {"ethereum": 1, "arbitrum": 42161, "avalanche": 43114, "base": 8453,
             "optimism": 10, "polygon": 137}  # plume not on Etherscan V2


def _mask(url):
    return re.sub(r"apikey=[^&]+", "apikey=***", url)


def etherscan(chain, **params):
    """GET Etherscan V2; returns 'result' or raises RuntimeError (message has key masked)."""
    if not ETHERSCAN_KEY or chain not in CHAIN_IDS:
        raise RuntimeError("etherscan: no key or chain unsupported")
    q = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{ETHERSCAN_V2}?chainid={CHAIN_IDS[chain]}&{q}&apikey={ETHERSCAN_KEY}"
    time.sleep(0.25)  # free tier: stay under ~5 req/s
    try:
        d = get(url, timeout=60)
    except Exception as e:
        raise RuntimeError(f"etherscan {_mask(url)}: {e}"[:200])
    if isinstance(d, dict) and "jsonrpc" in d:          # proxy module
        if "error" in d:
            raise RuntimeError(f"etherscan proxy: {d['error']}"[:200])
        return d["result"]
    if str(d.get("status")) == "1" or (d.get("message") == "No records found"):
        return d.get("result", [])
    raise RuntimeError(f"etherscan {params.get('module')}/{params.get('action')}: "
                       f"{d.get('message')} {str(d.get('result'))[:120]}")


def etherscan_logs(chain, address, start, end, step0=200_000):
    """All Transfer logs via module=logs&action=getLogs, paging (1000/page) within block windows.
    Returns logs in the same shape as eth_getLogs results."""
    out, b, step = [], start, step0
    while b <= end:
        e = min(b + step - 1, end)
        page, got = 1, []
        while True:
            res = etherscan(chain, module="logs", action="getLogs", address=address, topic0=TRANSFER,
                            fromBlock=b, toBlock=e, page=page, offset=1000)
            got += res
            if len(res) < 1000:
                break
            page += 1
            if page > 10:  # Etherscan caps page*offset at 10,000: split the window instead
                got = None
                break
        if got is None:
            step //= 4
            continue
        out += [dict(l, blockNumber=l["blockNumber"] if str(l["blockNumber"]).startswith("0x")
                     else hex(int(l["blockNumber"]))) for l in got]
        b = e + 1
        step = step0
    return out


# ---------------------------------------------------------------- HTTP helpers
def post(url, payload, timeout=60):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


_DEAD = set()  # endpoints that failed to connect; skipped for the rest of the run


def rpc(chain, method, params, tries=2):
    """Try each RPC for the chain; return (result, endpoint) or raise with all errors."""
    errs = []
    for url in RPCS[chain]:
        if url in _DEAD:
            continue
        for k in range(tries):
            try:
                out = post(url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=25)
                if "error" in out:
                    errs.append(f"{url}: {out['error'].get('message', out['error'])}"[:160])
                    break  # deterministic error: next endpoint
                return out["result"], url
            except urllib.error.HTTPError as e:
                errs.append(f"{url}: HTTP {e.code}"[:160])
                time.sleep(1 + k)
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
                errs.append(f"{url}: {e}"[:160])
                if isinstance(e, urllib.error.URLError) or k == tries - 1:
                    _DEAD.add(url)
                    print(f"   endpoint marked dead: {url} ({str(e)[:80]})", flush=True)
                    break
                time.sleep(1 + k)
    base = chain.replace("_logs", "")
    # NB: Etherscan proxy eth_call returned head state for historical tags (tested 2026-10-16),
    # so it is used for 'latest' only, never for month-end snapshots.
    if ETHERSCAN_KEY and base in CHAIN_IDS and (method in ("eth_blockNumber", "eth_getBlockByNumber")
                                               or (method == "eth_call" and params[1] == "latest")):
        try:
            if method == "eth_call":
                r = etherscan(base, module="proxy", action="eth_call", to=params[0]["to"],
                              data=params[0]["data"], tag=params[1])
            elif method == "eth_blockNumber":
                r = etherscan(base, module="proxy", action="eth_blockNumber")
            else:
                r = etherscan(base, module="proxy", action="eth_getBlockByNumber", tag=params[0], boolean="false")
            return r, "etherscan-v2-proxy"
        except RuntimeError as e:
            errs.append(str(e))
    raise RuntimeError(" | ".join(errs[-4:]))


def call_uint(chain, addr, selector, block="latest"):
    res, url = rpc(chain, "eth_call", [{"to": addr, "data": selector}, block])
    return int(res, 16), url


def call_str(chain, addr, selector):
    res, _ = rpc(chain, "eth_call", [{"to": addr, "data": selector}, "latest"])
    b = bytes.fromhex(res[2:])
    n = int.from_bytes(b[32:64], "big")
    return b[64:64 + n].decode(errors="replace")


def ts(s):  # 'YYYY-MM-DD' -> unix ts of 23:59:59 UTC
    return int(dt.datetime.fromisoformat(s + "T23:59:59+00:00").timestamp())


def block_ts(chain, n):
    b, _ = rpc(chain, "eth_getBlockByNumber", [hex(n), False])
    return int(b["timestamp"], 16)


_BLOCK_CACHE = {}


def block_at(chain, t, head):
    """Last block with timestamp <= t (binary search, cached per chain/time)."""
    if (chain, t) in _BLOCK_CACHE:
        return _BLOCK_CACHE[(chain, t)]
    _BLOCK_CACHE[(chain, t)] = _block_at(chain, t, head)
    return _BLOCK_CACHE[(chain, t)]


def _block_at(chain, t, head):
    lo, hi = 1, head
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if block_ts(chain, mid) <= t:
            lo = mid
        else:
            hi = mid - 1
    return lo


def deploy_block(chain, addr, head):
    """First block where contract code exists (needs archive eth_getCode)."""
    lo, hi = 1, head
    while lo < hi:
        mid = (lo + hi) // 2
        code, _ = rpc(chain, "eth_getCode", [addr, hex(mid)])
        if code not in ("0x", "0x0", ""):
            hi = mid
        else:
            lo = mid + 1
    return lo


# ---------------------------------------------------------------- A. Ethereum events
def l2_logs(tok, head):
    """Non-Ethereum EVM chains: Transfer logs via Etherscan V2 only (free tier: Arbitrum, Polygon;
    Base/Optimism/Avalanche are paid-tier). Start = contract creation block (getcontractcreation)."""
    ch = tok["chain"]
    path = RAWO / f"{TODAY}_{ch}_{tok['token']}_{tok['address'][:10]}_transfer_logs.jsonl.gz"
    if path.exists():
        logs = [json.loads(l) for l in gzip.open(path, "rt")]
        print(f"  reused {path.name}: {len(logs)} logs")
        return logs, path.name
    cc = etherscan(ch, module="contract", action="getcontractcreation", contractaddresses=tok["address"])
    start = int(cc[0]["blockNumber"])
    print(f"  creation block {start} (Etherscan); pulling logs to {head} via Etherscan V2")
    logs = etherscan_logs(ch, tok["address"], start, head, step0=2_000_000)
    with gzip.open(path, "wt") as f:
        for l in logs:
            f.write(json.dumps(l) + "\n")
    print(f"  saved {path.name}: {len(logs)} logs (Etherscan V2 getLogs)")
    return logs, path.name


def eth_logs(tok, head):
    path = RAWO / f"{TODAY}_ethereum_{tok['token']}_{tok['address'][:10]}_transfer_logs.jsonl.gz"
    if path.exists():
        logs = [json.loads(l) for l in gzip.open(path, "rt")]
        print(f"  reused {path.name}: {len(logs)} logs")
        return logs, path.name
    start = deploy_block("ethereum", tok["address"], head)
    print(f"  deploy block {start}; pulling logs to {head}")
    if ETHERSCAN_KEY:
        try:
            logs = etherscan_logs("ethereum", tok["address"], start, head)
            with gzip.open(path, "wt") as f:
                for l in logs:
                    f.write(json.dumps(l) + "\n")
            print(f"  saved {path.name}: {len(logs)} logs (Etherscan V2 getLogs)")
            return logs, path.name
        except RuntimeError as e:
            print(f"  Etherscan logs failed, falling back to public RPC: {e}")
    logs, step, b = [], 10_000, start
    while b <= head:
        e = min(b + step - 1, head)
        try:
            t0 = time.time()
            res, _ = rpc("ethereum_logs", "eth_getLogs",
                         [{"address": tok["address"], "topics": [TRANSFER], "fromBlock": hex(b), "toBlock": hex(e)}])
        except RuntimeError as err:
            if step > 500:
                step //= 4
                print(f"   shrinking chunk to {step}: {str(err)[:100]}")
                continue
            raise
        logs += res
        b = e + 1
        step = 10_000
        nchunk = locals().get("nchunk", 0) + 1
        if nchunk % 50 == 0:
            print(f"   chunk {nchunk}: block {e:,} / {head:,}, logs so far {len(logs)}, last call {time.time()-t0:.1f}s",
                  flush=True)
    with gzip.open(path, "wt") as f:
        for l in logs:
            f.write(json.dumps(l) + "\n")
    print(f"  saved {path.name}: {len(logs)} logs")
    return logs, path.name


def summarise_events(logs, dec, upto_block=None):
    bal = defaultdict(int)
    n = dict(mint=0, burn=0, other=0)
    s = dict(mint=0, burn=0, other=0)
    for l in logs:
        bn = int(l["blockNumber"], 16)
        if upto_block is not None and bn > upto_block:
            continue
        if len(l["topics"]) < 3:   # non-standard Transfer (e.g. ERC-721 style) -- counted separately
            continue
        fr, to = "0x" + l["topics"][1][-40:], "0x" + l["topics"][2][-40:]
        v = int(l["data"], 16) if l["data"] not in ("0x", "") else 0
        kind = "mint" if fr == ZERO else "burn" if to == ZERO else "other"
        n[kind] += 1
        s[kind] += v
        if fr != ZERO:
            bal[fr] -= v
        if to != ZERO:
            bal[to] += v
    neg = sum(1 for v in bal.values() if v < 0)
    # "burn wallets": addresses that EVER sent tokens to 0x0 over the full pulled history
    # (rule-based proxy for issuer/transfer-agent operational wallets; redemptions are parked
    # there before being burned). Their balance at the snapshot is reported separately.
    burners = {"0x" + l["topics"][1][-40:] for l in logs
               if len(l["topics"]) >= 3 and "0x" + l["topics"][2][-40:] == ZERO}
    return dict(n_mint=n["mint"], n_burn=n["burn"], n_other=n["other"],
                vol_mint=s["mint"] / 10 ** dec, vol_burn=s["burn"] / 10 ** dec, vol_other=s["other"] / 10 ** dec,
                supply_events=(s["mint"] - s["burn"]) / 10 ** dec,
                holders=sum(1 for v in bal.values() if v > 0), negative_balances=neg,
                burn_wallet_balance=sum(bal[a] for a in burners) / 10 ** dec,
                n_burn_wallets=len(burners))


# ---------------------------------------------------------------- D. Stellar
def stellar_supply(code, issuer):
    d = get(f"{HORIZON}/assets?asset_code={code}&asset_issuer={issuer}")
    r = d["_embedded"]["records"][0]
    b = r["balances"]
    tot = sum(float(b[k]) for k in b) + float(r["claimable_balances_amount"]) + \
        float(r["liquidity_pools_amount"]) + float(r["contracts_amount"])
    return tot, r


def stellar_ops(code, issuer):
    """Page issuer operations newest->oldest until created_at <= BACK_TO. Saves raw pages."""
    path = RAWO / f"{TODAY}_stellar_{code}_issuer_ops_to_{BACK_TO[:10]}.jsonl.gz"
    if path.exists():
        recs = [r for line in gzip.open(path, "rt") for r in json.loads(line)["_embedded"]["records"]]
        print(f"  reused {path.name}: {len(recs)} ops; fetching newer ops since cache")
        # ops newer than the cache (ascending from newest cached paging_token), saved as a delta file
        dpath = RAWO / f"{TODAY}_stellar_{code}_issuer_ops_delta_{dt.datetime.now(dt.timezone.utc):%H%M%S}.jsonl.gz"
        url = f"{HORIZON}/accounts/{issuer}/operations?order=asc&limit=200&cursor={recs[0]['paging_token']}"
        newer = []
        with gzip.open(dpath, "wt") as f:
            while url:
                page = get(url)
                f.write(json.dumps(page) + "\n")
                rs = page["_embedded"]["records"]
                if not rs:
                    break
                newer += rs
                url = page["_links"]["next"]["href"]
                time.sleep(0.15)
        print(f"  saved {dpath.name}: {len(newer)} newer ops")
        return list(reversed(newer)) + recs, f"{path.name}+{dpath.name}"
    url = f"{HORIZON}/accounts/{issuer}/operations?order=desc&limit=200"
    recs, pages = [], 0
    with gzip.open(path, "wt") as f:
        while url:
            for k in range(5):
                try:
                    page = get(url)
                    break
                except (urllib.error.URLError, TimeoutError, OSError) as e:
                    time.sleep(2 * (k + 1))
            else:
                raise RuntimeError(f"Horizon failed: {url}")
            f.write(json.dumps(page) + "\n")
            rs = page["_embedded"]["records"]
            pages += 1
            recs += rs
            if not rs or rs[-1]["created_at"] <= BACK_TO:
                break
            url = page["_links"]["next"]["href"]
            if pages % 50 == 0:
                print(f"   {pages} pages, at {rs[-1]['created_at']}")
            time.sleep(0.15)
    print(f"  saved {path.name}: {pages} pages, {len(recs)} ops")
    return recs, path.name


def classify_stellar(op, code, issuer):
    """Return (kind, amount) for supply-relevant ops on this asset, else (None, 0)."""
    t = op["type"]
    if t in ("payment", "path_payment_strict_receive", "path_payment_strict_send"):
        if op.get("asset_code") != code or op.get("asset_issuer") != issuer:
            return None, 0.0
        if op.get("from") == issuer and op.get("to") == issuer:
            return "self", float(op["amount"])
        if op.get("from") == issuer:
            return "mint", float(op["amount"])
        if op.get("to") == issuer:
            return "burn", float(op["amount"])
        return "other", float(op["amount"])
    if t == "clawback" and op.get("asset_code") == code and op.get("asset_issuer") == issuer:
        return "burn_clawback", float(op["amount"])
    if t == "create_claimable_balance" and op.get("asset", "").startswith(f"{code}:{issuer}") \
            and op.get("source_account") == issuer:
        return "mint_claimable", float(op["amount"])
    return None, 0.0


# ---------------------------------------------------------------- main
def main():
    RAWO.mkdir(parents=True, exist_ok=True)
    toks = issuer_addresses()
    print("Token addresses (parsed from raw issuer files):")
    for t in toks:
        print(f"  {t['fund']:6} {t['token']:6} {t['chain']:10} {t['address']}  [{t['status']}]")
    out = []
    access = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def row(t, snapshot, as_of, **kw):
        r = dict(fund=t["fund"], token=t["token"], chain=t["chain"], address=t["address"],
                 address_status=t["status"], snapshot=snapshot, as_of_utc=as_of, block_or_ledger="",
                 supply_call=None, supply_events=None, n_mint=None, n_burn=None, n_other=None,
                 vol_mint=None, vol_burn=None, vol_other=None, holders=None, burn_wallet_balance=None,
                 n_burn_wallets=None, method="", raw_file="",
                 accessed_utc=access, note="")
        r.update(kw)
        out.append(r)

    # ---- EVM chains
    heads = {}
    for t in [t for t in toks if t["chain"] in RPCS]:
        ch = t["chain"]
        print(f"\n[{t['fund']} {t['token']} on {ch}]")
        try:
            if ch not in heads:
                h, _ = rpc(ch, "eth_blockNumber", [])
                heads[ch] = int(h, 16)
            head = heads[ch]
            dec, _ = call_uint(ch, t["address"], "0x313ce567")
            name = call_str(ch, t["address"], "0x06fdde03")
            sup, url = call_uint(ch, t["address"], "0x18160ddd", hex(head))
            print(f"  name()={name!r} decimals={dec} totalSupply@{head}={sup / 10 ** dec:,.4f} via {url}")
        except RuntimeError as e:
            print(f"  FAILED head call: {e}")
            row(t, "head", access, method="eth_call totalSupply", note=f"FAILED: {e}"[:300])
            continue
        logs = None
        if ch != "ethereum" and ETHERSCAN_KEY and ch in CHAIN_IDS:
            try:
                logs, rawname = l2_logs(t, head)
            except (RuntimeError, KeyError, IndexError) as e:
                print(f"  Etherscan logs not available on {ch}: {str(e)[:150]}")
        if ch == "ethereum":
            logs, rawname = eth_logs(t, head)
        if logs is not None:
            ev = summarise_events(logs, dec)
            gap = sup / 10 ** dec - ev["supply_events"]
            print(f"  events: mints {ev['n_mint']} burns {ev['n_burn']} other {ev['n_other']} "
                  f"holders {ev['holders']} supply_events {ev['supply_events']:,.4f} (call-events gap {gap:,.6f})")
            row(t, "head", access, block_or_ledger=head, supply_call=sup / 10 ** dec,
                supply_events=ev["supply_events"], n_mint=ev["n_mint"], n_burn=ev["n_burn"],
                n_other=ev["n_other"], vol_mint=ev["vol_mint"], vol_burn=ev["vol_burn"],
                vol_other=ev["vol_other"], holders=ev["holders"],
                burn_wallet_balance=ev["burn_wallet_balance"], n_burn_wallets=ev["n_burn_wallets"],
                method=("eth_getLogs" if ch == "ethereum" else "Etherscan V2 getLogs")
                + " Transfer full history + eth_call totalSupply", raw_file=rawname,
                note=f"name()={name}; negative balances={ev['negative_balances']}")
        else:
            row(t, "head", access, block_or_ledger=head, supply_call=sup / 10 ** dec,
                method="eth_call totalSupply", note=f"name()={name}")
        for me in MONTH_ENDS + [(dt.date.fromisoformat(MONTH_ENDS[-1]) + dt.timedelta(days=1)).isoformat()]:
            snap = f"month_end {me}" if me in MONTH_ENDS else f"sensitivity +1d {me}"
            try:
                b = block_at(ch, ts(me), head)
            except RuntimeError as e:
                row(t, snap, f"{me}T23:59:59Z", method="block search", note=f"FAILED: {e}"[:300])
                continue
            kw = dict(block_or_ledger=b)
            try:
                s, url = call_uint(ch, t["address"], "0x18160ddd", hex(b))
                kw["supply_call"] = s / 10 ** dec
                kw["method"] = f"archive eth_call totalSupply ({url})"
            except RuntimeError as e:
                kw["note"] = f"archive call FAILED: {e}"[:300]
                kw["method"] = "archive eth_call totalSupply"
            if logs is not None:
                ev = summarise_events(logs, dec, upto_block=b)
                kw.update(supply_events=ev["supply_events"], n_mint=ev["n_mint"], n_burn=ev["n_burn"],
                          n_other=ev["n_other"], holders=ev["holders"], raw_file=rawname,
                          burn_wallet_balance=ev["burn_wallet_balance"], n_burn_wallets=ev["n_burn_wallets"])
                kw["method"] += " + events"
            row(t, snap, f"{me}T23:59:59Z", **kw)
            print(f"  {snap}: block {b} call={kw.get('supply_call')} events={kw.get('supply_events')}")

    # ---- Solana (head only)
    for t in [t for t in toks if t["chain"] == "solana"]:
        print(f"\n[{t['fund']} {t['token']} on solana]")
        try:
            r = post(SOLANA, {"jsonrpc": "2.0", "id": 1, "method": "getTokenSupply", "params": [t["address"]]})
            v = r["result"]["value"]
            row(t, "head", access, block_or_ledger=f"slot {r['result']['context']['slot']}",
                supply_call=float(v["uiAmountString"]), method="getTokenSupply (public RPC, head only)")
            print(f"  supply {v['uiAmountString']}")
        except Exception as e:
            row(t, "head", access, method="getTokenSupply", note=f"FAILED: {e}"[:300])
        row(t, "month_end (all)", "", method="not available",
            note="public Solana RPC has no historical state; needs an indexer (Dune public Solana transfers)")

    # ---- Aptos
    # Aptos fungible asset: 0x1::fungible_asset::supply view on the issuer-listed metadata object.
    # Public fullnode prunes state (oldest_ledger_version ~150M versions back), so month-end
    # history needs the Aptos indexer (GraphQL fungible_asset_activities) -- not done in the pilot.
    for t in [t for t in toks if t["chain"] == "aptos"]:
        print(f"\n[{t['fund']} {t['token']} on aptos]")
        try:
            def view(fn):
                return post("https://api.mainnet.aptoslabs.com/v1/view",
                            {"function": f"0x1::fungible_asset::{fn}",
                             "type_arguments": ["0x1::fungible_asset::Metadata"], "arguments": [t["address"]]})
            ledger = get("https://api.mainnet.aptoslabs.com/v1")
            dec = int(view("decimals")[0])
            sup = int(view("supply")[0]["vec"][0]) / 10 ** dec
            name = view("name")[0]
            print(f"  name={name!r} decimals={dec} supply={sup:,.4f} ledger_version={ledger['ledger_version']}")
            row(t, "head", access, block_or_ledger=f"ledger_version {ledger['ledger_version']}", supply_call=sup,
                method="Aptos view 0x1::fungible_asset::supply (public fullnode)",
                note=f"name()={name}; oldest_ledger_version={ledger['oldest_ledger_version']}")
        except Exception as e:
            row(t, "head", access, method="Aptos view", note=f"FAILED: {e}"[:300])
        row(t, "month_end (all)", "", method="not available",
            note="public Aptos fullnode prunes historical state; needs Aptos indexer GraphQL")

    # ---- Stellar
    for t in [t for t in toks if t["chain"] == "stellar"]:
        code, issuer = t["token"], t["address"]
        print(f"\n[{t['fund']} {code} on stellar]")
        t_sup = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        sup_now, rec = stellar_supply(code, issuer)
        auth = rec["accounts"]
        print(f"  supply now {sup_now:,.7f} (queried {t_sup}); accounts {auth}")
        ops, rawname = stellar_ops(code, issuer)
        # align the op window with the supply snapshot: drop ops after the /assets query time
        # (Horizon ledgers close ~5 s; a residual race of a few seconds is possible and noted)
        n0 = len(ops)
        ops = [o for o in ops if o["created_at"] <= t_sup]
        print(f"  ops after supply snapshot dropped: {n0 - len(ops)} (kept {len(ops)})")
        newest = ops[0]["created_at"] if ops else access
        kinds = defaultdict(lambda: [0, 0.0])
        cl = []
        for op in ops:
            k, a = classify_stellar(op, code, issuer)
            if k:
                kinds[k][0] += 1
                kinds[k][1] += a
                cl.append((op["created_at"], k, a))
        print("  op classification over pulled window:", {k: (v[0], round(v[1], 2)) for k, v in kinds.items()})
        print(f"  ops pulled {len(ops)}; window {ops[-1]['created_at'] if ops else ''} .. {newest}")
        row(t, "head", access, supply_call=sup_now, holders=auth["authorized"] + auth["authorized_to_maintain_liabilities"],
            method="Horizon /assets (sum of all balance buckets)", raw_file=rawname,
            note=f"accounts={auth}; holders = trustlines authorized (incl. zero balances? Horizon counts trustlines)")
        for me in MONTH_ENDS + [(dt.date.fromisoformat(MONTH_ENDS[-1]) + dt.timedelta(days=1)).isoformat()]:
            T = f"{me}T23:59:59Z"
            snap = f"month_end {me}" if me in MONTH_ENDS else f"sensitivity +1d {me}"
            after = [c for c in cl if c[0] > T]
            m = sum(a for _, k, a in after if k.startswith("mint"))
            bu = sum(a for _, k, a in after if k.startswith("burn"))
            within = [c for c in cl if c[0] <= T and c[0] > (dt.datetime.fromisoformat(T[:-1]) -
                      dt.timedelta(days=31)).strftime("%Y-%m-%dT%H:%M:%SZ")]
            row(t, snap, T, supply_call=None, supply_events=sup_now - m + bu,
                n_mint=sum(1 for c in within if c[1].startswith("mint")),
                n_burn=sum(1 for c in within if c[1].startswith("burn")),
                n_other=sum(1 for c in within if c[1] == "other"),
                vol_mint=sum(c[2] for c in within if c[1].startswith("mint")),
                vol_burn=sum(c[2] for c in within if c[1].startswith("burn")),
                method="back-out: Horizon supply now - issuer mints after T + issuer burns after T",
                raw_file=rawname,
                note=f"counts/volumes cover the 31 days to T; back-out uses {len(after)} ops after T")
            print(f"  {snap}: supply_backout {sup_now - m + bu:,.4f} (mints after {m:,.2f}, burns after {bu:,.2f})")

    with open(PROC / "pilot_onchain_summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(out)
    print(f"\nwrote pilot_onchain_summary.csv ({len(out)} rows)")


if __name__ == "__main__":
    main()
