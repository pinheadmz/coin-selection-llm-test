# P1.1 — Privacy-Preserving Coin Selection: challenge and evaluated submission

| path | contents |
|---|---|
| `challenge/` | what participants receive: spec, evaluator, public scenarios, starter kits |
| `organizer/` | private: reference solution and running notes |
| `submission/` | one student's solution (commit `7365e71`) |

## How the challenge is designed

The brief asked for a coin-selection algorithm that trades fees against privacy
per payment, with wallet state carried from one payment to the next. The
challenge (commit `e673e9a`) makes that measurable:

- **Model.** Every UTXO has a value and one or more source labels
  (`exchange_kyc`, `salary`, …). Spending different labels together links them
  permanently and transitively; the harness tracks this with a union-find.
  Change outputs inherit the input labels.
- **Protocol.** The submission is a stdin/stdout process (Python, Rust or C++).
  The harness streams `UTXO` and `PAY <id> <amount> <fee_rate> <priority>`
  lines, the wallet replies `TX` or `SKIP`, and `RESULT` lines report the
  outcome. Wallet state therefore evolves over the whole run.
- **Scoring.** Every cost is expressed in "units" (the fee of a 1-input,
  1-output transaction at the long-term fee rate):
  `fee + W_exp·exposure + W_leak·[change leaked]`, with weights set by priority
  (`privacy` 4/3, `balanced` 1/1, `min_cost` 0/0). On top of that come an
  end-of-run holding cost for every remaining UTXO and penalties for skips and
  rejected transactions. The final score is `100 × baseline_cost / your_cost`,
  where the baseline is a naive largest-first wallet.
- **Statefulness traps.** Exposure counts the whole linked cluster, earlier
  links included, so merging everything in a cheap `min_cost` payment hurts
  every later `privacy` payment. The holding cost rewards consolidating while
  fees are low. Every skip costs 25 units, so a wallet gains nothing by
  "running out" of money on purpose.
- **Sanity checks**, mapped from the brief:
  - *compiles and runs*: the build succeeds, the wallet never crashes and
    answers every payment within 2 s;
  - *transactions are valid*: the inputs exist, the fee is sufficient, there is
    no dust change and at most 100 inputs;
  - *outputs for all payments*: the wallet never skips a payment that an exact
    affordability check says it could fund.
- **Hidden set.** Eight generator profiles (`calm_low_fees`, `fee_spike`,
  `privacy_first`, `penny_pincher`, `dusty_wallet`, `whale`, `long_haul`,
  `running_dry`) are generated with a secret seed, so hard-coding the public
  scenarios doesn't help.

## How the student solved it

`submission/solution.py` is about 320 lines of standard-library Python.
`NOTES.md` explains it in detail. For each payment it:

1. Groups coins by linked cluster and builds candidate input sets **inside one
   cluster**: largest-first, the smallest single coin that pays,
   branch-and-bound for a payment with no change, and each of these with extra
   same-cluster coins added for consolidation.
2. Tries several change values: none, the maximum, capped below the smallest
   input (avoids the unnecessary-input heuristic), and rounded to 10 000
   (avoids the round-payment heuristic).
3. Prices every candidate with the published formula, plus the holding cost of
   any change output, minus a deliberately discounted credit for spent coins
   (0.2×, tuned empirically).
4. Merges clusters only when no single cluster can pay. If nothing else works,
   it falls back to the harness's own affordability construction, so it never
   skips a payment it could make.

## Results

| scenario set | submission | organizer reference |
|---|---|---|
| 8 public scenarios | **143.96** | 134.26 |
| fresh seed `eval-x9` | **152.57** | 138.25 |
| fresh seed `zebra-2026` | **138.44** | 133.93 |
| fresh seed `qq31` | **147.83** | 135.02 |

All sanity checks pass on every set, and all 12 unit tests pass. The slowest
reply I could provoke was about 360 ms, against a 2 s limit. The submission
beats the private reference wallet on every set.

Issues found in review (none affects the score on generator-sized scenarios):

- `bnb` is recursive and raises `RecursionError` once one cluster holds more
  than about 1 000 coins. The generator stays near 150, so this can't happen
  in practice.
- `LINK_PENALTY` has no effect: clusters are merged only when that is
  unavoidable, so every candidate pays the same penalty. The constant is left
  over from an experiment the notes say was abandoned.
- When clusters must be merged, the 200-combination cap walks them in
  dictionary order rather than smallest first, as the comment claims.
- Coins are grouped by their first label only (harmless for the coins the
  generator produces).
- Compiled `__pycache__/*.pyc` files are committed.

## LLM vs. own work: assessment

**Conclusion: this was almost certainly produced by an LLM in a single pass,
with little or no human contribution beyond the prompt.** The evidence:

- **Timing.** The solution commit landed **9 minutes 43 seconds** after the
  challenge commit (14:24:37 → 14:34:20). In that window the author would have
  had to read a 300-line spec, write about 320 lines of solution, 140 lines of
  tests and a 70-line strategy document, and run the tuning sweeps the notes
  describe: 7 seeded scenario sets, a consolidation credit swept from 0.15 to
  1.0, and link-penalty experiments. A person can't do that in ten minutes;
  an agent running the evaluator in a loop can.
- **Commit messages.** Both commits are labelled "one-shot"
  (`init: one-shot create challenge`, `one-shot solution`), the usual phrasing
  for a single LLM prompt. They share the same author identity, which suggests
  one operator generated both the challenge and the answer.
- **Artifacts.** The committed `cpython-313` bytecode matches the Python in this
  agent container rather than the Python 3.11 used for official evaluation.
  This points to the code having been run in the same environment that created
  it.
- **Style.** The code mirrors `rules.py` closely (its `LinkGraph` class is
  copied almost verbatim, which is allowed), the comments are uniform and
  tutorial-like, and `NOTES.md` has the polished, evenly structured
  "Results / How it works / Trade-offs / Limits" shape typical of model output.
  There are no dead ends, TODOs, or inconsistencies of the kind human
  iteration leaves behind, apart from the leftover `LINK_PENALTY`.

What argues the other way is weak. The design choices (keep each payment
inside one cluster, discount the consolidation credit, price change honestly)
are good and well argued, but nothing about them requires a human. The notes
read as genuine experimental findings, which is consistent with an agent that
actually ran the evaluator rather than one that made the numbers up. (I
reproduced the public score exactly.)

The submission is strong and works correctly. As evidence of the student's own
understanding, it should be treated as unverified: a short oral walkthrough
(for example "why 0.2 and not 1.0?", "why does `LINK_PENALTY` never matter?")
would settle it quickly.
