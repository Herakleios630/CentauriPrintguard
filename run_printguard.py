#!/usr/bin/env python3
"""PrintGuard application entry point."""

import asyncio
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

# Hide noisy FFmpeg decoder warnings while keeping native errors visible.
os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "16")

from printguard.monitor import main, manual_pause_test, parse_args


log_dir = Path("logs")
log_dir.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler(log_dir / f"printguard-{datetime.now():%Y%m%d}.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(manual_pause_test() if args.test_pause else main())
