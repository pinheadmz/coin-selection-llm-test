#!/usr/bin/env python3
"""Unit tests for the wallet. Run: python3 -m unittest -v test_solution.py"""
import random
import sys
import unittest
from pathlib import Path

from solution import LinkGraph, Wallet, bnb, change_leaks, vsize

# The challenge's own rules module, used as an oracle where available.
EVALUATOR = Path(__file__).resolve().parent.parent / "challenge" / "evaluator"
sys.path.insert(0, str(EVALUATOR))
try:
    import rules
except ImportError:  # running outside the repo layout
    rules = None


def make_wallet(coins, ltfr=10):
    """coins: list of (value, label). Outpoints are generated."""
    w = Wallet()
    w.ltfr = ltfr
    for i, (v, label) in enumerate(coins):
        w.add_utxo(f"{i:064x}:0", v, label.split(","))
    return w


def check_valid(test, w, sel, amount, fee_rate):
    inputs, change = sel
    test.assertTrue(inputs)
    test.assertLessEqual(len(inputs), 100)
    test.assertEqual(len(set(inputs)), len(inputs))
    test.assertTrue(all(op in w.utxos for op in inputs))
    test.assertFalse(0 < change < w.dust, "dust change")
    total = sum(w.utxos[op][0] for op in inputs)
    test.assertGreaterEqual(total - amount - change, vsize(len(inputs), change > 0) * fee_rate)


class LinkGraphTest(unittest.TestCase):
    def test_transitive(self):
        g = LinkGraph()
        for l in "abcd":
            g.add(l)
        g.union_all(["a", "b"])
        g.union_all(["b", "c"])
        self.assertEqual(g.find("a"), g.find("c"))
        self.assertEqual(g.size[g.find("a")], 3)
        self.assertNotEqual(g.find("a"), g.find("d"))


class LeakTest(unittest.TestCase):
    def test_matches_rules(self):
        if rules is None:
            self.skipTest("challenge/evaluator not found")
        rng = random.Random(1)
        for _ in range(2000):
            amount = rng.choice([10_000, 50_000, 12_345, 99_999])
            change = rng.choice([0, 20_000, 7_777, 1_000])
            values = [rng.randrange(500, 50_000) for _ in range(rng.randrange(1, 4))]
            self.assertEqual(change_leaks(amount, change, values),
                             bool(rules.change_leak_reasons(amount, change, values)))


class BnbTest(unittest.TestCase):
    def test_finds_exact_match(self):
        coins = [("a", 50_000), ("b", 30_000), ("c", 20_000), ("d", 7_000)]
        # Effective values at 1 sat/vB: a=49932, b+c=49864. Only b+c lands in the window.
        found = bnb(coins, 1, 49_800, 49_900)
        self.assertEqual(sorted(op for op, _ in found), ["b", "c"])

    def test_none_when_impossible(self):
        self.assertIsNone(bnb([("a", 1_000)], 1, 10_000, 10_100))


class SelectTest(unittest.TestCase):
    def test_stays_in_one_cluster(self):
        w = make_wallet([(500_000, "salary"), (400_000, "exchange_kyc"), (300_000, "salary")])
        inputs, _ = w.select(600_000, 5, "min_cost")
        labels = {w.utxos[op][1] for op in inputs}
        self.assertEqual(labels, {("salary",)})

    def test_merges_only_when_forced(self):
        w = make_wallet([(500_000, "salary"), (400_000, "exchange_kyc"), (300_000, "p2p_trade")])
        sel = w.select(850_000, 5, "privacy")
        check_valid(self, w, sel, 850_000, 5)
        # The two largest clusters suffice; the third must stay untouched.
        self.assertEqual(len(sel[0]), 2)
        self.assertNotIn(f"{2:064x}:0", sel[0])

    def test_prefers_changeless(self):
        w = make_wallet([(1_000_000, "a"), (100_000 + 110 * 20, "a")])
        inputs, change = w.select(100_000, 20, "privacy")
        self.assertEqual((inputs, change), ([f"{1:064x}:0"], 0))

    def test_avoids_round_payment_leak_for_privacy(self):
        # Rounding the change down to 950_000 burns only 500 sats: cheaper than a leak.
        coin = 1_000_000 + 141 * 2 + 500
        w = make_wallet([(coin, "a")])
        _, change = w.select(50_000, 2, "privacy")
        self.assertEqual(change, 950_000)

    def test_accepts_leak_when_rounding_is_expensive(self):
        # Rounding would burn ~9.7k sats (~8.8 units), more than the 3-unit leak.
        w = make_wallet([(1_000_000, "a")])
        _, change = w.select(50_000, 2, "privacy")
        self.assertEqual(change, 1_000_000 - 50_000 - 141 * 2)

    def test_skips_only_when_unaffordable(self):
        w = make_wallet([(10_000, "a"), (5_000, "b")])
        self.assertIsNone(w.select(20_000, 1, "balanced"))
        self.assertIsNotNone(w.select(14_000, 1, "balanced"))

    def test_result_updates_state(self):
        w = make_wallet([(500_000, "a"), (400_000, "b")])
        inputs, _ = w.select(850_000, 1, "balanced")
        w.pending["p0"] = inputs
        w.on_result("p0", "ACCEPTED")
        self.assertEqual(w.utxos, {})
        self.assertEqual(w.links.find("a"), w.links.find("b"))

    def test_random_wallets_valid_and_never_lazy(self):
        """Every reply is valid, and we SKIP only when the harness agrees it is unaffordable."""
        rng = random.Random(7)
        for _ in range(300):
            coins = [(int(rng.lognormvariate(10, 1.5)) + 300, rng.choice("abcdef"))
                     for _ in range(rng.randrange(1, 40))]
            w = make_wallet(coins, ltfr=rng.choice([10, 15, 20]))
            amount = rng.randrange(10_000, 400_000)
            fee_rate = rng.randrange(1, 180)
            prio = rng.choice(["privacy", "balanced", "min_cost"])
            sel = w.select(amount, fee_rate, prio)
            if sel is None:
                if rules is not None:
                    self.assertFalse(rules.is_payable([v for v, _ in coins], amount, fee_rate))
            else:
                check_valid(self, w, sel, amount, fee_rate)


if __name__ == "__main__":
    unittest.main()
