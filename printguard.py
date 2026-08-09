#!/usr/bin/env python3
"""Compatibility entry point for PrintGuard."""

from run_printguard import main, manual_pause_test, parse_args


if __name__ == "__main__":
    args = parse_args()
    import asyncio

    asyncio.run(manual_pause_test() if args.test_pause else main())
