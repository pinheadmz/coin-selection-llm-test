# P1.1 — Privacy-Preserving Coin Selection

Every time a Bitcoin wallet pays someone, it has to decide **which coins
(UTXOs) to spend**. That choice decides how much you pay in fees today, how
much you'll pay tomorrow, and how much a chain-analysis company or your
payment's recipient can learn about you.

Your task: write a wallet coin-selection engine that balances **fees** against
**privacy** and respects a per-payment priority. Your wallet lives through a
whole sequence of payments, so choices made early on (merging coins from
different sources, creating dust, consolidating while fees are cheap) help or
hurt you later.

Submissions may be written in **Python, Rust, or C++**.

---

## 1. What you get

```
challenge/
├── README.md                ← you are here
├── Dockerfile               ← the official evaluation environment
├── evaluator/
│   ├── evaluate.py          ← the automatic judge: run it as often as you like
│   ├── rules.py             ← every validity rule and scoring constant, in code
│   ├── baseline.py          ← the naive wallet that defines a score of 100
│   └── generate_scenarios.py← makes more practice scenarios
├── scenarios/public/*.json  ← 8 practice scenarios
└── starter/
    ├── python/              ← working starter wallets that already pass all
    ├── rust/                   sanity checks. Copy one and improve it.
    └── cpp/
```

Quick start:

```bash
cp -r starter/python my_wallet            # or starter/rust, starter/cpp
python3 evaluator/evaluate.py my_wallet   # builds, runs all scenarios, prints a score
```

Requirements: Python ≥ 3.9 for the evaluator; `cargo` (stable) for Rust; `g++`
with C++17 for C++. The evaluator itself has no third-party dependencies.

---

## 2. The model

### Coins and labels

Each UTXO has an **outpoint** (`<txid>:<vout>`), a **value** in satoshis, and one
or more **cluster labels** saying where the money came from:
`exchange_kyc`, `salary`, `p2p_trade`, `donations`, …

A label is an identity you'd like to keep separate. Your exchange knows who you
are; your P2P trading partners don't. Spending an `exchange_kyc` coin together
with a `p2p_trade` coin tells the world they belong to the same person.

### Linking is permanent and transitive

When a transaction spends inputs with different labels, those labels become
**linked** (the common-input-ownership heuristic). Links never go away, and
they are transitive: if `A` is linked to `B` and `B` to `C`, then `A` is linked
to `C`. The harness tracks the resulting clusters with a union-find.

Change outputs inherit the **union of all input labels**.

### Exposure

When you pay someone, the recipient (and anyone watching) learns every label in
the linked cluster your inputs belong to, **including links made by earlier
transactions**:

```
exposure(tx) = (number of labels in the merged cluster of tx's inputs, after the tx) − 1
```

Spending a coin whose label has never been linked to anything gives exposure 0.

> ⚠️ This is the stateful trap. Merging every cluster in one cheap `min_cost`
> consolidation costs you nothing *then*, but every later `privacy` payment
> now exposes your whole history.

### Change detection

If observers can tell which output is your change, they can follow your money.
A transaction **leaks** its change if it has a change output and either:

| heuristic | condition |
|---|---|
| round payment | `amount % 10000 == 0` and `change % 10000 != 0` |
| unnecessary input | ≥2 inputs and `change ≥ smallest input value` (the smallest input wasn't needed) |

A transaction with no change output never leaks.

### Transaction size and fees

All coins are P2WPKH. Fee rates are **integers** in sat/vB.

```
vsize = 11 + 68·inputs + 31·outputs        (outputs = 1 payment + 0 or 1 change)
fee   = Σ inputs − amount − change
valid ⇔ fee ≥ vsize · fee_rate
```

Anything you don't put in change goes to the miner (that's allowed, it's just
expensive).

---

## 3. Protocol

The harness launches your program once per scenario and talks to it over
**stdin/stdout, one message per line**, fields separated by single spaces. Only
`PAY` needs a reply. **Flush stdout after every reply** (it's the most common
reason for a timeout). Write debug output to **stderr**.

### Harness → you

| message | meaning |
|---|---|
| `PARAMS <long_term_fee_rate> <dust_limit>` | always the first line |
| `UTXO <outpoint> <value> <label>[,<label>…]` | a coin was added to your wallet (initial funds, an incoming payment, or your own change) |
| `PAY <id> <amount> <fee_rate> <priority>` | build a transaction paying `amount` sats. `priority` ∈ `privacy` \| `balanced` \| `min_cost` |
| `RESULT <id> ACCEPTED <txid>` | your transaction was valid and is final. Remove its inputs from your wallet. If it had change, a `UTXO <txid>:1 …` line follows immediately |
| `RESULT <id> REJECTED <reason>` | your transaction was invalid; nothing changed |
| `RESULT <id> SKIPPED -` | acknowledges your `SKIP` |
| `END` | exit cleanly |

### You → harness (reply to every `PAY`, within 2 seconds)

```
TX <id> <outpoint>,<outpoint>,... <change_value>
SKIP <id>
```

`change_value` is `0` for no change output. Use `SKIP` only when the wallet
genuinely cannot afford the payment.

### Example session

```
→ PARAMS 10 294
→ UTXO aa…01:0 150000 salary
→ UTXO bb…02:3 80000 exchange_kyc
→ PAY p000 50000 5 privacy
← TX p000 bb…02:3 29295
→ RESULT p000 ACCEPTED 3f…9c
→ UTXO 3f…9c:1 29295 exchange_kyc
→ PAY p001 500000 5 balanced
← SKIP p001
→ RESULT p001 SKIPPED -
→ END
```

(`p000`: 1 input, 2 outputs → vsize 141 → minimum fee 705 → change
80000 − 50000 − 705 = 29295. Exposure is 0 because `exchange_kyc` was never
linked to anything, but the payment is round and the change isn't, so the
change leaks: cost = 705/1100 + 4·0 + 3·1 ≈ 3.64 units. Could you do better?)

---

## 4. Validity rules (sanity checks)

A transaction is **rejected** if any of these hold:

- no inputs, more than 100 inputs, or the same input twice
- an input that isn't currently in your wallet (unknown, or already spent)
- `0 < change < dust_limit` (294 sats)
- `fee < vsize · fee_rate`
- the reply is malformed or names the wrong payment id

Your submission **passes the sanity checks** only if, on every scenario:

1. **Runs**: it builds, starts, never crashes, answers each `PAY` within 2 s,
   and finishes the scenario within 60 s.
2. **Valid transactions**: no transaction is rejected.
3. **All payments**: it never `SKIP`s a payment the wallet could afford. The
   harness checks this exactly: a payment is affordable if *any* set of ≤100
   coins funds it without change.

A scenario that fails a sanity check scores **0**.

---

## 5. Scoring

All costs are measured in **units**, where 1 unit is the fee of the smallest
possible payment (1 input, 1 output = 110 vB) at the scenario's
`long_term_fee_rate`. Lower total cost is better.

For every accepted transaction, with weights taken from its priority:

```
cost(tx) = fee / unit  +  W_exposure · exposure  +  W_leak · [change leaked]
```

| priority | W_exposure | W_leak |
|---|---|---|
| `privacy` | 4 | 3 |
| `balanced` | 1 | 1 |
| `min_cost` | 0 | 0 |

At the end of a scenario:

```
holding = Σ over remaining UTXOs of min(value, 68 · long_term_fee_rate) / unit
```

This is the future cost of spending what's left at the long-term fee rate.
Every UTXO is a small liability, and dust is a liability worth its full value.
So spending (consolidating) inputs while `fee_rate < long_term_fee_rate` is
cheap, and doing it at 150 sat/vB is not.

Penalties: **25** units per skipped payment (even unavoidable ones; wasting
money until you can't pay is not a strategy), plus **100** units per rejected
transaction, unjustified skip, or payment lost to a crash.

```
scenario_cost  = Σ cost(tx) + holding + penalties
scenario_score = 100 · baseline_cost / your_cost      (0 if a sanity check failed)
final score    = mean scenario_score over all scenarios
```

The baseline is the naive largest-first wallet in `evaluator/baseline.py`
(the same algorithm as the starter kits). **100 = baseline; higher is
better.** Our own (unpublished) reference wallet scores around 120–140.

### Hidden test set

Final ranking uses **hidden scenarios** made with the same generator profiles
(`calm_low_fees`, `fee_spike`, `privacy_first`, `penny_pincher`, `dusty_wallet`,
`whale`, `long_haul`, `running_dry`) but a secret seed. Make your own practice
sets with:

```bash
python3 evaluator/generate_scenarios.py --out /tmp/practice --seed anything-you-like
python3 evaluator/evaluate.py my_wallet --scenarios /tmp/practice
```

Hard-coding answers to the public scenarios won't help.

---

## 6. Submission format

A directory containing your source plus a `submission.json`:

```json
{
  "language": "python" | "rust" | "cpp",
  "build": ["command", "args", "..."],
  "run":   ["command", "args", "..."]
}
```

Both commands run with the submission directory as the working directory.
`build` may be `[]`. See the three starter kits for working examples.

Official evaluation constraints:

- Standard library only. **No network access** during build or run. Rust:
  vendor any crates and build with `--offline`.
- Python 3.11, Rust stable, g++ 12 (C++17): exactly the toolchain in
  `Dockerfile`. To reproduce the official environment:
  `docker build -t p11 . && docker run --rm --network none -v $PWD/my_wallet:/sub p11 /sub`
- 1 CPU core, 512 MB RAM, limits as in §4.

---

## 7. Evaluator tips

```bash
python3 evaluator/evaluate.py my_wallet -s fee_spike --trace  # every tx, with its cost breakdown
python3 evaluator/evaluate.py my_wallet --no-build            # skip recompiling
python3 evaluator/evaluate.py my_wallet --logs logs/          # save your stderr per scenario
python3 evaluator/evaluate.py my_wallet --json report.json    # machine-readable report
```

---

## 8. Ideas to get you started

- **Track the cluster graph yourself.** Your wallet sees every `UTXO` line and
  knows which inputs it spent, so it can keep the same union-find as the harness.
- **Branch and Bound** (as in Bitcoin Core) finds input sets that need no change
  at all: zero leak and no new UTXO to hold.
- **Stay inside one cluster** for `privacy` payments, even if it costs an extra input.
- **The waste metric**: an input's real cost is `68 · (fee_rate − long_term_fee_rate)`.
  It's negative when fees are low, so consolidate *within a cluster* when cheap.
- **Avoid the heuristics on purpose**: rounding change to a multiple of 10 000
  defeats the round-payment heuristic, at the price of a slightly higher fee.
- **Score candidates with the published formula.** Generate several input sets,
  compute the cost of each, and pick the cheapest. Remember the hidden future cost
  of linking clusters.
- **Don't trip over dust**: change below 294 sats is invalid. Drop it into the fee.

## 9. Judging (beyond the number)

The automated score ranks submissions. Finalists are also reviewed by humans
for code quality, tests, and a short `NOTES.md` explaining your strategy and
the trade-offs you made. Clear reasoning about *why* is worth as much as the
last few points.
