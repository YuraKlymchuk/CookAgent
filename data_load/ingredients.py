"""Heuristic normalisation of ingredient phrases.

The dataset's ``ingredients_canonical`` column is only partly cleaned: entries
still look like ``"½ salt"``, ``"cloves garlic minced"`` or
``"large eggs"``. To make "recipes containing X" lookups precise, every phrase
is reduced to a short name (``salt``, ``garlic``, ``egg``).

It is a heuristic, not a parser: compound phrases such as
``"salt ground black pepper taste"`` collapse to ``"salt ground black pepper"``.
The FTS index (see ``schema.py``) covers those cases with token matching.
"""

from __future__ import annotations

import re

_TOKEN = re.compile(r"[^\W\d_]+(?:['-][^\W\d_]+)*")

# Measurement and packaging words. Stripped only at the *start* of a phrase, so
# "ground cloves" keeps "cloves" while "cloves garlic" becomes "garlic".
_LEADING_UNITS = frozenset(
    """
    ounce ounces oz pound pounds lb lbs cup cups tablespoon tablespoons tbsp
    teaspoon teaspoons tsp pinch pinches dash dashes can cans package packages
    packet packets bunch bunches head heads clove cloves slice slices sprig
    sprigs stalk stalks fluid quart quarts pint pints gallon gallons liter
    liters milliliter milliliters gram grams kilogram kilograms inch inches
    count container containers jar jars bottle bottles box boxes stick sticks
    handful handfuls drop drops envelope envelopes bag bags carton cartons
    """.split()
)

# Words that never identify an ingredient: sizes, preparation, filler.
_NOISE = frozenset(
    """
    a an the of and or to as at in into for with from plus more such about total
    if desired needed taste optional garnish serving divided softened melted
    chopped minced diced sliced grated shredded peeled pitted seeded cored
    trimmed rinsed drained beaten lightly finely coarsely roughly thinly
    freshly fresh thawed packed cut halved quartered squeezed juiced zested
    patted room temperature cubed crumbled mashed large medium small
    extra-large piece pieces bite-size
    """.split()
)


# Adjectives that do not identify an ingredient by themselves (used for search terms only).
_MODIFIERS = frozenset(
    """
    ground dried whole boneless skinless lean unsalted salted extra virgin plain
    raw all-purpose
    """.split()
)


def _tokens(phrase: str) -> list[str]:
    candidates = _TOKEN.findall(phrase.lower())
    return [t for t in candidates if t.replace("-", "").replace("'", "").isalpha()]


def _strip_leading_units(tokens: list[str]) -> list[str]:
    start = 0
    while start < len(tokens) - 1 and tokens[start] in _LEADING_UNITS:
        start += 1
    return tokens[start:]


def _singularize(word: str) -> str:
    if len(word) <= 3 or word.endswith(("ss", "us", "is", "ses")):
        return word
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith("oes"):
        return word[:-2]
    if word.endswith("s"):
        return word[:-1]
    return word


def normalize_ingredient(phrase: str) -> str | None:
    """Reduce an ingredient phrase to its name, or ``None`` if nothing is left."""
    tokens = [t for t in _tokens(phrase) if t not in _NOISE]
    tokens = _strip_leading_units(tokens)
    if not tokens:
        return None
    return " ".join([*tokens[:-1], _singularize(tokens[-1])])


def normalize_ingredients(phrases: list[str]) -> tuple[str, ...]:
    """Normalise a recipe's ingredient list, dropping blanks and duplicates (order kept)."""
    names = (normalize_ingredient(phrase) for phrase in phrases)
    return tuple(dict.fromkeys(name for name in names if name))


def ingredient_terms(names: tuple[str, ...]) -> tuple[str, ...]:
    """Split normalised names into single singular words ("skinless chicken breast" -> chicken, breast).

    Users ask for "chicken", not "skinless boneless chicken breast", so the
    searchable unit is the word. Modifiers that are meaningless on their own
    ("ground", "dried") are skipped.
    """
    words = (
        _singularize(word)
        for name in names
        for word in name.split()
        if word not in _MODIFIERS
    )
    return tuple(dict.fromkeys(words))
