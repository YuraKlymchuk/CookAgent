from types import SimpleNamespace

from agent import queries
from agent.ui import EMPTY_RECIPE, candidate_rows, client_id, limit_message, render_recipe


def test_render_recipe_has_title_ingredients_and_numbered_steps(connection):
    markdown = render_recipe(queries.get_recipe(connection, 1))

    assert markdown.startswith("## Vegan Chickpea Salad")
    assert "- 1 cup chickpeas" in markdown
    assert "1. Mix everything.\n2. Cook until done." in markdown


def test_render_recipe_placeholder_when_nothing_selected():
    assert render_recipe(None) == EMPTY_RECIPE


def test_candidate_rows_match_table_headers(connection):
    cards = queries.search_recipes(connection, limit=2)["recipes"]
    rows = candidate_rows(cards)
    assert len(rows) == 2 and len(rows[0]) == 5


def test_client_id_prefers_forwarded_ip():
    request = SimpleNamespace(headers={"x-forwarded-for": "1.2.3.4, 10.0.0.1"}, client=None, session_hash="s")
    assert client_id(request) == "1.2.3.4"


def test_client_id_falls_back_to_socket_then_session():
    socket = SimpleNamespace(headers={}, client=SimpleNamespace(host="9.9.9.9"), session_hash="s")
    session = SimpleNamespace(headers={}, client=None, session_hash="abc")
    assert client_id(socket) == "9.9.9.9"
    assert client_id(session) == "abc"
    assert client_id(None) == "anonymous"


def test_limit_message_mentions_own_key_option():
    assert "власний ключ" in limit_message(600, "client")
