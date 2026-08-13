"""Thin human wrapper. Not an acceptance criterion.

This CLI is a debug aid only. Stage acceptance is the automated
chip_sim.Client V0 script.
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "chip-sim debug CLI (not used for P0 acceptance; "
            "use tests/test_v0_loop.py)"
        )
    )
    parser.add_argument("rest", nargs="*")
    parser.parse_args(argv)
    print(
        "chip-sim CLI is a human debug aid only and is not implemented in P0.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
