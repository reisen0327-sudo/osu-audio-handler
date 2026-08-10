# -*- coding: utf-8 -*-
"""
osu-audio-handler —— 为 osu!stable mania 谱面开头添加空白音频的工具
解决 first note lag：给音频开头加空白，并把谱面时间轴整体后移，保持 note 与音乐相对位置不变。

功能：
  1. 导入 .osz：自动识别每个谱面（.osu）与音频（AudioFilename）的对应关系，
     可单选/多选/全选处理组；输出完整 .osz + 可单独导出音频。
  2. 单独导入音频文件：输出处理后的音频。
  3. 自定义空白长度（ms）。
GUI: tkinter/ttk（中文），后台线程 + queue 消息泵。
CLI: --selftest <dir>  生成合成测试数据并自校验（供打包后验证 exe）。
"""
import os
import queue
import sys
import threading
import traceback

# GUI 相关（模块级导入，便于测试实例化 App）
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# ---------------------------------------------------------------------------
# 核心流程（与 GUI 无关，可测试）
# ---------------------------------------------------------------------------

DEFAULT_MS = 400


def suffix_for(ms):
    return "_plus%dms" % int(ms)


def unique_path(path):
    """若目标已存在，自动追加 (1)/(2)... 返回新路径。"""
    if not os.path.exists(path):
        return path
    root, ext = os.path.splitext(path)
    i = 1
    while True:
        cand = "%s(%d)%s" % (root, i, ext)
        if not os.path.exists(cand):
            return cand
        i += 1


# ---------------------------------------------------------------------------
# 自测（打包后验证 exe 用；无控制台时写文件）
# ---------------------------------------------------------------------------

def _safe_print(msg):
    try:
        print(msg)
    except Exception:
        pass


def _selftest(outdir):
    import struct
    from audio_utils import prepend_silence_mp3
    from osz_utils import analyze_osz, process_osz, shift_osu_bytes
    import io
    import zipfile

    os.makedirs(outdir, exist_ok=True)
    result = []

    def check(name, cond, detail=""):
        result.append("%s: %s %s" % ("PASS" if cond else "FAIL", name, detail))

    # ---- 1) 构造测试 ogg：1 秒 44.1k 单声道正弦波 ----
    lib = _load_for_test()
    ogg_bytes = _make_test_ogg(lib, 44100, 1, 1.0)
    check("生成测试 ogg", len(ogg_bytes) > 100)

    # ---- 2) 构造测试 mp3（ID3v2 tag + 3 个静音帧）----
    from audio_utils import _find_first_mp3_frame, _make_silent_frame
    frame = _make_silent_frame(bytes([0xFF, 0xFB, 0x90, 0x00]), 44100, 2, True)
    mp3_payload = frame * 3
    id3_size = len(b"ID3TESTDATA")
    id3 = b"ID3\x04\x00\x00" + bytes([(id3_size >> 21) & 0x7F, (id3_size >> 14) & 0x7F,
                                      (id3_size >> 7) & 0x7F, id3_size & 0x7F]) + b"ID3TESTDATA"
    mp3_bytes = id3 + mp3_payload
    off, hdr = _find_first_mp3_frame(mp3_bytes)
    check("mp3 第一帧定位", off == len(id3))

    # ---- 3) mp3 前插静音：ID3 后插入，原数据保留 ----
    mp3_new = prepend_silence_mp3(mp3_bytes, 400)
    off2, _ = _find_first_mp3_frame(mp3_new)
    check("mp3 插入点位于 ID3 之后", off2 == len(id3))
    check("mp3 原数据原样保留", mp3_new[len(id3) + (len(mp3_new) - len(id3) - len(mp3_payload)):] == mp3_payload)

    # ---- 4) 构造测试 .osu ----
    osu_text = """osu file format v14

[General]
AudioFilename: song.mp3
PreviewTime: 1234

[Metadata]
Version:Insane

[TimingPoints]
0,400.0,4,2,0,60,1,0
1000,400.0,4,2,0,60,1,0
1500,-100,4,2,0,50,0,1

[HitObjects]
64,192,200,1,0,0:0:0:0:
256,192,600,128,0,300:0:0:0:0:
"""
    shifted = shift_osu_bytes(osu_text.encode("utf-8"), 400, version_suffix="[+400ms]")
    st = shifted.decode("utf-8")
    check("红线后移", "400,400.0" in st)
    check("第二条红线后移", "1400,400.0" in st)
    check("绿线后移", "1900,-100" in st)
    check("PreviewTime 后移", "PreviewTime: 1634" in st)
    check("note 后移", ",192,600,1,0" in st)
    check("mania hold 时间后移", "256,192,1000,128,0,700:0:0:0:0:" in st)
    check("难度名加后缀", "Version:Insane [+400ms]" in st)
    check("BOM 保留", shifted.startswith(b"\xef\xbb\xbf") == osu_text.startswith("\ufeff"))
    check("不重复加后缀", shift_osu_bytes(shifted, 0, version_suffix="[+400ms]").decode("utf-8").count("+400ms") == 1)

    osu_with_events = """osu file format v14

[General]
AudioFilename: song.ogg
PreviewTime: 0

[Metadata]
Version:Normal

[Events]
1,100,"video.mp4"
2,5000,9000
3,10000,20000,255,255,255
5,0,"hit.wav",15000
 M,0,2000,4000,0,320
 F,0,3000,,1
 T,Failure,1000,2000
  M,0,0,0,0,320

[TimingPoints]
0,500,4,2,0,60,1,0

[HitObjects]
64,192,100,1,0,0:0:0:0:
"""
    se = shift_osu_bytes(osu_with_events.encode("utf-8"), 400).decode("utf-8")
    check("video 后移", '1,500,"video.mp4"' in se)
    check("break 后移", "2,5400,9400" in se)
    check("colour 后移", "3,10400,20400" in se)
    check("sample 事件后移", '5,0,"hit.wav",15400' in se)
    check("SB M 命令后移", " M,0,2400,4400,0,320" in se)
    check("SB F 无 end 后移", " F,0,3400,,1" in se)
    check("触发器时间后移", " T,Failure,1400,2400" in se)
    check("触发器内命令后移", "  M,0,400,400,0,320" in se)

    # ---- 5) 构造 osz 并整体处理 ----
    osz_src = os.path.join(outdir, "test_in.osz")
    bg = b"\x89PNG\r\n\x1a\nFAKE_BG_IMAGE_DATA"
    with zipfile.ZipFile(osz_src, "w") as z:
        z.writestr("song.mp3", mp3_bytes, compress_type=zipfile.ZIP_STORED)
        z.writestr("song.ogg", ogg_bytes, compress_type=zipfile.ZIP_STORED)
        z.writestr("beatmap1.osu", osu_text.encode("utf-8"))
        z.writestr("beatmap2.osu", osu_with_events.encode("utf-8"))
        z.writestr("bg.jpg", bg)

    groups, entries = analyze_osz(osz_src)
    names = sorted(a for a, _ in groups)
    check("识别谱面-音频对应", names == ["song.mp3", "song.ogg"], str(names))

    prog = []
    analyze_osz(osz_src, progress=lambda i, t, n: prog.append((i, t)))
    check("解析进度回调完整", bool(prog) and prog[-1] == (len(entries), len(entries)), str(prog[-1] if prog else None))

    osz_dst = os.path.join(outdir, "test_out.osz")
    sel = {"song.mp3"}
    logs = []
    process_osz(osz_src, osz_dst, sel, 400, log=logs.append, version_suffix="[+400ms]")
    with zipfile.ZipFile(osz_dst) as z:
        names_out = set(z.namelist())
        check("osz 条目完整", names_out == {"song.mp3", "song.ogg", "beatmap1.osu", "beatmap2.osu", "bg.jpg"})
        bg_out = z.read("bg.jpg")
        check("图片未改动", bg_out == bg)
        m1 = z.read("beatmap1.osu").decode("utf-8")
        check("组内谱面已后移", "400,400.0" in m1)
        check("组内谱面难度名加后缀", "Version:Insane [+400ms]" in m1)
        check("组内谱面 mania hold 保持长度", "256,192,1000,128,0,700:0:0:0:0:" in m1)
        m2 = z.read("beatmap2.osu").decode("utf-8")
        check("未选组谱面未动", "0,500,4,2" in m2)
        check("未选组 note 未动", ",192,100,1,0" in m2)
        check("未选组难度名未动", "Version:Normal" in m2 and "+400ms" not in m2)
        ogg_out = z.read("song.ogg")
        mp3_out = z.read("song.mp3")
    check("mp3 时长增加（帧数）", (len(mp3_out) - len(mp3_bytes)) > 0)
    check("未选组 ogg 未处理", ogg_out == ogg_bytes)

    # 再处理 song.ogg 组
    osz_dst2 = os.path.join(outdir, "test_out2.osz")
    process_osz(osz_src, osz_dst2, {"song.ogg"}, 400)
    with zipfile.ZipFile(osz_dst2) as z:
        m2b = z.read("beatmap2.osu").decode("utf-8")
        check("ogg 组谱面后移", "400,500,4,2" in m2b)
        ogg_out2 = z.read("song.ogg")
    frames_new = _read_ogg_frames(lib, ogg_out2)
    check("ogg 时长增加 400ms", abs(frames_new - (44100 + 0.4 * 44100)) <= 100, "frames=%d" % frames_new)

    # ---- 6) 单独音频导出路径（复用 process_audio_file）----
    import tempfile
    from audio_utils import process_audio_file
    with tempfile.TemporaryDirectory() as td:
        s = os.path.join(td, "song.ogg")
        d = os.path.join(td, "song_out.ogg")
        with open(s, "wb") as f:
            f.write(ogg_bytes)
        process_audio_file(s, d, 400)
        frames_d = _read_ogg_frames(lib, open(d, "rb").read())
        check("单独导出 ogg 时长 +400ms", abs(frames_d - (44100 + 0.4 * 44100)) <= 100)

    outfile = os.path.join(outdir, "selftest_result.txt")
    with open(outfile, "w", encoding="utf-8") as f:
        f.write("\n".join(result) + "\n")
    _safe_print("\n".join(result))
    _safe_print("结果已写入: %s" % outfile)
    return all(r.startswith("PASS") for r in result)


def _load_for_test():
    from audio_utils import _load_libsndfile
    return _load_libsndfile()


def _make_test_ogg(lib, sr, channels, seconds):
    import math
    import tempfile
    from audio_utils import _SF_INFO, _SFM_WRITE, _SF_FORMAT_OGG, _SFC_SET_VBR_ENCODING_QUALITY
    import ctypes
    td = tempfile.mkdtemp()
    p = os.path.join(td, "t.ogg")
    info = _SF_INFO(samplerate=sr, channels=channels, format=_SF_FORMAT_OGG | 0x0060)
    fout = lib.sf_open(p.encode("utf-8"), _SFM_WRITE, ctypes.byref(info))
    if not fout:
        raise RuntimeError("test ogg write failed")
    q = ctypes.c_float(0.4)
    lib.sf_command(fout, _SFC_SET_VBR_ENCODING_QUALITY, ctypes.byref(q), ctypes.sizeof(q))
    total = int(sr * seconds)
    buf = (ctypes.c_float * (total * channels))()
    for i in range(total):
        v = 0.3 * math.sin(2 * math.pi * 440 * i / sr)
        for c in range(channels):
            buf[i * channels + c] = v
    lib.sf_write_float(fout, buf, total * channels)
    lib.sf_close(fout)
    with open(p, "rb") as f:
        data = f.read()
    return data


def _read_ogg_frames(lib, ogg_bytes):
    import tempfile
    from audio_utils import _SF_INFO, _SFM_READ
    import ctypes
    td = tempfile.mkdtemp()
    p = os.path.join(td, "t.ogg")
    with open(p, "wb") as f:
        f.write(ogg_bytes)
    info = _SF_INFO()
    fin = lib.sf_open(p.encode("utf-8"), _SFM_READ, ctypes.byref(info))
    if not fin:
        raise RuntimeError("read ogg failed")
    lib.sf_close(fin)
    return info.frames


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

APP_TITLE = "osu-audio-handler - 谱面开头空白音频工具"


def run_gui():
    try:
        import ctypes as _ct
        _ct.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    root = tk.Tk()
    App(root)
    root.mainloop()


class App:
    def __init__(self, root):
        self.root = root
        root.title(APP_TITLE)
        root.geometry("900x880")
        root.minsize(760, 760)

        self.mode = tk.StringVar(value="osz")
        self.osz_path = tk.StringVar()
        self.ms_var = tk.StringVar(value=str(DEFAULT_MS))
        self.out_osz = tk.BooleanVar(value=True)
        self.out_audio = tk.BooleanVar(value=True)
        self.osz_dst = tk.StringVar()
        self.audio_dir = tk.StringVar()
        self.rename_version = tk.BooleanVar(value=True)   # 难度名加后缀，避免覆盖原谱面
        self.audio_files = []          # 音频模式：文件路径列表
        self.groups = []               # osz 模式：[(audio_name, [maps])]
        self.group_vars = {}           # audio_name -> BooleanVar
        self.worker = None
        self._analyzing = False        # osz 后台解析中标志
        self._aprog_first = True
        self.q = queue.Queue()

        self._build()
        self.root.after(100, self._pump)

    # ---------- 界面 ----------
    def _build(self):
        pad = {"padx": 10, "pady": 4}
        frm = ttk.Frame(self.root, padding=8)
        frm.pack(fill="both", expand=True)

        # 1. 导入方式
        ttk.Label(frm, text="1. 导入方式", font=("", 10, "bold")).pack(anchor="w", **pad)
        mode_row = ttk.Frame(frm)
        mode_row.pack(fill="x", **pad)
        ttk.Radiobutton(mode_row, text="导入 .osz 谱面包（自动识别谱面与音频对应关系）",
                        variable=self.mode, value="osz", command=self._mode_changed).pack(side="left")
        ttk.Radiobutton(mode_row, text="单独导入音频文件（.mp3/.ogg 等）",
                        variable=self.mode, value="audio", command=self._mode_changed).pack(side="left", padx=(16, 0))

        self.panel_container = ttk.Frame(frm)
        self.panel_container.pack(fill="x", **pad)

        self.panel_osz = ttk.Frame(self.panel_container)
        self.panel_osz.grid(row=0, column=0, sticky="we")
        self.btn_pick_osz = ttk.Button(self.panel_osz, text="选择 .osz 文件...", command=self._pick_osz)
        self.btn_pick_osz.pack(side="left")
        self.lbl_osz = ttk.Label(self.panel_osz, text="未选择", foreground="#666")
        self.lbl_osz.pack(side="left", padx=8)

        self.panel_audio = ttk.Frame(self.panel_container)
        self.panel_audio.grid(row=0, column=0, sticky="we")
        self.btn_pick_audio = ttk.Button(self.panel_audio, text="选择音频文件...", command=self._pick_audio)
        self.btn_pick_audio.pack(side="left")
        self.lbl_audio = ttk.Label(self.panel_audio, text="未选择", foreground="#666")
        self.lbl_audio.pack(side="left", padx=8)
        self.panel_audio.grid_remove()

        # 2. 处理对象
        ttk.Label(frm, text="2. 处理对象（谱面-音频组）", font=("", 10, "bold")).pack(anchor="w", **pad)
        listbox_row = ttk.Frame(frm)
        listbox_row.pack(fill="both", expand=True, **pad)
        self.list_canvas = tk.Canvas(listbox_row, highlightthickness=0, bg="#ffffff")
        sb = ttk.Scrollbar(listbox_row, orient="vertical", command=self.list_canvas.yview)
        self.list_frame = ttk.Frame(self.list_canvas)
        self.list_frame.bind("<Configure>",
                             lambda e: self.list_canvas.configure(scrollregion=self.list_canvas.bbox("all")))
        self.list_canvas.create_window((0, 0), window=self.list_frame, anchor="nw")
        self.list_canvas.configure(yscrollcommand=sb.set)
        self.list_canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.list_canvas.bind("<MouseWheel>",
                              lambda e: self.list_canvas.yview_scroll(int(-e.delta / 120), "units"))
        btn_row = ttk.Frame(frm)
        btn_row.pack(fill="x", **pad)
        ttk.Button(btn_row, text="全选", command=self._select_all).pack(side="left")
        ttk.Button(btn_row, text="全不选", command=self._select_none).pack(side="left", padx=6)
        ttk.Button(btn_row, text="清空处理对象", command=self._clear_all).pack(side="left", padx=6)
        self.lbl_hint = ttk.Label(btn_row, text="", foreground="#666")
        self.lbl_hint.pack(side="left", padx=8)

        # 3. 参数
        ttk.Label(frm, text="3. 空白音频长度", font=("", 10, "bold")).pack(anchor="w", **pad)
        ms_row = ttk.Frame(frm)
        ms_row.pack(fill="x", **pad)
        ttk.Entry(ms_row, textvariable=self.ms_var, width=10).pack(side="left")
        ttk.Label(ms_row, text="毫秒 (ms)   推荐 400（音频开头 <400ms 处出现首组 note 时易卡顿）").pack(side="left", padx=6)

        # 4. 输出
        ttk.Label(frm, text="4. 输出", font=("", 10, "bold")).pack(anchor="w", **pad)
        out_row = ttk.Frame(frm)
        out_row.pack(fill="x", **pad)
        ttk.Checkbutton(out_row, text="导出修改后的完整 .osz", variable=self.out_osz,
                        command=self._out_changed).pack(side="left")
        ttk.Button(out_row, text="选择保存位置...", command=self._pick_osz_dst).pack(side="left", padx=6)
        ttk.Label(out_row, textvariable=self.osz_dst, foreground="#666", width=40, anchor="w").pack(side="left")
        out_row2 = ttk.Frame(frm)
        out_row2.pack(fill="x", **pad)
        ttk.Checkbutton(out_row2, text="单独导出处理后的音频文件", variable=self.out_audio,
                        command=self._out_changed).pack(side="left")
        ttk.Button(out_row2, text="选择导出目录...", command=self._pick_audio_dir).pack(side="left", padx=6)
        ttk.Label(out_row2, textvariable=self.audio_dir, foreground="#666", width=40, anchor="w").pack(side="left")
        out_row3 = ttk.Frame(frm)
        out_row3.pack(fill="x", **pad)
        ttk.Checkbutton(out_row3, text="修改谱面难度名（追加 [+XXXms] 后缀），避免导入回 osu 时覆盖原谱面",
                        variable=self.rename_version).pack(side="left")

        # 5. 执行
        act = ttk.Frame(frm)
        act.pack(fill="x", **pad)
        self.btn_run = ttk.Button(act, text="开始处理", command=self._start)
        self.btn_run.pack(side="left")
        self.progress = ttk.Progressbar(act, mode="indeterminate", length=260)
        self.progress.pack(side="left", padx=10)

        ttk.Label(frm, text="日志", font=("", 10, "bold")).pack(anchor="w", **pad)
        log_row = ttk.Frame(frm)
        log_row.pack(fill="both", expand=True, **pad)
        self.log_text = tk.Text(log_row, height=8, state="disabled", wrap="none")
        log_sb = ttk.Scrollbar(log_row, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_sb.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_sb.pack(side="right", fill="y")

        self._mode_changed()
        self._out_changed()

    def _mode_changed(self):
        # 用 grid_remove/grid 保持固定位置，避免 pack_forget 后重排到末尾
        if self.mode.get() == "osz":
            self.panel_audio.grid_remove()
            self.panel_osz.grid()
        else:
            self.panel_osz.grid_remove()
            self.panel_audio.grid()

    def _out_changed(self):
        pass

    def _log(self, msg):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    # ---------- 导入 ----------
    def _pick_osz(self):
        from tkinter import filedialog
        p = filedialog.askopenfilename(title="选择 .osz 谱面包",
                                       filetypes=[("osu! 谱面包", "*.osz"), ("压缩包", "*.zip"), ("所有文件", "*.*")])
        if not p:
            return
        self.osz_path.set(p)
        self._analyze_osz_async(p)

    def _analyze_osz_async(self, p):
        """后台解析 osz：界面保持响应，进度条显示解析进度。"""
        if self._analyzing:
            return
        self._analyzing = True
        self._aprog_first = True
        self.btn_pick_osz.configure(state="disabled")
        self.lbl_osz.configure(text="正在解析 %s …" % os.path.basename(p))
        self.progress.configure(mode="determinate", maximum=1, value=0)
        self._log("开始解析：%s" % p)
        t = threading.Thread(target=self._analyze_work, args=(p,), daemon=True)
        t.start()

    def _analyze_work(self, p):
        from osz_utils import analyze_osz
        try:
            groups, entries = analyze_osz(
                p, progress=lambda i, t, n: self.q.put(("aprog", i, t, n)))
            self.q.put(("adone", groups, entries, None))
        except Exception as e:
            self.q.put(("adone", None, None, "%s" % e))

    def _pick_audio(self):
        from tkinter import filedialog
        files = filedialog.askopenfilenames(
            title="选择音频文件",
            filetypes=[("音频文件", "*.mp3 *.ogg *.flac *.wav *.w64 *.aiff *.aif *.caf"), ("所有文件", "*.*")])
        if not files:
            return
        self.audio_files = list(files)
        self.lbl_audio.configure(text="%d 个音频文件" % len(self.audio_files))
        self._refresh_groups()

    def _refresh_groups(self):
        for w in self.list_frame.winfo_children():
            w.destroy()
        self.group_vars = {}
        if self.mode.get() == "osz":
            for audio_name, maps in self.groups:
                var = tk.BooleanVar(value=True)
                self.group_vars[audio_name] = var
                ttk.Checkbutton(self.list_frame, variable=var,
                                text="%s   （%d 张谱面: %s）" % (audio_name, len(maps), "、".join(maps)),
                                ).pack(fill="x", padx=4, pady=1)
        else:
            for f in self.audio_files:
                var = tk.BooleanVar(value=True)
                self.group_vars[f] = var
                ttk.Checkbutton(self.list_frame, variable=var,
                                text=os.path.basename(f)).pack(fill="x", padx=4, pady=1)
        self.lbl_hint.configure(text="%d 组已识别" % len(self.group_vars))

    def _select_all(self):
        for v in self.group_vars.values():
            v.set(True)

    def _select_none(self):
        for v in self.group_vars.values():
            v.set(False)

    def _clear_all(self):
        """清空当前处理对象（导入的 osz/音频与勾选状态）。"""
        if self._analyzing:
            messagebox.showinfo("正在解析", "正在解析 .osz 文件，请稍候…")
            return
        self.groups = []
        self.audio_files = []
        self.group_vars = {}
        self.osz_path.set("")
        self._refresh_groups()
        self.lbl_osz.configure(text="未选择")
        self.lbl_audio.configure(text="未选择")
        self._log("已清空处理对象")

    # ---------- 输出 ----------
    def _pick_osz_dst(self):
        from tkinter import filedialog
        src = self.osz_path.get()
        base = os.path.basename(src) if src else "output.osz"
        name, ext = os.path.splitext(base)
        ms = self._ms()
        sfx = suffix_for(ms) if ms is not None else ""
        default = os.path.join(os.path.dirname(src) if src else "", "%s%s%s" % (name, sfx, ext))
        p = filedialog.asksaveasfilename(title="保存修改后的 .osz",
                                         initialfile=os.path.basename(default),
                                         initialdir=os.path.dirname(default),
                                         defaultextension=".osz",
                                         filetypes=[("osu! 谱面包", "*.osz")])
        if p:
            self.osz_dst.set(p)

    def _pick_audio_dir(self):
        from tkinter import filedialog
        d = filedialog.askdirectory(title="选择音频导出目录")
        if d:
            self.audio_dir.set(d)

    def _ms(self):
        try:
            return int(float(self.ms_var.get().strip()))
        except ValueError:
            return None

    # ---------- 执行 ----------
    def _selected(self):
        sel = [k for k, v in self.group_vars.items() if v.get()]
        return sel

    def _start(self):
        if self.worker and self.worker.is_alive():
            return
        if self._analyzing:
            messagebox.showinfo("正在解析", "正在解析 .osz 文件，请稍候…")
            return
        ms = self._ms()
        if ms is None or ms < 0:
            messagebox.showerror("参数错误", "空白长度必须是 >= 0 的数字（毫秒）")
            return
        sel = self._selected()
        if not sel:
            messagebox.showerror("未选择", "请至少勾选一个处理对象")
            return
        if not self.out_osz.get() and not self.out_audio.get():
            messagebox.showerror("未选择输出", "请至少选择一种输出形式")
            return
        if self.mode.get() == "osz":
            if not self.osz_path.get():
                messagebox.showerror("未导入", "请先选择 .osz 文件")
                return
            if self.out_osz.get() and not self.osz_dst.get():
                messagebox.showerror("未选择输出", "请选择 .osz 保存位置")
                return
        else:
            if not self.audio_files:
                messagebox.showerror("未导入", "请先选择音频文件")
                return
            if not self.out_audio.get():
                messagebox.showerror("未选择输出", "单独导入音频模式下请勾选「单独导出处理后的音频文件」")
                return
        if self.out_audio.get() and not self.audio_dir.get():
            messagebox.showerror("未选择输出", "请选择音频导出目录")
            return

        self.btn_run.configure(state="disabled")
        self.progress.start(12)
        self._log("开始处理（空白长度 %dms）..." % ms)
        args = (self.mode.get(), list(sel), ms)
        self.worker = threading.Thread(target=self._work, args=args, daemon=True)
        self.worker.start()

    def _work(self, mode, sel, ms):
        try:
            if mode == "osz":
                self._work_osz(sel, ms)
            else:
                self._work_audio(sel, ms)
            self.q.put(("done", None))
        except Exception as e:
            self.q.put(("done", traceback.format_exc() + "\n%s" % e))

    def _work_osz(self, sel, ms):
        from osz_utils import process_osz
        src = self.osz_path.get()
        total_done = []
        if self.out_osz.get():
            dst = self.osz_dst.get()
            vs = "[+%dms]" % ms if self.rename_version.get() else None
            self.q.put(("log", "输出完整 .osz → %s" % dst))
            pa, pm = process_osz(src, dst, sel, ms, log=lambda m: self.q.put(("log", m)),
                                 version_suffix=vs)
            total_done.append("osz: %d 个音频、%d 张谱面" % (len(pa), len(pm)))
        if self.out_audio.get():
            self.q.put(("log", "单独导出音频 → %s" % self.audio_dir.get()))
            cnt = self._export_audios_from_osz(src, sel, ms)
            total_done.append("音频: %d 个文件" % cnt)
        self.q.put(("log", "完成：%s" % "；".join(total_done)))

    def _export_audios_from_osz(self, src, sel, ms):
        import tempfile
        import zipfile
        from audio_utils import process_audio_file, prepend_silence_mp3, AudioError
        outdir = self.audio_dir.get()
        cnt = 0
        with zipfile.ZipFile(src) as z:
            for name in z.namelist():
                n = name.replace("\\", "/").lower()
                if n not in {s.replace("\\", "/").lower() for s in sel}:
                    continue
                ext = os.path.splitext(name)[1].lower()
                if ext not in (".mp3", ".ogg", ".flac", ".wav", ".w64", ".aiff", ".aif", ".caf"):
                    continue
                data = z.read(name)
                stem = os.path.splitext(os.path.basename(name))[0]
                dst = unique_path(os.path.join(outdir, stem + suffix_for(ms) + ext))
                try:
                    if ext == ".mp3":
                        out = prepend_silence_mp3(data, ms)
                        with open(dst, "wb") as f:
                            f.write(out)
                    else:
                        with tempfile.TemporaryDirectory() as td:
                            s = os.path.join(td, "in" + ext)
                            d = os.path.join(td, "out" + ext)
                            with open(s, "wb") as f:
                                f.write(data)
                            process_audio_file(s, d, ms)
                            with open(d, "rb") as f:
                                with open(dst, "wb") as fo:
                                    fo.write(f.read())
                    cnt += 1
                    self.q.put(("log", "已导出：%s" % os.path.basename(dst)))
                except AudioError as e:
                    self.q.put(("log", "导出失败：%s（%s）" % (name, e)))
        return cnt

    def _work_audio(self, sel, ms):
        from audio_utils import process_audio_file
        outdir = self.audio_dir.get()
        cnt = 0
        for f in sel:
            ext = os.path.splitext(f)[1].lower()
            stem = os.path.splitext(os.path.basename(f))[0]
            dst = unique_path(os.path.join(outdir, stem + suffix_for(ms) + ext))
            self.q.put(("log", "处理：%s → %s" % (os.path.basename(f), os.path.basename(dst))))
            process_audio_file(f, dst, ms)
            cnt += 1
        self.q.put(("log", "完成：导出 %d 个音频文件" % cnt))

    # ---------- 消息泵 ----------
    def _pump(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "log":
                    self._log(msg[1])
                elif kind == "aprog":
                    _, i, total, _name = msg
                    if self._aprog_first and total > 0:
                        self.progress.configure(maximum=total)
                        self._aprog_first = False
                    self.progress.configure(value=i)
                    self.lbl_osz.configure(
                        text="正在解析 %s …（%d/%d）" % (os.path.basename(self.osz_path.get()), i, total))
                elif kind == "adone":
                    _, groups, entries, err = msg
                    self._analyzing = False
                    self.btn_pick_osz.configure(state="normal")
                    self.progress.stop()
                    self.progress.configure(mode="indeterminate", value=0)
                    if err:
                        self._log("解析失败：%s" % err)
                        messagebox.showerror("解析失败", err)
                    else:
                        self.groups, self.entries = groups, entries
                        self._refresh_groups()
                        n_map = sum(len(m) for _, m in groups)
                        self.lbl_osz.configure(
                            text="%s（%d 个音频组 / %d 张谱面）"
                                 % (os.path.basename(self.osz_path.get()), len(groups), n_map))
                        if not groups:
                            self._log("警告：未在该 osz 中识别到任何谱面与音频对应关系")
                            messagebox.showwarning(
                                "未识别到谱面",
                                "该 osz 中未识别到任何 .osu 谱面及其音频对应关系。\n"
                                "请确认文件是 osu! 谱面包，或改用「单独导入音频」方式。")
                elif kind == "done":
                    payload = msg[1]
                    self.progress.stop()
                    self.btn_run.configure(state="normal")
                    if payload:
                        self._log("发生错误：\n" + payload)
                        messagebox.showerror("处理失败", payload[-800:])
                    else:
                        self._log("全部完成。")
                        messagebox.showinfo("完成", "处理完成，请查看日志与输出文件。")
        except queue.Empty:
            pass
        self.root.after(100, self._pump)

# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main():
    args = sys.argv[1:]
    if args and args[0] == "--selftest":
        outdir = args[1] if len(args) > 1 else os.path.join(os.getcwd(), "selftest")
        try:
            ok = _selftest(outdir)
            sys.exit(0 if ok else 1)
        except Exception:
            _safe_print(traceback.format_exc())
            sys.exit(2)
    run_gui()


if __name__ == "__main__":
    main()
