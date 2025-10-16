import logging
import telebot
import os
import openai
import json
from typing import Final
from telebot.types import BotCommand
import time
import io
from telebot import types
import docx
import pdfplumber
import datetime
import requests
from database import *
import schedule
from yookassa import Configuration, Payment
import uuid
import tempfile
from pydub import AudioSegment
from ddgs import DDGS
import re
import base64 
load_dotenv()

# Настройка логирования и окружения
print(f"Connecting to DB: {os.getenv('DB_NAME')}, User: {os.getenv('DB_USER')}, Host: {os.getenv('DB_HOST')}")
connect_to_db()

MIN_TOKENS_THRESHOLD: Final = 5000
FREE_DAILY_TOKENS: Final = 10000
PLAN_NAMES = {
    "free": "Бесплатный",
    "plus_trial": "Пробная подписка Plus (3 дня)",
    "plus_month": "Подписка Plus (месяц)"
}

ASSISTANT_DESCRIPTIONS = {
    "universal_expert": "отвечает на любые вопросы.",
    "fintech": "советы по онлайн-банкам, платежам, переводам, приложениям для денег и инвестиций.",
    "personal_finance": "как планировать бюджет, копить и экономить деньги.",
    "investments": "фондовый рынок, недвижимость, валюты. С чего начать и как выбрать.",
    "business_marketing": "как запустить бизнес, привлечь клиентов и увеличить продажи.",
    "cybersecurity": "защита данных, телефонов и аккаунтов от взломов и мошенников.",
    "comm_skills": "как разговаривать, договариваться и избегать конфликтов.",
    "legal_advisor": "помощь в бытовых и деловых правовых вопросах, разбор договоров, защита прав потребителей.",
    "psychology_selfdev": "управление стрессом, повышение уверенности, мотивация и личностный рост."
}

logger = telebot.logger
telebot.logger.setLevel(logging.INFO)
pay_token = os.getenv('PAY_TOKEN')
bot = telebot.TeleBot(os.getenv('BOT_TOKEN'), threaded=False)
openai.api_key = os.getenv('OPENAI_API_KEY')

# Настройка ЮKassa
Configuration.account_id = os.getenv("YOOKASSA_SHOP_ID")
Configuration.secret_key = os.getenv("YOOKASSA_SECRET_KEY")

print(f"[DEBUG] ShopID: {Configuration.account_id}")
print(f"[DEBUG] YOOKASSA_SECRET_KEY: {os.getenv('YOOKASSA_SECRET_KEY')}")

# ======== WEB SEARCH (DDGS) ========
import json
from textwrap import shorten

def _call_search_api(search_query):
    """Выполняет поиск через DDGS и выводит красиво отформатированные принты."""
    banner = "="*60
    print(f"\n{banner}\n[DDGS SEARCH] Запрос -> {search_query}\n{banner}")
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(search_query, region="ru-ru", safesearch="moderate", max_results=None))

        print(f"[DDGS] Всего результатов получено сырой выдачей: {len(results)}")

        formatted_results = []
        filtered_out = []
        for i, result in enumerate(results, start=1):
            title = result.get('title')
            href = result.get('href')
            body = result.get('body', '') or ""
            reason = None
            if not title:
                reason = "no title"
            elif not href:
                reason = "no href"
            elif href.endswith("wiktionary.org/wiki/"):
                reason = "wiktionary filter"
            if reason:
                filtered_out.append((i, href, reason))
                continue

            formatted = {
                'title': title,
                'snippet': body,
                'link': href
            }
            formatted_results.append(formatted)

        print(f"[DDGS] После фильтрации: {len(formatted_results)} результатов (отфильтровано: {len(filtered_out)})")

        # печатаем краткий список найденных ссылок
        if formatted_results:
            print("\n[DDGS] Список найденных ссылок (index, title, link, snippet...):")
            max_to_show = 5
            for idx, r in enumerate(formatted_results[:max_to_show], start=1):
                snippet_clean = r['snippet'].replace('\n', ' ')
                print(f" {idx:>2}. {shorten(r['title'], 100)}")
                print(f"      → {r['link']}")
                print(f"      snip: {shorten(snippet_clean, 180)}\n")
            if len(formatted_results) > max_to_show:
                print(f"  ... и ещё {len(formatted_results) - max_to_show} результатов скрыто.\n")
        else:
            print("[DDGS] Нет отфильтрованных результатов для показа.")


        if filtered_out:
            print("[DDGS] Отфильтрованные элементы:")
            for idx, href, why in filtered_out[:10]:
                print(f"  - raw#{idx}: {href} (reason: {why})")
            if len(filtered_out) > 10:
                print(f"  ...и ещё {len(filtered_out)-10} элементов отфильтровано.")

        print(f"{banner}\n")
        return formatted_results

    except Exception as e:
        print(f"[ERROR][DDGS] Ошибка при выполнении веб-поиска: {e}")
        return []

from bs4 import BeautifulSoup

def _fetch_page_content(url: str) -> str:
    """Скачивает и очищает контент страницы."""
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        html = requests.get(url, headers=headers, timeout=10).text
        soup = BeautifulSoup(html, "html.parser")
        # Извлекаем только текст из параграфов
        text = ' '.join(p.get_text() for p in soup.find_all('p'))
        return text[:4000]  # ограничение по длине
    except Exception as e:
        print(f"[ERROR] Ошибка при загрузке {url}: {e}")
        return ""


def _perform_web_search(user_id: int, query: str, assistant_key: str) -> str:
    """
    Расширенный веб-поиск: печатает подробности запроса, выбранные топ-3 ссылки,
    статусы загрузки страниц и формирует контекст для ИИ.
    """
    banner = "-"*60
    print(f"\n{banner}\n[WEB SEARCH] user_id={user_id} assistant={assistant_key}")
    cleaned_query = re.sub(
        r'^(привет|здравствуй|как дела|найди|найди мне)\s+',
        '', query, flags=re.IGNORECASE
    ).strip()
    search_query = f"{cleaned_query} lang:ru"
    print(f"[WEB SEARCH] Отправляем в DDGS: \"{search_query}\"")
    search_results = _call_search_api(search_query)
    if not search_results:
        print("[WEB SEARCH] Ничего не найдено по запросу.")
        return "🔍 Не удалось найти актуальные результаты по вашему запросу."

    # Пытаемся fetch до 3 успешных, из всех результатов (до 10 attempts max для скорости)
    page_texts = []
    successful_links = []
    max_success = 3
    max_attempts = 10  # лимит на attempts
    for i, r in enumerate(search_results[:max_attempts], start=1):
        url = r['link']
        print(f"[FETCH] #{i} Загружаем {url} ...")
        text = _fetch_page_content(url)
        if text:
            print(f"[FETCH] #{i} OK: извлечено {len(text)} символов (усечено до 4000 при добавлении в контекст)")
            page_texts.append(f"Источник: {r['title']} ({r['link']})\n{text}\n")
            successful_links.append(r)
            if len(page_texts) >= max_success:
                break
        else:
            print(f"[FETCH] #{i} FAILED: не удалось извлечь текст или пустой ответ")

    if not page_texts:
        print("[WEB SEARCH] Ошибка: не удалось получить текст ни с одного из топовых URL.")
        return "🔍 Нашлись ссылки, но не удалось загрузить содержимое страниц."

    combined_context = "\n\n".join(page_texts)
    # Показываем маленькую выдержку из контекста (без утечки всего текста)
    sample = shorten(combined_context.replace("\n", " "), 400)
    print(f"      snip: {shorten(sample, 180)}\n")

    # Загружаем промпт ассистента
    config = load_assistants_config()
    assistant_settings = config["assistants"].get(assistant_key, {})
    assistant_prompt = assistant_settings.get("prompt", "Вы просто бот.")
    print(f"[WEB SEARCH] Промпт ассистента загружен ({len(assistant_prompt)} символов)")

    full_prompt = f"""{assistant_prompt}

Пользователь задал вопрос:
"{query}"

Я нашёл следующую информацию в интернете:

{combined_context}

Используя эти источники, дай развёрнутый, связный и точный ответ.
Если источники противоречат — укажи это.
"""
    print("[WEB SEARCH] Генерируем ответ через OpenAI (промпт сформирован).")
    try:
        chat_completion = openai.ChatCompletion.create(
            model="gpt-5-mini-2025-08-07",
            messages=[{"role": "system", "content": full_prompt}]
        )
        final_answer = chat_completion.choices[0].message.content
        print("[WEB SEARCH] Ответ от OpenAI получен.")
    except Exception as e:
        print(f"[ERROR][OpenAI] Ошибка при генерации: {e}")
        final_answer = "Произошла ошибка при генерации ответа."

    sources_block = "\n\n📚 *Источники:*\n" + "\n".join(
        [f"🔗 [{r['title']}]({r['link']})" for r in successful_links]
    )

    print(f"{banner}\n[WEB SEARCH] Завершено для user_id={user_id}\n{banner}\n")
    return (final_answer, sources_block)




def needs_web_search(message: str) -> bool:
    keywords = ["найди", "что сейчас", "новости", "поиск", "в интернете", "актуально"]
    return any(kw in message.lower() for kw in keywords)

class ExceptionHandler:
    def handle(self, exception):
        if isinstance(exception, telebot.apihelper.ApiTelegramException):
            if exception.error_code == 403:
                try:
                    error_text = str(exception)
                    import re
                    match = re.search(r'chat_id=(\d+)', error_text)
                    if match:
                        user_id = match.group(1)
                        conn = connect_to_db()
                        cur = conn.cursor()
                        cur.execute("SELECT username FROM users WHERE user_id = %s", (user_id,))
                        result = cur.fetchone()
                        username = result[0] if result else "Неизвестный пользователь"
                        cur.close()
                        conn.close()
                        print(f"Пользователь заблокировал бота. ID: {user_id}, Username: {username}")
                    else:
                        print(f"Пользователь заблокировал бота. Не удалось определить ID. Ошибка: {error_text}")
                except Exception as e:
                    print(f"Ошибка при определении пользователя: {e}")
                    print(f"Исходная ошибка: {exception}")
                return True
        return False

bot.exception_handler = ExceptionHandler()

def create_command_logs_table():
    conn = connect_to_db()
    with conn.cursor() as cursor:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS command_logs (
                log_id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                command VARCHAR(255) NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.commit()
    conn.close()

# ---------- Нормализация команд ----------
ASSISTANT_COMMANDS = {
    "Консультант универсальный": "🤖 Ассистент: Универсальный",
    "Консультант по финтеху и цифровым финансам": "🤖 Ассистент: Финтех и цифровые финансы",
    "Консультант по личным финансам": "🤖 Ассистент: Личные финансы",
    "Консультант по инвестициям": "🤖 Ассистент: Инвестиции",
    "Консультант по бизнесу и маркетингу": "🤖 Ассистент: Бизнес и маркетинг",
    "Консультант по кибербезопасности": "🤖 Ассистент: Кибербезопасность",
    "Консультант по навыкам общения": "🤖 Ассистент: Навыки общения",
    "Юридический консультант": "🤖 Ассистент: Юридический",
    "Консультант по психологии и саморазвитию": "🤖 Ассистент: Психология и саморазвитие",
}

def normalize_command(command: str) -> str:
    """
    Возвращает человекочитаемое название команды или None (если команду нужно игнорировать).
    Нормализует англ. и русские варианты, select_assistant_* и др.
    """
    if not command or not isinstance(command, str):
        return None

    cmd = command.strip()

    # Прямой маппинг (англ/рус/варианты)
    mapping = {
        "start": "start",
        "profile": "👤 Мой профиль",
        "Мой профиль": "👤 Мой профиль",
        "👤 Мой профиль": "👤 Мой профиль",
        "back_to_profile": "⬅️ Назад к профилю",
        "back": "⬅️ Назад к профилю",
        "Назад": "⬅️ Назад к профилю",
        "statsadmin12": "📊 Статистика (админ)",
        "check_subscription": '✅ Нажатие "Я подписался"',
        "pay": "💳 Подписка",
        "subscription": "💳 Подписка",
        "buy_subscription": "💳 Купить подписку",
        "cancel_subscription": "❌ Отмена подписки",
        "cancel": "❌ Отмена подписки",
        "open_subscription_menu": "💳 Открытие меню подписки",
        "show_pay_menu": "💳 Открытие меню подписки",
        "search_on": "🔍 Включить веб-поиск",
        "search_off": "🔍 Выключить веб-поиск",
        "search_denied_no_subscription": "🚫 Попытка веб-поиска без подписки",
        "toggle_web_on": "🔍 Включить веб-поиск",
        "toggle_web_off": "🔍 Выключить веб-поиск",
        "support": "📞 Поддержка",
        "show_support": "📞 Поддержка (из профиля)",
        "clear_history": "🗑 Очистить историю чата",
        "new": "🗑 Очистить историю чата",
        "language": "🌐 Выбрать язык",
        "assistants": "🤖 Ассистенты",
        "Ассистенты": "🤖 Ассистенты",
        "assistants_from_profile": "🤖 Ассистенты (из профиля)",
        "show_assistants": "🤖 Ассистенты (из профиля)",
        "experts": "👨‍💼 Эксперты",
        "Эксперты": "👨‍💼 Эксперты",
        "experts_from_profile": "👨‍💼 Эксперты (из профиля)",
        # "referral": "🔗 Реферальная ссылка",
        "search": None,  # избегаем логирования "search" как мусор
        "universal": "🤖 Ассистент: Универсальный"  # Добавлено для /universal
    }

    # при прямом совпадении
    if cmd in mapping:
        return mapping[cmd]

    # lang_xx -> язык (нормируем в один пункт)
    if cmd.startswith("lang") or cmd.startswith("language_") or cmd.startswith("lang_"):
        return "🌐 Выбрать язык"

    # select_assistant_<id> или select_assistant_<readable name>
    if cmd.startswith("select_assistant_") or cmd.startswith("selectassistant_"):
        aid = cmd.replace("select_assistant_", "").replace("selectassistant_", "")
        # попробуем взять красивое имя из конфигурации
        try:
            cfg = load_assistants_config()
            assistants = cfg.get("assistants", {}) if isinstance(cfg, dict) else {}
            if aid in assistants:
                return f"🤖 Ассистент: {assistants[aid].get('name')}"
            # иногда id может быть 'personal_finance' или 'Fintech Consultant' — ищем по вхождению
            for k, v in assistants.items():
                if aid.lower() in k.lower() or aid.lower() in (v.get("name","").lower()):
                    return f"🤖 Ассистент: {v.get('name')}"
        except Exception:
            pass
        # если не нашли — подставляем тот текст, что в callback (человеческий)
        human = aid.replace("_", " ").strip()
        return f"🤖 Ассистент: {human.capitalize()}" if human else f"🤖 Ассистент: {aid}"

    # формат assistant:xyz (если где-то осталось)
    if cmd.startswith("assistant:"):
        aid = cmd.split(":", 1)[1]
        try:
            cfg = load_assistants_config()
            assistants = cfg.get("assistants", {}) if isinstance(cfg, dict) else {}
            if aid in assistants:
                return f"🤖 Ассистент: {assistants[aid].get('name')}"
        except Exception:
            pass
        return f"🤖 Ассистент: {aid}"

    # expert callbacks
    if cmd.startswith("expert_") or cmd.startswith("expert:"):
        # достаем id (число) если есть
        parts = cmd.replace("expert:", "expert_").split("_")
        for p in parts:
            if p.isdigit():
                return f"👨‍💼 Эксперт #{p}"
        return "👨‍💼 Эксперт"

    # если команда — уже человекочитаемая русская строка (с эмодзи или кириллицей) — сохраняем
    if any(ch.isalpha() for ch in cmd) and len(cmd) <= 200:
        # нормализуем пробелы
        return " ".join(cmd.split())

    # всё остальное — игнорируем
    return None

@bot.message_handler(commands=['universal'])
def set_universal_command(message):
    user_id = message.from_user.id
    assistant_id = 'universal_expert'
    set_user_assistant(user_id, assistant_id)

    # Сброс только для универсального
    clear_chat_history_for_user(user_id, message.chat.id)

    print(f"[INFO] Универсальный ассистент установлен для {user_id} через /universal с сбросом истории")
    config = load_assistants_config()
    assistant_info = config["assistants"][assistant_id]
    name = assistant_info.get("name", "Без названия")
    description = ASSISTANT_DESCRIPTIONS.get(assistant_id, "Описание отсутствует.")
    text = (
        f"✅ Вы выбрали: <b>{name}</b>\n\n"
        f"📌 Описание:\n{description}"
    )
    bot.reply_to(message, text, parse_mode="HTML", reply_markup=create_main_menu())


# ---------- Логирование команды (вставляет нормализованное значение) ----------
def log_command(user_id: int, command: str):
    """
    Нормализуем команду и пишем в command_logs.
    Игнорируем None (мусор).
    """
    try:
        normalized = normalize_command(command)
        if not normalized:
            return  # игнорируем мусор или технические варианты

        conn = connect_to_db()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO command_logs (user_id, command) VALUES (%s, %s)",
                    (user_id, normalized)
                )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        # не даём падать боту из-за логов
        print(f"[ERROR] log_command error: {e} (orig='{command}')")

def get_command_stats(period):
    conn = connect_to_db()
    with conn.cursor() as cursor:
        if period == 'week':
            cursor.execute("""
                SELECT command, COUNT(*) as count
                FROM command_logs
                WHERE timestamp >= CURRENT_DATE - INTERVAL '7 days'
                GROUP BY command
                ORDER BY count DESC;
            """)
        elif period == 'month':
            cursor.execute("""
                SELECT command, COUNT(*) as count
                FROM command_logs
                WHERE timestamp >= CURRENT_DATE - INTERVAL '30 days'
                GROUP BY command
                ORDER BY count DESC;
            """)
        elif period == 'year':
            cursor.execute("""
                SELECT command, COUNT(*) as count
                FROM command_logs
                WHERE timestamp >= CURRENT_DATE - INTERVAL '1 year'
                GROUP BY command
                ORDER BY count DESC;
            """)
        stats = cursor.fetchall()
    conn.close()
    return stats

def create_main_menu() -> types.ReplyKeyboardMarkup:
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    keyboard.add(
        types.KeyboardButton("👤 Мой профиль"),
        types.KeyboardButton("🌐 Выбрать язык"),
        types.KeyboardButton("🤖 Ассистенты"),
        types.KeyboardButton("👨‍💼 Эксперты"),
        types.KeyboardButton("🔍 Интернет поиск"),
        types.KeyboardButton("💳 Подписка"),
        types.KeyboardButton("❌ Отмена подписки"),
        types.KeyboardButton("🗑 Очистить историю чата"),
        types.KeyboardButton("📞 Поддержка"),
        # types.KeyboardButton("🔗 Реферальная ссылка"),
    )
    return keyboard

def setup_bot_commands():
    commands = [
        BotCommand("profile", "👤 Мой профиль"),
        BotCommand("language", "🌐 Выбрать язык"),
        BotCommand("assistants", "🤖 Ассистенты"),
        BotCommand("experts", "👨‍💼 Эксперты"),
        BotCommand("search", "🔍 Включить/выключить интернет-поиск"),
        BotCommand("pay", "💳 Подписка"),
        BotCommand("cancel_subscription", "❌ Отмена подписки"),
        BotCommand("new", "🗑 Очистить историю чата"),
        BotCommand("support", "📞 Поддержка"),
        # BotCommand("referral", "🔗 Реферальная ссылка"),
        BotCommand("universal", "🤖 Универсальный ассистент"),
    ]
    try:
        bot.set_my_commands(commands)
        print("Команды бота успешно настроены")
    except Exception as e:
        print(f"Ошибка при настройке команд: {e}")

def create_price_menu(user_data) -> types.InlineKeyboardMarkup:
    buttons = []
    if not user_data.get('trial_used'):
        buttons.append([
            types.InlineKeyboardButton(
                text="Пробная (3 дня за 99₽)",
                callback_data="buy_trial"
            )
        ])
    buttons.append([
        types.InlineKeyboardButton(
            text="Месячная - 399₽",
            callback_data="buy_month"
        )
    ])
    buttons.append([
        types.InlineKeyboardButton(
            text="⬅️ Назад", callback_data="back_to_profile"
        )
    ])
    return types.InlineKeyboardMarkup(keyboard=buttons)

def create_subscription_required_keyboard():
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton(
        text="Купить подписку",
        callback_data="show_pay_menu"
    ))
    return keyboard

def create_profile_menu() -> types.InlineKeyboardMarkup:
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        types.InlineKeyboardButton(text="🤖 Ассистенты", callback_data="show_assistants"),
        types.InlineKeyboardButton(text="👨‍💼 Эксперты", callback_data="show_experts")
    )
    keyboard.add(
        types.InlineKeyboardButton(text="💳 Подписка", callback_data="show_pay_menu"),
        types.InlineKeyboardButton(text="❌ Отписка", callback_data="cancel_subscription")
    )
    keyboard.add(
        types.InlineKeyboardButton(text="📞 Поддержка", callback_data="show_support")
    )
    return keyboard

def create_assistants_menu() -> types.InlineKeyboardMarkup:
    config = load_assistants_config()
    assistants = config.get("assistants", {})
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    
    # Сначала добавляем универсального ассистента, если он есть
    if 'universal_expert' in assistants:
        assistant_info = assistants['universal_expert']
        keyboard.add(
            types.InlineKeyboardButton(
                text=assistant_info['name'],
                callback_data="select_assistant_universal_expert"
            )
        )
    
    # Затем добавляем остальные ассистенты
    for assistant_id, assistant_info in assistants.items():
        if assistant_id != 'universal_expert':  # Пропускаем универсального
            callback_data = f"select_assistant_{assistant_id}"
            keyboard.add(
                types.InlineKeyboardButton(
                    text=assistant_info['name'],
                    callback_data=callback_data
                )
            )
    
    keyboard.add(
        types.InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_profile")
    )
    return keyboard

def create_experts_menu() -> types.InlineKeyboardMarkup:
    conn = connect_to_db()
    experts = get_all_experts(conn)
    conn.close()
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    for expert in experts:
        expert_id, name, specialization, *_ = expert
        keyboard.add(types.InlineKeyboardButton(
            text=f"{name} - {specialization}",
            callback_data=f"expert_{expert_id}"
        ))
    keyboard.add(
        types.InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_profile")
    )
    return keyboard

load_assistants_config()

REQUIRED_CHANNEL_ID = "@GuidingStarVlog"
SUBSCRIPTION_CHECK_CACHE = {}

def check_user_subscription(user_id):
    try:
        if user_id in SUBSCRIPTION_CHECK_CACHE:
            last_check, is_subscribed = SUBSCRIPTION_CHECK_CACHE[user_id]
            if (datetime.datetime.now() - last_check).total_seconds() < 3600:
                return is_subscribed
        chat_member = bot.get_chat_member(chat_id=REQUIRED_CHANNEL_ID, user_id=user_id)
        status = chat_member.status
        is_subscribed = status in ['member', 'administrator', 'creator']
        SUBSCRIPTION_CHECK_CACHE[user_id] = (datetime.datetime.now(), is_subscribed)
        return is_subscribed
    except Exception as e:
        print(f"Ошибка проверки подписки для {user_id}: {e}")
        return True

def create_subscription_keyboard():
    keyboard = types.InlineKeyboardMarkup()
    url_button = types.InlineKeyboardButton(text="Подписаться на канал", url="https://t.me/GuidingStarVlog")
    check_button = types.InlineKeyboardButton(text="Я подписался", callback_data="check_subscription")
    keyboard.add(url_button)
    keyboard.add(check_button)
    return keyboard

@bot.callback_query_handler(func=lambda call: call.data == "check_subscription")
def subscription_check_callback(call):
    user_id = call.from_user.id
    log_command(user_id, "check_subscription")
    if user_id in SUBSCRIPTION_CHECK_CACHE:
        del SUBSCRIPTION_CHECK_CACHE[user_id]
    if check_user_subscription(user_id):
        set_user_assistant(user_id, 'universal_expert')
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="Спасибо за подписку! Теперь вы можете использовать бота с универсальным экспертом.",
            reply_markup=None  
        )
        bot.send_message(
            call.message.chat.id,
            "Добро пожаловать обратно!", 
            reply_markup=create_main_menu()
        )
        user_data = load_user_data(user_id)
        if 'pending_query' in user_data and user_data['pending_query']:
            pending_text = user_data['pending_query']
            del user_data['pending_query']
            save_user_data(user_data)
            class FakeMessage:
                def __init__(self, text, from_user, chat):
                    self.text = text
                    self.from_user = from_user
                    self.chat = chat
            fake_message = FakeMessage(pending_text, call.from_user, call.message.chat)
            message_queues[user_id].append(fake_message)
            process_user_queue(user_id, call.message.chat.id)
    else:
        bot.answer_callback_query(
            call.id,
            "Вы всё ещё не подписаны. Подпишитесь для использования бота.",
            show_alert=True
        )

@bot.callback_query_handler(func=lambda call: call.data == "show_pay_menu")
def show_pay_menu_callback(call):
    log_command(call.from_user.id, "show_pay_menu")
    subscription_text = """Подписка Plus

Доступ к GPT 5 - безлимит
Чтение PDF файлов - безлимит
Чтение ссылок - безлимит
Интернет поиск - безлимит
Обработка запросов голосовыми

⚠️ Пробная подписка после истечения срока действия включает в себя автопродление на месяц: 399 рублей
Покупая, вы соглашаетесь с <a href="https://teletype.in/@st0ckholders_s/1X-lpJhx5rc">офертой</a>
Отменить можно в любое время после оплаты
По всем вопросам пишите сюда - <a href="https://t.me/mon_tti1">t.me/mon_tti1</a>"""
    
    user_data = load_user_data(call.from_user.id)
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=subscription_text,
        parse_mode="HTML",
        reply_markup=create_price_menu(user_data)
    )

from telebot.types import ReplyKeyboardRemove

@bot.message_handler(commands=['assistants'])
@bot.message_handler(func=lambda message: message.text == "🤖 Ассистенты")
def assistants_button_handler(message):
    log_command(message.from_user.id, "assistants")
    try:
        bot.send_message(
            chat_id=message.chat.id,
            text="Выберите ассистента:",
            reply_markup=create_assistants_menu(),  # Inline-кнопки ассистентов
            disable_notification=True,  # Отключаем уведомления
            disable_web_page_preview=True  # Отключаем предварительный просмотр
        )
        # Удаляем кастомную клавиатуру отдельным сообщением и сразу его удаляем
        msg = bot.send_message(
            chat_id=message.chat.id,
            text=".",
            reply_markup=ReplyKeyboardRemove(),  # Убираем кастомную клавиатуру
            disable_notification=True
        )
        bot.delete_message(
            chat_id=message.chat.id,
            message_id=msg.message_id
        )
    except telebot.apihelper.ApiTelegramException as e:
        print(f"[ERROR] Ошибка в assistants_button_handler: {e}")
        bot.send_message(
            chat_id=message.chat.id,
            text="Произошла ошибка. Попробуйте снова.",
            reply_markup=create_main_menu()
        )
# === Обработчик выбора ассистента ===
@bot.callback_query_handler(func=lambda call: call.data.startswith("select_assistant_"))
def assistant_callback_handler(call):
    try:
        # Более строгий guard: Проверяем тип call и его ключевые атрибуты
        if not isinstance(call, types.CallbackQuery):
            print(f"[ERROR] Некорректный тип call в assistant_callback_handler: {type(call)}")
            return  # Выходим сразу
        
        if not hasattr(call, 'data') or not call.data:
            print(f"[ERROR] Отсутствует data в call")
            return
        
        if not hasattr(call, 'from_user'):
            print(f"[ERROR] Отсутствует from_user в call")
            return
        
        # Проверяем, что from_user — это User объект, а не int или другой тип
        if not isinstance(call.from_user, types.User):
            print(f"[ERROR] call.from_user не User: тип {type(call.from_user)}")
            if hasattr(call, 'id'):
                bot.answer_callback_query(call.id, "Ошибка обработки. Попробуйте снова.")
            return
        
        if not hasattr(call.from_user, 'id'):
            print(f"[ERROR] Отсутствует id в call.from_user")
            if hasattr(call, 'id'):
                bot.answer_callback_query(call.id, "Ошибка обработки. Попробуйте снова.")
            return
        
        assistant_id = call.data.replace("select_assistant_", "")
        config = load_assistants_config()

        if assistant_id not in config["assistants"]:
            bot.answer_callback_query(call.id, "Ассистент не найден")
            return

        user_id = call.from_user.id  # Теперь безопасно

        # Логируем ассистента в нормализованном виде
        log_command(user_id, f"assistant:{assistant_id}")

        set_user_assistant(user_id, assistant_id)
        
        # Сброс истории только для универсального ассистента
        if assistant_id == 'universal_expert':
            clear_chat_history_for_user(call.from_user.id, getattr(call.message, "chat", {}).id if call.message else None)
            print(f"[INFO] Универсальный ассистент установлен для {user_id} с сбросом истории")
        
        assistant_info = config["assistants"][assistant_id]
        name = assistant_info.get("name", "Без названия")
        description = ASSISTANT_DESCRIPTIONS.get(assistant_id, "Описание отсутствует.")

        text = (
            f"✅ Вы выбрали: <b>{name}</b>\n\n"
            f"📌 Описание:\n{description}"
        )

        # Guard для call.message перед edit
        if not hasattr(call, 'message') or not call.message:
            print(f"[ERROR] Отсутствует message в call")
            bot.answer_callback_query(call.id, "Ошибка обновления сообщения.")
            return
        
        if not hasattr(call.message, 'chat') or not hasattr(call.message, 'message_id'):
            print(f"[ERROR] Отсутствуют chat/message_id в call.message")
            bot.answer_callback_query(call.id, "Ошибка обновления сообщения.")
            return

        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=None
        )
        
        # Безопасный answer_callback_query в конце
        bot.answer_callback_query(call.id, f"Ассистент {name} выбран")
        
    except Exception as e:
        print(f"[ERROR] Общая ошибка в assistant_callback_handler: {e}, call тип: {type(call)}, from_user тип: {type(getattr(call, 'from_user', None))}")
        try:
            if hasattr(call, 'id') and call.id:
                bot.answer_callback_query(call.id, "Ошибка. Попробуйте перезапустить выбор.")
        except Exception as answer_e:
            print(f"[ERROR] Ошибка в answer_callback_query: {answer_e}")

@bot.message_handler(commands=['experts'])
@bot.message_handler(func=lambda message: message.text == "👨‍💼 Эксперты")
def experts_button_handler(message):
    log_command(message.from_user.id, "experts")
    bot.send_message(
        message.chat.id,
        "Выберите эксперта:",
        reply_markup=create_experts_menu()
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("expert_"))
def expert_callback_handler(call):
    print(f"[DEBUG] Expert callback data: {call.data}")
    try:
        expert_id = int(call.data.split("_")[1])

        # Логируем эксперта
        log_command(call.from_user.id, f"expert:{expert_id}")

        conn = connect_to_db()
        expert = get_expert_by_id(conn, expert_id)
        conn.close()

        if not expert:
            bot.answer_callback_query(call.id, "Эксперт не найден")
            return

        expert_id, name, specialization, description, photo_url, telegram_username, contact_info, is_available = expert

        # 🔹 Кнопки под описанием эксперта
        keyboard = types.InlineKeyboardMarkup()
        if telegram_username:
            keyboard.add(types.InlineKeyboardButton(
                text="Написать эксперту",
                url=f"https://t.me/{telegram_username.replace('@', '')}"
            ))

        # 🔹 Кнопка назад теперь всегда возвращает к профилю
        keyboard.add(
            types.InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_profile")
        )

        message_text = f"<b>{name}</b>\n<i>{specialization}</i>\n\n{description}\n\n"
        if contact_info:
            message_text += f"<b>Контактная информация:</b>\n{contact_info}"

        # 🔹 Отображаем фото, если есть
        if photo_url:
            try:
                bot.edit_message_media(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    media=types.InputMediaPhoto(
                        media=photo_url,
                        caption=message_text,
                        parse_mode="HTML"
                    ),
                    reply_markup=keyboard
                )
            except Exception as e:
                print(f"Ошибка редактирования сообщения с фото эксперта: {e}")
                bot.edit_message_text(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    text=message_text,
                    parse_mode="HTML",
                    reply_markup=keyboard
                )
        else:
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=message_text,
                parse_mode="HTML",
                reply_markup=keyboard
            )

        bot.answer_callback_query(call.id)

    except ValueError:
        print(f"[ERROR] Неверный формат expert_id в callback: {call.data}")
        bot.answer_callback_query(call.id, "Ошибка при выборе эксперта")


@bot.message_handler(commands=['universal'])
@bot.message_handler(func=lambda message: message.text == "🌍 Универсальный ассистент")
def universal_assistant_handler(message):
    try:
        # Более строгий guard: Проверяем тип message и его ключевые атрибуты
        if not isinstance(message, types.Message):
            print(f"[ERROR] Некорректный тип message в universal_assistant_handler: {type(message)}")
            return  # Выходим сразу
        
        if not hasattr(message, 'from_user'):
            print(f"[ERROR] Отсутствует from_user в message")
            return
        
        # Проверяем, что from_user — это User объект, а не int или другой тип
        if not isinstance(message.from_user, types.User):
            print(f"[ERROR] message.from_user не User: тип {type(message.from_user)}")
            return
        
        if not hasattr(message.from_user, 'id'):
            print(f"[ERROR] Отсутствует id в message.from_user")
            return
        
        if not hasattr(message, 'chat'):
            print(f"[ERROR] Отсутствует chat в message")
            return
        
        user_id = message.from_user.id  # Теперь безопасно
        assistant_id = 'universal_expert'
        
        # Логируем (если log_command используется)
        log_command(user_id, "universal")
        
        # Устанавливаем через общую функцию (БД + Redis)
        set_user_assistant(user_id, assistant_id)
        
        # Сброс истории только для универсального
        clear_chat_history_for_user(user_id, message.chat.id)

        print(f"[INFO] Универсальный ассистент установлен для {user_id} через /universal с сбросом истории")
        
        config = load_assistants_config()
        assistant_info = config["assistants"].get(assistant_id, {})
        name = assistant_info.get("name", "Универсальный эксперт")
        description = ASSISTANT_DESCRIPTIONS.get(assistant_id, "Отвечает на любые вопросы.")
        
        text = (
            f"✅ Вы выбрали: <b>{name}</b>\n\n"
            f"📌 Описание:\n{description}"
        )
        
        bot.reply_to(message, text, parse_mode="HTML", reply_markup=create_main_menu())
        
    except Exception as e:
        print(f"[ERROR] Общая ошибка в universal_assistant_handler: {e}, message тип: {type(message)}, from_user тип: {type(getattr(message, 'from_user', None))}")
        try:
            bot.reply_to(message, "Ошибка. Попробуйте позже.", reply_markup=create_main_menu())
        except Exception as reply_e:
            print(f"[ERROR] Ошибка в reply_to: {reply_e}")

@bot.message_handler(func=lambda message: message.text == "Назад")
def back_button_handler(message):
    log_command(message.from_user.id, "Назад")
    bot.send_message(
        message.chat.id,
        "Вы вернулись в главное меню",
        reply_markup=create_main_menu()
    )

@bot.message_handler(func=lambda message: message.text == "👤 Мой профиль")
def profile_button_handler(message):
    log_command(message.from_user.id, "Мой профиль")
    show_profile(message)

@bot.message_handler(commands=["pay"])
@bot.message_handler(func=lambda message: message.text == "💳 Подписка")
def get_pay(message):
    log_command(message.from_user.id, "pay")
    subscription_text = """Подписка Plus

Доступ к GPT 5 - безлимит
Чтение PDF файлов - безлимит
Чтение ссылок - безлимит
Интернет поиск - безлимит
Обработка запросов голосовыми

⚠️ Пробная подписка после истечения срока действия включает в себя автопродление на месяц: 399 рублей
Покупая, вы соглашаетесь с <a href="https://teletype.in/@st0ckholders_s/1X-lpJhx5rc">офертой</a>
Отменить можно в любое время после оплаты
По всем вопросам пишите сюда - <a href="https://t.me/mon_tti1">t.me/mon_tti1</a>"""
    
    user_data = load_user_data(message.from_user.id)
    bot.send_message(
        message.chat.id,
        subscription_text,
        parse_mode="HTML",
        reply_markup=create_price_menu(user_data)
    )

# ... (остальной код остаётся без изменений)
import threading

def monitor_payment(user_id: int, payment_id: str, max_checks: int = 4, interval: int = 180):
    def run():
        for attempt in range(max_checks):
            try:
                payment = Payment.find_one(payment_id)
                print(f"[DEBUG] Проверка платежа {payment_id} для {user_id}: status={payment.status}")

                if payment.status == "succeeded":
                    save_payment_method_for_user(user_id, payment.payment_method.id)
                    set_user_subscription(user_id, "plus_trial")

                    # 🔹 Отмечаем, что триал уже использован
                    user_data = load_user_data(user_id)
                    if user_data:
                        user_data['trial_used'] = True
                        save_user_data(user_data)

                    bot.send_message(
                        user_id,
                        "✅ Пробная подписка Plus активирована на 3 дня!",
                        reply_markup=create_main_menu()
                    )
                    return
                elif payment.status in ["canceled", "failed"]:
                    bot.send_message(
                        user_id,
                        "❌ Оплата не прошла. Попробуйте снова: /pay",
                        reply_markup=create_main_menu()
                    )
                    return
            except Exception as e:
                print(f"[ERROR] Ошибка проверки платежа {payment_id} для {user_id}: {e}")

            time.sleep(interval)

        bot.send_message(
            user_id,
            "⚠️ Мы не получили подтверждение оплаты в течение 12 минут. "
            "Если деньги списались, напишите в поддержку: https://t.me/mon_tti1",
            reply_markup=create_main_menu()
        )

    threading.Thread(target=run, daemon=True).start()



@bot.callback_query_handler(func=lambda callback: callback.data in ["buy_trial", "buy_month"])
def buy_subscription(callback):
    user_id = callback.from_user.id
    user_data = load_user_data(user_id)
    if not user_data:
        print(f"[ERROR] Пользователь user_id={user_id} не найден в базе данных")
        bot.send_message(callback.message.chat.id, "Ошибка: пользователь не найден.", reply_markup=create_main_menu())
        bot.answer_callback_query(callback.id)
        return
    try:
        if callback.data == "buy_trial":
            if user_data['trial_used']:
                print(f"[INFO] Пользователь user_id={user_id} уже использовал пробную подписку")
                bot.send_message(callback.message.chat.id, "Вы уже использовали пробную подписку.", reply_markup=create_main_menu())
                bot.answer_callback_query(callback.id)
                return
            price = "99.00"
            payment_params = {
                "amount": {"value": price, "currency": "RUB"},
                "capture": True,
                "confirmation": {
                    "type": "redirect",
                    "return_url": "https://t.me/fiinny_bot"
                },
                "save_payment_method": True,
                "description": f"Пробная подписка Plus для {user_id}",
                "receipt": {
                    "customer": {"email": "sg050@yandex.ru"},
                    "items": [{
                        "description": "Пробная подписка Plus (3 дня)",
                        "quantity": "1.00",  # Исправлено на строку
                        "amount": {"value": price, "currency": "RUB"},
                        "vat_code": 1
                    }]
                },
                "idempotency_key": str(uuid.uuid4())
            }
            print(f"[DEBUG] Создание платежа для user_id={user_id}: {payment_params}")
            payment = Payment.create(payment_params)
            print(f"[DEBUG] Платёж создан: id={payment.id}, status={payment.status}, confirmation_url={payment.confirmation.confirmation_url}")
            save_payment_id_for_user(user_id, payment.id)

            # 🔹 запускаем мониторинг только этого платежа
            monitor_payment(user_id, payment.id)

            bot.send_message(
                callback.message.chat.id,
                f"Оплатите по ссылке: {payment.confirmation.confirmation_url}",
                reply_markup=types.InlineKeyboardMarkup([[
                    types.InlineKeyboardButton("Отменить подписку", callback_data="cancel_subscription")
                ]])
            )
        elif callback.data == "buy_month":
            print(f"[DEBUG] Создание инвойса для месячной подписки: user_id={user_id}")
            bot.send_invoice(
                chat_id=callback.message.chat.id,
                title="Подписка Plus (месяц)",
                description="Месячная подписка Plus: безлимитный доступ к GPT-5, веб-поиск, обработка PDF и голосовых сообщений.",
                invoice_payload=f"month_subscription_{user_id}",
                provider_token=pay_token,
                currency="RUB",
                prices=[types.LabeledPrice(label="Подписка Plus (месяц)", amount=39900)],
                start_parameter=f"month_{user_id}",
            )
        bot.answer_callback_query(callback.id)
    except Exception as e:
        print(f"[ERROR] Ошибка при создании платежа для user_id={user_id}: {e}")
        bot.send_message(
            callback.message.chat.id,
            f"Произошла ошибка при создании платежа. Пожалуйста, попробуйте позже или обратитесь в поддержку: https://t.me/mon_tti1",
            reply_markup=create_main_menu()
        )
        bot.answer_callback_query(callback.id)

@bot.pre_checkout_query_handler(func=lambda query: True)
def pre_checkout_query_handler(pre_checkout_query):
    bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@bot.message_handler(content_types=['successful_payment'])
def successful_payment_handler(message):
    user_id = message.from_user.id
    payload = message.successful_payment.invoice_payload
    if payload.startswith("month_subscription_"):
        set_user_subscription(user_id, "plus_month")
        bot.send_message(
            message.chat.id,
            "✅ Месячная подписка Plus активирована на 30 дней!",
            reply_markup=create_main_menu()
        )
    else:
        bot.send_message(
            message.chat.id,
            "Ошибка: неизвестный тип платежа.",
            reply_markup=create_main_menu()
        )

def check_pending_payments():
    conn = connect_to_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT user_id, payment_id FROM payments WHERE status = 'pending'")
            payments = cursor.fetchall()
            print(f"[INFO] Найдено {len(payments)} pending платежей")
            for user_id, payment_id in payments:
                try:
                    payment = Payment.find_one(payment_id)
                    print(f"[INFO] Платёж {payment_id} для user_id={user_id}: status={payment.status}")
                    if payment.status == "succeeded":
                        save_payment_method_for_user(user_id, payment.payment_method.id)
                        set_user_subscription(user_id, "plus_trial")
                        bot.send_message(
                            user_id,
                            "✅ Пробная подписка Plus активирована на 3 дня!",
                            reply_markup=create_main_menu()
                        )
                        cursor.execute("UPDATE payments SET status = 'succeeded' WHERE payment_id = %s", (payment_id,))
                    elif payment.status in ["canceled", "failed"]:
                        cursor.execute("UPDATE payments SET status = %s WHERE payment_id = %s", (payment.status, payment_id))
                except Exception as e:
                    print(f"[ERROR] Ошибка проверки платежа {payment_id} для user_id={user_id}: {e}")
            conn.commit()
    except Exception as e:
        print(f"[ERROR] Ошибка при проверке платежей: {e}")
    finally:
        conn.close()

def check_auto_renewal():
    conn = connect_to_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT user_id FROM users 
                WHERE subscription_plan = 'plus_trial' 
                AND subscription_end_date <= %s
                AND auto_renewal = TRUE
            """, (datetime.datetime.now().date(),))
            users = cursor.fetchall()
            print(f"[DEBUG] Найдено {len(users)} пользователей для автопродления")
            for user in users:
                user_id = user[0]
                method_id = get_payment_method_for_user(user_id)
                if method_id:
                    try:
                        payment_params = {
                            "amount": {"value": "399.00", "currency": "RUB"},
                            "capture": True,
                            "payment_method_id": method_id,
                            "description": f"Автопродление подписки для {user_id}",
                            "receipt": {
                                "customer": {"email": "sg050@yandex.ru"},
                                "items": [{
                                    "description": "Подписка Plus (месяц)",
                                    "quantity": "1.00",
                                    "amount": {"value": "399.00", "currency": "RUB"},
                                    "vat_code": 1
                                }]
                            },
                            "idempotency_key": str(uuid.uuid4())
                        }
                        print(f"[DEBUG] Создание платежа автопродления для user_id={user_id}: {payment_params}")
                        payment = Payment.create(payment_params)
                        print(f"[DEBUG] Платёж автопродления создан: id={payment.id}, status={payment.status}")

                        if payment.status == "succeeded":
                            set_user_subscription(user_id, "plus_month")
                            bot.send_message(
                                user_id,
                                "✅ Ваша подписка продлена на месяц за 399₽!",
                                reply_markup=create_main_menu()
                            )
                        else:
                            reason = None
                            if hasattr(payment, "cancellation_details") and payment.cancellation_details:
                                reason = getattr(payment.cancellation_details, "reason", None)

                            msg = f"❌ Не удалось продлить подписку.\nСтатус: {payment.status}"
                            if reason:
                                msg += f"\nПричина: {reason}"
                            msg += "\nПожалуйста, оплатите вручную: /pay"

                            # 🔹 Уведомляем пользователя
                            bot.send_message(user_id, msg, reply_markup=create_main_menu())

                            # 🔹 Отправляем уведомление админу
                            try:
                                bot.send_message(
                                    741831495,
                                    f"⚠️ Ошибка автопродления для user_id={user_id}\n"
                                    f"Статус: {payment.status}\n"
                                    f"Причина: {reason or 'неизвестно'}"
                                )
                            except Exception as e:
                                print(f"[WARN] Не удалось уведомить админа: {e}")

                            # 🔹 Сбрасываем подписку на free
                            set_user_subscription(user_id, "free")

                    except Exception as e:
                        print(f"[ERROR] Ошибка автопродления для user_id={user_id}: {e}")
                        bot.send_message(
                            user_id,
                            f"❌ Ошибка при автопродлении: {e}. Пожалуйста, оплатите вручную: /pay",
                            reply_markup=create_main_menu()
                        )
                        try:
                            bot.send_message(
                                741831495,
                                f"❌ Exception при автопродлении user_id={user_id}\nОшибка: {e}"
                            )
                        except:
                            pass
                else:
                    print(f"[INFO] Не найден payment_method_id для user_id={user_id}")
    except Exception as e:
        print(f"[ERROR] Ошибка при проверке автопродления: {e}")
    finally:
        conn.close()


schedule.every(5).minutes.do(check_pending_payments)
schedule.every().day.at("00:00").do(check_auto_renewal)

from telebot.types import ReplyKeyboardRemove

@bot.callback_query_handler(func=lambda call: call.data in ["show_assistants", "show_experts", "show_support", "cancel_subscription", "back_to_profile"])
def profile_menu_callback_handler(call):
    log_command(call.from_user.id, call.data)
    user_id = call.from_user.id
    user_data = load_user_data(user_id)
    if not user_data:
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="Ошибка: пользователь не найден. Попробуйте перезапустить бота с /start.",
            reply_markup=create_main_menu()
        )
        bot.answer_callback_query(call.id)
        return

    if call.data == "show_assistants":
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="Выберите ассистента:",
            reply_markup=create_assistants_menu()
        )

    elif call.data == "show_experts":
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="Выберите эксперта:",
            reply_markup=create_experts_menu()
        )

    elif call.data == "show_support":
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="Напишите в поддержку: <a href='https://t.me/mon_tti1'>t.me/mon_tti1</a>",
            parse_mode="HTML",
            reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_profile")
            )
        )

    elif call.data == "cancel_subscription":
        if not user_data or user_data['subscription_plan'] == 'free':
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text="У вас нет активной подписки для отмены.",
                reply_markup=types.InlineKeyboardMarkup().add(
                    types.InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_profile")
                )
            )
        else:
            conn = connect_to_db()
            cur = conn.cursor()
            cur.execute("""
                UPDATE users 
                SET auto_renewal = FALSE
                WHERE user_id = %s
            """, (user_id,))
            conn.commit()
            cur.close()
            conn.close()
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text="Автопродление отключено. Ваша подписка останется активной.",
                reply_markup=types.InlineKeyboardMarkup().add(
                    types.InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_profile")
                )
            )

    elif call.data == "back_to_profile":
        subscription_end_date = user_data.get('subscription_end_date')
        remaining_days = None
        if user_data['subscription_plan'] != 'free' and subscription_end_date:
            today = datetime.datetime.now().date()
            remaining_days = (subscription_end_date - today).days
            if remaining_days < 0:
                remaining_days = 0

        # 🔹 Веб-поиск
        web_search_status = "включён" if user_data['web_search_enabled'] else \
            "выключен" if user_data['subscription_plan'].startswith('plus_') else \
            "недоступен (требуется подписка Plus)"

        # 🔹 Квота токенов
        if user_data['subscription_plan'] in ['plus_trial', 'plus_month']:
            quota_text = "GPT-5: безлимит ✅"
        else:
            quota_text = f"GPT-5: {user_data['daily_tokens']} символов"

        profile_text = f"""
ID: {user_id}

Ваш текущий тариф: {PLAN_NAMES.get(user_data['subscription_plan'], user_data['subscription_plan'])}
"""
        if user_data['subscription_plan'] != 'free' and remaining_days is not None:
            profile_text += f"Подписка активна ещё {remaining_days} дней\n"

        profile_text += f"""
Веб-поиск: {web_search_status}

Оставшаяся квота:
{quota_text}

🏷 Детали расходов:
💰 Общая сумма: ${user_data['total_spent']:.4f}
"""

        try:
            # 🩵 Если сообщение было фото — Telegram не даст его редактировать, отправляем новое
            if call.message.content_type == "photo":
                bot.send_message(
                    chat_id=call.message.chat.id,
                    text=profile_text,
                    reply_markup=create_profile_menu()
                )
            else:
                bot.edit_message_text(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    text=profile_text,
                    reply_markup=create_profile_menu()
                )
        except telebot.apihelper.ApiTelegramException as e:
            print(f"[ERROR] Ошибка редактирования сообщения в back_to_profile: {e}")
            bot.send_message(
                chat_id=call.message.chat.id,
                text=profile_text,
                reply_markup=create_profile_menu()
            )


    bot.answer_callback_query(call.id)
# helper — делает реальную очистку по user_id
def clear_chat_history_for_user(user_id: int, chat_id: int | None = None):
    try:
        conn = connect_to_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM chat_history WHERE chat_id = %s", (user_id,))
        conn.commit()
        cur.close()
        conn.close()
        # ставим универсального ассистента
        set_user_assistant(user_id, 'universal_expert')
        # опционально шлём уведомление в чат (если у нас есть chat_id)
        if chat_id:
            try:
                bot.send_message(chat_id, "История чата очищена! Можете начать новый диалог.", reply_markup=create_main_menu())
            except Exception as e:
                print(f"[WARN] Не удалось отправить уведомление об очистке для {user_id}: {e}")
    except Exception as e:
        print(f"[ERROR] clear_chat_history_for_user({user_id}) failed: {e}")

# обработчик сообщения теперь вызывает helper
@bot.message_handler(commands=['new'])
@bot.message_handler(func=lambda message: message.text == "🗑 Очистить историю чата")
def clear_chat_history_handler(message):
    log_command(message.from_user.id, "new")
    clear_chat_history_for_user(message.from_user.id, message.chat.id)

def create_language_menu():
    keyboard = types.InlineKeyboardMarkup(row_width=3)
    languages = [
        ("Россия", "ru", "🇷🇺"),
        ("Английский", "en", "🇬🇧"),
        ("Франция", "fr", "🇫🇷"),
        ("Германия", "de", "🇩🇪"),
        ("Турция", "tr", "🇹🇷"),
        ("Бразилия", "pt", "🇧🇷"),
        ("Мексика", "es", "🇲🇽"),
        ("Италия", "it", "🇮🇹"),
        ("Индия", "hi", "🇮🇳"),
        ("Китай", "zh", "🇨🇳"),
    ]
    for lang_name, lang_code, emoji in languages:
        keyboard.add(types.InlineKeyboardButton(
            text=f"{emoji} {lang_name}",
            callback_data=f"lang_{lang_code}"
        ))
    keyboard.add(
        types.InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_profile")
    )
    return keyboard

@bot.message_handler(commands=['language'])
@bot.message_handler(func=lambda message: message.text == "🌐 Выбрать язык")
def language_handler(message):
    log_command(message.from_user.id, "language")
    bot.send_message(
        message.chat.id,
        "Выберите язык:",
        reply_markup=create_language_menu()
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("lang_"))
def language_callback_handler(call):
    lang_code = call.data.split("_")[-1]
    log_command(call.from_user.id, f"lang_{lang_code}")
    user_data = load_user_data(call.from_user.id)
    if user_data:
        user_data['language'] = lang_code
        save_user_data(user_data)
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"Выбран язык: {lang_code.upper()}",
            reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_profile")
            )
        )
    else:
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="Ошибка: пользователь не найден.",
            reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_profile")
            )
        )
    bot.answer_callback_query(call.id)

@bot.message_handler(commands=['search'])
@bot.message_handler(func=lambda message: message.text == "🔍 Интернет поиск")
def search_handler(message):
    user_id = message.from_user.id
    user_data = load_user_data(user_id)
    if not user_data:
        bot.reply_to(message, "Ошибка: пользователь не найден. Попробуйте перезапустить бота с /start.", reply_markup=create_main_menu())
        return
    if user_data['subscription_plan'] == 'free':
        bot.reply_to(
            message,
            "🌐 Веб-поиск доступен только с подпиской Plus.\nПодпишитесь, чтобы получить доступ к этой функции!",
            reply_markup=create_subscription_required_keyboard()
        )
        log_command(user_id, "search_denied_no_subscription")
        return
    new_state = not user_data['web_search_enabled']
    user_data['web_search_enabled'] = new_state
    save_user_data(user_data)
    log_command(user_id, f"search_{'on' if new_state else 'off'}")
    status_text = "включён" if new_state else "выключен"
    bot.reply_to(message, f"Веб-поиск {status_text}.", reply_markup=create_main_menu())

@bot.message_handler(commands=['support'])
@bot.message_handler(func=lambda message: message.text == "📞 Поддержка")
def support_handler(message):
    log_command(message.from_user.id, "support")
    bot.reply_to(message, "Напишите сюда - <a href='https://t.me/mon_tti1'>t.me/mon_tti1</a>", parse_mode="HTML", reply_markup=create_main_menu())

@bot.message_handler(commands=['cancel_subscription'])
@bot.message_handler(func=lambda message: message.text == "❌ Отмена подписки")
def cancel_subscription_handler(message):
    user_id = message.from_user.id
    user_data = load_user_data(user_id)
    if not user_data or user_data['subscription_plan'] == 'free':
        bot.reply_to(message, "У вас нет активной подписки для отмены.", reply_markup=create_main_menu())
        return
    conn = connect_to_db()
    cur = conn.cursor()
    cur.execute("""
        UPDATE users 
        SET auto_renewal = FALSE
        WHERE user_id = %s
    """, (user_id,))
    conn.commit()
    cur.close()
    conn.close()
    bot.reply_to(message, "Автопродление отключено. Ваша подписка останется активной.", reply_markup=create_main_menu())

def check_and_update_tokens(user_id):
    conn = connect_to_db()
    cur = conn.cursor()
    cur.execute(""" 
        SELECT daily_tokens, subscription_plan, last_token_update, subscription_end_date 
        FROM users WHERE user_id = %s 
    """, (user_id,))
    user_data = cur.fetchone()
    if not user_data:
        print(f"[DEBUG] Пользователь {user_id} не найден в базе данных")
        cur.close()
        conn.close()
        return

    tokens, current_plan, last_update, subscription_end_date = user_data
    current_date = datetime.datetime.now().date()

    # 🔹 Если подписка закончилась → переводим на free
    if current_plan != 'free' and subscription_end_date and current_date > subscription_end_date:
        print(f"[DEBUG] Подписка user_id={user_id} истекла, перевод на free")
        cur.execute(""" 
            UPDATE users 
            SET subscription_plan = 'free', 
                daily_tokens = %s,
                subscription_end_date = NULL,
                web_search_enabled = FALSE
            WHERE user_id = %s 
        """, (FREE_DAILY_TOKENS, user_id))
        bot.send_message(
            user_id,
            "Ваша подписка истекла. Вы переведены на бесплатный тариф. Веб-поиск отключён. "
            "Пожалуйста, выберите новый тариф: /pay",
            reply_markup=create_main_menu()
        )

    # 🔹 Если тариф free → начисляем токены раз в день
    if current_plan == 'free':
        if isinstance(last_update, str):
            last_update_date = datetime.datetime.strptime(last_update, '%Y-%m-%d').date()
        else:
            last_update_date = last_update

        if current_date > last_update_date:
            print(f"[DEBUG] Обновление токенов для user_id={user_id}: {FREE_DAILY_TOKENS}")
            cur.execute(""" 
                UPDATE users 
                SET daily_tokens = %s, 
                    last_token_update = %s 
                WHERE user_id = %s 
            """, (FREE_DAILY_TOKENS, current_date, user_id))

    # 🔹 Для платных тарифов токены не ограничиваем (ставим "бесконечность")
    elif current_plan in ['plus_trial', 'plus_month']:
        cur.execute("""
            UPDATE users 
            SET daily_tokens = 999999999  -- символизируем "безлимит"
            WHERE user_id = %s
        """, (user_id,))

    conn.commit()
    cur.close()
    conn.close()

@bot.message_handler(commands=['profile'])
@bot.message_handler(func=lambda message: message.text == "👤 Мой профиль")
def show_profile(message):
    log_command(message.from_user.id, "profile")
    user_id = message.from_user.id
    user_data = load_user_data(user_id)
    if not user_data:
        bot.reply_to(message, "Ошибка: пользователь не найден. Попробуйте перезапустить бота с /start.", reply_markup=create_main_menu())
        return

    subscription_end_date = user_data.get('subscription_end_date')
    remaining_days = None
    if user_data['subscription_plan'] != 'free' and subscription_end_date:
        today = datetime.datetime.now().date()
        remaining_days = (subscription_end_date - today).days
        if remaining_days < 0:
            remaining_days = 0

    # 🔹 Веб-поиск
    web_search_status = "включён" if user_data['web_search_enabled'] else \
        "выключен" if user_data['subscription_plan'].startswith('plus_') else \
        "недоступен (требуется подписка Plus)"

    # 🔹 Квота токенов
    if user_data['subscription_plan'] in ['plus_trial', 'plus_month']:
        quota_text = "GPT-5: безлимит ✅"
    else:
        quota_text = f"GPT-5: {user_data['daily_tokens']} символов"

    profile_text = f"""
ID: {user_id}

Ваш текущий тариф: {PLAN_NAMES.get(user_data['subscription_plan'], user_data['subscription_plan'])}
"""
    if user_data['subscription_plan'] != 'free' and remaining_days is not None:
        profile_text += f"Подписка активна ещё {remaining_days} дней\n"

    profile_text += f"""
Веб-поиск: {web_search_status}

Оставшаяся квота:
{quota_text}

🏷 Детали расходов:
💰 Общая сумма: ${user_data['total_spent']:.4f}

"""
    bot.send_message(message.chat.id, profile_text, reply_markup=create_profile_menu())


ADMIN_IDS = [998107476, 741831495]


# ---------- КЭШ ДЛЯ ASSISTANTS (чтобы не дергать Redis/БД на каждый вызов) ----------
_ASSISTANTS_CACHE = {"ts": 0, "data": {"assistants": {}}}
_ASSISTANTS_TTL = 30  # секунды кэша

def get_assistants_cached():
    """Возвращает конфигурацию ассистентов, кэшируя результат на _ASSISTANTS_TTL секунд."""
    try:
        import time as _time
        now = int(_time.time())
        if now - _ASSISTANTS_CACHE["ts"] < _ASSISTANTS_TTL and _ASSISTANTS_CACHE["data"]:
            return _ASSISTANTS_CACHE["data"]
        cfg = load_assistants_config()
        if isinstance(cfg, dict):
            _ASSISTANTS_CACHE["data"] = cfg
            _ASSISTANTS_CACHE["ts"] = now
            return cfg
    except Exception as e:
        print(f"[WARN] get_assistants_cached error: {e}")
    return {"assistants": {}}


# ---------- Окончательная функция показа статистики (без конфликтов) ----------
@bot.message_handler(commands=['statsadmin12'])
def show_stats_admin(message):
    # права
    if message.from_user.id not in ADMIN_IDS:
        bot.reply_to(message, "У вас нет прав для просмотра статистики.", reply_markup=create_main_menu())
        return

    # логируем сам запрос админа (он будет нормализован функцией log_command)
    log_command(message.from_user.id, "statsadmin12")

    # получаем сырые статистики из БД (списки кортежей (command, count))
    try:
        week_raw = get_command_stats('week')
        month_raw = get_command_stats('month')
        year_raw = get_command_stats('year')
    except Exception as e:
        print(f"[ERROR] Не удалось получить статистику: {e}")
        bot.reply_to(message, "Ошибка получения статистики.", reply_markup=create_main_menu())
        return

    # агрегируем и нормализуем (используем normalize_command, но normalize_command читает кэш)
    def aggregate(raw):
        agg = {}
        for cmd, cnt in raw:
            norm = normalize_command(cmd)
            if not norm:
                continue
            agg[norm] = agg.get(norm, 0) + int(cnt)
        return agg

    week = aggregate(week_raw)
    month = aggregate(month_raw)
    year = aggregate(year_raw)

    # группировка для читаемости
    def group_stats(agg):
        groups = {
            "Профиль": {},
            "Ассистенты": {},
            "Подписки": {},
            "Веб-поиск": {},
            "Поддержка": {},
            "Эксперты": {},
            "Платежи/прочее": {},
            "Админ/системное": {},
            "Другое": {}
        }
        for cmd, cnt in agg.items():
            if "Мой профиль" in cmd or "Назад" in cmd:
                groups["Профиль"][cmd] = cnt
            elif cmd.startswith("🤖 Ассистент") or "Ассистенты" in cmd:
                groups["Ассистенты"][cmd] = cnt
            elif "Подписк" in cmd or "Купить" in cmd or "Отмена подписки" in cmd:
                groups["Подписки"][cmd] = cnt
            elif "веб-поиск" in cmd or "Интернет поиск" in cmd or "Включить веб-поиск" in cmd or "Выключить веб-поиск" in cmd or "Попытка веб-поиска" in cmd:
                groups["Веб-поиск"][cmd] = cnt
            elif "Поддержк" in cmd:
                groups["Поддержка"][cmd] = cnt
            elif "Эксперт" in cmd:
                groups["Эксперты"][cmd] = cnt
            # elif cmd in ("start", "🔗 Реферальная ссылка", "referral"):
                groups["Платежи/прочее"][cmd] = cnt
            elif "Статистика" in cmd or cmd == "statsadmin12" or cmd.startswith("📊"):
                groups["Админ/системное"][cmd] = cnt
            else:
                groups["Другое"][cmd] = cnt
        return groups

    wk_g = group_stats(week)
    mo_g = group_stats(month)
    yr_g = group_stats(year)

    # форматирование
    def format_group(title, d):
        if not d:
            return ""
        lines = sorted(d.items(), key=lambda x: -x[1])
        s = f"<b>{title}</b>\n"
        for name, cnt in lines:
            s += f"• {name}: {cnt} раз\n"
        s += "\n"
        return s

    def format_report(period_title, groups_dict):
        header = f"<b>{period_title}</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        order = ["Профиль", "Ассистенты", "Подписки", "Веб-поиск", "Поддержка", "Эксперты", "Платежи/прочее", "Админ/системное", "Другое"]
        body = ""
        for g in order:
            body += format_group(g, groups_dict.get(g, {}))
        return header + body + "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"

    reports = [
        format_report("📅 За неделю:", wk_g),
        format_report("📅 За месяц:", mo_g),
        format_report("📅 За год:", yr_g)
    ]

    # отправляем аккуратно (разбитие длинных сообщений)
    for rpt in reports:
        try:
            if len(rpt) > 4096:
                for i in range(0, len(rpt), 4096):
                    bot.reply_to(message, rpt[i:i+4096], parse_mode="HTML", reply_markup=create_main_menu())
            else:
                bot.reply_to(message, rpt, parse_mode="HTML", reply_markup=create_main_menu())
        except Exception as e:
            print(f"[WARN] Ошибка отправки статистики (fallback): {e}")
            # fallback plain
            try:
                bot.reply_to(message, rpt, reply_markup=create_main_menu())
            except Exception as e2:
                print(f"[ERROR] fallback send failed: {e2}")

@bot.message_handler(func=lambda message: message.text == "Отменить")
def cancel_subscription(message):
    log_command(message.from_user.id, "Отменить")
    bot.send_message(message.chat.id, "Вы отменили выбор тарифного плана.", reply_markup=create_main_menu())

@bot.message_handler(commands=["start", "help"])
def send_welcome(message):
    log_command(message.from_user.id, "start")
    referrer_id = message.text.split()[1] if len(message.text.split()) > 1 else None
    user_id = message.from_user.id
    print(f"Start command received. User ID: {user_id}, Referrer ID: {referrer_id}")
    user_data = load_user_data(user_id)
    if user_data:
        if referrer_id:
            bot.reply_to(message, "Вы уже зарегистрированы. Нельзя использовать реферальную ссылку.", reply_markup=create_main_menu())
        else:
            bot.send_message(message.chat.id, "Добро пожаловать обратно!", reply_markup=create_main_menu())
    else:
        if referrer_id:
            try:
                referrer_id = int(referrer_id)
                referrer_data = load_user_data(referrer_id)
                if referrer_data:
                    referrer_data['invited_users'] = referrer_data.get('invited_users', 0) + 1
                    referrer_data['daily_tokens'] += 100000
                    save_user_data(referrer_data)
            except ValueError:
                print("Invalid referrer ID format")
        user_data = create_default_user(user_id, referrer_id)
        bot.send_message(message.chat.id, "Вы успешно зарегистрированы!", reply_markup=create_main_menu())
    set_user_assistant(user_id, 'universal_expert')
    if not check_user_subscription(user_id):
        bot.send_message(
            message.chat.id,
            """👋 Привет! Это быстро и бесплатно.
Чтобы начать пользоваться ботом, подпишись на наш канал Guiding Star — ты получишь доступ к бота и эксклюзивным материалам по финансам и ИИ.""",
            reply_markup=create_subscription_keyboard()
        )
        return
    bot.send_message(
    message.chat.id,
    """👋 Привет
Я — Finny, твой ИИ-финансовый помощник. Уже сегодня ты можешь:

🧮 Составить бюджет и найти +10% свободных денег — под твои доходы и цели
🛡 Настроить телефон ребёнка за 5 минут — пошаговые настройки и советы
🏦 Снизить платёж по ипотеке и сэкономить до 300 тыс — расчёт выгоды за 1 минуту

Что ещё я умею:
📺 <a href="https://telegra.ph/Moj-post-08-12">Видео-инструкция — как работать с ботом</a>
🎙 Голосовой чат — общайтесь с ботом голосом
🌍 Поиск по интернету — всегда актуальные данные
🤖 Оригинальный GPT от OpenAI
🧠 GPT-5 — умные ответы в любой теме
📂 Умеет работать с файлами PDF, XLSX, DOCX, CSV, TXT
🔗 Чтение ссылок — разбор содержимого страниц
🖼 Умеет распознавать картинки
🎥 Умеет суммаризировать YouTube-видео
📝 Запоминает контекст диалога

🔺 Наши соцсети:
Telegram — https://t.me/GuidingStarVlog
VK — https://vk.com/guidingstarvlog
Образовательная площадка — https://mindsy.ru/""",
    reply_markup=create_main_menu(),
    parse_mode="HTML"
)
# @bot.message_handler(commands=['referral'])
# @bot.message_handler(func=lambda message: message.text == "🔗 Реферальная ссылка")
# def send_referral_link(message):
#     log_command(message.from_user.id, "referral")
#     user_id = message.from_user.id
#     referral_link = generate_referral_link(user_id)
#     bot.reply_to(message, f"Ваша реферальная ссылка: {referral_link}", reply_markup=create_main_menu())

def typing(chat_id):
    while True:
        bot.send_chat_action(chat_id, "typing")
        time.sleep(5)


def get_all_users():
    """Возвращает список всех user_id из базы"""
    conn = connect_to_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT user_id FROM users")
        users = [row[0] for row in cur.fetchall()]
    except Exception as e:
        print(f"[ERROR] Не удалось получить список пользователей: {e}")
        users = []
    finally:
        cur.close()
        conn.close()
    return users

@bot.message_handler(commands=['broadcast'])
def broadcast(message):
    # Проверка прав
    if message.from_user.id not in ADMIN_IDS:
        bot.reply_to(message, "У вас нет прав для этой команды.", reply_markup=create_main_menu())
        return

    bot.reply_to(message, "📢 Отправьте сообщение, которое нужно разослать всем пользователям.")
    bot.register_next_step_handler(message, process_broadcast)


def process_broadcast(message):
    if message.from_user.id not in ADMIN_IDS:
        bot.reply_to(message, "У вас нет прав для этой команды.", reply_markup=create_main_menu())
        return

    users = get_all_users()
    success, failed = 0, 0

    bot.reply_to(message, "📡 Рассылка запущена...")

    for user_id in users:
        try:
            if message.content_type == "text":
                bot.send_message(user_id, message.text, reply_markup=create_main_menu())

            elif message.content_type == "photo":
                bot.send_photo(user_id, message.photo[-1].file_id, caption=message.caption or "")

            elif message.content_type == "document":
                bot.send_document(user_id, message.document.file_id, caption=message.caption or "")

            elif message.content_type == "video":
                bot.send_video(user_id, message.video.file_id, caption=message.caption or "")

            elif message.content_type == "voice":
                bot.send_voice(user_id, message.voice.file_id, caption=message.caption or "")

            elif message.content_type == "audio":
                bot.send_audio(user_id, message.audio.file_id, caption=message.caption or "")

            else:
                bot.send_message(user_id, "📢 (неподдерживаемый тип сообщения)", reply_markup=create_main_menu())

            success += 1
            time.sleep(0.05)  # антифлуд

        except Exception as e:
            print(f"[WARN] Не удалось отправить {user_id}: {e}")
            failed += 1

    bot.send_message(message.chat.id, f"✅ Рассылка завершена.\nУспешно: {success}\nОшибок: {failed}")


def perform_web_search(query: str) -> str:
    endpoint = "https://api.bing.microsoft.com/v7.0/search"
    headers = {"Ocp-Apim-Subscription-Key": BING_API_KEY}
    params = {"q": query, "count": 3, "textDecorations": False, "textFormat": "Raw"}
    try:
        response = requests.get(endpoint, headers=headers, params=params)
        data = response.json()
        web_pages = data.get("webPages", {}).get("value", [])
        if not web_pages:
            return "Нет результатов из веб-поиска."
        results = "\n".join([f"{item['name']}: {item['url']}" for item in web_pages])
        return results
    except Exception as e:
        print(f"[ОТЛАДКА] Ошибка Bing Search: {str(e)}")
        return "Ошибка при поиске в интернете."

def needs_web_search(message: str) -> bool:
    keywords = ["найди", "что сейчас", "новости", "поиск", "в интернете", "актуально"]
    return any(kw in message.lower() for kw in keywords)

from threading import Thread
from collections import defaultdict
import time

# Очередь сообщений для каждого пользователя
message_queues = defaultdict(list)
user_processing = defaultdict(bool)  # флаг "идёт обработка" для каждого пользователя


def split_message(text, chunk_size=4000):
    """
    Разбивает длинный текст на части по chunk_size символов,
    стараясь резать по предложениям или хотя бы по пробелу.
    """
    chunks = []
    while len(text) > chunk_size:
        # ищем ближайший перенос строки или точку перед лимитом
        split_at = max(
            text.rfind("\n", 0, chunk_size),
            text.rfind(". ", 0, chunk_size),
            text.rfind(" ", 0, chunk_size)
        )
        if split_at == -1 or split_at < chunk_size // 2:
            split_at = chunk_size  # если ничего не нашли — режем по лимиту

        chunks.append(text[:split_at].strip())
        text = text[split_at:].strip()
    if text:
        chunks.append(text.strip())
    return chunks


def send_typing(chat_id, stop_flag):
    """Отправляет typing каждые 3 секунды, пока stop_flag[0] == False."""
    while not stop_flag[0]:
        try:
            bot.send_chat_action(chat_id, "typing")
        except Exception:
            pass
        time.sleep(3)


def process_user_queue(user_id, chat_id):
    if user_processing[user_id]:
        return
    if not message_queues[user_id]:
        return

    user_processing[user_id] = True
    message = message_queues[user_id].pop(0)

    def _worker():
        stop_flag = [False]
        typing_thread = Thread(target=send_typing, args=(chat_id, stop_flag), daemon=True)
        typing_thread.start()

        try:
            text = message.text
            ai_response = process_text_message(text, chat_id)
            stop_flag[0] = True
            typing_thread.join(timeout=1)
            if isinstance(ai_response, tuple):
                final_answer, sources_block = ai_response
                for chunk in split_message(final_answer, 4000):
                    bot.send_message(chat_id, chunk, reply_markup=None)  # Изменено
                bot.send_message(chat_id, sources_block, disable_web_page_preview=True, reply_markup=None)  # Изменено
            else:
                for chunk in split_message(ai_response, 4000):
                    bot.send_message(chat_id, chunk, reply_markup=None)  # Изменено
        except Exception as e:
            stop_flag[0] = True
            bot.send_message(chat_id, f"Ошибка при обработке: {e}", reply_markup=None)  # Изменено
        finally:
            user_processing[user_id] = False
            if message_queues[user_id]:
                process_user_queue(user_id, chat_id)

    Thread(target=_worker, daemon=True).start()


@bot.message_handler(func=lambda message: True, content_types=["text"])
def echo_message(message):
    user_id = message.from_user.id
    chat_id = message.chat.id

    # Проверяем подписку
    if not check_user_subscription(user_id):
        user_data = load_user_data(user_id)
        user_data['pending_query'] = message.text
        save_user_data(user_data)
        bot.send_message(
            chat_id,
            """👋 Привет! Это быстро и бесплатно.
Чтобы начать пользоваться ботом, подпишись на наш канал Guiding Star — ты получишь доступ к бота и эксклюзивным материалам по финансам и ИИ.""",
            reply_markup=create_subscription_keyboard()
        )
        return

    # Добавляем сообщение в очередь
    message_queues[user_id].append(message)

    # Запускаем обработку, если не занята
    process_user_queue(user_id, chat_id)

# ----------------- Анализ больших документов без обрезки (не отправляя текст обратно) -----------------
def _chunk_text_full(text: str, max_chars: int = 8000, overlap: int = 300):
    """
    Разбивает текст на чанки длиной <= max_chars, с перекрытием overlap символов.
    НЕ обрезает текст: все символы покрыты.
    Возвращает список чанков (строк).
    """
    if not text:
        return []
    if max_chars <= overlap:
        raise ValueError("max_chars must be > overlap")
    chunks = []
    start = 0
    L = len(text)
    while start < L:
        end = start + max_chars
        chunk = text[start:end]
        chunks.append(chunk)
        # двигаться на (max_chars - overlap) символов, чтобы был контекст
        start = end - overlap
    return chunks

# ----------------- Анализ больших документов без обрезки (не отправляя текст обратно) -----------------
def _analyze_chunks_with_ai(chunks: list, filename: str, message, user_query: str | None = None):
    """
    Анализ чанков и синтез итогового ответа. Возвращает итоговый текст — без отправки исходного файла.
    Если user_query задан, итог — ответ на этот вопрос (используя данные из чанков).
    Если user_query == None, итог — аналитический разбор: ключевые факты, выводы и рекомендации.
    """
    partials = []
    total = len(chunks)
    for idx, chunk in enumerate(chunks):
        # Инструкция для анализа части — просим не делать общий резюме, а извлечь факты/данные релевантные вопросу
        if user_query:
            prompt = (
                f"[Файл: {filename}] Часть {idx+1}/{total}.\n"
                f""
                "\n\n"
                f"{chunk}\n\n"
                ""
            )
        else:
            prompt = (
                f"[Файл: {filename}] Часть {idx+1}/{total}.\n"
                "\n"
                "\n\n"
                f"{chunk}\n\n"
                ""
            )

        # показать typing
        try:
            bot.send_chat_action(message.chat.id, "typing")
        except Exception:
            pass

        try:
            partial = process_text_message(prompt, message.chat.id)
        except Exception as e:
            print(f"[WARN] AI chunk analysis failed (part {idx+1}): {e}")
            partial = f"[Ошибка анализа части {idx+1}]"
        partials.append(f"--- Часть {idx+1}/{total} ---\n{partial}\n")

    # Синтез итогового ответа (учитываем user_query)
    if user_query:
        synthesis_instruct = (
            f"[Файл: {filename}] Объединение частичных фактов для ответа на вопрос: «{user_query}».\n"
            "На основе приведённых частичных фактов составь развёрнутый ответ на вопрос. "
            "Если фактов недостаточно — честно укажи, какие данные отсутствуют и что нужно уточнить. "
            "Ответ структурируй: 1) Ответ на вопрос (по сути), 2) Ключевые факты, использованные при ответе, 3) Рекомендации/следующие шаги."
        )
    else:
        synthesis_instruct = (
            f"[Файл: {filename}] Объединение частичных фактов / аналитика.\n"
            "На основе частичных фактов составь аналитический ответ: 1) Ключевые выводы (пункты), "
            "2) Практические рекомендации (пункты), 3) 3 приоритетных вопроса/неясности для проверки."
        )

    synthesis_prompt = synthesis_instruct + "\n\nЧастичные анализы:\n\n" + "\n".join(partials)

    try:
        bot.send_chat_action(message.chat.id, "typing")
    except Exception:
        pass

    try:
        final_analysis = process_text_message(synthesis_prompt, message.chat.id)
    except Exception as e:
        print(f"[ERROR] AI synthesis failed: {e}")
        final_analysis = "Ошибка при объединении анализов документа."

    return final_analysis


# Новый хендлер документов — сохраняет файл в user_data + отвечает с учётом подписи (caption) или даёт аналитический ответ
@bot.message_handler(content_types=['document'])
def handle_document(message):
    user_data = load_user_data(message.from_user.id)
    if not user_data:
        bot.reply_to(message, "Ошибка: пользователь не найден. Попробуйте /start.", reply_markup=create_main_menu())
        return

    if user_data.get('subscription_plan') == 'free':
        bot.reply_to(message, "Для чтения документов требуется подписка Plus. Выберите тариф: /pay", reply_markup=create_main_menu())
        return

    try:
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        file_extension = message.document.file_name.split('.')[-1].lower()

        # читаем весь документ (никаких обрезок при чтении)
        if file_extension == 'txt':
            content = downloaded_file.decode('utf-8', errors='ignore')
        elif file_extension == 'pdf':
            with io.BytesIO(downloaded_file) as pdf_file:
                content = read_pdf(pdf_file)  # ваша функция, возвращающая весь текст
        elif file_extension == 'docx':
            with io.BytesIO(downloaded_file) as docx_file:
                content = read_docx(docx_file)
        else:
            bot.reply_to(message, "Неверный формат файла. Поддерживаются: .txt, .pdf, .docx.", reply_markup=create_main_menu())
            return

        if not content or not content.strip():
            bot.reply_to(message, "Файл пуст или в нём нет извлекаемого текста (возможно, это изображение/pdf-скан).", reply_markup=create_main_menu())
            return
        
        user_data['last_document'] = {
            'filename': message.document.file_name,
            'content': content,
            'uploaded_at': datetime.datetime.utcnow().isoformat()
        }
        save_user_data(user_data)

        caption = (message.caption or "").strip()
        if caption:
            # build assistant header safely
            assistant_key = user_data.get('assistant') or user_data.get('assistant_id') or user_data.get('current_assistant') or user_data.get('last_assistant')
            assistant_header = ""
            if assistant_key:
                try:
                    cfg = get_assistants_cached() if 'get_assistants_cached' in globals() else load_assistants_config()
                    assistants = cfg.get("assistants", {}) if isinstance(cfg, dict) else {}
                    info = assistants.get(assistant_key)
                    if not info:
                        for k, v in assistants.items():
                            if assistant_key.lower() in k.lower() or assistant_key.lower() == v.get("name","").lower():
                                info = v
                                break
                    if info:
                        ass_name = info.get("name", assistant_key)
                        ass_prompt = info.get("prompt") or info.get("system_prompt") or info.get("system") or ""
                        assistant_header = f"[Ассистент: {ass_name}]\n{ass_prompt}\n\n" if ass_prompt else f"[Ассистент: {ass_name}]\n\n"
                except Exception as e:
                    print(f"[WARN] assistant header build failed: {e}")

            combined = assistant_header + f"[Файл: {message.document.file_name}]\n\n{content}\n\nВопрос пользователя: {caption}"

            # теперь вызывать process_text_message как раньше
            bot.send_chat_action(message.chat.id, "typing")
            ai_response = process_text_message(combined, message.chat.id)
            send_in_chunks(message, ai_response)
            return


        # Если подписи нет или прямой вызов упал — делаем безопасный chunked анализ (без отправки исходника)
        chunks = _chunk_text_full(content, max_chars=8000, overlap=400)
        user_query = caption if caption else None
        final_analysis = _analyze_chunks_with_ai(chunks, message.document.file_name, message, user_query=user_query)

        # шлём ответ частями (телеграм лимиты)
        send_in_chunks(message, final_analysis)
    except Exception as e:
        print(f"[ERROR] handle_document exception: {e}")
        bot.reply_to(message, f"Ошибка при чтении файла: {e}", reply_markup=create_main_menu())


def send_in_chunks(message, text, chunk_size=4000):
    try:
        for i in range(0, len(text), chunk_size):
            bot.reply_to(message, text[i:i+chunk_size], reply_markup=None)  # Изменено
    except Exception as e:
        print(f"[WARN] sending analysis failed: {e}")
        try:
            bot.reply_to(message, text, reply_markup=None)  # Изменено
        except Exception as e2:
            print(f"[ERROR] final send failed: {e2}")
            bot.reply_to(message, "Ошибка при отправке результата анализа.", reply_markup=None)  # Изменено

def read_pdf(file):
    content = []
    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                content.append(text)
    return "\n".join(content)

def read_docx(file):
    document = docx.Document(file)
    content = []
    for para in document.paragraphs:
        content.append(para.text)
    return "\n".join(content)

def update_user_tokens(user_id, input_tokens, output_tokens):
    check_and_update_tokens(user_id)
    user_data = load_user_data(user_id)
    # Расширьте проверку: plus с или без _
    if user_data['subscription_plan'] in ['plus_trial', 'plus_month', 'plus']:
        return True  # Безлимит для всех Plus-вариантов
    total_tokens_used = input_tokens + output_tokens
    new_tokens = user_data['daily_tokens'] - total_tokens_used
    if new_tokens < 0:
        return False
    user_data['daily_tokens'] = new_tokens
    user_data['input_tokens'] += input_tokens
    user_data['output_tokens'] += output_tokens
    save_user_data(user_data)
    return True

def generate_referral_link(user_id):
    return f"https://t.me/fiinny_bot?start={user_id}"

def process_text_message(text, chat_id) -> str:
    user_data = load_user_data(chat_id)
    if not user_data:
        return "Ошибка: пользователь не найден. Попробуйте перезапустить бота с /start."
    input_tokens = len(text)
    
    # Проверка только для free
    if user_data['subscription_plan'] == 'free':
        check_and_update_tokens(chat_id)
        user_data = load_user_data(chat_id)
        if user_data['daily_tokens'] < input_tokens:
            return "У вас закончился лимит токенов. Попробуйте завтра или купите подписку: /pay"
    
    # Для Plus — сразу True, без вызова update_user_tokens
    if user_data['subscription_plan'] in ['plus_trial', 'plus_month', 'plus']:
        # Просто обновляем счётчики, но без лимита
        user_data['input_tokens'] += input_tokens
        save_user_data(user_data)
    elif not update_user_tokens(chat_id, input_tokens, 0):
        return "У вас закончился лимит токенов. Попробуйте завтра или купите подписку: /pay"
    
    config = load_assistants_config()
    current_assistant = get_user_assistant(chat_id)
    assistant_settings = config["assistants"].get(current_assistant, {})
    prompt = assistant_settings.get("prompt", "Вы просто бот.")

    if user_data['web_search_enabled'] or needs_web_search(text):
        if user_data['subscription_plan'] == 'free':
            return "Веб-поиск доступен только с подпиской Plus. Выберите тариф: /pay"
        print("[DEBUG] Выполняется веб-поиск")
        # Исправленный вызов: передайте все требуемые аргументы (user_id=chat_id, query=text, assistant_key=current_assistant)
        # Также напрямую возвращайте результат, чтобы избежать двойной генерации ИИ
        return _perform_web_search(chat_id, text, current_assistant)

    # Если веб-поиск не нужен, продолжайте с обычной генерацией (без изменений)
    input_text = f"{prompt}\n\nUser: {text}\nAssistant:"
    history = get_chat_history(chat_id)
    history.append({"role": "user", "content": input_text})
    try:
        chat_completion = openai.ChatCompletion.create(
            model="gpt-5-mini-2025-08-07",
            messages=history
        )
        ai_response = chat_completion.choices[0].message.content
        output_tokens = len(ai_response)
        if not update_user_tokens(chat_id, 0, output_tokens):
            return "Ответ слишком длинный для вашего лимита токенов. Оформите подписку"
        user_data = load_user_data(chat_id)
        user_data['total_spent'] += (input_tokens + output_tokens) * 0.000001
        save_user_data(user_data)
        store_message_in_db(chat_id, "user", input_text)
        store_message_in_db(chat_id, "assistant", ai_response)
        return ai_response
    except Exception as e:
        return f"Произошла ошибка: {str(e)}"

 # Добавьте в начало файла, если нет

@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    user_data = load_user_data(message.from_user.id)
    if not user_data:
        bot.reply_to(message, "Ошибка: пользователь не найден. Попробуйте /start.", reply_markup=None)
        return

    if user_data.get('subscription_plan') == 'free':
        bot.reply_to(message, "Для анализа изображений требуется подписка Plus. Выберите тариф: /pay", reply_markup=None)
        return

    try:
        # Берём фото наибольшего размера
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)

        # Кодируем в base64
        base64_image = base64.b64encode(downloaded_file).decode('utf-8')

        # Промпт: используем caption или дефолтный
        caption = (message.caption or "").strip()
        if caption:
            question = f"Пользовательский вопрос: {caption}"
        else:
            question = "Опиши подробно, что изображено на этой фотографии: объекты, цвета, действия, эмоции и возможный контекст."

        # Промпт ассистента
        current_assistant = get_user_assistant(message.from_user.id)
        config = load_assistants_config()
        assistant_settings = config["assistants"].get(current_assistant, {})
        prompt = assistant_settings.get("prompt", "Вы просто бот.")

        # Формируем сообщение для OpenAI (мультимодальное)
        messages = [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": question},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{base64_image}"
                        }
                    }
                ]
            }
        ]

        # Проверяем токены (примерно: текст + ~1.5x размер фото в символах)
        input_tokens = len(question) + len(base64_image) * 3 // 4  # Примерная оценка
        if user_data['subscription_plan'] == 'free':
            check_and_update_tokens(message.from_user.id)
            user_data = load_user_data(message.from_user.id)
            if user_data['daily_tokens'] < input_tokens:
                bot.reply_to(message, "У вас закончился лимит токенов. Попробуйте завтра или купите подписку: /pay", reply_markup=None)
                return
        if not update_user_tokens(message.from_user.id, input_tokens, 0):
            bot.reply_to(message, "У вас закончился лимит токенов. Попробуйте завтра или купите подписку: /pay", reply_markup=None)
            return

        # Отправляем запрос к OpenAI (используем gpt-4o-mini для vision)
        bot.send_chat_action(message.chat.id, "typing")
        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",  # Или "gpt-4-turbo" для лучшего качества
            messages=messages,
            max_tokens=1000  # Лимит ответа
        )
        ai_response = response.choices[0].message.content

        # Обновляем токены для вывода
        output_tokens = len(ai_response)
        update_user_tokens(message.from_user.id, 0, output_tokens)

        # Сохраняем историю (опционально)
        store_message_in_db(message.chat.id, "user", question)
        store_message_in_db(message.chat.id, "assistant", ai_response)

        # Отправляем ответ
        bot.reply_to(message, ai_response, reply_markup=None, parse_mode='HTML' if '<' in ai_response else None)

    except Exception as e:
        print(f"[ERROR] handle_photo exception: {e}")
        bot.reply_to(message, f"Ошибка при анализе изображения: {str(e)}. Проверьте формат или попробуйте снова.", reply_markup=None)

@bot.message_handler(content_types=["voice"])
def voice(message):
    user_data = load_user_data(message.from_user.id)
    if user_data['subscription_plan'] == 'free':
        bot.reply_to(message, "Для обработки голосовых сообщений требуется подписка Plus. Выберите тариф: /pay", reply_markup=create_main_menu())
        return
    try:
        file_info = bot.get_file(message.voice.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as temp_file:
            temp_file.write(downloaded_file)
            temp_file.flush()
            audio = AudioSegment.from_ogg(temp_file.name)
            wav_temp_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            audio.export(wav_temp_file.name, format="wav")
            with open(wav_temp_file.name, 'rb') as wav_file:
                response = openai.Audio.transcribe(
                    model="whisper-1",
                    file=wav_file
                )
        recognized_text = response['text'].strip()
        if len(recognized_text) > 1000000:
            bot.reply_to(message, "Текст слишком длинный, сократите его.", reply_markup=create_main_menu())
            return
        if not recognized_text:
            bot.reply_to(message, "Текст неразборчив. Попробуйте снова.", reply_markup=create_main_menu())
            return
        ai_response = process_text_message(recognized_text, message.chat.id)
        bot.reply_to(message, ai_response, reply_markup=None)
    except Exception as e:
        logging.error(f"Ошибка обработки голосового сообщения: {e}")
        bot.reply_to(message, "Произошла ошибка, попробуйте позже!", reply_markup=create_main_menu())

def handler(event, context):
    try:
        body = event.get("body", "")
        if not body:
            print(f"[WARN] Пустой body в handler")
            return {"statusCode": 200, "body": "ok"}
        
        message = json.loads(body)
        update = telebot.types.Update.de_json(message)
        
        # Проверка: update должен быть объектом Update, не int или другим
        if not isinstance(update, telebot.types.Update):
            print(f"[ERROR] Некорректное update (тип: {type(update)}, значение: {message})")
            return {"statusCode": 200, "body": "ok"}
        
        # Проверяем наличие message, callback_query или pre_checkout_query
        if update.message or update.callback_query or update.pre_checkout_query:
            try:
                bot.process_new_updates([update])
            except telebot.apihelper.ApiTelegramException as e:
                if e.error_code == 403:
                    print(f"Пользователь заблокировал бота.")
                else:
                    print(f"Ошибка API Telegram: {e}")
            except Exception as e:
                print(f"Ошибка обработки обновления: {e}")
        else:
            print(f"[WARN] Игнорируем update без ключевых полей: {message}")
    except json.JSONDecodeError as e:
        print(f"[ERROR] Ошибка парсинга JSON в handler: {e}")
    except Exception as e:
        print(f"[ERROR] Общая ошибка в handler: {e}")
    
    return {"statusCode": 200, "body": "ok"}


def check_experts_in_database(connection):
    with connection.cursor() as cursor:
        cursor.execute("SELECT expert_id, name, specialization FROM experts;")

def main():
    logger.info("Bot started")
    conn = None
    max_retries = 5
    for attempt in range(max_retries):
        try:
            conn = connect_to_db()
            create_command_logs_table()
            check_and_create_columns(conn)
            create_subscription_tables(conn)
            with conn.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM assistants;")
                count = cursor.fetchone()[0]
            if count == 0:
                logger.warning("Таблица 'assistants' пуста! Добавь ассистентов через SQL.")
            else:
                logger.info("Обновляем кэш ассистентов...")
                refresh_assistants_cache(conn)
            logger.info("Обновляем список экспертов...")
            insert_initial_experts(conn)
            check_experts_in_database(conn)
            assistants_config = load_assistants_config()
            setup_bot_commands()
            break
        except Exception as e:
            logger.error(f"Ошибка при инициализации бота (попытка {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(5)
            else:
                logger.error("Не удалось инициализировать бота после нескольких попыток")
                return
        finally:
            if conn:
                conn.close()

    # Запуск polling в цикле для устойчивости
    while True:
        try:
            logger.info("Starting polling...")
            bot.polling(non_stop=True, timeout=60)
        except Exception as e:
            logger.error(f"Ошибка в polling: {e}")
            time.sleep(5)  # Пауза перед повторной попыткой
        try:
            schedule.run_pending()
        except Exception as e:
            logger.error(f"Ошибка в schedule.run_pending: {e}")
        time.sleep(60)

if __name__ == "__main__":
    main()