import logging
import unittest

from logging_utils import CredentialRedactionFilter, redact_sensitive_text


class LoggingRedactionTests(unittest.TestCase):
    def test_redacts_sensitive_query_values_headers_and_bearer_tokens(self):
        text = (
            "https://example.test/v?sig=abc123&quality=best&token=xyz "
            "Authorization: Bearer secret.value Cookie: sid=private"
        )
        safe = redact_sensitive_text(text)
        self.assertNotIn("abc123", safe)
        self.assertNotIn("xyz", safe)
        self.assertNotIn("secret.value", safe)
        self.assertNotIn("sid=private", safe)
        self.assertIn("quality=best", safe)

    def test_redacts_youtube_signed_url_parameters(self):
        safe = redact_sensitive_text(
            "https://media.example.test/v?lsig=signed&n=challenge&s=signature&itag=401"
        )
        for secret in ("signed", "challenge", "signature"):
            self.assertNotIn(secret, safe)
        self.assertIn("itag=401", safe)

    def test_filter_redacts_format_arguments(self):
        record = logging.LogRecord("test", logging.INFO, "", 1, "URL %s", (
            "https://example.test/?signature=private",
        ), None)
        self.assertTrue(CredentialRedactionFilter().filter(record))
        self.assertNotIn("private", record.getMessage())


if __name__ == "__main__":
    unittest.main()
