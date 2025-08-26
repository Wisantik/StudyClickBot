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
def _call_search_api(search_query):
    print(f"[DEBUG] Выполнение поиска с DDGS: {search_query}")
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(search_query, region="ru-ru", safesearch="moderate", max_results=15))
        formatted_results = [
            {
                'title': result['title'],
                'snippet': result['body'],
                'link': result['href']
            } for result in results
            if result.get('title') and result.get('href') and not result['href'].endswith("wiktionary.org/wiki/")
        ]
        print(f"[DEBUG] Получено результатов поиска: {len(formatted_results)}")
        return formatted_results
    except Exception as e:
        print(f"[ERROR] Ошибка при выполнении веб-поиска: {str(e)}")
        return []

def _perform_web_search(query: str, limit: int = 5) -> str:
    print(f"[DEBUG] Начало веб-поиска для запроса: {query}")
    cleaned_query = re.sub(
        r'^(привет|здравствуй|как дела|найди|найди мне)\s+',
        '', query, flags=re.IGNORECASE
    ).strip()
    print(f"[DEBUG] Очищенный поисковый запрос: {cleaned_query}")
    search_query = f"{cleaned_query} lang:ru site:*.ru | site:bbc.com | site:reuters.com | site:theguardian.com | site:nature.com | site:sciencedaily.com"
    print(f"[DEBUG] Итоговый поисковый запрос: {search_query}")
    search_results = _call_search_api(search_query)
    if not search_results:
        return "🔍 Не удалось найти актуальные результаты по вашему запросу."

    formatted = [
        f"{i+1}️⃣ **{r['title']}**\n{r['snippet']}\n🔗 {r['link']}"
        for i, r in enumerate(search_results[:limit])
    ]
    return "\n\n🔎 Результаты веб-поиска:\n\n" + "\n\n".join(formatted)

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

def log_command(user_id, command):
    conn = connect_to_db()
    with conn.cursor() as cursor:
        cursor.execute("""
            INSERT INTO command_logs (user_id, command, timestamp)
            VALUES (%s, %s, CURRENT_TIMESTAMP)
        """, (user_id, command))
        conn.commit()
    conn.close()

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
        types.KeyboardButton("🔗 Реферальная ссылка"),
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
        BotCommand("referral", "🔗 Реферальная ссылка"),
        BotCommand("universal", "🤖 Универсальный ассистент"),
    ]
    try:
        bot.set_my_commands(commands)
        print("Команды бота успешно настроены")
    except Exception as e:
        print(f"Ошибка при настройке команд: {e}")

@bot.message_handler(commands=['universal'])
def set_universal_assistant(message):
    """Устанавливает универсального ассистента для пользователя"""
    user_id = message.from_user.id
    log_command(user_id, "universal")
    
    # Обновляем данные пользователя, устанавливая универсального ассистента
    user_data = load_user_data(user_id)
    if not user_data:
        bot.reply_to(message, "Ошибка: пользователь не найден. Попробуйте перезапустить бота с /start.", reply_markup=create_main_menu())
        return
    
    user_data['current_assistant'] = 'universal_expert'
    save_user_data(user_data)
    
    bot.reply_to(message, "Универсальный ассистент выбран! Теперь я могу отвечать на любые ваши вопросы.", reply_markup=create_main_menu())

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
            reply_markup=create_main_menu()
        )
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

Доступ к GPT 40 - безлимит
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

@bot.message_handler(commands=['assistants'])
@bot.message_handler(func=lambda message: message.text == "🤖 Ассистенты")
def assistants_button_handler(message):
    log_command(message.from_user.id, "assistants")
    bot.send_message(
        message.chat.id,
        "Выберите ассистента:",
        reply_markup=create_assistants_menu()
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("select_assistant_"))
def assistant_callback_handler(call):
    assistant_id = call.data.replace("select_assistant_", "")
    config = load_assistants_config()

    if assistant_id not in config["assistants"]:
        bot.answer_callback_query(call.id, "Ассистент не найден")
        return

    set_user_assistant(call.message.chat.id, assistant_id)
    assistant_info = config["assistants"][assistant_id]
    name = assistant_info.get("name", "Без названия")
    description = ASSISTANT_DESCRIPTIONS.get(assistant_id, "Описание отсутствует.")

    text = (
        f"✅ Вы выбрали: <b>{name}</b>\n\n"
        f"📌 Описание:\n{description}"
    )

    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=text,
        parse_mode="HTML",
        reply_markup=None  # убираем клавиатуру с ассистентами
    )


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
        log_command(call.from_user.id, f"expert_{expert_id}")
        conn = connect_to_db()
        expert = get_expert_by_id(conn, expert_id)
        conn.close()
        if not expert:
            bot.answer_callback_query(call.id, "Эксперт не найден")
            return
        expert_id, name, specialization, description, photo_url, telegram_username, contact_info, is_available = expert
        keyboard = types.InlineKeyboardMarkup()
        if telegram_username:
            keyboard.add(types.InlineKeyboardButton(
                text="Написать эксперту",
                url=f"https://t.me/{telegram_username.replace('@', '')}"
            ))
        keyboard.add(
            types.InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_profile")
        )
        message_text = f"<b>{name}</b>\n<i>{specialization}</i>\n\n{description}\n\n"
        if contact_info:
            message_text += f"<b>Контактная информация:</b>\n{contact_info}"
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
    log_command(message.from_user.id, "universal")
    set_user_assistant(message.from_user.id, 'universal_expert')
    bot.reply_to(message, "Универсальный ассистент выбран!", reply_markup=create_main_menu())

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

Доступ к GPT 40 - безлимит
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
    """
    Проверяет статус платежа для user_id каждые interval секунд,
    максимум max_checks раз (по умолчанию 12 минут).
    """
    def run():
        for attempt in range(max_checks):
            try:
                payment = Payment.find_one(payment_id)
                print(f"[DEBUG] Проверка платежа {payment_id} для {user_id}: status={payment.status}")

                if payment.status == "succeeded":
                    save_payment_method_for_user(user_id, payment.payment_method.id)
                    set_user_subscription(user_id, "plus_trial")
                    bot.send_message(
                        user_id,
                        "✅ Пробная подписка Plus активирована на 3 дня!",
                        reply_markup=create_main_menu()
                    )
                    return  # завершаем, всё ок
                elif payment.status in ["canceled", "failed"]:
                    bot.send_message(
                        user_id,
                        "❌ Оплата не прошла. Попробуйте снова: /pay",
                        reply_markup=create_main_menu()
                    )
                    return
            except Exception as e:
                print(f"[ERROR] Ошибка проверки платежа {payment_id} для {user_id}: {e}")

            # ждём перед следующей проверкой
            time.sleep(interval)

        # если все проверки закончились, но платёж так и не подтвердился
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
                description="Месячная подписка Plus: безлимитный доступ к GPT-4o, веб-поиск, обработка PDF и голосовых сообщений.",
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
                                    "quantity": "1.00",  # Исправлено на строку
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
                            print(f"[INFO] Платёж для user_id={user_id} не успешен: status={payment.status}")
                            bot.send_message(
                                user_id,
                                "❌ Не удалось продлить подписку. Пожалуйста, оплатите вручную: /pay",
                                reply_markup=create_main_menu()
                            )
                    except Exception as e:
                        print(f"[ERROR] Ошибка автопродления для user_id={user_id}: {e}")
                        bot.send_message(
                            user_id,
                            f"❌ Ошибка при автопродлении: {e}. Пожалуйста, оплатите вручную: /pay",
                            reply_markup=create_main_menu()
                        )
                else:
                    print(f"[INFO] Не найден payment_method_id для user_id={user_id}")
    except Exception as e:
        print(f"[ERROR] Ошибка при проверке автопродления: {e}")
    finally:
        conn.close()

schedule.every(5).minutes.do(check_pending_payments)
schedule.every().day.at("00:00").do(check_auto_renewal)

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
        invited_users = user_data['invited_users']
        referral_text = (
            "🙁 Вы пока не пригласили ни одного друга."
            if invited_users == 0
            else f"🎉 Вы пригласили: {invited_users} друзей"
        )
        web_search_status = "включён" if user_data['web_search_enabled'] else "выключен" if user_data['subscription_plan'].startswith('plus_') else "недоступен (требуется подписка Plus)"
        profile_text = f"""
ID: {user_id}

Ваш текущий тариф: {PLAN_NAMES.get(user_data['subscription_plan'], user_data['subscription_plan'])}
"""
        if user_data['subscription_plan'] != 'free' and remaining_days is not None:
            profile_text += f"Подписка активна еще {remaining_days} дней\n"
        profile_text += f"""
Веб-поиск: {web_search_status}

Оставшаяся квота:
GPT-4o: {user_data['daily_tokens']} символов

🏷 Детали расходов:
💰 Общая сумма: ${user_data['total_spent']:.4f}

📝 Входные токены: {user_data['input_tokens']}
📝 Выходные токены: {user_data['output_tokens']}
👥 Реферальная программа:
Количество приглашенных пользователей: {invited_users}
{referral_text}
{'👤 Вы были приглашены пользователем с ID: ' + str(user_data['referrer_id']) if user_data['referrer_id'] else 'Вы не были приглашены никем.'}
Чтобы пригласить пользователя, отправьте ему ссылку: {generate_referral_link(user_id)}
"""
        try:
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

@bot.message_handler(commands=['new'])
@bot.message_handler(func=lambda message: message.text == "🗑 Очистить историю чата")
def clear_chat_history(message):
    log_command(message.from_user.id, "new")
    chat_id = message.chat.id
    conn = connect_to_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM chat_history WHERE chat_id = %s", (chat_id,))
    conn.commit()
    cur.close()
    conn.close()
    set_user_assistant(message.from_user.id, 'universal_expert')
    bot.reply_to(message, "История чата очищена! Можете начать новый диалог с универсальным экспертом.", reply_markup=create_main_menu())

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
        SELECT daily_tokens, subscription_plan, last_token_update, last_warning_time, subscription_end_date 
        FROM users WHERE user_id = %s 
    """, (user_id,))
    user_data = cur.fetchone()
    if not user_data:
        print(f"[DEBUG] Пользователь {user_id} не найден в базе данных")
        cur.close()
        conn.close()
        return
    tokens, current_plan, last_update, last_warning_time, subscription_end_date = user_data
    current_date = datetime.datetime.now().date()
    if isinstance(last_update, str):
        last_update_date = datetime.datetime.strptime(last_update, '%Y-%m-%d').date()
    else:
        last_update_date = last_update
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
        try:
            bot.send_message(
                user_id,
                "Ваша подписка истекла. Вы переведены на бесплатный тариф. Веб-поиск отключён. Пожалуйста, выберите новый тариф: /pay",
                reply_markup=create_main_menu()
            )
        except telebot.apihelper.ApiTelegramException as e:
            if e.error_code == 403:
                print(f"Пользователь {user_id} заблокировал бота.")
            else:
                print(f"Ошибка API для {user_id}: {e}")
        except Exception as e:
            print(f"Ошибка отправки уведомления {user_id}: {e}")
    if tokens <= MIN_TOKENS_THRESHOLD and current_plan == 'free':
        if current_date > last_update_date:
            print(f"[DEBUG] Обновление токенов для user_id={user_id}: {FREE_DAILY_TOKENS}")
            cur.execute(""" 
                UPDATE users 
                SET daily_tokens = %s, 
                    last_token_update = %s 
                WHERE user_id = %s 
            """, (FREE_DAILY_TOKENS, current_date, user_id))
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
    invited_users = user_data['invited_users']
    referral_text = (
        "🙁 Вы пока не пригласили ни одного друга."
        if invited_users == 0
        else f"🎉 Вы пригласили: {invited_users} друзей"
    )
    web_search_status = "включён" if user_data['web_search_enabled'] else "выключен" if user_data['subscription_plan'].startswith('plus_') else "недоступен (требуется подписка Plus)"
    profile_text = f"""
ID: {user_id}

Ваш текущий тариф: {PLAN_NAMES.get(user_data['subscription_plan'], user_data['subscription_plan'])}
"""
    if user_data['subscription_plan'] != 'free' and remaining_days is not None:
        profile_text += f"Подписка активна еще {remaining_days} дней\n"
    profile_text += f"""
Веб-поиск: {web_search_status}

Оставшаяся квота:
GPT-4o: {user_data['daily_tokens']} символов

🏷 Детали расходов:
💰 Общая сумма: ${user_data['total_spent']:.4f}

📝 Входные токены: {user_data['input_tokens']}
📝 Выходные токены: {user_data['output_tokens']}
👥 Реферальная программа:
Количество приглашенных пользователей: {invited_users}
{referral_text}
{'👤 Вы были приглашены пользователем с ID: ' + str(user_data['referrer_id']) if user_data['referrer_id'] else 'Вы не были приглашены никем.'}
Чтобы пригласить пользователя, отправьте ему ссылку: {generate_referral_link(user_id)}
"""
    bot.send_message(message.chat.id, profile_text, reply_markup=create_profile_menu())

ADMIN_IDS = [998107476, 741831495]

@bot.message_handler(commands=['statsadmin12'])
def show_stats_admin(message):
    if message.from_user.id not in ADMIN_IDS:
        bot.reply_to(message, "У вас нет прав для просмотра статистики.", reply_markup=create_main_menu())
        return
    
    log_command(message.from_user.id, "statsadmin12")
    
    week_stats = get_command_stats('week')
    month_stats = get_command_stats('month')
    year_stats = get_command_stats('year')
    
    command_names = {
        'profile': 'Мой профиль',
        'language': 'Выбрать язык',
        'assistants': 'Ассистенты',
        'experts': 'Эксперты',
        'search_on': 'Включить веб-поиск',
        'search_off': 'Выключить веб-поиск',
        'search_denied_no_subscription': 'Попытка веб-поиска без подписки',
        'pay': 'Подписка',
        'cancel_subscription': 'Отмена подписки',
        'new': 'Очистить историю чата',
        'support': 'Поддержка',
        'statsadmin12': 'Статистика (админ)',
        'check_subscription': '✅ Нажатие "Я подписался"',
        'show_pay_menu': 'Открытие меню подписки',
        'show_assistants': 'Ассистенты (из профиля)',
        'show_experts': 'Эксперты (из профиля)',
        'show_support': 'Поддержка (из профиля)',
        'back_to_profile': 'Назад к профилю'
    }
    
    # Формируем части сообщения
    messages = []
    current_message = "📊 *Статистика использования команд* 📊\n\n"
    
    # За неделю
    current_message += "📅 *За неделю:*\n"
    current_message += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    for command, count in week_stats:
        display_name = command_names.get(command, command)
        current_message += f"🔹 {display_name}: {count} раз\n"
    
    current_message += "\n"
    messages.append(current_message)
    
    # За месяц
    current_message = "📅 *За месяц:*\n"
    current_message += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    for command, count in month_stats:
        display_name = command_names.get(command, command)
        current_message += f"🔹 {display_name}: {count} раз\n"
    
    current_message += "\n"
    messages.append(current_message)
    
    # За год
    current_message = "📅 *За год:*\n"
    current_message += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    for command, count in year_stats:
        display_name = command_names.get(command, command)
        current_message += f"🔹 {display_name}: {count} раз\n"
    
    current_message += "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    messages.append(current_message)
    
    # Отправляем сообщения по частям
    try:
        for msg in messages:
            if len(msg) > 4096:
                # Если сообщение всё ещё слишком длинное, разбиваем его
                for i in range(0, len(msg), 4096):
                    bot.reply_to(message, msg[i:i+4096], parse_mode="Markdown", reply_markup=create_main_menu())
            else:
                bot.reply_to(message, msg, parse_mode="Markdown", reply_markup=create_main_menu())
    except Exception as e:
        # Убираем Markdown и пробуем снова
        for msg in messages:
            msg_plain = msg.replace("*", "").replace("_", "")
            if len(msg_plain) > 4096:
                for i in range(0, len(msg_plain), 4096):
                    bot.reply_to(message, msg_plain[i:i+4096], reply_markup=create_main_menu())
            else:
                bot.reply_to(message, msg_plain, reply_markup=create_main_menu())

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
🎥 <a href="https://telegra.ph/Moj-post-08-12">Видео-инструкция — как работать с ботом</a>
🗣 Голосовой чат — общайся с ботом голосом
⚡ Оригинальный API — быстрее и стабильнее
🤖 GPT-4.0 — умные ответы в любой теме
📄 Обработка документов — загрузи файл и получи анализ
📖 Чтение ссылок — разбор содержимого страниц
🌐 Поиск по интернету — актуальная информация

🔺 Наши соцсети:
Telegram — https://t.me/GuidingStarVlog
VK — https://vk.com/guidingstarvlog
Образовательная площадка — https://mindsy.ru/""",
    reply_markup=create_main_menu(),
    parse_mode="HTML"
)
@bot.message_handler(commands=['referral'])
@bot.message_handler(func=lambda message: message.text == "🔗 Реферальная ссылка")
def send_referral_link(message):
    log_command(message.from_user.id, "referral")
    user_id = message.from_user.id
    referral_link = generate_referral_link(user_id)
    bot.reply_to(message, f"Ваша реферальная ссылка: {referral_link}", reply_markup=create_main_menu())

def typing(chat_id):
    while True:
        bot.send_chat_action(chat_id, "typing")
        time.sleep(5)

def send_broadcast(message_content, photo=None):
    conn = connect_to_db()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users")
    users = cur.fetchall()
    for user in users:
        try:
            if photo:
                bot.send_photo(user[0], photo, caption=message_content, reply_markup=create_main_menu())
            else:
                bot.send_message(user[0], message_content, reply_markup=create_main_menu())
        except telebot.apihelper.ApiTelegramException as e:
            if e.error_code == 403:
                print(f"Пользователь {user[0]} заблокировал бота.")
                continue
            else:
                print(f"Ошибка API для {user[0]}: {e}")
                continue
        except Exception as e:
            print(f"Ошибка отправки пользователю {user[0]}: {e}")
            continue
    cur.close()
    conn.close()

@bot.message_handler(commands=["broadcast"])
def broadcast(message):
    if message.from_user.id == 998107476:
        msg = bot.reply_to(message, "Отправьте изображение с подписью или текст для рассылки:", reply_markup=create_main_menu())
        bot.register_next_step_handler(msg, process_broadcast)
    else:
        bot.reply_to(message, "У вас нет прав на отправку рассылки.", reply_markup=create_main_menu())

def process_broadcast(message):
    if message.content_type == 'photo':
        photo = message.photo[-1].file_id
        caption = message.caption if message.caption else ""
        send_broadcast(caption, photo=photo)
    else:
        send_broadcast(message.text)
    bot.reply_to(message, "Рассылка успешно завершена!", reply_markup=create_main_menu())

@bot.message_handler(content_types=['photo'])
def handle_broadcast_photo(message):
    if message.from_user.id == 998107476 and message.caption and message.caption.startswith('/broadcast'):
        photo = message.photo[-1].file_id
        caption = message.caption.replace('/broadcast', '').strip()
        send_broadcast(caption, photo=photo)
        bot.reply_to(message, "Рассылка с изображением успешно завершена!", reply_markup=create_main_menu())

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

@bot.message_handler(func=lambda message: True, content_types=["text"])
def echo_message(message):
    if not check_user_subscription(message.from_user.id):
        bot.send_message(
            message.chat.id,
            """👋 Привет! Это быстро и бесплатно.
Чтобы начать пользоваться ботом, подпишись на наш канал Guiding Star — ты получишь доступ к бота и эксклюзивным материалам по финансам и ИИ.""",
            reply_markup=create_subscription_keyboard()
        )
        return
    bot.send_chat_action(message.chat.id, "typing")
    try:
        text = message.text
        ai_response = process_text_message(text, message.chat.id)
        bot.reply_to(message, ai_response, reply_markup=create_main_menu())
    except Exception as e:
        bot.reply_to(message, f"Произошла ошибка, попробуйте позже! {e}", reply_markup=create_main_menu())

@bot.message_handler(content_types=['document'])
def handle_document(message):
    user_data = load_user_data(message.from_user.id)
    if user_data['subscription_plan'] == 'free':
        bot.reply_to(message, "Для чтения документов требуется подписка Plus. Выберите тариф: /pay", reply_markup=create_main_menu())
        return
    file_info = bot.get_file(message.document.file_id)
    downloaded_file = bot.download_file(file_info.file_path)
    file_extension = message.document.file_name.split('.')[-1].lower()
    try:
        if file_extension == 'txt':
            content = downloaded_file.decode('utf-8')
            bot.reply_to(message, process_text_message(content, message.chat.id), reply_markup=create_main_menu())
        elif file_extension == 'pdf':
            with io.BytesIO(downloaded_file) as pdf_file:
                content = read_pdf(pdf_file)
                bot.reply_to(message, process_text_message(content, message.chat.id), reply_markup=create_main_menu())
        elif file_extension == 'docx':
            with io.BytesIO(downloaded_file) as docx_file:
                content = read_docx(docx_file)
                bot.reply_to(message, process_text_message(content, message.chat.id), reply_markup=create_main_menu())
        else:
            bot.reply_to(message, "Неверный формат файла. Поддерживаются: .txt, .pdf, .docx.", reply_markup=create_main_menu())
    except Exception as e:
        bot.reply_to(message, f"Ошибка при чтении файла: {e}", reply_markup=create_main_menu())

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
    if user_data['subscription_plan'].startswith('plus_'):
        return True
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
    if user_data['subscription_plan'] == 'free':
        check_and_update_tokens(chat_id)
        user_data = load_user_data(chat_id)
        if user_data['daily_tokens'] < input_tokens:
            return "У вас закончился лимит токенов. Попробуйте завтра или купите подписку: /pay"
    if not update_user_tokens(chat_id, input_tokens, 0):
        return "У вас закончился лимит токенов. Попробуйте завтра или купите подписку: /pay"
    config = load_assistants_config()
    current_assistant = get_user_assistant(chat_id)
    assistant_settings = config["assistants"].get(current_assistant, {})
    prompt = assistant_settings.get("prompt", "Вы просто бот.")

    web_search_appendix = ""
    if user_data['web_search_enabled'] or needs_web_search(text):
        if user_data['subscription_plan'] == 'free':
            return "Веб-поиск доступен только с подпиской Plus. Выберите тариф: /pay"
        print("[DEBUG] Выполняется веб-поиск")
        search_results = _perform_web_search(text)
        text += f"\n\n[Результаты веб-поиска]:\n{search_results}"
        web_search_appendix = f"\n\n{search_results}"

    input_text = f"{prompt}\n\nUser: {text}\nAssistant:"
    history = get_chat_history(chat_id)
    history.append({"role": "user", "content": input_text})
    try:
        chat_completion = openai.ChatCompletion.create(
            model="gpt-4.1-mini-2025-04-14",
            messages=history
        )
        ai_response = chat_completion.choices[0].message.content
        output_tokens = len(ai_response)
        if not update_user_tokens(chat_id, 0, output_tokens):
            return "Ответ слишком длинный для вашего лимита токенов."
        user_data = load_user_data(chat_id)
        user_data['total_spent'] += (input_tokens + output_tokens) * 0.000001
        save_user_data(user_data)
        store_message_in_db(chat_id, "user", input_text)
        store_message_in_db(chat_id, "assistant", ai_response)
        return ai_response + web_search_appendix
    except Exception as e:
        return f"Произошла ошибка: {str(e)}"


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
        bot.reply_to(message, ai_response, reply_markup=create_main_menu())
    except Exception as e:
        logging.error(f"Ошибка обработки голосового сообщения: {e}")
        bot.reply_to(message, "Произошла ошибка, попробуйте позже!", reply_markup=create_main_menu())

def handler(event, context):
    message = json.loads(event["body"])
    update = telebot.types.Update.de_json(message)
    allowed_updates=["message", "callback_query", "pre_checkout_query"]
    if update.message is not None:
        try:
            bot.process_new_updates([update])
        except telebot.apihelper.ApiTelegramException as e:
            if e.error_code == 403:
                print(f"Пользователь заблокировал бота.")
            else:
                print(f"Ошибка API Telegram: {e}")
        except Exception as e:
            print(f"Ошибка обработки обновления: {e}")
    return {
        "statusCode": 200,
        "body": "ok",
    }

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
                logger.info("Таблица 'assistants' пуста. Вставляем данные.")
                insert_initial_data(conn)
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
    