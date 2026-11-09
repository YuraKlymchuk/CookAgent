"""Gradio interface: chat on the left, recipe card and candidate list on the right."""

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
як змінити порції тощо.

*Прапорці дієт у базі визначені автоматично й можуть містити помилки. Перевіряйте склад, якщо маєте алергію.*"""

EMPTY_RECIPE = "_Тут з'явиться вибраний рецепт._"
CANDIDATE_HEADERS = ["id", "Назва", "Хв", "Складність", "Health"]


def render_recipe(recipe: dict | None) -> str:
    """Markdown card for one recipe (separate from the chat text)."""
    if not recipe:
        return EMPTY_RECIPE
    facts = [
        f"⏱ {recipe['total_min']} хв (підготовка {recipe['prep_min']}, готування {recipe['cook_min']})",
        f"📊 {recipe['difficulty']}",
        f"💚 {recipe['health_level']} ({recipe['healthiness_score']}/100)",
    ]
    blocks = [f"## {recipe['title']}", " · ".join(facts)]
    if recipe["diets"]:
        blocks.append("**Дієти (авто-мітки):** " + ", ".join(recipe["diets"]))
    if recipe["cuisines"]:
        blocks.append("**Кухня:** " + ", ".join(recipe["cuisines"]))
    if recipe["description"]:
        blocks.append(f"> {recipe['description']}")
    blocks.append("### Інгредієнти\n\n" + "\n".join(f"- {item}" for item in recipe["ingredients"]))
    steps = "\n".join(f"{number}. {step}" for number, step in enumerate(recipe["directions"], start=1))
    blocks.append("### Кроки\n\n" + steps)
    return "\n\n".join(blocks)


def candidate_rows(candidates: list[dict]) -> list[list]:
    return [
        [c["id"], c["title"], c["total_min"], c["difficulty"], c["healthiness_score"]] for c in candidates
    ]


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


def build_demo(toolbox: ToolBox, limiter: RateLimiter, server_key: str) -> gr.Blocks:
    def respond(user_text, chat, history, user_key, request: gr.Request):
        user_text = (user_text or "").strip()
        if not user_text:
            return "", chat, history, gr.skip(), gr.skip()

        chat = [*chat, {"role": "user", "content": user_text}]
        user_key = (user_key or "").strip()

        if not user_key:
            if not server_key:
                reply = "Сервісний ключ не налаштовано. Вставте власний ключ Groq у полі нижче."
                return "", [*chat, {"role": "assistant", "content": reply}], history, gr.skip(), gr.skip()
            decision = limiter.acquire(client_id(request))
            if not decision.allowed:
                reply = limit_message(decision.retry_after_s, decision.scope)
                return "", [*chat, {"role": "assistant", "content": reply}], history, gr.skip(), gr.skip()

        try:
            client = make_client(user_key or server_key)
            result = run_turn(client, toolbox, history, user_text)
        except AuthenticationError:
            reply = "Groq відхилив ключ. Перевірте, що ключ введено правильно."
        except RateLimitError:
            reply = "Groq тимчасово обмежив кількість запитів. Спробуйте за хвилину."
        except OpenAIError:
            reply = "Не вдалося звернутися до моделі. Спробуйте ще раз."
        else:
            recipe_view = render_recipe(result.recipe) if result.recipe else gr.skip()
            rows = candidate_rows(result.candidates) if result.candidates else gr.skip()
            chat = [*chat, {"role": "assistant", "content": result.answer}]
            return "", chat, result.messages, recipe_view, rows

        return "", [*chat, {"role": "assistant", "content": reply}], history, gr.skip(), gr.skip()

    def reset():
        return "", [], [], EMPTY_RECIPE, []

    with gr.Blocks(title="CookAgent") as demo:
        gr.Markdown(f"# {TITLE}")
        gr.Markdown(DESCRIPTION)
        history = gr.State([])

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
                candidates = gr.Dataframe(
                    headers=CANDIDATE_HEADERS,
                    value=[],
                    interactive=False,
                    wrap=True,
                    label="Знайдені варіанти",
                )

        inputs = [message, chatbot, history, api_key]
        outputs = [message, chatbot, history, recipe_panel, candidates]
        message.submit(respond, inputs, outputs)
        send.click(respond, inputs, outputs)
        new_chat.click(reset, None, outputs)

    return demo
