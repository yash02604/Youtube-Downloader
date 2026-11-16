import queue
import time
import unittest
from unittest.mock import Mock
from gui import IDMCloneGUI

class _RootStub:
    def __init__(self):
        self.after_calls = []

    def after(self, interval, callback):
        self.after_calls.append((interval, callback))


class _VarStub:
    def __init__(self):
        self.value = None

    def set(self, value):
        self.value = value


class GuiResponsivenessTests(unittest.TestCase):
    def make_gui(self):
        gui = IDMCloneGUI.__new__(IDMCloneGUI)
        gui.root = _RootStub()
        gui.update_interval = 250
        gui.full_refresh_interval = 2.0
        gui._last_download_list_refresh = time.monotonic()
        gui.ui_refresh_queue = queue.Queue(maxsize=1)
        gui.video_info_queue = queue.Queue()
        gui.thumbnail_preview_queue = queue.Queue()
        gui.tool_status_queue = queue.Queue(maxsize=1)
        gui.thumbnail_previews_in_flight = set()
        gui.video_inspection_pending = False
        gui._tool_status_refresh_pending = False
        gui.ffmpeg_status_var = _VarStub()
        gui.refresh_downloads_list = Mock()
        return gui

    def test_idle_tick_does_not_rescan_download_list(self):
        gui = self.make_gui()
        gui.update_ui()
        gui.refresh_downloads_list.assert_not_called()
        self.assertEqual(len(gui.root.after_calls), 1)

    def test_progress_event_requests_one_refresh(self):
        gui = self.make_gui()
        gui.on_progress_update("dl_1", 10, 100)
        gui.update_ui()
        gui.refresh_downloads_list.assert_called_once_with()
        self.assertTrue(gui.ui_refresh_queue.empty())

    def test_refresh_requests_are_coalesced(self):
        gui = self.make_gui()
        for _ in range(20):
            gui.on_status_update("dl_1", "Downloading")
        self.assertEqual(gui.ui_refresh_queue.qsize(), 1)
        gui.update_ui()
        gui.refresh_downloads_list.assert_called_once_with()

    def test_periodic_fallback_refreshes_without_event(self):
        gui = self.make_gui()
        gui._last_download_list_refresh = time.monotonic() - 3.0
        gui.update_ui()
        gui.refresh_downloads_list.assert_called_once_with()

    def test_tool_status_result_is_applied_on_ui_tick(self):
        gui = self.make_gui()
        tools = type("Tools", (), {"location": "C:/ffmpeg"})()
        runtime = type("Runtime", (), {"version": "2.9.8", "path": "C:/deno.exe"})()
        gui._tool_status_refresh_pending = True
        gui.tool_status_queue.put_nowait((tools, runtime, None))
        gui.update_ui()
        self.assertIs(gui.ffmpeg_tools, tools)
        self.assertIs(gui.deno_runtime, runtime)
        self.assertFalse(gui._tool_status_refresh_pending)
        self.assertIn("FFmpeg: Available", gui.ffmpeg_status_var.value)
        self.assertIn("Deno 2.9.8", gui.ffmpeg_status_var.value)


if __name__ == "__main__":
    unittest.main()
