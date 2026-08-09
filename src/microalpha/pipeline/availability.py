"""GET-based source availability checks for frozen research-date registries."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from socket import timeout as SocketTimeout
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

USER_AGENT = (
    "Mozilla/5.0 (compatible; microalpha-prephase7-source-probe/1.0; "
    "+https://github.com/H2nryHe/Microstructure_Alpha_Execution_Lab)"
)
GZIP_MAGIC = b"\x1f\x8b"
TRANSIENT_STATUS = "TRANSIENT_ERROR"
AVAILABLE_STATUS = "AVAILABLE"
CONFIRMED_UNAVAILABLE_STATUS = "CONFIRMED_UNAVAILABLE"
AUTH_REQUIRED_STATUS = "AUTH_REQUIRED"
CHECK_FAILED_STATUS = "CHECK_FAILED"


@dataclass(frozen=True)
class ProbeAttempt:
    attempt: int
    method: str
    status: int | None
    availability_status: str
    content_type: str
    content_length: int | None
    content_range: str
    accept_ranges: str
    bytes_read: int
    first_bytes_hex: str
    gzip_signature: bool | None
    elapsed_seconds: float
    exception_type: str
    exception_message: str
    headers: dict[str, str]
    redirect_url: str
    error_body: str


@dataclass(frozen=True)
class SourceProbe:
    url: str
    method: str
    availability_status: str
    attempts: list[ProbeAttempt]

    @property
    def ok(self) -> bool:
        return self.availability_status == AVAILABLE_STATUS


def _diagnostic_headers(headers: Any) -> dict[str, str]:
    names = {
        "accept-ranges",
        "age",
        "cache-control",
        "cf-cache-status",
        "content-encoding",
        "content-length",
        "content-range",
        "content-type",
        "date",
        "etag",
        "last-modified",
        "location",
        "server",
        "via",
        "x-amz-request-id",
        "x-amz-id-2",
        "x-cache",
        "x-md5",
    }
    return {
        key: value
        for key, value in dict(headers).items()
        if key.lower() in names or key.lower().startswith("x-")
    }


def _content_length(headers: Any) -> int | None:
    value = headers.get("Content-Length")
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _attempt_status(status: int) -> str:
    if status in {200, 206}:
        return AVAILABLE_STATUS
    if status == 404:
        return CONFIRMED_UNAVAILABLE_STATUS
    if status in {401, 403}:
        return AUTH_REQUIRED_STATUS
    if status in {408, 425, 429, 500, 502, 503, 504}:
        return TRANSIENT_STATUS
    return CHECK_FAILED_STATUS


def _should_retry(status: str) -> bool:
    return status == TRANSIENT_STATUS


def _body_sample_is_text(content_type: str, data: bytes) -> bool:
    if "text" in content_type or "json" in content_type or "xml" in content_type:
        return True
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _make_attempt(
    *,
    url: str,
    attempt: int,
    read_bytes: int,
    timeout_seconds: float,
    use_range: bool,
) -> ProbeAttempt:
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if use_range:
        headers["Range"] = f"bytes=0-{max(read_bytes - 1, 0)}"
    request = Request(url, headers=headers, method="GET")
    start = time.perf_counter()
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            status = int(response.status)
            content_type = response.headers.get("Content-Type", "")
            data = response.read(read_bytes)
            elapsed = time.perf_counter() - start
            availability_status = _attempt_status(status)
            return ProbeAttempt(
                attempt=attempt,
                method="GET",
                status=status,
                availability_status=availability_status,
                content_type=content_type,
                content_length=_content_length(response.headers),
                content_range=response.headers.get("Content-Range", ""),
                accept_ranges=response.headers.get("Accept-Ranges", ""),
                bytes_read=len(data),
                first_bytes_hex=data.hex(),
                gzip_signature=data.startswith(GZIP_MAGIC) if data else None,
                elapsed_seconds=elapsed,
                exception_type="",
                exception_message="",
                headers=_diagnostic_headers(response.headers),
                redirect_url=response.geturl() if response.geturl() != url else "",
                error_body="",
            )
    except HTTPError as exc:
        content_type = exc.headers.get("Content-Type", "")
        body = exc.read(read_bytes)
        elapsed = time.perf_counter() - start
        return ProbeAttempt(
            attempt=attempt,
            method="GET",
            status=exc.code,
            availability_status=_attempt_status(exc.code),
            content_type=content_type,
            content_length=_content_length(exc.headers),
            content_range=exc.headers.get("Content-Range", ""),
            accept_ranges=exc.headers.get("Accept-Ranges", ""),
            bytes_read=len(body),
            first_bytes_hex=body.hex(),
            gzip_signature=body.startswith(GZIP_MAGIC) if body else None,
            elapsed_seconds=elapsed,
            exception_type=type(exc).__name__,
            exception_message=str(exc),
            headers=_diagnostic_headers(exc.headers),
            redirect_url=exc.geturl() if exc.geturl() != url else "",
            error_body=(
                body.decode("utf-8", errors="replace")
                if body and _body_sample_is_text(content_type, body)
                else ""
            ),
        )
    except (TimeoutError, SocketTimeout) as exc:
        elapsed = time.perf_counter() - start
        return ProbeAttempt(
            attempt=attempt,
            method="GET",
            status=None,
            availability_status=TRANSIENT_STATUS,
            content_type="",
            content_length=None,
            content_range="",
            accept_ranges="",
            bytes_read=0,
            first_bytes_hex="",
            gzip_signature=None,
            elapsed_seconds=elapsed,
            exception_type=type(exc).__name__,
            exception_message=str(exc),
            headers={},
            redirect_url="",
            error_body="",
        )
    except URLError as exc:
        elapsed = time.perf_counter() - start
        return ProbeAttempt(
            attempt=attempt,
            method="GET",
            status=None,
            availability_status=TRANSIENT_STATUS,
            content_type="",
            content_length=None,
            content_range="",
            accept_ranges="",
            bytes_read=0,
            first_bytes_hex="",
            gzip_signature=None,
            elapsed_seconds=elapsed,
            exception_type=type(exc).__name__,
            exception_message=str(exc.reason),
            headers={},
            redirect_url="",
            error_body="",
        )
    except Exception as exc:  # noqa: BLE001 - unexpected probe errors must be recorded.
        elapsed = time.perf_counter() - start
        return ProbeAttempt(
            attempt=attempt,
            method="GET",
            status=None,
            availability_status=CHECK_FAILED_STATUS,
            content_type="",
            content_length=None,
            content_range="",
            accept_ranges="",
            bytes_read=0,
            first_bytes_hex="",
            gzip_signature=None,
            elapsed_seconds=elapsed,
            exception_type=type(exc).__name__,
            exception_message=str(exc),
            headers={},
            redirect_url="",
            error_body="",
        )


def probe_source_get(
    url: str,
    *,
    timeout_seconds: float = 15.0,
    read_bytes: int = 2,
    max_attempts: int = 3,
    initial_backoff_seconds: float = 0.25,
    use_range: bool = False,
) -> SourceProbe:
    """Probe a Tardis dataset URL with GET without downloading the whole object."""

    attempts: list[ProbeAttempt] = []
    for attempt_number in range(1, max_attempts + 1):
        attempt = _make_attempt(
            url=url,
            attempt=attempt_number,
            read_bytes=read_bytes,
            timeout_seconds=timeout_seconds,
            use_range=use_range,
        )
        attempts.append(attempt)
        if not _should_retry(attempt.availability_status):
            break
        if attempt_number < max_attempts:
            time.sleep(initial_backoff_seconds * (2 ** (attempt_number - 1)))
    final_status = attempts[-1].availability_status if attempts else CHECK_FAILED_STATUS
    return SourceProbe(url=url, method="GET", availability_status=final_status, attempts=attempts)


def _probe_to_dict(probe: SourceProbe) -> dict[str, Any]:
    return {
        "url": probe.url,
        "method": probe.method,
        "availability_status": probe.availability_status,
        "ok": probe.ok,
        "attempts": [asdict(attempt) for attempt in probe.attempts],
    }


def _preserve_previous_availability(record: dict[str, Any]) -> dict[str, Any]:
    updated = dict(record)
    previous = updated.pop("source_availability", None)
    if previous is None:
        return updated
    history = list(updated.get("source_availability_history", []))
    history.append({"checker": "legacy_or_previous", "result": previous})
    updated["source_availability_history"] = history
    return updated


def _record_status_from_probes(updated: dict[str, Any], l2: SourceProbe, trades: SourceProbe) -> None:
    if l2.ok and trades.ok:
        l2_length = l2.attempts[-1].content_length if l2.attempts else None
        trades_length = trades.attempts[-1].content_length if trades.attempts else None
        updated["compressed_file_size"] = {"l2": l2_length, "trades": trades_length}
        updated["exclusion_status"] = "included"
        updated["exclusion_reason"] = ""
        for status_key in (
            "qa_status",
            "book_replay_status",
            "feature_status",
            "label_status",
        ):
            if str(updated.get(status_key, "")).startswith("not_run_source"):
                updated[status_key] = "pending"
        return

    failures = []
    for label, probe in (("L2", l2), ("trades", trades)):
        if probe.ok:
            continue
        failures.append(
            f"{label} GET {probe.url} -> {probe.availability_status}"
            + (
                f" status={probe.attempts[-1].status}"
                if probe.attempts and probe.attempts[-1].status is not None
                else ""
            )
        )
    updated["exclusion_reason"] = "; ".join(failures)
    statuses = {l2.availability_status, trades.availability_status}
    if statuses <= {CONFIRMED_UNAVAILABLE_STATUS, AVAILABLE_STATUS}:
        updated["exclusion_status"] = "excluded"
        updated["qa_status"] = "not_run_source_unavailable"
        updated["book_replay_status"] = "not_run_source_unavailable"
        updated["feature_status"] = "not_run_source_unavailable"
        updated["label_status"] = "not_run_source_unavailable"
    else:
        updated["exclusion_status"] = "requires_recheck"
        updated["qa_status"] = "not_run_source_recheck"
        updated["book_replay_status"] = "not_run_source_recheck"
        updated["feature_status"] = "not_run_source_recheck"
        updated["label_status"] = "not_run_source_recheck"


def check_record_sources(
    record: dict[str, Any],
    *,
    timeout_seconds: float = 15.0,
    read_bytes: int = 2,
    max_attempts: int = 3,
) -> dict[str, Any]:
    updated = _preserve_previous_availability(record)
    l2 = probe_source_get(
        record["l2_source"],
        timeout_seconds=timeout_seconds,
        read_bytes=read_bytes,
        max_attempts=max_attempts,
    )
    trades = probe_source_get(
        record["trade_source"],
        timeout_seconds=timeout_seconds,
        read_bytes=read_bytes,
        max_attempts=max_attempts,
    )
    updated["source_availability"] = {
        "l2": _probe_to_dict(l2),
        "trades": _probe_to_dict(trades),
    }
    _record_status_from_probes(updated, l2, trades)
    return updated


def check_records_concurrently(
    records: list[dict[str, Any]],
    *,
    timeout_seconds: float = 15.0,
    read_bytes: int = 2,
    max_attempts: int = 3,
    max_workers: int = 1,
) -> list[dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                check_record_sources,
                record,
                timeout_seconds=timeout_seconds,
                read_bytes=read_bytes,
                max_attempts=max_attempts,
            ): record["date"]
            for record in records
        }
        for future in as_completed(futures):
            date = futures[future]
            try:
                results[date] = future.result()
            except Exception as exc:  # noqa: BLE001 - source checks must not abort the scan.
                matching = [record for record in records if record["date"] == date][0]
                updated = _preserve_previous_availability(matching)
                updated["exclusion_status"] = "requires_recheck"
                updated["exclusion_reason"] = (
                    f"source availability check failed: {type(exc).__name__}: {exc}"
                )
                updated["qa_status"] = "not_run_source_recheck"
                updated["book_replay_status"] = "not_run_source_recheck"
                updated["feature_status"] = "not_run_source_recheck"
                updated["label_status"] = "not_run_source_recheck"
                results[date] = updated
    return [results[record["date"]] for record in records]
