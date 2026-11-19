from __future__ import annotations

import sys
from pathlib import Path

import yt_dlp

from utils import get_downloads_folder, resource_path

TEST_URL = "https://www.youtube.com/watch?v=BaW_jenozKc"


def main() -> int:
    ffmpeg_dir = Path(resource_path("ffmpeg"))
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        if not (ffmpeg_dir / name).is_file():
            print(f"Missing ffmpeg binary: {ffmpeg_dir / name}")
            return 1

    output_dir = Path(get_downloads_folder()) / "GrabIt"
    output_dir.mkdir(parents=True, exist_ok=True)

    ydl_opts: dict = {
        "format": "bv*[height<=480]+ba/b[height<=480]/b",
        "outtmpl": str(output_dir / "%(title)s [%(id)s].%(ext)s"),
        "merge_output_format": "mp4",
        "ffmpeg_location": str(ffmpeg_dir),
        "noplaylist": True,
        "overwrites": False,
        "quiet": False,
        "no_warnings": False,
        "progress_hooks": [
            lambda d: print(
                f"\r{d.get('_percent_str', '').strip():>8} "
                f"{d.get('_speed_str', '').strip():>12} "
                f"{d.get('_eta_str', '').strip():>10}",
                end="",
                flush=True,
            )
            if d.get("status") == "downloading"
            else None
        ],
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(TEST_URL, download=True)
            if info is None:
                print("yt-dlp returned no info.")
                return 1
            print(f"\nSaved to: {ydl.prepare_filename(info)}")
    except yt_dlp.utils.DownloadError as exc:
        print(f"\nDownload failed: {exc}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
