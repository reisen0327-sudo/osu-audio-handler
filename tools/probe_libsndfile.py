# -*- coding: utf-8 -*-
"""探测 libsndfile_x64.dll 能力：SF_INFO 布局、OGG Vorbis 读写、MP3 读取。"""
import ctypes, os, struct, sys, tempfile

DLL = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'libsndfile', 'libsndfile_x64.dll')
lib = ctypes.CDLL(DLL)

class SF_INFO(ctypes.Structure):
    _fields_ = [
        ("frames", ctypes.c_int64),
        ("samplerate", ctypes.c_int),
        ("channels", ctypes.c_int),
        ("format", ctypes.c_int),
        ("sections", ctypes.c_int),
        ("seekable", ctypes.c_int),
    ]

lib.sf_open.restype = ctypes.c_void_p
lib.sf_open.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.POINTER(SF_INFO)]
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

SFM_READ, SFM_WRITE = 0x10, 0x20
SFC_GET_LIB_VERSION = 0x1000
SFC_SET_VBR_ENCODING_QUALITY = 0x1020
SF_FORMAT_OGG = 0x00200000
SF_FORMAT_VORBIS = 0x0060

print("sizeof(SF_INFO) =", ctypes.sizeof(SF_INFO))

def get_lib_version():
    buf = ctypes.create_string_buffer(128)
    lib.sf_command(None, SFC_GET_LIB_VERSION, buf, 128)
    return buf.value.decode()

print("libsndfile version:", get_lib_version())

tmp = tempfile.mkdtemp()
ogg_path = os.path.join(tmp, 'sine.ogg').encode('utf-8')

# 写一个 1 秒 44.1k 立体声正弦波 ogg
info = SF_INFO(frames=44100, samplerate=44100, channels=2, format=SF_FORMAT_OGG | SF_FORMAT_VORBIS)
fout = lib.sf_open(ogg_path, SFM_WRITE, ctypes.byref(info))
assert fout, "sf_open write failed: " + lib.sf_strerror(None).decode()
quality = ctypes.c_float(0.5)
lib.sf_command(fout, SFC_SET_VBR_ENCODING_QUALITY, ctypes.byref(quality), ctypes.sizeof(quality))
import math
buf = (ctypes.c_float * (44100 * 2))()
for i in range(44100):
    v = 0.3 * math.sin(2 * math.pi * 440 * i / 44100)
    buf[i * 2] = v
    buf[i * 2 + 1] = v
n = lib.sf_write_float(fout, buf, 44100 * 2)
print("wrote samples:", n)
rc = lib.sf_close(fout)
print("sf_close(write) rc:", rc)

# 读回
info2 = SF_INFO()
fin = lib.sf_open(ogg_path, SFM_READ, ctypes.byref(info2))
assert fin, "sf_open read failed: " + lib.sf_strerror(None).decode()
print("read back: frames=%d sr=%d ch=%d fmt=0x%08x" % (info2.frames, info2.samplerate, info2.channels, info2.format))
rbuf = (ctypes.c_float * (info2.frames * info2.channels))()
n = lib.sf_read_float(fin, rbuf, info2.frames * info2.channels)
print("read samples:", n)
lib.sf_close(fin)

# 生成静音 mp3 帧（44.1k 128kbps stereo）拼 20 帧，测试 libsndfile 能否读 mp3
def make_silent_mp3(frames, sr=44100, br_kbps=128, mono=False, mpeg1=True):
    MPEG1_SR = [44100, 48000, 32000]
    MPEG2_SR = [22050, 24000, 16000]
    MPEG25_SR = [11025, 12000, 8000]
    MPEG1_L3_BR = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320]
    MPEG2_L3_BR = [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160]
    srtab = MPEG1_SR if mpeg1 else MPEG2_SR
    brtab = MPEG1_L3_BR if mpeg1 else MPEG2_L3_BR
    sr_idx = srtab.index(sr)
    br_idx = brtab.index(br_kbps)
    ver = 3 if mpeg1 else 2
    layer = 1
    channel = 3 if mono else 0
    b1 = 0xE0 | (ver << 3) | (layer << 1) | 1
    b2 = (br_idx << 4) | (sr_idx << 2) | 0
    b3 = (channel << 6) | 0
    header = bytes([0xFF, b1, b2, b3])
    samples_per_frame = 1152 if mpeg1 else 576
    frame_len = (144 * br_kbps * 1000) // sr + 0
    side = 17 if mono else 32
    if not mpeg1:
        side = 9 if mono else 17
    frame = header + b'\x00' * (frame_len - 4)
    data = frame * frames
    return data, samples_per_frame

data, spf = make_silent_mp3(20)
mp3_path = os.path.join(tmp, 'silent.mp3').encode('utf-8')
with open(mp3_path, 'wb') as f:
    f.write(data)
info3 = SF_INFO()
fin3 = lib.sf_open(mp3_path, SFM_READ, ctypes.byref(info3))
if not fin3:
    print("MP3 read support: NO  ->", lib.sf_strerror(None).decode())
else:
    print("MP3 read support: YES frames=%d sr=%d ch=%d" % (info3.frames, info3.samplerate, info3.channels))
    rbuf3 = (ctypes.c_float * (info3.frames * info3.channels))()
    n3 = lib.sf_read_float(fin3, rbuf3, info3.frames * info3.channels)
    maxabs = max(abs(rbuf3[i]) for i in range(n3))
    print("  decoded samples:", n3, " max abs:", maxabs)
    lib.sf_close(fin3)
