# -*- coding: utf-8 -*-
"""GUI 自动化测试：布局切换位置不变 + .osz 后台解析与进度回调。"""
import os
import sys
import tempfile
import time
import zipfile
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import osu_audio_handler as h


def build_test_osz(path):
    osu = ("osu file format v14\n\n"
           "[General]\nAudioFilename: a.mp3\n\n"
           "[TimingPoints]\n0,400,4,2,0,60,1,0\n")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("a.mp3", b"\x00" * 1000)
        z.writestr("b.osu", osu.encode("utf-8"))
        z.writestr("bg.png", b"x")


def main():
    root = tk.Tk()
    app = h.App(root)          # 使用默认 900x800 窗口，验证默认尺寸下内容是否完整可见
    root.update_idletasks()
    root.update()

    # ---- 0) 默认窗口尺寸下全部关键控件可见（无需手动拉伸）----
    win_h = root.winfo_height()
    win_w = root.winfo_width()

    def check_visible(w, name):
        rx = w.winfo_rootx() - root.winfo_rootx()
        ry = w.winfo_rooty() - root.winfo_rooty()
        hh, ww = w.winfo_height(), w.winfo_width()
        assert 0 <= ry and ry + hh <= win_h + 1, "%s 超出窗口纵向范围（y=%d..%d, 窗口高=%d）" % (name, ry, ry + hh, win_h)
        assert 0 <= rx and rx + ww <= win_w + 1, "%s 超出窗口横向范围（x=%d..%d, 窗口宽=%d）" % (name, rx, rx + ww, win_w)

    for w, n in [(app.panel_container, "导入面板"), (app.list_canvas, "处理对象列表"),
                 (app.btn_run, "开始处理按钮"), (app.progress, "进度条"),
                 (app.log_text, "日志框")]:
        check_visible(w, n)
    print("默认窗口可见性 OK（%dx%d）" % (win_w, win_h))

    # ---- 1) 布局切换：来回切换模式后 osz 面板位置必须不变 ----
    y0 = app.panel_osz.winfo_rooty()
    app.mode.set("audio")
    app._mode_changed()
    root.update()
    y_audio = app.panel_audio.winfo_rooty()
    app.mode.set("osz")
    app._mode_changed()
    root.update()
    y_back = app.panel_osz.winfo_rooty()
    print("layout: y0=%d y_audio=%d y_back=%d" % (y0, y_audio, y_back))
    assert y0 == y_back, "布局位置改变：切回 osz 后按钮位置从 %d 变为 %d" % (y0, y_back)
    # 音频面板在 osz 模式应隐藏
    assert not app.panel_audio.winfo_ismapped(), "音频面板未隐藏"

    # ---- 2) 后台解析 + 进度 ----
    td = tempfile.mkdtemp(prefix="oah_gui_")
    osz = os.path.join(td, "t.osz")
    build_test_osz(osz)
    app._analyze_osz_async(osz)
    prog_seen = []
    deadline = time.time() + 10
    while time.time() < deadline and app._analyzing:
        root.update()
        time.sleep(0.03)
    assert not app._analyzing, "解析超时（>10s）"
    assert len(app.groups) == 1 and app.groups[0][0] == "a.mp3", str(app.groups)
    st = str(app.btn_pick_osz["state"])
    print("btn state = %r" % st)
    assert st == "normal", "解析结束后按钮未恢复"
    print("analyze OK: groups=%s" % (app.groups,))

    # ---- 3) 空 osz 提示路径（不弹窗，验证 groups 为空时状态正确）----
    empty_osz = os.path.join(td, "empty.osz")
    with zipfile.ZipFile(empty_osz, "w") as z:
        z.writestr("only_audio.mp3", b"\x00" * 100)
    app._analyze_osz_async(empty_osz)
    deadline = time.time() + 10
    while time.time() < deadline and app._analyzing:
        root.update()
        time.sleep(0.03)
    assert not app._analyzing
    assert app.groups == [], str(app.groups)   # 无 .osu → 空组（GUI 会弹警告，此处仅验证状态）
    print("empty-osz path OK")

    # ---- 4) 清空处理对象 ----
    app.groups = [("a.mp3", ["b.osu"])]
    app._refresh_groups()
    assert len(app.group_vars) == 1, "预置组失败"
    app.audio_files = [os.path.join(td, "x.mp3")]
    app._clear_all()
    assert app.groups == [] and app.group_vars == {} and app.audio_files == [], "清空后仍有残留"
    assert app.osz_path.get() == "", "osz 路径未清空"
    assert app.lbl_osz["text"] == "未选择", "osz 标签未重置"
    root.update()
    print("clear-all OK")

    root.destroy()
    print("GUI 测试全部通过")


if __name__ == "__main__":
    main()
