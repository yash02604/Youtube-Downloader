import io
import unittest
from unittest.mock import Mock, patch

from PIL import Image

import thumbnail_preview


def png_bytes(size=(800, 450)):
    image = Image.new("RGB", size, (20, 100, 180))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class ThumbnailPreviewTests(unittest.TestCase):
    def response(self, content, *, status=200, content_type="image/png", headers=None):
        response = Mock()
        response.status_code = status
        response.headers = {"Content-Type": content_type, **(headers or {})}
        response.iter_content.return_value = [content]
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        return response

    @patch("thumbnail_preview.socket.getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 0))])
    @patch("thumbnail_preview.requests.get")
    def test_fetches_and_resizes_public_image(self, get, _dns):
        get.return_value = self.response(png_bytes())

        image = thumbnail_preview.fetch_thumbnail_image("https://cdn.example/image.png")

        self.assertEqual(image.size, (320, 180))
        get.assert_called_once()
        self.assertFalse(get.call_args.kwargs["allow_redirects"])

    @patch("thumbnail_preview.requests.get")
    def test_rejects_non_http_url_before_request(self, get):
        with self.assertRaises(ValueError):
            thumbnail_preview.fetch_thumbnail_image("file:///private/image.png")
        get.assert_not_called()

    @patch("thumbnail_preview.socket.getaddrinfo", return_value=[(None, None, None, None, ("127.0.0.1", 0))])
    @patch("thumbnail_preview.requests.get")
    def test_rejects_private_addresses_before_request(self, get, _dns):
        with self.assertRaises(ValueError):
            thumbnail_preview.fetch_thumbnail_image("https://internal.example/image.png")
        get.assert_not_called()

    @patch("thumbnail_preview.socket.getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 0))])
    @patch("thumbnail_preview.requests.get")
    def test_rejects_redirects_and_non_image_responses(self, get, _dns):
        get.return_value = self.response(b"", status=302)
        with self.assertRaises(ValueError):
            thumbnail_preview.fetch_thumbnail_image("https://cdn.example/image.png")

        get.return_value = self.response(b"text", content_type="text/plain")
        with self.assertRaises(ValueError):
            thumbnail_preview.fetch_thumbnail_image("https://cdn.example/image.png")

    @patch("thumbnail_preview.socket.getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 0))])
    @patch("thumbnail_preview.requests.get")
    def test_enforces_response_size_limit(self, get, _dns):
        get.return_value = self.response(
            b"x" * 10,
            headers={"Content-Length": str(thumbnail_preview.MAX_THUMBNAIL_BYTES + 1)},
        )
        with self.assertRaises(ValueError):
            thumbnail_preview.fetch_thumbnail_image("https://cdn.example/image.png")

    @patch("thumbnail_preview.socket.getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 0))])
    @patch("thumbnail_preview.requests.get")
    def test_rejects_invalid_image_data(self, get, _dns):
        get.return_value = self.response(b"not an image")
        with self.assertRaises(ValueError):
            thumbnail_preview.fetch_thumbnail_image("https://cdn.example/image.png")


if __name__ == "__main__":
    unittest.main()
