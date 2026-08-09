"""Run the Phase 1-6 pipeline for one UTC research day."""

from __future__ import annotations

import argparse
import json

from microalpha.pipeline.day import DayRunConfig, run_day


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--work-root", default="/tmp/microalpha-multiday")
    parser.add_argument("--source-root", default="/tmp/microalpha-multiday/source")
    parser.add_argument("--config-dir", default="configs")
    args = parser.parse_args()
    result = run_day(
        args.date,
        config=DayRunConfig(
            work_root=args.work_root,
            raw_source_root=args.source_root,
            config_dir=args.config_dir,
        ),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
