#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成合成的 RIFF .at3 测试样本，用于验证 at3_to_aa3.exe"""
import os
import struct
import sys

GUID = bytes([0xBF, 0xAA, 0x23, 0xE9, 0x58, 0xCB, 0x71, 0x44,
              0xA1, 0x19, 0xFF, 0xFA, 0x01, 0xE4, 0xCE, 0x62])


def make(path, sr=44100, ch=2, frame=744, nframes=100, tag=b"\x00"):
    """WAVEFORMATEXTENSIBLE 52 字节 fmt 块 + data"""
    payload = struct.pack("<HHI", 52 - 16, 0, 0)   # cbSize=36, validbits, chmask
    payload += GUID
    payload += b"\x00\x00"                          # vendorWord
    payload += struct.pack("<H", 0)                 # configWord
    payload += b"\x00" * 8                          # padding
    assert len(payload) == 36, len(payload)

    fmt = struct.pack("<HHIIHH", 0xFFFE, ch, sr, sr * ch * frame // 2048,
                      frame, 16) + payload
    assert len(fmt) == 52, len(fmt)

    data = bytes((i * 37 + 11) & 0xFF for i in range(frame * nframes))
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt
    body += b"data" + struct.pack("<I", len(data)) + data
    out = b"RIFF" + struct.pack("<I", len(body)) + body

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as f:
        f.write(out)
    print("生成 %-40s sr=%d ch=%d frame=%d nframes=%d size=%d"
          % (path, sr, ch, frame, nframes, len(out)))


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else "test_at3"
    d = os.path.abspath(d)
    make(os.path.join(d, "sample128.at3"), 44100, 2, 744, 100)
    make(os.path.join(d, "sample64.at3"), 44100, 2, 376, 200)
    make(os.path.join(d, "mono132.at3"), 44100, 1, 376, 50)
    make(os.path.join(d, "坏文件.at3"), 44100, 2, 744, 30)
    # 故意做一个坏文件（data 长度不是帧长整数倍）
    p = os.path.join(d, "坏文件.at3")
    raw = open(p, "rb").read()
    open(p, "wb").write(raw[:-7])
    print("已故意截断坏文件.at3 用于测试失败分支")
