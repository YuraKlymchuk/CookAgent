"""Development-time step: shrink the raw CSV into a small ``.csv.gz``.

Order of operations (cheapest first, so later steps see less data):

1. read only the kept columns  - skips ~60% of the file's bytes
2. drop incomplete recipes
3. drop duplicate titles       - the source repeats a recipe once per category
4. evenly sample down to the size cap

This module needs pandas. The runtime path (``build_database``) does not, so
this module is deliberately not imported from ``data_load/__init__.py``.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .config import KEPT_COLUMNS, REQUIRED_COLUMNS, PrepareConfig

_EMPTY_VALUES = {"", "[]"}
_GZIP_OPTIONS = {"method": "gzip", "compresslevel": 9, "mtime": 0}  # mtime=0 -> reproducible bytes
_SAFETY_MARGIN = 0.99
_SHRINK_FACTOR = 0.97


@dataclass(frozen=True)
class FittedDataset:
    frame: pd.DataFrame
    payload: bytes


@dataclass(frozen=True)
class PrepareReport:
    rows_read: int
    rows_complete: int
    rows_unique: int
    rows_written: int
    bytes_written: int
    destination: Path


def read_kept_columns(path: Path, columns: tuple[str, ...] = KEPT_COLUMNS) -> pd.DataFrame:
    return pd.read_csv(path, usecols=list(columns))[list(columns)]


def drop_incomplete(frame: pd.DataFrame, columns: tuple[str, ...] = REQUIRED_COLUMNS) -> pd.DataFrame:
    usable = frame[list(columns)].notna().all(axis=1)
    for column in columns:
        usable &= ~frame[column].astype(str).str.strip().isin(_EMPTY_VALUES)
    return frame[usable]


def drop_duplicate_titles(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.drop_duplicates(subset="recipe_title", keep="first")


def evenly_spaced_sample(frame: pd.DataFrame, size: int) -> pd.DataFrame:
    """Pick ``size`` rows at a regular stride, preserving the original order.

    The source is sorted by category, so slicing off the head would keep only
    a few categories; a regular stride keeps the category mix intact.
    """
    total = len(frame)
    if size >= total:
        return frame
    positions = [index * total // size for index in range(size)]
    return frame.iloc[positions]


def to_gzip_bytes(frame: pd.DataFrame) -> bytes:
    buffer = io.BytesIO()
    frame.to_csv(buffer, index=False, compression=_GZIP_OPTIONS)
    return buffer.getvalue()


def fit_to_size(frame: pd.DataFrame, max_bytes: int) -> FittedDataset:
    """Return the largest evenly spaced sample whose gzip CSV fits ``max_bytes``."""
    payload = to_gzip_bytes(frame)
    if len(payload) <= max_bytes:
        return FittedDataset(frame, payload)

    size = int(len(frame) * max_bytes / len(payload) * _SAFETY_MARGIN)
    while size > 0:
        sample = evenly_spaced_sample(frame, size)
        payload = to_gzip_bytes(sample)
        if len(payload) <= max_bytes:
            return FittedDataset(sample, payload)
        size = int(size * _SHRINK_FACTOR)
    raise ValueError(f"Cannot fit any rows into {max_bytes} bytes")


def prepare_dataset(config: PrepareConfig = PrepareConfig()) -> PrepareReport:
    """Orchestrator: raw CSV -> slim ``.csv.gz`` that fits ``config.max_bytes``."""
    raw = read_kept_columns(config.source)
    complete = drop_incomplete(raw)
    unique = drop_duplicate_titles(complete)
    fitted = fit_to_size(unique, config.max_bytes)

    config.destination.parent.mkdir(parents=True, exist_ok=True)
    config.destination.write_bytes(fitted.payload)

    return PrepareReport(
        rows_read=len(raw),
        rows_complete=len(complete),
        rows_unique=len(unique),
        rows_written=len(fitted.frame),
        bytes_written=len(fitted.payload),
        destination=config.destination,
    )
