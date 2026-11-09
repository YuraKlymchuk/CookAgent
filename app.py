"""CookAgent entry point (Hugging Face Space: ``app_file: app.py``)."""

from agent.llm import server_api_key
from agent.rate_limit import RateLimiter
from agent.tools import ToolBox
from agent.ui import build_demo
from data_load import ensure_database, open_readonly

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
