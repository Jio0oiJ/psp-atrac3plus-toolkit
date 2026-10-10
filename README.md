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

**用「添加文件夹」时，输出会保留原来的目录结构**（含你添加的那个文件夹本身的名字）：

```
D:\音乐\范特西\              ← 添加这个文件夹
  01. 爱在西元前.mp3
  Disc2\02. 爸我回来了.mp3
        ↓ 输出目录 E:\PSP
E:\PSP\范特西\01. 爱在西元前.aa3
E:\PSP\范特西\Disc2\02. 爸我回来了.aa3
```

直接拷 `范特西` 整个文件夹到记忆棒 `PSP/MUSIC/` 下即可。
单独「添加文件」进来的仍然平铺到输出目录里。

### 直接写到 PSP（USB）

用 USB 线把 PSP 连上电脑（或插记忆棒读卡器），程序会自动扫描所有盘符，
找出**根目录有 `PSP` 文件夹、里面还有 `MUSIC`** 的那个盘，判定为 PSP 设备。

勾选「直接输出到该设备的 MUSIC 文件夹」，输出目录会被自动接管成
`X:\PSP\MUSIC\`，转完拔线就能在 PSP 上听。设备没认出来就点「刷新」。

> 注意：PSP 的 `MUSIC` 下**只认一层子目录**。如果你添加的文件夹里面还有子文件夹，
> 日志会提示「两层，PSP 可能扫不到」，这种情况建议一层一层加。

### 中间文件

转换过程产生的 WAV 和 `.at3` 一律放在系统的临时目录
（`%TEMP%\psp_atrac3plus_tmp`），**不会**落到输出目录里弄脏文件。
每个文件一个独立子目录，转完自动清理。勾选「保留中间文件」时它们会留在那里。

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
--no-apic        只写索尼 GEOB，不写标准 APIC（PSP 照样显示封面，省 12 KB）
```

## 关于封面的实现

索尼的 `.oma` 把封面放在两个 **GEOB** 帧里（`OMG_FENCA1` 主图、`OMG_TDFCA` 缩略图位，
索尼两张填的是同一张图），**根本没有 APIC 帧**。

真机对照实验（头部都做了 1024 对齐）：

| 头部写法 | 1024 对齐 | PSP 结果 |
| --- | --- | --- |
| 无封面（3168） | ✓ | 能播，不显示封面 |
| 索尼 GEOB×2（28768） | ✓ | **能播 + 显示封面** |
| **GEOB×2 + 标准 APIC（41056）** | ✓ | **能播 + 显示封面** ← 本工具默认 |
| 索尼原厂头部（150624） | ✓ | 能播 + 显示封面 |

结论：

- **对齐才是关键**。前面那张「只有 GEOB 不显示」的旧结论是错的 ——
  当时它既没对齐也放不出声，真正的变量是 1024 对齐，不是有没有 APIC。
  对齐之后，光写索尼 GEOB×2 PSP 一样能显示封面。
- 本工具**仍然两种都写**：GEOB×2 给 SonicStage / Walkman 看，
  APIC 给 PSP 看、顺带让 foobar2000 / Windows 资源管理器等第三方也能读到。
  代价只是每个文件多 12 KB。想省就用 `--no-apic`（PSP 照样能显示）。

### 一个坑：ID3 标签必须补到 1024 的整数倍

索尼所有原厂文件的 ID3v2 标签长度都是 **1024 的整数倍** —— 无封面 3072，
带封面 150528，实测 32 个文件无一例外。

插完封面帧后长度不再对齐，PSP 就会出现「**封面能显示、声音放不出来**」：
它按对齐后的长度推算音频起始偏移，从错误的位置开始解码，ATRAC 帧同步失败。
所以插完帧会自动补 0 到 1024 的整数倍（41056 而不是 40350）。

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
