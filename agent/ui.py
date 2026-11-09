"""Gradio interface: chat on the left, recipe card and options table on the right.

The whole conversation lives in a ``gr.BrowserState`` (the visitor's own
localStorage), so switching Gradio's theme or language, which reloads the page,
does not lose it.
"""

from __future__ import annotations

import gradio as gr
from openai import AuthenticationError, OpenAIError, RateLimitError

from .llm import make_client
from .loop import run_turn
from .rate_limit import RateLimiter
from .tools import ToolBox

TITLE = "🍳 CookAgent — порадник з приготування їжі"
DESCRIPTION = """\
Розкажіть, що хочете приготувати: дієта чи релігійні обмеження (веган, халяль, кошер), алергії, \
продукти, які є вдома, основний інгредієнт, смак, час. Агент сам шукає в базі з ~25 тис. рецептів \
і покаже рецепт та кроки приготування праворуч. Потім можна питати поради: чим замінити інгредієнт, \
як змінити порції тощо. Клікніть рядок у таблиці варіантів, щоб обрати рецепт.

*Прапорці дієт у базі визначені автоматично й можуть містити помилки. Перевіряйте склад, якщо маєте алергію.*"""

EMPTY_RECIPE = "_Тут з'явиться вибраний рецепт._"
CANDIDATE_HEADERS = ["Назва", "Хв", "Складність", "Корисність (0–100)"]
CANDIDATE_TYPES = ["str", "number", "str", "number"]

DIFFICULTY_UA = {"easy": "легка", "medium": "середня", "hard": "складна"}
HEALTH_UA = {"healthy": "корисна", "moderate": "помірна", "unhealthy": "нездорова"}
DIET_UA = {
    "vegan": "веганська",
    "vegetarian": "вегетаріанська",
    "halal": "халяль",
    "kosher": "кошер",
    "nut_free": "без горіхів",
    "dairy_free": "без молочного",
    "gluten_free": "без глютену",
}

STORAGE_KEY = "cookagent_session"
STORAGE_SECRET = "cookagent-ui-state"  # only obfuscates localStorage; holds the visitor's own chat


# ---------------------------------------------------------------- session state

def new_session() -> dict:
    return {"chat": [], "history": [], "recipe": None, "shown": []}


def load_session(raw: object) -> dict:
    """Saved browser state -> a complete session dict (tolerates empty or damaged data)."""
    session = new_session()
    if not isinstance(raw, dict):
        return session
    for key in ("chat", "history", "shown"):
        if isinstance(raw.get(key), list):
            session[key] = raw[key]
    if isinstance(raw.get("recipe"), dict):
        session["recipe"] = raw["recipe"]
    return session


# ---------------------------------------------------------------- rendering

def render_recipe(recipe: dict | None) -> str:
    """Markdown card for one recipe (separate from the chat text)."""
    if not recipe:
        return EMPTY_RECIPE
    health = HEALTH_UA.get(recipe["health_level"], recipe["health_level"])
    difficulty = DIFFICULTY_UA.get(recipe["difficulty"], recipe["difficulty"])
    facts = [
        f"⏱ {recipe['total_min']} хв (підготовка {recipe['prep_min']}, готування {recipe['cook_min']})",
        f"📊 складність: {difficulty}",
        f"💚 корисність: {health} ({recipe['healthiness_score']}/100)",
    ]
    blocks = [f"## {recipe['title']}", " · ".join(facts)]
    if recipe["diets"]:
        diets = ", ".join(DIET_UA.get(diet, diet) for diet in recipe["diets"])
        blocks.append(f"**Дієти (авто-мітки):** {diets}")
    if recipe["cuisines"]:
        blocks.append("**Кухня:** " + ", ".join(recipe["cuisines"]))
    if recipe["description"]:
        blocks.append(f"> {recipe['description']}")
    blocks.append("### Інгредієнти\n\n" + "\n".join(f"- {item}" for item in recipe["ingredients"]))
    steps = "\n".join(f"{number}. {step}" for number, step in enumerate(recipe["directions"], start=1))
    blocks.append("### Кроки\n\n" + steps)
    return "\n\n".join(blocks)


def candidate_rows(cards: list[dict]) -> list[list]:
    """Table rows without ids (ids stay in the session so a click can find the recipe)."""
    return [
        [
            card["title"],
            card["total_min"],
            DIFFICULTY_UA.get(card["difficulty"], card["difficulty"]),
            card["healthiness_score"],
        ]
        for card in cards
    ]


def find_card(shown: list[dict], row_value: list | None, index: int | None) -> dict | None:
    """The card for a clicked row. Matches by title so sorting the table cannot misdirect it."""
    if row_value:
        for card in shown:
            if card["title"] == row_value[0]:
                return card
    if index is not None and 0 <= index < len(shown):
        return shown[index]
    return None


def row_prompt(card: dict) -> str:
    return f"Покажи рецепт «{card['title']}» (id {card['id']}) і розкажи, як його приготувати."


# ---------------------------------------------------------------- rate limiting helpers

def client_id(request: gr.Request | None) -> str:
    """Best-effort visitor identity: forwarded IP behind a proxy, else socket IP, else session."""
    if request is None:
        return "anonymous"
    forwarded = request.headers.get("x-forwarded-for", "") if request.headers else ""
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return request.session_hash or "anonymous"


def limit_message(retry_after_s: int, scope: str) -> str:
    minutes = max(1, retry_after_s // 60)
    who = "Ви вичерпали ліміт безкоштовних запитів" if scope == "client" else "Загальний ліміт безкоштовних запитів сервісу вичерпано"
    return (
        f"⏳ {who}. Спробуйте приблизно через {minutes} хв або вставте власний ключ Groq у полі "
        "нижче, тоді ліміт не застосовується."
    )


# ---------------------------------------------------------------- app

def build_demo(toolbox: ToolBox, limiter: RateLimiter, server_key: str) -> gr.Blocks:
    def render(state: dict) -> tuple:
        """Common return shape: (message box, session, chat, recipe card, options table)."""
        return "", state, state["chat"], render_recipe(state["recipe"]), candidate_rows(state["shown"])

    def respond(user_text, session, user_key, request: gr.Request):
        state = load_session(session)
        user_text = (user_text or "").strip()
        if not user_text:
            return render(state)

        chat = [*state["chat"], {"role": "user", "content": user_text}]
        user_key = (user_key or "").strip()

        def reply_only(text: str) -> tuple:
            return render({**state, "chat": [*chat, {"role": "assistant", "content": text}]})

        if not user_key:
            if not server_key:
                return reply_only("Сервісний ключ не налаштовано. Вставте власний ключ Groq у полі нижче.")
            decision = limiter.acquire(client_id(request))
            if not decision.allowed:
                return reply_only(limit_message(decision.retry_after_s, decision.scope))

        try:
            result = run_turn(make_client(user_key or server_key), toolbox, state["history"], user_text)
        except AuthenticationError:
            return reply_only("Groq відхилив ключ. Перевірте, що ключ введено правильно.")
        except RateLimitError:
            return reply_only("Groq тимчасово обмежив кількість запитів. Спробуйте за хвилину.")
        except OpenAIError:
            return reply_only("Не вдалося звернутися до моделі. Спробуйте ще раз.")

        return render(
            {
                "chat": [*chat, {"role": "assistant", "content": result.answer}],
                "history": result.messages,
                # Keep the previous recipe / options when this turn produced none, so choosing
                # one option does not make the others disappear.
                "recipe": result.recipe or state["recipe"],
                "shown": result.candidates or state["shown"],
            }
        )

    def restore(session):
        state = load_session(session)
        return state["chat"], render_recipe(state["recipe"]), candidate_rows(state["shown"])

    def reset():
        return render(new_session())

    def pick_row(session, evt: gr.SelectData):
        index = evt.index[0] if isinstance(evt.index, (list, tuple)) else evt.index
        card = find_card(load_session(session)["shown"], evt.row_value, index)
        return row_prompt(card) if card else gr.skip()

    with gr.Blocks(title="CookAgent") as demo:
        gr.Markdown(f"# {TITLE}")
        gr.Markdown(DESCRIPTION)
        session = gr.BrowserState(new_session(), storage_key=STORAGE_KEY, secret=STORAGE_SECRET)

        with gr.Row():
            with gr.Column(scale=3):
                chatbot = gr.Chatbot(label="Розмова", height=460)
                message = gr.Textbox(
                    label="Повідомлення",
                    placeholder="Напр.: хочу веганську вечерю до 30 хвилин без горіхів",
                    lines=1,
                )
                with gr.Row():
                    send = gr.Button("Надіслати", variant="primary")
                    new_chat = gr.Button("Нова розмова")
                api_key = gr.Textbox(
                    label="Власний ключ Groq (необов'язково)",
                    type="password",
                    placeholder="gsk_… — знімає ліміт безкоштовних запитів; не зберігається",
                )
            with gr.Column(scale=2):
                recipe_panel = gr.Markdown(EMPTY_RECIPE, label="Рецепт")
                options = gr.Dataframe(
                    headers=CANDIDATE_HEADERS,
                    datatype=CANDIDATE_TYPES,
                    value=[],
                    interactive=False,
                    wrap=True,
                    label="Запропоновані варіанти (клікніть рядок, щоб обрати)",
                )

        inputs = [message, session, api_key]
        outputs = [message, session, chatbot, recipe_panel, options]
        message.submit(respond, inputs, outputs)
        send.click(respond, inputs, outputs)
        new_chat.click(reset, None, outputs)
        options.select(pick_row, [session], [message])
        demo.load(restore, [session], [chatbot, recipe_panel, options])

    return demo
