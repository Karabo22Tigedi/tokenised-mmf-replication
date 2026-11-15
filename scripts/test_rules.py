"""Unit tests for scripts/rules.py on small synthetic examples (no network, no real data).
Run from the project root:
  python -I scripts/test_rules.py
  python -I -m unittest discover -s scripts -p "test_*.py" -v     (all test files)
(`python -I -m unittest scripts/test_rules.py` does not work: -I drops the cwd from sys.path.)
"""
import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rules as R  # noqa: E402

UTC = dt.timezone.utc
Z = R.ZERO


def t(s):  # 'YYYY-MM-DD HH:MM' UTC
    return dt.datetime.fromisoformat(s).replace(tzinfo=UTC)


def ev(i, ts, frm, to, amt):
    kind = "mint" if frm == Z else "burn" if to == Z else "transfer"
    return dict(id=i, ts=t(ts), frm=frm, to=to, amount=float(amt), kind=kind, tx=f"tx{i}")


CAL = R.BusinessCalendar([dt.date(2026, 9, 7), dt.date(2026, 10, 12)])  # Labor Day, Columbus Day


def roles(evs, **kw):
    """tr5_roles with every mint labelled issuance unless mint_labels is given (audit fix C1:
    the hop leg needs TR-6 labels)."""
    kw.setdefault("mint_labels", {x["id"]: ("issuance", "", "") for x in evs if x["kind"] == "mint"})
    return R.tr5_roles(evs, CAL, **kw)


class TR1Clock(unittest.TestCase):
    def test_et_date_crosses_midnight_utc(self):
        # 02:30 UTC on 1 Sep 2026 = 22:30 EDT on 31 Aug
        self.assertEqual(R.et_date(t("2026-09-01 02:30")), dt.date(2026, 8, 31))
        self.assertEqual(R.et_date(t("2026-09-01 04:00")), dt.date(2026, 9, 1))

    def test_cutoff_et_and_utc(self):
        self.assertEqual(R.cutoff(dt.date(2026, 9, 30), "ET"), t("2026-10-01 03:59:59"))  # EDT = UTC-4
        self.assertEqual(R.cutoff(dt.date(2026, 1, 31), "ET"), t("2026-02-01 04:59:59"))  # EST = UTC-5
        self.assertEqual(R.cutoff(dt.date(2026, 9, 30), "UTC"), t("2026-09-30 23:59:59"))

    def test_naive_refused(self):
        with self.assertRaises(ValueError):
            R.et_date(dt.datetime(2026, 1, 1))


class TR4Calendar(unittest.TestCase):
    def test_weekend_and_holiday(self):
        self.assertFalse(CAL.is_business(dt.date(2026, 9, 5)))   # Sat
        self.assertFalse(CAL.is_business(dt.date(2026, 9, 7)))   # Labor Day
        self.assertTrue(CAL.is_business(dt.date(2026, 9, 8)))
        self.assertEqual(CAL.next_business(dt.date(2026, 9, 4)), dt.date(2026, 9, 8))  # Fri -> Tue
        self.assertEqual(CAL.business_days_after(dt.date(2026, 9, 4), dt.date(2026, 9, 8)), 1)

    def test_from_csv_real_calendar(self):
        p = R.ROOT / "data" / "processed" / "ref_fedwire_holidays.csv"
        if not p.exists():
            self.skipTest("run 00_fedwire_calendar.py first")
        cal = R.BusinessCalendar.from_csv(p)
        self.assertTrue(cal.is_business(dt.date(2026, 7, 3)))    # Sat 4 Jul: Reserve Banks open Fri
        self.assertFalse(cal.is_business(dt.date(2025, 7, 4)))
        self.assertTrue(cal.is_business(dt.date(2025, 1, 9)))    # day of mourning: Fedwire open
        with self.assertRaises(ValueError):
            cal.is_business(dt.date(2023, 12, 29))


class TR2Parked(unittest.TestCase):
    def setUp(self):
        W, H = "0xburn", "0xholder"
        self.W = W
        self.ev = [ev(1, "2026-08-01 15:00", Z, H, 100),
                   # holder sends 40 to burn wallet on Mon 31 Aug 16:40 ET, burned 1 Sep 03:45 UTC
                   ev(2, "2026-08-31 20:40", H, W, 40),
                   ev(3, "2026-09-01 03:45", W, Z, 40),
                   # 10 sent Fri 4 Sep; next business day is Tue 8 Sep (Labor Day); burned Wed 9 Sep -> late
                   ev(4, "2026-09-04 15:00", H, W, 10),
                   ev(5, "2026-09-09 15:00", W, Z, 10)]

    def test_lot_burned_next_day_counts(self):
        p = R.tr2_parked_lots(self.ev, [self.W], CAL)
        self.assertEqual([x["amount"] for x in p], [40.0])
        cut = R.cutoff(dt.date(2026, 8, 31), "UTC")  # 31 Aug 23:59:59 UTC: parked, not yet burned
        self.assertEqual(R.tr2_adjustment(p, cut), 40.0)
        cut_et = R.cutoff(dt.date(2026, 8, 31), "ET")  # 1 Sep 03:59:59 UTC: already burned
        self.assertEqual(R.tr2_adjustment(p, cut_et), 0.0)

    def test_late_burn_does_not_count(self):
        p = R.tr2_parked_lots(self.ev, [self.W], CAL)
        self.assertNotIn(10.0, [x["amount"] for x in p])

    def test_window_respects_holiday(self):
        e = self.ev[:3] + [ev(4, "2026-09-04 15:00", "0xholder", self.W, 10),
                           ev(5, "2026-09-08 20:00", self.W, Z, 10)]   # Tue 16:00 ET: in time
        p = R.tr2_parked_lots(e, [self.W], CAL)
        self.assertIn(10.0, [x["amount"] for x in p])


class TR3Dividend(unittest.TestCase):
    def test_next_business_day_mint_added(self):
        mints = [dict(id=1, ts=t("2026-09-01 18:00"), amount=5.0),   # Tue d+1, counted
                 dict(id=2, ts=t("2026-09-02 18:00"), amount=7.0)]   # d+2, not counted
        d = dt.date(2026, 8, 31)
        self.assertEqual(R.tr3_adjustment(mints, d, CAL, R.cutoff(d)), 5.0)

    def test_friday_month_end_business_vs_calendar(self):
        d = dt.date(2026, 7, 31)  # Friday
        mints = [dict(id=1, ts=t("2026-08-03 14:00"), amount=3.0)]  # Monday
        self.assertEqual(R.tr3_adjustment(mints, d, CAL, R.cutoff(d), "business"), 3.0)
        self.assertEqual(R.tr3_adjustment(mints, d, CAL, R.cutoff(d), "calendar"), 0.0)


class TR5Roles(unittest.TestCase):
    def setUp(self):
        self.ev = [
            ev(1, "2026-09-01 14:00", Z, "0xdist", 100),          # mint to distributor
            ev(2, "2026-09-01 15:00", "0xdist", "0xa", 60),       # forwards 100% same ET day
            ev(3, "2026-09-01 15:01", "0xdist", "0xb", 40),
            ev(4, "2026-09-02 14:00", "0xa", "0xb", 10),          # holder -> holder
            ev(5, "2026-09-02 15:00", "0xb", "0xpark", 20),       # to parking wallet
            ev(6, "2026-09-03 15:00", "0xpark", Z, 20),           # burned next day
            ev(7, "2026-09-03 16:00", "0xa", "0xpool", 5),        # to a contract
        ]

    def test_roles(self):
        r = roles(self.ev, has_code=lambda a: a == "0xpool")
        self.assertEqual(r[Z]["role"], "TA")
        self.assertIn("hop", r["0xdist"]["reasons"])
        self.assertEqual(r["0xpark"]["role"], "TA")
        self.assertTrue({"burner", "parking"} <= set(r["0xpark"]["reasons"]))
        self.assertEqual(r["0xpool"]["role"], "contract")
        self.assertEqual(r["0xa"]["role"], "holder")
        self.assertEqual(r["0xb"]["role"], "holder")

    def test_partial_forward_is_not_hop(self):
        e = [ev(1, "2026-09-01 14:00", Z, "0xa", 100), ev(2, "2026-09-01 15:00", "0xa", "0xb", 90)]
        self.assertEqual(roles(e)["0xa"]["role"], "holder")

    def test_forward_next_et_day_is_not_hop(self):
        # mint 22:00 ET 1 Sep (02:00 UTC 2 Sep), forward 01:00 ET 2 Sep: different ET days
        e = [ev(1, "2026-09-02 02:00", Z, "0xa", 100), ev(2, "2026-09-02 05:00", "0xa", "0xb", 100)]
        self.assertEqual(roles(e)["0xa"]["role"], "holder")

    def test_same_day_redemption_is_not_hop(self):
        e = [ev(1, "2026-09-01 14:00", Z, "0xa", 100), ev(2, "2026-09-01 15:00", "0xa", "0xpark", 100),
             ev(3, "2026-09-02 15:00", "0xpark", Z, 100)]
        self.assertEqual(roles(e)["0xa"]["role"], "holder")
        self.assertEqual(roles(e, hop_exclude_redemptions=False)["0xa"]["role"], "TA")

    def test_burner_leg_off_and_parking(self):
        # Stellar-style: holder redeems by paying the issuer once; leg off -> stays holder
        e = [ev(1, "2026-09-01 14:00", Z, "0xa", 100), ev(2, "2026-09-10 14:00", "0xa", Z, 50)]
        self.assertEqual(roles(e, burn_origin_leg=False)["0xa"]["role"], "holder")
        self.assertEqual(roles(e)["0xa"]["role"], "TA")

    def test_tx_sender_variant(self):
        e = [ev(1, "2026-09-01 14:00", Z, "0xa", 100), ev(2, "2026-09-10 14:00", "0xa", Z, 50)]
        r = roles(e, burn_origin="tx_sender", tx_sender={"tx2": "0xta_eoa"})
        self.assertEqual(r["0xa"]["role"], "holder")
        self.assertEqual(r["0xta_eoa"]["role"], "TA")


class TR6Dividends(unittest.TestCase):
    Y = R.YieldSeries({dt.date(2026, 8, 31): 0.0365})

    def test_batch_rule_i(self):
        e = [ev(1, "2026-09-01 14:00", Z, "0xa", 1000), ev(2, "2026-09-01 14:00", Z, "0xb", 3000),
             ev(3, "2026-09-01 14:00", Z, "0xc", 500),
             ev(4, "2026-09-02 14:00", Z, "0xa", 0.1), ev(5, "2026-09-02 14:00", Z, "0xb", 0.3),
             ev(6, "2026-09-02 14:00", Z, "0xc", 0.05),
             ev(7, "2026-09-02 14:01", Z, "0xd", 800)]           # new subscriber same day
        c = R.tr6_classify(e, self.Y)
        self.assertEqual([c[i][0] for i in (4, 5, 6)], ["dividend"] * 3)
        self.assertEqual(c[4][1], "i")
        self.assertEqual(c[7][0], "issuance")
        self.assertEqual(c[1][0], "issuance")

    def test_single_holder_rule_ii(self):
        # balance 1,000,000; 3 days at 3.65% -> 300.00 expected; 301 is within 2%
        e = [ev(1, "2026-08-28 14:00", Z, "0xa", 1_000_000), ev(2, "2026-08-31 14:00", Z, "0xa", 301)]
        c = R.tr6_classify(e, self.Y)
        self.assertEqual(c[2][:2], ("dividend", "ii"))
        e[1]["amount"] = 320.0   # +6.7% off -> issuance
        self.assertEqual(R.tr6_classify(e, self.Y)[2][0], "issuance")

    def test_rule_iii_requires_accrual_size(self):
        # yield missing on purpose for rule ii (no yield before 31 Aug), FDIT-style subscription match
        Y = R.YieldSeries({dt.date(2026, 8, 1): 0.0365})
        e = [ev(1, "2026-08-01 14:00", Z, "0xa", 1_000_000), ev(9, "2026-08-02 14:00", "0xa", "0xb", 1),
             ev(2, "2026-08-11 14:00", Z, "0xb", 1000)]           # 10 days * 100/day on 1m supply
        subs = {dt.date(2026, 8, 11): 1000.0}
        c = R.tr6_classify(e, Y, subs_by_date=subs, iii_mode="accrual")
        self.assertEqual(c[2][:2], ("dividend", "iii"))
        subs2 = {dt.date(2026, 8, 11): 1000.0}
        e2 = [e[0], e[1], ev(2, "2026-08-11 14:00", Z, "0xb", 1000.0)]
        e2[0] = ev(1, "2026-08-01 14:00", Z, "0xa", 5_000_000)  # accrual would be 5,000, not 1,000
        self.assertEqual(R.tr6_classify(e2, Y, subs_by_date=subs2, iii_mode="accrual")[2][0], "issuance")

    def test_rule_iii_first_business_day(self):
        Y = R.YieldSeries({dt.date(2026, 8, 1): 0.0365})
        base = [ev(1, "2026-08-01 14:00", Z, "0xa", 1_000_000)]
        # Tue 1 Sep 2026 23:42 ET (= 2 Sep 03:42 UTC) is the first business day of September
        e = base + [ev(2, "2026-09-02 03:42", Z, "0xa", 5000.0)]
        c = R.tr6_classify(e, Y, subs_by_date={dt.date(2026, 9, 1): 5000.0}, cal=CAL)
        self.assertEqual(c[2][:2], ("dividend", "iii"))
        e = base + [ev(2, "2026-09-09 16:00", Z, "0xa", 5000.0)]     # mid-month subscription match
        c = R.tr6_classify(e, Y, subs_by_date={dt.date(2026, 9, 9): 5000.0}, cal=CAL)
        self.assertEqual(c[2][0], "issuance")

    def test_rule_i_outlier_guard(self):
        e = [ev(i, "2026-08-31 14:00", Z, f"0x{i}", b) for i, b in
             enumerate([1000, 2000, 3000, 4000, 900_000], 1)]
        e += [ev(10 + i, "2026-09-01 14:00", Z, f"0x{i}", b * 0.0001) for i, b in enumerate([1000, 2000, 3000, 4000], 1)]
        e += [ev(20, "2026-09-01 20:00", Z, "0x5", 200_000)]             # big issuance to largest holder
        c = R.tr6_classify(e, self.Y)                      # final: 8b off, 8c k=5 catches mint 20
        self.assertEqual(c[20][0], "issuance")
        self.assertEqual(c[11][:2], ("dividend", "i"))
        self.assertEqual(R.tr6_classify(e, self.Y, outlier_k=None, rate_cap_k=None)[20][0], "dividend")   # literal reading

    def test_rule_i_outlier_guard_isolated(self):
        # audit: test 8b on its own (8c off). 8b is a robustness flag only (outlier_k=3).
        e = [ev(i, "2026-08-31 14:00", Z, f"0x{i}", b) for i, b in
             enumerate([1000, 2000, 3000, 4000, 900_000], 1)]
        e += [ev(10 + i, "2026-09-01 14:00", Z, f"0x{i}", b * 0.0001) for i, b in enumerate([1000, 2000, 3000, 4000], 1)]
        e += [ev(20, "2026-09-01 20:00", Z, "0x5", 200_000)]
        c = R.tr6_classify(e, self.Y, outlier_k=3.0, rate_cap_k=None)
        self.assertEqual(c[20][0], "issuance")
        self.assertEqual(c[11][:2], ("dividend", "i"))

    def test_initial_balance_seed(self):
        # IMPL-1b: history starts mid-life (Stellar crawl START). Seeded holders' first in-window
        # dividend batch is (i); without the seed their prior balance is 0 and it is issuance.
        e = [ev(1, "2026-09-01 14:00", Z, "0xa", 0.1), ev(2, "2026-09-01 14:00", Z, "0xb", 0.3),
             ev(3, "2026-09-01 14:00", Z, "0xc", 0.05)]
        seed = {"0xa": 1000.0, "0xb": 3000.0, "0xc": 500.0}
        c = R.tr6_classify(e, self.Y, initial_bal=seed)
        self.assertEqual([c[i][:2] for i in (1, 2, 3)], [("dividend", "i")] * 3)
        c = R.tr6_classify(e, self.Y)
        self.assertEqual([c[i][0] for i in (1, 2, 3)], ["issuance"] * 3)


class TR6RateCap(unittest.TestCase):
    def test_small_batch_issuance_not_dividend(self):
        Y = R.YieldSeries({dt.date(2026, 8, 31): 0.0365})
        # two holders top up 10% each on the same day: rho = 1, but 10% >> one day's accrual
        e = [ev(1, "2026-08-31 14:00", Z, "0xa", 1000), ev(2, "2026-08-31 14:00", Z, "0xb", 3000),
             ev(3, "2026-09-01 14:00", Z, "0xa", 100), ev(4, "2026-09-01 14:00", Z, "0xb", 300)]
        c = R.tr6_classify(e, Y)
        self.assertEqual((c[3][0], c[4][0]), ("issuance", "issuance"))
        c = R.tr6_classify(e, Y, rate_cap_k=None, outlier_k=None)
        self.assertEqual(c[3][:2], ("dividend", "i"))

    Y = R.YieldSeries({dt.date(2026, 8, 1): 0.0365})   # yield/365 = 0.0001

    def _weekend(self, day_ts, ratio):
        # Thu 27 Aug issuance, Fri 28 Aug one-day dividends, then a batch on day_ts at `ratio`
        # times one day's accrual on the prior balance (days since last mint = 1 or more)
        e = [ev(1, "2026-08-27 14:00", Z, "0xa", 1_000_000), ev(2, "2026-08-27 14:00", Z, "0xb", 3_000_000),
             ev(3, "2026-08-28 14:00", Z, "0xa", 100), ev(4, "2026-08-28 14:00", Z, "0xb", 300)]
        e += [ev(5, day_ts, Z, "0xa", 1_000_100 * 0.0001 * ratio), ev(6, day_ts, Z, "0xb", 3_000_300 * 0.0001 * ratio)]
        return e

    def test_prepaid_weekend_dividend_stays_dividend(self):
        # audit 8c: a Saturday dividend covering Sat-Mon (ratio 3.01) must stay a dividend at k=5;
        # it fails at the old k=3, and survives the day-aware cap (Sat..Mon = 3 days x 1.25)
        e = self._weekend("2026-08-29 14:00", 3.01)                # Saturday
        self.assertEqual(R.tr6_classify(e, self.Y)[5][:2], ("dividend", "i"))
        self.assertEqual(R.tr6_classify(e, self.Y, rate_cap_k=3.0)[5][0], "issuance")
        c = R.tr6_classify(e, self.Y, rate_cap_mode="day_aware", cal=CAL)
        self.assertEqual(c[5][:2], ("dividend", "i"))

    def test_day_aware_cap_is_tighter_on_weekdays(self):
        # Mon 31 Aug, 3 days after the Friday mint, a 3x payment is fine under both caps;
        # on Tue 1 Sep after the Monday mint, 3.01x exceeds the day-aware cap (Tue..Wed = 2 days
        # x 1.25 = 2.5) but not the flat k=5 cap
        e = self._weekend("2026-08-31 14:00", 1.0)[:4]              # Fri dividends only
        e += [ev(5, "2026-08-31 14:00", Z, "0xa", 300.03), ev(6, "2026-08-31 14:00", Z, "0xb", 900.09)]
        e += [ev(7, "2026-09-01 14:00", Z, "0xa", 1_000_400 * 0.0001 * 3.01),
              ev(8, "2026-09-01 14:00", Z, "0xb", 3_001_200 * 0.0001 * 3.01)]
        c = R.tr6_classify(e, self.Y, rate_cap_mode="day_aware", cal=CAL)
        self.assertEqual(c[5][:2], ("dividend", "i"))               # Mon, 3 days since Fri
        self.assertEqual(c[7][0], "issuance")
        self.assertEqual(R.tr6_classify(e, self.Y)[7][:2], ("dividend", "i"))   # flat k=5 keeps it


class TR6RuleIII(unittest.TestCase):
    Y = R.YieldSeries({dt.date(2026, 7, 1): 0.0365})            # yield/365 = 0.0001
    BASE = [ev(1, "2026-07-01 14:00", Z, "0xa", 1_000_000)]

    def lab(self, ts, amt, sub_day, sub, **kw):
        e = self.BASE + [ev(2, ts, Z, "0xa", amt)]
        kw.setdefault("cal", CAL)
        return R.tr6_classify(e, self.Y, subs_by_date={sub_day: sub}, **kw)[2]

    def test_first_of_month_on_weekend(self):
        # 1 Aug 2026 is a Saturday: the first business day is Mon 3 Aug (the FDIT pattern)
        self.assertEqual(self.lab("2026-08-03 18:00", 5000.0, dt.date(2026, 8, 3), 5000.0)[:2], ("dividend", "iii"))
        self.assertEqual(self.lab("2026-08-01 18:00", 5000.0, dt.date(2026, 8, 1), 5000.0)[0], "issuance")

    def test_first_of_month_after_holiday(self):
        cal = R.BusinessCalendar([dt.date(2026, 6, 1)])          # hypothetical Mon 1 Jun closure
        Y = R.YieldSeries({dt.date(2026, 5, 1): 0.0365})
        e = [ev(1, "2026-05-01 14:00", Z, "0xa", 1_000_000), ev(2, "2026-06-02 18:00", Z, "0xa", 5000.0)]
        c = R.tr6_classify(e, Y, subs_by_date={dt.date(2026, 6, 2): 5000.0}, cal=cal)
        self.assertEqual(c[2][:2], ("dividend", "iii"))

    def test_literal_mode(self):
        # mid-month subscription match: issuance under the final rule, dividend under 'literal'
        self.assertEqual(self.lab("2026-08-12 18:00", 5000.0, dt.date(2026, 8, 12), 5000.0)[0], "issuance")
        self.assertEqual(self.lab("2026-08-12 18:00", 5000.0, dt.date(2026, 8, 12), 5000.0,
                                  iii_mode="literal")[:2], ("dividend", "iii"))

    def test_sub_tol_boundary(self):
        self.assertEqual(self.lab("2026-08-03 18:00", 5000.0, dt.date(2026, 8, 3), 5000.009)[1], "iii")
        self.assertEqual(self.lab("2026-08-03 18:00", 5000.0, dt.date(2026, 8, 3), 5000.02)[0], "issuance")
        self.assertEqual(self.lab("2026-08-03 18:00", 5000.0, dt.date(2026, 8, 3), 5000.02, sub_tol=1)[1], "iii")

    def test_monthly_accrual_mode(self):
        # July accrual on 1,000,000 shares = 31 x 100 = 3,100; +-25% passes, 10x fails
        self.assertEqual(self.lab("2026-08-03 18:00", 3100.0, dt.date(2026, 8, 3), 3100.0,
                                  iii_mode="monthly_accrual")[:2], ("dividend", "iii"))
        self.assertEqual(self.lab("2026-08-03 18:00", 31000.0, dt.date(2026, 8, 3), 31000.0,
                                  iii_mode="monthly_accrual")[0], "issuance")


class HopTriggerC1(unittest.TestCase):
    """Audit C1: only issuance mints can trigger the distribution-hop leg."""

    def setUp(self):
        self.e = [ev(1, "2026-09-01 14:00", Z, "0xa", 100_000),
                  ev(2, "2026-09-02 14:00", Z, "0xa", 4.445),            # daily dividend
                  ev(3, "2026-09-02 16:00", "0xa", "0xb", 200)]          # ordinary transfer, same day
        self.lab = {1: ("issuance", "", ""), 2: ("dividend", "ii", "")}

    def test_dividend_mint_does_not_trigger_hop(self):
        self.assertEqual(roles(self.e, mint_labels=self.lab)["0xa"]["role"], "holder")

    def test_pre_audit_reading_and_leg_off(self):
        self.assertIn("hop", roles(self.e, mint_labels=self.lab, hop_trigger="all")["0xa"]["reasons"])
        self.assertEqual(roles(self.e, mint_labels=self.lab, hop_trigger="off")["0xa"]["role"], "holder")

    def test_issuance_mint_still_triggers(self):
        e = self.e + [ev(4, "2026-09-03 14:00", Z, "0xa", 1000), ev(5, "2026-09-03 15:00", "0xa", "0xc", 990)]
        lab = {**self.lab, 4: ("issuance", "", "")}
        self.assertIn("hop", roles(e, mint_labels=lab)["0xa"]["reasons"])

    def test_labels_required(self):
        with self.assertRaises(ValueError):
            R.tr5_roles(self.e, CAL)


class BurnOriginTxSender(unittest.TestCase):
    def test_burn_without_recorded_sender(self):
        # 5 real FOBXX Polygon burns have no recorded tx sender: no burner is added, no crash,
        # and the debited wallet stays a holder
        e = [ev(1, "2026-09-01 14:00", Z, "0xa", 100), ev(2, "2026-09-10 14:00", "0xa", Z, 50)]
        r = roles(e, burn_origin="tx_sender", tx_sender={})
        self.assertEqual(r["0xa"]["role"], "holder")
        self.assertEqual({a for a, v in r.items() if v["role"] == "TA"}, {Z})

    def test_redemption_sink_through_parking_only(self):
        # IMPL-7b under burn_origin='tx_sender': the burn wallet is not a 'burner' (an operator
        # signs the burn) and is a sink only because it passes the parking test
        e = [ev(1, "2026-09-01 14:00", Z, "0xa", 100),
             ev(2, "2026-09-01 15:00", "0xa", "0xburnw", 100),           # same-day forward to burn wallet
             ev(3, "2026-09-02 15:00", "0xburnw", Z, 100)]
        r = roles(e, burn_origin="tx_sender", tx_sender={"tx3": "0xop"})
        self.assertEqual(r["0xburnw"]["reasons"], ["parking"])
        self.assertEqual(r["0xop"]["role"], "TA")
        self.assertEqual(r["0xa"]["role"], "holder")
        self.assertEqual(roles(e, burn_origin="tx_sender", tx_sender={"tx3": "0xop"},
                               hop_exclude_redemptions=False)["0xa"]["role"], "TA")


class CalendarGuard(unittest.TestCase):
    def test_outside_coverage(self):
        with self.assertRaises(ValueError):
            CAL.is_business(dt.date(2023, 12, 29))
        cal = R.BusinessCalendar([], outside="weekends")
        self.assertTrue(cal.is_business(dt.date(2023, 12, 29)))     # Friday, weekend rule only
        self.assertFalse(cal.is_business(dt.date(2023, 12, 30)))
        self.assertEqual(cal.outside_dates, {dt.date(2023, 12, 29), dt.date(2023, 12, 30)})


class M4LagB(unittest.TestCase):
    RD = [dt.date(2026, 6, 30), dt.date(2026, 7, 31), dt.date(2026, 8, 31)]

    def test_lag_from_report_date(self):
        # accrual dated at the N-MFP3 report date d (Fri 31 Jul); (iii) mint Mon 3 Aug -> 1 BD
        self.assertEqual(R.m4_lag_from_report_date(dt.date(2026, 8, 3), self.RD, CAL), 1)
        self.assertEqual(R.m4_lag_from_report_date(dt.date(2026, 9, 1), self.RD, CAL), 1)
        # Tue 8 Sep after Labor Day: Sep 1, 2, 3, 4, 8 -> 5
        self.assertEqual(R.m4_lag_from_report_date(dt.date(2026, 9, 8), self.RD, CAL), 5)
        self.assertIsNone(R.m4_lag_from_report_date(dt.date(2026, 6, 15), self.RD, CAL))

    # TA batch (verification 4c; user approval CP15): main = TA mint source on every chain;
    # 1 h and 3 h spans are sensitivities
    def test_ta_batch(self):
        m = [dict(ts=t("2026-09-01 14:00"), chain="ethereum", frm=Z), dict(ts=t("2026-09-01 14:50"), chain="ethereum", frm=Z),
             dict(ts=t("2026-09-01 20:00"), chain="stellar", frm="GISSUER"),
             dict(ts=t("2026-09-02 14:00"), chain="ethereum", frm=Z), dict(ts=t("2026-09-02 16:00"), chain="ethereum", frm=Z)]
        src = {"ethereum": Z, "stellar": "GISSUER"}
        self.assertEqual(R.ta_batch_days(m, src), {dt.date(2026, 9, 1): True, dt.date(2026, 9, 2): True})
        self.assertEqual(R.ta_batch_days(m, src, max_span_s=3600),
                         {dt.date(2026, 9, 1): True, dt.date(2026, 9, 2): False})

    def test_ta_batch_multi_hour_single_run_is_batch(self):
        # one scheduled monthly TA run starting 09:15 ET, spanning 7 h on Ethereum (cf. WTGXX 1 Sep)
        m = [dict(ts=t("2026-09-01 13:15"), chain="ethereum", frm=Z), dict(ts=t("2026-09-01 20:22"), chain="ethereum", frm=Z),
             dict(ts=t("2026-09-01 13:20"), chain="arbitrum", frm=Z), dict(ts=t("2026-09-01 15:03"), chain="arbitrum", frm=Z),
             dict(ts=t("2026-09-01 13:12"), chain="stellar", frm="GISSUER"), dict(ts=t("2026-09-01 15:08"), chain="stellar", frm="GISSUER")]
        self.assertEqual(R.ta_batch_days(m, {"stellar": "GISSUER"}), {dt.date(2026, 9, 1): True})

    def test_ta_batch_non_ta_source_breaks_day(self):
        m = [dict(ts=t("2026-09-01 14:00"), chain="ethereum", frm=Z),
             dict(ts=t("2026-09-01 14:05"), chain="stellar", frm="GSOMEONEELSE")]   # not the issuer
        self.assertEqual(R.ta_batch_days(m, {"stellar": "GISSUER"}), {dt.date(2026, 9, 1): False})
        # EVM mint from a non-zero address also breaks the day; other days are unaffected
        m2 = [dict(ts=t("2026-09-01 14:00"), chain="ethereum", frm="0xabc"),
              dict(ts=t("2026-09-02 14:00"), chain="ethereum", frm=Z)]
        self.assertEqual(R.ta_batch_days(m2), {dt.date(2026, 9, 1): False, dt.date(2026, 9, 2): True})
        # a non-TA source also fails under the span sensitivities, even inside the span
        self.assertEqual(R.ta_batch_days(m, {"stellar": "GISSUER"}, max_span_s=10800), {dt.date(2026, 9, 1): False})

    def test_ta_batch_span_sensitivities(self):
        def run(minutes):
            return [dict(ts=t("2026-08-03 13:20"), chain="ethereum", frm=Z),
                    dict(ts=t("2026-08-03 13:20") + dt.timedelta(minutes=minutes), chain="ethereum", frm=Z)]
        for minutes, main, h1, h3 in ((60, True, True, True), (61, True, False, True), (105, True, False, True),
                                      (180, True, False, True), (181, True, False, False), (427, True, False, False)):
            self.assertEqual(R.ta_batch_days(run(minutes))[dt.date(2026, 8, 3)], main, minutes)
            self.assertEqual(R.ta_batch_days(run(minutes), max_span_s=3600)[dt.date(2026, 8, 3)], h1, minutes)
            self.assertEqual(R.ta_batch_days(run(minutes), max_span_s=10800)[dt.date(2026, 8, 3)], h3, minutes)
        # span is per chain: two chains each inside 1 h but offset by 5 h still pass the 1 h variant
        m = [dict(ts=t("2026-08-03 13:20"), chain="ethereum", frm=Z), dict(ts=t("2026-08-03 18:20"), chain="arbitrum", frm=Z)]
        self.assertEqual(R.ta_batch_days(m, max_span_s=3600), {dt.date(2026, 8, 3): True})


class F0OneSided(unittest.TestCase):
    def test_status(self):
        f = R.f0_month_status
        self.assertEqual(f(0.05, True), "complete_within")
        self.assertEqual(f(-0.11, True), "complete_exceeds")
        self.assertEqual(f(0.10, True), "complete_within")              # strictly above 0.10
        self.assertEqual(f(0.11, False), "incomplete_exceeds_onesided")  # covered chains already above
        self.assertEqual(f(-3.0, False), "incomplete_undetermined")      # missing chain may fill it
        self.assertEqual(f(0.05, False), "incomplete_undetermined")      # never a pass
        self.assertEqual(f("", True), "not_computed")
        self.assertIn(f(0.2, False), R.F0_EXCEEDS)

    def test_count_et_rows_only(self):
        rows = [dict(clock="ET", f0_month_status="complete_exceeds"),
                dict(clock="UTC", f0_month_status="complete_exceeds"),      # same month, sensitivity
                dict(clock="ET", f0_month_status="incomplete_exceeds_onesided"),
                dict(clock="UTC", f0_month_status="incomplete_exceeds_onesided"),
                dict(clock="ET", f0_month_status="incomplete_undetermined"),
                dict(clock="ET", f0_month_status="not_computed")]
        self.assertEqual(R.f0_count(rows), 2)
        self.assertEqual(R.f0_count(rows, clock="UTC"), 2)
        with self.assertRaises(ValueError):
            R.f0_count([dict(f0_month_status="complete_exceeds")])


class TR3Scope(unittest.TestCase):
    def test_rules_in(self):
        mints = [dict(id=1, ts=t("2026-09-01 18:00"), amount=5.0, rule="i"),
                 dict(id=2, ts=t("2026-09-02 03:42"), amount=7.0, rule="iii")]
        d = dt.date(2026, 8, 31)
        self.assertEqual(R.tr3_adjustment(mints, d, CAL, R.cutoff(d)), 12.0)
        self.assertEqual(R.tr3_adjustment(mints, d, CAL, R.cutoff(d), rules_in={"iii"}), 7.0)


class Helpers(unittest.TestCase):
    def test_supply_and_balances(self):
        e = [ev(1, "2026-09-01 14:00", Z, "0xa", 10), ev(2, "2026-09-02 14:00", "0xa", Z, 4),
             ev(3, "2026-09-03 14:00", "0xa", "0xb", 1)]
        self.assertEqual(R.supply_at(e, t("2026-09-02 23:00")), 6.0)
        b = R.balances_at(e, t("2026-09-30 00:00"))
        self.assertEqual((b["0xa"], b["0xb"]), (5.0, 1.0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
