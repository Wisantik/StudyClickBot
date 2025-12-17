import os
import json
import re
import requests
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS
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
            results = list(ddgs.text(search_query, region="ru-ru", safesearch="moderate", max_results=None))

        formatted_results = []
        for r in results:
            if r.get("href"):
                formatted_results.append({
                    "title": r.get("title", ""),
                    "snippet": r.get("body", ""),
                    "link": r["link"]
                })
        return formatted_results

    except Exception:
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
    }
]


# ============================================================
#                  Function-Calling Runner
# ============================================================

def run_fc(user_id: int, query: str, prompt: str, model="gpt-4o-mini"):
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": query}
    ]

    # 1) Первый вызов — модель решает сама, нужен ли tool
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        tools=tools,
        tool_choice="auto"
    )

    msg = resp.choices[0].message
    tool_calls = getattr(msg, "tool_calls", None)


    # Если поиск не вызван — возвращаем прямой ответ
    if not tool_calls:
        return msg.content

    messages.append(msg)

    # Выполняем tool
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

    # 2) Ответ после обработки результата
    final = client.chat.completions.create(
        model=model,
        messages=messages
    )

    return final.choices[0].message.content
