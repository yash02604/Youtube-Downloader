import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from media_validation import validate_media_output


class FakeFFprobe:
    def __init__(self, payload=None, error=None):
        self.ffprobe = "ffprobe.exe"
        self.payload = payload
        self.error = error
        self.calls = []

    def run_ffprobe(self, arguments, **kwargs):
        self.calls.append((arguments, kwargs))
        if self.error:
            raise self.error
        return subprocess.CompletedProcess(
            args=["ffprobe", *arguments],
            returncode=0,
            stdout=json.dumps(self.payload),
            stderr="",
        )


class MediaValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.output = Path(self.temp_dir.name) / "media.mkv"
        self.output.write_bytes(b"media bytes")

    def tearDown(self):
        self.temp_dir.cleanup()

    def probe(self, streams, *, expected_video=True, expected_audio=True):
        tools = FakeFFprobe({"streams": streams})
        result = validate_media_output(
            self.output,
            tools,
            expect_video=expected_video,
            expect_audio=expected_audio,
        )
        return result, tools

    def test_video_and_audio_streams_validate(self):
        result, tools = self.probe([
            {"codec_type": "video", "codec_name": "av1"},
            {"codec_type": "audio", "codec_name": "opus"},
        ])
        self.assertEqual(result.status, "valid")
        self.assertEqual(len(tools.calls), 1)

    def test_missing_audio_stream_is_invalid(self):
        result, _ = self.probe([{"codec_type": "video"}])
        self.assertEqual(result.status, "invalid")
        self.assertIn("audio", result.message.lower())

    def test_missing_video_stream_is_invalid(self):
        result, _ = self.probe([{"codec_type": "audio"}])
        self.assertEqual(result.status, "invalid")
        self.assertIn("video", result.message.lower())

    def test_audio_only_requires_only_audio(self):
        result, _ = self.probe(
            [{"codec_type": "audio", "codec_name": "opus"}],
            expected_video=False,
            expected_audio=True,
        )
        self.assertEqual(result.status, "valid")

    def test_zero_byte_file_is_invalid(self):
        self.output.write_bytes(b"")
        result = validate_media_output(self.output, FakeFFprobe({"streams": []}))
        self.assertEqual(result.status, "invalid")
        self.assertIn("empty", result.message.lower())

    def test_part_file_is_invalid(self):
        part = self.output.with_name("media.mkv.part")
        part.write_bytes(b"partial")
        result = validate_media_output(part, FakeFFprobe({"streams": []}))
        self.assertEqual(result.status, "invalid")
        self.assertIn("partial", result.message.lower())

    def test_missing_ffprobe_is_validation_unavailable(self):
        result = validate_media_output(self.output, None)
        self.assertEqual(result.status, "unavailable")

    def test_probe_timeout_is_validation_unavailable(self):
        result = validate_media_output(
            self.output,
            FakeFFprobe(error=subprocess.TimeoutExpired("ffprobe", 10)),
        )
        self.assertEqual(result.status, "unavailable")

    def test_probe_executable_failure_is_validation_unavailable(self):
        result = validate_media_output(
            self.output,
            FakeFFprobe(error=FileNotFoundError("ffprobe.exe")),
        )
        self.assertEqual(result.status, "unavailable")

    def test_probe_process_error_is_validation_unavailable(self):
        result = validate_media_output(
            self.output,
            FakeFFprobe(error=subprocess.CalledProcessError(1, "ffprobe")),
        )
        self.assertEqual(result.status, "unavailable")

    def test_malformed_probe_response_is_invalid(self):
        class MalformedFFprobe:
            ffprobe = "ffprobe.exe"

            def run_ffprobe(self, arguments, **kwargs):
                return subprocess.CompletedProcess(
                    args=arguments, returncode=0, stdout="not json", stderr=""
                )

        result = validate_media_output(self.output, MalformedFFprobe())
        self.assertEqual(result.status, "invalid")


if __name__ == "__main__":
    unittest.main()
