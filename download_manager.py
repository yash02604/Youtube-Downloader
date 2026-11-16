from core.models import Download, DownloadPriority, download_to_dict
from core.persistence import DownloadPersistence
import os
import logging
import ipaddress
import socket
import threading
import time
import requests
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue, Empty
from urllib.parse import parse_qs, urljoin, urlparse
from typing import Optional, Callable
import yt_dlp
from yt_dlp.utils import DownloadCancelled
from video_analyzer import VideoAnalyzer
from format_selector import FormatSelector
from js_runtime import discover_deno
from media_validation import validate_media_output
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.exceptions import ConnectTimeoutError, NewConnectionError
from urllib3.util.connection import create_connection as urllib3_create_connection
from config import config_manager, youtube_session_options
from config import get_application_dir, get_user_data_dir
from logging_utils import redact_sensitive_text

logger = logging.getLogger(__name__)

MIN_CHUNK_SIZE = 2 * 1024 * 1024  # 2 MB
HISTORY_LIMIT = 200


def _is_public_address(value: str) -> bool:
    """Return whether an IP is globally routable, including mapped IPv4."""
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_global and not address.is_multicast


def _origin(url: str):
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    return scheme, (parsed.hostname or "").lower(), parsed.port or (443 if scheme == "https" else 80)


def _approved_address_for_url(url: str, allow_non_public_origin: bool = False) -> str:
    """Resolve once, validate every answer, and return the address to pin."""
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("HTTP destination has no hostname")
    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        try:
            results = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
            addresses = {str(ipaddress.ip_address(result[4][0])) for result in results}
        except (OSError, ValueError) as exc:
            raise ValueError("HTTP destination could not be resolved safely") from exc
    else:
        addresses = {str(literal)}

    if not addresses:
        raise ValueError("HTTP destination did not resolve to an IP address")
    if allow_non_public_origin:
        if len(addresses) != 1 or next(iter(addresses)).lower() != hostname.lower():
            raise ValueError("Only the explicitly selected IP address may use a non-public destination")
    elif any(not _is_public_address(address) for address in addresses):
        raise ValueError("HTTP destination resolves to a non-public network address")
    return sorted(addresses)[0]


class _PinnedHTTPConnection(HTTPConnection):
    def __init__(self, *args, approved_address: str, **kwargs):
        self.approved_address = approved_address
        super().__init__(*args, **kwargs)

    def _new_conn(self):
        try:
            return urllib3_create_connection(
                (self.approved_address, self.port), self.timeout,
                source_address=self.source_address,
                socket_options=self.socket_options,
            )
        except socket.timeout as exc:
            raise ConnectTimeoutError(
                self, f"Connection to {self.host} timed out (approved address {self.approved_address})"
            ) from exc
        except OSError as exc:
            raise NewConnectionError(self, f"Connection to approved address failed: {exc}") from exc


class _PinnedHTTPSConnection(HTTPSConnection):
    def __init__(self, *args, approved_address: str, **kwargs):
        self.approved_address = approved_address
        super().__init__(*args, **kwargs)

    def _new_conn(self):
        try:
            return urllib3_create_connection(
                (self.approved_address, self.port), self.timeout,
                source_address=self.source_address,
                socket_options=self.socket_options,
            )
        except socket.timeout as exc:
            raise ConnectTimeoutError(
                self, f"Connection to {self.host} timed out (approved address {self.approved_address})"
            ) from exc
        except OSError as exc:
            raise NewConnectionError(self, f"Connection to approved address failed: {exc}") from exc


class _PinnedHTTPConnectionPool(HTTPConnectionPool):
    ConnectionCls = _PinnedHTTPConnection


class _PinnedHTTPSConnectionPool(HTTPSConnectionPool):
    ConnectionCls = _PinnedHTTPSConnection


class PinnedAddressHTTPAdapter(HTTPAdapter):
    """Requests adapter that validates and pins each direct HTTP connection."""

    def __init__(self, *, allowed_non_public_origins=()):
        self.allowed_non_public_origins = set(allowed_non_public_origins)
        self._pinned_pools = []
        self._pinned_pools_lock = threading.Lock()
        super().__init__()

    def _connection_for_url(self, url: str, *, verify=True, proxies=None):
        if proxies and any(proxies.values()):
            raise requests.exceptions.ProxyError(
                "HTTP downloads through proxies are disabled to enforce destination address checks"
            )
        if verify is False:
            raise requests.exceptions.SSLError("TLS certificate verification cannot be disabled")
        parsed = urlparse(url)
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"} or not parsed.hostname:
            raise requests.exceptions.InvalidURL("Only HTTP(S) URLs with a hostname are supported")
        allow_non_public = _origin(url) in self.allowed_non_public_origins
        address = _approved_address_for_url(url, allow_non_public)
        port = parsed.port or (443 if scheme == "https" else 80)
        common = {"host": parsed.hostname, "port": port, "approved_address": address}
        if scheme == "https":
            # The pool retains the original hostname, so urllib3 continues to
            # send that hostname for SNI and certificate validation while the
            # connection class opens its socket to the pinned IP.
            common.update(assert_hostname=parsed.hostname)
            pool = _PinnedHTTPSConnectionPool(**common)
        else:
            pool = _PinnedHTTPConnectionPool(**common)
        with self._pinned_pools_lock:
            self._pinned_pools.append(pool)
        return pool

    def get_connection_with_tls_context(self, request, verify, proxies=None, cert=None):
        return self._connection_for_url(request.url, verify=verify, proxies=proxies)

    def get_connection(self, url, proxies=None):
        # Requests 2.31 uses get_connection; newer releases use the TLS-aware hook.
        return self._connection_for_url(url, proxies=proxies)

    def close(self):
        with self._pinned_pools_lock:
            pools, self._pinned_pools = self._pinned_pools, []
        for pool in pools:
            pool.close()
        super().close()


class BandwidthLimiter:
    """Controls download bandwidth to prevent system resource exhaustion"""
    
    def __init__(self, max_bandwidth_kbps: Optional[int] = None):
        self.max_bandwidth = max_bandwidth_kbps * 1024 if max_bandwidth_kbps else None  # Convert to bytes/sec
        self.bytes_downloaded = 0
        self.last_reset_time = time.time()
        self.lock = threading.Lock()
        self.throttle_interval = 0.1  # Check every 100ms
        
    def set_max_bandwidth(self, kbps: Optional[int]):
        """Set maximum bandwidth in KB/s (None for unlimited)"""
        with self.lock:
            self.max_bandwidth = kbps * 1024 if kbps else None
    
    def get_max_bandwidth(self) -> Optional[int]:
        """Get maximum bandwidth in bytes/sec"""
        return self.max_bandwidth
    
    def add_bytes(self, bytes_count: int):
        """Add downloaded bytes to the counter"""
        with self.lock:
            self.bytes_downloaded += bytes_count
    
    def should_throttle(self) -> bool:
        """Check if we should throttle the download"""
        if not self.max_bandwidth:
            return False
            
        current_time = time.time()
        
        # Reset counter every second
        if current_time - self.last_reset_time >= 1.0:
            with self.lock:
                self.bytes_downloaded = 0
                self.last_reset_time = current_time
            return False
        
        # Check if we've exceeded bandwidth limit
        with self.lock:
            elapsed_time = current_time - self.last_reset_time
            if elapsed_time > 0:
                current_rate = self.bytes_downloaded / elapsed_time
                return current_rate > self.max_bandwidth
        
        return False
    
    def wait_if_needed(self):
        """Wait if bandwidth limit has been reached"""
        while self.should_throttle():
            time.sleep(self.throttle_interval)


class HTTPSessionManager:
    """Manages HTTP connection pooling for efficient downloads"""
    
    def __init__(self, max_connections: int = 20, max_retries: int = 3):
        self.max_connections = max_connections
        self.max_retries = max_retries
        self.sessions = {}
        self.session_lock = threading.Lock()
        self.default_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': '*/*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
        }
    
    def get_session(self, base_url: str) -> requests.Session:
        """Get or create a session for the given base URL"""
        with self.session_lock:
            if base_url not in self.sessions:
                session = requests.Session()
                
                # Configure retry strategy
                retry_strategy = Retry(
                    total=self.max_retries,
                    backoff_factor=1,
                    status_forcelist=[429, 500, 502, 503, 504],
                    allowed_methods=["HEAD", "GET", "OPTIONS"]
                )
                
                # Mount adapter with connection pooling
                adapter = HTTPAdapter(
                    pool_connections=self.max_connections,
                    pool_maxsize=self.max_connections,
                    max_retries=retry_strategy
                )
                
                session.mount("http://", adapter)
                session.mount("https://", adapter)
                session.headers.update(self.default_headers)
                
                self.sessions[base_url] = session
            
            return self.sessions[base_url]
    
    def close_session(self, base_url: str):
        """Close and remove a session for the given base URL"""
        with self.session_lock:
            if base_url in self.sessions:
                self.sessions[base_url].close()
                del self.sessions[base_url]
    
    def close_all_sessions(self):
        """Close all sessions and cleanup resources"""
        with self.session_lock:
            for session in self.sessions.values():
                session.close()
            self.sessions.clear()
    
    def get_base_url(self, url: str) -> str:
        """Extract base URL from a full URL"""
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"






class PriorityQueue:
    """Priority-based queue for download management"""
    
    def __init__(self):
        self.high_priority = Queue()
        self.medium_priority = Queue()
        self.low_priority = Queue()
        self.lock = threading.Lock()
        self.all_queues = [
            (DownloadPriority.HIGH, self.high_priority),
            (DownloadPriority.MEDIUM, self.medium_priority),
            (DownloadPriority.LOW, self.low_priority)
        ]
    
    def put(self, item: tuple, priority: int):
        """Add item to appropriate priority queue"""
        with self.lock:
            if priority >= DownloadPriority.HIGH:
                self.high_priority.put(item)
            elif priority >= DownloadPriority.MEDIUM:
                self.medium_priority.put(item)
            else:
                self.low_priority.put(item)
    
    def get(self) -> tuple:
        """Get item from highest priority non-empty queue"""
        with self.lock:
            for _, queue in self.all_queues:
                try:
                    return queue.get_nowait()
                except Empty:
                    continue
        return None

    def remove(self, item) -> bool:
        """Remove a queued item from whichever priority bucket currently holds it."""
        with self.lock:
            for _, work_queue in self.all_queues:
                with work_queue.mutex:
                    try:
                        work_queue.queue.remove(item)
                    except ValueError:
                        continue
                    work_queue.unfinished_tasks -= 1
                    if work_queue.unfinished_tasks == 0:
                        work_queue.all_tasks_done.notify_all()
                    work_queue.not_full.notify()
                    return True
        return False
    
    def empty(self) -> bool:
        """Check if all queues are empty"""
        with self.lock:
            return all(queue.empty() for _, queue in self.all_queues)
    
    def qsize(self) -> int:
        """Get total number of items in all queues"""
        with self.lock:
            return sum(queue.qsize() for _, queue in self.all_queues)
    
    def clear(self):
        """Clear all queues"""
        with self.lock:
            for _, queue in self.all_queues:
                while True:
                    try:
                        queue.get_nowait()
                    except Empty:
                        break


class DownloadManager(DownloadPersistence):
    """Main download manager class"""
    
    def __init__(self, max_concurrent_downloads: int = 3,
                 history_file: Optional[Path] = None,
                 state_file: Optional[Path] = None,
                 request_timeout: tuple = (10, 10),
                 max_retries: int = 3, allow_browser_session: bool = True):
        self.allow_browser_session = allow_browser_session
        self.downloads: dict[str, Download] = {}
        self.max_concurrent = max(1, max_concurrent_downloads)
        self.active_downloads = 0
        self.download_queue = PriorityQueue()  # Use priority queue instead of simple queue
        self.progress_callbacks = []
        self.status_callbacks = []
        self.lock = threading.RLock()
        data_dir = get_user_data_dir()
        self.history_file = Path(history_file) if history_file else data_dir / "download_history.json"
        self.state_file = Path(state_file) if state_file else data_dir / "download_state.json"
        if history_file is None:
            self._migrate_legacy_json(get_application_dir() / "download_history.json", self.history_file, list)
        if state_file is None:
            self._migrate_legacy_json(get_application_dir() / "download_state.json", self.state_file, list)
        self.request_timeout = request_timeout
        self.max_retries = max(0, max_retries)
        self.history = self._load_history()
        self._load_persisted_downloads()
        
        # Initialize HTTP session manager
        self.session_manager = HTTPSessionManager(
            max_connections=max_concurrent_downloads * 4,  # 4 connections per download
            max_retries=3
        )
        
        # Initialize bandwidth limiter (unlimited by default)
        self.bandwidth_limiter = BandwidthLimiter(None)





    @staticmethod
    def _validate_url(url: str) -> str:
        if not isinstance(url, str):
            raise ValueError("URL must be text")
        parsed = urlparse(url.strip())
        if (parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password):
            raise ValueError("Only HTTP(S) URLs without embedded credentials are supported")
        return url.strip()

    @staticmethod
    def _validate_http_redirect_target(initial_url: str, target_url: str):
        """Reject redirects to non-public hosts unless the user chose that origin."""
        initial = urlparse(initial_url)
        target = urlparse(target_url)
        initial_origin = _origin(initial_url)
        target_origin = _origin(target_url)
        if initial.scheme.lower() == "https" and target.scheme.lower() != "https":
            raise ValueError("HTTPS downloads cannot redirect to an unencrypted URL")
        if initial_origin == target_origin:
            try:
                explicitly_selected_ip = ipaddress.ip_address(initial.hostname or "")
            except ValueError:
                explicitly_selected_ip = None
            if explicitly_selected_ip is not None and not _is_public_address(str(explicitly_selected_ip)):
                return
        _approved_address_for_url(target_url)

    def _get_http_response_with_safe_redirects(self, session, download, headers):
        request_url = download.url
        for _ in range(6):
            response = session.get(
                request_url,
                headers=headers,
                stream=True,
                timeout=self.request_timeout,
                allow_redirects=False,
            )
            if not response.is_redirect:
                return response
            location = response.headers.get("Location")
            if not location:
                response.close()
                raise ValueError("HTTP redirect did not provide a destination")
            target_url = self._validate_url(urljoin(response.url or request_url, location))
            self._validate_http_redirect_target(download.url, target_url)
            response.close()
            request_url = target_url
        raise requests.TooManyRedirects("Too many HTTP redirects")

    @staticmethod
    def _extract_video_id(url: str) -> Optional[str]:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        segments = [part for part in parsed.path.split("/") if part]
        if host in {"youtu.be", "www.youtu.be"}:
            return segments[0] if segments else None
        if host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
            video_id = parse_qs(parsed.query).get("v", [None])[0]
            if video_id:
                return video_id
            if len(segments) > 1 and segments[0] in {"shorts", "embed", "live"}:
                return segments[1]
        return None

    @staticmethod
    def _is_safe_download_filename(filename: Optional[str]) -> bool:
        """Accept only a single portable Windows-safe filename component."""
        if not isinstance(filename, str) or filename in {"", ".", ".."}:
            return False
        if (filename != os.path.basename(filename)
                or any(char in filename for char in ("/", "\\", ":", "<", ">", '"', "|", "?", "*"))
                or filename.endswith((".", " "))
                or any(ord(char) < 32 for char in filename)):
            return False
        stem = filename.split(".", 1)[0].upper()
        if stem in {
            "CON", "PRN", "AUX", "NUL",
            *(f"COM{number}" for number in range(1, 10)),
            *(f"LPT{number}" for number in range(1, 10)),
        }:
            return False
        return True

    def _download_to_dict(self, download: Download) -> dict:
        return download_to_dict(download)

    def _add_history_entry(self, info: dict):
        if not info:
            return
        entry = dict(info)
        for key in ("url", "source_url", "error"):
            if isinstance(entry.get(key), str):
                entry[key] = redact_sensitive_text(entry[key])
        entry['speed'] = 0
        if entry['status'] == "Completed":
            entry['progress'] = 100.0
            entry['eta'] = 0
        with self.lock:
            self.history = [h for h in self.history if h.get('id') != entry.get('id')]
            self.history.append(entry)
            limit = max(10, min(5000, int(config_manager.get("history_limit", HISTORY_LIMIT))))
            if len(self.history) > limit:
                self.history = self.history[-limit:]
        self._save_history()

    def remove_history_entry(self, download_id: str):
        with self.lock:
            original_len = len(self.history)
            self.history = [h for h in self.history if h.get('id') != download_id]
        if len(self.history) != original_len:
            self._save_history()

    def remove_download(self, download_id: str) -> bool:
        """Remove a terminal download without racing its worker cleanup."""
        with self.lock:
            download = self.downloads.get(download_id)
            if download:
                if download.slot_active or download.status not in {
                    "Completed", "Error", "Cancelled"
                }:
                    return False
                del self.downloads[download_id]
            self.history = [
                entry for entry in self.history if entry.get("id") != download_id
            ]
        self._save_history()
        self._persist_downloads()
        return True

    def clear_history_entries(self, statuses: Optional[set] = None):
        statuses = statuses or {"Completed", "Error", "Cancelled"}
        with self.lock:
            self.history = [h for h in self.history if h.get('status') not in statuses]
        self._save_history()
        
    def add_download(self, url: str, download_path: str, 
                    download_id: Optional[str] = None,
                    num_threads: int = 8,
                    download_type: str = "Video",
                    video_quality: str = "Best",
                    audio_quality: str = "Best",
                    custom_filename: Optional[str] = None,
                    priority: int = DownloadPriority.DEFAULT,
                    playlist_group_id: Optional[str] = None,
                    playlist_title: Optional[str] = None,
                    playlist_index: Optional[int] = None,
                    playlist_count: Optional[int] = None,
                    subtitle_languages: Optional[list[str]] = None,
                    download_subtitles: Optional[bool] = None,
                    embed_subtitles: Optional[bool] = None,
                    save_thumbnail: Optional[bool] = None,
                    embed_thumbnail: Optional[bool] = None,
                    embed_metadata: Optional[bool] = None) -> str:
        """Add a new download to the queue"""
        url = self._validate_url(url)
        num_threads = max(1, min(32, num_threads))
        if download_id is None:
            download_id = f"dl_{uuid.uuid4().hex}"
        
        # Check if it's a video URL
        is_video = VideoAnalyzer.is_video_url(url)
        video_info = None
        
        if is_video:
            # Fast start: Skip analysis and go straight to queued
            download = Download(url, download_path, download_id, num_threads, is_video=True,
                                custom_filename=custom_filename, priority=priority)
            download.status = "Queued"  # Start directly in Queued state
            download.download_type = download_type
            download.video_quality = video_quality
            download.audio_quality = audio_quality
            download.playlist_group_id = playlist_group_id
            download.video_id = self._extract_video_id(url)
            download.playlist_entry_id = download.video_id if playlist_group_id else None
            download.playlist_title = playlist_title
            download.playlist_index = playlist_index
            download.playlist_count = playlist_count
            download.subtitle_languages = list(
                subtitle_languages if subtitle_languages is not None
                else config_manager.get("subtitle_languages", [])
            )
            download.download_subtitles = bool(
                config_manager.get("download_subtitles", False)
                if download_subtitles is None else download_subtitles
            )
            download.embed_subtitles = bool(
                config_manager.get("embed_subtitles", False)
                if embed_subtitles is None else embed_subtitles
            )
            download.save_thumbnail = bool(
                config_manager.get("save_thumbnail", False)
                if save_thumbnail is None else save_thumbnail
            )
            download.embed_thumbnail = bool(
                config_manager.get("embed_thumbnail", False)
                if embed_thumbnail is None else embed_thumbnail
            )
            download.embed_metadata = bool(
                config_manager.get("embed_metadata", False)
                if embed_metadata is None else embed_metadata
            )
        else:
            download = Download(url, download_path, download_id, num_threads, is_video=False,
                                custom_filename=custom_filename, priority=priority)
        with self.lock:
            if download_id in self.downloads:
                raise ValueError(f"Download ID already exists: {download_id}")
            signature = self._active_request_signature(download)
            active_states = {
                "Queued", "Downloading", "Fetching info", "Processing", "Merging",
                "Converting", "Pausing", "Paused", "Cancelling", "Interrupted",
            }
            if download.is_video and any(
                existing.is_video
                and existing.status in active_states
                and self._active_request_signature(existing) == signature
                for existing in self.downloads.values()
            ):
                raise ValueError("An active download already matches this URL, destination, and quality")
            self.downloads[download_id] = download
            self.download_queue.put(download_id, priority)
        self._persist_downloads()
        self._notify_status_change(download_id, "Queued")
        self._process_queue()
        
        return download_id

    @staticmethod
    def _optional_ytdlp_options(download: Download) -> dict:
        """Build only explicitly selected optional media postprocessing options."""
        options = {}
        languages = list(dict.fromkeys(
            language.strip() for language in download.subtitle_languages
            if isinstance(language, str) and language.strip()
        ))
        subtitles_requested = (
            download.is_video
            and download.download_type != "Audio Only"
            and (download.download_subtitles or download.embed_subtitles)
            and bool(languages)
        )
        if subtitles_requested:
            options.update({
                "writesubtitles": True,
                "writeautomaticsub": True,
                "subtitleslangs": languages,
                "subtitlesformat": "best",
            })
            if download.embed_subtitles:
                options["embedsubtitles"] = True
        if download.save_thumbnail or download.embed_thumbnail:
            options["writethumbnail"] = True
        if download.embed_thumbnail:
            options["embedthumbnail"] = True
        if download.embed_metadata:
            options["addmetadata"] = True
        return options

    @staticmethod
    def _active_request_signature(download: Download):
        parsed = urlparse(download.original_url)
        host = (parsed.hostname or "").lower()
        video_id = DownloadManager._extract_video_id(download.original_url)
        if video_id:
            source_key = ("youtube", video_id)
        else:
            source_key = (
                parsed.scheme.lower(), host, parsed.path.rstrip("/"),
                tuple(sorted(parse_qs(parsed.query).items())),
            )
        return (
            source_key,
            os.path.normcase(os.path.abspath(download.destination_dir)),
            download.download_type,
            download.video_quality,
            download.audio_quality,
        )
    
    def _analyze_video(self, download_id: str):
        """Analyze video URL and get download information - Fast mode, skip detailed analysis"""
        download = self.downloads.get(download_id)
        if not download:
            return
        
        # Set start time for timeout checking
        download.start_time = time.time()
        
        # Fast mode: Skip detailed analysis and go straight to download
        # This reduces start time significantly
        try:
            # Quick check if it's a video URL (already done, but set video_info to None for fast start)
            download.video_info = None  # Skip detailed analysis to start faster
            
            # Check if download was cancelled during analysis
            if download.status == "Cancelled":
                return
            
            # Immediately queue for download - yt-dlp will handle analysis during download
            download.status = "Queued"
            self._notify_status_change(download_id, "Queued")
            self.download_queue.put(download_id, download.priority)
            self._process_queue()
        except Exception as e:
            # Check if download was cancelled
            if download.status == "Cancelled":
                return
            
            # Even if something fails, try downloading with yt-dlp
            download.status = "Queued"
            download.error_message = None  # Clear any previous errors
            self._notify_status_change(download_id, "Queued")
            self.download_queue.put(download_id, download.priority)
            self._process_queue()
    
    def _process_queue(self):
        """Start queued work without exceeding the global concurrency limit."""
        while True:
            with self.lock:
                if getattr(self, '_shutting_down', False):
                    return
                if self.active_downloads >= self.max_concurrent or self.download_queue.empty():
                    return
                download_id = self.download_queue.get()
                download = self.downloads.get(download_id) if download_id else None
                if not download or download.status != "Queued" or download.slot_active:
                    continue
                download.status = "Downloading"
                download.execution_generation += 1
                generation = download.execution_generation
                download.slot_generation = generation
                download.error_message = None
                download.error_category = None
                download.cancel_event.clear()
                download.pause_event.clear()
                download.cancel_requested = False
                download.pause_requested = False
                download.start_time = time.time()
                download.last_update_time = download.start_time
                download.last_downloaded = download.downloaded
                download.speed = 0
                download.avg_speed = 0
                download.slot_active = True
                self.active_downloads += 1

            self._persist_downloads()
            self._notify_status_change(download_id, "Downloading")
            worker = threading.Thread(
                target=self._start_download,
                args=(download_id, generation),
                name=f"download-{download_id}",
                daemon=True,
            )
            with self.lock:
                download.worker = worker
            try:
                worker.start()
            except RuntimeError as exc:
                download.error_message = redact_sensitive_text(str(exc))
                self._transition_download(download_id, "Error")
                self._release_active_slot(download_id, generation)

    def _release_active_slot(self, download_id: str, generation: Optional[int] = None):
        queued_after_release = False
        with self.lock:
            download = self.downloads.get(download_id)
            if (not download or not download.slot_active
                    or (generation is not None and download.slot_generation != generation)):
                return
            download.slot_active = False
            self.active_downloads = max(0, self.active_downloads - 1)
            download.worker = None
            if download.resume_pending and download.status in {"Paused", "Error", "Interrupted"}:
                download.resume_pending = False
                download.pause_event.clear()
                download.cancel_event.clear()
                download.pause_requested = False
                download.cancel_requested = False
                self._set_status_locked(download, "Queued")
                self.download_queue.put(download_id, download.priority)
                queued_after_release = True
        if queued_after_release:
            self._persist_downloads()
            self._notify_status_change(download_id, "Queued")
        self._process_queue()

    def _transition_download(self, download_id: str, new_status: str):
        with self.lock:
            download = self.downloads.get(download_id)
            if not download:
                return
            changed = self._set_status_locked(download, new_status)
            if not changed:
                return
        self._persist_downloads()
        self._notify_status_change(download_id, new_status)

    def _set_video_stage(self, download_id: str, stage: str, generation: Optional[int] = None):
        with self.lock:
            download = self.downloads.get(download_id)
            if not download or download.status in {
                "Pausing", "Paused", "Cancelling", "Cancelled", "Completed", "Error"
            } or (generation is not None and download.execution_generation != generation):
                return
            download.video_stage = stage
            changed = self._set_status_locked(download, stage)
        if changed:
            self._persist_downloads()
            self._notify_status_change(download_id, stage)

    @staticmethod
    def _set_status_locked(download: Download, new_status: str, allowed_transitions=None):
        if download.status == new_status:
            return False
        if allowed_transitions is None:
            allowed_transitions = {
                "Queued": {"Downloading", "Paused", "Cancelled", "Error"},
                "Downloading": {
                    "Fetching info", "Processing", "Merging", "Converting",
                    "Pausing", "Cancelling", "Paused", "Completed", "Error",
                },
                "Fetching info": {
                    "Downloading", "Processing", "Merging", "Converting",
                    "Pausing", "Cancelling", "Paused", "Completed", "Error",
                },
                "Processing": {
                    "Downloading", "Merging", "Converting", "Pausing",
                    "Cancelling", "Paused", "Completed", "Error",
                },
                "Merging": {
                    "Processing", "Converting", "Pausing", "Cancelling",
                    "Paused", "Completed", "Error",
                },
                "Converting": {
                    "Processing", "Merging", "Pausing", "Cancelling",
                    "Paused", "Completed", "Error",
                },
                "Pausing": {"Paused", "Cancelling", "Completed", "Error"},
                "Paused": {"Queued", "Cancelling", "Cancelled", "Error"},
                "Interrupted": {"Queued", "Cancelling", "Cancelled", "Error"},
                "Cancelling": {"Cancelled", "Error"},
                "Error": {"Queued", "Cancelling", "Cancelled"},
            }
        if new_status not in allowed_transitions.get(download.status, set()):
            raise ValueError(
                f"Invalid download state transition: {download.status} -> {new_status}"
            )
        download.status = new_status
        if new_status in {"Paused", "Cancelled", "Completed", "Error"}:
            download.speed = 0
            download.eta = 0
        if new_status in {"Cancelled", "Completed"}:
            download.avg_speed = 0
        return True

    def _get_http_paths(self, download: Download):
        destination_dir = os.path.abspath(download.destination_dir)
        os.makedirs(destination_dir, exist_ok=True)
        with self.lock:
            if download.current_filename:
                filename = download.current_filename
            else:
                filename = self._ensure_unique_filename(
                    destination_dir, download.get_filename()
                )
                download.current_filename = filename
            final_path = os.path.abspath(os.path.join(destination_dir, filename))
            if os.path.dirname(final_path) != destination_dir:
                raise ValueError("The destination filename resolves outside the selected folder")
            download.download_path = final_path
            download.part_path = final_path + ".part"
        metadata_path = Path(download.part_path + ".json")
        self._assert_safe_temp_path(download, download.part_path)
        if metadata_path.is_symlink():
            raise ValueError("Resume metadata must not be a symbolic link")
        return final_path, download.part_path, metadata_path

    @staticmethod
    def _assert_safe_temp_path(download: Download, path: str):
        destination = os.path.normcase(os.path.realpath(download.destination_dir))
        resolved_parent = os.path.normcase(os.path.dirname(os.path.realpath(path)))
        if (os.path.islink(path) or resolved_parent != destination
                or (os.path.exists(path) and os.stat(path).st_nlink > 1)):
            raise ValueError(f"Unsafe partial download path: {path}")

    def _load_resume_metadata(self, download: Download, metadata_path: Path, final_path: str):
        part_path = download.part_path
        if not part_path or not os.path.isfile(part_path):
            return 0
        self._assert_safe_temp_path(download, part_path)
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if (metadata.get("url") != download.original_url
                    or os.path.abspath(metadata.get("final_path", "")) != final_path):
                raise ValueError("partial file metadata does not match this download")
            download.etag = metadata.get("etag")
            download.last_modified = metadata.get("last_modified")
            download.effective_url = metadata.get("effective_url")
            download.file_size = max(0, int(metadata.get("expected_size") or 0))
            return os.path.getsize(part_path)
        except FileNotFoundError:
            return (
                os.path.getsize(part_path)
                if download.etag or download.last_modified else 0
            )
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.warning("Discarding unverifiable partial download metadata: %s", exc)
            with open(part_path, "wb"):
                pass
            download.etag = None
            download.last_modified = None
            download.file_size = 0
            return 0

    def _save_resume_metadata(self, download: Download, metadata_path: Path):
        metadata = {
            "url": download.original_url,
            "final_path": download.download_path,
            "etag": download.etag,
            "last_modified": download.last_modified,
            "effective_url": download.effective_url,
            "expected_size": download.file_size,
        }
        self._write_json_atomic(metadata_path, metadata)

    @staticmethod
    def _parse_content_range(value: Optional[str]):
        match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+|\*)", value or "", re.IGNORECASE)
        if not match:
            raise ValueError(f"Invalid Content-Range response: {value!r}")
        start, end = int(match.group(1)), int(match.group(2))
        total = None if match.group(3) == "*" else int(match.group(3))
        if end < start or (total is not None and end >= total):
            raise ValueError(f"Invalid Content-Range bounds: {value!r}")
        return start, end, total

    def _download_http_body(self, download: Download, metadata_path: Path):
        part_path = download.part_path
        if not part_path:
            raise RuntimeError("The partial download path was not initialized")

        parsed_initial = urlparse(download.url)
        try:
            selected_address = ipaddress.ip_address(parsed_initial.hostname or "")
        except ValueError:
            selected_address = None
        allowed_non_public_origins = (
            {_origin(download.url)}
            if selected_address is not None and not _is_public_address(str(selected_address))
            else set()
        )
        with requests.Session() as session:
            # Environment proxies resolve the destination remotely and cannot be
            # constrained by this process, so this downloader uses direct pinned
            # connections for its SSRF protection guarantee.
            session.trust_env = False
            pinned_adapter = PinnedAddressHTTPAdapter(
                allowed_non_public_origins=allowed_non_public_origins
            )
            session.mount("http://", pinned_adapter)
            session.mount("https://", pinned_adapter)
            for attempt in range(self.max_retries + 1):
                if download.cancel_event.is_set():
                    return "cancelled"
                if download.pause_event.is_set():
                    return "paused"

                offset = os.path.getsize(part_path) if os.path.exists(part_path) else 0
                self._assert_safe_temp_path(download, part_path)
                if offset and not (download.etag or download.last_modified):
                    with open(part_path, "wb"):
                        pass
                    offset = 0
                    download.downloaded = 0

                headers = {"Accept-Encoding": "identity"}
                if offset:
                    headers["Range"] = f"bytes={offset}-"
                    headers["If-Range"] = download.etag or download.last_modified

                try:
                    response = self._get_http_response_with_safe_redirects(
                        session, download, headers
                    )
                except requests.exceptions.RequestException:
                    if download.cancel_event.is_set():
                        return "cancelled"
                    if download.pause_event.is_set():
                        return "paused"
                    if attempt >= self.max_retries:
                        raise
                    time.sleep(min(0.25 * (attempt + 1), 1.0))
                    continue

                with response:
                    effective_url = self._validate_url(response.url)
                    if response.status_code in {408, 429, 500, 502, 503, 504}:
                        if attempt < self.max_retries:
                            retry_after = response.headers.get("Retry-After", "")
                            try:
                                delay = min(max(float(retry_after), 0), 5)
                            except ValueError:
                                delay = min(0.25 * (attempt + 1), 1.0)
                            if download.cancel_event.wait(delay):
                                return "cancelled"
                            if download.pause_event.is_set():
                                return "paused"
                            continue
                    if response.status_code == 416:
                        match = re.fullmatch(
                            r"bytes \*/(\d+)",
                            response.headers.get("Content-Range", ""),
                            re.IGNORECASE,
                        )
                        if match and offset == int(match.group(1)):
                            validator_matches = (
                                (
                                    download.etag
                                    and response.headers.get("ETag")
                                    and download.etag == response.headers.get("ETag")
                                )
                                or (
                                    download.last_modified
                                    and response.headers.get("Last-Modified")
                                    and download.last_modified
                                    == response.headers.get("Last-Modified")
                                )
                            )
                            if validator_matches:
                                download.file_size = offset
                                return "completed"
                            if attempt < self.max_retries:
                                with open(part_path, "wb"):
                                    pass
                                download.downloaded = 0
                                download.file_size = 0
                                download.etag = None
                                download.last_modified = None
                                continue
                        raise ValueError("Server rejected the resume range (HTTP 416)")

                    old_etag = download.etag
                    old_modified = download.last_modified
                    response_etag = response.headers.get("ETag")
                    response_modified = response.headers.get("Last-Modified")
                    if offset and response.status_code == 206:
                        start, end, total = self._parse_content_range(
                            response.headers.get("Content-Range")
                        )
                        if start != offset:
                            raise ValueError(
                                f"Server returned byte range starting at {start}, expected {offset}"
                            )
                        changed_validator = (
                            (old_etag and response_etag and old_etag != response_etag)
                            or (old_modified and response_modified
                                and old_modified != response_modified)
                        )
                        if changed_validator or (
                            total is not None and download.file_size
                            and total != download.file_size
                        ):
                            with open(part_path, "wb"):
                                pass
                            download.downloaded = 0
                            download.file_size = 0
                            download.etag = None
                            download.last_modified = None
                            continue
                        expected_segment = end - start + 1
                        content_length = response.headers.get("Content-Length")
                        if content_length and int(content_length) != expected_segment:
                            raise ValueError("Content-Length does not match Content-Range")
                        expected_total = total or 0
                    elif response.status_code == 200:
                        if offset:
                            with open(part_path, "wb"):
                                pass
                            download.downloaded = 0
                            offset = 0
                        content_length = response.headers.get("Content-Length")
                        expected_segment = int(content_length) if content_length else None
                        expected_total = expected_segment or 0
                    else:
                        response.raise_for_status()
                        raise ValueError(f"Unexpected HTTP status: {response.status_code}")

                    download.etag = response_etag
                    download.last_modified = response_modified
                    download.effective_url = effective_url
                    download.file_size = expected_total
                    self._save_resume_metadata(download, metadata_path)
                    self._persist_downloads()

                    mode = "ab" if offset else "wb"
                    bytes_written = 0
                    try:
                        with open(part_path, mode) as output:
                            for chunk in response.iter_content(chunk_size=256 * 1024):
                                if download.cancel_event.is_set():
                                    output.flush()
                                    return "cancelled"
                                if download.pause_event.is_set():
                                    output.flush()
                                    return "paused"
                                if not chunk:
                                    continue
                                if expected_segment is not None and (
                                    bytes_written + len(chunk) > expected_segment
                                ):
                                    raise ValueError(
                                        "Response body exceeded its declared byte range"
                                    )
                                output.write(chunk)
                                bytes_written += len(chunk)
                                self._on_progress(download.download_id, len(chunk))
                    except requests.exceptions.RequestException:
                        if download.cancel_event.is_set():
                            return "cancelled"
                        if download.pause_event.is_set():
                            return "paused"
                        if attempt >= self.max_retries:
                            raise
                        continue

                    if expected_segment is not None and bytes_written != expected_segment:
                        if attempt < self.max_retries:
                            continue
                        raise IOError(
                            f"Incomplete response body: received {bytes_written} "
                            f"of {expected_segment} bytes"
                        )
                    actual_size = os.path.getsize(part_path)
                    if expected_total and actual_size != expected_total:
                        if attempt < self.max_retries:
                            continue
                        raise IOError(
                            f"Incomplete download: received {actual_size} "
                            f"of {expected_total} bytes"
                        )
                    return "completed"

        raise RuntimeError("Download retry loop exited unexpectedly")

    def _finalize_http_download(self, download: Download, metadata_path: Path):
        part_path = download.part_path
        final_path = download.download_path
        if not part_path or not final_path or not os.path.isfile(part_path):
            raise IOError("Downloaded temporary file is missing")
        actual_size = os.path.getsize(part_path)
        if download.file_size and actual_size != download.file_size:
            raise IOError(
                f"Downloaded size {actual_size} does not match expected size {download.file_size}"
            )
        if os.path.exists(final_path):
            raise FileExistsError(f"Destination already exists: {final_path}")
        with open(final_path, "xb"):
            pass
        try:
            os.replace(part_path, final_path)
        except Exception:
            if os.path.isfile(final_path) and os.path.getsize(final_path) == 0:
                os.remove(final_path)
            raise
        if metadata_path.exists():
            try:
                metadata_path.unlink()
            except OSError as exc:
                logger.warning("Could not remove resume metadata %s: %s", metadata_path, exc)
        download.downloaded = actual_size
        download.file_size = actual_size
        download.error_message = None

    def _finish_http_download(self, download: Download, result: str, metadata_path: Path):
        with self.lock:
            if download.cancel_event.is_set() or result == "cancelled":
                self._set_status_locked(download, "Cancelled")
            elif download.pause_event.is_set() or result == "paused":
                self._set_status_locked(download, "Paused")
            else:
                self._finalize_http_download(download, metadata_path)
                download.completed_at = datetime.now(timezone.utc).isoformat()
                self._set_status_locked(download, "Completed")
            status = download.status

        self._persist_downloads()
        self._notify_status_change(download.download_id, status)

    def _run_http_download(self, download_id: str, generation: Optional[int] = None):
        download = self.downloads.get(download_id)
        if not download:
            self._release_active_slot(download_id, generation)
            return
        metadata_path = None
        try:
            final_path, _, metadata_path = self._get_http_paths(download)
            if os.path.exists(final_path):
                raise FileExistsError(f"Destination already exists: {final_path}")
            offset = self._load_resume_metadata(download, metadata_path, final_path)
            download.downloaded = offset
            download.last_persisted_bytes = offset
            if offset:
                for callback in list(self.progress_callbacks):
                    callback(download_id, offset, download.file_size)

            result = self._download_http_body(download, metadata_path)
            self._finish_http_download(download, result, metadata_path)
        except Exception as exc:
            with self.lock:
                download.error_message = redact_sensitive_text(str(exc))
                if download.cancel_event.is_set():
                    self._set_status_locked(download, "Cancelled")
                elif download.pause_event.is_set():
                    self._set_status_locked(download, "Paused")
                elif download.status not in {"Cancelled", "Completed", "Error"}:
                    self._set_status_locked(download, "Error")
                status = download.status
            self._notify_status_change(download_id, status)
        finally:
            self._persist_downloads()
            self._release_active_slot(download_id, generation)
    
    def _calculate_optimal_chunk_size(self, file_size: int, num_threads: int) -> int:
        """Calculate optimal chunk size based on file size and network conditions"""
        if file_size <= 0:
            return 1024 * 1024  # 1MB default
        
        # Base chunk size based on file size
        if file_size > 1024 * 1024 * 1024:  # > 1GB
            base_chunk_size = 16 * 1024 * 1024  # 16MB
        elif file_size > 100 * 1024 * 1024:  # > 100MB
            base_chunk_size = 8 * 1024 * 1024   # 8MB
        elif file_size > 10 * 1024 * 1024:  # > 10MB
            base_chunk_size = 4 * 1024 * 1024   # 4MB
        elif file_size > 1024 * 1024:  # > 1MB
            base_chunk_size = 2 * 1024 * 1024   # 2MB
        else:
            base_chunk_size = 1024 * 1024       # 1MB
        
        # Adjust for number of threads to ensure efficient distribution
        min_chunks_per_thread = 2  # Minimum chunks each thread should handle
        max_reasonable_chunk_size = file_size // (num_threads * min_chunks_per_thread)
        
        # Use the smaller of calculated size and reasonable max to ensure distribution
        optimal_size = min(base_chunk_size, max_reasonable_chunk_size if max_reasonable_chunk_size > 0 else base_chunk_size)
        
        # Ensure chunk size is not smaller than minimum acceptable
        optimal_size = max(optimal_size, 256 * 1024)  # 256KB minimum
        
        return optimal_size
    
    def _ensure_unique_filename(self, directory: str, filename: str) -> str:
        """Ensure filename is unique within directory and current downloads"""
        with self.lock:
            if not directory:
                directory = os.getcwd()
            directory = os.path.abspath(directory)
            base, ext = os.path.splitext(filename)
            if not base:
                base = "download"
            counter = 1
            candidate = f"{base}{ext}"
            reserved_names = {
                d.current_filename for d in self.downloads.values()
                if getattr(d, "destination_dir", None) == directory and d.current_filename
            }

            while True:
                candidate_path = os.path.join(directory, candidate)
                if candidate not in reserved_names and not os.path.exists(candidate_path):
                    return candidate
                candidate = f"{base} ({counter}){ext}"
                counter += 1
    
    def _start_download(self, download_id: str, generation: Optional[int] = None):
        """Run one download after its queue slot has been reserved."""
        download = self.downloads.get(download_id)
        if not download:
            self._release_active_slot(download_id, generation)
            return
        generation = download.execution_generation if generation is None else generation
        if not self._is_current_generation(download_id, generation):
            self._release_active_slot(download_id, generation)
            return
        if download.cancel_event.is_set():
            if download.status != "Cancelled":
                self._transition_download(download_id, "Cancelled")
            self._release_active_slot(download_id, generation)
            return

        if download.is_video:
            self._download_video_with_ytdlp(download_id, generation)
            return
        self._run_http_download(download_id, generation)

    def _is_current_generation(self, download_id: str, generation: Optional[int]) -> bool:
        with self.lock:
            download = self.downloads.get(download_id)
            return bool(
                download
                and (generation is None or download.execution_generation == generation)
                and download.status not in {
                    "Cancelled", "Completed", "Error", "Paused", "Interrupted"
                }
            )
    
    def _download_video_with_ytdlp(self, download_id: str, generation: Optional[int] = None):
        """Download video using yt-dlp with error recovery strategies"""
        download = self.downloads.get(download_id)
        if not download:
            return
        generation = download.execution_generation if generation is None else generation
        if not self._is_current_generation(download_id, generation):
            return
        download.final_output_path = None
        download.active_postprocessor = None
        
        # Track retry attempts for this specific download
        if not hasattr(download, 'yt_dlp_retries'):
            download.yt_dlp_retries = 0
            download.fallback_attempted = False
        
        try:
            destination_dir = getattr(download, "destination_dir", download.download_path)
            with self.lock:
                if download.current_filename:
                    unique_filename = download.current_filename
                else:
                    unique_filename = self._ensure_unique_filename(
                        destination_dir, download.get_filename()
                    )
                    download.current_filename = unique_filename
                if not self._is_safe_download_filename(unique_filename):
                    raise ValueError("The video output filename is not a safe file name")
                full_path = os.path.join(destination_dir, unique_filename)
                destination_resolved = os.path.normcase(os.path.realpath(destination_dir))
                output_resolved = os.path.normcase(os.path.realpath(full_path))
                if os.path.commonpath((destination_resolved, output_resolved)) != destination_resolved:
                    raise ValueError("The video output path resolves outside the selected folder")
                download.download_path = full_path
            
            dir_path = destination_dir or os.path.dirname(full_path)
            if dir_path:
                os.makedirs(dir_path, exist_ok=True)
            
            download.start_time = time.time()
            download.last_update_time = time.time()
            download.avg_speed = 0
            
            ffmpeg_tools = FormatSelector.discover_ffmpeg(
                configured_path=config_manager.get("ffmpeg_location", ""),
            )
            deno_runtime = discover_deno(
                configured_path=config_manager.get("deno_location", ""),
            )
            ffmpeg_available = ffmpeg_tools is not None
            if FormatSelector.requires_ffmpeg(
                download.download_type, download.audio_quality
            ) and not ffmpeg_available:
                raise RuntimeError(
                    "MP3 conversion requires FFmpeg. Install FFmpeg or choose "
                    "Best audio to keep the original audio format."
                )

            # Configure yt-dlp options
            def progress_hook(d):
                self._ytdlp_progress_hook(download_id, d, generation=generation)

            def postprocessor_hook(d):
                if (not self._is_current_generation(download_id, generation)
                        or download.cancel_requested or download.pause_requested):
                    raise DownloadCancelled()
                hook_info = d.get("info_dict") or {}
                output_path = hook_info.get("filepath") or hook_info.get("_filename")
                if output_path:
                    download.final_output_path = output_path
                if d.get("status") != "started":
                    return
                processor = str(d.get("postprocessor") or "").lower()
                if processor:
                    download.active_postprocessor = processor
                if "merge" in processor:
                    stage = "Merging"
                elif "extractaudio" in processor or "audio" in processor:
                    stage = "Converting"
                else:
                    stage = "Processing"
                self._set_video_stage(download_id, stage, generation=generation)
            
            # Get format string based on user selections
            format_string = FormatSelector.get_format_string(
                download.download_type,
                download.video_quality,
                download.audio_quality,
                ffmpeg_available=ffmpeg_available,
            )
            
            # Let yt-dlp retain the selected container unless streams must be merged.
            base_path, _ = os.path.splitext(full_path)
            output_template = base_path + '.%(ext)s'
            
            ydl_opts = {
                'format': format_string,
                'outtmpl': output_template,
                'quiet': True,
                'no_warnings': False,
                'logger': logger,
                'progress_hooks': [progress_hook],
                'postprocessor_hooks': [postprocessor_hook],
                'noplaylist': True,  # Don't download playlists
                'continuedl': True,
                'nopart': False,
                'prefer_free_formats': False,
                'concurrent_fragment_downloads': min(8, download.num_threads),  # Reduced from 16 to prevent rate limiting
                # Keep blocked network calls bounded so pause/cancel can reach
                # yt-dlp's next callback promptly even on an unresponsive host.
                'fragment_retries': 3,
                'retries': 3,
                'filetimeout': 15,
                'socket_timeout': 15,
                'http_chunk_size': 10485760,  # 10MB chunks
                # Add headers to avoid 403 errors
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.5',
                    'Accept-Encoding': 'gzip, deflate',
                    'Connection': 'keep-alive',
                    'Upgrade-Insecure-Requests': '1',
                },
            }
            
            if "+" in format_string:
                ydl_opts["merge_output_format"] = (
                    "mp4" if download.video_quality == "Best MP4 Compatible" else "mkv"
                )
            if ffmpeg_tools:
                ydl_opts["ffmpeg_location"] = ffmpeg_tools.ffmpeg
            if deno_runtime:
                ydl_opts.update(deno_runtime.ytdlp_options)
            optional_media_options = self._optional_ytdlp_options(download)
            if not ffmpeg_tools:
                if optional_media_options.pop("embedsubtitles", None):
                    download.optional_media_warnings.append(
                        "Subtitle embedding skipped because FFmpeg is unavailable; selected subtitle files may still be saved."
                    )
                if optional_media_options.pop("embedthumbnail", None):
                    download.optional_media_warnings.append(
                        "Thumbnail embedding skipped because FFmpeg is unavailable; the thumbnail file may still be saved."
                    )
            ydl_opts.update(optional_media_options)
            if self.allow_browser_session:
                ydl_opts.update(youtube_session_options(download.original_url))
            if FormatSelector.requires_ffmpeg(
                download.download_type, download.audio_quality
            ):
                ydl_opts["postprocessors"] = [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "0",
                }]

            self._set_video_stage(download_id, "Fetching info", generation=generation)
            
            # Download in background thread
            def download_thread():
                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        download.ydl_process = ydl
                        info = ydl.extract_info(
                            download.original_url,
                            download=True,
                        )
                        self._record_selected_formats(download_id, info, generation=generation)
                    if not self._is_current_generation(download_id, generation):
                        return
                    if download.cancel_requested:
                        raise DownloadCancelled()
                    
                    # Resolve only output paths associated with this yt-dlp result/template.
                    candidates = []
                    if isinstance(info, dict):
                        candidates.extend((info.get("filepath"), info.get("_filename")))
                    candidates.append(download.final_output_path)
                    template_base = output_template.removesuffix(".%(ext)s")
                    candidates.append(template_base)
                    if os.path.isdir(destination_dir):
                        stem = os.path.basename(template_base)
                        candidates.extend(
                            os.path.join(destination_dir, name)
                            for name in os.listdir(destination_dir)
                            if os.path.splitext(name)[0] == stem
                        )
                    actual_file = None
                    destination = os.path.abspath(destination_dir)
                    expected_extension = None
                    if FormatSelector.requires_ffmpeg(
                        download.download_type, download.audio_quality
                    ):
                        expected_extension = ".mp3"
                    elif "+" in format_string:
                        expected_extension = (
                            ".mp4" if download.video_quality == "Best MP4 Compatible"
                            else ".mkv"
                        )
                    for candidate in candidates:
                        if not candidate:
                            continue
                        candidate_path = os.path.realpath(os.path.abspath(os.fspath(candidate)))
                        try:
                            destination_resolved = os.path.normcase(os.path.realpath(destination))
                            candidate_resolved = os.path.normcase(candidate_path)
                            if os.path.commonpath((destination_resolved, candidate_resolved)) != destination_resolved:
                                continue
                            if candidate_path.endswith((".part", ".ytdl")):
                                continue
                            if (expected_extension and os.path.splitext(candidate_path)[1].lower()
                                    != expected_extension):
                                continue
                            if os.path.isfile(candidate_path) and os.path.getsize(candidate_path) > 0:
                                actual_file = candidate_path
                                break
                        except (OSError, TypeError, ValueError):
                            continue
                    if actual_file:
                        self._complete_ytdlp_output(
                            download_id, generation, actual_file, ffmpeg_tools=ffmpeg_tools
                        )
                        if download.status in {"Cancelling", "Pausing"}:
                            raise DownloadCancelled()
                    else:
                        with self.lock:
                            if download.execution_generation == generation and download.status not in {
                                "Cancelled", "Cancelling", "Pausing", "Paused"
                            }:
                                download.error_category = "UNKNOWN_ERROR"
                                download.error_message = (
                                    "yt-dlp returned successfully but no non-empty final output was found"
                                )
                                self._set_status_locked(download, "Error")
                        self._persist_downloads()
                        if download.execution_generation == generation:
                            self._notify_status_change(download_id, download.status)
                except DownloadCancelled:
                    with self.lock:
                        if download.execution_generation != generation:
                            return
                        if download.cancel_requested or download.status == "Cancelling":
                            self._set_status_locked(download, "Cancelled")
                            download.cancel_requested = False
                        elif download.pause_requested or download.status == "Pausing":
                            self._set_status_locked(download, "Paused")
                        download.pause_requested = False
                        terminal_status = download.status
                    download.speed = 0
                    self._persist_downloads()
                    self._notify_status_change(download_id, terminal_status)
                except Exception as e:
                    if download.execution_generation != generation:
                        return
                    if download.cancel_requested or download.status == "Cancelling":
                        with self.lock:
                            self._set_status_locked(download, "Cancelled")
                        result_status = "Cancelled"
                    elif download.pause_requested or download.status == "Pausing":
                        with self.lock:
                            self._set_status_locked(download, "Paused")
                        result_status = "Paused"
                    else:
                        if self._recover_optional_postprocessor_failure(
                            download, e, generation, ffmpeg_tools
                        ):
                            return
                        error_kind = self._classify_ytdlp_error(e)
                        with self.lock:
                            download.error_category = error_kind
                            download.error_message = redact_sensitive_text(
                                f"{error_kind}: {str(e)[:300]}"
                            )
                            self._set_status_locked(download, "Error")
                            if (error_kind in {"NETWORK_ERROR", "TIMEOUT", "TEMPORARY_HTTP_ERROR"}
                                    and download.retry_count < self.max_retries):
                                download.retry_count += 1
                                download.resume_pending = True
                        result_status = "Error"
                    download.speed = 0
                    self._persist_downloads()
                    self._notify_status_change(download_id, result_status)
                finally:
                    if download.execution_generation == generation:
                        download.ydl_process = None
                    self._release_active_slot(download_id, generation)
            
            video_worker = threading.Thread(
                target=download_thread,
                name=f"video-download-{download_id}",
                daemon=True,
            )
            with self.lock:
                download.worker = video_worker
            video_worker.start()
            # The queue worker and yt-dlp worker are both background workers;
            # generation checks prevent either one's late hooks from winning.
            
        except Exception as e:
            if download.execution_generation == generation:
                with self.lock:
                    if download.cancel_requested or download.status == "Cancelling":
                        self._set_status_locked(download, "Cancelled")
                    elif download.pause_requested or download.status == "Pausing":
                        self._set_status_locked(download, "Paused")
                    else:
                        download.error_category = self._classify_ytdlp_error(e)
                        download.error_message = redact_sensitive_text(
                            f"{download.error_category}: {str(e)[:300]}"
                        )
                    if download.status not in {"Error", "Cancelled", "Completed", "Paused"}:
                        self._set_status_locked(download, "Error")
                    result_status = download.status
                self._persist_downloads()
                self._notify_status_change(download_id, result_status)
            self._release_active_slot(download_id, generation)
    
    def _ytdlp_progress_hook(self, download_id: str, d: dict,
                             generation: Optional[int] = None):
        """Progress hook for yt-dlp"""
        download = self.downloads.get(download_id)
        if not download:
            return

        with self.lock:
            if (generation is not None and download.execution_generation != generation):
                return
            if download.status in {"Cancelled", "Completed", "Error", "Paused", "Interrupted"}:
                return
        
        if getattr(download, "cancel_requested", False):
            raise DownloadCancelled()
        if getattr(download, "pause_requested", False):
            raise DownloadCancelled()
        
        if d.get('status') == 'downloading':
            self._set_video_stage(download_id, "Downloading", generation=generation)
            if 'downloaded_bytes' in d:
                downloaded = d['downloaded_bytes']
                total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
                
                # Calculate speed from yt-dlp progress
                current_time = time.time()
                if not hasattr(download, 'last_ydlp_time') or not hasattr(download, 'last_ydlp_bytes'):
                    download.last_ydlp_time = current_time
                    download.last_ydlp_bytes = downloaded
                    download.speed = 0
                else:
                    time_diff = current_time - download.last_ydlp_time
                    if time_diff > 0:
                        bytes_diff = downloaded - download.last_ydlp_bytes
                        download.speed = bytes_diff / time_diff
                        download.last_ydlp_time = current_time
                        download.last_ydlp_bytes = downloaded
                
                with self.lock:
                    if generation is not None and download.execution_generation != generation:
                        return
                    download.downloaded = downloaded
                    if total > 0:
                        download.file_size = total
                    filename = d.get("filename")
                    if filename:
                        download.current_filename = os.path.basename(filename)
                    download.fragment_index = d.get("fragment_index")
                    download.fragment_count = d.get("fragment_count")
                    download.last_update_time = current_time
                    download.last_downloaded = downloaded
                    if download.start_time:
                        elapsed = max(current_time - download.start_time, 1e-6)
                        download.avg_speed = download.downloaded / elapsed
                    else:
                        download.avg_speed = download.speed
                    download.eta = (
                        max(0, total - downloaded) / download.speed
                        if total > downloaded and download.speed > 0 else 0
                    )
                
                for callback in self.progress_callbacks:
                    try:
                        callback(download_id, downloaded, total)
                    except Exception as exc:
                        logger.exception("Progress callback failed for %s", download_id)
        elif d['status'] == 'finished':
            if 'total_bytes' in d:
                with self.lock:
                    if generation is None or download.execution_generation == generation:
                        download.file_size = d['total_bytes']

    @staticmethod
    def _classify_ytdlp_error(error: Exception) -> str:
        message = str(error).lower()
        errno = getattr(error, "errno", None)
        winerror = getattr(error, "winerror", None)
        if (isinstance(error, PermissionError) or errno in {13, 5} or winerror == 5
                or "permission denied" in message or "access denied" in message):
            return "PERMISSION_ERROR"
        if errno == 28 or winerror == 112 or "no space left" in message or "disk full" in message:
            return "DISK_FULL"
        if ("ffmpeg" in message or "ffprobe" in message) and any(
            token in message for token in (
                "not found", "not installed", "could not find", "no such file",
                "not configured", "cannot find", "not recognized",
            )
        ):
            return "FFMPEG_MISSING"
        if "ffmpeg" in message or "ffprobe" in message:
            return "FFMPEG_ERROR"
        if "javascript" in message or "js runtime" in message or "deno" in message:
            return "JS_RUNTIME_ERROR"
        if "private video" in message or "video is private" in message:
            return "PRIVATE_VIDEO"
        if "age-restricted" in message or "age restricted" in message:
            return "AGE_RESTRICTED"
        if "sign in" in message or "login required" in message or "authentication" in message:
            return "LOGIN_REQUIRED"
        if "requested format" in message or "format is not available" in message:
            return "FORMAT_UNAVAILABLE"
        if "video unavailable" in message or "has been removed" in message:
            return "VIDEO_UNAVAILABLE"
        if "http error 429" in message or any(
            f"http error {code}" in message for code in (408, 500, 502, 503, 504)
        ):
            return "TEMPORARY_HTTP_ERROR"
        if isinstance(error, TimeoutError) or "timed out" in message or "timeout" in message:
            return "TIMEOUT"
        if any(token in message for token in ("connection reset", "network", "fragment", "socket")):
            return "NETWORK_ERROR"
        if "postprocessor" in message or "post-processing" in message or "postprocessing" in message:
            return "POSTPROCESSING_ERROR"
        if "cancel" in message:
            return "CANCELLED"
        return "UNKNOWN_ERROR"

    def _recover_optional_postprocessor_failure(
        self, download: Download, error: Exception, generation: int, ffmpeg_tools
    ) -> bool:
        """Keep a valid media file if only an opted-in optional embed step failed."""
        processor = (download.active_postprocessor or "").lower()
        optional_processor = any(
            token in processor for token in ("embedsubtitle", "embedthumbnail", "metadata")
        )
        if not optional_processor:
            return False
        output = download.final_output_path
        if not output or not os.path.isfile(output):
            return False
        warning = redact_sensitive_text(
            f"Optional {download.active_postprocessor} step failed: {str(error)[:200]}"
        )
        if warning not in download.optional_media_warnings:
            download.optional_media_warnings.append(warning)
        self._complete_ytdlp_output(
            download.download_id, generation, output, ffmpeg_tools=ffmpeg_tools
        )
        return download.status == "Completed"

    @staticmethod
    def _format_details(format_info):
        if not format_info:
            return None
        return {
            key: format_info.get(key)
            for key in (
                "format_id", "width", "height", "fps", "vcodec", "acodec",
                "tbr", "vbr", "abr", "ext", "dynamic_range", "filesize",
                "filesize_approx",
            )
            if format_info.get(key) is not None
        }

    def _record_selected_formats(self, download_id: str, info,
                                 generation: Optional[int] = None):
        if not isinstance(info, dict):
            return
        with self.lock:
            download = self.downloads.get(download_id)
            if (not download or (generation is not None
                                 and download.execution_generation != generation)
                    or download.status in {"Cancelled", "Cancelling", "Completed", "Error"}):
                return
            if info.get("title"):
                download.video_title = info["title"]
            if info.get("extractor"):
                download.extractor = info["extractor"]
            if info.get("id") and not download.video_id:
                download.video_id = str(info["id"])
        selected = info.get("requested_formats") or [info]
        video_format = next(
            (item for item in selected if item.get("vcodec") not in (None, "none")),
            None,
        )
        audio_format = next(
            (item for item in selected if item.get("acodec") not in (None, "none")),
            None,
        )
        video_details = self._format_details(video_format)
        audio_details = self._format_details(audio_format)
        if not video_details and not audio_details:
            return
        with self.lock:
            download = self.downloads.get(download_id)
            if (not download or (generation is not None
                                 and download.execution_generation != generation)
                    or download.status in {"Cancelled", "Cancelling", "Completed", "Error"}):
                return
        if not download:
            return

        parts = []
        if video_details:
            height = video_details.get("height")
            if height:
                parts.append(f"{height}p")
            fps = video_details.get("fps")
            if fps:
                parts.append(f"{fps:g} FPS" if isinstance(fps, (int, float)) else f"{fps} FPS")
            codec = video_details.get("vcodec")
            if codec:
                codec_lower = str(codec).lower()
                if codec_lower.startswith(("av01", "av1")):
                    codec = "AV1"
                elif codec_lower.startswith(("vp9", "vp09")):
                    codec = "VP9"
                elif codec_lower.startswith(("avc1", "h264")):
                    codec = "H.264"
                elif codec_lower.startswith(("hev1", "hvc1", "h265")):
                    codec = "H.265"
                parts.append(str(codec))
        if audio_details:
            codec = audio_details.get("acodec")
            if codec:
                codec_lower = str(codec).lower()
                if codec_lower.startswith("opus"):
                    codec = "Opus"
                elif codec_lower.startswith(("mp4a", "aac")):
                    codec = "AAC"
                elif codec_lower.startswith(("vorbis",)):
                    codec = "Vorbis"
                if FormatSelector.requires_ffmpeg(
                    download.download_type, download.audio_quality
                ):
                    parts.extend(("MP3 VBR 0", f"source {codec}"))
                else:
                    parts.append(str(codec))
                    bitrate = audio_details.get("abr")
                    if bitrate:
                        parts.append(f"{bitrate:g} kbps" if isinstance(
                            bitrate, (int, float)
                        ) else f"{bitrate} kbps")

        with self.lock:
            download = self.downloads.get(download_id)
            if (not download or (generation is not None
                                 and download.execution_generation != generation)
                    or download.status in {"Cancelled", "Cancelling", "Completed", "Error"}):
                return
            download.selected_video_format = video_details
            download.selected_audio_format = audio_details
            download.selected_format_summary = " • ".join(parts) or None
        if download.selected_format_summary:
            logger.info(
                "yt-dlp selected formats for %s: video=%s audio=%s",
                download_id, video_details, audio_details,
            )
    
    def _check_stuck_downloads(self):
        """Check for stuck downloads and handle them"""
        current_time = time.time()
        for download_id, download in list(self.downloads.items()):
            # Check for stuck analysis (more than 60 seconds)
            if download.status == "Analyzing":
                if download.start_time and (current_time - download.start_time) > 60:
                    # Force move to queued
                    download.status = "Queued"
                    download.error_message = None
                    self._notify_status_change(download_id, "Queued")
                    self.download_queue.put(download_id)
                    self._process_queue()
    
    def _monitor_download(self, download_id: str):
        """Monitor download progress and update speed/ETA"""
        download = self.downloads.get(download_id)
        if not download:
            return
        
        # Initialize speed tracking if not exists
        if not hasattr(download, 'last_update_time') or download.last_update_time is None:
            download.last_update_time = time.time()
            download.last_downloaded = download.downloaded
        
        while download.status == "Downloading":
            time.sleep(0.5)  # Check more frequently for better speed updates
            
            if download.status != "Downloading":
                break
            
            # Calculate speed
            current_time = time.time()
            time_diff = current_time - download.last_update_time
            
            if time_diff > 0:
                downloaded_diff = download.downloaded - download.last_downloaded
                download.speed = downloaded_diff / time_diff
                download.last_update_time = current_time
                download.last_downloaded = download.downloaded
                if download.start_time:
                    elapsed = max(current_time - download.start_time, 1e-6)
                    download.avg_speed = download.downloaded / elapsed
                else:
                    download.avg_speed = download.speed
                
                # Calculate ETA
                if download.file_size > 0:
                    remaining = download.file_size - download.downloaded
                    if download.speed > 0:
                        download.eta = remaining / download.speed
                    else:
                        download.eta = 0
                else:
                    # Unknown file size, can't calculate ETA
                    download.eta = 0
            else:
                # If no time has passed, keep previous speed
                if download.speed == 0:
                    download.speed = 0
            
            # Check if all threads are done (for regular downloads)
            if download.threads:
                all_done = all(not thread.is_alive() for thread in download.threads)
                
                if all_done:
                    if download.downloaded >= download.file_size or download.file_size == 0:
                        download.status = "Completed"
                        if download.start_time:
                            elapsed = max(time.time() - download.start_time, 1e-6)
                            download.avg_speed = download.downloaded / elapsed
                        download.speed = 0  # Reset momentary speed when done
                        self._notify_status_change(download_id, "Completed")
                    else:
                        download.status = "Error"
                        download.error_message = "Download incomplete"
                        download.speed = 0
                        self._notify_status_change(download_id, "Error")
                    self.active_downloads -= 1
                    self._process_queue()
                    break
            # For yt-dlp downloads, status is managed by the progress hook
    
    def _on_progress(self, download_id: str, bytes_downloaded: int):
        """Callback when download progress is made"""
        download = self.downloads.get(download_id)
        if not download or bytes_downloaded <= 0:
            return
        now = time.time()
        with self.lock:
            if not download.start_time:
                download.start_time = now
            download.downloaded += bytes_downloaded
            elapsed = now - (download.last_update_time or now)
            if elapsed > 0:
                download.speed = (download.downloaded - download.last_downloaded) / elapsed
                download.last_update_time = now
                download.last_downloaded = download.downloaded
            if download.start_time and now > download.start_time:
                download.avg_speed = download.downloaded / (now - download.start_time)
            if download.file_size and download.speed > 0:
                download.eta = max(0, download.file_size - download.downloaded) / download.speed
            callbacks = list(self.progress_callbacks)
            should_persist = (
                download.downloaded - download.last_persisted_bytes >= 1024 * 1024
            )
            if should_persist:
                download.last_persisted_bytes = download.downloaded
        if should_persist:
            self._persist_downloads()
        for callback in callbacks:
            try:
                callback(download_id, download.downloaded, download.file_size)
            except Exception as exc:
                logger.exception("Progress callback failed for %s", download_id)
    
    def pause_download(self, download_id: str):
        """Pause a download"""
        with self.lock:
            download = self.downloads.get(download_id)
            if not download:
                return
            if download.status == "Queued":
                self._set_status_locked(download, "Paused")
                new_status = "Paused"
            elif download.status in {
                "Downloading", "Fetching info", "Processing", "Merging", "Converting"
            }:
                download.pause_requested = True
                download.pause_event.set()
                self._set_status_locked(download, "Pausing")
                new_status = "Pausing"
            else:
                return
        self._persist_downloads()
        self._notify_status_change(download_id, new_status)
    
    def resume_download(self, download_id: str):
        """Restart a stopped download from its retained partial files."""
        with self.lock:
            download = self.downloads.get(download_id)
            if not download or download.status not in {"Paused", "Error", "Interrupted"}:
                return
            download.error_message = None
            download.error_category = None
            download.retry_count = 0
            if download.slot_active:
                download.resume_pending = True
                queue_now = False
            else:
                download.pause_event.clear()
                download.cancel_event.clear()
                download.pause_requested = False
                download.cancel_requested = False
                self._set_status_locked(download, "Queued")
                self.download_queue.put(download_id, download.priority)
                queue_now = True
        if not queue_now:
            self._persist_downloads()
            return
        self._persist_downloads()
        self._notify_status_change(download_id, "Queued")
        self._process_queue()

    def _complete_ytdlp_output(self, download_id: str, generation: int, path: str,
                               ffmpeg_tools=None):
        path = os.path.abspath(path)
        try:
            download = self.downloads.get(download_id)
            if not download:
                return
            destination = os.path.normcase(os.path.realpath(download.destination_dir))
            resolved_output = os.path.normcase(os.path.realpath(path))
            if os.path.commonpath((destination, resolved_output)) != destination:
                raise ValueError("Final media output resolves outside the selected folder")
            if path.endswith((".part", ".ytdl")) or not os.path.isfile(path):
                raise FileNotFoundError("final media output is missing or incomplete")
            file_size = os.path.getsize(path)
            if file_size <= 0:
                raise FileNotFoundError("final media output is empty")
        except (OSError, ValueError) as exc:
            with self.lock:
                download = self.downloads.get(download_id)
                if not download or download.execution_generation != generation:
                    return
                download.error_category = self._classify_ytdlp_error(exc)
                download.error_message = redact_sensitive_text(str(exc))
                if download.status not in {"Cancelled", "Cancelling"}:
                    self._set_status_locked(download, "Error")
                status = download.status
            self._persist_downloads()
            self._notify_status_change(download_id, status)
            return

        with self.lock:
            download = self.downloads.get(download_id)
            if (not download or download.execution_generation != generation
                    or download.status in {
                        "Cancelled", "Cancelling", "Pausing", "Paused", "Completed", "Error", "Interrupted"
                    }
                    or download.cancel_requested or download.pause_requested):
                return
            expect_video = download.download_type != "Audio Only"
        validation = validate_media_output(
            path,
            ffmpeg_tools,
            expect_video=expect_video,
            expect_audio=True,
        )
        if validation.status == "invalid":
            with self.lock:
                download = self.downloads.get(download_id)
                if (not download or download.execution_generation != generation
                        or download.status in {"Cancelled", "Cancelling", "Pausing", "Paused"}
                        or download.cancel_requested or download.pause_requested):
                    return
                download.download_path = path
                download.final_output_path = path
                download.current_filename = os.path.basename(path)
                download.media_validation_status = "invalid"
                download.error_category = "OUTPUT_VALIDATION_ERROR"
                download.error_message = redact_sensitive_text(validation.message)
                self._set_status_locked(download, "Error")
            self._persist_downloads()
            self._notify_status_change(download_id, "Error")
            return

        with self.lock:
            download = self.downloads.get(download_id)
            if (not download or download.execution_generation != generation
                    or download.status in {
                        "Cancelled", "Cancelling", "Pausing", "Paused", "Completed", "Error", "Interrupted"
                    }
                    or download.cancel_requested or download.pause_requested):
                return
            download.downloaded = file_size
            download.file_size = file_size
            download.download_path = path
            download.final_output_path = path
            download.current_filename = os.path.basename(path)
            download.media_validation_status = validation.status
            download.completed_at = datetime.now(timezone.utc).isoformat()
            if download.start_time:
                elapsed = max(time.time() - download.start_time, 1e-6)
                download.avg_speed = file_size / elapsed
            self._set_status_locked(download, "Completed")
        self._persist_downloads()
        self._notify_status_change(download_id, "Completed")
    
    def cancel_download(self, download_id: str):
        """Cancel a download"""
        with self.lock:
            download = self.downloads.get(download_id)
            if not download or download.status in {"Completed", "Cancelled", "Cancelling"}:
                return
            running = download.slot_active
            download.cancel_requested = True
            download.cancel_event.set()
            download.pause_requested = False
            download.pause_event.clear()
            if running:
                self._set_status_locked(download, "Cancelling")
                new_status = "Cancelling"
            else:
                if download.status in {
                    "Downloading", "Fetching info", "Processing", "Merging", "Converting", "Pausing"
                }:
                    self._set_status_locked(download, "Cancelling")
                self._set_status_locked(download, "Cancelled")
                new_status = "Cancelled"

        if running:
            self._persist_downloads()
            self._notify_status_change(download_id, new_status)
            return

        self._persist_downloads()
        self._notify_status_change(download_id, new_status)
        self._persist_downloads()
        self._process_queue()
    
    def get_download_info(self, download_id: str) -> Optional[dict]:
        """Get information about a download"""
        with self.lock:
            download = self.downloads.get(download_id)
            if not download:
                return None
            progress = (
                download.downloaded / download.file_size * 100
                if download.file_size > 0 else 0
            )
            playlist_items = [
                item for item in self.downloads.values()
                if download.playlist_group_id
                and item.playlist_group_id == download.playlist_group_id
            ]
            playlist_progress = (
                sum(
                    min(100, item.downloaded / item.file_size * 100)
                    if item.file_size > 0 else 0
                    for item in playlist_items
                ) / len(playlist_items)
                if playlist_items else None
            )
            filename = download.current_filename or download.get_filename()
            return {
                'id': download_id,
                'url': download.url,
                'source_url': download.original_url,
                'title': download.video_title or filename,
                'extractor': download.extractor,
                'destination': download.destination_dir,
                'profile': (
                    download.audio_quality if download.download_type == "Audio Only"
                    else download.video_quality
                ),
                'download_type': download.download_type,
                'final_file': download.download_path if download.status == "Completed" else None,
                'completed_at': download.completed_at,
                'video_id': download.video_id,
                'playlist_entry_id': download.playlist_entry_id,
                'filename': filename,
                'status': download.status,
                'stage': download.video_stage,
                'is_video': download.is_video,
                'fragment_index': download.fragment_index,
                'fragment_count': download.fragment_count,
                'selected_video_format': download.selected_video_format,
                'selected_audio_format': download.selected_audio_format,
                'quality': download.selected_format_summary,
                'media_validation_status': download.media_validation_status,
                'optional_media_warnings': list(download.optional_media_warnings),
                'playlist_title': download.playlist_title,
                'playlist_index': download.playlist_index,
                'playlist_count': download.playlist_count,
                'playlist_progress': playlist_progress,
                'playlist_progress_estimated': bool(download.playlist_group_id),
                'progress': progress,
                'downloaded': download.downloaded,
                'file_size': download.file_size,
                'speed': download.speed,
                'eta': download.eta,
                'error': download.error_message,
                'error_category': download.error_category,
            }
    
    def get_all_downloads(self) -> list:
        """Get information about all downloads"""
        with self.lock:
            download_ids = list(self.downloads)
            history = list(self.history)
        active = [self.get_download_info(did) for did in download_ids]
        active_ids = {entry['id'] for entry in active if entry}
        history_entries = [entry for entry in history if entry['id'] not in active_ids]
        return [entry for entry in active if entry] + history_entries
    
    def stop_accepting_work(self):
        """Service shutdown hook: freeze queue and request resumable pauses."""
        with self.lock:
            self._shutting_down = True
            self._shutdown_workers = []
            ids = list(self.downloads)
            for job in self.downloads.values():
                job.resume_pending = False
                if job.worker is not None:
                    self._shutdown_workers.append(job.worker)
        for download_id in ids:
            self.pause_download(download_id)
        self._persist_downloads()

    def workers_stopped(self):
        with self.lock:
            for job in self.downloads.values():
                if job.worker is not None and job.worker not in self._shutdown_workers:
                    self._shutdown_workers.append(job.worker)
            return self.active_downloads == 0 and not any(w.is_alive() for w in self._shutdown_workers)

    def finish_shutdown(self):
        self._persist_downloads()
        self.session_manager.close_all_sessions()
        with self.lock:
            self._shutdown_workers.clear()
            for job in self.downloads.values():
                if job.worker is not None and not job.worker.is_alive():
                    job.worker = None

    def unregister_progress_callback(self, callback):
        with self.lock:
            if callback in self.progress_callbacks:
                self.progress_callbacks.remove(callback)

    def unregister_status_callback(self, callback):
        with self.lock:
            if callback in self.status_callbacks:
                self.status_callbacks.remove(callback)

    def get_event_snapshot(self, download_id):
        """Copy one job's event fields without scanning history or playlists."""
        with self.lock:
            job = self.downloads.get(download_id)
            if job is None:
                return {}
            return {
                'status': job.status, 'stage': job.video_stage,
                'filename': job.current_filename, 'downloaded_bytes': job.downloaded,
                'total_bytes': job.file_size, 'speed': job.speed, 'eta': job.eta,
                'progress': min(100, job.downloaded / job.file_size * 100) if job.file_size else 0,
                'error_category': job.error_category, 'error_message': job.error_message,
                'playlist_title': job.playlist_title, 'playlist_index': job.playlist_index,
                'playlist_count': job.playlist_count,
            }

    def register_progress_callback(self, callback: Callable):
        """Register a callback for download progress updates"""
        with self.lock:
            self.progress_callbacks.append(callback)
    
    def register_status_callback(self, callback: Callable):
        """Register a callback for download status changes"""
        with self.lock:
            self.status_callbacks.append(callback)
    
    def set_bandwidth_limit(self, kbps: Optional[int]):
        """Set global bandwidth limit in KB/s (None for unlimited)"""
        self.bandwidth_limiter.set_max_bandwidth(kbps)
    
    def get_bandwidth_limit(self) -> Optional[int]:
        """Get current bandwidth limit in KB/s (None if unlimited)"""
        bandwidth = self.bandwidth_limiter.get_max_bandwidth()
        return bandwidth // 1024 if bandwidth else None
    
    def set_download_priority(self, download_id: str, priority: int):
        """Change the priority of an existing download"""
        with self.lock:
            download = self.downloads.get(download_id)
            if not download or download.status != "Queued":
                return False
            if not self.download_queue.remove(download_id):
                return False
            download.priority = priority
            # Requeue with new priority
            self.download_queue.put(download_id, priority)
        self._persist_downloads()
        return True
    
    def get_download_priority(self, download_id: str) -> Optional[int]:
        """Get the priority of a download"""
        with self.lock:
            download = self.downloads.get(download_id)
            return download.priority if download else None

    def get_queue_info(self) -> dict:
        """Get information about the current queue state"""
        with self.lock:
            return {
                'total_queued': self.download_queue.qsize(),
                'high_priority': self.download_queue.high_priority.qsize(),
                'medium_priority': self.download_queue.medium_priority.qsize(),
                'low_priority': self.download_queue.low_priority.qsize(),
                'active_downloads': self.active_downloads,
                'max_concurrent': self.max_concurrent
            }
    
    def _apply_403_recovery(self, download_id: str, error_msg: str) -> bool:
        """Apply recovery strategies specifically for 403 Forbidden errors"""
        download = self.downloads.get(download_id)
        if not download:
            return False
        
        try:
            logger.info("Applying 403 recovery strategies")
            
            # Strategy 1: Reduce concurrent connections dramatically
            download.num_threads = max(1, min(2, download.num_threads // 8))
            logger.debug("Reduced connections to %s", download.num_threads)
            
            # Strategy 2: Add significant delays between requests
            download.fragment_delay = getattr(download, 'fragment_delay', 0) + 3
            logger.debug("Added fragment delay: %ss", download.fragment_delay)
            
            # Strategy 3: Rotate user agents
            user_agents = [
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0'
            ]
            
            current_ua_index = getattr(download, 'ua_index', 0)
            download.ua_index = (current_ua_index + 1) % len(user_agents)
            download.selected_user_agent = user_agents[download.ua_index]
            logger.debug("Rotated to user agent %s/%s", download.ua_index + 1, len(user_agents))
            
            # Restart download with recovery settings
            self._restart_download_with_adjustments(download_id)
            return True
            
        except Exception as e:
            logger.warning("403 recovery failed: %s", e)
            return False
    
    def _apply_yt_dlp_recovery(self, download_id: str, error_msg: str) -> bool:
        """Apply specific recovery strategies for yt-dlp errors"""
        download = self.downloads.get(download_id)
        if not download:
            return False
        
        try:
            # Strategy 1: Try alternative format selection
            if 'ffmpeg' in error_msg.lower():
                logger.info("Applying FFmpeg recovery strategy")
                return self._try_alternative_formats(download_id)
            
            # Strategy 3: Try different download options
            elif 'fragment' in error_msg.lower() or 'timeout' in error_msg.lower():
                logger.info("Applying connection recovery strategy")
                return self._try_connection_adjustments(download_id)
            
            return False
            
        except Exception as e:
            logger.warning("Recovery attempt failed: %s", e)
            return False
    
    def _apply_general_recovery(self, download_id: str, error_msg: str) -> bool:
        """Apply general recovery strategies for various errors"""
        download = self.downloads.get(download_id)
        if not download:
            return False
        
        try:
            # Strategy 1: Increase timeouts
            if 'timeout' in error_msg.lower():
                logger.info("Increasing timeout for recovery")
                download.timeout_multiplier = getattr(download, 'timeout_multiplier', 1.0) * 1.5
                # Restart download with increased timeout
                self._restart_download_with_adjustments(download_id)
                return True
            
            # Strategy 2: Reduce concurrent connections
            elif 'connection' in error_msg.lower() or 'too many' in error_msg.lower():
                logger.info("Reducing concurrent connections for recovery")
                download.num_threads = max(1, download.num_threads // 2)
                self._restart_download_with_adjustments(download_id)
                return True
            
            return False
            
        except Exception as e:
            logger.warning("General recovery attempt failed: %s", e)
            return False
    
    def _try_alternative_formats(self, download_id: str) -> bool:
        """Try alternative format selections to avoid ffmpeg issues"""
        download = self.downloads.get(download_id)
        if not download:
            return False
        
        # Define alternative formats with enhanced MKV support
        alternative_formats = [
            # Primary MKV formats (preferred for videos)
            "best[ext=mkv][vcodec!=none][acodec!=none]/best[ext=mp4][vcodec!=none][acodec!=none]/best[ext=webm][vcodec!=none][acodec!=none]/best",
            "bestvideo[ext=mkv]+bestaudio[ext=mka]/bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo[ext=webm]+bestaudio[ext=webm]/best",
            # Fallback formats with MKV remuxing
            "best[vcodec!=none][acodec!=none]/best"
        ]
        
        for format_str in alternative_formats:
            try:
                logger.info("Trying alternative format: %s", format_str[:50])
                success = self._attempt_download_with_format(download_id, format_str)
                if success:
                    return True
            except Exception as e:
                logger.warning("Alternative format failed: %s", e)
                continue
        
        return False
    
    def _try_lower_quality(self, download_id: str) -> bool:
        """Try reducing quality to find available formats"""
        download = self.downloads.get(download_id)
        if not download:
            return False
        
        # Quality reduction sequence
        quality_sequence = ["720p", "480p", "360p", "Worst"]
        current_quality = download.video_quality
        
        try:
            for quality in quality_sequence:
                if quality != current_quality:
                    logger.warning("Trying reduced quality after failure: %s", quality)
                    original_quality = download.video_quality
                    download.video_quality = quality
                    
                    format_str = FormatSelector.get_format_string(
                        download.download_type,
                        download.video_quality,
                        download.audio_quality
                    )
                    
                    success = self._attempt_download_with_format(download_id, format_str)
                    if success:
                        return True
                    
                    # Restore original quality for next attempt
                    download.video_quality = original_quality
        except Exception as e:
            logger.warning("Quality reduction failed: %s", e)
        
        return False
    
    def _try_connection_adjustments(self, download_id: str) -> bool:
        """Adjust connection settings to improve reliability"""
        download = self.downloads.get(download_id)
        if not download:
            return False
        
        try:
            # Reduce concurrent fragments and increase timeouts
            original_fragments = getattr(download, 'concurrent_fragments', 16)
            download.concurrent_fragments = max(4, original_fragments // 2)
            
            # Increase timeout values
            download.timeout_multiplier = getattr(download, 'timeout_multiplier', 1.0) * 2.0
            
            logger.debug(
                "Adjusted connection settings: fragments=%s timeout_multiplier=%s",
                download.concurrent_fragments, download.timeout_multiplier,
            )
            
            # Restart with adjusted settings
            self._restart_download_with_adjustments(download_id)
            return True
            
        except Exception as e:
            logger.warning("Connection adjustment failed: %s", e)
            return False
    
    def _attempt_download_with_format(self, download_id: str, format_string: str) -> bool:
        """Attempt download with specific format string"""
        download = self.downloads.get(download_id)
        if not download:
            return False
        
        # This would restart the download with new format
        # Implementation would depend on the specific download workflow
        return False  # Placeholder - would need actual implementation
    
    def _restart_download_with_adjustments(self, download_id: str):
        """Queue an adjusted retry only after the previous worker releases its slot."""
        queue_now = False
        with self.lock:
            download = self.downloads.get(download_id)
            if not download or download.status in {"Completed", "Cancelled", "Cancelling"}:
                return
            download.error_message = None
            if download.slot_active:
                if download.status not in {"Error", "Paused", "Interrupted"}:
                    self._set_status_locked(download, "Error")
                download.resume_pending = True
            elif download.status in {"Error", "Paused", "Interrupted"}:
                download.pause_event.clear()
                download.cancel_event.clear()
                download.pause_requested = False
                download.cancel_requested = False
                self._set_status_locked(download, "Queued")
                self.download_queue.put(download_id, download.priority)
                queue_now = True
        self._persist_downloads()
        if queue_now:
            self._notify_status_change(download_id, "Queued")
            self._process_queue()
    
    def _notify_status_change(self, download_id: str, status: str):
        """Notify all registered callbacks of status change"""
        if status in {"Completed", "Error", "Cancelled"}:
            info = self.get_download_info(download_id)
            if info:
                self._add_history_entry(info)
        with self.lock:
            callbacks = list(self.status_callbacks)
        for callback in callbacks:
            try:
                callback(download_id, status)
            except Exception as exc:
                logger.exception("Status callback failed for %s", download_id)
