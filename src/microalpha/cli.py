"""Command-line entry points."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

from microalpha.config import load_config_bundle
from microalpha.manifest import build_run_manifest, write_manifest
from microalpha.utils.logging import configure_logging
from microalpha.utils.random import set_random_seed


def _smoke(args: argparse.Namespace) -> int:
    configure_logging(args.log_level)
    config = load_config_bundle(args.config_dir)
    seed = int(config.get("experiment", {}).get("random_seed", args.random_seed))
    set_random_seed(seed)
    manifest = build_run_manifest(config=config, repo_dir=Path.cwd())
    output_path = write_manifest(manifest, args.manifest_out)
    print(f"generated_manifest={output_path}")
    print(f"config_hash={manifest.config_hash}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="microalpha-smoke")
    parser.add_argument(
        "--config-dir",
        default="configs",
        help="Directory containing project YAML configuration files.",
    )
    parser.add_argument(
        "--manifest-out",
        default="data/manifests/phase0_smoke.yaml",
        help="Path where the generated run manifest will be written.",
    )
    parser.add_argument("--random-seed", type=int, default=42)
    parser.add_argument("--log-level", default="INFO")
    parser.set_defaults(func=_smoke)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
