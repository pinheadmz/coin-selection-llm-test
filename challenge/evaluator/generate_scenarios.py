#!/usr/bin/env python3
"""
Scenario generator for P1.1.

    python3 evaluator/generate_scenarios.py --out scenarios/public --seed 1

Organizers generate the hidden test set with the same profiles and a secret seed,
so a solution tuned to the public scenarios should generalize -- but one hard-coded
to them will not.
"""
import argparse
import hashlib
import json
import math
import random
from pathlib import Path

LABEL_POOL = [
    "exchange_kyc", "exchange_nokyc", "salary", "p2p_trade", "mining_payout",
    "donations", "atm", "gift", "merchant_sales", "coinjoin", "loan_repayment",
    "freelance", "lightning_close", "faucet", "airdrop", "family",
]

# Fee regimes are (fraction_of_payments, min_rate, max_rate), played in order.
PROFILES = {
    "calm_low_fees": dict(
        n_utxos=25, n_labels=5, n_pay=30, ltfr=10, value=(20_000, 3_000_000), small_frac=0.1,
        amount=(10_000, 400_000), round_frac=0.5, receive_p=0.10,
        regimes=[(1.0, 1, 8)], prio=dict(privacy=0.3, balanced=0.4, min_cost=0.3)),
    "fee_spike": dict(
        n_utxos=30, n_labels=6, n_pay=40, ltfr=15, value=(10_000, 2_000_000), small_frac=0.2,
        amount=(10_000, 300_000), round_frac=0.5, receive_p=0.10,
        regimes=[(0.3, 2, 6), (0.4, 60, 180), (0.3, 3, 10)],
        prio=dict(privacy=0.3, balanced=0.4, min_cost=0.3)),
    "privacy_first": dict(
        n_utxos=30, n_labels=8, n_pay=35, ltfr=10, value=(20_000, 2_000_000), small_frac=0.1,
        amount=(10_000, 500_000), round_frac=0.6, receive_p=0.15,
        regimes=[(1.0, 2, 30)], prio=dict(privacy=0.7, balanced=0.2, min_cost=0.1)),
    "penny_pincher": dict(
        n_utxos=40, n_labels=5, n_pay=40, ltfr=20, value=(5_000, 1_500_000), small_frac=0.3,
        amount=(10_000, 300_000), round_frac=0.4, receive_p=0.10,
        regimes=[(0.5, 1, 15), (0.5, 15, 60)], prio=dict(privacy=0.1, balanced=0.2, min_cost=0.7)),
    "dusty_wallet": dict(
        n_utxos=150, n_labels=6, n_pay=40, ltfr=10, value=(1_000, 400_000), small_frac=0.6,
        amount=(20_000, 600_000), round_frac=0.5, receive_p=0.05,
        regimes=[(0.4, 1, 3), (0.3, 20, 50), (0.3, 1, 5)],
        prio=dict(privacy=0.3, balanced=0.4, min_cost=0.3)),
    "whale": dict(
        n_utxos=6, n_labels=4, n_pay=30, ltfr=10, value=(5_000_000, 50_000_000), small_frac=0.0,
        amount=(10_000, 800_000), round_frac=0.6, receive_p=0.10,
        regimes=[(1.0, 2, 40)], prio=dict(privacy=0.4, balanced=0.3, min_cost=0.3)),
    "long_haul": dict(
        n_utxos=60, n_labels=10, n_pay=150, ltfr=12, value=(10_000, 3_000_000), small_frac=0.2,
        amount=(10_000, 250_000), round_frac=0.5, receive_p=0.20,
        regimes=[(0.25, 1, 10), (0.25, 10, 40), (0.2, 50, 150), (0.3, 2, 12)],
        prio=dict(privacy=0.34, balanced=0.33, min_cost=0.33)),
    "running_dry": dict(
        n_utxos=20, n_labels=5, n_pay=25, ltfr=10, value=(20_000, 800_000), small_frac=0.15,
        amount=(30_000, 600_000), round_frac=0.5, receive_p=0.0, spend_frac=0.85,
        regimes=[(1.0, 5, 40)], prio=dict(privacy=0.3, balanced=0.4, min_cost=0.3),
        infeasible_tail=2),
}


def log_uniform(rng, lo, hi):
    return int(math.exp(rng.uniform(math.log(lo), math.log(hi))))


def outpoint(rng):
    return hashlib.sha256(rng.getrandbits(256).to_bytes(32, "big")).hexdigest() + f":{rng.randrange(4)}"


def make_utxo(rng, p, labels):
    if rng.random() < p["small_frac"]:
        value = log_uniform(rng, 400, 15_000)  # some of these cost more to spend than they are worth
    else:
        value = log_uniform(rng, *p["value"])
    return {"type": "utxo", "outpoint": outpoint(rng), "value": value, "labels": [rng.choice(labels)]}


def fee_schedule(rng, p, n):
    rates = []
    for frac, lo, hi in p["regimes"]:
        rates += [log_uniform(rng, lo, hi + 1) for _ in range(round(frac * n))]
    while len(rates) < n:
        rates.append(rates[-1])
    return [max(1, r) for r in rates[:n]]


def generate(name, seed):
    p = PROFILES[name]
    rng = random.Random(f"{name}:{seed}")
    pool = LABEL_POOL[:]
    rng.shuffle(pool)
    labels = pool[:p["n_labels"]]
    spare_labels = pool[p["n_labels"]:]

    events = [make_utxo(rng, p, labels) for _ in range(p["n_utxos"])]
    funds = sum(e["value"] for e in events)
    rates = fee_schedule(rng, p, p["n_pay"])
    names = list(p["prio"])
    weights = [p["prio"][k] for k in names]

    # Keep the total payment volume comfortably below available funds so every
    # reasonable wallet can pay everything (except deliberate infeasible ones).
    spend_frac = p.get("spend_frac", 0.55)
    n_tail = p.get("infeasible_tail", 0)
    raw = [log_uniform(rng, *p["amount"]) for _ in range(p["n_pay"] - n_tail)]
    receives = []
    for _ in range(p["n_pay"]):
        if rng.random() < p["receive_p"]:
            # Sometimes money arrives from a brand-new source (a fresh cluster).
            fresh = spare_labels and rng.random() < 0.4
            u = make_utxo(rng, p, [spare_labels.pop()] if fresh else labels)
            receives.append(u)
            funds += u["value"]
        else:
            receives.append(None)
    # Reserve room for fees at worst-case rates before scaling.
    budget = spend_frac * funds - sum(rates) * 400
    scale = min(1.0, budget / max(1, sum(raw)))
    amounts = []
    for a in raw:
        a = max(10_000, int(a * scale))
        if rng.random() < p["round_frac"]:
            a = max(10_000, a // 10_000 * 10_000)
        amounts.append(a)
    # Infeasible tail: more than the wallet ever held, under any strategy.
    for _ in range(n_tail):
        amounts.append((funds // 10_000 + 1 + rng.randrange(50)) * 10_000)

    for k in range(p["n_pay"]):
        if receives[k]:
            events.append(receives[k])
        events.append({
            "type": "pay", "id": f"p{k:03d}", "amount": amounts[k], "fee_rate": rates[k],
            "priority": rng.choices(names, weights)[0],
        })
    return {"name": name, "seed": seed, "long_term_fee_rate": p["ltfr"], "events": events}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seed", default="public-1")
    ap.add_argument("--profile", action="append", choices=sorted(PROFILES))
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for name in args.profile or PROFILES:
        sc = generate(name, args.seed)
        (args.out / f"{name}.json").write_text(json.dumps(sc, indent=1) + "\n")
        n_pay = sum(1 for e in sc["events"] if e["type"] == "pay")
        print(f"wrote {name}: {len(sc['events'])} events, {n_pay} payments")


if __name__ == "__main__":
    main()
