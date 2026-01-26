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

def run_fc(user_id: int, query: str, prompt: str, model="gpt-5.1-2025-11-13"):
    history = get_chat_history(user_id, limit=10)
    tools_used = []

    messages = [
        {"role": "system", "content": prompt},
        *history,
        {"role": "user", "content": query}
    ]


    print(f"[FC] User {user_id} | model={model}")
    print(f"[FC] Запрос(120 символов): {query[:120]!r}")

    # 1️⃣ Первый вызов — модель решает, нужен ли tool
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        tools=tools,
        tool_choice="auto"
    )

    msg = resp.choices[0].message
    tool_calls = getattr(msg, "tool_calls", None)

    # ❌ TOOLS НЕ ИСПОЛЬЗОВАНЫ
    if not tool_calls:
        print("[FC] ⚠️ tools NOT used")
        print("\n" + "─" * 16 + " ASSISTANT PREVIEW " + "─" * 16)
        print(msg.content[:300])
        print("─" * 56 + "\n")

        return msg.content


    # ✅ TOOLS ИСПОЛЬЗОВАНЫ
    print(f"[FC] Model decision: ✅ tools USED ({len(tool_calls)})")

    messages.append(msg)

    # 2️⃣ Выполнение tool'ов
    for call in tool_calls:
        print(f"[FC] Tool called: {call.function.name}")
        if call.function.name == "fetch_url":
            args = json.loads(call.function.arguments)
            url = args.get("url")

            print(f"[FC] fetch_url: {url}")

            content = fetch_url_content(url)

            messages.append({
                "tool_call_id": call.id,
                "role": "tool",
                "name": "fetch_url",
                "content": content
            })


        if call.function.name == "web_search":
            tools_used = True

            args = json.loads(call.function.arguments)
            search_query = args.get("query", "")
            print(f"[FC] web_search query: {search_query!r}")

            result = _perform_web_search(search_query)

            if not result:
                print("[FC] web_search result: ❌ empty")
            else:
                print(f"[FC] web_search result length: {len(result)}")

            messages.append({
                "tool_call_id": call.id,
                "role": "tool",
                "name": "web_search",
                "content": result or ""
            })
        if tools_used:
            print("[FC] 🔧 tools USED:")
            for call in tool_calls:
                print(f" - {call.function.name}")

            tools_policy = (
                "ВАЖНО:\n"
                "- Ты использовал инструмент web_search.\n"
                "- Тебе ЗАПРЕЩЕНО объяснять пользователю, как искать вручную.\n"
                "- Ты ОБЯЗАН использовать результаты web_search в ответе.\n"
                "- Если результаты поиска нерелевантны — прямо скажи: "
                "'Поиск дал нерелевантные результаты'.\n"
                "- Не давай общих советов и инструкций без ссылок из поиска.\n"
                "- Используй конкретные ссылки, названия и факты из результатов. \n"
                "Если ты используешь fetch_url: - ты обязан опираться на полученный контент- если контент пуст или нерелевантен — скажи об этом прямо- запрещено говорить, что у тебя нет доступа к ссылке"
            )

            print("[FC] Enforcing web_search usage policy")

            messages.append({
                "role": "system",
                "content": tools_policy
            })

    # 3️⃣ Финальный ответ модели с результатами tool
    final = client.chat.completions.create(
        model=model,
        messages=messages
    )

    print("[FC] Final answer generated")

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
