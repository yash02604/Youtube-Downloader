import asyncio
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
try:
    from fastapi.testclient import TestClient
except ImportError:
    raise unittest.SkipTest('Install web/backend/requirements-test.txt for backend tests')
from web.backend.app import create_app
from web.backend.settings import Settings
from web.backend.events import event_stream
from core.service import DownloadService
from core.destination_policy import RootedDestinationPolicy
from core.events import DownloadEvent
from core.event_bus import EventBus

URL = 'https://www.youtube.com/watch?v=tRoYSagYP7U'


class BackendTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.settings = Settings(download_root=self.root/'downloads', state_root=self.root/'state')
        self.service = DownloadService(destination_policy=RootedDestinationPolicy(self.settings.download_root),
            state_file=self.root/'state.json', history_file=self.root/'history.json', allow_browser_session=False)
        self.service.engine.max_concurrent = 0
        self.analyzer = Mock(return_value={'title': 'Video', 'formats': [{'format_id': '401', 'height': 2160, 'url': 'https://cdn/?token=secret'}],
            'http_headers': {'Cookie': 'secret'}, 'url': 'https://cdn/?signature=secret',
            'thumbnail': 'https://i.ytimg.com/vi/id/default.jpg?token=secret', 'resolutions': [2160]})
        self.app = create_app(self.settings, self.service, self.analyzer)
        self.client = TestClient(self.app, base_url='http://localhost', raise_server_exceptions=False)
        self.client.__enter__()
        self.addCleanup(lambda: self.client.__exit__(None, None, None))

    def add_job(self):
        response = self.client.post('/api/downloads', json={'url': URL})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()['id']

    def test_health_and_docs(self):
        self.assertEqual(self.client.get('/api/health').json()['status'], 'ok')
        self.assertEqual(self.client.get('/docs').status_code, 200)
        self.assertNotIn(str(self.root), self.client.get('/api/health').text)

    def test_create_get_list_and_unknown(self):
        job = self.add_job()
        self.assertEqual(self.client.get('/api/downloads/'+job).json()['id'], job)
        self.assertEqual(len(self.client.get('/api/downloads').json()), 1)
        self.assertEqual(self.client.get('/api/downloads/missing').status_code, 404)

    def test_invalid_urls_and_unknown_fields(self):
        for url in ('file:///secret', 'http://127.0.0.1', 'https://youtube.com.evil/watch?v=tRoYSagYP7U', 'https://user:pass@youtube.com/watch?v=tRoYSagYP7U', 'x'*2049):
            with self.subTest(url=url):
                self.assertEqual(self.client.post('/api/downloads', json={'url': url}).status_code, 400)
        for key in ('ffmpeg_location', 'cookiesfrombrowser', 'ydl_opts', 'shell'):
            self.assertEqual(self.client.post('/api/downloads', json={'url': URL, key: 'forbidden'}).status_code, 400)

    def test_destination_escapes_blocked(self):
        for destination in ('../x', '..\\x', 'C:\\private', 'D:relative', '\\\\host\\share', '/private', 'a/../../x', '%2e%2e/x', '%252e%252e/x', 'x:stream'):
            with self.subTest(destination=destination):
                self.assertEqual(self.client.post('/api/downloads', json={'url': URL, 'destination': destination}).status_code, 400)
        response = self.client.post('/api/downloads', json={'url': URL, 'destination': 'videos/./today'})
        self.assertEqual(response.status_code, 201)
        job = self.service.engine.downloads[response.json()['id']]
        self.assertTrue(Path(job.destination_dir).is_relative_to(self.settings.download_root))

    def test_filename_quality_and_language_validation(self):
        for fields in ({'filename': '../escape.mp4'}, {'filename': 'C:\\secret'}, {'video_quality': 'nonsense'}, {'download_type': 'Shell'}, {'subtitle_languages': ['../../en']}):
            self.assertEqual(self.client.post('/api/downloads', json={'url': URL, **fields}).status_code, 400)

    def test_lifecycle_and_invalid_operation(self):
        job = self.add_job()
        path = '/api/downloads/'+job
        self.assertEqual(self.client.post(path+'/pause').json()['status'], 'Paused')
        self.assertEqual(self.client.post(path+'/pause').status_code, 409)
        self.assertEqual(self.client.post(path+'/resume').json()['status'], 'Queued')
        self.assertEqual(self.client.delete(path).status_code, 409)
        self.assertEqual(self.client.post(path+'/cancel').json()['status'], 'Cancelled')
        self.assertTrue(self.client.delete(path).json()['removed'])
        self.assertEqual(self.client.post(path+'/cancel').status_code, 404)

    def test_shutting_down_maps_to_503(self):
        self.service.shutdown(1)
        self.assertEqual(self.client.post('/api/downloads', json={'url': URL}).status_code, 503)

    def test_metadata_is_sanitized(self):
        response = self.client.post('/api/analyze', json={'url': URL})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('secret', response.text)
        self.assertNotIn('http_headers', response.text)
        self.assertEqual(response.json()['formats'][0]['height'], 2160)

    def test_verification_and_internal_errors_are_safe(self):
        self.analyzer.side_effect = RuntimeError('Sign in to confirm you are not a bot token=secret')
        response = self.client.post('/api/analyze', json={'url': URL})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'LOGIN_REQUIRED')
        self.assertNotIn('secret', response.text)
        with patch.object(self.service, 'list_downloads', side_effect=OSError('C:/private secret traceback')):
            response = self.client.get('/api/downloads')
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {'detail': 'Internal server error'})

    def test_analysis_error_contract_uses_explicit_evidence(self):
        for message, code in (
            ('No supported JavaScript runtime could be found', 'JS_RUNTIME_UNAVAILABLE'),
            ('yt-dlp-ejs is not installed', 'EJS_UNAVAILABLE'),
            ('A PO Token is required for this client', 'PO_TOKEN_REQUIRED'),
            ('HTTP Error 403: Forbidden', 'UPSTREAM_REJECTED'),
            ('Unrecognized upstream response token=secret C:/private', 'ANALYSIS_FAILED'),
        ):
            with self.subTest(code=code):
                self.analyzer.side_effect = RuntimeError(message)
                response = self.client.post('/api/analyze', json={'url': URL})
                self.assertEqual(response.status_code, 502)
                self.assertEqual(response.json()['code'], code)
                self.assertNotIn('secret', response.text)
                self.assertNotIn('C:/', response.text)

    def test_snapshots_hide_paths_signed_urls_and_raw_errors(self):
        job = self.add_job()
        item = self.service.engine.downloads[job]
        item.error_message = 'C:/private?token=secret'
        response = self.client.get('/api/downloads/'+job)
        self.assertNotIn('secret', response.text)
        self.assertNotIn(str(self.root), response.text)
        self.assertNotIn('source_url', response.json())

    def test_cors_is_explicit_and_origins_are_checked(self):
        self.assertEqual(self.client.get('/api/health', headers={'Origin':'https://evil.example'}).status_code, 403)
        response = self.client.get('/api/health', headers={'Origin':'http://localhost:3000'})
        self.assertEqual(response.headers['access-control-allow-origin'], 'http://localhost:3000')
        self.assertNotIn('access-control-allow-credentials', response.headers)

    def test_sse_endpoint_delivery_and_cleanup(self):
        bus = EventBus()
        sub = bus.subscribe()
        bus.publish(DownloadEvent('job', 'completed'))
        original = sub.get_nowait
        def final_event():
            event = original()
            sub.closed = True
            return event
        sub.get_nowait = final_event
        with patch.object(self.service, 'subscribe', return_value=sub), patch.object(self.service, 'unsubscribe') as detach:
            response = self.client.get('/api/events')
            self.assertEqual(response.status_code, 200)
            self.assertIn('text/event-stream', response.headers['content-type'])
            self.assertIn('completed', response.text)
            self.assertTrue(detach.called)
            self.assertTrue(all(call.args == (sub,) for call in detach.call_args_list))

    def test_lifespan_invokes_bounded_shutdown(self):
        fake = Mock()
        fake.list_downloads.return_value = []
        fake.shutdown.return_value = False
        with TestClient(create_app(self.settings, fake), base_url='http://localhost'):
            pass
        fake.shutdown.assert_called_once_with(5.0)

    def test_factory_delays_service_creation_and_disables_browser_cookies(self):
        with patch('web.backend.app.DownloadService') as factory:
            instance = factory.return_value
            instance.list_downloads.return_value = []
            app = create_app(self.settings)
            factory.assert_not_called()
            with TestClient(app, base_url='http://localhost'):
                self.assertFalse(factory.call_args.kwargs['allow_browser_session'])
                self.assertEqual(factory.call_args.kwargs['state_file'], self.settings.state_root/'download_state.json')

    def test_analyze_disables_desktop_browser_session(self):
        with patch('web.backend.app.VideoAnalyzer.get_video_info', return_value={}) as analyze:
            with TestClient(create_app(self.settings, self.service), base_url='http://localhost') as client:
                client.post('/api/analyze', json={'url': URL})
        analyze.assert_called_once()
        self.assertEqual(analyze.call_args.args, (URL,))
        self.assertFalse(analyze.call_args.kwargs['allow_browser_session'])
        self.assertIn('logger', analyze.call_args.kwargs['ytdlp_options'])

    def test_fresh_process_dependency_direction(self):
        script = "import core,core.service,sys; assert 'tkinter' not in sys.modules; assert 'fastapi' not in sys.modules; import web.backend.app; assert 'tkinter' not in sys.modules"
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_untrusted_host_is_rejected(self):
        response = self.client.get('/api/health', headers={'Host': 'evil.example'})
        self.assertEqual(response.status_code, 400)

    def test_unsafe_restored_destination_is_rejected_before_serving(self):
        fake = Mock()
        fake.list_downloads.return_value = [{'destination': str(self.root.parent)}]
        with self.assertRaises(RuntimeError):
            with TestClient(create_app(self.settings, fake), base_url='http://localhost'):
                pass
        fake.shutdown.assert_called_once()

    def test_resume_rechecks_resolved_destination(self):
        job = self.add_job()
        self.service.pause(job)
        self.service.engine.downloads[job].destination_dir = str(self.root.parent)
        self.assertEqual(self.client.post('/api/downloads/'+job+'/resume').status_code, 400)


class StreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_coalescing_resync_and_cleanup(self):
        bus = EventBus()
        sub = bus.subscribe(2)
        service = Mock()
        service.unsubscribe.side_effect = bus.unsubscribe
        request = Mock()
        request.is_disconnected = Mock(side_effect=lambda: None)
        async def connected():
            return False
        request.is_disconnected = connected
        for n in range(1000):
            bus.publish(DownloadEvent('job', 'progress', downloaded_bytes=n))
        stream = event_stream(request, service, sub)
        frame = await anext(stream)
        self.assertIn('999', frame)
        await stream.aclose()
        self.assertTrue(sub.closed)
        sub = bus.subscribe(1)
        bus.publish(DownloadEvent('a', 'paused'))
        bus.publish(DownloadEvent('b', 'completed'))
        frames = [frame async for frame in event_stream(request, service, sub)]
        self.assertIn('event: resync', frames[0])
        self.assertTrue(sub.closed)

    async def test_disconnect_releases_subscription(self):
        bus = EventBus()
        sub = bus.subscribe()
        service = Mock()
        service.unsubscribe.side_effect = bus.unsubscribe
        request = Mock()
        async def disconnected():
            return True
        request.is_disconnected = disconnected
        self.assertEqual([f async for f in event_stream(request, service, sub)], [])
        self.assertTrue(sub.closed)
