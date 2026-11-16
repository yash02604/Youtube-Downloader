"""Local development settings; no service is constructed during import."""
from dataclasses import dataclass
import os
from pathlib import Path


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
        return cls(download_root=Path(os.environ.get('DOWNLOADER_WEB_ROOT', '.web-data/downloads')),
                   state_root=Path(os.environ.get('DOWNLOADER_WEB_STATE', '.web-data/state')),
                   frontend_origins=tuple(x.strip() for x in os.environ.get('DOWNLOADER_WEB_ORIGINS', 'http://localhost:3000').split(',') if x.strip()))
