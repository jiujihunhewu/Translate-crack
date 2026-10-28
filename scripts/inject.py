# -*- coding: utf-8 -*-
"""In-place (equal-length) text injection.

The whole point of this module is the four traps below. Every one of them was
found the hard way; each silently corrupts output if ignored.

TRAP 1 -- padding byte
    Never pad with 0x00. In most script formats 0x00 is the newline control
    code. Padding with it injects extra line breaks and pushes the following
    lines out of the text box (symptom: only the first line renders, sentences
    appear cut off mid-way). Pad with a fullwidth space instead -- for GBK the
    stored form is A0 A1, for Big5 it is A1 40.

TRAP 2 -- halfwidth ASCII
    If the engine decodes by fixed 2-byte stride, a 1-byte halfwidth char
    desynchronises everything after it (classic symptom: 'A' renders as '@',
    '1' as '0'). Fold ASCII to fullwidth before encoding.

TRAP 3 -- odd byte length
    Some renderers walk byte pairs; an odd-length replacement shifts the rest
    of the page. Force even length, or accept a trailing pad unit.

TRAP 4 -- over-budget translations
    Equal-length injection means the translation must fit the original slot.
    Never truncate silently: record the overflow, fall back to the original
    text for that slot, and report it so the translator can shorten it.

Usage:
  python inject.py <config> <src-data> <trans.json> -o build/ww4r.id
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C
import extract as X

# Characters that do not exist in the target charset; map them to something
# visually close instead of losing the line.
DEFAULT_FIX = {
    "\u266a": "\uff5e",   # musical note -> wave dash
    "\u266b": "\uff5e",
    "\u30fb": "\u00b7",   # katakana middle dot -> middle dot
    "\u2026": "\u2026",
}


class Encoder:
    """Target-charset encoder with per-char fallback."""

    def __init__(self, encoding="gbk", fix=None, xor=1, pad="a0a1",
                 fullwidth_ascii=True, force_even=True):
        self.encoding = encoding
        self.fix = dict(DEFAULT_FIX)
        if fix:
            self.fix.update(fix)
        self.xor = int(xor)
        self.pad = bytes.fromhex(pad)
        self.fullwidth_ascii = fullwidth_ascii
        self.force_even = force_even

    # -- single char -------------------------------------------------
    def ch(self, c: str):
        c = self.fix.get(c, c)
        try:
            b = c.encode(self.encoding)
        except (UnicodeEncodeError, LookupError):
            return None
        return b

    # -- whole string -------------------------------------------------
    def units(self, s: str):
        """str -> list of per-char byte blobs (None = unmappable)."""
        return [self.ch(c) for c in s]

    def store(self, units) -> bytes:
        """Apply the lead-byte xor to produce on-disk bytes."""
        out = bytearray()
        for u in units:
            if u is None:
                continue
            if self.xor and len(u) == 2 and u[0] >= 0x80:
                out.append(u[0] ^ self.xor)
                out.append(u[1])
            else:
                out += u
        return bytes(out)

    def encode(self, s: str) -> bytes:
        return self.store(self.units(s))

    # -- candidate list, best first -----------------------------------
    def candidates(self, s: str):
        """Yield progressively more conservative encodings of `s`."""
        if self.fullwidth_ascii:
            fw = C.to_fullwidth(s)
            if fw != s:
                yield "fullwidth", self.encode(fw)
                alt = C.fullwidth_last(s)
                if alt and alt != fw:
                    yield "fullwidth-last", self.encode(alt)
        yield "asis", self.encode(s)


def load_trans(path):
    t = C.read_json(path, {})
    if not isinstance(t, dict):
        raise SystemExit("translation file must be a JSON object")
    return t


def lookup(trans, item):
    k = "%06X" % item["off"]
    for key in (k, k.lower(), item["jp"]):
        if key in trans:
            v = trans[key]
            if isinstance(v, dict):
                return v.get("zh") or v.get("cn")
            return v
    return None


def build(src: bytes, strings, trans, enc: Encoder, verbose=True):
    buf = bytearray(src)
    stats = {"translated": 0, "kept": 0, "over": 0, "raw_kept": 0,
             "ascii_kept": 0, "odd": 0, "missing": 0}
    over = []

    for it in strings:
        o, ln, jp = it["off"], it["len"], it["jp"]
        orig = src[o:o + ln]
        zh = lookup(trans, it)
        blob = None

        if zh is not None:
            fw_needed = enc.fullwidth_ascii and C.to_fullwidth(zh) != zh
            for tag, cand in enc.candidates(zh):
                if len(cand) <= ln:
                    blob = cand
                    # only meaningful when the line actually contained ASCII
                    if tag == "asis" and fw_needed:
                        stats["ascii_kept"] += 1
                    break
            if blob is not None and enc.force_even and len(blob) % 2:
                stats["odd"] += 1
                if len(blob) < ln:
                    blob += enc.pad[:1] if len(blob) % 2 else b""
            if blob is None:
                stats["over"] += 1
                over.append({"off": o, "need": len(enc.encode(zh)),
                             "budget": ln, "jp": jp, "zh": zh})

        if blob is None:
            # Fall back: transliterate the Japanese into the target charset
            # char-by-char; chars that cannot be mapped keep their original
            # on-disk bytes (so nothing turns into mojibake).
            out = bytearray()
            i = 0
            for u in enc.units(jp):
                if u is None:
                    out += orig[i:i + 2]
                    i += 2
                    stats["raw_kept"] += 1
                else:
                    out += enc.store([u])
                    i += len(u)
            blob = bytes(out[:ln])
            if zh is None:
                stats["kept"] += 1
            else:
                stats["missing"] += 1

        need = ln - len(blob)
        if need > 0:
            blob += (enc.pad * (need // len(enc.pad) + 1))[:need]
        buf[o:o + ln] = blob
        if zh is not None and blob is not None:
            stats["translated"] += 1

    if verbose:
        C.hr("injection report")
        C.kv("translated", stats["translated"])
        C.kv("kept (source)", stats["kept"])
        C.kv("over budget", stats["over"])
        C.kv("raw bytes kept", stats["raw_kept"])
        C.kv("ascii kept", stats["ascii_kept"])
        C.kv("odd length", "%d  (should be 0)" % stats["odd"])
        for x in over[:12]:
            print("    %08X [%d > %d] %s -> %s"
                  % (x["off"], x["need"], x["budget"], x["jp"], x["zh"]))
    return bytes(buf), stats, over


def main(argv=None):
    ap = argparse.ArgumentParser(description="inject translations, equal length")
    ap.add_argument("config")
    ap.add_argument("src")
    ap.add_argument("trans")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--report", default="")
    a = ap.parse_args(argv)

    cfg = X.load_cfg(a.config)
    target = cfg.get("target", {})
    enc = Encoder(
        encoding=target.get("encoding", "gbk"),
        fix=target.get("fix"),
        xor=target.get("lead_xor", cfg.get("lead_xor", 0)),
        pad=target.get("pad", "a0a1"),
        fullwidth_ascii=target.get("fullwidth_ascii", True),
        force_even=target.get("force_even", True),
    )

    src = C.read_bytes(a.src)
    strings = X.scan(src, cfg)
    trans = load_trans(a.trans)
    out, stats, over = build(src, strings, trans, enc)
    C.write_bytes(a.out, out)

    print("\n  -> %s  (%d bytes, md5 %s)"
          % (a.out, len(out), C.md5_file(a.out)))
    if a.report:
        C.write_json(a.report, {"stats": stats, "overflow": over})
    return 0 if stats["over"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
