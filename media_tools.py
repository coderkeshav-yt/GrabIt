"""Local media conversion using the same FFmpeg installation as downloads."""

import json
import queue
import subprocess
import tempfile
import threading
from pathlib import Path

import engine


def convert_to_h264(source, progress_callback=None, *, cancel_event=None, ffmpeg_location=None, overwrite=False):
    source = Path(source).resolve()
    temporary = None
    process = None

    def cancelled():
        return cancel_event is not None and cancel_event.is_set()

    def result(ok, message, path=""):
        return {"ok": ok, "message": message, "filepath": str(path)}

    try:
        if cancelled():
            return result(False, "Download cancelled.")
        if not source.is_file():
            return result(False, "The source file is missing.")
        if ffmpeg_location is None:
            ffmpeg_location, blocked = engine._ffmpeg_for_download()
            if blocked:
                return result(False, "Application Control blocked FFmpeg.")
        ffmpeg, ffprobe = (str(Path(ffmpeg_location) / name) for name in engine.TOOLS)
        details = subprocess.check_output(
            [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(source)],
            stderr=subprocess.DEVNULL, timeout=10, creationflags=engine.WINDOW_FLAGS,
        )
        media = json.loads(details)
        video = next((stream for stream in media["streams"] if stream["codec_type"] == "video"
                      and not stream.get("disposition", {}).get("attached_pic")), None)
        audio = next((stream for stream in media["streams"] if stream["codec_type"] == "audio"), None)
        if not video:
            return result(False, "This file has no video stream.")
        if cancelled():
            return result(False, "Download cancelled.")
        if source.suffix.lower() == ".mp4" and video["codec_name"] == "h264" and (
            audio is None or audio["codec_name"] == "aac"
        ):
            return result(True, "Already an H.264 MP4.", source)

        target = source.with_name(f"{source.stem} [H.264].mp4")
        if not overwrite:
            number = 2
            while target.exists():
                target = source.with_name(f"{source.stem} [H.264] ({number}).mp4")
                number += 1
        with tempfile.NamedTemporaryFile(suffix=".mp4", dir=source.parent, delete=False) as file:
            temporary = Path(file.name)

        duration = float(media.get("format", {}).get("duration") or 0)
        video_codec = ["-c:v", "copy"] if video["codec_name"] == "h264" else [
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
            "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
        ]
        audio_args = ["-map", f"0:{audio['index']}", "-c:a", "copy" if audio["codec_name"] == "aac" else "aac"] if audio else []
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(
                [ffmpeg, "-nostdin", "-y", "-v", "error", "-progress", "pipe:1",
                 "-i", str(source), "-map", f"0:{video['index']}", *audio_args, *video_codec,
                 "-movflags", "+faststart", str(temporary)],
                stdout=subprocess.PIPE, stderr=errors, text=True, creationflags=engine.WINDOW_FLAGS,
            )
            updates = queue.SimpleQueue()

            def read_progress():
                try:
                    for line in process.stdout:
                        if line.startswith("out_time_us="):
                            updates.put(line.partition("=")[2].strip())
                finally:
                    updates.put(None)

            reader = threading.Thread(target=read_progress, daemon=True)
            reader.start()
            try:
                while not cancelled():
                    try:
                        elapsed = updates.get(timeout=0.15)
                    except queue.Empty:
                        continue
                    if elapsed is None:
                        break
                    if progress_callback and duration > 0 and elapsed.isdecimal():
                        progress_callback({
                            "percent": min(99.0, int(elapsed) / (10_000 * duration)),
                            "speed": "", "eta": "", "status": "converting",
                        })
                if cancelled():
                    return result(False, "Download cancelled.")
                if process.wait():
                    errors.seek(0)
                    reason = errors.read(500).decode(errors="replace").strip().splitlines()
                    return result(False, f"Conversion failed: {reason[0] if reason else 'FFmpeg could not convert this video.'}")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                reader.join()
                process.stdout.close()

        if cancelled():
            return result(False, "Download cancelled.")
        temporary.replace(target)
        return result(True, "H.264 conversion complete.", target)
    except FileNotFoundError:
        return result(False, "ffmpeg was not found.")
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        return result(False, f"Conversion failed: {str(error)[:160]}")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
