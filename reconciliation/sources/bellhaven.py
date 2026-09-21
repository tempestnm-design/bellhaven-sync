"""Bellhaven website adapter.

Discovery intentionally unions homepage and paginated-directory links. This catches
newly announced facilities (currently Findlay) that have not reached the directory.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlparse

from ..errors import ValidationError
from ..http import HttpClient
from ..models import Facility, OperatorProfile
from .base import FacilitySource


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.text_parts: list[str] = []
        self._href: str | None = None
        self._tag_stack: list[str] = []
        self.terms: list[str] = []
        self.definitions: list[str] = []
        self._capture: str | None = None
        self._buffer: list[str] = []
        self.headings: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._tag_stack.append(tag)
        if tag == "a":
            self._href = dict(attrs).get("href")
            if self._href:
                self.links.append(self._href)
        if tag in {"h1", "dt", "dd"}:
            self._capture = tag
            self._buffer = []

    def handle_endtag(self, tag: str) -> None:
        if self._capture == tag:
            value = " ".join(" ".join(self._buffer).split())
            if tag == "h1":
                self.headings.append(value)
            elif tag == "dt":
                self.terms.append(value)
            else:
                self.definitions.append(value)
            self._capture = None
            self._buffer = []
        if self._tag_stack:
            self._tag_stack.pop()
        if tag == "a":
            self._href = None

    def handle_data(self, data: str) -> None:
        clean = " ".join(data.split())
        if clean:
            self.text_parts.append(clean)
            if self._capture:
                self._buffer.append(clean)


class BellhavenSource(FacilitySource):
    FACILITY_PATH = re.compile(r"^/communities/[^/?#]+$")
    ADDRESS_RE = re.compile(
        r"^(?P<street>.+?)\s+(?P<city>[A-Za-z .'-]+),\s*"
        r"(?P<state>[A-Z]{2})\s+(?P<zip>\d{5}(?:-\d{4})?)$"
    )

    def __init__(self, profile: OperatorProfile, http: HttpClient | None = None) -> None:
        self.profile = profile
        self.http = http or HttpClient()

    def _parse(self, url: str) -> _PageParser:
        parser = _PageParser()
        parser.feed(self.http.get(url).text())
        return parser

    def _discover_links(self) -> tuple[set[str], set[int]]:
        base = self.profile.website_base_url
        links: set[str] = set()
        pages_seen: set[int] = set()

        homepage = self._parse(base + "/")
        links.update(href for href in homepage.links if self.FACILITY_PATH.match(href))

        page = 1
        while True:
            url = urljoin(base, self.profile.directory_path)
            if page > 1:
                url += f"?page={page}"
            parsed = self._parse(url)
            pages_seen.add(page)
            links.update(href for href in parsed.links if self.FACILITY_PATH.match(href))
            page_numbers = {
                int(values[0])
                for href in parsed.links
                if urlparse(href).path == self.profile.directory_path
                for values in [parse_qs(urlparse(href).query).get("page", [])]
                if values and values[0].isdigit()
            }
            unseen = sorted(number for number in page_numbers if number not in pages_seen)
            if not unseen:
                break
            page = unseen[0]
        return links, pages_seen

    def _facility(self, href: str) -> Facility:
        url = urljoin(self.profile.website_base_url, href)
        parsed = self._parse(url)
        fields = dict(zip(parsed.terms, parsed.definitions, strict=False))
        if not parsed.headings:
            raise ValidationError(f"Empty facility page: {url}")
        name = parsed.headings[0]
        address = fields.get("Address", "")
        match = self.ADDRESS_RE.match(address)
        if not match:
            raise ValidationError(f"Could not parse address on {url}: {address!r}")
        care_raw = fields.get("Care Offerings", "")
        offerings = tuple(
            label for label in self.profile.care_type_map if label in care_raw
        )
        if not offerings:
            raise ValidationError(f"No recognized care offering on {url}")
        return Facility(
            operator_key=self.profile.operator_key,
            name=name,
            street=match.group("street"),
            city=match.group("city"),
            state=match.group("state"),
            zip_code=match.group("zip"),
            care_offerings=offerings,
            source_url=url,
            administrator=fields.get("Administrator", ""),
            phone=fields.get("Phone", ""),
        )

    def fetch_facilities(self) -> list[Facility]:
        links, pages = self._discover_links()
        if self.profile.required_directory_pages and len(pages) != self.profile.required_directory_pages:
            raise ValidationError(
                f"Expected {self.profile.required_directory_pages} directory pages; saw {sorted(pages)}"
            )
        facilities = [self._facility(href) for href in sorted(links)]
        count = len(facilities)
        if not self.profile.minimum_facilities <= count <= self.profile.maximum_facilities:
            raise ValidationError(
                f"Facility count {count} outside configured bounds "
                f"{self.profile.minimum_facilities}..{self.profile.maximum_facilities}"
            )
        urls = {item.source_url for item in facilities}
        if len(urls) != count:
            raise ValidationError("Duplicate facility URLs in website snapshot")
        return facilities
