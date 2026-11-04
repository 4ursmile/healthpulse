"""Worker supervisor entry point: `python -m healthpulse.worker` (or `hp worker`)."""
from __future__ import annotations

import argparse
import logging

from .bootstrap import init
from .config import get_settings
from .core.jobs import run_worker


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="HealthPulse job worker")
    ap.add_argument("--concurrency", type=int, default=None)
    ap.add_argument("--types", default=None, help="comma-separated job types this worker accepts (e.g. ingest,probe)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    init()
    run_worker(a.concurrency or get_settings().worker_concurrency, set(a.types.split(",")) if a.types else None)


if __name__ == "__main__":
    main()
