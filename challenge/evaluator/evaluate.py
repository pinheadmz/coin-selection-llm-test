#!/usr/bin/env python3
"""
Automatic evaluator for challenge P1.1: Privacy-Preserving Coin Selection.

Usage:
    python3 evaluator/evaluate.py path/to/submission            # all public scenarios
    python3 evaluator/evaluate.py path/to/submission -s fee_spike
    python3 evaluator/evaluate.py path/to/submission --scenarios other/dir --json report.json

The submission directory must contain a `submission.json` manifest (see README.md).
The evaluator builds the submission (unless --no-build), then plays each scenario
against it over stdin/stdout, validating every transaction and scoring the result
against a naive baseline wallet.

Exit status: 0 if every sanity check passed, 1 otherwise, 2 on usage/build errors.
"""

import argparse
import hashlib
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import rules
from rules import (
    DUST_LIMIT, FAILURE_PENALTY, MAX_INPUTS, PRIORITY_WEIGHTS, REPLY_TIMEOUT,
    SCENARIO_TIMEOUT, SKIP_PENALTY, LinkGraph, change_leak_reasons, cost_unit,
    holding_cost_sats, is_payable, required_fee,
)

HERE = Path(__file__).resolve().parent
DEFAULT_SCENARIOS = HERE.parent / "scenarios" / "public"
BASELINE_CMD = [sys.executable, "-I", str(HERE / "baseline.py")]
ALLOWED_LANGUAGES = {"python", "rust", "cpp"}


class SolutionError(Exception):
    """The solution process crashed, hung, or spoke gibberish at the protocol level."""


# ---------------------------------------------------------------------------
# Child process wrapper
# ---------------------------------------------------------------------------
class Solution:
    def __init__(self, cmd, cwd, stderr_file):
        try:
            self.proc = subprocess.Popen(
                cmd, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=stderr_file, text=True, bufsize=1,
            )
        except OSError as e:
            raise SolutionError(f"could not start solution: {e}") from e
        self.lines: queue.Queue = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for line in self.proc.stdout:
            self.lines.put(line)
        self.lines.put(None)  # EOF

    def send(self, line: str):
        try:
            self.proc.stdin.write(line + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise SolutionError("solution closed its stdin (crashed?)") from e

    def recv(self, timeout: float) -> str:
        try:
            line = self.lines.get(timeout=max(timeout, 0.01))
        except queue.Empty:
            raise SolutionError(f"no reply within {timeout:.1f}s (did you flush stdout?)")
        if line is None:
            code = self.proc.poll()
            raise SolutionError(f"solution exited unexpectedly (exit code {code})")
        return line.strip()

    def close(self):
        try:
            self.send("END")
        except SolutionError:
            pass
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()


# ---------------------------------------------------------------------------
# Scenario playback
# ---------------------------------------------------------------------------
def make_txid(payment_id, inputs, amount, change):
    data = f"{payment_id}|{','.join(inputs)}|{amount}|{change}".encode()
    return hashlib.sha256(data).hexdigest()


def parse_reply(line: str, payment_id: str):
    """Return ("SKIP", None, None) or ("TX", [inputs], change). Raise ValueError on bad syntax."""
    parts = line.split()
    if len(parts) == 2 and parts[0] == "SKIP":
        if parts[1] != payment_id:
            raise ValueError(f"SKIP for wrong payment id {parts[1]!r}")
        return "SKIP", None, None
    if len(parts) == 4 and parts[0] == "TX":
        if parts[1] != payment_id:
            raise ValueError(f"TX for wrong payment id {parts[1]!r}")
        inputs = parts[2].split(",")
        try:
            change = int(parts[3])
        except ValueError:
            raise ValueError(f"change {parts[3]!r} is not an integer")
        return "TX", inputs, change
    raise ValueError(f"unparseable reply {line[:120]!r}")


def validate_tx(wallet, inputs, change, amount, fee_rate):
    """Return (fee, None) if valid, else (None, reason)."""
    if not inputs or any(not op for op in inputs):
        return None, "no_inputs"
    if len(inputs) > MAX_INPUTS:
        return None, "too_many_inputs"
    if len(set(inputs)) != len(inputs):
        return None, "duplicate_input"
    for op in inputs:
        if op not in wallet:
            return None, "unknown_or_spent_input"
    if change < 0:
        return None, "negative_change"
    if 0 < change < DUST_LIMIT:
        return None, "dust_change"
    total_in = sum(wallet[op][0] for op in inputs)
    fee = total_in - amount - change
    if fee < required_fee(len(inputs), change > 0, fee_rate):
        return None, "insufficient_fee"
    return fee, None


def run_scenario(scenario, cmd, cwd, log_dir=None):
    ltfr = scenario["long_term_fee_rate"]
    unit = cost_unit(ltfr)
    wallet: dict[str, tuple[int, tuple[str, ...]]] = {}
    links = LinkGraph()

    res = {
        "name": scenario["name"],
        "sanity": {"runs": True, "valid_txs": True, "all_payments": True},
        "errors": [],
        "cost": {"fee": 0.0, "privacy": 0.0, "holding": 0.0, "penalty": 0.0},
        "stats": {"payments": 0, "paid": 0, "skipped": 0, "skipped_ok": 0,
                  "rejected": 0, "fees_sats": 0, "exposure": 0, "leaks": 0},
        "trace": [],
    }

    def fail(kind, msg):
        res["sanity"][kind] = False
        if len(res["errors"]) < 20:
            res["errors"].append(msg)

    if log_dir:
        stderr_file = open(Path(log_dir) / f"{scenario['name']}.stderr.log", "w")
    else:
        stderr_file = tempfile.TemporaryFile(mode="w+")
    deadline = time.monotonic() + SCENARIO_TIMEOUT
    events = scenario["events"]
    sol = None
    i = 0
    try:
        sol = Solution(cmd, cwd, stderr_file)
        sol.send(f"PARAMS {ltfr} {DUST_LIMIT}")
        for i, ev in enumerate(events):
            if ev["type"] == "utxo":
                labels = tuple(sorted(ev["labels"]))
                wallet[ev["outpoint"]] = (ev["value"], labels)
                for l in labels:
                    links.add(l)
                sol.send(f"UTXO {ev['outpoint']} {ev['value']} {','.join(labels)}")
                continue

            # --- payment request ---
            pid, amount, rate, prio = ev["id"], ev["amount"], ev["fee_rate"], ev["priority"]
            w_exp, w_leak = PRIORITY_WEIGHTS[prio]
            res["stats"]["payments"] += 1
            payable = is_payable([v for v, _ in wallet.values()], amount, rate)
            sol.send(f"PAY {pid} {amount} {rate} {prio}")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SolutionError(f"scenario exceeded {SCENARIO_TIMEOUT:.0f}s total")
            line = sol.recv(min(REPLY_TIMEOUT, remaining))

            entry = {"id": pid, "amount": amount, "fee_rate": rate, "priority": prio}
            try:
                kind, inputs, change = parse_reply(line, pid)
            except ValueError as e:
                kind, reason = "BAD", "malformed_reply"
                fail("valid_txs", f"{pid}: {e}")

            if kind == "SKIP":
                res["stats"]["skipped"] += 1
                res["cost"]["penalty"] += SKIP_PENALTY
                entry["result"] = "skipped"
                if payable:
                    res["cost"]["penalty"] += FAILURE_PENALTY
                    fail("all_payments", f"{pid}: skipped a payment the wallet could afford")
                    entry["result"] = "skipped_but_payable"
                else:
                    res["stats"]["skipped_ok"] += 1
                sol.send(f"RESULT {pid} SKIPPED -")
            elif kind == "TX":
                fee, reason = validate_tx(wallet, inputs, change, amount, rate)
                if reason:
                    kind = "BAD"
                    fail("valid_txs", f"{pid}: rejected ({reason}): {line[:120]}")
            if kind == "BAD":
                res["stats"]["rejected"] += 1
                res["cost"]["penalty"] += FAILURE_PENALTY + SKIP_PENALTY
                entry["result"] = f"rejected:{reason}"
                sol.send(f"RESULT {pid} REJECTED {reason}")
            elif kind == "TX":
                in_values = [wallet[op][0] for op in inputs]
                in_labels = sorted({l for op in inputs for l in wallet[op][1]})
                root = links.union_all(in_labels)
                exposure = links.size[root] - 1
                leaks = change_leak_reasons(amount, change, in_values)
                fee_cost = fee / unit
                priv_cost = w_exp * exposure + w_leak * (1 if leaks else 0)
                res["cost"]["fee"] += fee_cost
                res["cost"]["privacy"] += priv_cost
                st = res["stats"]
                st["paid"] += 1
                st["fees_sats"] += fee
                st["exposure"] += exposure
                st["leaks"] += 1 if leaks else 0
                for op in inputs:
                    del wallet[op]
                txid = make_txid(pid, inputs, amount, change)
                sol.send(f"RESULT {pid} ACCEPTED {txid}")
                if change:
                    op = f"{txid}:1"
                    wallet[op] = (change, tuple(in_labels))
                    sol.send(f"UTXO {op} {change} {','.join(in_labels)}")
                entry.update(result="paid", n_inputs=len(inputs), change=change, fee=fee,
                             exposure=exposure, leaks=leaks,
                             cost=round(fee_cost + priv_cost, 4))
            res["trace"].append(entry)
    except SolutionError as e:
        fail("runs", f"event {i}: {e}")
        # Every payment we never got to counts as a failure.
        missed = sum(1 for ev in events[i:] if ev["type"] == "pay")
        res["cost"]["penalty"] += missed * (FAILURE_PENALTY + SKIP_PENALTY)
    finally:
        if sol:
            sol.close()
        if not log_dir and not res["sanity"]["runs"]:
            stderr_file.seek(0)
            tail = stderr_file.read()[-2000:]
            if tail.strip():
                res["errors"].append("stderr tail:\n" + tail)
        stderr_file.close()

    res["cost"]["holding"] = sum(holding_cost_sats(v, ltfr) for v, _ in wallet.values()) / unit
    res["total_cost"] = round(sum(res["cost"].values()), 4)
    res["cost"] = {k: round(v, 4) for k, v in res["cost"].items()}
    res["passed"] = all(res["sanity"].values())
    return res


# ---------------------------------------------------------------------------
# Submission handling
# ---------------------------------------------------------------------------
def load_manifest(sub_dir: Path):
    path = sub_dir / "submission.json"
    if not path.is_file():
        sys.exit(f"error: {path} not found")
    m = json.loads(path.read_text())
    lang = m.get("language")
    if lang not in ALLOWED_LANGUAGES:
        sys.exit(f"error: language must be one of {sorted(ALLOWED_LANGUAGES)}, got {lang!r}")
    for key in ("build", "run"):
        v = m.get(key, [])
        if not (isinstance(v, list) and all(isinstance(x, str) for x in v)):
            sys.exit(f"error: '{key}' must be a list of strings")
    if not m.get("run"):
        sys.exit("error: 'run' command is required")
    return m


def build(sub_dir: Path, manifest):
    cmd = manifest.get("build") or []
    if not cmd:
        return
    print(f"[build] {' '.join(cmd)}", flush=True)
    try:
        r = subprocess.run(cmd, cwd=sub_dir, timeout=900)
    except (OSError, subprocess.TimeoutExpired) as e:
        sys.exit(f"error: build failed: {e}")
    if r.returncode != 0:
        print("SANITY FAIL: solution does not compile", file=sys.stderr)
        sys.exit(2)


def resolve_run_cmd(sub_dir: Path, cmd):
    # Make "./solution" style paths absolute so they work regardless of how Popen resolves them.
    first = cmd[0]
    if first.startswith("./") or first.startswith("../"):
        first = str((sub_dir / first).resolve())
    return [first] + cmd[1:]


def load_scenarios(directory: Path, only):
    files = sorted(directory.glob("*.json"))
    scenarios = [json.loads(f.read_text()) for f in files]
    if only:
        scenarios = [s for s in scenarios if s["name"] in only]
        missing = set(only) - {s["name"] for s in scenarios}
        if missing:
            sys.exit(f"error: unknown scenario(s): {', '.join(sorted(missing))}")
    if not scenarios:
        sys.exit(f"error: no scenarios found in {directory}")
    return scenarios


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("submission", type=Path, help="directory containing submission.json")
    ap.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS, help="directory of scenario .json files")
    ap.add_argument("-s", "--scenario", action="append", help="run only this scenario (repeatable)")
    ap.add_argument("--no-build", action="store_true", help="skip the build step")
    ap.add_argument("--json", type=Path, help="write a full machine-readable report here")
    ap.add_argument("--trace", action="store_true", help="print every transaction")
    ap.add_argument("--logs", type=Path, help="save each scenario's stderr into this directory")
    args = ap.parse_args()

    sub_dir = args.submission.resolve()
    manifest = load_manifest(sub_dir)
    if not args.no_build:
        build(sub_dir, manifest)
    run_cmd = resolve_run_cmd(sub_dir, manifest["run"])
    scenarios = load_scenarios(args.scenarios, args.scenario)
    if args.logs:
        args.logs.mkdir(parents=True, exist_ok=True)

    results = []
    hdr = f"{'scenario':<22} {'sanity':<6} {'paid':>9} {'fee':>8} {'privacy':>8} {'holding':>8} {'penalty':>8} {'total':>9} {'baseline':>9} {'score':>6}"
    print(hdr)
    print("-" * len(hdr))
    for sc in scenarios:
        r = run_scenario(sc, run_cmd, sub_dir, args.logs)
        base = run_scenario(sc, BASELINE_CMD, HERE)
        if not base["passed"]:
            sys.exit(f"internal error: baseline failed {sc['name']}: {base['errors']}")
        r["baseline_cost"] = base["total_cost"]
        r["score"] = round(100.0 * base["total_cost"] / max(r["total_cost"], 1e-9), 2) if r["passed"] else 0.0
        results.append(r)
        c, st = r["cost"], r["stats"]
        print(f"{r['name']:<22} {'PASS' if r['passed'] else 'FAIL':<6} "
              f"{st['paid']:>4}/{st['payments']:<4} {c['fee']:>8.1f} {c['privacy']:>8.1f} "
              f"{c['holding']:>8.1f} {c['penalty']:>8.1f} {r['total_cost']:>9.1f} "
              f"{r['baseline_cost']:>9.1f} {r['score']:>6.1f}")
        for e in r["errors"]:
            print(f"    ! {e}")
        if args.trace:
            for t in r["trace"]:
                print(f"    {json.dumps(t)}")

    passed = all(r["passed"] for r in results)
    overall = sum(r["score"] for r in results) / len(results)
    print("-" * len(hdr))
    print(f"SANITY: {'PASS' if passed else 'FAIL'}   "
          f"(runs={all(r['sanity']['runs'] for r in results)}, "
          f"valid_txs={all(r['sanity']['valid_txs'] for r in results)}, "
          f"all_payments={all(r['sanity']['all_payments'] for r in results)})")
    print(f"SCORE:  {overall:.2f}   (100 = baseline; higher is better)")

    if args.json:
        args.json.write_text(json.dumps({
            "language": manifest["language"], "passed": passed, "score": round(overall, 2),
            "scenarios": results,
        }, indent=2))
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
