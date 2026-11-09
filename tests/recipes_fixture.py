"""A handful of hand-made recipes with known properties, in the raw CSV layout."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from data_load.config import KEPT_COLUMNS

_DIET_FLAGS = ("vegan", "vegetarian", "halal", "kosher", "nut_free", "dairy_free", "gluten_free")


def make_recipe(title: str, ingredients: list[str], *, diets: tuple[str, ...] = (), **overrides) -> dict:
    """Build one CSV record; ``diets`` lists the is_* flags that are True."""
    record = {
        "recipe_title": title,
        "category": "Test",
        "subcategory": "Test",
        "description": f"{title} is a test recipe.",
        "ingredients": json.dumps([f"1 cup {name}" for name in ingredients]),
        "directions": json.dumps(["Mix everything.", "Cook until done."]),
        "num_ingredients": len(ingredients),
        "num_steps": 2,
        "ingredients_canonical": json.dumps(ingredients),
        "cuisine_list": json.dumps(["american"]),
        "course_list": json.dumps(["main"]),
        "tastes": json.dumps(["savory"]),
        "primary_taste": "savory",
        "cook_speed": "fast",
        "est_prep_time_min": 10,
        "est_cook_time_min": 10,
        "difficulty": "easy",
        "healthiness_score": 70,
        "health_flags": json.dumps([]),
        "main_ingredient": "plant",
        "health_level": "moderate",
        **{f"is_{flag}": flag in diets for flag in _DIET_FLAGS},
    }
    record.update(overrides)
    return record


RECIPES = [
    make_recipe(
        "Vegan Chickpea Salad",
        ["chickpeas", "tomatoes", "olive oil", "lemon juice"],
        diets=("vegan", "vegetarian", "halal", "kosher", "nut_free", "dairy_free", "gluten_free"),
        cuisine_list=json.dumps(["mediterranean"]),
        course_list=json.dumps(["main"]),
        est_cook_time_min=5,
        health_level="healthy",
        healthiness_score=90,
    ),
    make_recipe(
        "Chicken Rice Bowl",
        ["skinless boneless chicken breast", "rice", "garlic", "soy sauce"],
        diets=("halal", "nut_free", "dairy_free"),
        cuisine_list=json.dumps(["asian"]),
        main_ingredient="poultry",
        est_cook_time_min=30,
        difficulty="medium",
    ),
    make_recipe(
        "Peanut Noodles",
        ["peanut butter", "noodles", "soy sauce", "garlic"],
        diets=("vegetarian", "halal", "dairy_free"),
        cuisine_list=json.dumps(["asian"]),
        tastes=json.dumps(["savory", "spicy"]),
        est_cook_time_min=15,
    ),
    make_recipe(
        "Pork Ribs",
        ["pork ribs", "brown sugar", "garlic"],
        diets=("nut_free", "dairy_free", "gluten_free"),
        main_ingredient="red_meat",
        est_cook_time_min=140,
        difficulty="hard",
        health_level="unhealthy",
        healthiness_score=30,
    ),
    make_recipe(
        "Fluffy Pancakes",
        ["all-purpose flour", "eggs", "milk", "butter", "white sugar"],
        diets=("vegetarian", "halal", "kosher", "nut_free"),
        course_list=json.dumps(["breakfast"]),
        tastes=json.dumps(["sweet"]),
        primary_taste="sweet",
        main_ingredient="egg_dairy",
        est_cook_time_min=20,
    ),
    make_recipe(
        "Shrimp Garlic Pasta",
        ["shrimp", "pasta", "garlic", "olive oil"],
        diets=("nut_free", "dairy_free"),
        cuisine_list=json.dumps(["italian"]),
        main_ingredient="seafood",
        est_cook_time_min=20,
    ),
    make_recipe(
        "Mushroom Soup",
        ["mushrooms", "onion", "vegetable broth", "garlic"],
        diets=("vegan", "vegetarian", "halal", "kosher", "nut_free", "dairy_free", "gluten_free"),
        course_list=json.dumps(["soup"]),
        est_cook_time_min=25,
        health_level="healthy",
        healthiness_score=85,
    ),
    make_recipe(
        "Chicken Garlic Soup",
        ["chicken", "garlic", "carrots", "onion", "water"],
        diets=("halal", "nut_free", "dairy_free", "gluten_free"),
        course_list=json.dumps(["soup"]),
        main_ingredient="poultry",
        est_cook_time_min=50,
        difficulty="medium",
    ),
]


def write_csv(path: Path, recipes: list[dict] = RECIPES) -> Path:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(KEPT_COLUMNS))
        writer.writeheader()
        writer.writerows(recipes)
    return path


def titles(result: dict) -> set[str]:
    return {recipe["title"] for recipe in result["recipes"]}
