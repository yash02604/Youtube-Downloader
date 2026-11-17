"""Local EJS asset validation and a controlled JavaScript execution check.

This does not establish that a real YouTube player challenge can be solved.
yt-dlp's private provider API is deliberately isolated here and fails closed.
"""

import json


class _SilentLogger:
    def debug(self, *args, **kwargs):
        pass

    info = warning = error = debug


def _yt_dlp_api():
    from yt_dlp import YoutubeDL
    from yt_dlp.extractor.youtube import YoutubeIE
    from yt_dlp.extractor.youtube.jsc._director import initialize_jsc_director

    return YoutubeDL, YoutubeIE, initialize_jsc_director


def _deny_network(*args, **kwargs):
    raise RuntimeError("Network is disabled for the local EJS probe")


def probe_ejs_runtime(runtime):
    """Return safe readiness fields without network, downloads, or raw logs.

    Runtime execution checks a fixed arithmetic expression through the actual
    provider sandbox. Asset access applies yt-dlp's normal version/hash checks.
    Neither operation substitutes for an authorized real player challenge.
    """
    result = {
        "runtime_name": runtime.name,
        "runtime_version": runtime.version,
        "installed_solver_valid": False,
        "provider_available": False,
        "runtime_executes": False,
        "actual_challenge_solved": False,
        "status": "unavailable",
        "code": "probe_unavailable",
    }
    try:
        YoutubeDL, YoutubeIE, initialize_director = _yt_dlp_api()
        options = {
            "quiet": True, "no_warnings": True, "verbose": False,
            "logger": _SilentLogger(), "remote_components": [], "cachedir": False,
            **runtime.ytdlp_options,
        }
        with YoutubeDL(options) as ydl:
            ydl.urlopen = _deny_network
            director = initialize_director(YoutubeIE(ydl))
            provider = next((
                candidate for candidate in director.providers.values()
                if getattr(candidate, "JS_RUNTIME_NAME", None) == runtime.name
                and candidate.is_available()
            ), None)
            if provider is None:
                result["code"] = "provider_unavailable"
                return result
            result["provider_available"] = True
            result["code"] = "solver_assets_unavailable"
            if not provider._lib_script or not provider._core_script:
                return result
            result["installed_solver_valid"] = True
            result["code"] = "runtime_execution_failed"
            output = provider._run_js_runtime(
                'console.log(JSON.stringify({web_runtime_probe: 1 + 1}));')
            if json.loads(output) != {"web_runtime_probe": 2}:
                return result
            result["runtime_executes"] = True
            result["status"] = "ready_for_challenge_check"
            result["code"] = "local_assets_and_execution_verified"
    except Exception:
        # Exceptions can contain executable paths, script text, and URLs.
        # The stage code preserves actionable information without disclosing them.
        pass
    return result
