"""Local and Vercel web settings; no service is constructed during import."""
from dataclasses import dataclass
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    download_root: Path = Path('.web-data/downloads')
    state_root: Path = Path('.web-data/state')
    frontend_origins: tuple[str, ...] = ('http://localhost:3000',)
    allowed_hosts: tuple[str, ...] = ('localhost', '127.0.0.1')
    shutdown_timeout: float = 5.0
    event_capacity: int = 128

    @classmethod
    def from_env(cls):
        vercel = os.environ.get('VERCEL') == '1'
        storage = Path(tempfile.gettempdir()) / 'downloader-web' if vercel else Path('.web-data')
        origins = tuple(x.strip() for x in os.environ.get(
            'DOWNLOADER_WEB_ORIGINS', 'http://localhost:3000').split(',') if x.strip())
        hosts = ('localhost', '127.0.0.1')
        if vercel:
            deployment_origins = tuple('https://' + os.environ[key] for key in (
                'VERCEL_URL', 'VERCEL_BRANCH_URL', 'VERCEL_PROJECT_PRODUCTION_URL') if os.environ.get(key))
            origins = tuple(dict.fromkeys(origins + deployment_origins))
            hosts += tuple(dict.fromkeys(urlsplit(origin).hostname for origin in origins
                                        if urlsplit(origin).hostname))
        return cls(download_root=Path(os.environ.get('DOWNLOADER_WEB_ROOT', str(storage / 'downloads'))),
                   state_root=Path(os.environ.get('DOWNLOADER_WEB_STATE', str(storage / 'state'))),
                   frontend_origins=origins, allowed_hosts=hosts)
