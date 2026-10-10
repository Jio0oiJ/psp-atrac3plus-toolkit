# psp-atrac3plus-toolkit

把任意音频一步转换成 **PSP 音乐播放器能直接播放的 `.aa3`**（ATRAC3plus / EA3 容器）。

不用 SonicStage，不用 `--ref` 参数，拖进去就能跑。

---

## 为什么需要这个

PSP 上有两种完全不同的「ATRAC3plus 文件」，很多人在这里踩坑：

| 容器 | 魔数 | 扩展名 | 用途 | PSP 音乐播放器 |
|---|---|---|---|---|
| **RIFF/WAVE** | `RIFF`…`WAVE` | `.at3` | 游戏音效资源 / 自制程序 | 不认 |
| **EA3 / OpenMG** | `ea3`（小写） | `.oma` / `.aa3` | Walkman、PSP 音乐播放 | **认** |

`psp_at3tool.exe` 编码出来的是 **RIFF `.at3`**，直接丢进 `PSP/MUSIC/` 不会显示；
手动改名成 `.aa3` 则会提示「损坏」——因为容器根本不对。

本项目做的最后一件事就是**换容器头**：把 RIFF 的 96 字节头换成索尼原厂的 3168 字节 EA3 头，
**音频数据一个字节都不动**，不重新编码，所以是秒级完成、零音质损失。

## 完整流程

```
任意音频 ──[ffmpeg]──────> 16bit / 44100Hz PCM WAV   （输入本身合规则跳过）
         ──[at3tool -e]──> .at3    RIFF 容器
         ──[换头]────────> .aa3    EA3 容器  ← PSP 认这个
```

## 快速开始

### 方式一：GUI（推荐）

1. 下载 Release 里的 `dist.zip`，解压。
2. 把 `ffmpeg.exe` 也放进同一个目录（**可选**，只在输入是 mp3/flac 等非 WAV 时才需要）。
3. 运行 `ATRAC3plus转PSP.exe` → 添加文件/文件夹 → 码率选 352 → 开始。
4. 产物 `.aa3` 拷到记忆棒 `PSP/MUSIC/` 下即可播放。

工具不用配路径，程序会自动在本程序同目录查找。

### 方式二：命令行

只有现成的 `.at3` 要换头时：

```bash
# 单个 / 多个文件（生成同名 .aa3，删掉原 .at3）
at3_to_aa3.exe song.at3

# 保留原 .at3
at3_to_aa3.exe --keep song.at3

# 整个文件夹递归
at3_to_aa3.exe D:\music\at3\

# 也可以直接把文件拖到 exe 图标上
```

源码版：`python at3_to_aa3.py song.at3`

---

## 目录结构

```
.
├─ atrac3plus_gui.py       GUI 源码：批量转换一步到位
├─ at3_to_aa3.py           核心：RIFF .at3 → EA3 .aa3 换头（不重编码）
├─ at3_inspect.py          诊断：dump EA3/RIFF 头，判断文件为何不播
├─ make_test_at3.py        生成合成 .at3 测试样本
├─ make_experiment.py      生成对照实验样本（同音频不同头）
├─ oma_header.bin          索尼原厂 EA3 头模板（3168 字节）
├─ ATRAC3plus转换链路分析.md  SonicStage 转换链路的逆向取证记录
└─ dist/                   已打包的可执行文件
   ├─ ATRAC3plus转PSP.exe  GUI 主程序（内置 oma_header.bin）
   ├─ at3_to_aa3.exe       换头命令行版
   ├─ psp_at3tool.exe      编码器（来自 PSP SDK）
   ├─ msvcr71.dll          at3tool 的运行库，必须同目录
   └─ oma_header.bin       原厂头模板
```

## 依赖

| 文件 | 必需 | 说明 |
|---|---|---|
| `psp_at3tool.exe` | ✅ | ATRAC3plus 编码器，来自 SCEI PSP SDK |
| `msvcr71.dll` | ✅ | at3tool 的运行库，必须与它同目录 |
| `oma_header.bin` | ✅ | 索尼原厂 EA3 头模板，GUI 已内置 |
| `ffmpeg.exe` | ⬜ 可选 | 只在输入不是 16bit/44100Hz WAV 时需要。**100.54 MiB 超过 GitHub 单文件上限，请到 Releases 附件下载** |

> `ffmpeg.exe` 也可以自己从 <https://www.gyan.dev/ffmpeg/builds/> 下载 essentials 版，
> 取出 `bin/ffmpeg.exe` 放到程序同目录即可。

## 码率选择

ATRAC3plus 立体声可用：48 / 64 / 96 / 128 / 160 / 192 / 256 / 320 / 352 kbps。

**推荐 352** —— 与索尼原厂文件完全一致（帧长 2048 字节，`params=0x0028FF`），音质最好。

## 验证

换头后的产物用 ffprobe 检查应得到：

```
codec_name=atrac3p
sample_rate=44100
channels=2
bit_rate=352800
format_name=oma
```

`format_name=oma` 是关键——说明容器被正确识别为 EA3。

---

## 技术细节

### EA3 头结构

```
[ID3v2.3 头 10B，魔数小写 "ea3"] + [EA3 块 96B，魔数大写 "EA3"] + 裸 ATRAC3plus 码流
```

索尼实际写出的文件是 3168 字节：ID3v2 里带 `TIT2`、`TLEN` 以及
`OMG_BKLSI` / `OMG_OLINF` / `OMG_TRACK` / `OMG_TIT2S` 等 OpenMG 私有帧。

### 一个坑：魔数大小写

`ffmpeg` 的 `libavformat/oma.h` 定义的是 `#define ID3v2_EA3_MAGIC "ea3"`（**小写**），
而 `ff_id3v2_match()` 是逐字节精确比较。写成大写 `EA3` 的话 ffprobe 会报
`Couldn't find the EA3 header !`，PSP 上表现为「损坏」。

### 自造头为什么不行

实测：只写最小化的 EA3 头（无 OpenMG 私有帧、无机器 ID）时，PSP 仍然拒绝播放。
对照实验（同一段音频、只有头不同）：

| 样本 | 做法 | SonicStage |
|---|---|---|
| A | 直接改名 `.at3` → `.aa3` | ✅ |
| B | 换成原厂头 | ✅ |
| C | 自造最小化头 | ❌ |
| D | 换头 + 改内容 ID | ✅ |

结论：头与音频之间**没有 MAC 绑定**，换头是安全的；但必须移植原厂头。
所以本项目默认从 `oma_header.bin` 移植，只改 `codec_params` / `TIT2` / `TLEN`。

### 编译

```bash
pip install pyinstaller

# GUI（--windowed 无控制台）
pyinstaller --onefile --windowed --name "ATRAC3plus转PSP" \
  --add-data "oma_header.bin;." atrac3plus_gui.py

# 命令行版（保留控制台，拖拽时才看得到输出）
pyinstaller --onefile --console --name at3_to_aa3 \
  --add-data "oma_header.bin;." at3_to_aa3.py
```

---

## 声明

`psp_at3tool.exe` 为索尼（SCEI）PSP SDK 中的工具，本仓库仅为方便使用而随附。
本项目不包含任何 OpenMG / DRM 相关代码，也不涉及绕过加密。
请仅对自己拥有合法权利的音乐使用。
