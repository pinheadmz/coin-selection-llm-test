"""
Consensus-ish rules and scoring constants for P1.1 (Privacy-Preserving Coin Selection).

Everything the harness uses to judge a transaction lives in this file so that
participants can read exactly how they are being scored. README.md mirrors
these values; if the two ever disagree, this file wins.
"""

# ---------------------------------------------------------------------------
# Transaction size model (all P2WPKH, virtual bytes)
# ---------------------------------------------------------------------------
OVERHEAD_VB = 11      # version, locktime, segwit marker, in/out counts
INPUT_VB = 68         # one P2WPKH input
OUTPUT_VB = 31        # one P2WPKH output
DUST_LIMIT = 294      # smallest change output the network will relay (sats)
MAX_INPUTS = 100      # standardness cap used by this challenge

# A payment amount that is a multiple of this is considered "round".
ROUND_UNIT = 10_000

# ---------------------------------------------------------------------------
# Scoring weights
# ---------------------------------------------------------------------------
# Per-priority weights: (exposure weight, change-leak weight).
# Fee cost is always weighted 1.0.
PRIORITY_WEIGHTS = {
    "privacy": (4.0, 3.0),
    "balanced": (1.0, 1.0),
    "min_cost": (0.0, 0.0),
}

# Penalties, in cost units (see cost_unit()).
SKIP_PENALTY = 25.0      # every skipped payment, even an unavoidable one
FAILURE_PENALTY = 100.0  # every rejected transaction / unjustified skip / crash

# Time limits (seconds)
REPLY_TIMEOUT = 2.0      # max time to answer a single PAY
SCENARIO_TIMEOUT = 60.0  # max wall-clock time for an entire scenario


def tx_vsize(n_inputs: int, has_change: bool) -> int:
    return OVERHEAD_VB + INPUT_VB * n_inputs + OUTPUT_VB * (2 if has_change else 1)


def required_fee(n_inputs: int, has_change: bool, fee_rate: int) -> int:
    """Minimum fee (sats) for a tx. Fee rates are integer sat/vB, so this is exact."""
    return tx_vsize(n_inputs, has_change) * fee_rate


def cost_unit(long_term_fee_rate: int) -> float:
    """
    One cost unit = the fee of the smallest possible payment (1 input, 1 output)
    at the scenario's long-term fee rate. Fees, privacy, and penalties are all
    expressed in these units so they can be added together.
    """
    return float(tx_vsize(1, False) * long_term_fee_rate)


def holding_cost_sats(value: int, long_term_fee_rate: int) -> int:
    """
    Future cost of a UTXO left in the wallet at the end of a scenario: what it
    will cost to spend it at the long-term fee rate (never more than it is worth).
    """
    return min(value, INPUT_VB * long_term_fee_rate)


def change_leak_reasons(amount: int, change: int, input_values: list[int]) -> list[str]:
    """
    Chain-analysis heuristics an observer can use to tell which output is change.
    A transaction without a change output never leaks.
    """
    if change == 0:
        return []
    reasons = []
    # Round-amount heuristic: a round output next to a non-round output is
    # probably the payment, so the other one is change.
    if amount % ROUND_UNIT == 0 and change % ROUND_UNIT != 0:
        reasons.append("round_payment")
    # Unnecessary-input heuristic: if the payment could have been funded
    # without the smallest input, the sender would not have added it unless
    # the larger leftover output was theirs.
    # sum(in) - min(in) >= amount + fee   <=>   change >= min(in)
    if len(input_values) >= 2 and change >= min(input_values):
        reasons.append("unnecessary_input")
    return reasons


def is_payable(values: list[int], amount: int, fee_rate: int) -> bool:
    """
    True iff *some* subset of `values` (at most MAX_INPUTS coins) can fund a
    change-less payment of `amount` at `fee_rate`. Greedy over effective value
    is exact here: sorting by value is sorting by effective value.
    """
    target = amount + (OVERHEAD_VB + OUTPUT_VB) * fee_rate
    total = 0
    for i, v in enumerate(sorted(values, reverse=True)):
        if i >= MAX_INPUTS:
            break
        ev = v - INPUT_VB * fee_rate
        if ev <= 0:
            break
        total += ev
        if total >= target:
            return True
    return False


class LinkGraph:
    """
    Union-find over cluster labels. Spending coins with different labels in
    one transaction links those labels forever (common-input-ownership
    heuristic), and linking is transitive: A~B and B~C means A~C.
    """

    def __init__(self):
        self.parent: dict[str, str] = {}
        self.size: dict[str, int] = {}

    def add(self, label: str) -> None:
        if label not in self.parent:
            self.parent[label] = label
            self.size[label] = 1

    def find(self, label: str) -> str:
        root = label
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[label] != root:
            self.parent[label], label = root, self.parent[label]
        return root

    def union_all(self, labels) -> str:
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
        return root

    def component_size(self, label: str) -> int:
        return self.size[self.find(label)]
