---
title: CookAgent
emoji: 🍳
sdk: gradio
sdk_version: 6.31.0
app_file: app.py
pinned: false
---

# CookAgent

LLM-агент — порадник з приготування їжі. Користувач описує, що хоче
приготувати (дієта чи релігійні обмеження, алергії, наявні продукти, основний інгредієнт, смак, час),
а агент сам вирішує, які інструменти викликати, щоб знайти рецепт у базі з ~25 тис. рецептів, і
пояснює приготування. Під час розмови можна питати поради щодо вибраного рецепта.

Модель: `openai/gpt-oss-120b` через Groq API.

## Призначення

Підібрати рецепт під потреби користувача й провести його через приготування. Агент працює лише на
читання: він не змінює базу, а всі відповіді про рецепти бере з результатів інструментів.

## Інструменти агента

| Інструмент | Що робить |
|---|---|
| `search_recipes` | фільтри: дієти (vegan, vegetarian, halal, kosher, nut/dairy/gluten-free), основний інгредієнт, обовʼязкові й виключені інгредієнти, кухня, тип страви, смаки, час, складність, «здоровість», текст у назві |
| `match_pantry` | рецепти, що використовують якомога більше продуктів, які є в користувача |
| `get_recipe` | повний рецепт: інгредієнти з кількостями та кроки |

Щоб не витрачати токени, кожен пошук повертає лише 5-10 **випадково** обраних збігів (разом із
`total_matches`), а повний текст дає тільки `get_recipe`. Рецепт і список знайдених варіантів
показуються окремими елементами інтерфейсу праворуч.

## Дані

[Extended Recipes Dataset: 64K Dishes](https://www.kaggle.com/datasets/wafaaelhusseini/extended-recipes-dataset-64k-dishes)
(Kaggle). `python -m data_load prepare` стискає його до `dataset/recipes_slim.csv.gz` (≈9 МіБ, 24 971
унікальних рецептів), а при старті застосунку `ensure_database()` будує з нього SQLite
(`data/recipes.db`, не комітиться). Мітки дієт, кухні та страви в датасеті згенеровані автоматично й
можуть бути неточними.

Приклади запитів для демонстрації: [DEMO_QUERIES.md](DEMO_QUERIES.md).

## Логи викликів інструментів

Кожен виклик інструмента агентом пишеться в лог (логер `cookagent.agent`) з назвою, аргументами,
результатом і часом виконання. Також логуються початок і кінець кожної репліки (кількість запитів до
моделі, токени, використані інструменти, id варіантів, які агент запропонував). У інтерфейс логи не
виводяться, вони йдуть у консоль, звідки запущено `python app.py`:

```
00:38:17 cookagent.agent INFO: turn start: user='Хочу веганську вечерю до 30 хвилин без горіхів'
00:38:18 cookagent.agent INFO: tool_call search_recipes args={"course":"main","diets":["vegan","nut_free"],"limit":5,"max_total_min":30} -> returned=5 total_matches=62 [Simple No-Cook Pizza Sauce, Pretzel Crust, ...] (215 ms)
00:38:18 cookagent.agent INFO: turn end: llm_requests=2 tokens=3252+241 tools=['search_recipes'] options=[24272, 23473, 19670]
00:39:29 cookagent.agent INFO: tool_call get_recipe args={"recipe_id":23473} -> recipe #23473 'No-Mayo Pasta Salad' (13 ms)
```

Як переглядати логи:

- **Локально:** просто запустіть `python app.py` і тримайте вікно термінала поруч з браузером.
  Щоб бачити лише виклики інструментів, відфільтруйте рядки: у PowerShell
  `python app.py 2>&1 | Select-String "tool_call"`, у bash `python app.py 2>&1 | grep tool_call`.
- **Зберегти в файл і дивитись у реальному часі:** `python app.py 2>&1 | Tee-Object agent.log`
  (PowerShell) або `python app.py 2>&1 | tee agent.log` (bash).
- **На Hugging Face Space:** вкладка **Logs** (Container logs) у вікні Space показує той самий вивід.
- **Рівень деталізації:** змінна `LOG_LEVEL` у `.env` (`INFO` за замовчуванням, `WARNING` вимикає
  логи викликів).

## Запуск

```bash
pip install -r requirements-dev.txt
cp .env.example .env   # вписати GROQ_API_KEY
python app.py
```

Ключ можна також вставити в поле інтерфейсу: тоді обмеження на кількість запитів не діють. Для
спільного серверного ключа діє ліміт (`FREE_TURNS_PER_HOUR` на відвідувача, `FREE_TURNS_PER_DAY`
загалом). Тести: `pytest` (без запитів до LLM), `pytest -m live` (справжній виклик Groq).
