# -*- coding: utf-8 -*-
"""打两种包：

1) 便携版  dist/
       ATRAC3plus转PSP.exe      ← 只留主程序，at3tool / msvcr71 / oma_header 已内置
       bin/ffmpeg.exe           ← 其余东西全进 bin
       bin/psp_at3tool.exe
       bin/msvcr71.dll
       bin/oma_header.bin

2) 单文件版  dist_single/
       ATRAC3plus转PSP.exe      ← 一个文件走天下，ffmpeg 也内置（约 125 MB）
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
FF = os.path.join(ROOT, "tools", "ffmpeg-9.0.2-essentials_build", "bin", "ffmpeg.exe")
ICO = os.path.join(ROOT, "app.ico")
EXE = "ATRAC3plus转PSP"


def pyi(extra, icon=True):
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
           "--onefile", "--windowed", "--name", EXE, "--collect-all", "PIL"]
    if icon and os.path.isfile(ICO):
        cmd += ["--icon", ICO]
    cmd += extra + ["atrac3plus_gui.py"]
    print(">>>", " ".join(cmd))
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode:
        sys.exit(r.returncode)


def add(*files):
    out = []
    for f in files:
        out += ["--add-data", "%s;." % f]
    return out


def main():
    common = [os.path.join(ROOT, "oma_header.bin"),
              os.path.join(ROOT, "psp_at3tool.exe"),
              os.path.join(ROOT, "msvcr71.dll")]

    # ---------- 1) 便携版 ----------
    shutil.rmtree(os.path.join(ROOT, "dist"), ignore_errors=True)
    pyi(["--distpath", "dist", "--workpath", os.path.join("build", "portable")]
        + add(*common, ICO))

    bindir = os.path.join(ROOT, "dist", "bin")
    os.makedirs(bindir, exist_ok=True)
    shutil.copy2(FF, os.path.join(bindir, "ffmpeg.exe"))
    for p in common:
        shutil.copy2(p, os.path.join(bindir, os.path.basename(p)))
    shutil.copy2(os.path.join(ROOT, "README.md"),
                 os.path.join(ROOT, "dist", "README.md"))
    print("\n[便携版] dist/ 完成")

    # ---------- 2) 单文件版 ----------
    shutil.rmtree(os.path.join(ROOT, "dist_single"), ignore_errors=True)
    pyi(["--distpath", "dist_single", "--workpath", os.path.join("build", "single")]
        + add(*common, FF, ICO))
    shutil.copy2(os.path.join(ROOT, "README.md"),
                 os.path.join(ROOT, "dist_single", "README.md"))
    print("\n[单文件版] dist_single/ 完成")

    for d in ("dist", "dist_single"):
        p = os.path.join(ROOT, d, EXE + ".exe")
        print("%-14s %10.1f MiB" % (d, os.path.getsize(p) / 1048576))


if __name__ == "__main__":
    main()
