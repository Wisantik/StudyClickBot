import os
import sys
import json
import types
import re
import requests
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS

# ДОБАВЛЯЕМ путь newSDK в sys.path
sdk_path = os.path.abspath(os.path.dirname(__file__))
if sdk_path not in sys.path:
    sys.path.insert(0, sdk_path)

# ===========================
#   ИМПОРТ OpenAI (ГИБРИД)
# ===========================
try:
    import openai
except Exception as e:
    raise ImportError("Не найден модуль openai!") from e


# --- НОВАЯ ВЕРСИЯ SDK (OpenAI >= v1.0) ---
if hasattr(openai, "OpenAI"):
    OpenAI = openai.OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# --- СТАРАЯ ВЕРСИЯ SDK (OpenAI <= v1.0) ---
else:
    class LegacyWrapper:
        def __init__(self, api_key):
            self._mod = openai
            self._mod.api_key = api_key

            # создаём client.chat.completions.create(...)
            self.chat = types.SimpleNamespace()
            self.chat.completions = types.SimpleNamespace(create=self._create)

        def _normalize(self, resp):
            if hasattr(resp, "choices"):
                return resp

            if isinstance(resp, dict):
                choices = []
                for c in resp.get("choices", []):
                    msg = c.get("message") or {}
                    content = msg.get("content") or c.get("text", "")
                    choices.append(types.SimpleNamespace(message=types.SimpleNamespace(content=content)))
                return types.SimpleNamespace(choices=choices)

            return types.SimpleNamespace(
                choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=str(resp)))]
            )

        def _create(self, model=None, messages=None, **kwargs):
            api = self._mod
            try:
                resp = api.ChatCompletion.create(
                    model=model,
                    messages=messages,
                    **kwargs
                )
            except Exception:
                # fallback для очень старых API
                prompt = self._messages_to_prompt(messages)
                resp = api.Completion.create(model=model, prompt=prompt, **kwargs)

            return self._normalize(resp)

        def _messages_to_prompt(self, messages):
            parts = []
            for m in messages:
                parts.append(f"[{m.get('role')}]\n{m.get('content')}")
            return "\n\n".join(parts)

    client = LegacyWrapper(api_key=os.getenv("OPENAI_API_KEY"))


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
