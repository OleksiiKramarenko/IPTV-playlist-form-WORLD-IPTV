"""CLI entry point: python -m iptv [--config config.json] [--force]"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import ConfigError, load_config
from .pipeline import BuildError, run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build playlist.m3u from the sources in config.json")
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument("--force", action="store_true", help="write the playlist even if the size guard fails")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        config = load_config(args.config)
        run(config, base_dir=args.config.resolve().parent, force=args.force)
    except (ConfigError, BuildError) as e:
        logging.error("ERROR: %s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
