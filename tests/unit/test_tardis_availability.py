from __future__ import annotations

import json
from io import BytesIO
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
    check_tardis_exchange_metadata,
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


def http_error(
    status: int,
    body: bytes = b"not found",
    content_type: str = "text/plain",
) -> HTTPError:
    return HTTPError(
        "https://datasets.tardis.dev/test.csv.gz",
        status,
        "error",
        {"Content-Type": content_type, "Content-Length": str(len(body))},
        BytesIO(body),
    )


def metadata_response(
    *,
    symbol: str = "BTCUSDT",
    data_types: list[str] | None = None,
    available_since: str = "2019-03-30T00:00:00.000Z",
    available_to: str = "2026-08-08T00:00:00.000Z",
) -> FakeResponse:
    if data_types is None:
        data_types = ["trades", "incremental_book_L2", "quotes"]
    body = json.dumps(
        {
            "id": "binance",
            "availableSymbols": [
                {
                    "id": "btcusdt",
                    "type": "spot",
                    "availableSince": available_since,
                }
            ],
            "datasets": {
                "exportedFrom": available_since,
                "exportedUntil": available_to,
                "symbols": [
                    {
                        "id": symbol,
                        "type": "spot",
                        "availableSince": available_since,
                        "availableTo": available_to,
                        "dataTypes": data_types,
                    }
                ],
            },
        }
    ).encode()
    return FakeResponse(
        status=200,
        headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
        body=body,
        url="https://api.tardis.dev/v1/exchanges/binance",
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


def test_metadata_confirms_known_good_2019_tardis_symbol_and_data_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = make_urlopen_sequence([metadata_response()])
    monkeypatch.setattr(availability, "urlopen", fake)

    check = check_tardis_exchange_metadata(
        exchange="binance",
        dataset_symbol="BTCUSDT",
        requested_date="2019-12-01",
        required_data_types=("incremental_book_L2", "trades"),
    )

    assert check.ok
    assert check.symbol_exists
    assert check.dataset_symbol_exists
    assert check.coverage_includes_date
    assert check.requested_data_types_supported
    assert set(check.supported_data_types) >= {"incremental_book_L2", "trades"}


def test_failed_head_history_no_longer_determines_availability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = empty_registry_record("2024-01-01", "development")
    record["source_availability"] = {
        "l2": {"method": "HEAD", "status": 404, "ok": False},
        "trades": {"method": "HEAD", "status": 404, "ok": False},
    }
    record["qa_status"] = "not_run_source_unavailable"
    record["book_replay_status"] = "not_run_source_unavailable"
    record["feature_status"] = "not_run_source_unavailable"
    record["label_status"] = "not_run_source_unavailable"
    fake = make_urlopen_sequence(
        [FakeResponse(status=200), FakeResponse(status=200), metadata_response()]
    )
    monkeypatch.setattr(availability, "urlopen", fake)

    updated = check_record_sources(record)

    assert updated["exclusion_status"] == "included"
    assert updated["qa_status"] == "pending"
    assert updated["book_replay_status"] == "pending"
    assert updated["feature_status"] == "pending"
    assert updated["label_status"] == "pending"
    assert updated["source_availability"]["l2"]["method"] == "GET"
    assert updated["source_availability_history"][0]["result"]["l2"]["method"] == "HEAD"
    assert updated["source_metadata_check"]["ok"] is True


def test_get_404_with_supporting_metadata_requires_recheck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = empty_registry_record("2024-04-01", "development")
    fake = make_urlopen_sequence([http_error(404), FakeResponse(status=200), metadata_response()])
    monkeypatch.setattr(availability, "urlopen", fake)

    updated = check_record_sources(record)

    assert updated["source_availability"]["l2"]["availability_status"] == (
        CONFIRMED_UNAVAILABLE_STATUS
    )
    assert updated["source_metadata_check"]["ok"] is True
    assert updated["exclusion_status"] == "requires_recheck"
    assert "metadata indicates" in updated["exclusion_reason"]


def test_get_404_with_metadata_gap_can_exclude(monkeypatch: pytest.MonkeyPatch) -> None:
    record = empty_registry_record("2027-01-01", "development")
    fake = make_urlopen_sequence(
        [
            http_error(404),
            http_error(404),
            metadata_response(available_to="2026-08-08T00:00:00.000Z"),
        ]
    )
    monkeypatch.setattr(availability, "urlopen", fake)

    updated = check_record_sources(record)

    assert updated["source_metadata_check"]["coverage_includes_date"] is False
    assert updated["exclusion_status"] == "excluded"
