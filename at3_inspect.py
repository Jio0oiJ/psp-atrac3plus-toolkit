#!/usr/bin/env python3
"""
AT3 / AA3 / OMA 文件体检工具

用途：判断你手上的文件到底是什么容器，参数是否符合 PSP 音乐播放器的要求。

用法：
    python at3_inspect.py <文件1.at3> [文件2.aa3 ...]
"""
import struct
import sys
import os

# ATRAC3plus 在 RIFF/WAVE 里的 SubFormat GUID（前 3 段小端，后两段大端）
GUID_ATRAC3PLUS = "e923aabf-cb58-4471-a119-fffa01e4ce62"
# 立体声下 码率 -> 每帧字节数（ATRAC3plus，CBR）
FRAME_SIZE = {
    280: 48, 376: 64, 560: 96, 744: 128, 936: 160,
    1120: 192, 1488: 256, 1864: 320, 2048: 352,
}
# 单声道下 码率 -> 每帧字节数（用于参考）
FRAME_SIZE_MONO = {
    152: 48, 192: 64, 280: 96, 376: 128, 472: 160,
    560: 192, 744: 256, 932: 320, 1024: 352,
}
SAMPLES_PER_FRAME = 2048
EA3_HEADER_SIZE = 96


def guid_str(b):
    if len(b) < 16:
        return "<短>"
    a, c, d = struct.unpack_from("<I", b, 0)[0], struct.unpack_from("<H", b, 4)[0], struct.unpack_from("<H", b, 6)[0]
    return "%08x-%04x-%04x-%s-%s" % (a, c, d, b[8:10].hex(), b[10:16].hex())


def scan_riff(d):
    out = {"container": "RIFF/WAVE (.at3)", "chunks": []}
    if len(d) < 12 or d[0:4] != b"RIFF" or d[8:12] != b"WAVE":
        return None
    pos = 12
    while pos + 8 <= len(d):
        cid = d[pos:pos + 4]
        size = struct.unpack_from("<I", d, pos + 4)[0]
        body = pos + 8
        out["chunks"].append((cid.decode("latin1"), size, body))
        if cid == b"fmt " and size >= 40:
            f = {}
            f["wFormatTag"] = struct.unpack_from("<H", d, body)[0]
            f["nChannels"] = struct.unpack_from("<H", d, body + 2)[0]
            f["nSamplesPerSec"] = struct.unpack_from("<I", d, body + 4)[0]
            f["nAvgBytesPerSec"] = struct.unpack_from("<I", d, body + 8)[0]
            f["nBlockAlign"] = struct.unpack_from("<H", d, body + 12)[0]
            f["wBitsPerSample"] = struct.unpack_from("<H", d, body + 14)[0]
            f["cbSize"] = struct.unpack_from("<H", d, body + 16)[0]
            # WAVEFORMATEXTENSIBLE: SubFormat 在载荷偏移 24
            if size >= 40:
                f["SubFormat"] = guid_str(d[body + 24:body + 40])
            if size >= 44:
                f["vendorWord"] = struct.unpack_from("<H", d, body + 40)[0]
                f["configWord"] = struct.unpack_from("<H", d, body + 42)[0]
            if size > 44:
                f["tail"] = d[body + 44:body + size].hex()
            out["fmt"] = f
        elif cid == b"fact" and size >= 4:
            out["fact_samples"] = struct.unpack_from("<I", d, body)[0]
        elif cid == b"data":
            out["data_size"] = size
            out["data_off"] = body
        pos = body + size + (size & 1)  # RIFF 按字对齐
    return out


OMA_SRATE_TAB = [320, 441, 480, 882, 960]
CODEC_NAMES = {0: "ATRAC3", 1: "ATRAC3plus", 2: "AAC", 3: "MP3", 4: "LPCM",
               5: "WMA", 33: "ATRAC3plusAL", 34: "ATRAC3AL"}


def scan_ea3(d):
    """OMA / AA3：OpenMG Audio（EA3）容器。
    布局：[ID3v2.3 风格标签头，magic 为**小写** "ea3"，10 字节] + [标签帧]
          + [96 字节 EA3 头，此处才是大写 "EA3"] + 码流
    """
    if len(d) < 10 or d[0:3].lower() != b"ea3":
        return None
    out = {"container": "EA3 / OpenMG Audio (.oma/.aa3)"}
    # 魔数必须是小写 ea3：ff_id3v2_match 区分大小写，大写会被判为非法文件
    out["magic_wrong_case"] = d[0:3] != b"ea3"
    # ID3v2 长度是 synchsafe（每字节低 7 位有效）
    tag_size = (((d[6] & 0x7F) << 21) | ((d[7] & 0x7F) << 14) |
                ((d[8] & 0x7F) << 7) | (d[9] & 0x7F))
    tags, pos = [], 10
    end = 10 + tag_size
    while pos + 10 <= min(end, len(d)):
        tid = d[pos:pos + 4]
        if not all(32 <= c < 127 for c in tid):
            break
        tsize = struct.unpack_from(">I", d, pos + 4)[0]
        tags.append((tid.decode("latin1"), tsize))
        if tid == b"GEOB":
            out["encrypted_hint"] = True
        pos += 10 + tsize
    out["tags"] = tags
    blk = end
    if d[blk:blk + 3] != b"EA3" or d[blk + 4] != 0 or d[blk + 5] != EA3_HEADER_SIZE:
        out["ea3_block_bad"] = True
        return out
    out["ea3_version"] = d[blk + 3]
    out["eid"] = struct.unpack_from(">h", d, blk + 6)[0]
    out["encrypted"] = out["eid"] not in (-1, -128)
    codec = d[blk + 32]
    params = struct.unpack_from(">I", d, blk + 32)[0] & 0xFFFFFF
    out["codec_id"] = codec
    out["codec"] = CODEC_NAMES.get(codec, "未知(%d)" % codec)
    out["params"] = params
    idx = (params >> 13) & 7
    out["sample_rate"] = OMA_SRATE_TAB[idx] * 100 if idx < len(OMA_SRATE_TAB) else 0
    if codec == 1:              # ATRAC3plus
        out["channels"] = (params >> 10) & 7
        out["frame_size"] = ((params & 0x3FF) * 8) + 8
    elif codec == 0:            # ATRAC3
        out["channels"] = 2
        out["joint_stereo"] = (params >> 17) & 1
        out["frame_size"] = (params & 0x3FF) * 8
    out["audio_off"] = blk + EA3_HEADER_SIZE
    out["payload"] = len(d) - out["audio_off"]
    if out.get("frame_size"):
        out["frames"] = out["payload"] // out["frame_size"]
        out["remainder"] = out["payload"] % out["frame_size"]
    return out


def scan_raw(d):
    """裸码流：只按已知帧长做整除探测"""
    out = {"container": "未知（可能是裸 ATRAC3plus 码流）"}
    hits = []
    for fs, br in list(FRAME_SIZE.items()) + list(FRAME_SIZE_MONO.items()):
        if fs and len(d) % fs == 0:
            hits.append((fs, br, len(d) // fs))
    out["frame_hits"] = hits[:10]
    out["head"] = d[:16].hex()
    return out


def verdict(info, d=b""):
    msgs = []
    if info["container"].startswith("RIFF"):
        f = info.get("fmt", {})
        if not f:
            return ["缺少 fmt 块，这不是合法的 .at3"]
        sr = f.get("nSamplesPerSec")
        ch = f.get("nChannels")
        ba = f.get("nBlockAlign")
        g = f.get("SubFormat", "")
        msgs.append("编码：%s（GUID=%s）" % ("ATRAC3plus" if g == GUID_ATRAC3PLUS else "非 ATRAC3plus", g))
        if g != GUID_ATRAC3PLUS:
            msgs.append("  → PSP 音乐播放器要的是 ATRAC3plus，这个 GUID 不对")
        msgs.append("采样率：%s Hz %s" % (sr, "OK" if sr == 44100 else "← PSP 要 44100（48000 是 PS3 用的）"))
        msgs.append("声道：%s %s" % (ch, "OK" if ch == 2 else "← 建议立体声"))
        tbl = FRAME_SIZE if ch == 2 else FRAME_SIZE_MONO
        br = tbl.get(ba)
        msgs.append("帧长：%d 字节 → 约 %s kbps %s" % (ba, br, "OK" if br else "← 不在已知码率表里"))
        dsz = info.get("data_size", 0)
        if ba:
            n = dsz // ba
            msgs.append("data：%d 字节 = %d 帧，余数 %d %s" % (dsz, n, dsz % ba, "OK" if dsz % ba == 0 else "← 帧不对齐"))
            if sr:
                msgs.append("时长：约 %.2f 秒" % (n * SAMPLES_PER_FRAME / sr))
        msgs.append("PSP 音乐播放器认的是 EA3 容器（.oma/.aa3），不是 RIFF .at3")
        msgs.append("→ 若给 PSP 听歌：请用 SonicStage 出 .oma 再改名 .aa3，或用 ATRACTool-Reloaded 的 Walkman 输出")
        msgs.append("→ 若给游戏/自制程序用：RIFF .at3 是对的，但别放进 PSP/MUSIC")
    elif info["container"].startswith("EA3"):
        if info.get("ea3_block_bad"):
            msgs.append("EA3 头不合法（找不到 E A 3 / buf[4]!=0 / buf[5]!=96）→ PSP 会报损坏")
            return msgs
        msgs.append("这是 EA3/OpenMG 容器，PSP 音乐播放器认这个 ✓")
        if info.get("magic_wrong_case"):
            msgs.append("✗ ID3v2 魔数是大写 'EA3'，必须是小写 'ea3'（65 61 33）")
            msgs.append("   ff_id3v2_match 区分大小写 → ffmpeg/PSP 都会报找不到 EA3 头")
            msgs.append("   → 用修正后的 at3_to_aa3 重新生成即可")
        msgs.append("编码：%s（id=%d，codec_params=0x%06X）"
                    % (info.get("codec"), info.get("codec_id", -1), info.get("params", 0)))
        msgs.append("加密：%s %s" % ("是" if info.get("encrypted") else "否（eid=%d）" % info.get("eid", 0),
                                    "← 加密件绑授权，换机可能失效" if info.get("encrypted") else "OK"))
        sr = info.get("sample_rate", 0)
        msgs.append("采样率：%d Hz %s" % (sr, "OK" if sr == 44100 else "← PSP 建议 44100"))
        msgs.append("声道：%s %s" % (info.get("channels"), "OK" if info.get("channels") == 2 else "← 建议立体声"))
        fs = info.get("frame_size", 0)
        tbl = FRAME_SIZE if info.get("channels") == 2 else FRAME_SIZE_MONO
        msgs.append("帧长：%s 字节 → 约 %s kbps" % (fs, tbl.get(fs, "?")))
        if "frames" in info:
            msgs.append("码流：%d 字节 = %d 帧，余数 %d %s"
                        % (info["payload"], info["frames"], info["remainder"],
                           "OK" if info["remainder"] == 0 else "← 帧不对齐"))
            if sr:
                per = 2048 if info.get("codec_id") == 1 else 1024
                msgs.append("时长：约 %.2f 秒" % (info["frames"] * per / sr))
        if info.get("tags"):
            msgs.append("标签：%s" % ", ".join("%s(%d)" % (t[0], t[1]) for t in info["tags"]))
        msgs.append("音频起点：0x%x（头共 %d 字节）" % (info.get("audio_off", 0), info.get("audio_off", 0)))
        msgs.append("放进记忆棒 PSP/MUSIC/ 下；.aa3 与 .oma 等价")
        # 头部 hexdump：拿索尼原厂 .oma 跑同一个脚本即可逐字节对比
        n = min(info.get("audio_off", 106), len(d))
        msgs.append("头部 %d 字节 hexdump（跟 SonicStage 出的 .oma 对比用）：" % n)
        for i in range(0, n, 16):
            chunk = d[i:i + 16]
            hexpart = " ".join("%02x" % c for c in chunk)
            asc = "".join(chr(c) if 32 <= c < 127 else "." for c in chunk)
            msgs.append("    %04x  %-47s  %s" % (i, hexpart, asc))
    else:
        msgs.append("既不是 RIFF 也不是 EA3 —— 大概率是裸码流")
        msgs.append("文件头 16 字节：%s" % info.get("head"))
        for fs, br, n in info.get("frame_hits", []):
            msgs.append("  可能：帧长 %d（%d kbps）→ %d 帧" % (fs, br, n))
        msgs.append("裸码流没有任何元数据，PSP 音乐播放器无法识别，必须封装")
    return msgs


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    for p in sys.argv[1:]:
        if not os.path.isfile(p):
            print("[跳过] 不存在：%s" % p)
            continue
        d = open(p, "rb").read()
        print("=" * 72)
        print("文件：%s" % p)
        print("大小：%d 字节" % len(d))
        info = scan_riff(d) or scan_ea3(d) or scan_raw(d)
        print("容器：%s" % info["container"])
        if "chunks" in info:
            print("chunk 布局：")
            for cid, size, body in info["chunks"]:
                print("    %-6s size=%-8d @0x%x" % (cid, size, body))
        print("-" * 72)
        for m in verdict(info, d):
            print("  " + m)


if __name__ == "__main__":
    main()
