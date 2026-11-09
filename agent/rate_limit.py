"""In-memory sliding-window rate limiter for the shared (server) API key.

Requests that bring their own key never go through it.
"""

from __future__ import annotations

import math
import os
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Callable

HOUR = 3600
DAY = 86400


@dataclass(frozen=True)
class Decision:
    allowed: bool
    retry_after_s: int = 0
    scope: str = ""  # "client" or "global" when denied


class RateLimiter:
    def __init__(
        self,
        per_client: int,
        per_client_window_s: int = HOUR,
        global_limit: int = 300,
        global_window_s: int = DAY,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._per_client = per_client
        self._client_window = per_client_window_s
        self._global_limit = global_limit
        self._global_window = global_window_s
        self._clock = clock
        self._lock = threading.Lock()
        self._clients: dict[str, deque[float]] = defaultdict(deque)
        self._global: deque[float] = deque()

    @classmethod
    def from_env(cls) -> "RateLimiter":
        return cls(
            per_client=int(os.environ.get("FREE_TURNS_PER_HOUR", "15")),
            global_limit=int(os.environ.get("FREE_TURNS_PER_DAY", "300")),
        )

    @staticmethod
    def _purge(stamps: deque[float], now: float, window: int) -> None:
        while stamps and now - stamps[0] >= window:
            stamps.popleft()

    def acquire(self, client_id: str) -> Decision:
        """Count one conversation turn for ``client_id`` if the limits allow it."""
        with self._lock:
            now = self._clock()
            mine = self._clients[client_id]
            self._purge(mine, now, self._client_window)
            self._purge(self._global, now, self._global_window)

            if len(mine) >= self._per_client:
                return Decision(False, math.ceil(self._client_window - (now - mine[0])), "client")
            if len(self._global) >= self._global_limit:
                return Decision(False, math.ceil(self._global_window - (now - self._global[0])), "global")

            mine.append(now)
            self._global.append(now)
            if len(self._clients) > 10_000:  # bound memory: forget clients with no recent turns
                for key in [k for k, v in self._clients.items() if not v]:
                    del self._clients[key]
            return Decision(True)
