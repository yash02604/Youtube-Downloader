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
server assets and their license. It installs the locked upstream production
dependencies and validates script imports/version both before and after
relocation without network permissions or token generation. Native canvas
dependencies are bundled; compiler caches are removed after offline smoke
checks and a writable runtime cache lives in temporary storage.

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
Vercel's Python builder also excludes node_modules by default. The backend
function explicitly includes the generated provider production node_modules
tree (including hidden .deno files) and upstream package-lock.json. Runtime
asset diagnostics separately check the launcher, script and modules directory.
Measured Linux builds showed that Deno's production and production-entrypoint
installs both retained development packages in the isolated store (including
SWC, TypeScript and format/lint tools). The build therefore uses the upstream
package-lock.json with npm ci --omit=dev to install only production packages.
The script still runs under Deno 2.9.8 in documented manual-node_modules mode;
Node/npm are build tools, not the deployed provider runtime. Build-only compiler
caches are not deployed. Runtime cannot download missing module code.

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

## First ready provider probe

Code 784b2f5f4dd12062c3f34719ad43eca80201ff18 built and deployed successfully.
Production modules measured 79,244,259 bytes (versus 209,148,023 with Deno's
isolated install); packaged Deno measured 96,055,992 bytes. Both offline startup
checks passed. GET health returned 200. The normal public video aqz-KE-bpKQ
returned 409 LOGIN_REQUIRED. Runtime diagnostics proved all assets available,
provider_ready=true, client mweb observed, and BOT_VERIFICATION before any token
request: provider_reached=false, po_token_attempted=false, po_token_generated=false.

This triggered only the README's documented bounded client fallback:
mweb,tv,web_safari. This is a web-only profile change shared by analysis and
downloads, not a global desktop setting or random player-client cycle.

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

## Final live results: 17 November 2026

**PROVIDER COULD NOT BE INVOKED ON VERCEL**

Tested code: `a374efd68a0107b6f7d049eed4f2d4f690977fd3`.
Cloud deployment: `dpl_EYPgzktTfRya22GeSPwuY5JTWgXR`, Ready.
[Protected feature preview](https://youtube-downloader-4yg1r7i6m-yash02604s-projects.vercel.app).
Cloud build completed successfully; the relocated script/version smoke checks
passed. Every live probe also received HTTP 200 from `/api/health`.

The final shared web player policy was `mweb,tv,web_safari`, exactly the
upstream-documented fallback. Safe extraction logs observed `mweb` and `tv`;
the configured policy does not prove every configured client was executed.
Requests used canonical public-video URLs and were run sequentially, checking
the provider counters after each request before proceeding.

| Video | Analyze HTTP | Metadata | Provider reached | Token attempted | Token generated | Final code |
|---|---|---|---|---|---|---|
| aqz-KE-bpKQ | 409 | Failed | No | No | No | LOGIN_REQUIRED |
| jNQXAC9IVRw | 409 | Failed | No | No | No | LOGIN_REQUIRED |
| Auvd4NPXHnI | 409 | Failed | No | No | No | LOGIN_REQUIRED |

All three records reported `provider_ready=true`, plugin version 2.0.2,
all script assets available, Deno 2.9.8, and
`provider_result=PO_PROVIDER_NOT_REACHED` with `reason=BOT_VERIFICATION`.
The EJS assets and sandbox probe passed, but did not solve a live challenge.
The script startup check ran successfully; the classification above refers
specifically to the token-generation operation, which YouTube rejection
prevented yt-dlp from reaching. This is not evidence of token-generation
success or a provider generation failure.

### Small download

Not attempted: none of the three metadata analyses succeeded, so the explicit
analysis gate for a tiny 360p download was not met. Media retrieval and file
delivery on Vercel remain unverified.

### Validation

| Check | Result |
|---|---|
| Complete Python unittest suite | 227 run: 226 passed, 1 skipped, 0 failures |
| Compilation and imports | 33 root/core/backend source files compiled; backend app, profile and packager imports passed |
| Complete frontend Vitest suite | 48 passed, 5 test files, 0 failures |
| Existing Playwright browser suite | 1 passed |
| Frontend ESLint | Passed |
| TypeScript checking | Passed |
| Frontend production build | Passed |
| Final Vercel cloud build | Passed, Ready |

The Python skip is the existing Windows symlink-permission case. Frontend,
browser, lint, TypeScript and local production-build checks were completed
during this implementation; they were not repeatedly rerun for subsequent
backend packaging corrections. The final full Python run includes the bounded
fallback and packaging regression checks. No desktop launcher, quality logic,
FFmpeg behavior, concurrency, CAPTCHA behavior or main-branch merge was changed.

### Implementation commits

- `83036ba6cd3595530d31c750117518a759b52abc`: provider integration and focused tests.
- `a1e95a4c7e0c612c90143c7f20138323f79abc25`: production assets and duplicate-cache pruning.
- `3bb5031cdf647fa3c622b4b108c72e7e14270726`: writable temporary provider cache.
- `8543a2e1637bce10e48c7419df75d4ec45b317d9`: explicit function asset inclusion.
- `527a7bcf100319fb63f9f16185411649e2d71b27`: service schema correction.
- `79ea7b3393044a0400c21eb7cc270d464c40e62a`: artifact size measurements.
- `f2e60cf8efc1b28002ddb1a62d98e390d1f22c3d`: runtime dependency graph and cache omission.
- `784b2f5f4dd12062c3f34719ad43eca80201ff18`: locked npm production installation; first Ready provider build.
- `a374efd68a0107b6f7d049eed4f2d4f690977fd3`: documented client fallback; tested final code.

The independent README update `fc4a21e4f0232c9798ed311a09c5e477592211c9`
was preserved. All implementation commits were pushed to
`perf/shared-core-web-foundation` without force-pushing or merging into main.

### Next step

Stop this bounded provider/client experiment. Decide where yt-dlp execution
should run if public-video retrieval is still required; the current evidence
shows Vercel-origin YouTube verification rejects extraction before PO-token
generation. Do not claim that a generated token failed, and do not introduce
cookies, proxies, CAPTCHA automation or additional client cycling. An
architectural change requires a separate decision.

References:
[upstream 2.0.2 README](https://github.com/Brainicism/bgutil-ytdlp-pot-provider/tree/2.0.2),
[script implementation](https://github.com/Brainicism/bgutil-ytdlp-pot-provider/blob/2.0.2/plugin/yt_dlp_plugins/extractor/getpot_bgutil_script.py),
[yt-dlp PO Token Guide](https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide).
