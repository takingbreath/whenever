"""Runs every example's tests with no model call, no network and no key.

    python3 examples/run_examples.py

For each folder in examples/ that holds a rule.py:

- wonder_test.py is the test that Wonder wrote with the rule. It runs as Wonder ran it: in a fresh
  folder with rule.py and a copy of rule_runtime.py (sandbox.create does the same), as
  `python wonder_test.py`.
- independent_test.py is an acceptance test written from the request alone. It drives the rule
  through the real rule_runtime.Context with a recorded scene (harness.py).

It prints each result, and exits 1 if any test failed.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TIMEOUT_S = 120


def run(command, cwd):
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        done = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return False, f"Stopped after {TIMEOUT_S} s."
    return done.returncode == 0, (done.stdout + done.stderr).strip()


def wonder_test(folder):
    with tempfile.TemporaryDirectory() as work:
        for name in ("rule.py", "wonder_test.py"):
            shutil.copy(folder / name, Path(work) / name)
        shutil.copy(ROOT / "rule_runtime.py", Path(work) / "rule_runtime.py")
        return run([sys.executable, "wonder_test.py"], work)


def independent_test(folder):
    return run([sys.executable, str(folder / "independent_test.py")], folder)


def main():
    examples = sorted(p for p in HERE.iterdir() if (p / "rule.py").is_file())
    print(f"Python {sys.version.split()[0]} ({sys.executable})")
    results = []
    for folder in examples:
        request = (folder / "request.txt").read_text().strip()
        print(f"\n{folder.name}: {request}")
        for label, test in (("wonder_test.py", wonder_test), ("independent_test.py", independent_test)):
            passed, output = test(folder)
            results.append((folder.name, label, passed))
            print(f"  {'PASS' if passed else 'FAIL'} {label}")
            for line in output.splitlines():
                print(f"       {line}")
    failed = [r for r in results if not r[2]]
    print(f"\n{len(results) - len(failed)} of {len(results)} test files passed")
    for name, label, _ in failed:
        print(f"  failed: {name}/{label}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
