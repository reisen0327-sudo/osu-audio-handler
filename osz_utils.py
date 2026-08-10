# -*- coding: utf-8 -*-
"""
osz 解析 / 重组 与 .osu 谱面时间轴整体后移。
时间戳移动范围（整体后移 offset_ms）：
  - [TimingPoints]：全部（红线 + 绿线）
  - [HitObjects]：note 时间、spinner/manias 结束时间
  - [General] PreviewTime
  - [Events]：视频、break、colour、sample、以及全部 Storyboard 命令（M/F/R/S/V/C/P/T 等）的时间
"""
import os
import re
import zipfile

__all__ = ["OszError", "analyze_osz", "shift_osu_bytes", "process_osz"]

BOM = b"\xef\xbb\xbf"


class OszError(Exception):
    pass


def _norm(name):
    """归一化 zip 条目名 / AudioFilename，用于匹配。"""
    return name.replace("\\", "/").strip().lower().lstrip("/")


def parse_audio_filename(text):
    """从 .osu 文本中提取 [General] 段的 AudioFilename（保持原样）。"""
    m = re.search(r"^AudioFilename\s*:\s*(.+?)\s*$", text, re.MULTILINE)
    if not m:
        return None
    return m.group(1).strip()


def analyze_osz(path, progress=None):
    """
    解析 osz，返回 (groups, entries)：
      groups: [(audio_name, [beatmap_name, ...]), ...]，按音频分组的谱面对应关系（保留顺序）
      entries: zip 全部条目名
      progress(done, total, name)：可选进度回调，每检查一个条目调用一次
    """
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise OszError("不是有效的 .osz/.zip 压缩包：%s" % os.path.basename(path))
    with z:
        entries = z.namelist()
        total = len(entries)
        audio_to_maps = {}
        map_audio = {}
        for idx, e in enumerate(entries):
            if progress:
                progress(idx + 1, total, e)
            if not e.lower().endswith(".osu"):
                continue
            try:
                raw = z.read(e)
            except Exception:
                continue
            text = raw.decode("utf-8-sig", errors="replace")
            af = parse_audio_filename(text)
            if not af:
                continue
            audio_to_maps.setdefault(_norm(af), []).append(e)
            map_audio[e] = af
        groups = list(audio_to_maps.items())
    return groups, entries


def shift_osu_bytes(data, delta, version_suffix=None):
    """将 .osu 文件 bytes 中所有时间戳整体后移 delta 毫秒，返回新 bytes。
    version_suffix：若非空，则给 [Metadata] 的 Version（难度名）追加该后缀，
    避免导入回 osu 时覆盖原谱面文件。"""
    has_bom = data.startswith(BOM)
    text = data.decode("utf-8-sig", errors="replace")
    lines = text.splitlines(keepends=True)
    section = None
    for i, line in enumerate(lines):
        body = line.rstrip("\r\n")
        nl = line[len(body):]
        stripped = body.strip()
        if stripped.startswith("["):
            section = stripped.strip("[]").strip()
            continue
        if not stripped or stripped.startswith("//"):
            continue
        if section == "TimingPoints":
            lines[i] = _shift_fields(body, nl, delta, (0,))
        elif section == "HitObjects":
            lines[i] = _shift_hit_object(body, nl, delta)
        elif section == "Events":
            lines[i] = _shift_event(body, nl, delta)
        elif section == "General" and stripped.startswith("PreviewTime"):
            # PreviewTime: 1234 —— 冒号分隔
            m = re.match(r"^PreviewTime\s*:\s*(-?\d+)", body)
            if m:
                lines[i] = body[:m.start(1)] + str(int(m.group(1)) + delta) + body[m.end(1):] + nl
        elif section == "Metadata" and version_suffix and stripped.startswith("Version"):
            m = re.match(r"^(Version\s*:\s*)(.+?)\s*$", body)
            if m and version_suffix not in m.group(2):
                lines[i] = m.group(1) + m.group(2) + " " + version_suffix + nl
    out = "".join(lines)
    out_bytes = out.encode("utf-8")
    return BOM + out_bytes if has_bom else out_bytes


def _shift_fields(body, nl, delta, indexes=(0,)):
    parts = body.split(",")
    changed = False
    for idx in indexes:
        if len(parts) > idx and re.fullmatch(r"-?\d+", parts[idx].strip()):
            parts[idx] = str(int(parts[idx].strip()) + delta)
            changed = True
    return ",".join(parts) + nl if changed else body + nl


def _shift_hit_object(body, nl, delta):
    parts = body.split(",")
    changed = False
    if len(parts) > 2 and re.fullmatch(r"-?\d+", parts[2].strip()):
        parts[2] = str(int(parts[2].strip()) + delta)
        changed = True
    if len(parts) > 3 and re.fullmatch(r"\d+", parts[3].strip()):
        t = int(parts[3].strip())
        if t & 8 and len(parts) > 5 and re.fullmatch(r"-?\d+", parts[5].strip()):
            # spinner：结束时间为纯数字
            parts[5] = str(int(parts[5].strip()) + delta)
            changed = True
        elif t & 128 and len(parts) > 5:
            # mania hold：结束时间在 "endTime:hitSample" 冒号组合字段里（如 300:0:0:0:0:）
            m = re.match(r"^(-?\d+)(:.*)?$", parts[5].strip())
            if m:
                parts[5] = str(int(m.group(1)) + delta) + (m.group(2) or "")
                changed = True
    return ",".join(parts) + nl if changed else body + nl


_SB_CMD_RE = re.compile(r"^\s*[A-Z],")


def _shift_event(body, nl, delta):
    stripped = body.lstrip()
    if stripped.startswith("2,"):          # break: start,end
        return _shift_fields(body, nl, delta, (1, 2))
    if stripped.startswith("3,"):          # colour: start,end
        return _shift_fields(body, nl, delta, (1, 2))
    if stripped.startswith("1,"):          # video: 时间在倒数第二字段（文件名在最后）
        return _shift_fields(body, nl, delta, (-2,))
    if stripped.startswith("5,"):          # sample 事件：时间为最后一个字段
        return _shift_fields(body, nl, delta, (-1,))
    if _SB_CMD_RE.match(body):             # storyboard 命令（M/F/R/S/V/C/P/T...）
        return _shift_fields(body, nl, delta, (2, 3))
    return body + nl


def process_osz(src, dst, selected_audio, offset_ms, log=None, version_suffix=None):
    """
    处理 osz：
      selected_audio: 归一化音频名集合（决定处理哪些组）
      offset_ms: 空白时长（毫秒）
      version_suffix: 若非空，给被处理谱面的难度名追加后缀（避免导入 osu 时覆盖原谱面）
      被选音频 → 前加空白；其对应 .osu → 时间轴整体后移（+难度名后缀）；
      其余条目原样保留。音频条目以 ZIP_STORED 写入（osu 读取更快）。
    """
    def _log(msg):
        if log:
            log(msg)

    if os.path.abspath(src) == os.path.abspath(dst):
        raise OszError("输出路径不能与原文件相同，请另选保存位置")

    zin = zipfile.ZipFile(src)
    names = set(zin.namelist())
    sel = set(selected_audio)
    processed_audio, processed_maps = set(), set()
    zout = zipfile.ZipFile(dst, "w")
    try:
        for info in zin.infolist():
            name = info.filename
            n = _norm(name)
            data = zin.read(name)
            if n in sel:
                # 处理音频（按扩展名分发）
                ext = os.path.splitext(n)[1].lower()
                if ext not in (".mp3", ".ogg", ".flac", ".wav", ".w64", ".aiff", ".aif", ".caf", ".au"):
                    _log("跳过音频（不支持格式）：%s" % name)
                else:
                    try:
                        if ext == ".mp3":
                            from audio_utils import prepend_silence_mp3
                            data = prepend_silence_mp3(data, int(offset_ms))
                        else:
                            import tempfile
                            from audio_utils import process_audio_file
                            with tempfile.TemporaryDirectory() as td:
                                s = os.path.join(td, "in" + ext)
                                d = os.path.join(td, "out" + ext)
                                with open(s, "wb") as f:
                                    f.write(data)
                                process_audio_file(s, d, int(offset_ms))
                                with open(d, "rb") as f:
                                    data = f.read()
                        processed_audio.add(name)
                        _log("已处理音频：%s (+%dms)" % (name, int(offset_ms)))
                    except Exception as e:
                        _log("音频处理失败：%s（%s）" % (name, e))
                info.compress_type = zipfile.ZIP_STORED
            elif name.lower().endswith(".osu"):
                try:
                    text = data.decode("utf-8-sig", errors="replace")
                    af = parse_audio_filename(text)
                except Exception:
                    af = None
                if af and _norm(af) in sel:
                    data = shift_osu_bytes(data, int(offset_ms), version_suffix=version_suffix)
                    processed_maps.add(name)
                    _log("已后移时间轴：%s" % name)
            zout.writestr(info, data)
    finally:
        zin.close()
        zout.close()
    if not processed_audio and not processed_maps:
        raise OszError("未处理任何内容：所选音频在压缩包中未找到")
    return processed_audio, processed_maps
