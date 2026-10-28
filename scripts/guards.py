# -*- coding: utf-8 -*-
"""Recover the display condition of every text block.

A dialogue block rarely carries its own condition. It is guarded by

  * forward branches that jump *over* it            (local guard)
  * branches in the caller that jump over the CALL  (inherited guard)

so the recovery is: build the CFG, collect forward-branch guards, then run a
fixpoint over the call graph pushing caller guards into callees.

Output is what turns a script dump into a walkthrough: for each text block you
get the exact variable/value combination required to reach it.

Usage:
  python guards.py <config> <datafile> --pages data/pages.json -o data/guards.json
"""
from __future__ import annotations

import argparse
import bisect
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C
from vmdis import VM, load_vm_cfg, build_ins_table, split_functions, OPS_CMP


# ---------------------------------------------------------------- conditions

def operand(vm, k):
    t, v = vm.A[k], vm.A[k + 1]
    if t == 0:
        return str(v), None, v
    if t == 1:
        return "v%d" % v, v, None
    if t == 2:
        return "v[v%d]" % v, ("ind", v), None
    return "?%d" % t, None, None


def condition_at(vm, q):
    """Describe the branch at q, or None if it is not a conditional forward jump."""
    op = vm.A[q]
    nm = vm.name(op)
    ln = vm.length(q)
    if ln is None:
        return None

    if nm == "if":
        L, vl, cl = operand(vm, q + 1)
        R, vr, cr = operand(vm, q + 4)
        return dict(q=q, ln=ln, t=vm.A[q + 6], kind="if",
                    expr="%s %s %s" % (L, OPS_CMP[vm.A[q + 3]], R),
                    vars=[x for x in (vl, vr) if x is not None],
                    consts=[x for x in (cl, cr) if x is not None])

    if nm == "if_any":
        n = vm.A[q + 1]
        parts, vs, cs = [], [], []
        for k in range(n):
            b = q + 2 + k * 5
            L, vl, cl = operand(vm, b)
            R, vr, cr = operand(vm, b + 3)
            parts.append("%s %s %s" % (L, OPS_CMP[vm.A[b + 2]], R))
            vs += [x for x in (vl, vr) if x is not None]
            cs += [x for x in (cl, cr) if x is not None]
        return dict(q=q, ln=ln, t=vm.A[q + n * 5 + 2], kind="if_any",
                    expr=" | ".join(parts), vars=vs, consts=cs)

    if nm == "notin":
        n = vm.A[q + 2]
        vals = [vm.A[q + 3 + k * 2 + 1] for k in range(n)]
        return dict(q=q, ln=ln, t=vm.A[q + 1], kind="notin",
                    expr="NOT IN %s" % (vals,), vars=[], consts=vals)

    return None


# ---------------------------------------------------------------- analysis

def collect_guards(vm, ins):
    """All conditional forward jumps, indexed by position."""
    cond = {}
    for q in ins:
        c = condition_at(vm, q)
        if c and c["t"] > q + c["ln"]:
            cond[q] = c
    fwd = sorted(cond.values(), key=lambda c: c["q"])
    qs = [c["q"] for c in fwd]

    def at(p, limit=12):
        out = []
        j = bisect.bisect_left(qs, p) - 1
        while j >= 0 and len(out) < limit:
            c = fwd[j]
            if c["t"] > p:
                out.append(c)
            j -= 1
        out.reverse()
        return out

    return cond, at


def propagate(vm, ins, starts, guard_at, rounds=12, verbose=True):
    """Push caller guards into callees until stable.

    Bodies are linear decodes (entry -> first stop op), not full CFG walks:
    a conditional block's tail is reached by fallthrough, so linear decoding
    already covers what the guards need, and it is two orders of magnitude
    cheaper across thousands of functions.
    """
    bodies = {s: [p for p, _ in vm.linear(s, limit=min(vm.code_len, s + 4000))]
              for s in starts}
    owner = {}
    for s in starts:
        for p in bodies[s]:
            owner.setdefault(p, s)

    calls = defaultdict(list)
    for q in ins:
        if vm.A[q] == vm.cfg["call_op"]:
            calls[vm.A[q + 1]].append(q)

    fguard = defaultdict(list)
    seen_sig = set()
    for _ in range(rounds):
        changed = False
        for callee, sites in calls.items():
            if callee not in bodies:
                continue
            for q in sites:
                local = guard_at(q)
                inherited = fguard.get(owner.get(q), [])
                key = (callee, tuple(sorted({c["q"] for c in local + inherited})))
                if key in seen_sig:
                    continue
                seen_sig.add(key)
                have = {c["q"] for c in fguard[callee]}
                for c in list(local) + list(inherited):
                    if c["q"] not in have:
                        fguard[callee].append(c)
                        have.add(c["q"])
                        changed = True
        if not changed:
            break
    if verbose:
        print("  functions: %d   with inherited guards: %d"
              % (len(starts), sum(1 for v in fguard.values() if v)))
    return bodies, owner, fguard


def main(argv=None):
    ap = argparse.ArgumentParser(description="recover per-text-block conditions")
    ap.add_argument("config")
    ap.add_argument("datafile")
    ap.add_argument("--pages", required=True)
    ap.add_argument("-o", "--out", default="guards.json")
    a = ap.parse_args(argv)

    cfg = load_vm_cfg(a.config)
    vm = VM(C.read_bytes(a.datafile), cfg)
    pages = C.read_json(a.pages, [])
    pages.sort(key=lambda p: p["off"])

    C.hr("guard recovery")
    ins = build_ins_table(vm)
    vm._ins = ins
    C.kv("instructions", len(ins))

    starts = split_functions(vm, ins)
    cond, guard_at = collect_guards(vm, ins)
    C.kv("conditional fwd jumps", len(cond))

    bodies, owner, fguard = propagate(vm, ins, starts, guard_at)

    def ser(c):
        return dict(q=c["q"], expr=c["expr"], kind=c["kind"],
                    vars=c["vars"], consts=c["consts"])

    res = []
    for q in ins:
        if vm.A[q] != cfg["text_op"]:
            continue
        g = {}
        for c in fguard.get(owner.get(q), []):
            g[c["q"]] = c
        for c in guard_at(q):
            g[c["q"]] = c
        res.append(dict(pos=q, x=vm.A[q + 1], n=vm.A[q + 2], fn=owner.get(q),
                        text=vm.text_at(vm.A[q + 1], pages).split(" / "),
                        guards=[ser(c) for c in sorted(g.values(), key=lambda z: z["q"])]))

    gv = Counter()
    for r in res:
        for c in r["guards"]:
            for v in c["vars"]:
                gv[v] += 1

    C.kv("text blocks", len(res))
    C.kv("with guards", sum(1 for r in res if r["guards"]))
    C.kv("guard variables", gv.most_common(12))

    C.write_json(a.out, res)
    print("\n  -> %s" % a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
