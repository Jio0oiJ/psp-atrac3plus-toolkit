# ATRAC3plus → PSP (.aa3)

把任意音频转换成 **PSP 音乐播放器能直接播放的 `.aa3`** 文件。

## 作用

`psp_at3tool.exe` 编码出来的是 RIFF 容器的 `.at3`，PSP 音乐播放器不认 —— 放进去不显示，手动改名成 `.aa3` 则提示损坏。

本工具在编码后把容器头换成索尼原厂的 EA3 头，**音频数据一个字节不动、不重新编码**，所以秒级完成、零音质损失。

流程：

```
任意音频 →[ffmpeg]→ PCM WAV →[at3tool -e]→ .at3 →[换头]→ .aa3
```

产物拷到记忆棒 `PSP/MUSIC/` 下即可播放。

## 用法

### GUI（推荐）

1. 下载 Release 里的 `dist.zip`，解压（已包含 ffmpeg）。
2. 运行 `ATRAC3plus转PSP.exe` → 添加文件或文件夹 → 码率选 352 → 开始。

工具不用配路径，程序自动在本程序同目录查找。

### 命令行

已有 `.at3`、只想换头时：

```bash
at3_to_aa3.exe song.at3          # 生成同名 .aa3，删除原文件
at3_to_aa3.exe --keep song.at3   # 保留原 .at3
at3_to_aa3.exe D:\music\at3\     # 整个文件夹递归
```

也可以直接把文件拖到 exe 图标上。

## 下载

[Releases](https://github.com/Jio0oiJ/psp-atrac3plus-toolkit/releases) 页面下载 `dist.zip`，解压即用。

包内 `ffmpeg.exe` 只在输入不是 16bit/44100Hz WAV 时才需要，用不到可以删。

## 源码编译

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name "ATRAC3plus转PSP" --add-data "oma_header.bin;." atrac3plus_gui.py
pyinstaller --onefile --console  --name at3_to_aa3        --add-data "oma_header.bin;." at3_to_aa3.py
```

`psp_at3tool.exe` 为索尼（SCEI）PSP SDK 中的工具，随附仅为方便使用。
请仅对自己拥有合法权利的音乐使用。
