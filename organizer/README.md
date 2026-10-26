# P1.1 organizer kit (do not distribute)

Hand participants the `challenge/` directory. Keep this one private.

| file | purpose |
|---|---|
| `reference_solution.py` | strong-ish reference wallet (~120–140 on public and fresh seeds). Shows the scoring rewards real strategy and gives a target to quote. |
| `selftest.py` | runs the evaluator against 10 deliberately broken wallets (double spend, low fee, dust change, lazy skip, crash, hang, no flush, garbage output, bad manifest, failing build) and asserts each is caught. Run after any change to `challenge/evaluator/`. |

## Running the competition

1. Pick a secret seed and generate the hidden set (keep the seed private until
   results are announced):
   ```bash
   python3 challenge/evaluator/generate_scenarios.py --out hidden/ --seed "$SECRET"
   ```
   For a more robust ranking, generate 3 seeds and merge them into one directory
   (rename files, e.g. `fee_spike_s1.json`, and give each a unique `"name"`).
2. Build the image once: `docker build -t p11 challenge/`
3. For each submission:
   ```bash
   docker run --rm --network none --cpus 1 --memory 512m \
     -v "$PWD/hidden:/hidden:ro" -v "$PWD/subs/$NAME:/sub" \
     p11 /sub --scenarios /hidden --json /sub/report.json
   ```
   Exit code 0 means all sanity checks passed. `report.json` has the score, the
   per-scenario breakdown and the full transaction trace.
4. Rank by `score`. Submissions with `"passed": false` still get partial credit
   from their passing scenarios, but can be disqualified at your discretion.

## Sanity-check mapping (from the challenge spec)

| spec | evaluator |
|---|---|
| Solution compiles and runs | build exit code, then `sanity.runs` (crash, timeout, unflushed output) |
| Transactions are valid | `sanity.valid_txs` (inputs exist and are unspent, no duplicates, ≤100 inputs, no dust change, fee ≥ vsize·rate, well-formed reply) |
| Outputs for all payment scenarios | `sanity.all_payments` (no `SKIP` when an exact affordability check says the wallet could pay) |

## Design notes

- **Statefulness** comes from three places: spent coins disappear and change
  appears; cluster links persist and are transitive, so early merges raise the
  exposure of every later payment; and end-of-run holding cost rewards
  consolidating when fees are below the long-term rate.
- **Exploits closed:** wasting funds so later payments become "unaffordable"
  (every skip costs 25 units); linking everything in a free `min_cost` tx
  (exposure counts the whole linked cluster, including earlier links).
- `rules.py` is the single source of truth for constants; the README mirrors it.
- Rust and C++ starters were written without being compiled in the authoring
  environment. Build them once (`cargo build --release`, `g++ -std=c++17`) and
  run `evaluate.py` on each before release. All three should score about 100 (ties between equal-value coins may break differently).
