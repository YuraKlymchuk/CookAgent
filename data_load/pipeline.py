"""Orchestration: slim dataset -> SQLite file, plus the app start-up hook.

Runtime path uses only the standard library (no pandas), so a Hugging Face
Space needs no extra requirements to build the database on start.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import os
import sqlite3
import tempfile
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from . import database
from .config import DB_PATH_ENV_VAR, DEFAULT_DB_PATH, SLIM_DATASET
from .schema import SCHEMA_VERSION
from .transform import Record, build_tables


@dataclass(frozen=True)
class BuildReport:
    db_path: Path
    recipes: int
    tags: int
    ingredients: int
    size_bytes: int
    seconds: float


def read_records(path: Path) -> Iterator[Record]:
    """Stream rows from a ``.csv`` or ``.csv.gz`` file as dicts."""
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, mode="rt", encoding="utf-8", newline="") as handle:
        yield from csv.DictReader(handle)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def resolve_db_path(explicit: Path | None = None) -> Path:
    """Explicit argument > env var > project default; falls back to the temp dir if unwritable."""
    chosen = explicit or Path(os.environ.get(DB_PATH_ENV_VAR, DEFAULT_DB_PATH))
    try:
        chosen.parent.mkdir(parents=True, exist_ok=True)
        probe = chosen.parent / ".write_probe"
        probe.touch()
        probe.unlink()
    except OSError:
        return Path(tempfile.gettempdir()) / chosen.name
    return chosen


def build_database(source: Path = SLIM_DATASET, db_path: Path | None = None) -> BuildReport:
    """Build the database from scratch.

    Written to a temporary file and swapped in atomically, so a crash or a
    concurrent reader never sees a half-built database.
    """
    started = time.perf_counter()
    target = resolve_db_path(db_path)
    staging = target.with_name(target.name + ".tmp")
    staging.unlink(missing_ok=True)

    tables = build_tables(read_records(source))

    with closing(database.connect_for_build(staging)) as connection:
        database.create_schema(connection)
        database.write_tables(connection, tables)
        database.create_indexes(connection)
        database.write_meta(
            connection,
            {
                "schema_version": SCHEMA_VERSION,
                "source_sha256": file_sha256(source),
                "recipe_count": str(len(tables.recipes)),
                "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
        )
        database.optimize(connection)

    os.replace(staging, target)
    return BuildReport(
        db_path=target,
        recipes=len(tables.recipes),
        tags=len(tables.tags),
        ingredients=len(tables.ingredients),
        size_bytes=target.stat().st_size,
        seconds=time.perf_counter() - started,
    )


def is_up_to_date(db_path: Path, source: Path) -> bool:
    """True when the database exists and was built from this exact source and schema."""
    if not db_path.exists():
        return False
    try:
        with closing(database.open_readonly(db_path)) as connection:
            return (
                database.read_meta(connection, "schema_version") == SCHEMA_VERSION
                and database.read_meta(connection, "source_sha256") == file_sha256(source)
            )
    except sqlite3.Error:
        return False


def ensure_database(
    source: Path = SLIM_DATASET, db_path: Path | None = None, *, force: bool = False
) -> Path:
    """Start-up hook for ``app.py``: build the database only if it is missing or stale.

    On Hugging Face Spaces the disk is wiped on every restart, so this runs a
    fresh build each time; locally it is a cheap no-op after the first run.
    """
    if not source.exists():
        raise FileNotFoundError(
            f"Dataset not found: {source}. Create it with `python -m data_load prepare`."
        )
    target = resolve_db_path(db_path)
    if not force and is_up_to_date(target, source):
        return target
    return build_database(source, target).db_path
