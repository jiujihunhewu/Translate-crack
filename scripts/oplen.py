# -*- coding: utf-8 -*-
"""Solve an unknown opcode-length table by maximising global decode coverage.

Why this works: a wrong length almost always lands the decoder in the middle of
an operand, producing an illegal opcode within a few steps. So the length table
that decodes the *most* dwords is, in practice, the correct one. Coordinate
ascent over one opcode at a time converges in 2-4 rounds.

Run this only for opcodes whose native handler you could not disassemble.
If you *can* read the dispatcher, read the lengths off it directly -- that is
exact and instant (see references/vm-reverse.md).

Usage:
  python oplen.py <config> <datafile> --unknown 0x15,0x17,0x1F -o oplen.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C
from vmdis import load_vm_cfg, _eval_len


def make_len(A, code_len, table):
    def L(ip):
        if not (0 <= ip < code_len) or ip >= len(A):
            return None
        spec = table.get(A[ip])
        if spec is None:
            return None
        ln = spec.get("len")
        if ln is None:
            return None
        try:
            n = _eval_len(ln, A, ip) if isinstance(ln, str) else int(ln)
        except (ValueError, IndexError):
            return None
        return n if n > 0 else None
    return L


def make_targets(A, code_len, table):
    def T(ip):
        spec = table.get(A[ip]) or {}
        out = []
        for k in spec.get("targets", []):
            try:
                v = _eval_len(k, A, ip) if isinstance(k, str) else A[ip + int(k)]
            except (ValueError, IndexError):
                continue
            if 4 <= v < code_len:
                out.append(v)
        return out
    return T


def walk(A, code_len, table, seeds, stop):
    L = make_len(A, code_len, table)
    T = make_targets(A, code_len, table)
    seen, fails = {}, []
    queue, i = list(seeds), 0
    while i < len(queue):
        ip = queue[i]
        i += 1
        while 4 <= ip < code_len and ip not in seen:
            ln = L(ip)
            if ln is None:
                fails.append(ip)
                break
            seen[ip] = ln
            for t in T(ip):
                if t not in seen:
                    queue.append(t)
            if A[ip] in stop:
                break
            ip += ln
    return seen, fails


def calibrate(A, code_len, seeds, table, unknown, stop,
              max_len=12, rounds=10, verbose=True):
    table = {k: dict(v) for k, v in table.items()}
    for op in unknown:
        table.setdefault(op, {})["len"] = 1

    def cov(t):
        s, _ = walk(A, code_len, t, seeds, stop)
        return sum(s.values()), len(s)

    best, _ = cov(table)
    for r in range(rounds):
        changed = False
        for op in unknown:
            cur = table[op]["len"]
            bc, bl = best, cur
            for c in range(1, max_len + 1):
                if c == cur:
                    continue
                t2 = {k: dict(v) for k, v in table.items()}
                t2[op]["len"] = c
                v, _ = cov(t2)
                if v > bc:
                    bc, bl = v, c
            if bl != cur:
                table[op]["len"] = bl
                best = bc
                changed = True
        if verbose:
            print("round %d  coverage %d (%.2f%%)"
                  % (r, best, 100.0 * best / max(code_len, 1)))
        if not changed:
            break

    seen, fails = walk(A, code_len, table, seeds, stop)
    return table, {"coverage": best, "pct": 100.0 * best / max(code_len, 1),
                   "insns": len(seen), "fails": len(fails),
                   "unknown": {hex(k): table[k]["len"] for k in unknown}}


def main(argv=None):
    ap = argparse.ArgumentParser(description="solve opcode lengths by coverage")
    ap.add_argument("config")
    ap.add_argument("datafile")
    ap.add_argument("--unknown", required=True,
                    help="comma separated opcodes, e.g. 0x15,0x17,0x1F")
    ap.add_argument("--max-len", type=int, default=12)
    ap.add_argument("--rounds", type=int, default=10)
    ap.add_argument("-o", "--out", default="oplen.json")
    a = ap.parse_args(argv)

    cfg = load_vm_cfg(a.config)
    raw = C.read_bytes(a.datafile)
    A = C.u32(raw)
    code_len = int(A[cfg["code_len_word"]]) or len(A)
    lo = int(A[cfg["label_offset_word"]]) * 4
    cw = cfg.get("label_count_word")
    cnt = (len(raw) - lo) // 4 if cw is None else int(A[cw])
    labels = [x for x in C.u32(raw[lo:], cnt) if 4 <= x < code_len] if cnt else []

    unknown = [int(x, 0) for x in a.unknown.split(",") if x.strip()]
    seeds = [cfg["entry"]] + labels
    table, rep = calibrate(A, code_len, seeds, cfg["opcodes"], unknown,
                           set(cfg["stop_ops"]), a.max_len, a.rounds)

    C.hr("calibration")
    for k, v in rep.items():
        C.kv(k, v)
    out = dict(cfg)
    out["opcodes"] = {hex(k): v for k, v in table.items()}
    C.write_json(a.out, out)
    print("\n  -> %s  (merged config, safe to reuse)" % a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
