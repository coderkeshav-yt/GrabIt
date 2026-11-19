import os
import unittest
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from unittest.mock import patch

import engine


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = cls.enterClassContext(TemporaryDirectory())
        cls.content = b"local downloader fixture" * 4096
        Path(source, "sample.mp4").write_bytes(cls.content)
        handler = partial(QuietHandler, directory=source)
        server = cls.enterClassContext(ThreadingHTTPServer(("127.0.0.1", 0), handler))
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        cls.addClassCleanup(thread.join)
        cls.addClassCleanup(server.shutdown)
        cls.url = f"http://127.0.0.1:{server.server_port}/sample.mp4"

    def test_metadata_direct_link_and_invalid_input(self):
        info = engine.get_info(f"  {self.url}  ")
        self.assertTrue(info["ok"], info)
        self.assertEqual(info["title"], "sample")
        self.assertEqual(info["duration"], "00:00")
        self.assertEqual(engine.get_direct_link(self.url)["link"], self.url)
        self.assertEqual(engine.get_info("   ")["message"], "Please paste a valid video link.")

    def test_playlist_preserves_positions_and_normalizes_youtube_ids(self):
        with patch.object(engine.yt_dlp, "YoutubeDL") as loader:
            loader.return_value.__enter__.return_value.extract_info.return_value = {
                "title": "Sample playlist",
                "entries": [None, {"id": "abc", "ie_key": "Youtube", "title": "Video"}, {"url": "/bad"}],
            }
            result = engine.get_playlist(self.url)
        self.assertEqual(result, {
            "ok": True, "title": "Sample playlist",
            "entries": [{"title": "Video", "url": "https://www.youtube.com/watch?v=abc", "index": 2, "duration": ""}],
        })

    def test_video_format_list_excludes_artwork_audio_and_invalid_ids(self):
        with patch.object(engine, "_extract", return_value={
            "title": "Sample", "formats": [
                {"format_id": "18", "height": 360, "ext": "mp4", "vcodec": "h264", "filesize": 7600},
                {"format_id": "sb", "height": 90, "ext": "mhtml", "vcodec": "images"},
                {"format_id": "251", "ext": "webm", "vcodec": "none"},
                {"format_id": "1+2", "height": 720, "ext": "mp4", "vcodec": "h264"},
            ],
        }):
            result = engine.get_info(self.url, include_formats=True)
        self.assertEqual([(item["id"], item["height"], item["size"]) for item in result["formats"]], [
            ("18", 360, 7600),
        ])

    def test_numbered_exact_format_uses_requested_id_and_avoids_overwrite(self):
        with TemporaryDirectory() as folder, patch.object(engine, "_ffmpeg_for_download", return_value=(folder, False)):
            result = engine.download(self.url, folder, "highest", None, format_id="mp4", batch_index=2)
            self.assertTrue(result["ok"], result)
            self.assertEqual(Path(result["filepath"]).name, "002 - sample [Format mp4].mp4")

    def test_cookie_input_is_copied_and_file_is_left_intact(self):
        with TemporaryDirectory() as folder:
            export = Path(folder, "cookies.txt")
            contents = "# Netscape HTTP Cookie File\n"
            export.write_text(contents, encoding="utf-8")
            with engine.yt_dlp.YoutubeDL(engine._options(cookie_file=export)) as ydl:
                self.assertEqual(list(ydl.cookiejar), [])
            self.assertEqual(export.read_text(encoding="utf-8"), contents)

    def test_download_and_progress(self):
        progress = []
        with TemporaryDirectory() as folder:
            with patch.object(engine, "_ffmpeg_for_download", return_value=(folder, False)):
                result = engine.download(self.url, folder, "best", progress.append)
            self.assertTrue(result["ok"], result)
            self.assertEqual(Path(result["filepath"]).read_bytes(), self.content)
        self.assertEqual(progress[-1]["status"], "finished")
        self.assertEqual(progress[-1]["percent"], 100.0)
        self.assertTrue(all(0.0 <= entry["percent"] <= 100.0 for entry in progress))

    def test_cancel_before_worker_starts(self):
        requested = Event()
        requested.set()
        with patch.object(engine, "_ffmpeg_for_download") as probe:
            result = engine.download(self.url, "", "best", None, cancel_event=requested)
        self.assertEqual(result["message"], "Download cancelled.")
        probe.assert_not_called()

    def test_cancel_during_transfer_and_retry(self):
        with TemporaryDirectory() as folder:
            with patch.object(engine, "_ffmpeg_for_download", return_value=(folder, False)):
                cancelled = engine.download(self.url, folder, "best", lambda _: engine.cancel())
                self.assertEqual(cancelled["message"], "Download cancelled.")
                retried = engine.download(self.url, folder, "best", None)
            self.assertTrue(retried["ok"], retried)
            self.assertEqual(Path(retried["filepath"]).read_bytes(), self.content)

    def test_ffmpeg_checks_past_a_blocked_pair_on_path(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            bundled, older, working = (root / name for name in ("bundled", "older", "working"))
            for candidate in (older, working):
                candidate.mkdir()
                for name in engine.TOOLS:
                    (candidate / name).touch()
            denied = OSError("Application Control blocked this file")
            denied.winerror = 4551
            search_path = os.pathsep.join(map(str, (older, working)))
            with (
                patch.object(engine, "_bundled_ffmpeg", return_value=bundled),
                patch.dict(os.environ, {"PATH": search_path}),
                patch.object(engine, "run", side_effect=[denied, None, None]),
            ):
                self.assertEqual(engine._ffmpeg_for_download(), (str(working.resolve()), False))
