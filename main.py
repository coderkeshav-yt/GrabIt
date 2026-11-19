from __future__ import annotations

import io
import json
import queue
import sys
import threading
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageOps

import engine
import updater
from jobs import Job
from media_tools import convert_to_h264
from utils import get_downloads_folder, get_profile_path, open_local_path, resource_path


FORMATS = {
    "Highest available": ("highest", "Best available picture and sound. Merged streams are saved as MKV."),
    "Editor-ready MP4": ("editor", "Downloads H.264 video and AAC audio when the source offers both."),
    "Best MP4": ("best", "Prefers MP4 video and M4A audio, with the best available fallback."),
    "1080p": ("1080p", "Downloads video at up to 1080p."),
    "720p": ("720p", "Downloads video at up to 720p."),
    "480p": ("480p", "Downloads video at up to 480p."),
    "MP3": ("mp3", "Saves audio as a 192 kbps MP3."),
    "M4A": ("m4a", "Saves audio in an M4A container."),
    "WAV": ("wav", "Saves uncompressed PCM audio."),
    "FLAC": ("flac", "Saves audio as lossless FLAC."),
}
DEFAULT_FORMAT = "Highest available"
BROWSERS = ("None", "Chrome", "Edge", "Firefox", "Brave", "Chromium") + (
    ("Safari",) if sys.platform == "darwin" else ()
)

BACK = ("#f2f0ea", "#121922")
SURFACE = ("#fffdf8", "#1a2530")
INK = ("#25241f", "#f1f5f9")
MUTED = ("#615e55", "#b8c7d3")
BORDER = ("#ded9cc", "#364758")
FIELD = ("#f9f7f0", "#131d27")
CONTROL = ("#eeeae0", "#2a3b4b")
CONTROL_HOVER = ("#e6dfd0", "#344b5e")
SIDE = "#17212d"
SIDE_LINE = "#303e4c"
SIDE_MUTED = "#b4c3d1"
SIDE_ACCENT = "#77e7d4"
SIDE_ACTIVE = "#293f52"
SIDE_HOVER = "#223444"
ACCENT = "#b94026"
ACCENT_HOVER = "#9c321d"
SUCCESS = ("#296340", "#8ae9b1")
ERROR = ("#a72e27", "#ffb2a6")


def font(size, bold=False, family="Segoe UI"):
    return ctk.CTkFont(family=family, size=size, weight="bold" if bold else "normal")


def label(parent, text, size=14, *, bold=False, color=INK, **options):
    return ctk.CTkLabel(
        parent, text=text, text_color=color, font=font(size, bold), anchor="w", **options
    )


def button(parent, text, action, *, primary=False, width=100):
    return ctk.CTkButton(
        parent, text=text, command=action, width=width, height=39, corner_radius=8,
        font=font(13, True), fg_color=ACCENT if primary else CONTROL,
        hover_color=ACCENT_HOVER if primary else CONTROL_HOVER,
        text_color="#ffffff" if primary else INK, text_color_disabled="#726c60",
    )


def panel(parent):
    return ctk.CTkFrame(
        parent, fg_color=SURFACE, corner_radius=13, border_width=1, border_color=BORDER
    )


def sidebar_icon(kind):
    image = Image.new("RGBA", (54, 54))
    draw = ImageDraw.Draw(image)

    def line(*points):
        draw.line([(x * 3, y * 3) for x, y in points], fill=SIDE_MUTED, width=5, joint="curve")

    if kind == "download":
        line((9, 2), (9, 11))
        line((5, 7), (9, 11), (13, 7))
        line((2, 12), (2, 15), (16, 15), (16, 12))
    elif kind == "library":
        for x in (3, 7, 11):
            line((x, 3), (x, 15))
        line((13, 3), (16, 15))
    elif kind == "preferences":
        for y, x in ((5, 6), (12, 12)):
            line((2, y), (16, y))
            draw.ellipse((x * 3 - 6, y * 3 - 6, x * 3 + 6, y * 3 + 6), fill=SIDE_MUTED)
    else:
        line((2, 14), (2, 4), (7, 4), (9, 6), (16, 6), (16, 14), (2, 14))

    return ctk.CTkImage(light_image=image, size=(18, 18))


def thumbnail_image(url: str) -> Image.Image | None:
    try:
        if urlsplit(url).scheme not in ("http", "https"):
            return None
        limit = 4 * 1024 * 1024
        request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(request, timeout=5) as response:
            data = response.read(limit + 1)
        if len(data) > limit:
            return None
        with Image.open(io.BytesIO(data)) as source:
            if source.width * source.height > 24_000_000:
                return None
            return source.convert("RGB")
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


def format_catalog(formats):
    choices = {}
    for item in formats:
        fps = f" {item['fps']:g}fps" if item.get("fps") else ""
        name = f"{item['height']}p{fps} · {item['ext'].upper()} · {item['id']}"
        size = f" · ~{item['size'] / 1048576:.1f} MiB video" if item.get("size") else ""
        choices[name] = (item["id"], f"{item['codec']}{size}")
    return choices


class GrabItApp(ctk.CTk):
    def __init__(self, profile_path: Path | None = None) -> None:
        super().__init__(fg_color=BACK)
        self.title("GrabIt")
        self.geometry("1100x705")
        self.minsize(940, 680)
        try:
            self.iconbitmap(resource_path("GrabIt.ico"))
        except tk.TclError:
            pass

        self.profile_path = profile_path or get_profile_path()
        saved = self._load_profile()
        self.folder = Path(saved["folder"] or get_downloads_folder())
        self.default_format = saved["format"]
        self.history = saved["history"]
        self.appearance = saved["appearance"]
        self.cookie_file = saved["cookie_file"]
        self.cookie_browser = saved["cookie_browser"]
        self.po_token = tk.StringVar()
        ctk.set_appearance_mode(self.appearance)
        self.events: queue.SimpleQueue[tuple[str, Any]] = queue.SimpleQueue()
        self.busy = False
        self.cancelling = False
        self.close_pending = False
        self.preview_id = 0
        self.source_formats = {}
        self.preview_image = None
        self.jobs: list[Job] = []
        self.active_job: Job | None = None
        self.queue_paused = False
        self.output: Path | None = None

        self._build_ui()
        self._navigate("New download")
        modifier = "Command" if sys.platform == "darwin" else "Control"
        self.bind(f"<{modifier}-l>", self._focus_link)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(80, self._poll_events)

    def _load_profile(self):
        try:
            saved = json.loads(self.profile_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = {}
        if not isinstance(saved, dict):
            saved = {}
        folder = saved.get("folder")
        quality = saved.get("format")
        history = saved.get("history")
        appearance = saved.get("appearance")
        cookie_file = saved.get("cookie_file")
        cookie_browser = saved.get("cookie_browser")
        return {
            "folder": folder if isinstance(folder, str) else "",
            "format": quality if isinstance(quality, str) and quality in FORMATS else DEFAULT_FORMAT,
            "appearance": appearance if appearance in ("Light", "Dark", "System") else "Light",
            "cookie_file": cookie_file if isinstance(cookie_file, str) else "",
            "cookie_browser": cookie_browser if cookie_browser in BROWSERS else "None",
            "history": [
                entry for entry in history
                if isinstance(entry, dict) and all(
                    isinstance(entry.get(key), str) and entry[key]
                    for key in ("path", "format", "date")
                )
            ][:50] if isinstance(history, list) else [],
        }

    def _save_profile(self) -> bool:
        temp = self.profile_path.with_suffix(".tmp")
        try:
            self.profile_path.parent.mkdir(parents=True, exist_ok=True)
            temp.write_text(json.dumps({
                "folder": str(self.folder), "format": self.default_format, "history": self.history,
                "appearance": self.appearance, "cookie_file": self.cookie_file, "cookie_browser": self.cookie_browser,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(self.profile_path)
        except OSError:
            return False
        return True

    def _build_ui(self):
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self._build_sidebar()

        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=0, column=1, sticky="nsew", padx=28, pady=(23, 23))
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)
        self.pages = {}
        for name, builder in (
            ("New download", self._build_download), ("Queue", self._build_queue), ("Library", self._build_library),
            ("Preferences", self._build_preferences),
        ):
            page = ctk.CTkFrame(self.content, fg_color="transparent")
            page.grid_columnconfigure(0, weight=1)
            builder(page)
            self.pages[name] = page

    def _build_sidebar(self):
        sidebar = ctk.CTkFrame(self, width=200, corner_radius=0, fg_color=SIDE)
        sidebar.grid(row=0, column=0, sticky="ns")
        sidebar.grid_propagate(False)
        sidebar.grid_columnconfigure(0, weight=1)
        sidebar.grid_rowconfigure(6, weight=1)

        brand = ctk.CTkFrame(sidebar, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="ew", padx=17, pady=(20, 14))
        try:
            with Image.open(resource_path("GrabIt.ico")) as icon:
                logo = ctk.CTkImage(light_image=icon.convert("RGBA"), size=(44, 44))
        except OSError:
            logo = None
        if logo:
            ctk.CTkLabel(brand, text="", image=logo, width=44, height=44).pack(side="left")
        label(brand, "GrabIt", 22, bold=True, color="#f4f8fc").pack(side="left", padx=(6, 0))
        ctk.CTkFrame(sidebar, height=1, fg_color=SIDE_LINE).grid(
            row=1, column=0, sticky="ew", padx=17, pady=(0, 13)
        )

        self.nav = {}
        for index, (name, icon) in enumerate((
            ("New download", "download"), ("Queue", "folder"), ("Library", "library"),
            ("Preferences", "preferences"),
        ), start=2):
            row = ctk.CTkFrame(sidebar, fg_color="transparent")
            row.grid(row=index, column=0, sticky="ew", padx=10, pady=3)
            row.grid_columnconfigure(1, weight=1)
            marker = ctk.CTkFrame(row, width=3, height=23, corner_radius=2, fg_color=SIDE)
            marker.grid(row=0, column=0, padx=(0, 5))
            nav = ctk.CTkButton(
                row, text=name, image=sidebar_icon(icon), compound="left",
                command=lambda page=name: self._navigate(page), anchor="w",
                height=43, corner_radius=9, border_spacing=10, fg_color=SIDE,
                hover_color=SIDE_HOVER, text_color=SIDE_MUTED, font=font(14, True),
            )
            nav.grid(row=0, column=1, sticky="ew")
            self.nav[name] = (nav, marker)

        footer = ctk.CTkFrame(sidebar, fg_color="transparent")
        footer.grid(row=7, column=0, sticky="ew", padx=17, pady=(0, 16))
        ctk.CTkFrame(footer, height=1, fg_color=SIDE_LINE).pack(fill="x", pady=(0, 9))
        ctk.CTkButton(
            footer, text="Open download folder", image=sidebar_icon("folder"), compound="left",
            anchor="w", height=35, corner_radius=8, fg_color=SIDE, hover_color=SIDE_HOVER,
            text_color=SIDE_MUTED, font=font(12, True), command=self._open_download_folder,
        ).pack(fill="x")
        self.sidebar_status = label(footer, "", 11, color="#ffc4ba")

    def _open_download_folder(self):
        try:
            open_local_path(self.folder)
        except OSError:
            self.sidebar_status.configure(text="Could not open the folder")
            self.sidebar_status.pack(fill="x", pady=(4, 0))
        else:
            self.sidebar_status.pack_forget()

    def _heading(self, page, text):
        ctk.CTkLabel(
            page, text=text, font=font(36, family="Georgia"), text_color=INK, anchor="w",
        ).grid(row=0, column=0, sticky="ew", pady=(0, 16))

    def _selector(self, parent, value, command):
        menu = ctk.CTkOptionMenu(
            parent, values=list(FORMATS), command=command, height=40, corner_radius=8,
            fg_color=CONTROL, button_color=CONTROL_HOVER, button_hover_color=BORDER,
            text_color=INK, dropdown_fg_color=SURFACE, dropdown_hover_color=CONTROL,
            dropdown_text_color=INK, text_color_disabled=MUTED,
            font=font(14, True), dropdown_font=font(14),
        )
        menu.set(value)
        return menu

    def _build_download(self, page):
        self._heading(page, "New download")

        link = panel(page)
        link.grid(row=1, column=0, sticky="ew", pady=(0, 14))
        link.grid_columnconfigure(0, weight=1)
        label(link, "Video link", bold=True).grid(row=0, column=0, sticky="w", padx=16, pady=(10, 4))
        self.url = tk.StringVar()
        self.url_entry = ctk.CTkEntry(
            link, textvariable=self.url, height=40, corner_radius=8, border_width=1,
            border_color=BORDER, fg_color=FIELD, text_color=INK, font=font(14),
        )
        self.url.trace_add("write", self._clear_preview)
        self.url_entry.grid(row=1, column=0, sticky="ew", padx=(16, 7), pady=(0, 14))
        self.url_entry.bind("<Return>", lambda _event: self._start_preview())
        button(link, "Paste", self._paste, width=65).grid(row=1, column=1, padx=(0, 7), pady=(0, 14))
        self.preview_button = button(link, "Preview", self._start_preview, width=82)
        self.preview_button.grid(row=1, column=2, padx=(0, 7), pady=(0, 14))
        self.playlist_button = button(link, "Playlist", self._load_playlist, width=78)
        self.playlist_button.grid(row=1, column=3, padx=(0, 16), pady=(0, 14))

        workspace = ctk.CTkFrame(page, fg_color="transparent")
        workspace.grid(row=2, column=0, sticky="ew", pady=(0, 14))
        workspace.grid_columnconfigure((0, 1), weight=1, uniform="download")
        preview = panel(workspace)
        preview.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        preview.grid_columnconfigure(0, weight=1)
        preview_header = ctk.CTkFrame(preview, fg_color="transparent")
        preview_header.grid(row=0, column=0, sticky="ew", padx=15, pady=(9, 6))
        label(preview_header, "Source preview", 17, bold=True).pack(side="left")
        self.thumbnail_button = button(preview_header, "Save image", self._save_thumbnail, width=82)
        self.thumbnail_button.pack(side="right")
        self.thumbnail_button.configure(state="disabled")

        art = ctk.CTkFrame(preview, fg_color="#353b2d", corner_radius=9, height=145)
        art.grid(row=1, column=0, sticky="ew", padx=15)
        art.grid_propagate(False)
        art.grid_columnconfigure(0, weight=1)
        art.grid_rowconfigure(0, weight=1)
        self.preview_art = ctk.CTkLabel(
            art, text="▷", text_color="#e1dcc2", font=font(43, True), bg_color="transparent"
        )
        self.preview_art.grid(row=0, column=0)
        self.preview_title = label(preview, "No video loaded", bold=True, wraplength=259, justify="left")
        self.preview_title.grid(row=2, column=0, sticky="w", padx=15, pady=(5, 0))
        self.preview_detail = label(preview, "Paste a link and select Preview.", 12, color=MUTED, wraplength=259, justify="left")
        self.preview_detail.grid(row=3, column=0, sticky="w", padx=15, pady=(0, 9))

        output = panel(workspace)
        output.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        output.grid_columnconfigure(0, weight=1)
        label(output, "Download options", 17, bold=True).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=15, pady=(11, 7)
        )
        self.format_menu = self._selector(output, self.default_format, self._format_changed)
        self.format_menu.grid(row=1, column=0, columnspan=2, sticky="ew", padx=15)
        self.format_detail = label(
            output, FORMATS[self.default_format][1], 12,
            color=MUTED, height=40, wraplength=250, justify="left",
        )
        self.format_detail.grid(row=2, column=0, columnspan=2, sticky="w", padx=15)
        label(output, "Save to", 12, bold=True).grid(row=3, column=0, sticky="w", padx=15)
        self.folder_value = tk.StringVar(value=str(self.folder))
        ctk.CTkEntry(
            output, textvariable=self.folder_value, state="readonly", height=36, corner_radius=8,
            border_width=1, border_color=BORDER, fg_color=FIELD, text_color=INK, font=font(12),
        ).grid(row=4, column=0, sticky="ew", padx=(15, 7))
        self.browse_button = button(output, "Browse", self._choose_folder, width=70)
        self.browse_button.grid(row=4, column=1, padx=(0, 15))
        self.convert_download = tk.BooleanVar()
        ctk.CTkCheckBox(
            output, text="Convert to H.264 after download", variable=self.convert_download,
            font=font(12), text_color=INK, fg_color=ACCENT, hover_color=ACCENT_HOVER,
        ).grid(row=5, column=0, columnspan=2, sticky="w", padx=15, pady=(8, 0))
        self.download_button = button(output, "Add to queue", self._start_download, primary=True)
        self.download_button.grid(row=6, column=0, columnspan=2, sticky="ew", padx=15, pady=(8, 12))

        transfer = panel(page)
        transfer.grid(row=3, column=0, sticky="ew")
        transfer.grid_columnconfigure(0, weight=1)
        self.status_label = label(transfer, "Ready to download", bold=True)
        self.status_label.grid(row=0, column=0, sticky="w", padx=15, pady=(7, 1))
        self.percent_label = label(transfer, "0%", bold=True, color=ACCENT)
        self.percent_label.grid(row=0, column=1, columnspan=2, sticky="e", padx=15, pady=(7, 1))
        self.progress_bar = ctk.CTkProgressBar(transfer, height=6, fg_color="#ebe5d8", progress_color=ACCENT)
        self.progress_bar.grid(row=1, column=0, columnspan=3, sticky="ew", padx=15)
        self.progress_bar.set(0)
        self.status_detail = label(transfer, "", 12, color=MUTED, wraplength=425, justify="left")
        self.status_detail.grid(row=2, column=0, sticky="ew", padx=(15, 6), pady=(6, 6))
        self.cancel_button = button(transfer, "Cancel", self._cancel_download, width=72)
        self.cancel_button.grid(row=2, column=2, padx=(0, 15), pady=6)
        self.cancel_button.grid_remove()
        self.open_button = button(transfer, "Open file", self._open_output, width=83)
        self.open_button.grid(row=2, column=2, padx=(0, 15), pady=6)
        self.open_button.grid_remove()

    def _build_queue(self, page):
        self._heading(page, "Download queue")
        actions = ctk.CTkFrame(page, fg_color="transparent")
        actions.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        button(actions, "Add links", self._bulk_dialog, primary=True).pack(side="left")
        button(actions, "Convert a file", self._select_conversion).pack(side="left", padx=6)
        self.pause_button = button(actions, "Pause queue", self._toggle_queue)
        self.pause_button.pack(side="left", padx=(0, 6))
        button(actions, "Clear finished", self._clear_jobs).pack(side="right")
        self.queue_status = label(page, "", 13, color=MUTED)
        self.queue_status.grid(row=2, column=0, sticky="w", pady=(0, 5))
        page.grid_rowconfigure(3, weight=1)
        self.queue_list = ctk.CTkScrollableFrame(
            page, fg_color=SURFACE, corner_radius=13, border_width=1, border_color=BORDER
        )
        self.queue_list.grid(row=3, column=0, sticky="nsew")
        self.queue_list.grid_columnconfigure(0, weight=1)
        self.queue_active_label = None

    def _build_library(self, page):
        self._heading(page, "Library")
        button(page, "Clear history", self._clear_history).grid(row=1, column=0, sticky="e", pady=(0, 8))
        page.grid_rowconfigure(2, weight=1)
        self.library = ctk.CTkScrollableFrame(
            page, fg_color=SURFACE, corner_radius=13, border_width=1, border_color=BORDER
        )
        self.library.grid(row=2, column=0, sticky="nsew")
        self.library.grid_columnconfigure(0, weight=1)

    def _build_preferences(self, page):
        self._heading(page, "Preferences")
        page.grid_rowconfigure(1, weight=1)
        card = ctk.CTkScrollableFrame(page, fg_color=SURFACE, corner_radius=13)
        card.grid(row=1, column=0, sticky="nsew")
        card.grid_columnconfigure(0, weight=1)
        label(card, "Default quality", 16, bold=True).grid(row=0, column=0, sticky="w", padx=18, pady=(14, 4))
        self._selector(card, self.default_format, self._change_default).grid(
            row=1, column=0, columnspan=2, sticky="ew", padx=18
        )
        label(card, "Download folder", 16, bold=True).grid(row=2, column=0, sticky="w", padx=18, pady=(15, 4))
        ctk.CTkEntry(
            card, textvariable=self.folder_value, state="readonly", height=39,
            border_width=1, border_color=BORDER, fg_color=FIELD, text_color=INK, font=font(13), corner_radius=8,
        ).grid(row=3, column=0, sticky="ew", padx=(18, 8))
        button(card, "Browse", self._choose_folder, width=78).grid(row=3, column=1, padx=(0, 18))
        label(card, "Appearance", 16, bold=True).grid(row=4, column=0, sticky="w", padx=18, pady=(12, 4))
        appearance = ctk.CTkOptionMenu(
            card, values=["Light", "Dark", "System"], command=self._apply_appearance,
            fg_color=CONTROL, button_color=CONTROL_HOVER, text_color=INK,
            dropdown_fg_color=SURFACE, dropdown_text_color=INK,
        )
        appearance.set(self.appearance)
        appearance.grid(row=5, column=0, sticky="w", padx=18)

        label(card, "YouTube account", 16, bold=True).grid(row=6, column=0, sticky="w", padx=18, pady=(12, 4))
        account = ctk.CTkFrame(card, fg_color="transparent")
        account.grid(row=7, column=0, columnspan=2, sticky="ew", padx=18)
        button(account, "Choose cookies file", self._choose_cookies, width=138).pack(side="left")
        button(account, "Disconnect", self._disconnect_cookies).pack(side="left", padx=6)
        button(account, "Open YouTube", lambda: webbrowser.open("https://www.youtube.com/"), width=104).pack(side="left")
        self.cookie_status = label(card, self._cookie_summary(), 12, color=MUTED)
        self.cookie_status.grid(row=8, column=0, sticky="w", padx=18)
        self.browser_menu = ctk.CTkOptionMenu(
            card, values=list(BROWSERS), command=self._choose_browser, fg_color=CONTROL,
            button_color=CONTROL_HOVER, text_color=INK, dropdown_fg_color=SURFACE, dropdown_text_color=INK,
        )
        self.browser_menu.set(self.cookie_browser)
        self.browser_menu.grid(row=9, column=0, sticky="w", padx=18, pady=(4, 0))
        label(card, "Proof of Origin token (optional, session only)", 12, color=MUTED).grid(
            row=10, column=0, sticky="w", padx=18, pady=(4, 0)
        )
        ctk.CTkEntry(
            card, textvariable=self.po_token, show="•", fg_color=FIELD, text_color=INK, border_color=BORDER,
        ).grid(row=11, column=0, sticky="ew", padx=(18, 8))

        label(card, f"Updates · GrabIt {updater.VERSION}", 16, bold=True).grid(
            row=12, column=0, sticky="w", padx=18, pady=(12, 4)
        )
        updates = ctk.CTkFrame(card, fg_color="transparent")
        updates.grid(row=13, column=0, columnspan=2, sticky="ew", padx=18)
        self.update_button = button(updates, "Check for updates", self._check_updates, width=128)
        self.update_button.pack(side="left")
        button(updates, "GrabIt releases", lambda: webbrowser.open(updater.RELEASES_URL), width=113).pack(side="left", padx=6)
        button(updates, "yt-dlp releases", lambda: webbrowser.open(updater.YTDLP_URL), width=108).pack(side="left")
        self.update_status = label(card, "", 12, color=MUTED, wraplength=500, justify="left")
        self.update_status.grid(row=14, column=0, sticky="w", padx=18)
        self.preference_status = label(card, "", 12, color=MUTED)
        self.preference_status.grid(row=15, column=0, sticky="w", padx=18, pady=(4, 9))

    def _navigate(self, name):
        for page_name, page in self.pages.items():
            if page_name == name:
                page.grid(row=0, column=0, sticky="nsew")
            else:
                page.grid_remove()
            nav, marker = self.nav[page_name]
            active = page_name == name
            nav.configure(
                fg_color=SIDE_ACTIVE if active else SIDE,
                hover_color=SIDE_ACTIVE if active else SIDE_HOVER,
                text_color="#ffffff" if active else SIDE_MUTED,
            )
            marker.configure(fg_color=SIDE_ACCENT if active else SIDE)
        if name == "Library":
            self._render_library()
        elif name == "Queue":
            self._render_queue()

    def _focus_link(self, _event):
        if self.grab_current() is None:
            self._navigate("New download")
            self.url_entry.focus_set()

    def _render_library(self):
        for child in self.library.winfo_children():
            child.destroy()
        if not self.history:
            label(self.library, "No completed downloads yet", 17, bold=True).grid(
                row=0, column=0, sticky="w", padx=17, pady=14
            )
        for index, entry in enumerate(self.history):
            path = Path(entry["path"])
            row = ctk.CTkFrame(self.library, fg_color=FIELD, corner_radius=9)
            row.grid(row=index, column=0, sticky="ew", padx=6, pady=5)
            row.grid_columnconfigure(0, weight=1)
            label(row, path.name, 14, bold=True, wraplength=270, justify="left").grid(
                row=0, column=0, sticky="w", padx=12, pady=(8, 0)
            )
            label(row, f"{entry['format']}  ·  {entry['date']}", 12, color=MUTED).grid(
                row=1, column=0, sticky="w", padx=12, pady=(0, 8)
            )
            button(row, "Open", lambda item=path: self._open(item), width=65).grid(
                row=0, column=1, rowspan=2, padx=5
            )
            button(row, "Folder", lambda item=path.parent: self._open(item), width=65).grid(
                row=0, column=2, rowspan=2, padx=2
            )
            button(row, "Convert", lambda item=path: self._queue_conversion(item), width=67).grid(
                row=0, column=3, rowspan=2, padx=2
            )
            button(row, "Remove", lambda item=entry: self._remove_history(item), width=66).grid(
                row=0, column=4, rowspan=2, padx=(2, 9)
            )
        self.library_status = label(self.library, "", 12, color=ERROR)
        self.library_status.grid(row=len(self.history) or 1, column=0, sticky="w", padx=12)

    def _format_changed(self, value):
        detail = self.source_formats[value][1] if value in self.source_formats else FORMATS[value][1]
        self.format_detail.configure(text=detail)

    def _change_default(self, value):
        self.default_format = value
        self.format_menu.set(value)
        self._format_changed(value)
        self._save_preferences()

    def _choose_folder(self):
        chosen = filedialog.askdirectory(parent=self, title="Choose download folder", initialdir=str(self.folder))
        if chosen:
            self.folder = Path(chosen)
            self.folder_value.set(chosen)
            self._save_preferences()

    def _save_preferences(self):
        if self._save_profile():
            self.preference_status.configure(text="Preferences saved", text_color=SUCCESS)
        else:
            self.preference_status.configure(text="Could not save preferences", text_color=ERROR)
            if not self.busy:
                self._status("Preferences could not be saved", color=ERROR)

    def _paste(self):
        try:
            text = self.clipboard_get().strip()
        except tk.TclError:
            text = ""
        if text:
            self.url.set(text)
            self.url_entry.focus_set()
            self._start_preview()
        else:
            self._status("Clipboard is empty", color=ERROR)

    def _clear_preview(self, *_):
        self.preview_id += 1
        if self.format_menu.get() in self.source_formats:
            self.format_menu.set(self.default_format)
            self._format_changed(self.default_format)
        self.source_formats.clear()
        self.format_menu.configure(values=list(FORMATS))
        self.preview_image = None
        self.thumbnail_button.configure(state="disabled")
        self.playlist_button.configure(state="normal")
        self.preview_button.configure(state="normal")
        self.preview_art.configure(image=None, text="▷")
        self.preview_title.configure(text="No video loaded")
        self.preview_detail.configure(text="Paste a link and select Preview.")

    def _start_preview(self):
        if self.preview_button.cget("state") == "disabled":
            return
        url = self.url.get().strip()
        if not url:
            self._status("Enter a video link first", color=ERROR)
            self.url_entry.focus_set()
            return
        self._clear_preview()
        self.preview_button.configure(state="disabled")
        self.preview_title.configure(text="Loading preview…")
        self.preview_detail.configure(text="")
        threading.Thread(target=self._preview_worker, args=(url, self.preview_id, self._access()), daemon=True).start()

    def _preview_worker(self, url, preview_id, access):
        result = engine.get_info(url, include_formats=True, **access)
        image = thumbnail_image(result["thumbnail"]) if result.get("thumbnail") else None
        self.events.put(("preview", (preview_id, url, result, image)))

    def _show_preview(self, preview_id, url, result, image):
        if preview_id != self.preview_id:
            return
        self.preview_button.configure(state="normal")
        if not result.get("ok"):
            self.preview_title.configure(text="Preview unavailable")
            self.preview_detail.configure(text=result.get("message") or "Check the video link.")
            return
        if image is not None:
            self.preview_image = image
            self.thumbnail_button.configure(state="normal")
            thumb = ImageOps.fit(image, (492, 276), Image.Resampling.LANCZOS)
            self.preview_art.configure(image=ctk.CTkImage(light_image=thumb, size=(246, 138)), text="")
        title = result.get("title") or "Untitled video"
        self.preview_title.configure(text=title[:100] + ("…" if len(title) > 100 else ""))
        host = urlsplit(url).hostname
        duration = result.get("duration")
        self.preview_detail.configure(text=f"{host}  ·  {duration}" if duration and duration != "00:00" else host)
        self.source_formats = format_catalog(result.get("formats", []))
        self.format_menu.configure(values=[*FORMATS, *self.source_formats])

    def _save_thumbnail(self):
        if self.preview_image is None:
            return
        destination = filedialog.asksaveasfilename(
            parent=self, initialdir=str(self.folder), initialfile="thumbnail.jpg",
            defaultextension=".jpg", filetypes=[("JPEG image", "*.jpg")],
        )
        if destination:
            try:
                self.preview_image.save(destination, format="JPEG", quality=95)
            except OSError:
                self._status("Could not save the thumbnail", color=ERROR)
            else:
                self._status("Thumbnail saved", Path(destination).name, SUCCESS)

    def _apply_appearance(self, value):
        self.appearance = value
        ctk.set_appearance_mode(value)
        self._save_preferences()

    def _cookie_summary(self):
        if self.cookie_file:
            return f"Using {Path(self.cookie_file).name}"
        if self.cookie_browser != "None":
            return f"Using cookies from {self.cookie_browser}"
        return "No account cookies selected"

    def _choose_cookies(self):
        path = filedialog.askopenfilename(
            parent=self, title="Select an exported Netscape cookies file", filetypes=[("Text files", "*.txt")]
        )
        if path:
            self.cookie_file = path
            self.cookie_browser = "None"
            self.browser_menu.set("None")
            self.cookie_status.configure(text=self._cookie_summary())
            self._save_preferences()

    def _choose_browser(self, value):
        if value != "None" and not messagebox.askyesno(
            "Connect browser", f"Allow GrabIt to use cookies from your local {value} profile for video extraction?",
            parent=self,
        ):
            self.browser_menu.set(self.cookie_browser)
            return
        self.cookie_browser = value
        if value != "None":
            self.cookie_file = ""
        self.cookie_status.configure(text=self._cookie_summary())
        self._save_preferences()

    def _disconnect_cookies(self):
        self.cookie_file = ""
        self.cookie_browser = "None"
        self.browser_menu.set("None")
        self.po_token.set("")
        self.cookie_status.configure(text=self._cookie_summary())
        self._save_preferences()

    def _access(self):
        return {
            "cookie_file": self.cookie_file,
            "cookie_browser": self.cookie_browser.lower() if self.cookie_browser != "None" else "",
            "po_token": self.po_token.get().strip(),
        }

    def _check_updates(self):
        self.update_button.configure(state="disabled")
        self.update_status.configure(text="Checking releases…")
        threading.Thread(
            target=lambda: self.events.put(("updates", updater.check_updates())), daemon=True
        ).start()

    def _remove_history(self, entry):
        self.history.remove(entry)
        self._render_library()
        if not self._save_profile():
            self.library_status.configure(text="Could not save the change")

    def _clear_history(self):
        if self.history and messagebox.askyesno(
            "Clear history", "Remove all Library entries? Downloaded files will remain on disk.", parent=self,
        ):
            self.history.clear()
            self._render_library()
            if not self._save_profile():
                self.library_status.configure(text="Could not save the change")

    def _load_playlist(self):
        url = self.url.get().strip()
        if not url:
            self._status("Enter a playlist link first", color=ERROR)
            return
        self.playlist_button.configure(state="disabled")
        self.playlist_button.configure(text="Loading…")
        threading.Thread(target=self._playlist_worker, args=(url, self._access()), daemon=True).start()

    def _playlist_worker(self, url, access):
        self.events.put(("playlist", (url, engine.get_playlist(url, **access))))

    def _dialog(self, title, geometry):
        dialog = ctk.CTkToplevel(self, fg_color=BACK)
        dialog.title(title[:100])
        dialog.geometry(geometry)
        dialog.transient(self)
        dialog.grab_set()
        return dialog

    def _show_playlist(self, url, result):
        self.playlist_button.configure(text="Playlist", state="normal")
        if url != self.url.get().strip():
            return
        if not result["ok"]:
            self._status("Could not load playlist", result["message"], ERROR)
            return

        dialog = self._dialog(result["title"], "770x630")
        label(dialog, result["title"][:80], 23, bold=True).pack(anchor="w", padx=19, pady=(14, 8))
        toolbar = ctk.CTkFrame(dialog, fg_color="transparent")
        toolbar.pack(fill="x", padx=18)
        options = self._selector(toolbar, self.default_format, lambda value: apply_format(value))
        options.pack(side="left")
        scroll = ctk.CTkScrollableFrame(dialog, fg_color=SURFACE)
        scroll.pack(fill="both", expand=True, padx=18, pady=9)
        scroll.grid_columnconfigure(0, weight=1)
        selections = []
        catalogs = {}

        def apply_format(value):
            for _, _, selector in selections:
                selector.set(value)

        def select_all(value):
            for _, selected, _ in selections:
                selected.set(value)

        button(toolbar, "Select all", lambda: select_all(True), width=75).pack(side="left", padx=6)
        button(toolbar, "Select none", lambda: select_all(False), width=81).pack(side="left")
        for row, entry in enumerate(result["entries"]):
            selected = tk.BooleanVar(master=dialog, value=True)
            text = f"{entry['index']:02d}.  {entry['title'][:44]}"
            if entry["duration"]:
                text += f"  ·  {entry['duration']}"
            check = ctk.CTkCheckBox(scroll, text=text, variable=selected, text_color=INK, font=font(13))
            check.grid(row=row, column=0, sticky="w", pady=4, padx=4)
            selector = self._selector(scroll, self.default_format, None)
            selector.grid(row=row, column=1, sticky="e", padx=4, pady=3)
            picker = button(scroll, "Formats", None, width=72)
            picker.configure(command=lambda item=entry, menu=selector, action=picker: self._load_playlist_formats(
                item["url"], menu, action, catalogs,
            ))
            picker.grid(row=row, column=2, sticky="e", padx=4, pady=3)
            selections.append((entry, selected, selector))

        footer = ctk.CTkFrame(dialog, fg_color="transparent")
        footer.pack(fill="x", padx=18, pady=(0, 14))
        numbered = tk.BooleanVar(master=dialog, value=True)
        overwrite = tk.BooleanVar(master=dialog)
        convert = tk.BooleanVar(master=dialog, value=self.convert_download.get())
        for text, variable in (("Number files", numbered), ("Overwrite", overwrite), ("H.264 conversion", convert)):
            ctk.CTkCheckBox(footer, text=text, variable=variable, text_color=INK, font=font(12)).pack(side="left", padx=(0, 9))

        def enqueue():
            jobs = [
                Job(entry["url"], entry["title"], self.folder, selector.get(),
                    format_id=catalogs.get(selector, {}).get(selector.get(), (None,))[0],
                    index=entry["index"] if numbered.get() else None, overwrite=overwrite.get(), convert=convert.get())
                for entry, selected, selector in selections if selected.get()
            ]
            if jobs:
                dialog.destroy()
                self._enqueue(jobs, show_queue=True)
            else:
                messagebox.showinfo("Playlist", "Select at least one video.", parent=dialog)

        button(footer, "Queue selected", enqueue, primary=True, width=113).pack(side="right")

    def _load_playlist_formats(self, url, selector, picker, catalogs):
        picker.configure(text="Loading…", state="disabled")
        access = self._access()

        def fetch():
            result = engine.get_info(url, include_formats=True, **access)
            self.events.put(("playlist_formats", (selector, picker, catalogs, result)))

        threading.Thread(target=fetch, daemon=True).start()

    def _show_playlist_formats(self, selector, picker, catalogs, result):
        if not selector.winfo_exists():
            return
        formats = format_catalog(result.get("formats", []))
        catalogs[selector] = formats
        selector.configure(values=[*FORMATS, *formats])
        picker.configure(text="Loaded" if formats else "Retry", state="disabled" if formats else "normal")
        if not result["ok"]:
            messagebox.showerror("Source formats", result["message"], parent=selector.winfo_toplevel())

    def _bulk_dialog(self):
        dialog = self._dialog("Add links", "620x390")
        label(dialog, "Enter one video URL per line", 16, bold=True).pack(anchor="w", padx=16, pady=12)
        links = ctk.CTkTextbox(dialog, fg_color=SURFACE, text_color=INK)
        links.pack(fill="both", expand=True, padx=16)
        options = ctk.CTkFrame(dialog, fg_color="transparent")
        options.pack(fill="x", padx=16, pady=12)
        selector = self._selector(options, self.default_format, None)
        selector.pack(side="left")

        def enqueue():
            urls = list(dict.fromkeys(line.strip() for line in links.get("1.0", "end").splitlines() if line.strip()))
            jobs = []
            try:
                for url in urls:
                    jobs.append(Job(engine._url(url), url, self.folder, selector.get(), convert=self.convert_download.get()))
            except ValueError:
                messagebox.showerror("Add links", f"Check this URL: {url[:100]}", parent=dialog)
                return
            if jobs:
                dialog.destroy()
                self._enqueue(jobs, show_queue=True)

        button(options, "Add to queue", enqueue, primary=True).pack(side="right")

    def _select_conversion(self):
        path = filedialog.askopenfilename(parent=self, title="Choose a video to convert", initialdir=str(self.folder))
        if path:
            self._queue_conversion(Path(path))

    def _queue_conversion(self, path):
        self._enqueue([Job(str(path), path.name, path.parent, "H.264 conversion", local=True)], show_queue=True)

    def _enqueue(self, jobs, *, show_queue=False):
        self.jobs.extend(jobs)
        if show_queue:
            self._navigate("Queue")
        else:
            self._refresh_queue()
        self._start_next()

    def _refresh_queue(self):
        pending = sum(job.state == "Waiting" for job in self.jobs)
        self.queue_status.configure(text=f"{pending} waiting" + (" · Queue paused" if self.queue_paused else ""))
        if self.pages["Queue"].winfo_ismapped():
            self._render_queue()

    def _render_queue(self):
        for child in self.queue_list.winfo_children():
            child.destroy()
        self.queue_active_label = None
        if not self.jobs:
            label(self.queue_list, "No queued downloads", 16, bold=True).grid(row=0, column=0, padx=14, pady=12, sticky="w")
        for index, job in enumerate(self.jobs):
            row = ctk.CTkFrame(self.queue_list, fg_color=FIELD, corner_radius=9)
            row.grid(row=index, column=0, sticky="ew", padx=5, pady=4)
            row.grid_columnconfigure(0, weight=1)
            label(row, job.title[:100], 14, bold=True, wraplength=340, justify="left").grid(
                row=0, column=0, sticky="w", padx=11, pady=(7, 0)
            )
            status = label(row, f"{job.quality}  ·  {job.state}", 12, color=MUTED, wraplength=360, justify="left")
            status.grid(row=1, column=0, sticky="w", padx=11, pady=(0, 7))
            if job is self.active_job:
                self.queue_active_label = status
                status.configure(text=f"{job.quality}  ·  {job.state}  {job.progress:.0f}%")
                if not job.local:
                    pause = button(row, "Pause", self._pause_active, width=68)
                    pause.grid(row=0, column=1, rowspan=2)
                    if self.cancelling:
                        pause.configure(state="disabled")
                cancel = button(row, "Cancel", self._cancel_download, width=72)
                cancel.grid(row=0, column=2, rowspan=2, padx=9)
                if self.cancelling:
                    cancel.configure(state="disabled")
            elif job.state == "Waiting":
                button(row, "Remove", lambda item=job: self._remove_job(item), width=72).grid(row=0, column=2, rowspan=2, padx=9)
            elif job.state in ("Failed", "Cancelled"):
                status.configure(text=job.message or job.state)
                button(row, "Retry", lambda item=job: self._retry_job(item), width=72).grid(row=0, column=2, rowspan=2, padx=9)

    def _remove_job(self, job):
        self.jobs.remove(job)
        self._refresh_queue()

    def _retry_job(self, job):
        job.cancelled.clear()
        job.state, job.message, job.progress = "Waiting", "", 0
        self._refresh_queue()
        self._start_next()

    def _clear_jobs(self):
        self.jobs = [job for job in self.jobs if job is self.active_job or job.state == "Waiting"]
        self._refresh_queue()

    def _toggle_queue(self):
        self.queue_paused = not self.queue_paused
        self.pause_button.configure(text="Resume queue" if self.queue_paused else "Pause queue")
        self._refresh_queue()
        self._start_next()

    def _pause_active(self):
        if self.active_job is None or self.cancelling:
            return
        self.active_job.pause_requested = True
        if not self.queue_paused:
            self._toggle_queue()
        self._cancel_download()

    def _status(self, state, detail="", color=INK):
        self.status_label.configure(text=state, text_color=color)
        self.status_detail.configure(text=detail)

    def _set_busy(self, busy):
        self.busy = busy
        self.cancelling = False
        if busy:
            self.cancel_button.configure(state="normal")
            self.cancel_button.grid()
        else:
            self.cancel_button.grid_remove()

    def _start_download(self):
        url = self.url.get().strip()
        if not url:
            self._status("Enter a video link first", color=ERROR)
            self.url_entry.focus_set()
            return
        quality = self.format_menu.get()
        source = self.source_formats.get(quality)
        title = self.preview_title.cget("text") if self.source_formats else url
        self._enqueue([Job(
            url, title, self.folder, quality, format_id=source[0] if source else None,
            convert=self.convert_download.get(),
        )])

    def _start_next(self):
        if self.busy or self.queue_paused or self.close_pending:
            return
        job = next((item for item in self.jobs if item.state == "Waiting"), None)
        if job is None:
            return
        self.active_job = job
        job.state = "Starting"
        self._set_busy(True)
        self.output = None
        self.open_button.grid_remove()
        self.progress_bar.set(0)
        self.percent_label.configure(text="0%")
        self._status("Preparing conversion" if job.local else "Preparing download", job.title[:120])
        self._refresh_queue()
        threading.Thread(
            target=self._download_worker, args=(job, self._access()), daemon=True
        ).start()

    def _download_worker(self, job, access):
        def report(data):
            self.events.put(("progress", data))

        try:
            if job.local:
                result = convert_to_h264(job.source, report, cancel_event=job.cancelled)
            else:
                result = engine.download(
                    job.source, str(job.folder), FORMATS.get(job.quality, ("highest",))[0], report,
                    cancel_event=job.cancelled, format_id=job.format_id, batch_index=job.index,
                    overwrite=job.overwrite, convert=job.convert, **access,
                )
        except Exception as error:
            result = {"ok": False, "message": f"Unexpected error: {error}"}
        self.events.put(("done", result))

    def _poll_events(self):
        for _ in range(100):
            try:
                event, data = self.events.get_nowait()
            except queue.Empty:
                break
            if event == "preview":
                self._show_preview(*data)
            elif event == "playlist":
                self._show_playlist(*data)
            elif event == "playlist_formats":
                self._show_playlist_formats(*data)
            elif event == "updates":
                self.update_status.configure(text=data)
                self.update_button.configure(state="normal")
            elif event == "progress":
                self._show_progress(data)
            elif event == "done":
                self._show_result(data)
                if self.close_pending:
                    self.destroy()
                    return
                self._start_next()
        self.after(80, self._poll_events)

    def _show_progress(self, data):
        if self.cancelling or self.active_job is None:
            return
        percent = float(data.get("percent") or 0)
        state = {"finished": "Processing", "converting": "Converting"}.get(data.get("status"), "Downloading")
        self.active_job.state, self.active_job.progress = state, percent
        if self.queue_active_label is not None:
            self.queue_active_label.configure(text=f"{self.active_job.quality}  ·  {state}  {percent:.0f}%")
        self.progress_bar.set(percent / 100)
        self.percent_label.configure(text=f"{percent:.0f}%")
        details = [data["speed"]] if data.get("speed") else []
        if data.get("eta"):
            details.append(f"{data['eta']} remaining")
        self._status(state, "  ·  ".join(details))

    def _show_result(self, result):
        self._set_busy(False)
        job = self.active_job
        self.active_job = None
        quality = job.quality if job else self.format_menu.get()
        message = str(result.get("message") or "Try the download again.")
        if result.get("ok"):
            if job:
                job.state, job.progress = "Complete", 100
            self.progress_bar.set(1)
            self.percent_label.configure(text="100%")
            path = result.get("filepath")
            detail = ""
            if path:
                self.output = Path(path)
                self.open_button.grid()
                detail = self.output.name
                self.history = [{
                    "path": str(self.output), "format": quality, "date": datetime.now().strftime("%d %b %Y, %H:%M"),
                }] + [entry for entry in self.history if entry["path"] != str(self.output)][:49]
                if not self._save_profile():
                    detail += " · Could not save to Library"
                if self.pages["Library"].winfo_ismapped():
                    self._render_library()
            self._status(message.rstrip("."), detail, SUCCESS)
        else:
            self.progress_bar.set(0)
            self.percent_label.configure(text="0%")
            cancelled = message.lower().startswith("download cancelled")
            if cancelled and job and job.pause_requested:
                job.pause_requested = False
                job.cancelled.clear()
                job.state, job.progress = "Waiting", 0
                self._status("Download paused", "Resume the queue to continue.")
            elif job:
                job.state, job.message = ("Cancelled" if cancelled else "Failed"), message
                if cancelled:
                    self._status("Download cancelled")
                else:
                    self._status("Download failed", message, ERROR)
        self._refresh_queue()

    def _cancel_download(self):
        if self.active_job is None or self.cancelling:
            return
        self.cancelling = True
        self.cancel_button.configure(state="disabled")
        state = "Pausing" if self.active_job.pause_requested else "Cancelling"
        self._status(f"{state} download")
        self.active_job.state = state
        self.active_job.cancelled.set()
        self._refresh_queue()

    def _open_output(self):
        if self.output is not None:
            self._open(self.output)

    def _open(self, path):
        try:
            open_local_path(path)
        except OSError:
            if self.pages["Library"].winfo_ismapped():
                self.library_status.configure(text=f"Could not open {path.name or path}.")
            else:
                self._status("Could not open the file", str(path), ERROR)

    def _on_close(self):
        if self.busy:
            self.close_pending = True
            self._cancel_download()
        else:
            self.destroy()


def main():
    ctk.set_appearance_mode("light")
    app = GrabItApp()
    app.mainloop()


if __name__ == "__main__":
    main()
