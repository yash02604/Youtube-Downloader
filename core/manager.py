"""Public headless entry points backed by the existing, single engine.

Implementation modules stay in place during the incremental migration to keep
existing imports, packaging and patch points compatible.
"""
from download_manager import DownloadManager, DownloadPriority
from video_analyzer import VideoAnalyzer
from format_selector import FormatSelector
from .events import DownloadEvents


def create_manager(**options):
    manager = DownloadManager(**options)
    return manager, DownloadEvents(manager)
