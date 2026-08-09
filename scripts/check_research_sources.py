"""Check source availability for registry dates without downloading data."""

from __future__ import annotations

import argparse
import json

from microalpha.pipeline.availability import check_records_concurrently
from microalpha.pipeline.registry import load_registry, records_for_role, write_registry


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", default="data/manifests/research_dates.yaml")
    parser.add_argument("--role", default="development")
    parser.add_argument("--date", action="append", default=[])
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    parser.add_argument("--read-bytes", type=int, default=2)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--max-workers", type=int, default=1)
    parser.add_argument("--metadata-timeout-seconds", type=float, default=15.0)
    args = parser.parse_args()
    registry = load_registry(args.registry)
    selected_dates = {record["date"] for record in records_for_role(registry["dates"], args.role)}
    if args.date:
        selected_dates &= set(args.date)
    checked = check_records_concurrently(
        [record for record in registry["dates"] if record["date"] in selected_dates],
        timeout_seconds=args.timeout_seconds,
        read_bytes=args.read_bytes,
        max_attempts=args.max_attempts,
        max_workers=args.max_workers,
        metadata_timeout_seconds=args.metadata_timeout_seconds,
    )
    by_date = {record["date"]: record for record in checked}
    registry["dates"] = [by_date.get(record["date"], record) for record in registry["dates"]]
    write_registry(args.registry, registry)
    summary = {
        "role": args.role,
        "checked": len(checked),
        "available": [
            record["date"]
            for record in checked
            if record.get("exclusion_status") == "included"
        ],
        "not_available": [
            {"date": record["date"], "reason": record.get("exclusion_reason", "")}
            for record in checked
            if record.get("exclusion_status") != "included"
        ],
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
