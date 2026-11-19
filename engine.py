"""Download engine for GrabIt."""

import io
import os
import re
import subprocess
from pathlib import Path
from shutil import which
from subprocess import DEVNULL, SubprocessError, run
from threading import Event, Lock
from urllib.parse import urlsplit

import yt_dlp
from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.utils import DownloadCancelled

import utils

_FORMATS = {
    "best": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
    "highest": "bestvideo+bestaudio/best",
    "editor": (
        "bestvideo[ext=mp4][vcodec~='^(avc1|h264)']"
        "+bestaudio[ext=m4a][acodec~='^(mp4a|aac)']"
        "/best[ext=mp4][vcodec~='^(avc1|h264)'][acodec~='^(mp4a|aac)']"
    ),
    **{audio: "bestaudio/best" for audio in ("mp3", "m4a", "wav", "flac")},
    **{f"{h}p": f"bestvideo[height<={h}]+bestaudio/best[height<={h}]" for h in (1080, 720, 480)},
}
_INVALID = "Please paste a valid video link."
_cancelled = Event()
_download_lock = Lock()
_AUDIO = frozenset(("mp3", "m4a", "wav", "flac"))
_FORMAT_ID = re.compile(r"[A-Za-z0-9_.-]+")
WINDOW_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)
TOOLS = ("ffmpeg.exe", "ffprobe.exe") if os.name == "nt" else ("ffmpeg", "ffprobe")


class _SilentLogger:
    def debug(self, message):
        pass

    info = warning = error = debug


def _url(value):
    try:
        value = value.strip()
        parts = urlsplit(value)
        if parts.scheme in ("http", "https") and parts.hostname and not re.search(r"\s", value):
            return value
    except (AttributeError, TypeError, ValueError):
        pass
    raise ValueError(_INVALID)


def _bundled_ffmpeg():
    ffmpeg_folder = getattr(utils, "ffmpeg_folder", None)
    return Path(ffmpeg_folder() if ffmpeg_folder else utils.resource_path("ffmpeg"))


def _ffmpeg_for_download():
    bundled = _bundled_ffmpeg()
    folders = [bundled, *(
        Path(folder.strip('"')) for folder in os.environ.get("PATH", "").split(os.pathsep) if folder.strip('"')
    )]

    blocked = False
    seen = set()
    for folder in folders:
        try:
            folder = folder.resolve()
            if folder in seen:
                continue
            seen.add(folder)
            tools = [folder / name for name in TOOLS]
            if not all(tool.is_file() for tool in tools):
                continue
            for tool in tools:
                run(
                    [str(tool), "-version"], stdout=DEVNULL, stderr=DEVNULL,
                    creationflags=WINDOW_FLAGS, timeout=5, check=True,
                )
        except OSError as error:
            blocked |= getattr(error, "winerror", None) in (1260, 4551)
        except SubprocessError:
            continue
        else:
            return str(folder), False
    return str(bundled), blocked


def _options(*, cookie_file=None, cookie_browser=None, po_token=None, **overrides):
    node_name = "node.exe" if os.name == "nt" else "node"
    bundled_node = Path(utils.resource_path(f"runtime/{node_name}"))
    node = str(bundled_node) if bundled_node.is_file() else which("node")
    return {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "logger": _SilentLogger(),
        "socket_timeout": 20,
        "ffmpeg_location": str(_bundled_ffmpeg()),
        "js_runtimes": {"deno": {}, **({"node": {"path": node}} if node else {})},
        "noplaylist": True,
        **({"cookiefile": io.StringIO(Path(cookie_file).read_text(encoding="utf-8-sig"))} if cookie_file else {}),
        **({"cookiesfrombrowser": (cookie_browser,)} if cookie_browser and not cookie_file else {}),
        **({"extractor_args": {"youtube": {"po_token": [po_token]}}} if po_token else {}),
        **overrides,
    }


def _message(error):
    reason = " ".join(re.sub(r"\x1b\[[0-9;]*m", "", str(error)).split()).removeprefix("ERROR: ")
    lower = reason.lower()
    if isinstance(error, DownloadCancelled):
        return "Download cancelled."
    if reason == _INVALID or any(term in lower for term in (
        "unsupported url", "invalid url", "not a valid url", "incomplete youtube id",
    )):
        return _INVALID
    if any(term in lower for term in (
        "unable to resolve", "getaddrinfo failed", "name resolution", "name or service not known",
        "no such host", "network is unreachable", "network is down", "not connected to the internet",
        "winerror 10051", "winerror 10065",
    )):
        return "No internet connection."
    if any(term in lower for term in (
        "private video", "video is private", "video is unavailable", "video unavailable",
        "has been removed", "has been deleted", "http error 404", "members-only",
    )):
        return "This video is private or not available."
    if "ffmpeg" in lower and any(term in lower for term in (
        "not found", "not installed", "not available", "does not exist", "could not find",
    )):
        return "ffmpeg was not found."
    return f"Something went wrong: {reason[:180] or type(error).__name__}"


def _extract(url, **options):
    with yt_dlp.YoutubeDL(_options(**options)) as ydl:
        info = ydl.extract_info(_url(url), download=False)
        while info and "entries" in info:
            info = next((entry for entry in info["entries"] if entry), None)
        if not info:
            raise ValueError("No video found.")
        return info


def _time(seconds):
    minutes, seconds = divmod(max(0, int(seconds)), 60)
    return f"{minutes:02d}:{seconds:02d}"


def get_info(url, *, include_formats=False, **access):
    try:
        info = _extract(url, **access)
        result = {
            "ok": True,
            "title": info.get("title") or "",
            "duration": _time(info.get("duration") or 0),
            "thumbnail": info.get("thumbnail") or "",
            "message": "",
        }
        if include_formats:
            result["formats"] = [
                {
                    "id": item["format_id"], "height": item.get("height"),
                    "width": item.get("width"), "fps": item.get("fps"), "ext": item.get("ext"),
                    "codec": item.get("vcodec"), "size": item.get("filesize") or item.get("filesize_approx"),
                }
                for item in reversed(info.get("formats") or [])
                if item.get("height") and item.get("ext") and item.get("vcodec") not in (None, "none", "images")
                and _FORMAT_ID.fullmatch(item.get("format_id") or "")
            ]
        return result
    except Exception as error:
        return {"ok": False, "title": "", "duration": "00:00", "thumbnail": "", "message": _message(error)}


def get_playlist(url, **access):
    try:
        with yt_dlp.YoutubeDL(_options(noplaylist=False, extract_flat="in_playlist", **access)) as ydl:
            data = ydl.extract_info(_url(url), download=False)
            entries = []
            for index, item in enumerate(data.get("entries") or (), 1):
                if not item:
                    continue
                target = item.get("webpage_url") or item.get("url") or ""
                if not target.startswith(("http://", "https://")) and item.get("ie_key") == "Youtube":
                    target = f"https://www.youtube.com/watch?v={item.get('id') or target}"
                if not target.startswith(("http://", "https://")):
                    continue
                entries.append({
                    "title": item.get("title") or target, "url": target, "index": index,
                    "duration": _time(item["duration"]) if item.get("duration") else "",
                })
            if not entries:
                raise ValueError("No downloadable playlist entries were found.")
            return {"ok": True, "title": data.get("title") or "Playlist", "entries": entries}
    except Exception as error:
        return {"ok": False, "message": _message(error)}


def get_direct_link(url):
    try:
        link = _extract(url, format="best").get("url")
        if not link:
            raise ValueError("No direct video link found.")
        return {"ok": True, "link": link, "message": ""}
    except Exception as error:
        return {"ok": False, "link": "", "message": _message(error)}


def cancel():
    _cancelled.set()


def download(
    url, folder, choice, progress_callback, playlist=False, *, cancel_event=None,
    format_id=None, batch_index=None, overwrite=False, convert=False, **access,
):
    if not _download_lock.acquire(blocking=False):
        return {"ok": False, "message": "Something went wrong: A download is already running.", "filepath": ""}
    _cancelled.clear()
    filepath = ""
    ffmpeg_blocked = False

    def check_cancelled():
        if _cancelled.is_set() or (cancel_event is not None and cancel_event.is_set()):
            raise DownloadCancelled()

    class CaptureFile(PostProcessor):
        def run(self, info):
            nonlocal filepath
            check_cancelled()
            filepath = info.get("filepath") or ""
            return [], info

    def progress(data):
        check_cancelled()
        status = data["status"]
        if progress_callback is None or status not in ("downloading", "finished"):
            return
        total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
        downloaded = data.get("downloaded_bytes") or 0
        percent = 100.0 if status == "finished" else (100.0 * downloaded / total if total > 0 else 0.0)
        speed, eta = data.get("speed"), data.get("eta")
        progress_callback({
            "percent": max(0.0, min(100.0, percent)),
            "speed": f"{speed / 1024:,.1f} KiB/s" if speed is not None else "",
            "eta": _time(eta) if eta is not None else "",
            "status": status,
        })
        check_cancelled()

    try:
        check_cancelled()
        url = _url(url)
        selected_format = _FORMATS[choice]
        if format_id is not None:
            if not _FORMAT_ID.fullmatch(format_id) or choice in _AUDIO:
                raise ValueError("Invalid video format.")
            selected_format = f"{format_id}[acodec=none]+bestaudio/{format_id}"
        ffmpeg_location, ffmpeg_blocked = _ffmpeg_for_download()
        check_cancelled()
        suffix = {"highest": " [Highest Quality]", "editor": " [H.264]"}.get(choice, "")
        if format_id:
            suffix = f" [Format {format_id}]"
        prefix = f"{int(batch_index):03d} - " if batch_index is not None else ""
        options = _options(
            **access,
            format=selected_format,
            ffmpeg_location=ffmpeg_location,
            paths={"home": folder or utils.get_downloads_folder()},
            outtmpl=f"{prefix}%(title)s{suffix}.%(ext)s",
            overwrites=overwrite,
            restrictfilenames=False,
            windowsfilenames=True,
            noplaylist=not playlist,
            progress_hooks=[progress],
        )
        if choice in _AUDIO:
            options["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": choice,
                **({"preferredquality": "192"} if choice == "mp3" else {}),
            }]
        else:
            options["merge_output_format"] = "mkv" if choice == "highest" else "mp4"

        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.add_post_processor(CaptureFile(ydl), when="after_move")
            result = ydl.download([url])
        check_cancelled()
        if result or not filepath:
            raise ValueError("No output file was produced.")
        if convert and choice not in _AUDIO:
            from media_tools import convert_to_h264
            return convert_to_h264(
                filepath, progress_callback, cancel_event=cancel_event or _cancelled,
                ffmpeg_location=ffmpeg_location, overwrite=overwrite,
            )
        return {"ok": True, "message": "Download complete.", "filepath": filepath}
    except Exception as error:
        message = _message(error)
        if ffmpeg_blocked and message == "ffmpeg was not found.":
            message = "Windows Application Control blocked FFmpeg. Use an approved FFmpeg build."
        return {"ok": False, "message": message, "filepath": ""}
    finally:
        _download_lock.release()
