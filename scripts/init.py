# -*- coding: utf-8 -*-
"""Scaffold a working directory for one game.

    python init.py <project-dir> [--title "Game name"] [--script game.id]

Creates the standard layout and a config template:

    <project>/
      orig/     pristine copies -- the ONLY data source, never edit in place
      build/    injected output
      trans/    translation batches (script_*.json)
      data/     intermediate json
      game.json config
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C

TEMPLATE = {
    "title": "",
    "files": {
        "script": "game.id",
        "exe": "game.exe",
    },
    "encoding": "cp932",
    "lead_xor": 1,
    "region": None,
    "min_chars": 2,
    "line_break": "0000",
    "page_break": ["03000200", "01000200"],
    "target": {
        "encoding": "gbk",
        "lead_xor": 1,
        "pad": "a0a1",
        "fullwidth_ascii": True,
        "force_even": True,
        "fix": {"\u266a": "\uff5e", "\u30fb": "\u00b7"},
    },
    "notes": [
        "orig/ must contain the untouched original files",
        "pad must never be 0000 -- 0x00 is the newline control code",
    ],
}

VM_TEMPLATE = {
    "code_len_word": 0,
    "label_offset_word": 1,
    "label_count_word": 2,
    "text_base": None,
    "text_scale": 2,
    "entry": 4,
    "stop_ops": ["0x0F", "0x02", "0x11", "0x25"],
    "call_op": "0x10",
    "jmp_op": "0x0F",
    "cond_ops": {"0x18": "if", "0x1C": "if2", "0x27": "notin", "0x40": "if_any"},
    "text_op": "0x41",
    "opcodes": {
        "0x02": {"len": 1, "name": "ret"},
        "0x03": {"len": 5, "name": "assign"},
        "0x04": {"len": 5, "name": "add"},
        "0x05": {"len": 5, "name": "sub"},
        "0x06": {"len": 5, "name": "mul"},
        "0x07": {"len": 5, "name": "mod"},
        "0x0A": {"len": 3, "name": "set1"},
        "0x0B": {"len": 3, "name": "set0"},
        "0x0F": {"len": 2, "name": "jmp", "targets": [1]},
        "0x10": {"len": 2, "name": "call", "targets": [1]},
        "0x11": {"len": 1, "name": "ret"},
        "0x18": {"len": 7, "name": "if", "targets": [6]},
        "0x25": {"len": 1, "name": "ret"},
        "0x27": {"len": "$2*2+3", "name": "notin", "targets": [1]},
        "0x28": {"len": "$1*2+2", "name": "push"},
        "0x40": {"len": "$1*5+3", "name": "if_any", "targets": ["$1*5+2"]},
        "0x41": {"len": 3, "name": "text"},
    },
}


def main(argv=None):
    ap = argparse.ArgumentParser(description="scaffold a game workspace")
    ap.add_argument("project")
    ap.add_argument("--title", default="")
    ap.add_argument("--script", default="game.id")
    ap.add_argument("--exe", default="game.exe")
    a = ap.parse_args(argv)

    root = Path(a.project)
    for sub in ("orig", "build", "trans", "data"):
        (root / sub).mkdir(parents=True, exist_ok=True)

    g = dict(TEMPLATE)
    g["title"] = a.title or root.name
    g["files"] = {"script": a.script, "exe": a.exe}
    C.write_json(root / "game.json", g)
    C.write_json(root / "vm.json", VM_TEMPLATE)

    readme = (
        "%s\n%s\n\n" % (g["title"], "=" * len(g["title"]))
        + "1. copy the UNTOUCHED original files into orig/\n"
        + "2. edit game.json (run: python scripts/probe.py orig/%s)\n" % a.script
        + "3. python scripts/extract.py game.json orig/%s -o data\n" % a.script
        + "4. translate data/strings.json -> trans/script_001.json\n"
        + "5. python scripts/inject.py game.json orig/%s trans/script_001.json -o build/%s\n"
          % (a.script, a.script)
        + "6. python scripts/verify.py game.json orig/%s build/%s\n" % (a.script, a.script)
        + "7. python scripts/package.py game.json --orig orig --build build --out patch\n"
    )
    (root / "README.txt").write_text(readme, encoding="utf-8")

    C.hr("scaffold: %s" % root.resolve())
    for sub in ("orig", "build", "trans", "data"):
        C.kv(sub + "/", "ready")
    C.kv("config", "game.json, vm.json")
    print("\n  next: put the original %s into %s/orig/" % (a.script, root.name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
