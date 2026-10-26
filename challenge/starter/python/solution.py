#!/usr/bin/env python3
"""
P1.1 starter wallet (Python).

Speaks the harness protocol on stdin/stdout and implements naive largest-first
coin selection. It passes every sanity check and scores ~100 (the baseline).
Your job: make select() smarter. Debug output goes to stderr, never stdout.
"""
import sys

INPUT_VB, OUTPUT_VB, OVERHEAD_VB = 68, 31, 11


def log(*args):
    print(*args, file=sys.stderr)


class Wallet:
    def __init__(self):
        self.long_term_fee_rate = 10
        self.dust_limit = 294
        self.utxos = {}     # outpoint -> (value, frozenset(labels))
        self.pending = {}   # payment id -> list of outpoints we proposed to spend

    # -- state updates -------------------------------------------------------
    def add_utxo(self, outpoint, value, labels):
        self.utxos[outpoint] = (value, frozenset(labels))

    def on_result(self, payment_id, status):
        inputs = self.pending.pop(payment_id, [])
        if status == "ACCEPTED":
            for op in inputs:
                del self.utxos[op]
            # TODO: remember which labels these inputs just linked together.
        elif status == "REJECTED":
            log(f"payment {payment_id} was rejected!")

    # -- coin selection --------------------------------------------------------
    def select(self, amount, fee_rate, priority):
        """
        Return (list_of_outpoints, change_value), or None to skip the payment.
        `priority` is one of "privacy", "balanced", "min_cost" -- this naive
        version ignores it.
        """
        chosen, total = [], 0
        for op, (value, _labels) in sorted(self.utxos.items(), key=lambda kv: -kv[1][0]):
            chosen.append(op)
            total += value
            fee_no_change = (OVERHEAD_VB + INPUT_VB * len(chosen) + OUTPUT_VB) * fee_rate
            if total >= amount + fee_no_change:
                break
        else:
            return None  # cannot afford it
        if len(chosen) > 100:
            return None
        fee_with_change = (OVERHEAD_VB + INPUT_VB * len(chosen) + 2 * OUTPUT_VB) * fee_rate
        change = total - amount - fee_with_change
        if change < self.dust_limit:
            change = 0  # leftover goes to the miner
        return chosen, change


def main():
    w = Wallet()
    for line in sys.stdin:
        parts = line.split()
        if not parts:
            continue
        cmd = parts[0]
        if cmd == "PARAMS":
            w.long_term_fee_rate, w.dust_limit = int(parts[1]), int(parts[2])
        elif cmd == "UTXO":
            w.add_utxo(parts[1], int(parts[2]), parts[3].split(","))
        elif cmd == "PAY":
            pid, amount, fee_rate, priority = parts[1], int(parts[2]), int(parts[3]), parts[4]
            sel = w.select(amount, fee_rate, priority)
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
