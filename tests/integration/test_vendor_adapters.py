import csv
import gzip
import zipfile
from pathlib import Path

from microalpha.data.ingest import sha256_file
from microalpha.data.vendor_adapters import (
    ingest_binance_spot_trades_zip,
    ingest_tardis_incremental_l2_gzip,
)

FIXTURES = Path("tests/fixtures/real_subsets")


def _zip_fixture(source_csv: Path, destination_zip: Path) -> None:
    with zipfile.ZipFile(destination_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(source_csv, arcname="BTCUSDT-trades-2024-01-01.csv")


def _gzip_fixture(source_csv: Path, destination_gzip: Path) -> None:
    with source_csv.open("rb") as source_file:
        with destination_gzip.open("wb") as output_file:
            with gzip.GzipFile(fileobj=output_file, mode="wb", mtime=0) as gzip_file:
                gzip_file.write(source_file.read())


def test_binance_spot_trade_adapter_maps_real_schema_subset(tmp_path: Path) -> None:
    source_csv = FIXTURES / "binance_spot_BTCUSDT_trades_2024-01-01_first5.csv"
    source_zip = tmp_path / "BTCUSDT-trades-2024-01-01.zip"
    checksum_path = tmp_path / "BTCUSDT-trades-2024-01-01.zip.CHECKSUM"
    _zip_fixture(source_csv, source_zip)
    checksum = sha256_file(source_zip)
    checksum_path.write_text(f"{checksum}  {source_zip.name}\n", encoding="utf-8")

    result = ingest_binance_spot_trades_zip(
        source_zip_path=source_zip,
        checksum_path=checksum_path,
        instrument="BTC-USDT",
        vendor_symbol="BTCUSDT",
        trade_date="2024-01-01",
        raw_dir=tmp_path / "raw",
        bronze_dir=tmp_path / "bronze",
        manifest_dir=tmp_path / "manifests",
    )

    with Path(result.bronze_path).open("r", encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))

    assert result.vendor_checksum_verified is True
    assert result.row_count == 5
    assert rows[0]["event_time"] == "2024-01-01T00:00:00.000000+00:00"
    assert rows[0]["side"] == "sell"
    assert rows[1]["side"] == "buy"
    assert rows[0]["source_time"] == "1704067200000"
    assert Path(result.raw_path).read_bytes() == source_zip.read_bytes()


def test_tardis_incremental_l2_adapter_maps_documented_schema_subset(tmp_path: Path) -> None:
    source_csv = FIXTURES / "tardis_binance_incremental_l2_schema_sample.csv"
    source_gzip = tmp_path / "BTCUSDT.csv.gz"
    _gzip_fixture(source_csv, source_gzip)

    result = ingest_tardis_incremental_l2_gzip(
        source_gzip_path=source_gzip,
        instrument="BTC-USDT",
        vendor_symbol="BTCUSDT",
        trade_date="2019-12-01",
        raw_dir=tmp_path / "raw",
        bronze_dir=tmp_path / "bronze",
        manifest_dir=tmp_path / "manifests",
    )

    with Path(result.bronze_path).open("r", encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))

    assert result.row_count == 4
    assert rows[0]["event_time"] == "2019-12-01T00:00:00.000000+00:00"
    assert rows[0]["receive_time"] == "2019-12-01T00:00:00.000100+00:00"
    assert rows[0]["update_type"] == "snapshot"
    assert rows[2]["quantity"] == "0"
    assert rows[2]["update_type"] == "set"
    assert rows[2]["source_local_timestamp"] == "1575158400100200"
