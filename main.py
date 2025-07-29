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

print(f"Connecting to DB: {os.getenv('DB_NAME')}, User: {os.getenv('DB_USER')}, Host: {os.getenv('DB_HOST')}")
connect_to_db()

MIN_TOKENS_THRESHOLD: Final = 5000
FREE_DAILY_TOKENS: Final = 10000

logger = telebot.logger
telebot.logger.setLevel(logging.INFO)
pay_token = os.getenv('PAY_TOKEN')
bot = telebot.TeleBot(os.getenv('BOT_TOKEN'), threaded=False)
openai.api_key = os.getenv('OPENAI_API_KEY')

BING_API_KEY = os.getenv('BING_API_KEY', "yLtkhrR3H6UjzBm3naReSJQ8G81ct409iLrcmQTeIAH338TwBZNEvSLQJ8og")

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
        BotCommand("referral", "🔗 Реферальная ссылка"),  # Добавляем команду /referral
    ]
    try:
        bot.set_my_commands(commands)
        print("Команды бота успешно настроены")
    except Exception as e:
        print(f"Ошибка при настройке команд: {e}")

def create_price_menu() -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup(
        keyboard=[
            [
                types.InlineKeyboardButton(
                    text="Пробная (3 дня за 99₽)",
                    callback_data="buy_trial"
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="Недельная - 149₽",
                    callback_data="buy_week"
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="Месячная - 399₽",
                    callback_data="buy_month"
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="Годовая - 2499₽",
                    callback_data="buy_year"
                )
            ],
        ]
    )
    return markup

def create_subscription_required_keyboard():
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton(
        text="Купить подписку",
        callback_data="show_pay_menu"
    ))
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
            text="Спасибо за подписку! Теперь вы можете использовать бота с универсальным экспертом."
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
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text="""Подписка Plus предоставляет безлимитный доступ к:
- GPT-4.0
- Чтение PDF файлов
- Чтение ссылок
- Интернет-поиск
- Обработка голосовых запросов

⚠️ Пробная подписка (3 дня за 99₽) включает автопродление на месяц за 399₽. Отменить можно в любое время после оплаты.

Варианты подписки:
- Пробная: 3 дня за 99₽
- Недельная: 149₽
- Месячная: 399₽
- Годовая: 2499₽

По вопросам: https://t.me/mon_tti1""",
        reply_markup=create_price_menu()
    )

def create_main_menu():
    keyboard = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    profile_btn = types.KeyboardButton("Мой профиль")
    language_btn = types.KeyboardButton("Выбрать язык")
    assistants_btn = types.KeyboardButton("Ассистенты")
    experts_btn = types.KeyboardButton("Эксперты")
    search_btn = types.KeyboardButton("Интернет поиск")
    pay_btn = types.KeyboardButton("Подписка")
    cancel_subscription_btn = types.KeyboardButton("Отмена подписки")
    new_btn = types.KeyboardButton("Очистить историю чата")
    support_btn = types.KeyboardButton("Поддержка")
    keyboard.add(profile_btn, language_btn)
    keyboard.add(assistants_btn, experts_btn)
    keyboard.add(search_btn, pay_btn)
    keyboard.add(cancel_subscription_btn, new_btn)
    keyboard.add(support_btn)
    return keyboard

def create_assistants_menu():
    config = load_assistants_config()
    assistants = config.get("assistants", {})
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    for assistant_id, assistant_info in assistants.items():
        keyboard.add(
            types.InlineKeyboardButton(
                text=assistant_info['name'],
                callback_data=f"select_assistant_{assistant_id}"
            )
        )
    return keyboard

def create_experts_menu():
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
    return keyboard

@bot.message_handler(commands=['assistants'])
@bot.message_handler(func=lambda message: message.text == "Ассистенты")
def assistants_button_handler(message):
    log_command(message.from_user.id, "assistants")
    bot.send_message(
        message.chat.id,
        "Выберите ассистента:",
        reply_markup=create_assistants_menu()
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("select_assistant_"))
def assistant_callback_handler(call):
    assistant_id = call.data.split("_")[-1]
    log_command(call.from_user.id, f"select_assistant_{assistant_id}")
    config = load_assistants_config()
    print(f"[DEBUG] Доступные ассистенты: {config['assistants'].keys()}")
    if assistant_id in config['assistants']:
        set_user_assistant(call.from_user.id, assistant_id)
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"Выбран ассистент: {config['assistants'][assistant_id]['name']}"
        )
    else:
        print(f"[ERROR] Ассистент {assistant_id} не найден в конфигурации")
        bot.answer_callback_query(call.id, "Ассистент не найден")

@bot.message_handler(commands=['experts'])
@bot.message_handler(func=lambda message: message.text == "Эксперты")
def experts_button_handler(message):
    log_command(message.from_user.id, "experts")
    bot.send_message(
        message.chat.id,
        "Выберите эксперта:",
        reply_markup=create_experts_menu()
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("expert_"))
def expert_callback_handler(call):
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
    message_text = f"*{name}*\n_{specialization}_\n\n{description}\n\n"
    if contact_info:
        message_text += f"*Контактная информация:*\n{contact_info}"
    if photo_url:
        try:
            bot.send_photo(
                call.message.chat.id,
                photo=photo_url,
                caption=message_text,
                parse_mode="Markdown",
                reply_markup=keyboard
            )
        except Exception as e:
            print(f"Ошибка отправки фото эксперта: {e}")
            bot.send_message(
                call.message.chat.id,
                message_text,
                parse_mode="Markdown",
                reply_markup=keyboard
            )
    else:
        bot.send_message(
            call.message.chat.id,
            message_text,
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    bot.answer_callback_query(call.id)

@bot.message_handler(func=lambda message: message.text == "Назад")
def back_button_handler(message):
    log_command(message.from_user.id, "Назад")
    bot.send_message(
        message.chat.id,
        "Вы вернулись в главное меню",
        reply_markup=create_main_menu()
    )

@bot.message_handler(func=lambda message: message.text == "Мой профиль")
def profile_button_handler(message):
    log_command(message.from_user.id, "Мой профиль")
    show_profile(message)

@bot.message_handler(commands=["pay"])
@bot.message_handler(func=lambda message: message.text == "Подписка")
def get_pay(message):
    log_command(message.from_user.id, "pay")
    bot.send_message(
        message.chat.id,
        """Подписка Plus предоставляет безлимитный доступ к:
- GPT-4.0
- Чтение PDF файлов
- Чтение ссылок
- Интернет-поиск
- Обработка голосовых запросов

⚠️ Пробная подписка (3 дня за 99₽) включает автопродление на месяц за 399₽. Отменить можно в любое время после оплаты.

Варианты подписки:
- Пробная: 3 дня за 99₽
- Недельная: 149₽
- Месячная: 399₽
- Годовая: 2499₽

По вопросам: https://t.me/mon_tti1""",
        reply_markup=create_price_menu()
    )

@bot.callback_query_handler(func=lambda callback: callback.data in ["buy_trial", "buy_week", "buy_month", "buy_year"])
def buy_subscription(callback):
    user_id = callback.from_user.id
    user_data = load_user_data(user_id)
    if not user_data:
        bot.send_message(callback.message.chat.id, "Ошибка: пользователь не найден.")
        return
    try:
        if callback.data == "buy_trial":
            if user_data['trial_used']:
                bot.send_message(callback.message.chat.id, "Вы уже использовали пробную подписку.")
                return
            price = 99  # Временно увеличим до 2 рублей для тестирования
            period = "trial"
            duration_days = 3
        elif callback.data == "buy_week":
            price = 149
            period = "week"
            duration_days = 7
        elif callback.data == "buy_month":
            price = 399
            period = "month"
            duration_days = 30
        elif callback.data == "buy_year":
            price = 2499
            period = "year"
            duration_days = 365
        amount_in_kopecks = price * 100
        print(f"[INFO] Отправка счёта для user_id={user_id}, period={period}, amount={amount_in_kopecks} копеек")
        bot.send_invoice(
            callback.message.chat.id,
            title=f"Подписка Plus ({period})",
            description=f"Подписка на {duration_days} дней",
            invoice_payload=f"plus_{period}",
            provider_token=pay_token,
            currency="RUB",
            start_parameter="test_bot",
            prices=[types.LabeledPrice(label=f"Подписка Plus ({period})", amount=amount_in_kopecks)]
        )
    except telebot.apihelper.ApiTelegramException as e:
        print(f"[ERROR] Ошибка отправки счёта: {e}")
        bot.send_message(
            callback.message.chat.id,
            "Произошла ошибка при создании счёта. Пожалуйста, попробуйте позже или обратитесь в поддержку: https://t.me/mon_tti1"
        )

@bot.pre_checkout_query_handler(func=lambda query: True)
def process_pre_checkout_query(pre_checkout_query):
    bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@bot.message_handler(content_types=['successful_payment'])
def successful_pay(message):
    payload = message.successful_payment.invoice_payload
    user_id = message.from_user.id
    conn = connect_to_db()
    cur = conn.cursor()
    if payload.startswith("plus_"):
        period = payload.split("_")[1]
        if period == "trial":
            duration_days = 3
            cur.execute("UPDATE users SET trial_used = TRUE WHERE user_id = %s", (user_id,))
        elif period == "week":
            duration_days = 7
        elif period == "month":
            duration_days = 30
        elif period == "year":
            duration_days = 365
        start_date = datetime.datetime.now().date()
        end_date = start_date + datetime.timedelta(days=duration_days)
        cur.execute("""
            UPDATE users 
            SET subscription_plan = %s,
                subscription_start_date = %s,
                subscription_end_date = %s,
                web_search_enabled = TRUE,
                auto_renewal = %s
            WHERE user_id = %s
        """, (f"plus_{period}", start_date, end_date, period == "trial", user_id))
        conn.commit()
        cur.close()
        conn.close()
        bot.send_message(
            message.chat.id, 
            f'Оплата прошла успешно!\nПодписка Plus ({period}) активирована до {end_date.strftime("%d.%m.%Y")}\n'
            f'Веб-поиск: включён\n'
            f'Автопродление: {"включено" if period == "trial" else "выключено"}'
        )
    else:
        bot.send_message(message.chat.id, "Неизвестный тип подписки.")

def check_auto_renewal():
    conn = connect_to_db()
    cur = conn.cursor()
    today = datetime.datetime.now().date()
    cur.execute("""
        SELECT user_id FROM users 
        WHERE subscription_plan = 'plus_trial' 
        AND subscription_end_date <= %s
        AND auto_renewal = TRUE
    """, (today,))
    users = cur.fetchall()
    for user_id in users:
        user_id = user_id[0]
        # Здесь должна быть интеграция с YooKassa для автоматической оплаты 399 рублей
        # Примерный код (нужна реализация через API YooKassa):
        # payment_result = make_payment(user_id, amount=399)
        # if payment_result:
        #     start_date = today
        #     end_date = start_date + datetime.timedelta(days=30)
        #     cur.execute("""
        #         UPDATE users 
        #         SET subscription_plan = 'plus_month',
        #             subscription_start_date = %s,
        #             subscription_end_date = %s
        #         WHERE user_id = %s
        #     """, (start_date, end_date, user_id))
        #     bot.send_message(user_id, "Ваша пробная подписка продлена на месяц за 399₽.")
        # else:
        #     bot.send_message(user_id, "Не удалось продлить подписку. Пожалуйста, обновите платёжные данные.")
    conn.commit()
    cur.close()
    conn.close()

schedule.every().day.at("00:00").do(check_auto_renewal)

@bot.message_handler(commands=['new'])
@bot.message_handler(func=lambda message: message.text == "Очистить историю чата")
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
    bot.reply_to(message, "История чата очищена! Можете начать новый диалог с универсальным экспертом.")

def create_language_menu():
    keyboard = types.InlineKeyboardMarkup(row_width=3)  # 3 кнопки в ряд для компактности
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
    return keyboard

@bot.message_handler(commands=['language'])
@bot.message_handler(func=lambda message: message.text == "Выбрать язык")
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
            text=f"Выбран язык: {lang_code.upper()}"
        )
    else:
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="Ошибка: пользователь не найден."
        )
    bot.answer_callback_query(call.id)

@bot.message_handler(commands=['search'])
@bot.message_handler(func=lambda message: message.text == "Интернет поиск")
def search_handler(message):
    user_id = message.from_user.id
    user_data = load_user_data(user_id)
    if not user_data:
        bot.reply_to(message, "Ошибка: пользователь не найден. Попробуйте перезапустить бота с /start.")
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
    bot.reply_to(message, f"Веб-поиск {status_text}.")

@bot.message_handler(commands=['support'])
@bot.message_handler(func=lambda message: message.text == "Поддержка")
def support_handler(message):
    log_command(message.from_user.id, "support")
    bot.reply_to(message, "Напишите сюда - https://t.me/mon_tti1")

@bot.message_handler(commands=['cancel_subscription'])
@bot.message_handler(func=lambda message: message.text == "Отмена подписки")
def cancel_subscription_handler(message):
    user_id = message.from_user.id
    user_data = load_user_data(user_id)
    if not user_data or user_data['subscription_plan'] == 'free':
        bot.reply_to(message, "У вас нет активной подписки для отмены.")
        return
    conn = connect_to_db()
    cur = conn.cursor()
    cur.execute("""
        UPDATE users 
        SET auto_renewal = FALSE,
            subscription_plan = 'free',
            subscription_end_date = NULL,
            web_search_enabled = FALSE
        WHERE user_id = %s
    """, (user_id,))
    conn.commit()
    cur.close()
    conn.close()
    bot.reply_to(message, "Подписка отменена. Автопродление отключено.")

def check_and_update_tokens(user_id):
    conn = connect_to_db()
    cur = conn.cursor()
    cur.execute(""" 
        SELECT daily_tokens, subscription_plan, last_token_update, last_warning_time, subscription_end_date 
        FROM users WHERE user_id = %s 
    """, (user_id,))
    user_data = cur.fetchone()
    if not user_data:
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
                "Ваша подписка истекла. Вы переведены на бесплатный тариф. Веб-поиск отключён. Пожалуйста, выберите новый тариф: /pay"
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
def show_profile(message):
    log_command(message.from_user.id, "profile")
    user_id = message.from_user.id
    user_data = load_user_data(user_id)
    if not user_data:
        bot.reply_to(message, "Ошибка: пользователь не найден. Попробуйте перезапустить бота с /start.")
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

Ваш текущий тариф: {user_data['subscription_plan'].capitalize()}
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
Чтобы добавить подписку нажмите /pay
"""
    bot.send_message(message.chat.id, profile_text)

ADMIN_IDS = [998107476, 741831495]

@bot.message_handler(commands=['statsadmin12'])
def show_stats_admin(message):
    if message.from_user.id not in ADMIN_IDS:
        bot.reply_to(message, "У вас нет прав для просмотра статистики.")
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
        'show_pay_menu': 'Открытие меню подписки'
    }
    stats_text = "📊 *Статистика использования команд* 📊\n\n"
    stats_text += "📅 *За неделю:*\n"
    stats_text += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    for command, count in week_stats:
        display_name = command_names.get(command, command)
        stats_text += f"🔹 {display_name}: {count} раз\n"
    stats_text += "\n📅 *За месяц:*\n"
    stats_text += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    for command, count in month_stats:
        display_name = command_names.get(command, command)
        stats_text += f"🔹 {display_name}: {count} раз\n"
    stats_text += "\n📅 *За год:*\n"
    stats_text += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    for command, count in year_stats:
        display_name = command_names.get(command, command)
        stats_text += f"🔹 {display_name}: {count} раз\n"
    stats_text += "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    try:
        bot.reply_to(message, stats_text, parse_mode="Markdown")
    except Exception as e:
        stats_text_plain = stats_text.replace("*", "").replace("_", "")
        bot.reply_to(message, stats_text_plain)

@bot.message_handler(func=lambda message: message.text == "Отменить")
def cancel_subscription(message):
    log_command(message.from_user.id, "Отменить")
    bot.send_message(message.chat.id, "Вы отменили выбор тарифного плана.", reply_markup=types.ReplyKeyboardRemove())

@bot.message_handler(commands=["start", "help"])
def send_welcome(message):
    log_command(message.from_user.id, "start")
    referrer_id = message.text.split()[1] if len(message.text.split()) > 1 else None
    user_id = message.from_user.id
    print(f"Start command received. User ID: {user_id}, Referrer ID: {referrer_id}")
    user_data = load_user_data(user_id)
    if user_data:
        if referrer_id:
            bot.reply_to(message, "Вы уже зарегистрированы. Нельзя использовать реферальную ссылку.")
        else:
            bot.send_message(message.chat.id, "Добро пожаловать обратно!")
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
        bot.send_message(message.chat.id, "Вы успешно зарегистрированы!")
    set_user_assistant(user_id, 'universal_expert')
    if not check_user_subscription(user_id):
        bot.send_message(
            message.chat.id,
            """👋 Привет! Это быстро и бесплатно.
Чтобы начать пользоваться ботом, подпишись на наш канал Guiding Star — ты получишь доступ к бота и эксклюзивным материалам по финансам и ИИ.""",
            reply_markup=create_subscription_keyboard()
        )
        return
    bot.send_message(message.chat.id, """Привет, я Финни! 👋
Я — твой друг и помощник в мире финансов! 🏆 Я здесь, чтобы сделать твой путь к финансовой грамотности лёгким и интересным — вне зависимости от твоего возраста или уровня знаний.
💡 Что я умею:
🎯 Я помогу тебе разобраться в любых финансовых вопросах — от базовых основ до сложных стратегий.
📚 Я адаптирую материал под твой уровень знаний, так что не волнуйся, если ты новичок — всё будет просто и понятно!
🔍 После каждого ответа я предложу три варианта, как двигаться дальше. Это поможет тебе лучше усвоить материал и не потеряться в сложных терминах.
🤝 Если у тебя возникнут вопросы — я всегда рядом! Мои контакты в шапке профиля — пиши, не стесняйся.
💬 У меня есть команда ассистентов по разным финансовым темам — инвестиции, кредиты, налоги, бизнес и многое другое. Просто открой меню, выбери нужную тему и получи профессиональную консультацию!
💬 Хочешь пообщаться с нашими экспертами? Легко! Просто открой меню, выбери нужную тему и получи профессиональную консультацию.""", reply_markup=create_main_menu())

@bot.message_handler(commands=['referral'])
def send_referral_link(message):
    log_command(message.from_user.id, "referral")
    user_id = message.from_user.id
    referral_link = generate_referral_link(user_id)
    bot.reply_to(message, f"Ваша реферальная ссылка: {referral_link}")

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
                bot.send_photo(user[0], photo, caption=message_content)
            else:
                bot.send_message(user[0], message_content)
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
        msg = bot.reply_to(message, "Отправьте изображение с подписью или текст для рассылки:")
        bot.register_next_step_handler(msg, process_broadcast)
    else:
        bot.reply_to(message, "У вас нет прав на отправку рассылки.")

def process_broadcast(message):
    if message.content_type == 'photo':
        photo = message.photo[-1].file_id
        caption = message.caption if message.caption else ""
        send_broadcast(caption, photo=photo)
    else:
        send_broadcast(message.text)
    bot.reply_to(message, "Рассылка успешно завершена!")

@bot.message_handler(content_types=['photo'])
def handle_broadcast_photo(message):
    if message.from_user.id == 998107476 and message.caption and message.caption.startswith('/broadcast'):
        photo = message.photo[-1].file_id
        caption = message.caption.replace('/broadcast', '').strip()
        send_broadcast(caption, photo=photo)
        bot.reply_to(message, "Рассылка с изображением успешно завершена!")

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
        bot.reply_to(message, ai_response)
    except Exception as e:
        bot.reply_to(message, f"Произошла ошибка, попробуйте позже! {e}")

@bot.message_handler(content_types=['document'])
def handle_document(message):
    user_data = load_user_data(message.from_user.id)
    if user_data['subscription_plan'] == 'free':
        bot.reply_to(message, "Для чтения документов требуется подписка Plus. Выберите тариф: /pay")
        return
    file_info = bot.get_file(message.document.file_id)
    downloaded_file = bot.download_file(file_info.file_path)
    file_extension = message.document.file_name.split('.')[-1].lower()
    try:
        if file_extension == 'txt':
            content = downloaded_file.decode('utf-8')
            bot.reply_to(message, process_text_message(content, message.chat.id))
        elif file_extension == 'pdf':
            with io.BytesIO(downloaded_file) as pdf_file:
                content = read_pdf(pdf_file)
                bot.reply_to(message, process_text_message(content, message.chat.id))
        elif file_extension == 'docx':
            with io.BytesIO(downloaded_file) as docx_file:
                content = read_docx(docx_file)
                bot.reply_to(message, process_text_message(content, message.chat.id))
        else:
            bot.reply_to(message, "Неверный формат файла. Поддерживаются: .txt, .pdf, .docx.")
    except Exception as e:
        bot.reply_to(message, f"Ошибка при чтении файла: {e}")

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
    if user_data['subscription_plan'] == 'free':
        return "Для использования этой функции требуется подписка Plus. Выберите тариф: /pay"
    input_tokens = len(text)
    if not update_user_tokens(chat_id, input_tokens, 0):
        return "У вас закончился лимит токенов. Попробуйте завтра или купите подписку."
    config = load_assistants_config()
    current_assistant = get_user_assistant(chat_id)
    assistant_settings = config["assistants"].get(current_assistant, {})
    prompt = assistant_settings.get("prompt", "Вы просто бот.")
    if needs_web_search(text) and user_data['web_search_enabled']:
        print("[ОТЛАДКА] Автоматически определён запрос для веб-поиска")
        search_results = perform_web_search(text)
        text += f"\n\n[Результаты веб-поиска]:\n{search_results}"
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
        return ai_response
    except Exception as e:
        return f"Произошла ошибка: {str(e)}"

import tempfile
from pydub import AudioSegment

@bot.message_handler(content_types=["voice"])
def voice(message):
    user_data = load_user_data(message.from_user.id)
    if user_data['subscription_plan'] == 'free':
        bot.reply_to(message, "Для обработки голосовых сообщений требуется подписка Plus. Выберите тариф: /pay")
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
            bot.reply_to(message, "Текст слишком длинный, сократите его.")
            return
        if not recognized_text:
            bot.reply_to(message, "Текст неразборчив. Попробуйте снова.")
            return
        ai_response = process_text_message(recognized_text, message.chat.id)
        bot.reply_to(message, ai_response)
    except Exception as e:
        logging.error(f"Ошибка обработки голосового сообщения: {e}")
        bot.reply_to(message, "Произошла ошибка, попробуйте позже!")

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
        experts = cursor.fetchall()
        print("Эксперты в базе данных:")
        for expert in experts:
            print(f"ID: {expert[0]}, Имя: {expert[1]}, Специализация: {expert[2]}")

if __name__ == "__main__":
    print("Bot started")
    conn = connect_to_db()
    try:
        create_command_logs_table()
        check_and_create_columns(conn)
        with conn.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM assistants;")
            count = cursor.fetchone()[0]
        if count == 0:
            print("Таблица 'assistants' пуста. Вставляем данные.")
            insert_initial_data(conn)
        print("Обновляем список экспертов...")
        insert_initial_experts(conn)
        check_experts_in_database(conn)
        assistants_config = load_assistants_config()
        setup_bot_commands()
        bot.polling(none_stop=True)  # Изменено для продолжения работы после ошибок
        while True:
            schedule.run_pending()
            time.sleep(60)
    except Exception as e:
        print(f"Ошибка в главном цикле: {e}")
    finally:
        if conn:
            conn.close()