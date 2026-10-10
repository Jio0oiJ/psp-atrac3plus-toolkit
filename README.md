# ATRAC3plus → PSP (.aa3)

把任意音频转换成 **PSP 音乐播放器能直接播放、且带专辑封面的 `.aa3`** 文件。

## 作用

`psp_at3tool.exe` 编码出来的是 RIFF 容器的 `.at3`，PSP 音乐播放器不认 —— 放进去不显示，手动改名成 `.aa3` 则提示损坏。

本工具在编码后把容器头换成索尼原厂的 EA3 头，**音频数据一个字节不动、不重新编码**，所以秒级完成、零音质损失。

流程：

```
任意音频 →[ffmpeg]→ PCM WAV →[at3tool -e]→ .at3 →[换头+写封面]→ .aa3
```

产物拷到记忆棒 `PSP/MUSIC/` 下即可播放，封面在 PSP 上正常显示。

## 用法

### GUI（推荐）

1. 下载 Release 里的 `dist.zip`，解压（已包含 ffmpeg）。
2. 运行 `ATRAC3plus转PSP.exe` → 添加文件或文件夹 → 码率选 352 → 开始。

默认勾选「自动嵌入专辑封面」。工具不用配路径，程序自动在本程序同目录查找。

### 封面怎么放

其实大多数情况什么都不用做 —— 转换时会按下面顺序找封面，找到就内嵌
（自动缩到 640×640 并转成 JPEG）：

1. 与音频同名的图片，如 `01. Fallen Embers.mp3` → `01. Fallen Embers.jpg`
2. 同目录下的 `cover.jpg` / `cover.jpeg` / `cover.png` / `cover.bmp` /
   `folder.jpg` / `album.jpg` / `front.jpg` / `artwork.jpg`
3. 同目录下唯一的那个图片文件
4. **原始音频自己内嵌的封面** —— mp3 的 ID3v2 APIC、flac 的 picture block、
   m4a 的 covr 等，直接抠出来用

第 4 步意味着：直接用带封面的 mp3/flac/m4a 转就行，不用另外准备图片文件。
mp3 是程序自己解析的（不需要 ffmpeg）；flac / m4a / ogg / wma 等会调用
同目录的 `ffmpeg.exe` 来抠。

整张专辑想统一用一张外部封面时，往文件夹里丢一个 `cover.jpg`，它会优先于内嵌封面。

### 命令行

已有 `.at3`、只想换头时：

```bash
at3_to_aa3.exe song.at3          # 生成同名 .aa3，删除原文件
at3_to_aa3.exe --keep song.at3   # 保留原 .at3
at3_to_aa3.exe D:\music\at3\     # 整个文件夹递归
```

也可以直接把文件拖到 exe 图标上。

封面相关（默认已自动开启，一般不用加）：

```bash
--cover [图片]   指定封面；不给值则自动按上面顺序找
--no-cover       不找封面
--no-apic        只写索尼 GEOB，不写标准 APIC（PSP 将不显示封面）
```

## 关于封面的实现

索尼的 `.oma` 把封面放在两个 **GEOB** 帧里（`OMG_FENCA1` 主图、`OMG_TDFCA` 缩略图位，
索尼两张填的是同一张图），**根本没有 APIC 帧**。

但真机对照实验表明 **PSP 读的是标准 APIC**：

| 头部写法 | PSP 结果 |
| --- | --- |
| 无封面 | 不显示 |
| 索尼 GEOB×2 | 不显示 |
| **GEOB×2 + 标准 APIC** | **显示封面** |
| 索尼原厂头部 | 显示（原厂当然可以） |

所以本工具**同时写两种**：GEOB×2 给 SonicStage / Walkman 看，APIC 给 PSP 看，两边通吃。
带 640×640 封面时头部约 40 KB（无封面时 3168 字节），音频部分不受影响。

## 下载

[Releases](https://github.com/Jio0oiJ/psp-atrac3plus-toolkit/releases) 页面下载 `dist.zip`，解压即用。

包内 `ffmpeg.exe` 只在输入不是 16bit/44100Hz WAV 时才需要，用不到可以删。

## 源码编译

```bash
pip install pyinstaller pillow
pyinstaller --onefile --windowed --name "ATRAC3plus转PSP" --collect-all PIL --add-data "oma_header.bin;." atrac3plus_gui.py
pyinstaller --onefile --console  --name at3_to_aa3        --collect-all PIL --add-data "oma_header.bin;." at3_to_aa3.py
```

`psp_at3tool.exe` 为索尼（SCEI）PSP SDK 中的工具，随附仅为方便使用。
请仅对自己拥有合法权利的音乐使用。
