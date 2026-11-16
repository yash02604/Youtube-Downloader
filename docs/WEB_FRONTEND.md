# Web frontend foundation — Phase 5

## Design and architecture
Next.js App Router + TypeScript, dark-first CSS variables, restrained mint accent,
system fonts, two-column analysis/download workspace, stacked on mobile. The
visual reference is the generated full-screen concept; controls are implemented
as HTML, not a flattened image. Real server data replaces the sample concept
content. Missing thumbnails have a neutral placeholder. No invented sample jobs
are shipped in the actual application.

Browser / Next.js -> FastAPI -> DownloadService -> DownloadManager -> yt-dlp /
FFmpeg / Deno. No downloader implementation or new backend endpoints are added.
The test-only fixture is separate from production and never handles live media.

## Local startup
Use supported Node LTS (24.15+ in the 24.x line, or newer supported LTS). The
machine's Node 25 is outside the newest test tools' engine range; validation uses
a workspace-local Node 24 runtime. No system installation was changed.

Backend, from the repository root:

    python -m pip install -r web/backend/requirements-test.txt
    python -m uvicorn web.backend.app:create_app --factory --host 127.0.0.1 --port 8000 --timeout-graceful-shutdown 10

Frontend, from web/frontend:

    npm ci
    npm run dev

Visit http://localhost:3000. NEXT_PUBLIC_DOWNLOADER_API_URL defaults to
http://localhost:8000; .env.example documents it. Set it in .env.local before
starting/building to select another explicitly configured local backend. Public
variables are bundled by Next.js. Backend CORS must match the exact frontend
origin; the default is http://localhost:3000. Neither adapter starts the desktop.

## Interaction and connection model
Analysis has a loading state and duplicate-request guard. Editing the URL clears
the previous analysis. The backend validates links; client controls improve UX.
The preview shows title, channel, duration, resolutions and subtitle languages.
Quality values exactly match the API. Audio Only disables video controls. MP4
compatibility explicitly notes the possible quality tradeoff. Optional settings
are enumerated fields, not arbitrary backend configuration.

Creation returns a job ID, then that job is fetched and inserted from its server snapshot. Creation
does not imply completion. Actions are guarded per job; the UI waits for server
state, and conflict/error recovery requests a snapshot. Completed items have no
file-download button. The settings disclosure explains local scope only.

Initial connection checks health, loads snapshots and connects EventSource.
Native EventSource reconnection handles stream interruptions without aggressive
manual retry loops. Open/reopen refreshes the snapshot. Explicit resync closes
the old stream, fetches the snapshot and reconnects after a bounded delay.
Unavailable startup/resync retries use exponential delay capped at 15 seconds.
There is no Last-Event-ID replay. Snapshot requests are coalesced; events arriving
during a snapshot are buffered up to 128 then recovered through a new snapshot.
Unknown IDs trigger snapshot recovery. Malformed events are ignored.

Progress changes only the affected job object, with memoized rows and stable
action callbacks. There is no continuous snapshot polling while SSE is healthy.
Unmount closes EventSource and clears timers/aborts pending connection requests.
All error text is selected locally from HTTP status; raw server errors are not
rendered. Explicit null error fields clear old errors after retries.

## Significant files
- src/app: App Router shell, page, global theme tokens and responsive styles.
- src/components: Dashboard, AnalysisForm, memoized DownloadRow.
- src/hooks/use-downloads.ts: server snapshots, per-job actions and pending state.
- src/lib/api.ts: public API types, fixed endpoints and safe errors.
- src/lib/events.ts: EventSource, refresh/reconnect/resync and per-job projection.
- src/lib/format.ts: byte counts, speed and duration formatting.
- tests: mocked API/component/control/event tests; browser smoke in tests/browser.
- tests/fixtures/web_api.py at repository root: offline injected service over the
  existing production FastAPI factory, only for the browser smoke test.

## Validation commands
From web/frontend: npm run lint, npm run typecheck, npm test, npm run build.
For browser validation: npm run test:browser. The Windows default uses installed Microsoft Edge through Playwright in a fresh headless profile. Set DOWNLOADER_BROWSER_CHANNEL=chromium and install Playwright Chromium to use that channel instead. Set DOWNLOADER_TEST_PYTHON to the Python executable containing backend dependencies; the default is python.
The browser test starts both Next.js and a controlled FastAPI fixture at the
standard ports. Stop any real servers using ports 3000/8000 first; do not point
the browser smoke at a live personal download session. No live YouTube calls are
made. The fixture alone exposes /__test__ routes to publish progress/resync and
never becomes part of the production application.

From repository root: python -m unittest discover -s tests -v.
Backend tests require the optional backend dependencies. Windows async/local HTTP
fixtures require loopback networking allowed in the execution environment.

## Limitations
Local development API only; there is no public deployment guarantee. No web
playlist flow, completed-file serving, filesystem browser, cookie upload, proxy
configuration or CAPTCHA bypass exists. YouTube verification can still block
live analysis/downloads. No live YouTube compatibility claim is made. Desktop
installer and installed application were not rebuilt or manually validated.

The frontend uses currently supported backend quality modes rather than explicit
format-ID selection, which the API does not accept. Progress is best effort and
resync uses complete snapshots. The backend's unauthenticated local-development
classification is unchanged. Public hosting needs the separate security review
already described in WEB_BACKEND.md.


## Final validation and visual review
- Frontend unit/component tests: 26 passed, 0 failed, 0 skipped (4 files).
- Lint: 0 errors, 0 warnings. TypeScript: 0 errors.
- Production build: passed; /, /_not-found and /icon.svg prerendered.
- Real browser controlled smoke: 1 passed, 0 failed, 0 skipped.
- Python: 173 total, 172 passed, 0 failures, 0 errors, 1 expected Windows
  symlink-privilege skip. Compilation/import checks: 42 files passed.

Browser checks use production Next.js at localhost:3000 and the real FastAPI
factory with an injected offline service at localhost:8000. Tested: title/nonblank
page, service connected, existing snapshot, EventSource progress update,
pause/resume/cancel/remove, snapshot resync/reconnect, analysis, creation,
1440x1000 desktop and 390x844 mobile, no horizontal overflow, zero page/console
errors. This is automated real-browser smoke validation, not live YouTube proof.
The servers are stopped after the test; existing servers are never reused.

The full-screen visual concept and final browser screenshots were inspected with
view_image. Comparison points: charcoal/mint palette, header hierarchy, composer/
queue columns, quality control grouping, progress/actions and mobile stacking.
The final adjustment stretches desktop panels to equal height and raises small
job-detail type. Intentional differences: the required YouTube URL label replaces
the concept's generic Video URL label; public snapshots have file icons rather
than invented job thumbnails; missing preview thumbnails use a placeholder;
queue count/content reflect actual controlled state rather than example rows.
The main headline, explanatory text, headings and action copy match the concept.
No clipping or overlapping controls was found at the checked widths.

Concept: generated full-screen design reference (preview only).
Evidence: frontend-desktop.png and frontend-mobile.png, saved outside the
repository in the Codex visualization workspace.
Screenshots, temporary tooling and build artifacts are not committed.

Next.js/React are resolved from the current stable registry to 16.4.6/19.3.1.
TypeScript is constrained to the compatible 6.0 line. ESLint 9 is required by
current Next.js lint-plugin peer ranges; npm marks that release line deprecated.
Moving lint to ESLint 10 awaits compatible upstream plugins. No lint rule was
disabled to hide errors. No production dependency security certification is made.
The lockfile records the complete resolved installation.

Readiness: READY FOR LOCAL WEB BETA, based on the automated and real-browser
controlled checks above. Live YouTube, actual remote thumbnail loading, long
running real downloads, and installed desktop behavior remain unverified.
