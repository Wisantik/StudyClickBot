import os
import json
import re
import requests
from bs4 import BeautifulSoup
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

        print("[FC] web_search RAW results:")
        for r in results:
            print(r)

        formatted_results = []
        for r in results:
            if r.get("href"):
                formatted_results.append({
                    "title": r.get("title", ""),
                    "snippet": r.get("body", ""),
                    "link": r.get("href", "")
                })

        print(f"[FC] web_search formatted count: {len(formatted_results)}")
        return formatted_results

    except Exception as e:
        print(f"[FC] web_search ERROR: {e}")
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

def run_fc(user_id: int, query: str, prompt: str, model="gpt-5.1-2025-11-13"):
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": query}
    ]

    print(f"[FC] User {user_id} | model={model}")
    print(f"[FC] User query (first 120 chars): {query[:120]!r}")

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
        print("[FC] Model decision: ❌ tools NOT used")
        return msg.content

    # ✅ TOOLS ИСПОЛЬЗОВАНЫ
    print(f"[FC] Model decision: ✅ tools USED ({len(tool_calls)})")

    messages.append(msg)

    # 2️⃣ Выполнение tool'ов
    for call in tool_calls:
        print(f"[FC] Tool called: {call.function.name}")

        if call.function.name == "web_search":
            tools_used = True

            args = json.loads(call.function.arguments)
            search_query = args.get("query", "")
            print(f"[FC] web_search query: {search_query!r}")

            result = _perform_web_search(search_query)
            web_search_result_text = result or ""

            result = _perform_web_search(search_query)

            if not result:
                print("[FC] web_search result: ❌ empty")
            else:
                print(f"[FC] web_search result length: {len(result)}")

            messages.append({
                "tool_call_id": call.id,
                "role": "tool",
                "name": "web_search",
                "content": result
            })
        if tools_used:
            tools_policy = (
                "ВАЖНО:\n"
                "- Ты использовал инструмент web_search.\n"
                "- Тебе ЗАПРЕЩЕНО объяснять пользователю, как искать вручную.\n"
                "- Ты ОБЯЗАН использовать результаты web_search в ответе.\n"
                "- Если результаты поиска нерелевантны — прямо скажи: "
                "'Поиск дал нерелевантные результаты'.\n"
                "- Не давай общих советов и инструкций без ссылок из поиска.\n"
                "- Используй конкретные ссылки, названия и факты из результатов."
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