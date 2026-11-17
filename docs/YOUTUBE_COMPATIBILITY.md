# Vercel YouTube compatibility evidence

Branch: `perf/shared-core-web-foundation`. No merge to main. This work does not
change retries, media selection, concurrency, FFmpeg, lifecycle, storage, SSE or
the public authentication model. No personal browser session is accessed.

## Diagnostic baseline, 17 November 2026

Diagnostics were deployed before selecting a new yt-dlp runtime. Preview code
commit: `e870cf5ca488e86ea7758fbb7d2875d1bbe5c780`.

Canonical reported public URL:
https://www.youtube.com/watch?v=Auvd4NPXHnI

Health returned HTTP 200. Analysis returned HTTP 409 with code LOGIN_REQUIRED.
The safe runtime record proved:

- yt-dlp 2026.10.26; yt-dlp-ejs 0.8.0 visible to yt-dlp.
- Linux / Python 3.12.16.
- No discovered supported JavaScript runtime and no enabled yt-dlp runtime.
- Explicit BOT_VERIFICATION and NO_SUPPORTED_JS_RUNTIME signals.
- No loaded PO-token provider plugin and no observed player-client request.

These are two distinct observations. A missing runtime is a configuration
defect; YouTube's bot-verification response does not itself prove an IP block,
a PO-token requirement or that adding a runtime will make the request succeed.

## Web compatibility profile

Web analysis prefers validated Deno >=2.3, then Node >=22. The root Vercel
manifest adds official `deno==2.9.8` on Linux only. Desktop `requirements.txt`,
local backend requirements and the desktop Deno-first path remain unchanged.
The package's PyPI attestations identify `denoland/deno_pypi` as its publisher.
No solver scripts or runtime binaries are downloaded at request time.

The Python builder excludes wheel scripts outside site-packages. The
`tool.vercel.scripts.vercel-build` hook therefore copies the installed official
wheel executable into `web/backend/_runtime/deno`, retaining executable mode.
That generated directory is ignored by Git and included by the backend
application-file bundle scan. Web discovery checks the bundled location.

The explicit web yt-dlp profile disables remote components and cache, uses a
safe logger, and passes the validated runtime explicitly. The normal yt-dlp
player-client policy is retained. No cookie options, proxy, static token or
provider client override is supplied.

`probe_ejs_runtime` isolates the private yt-dlp provider API. It validates local
core/library assets using yt-dlp's own version/hash checks and executes fixed
arithmetic through the selected provider's sandbox with network disabled. This
proves local assets and execution capability; `actual_challenge_solved` remains
false because no real player challenge is supplied to the offline probe.

## Safe error contract

Analysis errors return allowlisted `{code, message}` JSON. Raw exceptions,
URLs/query strings, headers, cookies, paths and token values are never copied
to logs or responses. Diagnostics distinguish an explicit bot/sign-in demand,
explicit PO-token demand, missing JS/EJS evidence, HTTP 403/429 and unknown
failure. Unknown failures stay ANALYSIS_FAILED; 403 alone does not imply PO
tokens. The frontend uses only recognized analysis codes and fixed messages.
Lifecycle 409 responses remain generic conflicts.

## Provider and live-test gates

No PO-token provider has been installed. A provider is considered only after
runtime/EJS readiness and explicit attestation evidence. Bot verification alone
does not satisfy that gate. No permanent provider daemon is launched in a
Function. No personal cookies, CAPTCHA solving, identity spoofing or proxy
rotation is implemented.

Live checks use a short-lived project OIDC token scoped to the exact preview;
it is never printed, committed or sent to YouTube. Deployment Protection stays
enabled. A tiny 360p download is gated on repeatable successful public analysis.

## Runtime-enabled deployment and final live results

Code commit: `2ab082b49ec7214a582ddbcb04a7ea144000aa6a`.
Preview: https://youtube-downloader-mh1nvwzs3-yash02604s-projects.vercel.app
Deployment: `dpl_HKcut8FyNwAZZTHRuqCqqpcWH1ZM`, Ready.

The cloud build executed `python -m web.backend.package_runtime`, confirmed
the pinned executable was packaged, and completed successfully. Live checks on
17 November 2026 used one request per canonical video without browser cookies:

| Check | HTTP | Result |
| --- | --- | --- |
| GET /api/health | 200 | Healthy |
| aqz-KE-bpKQ (normal public video) | 409 | LOGIN_REQUIRED |
| jNQXAC9IVRw (short public video) | 409 | LOGIN_REQUIRED |
| Auvd4NPXHnI (reported failing video) | 409 | LOGIN_REQUIRED |

All three safe runtime records agree:

- yt-dlp 2026.10.26; yt-dlp-ejs 0.8.0, visible to yt-dlp.
- Linux, Python 3.12.16; Deno 2.9.8 discovered and explicitly enabled.
- Installed solver assets valid; JS provider available; sandbox execution
  successful. The controlled probe does not claim a solved real player
  challenge (`actual_challenge_solved=false`).
- The explicit upstream failure is BOT_VERIFICATION; exception type DownloadError.
- NO_SUPPORTED_JS_RUNTIME is gone. No PO-token requirement was observed.
- yt-dlp's default player-client policy remains configured; no player-client
  API request was observed before rejection. No provider plugin is loaded.

Proven root cause of LOGIN_REQUIRED: YouTube explicitly requested bot/sign-in
verification for these server requests. Missing JS runtime was a separate
proven deployment defect and is fixed. It was not sufficient to remove the
verification response. These results do not identify the precise IP/reputation
mechanism or prove that a PO-token provider would resolve it.

Readiness: **YOUTUBE ANALYSIS STILL BLOCKED**. No provider was added because its
evidence gate was not met. No tiny download was attempted because repeatable
analysis success was not achieved. No repeated blocked-video retries were made.

## Final automated validation

- Python: 199 run, 198 passed, 0 failed, 1 skipped (Windows symlink capability).
- Frontend: 42 passed, 0 failed, across 5 files.
- Existing Playwright browser smoke: 1 passed, 0 failed.
- ESLint, TypeScript checking and Next.js production build: passed.
- Independent diagnostic/runtime reviews found no remaining blockers to the
  bounded implementation; their local capability claims were then confirmed
  on Linux by the deployed probe. Real challenge solving remains unverified.

Implementation commits:
`e870cf5ca488e86ea7758fbb7d2875d1bbe5c780` (safe diagnostics/error contract),
`2ab082b49ec7214a582ddbcb04a7ea144000aa6a` (packaged web runtime/profile).

References: [yt-dlp EJS](https://github.com/yt-dlp/yt-dlp/wiki/EJS),
[PO Token Guide](https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide),
[Deno Python package](https://pypi.org/project/deno/),
[official Deno package source](https://github.com/denoland/deno_pypi).
