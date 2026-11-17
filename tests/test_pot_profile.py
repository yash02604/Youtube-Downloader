"""Offline provider wiring and token-safe observations, no attestation calls."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import os


class PoTokenProfileTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        for relative in ('bgutil/server/src/generate_once.ts', 'deno-private'):
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch()
        (self.root / 'bgutil/server/node_modules').mkdir()
        (self.root / 'bgutil/deno_dir').mkdir()
        self.runtime = SimpleNamespace(name='deno', version='2.9.8', path=str(self.root/'deno'))
        self.runtime.ytdlp_options = {'js_runtimes': {'deno': {'path': self.runtime.path}}}

    def profile(self, installed=True):
        from web.backend.youtube_profile import WebYouTubeProfile
        with patch('web.backend.youtube_profile.plugin_status', return_value=(installed, '2.0.2' if installed else None)):
            return WebYouTubeProfile(runtime=self.runtime, runtime_root=self.root)

    def fake_ydl(self, provider):
        director = SimpleNamespace(providers={'chosen': provider, 'http': SimpleNamespace(PROVIDER_NAME='bgutil:http')})
        extractor = SimpleNamespace(initialize=Mock(), _pot_director=director)
        return SimpleNamespace(get_info_extractor=Mock(return_value=extractor)), director

    def provider(self, operation):
        return SimpleNamespace(PROVIDER_NAME='bgutil:script-deno', PROVIDER_VERSION='2.0.2',
                               is_available=lambda: True, _real_request_pot=operation)

    def test_shared_options_use_pinned_script_deno_and_mweb(self):
        profile = self.profile()
        self.assertEqual(profile.options['extractor_args']['youtube']['player_client'], ['mweb'])
        self.assertEqual(profile.options['extractor_args']['youtubepot-bgutilscript']['server_home'],
                         [str(self.root/'bgutil/server')])
        self.assertEqual(profile.options['js_runtimes']['deno']['path'], str(self.root/'deno-private'))
        for key in ('cookiesfrombrowser', 'cookiefile', 'proxy', 'po_token'):
            self.assertNotIn(key, profile.options)

    def test_missing_plugin_or_assets_fails_closed(self):
        from web.backend.youtube_profile import ProviderFailure
        for installed in (False, True):
            if installed:
                (self.root/'bgutil/server/src/generate_once.ts').unlink()
            profile = self.profile(installed)
            with self.assertRaises(ProviderFailure) as caught:
                profile.prepare(SimpleNamespace())
            self.assertEqual(caught.exception.analysis_code, 'PO_PROVIDER_UNAVAILABLE')

    def test_success_is_observed_without_logging_token(self):
        from yt_dlp.extractor.youtube.pot.provider import PoTokenResponse
        profile = self.profile()
        provider = self.provider(lambda request: PoTokenResponse(po_token='c2VjcmV0'))
        ydl, director = self.fake_ydl(provider)
        profile.prepare(ydl)
        self.assertEqual(list(director.providers), ['chosen'])
        provider._real_request_pot(None)
        with self.assertLogs('web.backend.youtube_profile', level='WARNING') as captured:
            profile.finish(RuntimeError('Sign in to confirm you are not a bot secret'))
        status = profile.safe_status(RuntimeError('Sign in to confirm you are not a bot'))
        self.assertTrue(status['po_token_attempted'])
        self.assertTrue(status['po_token_generated'])
        self.assertEqual(status['provider_result'], 'PO_TOKEN_GENERATED_BUT_LOGIN_REQUIRED')
        for secret in ('c2VjcmV0', 'secret', str(self.root)):
            self.assertNotIn(secret, '\n'.join(captured.output))

    def test_failure_and_not_reached_are_distinct(self):
        profile = self.profile()
        def fail(request):
            raise RuntimeError('provider private token=secret')
        provider = self.provider(fail)
        ydl, _ = self.fake_ydl(provider)
        profile.prepare(ydl)
        status = profile.safe_status(RuntimeError('Sign in to confirm you are not a bot'))
        self.assertEqual(status['provider_result'], 'PO_PROVIDER_NOT_REACHED')
        with self.assertRaises(RuntimeError):
            provider._real_request_pot(None)
        status = profile.safe_status(RuntimeError('analysis failed'))
        self.assertTrue(status['po_token_attempted'])
        self.assertFalse(status['po_token_generated'])
        self.assertEqual(status['provider_result'], 'PO_TOKEN_GENERATION_FAILED')
        self.assertNotIn('secret', json.dumps(status))

    def test_initialization_failure_is_safe(self):
        from web.backend.youtube_profile import ProviderFailure
        profile = self.profile()
        ydl = SimpleNamespace(get_info_extractor=Mock(side_effect=RuntimeError('private path token=secret')))
        with self.assertRaises(ProviderFailure) as caught:
            profile.prepare(ydl)
        self.assertEqual(caught.exception.analysis_code, 'PO_PROVIDER_INITIALIZATION_FAILED')
        self.assertNotIn('secret', str(caught.exception))

    def test_vercel_analysis_receives_shared_profile_options_and_setup(self):
        from web.backend.youtube_analysis import analyze_public_youtube
        profile = self.profile()
        with patch.dict(os.environ, {'VERCEL': '1'}), \
                patch('web.backend.youtube_analysis.discover_web_runtime', return_value=self.runtime), \
                patch('web.backend.youtube_analysis.probe_ejs_runtime', return_value={}), \
                patch('web.backend.youtube_profile.WebYouTubeProfile', return_value=profile), \
                patch('web.backend.youtube_analysis.VideoAnalyzer.get_video_info', return_value={'title': 'public'}) as analyze:
            analyze_public_youtube('https://www.youtube.com/watch?v=abcdefghijk')
        self.assertEqual(analyze.call_args.kwargs['ytdlp_options'], profile.options)
        self.assertEqual(analyze.call_args.kwargs['ytdlp_setup'], profile.prepare)
        self.assertFalse(analyze.call_args.kwargs['allow_browser_session'])

    def test_plugin_dependency_availability(self):
        from web.backend.youtube_profile import plugin_status
        with patch('web.backend.youtube_profile.importlib.metadata.version', return_value='2.0.2'), \
                patch('web.backend.youtube_profile.importlib.import_module', return_value=SimpleNamespace(BgUtilScriptDenoPTP=object())):
            self.assertEqual(plugin_status(), (True, '2.0.2'))
        with patch('web.backend.youtube_profile.importlib.import_module', side_effect=ImportError('private')):
            self.assertEqual(plugin_status(), (False, None))

    def test_provider_subprocess_does_not_inherit_project_credentials(self):
        profile = self.profile()
        provider = self.provider(lambda request: None)
        ydl, _ = self.fake_ydl(provider)
        with patch.dict(os.environ, {'VERCEL_OIDC_TOKEN': 'private-project-token',
                                     'SENSITIVE_API_KEY': 'private-api-key',
                                     'HTTPS_PROXY': 'https://private-proxy'}):
            profile.prepare(ydl)
            child_env = provider._jsrt_envs()
        for key in ('VERCEL_OIDC_TOKEN', 'SENSITIVE_API_KEY', 'HTTPS_PROXY', 'HOME', 'USERPROFILE'):
            self.assertNotIn(key, child_env)
        self.assertEqual(child_env['DENO_NO_PROMPT'], '1')
