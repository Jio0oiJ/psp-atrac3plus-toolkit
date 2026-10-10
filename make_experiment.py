#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成一组对照样本，全部拖进 SonicStage 看哪个能导入。
用来定位「换头」到底卡在哪一步：

  A 原样改名        —— 已知可播的文件，纯改名（对照组，必须能导入）
  B 换头            —— A 的音频 + 另一首歌的原厂头（测：头部能否跨文件复用）
  C 最小头          —— A 的音频 + 我最初合成的 106 字节最小头（预期失败）
  D 换头+改ID       —— B 的基础上，把 EA3 块里每首歌不同的 6 个字节也换过来
                      （若 B 失败而 D 成功 → 说明那 6 字节的内容 ID 起作用）

音频用同一个文件，保证唯一变量是头部。
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from at3_to_aa3 import (load_template, build_from_template, build_ea3,
                        OMA_CODECID_ATRAC3P, id3v2_tag_len, EA3_HEADER_SIZE)

REF_DIR = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"),
                       "SonicStage", "Packages", "Optimized Files")


def payload_of(path):
    d = open(path, "rb").read()
    blk = id3v2_tag_len(d)
    return d[blk + EA3_HEADER_SIZE:], blk


def main():
    names = sorted(os.listdir(REF_DIR))
    oma = [n for n in names if n.lower().endswith(".oma")]
    if len(oma) < 2:
        print("需要至少 2 个 .oma 文件")
        return 1
    # 头部来源用第一个，音频来源用最小的那个（省空间）
    src_hdr = os.path.join(REF_DIR, oma[0])
    src_audio = min((os.path.join(REF_DIR, n) for n in oma),
                    key=lambda p: os.path.getsize(p))
    print("头部来源：%s" % os.path.basename(src_hdr))
    print("音频来源：%s" % os.path.basename(src_audio))

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "实验样本")
    os.makedirs(out, exist_ok=True)

    audio, src_blk = payload_of(src_audio)
    d_src = open(src_audio, "rb").read()
    # 从音频来源文件自己的头部里读出参数
    params = struct.unpack_from(">I", d_src, src_blk + 32)[0] & 0xFFFFFF
    channels = (params >> 10) & 7
    sample_rate = [320, 441, 480, 882, 960][(params >> 13) & 7] * 100
    block_align = ((params & 0x3FF) * 8) + 8
    nframes = len(audio) // block_align
    dur = nframes * 2048 / sample_rate
    print("参数：%dHz %dch 帧长%d %d帧 %.1fs" %
          (sample_rate, channels, block_align, nframes, dur))

    title = os.path.splitext(os.path.basename(src_audio))[0]

    # --- A：原样改名（对照）---
    a = os.path.join(out, "A_原样改名_应能导入.aa3")
    with open(a, "wb") as f:
        f.write(d_src)

    # --- B：换头（01 的头 + 14 的音频）---
    tpl, blk = load_template(src_hdr)
    b = os.path.join(out, "B_换头_原厂头+他人音频.aa3")
    blob, _ = build_from_template(tpl, blk, OMA_CODECID_ATRAC3P, channels,
                                  sample_rate, block_align, audio, title, dur)
    with open(b, "wb") as f:
        f.write(blob)

    # --- C：最小头 ---
    c = os.path.join(out, "C_最小头_预期失败.aa3")
    blob, _ = build_ea3(OMA_CODECID_ATRAC3P, channels, sample_rate,
                        block_align, audio)
    with open(c, "wb") as f:
        f.write(blob)

    # --- D：换头 + 把 EA3 块里每首歌不同的 6 字节也换成音频来源的 ---
    tpl2, blk2 = load_template(src_hdr)
    for off in (21, 22, 28, 29, 30, 31):
        tpl2[blk2 + off] = d_src[src_blk + off]
    d = os.path.join(out, "D_换头+改内容ID.aa3")
    blob, _ = build_from_template(tpl2, blk2, OMA_CODECID_ATRAC3P, channels,
                                  sample_rate, block_align, audio, title, dur)
    with open(d, "wb") as f:
        f.write(blob)

    print()
    for p in (a, b, c, d):
        print("  %-40s %d 字节" % (os.path.basename(p), os.path.getsize(p)))
    print()
    print("输出目录：%s" % out)
    print("把这 4 个文件全部拖进 SonicStage，看哪个提示「不能导入」。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
