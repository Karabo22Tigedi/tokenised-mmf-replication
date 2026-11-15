"""Unit tests for the pure helpers in scripts/03e_aptos_pull.py (no network, no real data).
Run: python -I -m unittest scripts/test_aptos.py -v      (or: python -I scripts/test_aptos.py)
"""
import datetime as dt
import importlib.util
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import rules as R  # noqa: E402

spec = importlib.util.spec_from_file_location("aptos03e", HERE / "03e_aptos_pull.py")
A = importlib.util.module_from_spec(spec)
spec.loader.exec_module(A)
Z = R.ZERO


class Timestamps(unittest.TestCase):
    def test_floor_to_second_keeps_tr1_meaning(self):
        # 03:59:59.700000 UTC on 1 Oct 2026 = 23:59:59.7 EDT on 30 Sep: before midnight ET, so it must
        # fall inside the 30 Sep TR-1 cutoff (23:59:59 ET) once floored to whole seconds
        t = A.parse_ts("2026-10-01T03:59:59.700000")
        self.assertEqual(t, dt.datetime(2026, 10, 1, 3, 59, 59, tzinfo=dt.timezone.utc))
        self.assertLessEqual(t, R.cutoff(dt.date(2026, 9, 30)))
        self.assertGreater(A.parse_ts("2026-10-01T04:00:00"), R.cutoff(dt.date(2026, 9, 30)))

    def test_no_fraction_and_zulu(self):
        self.assertEqual(A.parse_ts("2025-01-02T03:04:05Z"), dt.datetime(2025, 1, 2, 3, 4, 5, tzinfo=dt.timezone.utc))


class ActivityTypes(unittest.TestCase):
    def test_signs(self):
        self.assertEqual(A.activity_sign("0x1::fungible_asset::Deposit"), 1)
        self.assertEqual(A.activity_sign("0x1::fungible_asset::DepositEvent"), 1)
        self.assertEqual(A.activity_sign("0x1::fungible_asset::Withdraw"), -1)
        self.assertEqual(A.activity_sign("0x1::fungible_asset::Frozen"), 0)


class Legs(unittest.TestCase):
    def test_mint_burn_transfer(self):
        self.assertEqual(A.txn_legs({"0xa": 5.0})[0], [(Z, "0xa", 5.0, "mint")])
        self.assertEqual(A.txn_legs({"0xa": -2.0})[0], [("0xa", Z, 2.0, "burn")])
        self.assertEqual(A.txn_legs({"0xa": -3.0, "0xb": 3.0})[0], [("0xa", "0xb", 3.0, "transfer")])

    def test_transfer_plus_mint_and_multileg(self):
        legs, multi = A.txn_legs({"0xa": -1.0, "0xb": 4.0})
        self.assertEqual(legs, [("0xa", "0xb", 1.0, "transfer"), (Z, "0xb", 3.0, "mint")])
        self.assertFalse(multi)
        legs, multi = A.txn_legs({"0xa": -2.0, "0xb": 1.0, "0xc": 1.0})
        self.assertTrue(multi)
        self.assertEqual(sum(v for *_, v, k in legs if k == "transfer"), 2.0)
        self.assertFalse([l for l in legs if l[3] != "transfer"])

    def test_supply_from_legs(self):
        ev = []
        for i, d in enumerate([{"0xa": 10.0}, {"0xa": -4.0, "0xb": 4.0}, {"0xb": -1.0}]):
            for j, (fr, to, v, k) in enumerate(A.txn_legs(d)[0]):
                ev.append(dict(id=f"{i}{j}", ts=dt.datetime(2026, 9, 1 + i, tzinfo=dt.timezone.utc),
                               frm=fr, to=to, amount=v, kind=k))
        self.assertEqual(R.supply_at(ev, dt.datetime(2026, 9, 30, tzinfo=dt.timezone.utc)), 9.0)
        b = R.balances_at(ev, dt.datetime(2026, 9, 30, tzinfo=dt.timezone.utc))
        self.assertEqual((b["0xa"], b["0xb"]), (6.0, 3.0))


class Calendar(unittest.TestCase):
    def test_month_ends(self):
        self.assertEqual(A.month_ends(dt.date(2024, 11, 1), dt.date(2025, 2, 28)),
                         [dt.date(2024, 11, 30), dt.date(2024, 12, 31), dt.date(2025, 1, 31), dt.date(2025, 2, 28)])
        self.assertEqual(A.month_ends(dt.date(2026, 9, 1), dt.date(2026, 9, 29)), [])


class Dedup(unittest.TestCase):
    def test_page_overlap_removed(self):
        rows = [dict(transaction_version="5", event_index="0"), dict(transaction_version="5", event_index="1"),
                dict(transaction_version="5", event_index="1"), dict(transaction_version="6", event_index="0")]
        self.assertEqual(len(A.dedup_activities(rows)), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
