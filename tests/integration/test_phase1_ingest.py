import csv
import json
from pathlib import Path

from microalpha.data.ingest import ingest_csv, sha256_file

FIXTURES = Path("tests/fixtures/phase1")


def test_ingest_preserves_raw_checksum_and_writes_manifest_and_bronze(tmp_path: Path) -> None:
    source_path = FIXTURES / "btc_usdt_trades_2026-01-02.csv"
    source_checksum = sha256_file(source_path)

    result = ingest_csv(
        source_path=source_path,
        dataset_type="trades",
        instrument="BTC-USDT",
        trade_date="2026-01-02",
        raw_dir=tmp_path / "raw",
        bronze_dir=tmp_path / "bronze",
        manifest_dir=tmp_path / "manifests",
        source_metadata={"provider": "fixture", "note": "tiny full-day fixture"},
    )

    assert result.source_checksum == source_checksum
    assert sha256_file(result.raw_path) == source_checksum
    assert result.row_count == 3
    assert Path(result.raw_path).read_bytes() == source_path.read_bytes()

    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    assert manifest["source_checksum_sha256"] == source_checksum
    assert manifest["source_timezone"] == "UTC"
    assert manifest["source_metadata"]["provider"] == "fixture"

    with Path(result.bronze_path).open("r", encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))

    assert len(rows) == 3
    assert rows[0]["instrument"] == "BTC-USDT"
    assert rows[0]["source_checksum"] == source_checksum
    assert rows[0]["source_event_time"] == "2026-01-02T00:00:00.000000Z"
    assert rows[0]["event_time"] == "2026-01-02T00:00:00.000000+00:00"
    assert rows[0]["price"] == "43000.10"
    assert rows[0]["quantity"] == "0.010"


def test_duplicate_ingestion_reuses_raw_and_bronze_paths_without_duplicate_data(
    tmp_path: Path,
) -> None:
    source_path = FIXTURES / "btc_usdt_book_updates_2026-01-02.csv"

    first = ingest_csv(
        source_path=source_path,
        dataset_type="book_updates",
        instrument="BTC-USDT",
        trade_date="2026-01-02",
        raw_dir=tmp_path / "raw",
        bronze_dir=tmp_path / "bronze",
        manifest_dir=tmp_path / "manifests",
    )
    second = ingest_csv(
        source_path=source_path,
        dataset_type="book_updates",
        instrument="BTC-USDT",
        trade_date="2026-01-02",
        raw_dir=tmp_path / "raw",
        bronze_dir=tmp_path / "bronze",
        manifest_dir=tmp_path / "manifests",
    )

    assert first.raw_path == second.raw_path
    assert first.bronze_path == second.bronze_path
    assert first.copied_raw is True
    assert second.copied_raw is False
    assert len(list((tmp_path / "raw").rglob("*.csv"))) == 1
    assert len(list((tmp_path / "bronze").rglob("*.csv"))) == 1
