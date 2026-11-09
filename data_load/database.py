"""Thin SQLite helpers: each function does exactly one write step."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Mapping

from .schema import CREATE_INDEXES, CREATE_TABLES, RECIPE_COLUMNS
from .transform import RecipeTables

_INSERT_RECIPE = (
    f"INSERT INTO recipes ({', '.join(RECIPE_COLUMNS)}) "
    f"VALUES ({', '.join(':' + column for column in RECIPE_COLUMNS)})"
)


def connect_for_build(path: Path) -> sqlite3.Connection:
    """Fast, non-durable settings: the file is rebuilt from source if it breaks."""
    connection = sqlite3.connect(path)
    connection.executescript(
        "PRAGMA journal_mode = OFF; PRAGMA synchronous = OFF; PRAGMA foreign_keys = ON;"
    )
    return connection


def open_readonly(path: Path) -> sqlite3.Connection:
    """Connection for the agent: read-only, safe to share across Gradio threads."""
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    return connection


def create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(CREATE_TABLES)


def write_tables(connection: sqlite3.Connection, tables: RecipeTables) -> None:
    with connection:
        connection.executemany(_INSERT_RECIPE, tables.recipes)
        connection.executemany(
            "INSERT INTO recipe_tags (kind, value, recipe_id) VALUES (?, ?, ?)", tables.tags
        )
        connection.executemany("INSERT INTO ingredients (id, name) VALUES (?, ?)", tables.ingredients)
        connection.executemany(
            "INSERT INTO recipe_ingredients (ingredient_id, recipe_id) VALUES (?, ?)",
            tables.recipe_ingredients,
        )
        connection.executemany(
            "INSERT INTO recipes_fts (rowid, title, category, ingredients) VALUES (?, ?, ?, ?)",
            tables.search_rows,
        )


def create_indexes(connection: sqlite3.Connection) -> None:
    with connection:
        for statement in CREATE_INDEXES:
            connection.execute(statement)


def write_meta(connection: sqlite3.Connection, items: Mapping[str, str]) -> None:
    with connection:
        connection.executemany("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", items.items())


def optimize(connection: sqlite3.Connection) -> None:
    """Refresh planner statistics and merge FTS segments for faster reads."""
    connection.execute("INSERT INTO recipes_fts (recipes_fts) VALUES ('optimize')")
    connection.execute("ANALYZE")
    connection.commit()


def read_meta(connection: sqlite3.Connection, key: str) -> str | None:
    row = connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None
