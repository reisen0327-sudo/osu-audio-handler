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
