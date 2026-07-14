import os
import json
import re
import requests
from bs4 import BeautifulSoup
from database import get_chat_history
from ddgs import DDGS
from openai import OpenAI
import os

LAOZHANG_API_KEY = "sk-7zVC8L2L1UEWdoNE1391FcD759Bc46F6Ab5642Ac57A2208b"
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


def generate_image(prompt: str):
    result = client.images.generate(
        model="gpt-image-1",
        prompt=prompt,
        size="1024x1024"
    )

    image_b64 = result.data[0].b64_json

    return image_b64

import os
import time
import requests

BASE_URL = "https://api.laozhang.ai"

VIDEO_MODELS = [

    "sora-2",

    "veo-3.1-generate-preview",

    "veo-3",

    "veo-2",

]

import requests
import time
import tempfile
import os
from typing import Optional

BASE_URL = "https://api.laozhang.ai"
LAOZHANG_API_KEY = "sk-7zVC8L2L1UEWdoNE1391FcD759Bc46F6Ab5642Ac57A2208b"

HEADERS = {"Authorization": f"Bearer {LAOZHANG_API_KEY}"}


def generate_video(prompt: str, timeout: int = 900, interval: int = 8) -> str:
    """Генерирует видео и возвращает путь к локальному файлу"""
    print(f"🎬 [VIDEO GEN] Запрос: {prompt[:120]}{'...' if len(prompt) > 120 else ''}")

    models_order = [
        ("sora-2", "Sora"),
        ("sora-2-pro", "Sora-Pro"),
        ("doubao-seedance-2-0-fast-260128", "Seedance-Fast"),
        ("doubao-seedance-2-0-260128", "Seedance"),
        ("veo-3.1-fast-generate-preview", "Veo-Fast"),
        ("veo-3.1-generate-preview", "Veo"),
    ]

    for model, name in models_order:
        try:
            print(f"   → Пробуем {name} ({model})")
            
            if "sora" in model:
                resp = requests.post(
                    f"{BASE_URL}/v1/videos",
                    headers=HEADERS,
                    data={"model": model, "prompt": prompt, "seconds": "8", "size": "1280x720"},
                    timeout=25
                )
            else:
                # Seedance / Veo
                resp = requests.post(
                    f"{BASE_URL}/v1/videos",
                    headers=HEADERS,
                    data={"model": model, "prompt": prompt, "seconds": "8", "size": "1280x720"},
                    timeout=25
                )

            print(f"   HTTP {resp.status_code}")

            if resp.status_code == 200:
                task_id = resp.json().get("id")
                if task_id:
                    print(f"   Задача создана → {task_id}")
                    path = _poll_and_download(task_id, model, timeout, interval)
                    if path:
                        print(f"✅ Видео готово: {path}")
                        return path

            elif resp.status_code in (503, 429):
                print(f"   {name}: нет свободных каналов (503/429)")
                time.sleep(2)
                continue
            else:
                print(f"   {name}: ошибка {resp.status_code}")

        except Exception as e:
            print(f"   {name} exception: {e}")

    raise Exception("Не удалось сгенерировать видео — все модели недоступны")


def _poll_and_download(task_id: str, model: str, timeout: int, interval: int) -> Optional[str]:
    """Единый polling + download"""
    start = time.time()
    is_veo = "veo" in model.lower()

    while time.time() - start < timeout:
        try:
            status_resp = requests.get(f"{BASE_URL}/v1/videos/{task_id}", headers=HEADERS, timeout=15)
            status_resp.raise_for_status()
            info = status_resp.json()
            status = info.get("status", "")

            print(f"   [{model}] статус = {status}")

            if status == "completed":
                # Пробуем скачать
                for attempt in range(3):
                    try:
                        dl_headers = HEADERS if attempt == 0 else {}  # 2-я и 3-я попытка — без токена
                        dl = requests.get(
                            f"{BASE_URL}/v1/videos/{task_id}/content",
                            headers=dl_headers,
                            stream=True,
                            timeout=60
                        )
                        dl.raise_for_status()

                        tmp_path = os.path.join(tempfile.gettempdir(), f"video_{task_id}.mp4")
                        with open(tmp_path, "wb") as f:
                            for chunk in dl.iter_content(chunk_size=8192 * 4):
                                f.write(chunk)

                        print(f"   ✅ Скачано успешно: {tmp_path}")
                        return tmp_path

                    except requests.exceptions.HTTPError as e:
                        if e.response.status_code == 401 and attempt < 2:
                            print(f"   401 → пробуем без Authorization (попытка {attempt+1})")
                            continue
                        raise

            if status == "failed":
                raise RuntimeError(f"Генерация failed: {info.get('error')}")

            time.sleep(interval)

        except Exception as e:
            print(f"   Poll error: {e}")
            time.sleep(interval)

    raise TimeoutError(f"Timeout при генерации {model}")

# ============================================================
#                       TOOLS
# ============================================================
tools = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Актуальный веб-поиск по запросу пользователя",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Поисковый запрос"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_video",
            "description": "Создает короткое видео по текстовому описанию. Используй когда пользователь просит 'видео', 'ролик', 'сделай видео'",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"}
                },
                "required": ["prompt"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": "Генерирует изображение по текстовому описанию. Используй когда пользователь просит 'фото', 'картинку', 'изображение', 'нарисуй', 'сгенерируй фото'",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"}
                },
                "required": ["prompt"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "Загружает содержимое веб-страницы по URL",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"}
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
    history = get_chat_history(user_id, limit=12)  # ← увеличил до 12, но теперь безопасно
    
    messages = [
        {"role": "system", "content": prompt},
        {"role": "system", "content": "Ты полезный ассистент. "
                                      "Используй generate_image когда просят фото/картинку/нарисуй. "
                                      "Используй generate_video когда просят видео/ролик."},
        *history,
        {"role": "user", "content": query}
    ]

    today = datetime.date.today().strftime("%d.%m.%Y")
    print(f"[FC] User {user_id} | model={model} | history_len={len(history)} | attempt=1/{max_reflection_attempts+1}")

    for attempt in range(max_reflection_attempts + 1):
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice="auto"
        )
        msg = resp.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None)

        if not tool_calls:
            print(f"[FC] ✅ Ответ без инструментов")
            return msg.content

        # Выполняем инструменты
        messages.append(msg)
        tools_used = False

        for call in tool_calls:
            if call.function.name == "web_search":
                tools_used = True
                args = json.loads(call.function.arguments)
                result = _perform_web_search(args.get("query", query))
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
            elif call.function.name == "generate_video":

                args = json.loads(
                    call.function.arguments
                )

                video_url = generate_video(
                    args["prompt"]
                )

                return f"[VIDEO]{video_url}"
            elif call.function.name == "generate_image":
                args = json.loads(call.function.arguments)

                image_b64 = generate_image(args["prompt"])

                return f"[IMAGE]{image_b64}"

        # === РЕФЛЕКСИЯ (на копии, чтобы не загрязнять историю) ===
        if tools_used and attempt < max_reflection_attempts:
            reflection_messages = messages.copy()  # ← копия!
            reflection_messages.append({
                "role": "system",
                "content": f"Сегодня {today}. Оцени качество источников (1-10). "
                           "Ответь **строго** в формате:\n"
                           "Оценка: X/10\n"
                           "Решение: OK или НУЖЕН_ПОВТОРНЫЙ_ПОИСК"
            })

            reflection = client.chat.completions.create(
                model=model,
                messages=reflection_messages,
                max_completion_tokens=250,
                temperature=0.2
            ).choices[0].message.content.strip()

            print(f"[FC] Reflection (попытка {attempt+1}): {reflection[:160]}...")

            if "НУЖЕН_ПОВТОРНЫЙ_ПОИСК" not in reflection.upper():
                print(f"[FC] ✅ Источники хорошие на попытке {attempt+1}")
                break

            # Нужен повтор
            messages.append({
                "role": "system",
                "content": "Источники были недостаточно качественными. Сделай более точный web_search."
            })
            print(f"[FC] 🔄 Повторный поиск (попытка {attempt+2})")
            continue

        break

    # === ФИНАЛЬНЫЙ ОТВЕТ (самое важное) ===
    messages.append({
        "role": "system",
        "content": "Теперь дай полный, естественный и точный ответ пользователю на его вопрос. "
                   "Не упоминай оценку источников, reflection, попытки или техническую информацию. "
                   "Отвечай как обычный полезный ассистент на русском языке."
    })

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
