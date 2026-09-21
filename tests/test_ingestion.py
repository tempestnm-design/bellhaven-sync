from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from reconciliation.config import load_profile
from reconciliation.crm import CrmClient
from reconciliation.db import SnapshotStore
from reconciliation.errors import ValidationError
from reconciliation.http import HttpResponse
from reconciliation.models import Account
from reconciliation.sources.bellhaven import BellhavenSource


class FakeHttp:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def get(self, url: str, headers: dict[str, str] | None = None) -> HttpResponse:
        self.calls.append(url)
        value = self.responses[url]
        body = value if isinstance(value, str) else json.dumps(value)
        return HttpResponse(url, 200, body.encode())


def detail(name: str, care: str = "Assisted Living Memory Support") -> str:
    return f"""<h1>{name}</h1><dl>
    <dt>Address</dt><dd>1800 N Blanchard St<br>Findlay, OH 45840</dd>
    <dt>Care Offerings</dt><dd>{care}</dd>
    <dt>Administrator</dt><dd>Sam Pruitt</dd><dt>Phone</dt><dd>(231) 533-2969</dd>
    </dl>"""


class WebsiteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = replace(
            load_profile("bellhaven"),
            website_base_url="https://example.test",
            minimum_facilities=2,
            maximum_facilities=2,
            required_directory_pages=2,
        )

    def test_unions_homepage_and_paginated_directory(self) -> None:
        http = FakeHttp({
            "https://example.test/": '<a href="/communities/new-findlay">new</a>',
            "https://example.test/communities": (
                '<a href="/communities/listed-one">one</a>'
                '<a href="/communities?page=2">next</a>'
            ),
            "https://example.test/communities?page=2": '<a href="/communities/listed-one">one</a>',
            "https://example.test/communities/listed-one": detail("Listed One", "Assisted Living"),
            "https://example.test/communities/new-findlay": detail("New Findlay"),
        })
        facilities = BellhavenSource(self.profile, http).fetch_facilities()
        self.assertEqual({item.name for item in facilities}, {"Listed One", "New Findlay"})
        findlay = next(item for item in facilities if item.name == "New Findlay")
        self.assertEqual(findlay.care_offerings, ("Assisted Living", "Memory Support"))

    def test_fails_closed_on_incomplete_count(self) -> None:
        profile = replace(self.profile, minimum_facilities=3, maximum_facilities=3)
        http = FakeHttp({
            "https://example.test/": "",
            "https://example.test/communities": '<a href="/communities/only">one</a>',
            "https://example.test/communities/only": detail("Only One"),
        })
        with self.assertRaises(ValidationError):
            BellhavenSource(profile, http).fetch_facilities()


class CrmTests(unittest.TestCase):
    def test_paginates_and_validates_parent(self) -> None:
        profile = replace(load_profile("bellhaven"), crm_api_base_url="https://crm.test")
        parent = {"account_id": profile.expected_parent_account_id, "name": profile.parent_account_name}
        child = {"account_id": "child", "name": "Child", "parent_id": parent["account_id"]}
        http = FakeHttp({
            "https://crm.test/accounts?page=1&page_size=1": {"data": [parent], "total": 2},
            "https://crm.test/accounts?page=2&page_size=1": {"data": [child], "total": 2},
        })
        client = CrmClient(profile, "not-a-real-token", http)
        accounts = client.fetch_accounts(page_size=1)
        self.assertEqual(len(accounts), 2)
        self.assertEqual(client.validate_parent(accounts).account_id, parent["account_id"])


class StoreTests(unittest.TestCase):
    def test_records_successful_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory) / "test.sqlite3")
            with store.run("bellhaven") as run_id:
                account = Account.from_api({"account_id": "a1", "name": "One"})
                store.save_accounts(run_id, [account])
            status = store.connection.execute(
                "SELECT status FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            self.assertEqual(status, "succeeded")
            store.close()


if __name__ == "__main__":
    unittest.main()
