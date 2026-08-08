"""Installed CLI entry point for Phase 1 CSV ingestion."""

from __future__ import annotations

import argparse
import json
from typing import Optional

from microalpha.data.ingest import ingest_csv, result_to_dict


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ingest a Phase 1 raw market-data CSV file.")
    parser.add_argument("--source-path", required=True)
    parser.add_argument(
        "--dataset-type",
        required=True,
        choices=["trades", "book_updates", "snapshots"],
    )
    parser.add_argument("--instrument", default="BTC-USDT")
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--source-timezone", default="UTC")
    parser.add_argument("--source-name", default="local_csv")
    parser.add_argument("--raw-dir", default="data/raw")
    parser.add_argument("--bronze-dir", default="data/bronze")
    parser.add_argument("--manifest-dir", default="data/manifests")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    result = ingest_csv(
        source_path=args.source_path,
        dataset_type=args.dataset_type,
        instrument=args.instrument,
        trade_date=args.trade_date,
        source_timezone=args.source_timezone,
        source_name=args.source_name,
        raw_dir=args.raw_dir,
        bronze_dir=args.bronze_dir,
        manifest_dir=args.manifest_dir,
    )
    print(json.dumps(result_to_dict(result), indent=2, sort_keys=True))
    return 0
