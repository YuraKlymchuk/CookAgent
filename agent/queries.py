"""Read-only recipe queries over the SQLite database.

Every function takes an open connection (dependency injection), uses only
parameterised SQL with whitelisted column names, and caps how much data it
returns so a vague request can never flood the model's context.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, replace

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


def to_term_groups(words: list[str] | None) -> list[tuple[str, ...]]:
    """One group of terms per user ingredient: "soy sauce" -> (soy, sauce), which counts as one."""
    groups = (ingredient_terms((name,)) for name in (normalize_ingredient(w) for w in words or []) if name)
    return list(dict.fromkeys(group for group in groups if group))


def _order_by(seed: int | None, id_column: str = "r.id") -> str:
    """Random order, or a reproducible pseudo-random one when ``seed`` is given (tests)."""
    if seed is None:
        return "random()"
    # The seed changes the multiplier, so each seed gives a different permutation of ids.
    return f"(({id_column} * ({_SEED_MULTIPLIER} + {int(seed)} * 7919)) % {_SEED_MODULUS})"


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
    "No recipes matched even after the tool relaxed the soft criteria, so the diet/allergy "
    "constraints or ingredients are too restrictive. Tell the user and ask what to change; "
    "never drop diet/allergy constraints yourself."
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


@dataclass(frozen=True)
class Filters:
    """Search criteria. ``diets`` and ``exclude`` are hard (safety); the rest can be relaxed."""

    diets: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    include: tuple[tuple[str, ...], ...] = ()  # one group of terms per ingredient
    main_ingredient: str | None = None
    cuisine: str | None = None
    course: str | None = None
    tastes: tuple[str, ...] = ()
    max_total_min: int | None = None
    difficulty: str | None = None
    health_level: str | None = None
    text: str | None = None


# When nothing matches, criteria are relaxed in this order. Ingredients come first (as a
# partial, best-coverage match) because they are what the user is most likely to care about,
# then the least important criteria go first. Diets and excluded ingredients are never relaxed.
RELAX_ORDER = (
    "text",
    "health_level",
    "difficulty",
    "tastes",
    "cuisine",
    "course",
    "main_ingredient",
    "max_total_min",
)
_INGREDIENTS_STEP = "include_ingredients"


def _where(f: Filters, *, partial: bool) -> tuple[str, list]:
    clauses = _diet_clauses(list(f.diets))
    params: list = []

    if f.main_ingredient:
        clauses.append("r.main_ingredient = ?")
        params.append(f.main_ingredient)
    if f.difficulty:
        if f.difficulty not in DIFFICULTIES:
            raise ValueError(f"Unknown difficulty {f.difficulty!r}. Allowed: {', '.join(DIFFICULTIES)}")
        clauses.append("r.difficulty = ?")
        params.append(f.difficulty)
    if f.health_level:
        if f.health_level not in HEALTH_LEVELS:
            raise ValueError(f"Unknown health_level {f.health_level!r}. Allowed: {', '.join(HEALTH_LEVELS)}")
        clauses.append("r.health_level = ?")
        params.append(f.health_level)
    if f.max_total_min is not None:
        clauses.append("r.total_min <= ?")
        params.append(int(f.max_total_min))

    tag_filters = [("cuisine", f.cuisine)] if f.cuisine else []
    tag_filters += [("course", f.course)] if f.course else []
    tag_filters += [("taste", taste) for taste in f.tastes]
    for kind, value in tag_filters:
        clause, tag_params = _tag_clause(kind, value)
        clauses.append(clause)
        params += tag_params

    if not partial:  # every ingredient must be present (partial mode ranks by coverage instead)
        for group in f.include:
            for term in group:
                clauses.append(f"r.id IN ({_term_subquery('?')})")
                params.append(term)
    if f.exclude:
        marks = ", ".join("?" * len(f.exclude))
        clauses.append(f"r.id NOT IN ({_term_subquery(marks)})")
        params += f.exclude

    match = _fts_query(f.text) if f.text else None
    if match:
        clauses.append("r.id IN (SELECT rowid FROM recipes_fts WHERE recipes_fts MATCH ?)")
        params.append(match)

    return " AND ".join(clauses) or "1", params


def _coverage_sql(groups: tuple[tuple[str, ...], ...]) -> tuple[str, list]:
    """SQL counting how many of the user's ingredients (groups) a recipe contains."""
    exists = (
        "EXISTS (SELECT 1 FROM recipe_ingredients ri JOIN ingredients i ON i.id = ri.ingredient_id "
        "WHERE ri.recipe_id = r.id AND i.name = ?)"
    )
    parts = [f"(CASE WHEN {' AND '.join([exists] * len(group))} THEN 1 ELSE 0 END)" for group in groups]
    return " + ".join(parts), [term for group in groups for term in group]


def _run(
    connection: sqlite3.Connection, f: Filters, *, partial: bool, limit: int | None, seed: int | None
) -> dict:
    where, params = _where(f, partial=partial)
    columns = "r.id, r.title, r.total_min, r.difficulty, r.healthiness_score, r.description"

    if partial and f.include:
        coverage, coverage_params = _coverage_sql(f.include)
        inner = f"SELECT {columns}, ({coverage}) AS matched FROM recipes r WHERE {where}"
        inner_params = [*coverage_params, *params]
        total = connection.execute(
            f"SELECT COUNT(*) FROM ({inner}) WHERE matched >= 1", inner_params
        ).fetchone()[0]
        rows = connection.execute(
            f"SELECT * FROM ({inner}) WHERE matched >= 1 "
            f"ORDER BY matched DESC, {_order_by(seed, 'id')} LIMIT ?",
            [*inner_params, clamp_limit(limit)],
        ).fetchall()
        recipes = [{**card(row), "matched_ingredients": row["matched"]} for row in rows]
        result = {"total_matches": total, "returned": len(recipes), "recipes": recipes}
        result["searched_ingredients"] = [" ".join(group) for group in f.include]
        return result

    total = connection.execute(f"SELECT COUNT(*) FROM recipes r WHERE {where}", params).fetchone()[0]
    rows = connection.execute(
        f"SELECT {columns} FROM recipes r WHERE {where} ORDER BY {_order_by(seed)} LIMIT ?",
        [*params, clamp_limit(limit)],
    ).fetchall()
    return {"total_matches": total, "returned": len(rows), "recipes": [card(row) for row in rows]}


def _relaxation_note(f: Filters, relaxed: list[str]) -> str:
    """Plain-language explanation the model is told to pass on to the user."""
    notes = []
    if _INGREDIENTS_STEP in relaxed:
        wanted = ", ".join(" ".join(group) for group in f.include)
        notes.append(
            f"No recipe contains all of: {wanted}. These contain the most of them "
            f"(matched_ingredients out of {len(f.include)})."
        )
    dropped = [name for name in relaxed if name != _INGREDIENTS_STEP]
    if dropped:
        notes.append("Ignored because nothing matched them: " + ", ".join(dropped) + ".")
    return " ".join(notes)


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
    """Filter recipes and return a small random sample of the matches.

    If nothing matches, criteria are relaxed automatically by priority (see
    ``RELAX_ORDER``) and the result lists what was relaxed under ``relaxed``.
    """
    filters = Filters(
        diets=tuple(diets or ()),
        exclude=tuple(to_terms(exclude_ingredients)),
        include=tuple(to_term_groups(include_ingredients)),
        main_ingredient=main_ingredient or None,
        cuisine=cuisine or None,
        course=course or None,
        tastes=tuple(tastes or ()),
        max_total_min=max_total_min,
        difficulty=difficulty or None,
        health_level=health_level or None,
        text=text or None,
    )
    partial = False
    relaxed: list[str] = []
    result = _run(connection, filters, partial=partial, limit=limit, seed=seed)

    # Step 1: several ingredients that never occur together -> best partial match instead.
    if result["total_matches"] == 0 and len(filters.include) > 1:
        partial = True
        relaxed.append(_INGREDIENTS_STEP)
        result = _run(connection, filters, partial=partial, limit=limit, seed=seed)

    # Step 2..n: drop the least important remaining criteria one by one.
    for name in RELAX_ORDER:
        if result["total_matches"] > 0:
            break
        if getattr(filters, name):
            filters = replace(filters, **{name: None if name != "tastes" else ()})
            relaxed.append(name)
            result = _run(connection, filters, partial=partial, limit=limit, seed=seed)

    if relaxed and result["total_matches"] > 0:
        result["relaxed"] = relaxed
        result["note"] = _relaxation_note(filters, relaxed)
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


def cards_by_ids(connection: sqlite3.Connection, ids: list[int]) -> list[dict]:
    """Cards for the given ids in the given order; unknown ids are silently dropped."""
    wanted = list(dict.fromkeys(int(i) for i in ids))[:MAX_LIMIT]
    if not wanted:
        return []
    marks = ", ".join("?" * len(wanted))
    rows = connection.execute(
        "SELECT r.id, r.title, r.total_min, r.difficulty, r.healthiness_score, r.description "
        f"FROM recipes r WHERE r.id IN ({marks})",
        wanted,
    ).fetchall()
    by_id = {row["id"]: card(row) for row in rows}
    return [by_id[i] for i in wanted if i in by_id]


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
