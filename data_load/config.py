"""Paths, column lists and defaults shared by the ``data_load`` package."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATASET_DIR = PROJECT_ROOT / "dataset"

RAW_DATASET = DATASET_DIR / "recipes_extended.csv"
SLIM_DATASET = DATASET_DIR / "recipes_slim.csv.gz"
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "recipes.db"

# Overrides the database location (useful when the app directory is read-only).
DB_PATH_ENV_VAR = "COOKAGENT_DB_PATH"

BYTES_PER_MB = 1024 * 1024
DEFAULT_MAX_MB = 10.0  # Hugging Face requires LFS/Xet for files above 10 MiB.

# Columns that are useful for the agent. Everything else is dropped:
#   ingredient_text, directions_text, combined_text -> derived text blobs
#   ingredients_raw, directions_raw                 -> identical to ingredients/directions
#   secondary_taste, fast/slow/medium_hits          -> internal scoring artefacts
#   dietary_profile                                 -> duplicates the is_* flags
KEPT_COLUMNS: tuple[str, ...] = (
    "recipe_title",
    "category",
    "subcategory",
    "description",
    "ingredients",
    "directions",
    "num_ingredients",
    "num_steps",
    "ingredients_canonical",
    "cuisine_list",
    "course_list",
    "tastes",
    "primary_taste",
    "cook_speed",
    "est_prep_time_min",
    "est_cook_time_min",
    "difficulty",
    "is_vegan",
    "is_vegetarian",
    "is_halal",
    "is_kosher",
    "is_nut_free",
    "is_dairy_free",
    "is_gluten_free",
    "healthiness_score",
    "health_flags",
    "main_ingredient",
    "health_level",
)

# A recipe without these cannot be shown to the user, so it is discarded.
REQUIRED_COLUMNS: tuple[str, ...] = ("recipe_title", "ingredients", "directions")


@dataclass(frozen=True)
class PrepareConfig:
    """Settings for shrinking the raw dataset into a git-friendly file."""

    source: Path = RAW_DATASET
    destination: Path = SLIM_DATASET
    max_bytes: int = int(DEFAULT_MAX_MB * BYTES_PER_MB)
