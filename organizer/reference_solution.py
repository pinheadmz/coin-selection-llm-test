#!/usr/bin/env python3
"""
ORGANIZER ONLY -- do not distribute.

A reasonably strong reference wallet used to sanity-check that the scoring
rewards good behaviour. It generates many candidate input sets per payment
(single coins, change-less branch-and-bound per cluster, cluster-restricted
largest/smallest-first, opportunistic consolidation) and picks the one with
the lowest expected cost under the published scoring rules, including the
holding cost of the UTXO set it leaves behind.
"""
import sys

INPUT_VB, OUTPUT_VB, OVERHEAD_VB = 68, 31, 11
ROUND_UNIT = 10_000
WEIGHTS = {"privacy": (4.0, 3.0), "balanced": (1.0, 1.0), "min_cost": (0.0, 0.0)}


class Links:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, labels):
        roots = {self.find(l) for l in labels}
        r = min(roots)
        for o in roots:
            self.parent[o] = r

    def merged_size(self, labels):
        roots = {self.find(l) for l in labels}
        return sum(1 for l in self.parent if self.find(l) in roots)


def vsize(n_in, change):
    return OVERHEAD_VB + INPUT_VB * n_in + OUTPUT_VB * (2 if change else 1)


class Wallet:
    def __init__(self):
        self.utxos = {}  # op -> (value, labels)
        self.links = Links()
        self.ltfr = 10
        self.dust = 294
        self.pending = {}

    def add(self, op, value, labels):
        self.utxos[op] = (value, labels)
        for l in labels:
            self.links.find(l)

    def finish(self, ops, amount, rate):
        """Turn a set of inputs into (change, fee) or None if it cannot fund the payment."""
        total = sum(self.utxos[o][0] for o in ops)
        n = len(ops)
        if total < amount + vsize(n, False) * rate:
            return None
        change = total - amount - vsize(n, True) * rate
        if change < self.dust:
            return [(0, total - amount)]
        opts = [(change, total - amount - change)]
        # Change too small to be worth keeping: drop it to fees.
        opts.append((0, total - amount))
        rounded = change // ROUND_UNIT * ROUND_UNIT
        if amount % ROUND_UNIT == 0 and rounded >= self.dust and rounded != change:
            opts.append((rounded, total - amount - rounded))
        return opts

    def cost(self, ops, change, fee, amount, prio):
        unit = vsize(1, False) * self.ltfr
        w_exp, w_leak = WEIGHTS[prio]
        labels = {l for o in ops for l in self.utxos[o][1]}
        exposure = self.links.merged_size(labels) - 1
        values = [self.utxos[o][0] for o in ops]
        leak = change and ((amount % ROUND_UNIT == 0 and change % ROUND_UNIT) or
                           (len(ops) >= 2 and change >= min(values)))
        # Future cost: removing inputs saves their holding cost; change adds one.
        hold = lambda v: min(v, INPUT_VB * self.ltfr)
        hold_delta = (hold(change) if change else 0) - sum(hold(v) for v in values)
        # Linking clusters has a future price too: later privacy payments expose more.
        future_link = 4.0 * (len({self.links.find(l) for l in labels}) - 1)
        return (fee + hold_delta) / unit + w_exp * exposure + (w_leak if leak else 0) + future_link

    def candidates(self, amount, rate):
        coins = sorted(self.utxos.items(), key=lambda kv: -kv[1][0])
        groups = {}
        for op, (v, labels) in coins:
            root = self.links.find(next(iter(labels)))
            groups.setdefault(root, []).append((op, v))
        pools = list(groups.values()) + [[(op, v) for op, (v, _) in coins]]
        target = amount + vsize(0, False) * rate
        for idx, pool in enumerate(pools):
            per_cluster = idx < len(pools) - 1
            eff = [(op, v - INPUT_VB * rate) for op, v in pool if v - INPUT_VB * rate > 0]
            # single coins
            for op, _ in pool:
                yield [op]
            # largest-first
            sel, acc = [], 0
            for op, e in eff:
                sel.append(op)
                acc += e
                if acc >= target:
                    yield list(sel)
                    break
            # smallest-sufficient then fill: walk ascending
            asc = eff[::-1]
            sel, acc = [], 0
            for op, e in asc:
                sel.append(op)
                acc += e
                if acc >= target:
                    yield list(sel)
                    break
            # change-less branch and bound (bounded effort)
            window = vsize(0, True) * rate - vsize(0, False) * rate + INPUT_VB * self.ltfr
            yield from self.bnb(eff[:40], target, window)
            # consolidation: best single coin + cheap extra coins from the same pool
            if per_cluster and rate < self.ltfr:
                for op, e in eff:
                    if e >= target:
                        extras = [o for o, ee in asc if o != op][:8]
                        yield [op] + extras
                        break

    def bnb(self, eff, target, window, budget=20000):
        found = []
        suffix = [0] * (len(eff) + 1)
        for i in range(len(eff) - 1, -1, -1):
            suffix[i] = suffix[i + 1] + eff[i][1]
        tries = [0]

        def rec(i, acc, sel):
            tries[0] += 1
            if tries[0] > budget or len(found) >= 5:
                return
            if acc >= target:
                if acc <= target + window:
                    found.append(list(sel))
                return
            if i == len(eff) or acc + suffix[i] < target:
                return
            sel.append(eff[i][0])
            rec(i + 1, acc + eff[i][1], sel)
            sel.pop()
            rec(i + 1, acc, sel)

        rec(0, 0, [])
        return found

    def pay(self, pid, amount, rate, prio):
        best = None
        seen = set()
        for ops in self.candidates(amount, rate):
            key = frozenset(ops)
            if key in seen or len(ops) > 100:
                continue
            seen.add(key)
            opts = self.finish(ops, amount, rate)
            if not opts:
                continue
            for change, fee in opts:
                c = self.cost(ops, change, fee, amount, prio)
                if best is None or c < best[0]:
                    best = (c, ops, change)
        if best is None:
            return f"SKIP {pid}"
        self.pending[pid] = best[1]
        return f"TX {pid} {','.join(best[1])} {best[2]}"


def main():
    w = Wallet()
    for line in sys.stdin:
        p = line.split()
        if not p:
            continue
        if p[0] == "PARAMS":
            w.ltfr, w.dust = int(p[1]), int(p[2])
        elif p[0] == "UTXO":
            w.add(p[1], int(p[2]), tuple(p[3].split(",")))
        elif p[0] == "PAY":
            print(w.pay(p[1], int(p[2]), int(p[3]), p[4]), flush=True)
        elif p[0] == "RESULT":
            ops = w.pending.pop(p[1], [])
            if p[2] == "ACCEPTED":
                w.links.union({l for o in ops for l in w.utxos[o][1]})
                for o in ops:
                    del w.utxos[o]
        elif p[0] == "END":
            break


if __name__ == "__main__":
    main()
