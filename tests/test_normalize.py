from __future__ import annotations

import unittest

from reconciliation import normalize


class NormalizeTests(unittest.TestCase):
    def test_address_suffixes_directions_and_punctuation(self) -> None:
        self.assertEqual(
            normalize.address("805 Northwest Colegate Drive"),
            normalize.address("805 NW Colegate Dr."),
        )

    def test_zip_and_phone(self) -> None:
        self.assertEqual(normalize.zip_code("45840-1234"), "45840")
        self.assertEqual(normalize.phone("+1 (231) 533-2969"), "2315332969")


if __name__ == "__main__":
    unittest.main()
