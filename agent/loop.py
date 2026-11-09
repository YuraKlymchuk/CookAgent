"""The agent loop: call the model, run the tools it asks for, repeat until it answers."""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from openai import BadRequestError

from .llm import MODEL, REASONING_EFFORT
from .prompts import SYSTEM_PROMPT
from .tools import ToolBox

log = logging.getLogger("cookagent.agent")

MAX_TOOL_ITERATIONS = 6
SEARCH_TOOLS = ("search_recipes", "match_pantry")

# The model ends a message that presents options with e.g. "[[options: 12, 45, 77]]".
_OPTIONS_MARKER = re.compile(r"\[\[\s*options\s*:\s*([\d,\s]*?)\s*\]\]", re.IGNORECASE)


@dataclass(frozen=True)
class TurnResult:
    """Outcome of one user message.

    ``recipe`` and ``candidates`` come from tool results and the model's
    options marker (not from parsing its prose), so the UI can render them as
    separate elements. ``candidates`` are the options the agent presented, or
    all results of its last search when it did not name any.
    """

    answer: str
    messages: list[dict]  # conversation without the system prompt; pass back next turn
    recipe: dict | None = None
    candidates: list[dict] = field(default_factory=list)


def extract_option_ids(text: str) -> list[int]:
    """Recipe ids named in the options marker (empty if the model added none)."""
    ids: list[int] = []
    for match in _OPTIONS_MARKER.finditer(text):
        ids += [int(part) for part in re.findall(r"\d+", match.group(1))]
    return list(dict.fromkeys(ids))


def strip_options_marker(text: str) -> str:
    return _OPTIONS_MARKER.sub("", text).rstrip()


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
        log.warning("model produced a malformed tool call, retrying once")
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


def _result_summary(result: dict) -> str:
    if "error" in result:
        return f"error: {result['error']}"
    if "recipes" in result:
        titles = ", ".join(recipe["title"] for recipe in result["recipes"])
        return f"returned={result['returned']} total_matches={result['total_matches']} [{titles}]"
    if "title" in result:
        return f"recipe #{result['id']} {result['title']!r}"
    return "ok"


def _used_tokens(response: Any) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    return (getattr(usage, "prompt_tokens", 0) or 0, getattr(usage, "completion_tokens", 0) or 0)


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
    last_search: list[dict] = []
    tools_used: list[str] = []
    prompt_tokens = completion_tokens = 0

    log.info("turn start: user=%r", user_text[:200])

    for iteration in range(max_iterations + 1):
        # On the last pass tools are switched off so the model has to answer.
        forced_answer = iteration == max_iterations
        response = _complete(client, messages, toolbox.specs, "none" if forced_answer else "auto")
        used = _used_tokens(response)
        prompt_tokens, completion_tokens = prompt_tokens + used[0], completion_tokens + used[1]
        message = response.choices[0].message
        messages.append(_assistant_message(message))

        if not message.tool_calls or forced_answer:
            raw_answer = message.content or ""
            option_ids = extract_option_ids(raw_answer)
            presented = toolbox.cards(option_ids) if option_ids else []
            candidates = presented or last_search
            log.info(
                "turn end: llm_requests=%d tokens=%d+%d tools=%s options=%s",
                iteration + 1, prompt_tokens, completion_tokens, tools_used or "none",
                [card["id"] for card in presented] or "n/a",
            )
            return TurnResult(strip_options_marker(raw_answer), messages, recipe, candidates)

        for call in message.tool_calls:
            started = time.perf_counter()
            result = toolbox.call(call.function.name, call.function.arguments)
            log.info(
                "tool_call %s args=%s -> %s (%.0f ms)",
                call.function.name,
                call.function.arguments,
                _result_summary(result),
                (time.perf_counter() - started) * 1000,
            )
            tools_used.append(call.function.name)
            if "error" not in result:
                if call.function.name == "get_recipe":
                    recipe = result
                elif call.function.name in SEARCH_TOOLS:
                    last_search = result["recipes"]
            messages.append(
                {"role": "tool", "tool_call_id": call.id, "content": json.dumps(result, ensure_ascii=False)}
            )

    raise AssertionError("unreachable")  # the loop always returns on its last iteration
