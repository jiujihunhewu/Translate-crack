# -*- coding: utf-8 -*-
"""Self-check: import every module, then run each CLI with --help.

Used by CI, but also useful locally after editing scripts:

    python scripts/selftest.py

Exits non-zero on the first failure so CI turns red immediately.
"""
from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

MODULES = [
    "common", "extract", "inject", "verify",
    "vmdis", "oplen", "guards", "probe", "package", "init",
]

CLIS = [
    "probe", "extract", "inject", "verify",
    "vmdis", "oplen", "guards", "package", "init",
]


def main() -> int:
    print("=" * 50)
    print("import check")
    print("=" * 50)
    for m in MODULES:
        try:
            importlib.import_module(m)
        except Exception as e:
            print("  FAIL %-10s %s: %s" % (m, type(e).__name__, e))
            return 1
        print("  ok   %-10s" % m)

    print()
    print("=" * 50)
    print("cli check (--help)")
    print("=" * 50)
    for c in CLIS:
        p = HERE / (c + ".py")
        if not p.exists():
            print("  FAIL %-10s missing file" % c)
            return 1
        r = subprocess.run([sys.executable, str(p), "--help"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print("  FAIL %-10s exit %d" % (c, r.returncode))
            print((r.stderr or r.stdout)[:400])
            return 1
        print("  ok   %-10s" % c)

    print()
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
