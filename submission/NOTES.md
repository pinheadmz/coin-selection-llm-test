# P1.1 submission: strategy notes

Python, standard library only, no build step. `python3 -m unittest test_solution.py` runs the tests.

## Results

| scenario set | score |
|---|---|
| public scenarios | 144.0 |
| 6 practice seeds used for tuning (mean) | 143.2 |
| 3 held-out seeds never used for tuning | 147.2 / 152.5 / 142.9 |

Every set passes all sanity checks. The slowest `PAY` reply I measured was about 130 ms.

## How it works

The wallet keeps the same label union-find as the harness. For each `PAY` it:

1. **Groups coins by linked cluster** and builds candidate input sets *inside each
   cluster*:
   - largest-first (fewest inputs);
   - the smallest single coin that pays, with and without room for change;
   - change-less branch and bound (no leak, nothing left to hold, 31 vB cheaper);
   - each of the above plus extra same-cluster coins (consolidation).
2. **Tries several change values** for each set: no change (excess goes to the fee),
   maximum change, change capped below the smallest input (dodges the
   unnecessary-input heuristic), and change rounded down to a multiple of 10 000
   (dodges the round-payment heuristic).
3. **Prices each (inputs, change) pair** with the published formula
   (`fee/unit + W_exp·exposure + W_leak·leak`), plus the holding cost of any
   change output, minus a partial credit for coins removed from the wallet.
4. **Picks the cheapest.** It merges clusters only when no single cluster can pay,
   using the fewest clusters that work. If nothing else works, it falls back to the
   harness's own affordability construction (largest coins, no change), so it never
   SKIPs a payable payment.

## Trade-offs and what I learned from tuning

- **Never link voluntarily.** Linking is permanent and transitive, and it makes every
  later payment from that cluster pay for exposure. I tried letting `min_cost`
  payments merge clusters behind a penalty. At every penalty level the score went
  down. So merging happens only when it is the only way to pay.
- **Consolidate much less than the naive model says.** Fully crediting the holding
  cost of every spent coin (68·ltfr) makes the wallet sweep whenever
  `fee_rate < ltfr`. That scored about 135. Cutting the credit to 0.2× scored about
  143, so the wallet now sweeps only when `fee_rate` is below roughly
  `0.2·ltfr`. Two reasons:
  - A coin that isn't swept now can usually be swept later at an even lower rate.
    The saving isn't unique to this transaction, so it shouldn't be fully credited.
  - Sweeping inside a payment that has change almost always trips the
    unnecessary-input heuristic (`change ≥ smallest input`). That adds 1–3 units on
    `balanced`/`privacy` payments. Privacy cost fell by roughly 3× once
    over-consolidation stopped.
  
  The score is flat between 0.15 and 0.25, so the choice isn't fragile.
- **Change is priced honestly**: 31 vB now, plus the change output's holding cost.
  This makes change-less solutions win whenever the burned excess is smaller than
  that.
- **Leaks are avoided only when that's cheap.** Rounding change to 10 000 can burn up
  to ~9 units, more than the 3-unit privacy leak. So the wallet rounds only when the
  remainder is small. The tests cover both cases.

## Limits / ideas not pursued

- The wallet doesn't know how many payments remain, so it can't judge whether a
  sweep will pay off before the run ends.
- It doesn't keep "clean" clusters in reserve for future `privacy` payments.
  Privacy cost is already small (about 1–7 units per scenario), so I left this out.
- High-fee payments dominate fee_spike and long_haul. After a low-fee sweep, there are
  fewer coin sizes for branch and bound to match against, which hurts later
  change-less payments. I didn't find a rule that reliably balanced the two.
