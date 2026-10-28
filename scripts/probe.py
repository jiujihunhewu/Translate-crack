# -*- coding: utf-8 -*-
"""Blind reconnaissance: guess encoding, lead-byte xor, text region, page breaks.

Run this first on any unknown data file. It scores every (encoding, xor)
combination by how many *plausible dialogue characters* it recovers, then
reports where the text region starts and ends, plus the most common 4-byte
separator patterns found between text runs (those are usually the page break).

Usage:
  python probe.py <datafile> [--top 5]
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C

ENCODINGS = ["cp932", "gbk", "big5", "euc-kr", "gb18030", "utf-16-le"]


def score(data: bytes, enc: str, xor: int, sample=None):
    """(valid_chars, valid_pairs, runs, kana) for one hypothesis.

    `kana` is the decisive signal for Japanese scripts: a wide-lead Chinese
    encoding (gb18030, gbk) happily decodes kana bytes into hanzi, so raw
    "valid char" counts favour the *wider* charset. Counting hiragana and
    katakana instead pins the answer immediately -- real dialogue is full of
    them, and no Chinese encoding maps them back.
    """
    if enc == "utf-16-le":
        try:
            s = data.decode("utf-16-le", "ignore")
        except LookupError:
            return 0, 0, 0, 0
        good = kana = 0
        for ch in s:
            o = ord(ch)
            if C.in_ranges(o):
                good += 1
                if 0x3040 <= o <= 0x30FF:
                    kana += 1
        return good, good, 1 if good else 0, kana

    n = len(data) if sample is None else min(len(data), sample)
    i, good, pairs, runs, kana = 0, 0, 0, 0, 0
    in_run = False
    while i < n - 1:
        if C.is_lead(data[i], enc):
            pairs += 1
            ch = C.decode_pair(data[i], data[i + 1], enc, xor)
            if ch:
                good += 1
                if 0x3040 <= ord(ch) <= 0x30FF:
                    kana += 1
                if not in_run:
                    runs += 1
                    in_run = True
                i += 2
                continue
        in_run = False
        i += 1
    return good, pairs, runs, kana


def find_region(data: bytes, enc: str, xor: int, min_run=4):
    """First and last offset of a plausible text region.

    Walks forward to the first run of `min_run` valid chars, then rewinds to
    the end of the preceding gap so the reported start is the real block start
    rather than the middle of the first line.
    """
    n = len(data)
    first = last = None
    i = 0
    run = 0
    start = 0
    while i < n - 1:
        if C.is_lead(data[i], enc) and C.decode_pair(data[i], data[i + 1], enc, xor):
            if run == 0:
                start = i
            run += 1
            if run >= min_run:
                if first is None:
                    # Round down: the true block start may sit a couple of
                    # bytes before the first long run. Starting early is safe
                    # (the scanner skips non-text), starting late loses text.
                    first = max(0, (start - 4) & ~3)
                last = i + 2
            i += 2
            continue
        run = 0
        i += 1
    return first, last


def find_breaks(data: bytes, enc: str, xor: int, limit=200):
    """Most frequent 4-byte gap patterns between text runs."""
    n = len(data)
    gaps = Counter()
    i, last_end = 0, None
    while i < n - 1:
        if C.is_lead(data[i], enc) and C.decode_pair(data[i], data[i + 1], enc, xor):
            if last_end is not None and i - last_end >= 4:
                gaps[data[last_end:i][:4].hex(" ")] += 1
            i += 2
            last_end = i
            continue
        i += 1
    return gaps.most_common(limit)


def main(argv=None):
    ap = argparse.ArgumentParser(description="guess encoding/region/page breaks")
    ap.add_argument("datafile")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--sample", type=int, default=4 << 20,
                    help="bytes to sample for scoring (default 4 MiB)")
    a = ap.parse_args(argv)

    data = C.read_bytes(a.datafile)
    C.hr("probe: %s" % Path(a.datafile).name)
    C.kv("size", "%d bytes" % len(data))

    rows = []
    for enc in ENCODINGS:
        for xor in (0, 1):
            good, pairs, runs, kana = score(data, enc, xor, a.sample)
            if pairs == 0 and enc != "utf-16-le":
                continue
            prec = good / pairs if pairs else 0.0
            rows.append((good, prec, enc, xor, pairs, runs, kana))
    if not rows:
        print("\n  no plausible CJK text found -- file may be compressed or encrypted")
        return 1

    # Japanese script? then kana count decides. Otherwise fall back to
    # precision, then volume.
    best_kana = max(r[6] for r in rows)
    if best_kana >= 50:
        rows.sort(key=lambda r: (r[6], r[1], r[0]), reverse=True)
        why = "kana count"
    else:
        rows.sort(key=lambda r: (r[1], r[0]), reverse=True)
        why = "precision"

    print("\n  %-10s %-5s %10s %8s %10s %10s" %
          ("encoding", "xor", "chars", "prec", "runs", "kana"))
    for good, prec, enc, xor, pairs, runs, kana in rows[: a.top]:
        print("  %-10s %-5d %10d %7.1f%% %10d %10d"
              % (enc, xor, good, prec * 100, runs, kana))
    print("\n  ranked by: %s" % why)

    good, prec, enc, xor, pairs, runs, kana = rows[0]
    C.hr("best hypothesis")
    C.kv("encoding", enc)
    C.kv("lead_xor", xor)
    C.kv("valid chars", good)
    C.kv("precision", "%.1f%%" % (prec * 100))
    C.kv("kana", kana)

    first, last = find_region(data, enc, xor)
    if first is not None:
        C.kv("text region", "[0x%X, 0x%X)  (%d bytes)" % (first, last, last - first))
        print("    region: [%d, %d]" % (first, last))

    gaps = find_breaks(data, enc, xor)
    if gaps:
        C.hr("candidate separators (gap bytes between text runs)")
        for pat, cnt in gaps[:8]:
            print("  %-16s %6d" % (pat, cnt))

    print("\nSuggested config fragment:")
    print('  "encoding": "%s", "lead_xor": %d, "region": [%d, %d],'
          % (enc, xor, first or 0, last or len(data)))
    if gaps:
        print('  "page_break": ["%s"], "line_break": "%s"'
              % (gaps[0][0].replace(" ", ""), "0000"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
