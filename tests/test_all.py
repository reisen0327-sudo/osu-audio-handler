# -*- coding: utf-8 -*-
"""零依赖自测：调用 osu_audio_handler 内置 selftest（合成音频 + 合成 osz + 校验）。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import osu_audio_handler as h

if __name__ == "__main__":
    td = tempfile.mkdtemp(prefix="oah_test_")
    ok = h._selftest(td)
    with open(os.path.join(td, "selftest_result.txt"), encoding="utf-8") as f:
        print(f.read())
    sys.exit(0 if ok else 1)
