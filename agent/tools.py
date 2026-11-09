"""Tool definitions the model can call: JSON schemas, validation and dispatch.

``ToolBox.call`` never raises: bad input becomes ``{"error": ...}`` so the
model can read the message and correct its next call.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from . import queries
from .queries import DIETS, DIFFICULTIES, HEALTH_LEVELS, MAX_LIMIT, Options

log = logging.getLogger(__name__)


class SearchArgs(BaseModel):
    diets: list[str] | None = None
    main_ingredient: str | None = None
    include_ingredients: list[str] | None = None
    exclude_ingredients: list[str] | None = None
    cuisine: str | None = None
    course: str | None = None
    tastes: list[str] | None = None
    max_total_min: int | None = None
    difficulty: str | None = None
    health_level: str | None = None
    text: str | None = None
    limit: int | None = None


class PantryArgs(BaseModel):
    ingredients: list[str]
    diets: list[str] | None = None
    limit: int | None = None


class RecipeArgs(BaseModel):
    recipe_id: int


def _function(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required or []},
        },
    }


def _enum(values: tuple[str, ...], description: str) -> dict:
    return {"type": "string", "enum": list(values), "description": description}


def build_tool_specs(options: Options) -> list[dict]:
    words = {"type": "array", "items": {"type": "string"}}
    limit = {
        "type": "integer",
        "minimum": 1,
        "maximum": MAX_LIMIT,
        "description": f"How many recipes to return (default {queries.DEFAULT_LIMIT}).",
    }
    diets = {
        "type": "array",
        "items": {"type": "string", "enum": list(DIETS)},
        "description": "Dietary, religious or allergy constraints; every one must hold.",
    }
    return [
        _function(
            "search_recipes",
            "Find recipes matching filters. Returns a random sample of matches (not all of them) "
            "plus total_matches; narrow the filters if total_matches is large.",
            {
                "diets": diets,
                "main_ingredient": _enum(options.main_ingredients, "Main ingredient group."),
                "include_ingredients": {**words, "description": "English ingredient words that must be present, e.g. chicken, rice."},
                "exclude_ingredients": {**words, "description": "English words to avoid (allergens), specific ones: peanut, almond, shrimp."},
                "cuisine": _enum(options.cuisines, "Cuisine."),
                "course": _enum(options.courses, "Course / dish type."),
                "tastes": {"type": "array", "items": {"type": "string", "enum": list(options.tastes)}, "description": "Taste profile; all must match."},
                "max_total_min": {"type": "integer", "description": "Maximum prep + cook time in minutes."},
                "difficulty": _enum(DIFFICULTIES, "Difficulty."),
                "health_level": _enum(HEALTH_LEVELS, "How healthy the dish is."),
                "text": {"type": "string", "description": "Words from the dish name, e.g. 'soup'."},
                "limit": limit,
            },
        ),
        _function(
            "match_pantry",
            "Find recipes that use as many of the user's available ingredients as possible. "
            "Each result reports matched_ingredients and extra_ingredients (what else is needed).",
            {
                "ingredients": {**words, "description": "Ingredients the user has, in English."},
                "diets": diets,
                "limit": limit,
            },
            required=["ingredients"],
        ),
        _function(
            "get_recipe",
            "Get the full recipe (ingredients with amounts and cooking steps) by id. "
            "Call it once the user picks a dish, using an id from earlier results.",
            {"recipe_id": {"type": "integer", "description": "Recipe id from search results."}},
            required=["recipe_id"],
        ),
    ]


class ToolBox:
    """Binds the tools to one read-only connection."""

    def __init__(self, connection: sqlite3.Connection, options: Options | None = None) -> None:
        self._connection = connection
        self.options = options or queries.load_options(connection)
        self.specs = build_tool_specs(self.options)
        self._handlers: dict[str, tuple[type[BaseModel], Callable[..., dict]]] = {
            "search_recipes": (SearchArgs, queries.search_recipes),
            "match_pantry": (PantryArgs, queries.match_pantry),
            "get_recipe": (RecipeArgs, queries.get_recipe),
        }

    def _check_choices(self, args: dict[str, Any]) -> None:
        checks = {
            "cuisine": ("cuisine", self.options.cuisines),
            "course": ("course", self.options.courses),
            "main_ingredient": ("main_ingredient", self.options.main_ingredients),
        }
        for field, (label, allowed) in checks.items():
            if args.get(field) and args[field] not in allowed:
                raise ValueError(f"Unknown {label} {args[field]!r}. Allowed: {', '.join(allowed)}")
        for taste in args.get("tastes") or []:
            if taste not in self.options.tastes:
                raise ValueError(f"Unknown taste {taste!r}. Allowed: {', '.join(self.options.tastes)}")

    def call(self, name: str, arguments: str | dict | None) -> dict:
        if name not in self._handlers:
            return {"error": f"Unknown tool {name!r}. Available: {', '.join(self._handlers)}"}
        try:
            raw = json.loads(arguments) if isinstance(arguments, str) and arguments.strip() else (arguments or {})
            model, handler = self._handlers[name]
            args = model.model_validate(raw).model_dump(exclude_none=True)
            self._check_choices(args)
            return handler(self._connection, **args)
        except json.JSONDecodeError:
            return {"error": "Arguments must be a valid JSON object"}
        except ValidationError as exc:
            details = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]
            return {"error": "Invalid arguments", "details": details}
        except ValueError as exc:
            return {"error": str(exc)}
        except Exception:  # noqa: BLE001 - a tool failure must not crash the conversation
            log.exception("Tool %s failed", name)
            return {"error": "Internal error while running the tool"}
