#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ATRAC3plus → PSP (.aa3) 一步到位

完整流程（每个文件）：
    任意音频 ──ffmpeg──> 44100Hz / 16bit / 立体声 WAV
             ──psp_at3tool -e -br N──> .at3（RIFF 容器）
             ──换壳──> .aa3（EA3 容器，PSP 音乐播放器认这个）

at3tool 用法（官方）：
    at3tool [-<option>] file1 file2
    -e          编码 file1(16bit linear PCM wav) → file2
    -br N       码率 N kbps
    -d          解码
    -loop S E / -wholeloop / -repeat N   循环用，听歌不需要

外部工具不用手填路径，按顺序在下面这些地方找：
    1. 本程序同目录            （自己换新版工具时放这里，优先级最高）
    2. 本程序同目录 bin\\
    3. 本程序同目录 tools\\     （旧布局，仍兼容）
    4. 单文件版内置            （打包时 --add-data 打进 exe 的）
    5. 系统 PATH

    psp_at3tool.exe   必须（另需 msvcr71.dll 在同一处）
    ffmpeg.exe        可选，仅当输入不是 16bit/44100Hz WAV 时需要
    oma_header.bin    原厂 EA3 头模板，已内置
"""
import ctypes
import json
import os
import queue
import shutil
import string
import subprocess
import sys
import tempfile
import threading
import traceback
import wave

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import at3_to_aa3 as M

APP_TITLE = "ATRAC3plus → PSP (.aa3) 一步到位"

# ATRAC3plus 立体声可用码率
BITRATES = ["352", "320", "256", "192", "160", "128", "96", "64", "48"]

AUDIO_EXT = (".wav", ".mp3", ".flac", ".ape", ".wma", ".m4a", ".aac",
             ".ogg", ".opus", ".aiff", ".aif", ".wv", ".tta", ".dsf")


def exe_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def config_path():
    return os.path.join(exe_dir(), "atrac3plus_gui.json")


def load_config():
    cfg = {"outdir": "", "bitrate": "352",
           "keep_at3": False, "keep_wav": False}
    try:
        with open(config_path(), "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    return cfg


def save_config(cfg):
    try:
        with open(config_path(), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def bundle_dir():
    """单文件版（PyInstaller --onefile）里 --add-data 打进来的文件会解压到 _MEIPASS"""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return exe_dir()


def find_tool(name):
    """查找顺序：程序同目录 → 同目录 bin\\ → 同目录 tools\\ → 单文件内置 → PATH

    便携版把 ffmpeg 等放在 bin\\ 下；单文件版全部内置在 exe 里。
    放在程序同目录的优先级最高，方便自己换新版工具。
    """
    for c in (os.path.join(exe_dir(), name),
              os.path.join(exe_dir(), "bin", name),
              os.path.join(exe_dir(), "tools", name),
              os.path.join(bundle_dir(), name)):
        if os.path.isfile(c):
            return c
    w = shutil.which(name)
    return w or ""


def find_at3tool():
    return find_tool("psp_at3tool.exe")


def find_ffmpeg():
    return find_tool("ffmpeg.exe")


# 中间文件（WAV / .at3）统一放这里，不落在输出目录里
TMP_ROOT = os.path.join(tempfile.gettempdir(), "psp_atrac3plus_tmp")


def tmp_workdir(idx):
    """每个文件一个独立的临时子目录，避免同名文件互相覆盖。

    注意文件名必须保持原样 —— convert_one 用 .at3 的文件名当曲目标题（TIT2），
    所以不能用前缀序号去破坏它，只能靠子目录隔离。
    """
    d = os.path.join(TMP_ROOT, "f%d" % idx)
    os.makedirs(d, exist_ok=True)
    return d


def list_drives():
    """列出本机所有本地盘符（含 U 盘 / 读卡器）。"""
    out = []
    if os.name != "nt":
        return out
    try:
        mask = ctypes.windll.kernel32.GetLogicalDrives()
    except Exception:
        return out
    for i, letter in enumerate(string.ascii_uppercase):
        if not (mask >> i) & 1:
            continue
        path = letter + ":\\"
        try:
            t = ctypes.windll.kernel32.GetDriveTypeW(path)
        except Exception:
            continue
        if t in (2, 3):            # 2 = 可移动（U盘/读卡器）, 3 = 本地硬盘
            out.append(path)
    return out


def is_psp_music_dir(p):
    """判断这个路径是不是「...\\PSP\\MUSIC」"""
    p = os.path.abspath(p)
    return (os.path.basename(p).upper() == "MUSIC"
            and os.path.basename(os.path.dirname(p)).upper() == "PSP")


def find_psp_devices():
    """找出看起来是 PSP / 记忆棒的盘：根目录有 PSP 文件夹，且里面有 MUSIC。

    返回 [(盘符, MUSIC 完整路径), ...]。个别卡把 MUSIC 直接放根目录，也认。
    """
    out = []
    for d in list_drives():
        try:
            if not os.path.isdir(d):
                continue
            psp = os.path.join(d, "PSP")
            music = ""
            if os.path.isdir(psp):
                for n in os.listdir(psp):
                    if n.upper() == "MUSIC" and os.path.isdir(os.path.join(psp, n)):
                        music = os.path.join(psp, n)
                        break
            if not music:
                for n in os.listdir(d):
                    if n.upper() == "MUSIC" and os.path.isdir(os.path.join(d, n)):
                        music = os.path.join(d, n)
                        break
            if music:
                out.append((d, music))
        except OSError:
            continue
    return out


def wav_is_ready(path):
    """是否已经是 at3tool 能直接吃的 16bit PCM WAV（44100Hz，1或2声道）"""
    try:
        with wave.open(path, "rb") as w:
            return (w.getsampwidth() == 2 and w.getframerate() == 44100
                    and w.getnchannels() in (1, 2)
                    and w.getcomptype() == "NONE")
    except Exception:
        return False


class App:
    def __init__(self, root):
        self.root = root
        root.title(APP_TITLE)
        root.geometry("900x680")
        root.minsize(800, 600)

        self.cfg = load_config()
        self.tools = {"at3tool": find_at3tool(), "ffmpeg": find_ffmpeg()}

        self.files = []
        # 文件 → 它所属的根目录（只有「添加文件夹」加进来的才有）。
        # 有根目录时，输出到别处会按相对路径重建原来的目录结构。
        self.file_root = {}
        self.q = queue.Queue()
        self.worker = None
        self.stop_flag = False
        self.psp_devs = []
        self._psp_active = False
        self._last_outdir = ""

        self._build_ui()
        self._scan_psp()
        if self.cfg.get("to_psp") and self.psp_devs:
            self.v_to_psp.set(True)
            self._toggle_psp()
        self._poll()

    # ---------------- UI ----------------
    def _build_ui(self):
        pad = {"padx": 6, "pady": 4}

        # ---- 顶部：工具状态（自动从程序同目录查找，无需手填路径）----
        top = ttk.LabelFrame(self.root, text="工具（自动在本程序同目录查找）")
        top.pack(fill="x", **pad)
        top.columnconfigure(0, weight=1)

        self.v_out = tk.StringVar(value=self.cfg["outdir"])
        self.v_tools = tk.StringVar()

        ttk.Label(top, textvariable=self.v_tools).grid(
            row=0, column=0, sticky="w", padx=6, pady=4)
        ttk.Button(top, text="打开程序目录", command=self._open_exedir).grid(
            row=0, column=1, padx=4, pady=4)
        ttk.Button(top, text="重新检测", command=self._redetect).grid(
            row=0, column=2, padx=4, pady=4)

        self._refresh_tools()

        # ---- 文件列表 ----
        mid = ttk.LabelFrame(self.root, text="待转换文件")
        mid.pack(fill="both", expand=True, **pad)
        mid.columnconfigure(0, weight=1)
        mid.rowconfigure(0, weight=1)

        lb_frame = ttk.Frame(mid)
        lb_frame.grid(row=0, column=0, rowspan=5, sticky="nsew", **pad)
        lb_frame.rowconfigure(0, weight=1)
        lb_frame.columnconfigure(0, weight=1)

        self.listbox = tk.Listbox(lb_frame, selectmode="extended")
        self.listbox.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(lb_frame, orient="vertical", command=self.listbox.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.listbox.configure(yscrollcommand=sb.set)

        btns = ttk.Frame(mid)
        btns.grid(row=0, column=1, sticky="nw", **pad)
        ttk.Button(btns, text="添加文件…", command=self._add_files).pack(fill="x", pady=2)
        ttk.Button(btns, text="添加文件夹…", command=self._add_dir).pack(fill="x", pady=2)
        ttk.Button(btns, text="移除选中", command=self._remove_sel).pack(fill="x", pady=2)
        ttk.Button(btns, text="清空", command=self._clear).pack(fill="x", pady=2)

        # ---- 选项 ----
        opt = ttk.LabelFrame(self.root, text="选项")
        opt.pack(fill="x", **pad)

        opt.columnconfigure(1, weight=1)

        ttk.Label(opt, text="码率：").grid(row=0, column=0, sticky="w", **pad)
        self.v_br = tk.StringVar(value=self.cfg.get("bitrate", "352"))
        ttk.Combobox(opt, textvariable=self.v_br, values=BITRATES,
                     width=6, state="readonly").grid(row=0, column=1, sticky="w", **pad)
        ttk.Label(opt, text="kbps（352 = 与索尼原厂一致，音质最好）").grid(
            row=0, column=2, sticky="w")

        ttk.Label(opt, text="输出目录：").grid(row=1, column=0, sticky="w", **pad)
        self.en_out = ttk.Entry(opt, textvariable=self.v_out)
        self.en_out.grid(row=1, column=1, sticky="ew", **pad)
        ttk.Button(opt, text="浏览", command=self._pick_outdir).grid(row=1, column=2, sticky="w", **pad)
        ttk.Label(opt, text="（留空 = 与源文件同目录；填写则保留原目录结构）").grid(
            row=1, column=3, sticky="w")

        self.v_keep_at3 = tk.BooleanVar(value=self.cfg.get("keep_at3", False))
        self.v_keep_wav = tk.BooleanVar(value=self.cfg.get("keep_wav", False))
        self.v_del_src = tk.BooleanVar(value=False)
        self.v_cover = tk.BooleanVar(value=self.cfg.get("cover", True))
        ttk.Checkbutton(opt, text="保留中间 .at3 文件",
                        variable=self.v_keep_at3).grid(row=2, column=0, columnspan=2, sticky="w", **pad)
        ttk.Checkbutton(opt, text="保留中间 WAV 文件",
                        variable=self.v_keep_wav).grid(row=2, column=2, sticky="w", **pad)
        ttk.Checkbutton(opt, text="转换后删除源文件（谨慎）",
                        variable=self.v_del_src).grid(row=2, column=3, sticky="w", **pad)
        ttk.Checkbutton(opt, text="自动嵌入专辑封面（cover.jpg / 同名图片，PSP 可显示）",
                        variable=self.v_cover).grid(row=3, column=0, columnspan=3, sticky="w", **pad)

        # ---- 输出到 PSP（USB 大容量存储设备）----
        pspf = ttk.LabelFrame(self.root, text="直接输出到 PSP（USB 设备）")
        pspf.pack(fill="x", **pad)

        self.v_psp = tk.StringVar()
        self.cb_psp = ttk.Combobox(pspf, textvariable=self.v_psp, width=44,
                                   state="readonly")
        self.cb_psp.pack(side="left", **pad)
        ttk.Button(pspf, text="刷新", command=self._scan_psp).pack(side="left", **pad)
        self.v_to_psp = tk.BooleanVar(value=self.cfg.get("to_psp", False))
        ttk.Checkbutton(pspf, text="直接输出到该设备的 MUSIC 文件夹",
                        variable=self.v_to_psp,
                        command=self._toggle_psp).pack(side="left", **pad)
        self.v_psp_info = tk.StringVar(value="")
        ttk.Label(pspf, textvariable=self.v_psp_info).pack(side="left", **pad)

        # ---- 运行 ----
        run = ttk.Frame(self.root)
        run.pack(fill="x", **pad)
        self.btn_start = ttk.Button(run, text="开始转换", command=self._start)
        self.btn_start.pack(side="left", **pad)
        self.btn_stop = ttk.Button(run, text="停止", command=self._stop, state="disabled")
        self.btn_stop.pack(side="left", **pad)
        ttk.Button(run, text="打开输出目录", command=self._open_outdir).pack(side="left", **pad)
        self.pb = ttk.Progressbar(run, mode="determinate")
        self.pb.pack(side="left", fill="x", expand=True, **pad)
        self.v_status = tk.StringVar(value="就绪")
        ttk.Label(run, textvariable=self.v_status).pack(side="left", **pad)

        # ---- 日志 ----
        logf = ttk.LabelFrame(self.root, text="日志")
        logf.pack(fill="both", expand=True, **pad)
        self.log = tk.Text(logf, height=12, wrap="none")
        self.log.pack(fill="both", expand=True, side="left")
        sb2 = ttk.Scrollbar(logf, command=self.log.yview)
        sb2.pack(fill="y", side="left")
        self.log.configure(yscrollcommand=sb2.set)

        self._log("程序目录：%s" % exe_dir())
        self._log("psp_at3tool.exe：%s" % (self.tools["at3tool"] or "未找到 —— 请放到程序同目录或 bin\\"))
        if self.tools["ffmpeg"]:
            self._log("ffmpeg.exe    ：%s" % self.tools["ffmpeg"])
        else:
            self._log("ffmpeg.exe    ：未找到 —— 没关系，输入是 16bit/44100Hz WAV 时不需要；"
                      "输入是 mp3/flac 等时才需要它先转 WAV")
        tpl, notes = M.resolve_template()
        if tpl:
            self._log("头部模板：%s（%d 字节）" % (tpl[2], len(tpl[0])))
        else:
            self._log("警告：没找到原厂头部模板，生成的 .aa3 PSP 可能不认")
        for n in notes:
            self._log("  " + n)

    def _refresh_tools(self):
        a = self.tools["at3tool"]
        f = self.tools["ffmpeg"]
        self.v_tools.set(
            "psp_at3tool.exe：%s        ffmpeg.exe：%s" %
            (a if a else "未找到，请放到程序同目录或 bin\\",
             f if f else "未找到（仅非 WAV 输入需要）"))

    def _redetect(self):
        self.tools["at3tool"] = find_at3tool()
        self.tools["ffmpeg"] = find_ffmpeg()
        self._refresh_tools()
        self._log("重新检测：psp_at3tool.exe=%s；ffmpeg.exe=%s" %
                  (self.tools["at3tool"] or "未找到",
                   self.tools["ffmpeg"] or "未找到"))

    def _scan_psp(self):
        """扫描 USB 设备，找出带 PSP\\MUSIC 的盘"""
        self.psp_devs = find_psp_devices()
        vals = [m for _d, m in self.psp_devs]
        self.cb_psp["values"] = vals
        if vals:
            if self.v_psp.get() not in vals:
                self.cb_psp.set(vals[0])
            self.v_psp_info.set("检测到 %d 个 PSP 设备" % len(vals))
        else:
            self.cb_psp.set("")
            self.v_psp_info.set("未检测到 —— 用 USB 线连上 PSP（或插记忆棒读卡器）后再刷新")
        if self.v_to_psp.get():
            self._toggle_psp()
        self._log("扫描 USB 设备：%s" % ("、".join(vals) if vals
                                        else "没找到带 PSP\\MUSIC 的盘"))

    def _toggle_psp(self):
        """勾选「直接输出到 PSP」时接管输出目录，取消勾选时还原"""
        if self.v_to_psp.get():
            m = self.v_psp.get().strip()
            if not m:
                self.v_to_psp.set(False)
                messagebox.showwarning(
                    APP_TITLE,
                    "没检测到 PSP 设备。\n\n"
                    "请先用 USB 线把 PSP 连上电脑（或插记忆棒读卡器），再点「刷新」。")
                return
            if not getattr(self, "_psp_active", False):
                self._last_outdir = self.v_out.get()
            self.v_out.set(m)
            self.en_out.configure(state="disabled")
            self._psp_active = True
        else:
            self.en_out.configure(state="normal")
            if getattr(self, "_psp_active", False):
                self.v_out.set(getattr(self, "_last_outdir", ""))
                self._psp_active = False

    def _open_exedir(self):
        os.startfile(exe_dir())

    def _pick_outdir(self):
        p = filedialog.askdirectory(title="选择输出目录")
        if p:
            self.v_out.set(p)

    def _add_files(self):
        ps = filedialog.askopenfilenames(
            title="选择音频文件",
            filetypes=[("音频文件", "*" + e) for e in AUDIO_EXT] + [("所有文件", "*.*")])
        for p in ps:
            if p not in self.files:
                self.files.append(p)
                self.listbox.insert("end", p)

    def _add_dir(self):
        d = filedialog.askdirectory(title="选择文件夹（会递归扫描音频文件）")
        if not d:
            return
        d = os.path.abspath(d)
        n = 0
        for root, _dirs, names in os.walk(d):
            for fn in sorted(names):
                if fn.lower().endswith(AUDIO_EXT):
                    p = os.path.join(root, fn)
                    if p not in self.files:
                        self.files.append(p)
                        self.listbox.insert("end", p)
                        # 记住它属于哪个文件夹，输出时好还原目录结构
                        self.file_root[os.path.abspath(p)] = d
                        n += 1
        top = os.path.basename(d) or d
        self._log("从文件夹加入了 %d 个文件：%s" % (n, d))
        self._log("  指定了输出目录时会保留目录结构，例如 %s\\<子目录>\\<文件名>.aa3"
                  % top)

    def _remove_sel(self):
        sel = list(self.listbox.curselection())[::-1]
        for i in sel:
            self.listbox.delete(i)
            self.file_root.pop(os.path.abspath(self.files[i]), None)
            del self.files[i]

    def _clear(self):
        self.listbox.delete(0, "end")
        self.files = []
        self.file_root = {}

    def _open_outdir(self):
        d = self.v_out.get()
        if not d and self.files:
            d = os.path.dirname(self.files[0])
        if d and os.path.isdir(d):
            os.startfile(d)

    def _log(self, msg):
        self.log.insert("end", msg + "\n")
        self.log.see("end")

    # ---------------- 运行 ----------------
    def _start(self):
        if not self.files:
            messagebox.showwarning(APP_TITLE, "请先添加要转换的文件")
            return
        at3 = self.tools["at3tool"]
        if not at3 or not os.path.isfile(at3):
            messagebox.showwarning(
                APP_TITLE,
                "没找到 psp_at3tool.exe。\n\n"
                "请把它（连同 msvcr71.dll）复制到本程序所在目录，"
                "或本程序目录下的 bin\\ 里：\n%s\n\n"
                "（点「打开程序目录」可直接前往）" % exe_dir())
            return
        if self.v_to_psp.get():
            m = self.v_psp.get().strip()
            if not m or not os.path.isdir(m):
                messagebox.showwarning(
                    APP_TITLE,
                    "选中的 PSP 设备现在访问不了：\n%s\n\n"
                    "请确认还连着，然后点「刷新」重新检测。" % (m or "(未选择)"))
                return
        # 保存配置
        self.cfg.update({"outdir": self._last_outdir or self.v_out.get(),
                         "bitrate": self.v_br.get(),
                         "keep_at3": self.v_keep_at3.get(),
                         "keep_wav": self.v_keep_wav.get(),
                         "cover": self.v_cover.get(),
                         "to_psp": self.v_to_psp.get()})
        save_config(self.cfg)

        self.stop_flag = False
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="enabled")
        self.pb["value"] = 0
        self.pb["maximum"] = len(self.files) * 3
        self.v_status.set("转换中…")

        self.worker = threading.Thread(target=self._run, daemon=True)
        self.worker.start()

    def _stop(self):
        self.stop_flag = True
        self.v_status.set("正在停止…")

    def _poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._log(payload)
                elif kind == "step":
                    self.pb.step(1)
                elif kind == "status":
                    self.v_status.set(payload)
                elif kind == "done":
                    self.btn_start.configure(state="enabled")
                    self.btn_stop.configure(state="disabled")
                    self.v_status.set(payload)
        except queue.Empty:
            pass
        self.root.after(120, self._poll)

    def _run(self):
        ok = fail = 0
        at3tool = self.tools["at3tool"]
        ffmpeg = self.tools["ffmpeg"]
        outdir = self.v_out.get().strip()
        br = self.v_br.get()
        keep_at3 = self.v_keep_at3.get()
        keep_wav = self.v_keep_wav.get()
        del_src = self.v_del_src.get()
        want_cover = self.v_cover.get()

        tpl, notes = M.resolve_template()
        for n in notes:
            self.q.put(("log", "  " + n))
        if tpl is None:
            self.q.put(("log", "错误：没有可用的原厂头部模板，放弃"))
            self.q.put(("done", "失败：无模板"))
            return

        cwd = os.path.dirname(at3tool) or None
        no_win = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.q.put(("log", "中间文件目录：%s" % TMP_ROOT))
        warned_depth = False

        for idx, src in enumerate(list(self.files)):
            if self.stop_flag:
                self.q.put(("log", "已停止"))
                break
            name = os.path.basename(src)
            base = os.path.splitext(name)[0]
            src_abs = os.path.abspath(src)
            # 「添加文件夹」进来的文件，输出到别处时按相对路径重建目录结构。
            # 以文件夹的**上一级**为基准，这样文件夹自己的名字也保留下来
            # （例如加 D:\音乐\范特西 → 输出 <out>\范特西\01.xxx.aa3）
            root = self.file_root.get(src_abs)
            if outdir and root:
                rel = os.path.relpath(os.path.dirname(src_abs),
                                      os.path.dirname(root))
                dst_dir = os.path.normpath(os.path.join(outdir, rel))
                disp = os.path.normpath(os.path.relpath(
                    src_abs, os.path.dirname(root)))
                # PSP 的 PSP/MUSIC 下面只认一层子目录，两层很可能扫不到
                if (not warned_depth and os.sep in rel
                        and is_psp_music_dir(outdir)):
                    self.q.put(("log", "   ⚠ 注意：PSP 的 MUSIC 下只认一层子目录，"
                                       "「%s」是两层，PSP 可能扫不到" % rel))
                    warned_depth = True
            elif outdir:
                dst_dir = outdir
                disp = name
            else:
                dst_dir = os.path.dirname(src_abs)
                disp = name
            try:
                os.makedirs(dst_dir, exist_ok=True)
            except Exception as e:
                self.q.put(("log", "[FAIL] %s：无法创建输出目录 %s (%s)" % (name, dst_dir, e)))
                fail += 1
                continue

            self.q.put(("log", "── %s" % disp))
            # 中间文件（WAV / .at3）一律放临时目录，不落在输出目录里。
            # 每个文件一个子目录，文件名保持原样 —— convert_one 拿 .at3 的
            # 文件名当曲目标题，不能加前缀破坏它。
            # 必须绝对路径：子进程 cwd 会被设成 at3tool 所在目录（找 msvcr71.dll），
            # 相对路径会被解析到那里去
            work = tmp_workdir(idx)
            tmp_wav = os.path.abspath(os.path.join(work, base + "__pcm.wav"))
            at3_path = os.path.abspath(os.path.join(work, base + ".at3"))

            # --- 1) 准备 16bit PCM WAV ---
            if wav_is_ready(src_abs):
                wav_path = src_abs
                self.q.put(("log", "   WAV 已是 44100Hz/16bit，直接使用"))
            else:
                if not ffmpeg or not os.path.isfile(ffmpeg):
                    self.q.put(("log", "[FAIL] %s：不是 16bit/44100Hz 的 WAV，"
                                       "需要 ffmpeg.exe 先转码，但程序同目录里没有找到它" % name))
                    fail += 1
                    continue
                cmd = [ffmpeg, "-v", "error", "-y", "-i", src_abs,
                       "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", tmp_wav]
                self.q.put(("log", "   ffmpeg → 44100Hz/16bit/立体声 WAV"))
                r = subprocess.run(cmd, capture_output=True, cwd=cwd,
                                   creationflags=no_win)
                if r.returncode != 0 or not os.path.isfile(tmp_wav):
                    err = (r.stderr or b"").decode("utf-8", "replace").strip()
                    self.q.put(("log", "[FAIL] %s：ffmpeg 转码失败 %s" % (name, err[:300])))
                    fail += 1
                    continue
                wav_path = tmp_wav
            self.q.put(("step", None))

            # --- 2) at3tool 编码 ---
            self.q.put(("log", "   at3tool -e -br %s" % br))
            r = subprocess.run([at3tool, "-e", "-br", br, wav_path, at3_path],
                               capture_output=True, cwd=cwd, creationflags=no_win)
            if r.returncode != 0 or not os.path.isfile(at3_path):
                out = (r.stdout or b"").decode("utf-8", "replace").strip()
                err = (r.stderr or b"").decode("utf-8", "replace").strip()
                self.q.put(("log", "[FAIL] %s：at3tool 失败 %s %s" % (name, out[:200], err[:200])))
                self._cleanup(tmp_wav, keep_wav, work, keep_at3)
                fail += 1
                continue
            self.q.put(("step", None))

            # --- 3) 换头 → .aa3 ---
            # 先找输出目录 / 源目录里的图片，找不到就从原始音频的内嵌封面里抠
            cover = (M.find_cover_for(at3_path, src_abs, ffmpeg=ffmpeg)
                     if want_cover else None)
            if cover:
                self.q.put(("log", "   封面：%s" % M.cover_label(cover)))
            try:
                good, dst, msg = M.convert_one(at3_path, keep=keep_at3,
                                               tag=True, tpl=tpl, cover=cover)
            except Exception:
                self.q.put(("log", "[FAIL] %s：换头异常\n%s" % (name, traceback.format_exc())))
                good, dst, msg = False, None, ""
            if not good:
                self.q.put(("log", "[FAIL] %s" % (msg or name)))
                try:
                    if os.path.isfile(at3_path):
                        os.remove(at3_path)
                except OSError:
                    pass
                self._cleanup(tmp_wav, keep_wav, work, keep_at3)
                fail += 1
                continue
            self.q.put(("log", "[OK]   %s" % msg))
            self.q.put(("step", None))

            # convert_one 把 .aa3 写在输入 .at3 旁边，也就是 tmp 的工作目录里。
            # 必须搬到真正的输出目录 —— 否则下一步清 tmp 会把成品一起删掉。
            final = os.path.join(dst_dir, base + ".aa3")
            if os.path.isfile(dst) and os.path.abspath(dst) != os.path.abspath(final):
                try:
                    shutil.move(dst, final)
                    dst = final
                except OSError as e:
                    self.q.put(("log", "[FAIL] %s：无法移到输出目录 %s (%s)"
                                % (name, dst_dir, e)))
                    self._cleanup(tmp_wav, keep_wav, work, keep_at3)
                    fail += 1
                    continue

            self._cleanup(tmp_wav, keep_wav, work, keep_at3)
            if del_src and os.path.abspath(src) != os.path.abspath(at3_path):
                try:
                    os.remove(src)
                    self.q.put(("log", "   已删除源文件"))
                except OSError as e:
                    self.q.put(("log", "   源文件删除失败：%s" % e))
            ok += 1

        self.q.put(("log", "=" * 60))
        self.q.put(("log", "完成：成功 %d，失败 %d" % (ok, fail)))
        if outdir and is_psp_music_dir(outdir):
            self.q.put(("log", "已直接写进 PSP 的 MUSIC 文件夹，拔掉 USB 线即可播放"))
        else:
            self.q.put(("log", "把 .aa3 拷到记忆棒 PSP/MUSIC/ 下即可播放"))
        self.q.put(("done", "完成：成功 %d，失败 %d" % (ok, fail)))

    def _cleanup(self, path, keep, work=None, keep_dir=False):
        """删掉中间文件；顺带把 TMP_ROOT 下那个临时子目录一起清掉。"""
        if keep:
            return
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass
        if work and not keep_dir:
            shutil.rmtree(work, ignore_errors=True)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
