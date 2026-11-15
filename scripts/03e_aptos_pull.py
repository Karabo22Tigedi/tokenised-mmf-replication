"""03e_aptos_pull.py -- Aptos leg (BENJI = FOBXX) from the Aptos Indexer GraphQL and fullnode REST API

Asset: the BENJI fungible-asset metadata object, parsed from the raw Franklin contracts page
(03_onchain_pilot.issuer_addresses(): heading "BENJI (Aptos)", label "Fund Token"), never typed by hand.
The script checks it on chain: the fullnode views 0x1::fungible_asset::{symbol,name,decimals} must give
symbol "BENJI" and a name containing "OnChain" (the issuer page lists only the address).

Method
  1. Indexer GraphQL `fungible_asset_activities` where asset_type = <metadata address>, full history,
     paged backwards (newest first) from a FIXED upper version: the indexer's last processed version
     at the first run, stored in activities/crawl_state.json so a resumed crawl keeps one vintage.
     Keyset on transaction_version with _lte, so a page cut inside one transaction loses nothing;
     the overlap rows are removed by (version, event_index). (The ascending query timed out on the
     anonymous endpoint in the 2026-10-16 smoke test; the descending one is index-friendly.) Failed transactions (is_transaction_success =
     false) are skipped and counted. Rows whose type is neither a Deposit nor a Withdraw (e.g. Frozen)
     carry no balance change and are counted, not used.
  2. Per transaction, owner-level net deltas (Deposit +, Withdraw -) are turned into events exactly as
     in 03c (Solana): net > 0 -> mint to the owner whose balance rose; net < 0 -> burn from the owner
     whose balance fell; matched rise/fall -> transfer. More than one owner rising or falling is split
     pairwise in order and flagged 'multi_leg'. (Fungible-asset mint/burn emit only the store-level
     Deposit/Withdraw, so supply changes are read from the per-transaction imbalance.)
  3. Timestamps: the indexer gives microseconds (UTC, no zone). They are floored to whole seconds so that
     the TR-1 cutoff 23:59:59 ET (scripts/rules.py, written for whole-second block times) keeps its
     meaning "before midnight ET". Order within a second is kept through the event id (version, leg).
  4. Month-end supply is rebuilt from events with rules.cutoff() (23:59:59 ET, plus the UTC sensitivity)
     at every calendar month-end and every governing FOBXX N-MFP3 report date (if <prefix>_nmfp_summary.csv
     exists) from the deployment month to --end (default 2026-09-30).
  5. Holder balances for M3: 05_measures rebuilds balances from the events (rules.balances_at). This
     script also pages `current_fungible_asset_balances` (positive balances kept in code) as a head cross-check and writes
     both side by side.
  Checks (printed): event-derived supply at the indexer's last processed version equals the fullnode
  view 0x1::fungible_asset::supply at that ledger version (falls back to the head if pruned);
  sum of indexer head balances equals the head supply.

Key: APTOS_API_KEY from <project>/.env (git-ignored), sent as "Authorization: Bearer". It is never
printed, logged or written. Without a key the script refuses a full pull; `--smoke` runs a handful of
anonymous requests (anonymous limit: 40,000 compute units per 300 s per IP) to validate query shapes.

Raw (unmodified response bodies, resumable): data/raw/aptos/<TAG>/
  activities/page_<NNNNNN>.json   one GraphQL response per page (the cursor is re-derived from them)
  balances/page_<NNNNNN>.json     head store balances (current_fungible_asset_balances)
  owners/, activities_by_owner/<owner>/   only if the per-owner fallback route is used
  fullnode_calls.jsonl            every REST call: url, body, accessed_utc, response
  manifest.jsonl                  file, query name, variables, accessed_utc, sha256
Output (data/processed):
  <prefix>_aptos_events.csv              same columns as <prefix>_solana_events.csv (block = txn version)
  <prefix>_aptos_month_end_supply.csv    fund, chain, token, date, clock, cutoff_utc, supply_events, n_events
  <prefix>_aptos_balances_head.csv       owner, indexer_amount, events_amount, diff
  --smoke: prep_aptos_smoke.csv only (check, result); raw under data/raw/aptos/prep_smoke_<date>/
Run: python -I scripts/03e_aptos_pull.py --tag freeze_2026-10-xx --prefix freeze
     python -I scripts/03e_aptos_pull.py --smoke
"""
import argparse
import csv
import datetime as dt
import hashlib
import importlib.util
import json
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import rules as R  # noqa: E402

spec = importlib.util.spec_from_file_location("pilot03", ROOT / "scripts" / "03_onchain_pilot.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)

GQL = "https://api.mainnet.aptoslabs.com/v1/graphql"
NODE = "https://api.mainnet.aptoslabs.com/v1"
PAGE = 1000          # rows per GraphQL page with a key
PAGE_ANON = 5        # rows per page in --smoke
ZERO = R.ZERO        # mint source / burn sink, as in every other chain's events

Q_ACT = """query Act($a: String!, $v: bigint!, $n: Int!) {
  fungible_asset_activities(
    where: {asset_type: {_eq: $a}, transaction_version: {_lte: $v}}
    order_by: [{transaction_version: desc}, {event_index: desc}], limit: $n) {
    transaction_version event_index owner_address storage_id amount type
    is_transaction_success transaction_timestamp block_height entry_function_id_str
  }
}"""
Q_ACT_OWNER = """query ActO($a: String!, $o: String!, $v: bigint!, $n: Int!) {
  fungible_asset_activities(
    where: {asset_type: {_eq: $a}, owner_address: {_eq: $o}, transaction_version: {_lte: $v}}
    order_by: [{transaction_version: desc}, {event_index: desc}], limit: $n) {
    transaction_version event_index owner_address storage_id amount type
    is_transaction_success transaction_timestamp block_height entry_function_id_str
  }
}"""
Q_OWNERS = """query Own($a: String!, $s: String!, $n: Int!) {
  current_fungible_asset_balances(
    where: {asset_type: {_eq: $a}, storage_id: {_gt: $s}}
    order_by: {storage_id: asc}, limit: $n) {
    storage_id owner_address amount is_primary is_frozen last_transaction_version
  }
}"""
Q_META = """query Meta($a: String!) {
  fungible_asset_metadata(where: {asset_type: {_eq: $a}}) {
    asset_type creator_address name symbol decimals token_standard
  }
}"""
Q_STATUS = """query { processor_status(where: {processor: {_eq: "fungible_asset_processor"}}) {
  processor last_success_version last_transaction_timestamp } }"""


# ---------------------------------------------------------------- pure helpers (unit-tested)
def parse_ts(s: str) -> dt.datetime:
    """Indexer timestamp (UTC, no zone, optional microseconds) -> aware UTC datetime floored to the second."""
    t = dt.datetime.fromisoformat(s.replace("Z", "")).replace(tzinfo=dt.timezone.utc)
    return t.replace(microsecond=0)


def activity_sign(typ: str) -> int:
    """+1 for a Deposit, -1 for a Withdraw, 0 for anything else (no balance change)."""
    name = typ.rsplit("::", 1)[-1]
    if name.startswith("Deposit"):
        return 1
    if name.startswith("Withdraw"):
        return -1
    return 0


def txn_legs(delta: dict, eps: float = 1e-12):
    """Owner -> net delta (one transaction) -> list of (frm, to, amount, kind), as in 03c (Solana).
    Returns (legs, multi_leg flag)."""
    ups = sorted([[o, v] for o, v in delta.items() if v > eps])
    downs = sorted([[o, -v] for o, v in delta.items() if v < -eps])
    multi = len(ups) > 1 or len(downs) > 1
    legs = []
    while ups and downs:
        m = min(ups[0][1], downs[0][1])
        legs.append((downs[0][0], ups[0][0], m, "transfer"))
        ups[0][1] -= m
        downs[0][1] -= m
        if ups[0][1] <= eps:
            ups.pop(0)
        if downs[0][1] <= eps:
            downs.pop(0)
    legs += [(ZERO, o, v, "mint") for o, v in ups]
    legs += [(o, ZERO, v, "burn") for o, v in downs]
    return legs, multi


def month_ends(first: dt.date, last: dt.date):
    out, y, m = [], first.year, first.month
    while True:
        nxt = dt.date(y + (m == 12), m % 12 + 1, 1)
        d = nxt - dt.timedelta(days=1)
        if d > last:
            return out
        out.append(d)
        y, m = nxt.year, nxt.month


# ---------------------------------------------------------------- I/O
class Client:
    def __init__(self, rawdir: Path, key, pause: float, tries: int = 8):
        self.rawdir, self.key, self.pause, self.tries = rawdir, key, pause, tries
        self.n = 0

    def _req(self, url, body=None):
        tries = self.tries
        hdr = {"Content-Type": "application/json", "User-Agent": P.UA}
        if self.key:
            hdr["Authorization"] = "Bearer " + self.key      # never printed or written
        data = json.dumps(body).encode() if body is not None else None
        last = None
        for k in range(tries):
            time.sleep(self.pause)
            self.n += 1
            try:
                with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=hdr), timeout=60) as r:
                    raw = r.read()
                d = json.loads(raw)
                errs = d.get("errors") if isinstance(d, dict) else None
                if errs and any("rate limit" in str(e).lower() for e in errs):
                    last = "GraphQL rate limit"
                    print(f"   rate limited (GraphQL); waiting {30 * (k + 1)} s", flush=True)
                    time.sleep(30 * (k + 1))
                    continue
                return d, raw
            except urllib.error.HTTPError as e:
                msg = e.read()[:300].decode(errors="replace")
                last = f"HTTP {e.code}: {msg[:120]}"
                if e.code == 429:
                    print(f"   HTTP 429; waiting {30 * (k + 1)} s", flush=True)
                    time.sleep(30 * (k + 1))
                    continue
                if e.code in (400, 404, 410) or (e.code == 408 and k >= 1):
                    # 408 = the indexer's 10 s upstream timeout: a query-plan problem, not transient
                    raise RuntimeError(f"HTTP {e.code} {url}: {msg}") from None
                time.sleep(3 * (k + 1))
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = repr(e)[:120]
                time.sleep(3 * (k + 1))
        raise RuntimeError(f"Aptos request failed after {tries} tries: {url} (last: {last})")

    def _manifest(self, rel, name, variables, raw):
        with open(self.rawdir / "manifest.jsonl", "a") as f:
            f.write(json.dumps(dict(file=rel, query=name, variables=variables,
                                    accessed_utc=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                                    sha256=hashlib.sha256(raw).hexdigest())) + "\n")

    def gql(self, name, query, variables, save_as=None):
        d, raw = self._req(GQL, {"query": query, "variables": variables})
        if save_as is None:
            save_as = f"{name}_{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%S}_{self.n}.json"
        p = self.rawdir / save_as
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".part")
        tmp.write_bytes(raw)
        tmp.replace(p)                       # atomic: a crash never leaves a half page behind
        self._manifest(save_as, name, variables, raw)
        if d.get("errors"):
            raise RuntimeError(f"GraphQL {name} errors: {json.dumps(d['errors'])[:400]}")
        return d["data"]

    def node(self, path, body=None):
        url = NODE + path
        d, raw = self._req(url, body)
        with open(self.rawdir / "fullnode_calls.jsonl", "a") as f:
            f.write(json.dumps(dict(url=url, body=body, accessed_utc=dt.datetime.now(dt.timezone.utc)
                                    .isoformat(timespec="seconds"), response=d)) + "\n")
        return d

    def view(self, fn, asset, version=None):
        q = f"?ledger_version={version}" if version is not None else ""
        return self.node("/view" + q, {"function": f"0x1::fungible_asset::{fn}",
                                       "type_arguments": ["0x1::fungible_asset::Metadata"], "arguments": [asset]})


def load_env_key(name="APTOS_API_KEY"):
    return P._load_env_key(name)      # reads .env; value never printed


def verify_asset(cl, asset):
    sym, name, dec = cl.view("symbol", asset)[0], cl.view("name", asset)[0], int(cl.view("decimals", asset)[0])
    ok = sym == "BENJI" and "OnChain" in name
    print(f"  on-chain metadata: symbol={sym!r} name={name!r} decimals={dec} -> {'OK' if ok else 'MISMATCH'}")
    if not ok:
        raise SystemExit("Aptos metadata does not match BENJI/FOBXX; stop and check the issuer page")
    return dec, sym, name


def crawl_pages(cl, sub, query, name, vars_fn, rows_key, cursor_fn, page):
    """Resumable keyset crawl: existing page files are re-read in order; the cursor comes from them."""
    d = cl.rawdir / sub
    d.mkdir(parents=True, exist_ok=True)
    rows, files = [], sorted(d.glob("page_*.json"))
    for f in files:
        rows += json.loads(f.read_bytes())["data"][rows_key]
    k, prev = len(files), object()
    if files:
        print(f"  resumed {sub}: {k} cached pages, {len(rows)} rows")
    while True:
        cur = cursor_fn(rows)
        if cur == prev:
            raise SystemExit(f"{sub}: cursor did not advance ({cur}); raise the page size and re-run")
        prev = cur
        got = cl.gql(name, query, vars_fn(cur, page), save_as=f"{sub}/page_{k:06d}.json")[rows_key]
        k += 1
        rows += got
        if k % 20 == 0:
            print(f"   {sub}: {k} pages, {len(rows)} rows", flush=True)
        if len(got) < page:
            break
    return rows


def dedup_activities(rows):
    seen, out = set(), []
    for r in rows:
        key = (int(r["transaction_version"]), int(r["event_index"]))
        if key not in seen:
            seen.add(key)
            out.append(r)
    return sorted(out, key=lambda r: (int(r["transaction_version"]), int(r["event_index"])))


# ---------------------------------------------------------------- modes
def smoke(asset):
    rawdir = ROOT / "data" / "raw" / "aptos" / f"prep_smoke_{dt.date.today().isoformat()}"
    rawdir.mkdir(parents=True, exist_ok=True)
    cl = Client(rawdir, None, pause=3.0, tries=1)   # smoke: one try per request, no retry storm
    res = []
    led = cl.node("")
    res.append(("fullnode ledger_version / oldest_ledger_version", f"{led['ledger_version']} / {led['oldest_ledger_version']}"))
    dec, sym, name = verify_asset(cl, asset)
    res.append(("fullnode metadata symbol, name, decimals", f"{sym}; {name}; {dec}"))
    for nm, q, v, key in [("meta", Q_META, {"a": asset}, "fungible_asset_metadata"),
                          ("status", Q_STATUS, {}, "processor_status"),
                          ("owners", Q_OWNERS, {"a": asset, "s": "", "n": PAGE_ANON}, "current_fungible_asset_balances"),
                          # last: the asset-wide query is the expensive one (408 + compute units)
                          ("activities", Q_ACT, {"a": asset, "v": 2 ** 62, "n": PAGE_ANON}, "fungible_asset_activities")]:
        try:
            rows = cl.gql(nm, q, v)[key]
            fields = sorted(rows[0]) if rows else []
            res.append((f"graphql {nm}: rows returned (limit {v.get('n', '-')}), fields", f"{len(rows)}; {' '.join(fields)}"))
            if nm == "owners" and rows:
                o = rows[0]["owner_address"]
                try:
                    r2 = cl.gql("activities_owner", Q_ACT_OWNER, {"a": asset, "o": o, "v": 2 ** 62, "n": PAGE_ANON})["fungible_asset_activities"]
                    res.append(("graphql activities_owner (fallback route): rows for first owner, types",
                                f"{len(r2)}; {';'.join(sorted({r['type'] for r in r2}))}"))
                except Exception as e:
                    res.append(("graphql activities_owner (fallback route)", f"FAILED: {e}"[:300]))
            if nm == "activities" and rows:
                res.append(("latest activity in sample: version, timestamp UTC, type",
                            f"{rows[0]['transaction_version']}; {parse_ts(rows[0]['transaction_timestamp']):%Y-%m-%dT%H:%M:%SZ}; {rows[0]['type']}"))
                res.append(("activity types in sample", ";".join(sorted({r['type'] for r in rows}))))
        except Exception as e:
            res.append((f"graphql {nm}", f"FAILED: {e}"[:300]))
    res.append(("requests sent", str(cl.n)))
    for k, v in res:
        print(f"  {k}: {v}")
    fn = ROOT / "data" / "processed" / "prep_aptos_smoke.csv"
    with open(fn, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["check", "result"])
        w.writerows(res)
    print(f"wrote {fn.name} (smoke test only; no supply history, no month-end values)")


def full(asset, tag, prefix, end, key):
    rawdir = ROOT / "data" / "raw" / "aptos" / tag
    rawdir.mkdir(parents=True, exist_ok=True)
    cl = Client(rawdir, key, pause=0.25)
    dec, _, _ = verify_asset(cl, asset)
    scale = Decimal(10) ** dec
    st = cl.gql("status", Q_STATUS, {})["processor_status"]
    last_ver = int(st[0]["last_success_version"]) if st else None
    print(f"  indexer fungible_asset_processor last_success_version {last_ver}")

    state = rawdir / "activities" / "crawl_state.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    if state.exists():
        upper = json.loads(state.read_text())["upper_version"]
        print(f"  resuming with the stored upper version {upper}")
    else:
        if last_ver is None:
            raise SystemExit("indexer processor_status unavailable; cannot fix the upper version")
        upper = last_ver
        state.write_text(json.dumps(dict(upper_version=upper, set_utc=dt.datetime.now(dt.timezone.utc)
                                         .isoformat(timespec="seconds"))))
    last_ver = upper

    def act_cursor(rows):       # descending pages: the next page starts at the oldest version seen
        return int(rows[-1]["transaction_version"]) if rows else upper

    try:
        if (rawdir / "owners").exists():
            raise RuntimeError("HTTP 408 (remembered from an earlier run of this tag)")
        raw_rows = crawl_pages(cl, "activities", Q_ACT, "activities",
                               lambda v, n: {"a": asset, "v": v, "n": n}, "fungible_asset_activities", act_cursor, PAGE)
        route = "asset-wide"
    except RuntimeError as e:
        if "HTTP 408" not in str(e):
            raise
        # Fallback (the asset-wide filter timed out in the 2026-10-16 smoke test): every store that ever
        # held the asset keeps a row in current_fungible_asset_balances (zero balances included), so the
        # owner list from that table plus per-owner activity queries (owner index) covers the history.
        # Completeness is checked below: event supply must equal the fullnode supply.
        print("  asset-wide activity query timed out (HTTP 408): falling back to per-owner queries")
        stores = crawl_pages(cl, "owners", Q_OWNERS, "owners", lambda s_, n: {"a": asset, "s": s_, "n": n},
                             "current_fungible_asset_balances", lambda rows: rows[-1]["storage_id"] if rows else "", PAGE)
        owners = sorted({r["owner_address"] for r in stores})
        print(f"  stores ever holding BENJI (incl. zero balance): {len(stores)}; owners {len(owners)}")
        raw_rows = []
        for i, o in enumerate(owners, 1):
            raw_rows += crawl_pages(cl, f"activities_by_owner/{o}", Q_ACT_OWNER, "activities_owner",
                                    lambda v, n, o=o: {"a": asset, "o": o, "v": v, "n": n},
                                    "fungible_asset_activities", act_cursor, PAGE)
            if i % 100 == 0:
                print(f"   owners done {i}/{len(owners)}", flush=True)
        route = "per-owner"
    print(f"  activity route: {route}")
    acts = dedup_activities(raw_rows)
    print(f"  activity rows: {len(raw_rows)} fetched, {len(acts)} after removing page-overlap duplicates")

    by_tx = defaultdict(list)
    n_fail = n_other = 0
    for r in acts:
        if not r["is_transaction_success"]:
            n_fail += 1                                      # failed txn: no state change
            continue
        s = activity_sign(r["type"])
        if s == 0 or r["amount"] is None:
            n_other += 1                                     # e.g. Frozen: no balance change
            continue
        by_tx[int(r["transaction_version"])].append((r, s))
    print(f"  filter: {len(acts)} rows -> skipped failed-txn rows {n_fail}, non-balance rows {n_other}; "
          f"{sum(len(v) for v in by_tx.values())} balance rows in {len(by_tx)} transactions")

    evs, n_multi = [], 0
    for ver in sorted(by_tx):
        rr = by_tx[ver]
        delta = defaultdict(Decimal)
        for r, s in rr:
            delta[r["owner_address"]] += s * Decimal(r["amount"]) / scale
        legs, multi = txn_legs({o: float(v) for o, v in delta.items() if v != 0})
        n_multi += multi and bool(legs)
        ts = parse_ts(rr[0][0]["transaction_timestamp"]).strftime("%Y-%m-%dT%H:%M:%SZ")
        for j, (fr, to, v, kind) in enumerate(legs):
            evs.append(dict(fund="FOBXX", chain="aptos", token="BENJI", id=f"{ver:012d}{j:06d}", ts_utc=ts,
                            frm=fr, to=to, amount=repr(v), kind=kind, tx=str(ver), block=ver, log_index=j,
                            raw_file="activities/page_*.json" + (";multi_leg" if multi else "")))
    kinds = {k: sum(e["kind"] == k for e in evs) for k in ("mint", "burn", "transfer")}
    print(f"  events {len(evs)} {kinds}; multi-leg transactions {n_multi}")
    if not evs:
        raise SystemExit("no Aptos events built; check the query shape with --smoke")

    # check: event supply vs fullnode supply at the indexer's last processed version (else head)
    sup_ev = sum(float(e["amount"]) * (1 if e["kind"] == "mint" else -1) for e in evs if e["kind"] != "transfer")
    try:
        chk = cl.view("supply", asset, last_ver)
        at = f"ledger_version {last_ver}"
    except RuntimeError as e:
        print(f"  view at {last_ver} failed ({str(e)[:80]}); using head")
        chk, at = cl.view("supply", asset), "head"
    sup_node = int(chk[0]["vec"][0]) / 10 ** dec
    print(f"  CHECK supply from events {sup_ev:,.9f} vs fullnode supply {sup_node:,.9f} ({at}): diff {sup_ev - sup_node:,.9f}")

    # month-end supply (TR-1: rules.cutoff, ET main and UTC sensitivity)
    e_rules = [dict(id=e["id"], ts=dt.datetime.fromisoformat(e["ts_utc"].replace("Z", "+00:00")),
                    frm=e["frm"], to=e["to"], amount=float(e["amount"]), kind=e["kind"]) for e in evs]
    first = e_rules[0]["ts"].astimezone(R.ET).date()
    dates = set(month_ends(first.replace(day=1), end))
    summ = ROOT / "data" / "processed" / f"{prefix}_nmfp_summary.csv"
    if summ.exists():
        with open(summ, newline="") as f:
            dates |= {dt.date.fromisoformat(r["report_date"]) for r in csv.DictReader(f)
                      if r["ticker_file"] == "FOBXX" and r["governing"] == "1"
                      and first <= dt.date.fromisoformat(r["report_date"]) <= end}
    me = []
    for d in sorted(dates):
        for clock in ("ET", "UTC"):
            cut = R.cutoff(d, clock)
            me.append(dict(fund="FOBXX", chain="aptos", token="BENJI", date=d, clock=clock,
                           cutoff_utc=cut.strftime("%Y-%m-%dT%H:%M:%SZ"),
                           supply_events=round(R.supply_at(e_rules, cut), 9),
                           n_events=sum(1 for e in e_rules if e["ts"] <= cut)))
    print(f"  month-end supply rows: {len(me)} ({len(dates)} dates x ET/UTC, {first} to {end})")

    # head balances (indexer) vs event-derived balances at the head
    # same query as the owner list (validated in the smoke test); positive balances are kept in code
    bal_all = crawl_pages(cl, "balances", Q_OWNERS, "balances", lambda s, n: {"a": asset, "s": s, "n": n},
                           "current_fungible_asset_balances",
                           lambda rows: rows[-1]["storage_id"] if rows else "", PAGE)
    bal_rows = [r for r in bal_all if int(r["amount"]) > 0]
    print(f"  head store rows {len(bal_all)} -> positive balance {len(bal_rows)} (zero-balance stores dropped)")
    idx = defaultdict(float)
    for r in bal_rows:
        idx[r["owner_address"]] += int(r["amount"]) / 10 ** dec
    evb = R.balances_at(e_rules, dt.datetime.now(dt.timezone.utc))
    owners = sorted(set(idx) | {o for o, v in evb.items() if abs(v) > 1e-9})
    hb = [dict(owner=o, indexer_amount=round(idx.get(o, 0.0), 9), events_amount=round(evb.get(o, 0.0), 9),
               diff=round(evb.get(o, 0.0) - idx.get(o, 0.0), 9)) for o in owners]
    print(f"  head balances: indexer {len(idx)} owners, sum {sum(idx.values()):,.9f}; events sum "
          f"{sum(v for v in evb.values() if v > 1e-9):,.9f}; owners with |diff| > 1e-6: "
          f"{sum(1 for r in hb if abs(r['diff']) > 1e-6)}")

    for rows, name in [(evs, f"{prefix}_aptos_events.csv"), (me, f"{prefix}_aptos_month_end_supply.csv"),
                       (hb, f"{prefix}_aptos_balances_head.csv")]:
        with open(ROOT / "data" / "processed" / name, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print(f"wrote {name}: {len(rows)} rows")
    print(f"requests sent: {cl.n}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag")
    ap.add_argument("--prefix")
    ap.add_argument("--end", default="2026-09-30", help="last month-end (ET date) for the supply series")
    ap.add_argument("--smoke", action="store_true", help="a handful of anonymous requests; prep_aptos_smoke.csv only")
    a = ap.parse_args()
    toks = [t for t in P.issuer_addresses() if t["chain"] == "aptos"]
    if len(toks) != 1 or toks[0]["fund"] != "FOBXX":
        raise SystemExit(f"expected exactly one FOBXX Aptos Fund Token on the issuer page, got {toks}")
    asset = toks[0]["address"].lower()
    print(f"[FOBXX BENJI aptos metadata {asset}] source: {toks[0]['source_file']} ({toks[0]['status']})")
    if a.smoke:
        return smoke(asset)
    key = load_env_key()
    if not key:
        raise SystemExit("APTOS_API_KEY not in .env: full pull refused (anonymous limit is too low). "
                         "Use --smoke to validate query shapes, or see notes/manual_downloads.md D5a.")
    if not (a.tag and a.prefix):
        raise SystemExit("--tag and --prefix are required for the full pull")
    full(asset, a.tag, a.prefix, dt.date.fromisoformat(a.end), key)


if __name__ == "__main__":
    main()
