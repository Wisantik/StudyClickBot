import os
import json
import re
import requests
from bs4 import BeautifulSoup
from database import get_chat_history
from ddgs import DDGS
from openai import OpenAI

# жёстко отключаем прокси ENV (на всякий случай)
os.environ.pop("OPENAI_BASE_URL", None)
os.environ.pop("OPENAI_API_BASE", None)
os.environ.pop("OPENAI_ENDPOINT", None)

api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
if not api_key:
    raise RuntimeError("OPENAI_API_KEY is not set")

client = OpenAI(api_key=api_key, base_url="https://api.openai.com/v1")

# ============================================================
#                     ВЕБ ПОИСК (DDGS)
# ============================================================

def _call_search_api(search_query):
    try:
        with DDGS() as ddgs:
            results = list(
                ddgs.text(
                    search_query,
                    region="ru-ru",
                    safesearch="moderate",
                    max_results=5
                )
            )

        formatted_results = []
        for r in results:
            if r.get("href"):
                formatted_results.append({
                    "title": r.get("title", ""),
                    "snippet": r.get("body", ""),
                    "link": r.get("href", "")
                })

        return formatted_results

    except Exception as e:
        print(f"[FC][ERROR] web_search failed: {e}")
        return []




def _fetch_page_content(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        html = requests.get(url, headers=headers, timeout=10).text
        soup = BeautifulSoup(html, "html.parser")
        text = " ".join(p.get_text() for p in soup.find_all("p"))
        return text[:4000]
    except:
        return ""


def _perform_web_search(query):
    cleaned = re.sub(
        r"^(привет|здравствуй|найди|что сейчас|как дела)\s+",
        "",
        query,
        flags=re.I
    ).strip()

    search_query = f"{cleaned} lang:ru"
    results = _call_search_api(search_query)

    # 🔥 ВОТ ЗДЕСЬ ЛОГИРУЕМ
    log_web_search(search_query, results)

    if not results:
        return "🔍 Не удалось найти результаты."

    pages = []
    links = []

    for r in results[:10]:
        txt = _fetch_page_content(r["link"])
        if txt:
            pages.append(f"Источник: {r['title']} ({r['link']})\n{txt}\n")
            links.append(r)
            if len(pages) >= 3:
                break

    if not pages:
        return "🔍 Нашлись ссылки, но страница не загрузилась."

    final_text = "\n\n".join(pages)
    final_text += "\n\n📚 Источники:\n" + "\n".join(
        [f"🔗 [{r['title']}]({r['link']})" for r in links]
    )

    return final_text


import requests
from bs4 import BeautifulSoup

def fetch_url_content(url: str, max_chars: int = 12000) -> str:
    try:
        headers = {
            "User-Agent": "Mozilla/5.0"
        }
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()

        soup = BeautifulSoup(r.text, "html.parser")

        # удаляем мусор
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        text = soup.get_text(separator="\n")
        text = "\n".join(line.strip() for line in text.splitlines() if line.strip())

        return text[:max_chars]

    except Exception as e:
        return f"ERROR: Не удалось загрузить страницу: {e}"

# ============================================================
#                       TOOLS
# ============================================================
tools = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Актуальный веб-поиск (DDGS)",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "Загружает содержимое страницы по URL и возвращает текст для анализа",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Полный URL страницы"
                    }
                },
                "required": ["url"]
            }
        }
    }
]



# ============================================================
#                  Function-Calling Runner
# ============================================================

import datetime

def run_fc(user_id: int, query: str, prompt: str, model="gpt-5.1-2025-11-13", max_reflection_attempts: int = 3):
    history = get_chat_history(user_id, limit=10)
    messages = [
        {"role": "system", "content": prompt},
        *history,
        {"role": "user", "content": query}
    ]

    today = datetime.date.today().strftime("%d.%m.%Y")
    print(f"[FC] User {user_id} | model={model} | attempt=1/ {max_reflection_attempts+1}")

    for attempt in range(max_reflection_attempts + 1):
        # 1. Вызываем модель с инструментами
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice="auto"
        )
        msg = resp.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None)

        # Если инструменты не нужны — сразу возвращаем ответ
        if not tool_calls:
            print(f"[FC] ✅ Финальный ответ после {attempt+1} попыток (без инструментов)")
            return msg.content

        # 2. Выполняем инструменты (web_search / fetch_url)
        messages.append(msg)  # добавляем вызов инструментов
        tools_used = False

        for call in tool_calls:
            if call.function.name == "web_search":
                tools_used = True
                args = json.loads(call.function.arguments)
                search_query = args.get("query", query)
                result = _perform_web_search(search_query)
                messages.append({
                    "tool_call_id": call.id,
                    "role": "tool",
                    "name": "web_search",
                    "content": result or "Поиск не дал результатов."
                })
            elif call.function.name == "fetch_url":
                args = json.loads(call.function.arguments)
                content = fetch_url_content(args.get("url"))
                messages.append({
                    "tool_call_id": call.id,
                    "role": "tool",
                    "name": "fetch_url",
                    "content": content
                })

        # 3. Самооценка (Reflection) — только если был поиск
        if tools_used and attempt < max_reflection_attempts:
            reflection_prompt = (
                f"Сегодня {today}. Ты только что сделал web_search. "
                "Оцени качество полученных источников по шкале 1–10.\n"
                "Критерии:\n"
                "- 10 = самые свежие официальные источники (cbr.ru, consultant.ru, government.ru и т.д.)\n"
                "- 7–9 = хорошие, но можно лучше\n"
                "- ниже 7 = устаревшие, противоречивые или нерелевантные\n\n"
                "Ответь ТОЛЬКО в формате:\n"
                "Оценка: X/10\n"
                "Решение: OK или НУЖЕН_ПОВТОРНЫЙ_ПОИСК\n"
                "Если нужен повтор — предложи улучшенный поисковый запрос."
            )

            messages.append({"role": "system", "content": reflection_prompt})

            reflection = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=300
            ).choices[0].message.content

            print(f"[FC] Reflection (попытка {attempt+1}): {reflection[:200]}...")

            if "НУЖЕН_ПОВТОРНЫЙ_ПОИСК" not in reflection.upper():
                print(f"[FC] ✅ Источники признаны хорошими на попытке {attempt+1}")
                break  # выходим из цикла — идём к финальному ответу

            # Если нужно повторить — добавляем уточнение
            messages.append({
                "role": "system",
                "content": "Источники были недостаточно хорошими. Сделай повторный web_search с более точным запросом."
            })
            print(f"[FC] 🔄 Делаем повторный поиск (попытка {attempt+2})")
            continue  # идём на следующую итерацию

        # Если дошли сюда — либо последняя попытка, либо источники хорошие
        break

    # 4. Финальный ответ с лучшими источниками
    final = client.chat.completions.create(
        model=model,
        messages=messages
    )
    print(f"[FC] ✅ Финальный ответ после {attempt+1} попыток")
    return final.choices[0].message.content

def log_web_search(query: str, results: list):
    print("\n" + "─" * 18 + " WEB SEARCH " + "─" * 18)
    print(f"Query: {query}")
    print(f"Results: {len(results)}\n")

    for i, r in enumerate(results, 1):
        title = (r.get("title") or "").strip()
        link = (r.get("link") or "").replace("https://", "").replace("http://", "")
        print(f"{i}. {title}")
        print(f"   🔗 {link}")

    print("─" * 54 + "\n")
