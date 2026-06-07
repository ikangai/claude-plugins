#!/usr/bin/env python3
"""Run every ``*_test.py`` in this directory and report a single roll-up.

Dependency-free, no framework — each module is a standalone script that exits 0
on success. This just discovers them, runs each in its own subprocess (so one
module's in-process ``chat`` import / env mutations can't leak into another's),
and aggregates. Run:

    python3 tests/run_all.py            # all modules
    python3 tests/run_all.py -q         # quiet: only the per-module roll-up
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main(argv):
    quiet = "-q" in argv
    modules = sorted(
        f for f in os.listdir(HERE)
        if f.endswith("_test.py") and f != os.path.basename(__file__)
    )
    results = []
    for m in modules:
        r = subprocess.run([sys.executable, os.path.join(HERE, m)],
                           capture_output=True, text=True)
        ok = r.returncode == 0
        results.append((m, ok))
        if not quiet:
            sys.stdout.write(r.stdout)
            if r.stderr.strip():
                sys.stdout.write(r.stderr)
        # A concise per-module line even in verbose mode.
        print(f"[{'PASS' if ok else 'FAIL'}] {m}")

    failed = [m for m, ok in results if not ok]
    print("\n" + "=" * 60)
    print(f"SUITE: {len(results) - len(failed)}/{len(results)} modules passed")
    if failed:
        print("FAILED MODULES: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
