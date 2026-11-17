# Vercel bgutil script-provider experiment

Branch: `perf/shared-core-web-foundation`; no main merge or desktop profile change.
This phase is explicitly authorized after the earlier runtime-only experiment.
No Windows-versus-Vercel comparison is part of this work.

## Selected upstream implementation

Latest stable GitHub release and PyPI plugin checked on 17 November 2026:
**bgutil-ytdlp-pot-provider 2.0.2** (released 7 October 2026).
Upstream's base requirement is yt-dlp >=2025.05.22; the installed plugin imports
and registers with the tested yt-dlp 2026.10.26 API. The script implementation
requires Deno >=2.4.3 (stricter than the README's generic runtime minimum), or
Node >=22. This experiment uses the already packaged Deno 2.9.8.

Pinned source commit: `26475e9d3665b972c4aeb83b4dbaafcf64b88438`.
Source archive SHA256:
`a8144eab6a4f38f320063688531f461afe18c0a58be2dd3152b780f4851e954d`.

The Linux Vercel manifest installs the matching Python plugin. The existing
build hook fetches that immutable archive, verifies its hash and extracts only
server assets and their license. It installs frozen upstream dependencies and
validates script imports/version both before and after relocation without
network permissions or token generation. Native canvas dependencies and Deno
caches are bundled; final Linux cloud build/runtime validation is required.

The first Linux build passed provider import/version validation but exceeded
the builder's 225 MB function limit (321.91 MB after optimization). The bounded
size fix installs production dependencies only, verifies dependency symlinks
stay inside bundled node_modules, and removes only Deno's duplicate global npm
cache. All production/transitive/optional/native assets remain intact. Both
offline smoke checks and the runtime launcher force manual node_modules mode.
The runtime uses a writable temporary Deno cache; the absence of an empty
build-time cache directory is not treated as a missing provider asset. All
module resolution remains local/manual and cached-only, so this does not allow
request-time dependency downloads.

## One profile for analysis and downloads

Vercel alone opts into `WebYouTubeProfile`: Deno, script mode, and initially
the `mweb` player client. `youtubepot-bgutilscript:server_home` points at the
packaged server. Only `bgutil:script-deno` remains in that extractor instance's
provider director. No HTTP provider server is started or contacted for tokens.

The analyzer passes profile options and setup to the existing VideoAnalyzer.
The web DownloadService supplies the same profile factory to DownloadManager.
Desktop factories default to None; format selection, concurrency, retries,
FFmpeg, pause/resume and output handling retain their existing behavior.

The private Deno launcher forces cached-only/frozen code resolution and
suppresses inherited subprocess stderr, which upstream errors can populate
with request details. Token-generation subprocesses receive only required
runtime/cache variables, not project OIDC tokens, API credentials or proxies.
Provider cache files use ephemeral temporary storage, never frontend assets.
There are no personal cookies, user account credentials, CAPTCHA automation,
identity spoofing, proxy rotation or static PO tokens.

## Diagnostic semantics and stop gates

Provider readiness means the plugin/version, local assets and script startup
were validated. It is distinct from token generation. Instance-local wrapping
of the plugin's actual request method records attempted/failed/validated-return
booleans and never retains a token value. Returned metadata is filtered by the
existing public API schema. Unknown raw logger output is discarded.

The report distinguishes PO_PROVIDER_NOT_REACHED from
PO_TOKEN_GENERATED_BUT_LOGIN_REQUIRED. A selected client rejected before any
token request is not reported as a token-generation failure. Only the current
README's documented `mweb,tv,web_safari` fallback may be tried if mweb is rejected
before provider invocation. No random client cycling is permitted.

If at least two public analyses succeed, stop tuning and test one tiny 360p job
without optional subtitles/thumbnail/metadata processing. If generated tokens
still yield LOGIN_REQUIRED, stop compatibility workarounds and report the
server-verification boundary. Persistent file delivery remains out of scope.

References:
[upstream 2.0.2 README](https://github.com/Brainicism/bgutil-ytdlp-pot-provider/tree/2.0.2),
[script implementation](https://github.com/Brainicism/bgutil-ytdlp-pot-provider/blob/2.0.2/plugin/yt_dlp_plugins/extractor/getpot_bgutil_script.py),
[yt-dlp PO Token Guide](https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide).
