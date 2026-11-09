import os

import pytest

from data_load import ensure_database, open_readonly
from data_load.ingredients import ingredient_terms, normalize_ingredient

from .recipes_fixture import RECIPES, make_recipe, write_csv


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("cloves garlic minced", "garlic"),
        ("large eggs", "egg"),
        ("½ salt", "salt"),
        ("ground cloves", "ground clove"),
        ("fresh tomatoes diced", "tomato"),
        ("ounce -count package pie crusts thawed", "pie crust"),
    ],
)
def test_normalize_ingredient(phrase, expected):
    assert normalize_ingredient(phrase) == expected


def test_normalize_ingredient_can_be_empty():
    assert normalize_ingredient("½ ¼") is None


def test_ingredient_terms_split_names_and_skip_modifiers():
    assert ingredient_terms(("skinless boneless chicken breast",)) == ("chicken", "breast")


def test_ensure_database_is_idempotent_and_detects_changed_source(tmp_path):
    source = write_csv(tmp_path / "recipes.csv")
    target = tmp_path / "recipes.db"

    ensure_database(source, target)
    built_at = os.stat(target).st_mtime_ns
    ensure_database(source, target)
    assert os.stat(target).st_mtime_ns == built_at  # up to date: not rebuilt

    write_csv(source, [*RECIPES, make_recipe("Extra Dish", ["salt"])])
    ensure_database(source, target)
    count = open_readonly(target).execute("SELECT COUNT(*) FROM recipes").fetchone()[0]
    assert count == len(RECIPES) + 1


def test_ensure_database_explains_missing_dataset(tmp_path):
    with pytest.raises(FileNotFoundError, match="prepare"):
        ensure_database(tmp_path / "missing.csv.gz", tmp_path / "x.db")
