#!/usr/bin/env python3
"""
P1.1 wallet: cost-model-driven, cluster-aware coin selection.

For every PAY we generate a handful of candidate input sets, try several change
values for each, and price every (inputs, change) pair with the published
scoring formula plus two forward-looking terms:

  * holding delta: a change output adds an end-of-run holding cost, and
    spending a coin removes one (credited at CONSOLIDATION_CREDIT, see below),
    so the wallet sweeps small coins only when fees are very low.
  * link penalty: merging clusters is permanent and raises the exposure of
    every later payment from the merged cluster, so each extra cluster merged
    costs LINK_PENALTY units up front.

The cheapest candidate wins. Debug output goes to stderr.
"""
import sys
from itertools import combinations

INPUT_VB, OUTPUT_VB, OVERHEAD_VB = 68, 31, 11
MAX_INPUTS = 100
ROUND_UNIT = 10_000
WEIGHTS = {"privacy": (4.0, 3.0), "balanced": (1.0, 1.0), "min_cost": (0.0, 0.0)}

LINK_PENALTY = 12.0     # units charged per additional cluster merged into a tx
# Credit for removing a coin's holding cost by spending it now. The full credit
# (1.0) over-consolidates: a coin left alone can usually be swept later at an
# even lower fee, or inside a payment that needed an extra input anyway, and
# consolidating inside a payment with change almost always trips the
# unnecessary-input heuristic. 0.2 means "consolidate only when the fee rate is
# below ~1/5 of the long-term rate". Tuned on 7 independently seeded scenario sets.
CONSOLIDATION_CREDIT = 0.2
BNB_MAX_TRIES = 20_000  # search budget for each change-less branch-and-bound run
MAX_COMBOS = 200        # cluster subsets examined when one cluster cannot pay


def log(*args):
    print(*args, file=sys.stderr)


def vsize(n_inputs, has_change):
    return OVERHEAD_VB + INPUT_VB * n_inputs + OUTPUT_VB * (2 if has_change else 1)


def change_leaks(amount, change, values):
    """Mirror of rules.change_leak_reasons, as a bool."""
    if change == 0:
        return False
    if amount % ROUND_UNIT == 0 and change % ROUND_UNIT != 0:
        return True
    return len(values) >= 2 and change >= min(values)


class LinkGraph:
    """Union-find over labels; the same structure the harness keeps."""

    def __init__(self):
        self.parent = {}
        self.size = {}

    def add(self, label):
        if label not in self.parent:
            self.parent[label] = label
            self.size[label] = 1

    def find(self, label):
        root = label
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[label] != root:
            self.parent[label], label = root, self.parent[label]
        return root

    def union_all(self, labels):
        labels = list(labels)
        for l in labels:
            self.add(l)
        root = self.find(labels[0])
        for l in labels[1:]:
            r = self.find(l)
            if r != root:
                if self.size[r] > self.size[root]:
                    r, root = root, r
                self.parent[r] = root
                self.size[root] += self.size[r]


def bnb(coins, fee_rate, target, upper, max_tries=BNB_MAX_TRIES):
    """
    Change-less branch and bound over `coins` (list of (outpoint, value),
    sorted by value descending). Finds a subset whose effective value lies in
    [target, upper], preferring the smallest excess. Returns a list of coins
    or None.
    """
    evs = [v - INPUT_VB * fee_rate for _, v in coins]
    n = len(evs)
    suffix = [0] * (n + 1)
    for i in range(n - 1, -1, -1):
        suffix[i] = suffix[i + 1] + max(evs[i], 0)
    best = [None, upper + 1]
    tries = [0]
    picked = []

    def rec(i, total):
        tries[0] += 1
        if tries[0] > max_tries or total > upper or total + suffix[i] < target:
            return
        if total >= target:
            if total < best[1]:
                best[0], best[1] = list(picked), total
            return
        if i == n or len(picked) >= MAX_INPUTS or evs[i] <= 0:
            return
        picked.append(i)
        rec(i + 1, total + evs[i])
        picked.pop()
        # Skip runs of equal values: excluding one and including its twin is redundant.
        j = i + 1
        while j < n and evs[j] == evs[i]:
            j += 1
        rec(j, total)

    rec(0, 0)
    return None if best[0] is None else [coins[i] for i in best[0]]


class Wallet:
    def __init__(self):
        self.ltfr = 10
        self.dust = 294
        self.utxos = {}  # outpoint -> (value, tuple(labels))
        self.links = LinkGraph()
        self.pending = {}

    @property
    def unit(self):
        return float(vsize(1, False) * self.ltfr)

    def holding(self, value):
        return min(value, INPUT_VB * self.ltfr)

    # -- state ---------------------------------------------------------------
    def add_utxo(self, outpoint, value, labels):
        self.utxos[outpoint] = (value, tuple(labels))
        for l in labels:
            self.links.add(l)

    def on_result(self, pid, status):
        inputs = self.pending.pop(pid, [])
        if status != "ACCEPTED":
            if status == "REJECTED":
                log(f"{pid} rejected")
            return
        labels = {l for op in inputs for l in self.utxos[op][1]}
        self.links.union_all(sorted(labels))
        for op in inputs:
            del self.utxos[op]

    def clusters(self):
        """Group spendable coins by their current cluster root."""
        groups = {}
        for op, (v, labels) in self.utxos.items():
            groups.setdefault(self.links.find(labels[0]), []).append((op, v))
        for coins in groups.values():
            coins.sort(key=lambda c: (-c[1], c[0]))
        return groups

    # -- pricing -------------------------------------------------------------
    def price(self, coins, amount, fee_rate, prio):
        """Cheapest (cost, change) for spending exactly `coins`, or None if unaffordable."""
        n = len(coins)
        if n == 0 or n > MAX_INPUTS:
            return None
        values = [v for _, v in coins]
        total = sum(values)
        excess = total - amount - vsize(n, False) * fee_rate
        if excess < 0:
            return None
        w_exp, w_leak = WEIGHTS[prio]
        unit = self.unit
        roots = {self.links.find(l) for op, _ in coins for l in self.utxos[op][1]}
        exposure = sum(self.links.size[r] for r in roots) - 1
        base = (w_exp * exposure + LINK_PENALTY * (len(roots) - 1)
                - CONSOLIDATION_CREDIT * sum(self.holding(v) for v in values) / unit)

        options = {0}
        cmax = excess - OUTPUT_VB * fee_rate
        if cmax >= self.dust:
            options.add(cmax)
            caps = [cmax]
            if n >= 2:
                caps.append(min(cmax, min(values) - 1))  # dodge unnecessary-input
            for cap in caps:
                options.add(cap)
                options.add(cap // ROUND_UNIT * ROUND_UNIT)  # dodge round-payment
        best = None
        for c in options:
            if c != 0 and (c < self.dust or c > cmax):
                continue
            fee = total - amount - c
            cost = (fee + self.holding(c)) / unit if c else fee / unit
            if change_leaks(amount, c, values):
                cost += w_leak
            if best is None or cost < best[0]:
                best = (cost, c)
        return best[0] + base, best[1]

    # -- candidate generation ------------------------------------------------
    def candidate_sets(self, coins, amount, fee_rate, prio):
        """Plausible input sets drawn from `coins` (sorted by value descending)."""
        coins = [c for c in coins if c[1] > INPUT_VB * fee_rate]  # positive effective value
        if not coins:
            return []
        target_nc = amount + (OVERHEAD_VB + OUTPUT_VB) * fee_rate
        target_c = target_nc + OUTPUT_VB * fee_rate + self.dust
        out = []

        # Largest-first (as few inputs as possible).
        acc, total = [], 0
        for c in coins[:MAX_INPUTS]:
            acc.append(c)
            total += c[1] - INPUT_VB * fee_rate
            if total >= target_c:
                break
        if total >= target_nc:
            out.append(acc)

        # Smallest single coin that pays, with and without room for change.
        for tgt in (target_nc, target_c):
            for c in reversed(coins):
                if c[1] - INPUT_VB * fee_rate >= tgt:
                    out.append([c])
                    break

        # Change-less branch and bound: no change means no leak and nothing to hold.
        w_leak = WEIGHTS[prio][1]
        slack = OUTPUT_VB * fee_rate + self.holding(10 ** 9) + (w_leak + 0.5) * self.unit
        found = bnb(coins, fee_rate, target_nc, target_nc + int(slack))
        if found:
            out.append(found)

        # Consolidation: when spending a coin now is cheaper than holding it,
        # add the remaining coins of this cluster (smallest first).
        gain = [c for c in coins if self.holding(c[1]) > INPUT_VB * fee_rate]
        if gain:
            for base in list(out):
                chosen = {op for op, _ in base}
                extra = [c for c in reversed(gain) if c[0] not in chosen]
                room = MAX_INPUTS - len(base)
                for k in sorted({min(len(extra), room), min(len(extra), room, 5)}):
                    if k > 0:
                        out.append(base + extra[:k])
        return out

    def select(self, amount, fee_rate, prio):
        groups = self.clusters()
        best = None

        def consider(coin_sets):
            nonlocal best
            for coins in coin_sets:
                priced = self.price(coins, amount, fee_rate, prio)
                if priced and (best is None or priced[0] < best[0]):
                    best = (priced[0], [op for op, _ in coins], priced[1])

        for coins in groups.values():
            consider(self.candidate_sets(coins, amount, fee_rate, prio))

        # No single cluster can pay: merge as few (and as small) clusters as needed.
        if best is None and len(groups) > 1:
            roots = list(groups)
            for k in range(2, len(roots) + 1):
                for n_tried, combo in enumerate(combinations(roots, k)):
                    if n_tried >= MAX_COMBOS:
                        break
                    pool = sorted((c for r in combo for c in groups[r]), key=lambda c: (-c[1], c[0]))
                    consider(self.candidate_sets(pool, amount, fee_rate, prio))
                if best is not None:
                    break

        # Last resort: exactly the harness's affordability check (largest coins, no change).
        if best is None:
            pool = sorted(((op, v) for op, (v, _) in self.utxos.items()), key=lambda c: (-c[1], c[0]))
            consider(self.candidate_sets(pool, amount, fee_rate, prio))
        if best is None:
            return None
        return best[1], best[2]


def main():
    w = Wallet()
    for line in sys.stdin:
        parts = line.split()
        if not parts:
            continue
        cmd = parts[0]
        if cmd == "PARAMS":
            w.ltfr, w.dust = int(parts[1]), int(parts[2])
        elif cmd == "UTXO":
            w.add_utxo(parts[1], int(parts[2]), parts[3].split(","))
        elif cmd == "PAY":
            pid, amount, fee_rate, prio = parts[1], int(parts[2]), int(parts[3]), parts[4]
            sel = w.select(amount, fee_rate, prio)
            if sel is None:
                print(f"SKIP {pid}", flush=True)
            else:
                inputs, change = sel
                w.pending[pid] = inputs
                print(f"TX {pid} {','.join(inputs)} {change}", flush=True)
        elif cmd == "RESULT":
            w.on_result(parts[1], parts[2])
        elif cmd == "END":
            break


if __name__ == "__main__":
    main()
