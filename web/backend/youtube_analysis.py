"""Logged-out web analysis with allowlisted observations, never raw yt-dlp logs."""
import importlib.metadata
import json
import logging
import platform
import re
from pathlib import Path

from js_runtime import discover_web_runtime
from video_analyzer import VideoAnalyzer
from .ejs_probe import probe_ejs_runtime

logger = logging.getLogger(__name__)

MESSAGES = {
    'LOGIN_REQUIRED': 'YouTube requires verification for this request.',
    'JS_RUNTIME_UNAVAILABLE': 'The server JavaScript runtime is unavailable.',
    'EJS_UNAVAILABLE': 'The server challenge solver is unavailable.',
    'PO_TOKEN_REQUIRED': 'YouTube requires additional playback verification.',
    'UPSTREAM_REJECTED': 'YouTube rejected the analysis request.',
    'ANALYSIS_FAILED': 'The video could not be analyzed.',
}


def classify_error(error):
    # Inspect in memory only. Never forward exception text or extracted values.
    text = str(error).lower().replace('\u2019', "'")
    if 'not a bot' in text:
        return 'LOGIN_REQUIRED', 'BOT_VERIFICATION'
    if 'sign in' in text or 'login required' in text:
        return 'LOGIN_REQUIRED', 'SIGN_IN_REQUIRED'
    if re.search(r'po[ _-]?token.{0,100}(?:required|missing)|(?:requires?|missing).{0,60}po[ _-]?token', text) and 'not required' not in text:
        return 'PO_TOKEN_REQUIRED', 'PO_TOKEN_REQUIRED'
    if 'no supported javascript runtime' in text or 'javascript runtime is unavailable' in text:
        return 'JS_RUNTIME_UNAVAILABLE', 'NO_SUPPORTED_JS_RUNTIME'
    if 'ejs' in text and ('not installed' in text or 'not available' in text):
        return 'EJS_UNAVAILABLE', 'EJS_UNAVAILABLE'
    if ('no usable challenge solver' in text or
            ('challenge solver' in text and any(marker in text for marker in (
                'failed to load', 'hash mismatch', 'version mismatch')))):
        return 'EJS_UNAVAILABLE', 'SOLVER_ASSETS_UNAVAILABLE'
    if 'challenge solving failed' in text or 'signature solving failed' in text:
        return 'ANALYSIS_FAILED', 'JS_CHALLENGE_FAILED'
    for status in (403, 429):
        if f'http error {status}' in text or f'http error: {status}' in text:
            return 'UPSTREAM_REJECTED', f'HTTP_{status}'
    return 'ANALYSIS_FAILED', 'UNKNOWN'


def _version(package):
    try:
        value = importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return None
    return value if re.fullmatch(r'[0-9][0-9a-zA-Z.+-]{0,50}', value) else None


def ejs_status():
    from yt_dlp import dependencies
    return {'version': _version('yt-dlp-ejs'),
            'visible_to_ytdlp': bool(dependencies.yt_dlp_ejs)}


def _pot_plugin_loaded():
    try:
        from yt_dlp.extractor.youtube.pot._registry import _pot_providers
        return any(provider.__module__.startswith('yt_dlp_plugins.')
                   for provider in _pot_providers.value.values())
    except (ImportError, AttributeError):
        return None  # Unknown API is not evidence of provider availability.


class SafeYtdlpLogger:
    def __init__(self):
        self.signals = set()
        self.clients = set()
        self.runtimes = {}

    def _observe(self, message):
        text = str(message)
        code, reason = classify_error(text)
        if reason != 'UNKNOWN':
            self.signals.add(reason)
        if 'Solving JS challenges using ' in text or re.search(r'Attempting to solve \d+ challenges using ', text):
            self.signals.add('JS_CHALLENGE_ATTEMPTED')
        if 'Running deno:' in text or 'Running node:' in text:
            self.signals.add('JS_EXECUTION_ATTEMPTED')
        if 'JS runtimes:' in text:
            for name, version in re.findall(r'\b(deno|node|quickjs)-([0-9]+(?:\.[0-9]+){1,3})', text):
                self.runtimes[name] = version
        match = re.search(r'Downloading (web|web_safari|mweb|tv|tv_simply|web_embedded|web_music|web_creator|android|android_vr|ios) player API JSON', text)
        if match:
            self.clients.add(match[1])

    debug = warning = error = _observe

    def snapshot(self):
        return {'signals': sorted(self.signals), 'player_clients': sorted(self.clients),
                'enabled_js_runtimes': dict(self.runtimes)}


def analyze_public_youtube(url):
    observation = SafeYtdlpLogger()
    available = discover_web_runtime(application_dir=Path(__file__).parent / '_runtime')
    diagnostic = {
        'event': 'youtube_analysis', 'profile': 'web_verified_js_local_ejs',
        'player_client_policy': 'yt-dlp-default',
        'yt_dlp_version': _version('yt-dlp'), 'ejs': ejs_status(),
        'os': platform.system(), 'python_version': platform.python_version(),
        'discovered_js_runtime': ({'name': available.name, 'version': available.version} if available else None),
    }
    options = {'logger': observation, 'verbose': True, 'remote_components': [], 'cachedir': False}
    if available:
        options.update(available.ytdlp_options)
        diagnostic['ejs_probe'] = probe_ejs_runtime(available)
    try:
        result = VideoAnalyzer.get_video_info(url, allow_browser_session=False, ytdlp_options=options)
    except Exception as exc:
        code, reason = classify_error(exc)
        diagnostic.update(code=code, reason=reason, exception_type=(type(exc).__name__
            if type(exc).__name__ in {'DownloadError', 'ExtractorError', 'RuntimeError', 'OSError', 'ValueError'} else 'Exception'))
        raise
    else:
        diagnostic.update(code='SUCCESS' if result else 'ANALYSIS_FAILED', reason='METADATA' if result else 'NO_METADATA')
        return result
    finally:
        diagnostic.update(observation.snapshot())
        diagnostic['po_token_provider_plugin_loaded'] = _pot_plugin_loaded()
        logger.warning('youtube_analysis %s', json.dumps(diagnostic, sort_keys=True))
