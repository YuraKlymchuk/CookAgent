"""CookAgent entry point (Hugging Face Space: ``app_file: app.py``)."""

import logging
import os

from agent.llm import server_api_key
from agent.rate_limit import RateLimiter
from agent.tools import ToolBox
from agent.ui import build_demo
from data_load import ensure_database, open_readonly

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
# Tool calls are logged by the "cookagent.*" loggers; keep library chatter out of the way.
for noisy in ("httpx", "httpcore", "openai"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

try:
    import spaces  # provided on Hugging Face; absent (and unnecessary) on a normal machine
except ImportError:
    spaces = None

if spaces is not None:

    @spaces.GPU
    def _zerogpu_placeholder() -> None:
        """Never called. A ZeroGPU Space refuses to start unless some function carries
        ``@spaces.GPU``; this app needs no GPU (the model runs on Groq), and a GPU is
        only allocated when such a function is actually called."""


# Runs on every start: builds data/recipes.db if it is missing or stale
# (always the case on a freshly started Space, whose disk is not persistent).
DB_PATH = ensure_database()

demo = build_demo(
    toolbox=ToolBox(open_readonly(DB_PATH)),
    limiter=RateLimiter.from_env(),
    server_key=server_api_key(),
)

if __name__ == "__main__":
    demo.launch()
