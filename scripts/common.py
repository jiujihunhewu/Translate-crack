# -*- coding: utf-8 -*-
"""Shared helpers: byte IO, CJK encoding probing, fullwidth folding, width-aware wrap.

Pure standard library. No platform assumptions (pathlib everywhere).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------- byte IO

def read_bytes(p) -> bytes:
    with open(str(p), "rb") as f:
        return f.read()


def write_bytes(p, data: bytes) -> None:
    Path(str(p)).parent.mkdir(parents=True, exist_ok=True)
    with open(str(p), "wb") as f:
        f.write(data)


def read_json(p, default=None):
    p = Path(str(p))
    if not p.exists():
        return default
    with open(str(p), encoding="utf-8") as f:
        return json.load(f)


def write_json(p, obj) -> None:
    p = Path(str(p))
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(str(p), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def md5_file(p, chunk=1 << 20) -> str:
    h = hashlib.md5()
    with open(str(p), "rb") as f:
        for blk in iter(lambda: f.read(chunk), b""):
            h.update(blk)
    return h.hexdigest()


def u32(data: bytes, count: int = None):
    """Unpack little-endian uint32 array (truncating a trailing partial word)."""
    import struct
    n = len(data) // 4 if count is None else count
    return list(struct.unpack("<%dI" % n, data[: n * 4]))


# ---------------------------------------------------------------- CJK probe
# Character ranges that appear in real dialogue. Used to reject false
# positives when a byte pair happens to be a valid two-byte sequence.
TEXT_RANGES = (
    (0x3000, 0x303F),   # CJK punctuation
    (0x3040, 0x309F),   # hiragana
    (0x30A0, 0x30FF),   # katakana
    (0x4E00, 0x9FFF),   # CJK unified ideographs
    (0x3400, 0x4DBF),   # CJK ext A
    (0xFF01, 0xFF60),   # fullwidth forms
    (0xFF61, 0xFF9F),   # halfwidth katakana
    (0x2010, 0x2027),   # dashes / quotes
    (0x2100, 0x21FF),   # letterlike
    (0x2460, 0x24FF),   # circled numbers
    (0x2500, 0x25FF),   # box drawing
    (0x2600, 0x26FF),   # misc symbols
    (0x0391, 0x03C9),   # greek
    (0x0410, 0x044F),   # cyrillic
)

# Byte ranges that can start a two-byte character, in *stored* form.
# Deliberately wider than the canonical ranges: a lead-byte xor of 1 shifts
# 0x81->0x80 and 0xFC->0xFD, and missing those two bytes splits every long
# run at the first such character. decode_pair() rejects the false positives,
# so the only cost of the extra width is a few wasted iterations.
LEAD_RANGES = {
    "cp932": ((0x80, 0x9F), (0xE0, 0xFD)),
    "shift_jis": ((0x80, 0x9F), (0xE0, 0xFD)),
    "gbk": ((0x80, 0xFF),),
    "gb18030": ((0x80, 0xFF),),
    "big5": ((0x80, 0xFF),),
    "euc-kr": ((0x80, 0xFF),),
    "cp949": ((0x80, 0xFF),),
}


def in_ranges(code: int, ranges=TEXT_RANGES) -> bool:
    for lo, hi in ranges:
        if lo <= code <= hi:
            return True
    return False


def lead_ranges(encoding: str):
    e = encoding.lower()
    for k, v in LEAD_RANGES.items():
        if e == k:
            return v
    return ((0x81, 0xFE),)


def is_lead(b: int, encoding: str = "cp932") -> bool:
    for lo, hi in lead_ranges(encoding):
        if lo <= b <= hi:
            return True
    return False


def decode_pair(b1: int, b2: int, encoding: str = "cp932", xor: int = 0):
    """Decode one stored two-byte char. Returns str or None.

    `xor` is applied to the lead byte only -- the classic "lead ^ 1" obfuscation
    where the engine flips bit 0 back before handing bytes to the text renderer.
    """
    a = b1 ^ xor
    try:
        ch = bytes([a, b2]).decode(encoding)
    except (UnicodeDecodeError, LookupError):
        return None
    if len(ch) != 1:
        return None
    return ch if in_ranges(ord(ch)) else None


# ---------------------------------------------------------------- fullwidth

_FW_ASCII = {i: i + 0xFEE0 for i in range(0x21, 0x7F)}


def to_fullwidth(s: str) -> str:
    """Fold halfwidth ASCII to fullwidth so every char is 2 bytes in GBK/Big5.

    Needed when the engine decodes by fixed 2-byte stride: a 1-byte halfwidth
    char desynchronises every following character.
    """
    out = []
    for c in s:
        o = ord(c)
        if o in _FW_ASCII:
            out.append(chr(_FW_ASCII[o]))
        elif o == 0x20:
            out.append("\u3000")
        else:
            out.append(c)
    return "".join(out)


def fullwidth_last(s: str):
    """Fullwidth-fold only the last halfwidth char (length +1). Parity fixer."""
    for i in range(len(s) - 1, -1, -1):
        o = ord(s[i])
        if o in _FW_ASCII:
            return s[:i] + chr(_FW_ASCII[o]) + s[i + 1:]
        if o == 0x20:
            return s[:i] + "\u3000" + s[i + 1:]
    return None


# ---------------------------------------------------------------- wrapping
# East-Asian width table for the chars that actually show up in dialogue.
# Ambiguous chars are treated as narrow (matches most Japanese VN renderers).

def char_width(ch: str) -> int:
    o = ord(ch)
    if o < 0x1100:
        return 1
    if o in (0x2018, 0x2019, 0x201C, 0x201D, 0x2026, 0x2014):
        return 2
    if (0x1100 <= o <= 0x115F or 0x2E80 <= o <= 0xA4CF
            or 0xAC00 <= o <= 0xD7A3 or 0xF900 <= o <= 0xFAFF
            or 0xFE30 <= o <= 0xFE6F or 0xFF00 <= o <= 0xFF60
            or 0xFFE0 <= o <= 0xFFE6 or 0x20000 <= o <= 0x3FFFD):
        return 2
    return 1


def wrap_width(s: str, limit: int):
    """Greedy wrap by display width. Prefers breaking after CJK punctuation."""
    NO_LEAD = "、。，．！？）」』】〕｝〉》”’…‥ー～ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮ"
    lines, cur, w = [], [], 0
    for ch in s:
        cw = char_width(ch)
        if w + cw > limit and cur:
            cut = len(cur)
            for back in range(1, min(6, len(cur)) + 1):
                if cur[-back] in "、。，．！？ ":
                    cut = len(cur) - back + 1
                    break
            lines.append("".join(cur[:cut]))
            cur = cur[cut:]
            w = sum(char_width(c) for c in cur)
        cur.append(ch)
        w += cw
    if cur:
        lines.append("".join(cur))
    # a line must not start with closing punctuation
    for i in range(1, len(lines)):
        while lines[i] and lines[i][0] in NO_LEAD and len(lines[i - 1]) > 1:
            lines[i] = lines[i - 1][-1] + lines[i]
            lines[i - 1] = lines[i - 1][:-1]
    return lines


# ---------------------------------------------------------------- console

def hr(title: str = "") -> None:
    line = "=" * 66
    print(line)
    if title:
        print(title)
        print(line)


def kv(k: str, v, width: int = 22) -> None:
    print("  %-*s %s" % (width, k, v))


def which(cmd: str):
    """Portable `which`. Returns path or None."""
    from shutil import which as _w
    return _w(cmd)


def default_python() -> str:
    return sys.executable
