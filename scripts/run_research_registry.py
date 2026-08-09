"""Run the multi-day pipeline over a frozen registry role."""

from __future__ import annotations

import argparse
import json

from microalpha.pipeline.day import DayRunConfig
from microalpha.pipeline.multiday import process_registry


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", default="data/manifests/research_dates.yaml")
    parser.add_argument("--role", default="development")
    parser.add_argument("--work-root", default="/tmp/microalpha-multiday")
    parser.add_argument("--source-root", default="/tmp/microalpha-multiday/source")
    parser.add_argument("--date", action="append", help="Optional date filter; repeatable.")
    parser.add_argument("--stop-on-error", action="store_true")
    args = parser.parse_args()
    result = process_registry(
        registry_path=args.registry,
        role=args.role,
        dates=set(args.date) if args.date else None,
        stop_on_error=args.stop_on_error,
        config=DayRunConfig(
            work_root=args.work_root,
            raw_source_root=args.source_root,
        ),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
