"""The agent loop: call the model, run the tools it asks for, repeat until it answers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from openai import BadRequestError

from .llm import MODEL, REASONING_EFFORT
from .prompts import SYSTEM_PROMPT
from .tools import ToolBox

MAX_TOOL_ITERATIONS = 6
SEARCH_TOOLS = ("search_recipes", "match_pantry")


@dataclass(frozen=True)
class TurnResult:
    """Outcome of one user message.

    ``recipe`` and ``candidates`` are taken from tool results (not parsed out
    of the model's text), so the UI can render them as separate elements.
    """

    answer: str
    messages: list[dict]  # conversation without the system prompt; pass back next turn
    recipe: dict | None = None
    candidates: list[dict] = field(default_factory=list)


def _complete(client: Any, messages: list[dict], tools: list[dict], tool_choice: str) -> Any:
    request = dict(
        model=MODEL,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, *messages],
        tools=tools,
        tool_choice=tool_choice,
        reasoning_effort=REASONING_EFFORT,
    )
    try:
        return client.chat.completions.create(**request)
    except BadRequestError as exc:
        # gpt-oss occasionally emits a malformed tool call; one retry usually fixes it.
        if "tool_use_failed" not in str(exc):
            raise
        return client.chat.completions.create(**request)


def _assistant_message(message: Any) -> dict:
    entry: dict = {"role": "assistant", "content": message.content or ""}
    if message.tool_calls:
        entry["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.function.name, "arguments": call.function.arguments or "{}"},
            }
            for call in message.tool_calls
        ]
    return entry


def compact_history(messages: list[dict]) -> list[dict]:
    """Shrink old tool results so the context does not grow every turn.

    Search results before the latest user message keep only ``id`` and
    ``title``; a recipe is kept in full only while it is the most recent one.
    """
    last_user = max((i for i, m in enumerate(messages) if m["role"] == "user"), default=-1)
    names = {
        call["id"]: call["function"]["name"]
        for m in messages
        for call in m.get("tool_calls", [])
    }
    latest_recipe = max(
        (i for i, m in enumerate(messages) if m["role"] == "tool" and names.get(m["tool_call_id"]) == "get_recipe"),
        default=-1,
    )

    compacted = []
    for index, message in enumerate(messages):
        if message["role"] != "tool":
            compacted.append(message)
            continue
        name = names.get(message["tool_call_id"])
        try:
            data = json.loads(message["content"])
        except ValueError:
            compacted.append(message)
            continue
        if name in SEARCH_TOOLS and index < last_user and "recipes" in data:
            short = [{"id": r["id"], "title": r["title"]} for r in data["recipes"]]
            compacted.append({**message, "content": json.dumps(short, ensure_ascii=False)})
        elif name == "get_recipe" and index != latest_recipe and "title" in data:
            short = {"id": data["id"], "title": data["title"]}
            compacted.append({**message, "content": json.dumps(short, ensure_ascii=False)})
        else:
            compacted.append(message)
    return compacted


def run_turn(
    client: Any,
    toolbox: ToolBox,
    history: list[dict],
    user_text: str,
    *,
    max_iterations: int = MAX_TOOL_ITERATIONS,
) -> TurnResult:
    # Compact *with* the new message appended, so everything before it counts as "old".
    messages = compact_history([*history, {"role": "user", "content": user_text}])
    recipe: dict | None = None
    candidates: list[dict] = []

    for iteration in range(max_iterations + 1):
        # On the last pass tools are switched off so the model has to answer.
        forced_answer = iteration == max_iterations
        response = _complete(client, messages, toolbox.specs, "none" if forced_answer else "auto")
        message = response.choices[0].message
        messages.append(_assistant_message(message))

        if not message.tool_calls or forced_answer:
            return TurnResult(message.content or "", messages, recipe, candidates)

        for call in message.tool_calls:
            result = toolbox.call(call.function.name, call.function.arguments)
            if "error" not in result:
                if call.function.name == "get_recipe":
                    recipe = result
                elif call.function.name in SEARCH_TOOLS:
                    candidates = result["recipes"]
            messages.append(
                {"role": "tool", "tool_call_id": call.id, "content": json.dumps(result, ensure_ascii=False)}
            )

    raise AssertionError("unreachable")  # the loop always returns on its last iteration
