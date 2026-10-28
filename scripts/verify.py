# -*- coding: utf-8 -*-
"""Verify a patched file against its original.

Checks, in order of how badly they bite:

  1. same size                 -- equal-length injection must not resize
  2. bytes outside text slots  -- must be byte-identical; any drift means the
                                  slot list is wrong and you corrupted code
  3. every slot decodes        -- read the slot back with the target encoding;
                                  count how many yield readable text
  4. no stray NUL in slots     -- a 0x00 pad is the classic "only first line
                                  renders" bug

Exit code 0 when clean, 1 when any structural check fails.

Usage:
  python verify.py <config> <orig-file> <patched-file> [--trans trans.json]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C
import extract as X


def decode_slot(raw: bytes, enc: str, xor: int):
    out = []
    i = 0
    while i + 1 < len(raw):
        b1, b2 = raw[i], raw[i + 1]
        if b1 == 0 and b2 == 0:
            out.append("\n")
            i += 2
            continue
        ch = C.decode_pair(b1, b2, enc, xor)
        if ch is None:
            a = b1 ^ xor
            try:
                ch = bytes([a, b2]).decode(enc)
            except (UnicodeDecodeError, LookupError):
                return None
        out.append(ch)
        i += 2
    return "".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="verify equal-length injection")
    ap.add_argument("config")
    ap.add_argument("orig")
    ap.add_argument("patched")
    ap.add_argument("--trans", default="")
    a = ap.parse_args(argv)

    cfg = X.load_cfg(a.config)
    target = cfg.get("target", {})
    tenc = target.get("encoding", "gbk")
    txor = int(target.get("lead_xor", cfg.get("lead_xor", 0)))

    old = C.read_bytes(a.orig)
    new = C.read_bytes(a.patched)
    slots = X.scan(old, cfg)

    C.hr("verify")
    C.kv("original", "%d bytes  %s" % (len(old), C.md5_file(a.orig)))
    C.kv("patched", "%d bytes  %s" % (len(new), C.md5_file(a.patched)))

    ok = True
    if len(old) != len(new):
        C.kv("SIZE", "MISMATCH  (%+d bytes)" % (len(new) - len(old)))
        ok = False
    else:
        C.kv("size", "same")

    # --- bytes outside the text slots -------------------------------
    mask = bytearray(len(old))
    for s in slots:
        for i in range(s["off"], s["off"] + s["len"]):
            mask[i] = 1
    drift = [i for i in range(len(old)) if not mask[i] and old[i] != new[i]]
    if drift:
        C.kv("non-text drift", "%d bytes differ  (first @0x%X)" % (len(drift), drift[0]))
        ok = False
    else:
        C.kv("non-text drift", "none")

    # --- slot readability -------------------------------------------
    readable = empty = bad = 0
    bad_samples = []
    for s in slots:
        raw = new[s["off"]:s["off"] + s["len"]]
        if not raw.strip(b"\x00"):
            empty += 1
            continue
        if b"\x00\x00" in raw[:2] or raw.count(0) > 0:
            pass
        txt = decode_slot(raw, tenc, txor)
        if txt is None:
            bad += 1
            if len(bad_samples) < 8:
                bad_samples.append((s["off"], raw[:16].hex(" ")))
        else:
            readable += 1

    C.kv("slots", len(slots))
    C.kv("decodable", readable)
    C.kv("undecodable", bad)
    if bad_samples:
        for off, hexs in bad_samples:
            print("    %08X  %s" % (off, hexs))

    # --- padding sanity ---------------------------------------------
    pad = bytes.fromhex(target.get("pad", "a0a1"))
    nul_in_slot = 0
    for s in slots:
        raw = new[s["off"]:s["off"] + s["len"]]
        if raw.endswith(b"\x00") or b"\x00\x00" in raw[2:]:
            nul_in_slot += 1
    if nul_in_slot:
        C.kv("slots with NUL pad", "%d  <-- check the pad byte" % nul_in_slot)

    if a.trans:
        tr = C.read_json(a.trans, {})
        hit = 0
        for s in slots:
            k = "%06X" % s["off"]
            if k in tr or k.lower() in tr or s["jp"] in tr:
                hit += 1
        C.kv("translation coverage", "%d / %d  (%.1f%%)"
             % (hit, len(slots), 100.0 * hit / max(len(slots), 1)))

    C.hr("result: %s" % ("OK" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
