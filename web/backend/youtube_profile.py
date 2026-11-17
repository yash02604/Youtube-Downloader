"""One opt-in Vercel profile shared by metadata and the existing download engine."""
import importlib
import importlib.metadata
import json
import logging
import os
import re
from pathlib import Path
import tempfile

from js_runtime import discover_web_runtime

logger = logging.getLogger(__name__)
PROVIDER_VERSION = '2.0.2'
PLAYER_CLIENTS = ('mweb',)


class ProviderFailure(RuntimeError):
    def __init__(self, code):
        self.analysis_code = code
        super().__init__('The YouTube playback verification provider is unavailable.')


def plugin_status():
    try:
        version = importlib.metadata.version('bgutil-ytdlp-pot-provider')
        version = version if re.fullmatch(r'\d+\.\d+\.\d+', version) else None
        module = importlib.import_module('yt_dlp_plugins.extractor.getpot_bgutil_script')
        return hasattr(module, 'BgUtilScriptDenoPTP') and version == PROVIDER_VERSION, version
    except (ImportError, importlib.metadata.PackageNotFoundError, AttributeError):
        return False, None


class WebYouTubeProfile:
    def __init__(self, *, observation=None, runtime=None, runtime_root=None):
        from .youtube_analysis import SafeYtdlpLogger
        self.root = Path(runtime_root) if runtime_root else Path(__file__).parent / '_runtime'
        self.runtime = runtime or discover_web_runtime(application_dir=self.root)
        self.observation = observation or SafeYtdlpLogger()
        self.installed, self.version = plugin_status()
        self.server = self.root / 'bgutil' / 'server'
        self.wrapper = self.root / 'deno-private'
        self.asset_checks = {
            'script': (self.server / 'src/generate_once.ts').is_file(),
            'modules': (self.server / 'node_modules').is_dir(),
            'launcher': self.wrapper.is_file(),
        }
        self.assets = all(self.asset_checks.values())
        self.ready = False
        self.initialization_failed = False
        self.attempted = False
        self.generated = False
        self.generation_failed = False
        self.options = {
            'logger': self.observation, 'verbose': True,
            'remote_components': [], 'cachedir': False,
            'js_runtimes': {'deno': {'path': str(self.wrapper)}},
            'extractor_args': {
                'youtube': {'player_client': list(PLAYER_CLIENTS)},
                'youtubepot-bgutilscript': {'server_home': [str(self.server)]},
            },
        }

    def prepare(self, ydl):
        if not self.installed or not self.assets or not self.runtime or self.runtime.name != 'deno':
            raise ProviderFailure('PO_PROVIDER_UNAVAILABLE')
        # These settings are process-wide but fixed for this Vercel function,
        # never changed on a desktop engine or per user/account.
        if os.environ.get('VERCEL') == '1':
            os.environ['DENO_DIR'] = str(Path(tempfile.gettempdir()) / 'downloader-web' / 'provider-deno')
            os.environ['XDG_CACHE_HOME'] = str(Path(tempfile.gettempdir()) / 'downloader-web' / 'provider-cache')
            os.environ['DENO_NO_PROMPT'] = '1'
            os.environ['DENO_NO_UPDATE_CHECK'] = '1'
        try:
            ie = ydl.get_info_extractor('Youtube')
            ie.initialize()
            director = ie._pot_director
            chosen = {key: provider for key, provider in director.providers.items()
                      if provider.PROVIDER_NAME == 'bgutil:script-deno'
                      and provider.PROVIDER_VERSION == PROVIDER_VERSION}
            # The profile never requests an HTTP provider or an alternate engine.
            director.providers = chosen
            if len(chosen) != 1:
                raise ProviderFailure('PO_PROVIDER_UNAVAILABLE')
            provider = next(iter(chosen.values()))
            # The attestation subprocess needs runtime/cache locations, not
            # Vercel's project credentials or arbitrary application secrets.
            child_env = {key: os.environ[key] for key in (
                'PATH', 'SYSTEMROOT', 'LD_LIBRARY_PATH', 'TMPDIR', 'TEMP', 'TMP',
                'DENO_DIR', 'XDG_CACHE_HOME', 'SSL_CERT_FILE', 'SSL_CERT_DIR',
            ) if key in os.environ}
            child_env.update(DENO_NO_PROMPT='1', DENO_NO_UPDATE_CHECK='1', FORCE_COLOR='false')
            provider._jsrt_envs = lambda: dict(child_env)
            if not provider.is_available():
                raise ProviderFailure('PO_PROVIDER_UNAVAILABLE')
            original = provider._real_request_pot

            def observed_request(request):
                self.attempted = True
                try:
                    response = original(request)
                    from yt_dlp.extractor.youtube.pot._director import validate_response
                    if not validate_response(response):
                        self.generation_failed = True
                    else:
                        self.generated = True
                    return response
                except Exception:
                    self.generation_failed = True
                    raise

            # Instance-only observation: no global plugin monkeypatches, and no
            # token copied into our state, logs, API objects or configuration.
            provider._real_request_pot = observed_request
            self.ready = True
        except ProviderFailure:
            raise
        except Exception:
            self.initialization_failed = True
            raise ProviderFailure('PO_PROVIDER_INITIALIZATION_FAILED') from None

    def safe_status(self, error=None):
        from .youtube_analysis import classify_error
        code = classify_error(error)[0] if error else 'SUCCESS'
        if self.initialization_failed:
            result = 'PO_PROVIDER_INITIALIZATION_FAILED'
        elif not self.ready:
            result = 'PO_PROVIDER_UNAVAILABLE'
        elif code == 'LOGIN_REQUIRED' and self.generated:
            result = 'PO_TOKEN_GENERATED_BUT_LOGIN_REQUIRED'
        elif self.generation_failed:
            result = 'PO_TOKEN_GENERATION_FAILED'
        elif not self.attempted:
            result = 'PO_PROVIDER_NOT_REACHED'
        elif self.generated:
            result = 'PO_TOKEN_GENERATED'
        else:
            result = 'PO_TOKEN_GENERATION_NOT_CONFIRMED'
        return {
            'provider': 'bgutil', 'provider_version': self.version,
            'provider_mode': 'script', 'provider_installed': self.installed,
            'script_assets_available': self.assets, 'provider_ready': self.ready,
            'provider_assets': dict(self.asset_checks),
            'provider_reached': self.attempted, 'po_token_attempted': self.attempted,
            'po_token_generated': self.generated, 'provider_result': result,
            'player_client': ','.join(PLAYER_CLIENTS),
            'js_runtime': self.runtime.name if self.runtime else None,
            'runtime_version': self.runtime.version if self.runtime else None,
            'error_code': code,
        }

    def finish(self, error=None):
        logger.warning('youtube_pot_provider %s', json.dumps(
            {'event': 'youtube_pot_provider', **self.safe_status(error)}, sort_keys=True))
