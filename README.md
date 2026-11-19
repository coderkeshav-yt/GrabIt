# GrabIt

GrabIt is a desktop downloader powered by yt-dlp. Preview a video, choose a preset or a source format, and queue videos or selected playlist items. Completed files appear in the local Library. Download media you have permission to save.

| Quality option | Output |
| --- | --- |
| Highest available | Best available video and audio. Separate streams are merged into MKV. |
| Editor-ready MP4 | H.264 video with AAC audio, when the source offers both. |
| Best MP4 | Prefers MP4 video and M4A audio, with the best available fallback. |
| 1080p, 720p, 480p | Video at or below the selected resolution. |
| MP3 | Audio converted to a 192 kbps MP3. |
| M4A, WAV, FLAC | M4A audio, uncompressed PCM, or lossless FLAC. |

## Queue and playlists

- Select **Preview** to see the title, duration, thumbnail, and available video formats. Format sizes are approximate and refer to the video stream before audio is added.
- Select **Playlist** to pick individual videos and give each a quality preset. The playlist dialog can number files and optionally replace existing files.
- Use **Queue → Add links** to add several URLs. **Pause queue** stops new jobs from starting. An active download can also be paused, cancelled, or retried. Resuming can reuse partial downloads where the source and transfer protocol support it.
- Use **Convert a file**, convert from the **Library**, or enable conversion on a download. GrabIt keeps the source and creates an H.264 MP4, reusing compatible streams where possible.

## YouTube account and updates

In **Preferences**, open YouTube in your browser to sign in, then opt in to using its cookies or select a Netscape-format cookies file you already have. Some browsers restrict cookie access on certain systems. Only the browser choice or file path is saved. Treat exported cookies as account credentials.

If your yt-dlp setup requires a [Proof of Origin token](https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide), you can enter a token in yt-dlp's `CLIENT.CONTEXT+TOKEN` form for the current session. A supported provider can also be installed using yt-dlp's normal plugin mechanism. **Check for updates** compares the installed versions against GrabIt's releases and yt-dlp's published version.

## Run from source

Use Python 3.11 or newer with Tk. For YouTube, install a [JavaScript runtime supported by yt-dlp](https://github.com/yt-dlp/yt-dlp/wiki/EJS), such as Node.js or Deno, and make it available on PATH. Install [FFmpeg and FFprobe](https://ffmpeg.org/download.html) together in the same PATH directory or put native executables in the repository's `ffmpeg/` folder. GrabIt uses them to merge streams and convert media.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

On macOS or Linux, use `python3 -m venv .venv` and `.venv/bin/python` for the equivalent commands. Linux also needs the distribution's Tk package and a graphical session.

Quality, destination, appearance, and recent downloads are stored locally in `%LOCALAPPDATA%\GrabIt\profile.json` on Windows, `~/Library/Application Support/GrabIt/profile.json` on macOS, and `$XDG_CONFIG_HOME/GrabIt/profile.json` (default `~/.config`) on Linux.

## Test

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

The tests run against a local HTTP server and cover metadata, transfers, cancellation, and FFmpeg selection. For an interactive smoke test with your own URL, run `python test_engine.py` from the same environment.

## Build the desktop app

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm GrabIt.spec
```

Build on the target operating system. PyInstaller writes `dist\GrabIt.exe` on Windows, `dist/GrabIt.app` on macOS, or `dist/GrabIt` on Linux. If both native executables are present in `ffmpeg/`, PyInstaller bundles them. A clean checkout uses FFmpeg and FFprobe from PATH. You can optionally bundle a native Node.js executable at `runtime/node.exe` on Windows or `runtime/node` on macOS and Linux.

Windows Smart App Control may block a locally built, unsigned EXE. If it does, run from source while you arrange signing with a certificate trusted by Windows. Distribute builds through release artifacts, and include the applicable FFmpeg license and source notices if you redistribute a bundled FFmpeg build.
