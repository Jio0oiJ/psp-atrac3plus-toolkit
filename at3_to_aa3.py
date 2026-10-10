#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
at3 → aa3 换壳器（支持拖拽多个文件）

把 at3tool 产出的 RIFF (.at3) 封装成 PSP 音乐播放器认的 EA3 容器 (.aa3 / .oma)。
只换容器不重编码，音质零损失。

用法：
    把一个或多个 .at3 文件拖到本 exe 图标上
    或在命令行：at3_to_aa3.exe a.at3 b.at3 c.at3 [--keep]

行为：
    - 在**同目录**生成同名 .aa3（a.at3 -> a.aa3）
    - 转换成功且校验通过后，**删除原始 .at3**
    - 加 --keep 则保留原文件（建议第一次先这样跑，确认无误再正式用）
    - 任何一步失败都不会删除原文件

EA3 头部各字节的取值依据 ffmpeg 的 OMA 复用器 libavformat/omaenc.c
（oma_write_header）与解复用器 omadec.c 的校验逻辑，非猜测。
"""
import io
import os
import struct
import sys

ATRAC3P_GUID = bytes([0xBF, 0xAA, 0x23, 0xE9, 0x58, 0xCB, 0x71, 0x44,
                      0xA1, 0x19, 0xFF, 0xFA, 0x01, 0xE4, 0xCE, 0x62])
ATRAC3_WFORMATTAG = 0x0270

EA3_HEADER_SIZE = 96
ID3V2_HEADER_SIZE = 10
# 注意大小写！ffmpeg libavformat/oma.h: #define ID3v2_EA3_MAGIC "ea3"
# ID3v2 标签头的魔数是**小写** ea3，而后面第 10 字节起的 EA3 块才是大写 "EA3"。
# ff_id3v2_match() 是逐字节 == 比较、区分大小写，写成大写会导致整个文件被判为非法。
ID3V2_EA3_MAGIC = b"ea3"
OMA_CODECID_ATRAC3 = 0
OMA_CODECID_ATRAC3P = 1
# ff_oma_srate_tab = { 320, 441, 480, 882, 960, 0 }，单位 100Hz
OMA_SRATE_TAB = [320, 441, 480, 882, 960]

# ATRAC3plus 立体声 帧长 -> 码率，仅用于显示
FRAME_TO_KBPS = {
    280: 48, 376: 64, 560: 96, 744: 128, 936: 160,
    1120: 192, 1488: 256, 1864: 320, 2048: 352,
}

HEADER_TOTAL = ID3V2_HEADER_SIZE + EA3_HEADER_SIZE   # 106

# 【关键】索尼所有原厂 OMA 的 ID3v2 标签长度都是这个数的整数倍：
#   无封面 3072（=1024×3）      带封面 150528（=1024×147）
# 实测 32 个原厂文件，无一例外。
# 我们自己插帧后长度不再对齐，PSP 会出现「封面能显示、声音放不出来」——
# 它按对齐后的长度推算音频起始偏移，于是从错误位置开始解码，帧同步失败。
# 所以插完帧必须把标签补回 ID3_PAD_UNIT 的整数倍。
ID3_PAD_UNIT = 1024

# ---- 专辑封面 ----
# 索尼不用标准 APIC，而是塞进两个 **GEOB** 帧（实测自 SonicStage 转出的 .OMA）：
#   OMG_FENCA1  主封面   image/jpeg
#   OMG_TDFCA   缩略图位 image/jpeg —— 索尼实测填的是**同一张图**（md5 一致）
# 规格：640x640、约 73KB，写两份 → 头部从 3168 涨到约 150624 字节。
# 全部 8 首同专辑的曲子共用同一张图，md5 完全相同。
COVER_MAX_SIDE = 640
COVER_MAX_BYTES = 80 * 1024
COVER_MIME = "image/jpeg"
COVER_PTYPE = 3          # ID3v2 APIC picture type: $03 = 封面(front cover)
SONY_COVER_MAIN = "OMG_FENCA1"
SONY_COVER_THUMB = "OMG_TDFCA"
SONY_BKLSI = "OMG_BKLSI"
# 自动查找时的候选文件名（小写比较）
COVER_NAMES = ("cover.jpg", "cover.jpeg", "cover.png", "cover.bmp",
               "folder.jpg", "folder.jpeg", "album.jpg", "front.jpg",
               "artwork.jpg")
# 同目录里没有图片时，去这些音频文件的**内嵌封面**里抠（按此顺序）
AUDIO_EXTS = (".mp3", ".flac", ".m4a", ".mp4", ".aac", ".ogg", ".opus",
              ".wma", ".ape", ".wav", ".aiff")
# 常见图片格式的魔数，用于判断抠出来的到底是不是图
_IMG_MAGIC = (b"\xff\xd8\xff",            # JPEG
              b"\x89PNG\r\n\x1a\n",       # PNG
              b"BM",                      # BMP
              b"GIF87a", b"GIF89a",       # GIF
              b"II*\x00", b"MM\x00*")     # TIFF


def parse_riff_at3(d):
    """解析 RIFF/WAVE 版 .at3，返回 (fmt, data_off, data_size)"""
    if len(d) < 12 or d[0:4] != b"RIFF" or d[8:12] != b"WAVE":
        raise ValueError("不是 RIFF/WAVE 文件")
    fmt = None
    data_off = data_size = None
    pos = 12
    while pos + 8 <= len(d):
        cid = d[pos:pos + 4]
        size = struct.unpack_from("<I", d, pos + 4)[0]
        body = pos + 8
        if cid == b"fmt " and size >= 40:
            fmt = {
                "wFormatTag": struct.unpack_from("<H", d, body)[0],
                "channels": struct.unpack_from("<H", d, body + 2)[0],
                "sample_rate": struct.unpack_from("<I", d, body + 4)[0],
                "block_align": struct.unpack_from("<H", d, body + 12)[0],
                "guid": d[body + 24:body + 40],
            }
        elif cid == b"data":
            data_off, data_size = body, size
        pos = body + size + (size & 1)
    if fmt is None:
        raise ValueError("找不到 fmt 块")
    if data_off is None:
        raise ValueError("找不到 data 块")
    return fmt, data_off, data_size


def synchsafe(n):
    """ID3v2 头部的长度字段是 synchsafe（每字节只用低 7 位）"""
    return bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F])


def build_id3_title(title):
    """ID3v2.3 的 TIT2 帧。v2.3 的帧长度是普通大端整数（不是 synchsafe）。"""
    raw = b"\x01" + b"\xFF\xFE" + title.encode("utf-16-le") + b"\x00\x00"
    frame = b"TIT2" + struct.pack(">I", len(raw)) + b"\x00\x00" + raw
    return (ID3V2_EA3_MAGIC + bytes([3, 0]) + bytes([0]) +
            synchsafe(len(frame)) + frame)


def build_ea3(codec_id, channels, sample_rate, block_align, frames, title=None):
    """按 ffmpeg omaenc.c 的写法组装 EA3 文件，返回 (bytes, codec_params)"""
    out = bytearray()
    # ID3v2.3 风格标签头，magic 为小写 "ea3"
    if title:
        out += build_id3_title(title)
    else:
        out += ID3V2_EA3_MAGIC
        out += bytes([3, 0])            # version 2.3 / revision 0
        out += bytes([0])               # flags
        out += b"\x00\x00\x00\x00"      # size = 0（无标签帧）

    # EA3 头，96 字节
    out += b"EA3\x00"                                    # +0
    out += bytes([EA3_HEADER_SIZE >> 7,                  # +4  0x00
                  EA3_HEADER_SIZE & 0x7F])               # +5  0x60
    out += b"\xFF\xFF"                                   # +6  未加密
    out += b"\x00" * 24                                  # +8  padding + DRM id
    if codec_id == OMA_CODECID_ATRAC3P:
        srate_index = OMA_SRATE_TAB.index(sample_rate // 100)
        params = ((OMA_CODECID_ATRAC3P << 24) |
                  (srate_index << 13) |
                  (channels << 10) |
                  (block_align // 8 - 1))
    elif codec_id == OMA_CODECID_ATRAC3:
        raise ValueError("ATRAC3（非 plus）需要 joint-stereo extradata，"
                         "RIFF 里取不到；请用输出 ATRAC3plus 的 at3tool 版本")
    else:
        raise ValueError("不支持的 codec id: %r" % codec_id)
    out += struct.pack(">I", params)                     # +32
    out += b"\x00" * (EA3_HEADER_SIZE - 36)              # +36 padding 到 96
    out += frames
    return bytes(out), params


def id3v2_tag_len(d):
    """ID3v2 长度是 synchsafe（每字节只用低 7 位），见 ff_id3v2_tag_len"""
    return (((d[6] & 0x7F) << 21) | ((d[7] & 0x7F) << 14) |
            ((d[8] & 0x7F) << 7) | (d[9] & 0x7F)) + ID3V2_HEADER_SIZE


def validate_ea3(blob, channels, sample_rate, block_align):
    """按 ffmpeg omadec.c / id3v2.c 的判定逐条复核输出文件。
    返回问题列表，空列表表示通过。"""
    bad = []
    if len(blob) < HEADER_TOTAL:
        return ["输出过短（%d 字节）" % len(blob)]

    # --- ID3v2 标签头（ff_id3v2_match）---
    if blob[0:3] != ID3V2_EA3_MAGIC:
        bad.append("ID3v2 魔数应为小写 b'ea3'，实际 %r" % blob[0:3])
    if blob[3] == 0xFF or blob[4] == 0xFF:
        bad.append("ID3v2 版本/修订字节非法")
    if any(blob[i] & 0x80 for i in (6, 7, 8, 9)):
        bad.append("ID3v2 长度字段未使用 synchsafe 编码")

    taglen = id3v2_tag_len(blob)
    blk = taglen
    if blk + EA3_HEADER_SIZE > len(blob):
        bad.append("EA3 块超出文件范围")
        return bad

    # 标签体里必须能解析出帧；解析不出来通常意味着拼头时把 10 字节头写了两遍
    if not find_frames(bytearray(blob[:blk]), blk):
        bad.append("ID3v2 标签体里解析不出任何帧（检查是否重复写了标签头）")

    # --- EA3 块（oma_read_header）---
    if blob[blk:blk + 3] != b"EA3":
        bad.append("EA3 块魔数错误：%r（应为大写 b'EA3'）" % blob[blk:blk + 3])
    if blob[blk + 4] != 0:
        bad.append("EA3 buf[4] 应为 0，实际 %d" % blob[blk + 4])
    if blob[blk + 5] != EA3_HEADER_SIZE:
        bad.append("EA3 buf[5] 应为 96，实际 %d" % blob[blk + 5])

    eid = struct.unpack_from(">h", blob, blk + 6)[0]
    if eid not in (-1, -128):
        bad.append("eid=%d 会被当作加密文件处理（应为 0xFFFF）" % eid)

    codec = blob[blk + 32]
    if codec != OMA_CODECID_ATRAC3P:
        bad.append("codec id 应为 1(ATRAC3plus)，实际 %d" % codec)

    params = struct.unpack_from(">I", blob, blk + 32)[0] & 0xFFFFFF
    idx = (params >> 13) & 7
    got_sr = OMA_SRATE_TAB[idx] * 100 if idx < len(OMA_SRATE_TAB) else 0
    if got_sr != sample_rate:
        bad.append("回读采样率 %d ≠ 源 %d" % (got_sr, sample_rate))
    got_ch = (params >> 10) & 7
    if got_ch != channels:
        bad.append("回读声道 %d ≠ 源 %d" % (got_ch, channels))
    got_fs = ((params & 0x3FF) * 8) + 8
    if got_fs != block_align:
        bad.append("回读帧长 %d ≠ 源 %d" % (got_fs, block_align))

    payload = len(blob) - (blk + EA3_HEADER_SIZE)
    if payload % block_align:
        bad.append("码流长度 %d 不是帧长 %d 的整数倍" % (payload, block_align))
    return bad


def repair_ea3(src, tpl=None, tag=False):
    """就地修复已有的 .aa3/.oma：
    1) 早期版本 ID3v2 魔数写成大写 'EA3' → 改写为小写
    2) 若已是最小头且提供了原厂模板 → 用原厂头部重建（这才是 PSP 认的结构）
    返回 (成功与否, 信息字符串)"""
    d = bytearray(open(src, "rb").read())
    name = os.path.basename(src)
    if len(d) < HEADER_TOTAL:
        return False, "%s：文件过短（%d 字节），不是完整的 EA3 文件" % (name, len(d))

    # --- 1) 修大写魔数 ---
    fixed_magic = False
    if bytes(d[0:3]) == b"EA3" and bytes(d[10:13]) == b"EA3" \
            and d[14] == 0 and d[15] == EA3_HEADER_SIZE:
        d[0:3] = ID3V2_EA3_MAGIC
        fixed_magic = True
    elif bytes(d[0:3]) != ID3V2_EA3_MAGIC:
        return False, "%s：不是 EA3 容器（头 3 字节 %r）" % (name, bytes(d[0:3]))

    blk = id3v2_tag_len(d)
    if bytes(d[blk:blk + 3]) != b"EA3" or d[blk + 4] != 0 \
            or d[blk + 5] != EA3_HEADER_SIZE:
        return False, "%s：EA3 块损坏，无法修复，请用原始 .at3 重新转换" % name

    codec = d[blk + 32]
    params = struct.unpack_from(">I", d, blk + 32)[0] & 0xFFFFFF
    idx = (params >> 13) & 7
    sr = OMA_SRATE_TAB[idx] * 100 if idx < len(OMA_SRATE_TAB) else 0
    ch = (params >> 10) & 7
    fs = ((params & 0x3FF) * 8) + 8
    audio_off = blk + EA3_HEADER_SIZE
    payload = bytes(d[audio_off:])

    # --- 2) 最小头 → 套用原厂头重建 ---
    if tpl and audio_off <= 128 and codec == OMA_CODECID_ATRAC3P \
            and sr and ch and fs > 8 and payload and not payload.__len__() % fs:
        title = os.path.splitext(name)[0] if tag else None
        n = len(payload) // fs
        blob, _ = build_from_template(tpl[0], tpl[1], codec, ch, sr, fs,
                                      payload, title, n * 2048 / sr)
        action = "套用原厂头重建（头 %d → %d 字节）" % (audio_off, len(blob) - len(payload))
    elif fixed_magic:
        blob = bytes(d)
        action = "修复大写魔数"
    else:
        return True, "%s：头部已正常，无需处理" % name

    bad = validate_ea3(blob, ch, sr, fs)
    if bad:
        return False, "%s：处理后自检未通过（%s），未改动原文件" % (
            name, "；".join(bad))
    if not blob.endswith(payload):
        return False, "%s：码流拼接异常，未改动原文件" % name

    tmp = src + ".tmp"
    with open(tmp, "wb") as f:
        f.write(blob)
        f.flush()
        os.fsync(f.fileno())
    if open(tmp, "rb").read() != blob:
        os.remove(tmp)
        return False, "%s：写回校验失败，未改动原文件" % name
    os.replace(tmp, src)

    nframes = len(payload) // fs if fs else 0
    return True, ("%s：%s | %dHz %dch 帧长%d | %d 帧 %.1fs"
                  % (name, action, sr, ch, fs, nframes,
                     nframes * 2048 / sr if sr else 0))


def find_frames(tag, end):
    """枚举 ID3v2.3 标签体里的帧，返回 [(id, 头偏移, 数据偏移, 数据长度)]。
    ID3v2.3 的帧长度是普通大端整数（不是 synchsafe）。"""
    out, pos = [], 10
    while pos + 10 <= end:
        fid = bytes(tag[pos:pos + 4])
        if not all(32 <= c < 127 for c in fid):
            break
        size = struct.unpack_from(">I", tag, pos + 4)[0]
        if size <= 0 or pos + 10 + size > end:
            break
        out.append((fid, pos, pos + 10, size))
        pos += 10 + size
    return out


def find_cover_in_dir(audio_path):
    """给一个音频文件在同目录里找封面图：先试同名图片，再试 cover.jpg 之类。"""
    d = os.path.dirname(os.path.abspath(audio_path))
    base = os.path.splitext(os.path.basename(audio_path))[0]
    cands = [os.path.join(d, base + e) for e in
             (".jpg", ".jpeg", ".png", ".bmp")]
    cands += [os.path.join(d, n) for n in COVER_NAMES]
    try:
        names = {n.lower(): n for n in os.listdir(d)}
    except OSError:
        return None
    for c in cands:
        n = os.path.basename(c).lower()
        if n in names:
            return os.path.join(d, names[n])
    # 目录里只有一张图片时也认（单曲自提取封面的常见情况）
    imgs = [n for n in names.values()
            if n.lower().endswith((".jpg", ".jpeg", ".png", ".bmp"))]
    if len(imgs) == 1:
        return os.path.join(d, imgs[0])
    return None


def find_ffmpeg():
    """在 exe 同目录 / tools 子目录 / PATH 里找 ffmpeg.exe"""
    import shutil
    for d in (exe_dir(), os.path.join(exe_dir(), "tools")):
        p = os.path.join(d, "ffmpeg.exe")
        if os.path.isfile(p):
            return p
    return shutil.which("ffmpeg")


def _id3v2_apic(path):
    """直接解析 ID3v2 标签里的 APIC 帧（mp3 最常用，不需要任何外部工具）。"""
    try:
        with open(path, "rb") as f:
            head = f.read(10)
            if head[:3] != b"ID3":
                return None
            size = ((head[6] & 0x7F) << 21) | ((head[7] & 0x7F) << 14) | \
                   ((head[8] & 0x7F) << 7) | (head[9] & 0x7F)
            tag = bytearray(head + f.read(size))
    except OSError:
        return None
    first = None
    for fid, _hoff, doff, sz in find_frames(tag, len(tag)):
        if fid != b"APIC":
            continue
        b = bytes(tag[doff:doff + sz])
        if len(b) < 4:
            continue
        enc, i = b[0], 1
        j = b.find(b"\x00", i)
        if j < 0:
            continue
        i = j + 1                      # 跳过 mime
        if i >= len(b):
            continue
        ptype = b[i]
        i += 1                         # 跳过 picture type
        if enc in (1, 2):              # UTF-16（带不带 BOM），结束符两个字节
            k = i
            while k + 1 < len(b) and not (b[k] == 0 and b[k + 1] == 0):
                k += 2
            i = k + 2
        else:                          # ISO-8859-1 / UTF-8，结束符一个字节
            k = b.find(b"\x00", i)
            i = (k + 1) if k >= 0 else len(b)
        img = b[i:]
        if not img:
            continue
        if ptype == COVER_PTYPE:       # 优先「封面(front cover)」
            return img
        if first is None:
            first = img
    return first


def _ffmpeg_apic(path, ffmpeg):
    """用 ffmpeg 抠内嵌封面（flac / m4a / ogg / wma 等，ID3v2 解析不到的靠它）。"""
    import subprocess
    try:
        r = subprocess.run(
            [ffmpeg, "-v", "error", "-i", path, "-an",
             "-map", "0:v:0", "-c:v", "copy", "-f", "image2pipe", "-"],
            capture_output=True, timeout=60)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    return r.stdout or None


def extract_embedded_cover(path, ffmpeg=None):
    """从音频文件内嵌的封面里抠图。返回图片字节，没有则返回 None。

    先自己解析 ID3v2 APIC（mp3，零依赖、快），不行再叫 ffmpeg。
    """
    if not os.path.isfile(path):
        return None
    data = _id3v2_apic(path)
    if not data and ffmpeg and os.path.isfile(ffmpeg):
        data = _ffmpeg_apic(path, ffmpeg)
    if data and data.startswith(_IMG_MAGIC):
        return data
    return None


def find_cover_for(*paths, **kw):
    """按优先级找封面，返回 **图片路径(str)** 或 **图片字节(bytes)**，没有则 None。

    顺序：
      1. 每个 path 同目录里的图片（同名图 → cover.jpg 之类 → 目录里唯一的图）
      2. 每个 path 本身 / 同目录同名音频 的**内嵌封面**

    返回 bytes 时表示是从音频里抠出来的，调用方直接用即可。
    kw: embed=False 关闭第 2 步，ffmpeg=<路径> 指定 ffmpeg。
    """
    embed = kw.get("embed", True)
    ffmpeg = kw.get("ffmpeg", None)
    for p in paths:
        c = find_cover_in_dir(p)
        if c:
            return c
    if not embed:
        return None

    cands = []
    for p in paths:
        ap = os.path.abspath(p)
        cands.append(ap)
        d, base = os.path.dirname(ap), os.path.splitext(os.path.basename(ap))[0]
        cands += [os.path.join(d, base + e) for e in AUDIO_EXTS]
    seen = set()
    for c in cands:
        cl = c.lower()
        if cl in seen:
            continue
        seen.add(cl)
        # 只对真正的音频格式动用 ffmpeg（否则对 .at3 白起一堆子进程）
        ext = os.path.splitext(c)[1].lower()
        data = extract_embedded_cover(c, ffmpeg if ext in AUDIO_EXTS else None)
        if data:
            return data
    return None


def cover_label(cover):
    """给日志用的封面来源说明（可能是路径，也可能是抠出来的字节）"""
    if isinstance(cover, (bytes, bytearray)):
        return "从音频内嵌提取（%d 字节）" % len(cover)
    return os.path.basename(cover)


def load_cover_bytes(path, max_side=COVER_MAX_SIDE, max_bytes=COVER_MAX_BYTES):
    """读入图片 → RGB → 缩到 max_side → 压成 JPEG，控制在 max_bytes 以内。
    参数可以是**文件路径**，也可以是**图片字节**（从音频里抠出来的那种）。
    没有 Pillow 时退化：只接受已经是 JPEG 且体积合规的图片，原样嵌入。"""
    raw = path if isinstance(path, (bytes, bytearray)) else open(path, "rb").read()
    try:
        from PIL import Image
    except ImportError:
        if raw[:2] != b"\xff\xd8":
            raise RuntimeError("未安装 Pillow，只能直接嵌入 JPEG；"
                               "请把封面转成 .jpg（或 pip install Pillow）")
        if len(raw) > max_bytes:
            raise RuntimeError("未安装 Pillow，无法压缩；封面 %d 字节超过 %d 上限"
                               % (len(raw), max_bytes))
        return raw, COVER_MIME
    im = Image.open(io.BytesIO(bytes(raw)))
    im = im.convert("RGB")
    if max(im.size) > max_side:
        im.thumbnail((max_side, max_side), Image.LANCZOS)
    q = 88
    while True:
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=q, optimize=True, progressive=False)
        data = buf.getvalue()
        if len(data) <= max_bytes or q <= 45:
            return data, COVER_MIME
        q -= 8


def build_apic(data, mime=COVER_MIME, ptype=COVER_PTYPE, desc=""):
    """标准 ID3v2.3 APIC 帧。索尼原厂文件里没有它，但 **PSP 靠它显示封面**。"""
    body = (b"\x00" +                       # text encoding: $00 = ISO-8859-1
            mime.encode("ascii") + b"\x00" +
            bytes([ptype]) +
            desc.encode("latin-1", "replace") + b"\x00" +
            data)
    return (b"APIC" + struct.pack(">I", len(body)) + b"\x00\x00" + body)


def build_geob(obj, desc, mime=COVER_MIME, enc=2):
    """索尼风格的 GEOB 帧。

    enc=2 表示后面的 filename / description 是 UTF-16BE（无 BOM），
    结束符是 0x00 0x00 两个字节。原厂文件里 filename 是空串。
    """
    body = (bytes([enc]) +
            mime.encode("ascii") + b"\x00" +      # MIME 永远是单字节 + 0x00
            b"\x00\x00" +                          # filename：UTF-16BE 空串
            desc.encode("utf-16-be") + b"\x00\x00" +
            obj)
    return b"GEOB" + struct.pack(">I", len(body)) + b"\x00\x00" + body


def build_cover_frames(jpeg, apic=True):
    """封面帧组合。

    索尼格式 = 两个 GEOB（主图 OMG_FENCA1 + 缩略图位 OMG_TDFCA，索尼填同一张），
    索尼自家软件（SonicStage / Walkman）读这个。

    apic=True 时再补一个标准 APIC 帧 —— **PSP 音乐播放器读的是 APIC**。
    真机对照实验：只写 GEOB×2 → PSP 不显示封面；GEOB×2 + APIC → PSP 显示封面。
    所以 apic 默认开。"""
    out = (build_geob(jpeg, SONY_COVER_MAIN) +
           build_geob(jpeg, SONY_COVER_THUMB))
    if apic:
        out += build_apic(jpeg)
    return out


def _geob_desc(tag, doff, size):
    """取 GEOB 的 description（UTF-16BE）"""
    b = bytes(tag[doff:doff + size])
    if len(b) < 3 or b[0] not in (1, 2):
        return ""
    i = 1
    j = b.find(b"\x00", i)
    if j < 0:
        return ""
    i = j + 1
    if b[i:i + 2] == b"\x00\x00":      # 空 filename
        i += 2
    else:
        k = i
        while k + 1 < len(b) and not (b[k] == 0 and b[k + 1] == 0):
            k += 2
        i = k + 2
    k = i
    while k + 1 < len(b) and not (b[k] == 0 and b[k + 1] == 0):
        k += 2
    return b[i:k].decode("utf-16-be", "replace")


def align_id3_tag(tag, unit=ID3_PAD_UNIT):
    """把 ID3v2 标签（含 10 字节头）补 0 到 unit 的整数倍。

    索尼原厂文件一律如此；不对齐时 PSP 会「显示封面但放不出声」。
    """
    r = len(tag) % unit
    if r == 0:
        return bytes(tag)
    body = bytes(tag[ID3V2_HEADER_SIZE:]) + b"\x00" * (unit - r)
    return (ID3V2_EA3_MAGIC + bytes([3, 0]) + b"\x00" +
            synchsafe(len(body)) + body)


def insert_frames(hdr, blk, blob, after_desc=None, pad_to=None, align=True):
    """把若干帧插进 ID3v2 标签，重建头部。

    hdr  = [ID3v2 标签][EA3 96B]，blk = EA3 块起始 = ID3v2 标签总长
    blob = 要插入的一串完整帧
    after_desc = 插在 description 为该值的 GEOB 之后（复刻原厂的帧顺序），
                 不给则追加到最后一帧之后
    pad_to = 把整个头部补到该长度（排查 PSP 是否容忍头部变长）
    返回 (新头部 bytes, 新的 blk)
    """
    tag = bytearray(hdr[:blk])
    ea3 = bytes(hdr[blk:blk + EA3_HEADER_SIZE])
    frames = find_frames(tag, blk)
    if not frames:
        raise RuntimeError("模板标签里解析不出帧，无法插入封面")

    cut = frames[-1][1] + 10 + frames[-1][3]      # 默认追加到末尾
    if after_desc:
        for fid, hoff, doff, size in frames:
            if fid == b"GEOB" and _geob_desc(tag, doff, size) == after_desc:
                cut = hoff + 10 + size
                break

    # tag 含 10 字节 ID3v2 头，帧体要从 ID3V2_HEADER_SIZE 开始取，
    # 否则新旧头会被拼在一起，标签里出现两个头
    body = bytes(tag[ID3V2_HEADER_SIZE:cut]) + blob + bytes(tag[cut:blk])
    if pad_to is not None:
        total = ID3V2_HEADER_SIZE + len(body) + EA3_HEADER_SIZE
        if total > pad_to:
            raise RuntimeError("内容太大：头部需要 %d 字节，超过 pad_to=%d"
                               % (total, pad_to))
        body += b"\x00" * (pad_to - total)
    new = (ID3V2_EA3_MAGIC + bytes([3, 0]) + b"\x00" + synchsafe(len(body)) + body)
    if pad_to is None and align:
        new = align_id3_tag(new)          # 补回 1024 对齐，否则 PSP 放不出声
    return bytes(new) + ea3, len(new)


def frame_desc(tag, doff, size):
    """取 TXXX 之类的 description 文本，返回 (描述字符串, 值起始偏移)"""
    enc = tag[doff]
    i = doff + 1
    try:
        if enc in (1, 2):                       # UTF-16
            i += 2 if enc == 1 else 0           # 跳过 BOM
            j = i
            while j + 1 < doff + size:
                if tag[j] == 0 and tag[j + 1] == 0:
                    break
                j += 2
            return tag[i:j].decode("utf-16-be", "replace"), j + 2
        i = doff + 1
        j = i
        while j < doff + size and tag[j] != 0:
            j += 1
        return tag[i:j].decode("latin-1", "replace"), j + 1
    except Exception:
        return "", doff + 1


def patch_frame(tag, fid, value, want_desc=None):
    """就地替换文本帧的值（保持帧长度不变，多出来的补 0）。成功返回 True。"""
    for f, hoff, doff, size in find_frames(tag, len(tag)):
        if f != fid:
            continue
        if want_desc is not None:
            desc, voff = frame_desc(tag, doff, size)
            if desc != want_desc:
                continue
        else:
            voff = doff + 1
        enc = tag[doff]
        if enc == 1:
            bom = bytes(tag[voff:voff + 2])
            body = value.encode("utf-16-le" if bom == b"\xff\xfe" else "utf-16-be")
            enc_val = bom + body
        elif enc == 2:
            enc_val = value.encode("utf-16-be")
        elif enc == 3:
            enc_val = value.encode("utf-8")
        else:
            enc_val = value.encode("latin-1", "replace")
        avail = doff + size - voff
        if len(enc_val) > avail:
            return False
        tag[voff:voff + len(enc_val)] = enc_val
        tag[voff + len(enc_val):doff + size] = b"\x00" * (avail - len(enc_val))
        return True
    return False


def load_template(path):
    """从一个能正常播放的 .oma/.aa3 里截出完整头部（ID3v2 标签 + 96 字节 EA3 块）"""
    d = open(path, "rb").read()
    taglen = id3v2_tag_len(d)
    blk = taglen
    if blk + EA3_HEADER_SIZE > len(d):
        raise ValueError("文件太短，不是完整的 EA3 文件")
    if d[blk:blk + 3] != b"EA3" or d[blk + 4] != 0 or d[blk + 5] != EA3_HEADER_SIZE:
        raise ValueError("在 0x%x 处找不到合法的 EA3 块" % blk)
    eid = struct.unpack_from(">h", d, blk + 6)[0]
    if eid not in (-1, -128):
        raise ValueError("参考文件是加密的（eid=%d），换机后会失效，请换一个未加密的" % eid)
    return bytearray(d[:blk + EA3_HEADER_SIZE]), blk


def exe_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def resource_dir():
    """PyInstaller 单文件模式下，--add-data 打进去的文件会解压到 _MEIPASS"""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def template_path():
    return os.path.join(exe_dir(), "oma_header.bin")


# SonicStage 为 PSP 转码产物的默认位置，本机实测这里的文件都能播
REF_DIRS = [
    os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"),
                 "SonicStage", "Packages", "Optimized Files"),
]


def discover_ref():
    """在已知目录里自动找一个未加密的 .oma/.aa3 当作头部来源。
    返回 (头部 bytes, 来源路径) 或 None。"""
    for d in REF_DIRS:
        if not os.path.isdir(d):
            continue
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for n in names:
            if not n.lower().endswith((".oma", ".aa3")):
                continue
            p = os.path.join(d, n)
            try:
                tpl, _blk = load_template(p)
            except Exception:
                continue
            return tpl, p
    return None


def build_from_template(tpl, blk, codec_id, channels, sample_rate,
                        block_align, frames, title, duration_s,
                        cover=None, pad_to=None, apic=True):
    """在原厂头部基础上改参数，返回 (bytes, params)

    cover  : 已压好的 JPEG 字节，写成 GEOB×2（+ APIC）
    apic   : 是否额外写标准 APIC 帧。默认开 —— PSP 显示封面靠它
             （真机实测：只写 GEOB×2 不显示，加上 APIC 才显示）
    pad_to : 把整个头部补到这个长度（None = 随封面自然变长）
    """
    hdr = bytearray(tpl)
    srate_index = OMA_SRATE_TAB.index(sample_rate // 100)
    params = ((codec_id << 24) | (srate_index << 13) |
              (channels << 10) | (block_align // 8 - 1))
    struct.pack_into(">I", hdr, blk + 32, params)
    if title:
        patch_frame(hdr, b"TIT2", title)
        patch_frame(hdr, b"TXXX", title, want_desc="OMG_TIT2S")
    if duration_s:
        patch_frame(hdr, b"TLEN", str(int(round(duration_s * 1000))))
    if cover:
        # 索尼原厂的帧顺序是 … GEOB(OMG_BKLSI) GEOB(OMG_FENCA1)
        # GEOB(OMG_OLINF) GEOB(OMG_TDFCA)，这里照抄：
        # 主封面紧跟 OMG_BKLSI，缩略图放在最后。
        main_f = build_geob(cover, SONY_COVER_MAIN)
        thumb_f = build_geob(cover, SONY_COVER_THUMB) + (
            build_apic(cover) if apic else b"")
        # 插帧会改变标签长度 → blk（EA3 块偏移）也随之改变
        # 只在最后一次插帧时对齐（插两次的话中间那次会白补一层 1024）
        newhdr, blk = insert_frames(hdr, blk, main_f, after_desc=SONY_BKLSI,
                                    align=False)
        newhdr, blk = insert_frames(newhdr, blk, thumb_f, pad_to=pad_to)
        return newhdr + frames, params
    # 无封面：模板（3168）本来就是对齐的；万一不是，也补一下
    if blk % ID3_PAD_UNIT:
        return align_id3_tag(bytes(hdr[:blk])) + bytes(hdr[blk:]) + frames, params
    return bytes(hdr) + frames, params


def convert_one(src, keep=False, tag=False, tpl=None, cover=None, pad_to=None,
                apic=True):
    """转换单个文件。返回 (成功与否, 输出路径或 None, 信息字符串)

    cover  : 封面图片路径，或图片原始字节（如从音频内嵌抠出来的），或 None
    apic   : 除索尼 GEOB 外，是否再写一个标准 APIC 帧（默认开，PSP 靠它显示）
    pad_to : 把头部补到该长度（调试用，None = 自然变长）
    """
    if not os.path.isfile(src):
        return False, None, "文件不存在：%s" % src
    ext = os.path.splitext(src)[1].lower()
    if ext in (".aa3", ".oma"):
        # 拖进来的是已生成的 aa3 → 走就地修复/重建分支
        good, msg = repair_ea3(src, tpl, tag)
        return good, src, msg
    if ext not in (".at3",):
        return False, None, "跳过（不是 .at3 / .aa3）：%s" % os.path.basename(src)

    d = open(src, "rb").read()
    fmt, data_off, data_size = parse_riff_at3(d)

    if fmt["wFormatTag"] == 0xFFFE and fmt["guid"] == ATRAC3P_GUID:
        codec_id = OMA_CODECID_ATRAC3P
        codec_name = "ATRAC3plus"
    elif fmt["wFormatTag"] == ATRAC3_WFORMATTAG:
        codec_id = OMA_CODECID_ATRAC3
        codec_name = "ATRAC3"
    else:
        return False, None, "%s：无法识别的编码 wFormatTag=0x%04X GUID=%s" % (
            os.path.basename(src), fmt["wFormatTag"], fmt["guid"].hex())

    if fmt["sample_rate"] // 100 not in OMA_SRATE_TAB:
        return False, None, "%s：采样率 %d Hz 不在 OMA 支持范围（32000/44100/48000/88200/96000）" % (
            os.path.basename(src), fmt["sample_rate"])
    if fmt["block_align"] <= 0 or data_size % fmt["block_align"]:
        return False, None, "%s：data 长度 %d 不是帧长 %d 的整数倍，文件可能已损坏" % (
            os.path.basename(src), data_size, fmt["block_align"])
    # 防止 data 块声明的长度超过文件实际长度，导致静默产出残缺的 aa3
    avail = len(d) - data_off
    if data_size > avail:
        return False, None, (
            "%s：data 块声明 %d 字节，但文件里只剩 %d 字节（缺 %d 字节），"
            "文件已损坏/不完整" % (os.path.basename(src), data_size, avail,
                                  data_size - avail))

    frames = d[data_off:data_off + data_size]
    title = os.path.splitext(os.path.basename(src))[0] if tag else None
    nframes0 = data_size // fmt["block_align"]

    # ---- 封面 ----
    cover_note = ""
    if cover is not None:
        # cover 可能是图片路径，也可能是从音频内嵌抠出来的原始字节（png/jpeg 都行）
        try:
            cover_bytes, _mime = load_cover_bytes(cover)
        except Exception as e:
            return False, None, "%s：封面处理失败（%s）" % (
                os.path.basename(src), e)
        cover_note = " | 封面 %d 字节" % len(cover_bytes)
    else:
        cover_bytes = None

    if tpl:
        hdr, blk = tpl[0], tpl[1]
        blob, params = build_from_template(
            hdr, blk, codec_id, fmt["channels"], fmt["sample_rate"],
            fmt["block_align"], frames, title,
            nframes0 * 2048 / fmt["sample_rate"],
            cover=cover_bytes, pad_to=pad_to, apic=apic)
    else:
        blob, params = build_ea3(codec_id, fmt["channels"], fmt["sample_rate"],
                                 fmt["block_align"], frames, title)

    dst = os.path.splitext(src)[0] + ".aa3"
    audio_off = len(blob) - len(frames)
    with open(dst, "wb") as f:
        f.write(blob)
        f.flush()
        os.fsync(f.fileno())

    # ---- 写回校验：必须字节级一致才允许删原文件 ----
    chk = open(dst, "rb").read()
    if len(chk) != audio_off + len(frames) or chk[audio_off:] != frames:
        try:
            os.remove(dst)
        except OSError:
            pass
        return False, None, "输出校验失败，已撤销输出文件（原 .at3 保留）：%s" % os.path.basename(src)

    # ---- 规范自检：按 ffmpeg omadec.c 的判定逐条复核 ----
    problems = validate_ea3(chk, fmt["channels"], fmt["sample_rate"],
                            fmt["block_align"])
    if problems:
        try:
            os.remove(dst)
        except OSError:
            pass
        return False, None, "%s：输出未通过 EA3 规范自检（%s），已撤销" % (
            os.path.basename(src), "；".join(problems))

    nframes = data_size // fmt["block_align"]
    # 码率 = 帧长(B) * 8 * 采样率 / 每帧样本数(2048) / 1000，与声道数无关
    kbps = fmt["block_align"] * 8 * fmt["sample_rate"] / 2048 / 1000
    dur = nframes * 2048 / fmt["sample_rate"]

    msg = ("%s → %s | %s %dHz %dch 帧长%d(%.0fkbps) | %d帧 %.1fs | params=0x%06X"
           % (os.path.basename(src), os.path.basename(dst), codec_name,
              fmt["sample_rate"], fmt["channels"],               fmt["block_align"], kbps,
              nframes, dur, params & 0xFFFFFF))
    msg += cover_note
    if fmt["sample_rate"] != 44100:
        msg += "\n       ⚠ PSP 音乐播放器要 44100Hz，当前 %dHz，可能被判损坏" % fmt["sample_rate"]
    if fmt["channels"] not in (1, 2):
        msg += "\n       ⚠ 声道数 %d 超出 PSP 支持范围（1/2）" % fmt["channels"]

    if keep:
        msg += " | 原文件保留（--keep）"
    else:
        try:
            os.remove(src)
            msg += " | 原文件已删除"
        except OSError as e:
            msg += " | 原文件删除失败：%s" % e
    return True, dst, msg


def resolve_template(ref=None, minimal=False):
    """按优先级找到可用的原厂头部模板。
    返回 (tpl, notes)；tpl = (bytearray, EA3块偏移, 来源说明) 或 None。
    GUI 与命令行共用这段逻辑。"""
    notes = []
    tpl = None
    tp = template_path()

    def _mk(raw, origin):
        blk = id3v2_tag_len(raw)
        if not (raw[blk:blk + 3] == b"EA3" and raw[blk + 4] == 0
                and raw[blk + 5] == EA3_HEADER_SIZE):
            raise ValueError("内容不是合法的 EA3 头部")
        return (bytearray(raw), blk, origin)

    if minimal:
        return None, notes

    if ref:
        try:
            t = load_template(ref)
        except Exception as e:
            notes.append("读取参考文件失败：%s" % e)
            return None, notes
        tpl = (t[0], t[1], ref)
        try:
            with open(tp, "wb") as f:
                f.write(bytes(tpl[0]))
            notes.append("已把参考头部（%d 字节）保存到 %s" % (len(tpl[0]), tp))
        except OSError as e:
            notes.append("警告：模板保存失败（%s），本次仍会使用" % e)
        return tpl, notes

    # 1) exe 同目录的模板
    if os.path.isfile(tp):
        try:
            tpl = _mk(open(tp, "rb").read(), "外部模板 " + tp)
        except Exception as e:
            notes.append("警告：读取 %s 失败（%s）" % (tp, e))
    # 2) 自动到 SonicStage 的 PSP 优化目录里找
    if tpl is None:
        found = discover_ref()
        if found:
            raw, src = found
            try:
                tpl = _mk(bytes(raw), "自动发现 " + src)
                notes.append("自动找到可播放的参考文件：%s" % os.path.basename(src))
                try:
                    with open(tp, "wb") as f:
                        f.write(bytes(tpl[0]))
                except OSError:
                    pass
            except Exception as e:
                notes.append("警告：参考文件解析失败（%s）" % e)
    # 3) 打包进 exe 的内置模板
    if tpl is None:
        bp = os.path.join(resource_dir(), "oma_header.bin")
        if os.path.isfile(bp):
            try:
                tpl = _mk(open(bp, "rb").read(), "内置模板")
                notes.append("使用内置头部模板")
            except Exception as e:
                notes.append("警告：内置模板异常（%s）" % e)
    return tpl, notes


def main():
    # 中文路径 / 中文提示在 GBK 控制台下可能抛 UnicodeEncodeError，兜一下底
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    args = sys.argv[1:]
    keep = "--keep" in args or "-k" in args
    tag = "--tag" in args or "-t" in args
    minimal = "--minimal" in args
    apic = "--no-apic" not in args      # 默认开：PSP 靠 APIC 显示封面
    ref = None
    cover = "auto" if "--no-cover" not in args else None   # 默认自动找封面
    pad_to = None
    val_idx = set()          # 这些位置是选项的值，不是待转换文件
    for i, a in enumerate(args):
        if a == "--ref" and i + 1 < len(args):
            ref = args[i + 1]
            val_idx.add(i + 1)
        elif a.startswith("--ref="):
            ref = a[6:]
        elif a == "--cover":
            if i + 1 < len(args) and not args[i + 1].startswith("-"):
                cover = args[i + 1]
                val_idx.add(i + 1)
            else:
                cover = "auto"
        elif a.startswith("--cover="):
            cover = a[8:] or "auto"
        elif a == "--pad" and i + 1 < len(args):
            try:
                pad_to = int(args[i + 1])
            except ValueError:
                pad_to = None
            val_idx.add(i + 1)
        elif a.startswith("--pad="):
            try:
                pad_to = int(a[6:])
            except ValueError:
                pad_to = None
    # --ref / --cover 后面跟的是文件路径，不能当成待转换文件
    args_files = [a for i, a in enumerate(args)
                  if not a.startswith("-") and i not in val_idx and a != ref]

    # 拖进来的是文件夹就展开成里面的 .at3 / .aa3 / .oma（含子目录）
    files = []
    for a in args_files:
        if os.path.isdir(a):
            for root, _dirs, names in os.walk(a):
                for n in sorted(names):
                    if n.lower().endswith((".at3", ".aa3", ".oma")):
                        files.append(os.path.join(root, n))
        else:
            files.append(a)

    tpl, notes = resolve_template(ref, minimal)
    for line in notes:
        print(line)
    if ref and tpl is None:
        return 2

    if not files:
        print("=" * 62)
        print("  at3 → aa3 换壳器（PSP 音乐播放器专用）")
        print("=" * 62)
        print()
        print("用法：把一个或多个文件拖到本程序图标上。")
        print()
        print("  也可以直接拖一个**文件夹**进来，会批量处理里面所有的 .at3 / .aa3")
        print()
        print("  .at3  → 在同目录生成同名 .aa3，并删除原 .at3（校验通过后）")
        print("  .aa3  → 就地修复：早期版本头部魔数写成大写 EA3 导致 PSP 报损坏，")
        print("          这里直接改写那 3 个字节，音频数据不动")
        print()
        print("头部模板（全自动，一般不用管）：")
        print("  程序会自动在 SonicStage 的『Optimized Files』目录里找一个能播的 .oma，")
        print("  截下它那 3168 字节头部当模板，只改编码参数 / 标题 / 时长。")
        print("  那个目录不存在时，会退回 exe 内置的模板。")
        print("  已用 SonicStage 验证：换头的文件能正常导入，最小头提示不能导入，")
        print("  且头部与音频之间无绑定校验（可跨文件复用）。别加 --minimal。")
        print()
        print("命令行：at3_to_aa3.exe a.at3 b.aa3 [--keep] [--tag] [--minimal]")
        print("        --keep / -k       保留原文件（建议首次用它试跑）")
        print("        --tag  / -t       写入曲目标题（TIT2 / OMG_TIT2S / TLEN）")
        print("        --minimal         不用模板，生成最小头（PSP 通常不认）")
        print("        --ref <file.oma>  指定参考文件并保存为模板")
        print("        --cover [图片]    内嵌专辑封面（默认自动找同目录 cover.jpg /")
        print("                          同名图片 / 目录里唯一的图片；")
        print("                          都找不到就从音频的内嵌封面里抠）")
        print("        --no-cover        不找封面")
        print("        --no-apic         只写索尼 GEOB，不写标准 APIC")
        print("                          （默认写 —— PSP 显示封面靠 APIC，实测只写")
        print("                            GEOB 不显示）")
        print("        --pad <字节数>    把头部补到该长度（排查 PSP 是否容忍头部变长）")
        print()
        print("说明：at3tool 出的 .at3 是 RIFF 容器（给 PSP 游戏资源用的），")
        print("      PSP 音乐播放器认的是 EA3 容器（.aa3/.oma）。本程序只换容器，")
        print("      不重新编码，音质无损。")
        print()
        input("按回车键退出...")
        return 0

    need_at3 = [a for a in files if a.lower().endswith(".at3")]
    if need_at3 and tpl is None and not minimal:
        print("!" * 62)
        print("没找到可用的原厂头部（已试过 exe 同目录模板、SonicStage 优化目录、内置模板），")
        print("而实测「最小合成头」PSP 不认。请手动指定一个 PSP 上能播的 .oma/.aa3：")
        print('    %s --ref "C:\\路径\\能播放的文件.OMA"' % os.path.basename(sys.executable))
        print("之后直接把 .at3 拖到本程序上即可自动套用。")
        print("确实想生成最小头，请加 --minimal。")
        print("!" * 62)
        input("按回车键退出...")
        return 2

    ok = fail = 0
    print("共 %d 个文件，模式：%s" % (len(files), "保留原文件" if keep else "转换后删除原文件"))
    print("头部来源：%s" % (("移植原厂头部 —— " + tpl[2]) if tpl else
                           "最小合成头 —— PSP 不认，请用 --ref 指定一个能播的 .oma"))
    print("-" * 62)
    ff = find_ffmpeg()
    for src in files:
        c = cover
        if cover == "auto":
            c = find_cover_for(src, ffmpeg=ff)
            if c:
                print("   封面：%s" % cover_label(c))
        try:
            good, dst, msg = convert_one(src, keep, tag, tpl,
                                         cover=c, pad_to=pad_to, apic=apic)
        except Exception as e:
            good, dst, msg = False, None, "%s → 失败：%s" % (os.path.basename(src), e)
        print(("[OK]   " if good else "[FAIL] ") + msg)
        ok += good
        fail += (not good)
    print("-" * 62)
    print("完成：成功 %d，失败 %d" % (ok, fail))
    n_del = 0
    for a in files:
        if a.lower().endswith(".at3") and not os.path.exists(a):
            n_del += 1
    if n_del:
        print("提示：已删除 %d 个原始 .at3（均在校验通过后执行）" % n_del)
    print()
    print("下一步：把生成的 .aa3 拷到记忆棒 PSP/MUSIC/ 下（最多一层子目录）")
    print()
    try:
        input("按回车键退出...")
    except EOFError:
        pass
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
