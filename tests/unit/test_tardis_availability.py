from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError

import pytest

from microalpha.pipeline import availability
from microalpha.pipeline.availability import (
    AUTH_REQUIRED_STATUS,
    AVAILABLE_STATUS,
    CONFIRMED_UNAVAILABLE_STATUS,
    TRANSIENT_STATUS,
    check_record_sources,
    probe_source_get,
)
from microalpha.pipeline.registry import empty_registry_record, tardis_source_url


class FakeResponse:
    def __init__(
        self,
        *,
        status: int,
        headers: dict[str, str] | None = None,
        body: bytes = b"\x1f\x8brest of gzip payload",
        url: str = "https://datasets.tardis.dev/test.csv.gz",
    ) -> None:
        self.status = status
        self.headers = headers or {
            "Content-Type": "application/gzip",
            "Content-Length": str(len(body)),
            "Accept-Ranges": "bytes",
        }
        self.body = body
        self.url = url
        self.read_calls: list[int] = []

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        self.read_calls.append(size)
        if size < 0:
            return self.body
        return self.body[:size]

    def geturl(self) -> str:
        return self.url


def make_urlopen_sequence(responses: list[Any]):
    calls = []

    def fake_urlopen(request, timeout: float):  # noqa: ANN001 - matches urllib callback shape.
        calls.append({"method": request.get_method(), "headers": dict(request.header_items())})
        response = responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    fake_urlopen.calls = calls
    return fake_urlopen


def http_error(status: int, body: bytes = b"not found", content_type: str = "text/plain") -> HTTPError:
    return HTTPError(
        "https://datasets.tardis.dev/test.csv.gz",
        status,
        "error",
        {"Content-Type": content_type, "Content-Length": str(len(body))},
        BytesIO(body),
    )


def test_get_200_is_available(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = make_urlopen_sequence([FakeResponse(status=200)])
    monkeypatch.setattr(availability, "urlopen", fake)

    probe = probe_source_get("https://datasets.tardis.dev/test.csv.gz", read_bytes=2)

    assert probe.availability_status == AVAILABLE_STATUS
    assert probe.attempts[0].method == "GET"
    assert probe.attempts[0].status == 200
    assert probe.attempts[0].gzip_signature is True
    assert fake.calls[0]["method"] == "GET"


def test_get_206_is_available(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = make_urlopen_sequence(
        [FakeResponse(status=206, headers={"Content-Range": "bytes 0-1/10"})]
    )
    monkeypatch.setattr(availability, "urlopen", fake)

    probe = probe_source_get("https://datasets.tardis.dev/test.csv.gz", read_bytes=2)

    assert probe.availability_status == AVAILABLE_STATUS
    assert probe.attempts[0].content_range == "bytes 0-1/10"


def test_get_404_is_confirmed_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = make_urlopen_sequence([http_error(404, b"missing")])
    monkeypatch.setattr(availability, "urlopen", fake)

    probe = probe_source_get("https://datasets.tardis.dev/test.csv.gz", read_bytes=20)

    assert probe.availability_status == CONFIRMED_UNAVAILABLE_STATUS
    assert probe.attempts[0].status == 404
    assert probe.attempts[0].error_body == "missing"


def test_get_403_is_auth_required_not_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = make_urlopen_sequence([http_error(403, b"auth required")])
    monkeypatch.setattr(availability, "urlopen", fake)

    probe = probe_source_get("https://datasets.tardis.dev/test.csv.gz")

    assert probe.availability_status == AUTH_REQUIRED_STATUS


def test_timeout_is_transient_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = make_urlopen_sequence([TimeoutError("timed out")])
    monkeypatch.setattr(availability, "urlopen", fake)
    monkeypatch.setattr(availability.time, "sleep", lambda _: None)

    probe = probe_source_get("https://datasets.tardis.dev/test.csv.gz", max_attempts=1)

    assert probe.availability_status == TRANSIENT_STATUS


def test_network_error_is_transient_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = make_urlopen_sequence([URLError("network down")])
    monkeypatch.setattr(availability, "urlopen", fake)

    probe = probe_source_get("https://datasets.tardis.dev/test.csv.gz", max_attempts=1)

    assert probe.availability_status == TRANSIENT_STATUS


def test_retry_succeeds_after_transient_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = make_urlopen_sequence([TimeoutError("timed out"), FakeResponse(status=200)])
    monkeypatch.setattr(availability, "urlopen", fake)
    monkeypatch.setattr(availability.time, "sleep", lambda _: None)

    probe = probe_source_get("https://datasets.tardis.dev/test.csv.gz", max_attempts=3)

    assert probe.availability_status == AVAILABLE_STATUS
    assert len(probe.attempts) == 2


def test_gzip_signature_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = make_urlopen_sequence([FakeResponse(status=200, body=b"PKnotgzip")])
    monkeypatch.setattr(availability, "urlopen", fake)

    probe = probe_source_get("https://datasets.tardis.dev/test.csv.gz", read_bytes=2)

    assert probe.availability_status == AVAILABLE_STATUS
    assert probe.attempts[0].gzip_signature is False
    assert probe.attempts[0].first_bytes_hex == "504b"


def test_response_body_is_not_fully_downloaded(monkeypatch: pytest.MonkeyPatch) -> None:
    response = FakeResponse(status=200, body=b"\x1f\x8b" + b"x" * 1_000_000)
    fake = make_urlopen_sequence([response])
    monkeypatch.setattr(availability, "urlopen", fake)

    probe = probe_source_get("https://datasets.tardis.dev/test.csv.gz", read_bytes=2)

    assert probe.ok
    assert probe.attempts[0].bytes_read == 2
    assert response.read_calls == [2]


def test_known_good_2019_tardis_url_logic(monkeypatch: pytest.MonkeyPatch) -> None:
    url = tardis_source_url("2019-12-01", "incremental_book_L2")
    assert url == (
        "https://datasets.tardis.dev/v1/binance/incremental_book_L2/"
        "2019/12/01/BTCUSDT.csv.gz"
    )
    fake = make_urlopen_sequence([FakeResponse(status=200, url=url)])
    monkeypatch.setattr(availability, "urlopen", fake)

    probe = probe_source_get(url)

    assert probe.availability_status == AVAILABLE_STATUS


def test_failed_head_history_no_longer_determines_availability(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    del tmp_path
    record = empty_registry_record("2024-01-01", "development")
    record["source_availability"] = {
        "l2": {"method": "HEAD", "status": 404, "ok": False},
        "trades": {"method": "HEAD", "status": 404, "ok": False},
    }
    fake = make_urlopen_sequence([FakeResponse(status=200), FakeResponse(status=200)])
    monkeypatch.setattr(availability, "urlopen", fake)

    updated = check_record_sources(record)

    assert updated["exclusion_status"] == "included"
    assert updated["source_availability"]["l2"]["method"] == "GET"
    assert updated["source_availability_history"][0]["result"]["l2"]["method"] == "HEAD"
