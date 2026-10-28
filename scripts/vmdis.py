# -*- coding: utf-8 -*-
"""Config-driven disassembler for dword-oriented script VMs.

Many Japanese VN engines of the 2000s compile their scenario into a flat
array of little-endian uint32: an opcode word followed by fixed-shape operand
words. Recovering the instruction boundaries is the whole game -- once you have
them, call graphs, conditions and text references all fall out.

The trick that makes this tractable: the VM writes its instruction pointer back
to a global after every dispatch (`add edx,N` / `mov [g_ip],edx` in the native
handler). That `N` is the instruction length. Disassemble the native dispatcher
once, read N per opcode, and you never have to guess.

When the binary is stripped or packed, fall back to `oplen.py`, which solves the
length table by maximising global decode coverage instead.

Usage:
  python vmdis.py <config> <datafile> --list 12000 12100
  python vmdis.py <config> <datafile> --funcs
  python vmdis.py <config> <datafile> --callers 8315
  python vmdis.py <config> <datafile> --xref 531
  python vmdis.py <config> <datafile> --dump data/ins.json
"""
from __future__ import annotations

import argparse
import bisect
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C

OPS_CMP = ["<", "<=", ">", ">=", "==", "!="]
STOP = {0x0F, 0x02, 0x11, 0x25}


# ---------------------------------------------------------------- config

VM_DEFAULT = {
    "code_len_word": 0,
    "label_offset_word": 1,
    "label_count_word": 2,
    "text_base": None,
    "text_scale": 2,
    "entry": 4,
    "stop_ops": [0x0F, 0x02, 0x11, 0x25],
    "call_op": 0x10,
    "jmp_op": 0x0F,
    "cond_ops": {"0x18": "if", "0x1C": "if2", "0x27": "notin", "0x40": "if_any"},
    "text_op": 0x41,
    "opcodes": {},
}


def load_vm_cfg(path):
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    out = dict(VM_DEFAULT)
    out.update(cfg)
    out["opcodes"] = {int(k, 0): v for k, v in out["opcodes"].items()}
    out["cond_ops"] = {int(k, 0): v for k, v in out["cond_ops"].items()}
    # JSON cannot hold int keys, so scalar opcodes arrive as "0x41" strings.
    for k in ("text_op", "call_op", "jmp_op"):
        if isinstance(out.get(k), str):
            out[k] = int(out[k], 0)
    out["stop_ops"] = [int(x, 0) if isinstance(x, str) else x for x in out["stop_ops"]]
    return out


_SAFE = re.compile(r"^[0-9+\*()\s]+$")


def _eval_len(expr, A, ip):
    """Evaluate a length expression like '$2*2+3' ($N == A[ip+N])."""
    e = re.sub(r"\$(\d+)", lambda m: str(A[ip + int(m.group(1))]), str(expr))
    if not _SAFE.match(e):
        raise ValueError("unsafe length expression: %r" % expr)
    return int(eval(e, {"__builtins__": {}}, {}))


def _compile_len(expr):
    """Precompile a length expression to a code object bound to (A, ip)."""
    src = re.sub(r"\$(\d+)", r"A[ip+\1]", str(expr))
    if not re.match(r"^[Aip0-9\[\]\+\*\s]+$", src):
        raise ValueError("unsafe length expression: %r" % expr)
    return compile(src, "<oplen>", "eval")


# ---------------------------------------------------------------- VM

class VM:
    def __init__(self, data: bytes, cfg: dict):
        self.d = data
        self.cfg = cfg
        self.A = C.u32(data)
        self.code_len = int(self.A[cfg["code_len_word"]]) or len(self.A)
        lo = int(self.A[cfg["label_offset_word"]]) * 4
        cw = cfg.get("label_count_word")
        if cw is None:
            # table runs to EOF; that is the common layout
            cnt = max(0, (len(data) - lo) // 4)
        else:
            cnt = int(self.A[cw])
        self.labels = C.u32(data[lo:], cnt) if cnt else []
        self.labels = [x for x in self.labels if 4 <= x < self.code_len]
        self.text_base = cfg.get("text_base")
        self.text_scale = cfg.get("text_scale", 2)
        self.ops = cfg["opcodes"]
        self.stop = set(cfg["stop_ops"])

        # Precompile length expressions once. They are evaluated for every
        # instruction of every function, so a regex here costs minutes.
        self._lenfn = {}
        for op, spec in self.ops.items():
            ln = spec.get("len")
            if ln is None:
                continue
            if isinstance(ln, int):
                self._lenfn[op] = ln
            else:
                self._lenfn[op] = _compile_len(ln)

    # -- instruction shape -------------------------------------------
    def length(self, ip: int):
        if not (0 <= ip < self.code_len) or ip >= len(self.A):
            return None
        f = self._lenfn.get(self.A[ip])
        if f is None:
            return None
        if isinstance(f, int):
            return f if f > 0 else None
        try:
            n = eval(f, {"A": self.A, "ip": ip})
        except (IndexError, ZeroDivisionError, TypeError):
            return None
        return n if n > 0 else None

    def name(self, op: int) -> str:
        spec = self.ops.get(op)
        return spec.get("name", "op%02X" % op) if spec else "op%02X" % op

    def targets(self, ip: int):
        """Static branch targets of the instruction at ip.

        Entries are either an operand index ("6" -> A[ip+6]) or a length-style
        expression ("$1*5+2") for variable-shape instructions.
        """
        op = self.A[ip]
        spec = self.ops.get(op) or {}
        out = []
        for k in spec.get("targets", []):
            try:
                v = _eval_len(k, self.A, ip) if isinstance(k, str) else self.A[ip + int(k)]
            except (ValueError, IndexError):
                continue
            if 4 <= v < self.code_len:
                out.append(v)
        return out

    def is_stop(self, ip: int) -> bool:
        return self.A[ip] in self.stop

    # -- decoding ----------------------------------------------------
    def linear(self, start: int, limit: int = None, maxn: int = 100000):
        """Sequential decode from `start` until a stop op or an unknown word."""
        ip = start
        n = 0
        while n < maxn:
            if limit is not None and ip >= limit:
                return
            ln = self.length(ip)
            if ln is None:
                return
            yield ip, ln
            if self.is_stop(ip):
                return
            ip += ln
            n += 1

    def body(self, start: int, limit: int = None):
        """Reachable positions from `start` following branches (function CFG)."""
        seen = set()
        stack = [start]
        guard = 0
        while stack and guard < 400000:
            ip = stack.pop()
            guard += 1
            for pos, ln in self.linear(ip, limit):
                if pos in seen:
                    break
                seen.add(pos)
                t = self.targets(pos)
                if self.A[pos] == self.cfg["jmp_op"]:
                    for x in t:
                        stack.append(x)
                    break
                for x in t:
                    if x not in seen:
                        stack.append(x)
                ip = pos + ln
        return seen

    def callers(self, target: int):
        return [q for q in self.positions()
                if self.A[q] == self.cfg["call_op"] and self.A[q + 1] == target]

    def positions(self):
        return sorted(self._ins) if getattr(self, "_ins", None) else []

    # -- operand helpers ---------------------------------------------
    def arg(self, k: int) -> str:
        t, v = self.A[k], self.A[k + 1]
        if t == 0:
            return str(v)
        if t == 1:
            return "v%d" % v
        if t == 2:
            return "v[v%d]" % v
        return "?%d:%d" % (t, v)

    def text_at(self, x: int, pages=None):
        if self.text_base is None or not pages:
            return ""
        o = self.text_base + x * self.text_scale
        offs = [p["off"] for p in pages]
        i = bisect.bisect_right(offs, o) - 1
        if i < 0 or o - offs[i] >= 400:
            return ""
        out = []
        for ln in pages[i]["lines"]:
            t = re.sub(r"^[\x00\^\uff61-\uff9f\u70d9]+", "", ln.get("jp", "")).strip()
            if t:
                out.append(t)
        return " / ".join(out)

    # -- rendering ---------------------------------------------------
    def fmt(self, ip: int, ln: int, pages=None) -> str:
        op = self.A[ip]
        nm = self.name(op)
        a = self.arg

        if op == self.cfg["text_op"]:
            return "%6d: %-10s x=%-6d n=%-3d %s" % (
                ip, nm, self.A[ip + 1], self.A[ip + 2],
                self.text_at(self.A[ip + 1], pages)[:72])
        if nm == "if":
            return "%6d: IF %s %s %s -> %d" % (
                ip, a(ip + 1), OPS_CMP[self.A[ip + 3]], a(ip + 4), self.A[ip + 6])
        if nm == "if_any":
            n = self.A[ip + 1]
            parts = " | ".join(
                "%s %s %s" % (a(ip + 2 + k * 5), OPS_CMP[self.A[ip + 4 + k * 5]],
                              a(ip + 5 + k * 5)) for k in range(n))
            return "%6d: SKIP-IF %s -> %d" % (ip, parts, self.A[ip + n * 5 + 2])
        if nm == "notin":
            n = self.A[ip + 2]
            vals = [self.A[ip + 3 + k * 2 + 1] for k in range(n)]
            return "%6d: NOTIN %s -> %d" % (ip, vals, self.A[ip + 1])
        if nm in ("assign", "add", "sub", "mul", "mod", "div"):
            sym = {"assign": "=", "add": "+=", "sub": "-=", "mul": "*=",
                   "mod": "%=", "div": "//="}[nm]
            return "%6d: %s %s %s" % (ip, a(ip + 1), sym, a(ip + 3))
        if nm == "jmp":
            return "%6d: JMP %d" % (ip, self.A[ip + 1])
        if nm == "call":
            return "%6d: CALL %d" % (ip, self.A[ip + 1])
        if nm == "push":
            n = self.A[ip + 1]
            return "%6d: PUSH %s" % (ip, " ".join(a(ip + 2 + k * 2) for k in range(n)))
        if nm == "ret":
            return "%6d: RET" % ip
        return "%6d: %-10s %s" % (ip, nm, " ".join(str(x) for x in self.A[ip + 1:ip + ln]))


# ---------------------------------------------------------------- analysis

def build_ins_table(vm: VM):
    """Full instruction table by seeded CFG walk. Returns {pos: len}."""
    seeds = [vm.cfg["entry"]] + vm.labels
    for q in range(4, min(vm.code_len, len(vm.A) - 7)):
        pass  # targets discovered during the walk
    ins, queue, i = {}, list(seeds), 0
    while i < len(queue):
        ip = queue[i]
        i += 1
        while 4 <= ip < vm.code_len and ip not in ins:
            ln = vm.length(ip)
            if ln is None:
                break
            ins[ip] = ln
            for t in vm.targets(ip):
                if t not in ins:
                    queue.append(t)
            if vm.is_stop(ip):
                break
            ip += ln
    return ins


def split_functions(vm: VM, ins: dict):
    """Function starts = entry + labels + every branch/call target."""
    starts = {vm.cfg["entry"]}
    starts.update(vm.labels)
    for q, ln in ins.items():
        for t in vm.targets(q):
            starts.add(t)
    starts = sorted(s for s in starts if 4 <= s < vm.code_len)
    return starts


def merged_span(ins: dict) -> int:
    """Dwords covered by the instruction table, overlaps counted once."""
    total, end = 0, -1
    for q in sorted(ins):
        e = q + ins[q]
        if q > end:
            total += e - q
        elif e > end:
            total += e - end
        end = max(end, e)
    return total


def main(argv=None):
    ap = argparse.ArgumentParser(description="disassemble a dword-oriented script VM")
    ap.add_argument("config")
    ap.add_argument("datafile")
    ap.add_argument("--pages", default="", help="pages.json for text resolution")
    ap.add_argument("--list", nargs=2, type=int, metavar=("LO", "HI"))
    ap.add_argument("--funcs", action="store_true")
    ap.add_argument("--callers", type=int)
    ap.add_argument("--body", type=int, help="dump one function body")
    ap.add_argument("--xref", type=int, help="find all uses of variable N")
    ap.add_argument("--texts", action="store_true", help="list every text block")
    ap.add_argument("--dump", help="write instruction table json here")
    a = ap.parse_args(argv)

    cfg = load_vm_cfg(a.config)
    vm = VM(C.read_bytes(a.datafile), cfg)
    pages = C.read_json(a.pages, None) if a.pages else None

    C.hr("VM")
    C.kv("words", len(vm.A))
    C.kv("code words", vm.code_len)
    C.kv("labels", len(vm.labels))
    C.kv("text base", "0x%X" % vm.text_base if vm.text_base else "-")

    ins = build_ins_table(vm)
    vm._ins = ins
    covered = merged_span(ins)
    C.kv("instructions", len(ins))
    C.kv("coverage", "%.1f%%" % (100.0 * covered / max(vm.code_len, 1)))

    if a.dump:
        C.write_json(a.dump, {str(k): v for k, v in ins.items()})
        print("  -> %s" % a.dump)

    if a.list:
        lo, hi = a.list
        if lo not in ins:
            # snap to the nearest instruction boundary at or before `lo`
            cand = [p for p in ins if p <= lo]
            if cand:
                snapped = max(cand)
                print("\n  note: %d is not an instruction boundary, "
                      "snapped to %d" % (lo, snapped))
                lo = snapped
        print()
        ip = lo
        while ip < hi:
            ln = vm.length(ip)
            if ln is None:
                print("%6d: ?? data %s" % (ip, vm.A[ip:ip + 8]))
                break
            print(vm.fmt(ip, ln, pages))
            ip += ln

    if a.funcs:
        starts = split_functions(vm, ins)
        print("\nfunctions: %d" % len(starts))
        for s in starts[:80]:
            n = len(list(vm.linear(s)))
            print("  %6d  len=%-5d %s" % (s, n, vm.name(vm.A[s])))

    if a.callers is not None:
        cs = [q for q in ins if vm.A[q] == cfg["call_op"] and vm.A[q + 1] == a.callers]
        print("\ncallers of %d: %s" % (a.callers, cs or "none"))

    if a.body is not None:
        print()
        for ip in sorted(vm.body(a.body)):
            ln = ins.get(ip) or vm.length(ip)
            if ln:
                print(vm.fmt(ip, ln, pages))

    if a.xref is not None:
        v = a.xref
        print("\nreferences to v%d:" % v)
        for q, ln in sorted(ins.items()):
            for k in range(1, max(ln - 1, 1)):
                if vm.A[q + k] == 1 and vm.A[q + k + 1] == v:
                    print("  %6d  %-10s arg@%d" % (q, vm.name(vm.A[q]), k))
                    break

    if a.texts:
        print()
        n = 0
        for q, ln in sorted(ins.items()):
            if vm.A[q] == cfg["text_op"]:
                n += 1
                print("%6d: x=%-6d %s" % (q, vm.A[q + 1], vm.text_at(vm.A[q + 1], pages)[:70]))
        print("total text blocks: %d" % n)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
