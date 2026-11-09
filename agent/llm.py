"""Groq (OpenAI-compatible) client factory."""

from __future__ import annotations

import os

from dotenv import load_dotenv
from openai import OpenAI

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
MODEL = "openai/gpt-oss-120b"
REASONING_EFFORT = "low"  # keeps latency and token use down; switch to "medium" for harder tasks
TIMEOUT_SECONDS = 60.0


def server_api_key() -> str:
    """The shared key: ``.env`` locally, a Space Secret on Hugging Face."""
    load_dotenv()
    return os.environ.get("GROQ_API_KEY", "").strip()


def make_client(api_key: str) -> OpenAI:
    # The free tier caps tokens per minute; the SDK waits for the server's retry-after
    # (usually under a second) and retries, so short bursts succeed instead of failing.
    return OpenAI(api_key=api_key, base_url=GROQ_BASE_URL, timeout=TIMEOUT_SECONDS, max_retries=3)
