import json
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
import requests

from download_manager import (
    Download,
    DownloadManager,
    DownloadPriority,
    PinnedAddressHTTPAdapter,
    _is_public_address,
)


DATA = b"safe-download-payload-" * 60000
CHANGED_DATA = b"new-resource-content-" * 12000


class DownloadHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    state_lock = threading.Lock()
    request_ranges = []
    active_requests = 0
    peak_requests = 0
    drop_count = 0
    retry_count = 0

    def log_message(self, _format, *_args):
        pass

    def do_GET(self):
        with self.state_lock:
            type(self).active_requests += 1
            type(self).peak_requests = max(
                type(self).peak_requests, type(self).active_requests
            )

        try:
            path = urlsplit(self.path).path
            range_header = self.headers.get("Range")
            with self.state_lock:
                self.request_ranges.append((path, range_header))

            if path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/file")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            if path == "/retry":
                with self.state_lock:
                    retry_now = type(self).retry_count == 0
                    type(self).retry_count += 1
                if retry_now:
                    self.send_response(503)
                    self.send_header("Retry-After", "0")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return

            if path == "/timeout":
                time.sleep(0.2)

            payload = CHANGED_DATA if path == "/changed" else DATA
            if path == "/slow":
                payload *= 4
            etag = '"v2"' if path == "/changed" else '"v1"'

            if path == "/drop" and not range_header:
                with self.state_lock:
                    drop_now = type(self).drop_count == 0
                    type(self).drop_count += 1
                if drop_now:
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(payload)))
                    self.send_header("ETag", etag)
                    self.end_headers()
                    self.wfile.write(payload[:len(payload) // 2])
                    self.wfile.flush()
                    self.connection.shutdown(2)
                    self.connection.close()
                    return

            if range_header and path != "/ignore-range":
                start = int(range_header.removeprefix("bytes=").split("-", 1)[0])
                response_start = start + 1 if path == "/malformed" else start
                body = payload[start:]
                self.send_response(206)
                self.send_header(
                    "Content-Range",
                    f"bytes {response_start}-{len(payload) - 1}/{len(payload)}",
                )
            else:
                body = payload
                self.send_response(200)

            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("ETag", etag)
            self.send_header("Last-Modified", "Wed, 21 Oct 2015 07:28:00 GMT")
            self.end_headers()

            chunk_size = 16 * 1024 if path == "/slow" else 128 * 1024
            for offset in range(0, len(body), chunk_size):
                self.wfile.write(body[offset:offset + chunk_size])
                self.wfile.flush()
                if path == "/slow":
                    time.sleep(0.003)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with self.state_lock:
                type(self).active_requests -= 1


class QuietHTTPServer(ThreadingHTTPServer):
    def handle_error(self, _request, _client_address):
        pass


class DownloadManagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = QuietHTTPServer(("127.0.0.1", 0), DownloadHandler)
        cls.server.daemon_threads = True
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_thread.join(timeout=2)

    def setUp(self):
        with DownloadHandler.state_lock:
            DownloadHandler.request_ranges = []
            DownloadHandler.active_requests = 0
            DownloadHandler.peak_requests = 0
            DownloadHandler.drop_count = 0
            DownloadHandler.retry_count = 0
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.managers = []
        self.manager = self.make_manager()

    def tearDown(self):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if all(manager.active_downloads == 0 for manager in self.managers):
                break
            time.sleep(0.01)
        self.temp_dir.cleanup()

    def make_manager(self, **kwargs):
        manager = DownloadManager(
            max_concurrent_downloads=kwargs.pop("max_concurrent_downloads", 3),
            history_file=self.root / "history.json",
            state_file=self.root / "state.json",
            request_timeout=kwargs.pop("request_timeout", (1, 1)),
            max_retries=kwargs.pop("max_retries", 2),
        )
        self.managers.append(manager)
        return manager

    def wait_for_idle(self, manager, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if manager.active_downloads == 0:
                return
            time.sleep(0.01)
        self.fail(f"Download manager did not become idle: {manager.active_downloads}")

    def wait_for_status(self, manager, download_id, expected, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            info = manager.get_download_info(download_id)
            if info and info["status"] in expected:
                return info
            time.sleep(0.01)
        self.fail(
            f"Download {download_id} did not reach {expected}; "
            f"last state was {manager.get_download_info(download_id)}"
        )

    def add_download(self, path="/file", filename="target.bin", **kwargs):
        return self.manager.add_download(
            self.base_url + path,
            str(self.root),
            custom_filename=filename,
            **kwargs,
        )

    def seed_partial(self, manager, path, partial, filename="target.bin", etag='"v1"'):
        download_id = "recovered"
        url = self.base_url + path
        download = Download(
            url, str(self.root), download_id, is_video=False,
            custom_filename=filename,
        )
        download.current_filename = download.get_filename()
        download.download_path = str(self.root / download.current_filename)
        download.part_path = download.download_path + ".part"
        download.file_size = len(DATA)
        download.downloaded = len(partial)
        download.etag = etag
        download.last_modified = "Wed, 21 Oct 2015 07:28:00 GMT"
        download.status = "Paused"
        Path(download.part_path).write_bytes(partial)
        manager.downloads[download_id] = download
        manager._save_resume_metadata(
            download, Path(download.part_path + ".json")
        )
        manager._persist_downloads()
        return download_id, download.download_path

    def test_successful_download_is_finalized_and_recorded(self):
        download_id = self.add_download()
        info = self.wait_for_status(self.manager, download_id, {"Completed"})
        final_path = self.root / info["filename"]
        self.assertEqual(final_path.read_bytes(), DATA)
        self.assertFalse(Path(str(final_path) + ".part").exists())
        self.assertEqual(info["downloaded"], len(DATA))
        self.assertEqual(self.manager.history[-1]["status"], "Completed")

    def test_same_origin_http_redirect_still_downloads(self):
        download_id = self.add_download("/redirect", filename="redirected.bin")
        info = self.wait_for_status(self.manager, download_id, {"Completed"})
        self.assertEqual((self.root / info["filename"]).read_bytes(), DATA)

    def test_public_url_cannot_redirect_to_loopback_or_private_ip(self):
        for target in ("http://127.0.0.1/private", "http://10.0.0.5/private"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                DownloadManager._validate_http_redirect_target(
                    "https://media.example.test/file", target
                )

    def test_non_public_address_policy_covers_ipv6_and_mapped_ipv4(self):
        for address in (
            "::1", "::", "fe80::1", "fc00::1", "ff02::1",
            "::ffff:127.0.0.1", "::ffff:10.0.0.1",
        ):
            with self.subTest(address=address):
                self.assertFalse(_is_public_address(address))
        self.assertTrue(_is_public_address("2606:4700:4700::1111"))

    def test_connection_resolution_rejects_dns_rebinding_to_private_ip(self):
        answers = [
            [(None, None, None, None, ("93.184.216.34", 443))],
            [(None, None, None, None, ("10.0.0.8", 443))],
        ]
        request = requests.Request(
            "GET", "https://media.example.test/file"
        ).prepare()
        adapter = PinnedAddressHTTPAdapter()
        with patch("download_manager.socket.getaddrinfo", side_effect=answers):
            DownloadManager._validate_http_redirect_target(
                "https://start.example.test/file", request.url
            )
            with self.assertRaises(ValueError):
                adapter.get_connection_with_tls_context(request, True, {})
        adapter.close()

    def test_connection_resolution_rejects_dns_rebinding_to_loopback(self):
        answers = [
            [(None, None, None, None, ("93.184.216.34", 443))],
            [(None, None, None, None, ("127.0.0.1", 443))],
        ]
        request = requests.Request(
            "GET", "https://media.example.test/file"
        ).prepare()
        adapter = PinnedAddressHTTPAdapter()
        with patch("download_manager.socket.getaddrinfo", side_effect=answers):
            DownloadManager._validate_http_redirect_target(
                "https://start.example.test/file", request.url
            )
            with self.assertRaises(ValueError):
                adapter.get_connection_with_tls_context(request, True, {})
        adapter.close()

    def test_public_connection_uses_validated_address_and_keeps_tls_hostname(self):
        request = requests.Request(
            "GET", "https://media.example.test/file"
        ).prepare()
        adapter = PinnedAddressHTTPAdapter()
        with patch(
            "download_manager.socket.getaddrinfo",
            return_value=[(None, None, None, None, ("93.184.216.34", 443))],
        ) as resolve:
            pool = adapter.get_connection_with_tls_context(request, True, {})
        self.assertEqual(resolve.call_count, 1)
        self.assertEqual(pool.host, "media.example.test")
        self.assertEqual(pool.conn_kw["approved_address"], "93.184.216.34")
        self.assertEqual(pool.conn_kw.get("assert_hostname", "media.example.test"), "media.example.test")
        with patch("download_manager.urllib3_create_connection") as connect:
            connection = pool.ConnectionCls(
                host=pool.host,
                port=pool.port,
                approved_address=pool.conn_kw["approved_address"],
            )
            connection._new_conn()
        self.assertEqual(connect.call_args.args[0], ("93.184.216.34", 443))
        adapter.close()

    def test_pinned_adapter_rejects_proxies_and_disabled_tls_verification(self):
        adapter = PinnedAddressHTTPAdapter()
        request = requests.Request(
            "GET", "https://media.example.test/file"
        ).prepare()
        with self.assertRaises(requests.exceptions.ProxyError):
            adapter.get_connection_with_tls_context(
                request, True, {"https": "http://proxy.example.test"}
            )
        with self.assertRaises(requests.exceptions.SSLError):
            adapter.get_connection_with_tls_context(request, False, {})
        adapter.close()

    @patch(
        "download_manager.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("93.184.216.34", 443))],
    )
    def test_redirect_to_public_host_is_allowed(self, _resolve):
        DownloadManager._validate_http_redirect_target(
            "https://media.example.test/file", "https://cdn.example.test/file"
        )

    @patch(
        "download_manager.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("::1", 443, 0, 0))],
    )
    def test_public_redirect_cannot_resolve_to_ipv6_loopback(self, _resolve):
        with self.assertRaises(ValueError):
            DownloadManager._validate_http_redirect_target(
                "https://media.example.test/file", "https://cdn.example.test/file"
            )

    def test_https_redirect_cannot_downgrade_to_http(self):
        with self.assertRaises(ValueError):
            DownloadManager._validate_http_redirect_target(
                "https://media.example.test/file", "http://cdn.example.test/file"
            )

    @patch(
        "download_manager.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("10.0.0.8", 443))],
    )
    def test_same_hostname_redirect_rejects_private_dns_resolution(self, _resolve):
        with self.assertRaises(ValueError):
            DownloadManager._validate_http_redirect_target(
                "https://media.example.test/file", "https://media.example.test/next"
            )

    def test_changing_queue_priority_removes_stale_queue_entry(self):
        first = Download(self.base_url + "/file", str(self.root), "first")
        second = Download(self.base_url + "/file", str(self.root), "second")
        self.manager.downloads.update({"first": first, "second": second})
        self.manager.download_queue.put("first", DownloadPriority.DEFAULT)
        self.manager.download_queue.put("second", DownloadPriority.DEFAULT)

        self.assertTrue(self.manager.set_download_priority("second", DownloadPriority.HIGH))

        self.assertEqual(self.manager.download_queue.qsize(), 2)
        self.assertEqual(self.manager.download_queue.get(), "second")
        self.assertEqual(self.manager.download_queue.get(), "first")

    def test_existing_destination_file_is_never_overwritten(self):
        existing_file = self.root / "target.bin"
        existing_file.write_bytes(b"keep-user-data")
        download_id = self.add_download()
        info = self.wait_for_status(self.manager, download_id, {"Completed"})
        self.assertEqual(existing_file.read_bytes(), b"keep-user-data")
        self.assertNotEqual(info["filename"], existing_file.name)

    def test_interrupted_response_retries_using_validated_206(self):
        download_id = self.add_download("/drop")
        info = self.wait_for_status(self.manager, download_id, {"Completed"})
        self.assertEqual((self.root / info["filename"]).read_bytes(), DATA)
        self.assertTrue(any(
            path == "/drop" and value and value.startswith("bytes=")
            for path, value in DownloadHandler.request_ranges
        ))

    def test_transient_server_error_uses_bounded_retry(self):
        download_id = self.add_download("/retry")
        info = self.wait_for_status(self.manager, download_id, {"Completed"})
        self.assertEqual((self.root / info["filename"]).read_bytes(), DATA)
        self.assertEqual(DownloadHandler.retry_count, 2)

    def test_resume_after_restart_uses_persisted_metadata(self):
        first_manager = self.make_manager()
        download_id, final_path = self.seed_partial(
            first_manager, "/file", DATA[:4096]
        )
        recovered_manager = self.make_manager()
        self.assertEqual(
            recovered_manager.get_download_info(download_id)["status"], "Paused"
        )
        recovered_manager.resume_download(download_id)
        info = self.wait_for_status(recovered_manager, download_id, {"Completed"})
        self.assertEqual(Path(final_path).read_bytes(), DATA)
        self.assertTrue(any(
            value == f"bytes={4096}-"
            for path, value in DownloadHandler.request_ranges if path == "/file"
        ))

    def test_server_ignoring_range_restarts_without_appending(self):
        download_id, final_path = self.seed_partial(
            self.manager, "/ignore-range", DATA[:2048]
        )
        self.manager.resume_download(download_id)
        self.wait_for_status(self.manager, download_id, {"Completed"})
        self.assertEqual(Path(final_path).read_bytes(), DATA)

    def test_invalid_content_range_does_not_append_bytes(self):
        partial = DATA[:1024]
        download_id, final_path = self.seed_partial(
            self.manager, "/malformed", partial
        )
        self.manager.resume_download(download_id)
        info = self.wait_for_status(self.manager, download_id, {"Error"})
        self.assertIn("expected 1024", info["error"])
        self.assertEqual(Path(final_path + ".part").read_bytes(), partial)

    def test_changed_resource_is_restarted_from_zero(self):
        download_id, final_path = self.seed_partial(
            self.manager, "/changed", DATA[:2048]
        )
        self.manager.resume_download(download_id)
        self.wait_for_status(self.manager, download_id, {"Completed"})
        self.assertEqual(Path(final_path).read_bytes(), CHANGED_DATA)
        self.assertIn(
            ("/changed", None), DownloadHandler.request_ranges
        )

    def test_network_timeout_is_reported_after_bounded_retries(self):
        manager = self.make_manager(request_timeout=(0.05, 0.05), max_retries=0)
        download_id = manager.add_download(
            self.base_url + "/timeout", str(self.root), custom_filename="timeout.bin"
        )
        info = self.wait_for_status(manager, download_id, {"Error"})
        self.assertTrue(info["error"])

    def test_global_concurrent_download_limit_is_enforced(self):
        manager = self.make_manager(max_concurrent_downloads=2)
        ids = [
            manager.add_download(
                self.base_url + "/slow",
                str(self.root),
                custom_filename=f"concurrent-{index}.bin",
            )
            for index in range(4)
        ]
        for download_id in ids:
            self.wait_for_status(manager, download_id, {"Completed"})
        self.wait_for_idle(manager)
        self.assertLessEqual(DownloadHandler.peak_requests, 2)

    def test_pause_resume_and_cancel_are_safe(self):
        download_id = self.add_download("/slow", "controlled.bin")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.manager.get_download_info(download_id)["downloaded"] > 0:
                break
            time.sleep(0.01)
        self.manager.pause_download(download_id)
        self.wait_for_status(self.manager, download_id, {"Paused"})
        self.manager.resume_download(download_id)
        self.wait_for_status(self.manager, download_id, {"Downloading"})
        self.manager.cancel_download(download_id)
        self.wait_for_status(self.manager, download_id, {"Cancelled"})
        self.wait_for_idle(self.manager)
        download = self.manager.downloads[download_id]
        self.assertTrue(
            Path(download.part_path).exists(),
            "cancelled downloads retain their partial file for an explicit later resume",
        )
        self.assertEqual(self.manager.active_downloads, 0)

    def test_pause_cancel_race_releases_exactly_one_queue_slot(self):
        download_id = self.add_download("/slow", "race.bin")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.manager.get_download_info(download_id)["downloaded"] > 0:
                break
            time.sleep(0.01)
        barrier = threading.Barrier(3)

        def act(action):
            barrier.wait()
            action(download_id)

        pause = threading.Thread(target=act, args=(self.manager.pause_download,))
        cancel = threading.Thread(target=act, args=(self.manager.cancel_download,))
        pause.start()
        cancel.start()
        barrier.wait()
        pause.join(timeout=2)
        cancel.join(timeout=2)
        self.wait_for_status(self.manager, download_id, {"Cancelled"})
        self.wait_for_idle(self.manager)

    def test_disk_errors_are_exposed_as_error_state(self):
        blocked_path = self.root / "not-a-directory"
        blocked_path.write_text("occupied", encoding="utf-8")
        download_id = self.manager.add_download(
            self.base_url + "/file",
            str(blocked_path),
            custom_filename="blocked.bin",
        )
        info = self.wait_for_status(self.manager, download_id, {"Error"})
        self.assertIn("not-a-directory", info["error"])

    def test_unsafe_custom_filenames_are_reduced_to_safe_names(self):
        traversal = Download(
            "https://example.test/a",
            str(self.root),
            "safe1",
            custom_filename="..\\..\\outside.exe",
        ).get_filename()
        reserved = Download(
            "https://example.test/a",
            str(self.root),
            "safe2",
            custom_filename="CON.txt",
        ).get_filename()
        self.assertNotIn("\\", traversal)
        self.assertNotIn("/", traversal)
        self.assertNotEqual(reserved.upper(), "CON.TXT")

    def test_non_http_schemes_are_rejected(self):
        with self.assertRaises(ValueError):
            self.manager.add_download("file:///C:/Windows/win.ini", str(self.root))

    def test_persistent_state_contains_no_unfinished_external_data(self):
        download_id = self.add_download()
        self.wait_for_status(self.manager, download_id, {"Completed"})
        self.wait_for_idle(self.manager)
        saved = json.loads((self.root / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(saved, [])

    def test_terminal_download_removal_waits_for_worker_slot_release(self):
        download_id = self.add_download()
        self.wait_for_status(self.manager, download_id, {"Completed"})
        self.wait_for_idle(self.manager)
        download = self.manager.downloads[download_id]
        with self.manager.lock:
            download.slot_active = True
        self.assertFalse(self.manager.remove_download(download_id))
        self.assertIn(download_id, self.manager.downloads)
        with self.manager.lock:
            download.slot_active = False
        self.assertTrue(self.manager.remove_download(download_id))
        self.assertNotIn(download_id, self.manager.downloads)


if __name__ == "__main__":
    unittest.main()
