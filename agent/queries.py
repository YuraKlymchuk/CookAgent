"""Read-only recipe queries over the SQLite database.

Every function takes an open connection (dependency injection), uses only
parameterised SQL with whitelisted column names, and caps how much data it
returns so a vague request can never flood the model's context.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass

from data_load.ingredients import ingredient_terms, normalize_ingredient
from data_load.schema import DIET_FLAGS

DEFAULT_LIMIT = 5
MAX_LIMIT = 10
DESCRIPTION_CHARS = 100

DIETS: tuple[str, ...] = tuple(flag.removeprefix("is_") for flag in DIET_FLAGS)
DIFFICULTIES = ("easy", "medium", "hard")
HEALTH_LEVELS = ("healthy", "moderate", "unhealthy")

_SEED_MULTIPLIER = 2654435761
_SEED_MODULUS = 1000003


@dataclass(frozen=True)
class Options:
    """Values that actually occur in the database (used as tool-schema enums)."""

    cuisines: tuple[str, ...]
    courses: tuple[str, ...]
    tastes: tuple[str, ...]
    main_ingredients: tuple[str, ...]


def load_options(connection: sqlite3.Connection) -> Options:
    def distinct_tags(kind: str) -> tuple[str, ...]:
        rows = connection.execute(
            "SELECT DISTINCT value FROM recipe_tags WHERE kind = ? ORDER BY value", (kind,)
        )
        return tuple(row[0] for row in rows)

    mains = connection.execute("SELECT DISTINCT main_ingredient FROM recipes ORDER BY 1")
    return Options(
        cuisines=distinct_tags("cuisine"),
        courses=distinct_tags("course"),
        tastes=distinct_tags("taste"),
        main_ingredients=tuple(row[0] for row in mains),
    )


def clamp_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    return max(1, min(int(limit), MAX_LIMIT))


def to_terms(words: list[str] | None) -> list[str]:
    """User words -> the single-word terms stored in the ``ingredients`` table."""
    names = (normalize_ingredient(word) for word in words or [])
    return list(ingredient_terms(tuple(name for name in names if name)))


def _order_by(seed: int | None) -> str:
    """Random order, or a reproducible pseudo-random one when ``seed`` is given (tests)."""
    if seed is None:
        return "random()"
    # The seed changes the multiplier, so each seed gives a different permutation of ids.
    return f"((r.id * ({_SEED_MULTIPLIER} + {int(seed)} * 7919)) % {_SEED_MODULUS})"


def _fts_query(text: str) -> str | None:
    words = re.findall(r"\w+", text)
    return " ".join(f'"{word}"' for word in words) or None


def _diet_clauses(diets: list[str] | None) -> list[str]:
    clauses = []
    for diet in diets or []:
        if diet not in DIETS:
            raise ValueError(f"Unknown diet {diet!r}. Allowed: {', '.join(DIETS)}")
        clauses.append(f"r.is_{diet} = 1")
    return clauses


def _tag_clause(kind: str, value: str) -> tuple[str, list]:
    return (
        "r.id IN (SELECT recipe_id FROM recipe_tags WHERE kind = ? AND value = ?)",
        [kind, value],
    )


def _term_subquery(placeholders: str) -> str:
    return (
        "SELECT ri.recipe_id FROM recipe_ingredients ri "
        f"JOIN ingredients i ON i.id = ri.ingredient_id WHERE i.name IN ({placeholders})"
    )


NO_MATCH_HINT = (
    "No recipes matched. Relax soft filters first (time, difficulty, taste, cuisine, required "
    "ingredients) and tell the user; never drop diet/allergy constraints."
)


def _with_hint(result: dict) -> dict:
    """Steer the model when a search is empty, so it does not silently drop safety filters."""
    return {**result, "hint": NO_MATCH_HINT} if result["total_matches"] == 0 else result


def card(row: sqlite3.Row | tuple) -> dict:
    """Compact recipe summary: the only fields a search result carries."""
    description = (row["description"] or "").strip()
    if len(description) > DESCRIPTION_CHARS:
        description = description[: DESCRIPTION_CHARS - 1].rstrip() + "…"
    return {
        "id": row["id"],
        "title": row["title"],
        "total_min": row["total_min"],
        "difficulty": row["difficulty"],
        "healthiness_score": row["healthiness_score"],
        "description": description,
    }


def search_recipes(
    connection: sqlite3.Connection,
    *,
    diets: list[str] | None = None,
    main_ingredient: str | None = None,
    include_ingredients: list[str] | None = None,
    exclude_ingredients: list[str] | None = None,
    cuisine: str | None = None,
    course: str | None = None,
    tastes: list[str] | None = None,
    max_total_min: int | None = None,
    difficulty: str | None = None,
    health_level: str | None = None,
    text: str | None = None,
    limit: int | None = None,
    seed: int | None = None,
) -> dict:
    """Filter recipes and return a small random sample of the matches."""
    clauses = _diet_clauses(diets)
    params: list = []

    if main_ingredient:
        clauses.append("r.main_ingredient = ?")
        params.append(main_ingredient)
    if difficulty:
        if difficulty not in DIFFICULTIES:
            raise ValueError(f"Unknown difficulty {difficulty!r}. Allowed: {', '.join(DIFFICULTIES)}")
        clauses.append("r.difficulty = ?")
        params.append(difficulty)
    if health_level:
        if health_level not in HEALTH_LEVELS:
            raise ValueError(f"Unknown health_level {health_level!r}. Allowed: {', '.join(HEALTH_LEVELS)}")
        clauses.append("r.health_level = ?")
        params.append(health_level)
    if max_total_min is not None:
        clauses.append("r.total_min <= ?")
        params.append(int(max_total_min))

    tag_filters = [("cuisine", cuisine)] if cuisine else []
    tag_filters += [("course", course)] if course else []
    tag_filters += [("taste", taste) for taste in tastes or []]
    for kind, value in tag_filters:
        clause, tag_params = _tag_clause(kind, value)
        clauses.append(clause)
        params += tag_params

    for term in to_terms(include_ingredients):
        clauses.append(f"r.id IN ({_term_subquery('?')})")
        params.append(term)
    excluded = to_terms(exclude_ingredients)
    if excluded:
        marks = ", ".join("?" * len(excluded))
        clauses.append(f"r.id NOT IN ({_term_subquery(marks)})")
        params += excluded

    match = _fts_query(text) if text else None
    if match:
        clauses.append("r.id IN (SELECT rowid FROM recipes_fts WHERE recipes_fts MATCH ?)")
        params.append(match)

    where = " AND ".join(clauses) or "1"
    total = connection.execute(f"SELECT COUNT(*) FROM recipes r WHERE {where}", params).fetchone()[0]
    rows = connection.execute(
        "SELECT r.id, r.title, r.total_min, r.difficulty, r.healthiness_score, r.description "
        f"FROM recipes r WHERE {where} ORDER BY {_order_by(seed)} LIMIT ?",
        [*params, clamp_limit(limit)],
    ).fetchall()
    result = {"total_matches": total, "returned": len(rows), "recipes": [card(row) for row in rows]}
    return _with_hint(result)


def match_pantry(
    connection: sqlite3.Connection,
    *,
    ingredients: list[str],
    diets: list[str] | None = None,
    limit: int | None = None,
    seed: int | None = None,
) -> dict:
    """Rank recipes by how many of the user's ingredients they use (ties: fewer extras, then random)."""
    terms = to_terms(ingredients)
    if not terms:
        raise ValueError("Provide at least one ingredient")
    marks = ", ".join("?" * len(terms))
    diet_sql = "".join(f" AND {clause}" for clause in _diet_clauses(diets))

    rows = connection.execute(
        "SELECT r.id, r.title, r.total_min, r.difficulty, r.healthiness_score, r.description, "
        "COUNT(*) AS matched, "
        "(SELECT COUNT(*) FROM recipe_ingredients x WHERE x.recipe_id = r.id) AS recipe_terms, "
        "COUNT(*) OVER () AS total "
        "FROM recipe_ingredients ri "
        "JOIN ingredients i ON i.id = ri.ingredient_id "
        "JOIN recipes r ON r.id = ri.recipe_id "
        f"WHERE i.name IN ({marks}){diet_sql} "
        "GROUP BY r.id "
        f"ORDER BY matched DESC, recipe_terms - matched ASC, {_order_by(seed)} LIMIT ?",
        [*terms, clamp_limit(limit)],
    ).fetchall()

    recipes = [
        {**card(row), "matched_ingredients": row["matched"], "extra_ingredients": row["recipe_terms"] - row["matched"]}
        for row in rows
    ]
    return _with_hint(
        {
            "searched_terms": terms,
            "total_matches": rows[0]["total"] if rows else 0,
            "returned": len(recipes),
            "recipes": recipes,
        }
    )


def get_recipe(connection: sqlite3.Connection, recipe_id: int) -> dict:
    """Full details of one recipe, or an ``error`` entry if the id is unknown."""
    row = connection.execute("SELECT * FROM recipes WHERE id = ?", (int(recipe_id),)).fetchone()
    if row is None:
        return {"error": f"Recipe {recipe_id} not found"}

    tags = connection.execute(
        "SELECT kind, value FROM recipe_tags WHERE recipe_id = ? ORDER BY kind, value", (row["id"],)
    ).fetchall()

    def tag_values(kind: str) -> list[str]:
        return [tag["value"] for tag in tags if tag["kind"] == kind]

    return {
        "id": row["id"],
        "title": row["title"],
        "category": row["category"],
        "description": row["description"],
        "prep_min": row["prep_min"],
        "cook_min": row["cook_min"],
        "total_min": row["total_min"],
        "difficulty": row["difficulty"],
        "healthiness_score": row["healthiness_score"],
        "health_level": row["health_level"],
        "main_ingredient": row["main_ingredient"],
        "primary_taste": row["primary_taste"],
        "diets": [diet for diet in DIETS if row[f"is_{diet}"]],
        "cuisines": tag_values("cuisine"),
        "courses": tag_values("course"),
        "tastes": tag_values("taste"),
        "health_flags": tag_values("health_flag"),
        "ingredients": json.loads(row["ingredients_json"]),
        "directions": json.loads(row["directions_json"]),
    }
