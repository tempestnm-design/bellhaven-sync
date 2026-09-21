from __future__ import annotations

import unittest
from unittest.mock import patch
from urllib.error import URLError

from reconciliation.errors import IndeterminateWriteError, SourceError
from reconciliation.http import HttpClient


class HttpRetryTests(unittest.TestCase):
    @patch("reconciliation.http.time.sleep")
    @patch("reconciliation.http.urlopen")
    def test_get_retries_bounded_network_failures(self, mocked_open, _mocked_sleep) -> None:
        mocked_open.side_effect = URLError("offline")
        with self.assertRaises(SourceError):
            HttpClient(retries=2).get("https://example.test")
        self.assertEqual(mocked_open.call_count, 3)

    @patch("reconciliation.http.urlopen")
    def test_write_is_never_automatically_retried(self, mocked_open) -> None:
        mocked_open.side_effect = URLError("response lost")
        with self.assertRaises(IndeterminateWriteError):
            HttpClient(retries=2).request(
                "POST", "https://example.test/accounts", json_body={"name": "Test"}
            )
        self.assertEqual(mocked_open.call_count, 1)


if __name__ == "__main__":
    unittest.main()
