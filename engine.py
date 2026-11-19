from __future__ import annotations

import re
import threading
from glob import escape as glob_escape
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

try:
    import yt_dlp
except Exception as import_error:  # pragma: no cover
    yt_dlp = None
    _YTDLP_IMPORT_ERROR: Exception | None = import_error
else:
    _YTDLP_IMPORT_ERROR = None

from utils import resource_path


ProgressCallback = Callable[[float, str, str], None]

_FFMPEG_DIR = Path(resource_path("ffmpeg"))
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

_ACTIVE_LOCK = threading.Lock()
_ACTIVE = False
_CANCEL = threading.Event()

_FORMAT_SELECTORS = {
    "best": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
    "1080": (
        "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/"
        "b[height<=1080][ext=mp4]/"
        "bv*[height<=1080]+ba/b[height<=1080]"
    ),
    "720": (
        "bv*[height<=720][ext=mp4]+ba[ext=m4a]/"
        "b[height<=720][ext=mp4]/"
        "bv*[height<=720]+ba/b[height<=720]"
    ),
    "480": (
        "bv*[height<=480][ext=mp4]+ba[ext=m4a]/"
        "b[height<=480][ext=mp4]/"
        "bv*[height<=480]+ba/b[height<=480]"
    ),
    "audio": "ba/b",
}

_MEDIA_EXTS = (".mp4", ".mkv", ".webm", ".mp3", ".m4a", ".opus", ".aac", ".ogg")


class _DownloadCancelled(Exception):
    pass


def _ok(message: str = "", **extra: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": True,
        "message": message,
        "error": "",
        "cancelled": False,
    }
    result.update(extra)
    return result


def _fail(message: str, *, cancelled: bool = False, **extra: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": False,
        "message": message,
        "error": message,
        "cancelled": cancelled,
    }
    result.update(extra)
    return result


def _normalize_url(url: Any) -> str | None:
    if not isinstance(url, str):
        return None

    value = url.strip()
    if not value or any(ch.isspace() for ch in value):
        return None

    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", value):
        value = f"https://{value}"

    try:
        parsed = urlparse(value)
    except ValueError:
        return None

    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        return None
    if "." not in parsed.netloc and parsed.netloc.lower() != "localhost":
        return None
    return value


def _base_opts() -> dict[str, Any]:
    return {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "no_color": True,
        "socket_timeout": 20,
        "retries": 3,
        "extractor_retries": 2,
    }


def _format_key(format_choice: str | None) -> str:
    value = (format_choice or "").strip().lower()
    if "mp3" in value or "audio" in value:
        return "audio"
    for key in ("1080", "720", "480"):
        if key in value:
            return key
    return "best"


def _ffmpeg_missing() -> bool:
    return not (
        (_FFMPEG_DIR / "ffmpeg.exe").is_file()
        and (_FFMPEG_DIR / "ffprobe.exe").is_file()
    )


def _clean_error(exc: BaseException) -> str:
    message = _ANSI_RE.sub("", str(exc)).strip()
    message = re.sub(r"^ERROR:\s*", "", message, flags=re.IGNORECASE)
    message = message.splitlines()[0].strip() if message else ""
    return message[:300]


def _friendly_error(exc: BaseException) -> str:
    raw = _clean_error(exc)
    text = raw.lower()

    if isinstance(exc, _DownloadCancelled) or "cancel" in text:
        return "Download cancelled."
    if "ffmpeg" in text or "ffprobe" in text:
        return "ffmpeg not found. Put ffmpeg.exe and ffprobe.exe in the ffmpeg folder."
    if "private video" in text or "video is private" in text:
        return "This video is private."
    if "members-only" in text or "members only" in text or "join this channel" in text:
        return "This video is for channel members only."
    if "age" in text and ("sign in" in text or "restricted" in text or "confirm" in text):
        return "This video is age-restricted. Sign-in is required."
    if (
        "sign in" in text
        or "sign-in" in text
        or "login required" in text
        or "logged-in" in text
        or "cookies" in text
    ):
        return "This site is asking for sign-in. Try another link or provide cookies."
    if any(
        phrase in text
        for phrase in (
            "video unavailable",
            "has been removed",
            "does not exist",
            "no longer available",
            "not available",
        )
    ):
        return "This video is unavailable."
    if any(
        phrase in text
        for phrase in (
            "unsupported url",
            "is not a valid url",
            "invalid url",
            "no video formats",
        )
    ):
        return "Invalid or unsupported link. Paste a full video URL."
    if "http error 403" in text or "forbidden" in text:
        return "Access denied by the site (HTTP 403)."
    if "http error 404" in text:
        return "Video not found (HTTP 404)."
    if "http error 429" in text or "too many requests" in text:
        return "The site is rate-limiting requests. Wait a moment and try again."
    if re.search(r"http error 5\d\d", text):
        return "The site is having server problems. Try again later."
    if any(
        phrase in text
        for phrase in (
            "getaddrinfo",
            "name resolution",
            "temporary failure",
            "network is unreachable",
            "no connection",
            "connection refused",
            "connection reset",
            "connection aborted",
            "failed to establish",
            "max retries exceeded",
            "timed out",
            "timeout",
            "urlopen error",
            "ssl",
            "certificate",
            "proxyerror",
            "no internet",
        )
    ):
        return "No internet connection. Check your network and try again."
    if "permission denied" in text or "access is denied" in text:
        return "Cannot write to the download folder. Choose another folder."
    if "no space left" in text or "disk full" in text:
        return "Not enough disk space."
    if raw:
        return f"Download failed: {raw}"
    return "Download failed. Please try again."


def _format_speed(value: Any) -> str:
    try:
        speed = float(value or 0)
    except (TypeError, ValueError):
        return "--"
    if speed <= 0:
        return "--"

    units = ("B/s", "KB/s", "MB/s", "GB/s")
    index = 0
    while speed >= 1024 and index < len(units) - 1:
        speed /= 1024
        index += 1
    if index == 0:
        return f"{speed:.0f} {units[index]}"
    return f"{speed:.1f} {units[index]}"


def _format_eta(value: Any) -> str:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return "--:--"
    if seconds < 0:
        return "--:--"

    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _emit(
    callback: ProgressCallback | None,
    percent: float,
    speed: str,
    eta: str,
) -> None:
    if callback is None:
        return
    try:
        callback(percent, speed, eta)
    except Exception:
        pass


def _make_progress_hook(
    callback: ProgressCallback | None,
    last_file: dict[str, str],
) -> Callable[[dict[str, Any]], None]:
    def hook(data: dict[str, Any]) -> None:
        if _CANCEL.is_set():
            raise _DownloadCancelled("Download cancelled.")

        filename = data.get("filename")
        if filename:
            last_file["path"] = str(filename)

        status = data.get("status")
        if status == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            done = data.get("downloaded_bytes") or 0
            try:
                percent = (float(done) / float(total) * 100.0) if total else 0.0
            except (TypeError, ValueError, ZeroDivisionError):
                percent = 0.0
            percent = max(0.0, min(100.0, percent))
            _emit(
                callback,
                percent,
                _format_speed(data.get("speed")),
                _format_eta(data.get("eta")),
            )
        elif status == "finished":
            _emit(callback, 100.0, _format_speed(data.get("speed")), "0:00")

    return hook


def _resolve_path(
    info: dict[str, Any] | None,
    ydl: Any,
    output_dir: Path,
    hint: str = "",
) -> str:
    if not info:
        return ""

    if info.get("_type") == "playlist":
        for entry in reversed([e for e in (info.get("entries") or []) if e]):
            found = _resolve_path(entry, ydl, output_dir)
            if found:
                return found
        return ""

    candidates: list[Path] = []
    if hint:
        candidates.append(Path(hint))

    try:
        candidates.append(Path(ydl.prepare_filename(info)))
    except Exception:
        pass

    filepath = info.get("filepath")
    if filepath:
        candidates.append(Path(str(filepath)))

    for item in info.get("requested_downloads") or []:
        path = item.get("filepath") if isinstance(item, dict) else None
        if path:
            candidates.append(Path(str(path)))

    expanded: list[Path] = []
    for path in candidates:
        expanded.append(path)
        for extension in _MEDIA_EXTS:
            expanded.append(path.with_suffix(extension))

    for path in expanded:
        try:
            if path.is_file():
                return str(path)
        except OSError:
            continue

    video_id = str(info.get("id") or "")
    if video_id:
        try:
            pattern = f"*{glob_escape(video_id)}*"
            matches = [
                path
                for path in output_dir.glob(pattern)
                if path.is_file() and path.suffix.lower() not in {".part", ".ytdl"}
            ]
            if matches:
                return str(max(matches, key=lambda path: path.stat().st_mtime))
        except OSError:
            pass

    return ""


def get_info(url: str) -> dict[str, Any]:
    """Return title, duration and thumbnail for a video."""
    empty = {"title": "", "duration": 0, "thumbnail": ""}
    if yt_dlp is None:
        return _fail(
            "yt-dlp is not installed. Run: pip install -r requirements.txt",
            **empty,
        )

    target = _normalize_url(url)
    if target is None:
        return _fail("Invalid link. Paste a full video URL.", **empty)

    opts = _base_opts()
    opts.update({"noplaylist": True, "skip_download": True})

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(target, download=False)
    except Exception as exc:
        return _fail(_friendly_error(exc), **empty)

    if not info:
        return _fail("Could not read video information.", **empty)

    if info.get("_type") in {"playlist", "multi_video"}:
        entries = [entry for entry in (info.get("entries") or []) if entry]
        if not entries:
            return _fail("Playlist is empty or unavailable.", **empty)
        info = entries[0]

    try:
        duration = int(info.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0

    thumbnail = info.get("thumbnail") or ""
    if not thumbnail:
        thumbnails = [
            item.get("url")
            for item in (info.get("thumbnails") or [])
            if isinstance(item, dict) and item.get("url")
        ]
        if thumbnails:
            thumbnail = thumbnails[-1]

    return _ok(
        "",
        title=str(info.get("title") or "Untitled"),
        duration=duration,
        thumbnail=str(thumbnail),
    )


def get_direct_link(url: str) -> dict[str, Any]:
    """Return a direct media URL without downloading the video."""
    if yt_dlp is None:
        return _fail(
            "yt-dlp is not installed. Run: pip install -r requirements.txt",
            url="",
        )

    target = _normalize_url(url)
    if target is None:
        return _fail("Invalid link. Paste a full video URL.", url="")

    info: dict[str, Any] | None = None
    last_error: Exception | None = None

    for selector in ("b[ext=mp4]/b", "bv*+ba/b"):
        opts = _base_opts()
        opts.update({"noplaylist": True, "format": selector})
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(target, download=False)
            break
        except Exception as exc:
            last_error = exc
            text = _clean_error(exc).lower()
            if "requested format is not available" not in text:
                return _fail(_friendly_error(exc), url="")

    if not info:
        return _fail(
            _friendly_error(last_error) if last_error else "Could not read video information.",
            url="",
        )

    if info.get("_type") in {"playlist", "multi_video"}:
        entries = [entry for entry in (info.get("entries") or []) if entry]
        if not entries:
            return _fail("Playlist is empty or unavailable.", url="")
        info = entries[0]

    direct = info.get("url") or ""
    if not direct:
        for item in info.get("requested_formats") or []:
            if isinstance(item, dict) and item.get("url"):
                direct = item["url"]
                break

    if not direct:
        return _fail("No direct link is available for this video.", url="")

    return _ok("", url=str(direct), title=str(info.get("title") or ""))


def download(
    url: str,
    folder: str,
    format_choice: str = "Best MP4",
    progress_callback: ProgressCallback | None = None,
    playlist: bool = False,
) -> dict[str, Any]:
    """Download a video or playlist. Returns a dict with ok, message and path."""
    global _ACTIVE

    if yt_dlp is None:
        return _fail("yt-dlp is not installed. Run: pip install -r requirements.txt", path="", title="")

    target = _normalize_url(url)
    if target is None:
        return _fail("Invalid link. Paste a full video URL.", path="", title="")

    if _ffmpeg_missing():
        return _fail(
            "ffmpeg not found. Put ffmpeg.exe and ffprobe.exe in the ffmpeg folder.",
            path="",
            title="",
        )

    if not isinstance(folder, str) or not folder.strip():
        return _fail("Choose a download folder.", path="", title="")

    try:
        output_dir = Path(folder).expanduser()
        output_dir.mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError):
        return _fail(
            "Cannot write to that download folder. Choose another folder.",
            path="",
            title="",
        )

    with _ACTIVE_LOCK:
        if _ACTIVE:
            return _fail("A download is already in progress.", path="", title="")
        _CANCEL.clear()
        _ACTIVE = True

    last_file: dict[str, str] = {"path": ""}
    hook = _make_progress_hook(progress_callback, last_file)
    key = _format_key(format_choice)

    opts: dict[str, Any] = {
        **_base_opts(),
        "format": _FORMAT_SELECTORS[key],
        "outtmpl": str(output_dir / "%(title).120B [%(id)s].%(ext)s"),
        "ffmpeg_location": str(_FFMPEG_DIR),
        "merge_output_format": "mp4",
        "noplaylist": not bool(playlist),
        "ignoreerrors": bool(playlist),
        "windowsfilenames": True,
        "continuedl": True,
        "overwrites": False,
        "concurrent_fragment_downloads": 4,
        "progress_hooks": [hook],
    }

    if key == "audio":
        opts["format"] = _FORMAT_SELECTORS["audio"]
        opts["final_ext"] = "mp3"
        opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ]

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(target, download=True)

            if _CANCEL.is_set():
                return _fail("Download cancelled.", cancelled=True, path="", title="")

            if not info:
                return _fail(
                    "Nothing was downloaded. The link may be unavailable.",
                    path="",
                    title="",
                )

            path = _resolve_path(info, ydl, output_dir, last_file["path"])

            if info.get("_type") == "playlist":
                entries = list(info.get("entries") or [])
                completed = sum(1 for entry in entries if entry)
                title = str(info.get("title") or "Playlist")
                if completed == 0:
                    return _fail("No videos in this playlist could be downloaded.", path="", title=title)
                skipped = len(entries) - completed
                message = f"Downloaded {completed} item(s) from: {title}"
                if skipped:
                    message += f" ({skipped} skipped)"
                return _ok(message, path=path, title=title)

            title = str(info.get("title") or "video")
            return _ok(f"Downloaded: {title}", path=path, title=title)

    except Exception as exc:
        if _CANCEL.is_set() or isinstance(exc, _DownloadCancelled):
            return _fail("Download cancelled.", cancelled=True, path="", title="")
        return _fail(_friendly_error(exc), path="", title="")

    finally:
        with _ACTIVE_LOCK:
            _ACTIVE = False
        _CANCEL.clear()


def cancel() -> bool:
    """Stop the active download safely. Returns True if one was active."""
    with _ACTIVE_LOCK:
        active = _ACTIVE
    if active:
        _CANCEL.set()
    return active
