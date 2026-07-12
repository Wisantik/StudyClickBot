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

def generate_video(prompt: str, timeout: int = 600, interval: int = 10) -> str:
    """
    Генерирует видео по текстовому prompt, перебирая модели LaoZhang до успешного результата.
    Возвращает либо URL видео, либо путь к локальному файлу (если требуется скачивание).
    Выбрасывает Exception с описанием ошибок для всех моделей при неудаче.
    """
    import requests, time, tempfile, os

    headers = {"Authorization": f"Bearer {LAOZHANG_API_KEY}"}
    errors = []

    print("🎬 Запрос генерации видео:", prompt)

    # 1) Sora 2 (OpenAI-совместимый route)
    for model in ["sora-2", "sora-2-pro"]:
        try:
            print(f"\n> Модель {model}: POST /v1/videos")
            # Посылаем POST-запрос (form-data)
            resp = requests.post(f"{BASE_URL}/v1/videos",
                                 headers=headers,
                                 data={"model": model, "prompt": prompt, "seconds": "8", "size": "1280x720"})
            print(f"HTTP {resp.status_code}: {resp.text[:100]}")
            if resp.status_code == 200:
                vid = resp.json().get("id")
                if not vid:
                    raise RuntimeError("Поля 'id' нет в ответе")
                print("Задача создана, ID =", vid)
                # Polling
                start = time.time()
                while True:
                    st = requests.get(f"{BASE_URL}/v1/videos/{vid}", headers=headers)
                    st.raise_for_status()
                    info = st.json()
                    status = info.get("status")
                    progress = info.get("progress", 0)
                    print(f"[{model}] статус={status}, прогресс={progress}%")
                    if status == "completed":
                        # Скачиваем видео
                        dl = requests.get(f"{BASE_URL}/v1/videos/{vid}/content",
                                          headers=headers, stream=True)
                        dl.raise_for_status()
                        tmp = os.path.join(tempfile.gettempdir(), f"{vid}.mp4")
                        with open(tmp, "wb") as f:
                            for chunk in dl.iter_content(chunk_size=8192):
                                f.write(chunk)
                        print("Видео сохранено в", tmp)
                        return tmp
                    if status == "failed":
                        raise RuntimeError(f"Не удалось: {info.get('error') or info}")
                    if time.time() - start > timeout:
                        raise TimeoutError("Превышено время генерации видео")
                    time.sleep(interval)
            elif resp.status_code in (401, 403, 404):
                errors.append(f"{model}: {resp.status_code} {resp.text}")
                print(f"Модель {model} недоступна ({resp.status_code}), пробуем следующую.")
                break
            elif resp.status_code in (429, 503):
                print(f"{model}: получен {resp.status_code}, пробуем заново через некоторое время")
                time.sleep(2)  # простая задержка перед повтором
                continue
            else:
                errors.append(f"{model}: {resp.status_code} {resp.text}")
                print(f"{model}: непредвиденный код {resp.status_code}, пропуск модели.")
                break
        except Exception as e:
            errors.append(f"{model}: {e}")
            print(f"Ошибка {model}: {e}")
        # Если мы не вернулись внутри try, переходим к следующей модели

    # 2) Veo 3.1 Fast/Standard (OpenAI-style)
    for model in ["veo-3.1-fast-generate-preview", "veo-3.1-generate-preview"]:
        try:
            print(f"\n> Модель {model}: POST /v1/videos")
            resp = requests.post(f"{BASE_URL}/v1/videos",
                                 headers=headers,
                                 data={"model": model, "prompt": prompt, "seconds": "8",
                                       "size": "1280x720", "resolution": "720p"})
            print(f"HTTP {resp.status_code}: {resp.text[:100]}")
            if resp.status_code == 200:
                vid = resp.json().get("id")
                if not vid:
                    raise RuntimeError("Поля 'id' нет в ответе")
                print("Задача создана, ID =", vid)
                start = time.time()
                while True:
                    st = requests.get(f"{BASE_URL}/v1/videos/{vid}", headers=headers)
                    st.raise_for_status()
                    info = st.json()
                    status = info.get("status")
                    progress = info.get("progress", 0)
                    print(f"[{model}] статус={status}, прогресс={progress}%")
                    if status == "completed":
                        dl = requests.get(f"{BASE_URL}/v1/videos/{vid}/content",
                                          headers=headers, stream=True)
                        dl.raise_for_status()
                        tmp = os.path.join(tempfile.gettempdir(), f"{vid}.mp4")
                        with open(tmp, "wb") as f:
                            for chunk in dl.iter_content(chunk_size=8192):
                                f.write(chunk)
                        print("Видео сохранено в", tmp)
                        return tmp
                    if status == "failed":
                        raise RuntimeError(f"Не удалось: {info.get('error') or info}")
                    if time.time() - start > timeout:
                        raise TimeoutError("Превышено время генерации видео")
                    time.sleep(interval)
            elif resp.status_code == 403:
                errors.append(f"{model}: {resp.status_code} {resp.text}")
                print(f"{model}: квота или разрешение отсутствует (код 403), пропускаем модель.")
                break
            elif resp.status_code in (429, 503):
                print(f"{model}: {resp.status_code}, пробуем снова")
                time.sleep(2)
                continue
            else:
                errors.append(f"{model}: {resp.status_code} {resp.text}")
                print(f"{model}: код {resp.status_code}, пропускаем.")
                break
        except Exception as e:
            errors.append(f"{model}: {e}")
            print(f"Ошибка {model}: {e}")

    # 3) Wan 2.7 (Text-to-Video через DashScope)
    try:
        model = "wan2.7-t2v"
        print(f"\n> Модель {model}: POST /wan/api/v1/services/aigc/video-generation/video-synthesis")
        payload = {
            "model": model,
            "input": {"prompt": prompt},
            "parameters": {"resolution": "720P", "duration": 5, "prompt_extend": False}
        }
        resp = requests.post(f"{BASE_URL}/wan/api/v1/services/aigc/video-generation/video-synthesis",
                             headers={**headers, "Content-Type": "application/json", "X-DashScope-Async": "enable"},
                             json=payload)
        print(f"HTTP {resp.status_code}: {resp.text[:100]}")
        if resp.status_code == 200:
            task_id = resp.json().get("output", {}).get("task_id")
            if not task_id:
                raise RuntimeError("WAN: нет task_id в ответе")
            print("Задача создана, task_id =", task_id)
            start = time.time()
            while True:
                st = requests.get(f"{BASE_URL}/v1/tasks/{task_id}", headers=headers)
                st.raise_for_status()
                info = st.json()
                status = info.get("status")
                print(f"[WAN] статус={status}")
                if status == "completed":
                    result_url = info.get("result_url")
                    if not result_url:
                        raise RuntimeError("WAN: нет result_url")
                    print("Скачиваем видео по result_url...")
                    # Для WAN не нужен заголовок авторизации
                    dl = requests.get(result_url, stream=True)
                    dl.raise_for_status()
                    tmp = os.path.join(tempfile.gettempdir(), f"{task_id}.mp4")
                    with open(tmp, "wb") as f:
                        for chunk in dl.iter_content(chunk_size=8192):
                            f.write(chunk)
                    print("Видео сохранено в", tmp)
                    return tmp
                if status == "failed":
                    raise RuntimeError(f"WAN не удалось: {info}")
                if time.time() - start > timeout:
                    raise TimeoutError("WAN: превышено время генерации")
                time.sleep(interval)
        else:
            errors.append(f"WAN: {resp.status_code} {resp.text}")
            print(f"WAN: код {resp.status_code}, не удалось создать задачу.")
    except Exception as e:
        errors.append(f"WAN: {e}")
        print(f"Ошибка WAN: {e}")

    # 4) Seedance 2.0 (Fast и Standard)
    for model in ["doubao-seedance-2-0-fast-260128", "doubao-seedance-2-0-260128"]:
        try:
            print(f"\n> Seedance: модель {model}: POST /seedance/api/v3/contents/generations/tasks")
            payload = {
                "model": model,
                "content": [{"type": "text", "text": prompt}],
                "ratio": "16:9",
                "duration": 5,
                "resolution": "720p",
                "watermark": False,
                "generate_audio": False,
                "return_last_frame": False
            }
            resp = requests.post(f"{BASE_URL}/seedance/api/v3/contents/generations/tasks",
                                 headers={**headers, "Content-Type": "application/json", "Accept": "application/json"},
                                 json=payload)
            print(f"HTTP {resp.status_code}: {resp.text[:100]}")
            if resp.status_code == 200:
                task_id = resp.json().get("id")
                if not task_id:
                    raise RuntimeError("Seedance: нет id задачи")
                print("Задача создана, ID =", task_id)
                start = time.time()
                while True:
                    st = requests.get(f"{BASE_URL}/seedance/api/v3/contents/generations/tasks/{task_id}",
                                      headers={**headers, "Accept": "application/json"})
                    st.raise_for_status()
                    info = st.json()
                    status = info.get("status")
                    print(f"[Seedance] статус={status}")
                    if status in ("succeeded", "completed"):
                        # пытаемся найти URL видео
                        vid_url = None
                        if info.get("content"):
                            vid_url = info["content"].get("video_url")
                        # возможные альт. поля
                        if not vid_url:
                            vid_url = info.get("result_url") or info.get("data", {}).get("content", {}).get("video_url")
                        if not vid_url:
                            raise RuntimeError("Seedance: URL видео не найден")
                        print("Скачиваем видео по видео-URL...")
                        dl = requests.get(vid_url, stream=True)
                        dl.raise_for_status()
                        tmp = os.path.join(tempfile.gettempdir(), f"{task_id}.mp4")
                        with open(tmp, "wb") as f:
                            for chunk in dl.iter_content(chunk_size=8192):
                                f.write(chunk)
                        print("Видео сохранено в", tmp)
                        return tmp
                    if status == "failed":
                        raise RuntimeError(f"Seedance не удалось: {info}")
                    if time.time() - start > timeout:
                        raise TimeoutError("Seedance: превышено время генерации")
                    time.sleep(interval)
            else:
                errors.append(f"Seedance {model}: {resp.status_code} {resp.text}")
                print(f"Seedance {model}: код {resp.status_code}, пропускаем модель.")
        except Exception as e:
            errors.append(f"Seedance {model}: {e}")
            print(f"Ошибка Seedance {model}: {e}")

    raise Exception("Все модели завершились ошибкой:\n" + "\n".join(errors))


# Пример использования:
video_path = generate_video("Летящий дракон в вечернем небе, эпическая атмосфера")
print("Видео получено:", video_path)

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
            "name": "generate_video",
            "description": "Создает видео по текстовому описанию",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string"
                    }
                },
                "required": ["prompt"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_video",
            "description": "Создает видео по текстовому описанию",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string"
                    }
                },
                "required": ["prompt"]
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
    
    # Основная история (без reflection)
    messages = [
        {"role": "system", "content": prompt},
        *history,
        {"role": "user", "content": query}
    ]

    today = datetime.date.today().strftime("%d.%m.%Y")
    print(f"[FC] User {user_id} | model={model} | attempt=1/{max_reflection_attempts+1}")

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
