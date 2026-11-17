"""Local FastAPI adapter. Importing this module creates no downloader/service."""
from contextlib import asynccontextmanager
from functools import partial
import logging
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.background import BackgroundTask
from core.service import DownloadService
from core.destination_policy import RootedDestinationPolicy
from core.manager import VideoAnalyzer, DownloadManager
from .settings import Settings
from .schemas import AnalyzeRequest, AnalyzeResponse, DownloadRequest, public_metadata, public_snapshot
from .events import event_stream
from .youtube_analysis import analyze_public_youtube, classify_error, MESSAGES

logger = logging.getLogger(__name__)


def create_app(settings=None, service=None, analyzer=None):
    settings = settings or Settings.from_env()
    if '*' in settings.frontend_origins:
        raise ValueError('Explicit frontend origins are required')
    policy = RootedDestinationPolicy(settings.download_root)
    analyze = analyzer or analyze_public_youtube

    @asynccontextmanager
    async def lifespan(app):
        if service is None:
            settings.download_root.mkdir(parents=True, exist_ok=True)
            settings.state_root.mkdir(parents=True, exist_ok=True)
            instance = DownloadService(destination_policy=policy,
                state_file=settings.state_root / 'download_state.json',
                history_file=settings.state_root / 'download_history.json',
                allow_browser_session=False)
        else:
            instance = service
        app.state.service = instance
        try:
            # Refuse unsafe legacy jobs before exposing a resume endpoint.
            for info in instance.list_downloads():
                destination = info.get('destination')
                if destination and not Path(destination).resolve().is_relative_to(policy.root):
                    raise RuntimeError('Stored destination is outside the web root')
                source = info.get('source_url') or info.get('url')
                if source:
                    AnalyzeRequest(url=source)
            yield
        finally:
            app.state.shutdown_complete = await run_in_threadpool(instance.shutdown, settings.shutdown_timeout)
            if not app.state.shutdown_complete:
                logger.warning('Web service shutdown timed out or failed; cleanup may continue')

    app = FastAPI(title='YouTube Downloader Local API', lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.frontend_origins),
                       allow_credentials=False, allow_methods=['GET', 'POST', 'DELETE'], allow_headers=['Content-Type'])

    @app.middleware('http')
    async def check_origin(request, call_next):
        origin = request.headers.get('origin')
        if origin and origin not in settings.frontend_origins:
            return JSONResponse({'detail': 'Origin is not allowed'}, status_code=403)
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, _exc):
        return JSONResponse({'detail': 'Invalid request fields'}, status_code=400)

    @app.exception_handler(Exception)
    async def internal_error(_request, exc):
        logger.error('Backend operation failed (%s)', type(exc).__name__)
        return JSONResponse({'detail': 'Internal server error'}, status_code=500)

    def invoke(operation, *args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except HTTPException:
            raise
        except ValueError:
            raise HTTPException(400, 'Invalid request') from None
        except RuntimeError:
            raise HTTPException(503, 'Service unavailable') from None

    def current(request, download_id):
        info = invoke(request.app.state.service.get_download, download_id)
        if info is None:
            raise HTTPException(404, 'Download not found')
        return info

    @app.get('/api/health')
    def health():
        return {'status': 'ok', 'scope': 'local-development'}

    @app.post('/api/analyze', response_model=AnalyzeResponse)
    async def analyze_url(body: AnalyzeRequest):
        try:
            info = await run_in_threadpool(analyze, body.url)
        except Exception as exc:
            code, _reason = classify_error(exc)
            return JSONResponse({'code': code, 'message': MESSAGES[code]},
                                status_code=409 if code == 'LOGIN_REQUIRED' else 502)
        if not info:
            return JSONResponse({'code': 'ANALYSIS_FAILED', 'message': MESSAGES['ANALYSIS_FAILED']}, status_code=404)
        return public_metadata(info)

    @app.post('/api/downloads', status_code=201)
    def add_download(body: DownloadRequest, request: Request):
        # Recheck the real configured root even for injected test services.
        invoke(policy.resolve, body.destination)
        options = body.model_dump(exclude={'url', 'destination', 'filename'})
        job_id = invoke(request.app.state.service.add_download, body.url,
                        body.destination, custom_filename=body.filename, **options)
        return {'id': job_id}

    @app.get('/api/downloads')
    def downloads(request: Request):
        return [public_snapshot(info) for info in invoke(request.app.state.service.list_downloads)]

    @app.get('/api/downloads/{download_id}')
    def download(download_id: str, request: Request):
        return public_snapshot(current(request, download_id))

    def command(request, download_id, operation):
        before = current(request, download_id)
        if operation == 'resume' and before.get('destination'):
            try:
                relative = Path(before['destination']).resolve().relative_to(policy.root)
                policy.resolve(relative.as_posix())
            except ValueError:
                raise HTTPException(400, 'Stored destination is outside the web root') from None
        method = getattr(request.app.state.service, operation)
        result = invoke(method, download_id)
        if operation == 'remove':
            if not result:
                raise HTTPException(409, 'Download cannot be removed in its current state')
            return {'id': download_id, 'removed': True}
        after = current(request, download_id)
        if after['status'] == before['status']:
            raise HTTPException(409, 'Operation did not change the current state')
        return public_snapshot(after)

    @app.post('/api/downloads/{download_id}/pause')
    def pause(download_id: str, request: Request):
        return command(request, download_id, 'pause')

    @app.post('/api/downloads/{download_id}/resume')
    def resume(download_id: str, request: Request):
        return command(request, download_id, 'resume')

    @app.post('/api/downloads/{download_id}/cancel')
    def cancel(download_id: str, request: Request):
        return command(request, download_id, 'cancel')

    @app.delete('/api/downloads/{download_id}')
    def remove(download_id: str, request: Request):
        return command(request, download_id, 'remove')

    @app.get('/api/events')
    async def events(request: Request):
        instance = request.app.state.service
        subscription = invoke(instance.subscribe, settings.event_capacity)
        return StreamingResponse(event_stream(request, instance, subscription),
            media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
            background=BackgroundTask(instance.unsubscribe, subscription))

    return app
