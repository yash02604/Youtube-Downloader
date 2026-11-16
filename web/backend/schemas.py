"""Explicit request fields and public output projection."""
import ntpath
import re
from typing import Literal
from urllib.parse import urlparse, parse_qs, unquote, urlunparse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from core.manager import DownloadManager


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    url: str = Field(min_length=1, max_length=2048)

    @field_validator('url')
    @classmethod
    def validate_url(cls, value):
        parsed = urlparse(value)
        if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443):
            raise ValueError('Use a public HTTPS YouTube video URL')
        host = (parsed.hostname or '').lower()
        if host == 'youtu.be':
            video_id = parsed.path.strip('/')
        elif host in {'youtube.com', 'www.youtube.com', 'm.youtube.com'}:
            if parsed.path == '/watch':
                video_id = parse_qs(parsed.query).get('v', [''])[0]
            elif parsed.path.startswith(('/shorts/', '/live/', '/embed/')):
                video_id = parsed.path.split('/')[2]
            else:
                video_id = ''
        else:
            raise ValueError('Unsupported media host')
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
            raise ValueError('Invalid video identifier')
        return 'https://www.youtube.com/watch?v=' + video_id


class DownloadRequest(AnalyzeRequest):
    filename: str | None = Field(default=None, max_length=120)
    destination: str = Field(default='.', max_length=240)
    download_type: Literal['Video', 'Audio Only'] = 'Video'
    video_quality: Literal['Best Available', 'Best MP4 Compatible', '2160p / 4K', '1440p / 2K', '1080p', '720p', '480p', '360p'] = 'Best Available'
    audio_quality: Literal['Best Original Audio', 'MP3 High Quality'] = 'Best Original Audio'
    subtitle_languages: list[str] = Field(default_factory=list, max_length=10)
    download_subtitles: bool = False
    embed_subtitles: bool = False
    save_thumbnail: bool = False
    embed_thumbnail: bool = False
    embed_metadata: bool = False

    @field_validator('filename')
    @classmethod
    def filename_safe(cls, value):
        if value is not None and not DownloadManager._is_safe_download_filename(value):
            raise ValueError('Invalid logical filename')
        return value

    @field_validator('destination')
    @classmethod
    def destination_safe(cls, value):
        if unquote(value) != value:
            raise ValueError('Encoded destinations are not supported')
        from core.destination_policy import RootedDestinationPolicy
        # Syntax validation only. Actual configured-root resolution happens at admission.
        RootedDestinationPolicy('.').resolve(value)
        return value

    @field_validator('subtitle_languages')
    @classmethod
    def languages_safe(cls, value):
        if any(not re.fullmatch(r'[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*', x) for x in value):
            raise ValueError('Invalid subtitle language')
        return value


def public_snapshot(info):
    fields = ('id', 'title', 'status', 'stage', 'profile', 'download_type', 'progress',
              'downloaded', 'file_size', 'speed', 'eta', 'quality', 'error_category',
              'playlist_title', 'playlist_index', 'playlist_count', 'completed_at')
    result = {key: info.get(key) for key in fields}
    result['filename'] = ntpath.basename(info.get('filename') or '')
    result['error'] = 'Verification required' if info.get('error_category') == 'LOGIN_REQUIRED' else ('Download failed; see local logs' if info.get('error') else None)
    return result


def public_metadata(info):
    fields = ('format_id', 'width', 'height', 'fps', 'vcodec', 'acodec', 'ext', 'tbr', 'abr', 'filesize')
    thumbnail = info.get('thumbnail')
    if thumbnail:
        parsed = urlparse(thumbnail)
        thumbnail = urlunparse(('https', parsed.hostname, parsed.path, '', '', '')) if parsed.scheme == 'https' and parsed.hostname in {'i.ytimg.com', 'img.youtube.com'} else None
    return {key: info.get(key) for key in ('title', 'channel', 'uploader', 'duration', 'resolutions', 'subtitles', 'automatic_subtitles')} | {
        'thumbnail': thumbnail,
        'formats': [{key: fmt.get(key) for key in fields} for fmt in (info.get('formats') or [])],
    }

class FormatSummary(BaseModel):
    format_id: str | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    vcodec: str | None = None
    acodec: str | None = None
    ext: str | None = None
    tbr: float | None = None
    abr: float | None = None
    filesize: int | None = None


class AnalyzeResponse(BaseModel):
    title: str | None = None
    channel: str | None = None
    uploader: str | None = None
    duration: float | None = None
    thumbnail: str | None = None
    resolutions: list[int] | None = None
    formats: list[FormatSummary] = Field(default_factory=list)
    subtitles: list[str] | None = None
    automatic_subtitles: list[str] | None = None
