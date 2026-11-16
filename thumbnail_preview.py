"""Bounded, opt-in thumbnail loading for the desktop preview."""

import ipaddress
import io
import socket
from urllib.parse import urlparse

import requests
from PIL import Image, UnidentifiedImageError


MAX_THUMBNAIL_BYTES = 5 * 1024 * 1024
MAX_THUMBNAIL_PIXELS = 25_000_000


def _require_public_http_url(url: str) -> tuple[str, int]:
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except (TypeError, ValueError) as exc:
        raise ValueError("Thumbnail URL is invalid") from exc
    if (parsed.scheme.lower() not in {"http", "https"} or not hostname
            or parsed.username or parsed.password):
        raise ValueError("Thumbnail URL must be an HTTP(S) URL without credentials")

    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        try:
            addresses = {
                ipaddress.ip_address(result[4][0])
                for result in socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
            }
        except (OSError, ValueError) as exc:
            raise ValueError("Thumbnail host could not be resolved safely") from exc
    else:
        addresses = {literal}

    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("Thumbnail URL must resolve only to public addresses")
    return hostname, port


def fetch_thumbnail_image(url: str, *, max_size=(320, 180), timeout=(3, 5)):
    """Fetch a small public image without redirects and return a resized PIL image."""
    _require_public_http_url(url)
    with requests.get(
        url,
        stream=True,
        timeout=timeout,
        allow_redirects=False,
        headers={"Accept": "image/*"},
    ) as response:
        if response.status_code != 200:
            raise ValueError("Thumbnail server did not return an image")
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if not content_type.startswith("image/"):
            raise ValueError("Thumbnail response is not an image")
        try:
            declared_length = int(response.headers.get("Content-Length", "0") or 0)
        except ValueError as exc:
            raise ValueError("Thumbnail response size is invalid") from exc
        if declared_length > MAX_THUMBNAIL_BYTES:
            raise ValueError("Thumbnail image is too large")

        content = bytearray()
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            content.extend(chunk)
            if len(content) > MAX_THUMBNAIL_BYTES:
                raise ValueError("Thumbnail image is too large")

    try:
        with Image.open(io.BytesIO(content)) as source:
            if source.width * source.height > MAX_THUMBNAIL_PIXELS:
                raise ValueError("Thumbnail image dimensions are too large")
            source.verify()
        with Image.open(io.BytesIO(content)) as source:
            source.load()
            image = source.convert("RGB")
            image.thumbnail(max_size, Image.Resampling.LANCZOS)
            return image.copy()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("Thumbnail image data is invalid") from exc
