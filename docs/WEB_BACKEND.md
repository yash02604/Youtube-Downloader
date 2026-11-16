# Phase 4: local FastAPI adapter

## Start and test
Backend dependencies are optional and separate from desktop requirements:

    python -m pip install -r web/backend/requirements-test.txt
    python -m uvicorn web.backend.app:create_app --factory --host 127.0.0.1 --port 8000 --timeout-graceful-shutdown 10

Run from the repository root, with a single server process. /docs and
/openapi.json describe the API. Desktop startup does not start this server.
No service/workers are created by importing web.backend.app or create_app().
The service is constructed during lifespan startup, or injected for tests.

Default downloads: .web-data/downloads. Default state/history: .web-data/state.
These generated directories are excluded from Git. Configure DOWNLOADER_WEB_ROOT
and DOWNLOADER_WEB_STATE to dedicated application-owned directories for retained
storage. Do not share them with the desktop app or multiple server processes.
DOWNLOADER_WEB_ORIGINS accepts comma-separated explicit frontend origins; default
http://localhost:3000. Wildcard origins are rejected and credentials are disabled.
Unknown Origin headers are rejected. Trusted Host defaults: localhost, 127.0.0.1.
Settings may be supplied to the factory to explicitly change allowed_hosts.
Changing --host is intentional exposure, not sufficient public deployment setup.

## Scope and ownership
Local development API only. Public deployment requires a separate authentication
and security review, TLS, request/job limits and URL/extractor network policies.
No account system, frontend, file serving, directory mount, cookie upload,
CAPTCHA bypass or proxy rotation was introduced.

Web client -> FastAPI adapter -> DownloadService -> existing DownloadManager ->
yt-dlp / FFmpeg / Deno. REST methods delegate to the same service; no engine,
quality selector, retries, state store or event queue is duplicated. Core imports
remain free of FastAPI and Tkinter. Existing engine defaults are unchanged.

A backend-only allow_browser_session=False capability disables browser cookie
loading for downloads; analysis uses the same explicit opt-out. Desktop defaults
retain the existing opt-in behavior. Tool paths remain owner configuration, never
API request inputs. The backend does not mutate global desktop settings.

## Endpoints
- GET /api/health: minimal local-development health.
- POST /api/analyze: inspect metadata via the existing analyzer on a worker thread.
- POST /api/downloads: create a job (201, {"id": "..."}).
- GET /api/downloads: sanitized state snapshots.
- GET /api/downloads/{id}: sanitized snapshot, or 404.
- POST /api/downloads/{id}/pause
- POST /api/downloads/{id}/resume
- POST /api/downloads/{id}/cancel
- DELETE /api/downloads/{id}: follows service removal rules; media is not served.
- GET /api/events: bounded per-client Server-Sent Events.

Example create body (use a public YouTube video identifier):

    {"url":"https://www.youtube.com/watch?v=tRoYSagYP7U",
     "destination":"videos","video_quality":"Best Available"}

Phase 4 deliberately supports single YouTube video URLs only, canonicalized to a
watch URL. Shorts/live/embed/youtu.be video links normalize to that form. Radio
and playlist query parameters are stripped. Other hosts and arbitrary HTTP/file
URLs are rejected, reducing network admission scope for this first adapter.
Desktop platform and playlist features remain unchanged. Playlist/batch API work
is deferred. Analysis returns title/channel/uploader/duration, safe thumbnail URL,
resolutions, typed format summaries, subtitles and automatic subtitle languages.
It never returns raw extractor dictionaries, request headers or playback URLs.

Request fields are explicitly enumerated with extra keys forbidden. Accepted
creation options: filename, relative destination, download_type, video_quality,
audio_quality, subtitle_languages, download_subtitles, embed_subtitles,
save_thumbnail, embed_thumbnail, embed_metadata. Quality names use the existing
selector choices. Language tags are bounded and validated. No executable paths,
cookies, config paths, shell options or yt-dlp option dictionaries are accepted.

## Error and security boundary
400: invalid request/path/schema. 404: unknown job/metadata. 409: lifecycle no-op
or removal conflict, or metadata verification required. 503: service shutdown or
subscription capacity unavailable. 502: upstream analysis failure. 500: generic
internal error. Responses omit raw exceptions, traces, absolute output paths,
source/playback URLs and raw download errors. Runtime errors in snapshots/events
are replaced with safe text; LOGIN_REQUIRED is represented as verification needed.
Metadata fields and snapshots are projected explicitly, not copied wholesale.
The internal error log includes exception class only, never exception contents.

Remote folders use RootedDestinationPolicy and are revalidated on creation and
resume. Traversal, encoded traversal, drive-relative/absolute/UNC paths, alternate
streams and Windows normalization tricks are rejected. Startup refuses restored
entries outside the configured web root or with unsupported URLs. Existing engine
final-output checks remain intact. OS permissions must protect root and storage
against symlink/junction replacement and tampering; this is not a filesystem
sandbox against local administrators. The root may not be safely shared among
untrusted local writers. Default bind is loopback in the documented command.

## SSE and resynchronization
SSE was chosen because events flow one way from service to clients. Each client
uses one existing bounded subscription (128 events by default, 64-client service
limit). No asyncio event queue is added. The stream polls without blocking the
async loop. Progress coalesces in the service; lifecycle events displace progress.
On lifecycle overflow the stream sends:

    event: resync
    data: {"refresh": "/api/downloads"}

and closes. The client must fetch /api/downloads, rebuild state and reconnect.
Delivery is best-effort, not replayable/lossless. There are no Last-Event-ID replay
guarantees. Disconnect/cancellation and response cleanup unsubscribe idempotently.
Idle frames are SSE comments. Event error messages are sanitized like snapshots.

## Shutdown
FastAPI lifespan calls service.shutdown(5) in a worker thread. False is recorded
as app.state.shutdown_complete=False and logged; cleanup may continue in the
existing coordinator. No workers are forcibly terminated or marked completed.
The documented Uvicorn outer graceful timeout is 10 seconds so a connected SSE
client cannot indefinitely postpone lifespan teardown. Core's bounded timeout
contract and existing persistence implementation remain authoritative.

Official lifecycle reference: https://fastapi.tiangolo.com/advanced/events/ .

## Validation and remaining work
Offline TestClient tests cover REST, input policies, safe output/error mapping,
verification, service shutdown, import boundaries, factory behavior, browser
session opt-out, streaming, coalescing, resync and disconnect cleanup. Existing
desktop/core/HTTP/quality/lifecycle tests are preserved. Local Windows async tests
need loopback networking: restricted execution stalled while creating asyncio's
socket pair; unrestricted local-fixture validation succeeds.

No live YouTube download, installed desktop validation, browser EventSource test,
external server startup or file delivery was performed. The upstream TestClient
currently emits a deprecation warning for httpx; tests still pass. Dependencies
remain isolated in backend requirements.

Frontend readiness: READY FOR WEB FRONTEND FOUNDATION for this local single-video
scope. Before broader use: manual browser/SSE smoke test, typed client contracts,
metadata request limits/timeouts, authentication/security review for any remote
exposure, playlist/batch support, and narrowly authorized completed-file serving.
The earlier YouTube bot-verification issue is not solved or bypassed here.

## Final results and files changed
Backend: 21 passed, zero failures/errors/skips. Full suite: 172 passed,
zero failures/errors, one skipped (Windows symlink privileges), 173 total.
Compilation and import checks passed for 41 Python files. No automatic regression
was found. No live download compatibility claim is made.

Modified: .gitignore, download_manager.py, video_analyzer.py.
Added: web/__init__.py, web/backend/__init__.py, web/backend/app.py,
web/backend/settings.py, web/backend/schemas.py, web/backend/events.py,
web/backend/requirements.txt, web/backend/requirements-test.txt,
tests/test_backend.py, docs/PHASE4_PLAN.md, docs/WEB_BACKEND.md.
Deleted: none.
