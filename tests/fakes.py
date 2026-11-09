"""Scripted stand-in for the OpenAI client, so the agent loop is testable without a key."""

from __future__ import annotations

import json
from types import SimpleNamespace


def tool_call(call_id: str, name: str, arguments: dict) -> SimpleNamespace:
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))


def reply(content: str | None = None, tool_calls: list | None = None) -> SimpleNamespace:
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeClient:
    """Returns the scripted replies in order; records every request it received."""

    def __init__(self, script: list[SimpleNamespace] | None = None, *, always_tools: bool = False) -> None:
        self._script = list(script or [])
        self._always_tools = always_tools
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **request):
        self.requests.append(request)
        if self._always_tools:
            if request["tool_choice"] == "none":
                return reply("Final answer without tools.")
            return reply(tool_calls=[tool_call("loop", "search_recipes", {})])
        return self._script.pop(0)
