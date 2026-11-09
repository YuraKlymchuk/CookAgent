import json

from agent.loop import compact_history, run_turn

from .fakes import FakeClient, reply, tool_call
from .recipes_fixture import titles


def scripted_conversation():
    return FakeClient(
        [
            reply(tool_calls=[tool_call("c1", "search_recipes", {"diets": ["vegan"]})]),
            reply(tool_calls=[tool_call("c2", "get_recipe", {"recipe_id": 1})]),
            reply("Here is your recipe."),
        ]
    )


def test_agent_chains_tools_and_exposes_structured_results(toolbox):
    client = scripted_conversation()

    result = run_turn(client, toolbox, [], "I want something vegan")

    assert result.answer == "Here is your recipe."
    assert result.recipe["title"] == "Vegan Chickpea Salad"
    assert titles({"recipes": result.candidates}) == {"Vegan Chickpea Salad", "Mushroom Soup"}
    roles = [m["role"] for m in result.messages]
    assert roles == ["user", "assistant", "tool", "assistant", "tool", "assistant"]


def test_requests_carry_system_prompt_and_tools(toolbox):
    client = scripted_conversation()
    run_turn(client, toolbox, [], "hi")

    first = client.requests[0]
    assert first["messages"][0]["role"] == "system"
    assert first["tool_choice"] == "auto"
    assert len(first["tools"]) == 3


def test_failed_tool_call_does_not_set_recipe_but_is_reported_to_the_model(toolbox):
    client = FakeClient(
        [
            reply(tool_calls=[tool_call("c1", "get_recipe", {"recipe_id": 999})]),
            reply("Sorry, that recipe does not exist."),
        ]
    )

    result = run_turn(client, toolbox, [], "show recipe 999")

    assert result.recipe is None
    tool_message = next(m for m in result.messages if m["role"] == "tool")
    assert "error" in json.loads(tool_message["content"])


def test_loop_stops_calling_tools_after_the_iteration_limit(toolbox):
    client = FakeClient(always_tools=True)

    result = run_turn(client, toolbox, [], "loop forever", max_iterations=2)

    assert result.answer == "Final answer without tools."
    assert len(client.requests) == 3
    assert client.requests[-1]["tool_choice"] == "none"


def test_old_search_results_are_compacted_but_the_latest_recipe_is_kept(toolbox):
    first = run_turn(scripted_conversation(), toolbox, [], "something vegan")

    client = FakeClient([reply("Sure.")])
    run_turn(client, toolbox, first.messages, "what can replace lemon juice?")

    sent = client.requests[0]["messages"]
    tool_payloads = [json.loads(m["content"]) for m in sent if m["role"] == "tool"]
    search_payload, recipe_payload = tool_payloads
    assert all(set(item) == {"id", "title"} for item in search_payload)
    assert "directions" in recipe_payload


def test_compact_history_keeps_only_the_newest_full_recipe(toolbox):
    first = run_turn(scripted_conversation(), toolbox, [], "vegan please")
    second = FakeClient(
        [
            reply(tool_calls=[tool_call("c9", "get_recipe", {"recipe_id": 7})]),
            reply("Mushroom soup it is."),
        ]
    )
    result = run_turn(second, toolbox, first.messages, "actually the soup")

    compacted = compact_history(result.messages)
    recipes = [json.loads(m["content"]) for m in compacted
               if m["role"] == "tool" and "title" in json.loads(m["content"]) and "id" in json.loads(m["content"])
               and not isinstance(json.loads(m["content"]), list)]
    full = [r for r in recipes if "directions" in r]
    assert [r["title"] for r in full] == ["Mushroom Soup"]
