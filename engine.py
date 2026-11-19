"""Download engine for GrabIt."""

import re
from threading import Event, Lock
from urllib.parse import urlsplit

import yt_dlp
from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.utils import DownloadCancelled

import utils

_FORMATS = {
    "best": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
    "mp3": "bestaudio/best",
    **{f"{h}p": f"bestvideo[height<={h}]+bestaudio/best[height<={h}]" for h in (1080, 720, 480)},
}
_INVALID = "Please paste a valid video link."
_cancelled = Event()
_download_lock = Lock()


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


def _options(**overrides):
    ffmpeg_folder = getattr(utils, "ffmpeg_folder", None)
    return {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "logger": _SilentLogger(),
        "ffmpeg_location": ffmpeg_folder() if ffmpeg_folder else utils.resource_path("ffmpeg"),
        "noplaylist": True,
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


def get_info(url):
    try:
        info = _extract(url)
        return {
            "ok": True,
            "title": info.get("title") or "",
            "duration": _time(info.get("duration") or 0),
            "thumbnail": info.get("thumbnail") or "",
            "message": "",
        }
    except Exception as error:
        return {"ok": False, "title": "", "duration": "00:00", "thumbnail": "", "message": _message(error)}


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


def _check_cancelled():
    if _cancelled.is_set():
        raise DownloadCancelled()


def download(url, folder, choice, progress_callback, playlist=False):
    if not _download_lock.acquire(blocking=False):
        return {"ok": False, "message": "Something went wrong: A download is already running.", "filepath": ""}
    _cancelled.clear()
    filepath = ""

    class CaptureFile(PostProcessor):
        def run(self, info):
            nonlocal filepath
            _check_cancelled()
            filepath = info.get("filepath") or ""
            return [], info

    def progress(data):
        _check_cancelled()
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
        _check_cancelled()

    try:
        url = _url(url)
        options = _options(
            format=_FORMATS[choice],
            paths={"home": folder or utils.get_downloads_folder()},
            outtmpl="%(title)s.%(ext)s",
            restrictfilenames=False,
            windowsfilenames=True,
            noplaylist=not playlist,
            progress_hooks=[progress],
        )
        if choice == "mp3":
            options["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }]
        else:
            options["merge_output_format"] = "mp4"

        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.add_post_processor(CaptureFile(ydl), when="after_move")
            result = ydl.download([url])
        _check_cancelled()
        if result or not filepath:
            raise ValueError("No output file was produced.")
        return {"ok": True, "message": "Download complete.", "filepath": filepath}
    except Exception as error:
        return {"ok": False, "message": _message(error), "filepath": ""}
    finally:
        _download_lock.release()
