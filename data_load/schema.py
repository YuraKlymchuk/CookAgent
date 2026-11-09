"""SQLite schema: tables, FTS index and the secondary indexes the agent's filters use.

Query patterns each structure serves
------------------------------------
* diet / religion / allergy flags   -> partial indexes on ``recipes`` (``is_*`` = 1)
* time, difficulty, health level    -> plain indexes on ``recipes``
* key (protein) ingredient          -> ``recipes.main_ingredient``
* cuisine / course / taste / flags  -> ``recipe_tags`` (one row per label)
* "contains all of these"           -> ``recipe_ingredients`` + GROUP BY ... HAVING
* "best match for my pantry"        -> ``recipe_ingredients`` coverage count
* free text ("spicy chicken soup")  -> ``recipes_fts`` (porter stemming)
"""

from __future__ import annotations

SCHEMA_VERSION = "2"

# kind values stored in recipe_tags
TAG_KINDS = ("cuisine", "course", "taste", "health_flag")

DIET_FLAGS = (
    "is_vegan",
    "is_vegetarian",
    "is_halal",
    "is_kosher",
    "is_nut_free",
    "is_dairy_free",
    "is_gluten_free",
)

RECIPE_COLUMNS = (
    "id",
    "title",
    "category",
    "subcategory",
    "description",
    "ingredients_json",
    "directions_json",
    "num_ingredients",
    "num_steps",
    "prep_min",
    "cook_min",
    "total_min",
    "cook_speed",
    "difficulty",
    "healthiness_score",
    "health_level",
    "main_ingredient",
    "primary_taste",
    *DIET_FLAGS,
)

CREATE_TABLES = """
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE recipes (
    id                INTEGER PRIMARY KEY,
    title             TEXT    NOT NULL,
    category          TEXT    NOT NULL,
    subcategory       TEXT    NOT NULL,
    description       TEXT,
    ingredients_json  TEXT    NOT NULL,   -- JSON list, original text with quantities
    directions_json   TEXT    NOT NULL,   -- JSON list of steps
    num_ingredients   INTEGER NOT NULL,
    num_steps         INTEGER NOT NULL,
    prep_min          INTEGER NOT NULL,
    cook_min          INTEGER NOT NULL,
    total_min         INTEGER NOT NULL,   -- prep_min + cook_min
    cook_speed        TEXT    NOT NULL,   -- fast | medium | slow
    difficulty        TEXT    NOT NULL,   -- easy | medium | hard
    healthiness_score INTEGER NOT NULL,   -- 0..100
    health_level      TEXT    NOT NULL,   -- healthy | moderate | unhealthy
    main_ingredient   TEXT    NOT NULL,   -- poultry | red_meat | seafood | egg_dairy | plant | unknown
    primary_taste     TEXT    NOT NULL,
    is_vegan          INTEGER NOT NULL,
    is_vegetarian     INTEGER NOT NULL,
    is_halal          INTEGER NOT NULL,
    is_kosher         INTEGER NOT NULL,
    is_nut_free       INTEGER NOT NULL,
    is_dairy_free     INTEGER NOT NULL,
    is_gluten_free    INTEGER NOT NULL
);

-- Multi-valued labels: cuisine, course, taste, health_flag.
CREATE TABLE recipe_tags (
    kind      TEXT    NOT NULL,
    value     TEXT    NOT NULL,
    recipe_id INTEGER NOT NULL REFERENCES recipes(id),
    PRIMARY KEY (kind, value, recipe_id)
) WITHOUT ROWID;

-- Vocabulary of single, singular ingredient words ("chicken", "garlic", "flour").
CREATE TABLE ingredients (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE recipe_ingredients (
    ingredient_id INTEGER NOT NULL REFERENCES ingredients(id),
    recipe_id     INTEGER NOT NULL REFERENCES recipes(id),
    PRIMARY KEY (ingredient_id, recipe_id)
) WITHOUT ROWID;

-- rowid of this table equals recipes.id
CREATE VIRTUAL TABLE recipes_fts USING fts5(
    title,
    category,
    ingredients,
    tokenize = 'porter unicode61 remove_diacritics 2'
);
"""

# Created after the bulk load: building an index once is much faster than
# maintaining it row by row.
CREATE_INDEXES = (
    "CREATE INDEX idx_recipe_tags_recipe ON recipe_tags(recipe_id)",
    "CREATE INDEX idx_recipe_ingredients_recipe ON recipe_ingredients(recipe_id)",
    "CREATE INDEX idx_recipes_difficulty ON recipes(difficulty, total_min)",
    "CREATE INDEX idx_recipes_total_min ON recipes(total_min)",
    "CREATE INDEX idx_recipes_cook_speed ON recipes(cook_speed)",
    "CREATE INDEX idx_recipes_health ON recipes(health_level, healthiness_score)",
    "CREATE INDEX idx_recipes_main_ingredient ON recipes(main_ingredient, total_min)",
    "CREATE INDEX idx_recipes_taste ON recipes(primary_taste)",
    "CREATE INDEX idx_recipes_category ON recipes(category)",
    # Partial indexes stay tiny and serve "diet flag = 1, sorted/limited by time".
    *(
        f"CREATE INDEX idx_recipes_{flag} ON recipes(total_min) WHERE {flag} = 1"
        for flag in DIET_FLAGS
    ),
)
