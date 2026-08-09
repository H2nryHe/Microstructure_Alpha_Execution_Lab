"""Probe one Tardis dataset URL with a small GET request."""

from __future__ import annotations

import argparse
import json

from microalpha.pipeline.availability import probe_source_get


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    parser.add_argument("--read-bytes", type=int, default=2)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--use-range", action="store_true")
    args = parser.parse_args()
    probe = probe_source_get(
        args.url,
        timeout_seconds=args.timeout_seconds,
        read_bytes=args.read_bytes,
        max_attempts=args.max_attempts,
        use_range=args.use_range,
    )
    payload = {
        "url": probe.url,
        "method": probe.method,
        "availability_status": probe.availability_status,
        "ok": probe.ok,
        "attempts": [attempt.__dict__ for attempt in probe.attempts],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if probe.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
