from __future__ import annotations

import os
import sys
from pathlib import Path


def resource_path(relative_path: str) -> str:
    """Return an absolute path that works in dev and inside a PyInstaller exe."""
    base = getattr(sys, "_MEIPASS", None)
    if base is None:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, relative_path)


def get_downloads_folder() -> str:
    """Return the current user's Downloads folder."""
    if sys.platform == "win32":
        try:
            import winreg

            sub_key = (
                r"Software\Microsoft\Windows\CurrentVersion"
                r"\Explorer\User Shell Folders"
            )
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub_key) as key:
                value, _ = winreg.QueryValueEx(
                    key, "{374DE290-123F-4565-9164-39C4925E467B}"
                )
            path = Path(os.path.expandvars(value))
            if path.is_dir():
                return str(path)
        except OSError:
            pass

    fallback = Path.home() / "Downloads"
    fallback.mkdir(parents=True, exist_ok=True)
    return str(fallback)
