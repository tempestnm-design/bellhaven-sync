"""Small, dependency-free HTTP client with bounded retries."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .errors import IndeterminateWriteError, SourceError


@dataclass(frozen=True)
class HttpResponse:
    url: str
    status: int
    body: bytes

    def text(self) -> str:
        return self.body.decode("utf-8")

    def json(self) -> object:
        try:
            return json.loads(self.text())
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SourceError(f"Invalid JSON from {self.url}: {exc}") from exc


class HttpClient:
    def __init__(self, *, timeout: float = 15, retries: int = 2) -> None:
        self.timeout = timeout
        self.retries = retries

    def get(self, url: str, headers: dict[str, str] | None = None) -> HttpResponse:
        return self.request("GET", url, headers=headers)

    def request(
        self, method: str, url: str, *, headers: dict[str, str] | None = None,
        json_body: dict[str, object] | None = None,
    ) -> HttpResponse:
        request_headers = dict(headers or {})
        body = None
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request = Request(url, data=body, headers=request_headers, method=method)
        # Reads can be retried safely. A timed-out POST/PATCH may already have
        # committed remotely, so write requests are attempted exactly once.
        max_attempts = self.retries + 1 if method.upper() == "GET" else 1
        for attempt in range(max_attempts):
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    return HttpResponse(
                        url=response.geturl(), status=response.status, body=response.read()
                    )
            except HTTPError as exc:
                retryable = exc.code == 429 or exc.code >= 500
                if method.upper() != "GET" or not retryable or attempt == max_attempts - 1:
                    safe_body = exc.read().decode("utf-8", errors="replace")[:1000]
                    raise SourceError(
                        f"{method} {url} failed with HTTP {exc.code}: {safe_body}"
                    ) from exc
            except (URLError, TimeoutError) as exc:
                if method.upper() != "GET":
                    raise IndeterminateWriteError(
                        f"{method} {url} ended without a definitive response"
                    ) from exc
                if attempt == max_attempts - 1:
                    raise SourceError(f"{method} {url} failed: {exc}") from exc
            time.sleep(0.4 * (2**attempt))
        raise AssertionError("request loop exhausted")
