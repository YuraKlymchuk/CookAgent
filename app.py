"""CookAgent entry point (Hugging Face Space: ``app_file: app.py``)."""

from data_load import ensure_database

# Runs on every start: builds data/recipes.db if it is missing or stale
# (always the case on a freshly started Space, whose disk is not persistent).
DB_PATH = ensure_database()


def main() -> None:
    print(f"Recipe database ready: {DB_PATH}")
    # TODO: build the agent and the Gradio UI here.


if __name__ == "__main__":
    main()
