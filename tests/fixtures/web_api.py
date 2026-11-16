"""Offline browser-test server. Never imported by the production app factory."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import copy
import tempfile
from fastapi import Request
import uvicorn
from core.event_bus import EventBus
from core.events import DownloadEvent
from web.backend.app import create_app
from web.backend.settings import Settings


class FixtureService:
    def __init__(self):
        self.jobs = {'seed': {'id': 'seed', 'title': 'A walk through the mountains', 'filename': 'mountains.mkv', 'status': 'Downloading', 'progress': 25, 'downloaded': 25*1024*1024, 'file_size': 100*1024*1024, 'speed': 1024*1024, 'eta': 75, 'profile': 'Best Available'}}
        self.bus = EventBus()

    def list_downloads(self): return copy.deepcopy(list(self.jobs.values()))
    def get_download(self, job_id): return copy.deepcopy(self.jobs.get(job_id))
    def add_download(self, url, path, **options):
        job_id = 'added-' + str(len(self.jobs))
        self.jobs[job_id] = {'id': job_id, 'title': 'A walk through the mountains', 'filename':'mountains.mkv', 'status': 'Queued', 'progress': 0, 'profile': options['video_quality']}
        return job_id
    def update(self, job_id, status):
        self.jobs[job_id]['status'] = status
        self.bus.publish(DownloadEvent(job_id, status.lower(), status=status, progress=self.jobs[job_id]['progress']))
    def pause(self, job_id): self.update(job_id, 'Paused')
    def resume(self, job_id): self.update(job_id, 'Downloading')
    def cancel(self, job_id): self.update(job_id, 'Cancelled')
    def remove(self, job_id): return self.jobs.pop(job_id, None) is not None
    def subscribe(self, capacity=128): return self.bus.subscribe(capacity)
    def unsubscribe(self, subscription): self.bus.unsubscribe(subscription)
    def shutdown(self, timeout): self.bus.close(); return True


def main():
    service = FixtureService()
    with tempfile.TemporaryDirectory() as directory:
        settings = Settings(download_root=Path(directory)/'downloads', state_root=Path(directory)/'state')
        def analyze(url):
            return {'title': 'A walk through the mountains', 'channel': 'Field Notes', 'duration': 754, 'resolutions': [2160,1080,720], 'formats': [], 'subtitles':['en'], 'thumbnail': None}
        app = create_app(settings, service, analyze)
        @app.post('/__test__/progress')
        def progress():
            service.jobs['seed'].update(progress=62, downloaded=62*1024*1024, speed=4.2*1024*1024, eta=18)
            service.bus.publish(DownloadEvent('seed','progress',status=service.jobs['seed']['status'],progress=62,downloaded_bytes=62*1024*1024,total_bytes=100*1024*1024,speed=4.2*1024*1024,eta=18))
            return {'ok':True}
        @app.post('/__test__/resync')
        def resync():
            service.jobs['seed']['progress'] = 80
            for subscription in tuple(service.bus._subscriptions):
                subscription.resync_required = True
            return {'ok': True}
        @app.get('/__test__/subscriptions')
        def subscriptions(): return {'count':len(service.bus._subscriptions)}
        uvicorn.run(app, host='127.0.0.1', port=8000, log_level='warning', timeout_graceful_shutdown=2)

if __name__ == '__main__': main()
