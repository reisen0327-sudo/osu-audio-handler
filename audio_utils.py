# -*- coding: utf-8 -*-
"""
音频处理模块（零第三方依赖）：
  - mp3：无损帧拼接 —— 在第一个 MPEG Layer III 音频帧前插入若干静音帧，
    原始音频字节完全不变（bit reservoir 引用不受影响）。
  - ogg/flac/wav 等：通过 ctypes 直调 libsndfile 解码 → 前补静音 → 写回原容器格式。
    （ogg 为 Vorbis 重编码，轻微有损；flac/wav 无损。）
"""
import ctypes
import math
import os
import sys

__all__ = ["AudioError", "prepend_silence_mp3", "process_audio_file", "SUPPORTED_EXTS"]

SUPPORTED_EXTS = {".mp3", ".ogg", ".flac", ".wav", ".w64", ".aiff", ".aif", ".caf", ".au"}

# ---------------------------------------------------------------------------
# libsndfile ctypes 封装
# ---------------------------------------------------------------------------

class _SF_INFO(ctypes.Structure):
    _fields_ = [
        ("frames", ctypes.c_int64),
        ("samplerate", ctypes.c_int),
        ("channels", ctypes.c_int),
        ("format", ctypes.c_int),
        ("sections", ctypes.c_int),
        ("seekable", ctypes.c_int),
    ]

_SFM_READ = 0x10
_SFM_WRITE = 0x20
_SFC_SET_VBR_ENCODING_QUALITY = 0x1020
_SF_FORMAT_OGG = 0x00200000
_SF_FORMAT_WAV = 0x00010000
_SF_FORMAT_WAVEX = 0x00130000
_SF_FORMAT_FLAC = 0x00170000
_SF_FORMAT_AIFF = 0x00020000
_SF_FORMAT_AU = 0x00030000
_SF_FORMAT_CAF = 0x00180000
_SF_FORMAT_RF64 = 0x00190000
_SF_FORMAT_W64 = 0x000B0000
_ALLOWED_MAIN = {0x00010000, 0x00130000, 0x00170000, 0x00200000,
                 0x00020000, 0x00030000, 0x00180000, 0x00190000, 0x000B0000}

_lib = None


def _load_libsndfile():
    global _lib
    if _lib is not None:
        return _lib
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    candidates = []
    dll_dir = os.path.join(base, "libsndfile")
    if os.path.isdir(dll_dir):
        for f in sorted(os.listdir(dll_dir)):
            if f.lower().startswith("libsndfile") and f.lower().endswith(".dll"):
                candidates.append(os.path.join(dll_dir, f))
    # 开发环境兜底：soundfile 包自带的 DLL
    try:
        import soundfile as _sf
        d = os.path.join(os.path.dirname(_sf.__file__), "_soundfile_data")
        if os.path.isdir(d):
            candidates += [os.path.join(d, f) for f in sorted(os.listdir(d))
                           if f.lower().startswith("libsndfile") and f.lower().endswith(".dll")]
    except Exception:
        pass
    for c in candidates:
        try:
            lib = ctypes.CDLL(c)
            lib.sf_open.restype = ctypes.c_void_p
            lib.sf_open.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.POINTER(_SF_INFO)]
            lib.sf_close.restype = ctypes.c_int
            lib.sf_close.argtypes = [ctypes.c_void_p]
            lib.sf_read_float.restype = ctypes.c_int64
            lib.sf_read_float.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.c_int64]
            lib.sf_write_float.restype = ctypes.c_int64
            lib.sf_write_float.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.c_int64]
            lib.sf_strerror.restype = ctypes.c_char_p
            lib.sf_strerror.argtypes = [ctypes.c_void_p]
            lib.sf_command.restype = ctypes.c_int
            lib.sf_command.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
            _lib = lib
            return lib
        except OSError:
            continue
    raise RuntimeError("未找到 libsndfile 运行时（libsndfile/*.dll）")


class AudioError(Exception):
    pass


# ---------------------------------------------------------------------------
# MP3 无损静音帧拼接
# ---------------------------------------------------------------------------

_MPEG1_SR = (44100, 48000, 32000)
_MPEG2_SR = (22050, 24000, 16000)
_MPEG25_SR = (11025, 12000, 8000)
_MPEG1_L3_BR = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)
_MPEG2_L3_BR = (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)
# 默认码率（free-format 帧兜底），kbps
_DEFAULT_BR = {1: 128, 2: 128, 0: 64}  # MPEG1 / MPEG2 / MPEG2.5


def _find_first_mp3_frame(data):
    """扫描 data，返回第一个合法 MPEG Layer III 帧头的 (offset, header4) 或 (None, None)。"""
    n = len(data)
    i = 0
    while i < n - 4:
        if data[i] == 0xFF and (data[i + 1] & 0xE0) == 0xE0:
            b1 = data[i + 1]
            b2 = data[i + 2]
            version = (b1 >> 3) & 0x03       # 3=MPEG1 2=MPEG2 0=MPEG2.5 1=保留
            layer = (b1 >> 1) & 0x03         # 1=Layer III 2=Layer II 3=Layer I
            br_idx = (b2 >> 4) & 0x0F
            sr_idx = (b2 >> 2) & 0x03
            if version != 1 and layer == 1 and br_idx != 15 and sr_idx != 3:
                return i, data[i:i + 4]
        i += 1
    return None, None


def _make_silent_frame(header4, samplerate, channels, mpeg1):
    """按给定参数生成一个完整的静音 MPEG Layer III 帧（main data 全零 → 解码为静音）。"""
    b1, b2, b3 = header4[1], header4[2], header4[3]
    br_idx = (b2 >> 4) & 0x0F
    sr_idx = (b2 >> 2) & 0x03
    channel_mode = (b3 >> 6) & 0x03          # 3 = mono
    mono = (channel_mode == 3)
    if br_idx == 0:                          # free format → 兜底码率
        br_tab = _MPEG1_L3_BR if mpeg1 else _MPEG2_L3_BR
        br_kbps = _DEFAULT_BR.get(1 if mpeg1 else 2)
        br_idx = br_tab.index(br_kbps)
    else:
        br_tab = _MPEG1_L3_BR if mpeg1 else _MPEG2_L3_BR
        br_kbps = br_tab[br_idx]
    # 重建帧头：保留 version/layer 位，强制 protection=1（无 CRC），padding=0
    b1n = (b1 | 0x01)
    b2n = (br_idx << 4) | (sr_idx << 2) | 0
    b3n = (b3 & 0xC0) | 0x04                 # original=1
    header = bytes([0xFF, b1n, b2n, b3n])
    frame_len = (144 * br_kbps * 1000) // samplerate
    if mpeg1:
        side_len = 17 if mono else 32
    else:
        side_len = 9 if mono else 17
    frame = header + b"\x00" * (frame_len - 4)
    assert len(frame) == frame_len
    return frame


def prepend_silence_mp3(data, offset_ms):
    """在 mp3 开头（第一个音频帧之前）插入 offset_ms 毫秒的静音帧，返回新 bytes。"""
    if offset_ms <= 0:
        return data
    offset, hdr = _find_first_mp3_frame(data)
    if offset is None:
        raise AudioError("无法识别 MP3 音频帧（文件可能损坏或不是标准 MP3）")
    b1, b2 = hdr[1], hdr[2]
    version = (b1 >> 3) & 0x03
    sr_idx = (b2 >> 2) & 0x03
    if version == 3:
        samplerate = _MPEG1_SR[sr_idx]
        spf = 1152
        mpeg1 = True
    elif version == 2:
        samplerate = _MPEG2_SR[sr_idx]
        spf = 576
        mpeg1 = False
    else:
        samplerate = _MPEG25_SR[sr_idx]
        spf = 576
        mpeg1 = False
    channels = 1 if ((hdr[3] >> 6) & 0x03) == 3 else 2
    total_samples = int(round(offset_ms * samplerate / 1000.0))
    n_frames = int(math.ceil(total_samples / float(spf)))
    if n_frames <= 0:
        return data
    frame = _make_silent_frame(hdr, samplerate, channels, mpeg1)
    silence = frame * n_frames
    return data[:offset] + silence + data[offset:]


# ---------------------------------------------------------------------------
# libsndfile 路径：ogg/flac/wav 等
# ---------------------------------------------------------------------------

def _libsndfile_prepend(src, dst, offset_ms):
    lib = _load_libsndfile()
    info = _SF_INFO()
    fin = lib.sf_open(src.encode("utf-8"), _SFM_READ, ctypes.byref(info))
    if not fin:
        raise AudioError("无法读取音频: %s" % (lib.sf_strerror(None) or b"").decode("utf-8", "replace"))
    try:
        sr, ch = info.samplerate, info.channels
        if sr <= 0 or ch <= 0:
            raise AudioError("音频参数无效（采样率=%d 声道=%d）" % (sr, ch))
        main = info.format & 0x00FF0000
        sub = info.format & 0x0000FFFF
        if main not in _ALLOWED_MAIN:
            raise AudioError("不支持的音频容器格式 (0x%06X)" % main)
        out_info = _SF_INFO(samplerate=sr, channels=ch, format=main | sub)
        fout = lib.sf_open(dst.encode("utf-8"), _SFM_WRITE, ctypes.byref(out_info))
        if not fout:
            raise AudioError("无法写入音频: %s" % (lib.sf_strerror(None) or b"").decode("utf-8", "replace"))
        try:
            if main == _SF_FORMAT_OGG:
                q = ctypes.c_float(0.8)
                lib.sf_command(fout, _SFC_SET_VBR_ENCODING_QUALITY, ctypes.byref(q), ctypes.sizeof(q))
            silence_samples = int(round(offset_ms * sr / 1000.0)) * ch
            zero = (ctypes.c_float * (1 << 18))()
            rem = silence_samples
            while rem > 0:
                chunk = min(rem, len(zero))
                w = lib.sf_write_float(fout, zero, chunk)
                if w <= 0:
                    raise AudioError("写入静音失败: %s"
                                     % (lib.sf_strerror(fout) or b"").decode("utf-8", "replace"))
                rem -= w
            buf = (ctypes.c_float * (1 << 18))()
            while True:
                r = lib.sf_read_float(fin, buf, len(buf))
                if r <= 0:
                    break
                w = lib.sf_write_float(fout, buf, r)
                if w != r:
                    raise AudioError("写入音频数据失败")
        finally:
            lib.sf_close(fout)
    finally:
        lib.sf_close(fin)


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------

def process_audio_file(src, dst, offset_ms):
    """处理单个音频文件：src → dst（dst 已含新扩展名），前加 offset_ms 空白。"""
    ext = os.path.splitext(src)[1].lower()
    if ext not in SUPPORTED_EXTS:
        raise AudioError("不支持的音频格式：%s（支持 mp3/ogg/flac/wav 等）" % ext)
    if ext == ".mp3":
        with open(src, "rb") as f:
            data = f.read()
        out = prepend_silence_mp3(data, int(offset_ms))
        with open(dst, "wb") as f:
            f.write(out)
    else:
        _libsndfile_prepend(src, dst, int(offset_ms))
