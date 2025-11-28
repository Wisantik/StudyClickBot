import json
from textwrap import shorten
from bs4 import BeautifulSoup
import requests
import re

import os
import sys
import types

# Убедимся, что newSDK в PYTHONPATH (чтобы локальная папка openai импортировалась)
sdk_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '.'))
if sdk_path not in sys.path:
    sys.path.insert(0, sdk_path)

# Попытка импортировать пакет openai (локальный в newSDK/openai или установленный)
try:
    import openai
except Exception as e:
    raise ImportError("Не удалось импортировать 'openai'. Убедись, что в newSDK/ есть папка openai или установи пакет: pip install --upgrade openai -t newSDK") from e

# Если это новая версия (v2+) с классом OpenAI — используем её прямо
if hasattr(openai, "OpenAI"):
    OpenAI = openai.OpenAI
    client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
else:
    # Legacy wrapper: оборачиваем старый openai (v<=1.x) чтобы обеспечить
    # интерфейс client.chat.completions.create(...) и нормализованный ответ.
    class _LegacyWrapper:
        def __init__(self, api_key=None):
            self._mod = openai
            self._mod.api_key = api_key or os.getenv('OPENAI_API_KEY')

            # создаём объект client.chat.completions.create
            self.chat = types.SimpleNamespace()
            self.chat.completions = types.SimpleNamespace(create=self._create_completion)

        def _normalize_response(self, resp):
            """
            Преобразует ответ legacy (dict) в объект с .choices[0].message.content
            и совместим с кодом, ожидающим object. Если resp уже объект — пытаемся вернуть как есть.
            """
            # Если это уже объект с атрибутом choices — возвращаем
            if hasattr(resp, "choices"):
                return resp

            # Обычно legacy возвращает dict
            if isinstance(resp, dict):
                choices = []
                for c in resp.get("choices", []):
                    # c может содержать 'message' (dict) или 'text' (старые модели)
                    if "message" in c and isinstance(c["message"], dict):
                        msg_content = c["message"].get("content", c["message"].get("text", ""))
                    else:
                        # fallback к 'text' или пустой строке
                        msg_content = c.get("text", "")
                    choices.append(types.SimpleNamespace(message=types.SimpleNamespace(content=msg_content)))
                return types.SimpleNamespace(choices=choices, raw=resp)
            # fallback — вернуть оригинал упакованный
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=str(resp)))], raw=resp)

        def _create_completion(self, model=None, messages=None, **kwargs):
            """
            Вызывает legacy openai.ChatCompletion.create и возвращает нормализованный объект.
            Поддерживает передачу messages как в новом API.
            """
            # Для старых версий используется openai.ChatCompletion.create
            api = self._mod
            # Некоторые старые версии требуют ключи в глобале (api.api_key установлен выше)
            try:
                resp = api.ChatCompletion.create(model=model, messages=messages, **kwargs)
            except AttributeError:
                # Если в крайне старой версии нет ChatCompletion, попробуем Completion (gpt-3-style)
                resp = api.Completion.create(model=model, prompt=_messages_to_prompt(messages), **kwargs)

            return self._normalize_response(resp)

    def _messages_to_prompt(messages):
        # конвертируем messages -> single prompt (если нужно для старых Completion)
        if not messages:
            return ""
        parts = []
        for m in messages:
            role = m.get("role", "")
            content = m.get("content", "")
            parts.append(f"[{role}]\n{content}")
        return "\n\n".join(parts)

    client = _LegacyWrapper(api_key=os.getenv('OPENAI_API_KEY'))


# ======== WEB SEARCH (DDGS) ======== (переносим сюда старые функции, но адаптируем для FC)
def _call_search_api(search_query):
    """Выполняет поиск через DDGS и возвращает форматированные результаты."""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(search_query, region="ru-ru", safesearch="moderate", max_results=None))
        
        formatted_results = []
        for result in results:
            title = result.get('title')
            href = result.get('href')
            body = result.get('body', '') or ""
            if title and href and not href.endswith("wiktionary.org/wiki/"):
                formatted = {
                    'title': title,
                    'snippet': body,
                    'link': href
                }
                formatted_results.append(formatted)
        
        return formatted_results
    except Exception as e:
        return []  # Без принтов, чтобы не засорять (логируем в main если нужно)

def _fetch_page_content(url: str) -> str:
    """Скачивает и очищает контент страницы."""
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        html = requests.get(url, headers=headers, timeout=10).text
        soup = BeautifulSoup(html, "html.parser")
        text = ' '.join(p.get_text() for p in soup.find_all('p'))
        return text[:4000]  # ограничение по длине
    except Exception:
        return ""

def _perform_web_search(query: str) -> str:
    """Выполняет веб-поиск и возвращает объединённый контекст (без AI-генерации здесь)."""
    cleaned_query = re.sub(
        r'^(привет|здравствуй|как дела|найди|найди мне)\s+',
        '', query, flags=re.IGNORECASE
    ).strip()
    search_query = f"{cleaned_query} lang:ru"
    search_results = _call_search_api(search_query)
    if not search_results:
        return "🔍 Не удалось найти актуальные результаты по вашему запросу."

    page_texts = []
    successful_links = []
    max_success = 3
    max_attempts = 10
    for r in search_results[:max_attempts]:
        url = r['link']
        text = _fetch_page_content(url)
        if text:
            page_texts.append(f"Источник: {r['title']} ({r['link']})\n{text}\n")
            successful_links.append(r)
            if len(page_texts) >= max_success:
                break

    if not page_texts:
        return "🔍 Нашлись ссылки, но не удалось загрузить содержимое страниц."

    combined_context = "\n\n".join(page_texts)
    sources_block = "\n\n📚 *Источники:*\n" + "\n".join(
        [f"🔗 [{r['title']}]({r['link']})" for r in successful_links]
    )
    
    return combined_context + sources_block  # Возвращаем только контекст + источники (AI обработает в FC)

# ======== TOOLS для Function Calling ========
tools = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Выполняет веб-поиск по запросу пользователя, если в сообщении есть ключевые слова вроде 'найди', 'что сейчас', 'новости', 'поиск', 'в интернете', 'актуально' или если ответ требует актуальной информации из интернета. Возвращает релевантный контекст и источники.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Поисковый запрос (очищенный от приветствий, на русском, с добавлением 'lang:ru' для релевантности)."
                    }
                },
                "required": ["query"]
            }
        }
    }
]

# ======== FUNCTION CALLING RUNNER ========
def run_fc(user_id: int, query: str, assistant_key: str, model: str = "gpt-4o-mini") -> str:
    """
    Запускает Function Calling для веб-поиска.
    - Формирует messages на основе промпта ассистента + запроса пользователя.
    - Если модель вызывает tool, выполняет _perform_web_search.
    - Затем генерирует финальный ответ на основе контекста.
    - Возвращает финальный ответ (str).
    """
    # Загружаем промпт ассистента (из конфига, но поскольку конфиг в main.py, предполагаем, что assistant_prompt передаётся или загружается здесь; для простоты используем placeholder)
    # В реальности: или импортируйте load_assistants_config из main, или передавайте prompt как параметр (рекомендую добавить в вызов run_fc)
    # Но для этого примера: загружаем конфиг здесь (дублируем импорт, но ок для изоляции)
    from assistance import load_assistants_config  # Предполагаем, что assistance.py доступен (импортируйте если нужно)
    config = load_assistants_config()
    assistant_settings = config["assistants"].get(assistant_key, {})
    assistant_prompt = assistant_settings.get("prompt", "Вы просто бот.")

    # Формируем messages
    messages = [
        {"role": "system", "content": assistant_prompt},
        {"role": "user", "content": query}
    ]

    # Первый вызов: с tools
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        tools=tools,
        tool_choice="auto"  # Авто-выбор: модель решает, нужен ли tool
    )

    response_message = response.choices[0].message
    tool_calls = response_message.tool_calls

    if tool_calls:
        # Добавляем ответ модели в историю
        messages.append(response_message)
        
        for tool_call in tool_calls:
            if tool_call.function.name == "web_search":
                # Парсим аргументы
                function_args = json.loads(tool_call.function.arguments)
                search_query = function_args.get("query")
                
                # Выполняем поиск
                search_result = _perform_web_search(search_query)
                
                # Добавляем результат tool в историю
                messages.append({
                    "tool_call_id": tool_call.id,
                    "role": "tool",
                    "name": "web_search",
                    "content": search_result
                })
        
        # Второй вызов: с результатами tool для финальной генерации
        second_response = client.chat.completions.create(
            model=model,
            messages=messages
        )
        final_answer = second_response.choices[0].message.content
    else:
        # Если tool не нужен — берём прямой ответ
        final_answer = response_message.content

    return final_answer

# ======== ПЕРЕНЕСЁННАЯ ФУНКЦИЯ ========
def needs_web_search(message: str) -> bool:
    keywords = ["найди", "что сейчас", "новости", "поиск", "в интернете", "актуально"]
    return any(kw in message.lower() for kw in keywords)