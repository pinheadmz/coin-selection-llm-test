#!/usr/bin/env python3
"""
Baseline wallet used as the 100-point reference: largest-first coin selection,
ignores priorities and privacy entirely. Do not edit -- scores are relative to it.
"""
import sys

INPUT_VB, OUTPUT_VB, OVERHEAD_VB = 68, 31, 11


def main():
    dust = 294
    wallet = {}   # outpoint -> value
    pending = {}  # payment id -> inputs
    for line in sys.stdin:
        parts = line.split()
        if not parts:
            continue
        cmd = parts[0]
        if cmd == "PARAMS":
            dust = int(parts[2])
        elif cmd == "UTXO":
            wallet[parts[1]] = int(parts[2])
        elif cmd == "PAY":
            pid, amount, rate = parts[1], int(parts[2]), int(parts[3])
            chosen, total = [], 0
            for op, v in sorted(wallet.items(), key=lambda kv: -kv[1]):
                chosen.append(op)
                total += v
                if total >= amount + (OVERHEAD_VB + INPUT_VB * len(chosen) + OUTPUT_VB) * rate:
                    break
            else:
                chosen = []
            if not chosen or len(chosen) > 100:
                print(f"SKIP {pid}", flush=True)
                continue
            change = total - amount - (OVERHEAD_VB + INPUT_VB * len(chosen) + 2 * OUTPUT_VB) * rate
            if change < dust:
                change = 0
            pending[pid] = chosen
            print(f"TX {pid} {','.join(chosen)} {change}", flush=True)
        elif cmd == "RESULT":
            inputs = pending.pop(parts[1], [])
            if parts[2] == "ACCEPTED":
                for op in inputs:
                    del wallet[op]
        elif cmd == "END":
            break


if __name__ == "__main__":
    main()
