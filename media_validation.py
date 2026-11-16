"""Lightweight validation of completed media files using an optional ffprobe."""

import json
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MediaValidationResult:
    status: str  # valid, invalid, or unavailable
    message: str
    streams: tuple[dict, ...] = ()


def validate_media_output(
    path: str | os.PathLike,
    ffmpeg_tools,
    *,
    expect_video: bool = True,
    expect_audio: bool = True,
    timeout: float = 15,
) -> MediaValidationResult:
    """Validate non-empty final output and its required stream types.

    An unavailable probe does not fail the media download. A successful probe
    with malformed output or missing required streams is a validation failure.
    """
    output = Path(path)
    if output.name.lower().endswith((".part", ".ytdl")):
        return MediaValidationResult("invalid", "Output is only a partial file")
    try:
        if not output.is_file() or output.stat().st_size <= 0:
            return MediaValidationResult("invalid", "Output file is missing or empty")
    except OSError as exc:
        return MediaValidationResult("invalid", f"Output file cannot be inspected: {exc}")

    if ffmpeg_tools is None or not getattr(ffmpeg_tools, "ffprobe", None):
        return MediaValidationResult("unavailable", "ffprobe is not available")

    try:
        result = ffmpeg_tools.run_ffprobe(
            [
                "-v", "error",
                "-show_entries", "stream=codec_type,codec_name",
                "-of", "json",
                str(output.resolve()),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("ffprobe validation unavailable for %s: %s", output, exc)
        return MediaValidationResult("unavailable", "ffprobe could not validate this output")

    try:
        response = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError):
        return MediaValidationResult("invalid", "ffprobe returned malformed JSON")
    streams = response.get("streams") if isinstance(response, dict) else None
    if not isinstance(streams, list) or any(not isinstance(stream, dict) for stream in streams):
        return MediaValidationResult("invalid", "ffprobe returned an invalid stream list")

    stream_types = {stream.get("codec_type") for stream in streams}
    missing = []
    if expect_video and "video" not in stream_types:
        missing.append("video")
    if expect_audio and "audio" not in stream_types:
        missing.append("audio")
    if missing:
        names = " and ".join(missing)
        return MediaValidationResult(
            "invalid",
            f"Required {names} stream{'s' if len(missing) > 1 else ''} missing",
            tuple(streams),
        )
    return MediaValidationResult("valid", "Required media streams are present", tuple(streams))
