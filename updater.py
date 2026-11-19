"""Check release versions without modifying the running installation."""

import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from packaging.version import InvalidVersion, Version
from yt_dlp.version import __version__ as YTDLP_VERSION

VERSION = "0.1.0"
RELEASES_URL = "https://github.com/coderkeshav-yt/GrabIt/releases"
YTDLP_URL = "https://github.com/yt-dlp/yt-dlp/releases"


def check_updates():
    releases = (
        ("GrabIt", VERSION, "https://api.github.com/repos/coderkeshav-yt/GrabIt/releases/latest"),
        ("yt-dlp", YTDLP_VERSION, "https://pypi.org/pypi/yt-dlp/json"),
    )
    messages = []
    for name, current, endpoint in releases:
        try:
            request = Request(endpoint, headers={"User-Agent": "GrabIt"})
            with urlopen(request, timeout=8) as response:
                data = json.load(response)
            latest = data["tag_name"] if name == "GrabIt" else data["info"]["version"]
            if Version(latest) > Version(current):
                messages.append(f"{name} {latest} is available")
            else:
                messages.append(f"{name} {current} is up to date")
        except HTTPError as error:
            messages.append(f"{name} has no published release" if error.code == 404 else f"Could not check {name}")
        except (OSError, ValueError, KeyError, InvalidVersion):
            messages.append(f"Could not check {name}")
    return " · ".join(messages)
