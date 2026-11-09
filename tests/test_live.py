"""Real Groq call. Run with ``pytest -m live`` after putting GROQ_API_KEY in .env."""

import pytest

from agent.llm import make_client, server_api_key
from agent.loop import run_turn

pytestmark = pytest.mark.live


def test_model_uses_tools_for_a_dietary_request(toolbox):
    key = server_api_key()
    if not key:
        pytest.skip("GROQ_API_KEY is not set")

    result = run_turn(make_client(key), toolbox, [], "Suggest a vegan dinner that takes under 30 minutes.")

    assert result.answer.strip()
    assert result.candidates, "the model should have searched the database"
