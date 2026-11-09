"""Integration tests: real SQL against a database built from the fixture recipes."""

import sqlite3

import pytest

from agent import queries

from .recipes_fixture import titles


def search(connection, **filters):
    return queries.search_recipes(connection, **filters)


def test_vegan_filter(connection):
    result = search(connection, diets=["vegan"])
    assert titles(result) == {"Vegan Chickpea Salad", "Mushroom Soup"}
    assert result["total_matches"] == 2


def test_religious_allergy_and_time_filters_combine(connection):
    result = search(connection, diets=["halal", "nut_free"], max_total_min=25)
    assert titles(result) == {"Vegan Chickpea Salad"}


def test_include_ingredients_requires_all_terms(connection):
    result = search(connection, include_ingredients=["chicken", "garlic"])
    assert titles(result) == {"Chicken Rice Bowl", "Chicken Garlic Soup"}


def test_exclude_ingredients_removes_allergens_even_when_plural(connection):
    result = search(connection, exclude_ingredients=["peanuts"])
    assert "Peanut Noodles" not in titles(result)
    assert result["total_matches"] == 7


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ({"main_ingredient": "seafood"}, {"Shrimp Garlic Pasta"}),
        ({"cuisine": "asian"}, {"Chicken Rice Bowl", "Peanut Noodles"}),
        ({"tastes": ["sweet"]}, {"Fluffy Pancakes"}),
        ({"tastes": ["savory", "spicy"]}, {"Peanut Noodles"}),
        ({"difficulty": "hard"}, {"Pork Ribs"}),
        ({"health_level": "healthy"}, {"Vegan Chickpea Salad", "Mushroom Soup"}),
        ({"course": "soup"}, {"Mushroom Soup", "Chicken Garlic Soup"}),
        ({"text": "soup"}, {"Mushroom Soup", "Chicken Garlic Soup"}),
    ],
)
def test_single_filters(connection, filters, expected):
    assert titles(search(connection, **filters)) == expected


def test_limit_caps_results_but_reports_all_matches(connection):
    result = search(connection, limit=2)
    assert result["returned"] == len(result["recipes"]) == 2
    assert result["total_matches"] == 8


def test_cards_are_compact(connection):
    card = search(connection, limit=1)["recipes"][0]
    assert set(card) == {"id", "title", "total_min", "difficulty", "healthiness_score", "description"}


def test_sampling_is_random_but_reproducible_with_seed(connection):
    first = [search(connection, seed=1, limit=3)["recipes"][i]["id"] for i in range(3)]
    again = [search(connection, seed=1, limit=3)["recipes"][i]["id"] for i in range(3)]
    assert first == again

    samples = {tuple(r["id"] for r in search(connection, seed=seed, limit=3)["recipes"]) for seed in range(20)}
    assert len(samples) > 1


def test_empty_result_carries_a_hint_that_protects_safety_filters(connection):
    result = search(connection, diets=["vegan"], include_ingredients=["chicken"])
    assert result["total_matches"] == 0 and result["recipes"] == []
    assert "never drop diet/allergy" in result["hint"]


def test_non_empty_result_has_no_hint(connection):
    assert "hint" not in search(connection, diets=["vegan"])


def test_unknown_diet_is_rejected(connection):
    with pytest.raises(ValueError, match="Unknown diet"):
        search(connection, diets=["paleo"])


def test_match_pantry_ranks_by_coverage(connection):
    result = queries.match_pantry(connection, ingredients=["eggs", "flour", "milk"])
    top = result["recipes"][0]
    assert top["title"] == "Fluffy Pancakes"
    assert top["matched_ingredients"] == 3


def test_match_pantry_prefers_fewer_extra_ingredients_on_ties(connection):
    result = queries.match_pantry(connection, ingredients=["garlic", "chicken"])
    assert result["recipes"][0]["title"] == "Chicken Garlic Soup"


def test_match_pantry_respects_diets(connection):
    result = queries.match_pantry(connection, ingredients=["garlic"], diets=["vegan"])
    assert titles(result) == {"Mushroom Soup"}


def test_match_pantry_requires_ingredients(connection):
    with pytest.raises(ValueError):
        queries.match_pantry(connection, ingredients=[])


def test_get_recipe_returns_full_details(connection):
    recipe = queries.get_recipe(connection, 1)
    assert recipe["title"] == "Vegan Chickpea Salad"
    assert "vegan" in recipe["diets"]
    assert recipe["directions"] == ["Mix everything.", "Cook until done."]
    assert len(recipe["ingredients"]) == 4


def test_get_recipe_unknown_id(connection):
    assert "error" in queries.get_recipe(connection, 9999)


def test_database_is_read_only(connection):
    with pytest.raises(sqlite3.OperationalError):
        connection.execute("INSERT INTO meta (key, value) VALUES ('x', 'y')")


def test_cards_by_ids_keeps_requested_order_and_drops_unknown_ids(connection):
    cards = queries.cards_by_ids(connection, [3, 1, 9999, 3])
    assert [c["id"] for c in cards] == [3, 1]
    assert queries.cards_by_ids(connection, []) == []


# ---- relaxation by priority ---------------------------------------------------------

def test_ingredients_that_never_occur_together_fall_back_to_best_partial_match(connection):
    # No recipe has chicken, soy sauce and noodles together; keep the taste filter.
    result = search(connection, include_ingredients=["chicken", "soy sauce", "noodles"], tastes=["spicy"])

    assert result["relaxed"] == ["include_ingredients"]
    assert "No recipe contains all of: chicken, soy sauce, noodle" in result["note"]
    assert titles(result) == {"Peanut Noodles"}  # the only spicy recipe; has soy sauce + noodles
    assert result["recipes"][0]["matched_ingredients"] == 2  # "soy sauce" counts once, not twice


def test_partial_matches_are_ordered_by_ingredient_coverage(connection):
    result = search(connection, include_ingredients=["chicken", "garlic", "rice", "shrimp"], limit=10)

    matched = [r["matched_ingredients"] for r in result["recipes"]]
    assert matched == sorted(matched, reverse=True)
    assert result["recipes"][0]["title"] == "Chicken Rice Bowl"  # chicken + garlic + rice = 3


def test_soft_criteria_are_dropped_in_priority_order_after_ingredients(connection):
    # nothing cooks in 10 minutes, so ingredients go partial first and then time is dropped
    result = search(connection, include_ingredients=["chicken", "garlic"], max_total_min=10, difficulty="hard")

    assert result["relaxed"] == ["include_ingredients", "difficulty", "max_total_min"]
    assert result["recipes"][0]["matched_ingredients"] == 2


def test_taste_is_relaxed_before_time_and_time_is_kept_when_that_is_enough(connection):
    result = search(connection, tastes=["bitter"], max_total_min=20)

    assert result["relaxed"] == ["tastes"]
    assert result["note"] == "Ignored because nothing matched them: tastes."
    assert all(r["total_min"] <= 20 for r in result["recipes"])


def test_nothing_is_reported_as_relaxed_when_the_strict_search_works(connection):
    strict = search(connection, include_ingredients=["chicken", "garlic"])
    assert "relaxed" not in strict and "note" not in strict


def test_diets_are_never_relaxed(connection):
    assert search(connection, diets=["vegan"], include_ingredients=["chicken"])["total_matches"] == 0

    result = search(connection, diets=["vegan"], include_ingredients=["chicken", "garlic"])
    assert result["relaxed"] == ["include_ingredients"]
    assert titles(result) == {"Mushroom Soup"}  # garlic only, but still vegan


def test_excluded_ingredients_are_never_relaxed(connection):
    assert search(connection, include_ingredients=["peanut"], exclude_ingredients=["peanut"])["total_matches"] == 0

    result = search(connection, include_ingredients=["peanut", "garlic"], exclude_ingredients=["peanut"])
    assert "Peanut Noodles" not in titles(result)
    assert result["recipes"]  # still finds garlic recipes, just never one with peanut
