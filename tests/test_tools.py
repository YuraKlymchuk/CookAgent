import json

import pytest

from agent.queries import DEFAULT_LIMIT, MAX_LIMIT, clamp_limit

from .recipes_fixture import titles


@pytest.mark.parametrize(
    ("given", "expected"),
    [(None, DEFAULT_LIMIT), (0, 1), (-3, 1), (4, 4), (10_000, MAX_LIMIT)],
)
def test_clamp_limit(given, expected):
    assert clamp_limit(given) == expected


def test_specs_expose_three_tools_with_database_enums(toolbox):
    specs = {spec["function"]["name"]: spec["function"] for spec in toolbox.specs}
    assert set(specs) == {"search_recipes", "match_pantry", "get_recipe"}
    cuisine = specs["search_recipes"]["parameters"]["properties"]["cuisine"]
    assert "asian" in cuisine["enum"]


def test_call_accepts_json_string_arguments(toolbox):
    result = toolbox.call("search_recipes", json.dumps({"diets": ["vegan"]}))
    assert titles(result) == {"Vegan Chickpea Salad", "Mushroom Soup"}


def test_call_ignores_null_arguments(toolbox):
    result = toolbox.call("search_recipes", {"cuisine": None, "diets": None, "limit": 3})
    assert result["returned"] == 3


def test_call_handles_empty_arguments(toolbox):
    assert toolbox.call("search_recipes", "")["total_matches"] == 8


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("search_recipes", "{not json"),
        ("search_recipes", "[1, 2]"),
        ("search_recipes", {"cuisine": "martian"}),
        ("search_recipes", {"diets": ["paleo"]}),
        ("search_recipes", {"max_total_min": "soon"}),
        ("match_pantry", {}),
        ("get_recipe", {"recipe_id": "abc"}),
        ("get_recipe", {"recipe_id": 99999}),
        ("delete_everything", {}),
    ],
)
def test_bad_calls_return_error_instead_of_raising(toolbox, name, arguments):
    assert "error" in toolbox.call(name, arguments)


def test_error_lists_allowed_values_so_the_model_can_retry(toolbox):
    message = toolbox.call("search_recipes", {"cuisine": "martian"})["error"]
    assert "asian" in message
