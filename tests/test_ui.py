from types import SimpleNamespace

from agent import queries
from agent.ui import (
    CANDIDATE_HEADERS,
    EMPTY_RECIPE,
    candidate_rows,
    client_id,
    find_card,
    limit_message,
    load_session,
    new_session,
    render_recipe,
    row_prompt,
)


def test_render_recipe_has_title_ingredients_and_numbered_steps(connection):
    markdown = render_recipe(queries.get_recipe(connection, 1))

    assert markdown.startswith("## Vegan Chickpea Salad")
    assert "- 1 cup chickpeas" in markdown
    assert "1. Mix everything.\n2. Cook until done." in markdown


def test_render_recipe_translates_difficulty_health_and_diets(connection):
    markdown = render_recipe(queries.get_recipe(connection, 1))

    assert "складність: легка" in markdown
    assert "корисність: корисна" in markdown
    assert "веганська" in markdown and "vegan" not in markdown


def test_render_recipe_placeholder_when_nothing_selected():
    assert render_recipe(None) == EMPTY_RECIPE


def test_candidate_rows_have_no_id_and_translated_difficulty(connection):
    cards = queries.search_recipes(connection, difficulty="hard")["recipes"]
    rows = candidate_rows(cards)

    assert len(CANDIDATE_HEADERS) == len(rows[0]) == 4
    assert rows[0][0] == "Pork Ribs"
    assert rows[0][2] == "складна"
    assert cards[0]["id"] not in rows[0][:1]


def test_clicked_row_is_matched_by_title_even_if_table_was_sorted(connection):
    shown = queries.search_recipes(connection, limit=5, seed=3)["recipes"]
    target = shown[-1]

    found = find_card(shown, [target["title"], 1, "x", 2], index=0)  # index points at the wrong row

    assert found == target


def test_clicked_row_falls_back_to_index_and_handles_nothing():
    shown = [{"id": 5, "title": "A"}, {"id": 6, "title": "B"}]
    assert find_card(shown, None, 1)["id"] == 6
    assert find_card(shown, None, 9) is None
    assert find_card([], ["A"], None) is None


def test_row_prompt_names_the_recipe_and_its_id():
    prompt = row_prompt({"id": 42, "title": "Mushroom Soup"})
    assert "«Mushroom Soup»" in prompt and "id 42" in prompt


def test_load_session_survives_empty_or_damaged_browser_state():
    assert load_session({}) == new_session()
    assert load_session(None) == new_session()
    assert load_session({"chat": "oops", "recipe": 7, "shown": None}) == new_session()


def test_load_session_restores_a_saved_conversation():
    saved = {
        "chat": [{"role": "user", "content": "hi"}],
        "history": [{"role": "user", "content": "hi"}],
        "recipe": {"title": "X"},
        "shown": [{"id": 1, "title": "X"}],
    }
    assert load_session(saved) == saved


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
