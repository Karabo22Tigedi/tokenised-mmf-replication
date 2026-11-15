"""15_addl_analysis.py -- ADDITION 2026-10-17 (exploratory, post-freeze, user-approved CP35): analyses

Exploratory analyses requested in reviews/cycle1_panel_markets.md and reviews/cycle1_substance_critic.md.
They do NOT change any pre-registered measure, rule or verdict, and do not write any freeze_* file.

Inputs
  frozen:   data/processed/freeze_*.csv, data/raw/stellar/freeze_2026-10-16/ (Horizon pages),
            data/raw/onchain/freeze_2026-10-16/solana_*_transactions.jsonl.gz
  addition: data/processed/addl_f1_events.csv (13), data/raw/addition_2026-10-17/ (14)
Outputs (data/processed/)
  A1 addl_signer_function.csv   one row per non-TA (F1 upper-numerator) transfer: signer, called function,
                                signer class, function class
     addl_signer_summary.csv    counts and value shares by fund x chain x signer class (and x function class)
  A2 addl_concentration.csv     top-1/3/5 share of non-TA transfer value (lower and upper sets)
     addl_top_counterparties.csv largest non-TA counterparties with public labels and primary-market flags
     addl_pm_wallet_profile.csv  wallet 0x0c561ed8... and the wallets it sent to (long format)
  A3 addl_m1_excl_pm.csv        M1 (pooled, transfers_only) recomputed without primary-market wallets
  A4 addl_stellar_issuer_controls.csv, addl_evm_admin_functions.csv, addl_solana_mint_controls.csv
  A5 addl_fyoxx_quarter_gaps.csv, addl_fyoxx_parked_lots.csv
  A6 addl_fragmentation_supply.csv, addl_fragmentation_transfers.csv
  A7 addl_nmfp_rec_ben.csv, addl_nmfp_rec_ben_summary.csv
  addl_summary_numbers.csv      name (addl_*), value, unit, as_of, source_file: read by 07_key_numbers.py

Run: python -I scripts/15_addl_analysis.py
"""
import base64
import csv
import datetime as dt
import gzip
import importlib.util
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import rules as R            # noqa: E402
import stellar_events as SE  # noqa: E402


def _imp(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


P = _imp("pilot03", "03_onchain_pilot.py")
F14 = _imp("fetch14", "14_addl_fetch.py")
F13 = _imp("f13", "13_addl_f1_transfers.py")
PROC = ROOT / "data" / "processed"
ADD = ROOT / "data" / "raw" / "addition_2026-10-17"
STAG = ROOT / "data" / "raw" / "stellar" / "freeze_2026-10-16"
SOLRAW = ROOT / "data" / "raw" / "onchain" / "freeze_2026-10-16"
CRITIC_WALLET = "0x0c561ed86ef65dd681500d6266c99e03dec3c541"
ENTRYPOINTS = {"0x0000000071727de22e5e9d8baf0edac6f37da032", "0x5ff137d4b0fdcd49dca30c7cf57e578a026d2789"}
SAFE_NAMES = {"SafeProxy", "GnosisSafeProxy"}
SUMMARY = []


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


def sn(name, value, unit, as_of, source):
    assert name.startswith("addl_")
    SUMMARY.append(dict(name=name, value=value, unit=unit, as_of=as_of, source_file=f"data/processed/{source}"))


def pct(a, b):
    return round(100 * a / b, 4) if b else ""


def jl(name):
    p = ADD / name
    return [json.loads(l) for l in open(p, encoding="utf-8")] if p.exists() else []


# ====================================================================== shared inputs
EV = rd("addl_f1_events.csv")
for r in EV:
    r["amount"] = float(r["amount"])
NONTA = [r for r in EV if r["nonta"] == "1"]
ROLES = {(r["fund"], r["chain"], r["address"]): (r["role"], r["reasons"]) for r in rd("freeze_wallet_roles.csv")}
TOKS = P.issuer_addresses()
TOKADDR = {(t["fund"], t["chain"]): t["address"].lower() if t["address"].startswith("0x") else t["address"] for t in TOKS}
ISSUER = {t["fund"]: t["address"] for t in TOKS if t["chain"] == "stellar"}
WINDOW = "2026-01-01:2026-09-30"

evm_calls = jl("evm_calls.jsonl")
TX = {tuple(c["params"]): (c["response"].get("result") or {}) for c in evm_calls if c["method"] == "eth_getTransactionByHash"}
SRC = {}
for c in evm_calls:
    if c["method"] == "getsourcecode":
        res = c["response"].get("result") or [{}]
        SRC[tuple(c["params"])] = res[0] if isinstance(res, list) and res else {}
OWNERS = {}
for c in evm_calls:
    if c["method"] == "eth_call_getOwners":
        h = (c["response"].get("result") or "0x")[2:]
        words = [h[i:i + 64] for i in range(0, len(h), 64)]
        n = int(words[1], 16) if len(words) > 1 else 0
        OWNERS[tuple(c["params"])] = {"0x" + w[-40:] for w in words[2:2 + n]}
FOURBYTE = {c["params"][0]: [x["text_signature"] for x in c["response"].get("results", [])] for c in jl("fourbyte_calls.jsonl")}


def abi_of(chain, addr):
    """Function list from the verified ABI of addr, plus its implementation if a proxy."""
    out = []
    for a in (addr, (SRC.get((chain, addr), {}).get("Implementation") or "").lower()):
        s = SRC.get((chain, a), {})
        abi = s.get("ABI", "")
        if abi.startswith("["):
            out += [it for it in json.loads(abi) if it.get("type") == "function"]
    return out


def selector_name(chain, addr, sel):
    for it in abi_of(chain, addr):
        sig = f"{it['name']}({','.join(F14.P_type(i) for i in it.get('inputs', []))})"
        if F14.keccak_selector(sig) == sel:
            return it["name"], "verified ABI"
    if FOURBYTE.get(sel):
        return FOURBYTE[sel][-1].split("(")[0], "4byte"
    return f"unknown({sel})", "not in verified ABI or 4byte"


def cname(chain, addr):
    s = SRC.get((chain, addr), {})
    n = s.get("ContractName", "")
    impl = (s.get("Implementation") or "").lower()
    if impl and SRC.get((chain, impl), {}).get("ContractName"):
        n = f"{n}->{SRC[(chain, impl)]['ContractName']}"
    return n


def label_title(chain, addr):
    import source_facts
    t = source_facts.label_title(ADD / "labels" / f"{chain}_{addr}.html")
    return t.split("| Address")[0].strip() if "| Address" in t else ""


def words(data):
    h = data[10:]
    return [h[i:i + 64] for i in range(0, len(h), 64)]


def safe_inner(data):
    """execTransaction(address to,uint256 value,bytes data,...) -> (inner_to, inner_selector)."""
    w = words(data)
    to = "0x" + w[0][-40:]
    off = int(w[2], 16) // 32
    ln = int(w[off], 16)
    inner = "".join(w[off + 1:off + 1 + (ln + 31) // 32])[:ln * 2]
    return to, "0x" + inner[:8]


def userop_senders(data):
    """handleOps(PackedUserOperation[] ops, address beneficiary): the sender (first field) of each op."""
    w = words(data)
    arr = int(w[0], 16) // 32
    n = int(w[arr], 16)
    out = []
    for i in range(n):
        off = int(w[arr + 1 + i], 16) // 32
        out.append("0x" + w[arr + 1 + off][-40:])
    return out


# ====================================================================== A1 signer and function
def a1():
    print("A1 signer and function")
    rows = []
    # Stellar: frozen holder account signers (trustline records), historical set_options from the frozen ops
    acct = {}
    for code in ("BENJI", "WTGX"):
        for pg in SE.read_pages(STAG / f"trustlines_{code}.jsonl.gz"):
            for r in pg["_embedded"]["records"]:
                acct[r["account_id"]] = r
    st = {}
    for c in jl("stellar_calls.jsonl"):
        st[(c["source"], c["params"][0])] = c["response"]
    issuer_signers = {}
    for fund, iss in ISSUER.items():
        a = st.get(("horizon", f"/accounts/{iss}"), {})
        issuer_signers[fund] = {s["key"] for s in a.get("signers", []) if s["weight"] > 0}
    key_hist = defaultdict(set)          # key -> accounts it was ever added to (set_options weight>0 or current)
    key_removed_on = defaultdict(set)
    for f in sorted((STAG / "ops").glob("*.jsonl.gz")):
        for pg in SE.read_pages(f):
            for r in pg.get("_embedded", {}).get("records", []):
                if r["type"] == "set_options" and str(r.get("signer_key", "")).startswith("G"):
                    if int(r.get("signer_weight", 0)) > 0:
                        key_hist[r["signer_key"]].add(r["source_account"])
                        for fund_, iss_ in ISSUER.items():
                            if r["source_account"] == iss_:
                                issuer_signers[fund_].add(r["signer_key"])     # former issuer-account signers
                    else:
                        key_removed_on[r["signer_key"]].add(r["created_at"][:10])
                        key_hist[r["signer_key"]].add(r["source_account"])
    for a in acct.values():
        for s in a["signers"]:
            key_hist[s["key"]].add(a["account_id"])
    hint_of = {}
    for k in key_hist:
        try:
            hint_of.setdefault(base64.b32decode(k)[1:33][-4:].hex(), set()).add(k)
        except Exception:
            pass
    # issuer-account signer keys ever (current + historical set_options with issuer as source)
    sol_tx = {}
    for tok in ("WTGXX", "BENJI"):
        p = SOLRAW / f"solana_{tok}_transactions.jsonl.gz"
        for line in gzip.open(p):
            d = json.loads(line)
            res = d.get("result")
            if res:
                sol_tx[res["transaction"]["signatures"][0]] = res

    for e in NONTA:
        fund, ch = e["fund"], e["chain"]
        ta = {a for (f_, c_, a), (rl, _) in ROLES.items() if f_ == fund and c_ == ch and rl == "TA"}
        base = dict(fund=fund, chain=ch, ts_utc=e["ts_utc"], frm=e["frm"], to=e["to"], amount=e["amount"],
                    pair=e["pair"], tx=e["tx"])
        if ch in P.CHAIN_IDS:
            t = TX[(ch, e["tx"])]
            signer, tx_to, data = t["from"].lower(), (t.get("to") or "").lower(), t.get("input", "0x")
            sel = data[:10]
            fn, fn_src = selector_name(ch, tx_to, sel)
            inner, extra = "", ""
            token = TOKADDR[(fund, ch)]
            owners = set()
            for (c_, a_, blk), o in OWNERS.items():
                if c_ == ch and blk == t["blockNumber"] and a_ in (e["frm"], e["to"], tx_to):
                    owners |= {(a_, x) for x in o}
            own_frm = {x for a_, x in owners if a_ == e["frm"]}
            own_to = {x for a_, x in owners if a_ == e["to"]}
            senders = []
            if fn == "execTransaction":
                ito, isel = safe_inner(data)
                iname, _ = selector_name(ch, ito, isel)
                inner = f"{cname(ch, ito) or ito}.{iname}"
            elif fn == "handleOps" or tx_to in ENTRYPOINTS:
                senders = userop_senders(data)
                extra = "UserOp senders " + ";".join(senders)
            # signer class (first match wins; order stated in notes/addl_analysis.md). For ERC-4337 bundles the
            # authorising party is the UserOp sender (smart account), not the bundler EOA (tx.from).
            if senders:
                bundler_role = "TA" if signer in ta else "not TA"
                extra += f"; bundler {signer} freeze role {bundler_role}"
                if e["frm"] in senders:
                    sc = "sender smart account (ERC-4337 UserOp)"
                elif any(x in ta for x in senders):
                    sc = "issuer/TA smart account (ERC-4337 UserOp)"
                elif e["to"] in senders:
                    sc = "receiver smart account (ERC-4337 UserOp)"
                else:
                    sc = "third-party smart account (ERC-4337 UserOp sender is neither party)"
            elif signer in ta:
                sc = "issuer/TA wallet signs"
            elif signer == e["frm"]:
                sc = "sender EOA signs"
            elif fn == "execTransaction" and tx_to == e["frm"] and signer in own_frm:
                sc = "owner of sender's Safe executes"
            elif e["frm"] in senders:
                sc = "sender smart account (ERC-4337 UserOp)"
            elif signer in own_frm:
                sc = "owner of sender's Safe calls other contract"
            elif signer == e["to"]:
                sc = "receiver EOA signs (pull via contract)"
            elif fn == "execTransaction" and tx_to == e["to"] and signer in own_to:
                sc = "owner of receiver's Safe executes (pull via contract)"
            elif e["to"] in senders:
                sc = "receiver smart account (ERC-4337 UserOp)"
            else:
                sc = "other third party signs"
            if tx_to == token:
                fc = f"token.{fn} (direct)"
            elif fn == "execTransaction":
                fc = f"Safe.execTransaction -> {inner}"
            elif tx_to in ENTRYPOINTS:
                fc = "EntryPoint.handleOps (ERC-4337 bundle)"
            else:
                fc = f"{cname(ch, tx_to) or 'unverified contract'}.{fn}"
            rows.append(dict(base, signer=signer, tx_to=tx_to, tx_to_contract=cname(ch, tx_to), selector=sel,
                             function=fn, function_source=fn_src, inner_call=inner, detail=extra,
                             signer_label=label_title(ch, signer), signer_class=sc, function_class=fc))
        elif ch == "stellar":
            t = st[("horizon", f"/transactions/{e['tx']}")]
            ops = st[("horizon", f"/transactions/{e['tx']}/operations?limit=200")]["_embedded"]["records"]
            env = base64.b64decode(t["envelope_xdr"])
            keys = []
            for sg in t["signatures"]:
                b = base64.b64decode(sg)
                i = env.find(b)
                h = env[i - 8:i - 4].hex() if i >= 8 else ""
                cand = hint_of.get(h, set())
                keys.append(sorted(cand)[0] if len(cand) == 1 else (f"hint:{h}" if not cand else f"ambiguous:{h}"))
            frm_master_w = next((s["weight"] for s in acct.get(e["frm"], {}).get("signers", []) if s["key"] == e["frm"]), "")
            iss_keys = issuer_signers.get(fund, set())
            n_iss = sum(1 for k in keys if k in iss_keys)
            n_master = sum(1 for k in keys if k == e["frm"])
            n_multi = sum(1 for k in keys if k.startswith("G") and len(key_hist[k]) > 100)
            if n_master and n_master == len(keys):
                sc = "sender account master key signs"
            elif n_iss == len(keys):
                sc = "issuer-account signer keys (current or former) sign"
            elif n_iss + n_multi >= len(keys) and keys:
                sc = "operator keys sign (keys on >100 holder accounts; incl. issuer-account signers)"
            else:
                sc = "other / unresolved keys"
            optypes = ";".join(f"{o['type']}[src={'sender' if o.get('source_account') == e['frm'] else 'issuer' if o.get('source_account') == ISSUER[fund] else 'other'}]" for o in ops)
            rows.append(dict(base, signer=";".join(keys), tx_to="", tx_to_contract="",
                             selector="", function=optypes, function_source="Horizon operations",
                             inner_call="", detail=f"tx source {'sender' if t['source_account'] == e['frm'] else t['source_account']}; "
                                                   f"fee account {'sender' if t.get('fee_account') == e['frm'] else t.get('fee_account')}; "
                                                   f"{len(t['signatures'])} signatures; sender master weight {frm_master_w}; "
                                                   f"sender signers (16 Oct) {len(acct.get(e['frm'], {}).get('signers', []))}; "
                                                   f"signature keys that are current or former issuer-account signers {n_iss}",
                             signer_label="", signer_class=sc,
                             function_class="payment (op source = sender)" if all(o.get("source_account") == e["frm"] for o in ops) and len(ops) == 1 else optypes))
        elif ch == "solana":
            res = sol_tx[e["tx"]]
            msg = res["transaction"]["message"]
            signers = [k["pubkey"] for k in msg["accountKeys"] if k.get("signer")]
            allins = list(msg["instructions"]) + [i for g in res["meta"].get("innerInstructions", []) for i in g["instructions"]]
            ins = [i for i in allins if isinstance(i.get("parsed"), dict)
                   and i["parsed"].get("type") in ("transfer", "transferChecked")
                   and i["parsed"].get("info", {}).get("mint", "") in ("", TOKADDR[(fund, ch)])]
            top_prog = ";".join(sorted({i.get("programId", "") for i in msg["instructions"]} - {"ComputeBudget111111111111111111111111111111"}))
            info = ins[0]["parsed"]["info"] if ins else {}
            auth = info.get("authority") or info.get("multisigAuthority") or ""
            src_owner = ""
            keys = [k["pubkey"] for k in msg["accountKeys"]]
            for b in res["meta"].get("preTokenBalances", []):
                if keys[b["accountIndex"]] == info.get("source"):
                    src_owner = b.get("owner", "")
            # signer class from the transaction signers (fee payer first); the parsed transfer authority is
            # reported in detail (equal to the source owner in every row, or a program-derived account)
            if any(x in ta for x in signers):
                sc = "issuer/TA wallet signs"
            elif e["frm"] in signers:
                sc = "sender signs"
            elif e["to"] in signers:
                sc = "receiver signs (source owner not among tx signers)"
            else:
                sc = "other signer"
            rows.append(dict(base, signer=";".join(signers), tx_to="", tx_to_contract="", selector="",
                             function=";".join(i["parsed"]["type"] for i in ins), function_source="frozen getTransaction(jsonParsed)",
                             inner_call=f"top-level programs {top_prog}; transfer instruction {'inner (CPI)' if ins and ins[0] not in msg['instructions'] else 'top-level'}",
                             detail=f"authority {auth}; authority role {ROLES.get((fund, ch, auth), ('holder', ''))[0]}; "
                                                   f"source token account owner {src_owner}; extra accounts parsed as signers (transfer-hook accounts) {len(info.get('signers', []))}",
                             signer_label="", signer_class=sc,
                             function_class=f"spl-token {ins[0]['parsed']['type'] if ins else '?'}"))
    for r in rows:
        c_ = r["signer_class"]
        r["signer_group"] = ("issuer/TA/operator" if c_.startswith("issuer") or "operator keys" in c_
                             else "sender holder" if c_.startswith("sender") or "of sender" in c_
                             else "receiver (pull)" if c_.startswith("receiver") or "of receiver" in c_
                             else "other")
    write(rows, "addl_signer_function.csv")

    # summary
    summ = []
    tot = defaultdict(lambda: [0, 0.0])
    for r in rows:
        for k in ((r["fund"], r["chain"]), (r["fund"], "all")):
            tot[k][0] += 1
            tot[k][1] += r["amount"]
    for dim in ("signer_group", "signer_class", "function_class"):
        agg = defaultdict(lambda: [0, 0.0])
        for r in rows:
            for k in ((r["fund"], r["chain"], r[dim]), (r["fund"], "all", r[dim])):
                agg[k][0] += 1
                agg[k][1] += r["amount"]
        for (fund, ch, cls), (n, v) in sorted(agg.items()):
            if ch == "all" and len({c for (f_, c, _) in agg if f_ == fund and c != "all"}) < 2:
                continue
            summ.append(dict(dimension=dim, fund=fund, chain=ch, cls=cls, n=n, value=round(v, 6),
                             share_count_pct=pct(n, tot[(fund, ch)][0]), share_value_pct=pct(v, tot[(fund, ch)][1])))
    write(summ, "addl_signer_summary.csv")
    for s in summ:
        if s["dimension"] in ("signer_group", "signer_class"):
            slug = ("grp_" if s["dimension"] == "signer_group" else "") + re.sub(r"[^a-z0-9]+", "_", s["cls"].lower()).strip("_")[:50]
            slug = re.sub(r"[^a-z0-9]+", "_", s["cls"].lower()).strip("_")[:50]
            sn(f"addl_signer_n__{s['fund']}__{s['chain']}__{slug}", s["n"], "count", WINDOW, "addl_signer_summary.csv")
            sn(f"addl_signer_value_share_pct__{s['fund']}__{s['chain']}__{slug}", s["share_value_pct"], "percent of non-TA transfer value", WINDOW, "addl_signer_summary.csv")
    for (fund, ch), (n, v) in sorted(tot.items()):
        sn(f"addl_nonta_transfers_n__{fund}__{ch}", n, "count", WINDOW, "addl_signer_summary.csv")
    # admin function use among non-TA transfers
    sn("addl_jltxx_bundler_is_ta_wallet_n", sum(1 for r in rows if r["fund"] == "JLTXX" and "freeze role TA" in r["detail"]),
       "count of JLTXX non-TA transfers whose ERC-4337 bundler (tx.from) is a TA-role wallet", WINDOW, "addl_signer_function.csv")
    sn("addl_fobxx_stellar_sender_master_weight0_n", sum(1 for r in rows if r["fund"] == "FOBXX" and "sender master weight 0" in r["detail"]),
       "count of FOBXX non-TA transfers whose sender account master key has weight 0 (16 Oct)", WINDOW, "addl_signer_function.csv")
    adminish = [r for r in rows if re.search(r"force|admin|operator|recover|seize|clawback|burnFrom", r["function"] + r["inner_call"], re.I)]
    sn("addl_nonta_transfers_using_admin_like_function_n", len(adminish), "count", WINDOW, "addl_signer_function.csv")
    return rows


# ====================================================================== A2 concentration, counterparties, PM wallet
def primary_market_sets(allev):
    """Wallets that ever receive an issuance mint (TR-6 label != dividend) or originate a burn
    (burn event frm, or recorded burn tx sender), TA wallets excluded. Per fund x chain."""
    lab = {(r["fund"], r["chain"], r["id"]): r["label"] for r in rd("freeze_mint_labels.csv")}
    senders = {r["tx"]: r["tx_from"] for r in rd("freeze_evm_burn_senders.csv") if r["tx_from"]}
    pm = defaultdict(lambda: defaultdict(set))   # (fund, chain) -> addr -> reasons
    for e in allev:
        key = (e["fund"], e["chain"])
        if e["kind"] == "mint":
            l = lab.get((e["fund"], e["chain"], e["id"]))
            if l == "issuance":
                pm[key][e["to"]].add("issuance_mint_recipient")
            elif l is None:
                pm[key][e["to"]].add("mint_recipient_unlabelled")
        elif e["kind"] == "burn":
            pm[key][e["frm"]].add("burn_from")
            s = senders.get(e["tx"])
            if s:
                pm[key][s].add("burn_tx_sender")
    out = defaultdict(dict)
    for key, d in pm.items():
        for a, rs in d.items():
            if ROLES.get((key[0], key[1], a), ("holder",))[0] != "TA":
                out[key][a] = rs
    return out


def a2(allev, pm):
    print("A2 concentration")
    conc = []
    tot_v = defaultdict(float)
    for r in EV:
        tot_v[r["fund"]] += r["amount"]
    for fund in ("WTGXX", "FOBXX", "JLTXX"):
        for bound, sel in (("lower (hh)", lambda r: r["pair"] == "hh"), ("upper (non-TA)", lambda r: True)):
            xs = sorted((r["amount"] for r in NONTA if r["fund"] == fund and sel(r)), reverse=True)
            v = sum(xs)
            row = dict(fund=fund, bound=bound, n=len(xs), value=round(v, 6),
                       share_of_all_transfer_value_pct=pct(v, tot_v[fund]))
            for k in (1, 3, 5):
                row[f"top{k}_share_of_set_pct"] = pct(sum(xs[:k]), v)
                row[f"top{k}_share_of_all_transfer_value_pct"] = pct(sum(xs[:k]), tot_v[fund])
            conc.append(row)
            b = "lower" if bound.startswith("lower") else "upper"
            for k in (1, 3, 5):
                sn(f"addl_conc_top{k}_share_pct__{fund}__{b}", row[f"top{k}_share_of_set_pct"], f"percent of {bound} value", WINDOW, "addl_concentration.csv")
            sn(f"addl_conc_value__{fund}__{b}", row["value"], "shares", WINDOW, "addl_concentration.csv")
    write(conc, "addl_concentration.csv")

    # top counterparties
    cp = []
    for fund in ("WTGXX", "FOBXX"):
        agg = defaultdict(lambda: dict(sent=0.0, recv=0.0, n=0, chain=""))
        for r in NONTA:
            if r["fund"] != fund:
                continue
            agg[r["frm"]]["sent"] += r["amount"]
            agg[r["to"]]["recv"] += r["amount"]
            for a in (r["frm"], r["to"]):
                agg[a]["n"] += 1
                agg[a]["chain"] = r["chain"]
        tv = sum(r["amount"] for r in NONTA if r["fund"] == fund)
        for rank, (a, d) in enumerate(sorted(agg.items(), key=lambda kv: -(kv[1]["sent"] + kv[1]["recv"]))[:8], 1):
            ch = d["chain"]
            role = ROLES.get((fund, ch, a), ("holder", ""))[0]
            lab = label_title(ch, a) if ch in ("ethereum", "arbitrum") else ""
            if ch == "stellar":
                resp = next((c["response"] for c in jl("stellar_calls.jsonl") if c["source"] == "stellar.expert" and c["params"][0] == a), None)
                lab = "" if resp is None or resp.get("_http_status") == 404 else json.dumps(resp)[:120]
                lab_src = "stellar.expert directory (404 = no entry)" if resp is not None else "not looked up"
            else:
                lab_src = "Etherscan page title" if __import__("source_facts").label_exists(ADD / "labels" / f"{ch}_{a}.html") else "not looked up"
            cp.append(dict(fund=fund, rank=rank, chain=ch, address=a, freeze_role=role, contract=cname(ch, a) if ch in P.CHAIN_IDS else "",
                           public_label=lab, label_source=lab_src, n_nonta_transfers=d["n"],
                           sent=round(d["sent"], 6), received=round(d["recv"], 6),
                           share_of_nonta_value_touching_pct=pct(d["sent"] + d["recv"], tv),
                           primary_market_flags=";".join(sorted(pm[(fund, ch)].get(a, set())))))
    write(cp, "addl_top_counterparties.csv")

    # PM wallet profile (critic's wallet) and its recipients
    prof = []
    w = CRITIC_WALLET
    evw = [e for e in allev if e["fund"] == "WTGXX" and e["chain"] == "ethereum" and w in (e["frm"], e["to"])]
    lab = {(r["fund"], r["chain"], r["id"]): r["label"] for r in rd("freeze_mint_labels.csv")}
    senders = {r["tx"]: r["tx_from"] for r in rd("freeze_evm_burn_senders.csv")}
    mints = [e for e in evw if e["kind"] == "mint"]
    iss = [e for e in mints if lab.get(("WTGXX", "ethereum", e["id"])) == "issuance"]
    burns = [e for e in evw if e["kind"] == "burn"]
    outs = [e for e in evw if e["kind"] == "transfer" and e["frm"] == w]
    ins = [e for e in evw if e["kind"] == "transfer" and e["to"] == w]
    lower = [r for r in NONTA if r["fund"] == "WTGXX" and r["pair"] == "hh"]
    upper = [r for r in NONTA if r["fund"] == "WTGXX"]
    lw = [r for r in lower if r["frm"] == w or r["to"] == w]
    uw = [r for r in upper if r["frm"] == w or r["to"] == w]
    allv = tot_v["WTGXX"]
    P_ = lambda item, value, note="": prof.append(dict(wallet=w, item=item, value=value, note=note))
    P_("freeze_role", ROLES.get(("WTGXX", "ethereum", w), ("holder", ""))[0], "freeze_wallet_roles.csv (default holder)")
    P_("public_label", label_title("ethereum", w) or "(none)", "Etherscan page title, fetched 2026-10-17")
    P_("mints_received_n", len(mints), "full history to 2026-10-16")
    P_("mints_received_value", round(sum(e["amount"] for e in mints), 6))
    P_("issuance_mints_n", len(iss), "TR-6 label issuance")
    P_("issuance_mints_value", round(sum(e["amount"] for e in iss), 6))
    P_("largest_issuance_mint", max((e["amount"] for e in iss), default=0), max(iss, key=lambda e: e["amount"])["ts"].isoformat() if iss else "")
    P_("burns_from_wallet_n", len(burns))
    P_("burns_from_wallet_value", round(sum(e["amount"] for e in burns), 6))
    P_("burn_tx_senders", ";".join(sorted({senders.get(e["tx"], "") for e in burns})),
       "tx.from of the burn transactions (freeze_evm_burn_senders.csv); role " +
       ";".join(sorted({ROLES.get(('WTGXX', 'ethereum', senders.get(e['tx'], '')), ('holder', ''))[0] for e in burns})))
    rpc_in = {}
    for fz in sorted(SOLRAW.glob("rpc_calls_*.jsonl.gz")):
        for line in gzip.open(fz):
            d = json.loads(line)
            if d.get("method") == "eth_getTransactionByHash" and isinstance(d.get("result"), dict):
                rpc_in[d["params"][0].lower()] = d["result"].get("input", "")
    fnames = sorted({selector_name("ethereum", TOKADDR[("WTGXX", "ethereum")], rpc_in.get(e["tx"].lower(), "")[:10])[0] for e in burns})
    P_("burn_function_called", ";".join(fnames), "selector from frozen rpc_calls eth_getTransactionByHash input, decoded with the verified WTGXX ABI")
    P_("transfers_out_n_all_time", len(outs))
    P_("transfers_in_n_all_time", len(ins))
    P_("f1_lower_transfers_touching_n", len(lw), "2026 window")
    P_("f1_lower_value_touching", round(sum(r["amount"] for r in lw), 6))
    P_("f1_lower_value_touching_share_of_lower_pct", pct(sum(r["amount"] for r in lw), sum(r["amount"] for r in lower)))
    P_("f1_upper_value_touching_share_of_upper_pct", pct(sum(r["amount"] for r in uw), sum(r["amount"] for r in upper)))
    P_("f1_lower_share_without_wallet_pct_of_all_transfer_value",
       pct(sum(r["amount"] for r in lower) - sum(r["amount"] for r in lw), allv),
       "numerator drops the wallet's transfers, denominator unchanged")
    big = max((r for r in lw), key=lambda r: r["amount"])
    P_("largest_single_transfer_points_of_upper_bound", round(100 * big["amount"] / allv, 4),
       f"{big['amount']} shares on {big['ts_utc']} to {big['to']}")
    for rcpt in sorted({r["to"] for r in lw if r["frm"] == w}):
        er = sorted([e for e in allev if e["fund"] == "WTGXX" and e["chain"] == "ethereum" and rcpt in (e["frm"], e["to"])], key=lambda e: e["ts"])
        for e in er:
            other = e["to"] if e["frm"] == rcpt else e["frm"]
            prof.append(dict(wallet=rcpt, item=f"event {e['ts'].strftime('%Y-%m-%dT%H:%M:%SZ')}",
                             value=f"{e['kind']} {'out' if e['frm'] == rcpt else 'in'} {e['amount']}",
                             note=f"counterparty {other} role {ROLES.get(('WTGXX', 'ethereum', other), ('holder', ''))[0]} "
                                  f"{cname('ethereum', other)} {label_title('ethereum', other)}".strip()))
        prof.append(dict(wallet=rcpt, item="primary_market_flags", value=";".join(sorted(pm[("WTGXX", "ethereum")].get(rcpt, set()))) or "(none)", note=""))
    write(prof, "addl_pm_wallet_profile.csv")
    for r in prof:
        if r["wallet"] == w and r["item"] in ("issuance_mints_value", "largest_issuance_mint", "burns_from_wallet_value",
                                              "f1_lower_value_touching", "f1_lower_value_touching_share_of_lower_pct",
                                              "f1_upper_value_touching_share_of_upper_pct",
                                              "f1_lower_share_without_wallet_pct_of_all_transfer_value",
                                              "largest_single_transfer_points_of_upper_bound", "f1_lower_transfers_touching_n"):
            sn(f"addl_pmwallet_{r['item']}", r["value"], "see name", "2026-10-16", "addl_pm_wallet_profile.csv")


# ====================================================================== A3 M1 excluding primary-market wallets
def a3(pm):
    print("A3 M1 excluding primary-market wallets")
    out = []
    variants = [("frozen_main", lambda r: False),
                ("excl_wallet_0x0c561ed8", lambda r: CRITIC_WALLET in (r["frm"], r["to"])),
                ("excl_all_primary_market_wallets", lambda r: r["frm"] in pm[(r["fund"], r["chain"])] or r["to"] in pm[(r["fund"], r["chain"])])]
    for fund in ("FOBXX", "WTGXX", "JLTXX", "FYOXX"):
        allr = [r for r in EV if r["fund"] == fund]
        for vname, excl in variants:
            for mode in ("drop_from_sample", "drop_from_numerator_only"):
                if vname == "frozen_main" and mode == "drop_from_numerator_only":
                    continue
                den = [r for r in allr if not excl(r)] if mode == "drop_from_sample" else allr
                num = [r for r in allr if r["nonta"] == "1" and not excl(r)]
                lo = [r for r in num if r["pair"] == "hh"]
                n, v = len(den), sum(r["amount"] for r in den)
                row = dict(fund=fund, variant=vname, mode=mode, n_events=n, value=round(v, 6),
                           excluded_events=len(allr) - len([r for r in allr if not excl(r)]),
                           pm_wallets_in_set=len(pm[(fund, "ethereum")]) + len(pm[(fund, "stellar")]) + len(pm[(fund, "solana")]) + len(pm[(fund, "arbitrum")]) + len(pm[(fund, "polygon")]),
                           lower_share_value_pct=pct(sum(r["amount"] for r in lo), v), lower_share_count_pct=pct(len(lo), n),
                           upper_share_value_pct=pct(sum(r["amount"] for r in num), v), upper_share_count_pct=pct(len(num), n))
                out.append(row)
                if vname != "frozen_main" and fund in ("WTGXX", "FOBXX"):
                    for k in ("lower_share_value_pct", "lower_share_count_pct", "upper_share_value_pct", "upper_share_count_pct"):
                        sn(f"addl_m1x_{k}__{fund}__{vname}__{mode}", row[k], "percent", WINDOW, "addl_m1_excl_pm.csv")
    # check the frozen_main rows reproduce freeze_m1_secondary
    fr = {r["fund"]: r for r in rd("freeze_m1_secondary.csv") if r["chain_scope"] == "pooled" and r["basis"] == "transfers_only"}
    for r in out:
        if r["variant"] == "frozen_main":
            for k in ("lower_share_value_pct", "upper_share_value_pct", "lower_share_count_pct", "upper_share_count_pct"):
                assert fr[r["fund"]][k] == "" or abs(float(fr[r["fund"]][k]) - float(r[k] or 0)) < 1e-9, (r["fund"], k)
    write(out, "addl_m1_excl_pm.csv")
    for fund in ("WTGXX", "FOBXX"):
        sn(f"addl_pm_wallets_n__{fund}", sum(len(v) for (f_, c_), v in pm.items() if f_ == fund), "count", "2026-10-16", "addl_m1_excl_pm.csv")


# ====================================================================== A4 issuer controls
ADMIN_PAT = [("roles/ownership/upgrade", r"role|owner|upgrade|initiali|implementation|transferControl|context"),
             ("allowlist/compliance", r"whitelist|allowlist|kyc|compliance|restrict|blacklist|blocklist|publicAccess"),
             ("pause/freeze/stop", r"pause|freeze|stop|halt|^disable|^enable"),
             ("forced transfer/clawback", r"force|recover|seize|clawback|wipe"),
             ("mint/burn", r"mint|burn")]


def a4():
    print("A4 issuer controls")
    st_rows = []
    st = {(c["source"], c["params"][0]): c["response"] for c in jl("stellar_calls.jsonl")}
    acct = {}
    for code, fund in (("BENJI", "FOBXX"), ("WTGX", "WTGXX")):
        a = json.loads((STAG / f"asset_{code}.json").read_bytes())["_embedded"]["records"][0]
        iss = a["asset_issuer"]
        ia = st.get(("horizon", f"/accounts/{iss}"), {})
        tl = []
        for pg in SE.read_pages(STAG / f"trustlines_{code}.jsonl.gz"):
            for r in pg["_embedded"]["records"]:
                acct[r["account_id"]] = r
                for b in r["balances"]:
                    if b.get("asset_code") == code and b.get("asset_issuer") == iss:
                        tl.append((r, b))
        iss_keys = {s["key"] for s in ia.get("signers", []) if s["weight"] > 0}
        unauth = [x for x in tl if not x[1].get("is_authorized") and not x[1].get("is_authorized_to_maintain_liabilities")]
        auth = [x for x in tl if x[1].get("is_authorized")]
        shared = [x for x in auth if iss_keys & {s["key"] for s in x[0]["signers"] if s["weight"] > 0}]
        master0 = [x for x in auth if next((s["weight"] for s in x[0]["signers"] if s["key"] == x[0]["account_id"]), 0) == 0]
        bal_auth = sum(float(b["balance"]) for _, b in auth)
        bal_shared = sum(float(b["balance"]) for r, b in shared)
        # issuer control ops in the frozen crawl (issuer as op source)
        opc = Counter()
        for f in sorted((STAG / "ops").glob("*.jsonl.gz")):
            for pg in SE.read_pages(f):
                for r in pg.get("_embedded", {}).get("records", []):
                    if r.get("source_account") == iss and r["type"] in ("allow_trust", "set_trust_line_flags", "clawback", "clawback_claimable_balance", "set_options"):
                        touches = r.get("asset_code") == code or r["type"] in ("set_options",)
                        if touches and SE.ts(r["created_at"]) >= SE.crawl_start(STAG):
                            opc[(r["id"], r["type"])] = 1
        opn = Counter(t for (_, t) in opc)
        vals = dict(fund=fund, asset=code, issuer=iss, home_domain=ia.get("home_domain", ""),
                    auth_required=a["flags"]["auth_required"], auth_revocable=a["flags"]["auth_revocable"],
                    auth_clawback_enabled=a["flags"]["auth_clawback_enabled"], auth_immutable=a["flags"]["auth_immutable"],
                    issuer_account_flags=json.dumps(ia.get("flags", {})), issuer_thresholds=json.dumps(ia.get("thresholds", {})),
                    issuer_signers_weight_gt0=len(iss_keys),
                    issuer_master_weight=next((s["weight"] for s in ia.get("signers", []) if s["key"] == iss), ""),
                    asset_accounts_authorized=a["accounts"]["authorized"],
                    asset_accounts_auth_to_maintain_liabilities=a["accounts"]["authorized_to_maintain_liabilities"],
                    asset_accounts_unauthorized=a["accounts"]["unauthorized"],
                    trustlines_in_file=len(tl), trustlines_unauthorized_in_file=len(unauth),
                    unauthorized_with_positive_balance=sum(1 for _, b in unauth if float(b["balance"]) > 0),
                    authorized_accounts_sharing_a_signer_key_with_issuer=len(shared),
                    authorized_accounts_master_weight_0=len(master0),
                    balance_share_in_accounts_sharing_issuer_key_pct=pct(bal_shared, bal_auth),
                    issuer_ops_allow_trust_since_crawl_start=opn.get("allow_trust", 0),
                    issuer_ops_set_trust_line_flags_since_crawl_start=opn.get("set_trust_line_flags", 0),
                    issuer_ops_clawback_since_crawl_start=opn.get("clawback", 0) + opn.get("clawback_claimable_balance", 0),
                    issuer_ops_set_options_since_crawl_start=opn.get("set_options", 0),
                    source="asset JSON and trustlines: frozen 2026-10-16; issuer account: Horizon 2026-10-17 (addition)")
        st_rows.append(vals)
        for k in ("auth_required", "auth_revocable", "auth_clawback_enabled", "asset_accounts_unauthorized",
                  "asset_accounts_authorized", "authorized_accounts_sharing_a_signer_key_with_issuer",
                  "authorized_accounts_master_weight_0", "balance_share_in_accounts_sharing_issuer_key_pct",
                  "issuer_signers_weight_gt0", "issuer_ops_clawback_since_crawl_start",
                  "issuer_ops_set_trust_line_flags_since_crawl_start", "issuer_ops_allow_trust_since_crawl_start"):
            sn(f"addl_stellar_{k}__{fund}", vals[k], "see name", "2026-10-16", "addl_stellar_issuer_controls.csv")
    write(st_rows, "addl_stellar_issuer_controls.csv")

    # EVM token admin functions from verified ABIs (names only)
    ev_rows = []
    for t in TOKS:
        ch = t["chain"]
        if ch not in P.CHAIN_IDS:
            continue
        a = t["address"].lower()
        s = SRC.get((ch, a), {})
        impl = (s.get("Implementation") or "").lower()
        fns = abi_of(ch, a)
        verified = bool(fns)
        if not verified:
            ev_rows.append(dict(fund=t["fund"], chain=ch, token=a, contract=cname(ch, a), implementation=impl,
                                function="", state_mutability="", category="", note="no verified ABI on Etherscan V2"))
            continue
        seen = set()
        for it in fns:
            if it.get("stateMutability") in ("view", "pure") or it["name"] in seen:
                continue
            seen.add(it["name"])
            cats = [c for c, pat in ADMIN_PAT if re.search(pat, it["name"], re.I)][:1]     # first match only
            ins_ = it.get("inputs", [])
            if not cats and it["name"] != "transferFrom" and any(i["type"] == "address" and re.search(r"from|holder|account|owner", i.get("name", ""), re.I) for i in ins_)                     and re.search(r"transfer", it["name"], re.I):
                cats = ["transfer naming a source address (not ERC-20 transferFrom)"]
            if it["name"] in ("transfer", "transferFrom", "approve", "increaseAllowance", "decreaseAllowance", "permit"):
                cats = ["holder ERC-20"]
            ev_rows.append(dict(fund=t["fund"], chain=ch, token=a, contract=cname(ch, a), implementation=impl,
                                function=it["name"], signature=f"{it['name']}({', '.join((i['type'] + ' ' + i.get('name', '')).strip() for i in it.get('inputs', []))})",
                                state_mutability=it.get("stateMutability", ""),
                                category=";".join(cats) or "other",
                                note="verified ABI (proxy + implementation)" + ("; diamond proxy: only the facet Etherscan lists as implementation was read" if "Diamond" in cname(ch, a) else "")))
    write(ev_rows, "addl_evm_admin_functions.csv")
    for (fund, ch), grp in sorted(defaultdict(list, {(r["fund"], r["chain"]): [x for x in ev_rows if x["fund"] == r["fund"] and x["chain"] == r["chain"]] for r in ev_rows}).items()):
        sn(f"addl_evm_nonview_functions_n__{fund}__{ch}", sum(1 for x in grp if x["function"]), "count", "2026-10-17", "addl_evm_admin_functions.csv")
        sn(f"addl_evm_forced_transfer_like_functions__{fund}__{ch}",
           (";".join(sorted(x["function"] for x in grp if ("forced transfer" in x["category"] or "naming a source" in x["category"]))) or "(none)")
           if any(x["function"] for x in grp) else "(no verified ABI)", "text", "2026-10-17", "addl_evm_admin_functions.csv")

    # Solana mint controls
    so = []
    for c in jl("solana_calls.jsonl"):
        v = c["response"]["result"]["value"]
        info = v["data"]["parsed"]["info"]
        tok = next(t for t in TOKS if t["address"] == c["params"][0])
        exts = {e["extension"]: e.get("state", {}) for e in info.get("extensions", [])}
        so.append(dict(fund=tok["fund"], mint=c["params"][0], program=v["owner"], mint_authority=info.get("mintAuthority"),
                       freeze_authority=info.get("freezeAuthority"), extensions=";".join(exts),
                       permanent_delegate=exts.get("permanentDelegate", {}).get("delegate", ""),
                       transfer_hook_program=exts.get("transferHook", {}).get("programId", ""),
                       pausable_authority=exts.get("pausableConfig", {}).get("authority", ""),
                       paused=exts.get("pausableConfig", {}).get("paused", ""),
                       accessed_utc=c["accessed_utc"]))
    write(so, "addl_solana_mint_controls.csv")
    for r in so:
        sn(f"addl_solana_extensions__{r['fund']}", r["extensions"], "text", "2026-10-17", "addl_solana_mint_controls.csv")


# ====================================================================== A5 FYOXX quarter-end gaps
def a5():
    print("A5 FYOXX quarter-end gaps")
    cal = R.BusinessCalendar.from_csv(outside="weekends")
    fy = sorted([dict(id=r["id"], ts=SE.ts(r["ts_utc"]), frm=r["frm"], to=r["to"], amount=float(r["amount"]), kind=r["kind"], tx=r["tx"])
                 for r in rd("freeze_evm_events.csv") if r["fund"] == "FYOXX"], key=lambda e: (e["ts"], e["id"]))
    bw = [a for a in {e["frm"] for e in fy if e["kind"] == "burn"} if ROLES.get(("FYOXX", "ethereum", a), ("holder",))[0] == "TA" and a != R.ZERO]
    parked = R.tr2_parked_lots(fy, bw, cal)
    cls = {r["ticker"]: r["class_id"] for r in rd("freeze_nmfp_filings.csv")}["FYOXX"]
    reg = {r["report_date"]: r for r in rd("freeze_nmfp_summary.csv") if r["ticker_file"] == "FYOXX" and r["governing"] == "1" and r["class_id"] == cls}
    daily = {(r["date"]): r for r in rd("freeze_nmfp_daily.csv") if r["ticker_file"] == "FYOXX" and r["governing"] == "1" and r["class_id"] == cls}
    snaps = {(r["report_date"], r["clock"]): float(r["supply_call"] or r["supply_events"]) for r in rd("freeze_supply_snapshots_evm.csv") if r["fund"] == "FYOXX"}
    oos = {(r["report_date"]): r for r in rd("freeze_m0_oos_codepath.csv") if r["fund"] == "FYOXX" and r["clock"] == "ET"}
    rows, lots = [], []
    for d in ("2025-12-31", "2026-03-31", "2026-06-30", "2026-09-30"):
        cut = R.cutoff(dt.date.fromisoformat(d))
        sh = float(reg[d]["class_shares_outstanding"])
        raw = snaps[(d, "ET")]
        tr2 = R.tr2_adjustment(parked, cut)
        q = [p for p in parked if p["arrival_ts"] <= cut < p["burn_ts"]]
        dd = daily.get(d, {})
        nb = cal.next_business(dt.date.fromisoformat(d), 1).isoformat()
        frozen = oos.get(d)
        if frozen is None:
            frozen = next((r for r in rd("freeze_m0_reconciliation.csv") if r["fund"] == "FYOXX" and r["report_date"] == d and r["clock"] == "ET"), {})
        row = dict(report_date=d, nmfp_class_shares=sh, onchain_supply_raw=round(raw, 6),
                   gap_raw_pct_recomputed=pct(raw - sh, sh), gap_raw_pct_frozen=frozen.get("gap_raw_pct", ""),
                   tr2_parked_shares=round(tr2, 6), tr2_parked_pct_of_class=pct(tr2, sh),
                   gap_after_tr2_pct_recomputed=pct(raw - tr2 - sh, sh), gap_pct_frozen=frozen.get("gap_pct", ""),
                   parked_lots_n=len(q), parked_wallets=";".join(sorted({p["wallet"] for p in q})),
                   nmfp_gross_reds_on_report_date=dd.get("gross_reds", ""),
                   nmfp_gross_subs_on_report_date=dd.get("gross_subs", ""),
                   burn_dates_et=";".join(sorted({R.et_date(p["burn_ts"]).isoformat() for p in q})), next_business_day=nb)
        rows.append(row)
        for p in q:
            lots.append(dict(report_date=d, wallet=p["wallet"], arrival_ts=p["arrival_ts"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                             burn_ts=p["burn_ts"].strftime("%Y-%m-%dT%H:%M:%SZ"), amount=p["amount"]))
        for k in ("gap_raw_pct_recomputed", "tr2_parked_pct_of_class", "gap_after_tr2_pct_recomputed", "tr2_parked_shares"):
            sn(f"addl_fyoxx_{k}__{d}", row[k], "see name", d, "addl_fyoxx_quarter_gaps.csv")
        if row["gap_raw_pct_frozen"] != "":
            assert abs(float(row["gap_raw_pct_frozen"]) - row["gap_raw_pct_recomputed"]) < 1e-3, row
    write(rows, "addl_fyoxx_quarter_gaps.csv")
    write(lots, "addl_fyoxx_parked_lots.csv")


# ====================================================================== A6 fragmentation
def a6():
    print("A6 fragmentation")
    d = "2026-09-30"
    cut = R.cutoff(dt.date.fromisoformat(d))
    sup = defaultdict(dict)
    for r in rd("freeze_supply_snapshots_evm.csv"):
        if r["report_date"] == d and r["clock"] == "ET":
            sup[r["fund"]][r["chain"]] = (float(r["supply_call"] or r["supply_events"] or 0), "totalSupply@block" if r["supply_call"] else "events")
    sol = defaultdict(list)
    for r in rd("freeze_solana_events.csv"):
        sol[r["fund"]].append(dict(ts=SE.ts(r["ts_utc"]), frm=r["frm"], to=r["to"], amount=float(r["amount"]), kind=r["kind"]))
    for f, evs in sol.items():
        sup[f]["solana"] = (R.supply_at(evs, cut), "events_full_history")
    for t in TOKS:
        if t["chain"] == "stellar":
            evs, _ = SE.load(STAG, t["token"], t["address"])
            evs = [e for e in evs if e["ts"] >= SE.crawl_start(STAG)]
            s_now, t_now, _ = SE.asset_snapshot(STAG, t["token"])
            sup[t["fund"]]["stellar"] = (SE.supply_backout(evs, s_now, t_now, cut), "horizon_backout")
    m0 = {r["fund"]: r for r in rd("freeze_m0_reconciliation.csv") if r["report_date"] == d and r["clock"] == "ET"}
    rows = []
    for fund in ("FOBXX", "WTGXX", "JLTXX", "FYOXX"):
        chains = sorted({t["chain"] for t in TOKS if t["fund"] == fund})
        tot = sum(v for v, _ in sup[fund].values())
        frozen_tot = float(m0[fund]["onchain_supply_raw"])
        assert abs(tot - frozen_tot) < 1e-3, (fund, tot, frozen_tot)
        sh = float(m0[fund]["nmfp_class_shares"])
        for ch in chains:
            v, src = sup[fund].get(ch, (None, "not measured (no data in vintage)"))
            rows.append(dict(fund=fund, chain=ch, supply=round(v, 6) if v is not None else "", source=src,
                             share_of_measured_onchain_pct=pct(v, tot) if v is not None else "",
                             share_of_nmfp_class_shares_pct=pct(v, sh) if v is not None else ""))
            if v is not None:
                sn(f"addl_frag_supply_share_pct__{fund}__{ch}", pct(v, tot), "percent of measured on-chain supply", d, "addl_fragmentation_supply.csv")
        rows.append(dict(fund=fund, chain="(total measured)", supply=round(tot, 6), source="sum; equals freeze_m0_reconciliation onchain_supply_raw",
                         share_of_measured_onchain_pct=100.0, share_of_nmfp_class_shares_pct=pct(tot, sh)))
        rows.append(dict(fund=fund, chain="(N-MFP3 class shares)", supply=sh, source="freeze_m0_reconciliation nmfp_class_shares",
                         share_of_measured_onchain_pct="", share_of_nmfp_class_shares_pct=100.0))
        sn(f"addl_frag_chains_listed_n__{fund}", len(chains), "count", d, "addl_fragmentation_supply.csv")
        sn(f"addl_frag_chains_supply_gt0_n__{fund}", sum(1 for v, _ in sup[fund].values() if v > 1e-9), "count", d, "addl_fragmentation_supply.csv")
    write(rows, "addl_fragmentation_supply.csv")
    tr = []
    for fund in ("FOBXX", "WTGXX", "JLTXX", "FYOXX"):
        allr = [r for r in EV if r["fund"] == fund]
        nt = [r for r in allr if r["nonta"] == "1"]
        for ch in sorted({t["chain"] for t in TOKS if t["fund"] == fund}):
            a = [r for r in allr if r["chain"] == ch]
            b = [r for r in nt if r["chain"] == ch]
            tr.append(dict(fund=fund, chain=ch, transfers_n=len(a), transfers_value=round(sum(r["amount"] for r in a), 6),
                           nonta_transfers_n=len(b), nonta_value=round(sum(r["amount"] for r in b), 6),
                           share_of_fund_nonta_events_pct=pct(len(b), len(nt)) if nt else "",
                           share_of_fund_nonta_value_pct=pct(sum(r["amount"] for r in b), sum(r["amount"] for r in nt)) if nt else ""))
            if nt:
                sn(f"addl_frag_nonta_event_share_pct__{fund}__{ch}", pct(len(b), len(nt)), "percent of fund non-TA transfer events", WINDOW, "addl_fragmentation_transfers.csv")
    write(tr, "addl_fragmentation_transfers.csv")


# ====================================================================== A7 N-MFP3 record vs beneficial
def a7():
    print("A7 N-MFP3 rec vs ben")
    cls = {r["ticker"]: r["class_id"] for r in rd("freeze_nmfp_filings.csv")}
    rows = []
    for r in rd("freeze_nmfp_summary.csv"):
        if r["governing"] != "1":
            continue
        token_class = r["class_id"] == cls[r["ticker_file"]]
        for part in [p for p in r["beneficial_record_owner_categories"].split(";") if p]:
            bits = part.split("|")
            cat = bits[0]
            kv = dict(b.split("=", 1) for b in bits[1:] if "=" in b)
            rows.append(dict(fund=r["ticker_file"], class_id=r["class_id"], class_name=r["class_name"], token_class=int(token_class),
                             report_date=r["report_date"], category=cat, rec=kv.get("rec", ""), ben=kv.get("ben", ""),
                             ben_blank_or_zero=int(kv.get("ben", "") in ("", None) or float(kv.get("ben") or 0) == 0)))
    rows.sort(key=lambda x: (x["fund"], x["class_id"], x["report_date"], x["category"]))
    write(rows, "addl_nmfp_rec_ben.csv")
    summ = []
    for (fund, cid), g in sorted(defaultdict(list, {}).items()):
        pass
    grp = defaultdict(list)
    for x in rows:
        grp[(x["fund"], x["class_id"], x["class_name"], x["token_class"])].append(x)
    for (fund, cid, cname_, tc), g in sorted(grp.items()):
        months = sorted({x["report_date"] for x in g})
        pos = [x for x in g if not x["ben_blank_or_zero"]]
        summ.append(dict(fund=fund, class_id=cid, class_name=cname_, token_class=tc, months=len(months),
                         first=months[0], last=months[-1], category_rows=len(g),
                         rows_ben_positive=len(pos), months_with_any_ben_positive=len({x["report_date"] for x in pos}),
                         max_ben=max((float(x["ben"]) for x in g if x["ben"] not in ("",)), default=""),
                         reading="ben reported (positive in some rows)" if pos else "ben always blank or zero"))
        if tc:
            sn(f"addl_nmfp_months_with_ben_positive__{fund}", len({x['report_date'] for x in pos}), "count of report months", f"{months[0]}:{months[-1]}", "addl_nmfp_rec_ben_summary.csv")
            sn(f"addl_nmfp_months__{fund}", len(months), "count of report months", f"{months[0]}:{months[-1]}", "addl_nmfp_rec_ben_summary.csv")
    write(summ, "addl_nmfp_rec_ben_summary.csv")


def main():
    allev = F13.load_events()
    print(f"events loaded: {len(allev)}")
    pm = primary_market_sets(allev)
    a1()
    a2(allev, pm)
    a3(pm)
    a4()
    a5()
    a6()
    a7()
    names = Counter(r["name"] for r in SUMMARY)
    dup = [n for n, c in names.items() if c > 1]
    assert not dup, dup[:5]
    write(SUMMARY, "addl_summary_numbers.csv")


if __name__ == "__main__":
    main()
