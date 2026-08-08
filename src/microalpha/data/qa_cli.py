"""Installed CLI entry point for Phase 2 QA."""

from __future__ import annotations

import argparse
from typing import Optional

from microalpha.data.qa import load_qa_config, validate_market_data_csv, write_qa_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Phase 2 market-data QA.")
    parser.add_argument("--input-path", required=True)
    parser.add_argument(
        "--dataset-type",
        required=True,
        choices=["trades", "book_updates", "snapshots"],
    )
    parser.add_argument("--config-path", default="configs/qa.yaml")
    parser.add_argument("--report-out", required=True)
    parser.add_argument("--order-timestamp-column", default="event_time")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_qa_config(args.config_path)
    report = validate_market_data_csv(
        args.input_path,
        dataset_type=args.dataset_type,
        config=config,
        order_timestamp_column=args.order_timestamp_column,
    )
    write_qa_report(report, args.report_out)
    print(report.to_json(), end="")
    return 0 if report.can_continue else 2
