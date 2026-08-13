#!/usr/bin/env python3
"""Run every ``*_test.py`` in this directory and report a single roll-up.

Dependency-free, no framework — each module is a standalone script that exits 0
on success. This just discovers them, runs each in its own subprocess (so one
module's in-process ``chat`` import / env mutations can't leak into another's),
and aggregates. Modules are mutually isolated (each uses its own ``GROUPCHAT_DIR``
temp room), so they run **concurrently** across a process pool — the wall-clock is
the slowest module, not the sum. Run:

    python3 tests/run_all.py            # all modules
    python3 tests/run_all.py -q         # quiet: only the per-module roll-up
    python3 tests/run_all.py -j1        # force sequential (debugging)
"""
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
# Self-tuning schedule: the runner records each module's wall-clock here and, next
# run, dispatches longest-first (LPT) so the critical-path modules start at t=0
# instead of whenever they happen to fall alphabetically. Gitignored, best-effort.
TIMINGS = os.path.join(HERE, ".test-timings.json")


def _run_module(m):
    t = time.monotonic()
    r = subprocess.run([sys.executable, os.path.join(HERE, m)],
                       capture_output=True, text=True)
    return m, r.returncode == 0, r.stdout, r.stderr, time.monotonic() - t


def _dispatch_order(modules):
    """Longest-first by last run's timings; an unseen module sorts first (treated as
    long) so a new slow test is never starved to the tail. Falls back to alphabetical
    when there's no cache yet."""
    try:
        cached = json.load(open(TIMINGS))
    except Exception:
        cached = {}
    return sorted(modules, key=lambda m: cached.get(m, float("inf")), reverse=True)


def main(argv):
    quiet = "-q" in argv
    workers = (os.cpu_count() or 4)
    for a in argv:  # -jN overrides the worker count (-j1 = sequential)
        if a.startswith("-j") and a[2:].isdigit():
            workers = max(1, int(a[2:]))
    modules = sorted(
        f for f in os.listdir(HERE)
        if f.endswith("_test.py") and f != os.path.basename(__file__)
    )
    # A thread pool suffices: each worker only waits on a blocking subprocess, so
    # the GIL is never the bottleneck, and no module state is shared in-process.
    # Dispatch longest-first for a tight makespan; collect, then print in a STABLE
    # alphabetical order so the roll-up reads the same regardless of finish order.
    collected, durations = {}, {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for m, ok, out, err, dt in ex.map(_run_module, _dispatch_order(modules)):
            collected[m] = (ok, out, err)
            durations[m] = round(dt, 2)
    try:
        json.dump(durations, open(TIMINGS, "w"))
    except Exception:
        pass  # a cache write failure just costs next run its LPT ordering

    results = []
    for m in modules:
        ok, out, err = collected[m]
        results.append((m, ok))
        if not quiet:
            sys.stdout.write(out)
            if err.strip():
                sys.stdout.write(err)
        print(f"[{'PASS' if ok else 'FAIL'}] {m}")

    failed = [m for m, ok in results if not ok]
    print("\n" + "=" * 60)
    print(f"SUITE: {len(results) - len(failed)}/{len(results)} modules passed")
    if failed:
        print("FAILED MODULES: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
