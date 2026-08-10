# osu-audio-handler

A Windows tool that prepends leading silence to the audio of osu!stable beatmaps
(especially osu!mania mapsets) to work around the **first note lag** issue.

In the legacy osu!stable client, if the first notes of a mania map land too
early in the audio track (typically within the first 400 ms), that first batch
of notes can stutter or feel delayed during gameplay. Since the game itself
cannot fix this, the standard mapper-side workaround is to prepend silence to
the very beginning of the audio and shift the entire beatmap timeline by the
same amount — keeping every note in the same position relative to the music.

## Features

- **Single portable `.exe`** — self-contained, no runtime or dependencies to
  install (no Python, ffmpeg or numpy).
- **Import a `.osz` mapset**: automatically reads the `AudioFilename` from every
  `.osu` file inside and maps each difficulty to its audio file (grouped by
  audio). Pick one group, several, or all of them (batch) to process.
- **Or import audio files directly** (`.mp3`/`.ogg`/`.flac`/`.wav` and more),
  leaving any beatmap-side edits to you.
- **Custom silence length** in milliseconds (400 ms recommended).
- **Two kinds of output**:
  - a complete `.osz` package (audio re-packed with `ZIP_STORED`, all other
    files preserved), and/or
  - the processed audio files individually (with a `_plusXXXms` suffix so the
    originals are never overwritten).
- Optionally **append a suffix to the difficulty name** (`Version`, e.g.
  `Insane [+400ms]`) so re-importing the mapset into osu! adds new difficulties
  instead of overwriting the original beatmap files.
- **Clear** button to reset the current import list between batches.
- The `.osz` is parsed in a background thread with a progress bar, so the UI
  stays responsive even for large mapsets.

## What happens to your beatmap

### Audio

- `.mp3` — **lossless frame splicing**: silent MPEG Layer III frames (matching
  the original sample rate, channel mode and bitrate) are inserted right before
  the first audio frame. The original audio bytes are left untouched, so
  bit-reservoir references, ID3 tags and everything else stay intact.
- `.ogg` — decoded via libsndfile, silence is prepended and the result is
  re-encoded as Ogg/Vorbis (slightly lossy).
- `.flac` / `.wav` and other containers — rewritten losslessly via libsndfile.

### Timeline (every timestamp shifted by the silence length)

- `[TimingPoints]` — **all** timing points: both uninherited (red) and
  inherited (green) lines, covering BPM changes and Kiai time.
- `[HitObjects]` — note times, spinner end times, and osu!mania **hold notes
  (LNs)**: both the start time and the end time inside the `endTime:hitSample`
  field are shifted, so hold lengths are preserved exactly.
- `[General]` — `PreviewTime`.
- `[Events]` — video events, break periods, colour changes, sample events, and
  every storyboard command (`M`/`F`/`R`/`S`/`V`/`C`/`P`/`T`..., including
  triggers and the commands inside them).
- Background images, videos and any other files inside the `.osz` are **left
  untouched**.

## Usage

1. Double-click `run.bat` (starts `dist\osu-audio-handler.exe` if present).
2. Choose an import mode:
   - `.osz` — the mapset is parsed in the background; a progress bar shows the
     parsing progress.
   - Individual audio files.
3. Tick the audio groups you want to process (select all / none / clear).
4. Enter the silence length in milliseconds.
5. Choose the output: a new `.osz` and/or separate audio files.
6. Optional (on by default): "append difficulty name suffix" — adds
   `[+XXXms]` to `Version` so the mapset can be re-imported without overwriting
   the original difficulties.
7. Click **Start** and watch the log.

The default window size fits all content — no manual resizing needed.

## Technical notes

- Zero third-party Python dependencies: `libsndfile` is called directly through
  `ctypes` (only the bundled `libsndfile_x64.dll` is required).
- The DLL under `libsndfile/` is taken from the PySoundFile wheel and is
  distributed under the LGPL — see `COPYING-libsndfile.txt`.

## Development

(Run inside `osu-audio-handler/`)

- Self-test: `python tests/test_all.py` (36 checks using synthetic audio + a
  synthetic `.osz`).
- GUI test: `python tests/test_gui.py` (default-window visibility, layout
  switching, background `.osz` parsing progress, empty-`.osz` notice, clear
  button).
- Build the `.exe`:

  ```
  python -m PyInstaller --noconfirm --clean --onefile --noconsole --icon ..\osu-songs-cleaner\icons\osu.ico --name osu-audio-handler --add-data "libsndfile;libsndfile" osu_audio_handler.py
  ```

  The result lands in `dist\osu-audio-handler.exe`.
- Verify a build headlessly:
  `dist\osu-audio-handler.exe --selftest <out-dir>` — writes
  `selftest_result.txt` into `<out-dir>`.


# osu-audio-handler

为 osu!stable（尤其 mania）谱面开头添加空白音频的工具，解决旧版 osu! 的
**first note lag**：当首组 note 出现在音频开头过早位置（通常 <400ms）时，
第一组 note 会出现卡顿。给音频开头补空白、并把谱面时间轴整体后移，
即可在不改变 note 与音乐相对位置的前提下修复该问题。

## 用法

双击 `run.bat`（优先启动 `dist\osu-audio-handler.exe`，无需安装任何环境）。

1. **导入**（二选一）
   - 导入 `.osz`：后台解析（界面不冻结，进度条显示解析进度），自动读取内部所有
     `.osu` 的 `AudioFilename`，识别每个谱面与音频的对应关系（按音频分组）。
   - 单独导入音频文件（`.mp3`/`.ogg`/`.flac`/`.wav` 等），由用户自行处理谱面。
2. 勾选要处理的音频组（支持全选/多选/批量；可用“清空处理对象”清掉当前导入内容）。
3. 输入空白音频长度（毫秒，推荐 400）。
4. 选择输出：完整 `.osz` 压缩包 和/或 单独导出的音频文件（自动追加
   `_plusXXXms` 后缀，不覆盖原文件）。
5. （可选，默认开启）勾选“修改谱面难度名”：给被处理谱面的难度名追加
   `[+XXXms]` 后缀（如 `Insane [+400ms]`），导入回 osu 时作为新难度，
   不会覆盖原谱面文件。
6. 开始处理，查看日志。

窗口默认尺寸已保证全部内容可见，无需手动拉伸。

## 处理内容

- **音频**：开头插入 `offset_ms` 空白。
  - `.mp3`：无损帧拼接 —— 在第一个 MPEG Layer III 音频帧前插入静音帧，
    原始音频字节不变（不受 bit reservoir 影响）。
  - `.ogg`：libsndfile 解码 → 前补静音 → 重编码回 Ogg/Vorbis（轻微有损）。
  - `.flac`/`.wav` 等：libsndfile 无损重写。
- **谱面时间轴整体后移**（保持 note 与音乐相对位置不变）：
  - `[TimingPoints]`：全部红线与绿线（含变 BPM 多红线、Kiai 绿线）
  - `[HitObjects]`：note 时间、spinner 结束时间、mania hold（Long Note）的
    开始时间与结束时间（`endTime:hitSample` 冒号组合字段一并后移，保持条长不变）
  - `[General] PreviewTime`
  - `[Events]`：视频、break、colour、sample，以及全部 Storyboard 命令
    （M/F/R/S/V/C/P/T 等，含触发器及其内部命令）
- `.osz` 内的背景/图片/视频等文件**原样保留，不做任何处理**；
  音频条目以 `ZIP_STORED` 写入（osu 读取更快）。

## 技术说明

- 零第三方依赖（`ctypes` 直调 `libsndfile`，仅需随附的 `libsndfile_x64.dll`；
  无需安装 numpy/ffmpeg）。
- `libsndfile/` 下的 DLL 取自 PySoundFile wheel（LGPL，见 `COPYING-libsndfile.txt`）。

## 开发

（在 `osu-audio-handler/` 目录内执行）

- 自测：`python tests/test_all.py`（合成音频 + 合成 osz + 36 项校验）
- GUI 测试：`python tests/test_gui.py`（默认窗口内容可见、布局切换位置不变、
  .osz 后台解析进度、空 osz 提示、清空处理对象）
- 打包 exe：
  `python -m PyInstaller --noconfirm --clean --onefile --noconsole --icon ..\osu-songs-cleaner\icons\osu.ico --name osu-audio-handler --add-data "libsndfile;libsndfile" osu_audio_handler.py`
  产物在 `dist\osu-audio-handler.exe`。
- 验证打包结果：`dist\osu-audio-handler.exe --selftest <输出目录>`
  （无窗口自测，结果写入 `<输出目录>\selftest_result.txt`）
