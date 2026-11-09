"""Command line: ``python -m data_load prepare`` / ``python -m data_load build``."""

from __future__ import annotations

import argparse
from pathlib import Path

from .config import BYTES_PER_MB, DEFAULT_MAX_MB, RAW_DATASET, SLIM_DATASET, PrepareConfig


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m data_load", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser("prepare", help="shrink the raw CSV into a .csv.gz that fits a size cap")
    prepare.add_argument("--source", type=Path, default=RAW_DATASET)
    prepare.add_argument("--destination", type=Path, default=SLIM_DATASET)
    prepare.add_argument("--max-mb", type=float, default=DEFAULT_MAX_MB, help="size cap in MiB (default: %(default)s)")

    build = commands.add_parser("build", help="build the SQLite database from the slim dataset")
    build.add_argument("--source", type=Path, default=SLIM_DATASET)
    build.add_argument("--db", type=Path, default=None, help="output path (default: data/recipes.db)")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)

    if args.command == "prepare":
        from .prepare import prepare_dataset  # imported lazily: needs pandas

        report = prepare_dataset(
            PrepareConfig(args.source, args.destination, int(args.max_mb * BYTES_PER_MB))
        )
        print(
            f"read {report.rows_read} -> complete {report.rows_complete} -> unique titles {report.rows_unique} "
            f"-> written {report.rows_written}\n"
            f"{report.destination} ({report.bytes_written / BYTES_PER_MB:.2f} MiB)"
        )
    else:
        from .pipeline import build_database

        report = build_database(args.source, args.db)
        print(
            f"{report.db_path} ({report.size_bytes / BYTES_PER_MB:.2f} MiB): {report.recipes} recipes, "
            f"{report.ingredients} ingredients, {report.tags} tags in {report.seconds:.1f}s"
        )


if __name__ == "__main__":
    main()
