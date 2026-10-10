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

外部工具不用手填路径，放本程序同目录即可（同目录 tools\\ 也认）：
    psp_at3tool.exe   必须（另需同目录 msvcr71.dll）
    ffmpeg.exe        可选，仅当输入不是 16bit/44100Hz WAV 时需要
    oma_header.bin    原厂 EA3 头模板，已内置，同目录有则优先用外部
"""
import json
import os
import queue
import shutil
import subprocess
import sys
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


def find_tool(name):
    """只在本程序同目录 / 同目录 tools\\ 下找，找不到再试 PATH"""
    for c in (os.path.join(exe_dir(), name),
              os.path.join(exe_dir(), "tools", name)):
        if os.path.isfile(c):
            return c
    w = shutil.which(name)
    return w or ""


def find_at3tool():
    return find_tool("psp_at3tool.exe")


def find_ffmpeg():
    return find_tool("ffmpeg.exe")


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

        self._build_ui()
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
        ttk.Entry(opt, textvariable=self.v_out).grid(row=1, column=1, sticky="ew", **pad)
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
        self._log("psp_at3tool.exe：%s" % (self.tools["at3tool"] or "未找到 —— 请放到程序同目录"))
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
            (a if a else "未找到，请放到程序同目录",
             f if f else "未找到（仅非 WAV 输入需要）"))

    def _redetect(self):
        self.tools["at3tool"] = find_at3tool()
        self.tools["ffmpeg"] = find_ffmpeg()
        self._refresh_tools()
        self._log("重新检测：psp_at3tool.exe=%s；ffmpeg.exe=%s" %
                  (self.tools["at3tool"] or "未找到",
                   self.tools["ffmpeg"] or "未找到"))

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
                "请把它复制到本程序所在目录：\n%s\n\n"
                "（点「打开程序目录」可直接前往）" % exe_dir())
            return
        # 保存配置
        self.cfg.update({"outdir": self.v_out.get(), "bitrate": self.v_br.get(),
                         "keep_at3": self.v_keep_at3.get(),
                         "keep_wav": self.v_keep_wav.get(),
                         "cover": self.v_cover.get()})
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

        for src in list(self.files):
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
            # 必须绝对路径：子进程 cwd 会被设成 at3tool 所在目录（找 msvcr71.dll），
            # 相对路径会被解析到那里去
            tmp_wav = os.path.abspath(os.path.join(dst_dir, base + "__pcm.wav"))
            at3_path = os.path.abspath(os.path.join(dst_dir, base + ".at3"))

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
                self._cleanup(tmp_wav, keep_wav)
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
                self._cleanup(tmp_wav, keep_wav)
                fail += 1
                continue
            self.q.put(("log", "[OK]   %s" % msg))
            self.q.put(("step", None))

            self._cleanup(tmp_wav, keep_wav)
            if del_src and os.path.abspath(src) != os.path.abspath(at3_path):
                try:
                    os.remove(src)
                    self.q.put(("log", "   已删除源文件"))
                except OSError as e:
                    self.q.put(("log", "   源文件删除失败：%s" % e))
            ok += 1

        self.q.put(("log", "=" * 60))
        self.q.put(("log", "完成：成功 %d，失败 %d" % (ok, fail)))
        self.q.put(("log", "把 .aa3 拷到记忆棒 PSP/MUSIC/ 下即可播放"))
        self.q.put(("done", "完成：成功 %d，失败 %d" % (ok, fail)))

    def _cleanup(self, path, keep):
        if keep:
            return
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
