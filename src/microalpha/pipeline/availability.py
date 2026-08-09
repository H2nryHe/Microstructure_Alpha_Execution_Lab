"""Source availability checks for frozen research-date registries."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class SourceCheck:
    url: str
    ok: bool
    status: int | None
    content_length: int | None
    error: str


def head_source(url: str, *, timeout_seconds: float = 15.0) -> SourceCheck:
    request = Request(url, method="HEAD")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            length = response.headers.get("Content-Length")
            return SourceCheck(
                url=url,
                ok=200 <= response.status < 300,
                status=response.status,
                content_length=int(length) if length else None,
                error="",
            )
    except HTTPError as exc:
        return SourceCheck(
            url=url,
            ok=False,
            status=exc.code,
            content_length=None,
            error=f"HTTPError: {exc.code}",
        )
    except URLError as exc:
        return SourceCheck(
            url=url,
            ok=False,
            status=None,
            content_length=None,
            error=f"URLError: {exc.reason}",
        )
    except TimeoutError:
        return SourceCheck(
            url=url,
            ok=False,
            status=None,
            content_length=None,
            error="TimeoutError",
        )


def check_record_sources(
    record: dict[str, Any],
    *,
    timeout_seconds: float = 15.0,
) -> dict[str, Any]:
    l2 = head_source(record["l2_source"], timeout_seconds=timeout_seconds)
    trades = head_source(record["trade_source"], timeout_seconds=timeout_seconds)
    updated = dict(record)
    updated["source_availability"] = {
        "l2": l2.__dict__,
        "trades": trades.__dict__,
    }
    if l2.ok and trades.ok:
        updated["compressed_file_size"] = {
            "l2": l2.content_length,
            "trades": trades.content_length,
        }
        return updated
    failures = []
    if not l2.ok:
        failures.append(f"L2 HEAD {l2.url} -> {l2.status or l2.error}")
    if not trades.ok:
        failures.append(f"trades HEAD {trades.url} -> {trades.status or trades.error}")
    updated["exclusion_status"] = "excluded"
    updated["exclusion_reason"] = "; ".join(failures)
    updated["qa_status"] = "not_run_source_unavailable"
    updated["book_replay_status"] = "not_run_source_unavailable"
    updated["feature_status"] = "not_run_source_unavailable"
    updated["label_status"] = "not_run_source_unavailable"
    return updated


def check_records_concurrently(
    records: list[dict[str, Any]],
    *,
    timeout_seconds: float = 15.0,
    max_workers: int = 8,
) -> list[dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                check_record_sources,
                record,
                timeout_seconds=timeout_seconds,
            ): record["date"]
            for record in records
        }
        for future in as_completed(futures):
            date = futures[future]
            try:
                results[date] = future.result()
            except Exception as exc:  # noqa: BLE001 - source checks must not abort the scan.
                matching = [record for record in records if record["date"] == date][0]
                updated = dict(matching)
                updated["exclusion_status"] = "excluded"
                updated["exclusion_reason"] = (
                    f"source availability check failed: {type(exc).__name__}: {exc}"
                )
                updated["qa_status"] = "not_run_source_unavailable"
                updated["book_replay_status"] = "not_run_source_unavailable"
                updated["feature_status"] = "not_run_source_unavailable"
                updated["label_status"] = "not_run_source_unavailable"
                results[date] = updated
    return [results[record["date"]] for record in records]
