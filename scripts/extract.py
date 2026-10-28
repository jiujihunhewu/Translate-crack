# -*- coding: utf-8 -*-
"""Generic script-text extractor + page rebuilder.

Two stages:
  1. scan()        -- walk the file, harvest every run of valid two-byte chars
  2. build_pages() -- group runs into dialogue pages using the bytes *between*
                      runs, which is the only reliable framing signal in most
                      engines (fixed-size headers get eaten by false-positive
                      SJIS pairs inside their parameters).

Both are driven by a plain dict config, so the same code works across games.

Usage:
  python extract.py <game.toml|game.json> <data-file> [-o out-dir]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C


# ---------------------------------------------------------------- config

DEFAULT = {
    "encoding": "cp932",
    "lead_xor": 1,
    "region": None,          # [start, end) byte offsets; None = whole file
    "min_chars": 2,
    "line_break": "0000",
    "page_break": ["03000200", "01000200"],
}


def load_cfg(path=None) -> dict:
    cfg = dict(DEFAULT)
    if path:
        p = Path(path)
        if not p.exists():
            raise SystemExit("config not found: %s" % p)
        if p.suffix.lower() in (".json",):
            cfg.update(json.loads(p.read_text(encoding="utf-8")))
        elif p.suffix.lower() in (".toml",):
            import tomllib
            cfg.update(tomllib.loads(p.read_text(encoding="utf-8")))
        else:
            raise SystemExit("config must be .json or .toml")
    return cfg


def region(cfg, size):
    r = cfg.get("region")
    return (int(r[0]), int(r[1])) if r else (0, size)


# ---------------------------------------------------------------- stage 1

def scan(d: bytes, cfg: dict):
    """Return [{'off', 'len', 'jp'}] for every CJK run inside the region."""
    enc = cfg["encoding"]
    xor = int(cfg.get("lead_xor", 0))
    s, e = region(cfg, len(d))
    min_chars = int(cfg.get("min_chars", 2))

    out = []
    i = s
    while i < e - 1:
        if C.is_lead(d[i], enc) and C.decode_pair(d[i], d[i + 1], enc, xor):
            start = i
            buf = []
            while i < e - 1 and C.is_lead(d[i], enc):
                ch = C.decode_pair(d[i], d[i + 1], enc, xor)
                if ch is None:
                    break
                buf.append(ch)
                i += 2
            if len(buf) >= min_chars:
                out.append({"off": start, "len": i - start, "jp": "".join(buf)})
            continue
        i += 1
    return out


# ---------------------------------------------------------------- stage 2

def _runs(d: bytes, cfg: dict):
    """Split the region into alternating ('text'|'gap', offset, payload)."""
    enc = cfg["encoding"]
    xor = int(cfg.get("lead_xor", 0))
    s, e = region(cfg, len(d))
    out = []
    i = s
    while i < e - 1:
        if C.is_lead(d[i], enc) and C.decode_pair(d[i], d[i + 1], enc, xor):
            st = i
            buf = []
            while i < e - 1 and C.is_lead(d[i], enc):
                ch = C.decode_pair(d[i], d[i + 1], enc, xor)
                if ch is None:
                    break
                buf.append(ch)
                i += 2
            out.append(("text", st, "".join(buf)))
        else:
            st = i
            while i < e - 1 and not (C.is_lead(d[i], enc)
                                     and C.decode_pair(d[i], d[i + 1], enc, xor)):
                i += 1
            out.append(("gap", st, d[st:i].hex(" ")))
    return out


def build_pages(d: bytes, cfg: dict):
    """Group runs into pages. A page = one dialogue box / one text screen."""
    rs = _runs(d, cfg)
    nl = bytes.fromhex(cfg.get("line_break", "0000"))
    breaks = [bytes.fromhex(x) for x in cfg.get("page_break", [])]

    pages = []
    cur = {"lines": [], "off": None, "breaks": []}

    def flush(why):
        nonlocal cur
        if cur["lines"]:
            cur["end"] = why
            pages.append(cur)
        cur = {"lines": [], "off": None, "breaks": []}

    for kind, off, s in rs:
        if kind == "text":
            if cur["off"] is None:
                cur["off"] = off
            if not cur["lines"]:
                cur["lines"].append([off, [s]])
            else:
                if cur["lines"][-1][0] is None:
                    cur["lines"][-1][0] = off
                cur["lines"][-1][1].append(s)
        else:
            raw = bytes.fromhex(s)
            hit = next((b for b in breaks if b in raw), None)
            if raw == nl:
                if cur["lines"]:
                    cur["lines"].append([None, []])
            elif hit is not None:
                cur["breaks"].append(raw.hex(" "))
                flush(hit.hex())
            else:
                if cur["lines"] and cur["lines"][-1][1]:
                    cur["lines"].append([None, []])
    flush("end")

    for p in pages:
        p["lines"] = [{"off": o, "jp": "".join(l)} for o, l in p["lines"] if l]
    return pages


# ---------------------------------------------------------------- report

def summarize(strings, pages):
    C.hr("extraction summary")
    C.kv("strings", len(strings))
    C.kv("chars", sum(len(x["jp"]) for x in strings))
    C.kv("bytes", sum(x["len"] for x in strings))
    C.kv("pages", len(pages))
    dist = {}
    for p in pages:
        dist[len(p["lines"])] = dist.get(len(p["lines"]), 0) + 1
    C.kv("lines/page", sorted(dist.items()))
    C.kv("max chars/line",
         max((len(l["jp"]) for p in pages for l in p["lines"]), default=0))
    if strings:
        print("\n  first entries:")
        for x in strings[:4]:
            print("    %08X  len=%-3d  %s" % (x["off"], x["len"], x["jp"][:40]))


def main(argv=None):
    ap = argparse.ArgumentParser(description="extract script text from a VN data file")
    ap.add_argument("config", help="game config (.json/.toml), or '-' for defaults")
    ap.add_argument("datafile")
    ap.add_argument("-o", "--out", default="data")
    ap.add_argument("--no-pages", action="store_true")
    a = ap.parse_args(argv)

    cfg = load_cfg(None if a.config == "-" else a.config)
    d = C.read_bytes(a.datafile)
    strings = scan(d, cfg)
    C.write_json(Path(a.out) / "strings.json", strings)
    pages = []
    if not a.no_pages:
        pages = build_pages(d, cfg)
        C.write_json(Path(a.out) / "pages.json", pages)
    summarize(strings, pages)
    print("\n  -> %s/strings.json%s" % (a.out, "" if a.no_pages else ", pages.json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
