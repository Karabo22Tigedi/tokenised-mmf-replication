"""rules.py -- pre-registered timing and role rules TR-1 to TR-6 (notes/preregistration.md §4.4)

Pure functions on small, chain-agnostic event records, so they can be unit-tested on synthetic
data (scripts/test_rules.py) and applied identically to EVM and Stellar events.

Where the written rule needed an operational choice, the choice is a named parameter whose
default is the final implementation in notes/preregistration_implementation_notes.md, section
"Final implementation after audit (approved by user 2026-10-16, CP11)" (IMPL-n tags below).
Audit fixes (reviews/impl_audit.md): hop leg triggered by issuance mints only (C1); TR-6 8c cap
k=5 (day-aware cap as sensitivity); 8b outlier guard off by default (robustness flag); IMPL-14 B
lag from the N-MFP3 report date (m4_lag_from_report_date); TA batch (ta_batch_days); one-sided
F0 month status (f0_month_status) and F0 count on ET rows only (f0_count); calendar guard for
dates before TR-4 coverage.

Event record (dict):
  id      unique, sortable within a chain (block*10^6+logIndex for EVM; Horizon paging_token)
  ts      timezone-aware datetime (UTC)
  frm,to  addresses (strings). Mint: frm == MINT_SRC of the chain; burn: to == BURN_DST.
  amount  float, token units (= fund shares, 1 token = 1 share)
  kind    'mint' | 'burn' | 'transfer'
  tx      transaction hash (optional; used for tx-sender variants)
"""
from __future__ import annotations

import csv
import datetime as dt
from bisect import bisect_right
from collections import defaultdict, deque
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = dt.timezone.utc
ZERO = "0x0000000000000000000000000000000000000000"
ROOT = Path(__file__).resolve().parent.parent


# ======================================================================= TR-1 clock
def et_date(ts: dt.datetime) -> dt.date:
    """TR-1: the America/New_York calendar date of an on-chain timestamp."""
    if ts.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware (UTC)")
    return ts.astimezone(ET).date()


def et_hour(ts: dt.datetime) -> int:
    return ts.astimezone(ET).hour


def end_of_day(d: dt.date, clock: str = "ET") -> dt.datetime:
    """23:59:59 on date d in the given clock ('ET' main, 'UTC' sensitivity), returned in UTC.
    Events with ts <= this instant are 'on or before d'. Block/ledger timestamps are whole
    seconds, so 23:59:59 is equivalent to 'before midnight'."""
    tz = ET if clock == "ET" else UTC
    return dt.datetime.combine(d, dt.time(23, 59, 59), tz).astimezone(UTC)


def cutoff(report_date: dt.date, clock: str = "ET") -> dt.datetime:
    """TR-1 month-end cutoff: 23:59:59 ET on the N-MFP3 report date (UTC sensitivity row)."""
    return end_of_day(report_date, clock)


# ======================================================================= TR-4 calendar
class BusinessCalendar:
    """TR-4: Saturdays, Sundays and Federal Reserve Bank (Fedwire/FRBNY) closures are
    non-business days. Closures come from data/processed/ref_fedwire_holidays.csv
    (scripts/00_fedwire_calendar.py), covering 2024-2026; dates outside are refused."""

    def __init__(self, closures, first=dt.date(2024, 1, 1), last=dt.date(2026, 12, 31), outside="raise"):
        """outside: 'raise' (default) refuses dates outside coverage. 'weekends' (audit TR-4 guard,
        used by 05_measures.py) treats such dates by the weekend rule only and records every such
        date in self.outside_dates, which callers print. The locked TR-4 calendar covers
        2024-2026; FOBXX events start in Oct 2023, and only TR-5 roles and TR-6 labels (no measure
        window) can look up 2023 dates."""
        self.closures = set(closures)
        self.first, self.last = first, last
        if outside not in ("raise", "weekends"):
            raise ValueError(outside)
        self.outside = outside
        self.outside_dates = set()

    @classmethod
    def from_csv(cls, path=None, outside="raise"):
        path = path or ROOT / "data" / "processed" / "ref_fedwire_holidays.csv"
        with open(path, newline="") as f:
            cl = [dt.date.fromisoformat(r["reserve_banks_closed_date"]) for r in csv.DictReader(f)
                  if r["reserve_banks_closed_date"]]
        return cls(cl, outside=outside)

    def _chk(self, d):
        if not (self.first <= d <= self.last):
            if self.outside == "weekends":
                self.outside_dates.add(d)
                return False
            raise ValueError(f"{d} outside calendar coverage {self.first}..{self.last}")
        return True

    def is_business(self, d: dt.date) -> bool:
        if not self._chk(d):
            return d.weekday() < 5
        return d.weekday() < 5 and d not in self.closures

    def next_business(self, d: dt.date, n: int = 1) -> dt.date:
        """n-th business day strictly after d."""
        k = 0
        while k < n:
            d += dt.timedelta(days=1)
            if self.is_business(d):
                k += 1
        return d

    def business_days_after(self, a: dt.date, b: dt.date) -> int:
        """Number of business days in (a, b]; 0 if b <= a."""
        n, d = 0, a
        while d < b:
            d += dt.timedelta(days=1)
            if self.is_business(d):
                n += 1
        return n


# ======================================================================= helpers
def sort_events(events):
    return sorted(events, key=lambda e: (e["ts"], e["id"]))


def balances_at(events, t: dt.datetime, mint_src=ZERO, burn_dst=ZERO):
    """Address balances after all events with ts <= t."""
    bal = defaultdict(float)
    for e in events:
        if e["ts"] > t:
            continue
        if e["frm"] != mint_src:
            bal[e["frm"]] -= e["amount"]
        if e["to"] != burn_dst:
            bal[e["to"]] += e["amount"]
    return bal


def supply_at(events, t: dt.datetime):
    """Event-derived supply (mints - burns) at t."""
    return sum(e["amount"] if e["kind"] == "mint" else -e["amount"] if e["kind"] == "burn" else 0.0
               for e in events if e["ts"] <= t)


def fifo_lots(events, wallet, mint_src=ZERO, burn_dst=ZERO):
    """FIFO-match a wallet's inflows to its outflows. Returns a list of lots:
    dict(arrival_ts, amount, legs=[(out_ts, out_amount, out_kind)]) where out_kind is 'burn' or
    'transfer'. Inflow lots are consumed in arrival order. Used by TR-2 and TR-5 parking."""
    lots, q = [], deque()
    for e in sort_events(events):
        if e["to"] == wallet and e["frm"] != wallet:
            lot = dict(arrival_ts=e["ts"], amount=e["amount"], remaining=e["amount"], legs=[],
                       from_mint=(e["kind"] == "mint"))
            lots.append(lot)
            q.append(lot)
        elif e["frm"] == wallet and e["to"] != wallet:
            need = e["amount"]
            while need > 1e-12 and q:
                lot = q[0]
                take = min(lot["remaining"], need)
                lot["remaining"] -= take
                need -= take
                lot["legs"].append((e["ts"], take, e["kind"]))
                if lot["remaining"] <= 1e-12:
                    q.popleft()
            # need > 0 here would mean outflow without recorded inflow (history starts after
            # the wallet was funded); it is left unmatched and reported by callers.
    return lots


# ======================================================================= TR-2 parked redemptions
def tr2_parked_lots(events, burn_wallets, cal: BusinessCalendar, window_bd: int = 1):
    """TR-2: a balance in a TA burn wallet that is burned by the end of the next ET business day
    counts as redeemed on the day it arrived.
    IMPL-3: lots are matched FIFO per wallet; 'next ET business day' is counted from the ET date
    of ARRIVAL; only the part of a lot that is burned (not forwarded) within the window qualifies.
    Returns qualifying pieces: dict(wallet, arrival_ts, burn_ts, amount)."""
    out = []
    for w in burn_wallets:
        for lot in fifo_lots(events, w):
            if lot["from_mint"]:
                continue  # a mint parked in the wallet is not a redemption
            deadline = end_of_day(cal.next_business(et_date(lot["arrival_ts"]), window_bd), "ET")
            for ts, amt, kind in lot["legs"]:
                if kind == "burn" and ts <= deadline:
                    out.append(dict(wallet=w, arrival_ts=lot["arrival_ts"], burn_ts=ts, amount=amt))
    return out


def tr2_adjustment(parked, t_cut: dt.datetime) -> float:
    """Amount to subtract from on-chain supply at t_cut: qualifying parked balances that arrived
    on or before the cutoff and were burned after it (they are 'redeemed on arrival')."""
    return sum(p["amount"] for p in parked if p["arrival_ts"] <= t_cut < p["burn_ts"])


# ======================================================================= TR-3 dividend accrual
def tr3_adjustment(dividend_mints, report_date: dt.date, cal: BusinessCalendar, t_cut: dt.datetime,
                   next_day: str = "business", rules_in=None) -> float:
    """TR-3: dividend shares recorded in N-MFP3 on business day d and minted on chain by the end
    of d+1 (ET) are counted as on-chain at d. Returns the amount to ADD to supply at t_cut.
    IMPL-4: d = the N-MFP3 report date; d+1 = next ET business day (next_day='business',
    proposed) or next calendar day ('calendar'). N-MFP3 does not date dividend shares separately,
    so 'recorded on d' cannot be checked per share; any TR-6 dividend mint in (cutoff, end of d+1]
    is added.
    IMPL-4b rules_in: restrict to dividend mints labelled by these TR-6 rules (e.g. {'iii'}: only
    dividends with an N-MFP3 record, the only ones whose 'recording on d' is evidenced). None = all.
    Each mint dict may carry 'rule'."""
    if rules_in is not None:
        dividend_mints = [m for m in dividend_mints if m.get("rule") in rules_in]
    d1 = cal.next_business(report_date) if next_day == "business" else report_date + dt.timedelta(days=1)
    lim = end_of_day(d1, "ET")
    return sum(m["amount"] for m in dividend_mints if t_cut < m["ts"] <= lim)


# ======================================================================= TR-5 wallet roles
def tr5_roles(events, cal: BusinessCalendar, *, mint_src=ZERO, burn_dst=ZERO, module_contracts=(),
              has_code=lambda a: False, burn_origin="transfer_from", tx_sender=None,
              parking_bd: int = 2, parking_share: float = 0.5, hop_share: float = 0.95,
              burn_origin_leg: bool = True, hop_exclude_redemptions: bool = True,
              mint_labels=None, hop_trigger: str = "issuance"):
    """TR-5, applied in order: TA set -> Contract -> Holder.
    TA set legs (reason codes):
      'zero'     the zero address (Stellar: the issuer account, IMPL-1)
      'module'   issuer-listed module contracts
      'burner'   any address that ever originates a burn. IMPL-5: burn_origin='transfer_from'
                 (the address whose balance the burn reduces; proposed) or 'tx_sender' (needs
                 tx_sender: dict tx -> sender). burn_origin_leg=False disables the leg (proposed
                 for Stellar, where holders redeem by paying the issuer).
      'parking'  any address whose inflows are burned within `parking_bd` ET business days.
                 IMPL-6: FIFO; qualifies if the share of its non-mint inflow value burned within
                 the window exceeds `parking_share` (proposed 0.5).
      'hop'      any address receiving a mint and forwarding >= 95% of it within the same ET day
                 (to anyone other than the burn sink). IMPL-7: one occurrence suffices.
                 Audit fix C1 (hop_trigger='issuance', final): only mints labelled 'issuance' by
                 TR-6 can trigger the leg, so TR-6 runs first and its labels are passed as
                 mint_labels (dict id -> (label, rule, detail)); a dividend of a few shares inside
                 a large balance cannot be "forwarded". hop_trigger='all' = the pre-audit reading
                 (every mint triggers; sensitivity); 'off' = leg disabled (sensitivity).
                 IMPL-7b (hop_exclude_redemptions, proposed True): forwards INTO burner/parking
                 wallets are redemptions, not distribution, and do not count. Calibration
                 evidence: all four JLTXX investor wallets flagged by the literal rule sent their
                 only outflows to the JLTXX burn wallet.
    Contract: has_code(addr) and not TA. Holder: everything else.
    Returns dict addr -> dict(role, reasons)."""
    ev = sort_events(events)
    addrs = {e["frm"] for e in ev} | {e["to"] for e in ev}
    reasons = defaultdict(set)
    for a in (mint_src, burn_dst):
        reasons[a].add("zero")
    for a in module_contracts:
        reasons[a].add("module")
    # burner
    if burn_origin_leg:
        for e in ev:
            if e["kind"] == "burn":
                who = e["frm"] if burn_origin == "transfer_from" else (tx_sender or {}).get(e.get("tx"))
                if who:
                    reasons[who].add("burner")
    # parking (events indexed by address so each burner is scanned once)
    by_addr = defaultdict(list)
    for e in ev:
        by_addr[e["frm"]].append(e)
        if e["to"] != e["frm"]:
            by_addr[e["to"]].append(e)
    burners = {e["frm"] for e in ev if e["kind"] == "burn"} - {mint_src, burn_dst}
    for a in burners:
        lots = [l for l in fifo_lots(by_addr[a], a) if not l["from_mint"]]
        tot = sum(l["amount"] for l in lots)
        if tot <= 0:
            continue
        fast = 0.0
        for l in lots:
            dl = end_of_day(cal.next_business(et_date(l["arrival_ts"]), parking_bd), "ET")
            fast += sum(amt for ts, amt, k in l["legs"] if k == "burn" and ts <= dl)
        if fast / tot > parking_share:
            reasons[a].add("parking")
    # distribution hop
    if hop_trigger not in ("issuance", "all", "off"):
        raise ValueError(hop_trigger)
    if hop_trigger == "issuance" and mint_labels is None:
        raise ValueError("hop_trigger='issuance' needs mint_labels from tr6_classify (audit fix C1)")
    by_addr_day = defaultdict(list)
    for e in ev:
        by_addr_day[(e["frm"], et_date(e["ts"]))].append(e)
    for m in ev:
        if m["kind"] != "mint" or hop_trigger == "off":
            continue
        if hop_trigger == "issuance" and mint_labels.get(m["id"], ("issuance",))[0] != "issuance":
            continue
        r = m["to"]
        sinks = {a for a, rs in reasons.items() if rs & {"burner", "parking"}} if hop_exclude_redemptions else set()
        fwd = sum(e["amount"] for e in by_addr_day[(r, et_date(m["ts"]))]
                  if e["kind"] == "transfer" and (e["ts"], e["id"]) > (m["ts"], m["id"]) and e["to"] not in sinks)
        if fwd >= hop_share * m["amount"]:
            reasons[r].add("hop")
    out = {}
    for a in addrs | set(reasons):
        if reasons.get(a):
            out[a] = dict(role="TA", reasons=sorted(reasons[a]))
        elif has_code(a):
            out[a] = dict(role="contract", reasons=["bytecode"])
        else:
            out[a] = dict(role="holder", reasons=[])
    return out


# ======================================================================= TR-6 dividend mints
def pearson(x, y):
    n = len(x)
    if n < 2:
        return float("nan")
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    if sxx == 0 or syy == 0:
        return float("nan")
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (sxx * syy) ** 0.5


class YieldSeries:
    """7-day net yield (decimal, e.g. 0.035) by N-MFP3 date; lookup = latest date <= d."""

    def __init__(self, by_date: dict):
        self.d = sorted(by_date)
        self.v = [by_date[k] for k in self.d]

    def at(self, d: dt.date):
        i = bisect_right(self.d, d) - 1
        return self.v[i] if i >= 0 else None


def tr6_classify(events, yields: YieldSeries, *, mint_src=ZERO, burn_dst=ZERO, rho_min=0.95,
                 tol=0.02, subs_by_date=None, sub_tol=0.01, drop_zero_prior=True,
                 iii_mode="first_bd_of_month", cal=None, outlier_k=None, initial_bal=None,
                 rate_cap_k=5.0, rate_cap_mode="flat", day_aware_k=1.25, monthly_tol=0.25):
    """TR-6: label every mint 'dividend' (rule i, ii or iii) or 'issuance'.
      (i)  same-ET-day batch to >= 2 holders whose amounts correlate with prior balances at
           rho >= 0.95. IMPL-8: batch = all mints of the ET day on this chain; prior balance =
           balance immediately before the mint; recipients with zero prior balance cannot be
           receiving a dividend and are left out of the batch (drop_zero_prior, proposed);
           recipients may be TA-set wallets (omnibus wallets receive their investors' dividends).
           IMPL-8b (outlier_k; FINAL None = off, per audit: inert once 8c is on; k=3 is a
           robustness flag only): before computing rho, mints whose
           amount/prior-balance ratio exceeds outlier_k x the batch median ratio are removed from
           the batch (they fall through to (ii)/issuance). Calibration evidence: a 200,000,000
           WTGXX issuance mint to the largest holder on 1 Sep 2026 sat in a 62-mint dividend batch
           at rho = 0.991, because one large holder dominates Pearson's rho.
           IMPL-8c (rate_cap_k; FINAL 5.0 with rate_cap_mode='flat'; None = off): a batch mint
           whose amount exceeds rate_cap_k x prior balance x (7-day net yield / 365) x days since
           that address's last mint is not a dividend and leaves the batch. k=5 = the longest run
           of calendar days one payment can cover in the 2024-26 Fedwire calendar (4: a holiday
           Monday plus the weekend, or Fri 3 Jul 2026) x 1.25 for yield noise (audit). k=3 sat on
           the 3-day weekend multiple. Sensitivities: k in {3, 10, None}, and
           rate_cap_mode='day_aware': cap = day_aware_k (1.25) x prior x yield/365 x
           max(days since last mint, calendar days from d through the next business day after d,
           inclusive), so a Saturday payment may cover Sat-Mon (3 days) and a weekday payment
           2 days (needs cal).
           Calibration evidence: with 2-4 recipients (JLTXX, FDIT) rho >= 0.95 is met by issuance
           batches, and on-chain 'dividends' summed to 548% (JLTXX) and 3,073% (WTGXX) of the
           estimated accrual.
      (ii) single holder: amount within +-2% of balance x (7-day net yield / 365) x days since the
           last mint to that address. IMPL-9: applied to every mint not labelled by (i); days =
           ET calendar days since the previous mint to the address (first mint: since the
           balance first became positive); yield = latest N-MFP3 class 7-day net yield <= date.
      (iii) on a day when N-MFP3 shows a subscription equal to the accrual (FDIT pattern).
           IMPL-10: the day's unlabelled mints equal the N-MFP3 gross subscription for the same ET
           date (within sub_tol shares) AND, by iii_mode:
             'first_bd_of_month' (proposed): the ET date is the first business day of a month
                (monthly reinvestment, as in the FYOXX prospectus; needs cal);
             'accrual': the total is within +-2% of supply x yield/365 x days since the previous
                mint day (rejected on calibration: FDIT's monthly batches fail it);
             'monthly_accrual' (audit sensitivity): the total is within +-monthly_tol (25%) of
                the sum over the calendar days of the previous month of end-of-day supply on
                this chain x yield/365;
             'literal': no further condition (relabels any issuance that equals a subscription).
    initial_bal: balances at the start of the event history (needed when the history does not
    start at the token's creation, e.g. the Stellar Horizon window); default all zero.
    Returns dict event id -> (label, rule, detail)."""
    ev = sort_events(events)
    bal = defaultdict(float, initial_bal or {})
    first_pos, last_mint = {}, {}
    if initial_bal and ev:
        d0 = et_date(ev[0]["ts"])
        for a_, v_ in initial_bal.items():
            if v_ > 0:
                first_pos[a_] = d0
    prior = {}
    supply = 0.0
    supply_before = {}
    last_mint_day = None
    days_since_mint_day = {}
    eod_supply = {}                     # ET date -> supply after the last event of that date
    for e in ev:
        if e["kind"] == "mint":
            prior[e["id"]] = bal[e["to"]]
            supply_before[e["id"]] = supply
            d = et_date(e["ts"])
            lm = last_mint.get(e["to"])
            fp = first_pos.get(e["to"])
            ref = lm or fp
            e["_days"] = (d - ref).days if ref else None
            if d != last_mint_day:
                days_since_mint_day[d] = (d - last_mint_day).days if last_mint_day else None
                last_mint_day = d
            last_mint[e["to"]] = d
        if e["frm"] != mint_src:
            bal[e["frm"]] -= e["amount"]
        if e["to"] != burn_dst:
            bal[e["to"]] += e["amount"]
            if bal[e["to"]] > 0 and e["to"] not in first_pos:
                first_pos[e["to"]] = et_date(e["ts"])
        supply += e["amount"] if e["kind"] == "mint" else -e["amount"] if e["kind"] == "burn" else 0.0
        eod_supply[et_date(e["ts"])] = supply
    eod_days = sorted(eod_supply)

    def supply_eod(day):
        i = bisect_right(eod_days, day) - 1
        return eod_supply[eod_days[i]] if i >= 0 else 0.0

    def cap_ok(m, d, y_d):
        days = max(m.get("_days") or 1, 1)
        if rate_cap_mode == "flat":
            return m["amount"] <= rate_cap_k * prior[m["id"]] * y_d / 365 * days
        if rate_cap_mode == "day_aware":
            cover = (cal.next_business(d) - d).days + 1
            return m["amount"] <= day_aware_k * prior[m["id"]] * y_d / 365 * max(days, cover)
        raise ValueError(rate_cap_mode)
    mints = [e for e in ev if e["kind"] == "mint"]
    by_day = defaultdict(list)
    for m in mints:
        by_day[et_date(m["ts"])].append(m)
    out = {}
    for d, ms in by_day.items():
        batch = [m for m in ms if prior[m["id"]] > 0] if drop_zero_prior else ms
        if outlier_k and len(batch) >= 3:
            ratios = sorted(m["amount"] / prior[m["id"]] for m in batch if prior[m["id"]] > 0)
            med = ratios[len(ratios) // 2] if ratios else 0
            if med > 0:
                batch = [m for m in batch if prior[m["id"]] > 0 and m["amount"] / prior[m["id"]] <= outlier_k * med]
        y_d = yields.at(d)
        if (rate_cap_k or rate_cap_mode == "day_aware") and y_d is not None:
            batch = [m for m in batch if cap_ok(m, d, y_d)]
        if len({m["to"] for m in batch}) >= 2:
            rho = pearson([prior[m["id"]] for m in batch], [m["amount"] for m in batch])
            if rho == rho and rho >= rho_min:
                for m in batch:
                    out[m["id"]] = ("dividend", "i", f"rho={rho:.4f} n={len(batch)}")
        y = yields.at(d)
        for m in ms:
            if m["id"] in out:
                continue
            if y is not None and m.get("_days") and prior[m["id"]] > 0:
                exp = prior[m["id"]] * y / 365 * m["_days"]
                if exp > 0 and abs(m["amount"] - exp) <= tol * exp:
                    out[m["id"]] = ("dividend", "ii", f"expected={exp:.6f} days={m['_days']}")
        if subs_by_date is not None and d in subs_by_date:
            tot = sum(m["amount"] for m in ms if m["id"] not in out)
            ok = abs(tot - subs_by_date[d]) <= sub_tol and tot > 0
            if ok and iii_mode == "first_bd_of_month":
                first = d.replace(day=1)
                while not cal.is_business(first):
                    first += dt.timedelta(days=1)
                ok = d == first
            elif ok and iii_mode == "accrual":
                sb = supply_before[ms[0]["id"]]
                ds = days_since_mint_day.get(d)
                exp = sb * y / 365 * ds if (y is not None and ds) else None
                ok = exp is not None and exp > 0 and abs(tot - exp) <= tol * exp
            elif ok and iii_mode == "monthly_accrual":
                last_prev = d.replace(day=1) - dt.timedelta(days=1)
                day, exp = last_prev.replace(day=1), 0.0
                while day <= last_prev:
                    yy = yields.at(day)
                    exp += supply_eod(day) * (yy or 0.0) / 365
                    day += dt.timedelta(days=1)
                ok = exp > 0 and abs(tot - exp) <= monthly_tol * exp
            elif ok and iii_mode != "literal":
                raise ValueError(iii_mode)
            if ok:
                for m in ms:
                    if m["id"] not in out:
                        out[m["id"]] = ("dividend", "iii", f"sub={subs_by_date[d]}")
        for m in ms:
            out.setdefault(m["id"], ("issuance", "", ""))
    for e in ev:
        e.pop("_days", None)
    return out


# ======================================================================= M4 lag and TA batch (IMPL-14 B)
def m4_lag_from_report_date(mint_date: dt.date, report_dates, cal: BusinessCalendar):
    """IMPL-14 option B, as revised after the audit (C2). The accrual is dated at the N-MFP3
    report date d, as in TR-3 ("dividend shares recorded in N-MFP3 on business day d"):
    d = the latest N-MFP3 report date of the fund strictly before the mint's ET date. The lag is
    the number of ET business days in (d, mint date]. Applied only to TR-6 rule-(iii) dividend
    mints, the only ones N-MFP3 dates. Returns None if no report date precedes the mint."""
    prev = [r for r in report_dates if r < mint_date]
    if not prev:
        return None
    return cal.business_days_after(max(prev), mint_date)


def ta_batch_days(dividend_mints, mint_src_by_chain=None, max_span_s=None):
    """IMPL-14 'TA batch' (audit C2; verification item 4c; user approval CP15, 2026-10-16).
    Main specification (max_span_s=None): a fund's dividend day (ET) is a TA batch day if, on
    every chain with dividend mints that day, all of that day's dividend mints originate from that
    chain's TA mint source: the zero address (EVM, Solana, Aptos) or the issuer account (Stellar),
    both in the TR-5 TA set ('zero' leg). Since only the TA can mint on these chains, this holds
    by construction for genuine mints; the test guards only against mis-sourced events.
    Sensitivities (max_span_s = 3600 or 10800): additionally, on every chain the day's dividend
    mints fall within max_span_s seconds of each other (last minus first timestamp).
    dividend_mints: dicts with ts, chain and frm (mint source).
    mint_src_by_chain: chain -> TA mint source (default ZERO for any chain not listed).
    Returns dict ET date -> bool."""
    src = mint_src_by_chain or {}
    by = defaultdict(list)
    for m in dividend_mints:
        by[(et_date(m["ts"]), m.get("chain", ""))].append(m)
    out = {}
    for (d, ch), ms in by.items():
        ok = all(m.get("frm") == src.get(ch, ZERO) for m in ms)
        if ok and max_span_s is not None:
            tss = [m["ts"] for m in ms]
            ok = (max(tss) - min(tss)).total_seconds() <= max_span_s
        out[d] = out.get(d, True) and ok
    return out


# ======================================================================= F0 month status (IMPL-18)
F0_EXCEEDS = {"complete_exceeds", "incomplete_exceeds_onesided"}


def f0_month_status(gap_pct, complete: bool, threshold: float = 0.10):
    """Status of one out-of-sample fund-month for F0, after TR-1 to TR-3 (gap = on-chain minus
    N-MFP3, in % of class shares).
      complete months: |gap| > threshold -> 'complete_exceeds', else 'complete_within'.
      coverage-incomplete months are never a pass (locked rule). One-sided fail test approved by
      the user on 2026-10-16 (CHECKPOINTS CP11 item 2): a missing chain can only ADD supply, so
      covered supply already above N-MFP3 by more than the threshold (gap > +threshold) is an
      unexplained gap -> 'incomplete_exceeds_onesided', which counts as an F0 exceedance
      (F0_EXCEEDS). Any other incomplete month -> 'incomplete_undetermined'.
      no register row or no value -> 'not_computed'."""
    if gap_pct is None or gap_pct == "":
        return "not_computed"
    g = float(gap_pct)
    if complete:
        return "complete_exceeds" if abs(g) > threshold else "complete_within"
    return "incomplete_exceeds_onesided" if g > threshold else "incomplete_undetermined"


F0_CLOCK = "ET"   # TR-1 main clock; UTC rows are the cutoff sensitivity and never count toward F0


def f0_count(rows, clock: str = F0_CLOCK):
    """Number of F0 exceedances for one fund: OOS fund-month rows whose clock is the TR-1 main
    clock (ET) and whose status is in F0_EXCEEDS. UTC rows are the TR-1 sensitivity and are
    excluded, so a month is never counted twice (verification report, item 6). Every row must carry
    a 'clock' field; rows without one raise, so the ET-only rule cannot be bypassed silently."""
    n = 0
    for r in rows:
        if "clock" not in r:
            raise ValueError("F0 row without a clock field")
        if r["clock"] == clock and r.get("f0_month_status") in F0_EXCEEDS:
            n += 1
    return n
