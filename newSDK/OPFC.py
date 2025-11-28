import json
import re
import os
import sys
from bs4 import BeautifulSoup
import requests
from duckduckgo_search import DDGS
from openai import OpenAI

# =======================
#  ENSURE LOCAL OPENAI
# =======================

sdk_path = os.path.abspath(os.path.dirname(__file__))
if sdk_path not in sys.path:
    sys.path.insert(0, sdk_path)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# =======================
#     DDGS WEB SEARCH
# =======================

def _call_search_api(search_query):
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(search_query, region="ru-ru",
                                     safesearch="moderate", max_results=None))
        formatted = []
        for r in results:
            if not r.get("href"):
                continue
            formatted.append({
                "title": r.get("title", ""),
                "snippet": r.get("body", ""),
                "link": r["link"]
            })
        return formatted
    except:
        return []


def _fetch_page_content(url: str) -> str:
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        html = requests.get(url, headers=headers, timeout=10).text
        soup = BeautifulSoup(html, "html.parser")
        text = " ".join(p.get_text() for p in soup.find_all("p"))
        return text[:4000]
    except:
        return ""


def _perform_web_search(query: str) -> str:
    cleaned_query = re.sub(
        r'^(привет|здравствуй|как дела|найди|найди мне)\s+',
        "", query, flags=re.IGNORECASE
    ).strip()

    search_query = f"{cleaned_query} lang:ru"
    results = _call_search_api(search_query)

    if not results:
        return "🔍 Не удалось найти актуальные результаты."

    pages = []
    links = []

    for r in results[:10]:
        text = _fetch_page_content(r["link"])
        if text:
            pages.append(f"Источник: {r['title']} ({r['link']})\n{text}\n")
            links.append(r)
            if len(pages) == 3:
                break

    if not pages:
        return "🔍 Нашлись ссылки, но не удалось загрузить содержимое страниц."

    combined = "\n\n".join(pages)
    src = "\n\n📚 *Источники:*\n" + "\n".join(
        [f"🔗 [{r['title']}]({r['link']})" for r in links]
    )

    return combined + src


# =======================
#    TOOLS Definition
# =======================

tools = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Актуальный интернет-поиск через DDGS.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Поисковый запрос пользователя."
                    }
                },
                "required": ["query"]
            }
        }
    }
]


# =======================
#     MAIN FC RUNNER
# =======================

def run_fc(user_id: int, query: str, prompt: str, model="gpt-4o-mini"):
    """
    - prompt ассистента берётся из main.py
    - если модель вызывает tool -> выполняется DDGS-поиск
    - иначе возвращается обычный ответ
    """

    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": query}
    ]

    # === Первый вызов — модель решает, нужен ли tool ===
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        tools=tools,
        tool_choice="auto"
    )

    msg = response.choices[0].message
    tool_calls = msg.tool_calls

    # Если модель НЕ вызвала tool — возвращаем её ответ
    if not tool_calls:
        return msg.content

    # Модель вызвала tool => выполняем
    messages.append(msg)

    for call in tool_calls:
        if call.function.name == "web_search":
            args = json.loads(call.function.arguments)
            search_query = args["query"]

            result = _perform_web_search(search_query)

            messages.append({
                "tool_call_id": call.id,
                "role": "tool",
                "name": "web_search",
                "content": result
            })

    # === Второй вызов — финальный ответ после web search ===
    final = client.chat.completions.create(
        model=model,
        messages=messages
    )

    return final.choices[0].message.content