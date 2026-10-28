# -*- coding: utf-8 -*-
"""Assemble a distributable patch folder.

Layout produced:

    patch/
      files/            translated game files (copied into the game dir)
      backup/           pristine originals (used by the uninstaller)
      install.bat/.sh   installer
      uninstall.bat/.sh uninstaller
      README.txt        install notes + md5 manifest
      manifest.json     machine-readable manifest

The backup copy is not optional: a patch without a rollback path is a trap.

Usage:
  python package.py <config> --orig orig/ --build build/ --out patch/
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C

BAT_INSTALL = """@echo off
chcp 936 >nul
setlocal
cd /d "%~dp0"
echo ============================================
echo   {title}  --  install
echo ============================================
if not exist "files\\{main}" goto nofiles
if not exist "backup\\{main}" goto nobak
if not exist "..\\{main}" goto nogame
echo [1/2] copying translated files ...
{cmds}
echo [2/2] done.  Run {main} to play.
echo To restore the original, run uninstall.bat
pause
exit /b 0
:nofiles
echo [error] patch incomplete: files\\{main} missing
pause
exit /b 1
:nobak
echo [error] patch incomplete: backup\\ missing, cannot roll back
pause
exit /b 1
:nogame
echo [error] put this folder next to {main} and run again
pause
exit /b 1
"""

BAT_UNINSTALL = """@echo off
chcp 936 >nul
setlocal
cd /d "%~dp0"
echo restoring originals ...
{cmds}
echo done.
pause
"""

SH_INSTALL = """#!/bin/sh
set -e
cd "$(dirname "$0")"
echo "[1/2] copying translated files ..."
{cmds}
echo "[2/2] done. Run {main} to play."
echo "To restore: sh uninstall.sh"
"""

SH_UNINSTALL = """#!/bin/sh
set -e
cd "$(dirname "$0")"
echo "restoring originals ..."
{cmds}
echo "done."
"""


def main(argv=None):
    ap = argparse.ArgumentParser(description="build a patch package folder")
    ap.add_argument("config")
    ap.add_argument("--orig", required=True, help="dir with pristine files")
    ap.add_argument("--build", required=True, help="dir with translated files")
    ap.add_argument("--out", default="patch")
    ap.add_argument("--title", default="")
    a = ap.parse_args(argv)

    cfg = C.read_json(a.config, {})
    files = cfg.get("files") or {}
    if not files:
        raise SystemExit("config needs a 'files' map, e.g. {\"script\": \"game.id\"}")

    names = [v for v in files.values() if isinstance(v, str)]
    main_file = names[0] if names else "game.exe"
    title = a.title or cfg.get("title") or "Translation patch"

    out = Path(a.out)
    if out.exists():
        shutil.rmtree(out)
    (out / "files").mkdir(parents=True)
    (out / "backup").mkdir(parents=True)

    manifest = {"title": title, "files": []}
    for name in names:
        src_new = Path(a.build) / name
        src_old = Path(a.orig) / name
        if not src_new.exists():
            raise SystemExit("missing built file: %s" % src_new)
        if not src_old.exists():
            raise SystemExit("missing original file: %s" % src_old)
        shutil.copy2(src_new, out / "files" / name)
        shutil.copy2(src_old, out / "backup" / name)
        manifest["files"].append({
            "name": name,
            "patched": {"size": (out / "files" / name).stat().st_size,
                        "md5": C.md5_file(out / "files" / name)},
            "original": {"size": (out / "backup" / name).stat().st_size,
                         "md5": C.md5_file(out / "backup" / name)},
        })

    win = "\n".join('copy /y "files\\%s" "..\\%s" >nul' % (n, n) for n in names)
    win_u = "\n".join('copy /y "backup\\%s" "..\\%s" >nul' % (n, n) for n in names)
    nix = "\n".join('cp -f "files/%s" "../%s"' % (n, n) for n in names)
    nix_u = "\n".join('cp -f "backup/%s" "../%s"' % (n, n) for n in names)

    def w(name, text, mode=None):
        p = out / name
        raw = text.replace("\n", "\r\n") if name.endswith(".bat") else text
        enc = "gbk" if name.endswith(".bat") else "utf-8"
        with open(str(p), "wb") as f:
            f.write(raw.encode(enc, "replace"))
        if mode:
            p.chmod(mode)

    w("install.bat", BAT_INSTALL.format(title=title, main=main_file, cmds=win))
    w("uninstall.bat", BAT_UNINSTALL.format(cmds=win_u))
    w("install.sh", SH_INSTALL.format(main=main_file, cmds=nix), 0o755)
    w("uninstall.sh", SH_UNINSTALL.format(cmds=nix_u), 0o755)

    lines = [title, "=" * 56, "",
             "[install]",
             "  1. copy this whole folder into the game directory",
             "  2. run install.bat (Windows) or sh install.sh (Linux/macOS)",
             "  3. start %s" % main_file, "",
             "  rollback: run uninstall.bat / sh uninstall.sh", "",
             "[layout]",
             "  files/    translated files",
             "  backup/   pristine originals -- keep this folder", "",
             "[checksums]"]
    for f in manifest["files"]:
        lines.append("  %-14s patched  %9d  %s"
                     % (f["name"], f["patched"]["size"], f["patched"]["md5"]))
        lines.append("  %-14s original %9d  %s"
                     % (f["name"], f["original"]["size"], f["original"]["md5"]))
    notes = cfg.get("notes")
    if notes:
        lines += ["", "[notes]"] + ["  " + str(x) for x in notes]
    w("README.txt", "\n".join(lines) + "\n")

    C.write_json(out / "manifest.json", manifest)

    C.hr("package")
    C.kv("output", str(out.resolve()))
    for f in manifest["files"]:
        C.kv(f["name"], "patched %d  %s" % (f["patched"]["size"], f["patched"]["md5"][:16]))
    C.kv("installer", "install.bat / install.sh")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
