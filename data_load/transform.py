"""Pure transformation: CSV records -> rows for the SQLite tables.

Nothing here touches the filesystem or the database, so it is easy to test.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable, Mapping

from .ingredients import ingredient_terms, normalize_ingredients
from .schema import DIET_FLAGS

Record = Mapping[str, str]

# recipe_tags.kind -> CSV column holding a JSON list of labels
TAG_COLUMNS = {
    "cuisine": "cuisine_list",
    "course": "course_list",
    "taste": "tastes",
    "health_flag": "health_flags",
}
_PLACEHOLDER_LABELS = frozenset({"unknown", ""})


@dataclass(frozen=True)
class RecipeTables:
    recipes: list[dict]
    tags: list[tuple[str, str, int]]  # (kind, value, recipe_id)
    ingredients: list[tuple[int, str]]  # (ingredient_id, single-word term)
    recipe_ingredients: list[tuple[int, int]]  # (ingredient_id, recipe_id)
    search_rows: list[tuple[int, str, str, str]]  # (recipe_id, title, category, ingredients)


def parse_json_list(raw: str) -> list[str]:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [str(item) for item in value] if isinstance(value, list) else []


def parse_bool(raw: str) -> int:
    return 1 if str(raw).strip().lower() == "true" else 0


def to_recipe_row(recipe_id: int, record: Record) -> dict:
    prep = int(record["est_prep_time_min"])
    cook = int(record["est_cook_time_min"])
    return {
        "id": recipe_id,
        "title": record["recipe_title"].strip(),
        "category": record["category"],
        "subcategory": record["subcategory"],
        "description": record["description"],
        "ingredients_json": record["ingredients"],
        "directions_json": record["directions"],
        "num_ingredients": int(record["num_ingredients"]),
        "num_steps": int(record["num_steps"]),
        "prep_min": prep,
        "cook_min": cook,
        "total_min": prep + cook,
        "cook_speed": record["cook_speed"],
        "difficulty": record["difficulty"],
        "healthiness_score": int(record["healthiness_score"]),
        "health_level": record["health_level"],
        "main_ingredient": record["main_ingredient"],
        "primary_taste": record["primary_taste"],
        **{flag: parse_bool(record[flag]) for flag in DIET_FLAGS},
    }


def to_tags(recipe_id: int, record: Record) -> list[tuple[str, str, int]]:
    labels = (
        (kind, label.strip().lower())
        for kind, column in TAG_COLUMNS.items()
        for label in parse_json_list(record[column])
    )
    unique = dict.fromkeys(
        (kind, label, recipe_id) for kind, label in labels if label not in _PLACEHOLDER_LABELS
    )
    return list(unique)


def to_ingredient_names(record: Record) -> tuple[str, ...]:
    return normalize_ingredients(parse_json_list(record["ingredients_canonical"]))


def to_ingredient_terms(record: Record) -> tuple[str, ...]:
    return ingredient_terms(to_ingredient_names(record))


def build_tables(records: Iterable[Record]) -> RecipeTables:
    """Turn CSV records into table rows; ids are assigned in input order (from 1)."""
    recipes: list[dict] = []
    tags: list[tuple[str, str, int]] = []
    recipe_ingredients: list[tuple[int, int]] = []
    search_rows: list[tuple[int, str, str, str]] = []
    ingredient_ids: dict[str, int] = {}

    for recipe_id, record in enumerate(records, start=1):
        row = to_recipe_row(recipe_id, record)
        names = to_ingredient_names(record)

        recipes.append(row)
        tags.extend(to_tags(recipe_id, record))
        search_rows.append((recipe_id, row["title"], row["category"], " ".join(names)))
        for term in ingredient_terms(names):
            ingredient_id = ingredient_ids.setdefault(term, len(ingredient_ids) + 1)
            recipe_ingredients.append((ingredient_id, recipe_id))

    ingredients = [(ingredient_id, name) for name, ingredient_id in ingredient_ids.items()]
    return RecipeTables(recipes, tags, ingredients, recipe_ingredients, search_rows)
