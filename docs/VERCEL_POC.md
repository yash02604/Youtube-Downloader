# Vercel Services proof of concept

Run from branch `perf/shared-core-web-foundation`, with the Vercel project Root
Directory set to the repository root (`.`). Do not use the desktop `main.py`
as a web application. No change to the desktop install/start workflow is needed.

## Services and routing

The single root project configuration is `vercel.toml`:

| Service | Root | Framework | Entrypoint |
| --- | --- | --- | --- |
| frontend | `web/frontend/` | Next.js | Framework default |
| backend | `.` | FastAPI | `pyproject.toml` -> `web.backend.vercel_app:app` |

The backend adapter only calls the existing `create_app()` factory. Imports
resolve from the repository root, including `core/`, `download_manager.py` and
`video_analyzer.py`. No routes or downloader code are duplicated. Workers are
created by the existing lifespan startup, never by importing the adapter.

Top-level rewrites send both `/api` and `/api/(.*)` to the backend, followed by
a catch-all for the frontend. Service destinations preserve the original path;
FastAPI continues to receive `/api/health`, `/api/downloads` and `/api/events`.
Bare `/api` reaches FastAPI and returns its own 404 because no such API route
exists. There is no separate public backend domain.

Production frontend builds always use relative API URLs, including EventSource
at `/api/events`. A stale `NEXT_PUBLIC_DOWNLOADER_API_URL` cannot redirect a
production browser to localhost. Development builds retain the configurable URL
and its `http://localhost:8000` default. A standalone production Next.js server
therefore needs the same API reverse proxy if tested outside Vercel.

## Python dependencies and settings

Root `pyproject.toml` declares all shared runtime requirements plus FastAPI and
Uvicorn, where the backend's Python builder can discover them. It also declares
the explicit ASGI module via `tool.vercel.entrypoint`. Keep the shared runtime
requirements aligned with `requirements.txt`; the desktop command
`pip install -r requirements.txt` and the local backend command
`pip install -r web/backend/requirements.txt` remain unchanged.

The service explicitly selects `pyproject.toml` (declared-only mode). This
anchors dependency discovery at the root and disables filename-based fallback
to desktop `main.py`. Selecting the nested ASGI module directly instead caused
Vercel's dependency discovery to select `web/backend/requirements.txt`, whose
relative `-r` include failed its parser in the initial cloud preview build.

On Vercel (`VERCEL=1`), web storage defaults to the system temporary directory
under `downloader-web/`. `DOWNLOADER_WEB_ROOT` and `DOWNLOADER_WEB_STATE` still
override those defaults. Exact hosts and HTTPS origins are derived from
`VERCEL_URL`, `VERCEL_BRANCH_URL` and `VERCEL_PROJECT_PRODUCTION_URL`.
For additional aliases/custom domains, include their exact HTTPS origins in
`DOWNLOADER_WEB_ORIGINS`. No wildcard host or origin is enabled.

`.vercelignore` excludes local environments, build artifacts, credentials,
download state and media. The backend function additionally excludes frontend
sources/dependencies, test fixtures and documentation from its bundle.

## Validation and deployment boundaries

Use a Python environment with `web/backend/requirements-test.txt` installed:

    python -m unittest discover -s tests -v

From `web/frontend/`, with a supported Node.js version:

    npm test
    npm run lint
    npm run typecheck
    npm run build
    npm run test:browser

From the repository root, after verifying the linked Vercel project:

    vercel project inspect --non-interactive
    vercel build --yes

Verify the generated service configuration and preview responses to `/` and
`/api/health`. Source-level routing checks and local FastAPI/Next.js smoke tests
do not establish that Vercel's deployed routing works.

Local validation on 17 November 2026: Python suite ran 176 tests (175 passed,
one Windows symlink-capability skip); frontend unit/component tests passed
28/28; ESLint, TypeScript checking and the Next.js production build passed;
the existing Playwright browser smoke passed 1/1. The built Next.js `/` returned
HTTP 200 with the expected title. The Vercel-host FastAPI health test returned
HTTP 200, and the adapter import check started no workers or desktop modules.
Vercel CLI 65.16.0 compiled the TOML into the expected Services configuration,
but its Windows local build failed during builder package installation with
`EPERM` creating symlinks, before application compilation.

A disposable Linux container running the same CLI compiled Next.js and passed
its TypeScript stage, then exited with code 137 while collecting page data.
The cause of that termination was not confirmed. That local attempt did not
establish full Vercel build success or backend packaging.

The subsequent Git-triggered cloud preview for code commit
`7e0ca1ece6d002547d0ed9d0bd888658f7203bbb` built successfully and reached Ready.
Its build log confirms Python 3.12 and dependency installation from the root
`pyproject.toml`; the backend FastAPI function is approximately 31.55 MB.
Authenticated requests to the same preview hostname verified:

- `/`: HTTP 200 with the Next.js page title `Video Download Manager`.
- `/api/health`: HTTP 200 and `{"status":"ok","scope":"local-development"}`,
  including with the preview's exact HTTPS Origin header. The existing health
  response's scope label was retained unchanged.
- `/api`: FastAPI's JSON 404 (`{"detail":"Not Found"}`), confirming bare `/api`
  reaches the backend rather than the Next.js catch-all.

Preview: https://youtube-downloader-lc3p2kozy-yash02604s-projects.vercel.app

Deployment Protection remained enabled. The automation bypass credential
automatically created by `vercel curl` for these checks was revoked afterward.
No real media download or deployed SSE stream was tested.

This is a deployment proof of concept, not a production downloader host.
Temporary files and in-memory job state are per-instance and disposable.
Background download threads and SSE subscriptions cannot be relied on across
function termination, scaling, cold starts or maximum request durations. Media
processing additionally depends on FFmpeg/ffprobe and a supported JavaScript
runtime being available; this configuration does not provision those binaries.
YouTube reachability and verification behavior must be tested in the deployed
environment before claiming that real downloading works. The API has no user
authentication; retain Vercel Deployment Protection for this experiment.

References: [Services](https://vercel.com/docs/services),
[service configuration](https://vercel.com/docs/services/config-reference),
[service routing](https://vercel.com/docs/services/routing),
[Python runtime](https://vercel.com/docs/functions/runtimes/python).
