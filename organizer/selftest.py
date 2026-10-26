#!/usr/bin/env python3
"""
ORGANIZER ONLY: check that the evaluator catches broken submissions.

    python3 organizer/selftest.py

Each case derives a deliberately broken wallet from the baseline (by text
substitution) and asserts which sanity check the evaluator reports as failing.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "challenge" / "evaluator" / "evaluate.py"
BASELINE = (ROOT / "challenge" / "evaluator" / "baseline.py").read_text()
REF = (ROOT / "organizer" / "reference_solution.py").read_text()

# name -> (solution source, expected failing sanity key or None for pass, expected exit code)
CASES = {
    "baseline_passes": (BASELINE, None, 0),
    "reference_passes": (REF, None, 0),
    "double_spend": (BASELINE.replace("del wallet[op]", "pass"), "valid_txs", 1),
    "underpays_fee": (BASELINE.replace(
        "print(f\"TX {pid} {','.join(chosen)} {change}\"",
        "print(f\"TX {pid} {','.join(chosen)} {change + (OVERHEAD_VB + INPUT_VB * len(chosen) + OUTPUT_VB * (2 if change else 1)) * rate - (total - amount - change) + 1 if change else change}\""),
        "valid_txs", 1),
    "dust_change": (BASELINE.replace("pending[pid] = chosen", "change = 100 if pid == 'p000' else change\n            pending[pid] = chosen"), "valid_txs", 1),
    "lazy_skip": (BASELINE.replace("if not chosen or len(chosen) > 100:", "if True:"), "all_payments", 1),
    "crashes": (BASELINE.replace("pid, amount, rate = ", "assert parts[1] != 'p003'\n            pid, amount, rate = "), "runs", 1),
    "hangs": (BASELINE.replace("pid, amount, rate = ", "import time; time.sleep(30) if parts[1] == 'p002' else None\n            pid, amount, rate = "), "runs", 1),
    "forgets_flush": (BASELINE.replace(", flush=True", ""), "runs", 1),
    "garbage_reply": (BASELINE.replace("print(f\"SKIP {pid}\", flush=True)\n                continue",
                                       "print(f\"SKIP {pid}\", flush=True)\n                continue\n            if pid == 'p001':\n                print('hello', flush=True); continue"), "valid_txs", 1),
}


def run_case(name, source, manifest_overrides=None):
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "solution.py").write_text(source)
        manifest = {"language": "python", "build": [], "run": ["python3", "-I", "solution.py"]}
        manifest.update(manifest_overrides or {})
        (d / "submission.json").write_text(json.dumps(manifest))
        report = d / "report.json"
        p = subprocess.run([sys.executable, str(EVAL), str(d), "-s", "calm_low_fees", "-s", "running_dry",
                            "--json", str(report)], capture_output=True, text=True, timeout=300)
        data = json.loads(report.read_text()) if report.exists() else None
        return p.returncode, data, p.stdout + p.stderr


def main():
    ok = True
    for name, (src, expect_fail, expect_code) in CASES.items():
        code, data, out = run_case(name, src)
        failed_keys = set()
        if data:
            for sc in data["scenarios"]:
                failed_keys |= {k for k, v in sc["sanity"].items() if not v}
        good = code == expect_code and (failed_keys == set() if expect_fail is None else expect_fail in failed_keys)
        if name not in ("baseline_passes", "reference_passes") and src == BASELINE:
            print(f"  (substitution for {name} did not apply)")
            good = False
        ok &= good
        print(f"{'ok  ' if good else 'FAIL'} {name:<18} exit={code} failed={sorted(failed_keys) or '-'}")
        if not good:
            print(out[-3000:])

    for name, override, want in [
        ("bad_language", {"language": "java"}, 1),  # sys.exit(msg) -> 1
        ("build_fails", {"build": ["false"]}, 2),
    ]:
        code, _, out = run_case(name, BASELINE, override)
        good = code == want
        ok &= good
        print(f"{'ok  ' if good else 'FAIL'} {name:<18} exit={code}")
        if not good:
            print(out[-2000:])
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
