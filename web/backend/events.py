"""SSE over the service's existing bounded per-client buffer."""
import asyncio
import json
from queue import Empty


def encode_event(event):
    payload = event.to_dict()
    payload['error_message'] = 'Verification required' if event.error_category == 'LOGIN_REQUIRED' else ('Download failed' if event.error_message else None)
    # Filename is a logical name, never a server path.
    import ntpath
    payload['filename'] = ntpath.basename(payload['filename'] or '')
    return 'event: download\ndata: ' + json.dumps(payload, allow_nan=False) + '\n\n'


async def event_stream(request, service, subscription):
    try:
        while not subscription.closed:
            if await request.is_disconnected():
                break
            if subscription.resync_required:
                yield 'event: resync\ndata: {"refresh": "/api/downloads"}\n\n'
                # Flag is sticky; reconnect after fetching a snapshot.
                break
            try:
                yield encode_event(subscription.get_nowait())
            except Empty:
                yield ': keepalive\n\n'
                await asyncio.sleep(0.25)
    finally:
        service.unsubscribe(subscription)
