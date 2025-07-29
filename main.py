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
from database import *
from assistance import *

# Подключение к базе данных
print(f"Connecting to DB: {os.getenv('DB_NAME')}, User: {os.getenv('DB_USER')}, Host: {os.getenv('DB_HOST')}")
connect_to_db()

# Тарифные планы как константы
TOKEN_PLANS = {
    "free": {"tokens": 30000},
    "basic": {"price": 149, "tokens": 200000},
    "advanced": {"price": 349, "tokens": 500000},
    "premium": {"price": 649, "tokens": 1200000},
    "unlimited": {"price": 1499, "tokens": 3000000},
}

MIN_TOKENS_THRESHOLD: Final = 5000  # Порог для обновления токенов
FREE_DAILY_TOKENS: Final = 30000    # Бесплатные ежедневные токены

# Настройка логирования
logger = telebot.logger
telebot.logger.setLevel(logging.INFO)
pay_token = os.getenv('PAY_TOKEN')
bot = telebot.TeleBot(os.getenv('BOT_TOKEN'), threaded=False)
openai.api_key = os.getenv('OPENAI_API_KEY')

# Класс для обработки исключений
class ExceptionHandler:
    """Обработчик исключений для бота"""
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

# Установка обработчика исключений
bot.exception_handler = ExceptionHandler()

# Работа с таблицей логов команд
def create_command_logs_table():
    """Создаёт таблицу для логов использования команд"""
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
    """Логирует вызов команды в базу данных"""
    conn = connect_to_db()
    with conn.cursor() as cursor:
        cursor.execute("""
            INSERT INTO command_logs (user_id, command, timestamp)
            VALUES (%s, %s, CURRENT_TIMESTAMP)
        """, (user_id, command))
        conn.commit()
    conn.close()

def get_command_stats(period):
    """Получает статистику команд за неделю, месяц или год"""
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

# Настройка команд бота
def setup_bot_commands():
    """Устанавливает команды бота в Telegram"""
    commands = [
        BotCommand("start", "Начать работу с ботом"),
        BotCommand("new", "Очистить историю чата"),
        BotCommand("profile", "Посмотреть профиль"),
        BotCommand("pay", "Купить подписку"),
        BotCommand("universal", "Универсальный эксперт"),
        BotCommand("cybersecurity", "Консультант по кибербезопасности"),
        BotCommand("dig_marketing", "Консультант по маркетингу"),
        BotCommand("brand_mgmt", "Консультант по бренд-менеджменту"),
        BotCommand("biz_create", "Консультант по открытию бизнеса"),
        BotCommand("comm_skills", "Консультант по навыкам общения"),
        BotCommand("stk_trading", "Консультант по фондовому рынку"),
        BotCommand("crypto", "Консультант по криптовалютам"),
        BotCommand("real_estate", "Консультант по недвижимости"),
        BotCommand("startups", "Консультант по стартапам"),
        BotCommand("passive_inv", "Консультант по пассивным инвестициям"),
        BotCommand("esg", "Консультант по ESG-инвестициям"),
        BotCommand("forex", "Консультант по валютным рынкам"),
        BotCommand("finance", "Консультант по международным финансам"),
        BotCommand("fintech", "Консультант по финтеху"),
        BotCommand("pensions", "Консультант по пенсиям"),
        BotCommand("insurance", "Консультант по страхованию"),
        BotCommand("tax_credit", "Консультант по налогам и кредитам"),
        BotCommand("personal_fin", "Консультант по личным финансам"),
        BotCommand("income_edu", "Консультант по доходам и образованию"),
        BotCommand("prod_mgmt", "Консультант по продакт-менеджменту"),
    ]
    try:
        bot.set_my_commands(commands)
        print("Команды бота успешно настроены")
    except Exception as e:
        print(f"Ошибка при настройке команд: {e}")

# Работа с ассистентами
def get_full_assistant_key(command: str) -> str:
    """Возвращает полный ключ ассистента по команде"""
    command_to_key = {
        'universal': 'universal_expert',
        'cybersecurity': 'cybersecurity',
        'dig_marketing': 'Digital Marketing Consultant',
        'brand_mgmt': 'Brand Management Consultant',
        'biz_create': 'Business Creation Consultant',
        'comm_skills': 'Communication Skills Consultant',
        'stk_trading': 'Stock Market Trading Consultant',
        'crypto': 'Cryptocurrency Consultant',
        'real_estate': 'Real Estate Investment Consultant',
        'startups': 'Startup Investment Consultant',
        'passive_inv': 'Passive Investment Consultant',
        'esg': 'ESG Investment Consultant',
        'forex': 'Forex Market Consultant',
        'finance': 'Digital Finance Consultant',
        'fintech': 'Fintech Consultant',
        'pensions': 'Pension Consultant',
        'insurance': 'Insurance Consultant',
        'tax_credit': 'Tax and Credit Consultant',
        'personal_fin': 'Personal Finance Consultant',
        'income_edu': 'Income and Finance Education Consultant',
        'prod_mgmt': 'Product_management_con',
    }
    return command_to_key.get(command)

@bot.message_handler(commands=[
    'universal', 'cybersecurity', 'dig_marketing', 'brand_mgmt',
    'biz_create', 'comm_skills', 'stk_trading',
    'crypto', 'real_estate', 'startups',
    'passive_inv', 'esg', 'forex',
    'finance', 'fintech', 'pensions',
    'insurance', 'tax_credit', 'personal_fin',
    'income_edu', 'prod_mgmt'
])
def handle_assistant_commands(message):
    command = message.text[1:] 
    log_command(message.from_user.id, command)
    full_key = get_full_assistant_key(command)
    if full_key:
        config = load_assistants_config()
        if full_key in config['assistants']:
            set_user_assistant(message.from_user.id, full_key)
            bot.reply_to(message, f"Выбран ассистент: {config['assistants'][full_key]['name']}")
        else:
            bot.reply_to(message, "Ассистент не найден в конфигурации.")
    else:
        bot.reply_to(message, "Ассистент не найден")

def setup_assistant_handlers():
    """Настраивает обработчики для ассистентов"""
    config = load_assistants_config()
    assistants = config.get("assistants", {})
    for assistant_id, assistant_info in assistants.items():
        @bot.message_handler(func=lambda message, name=assistant_info['name']: message.text == name)
        def handle_assistant(message, assistant_id=assistant_id):
            global current_assistant
            current_assistant = assistant_id
            bot.reply_to(message, f"Текущий ассистент установлен на: {message.text}.")

# Меню и подписка
def create_price_menu() -> types.InlineKeyboardMarkup:
    """Создаёт меню с ценами на подписки"""
    markup = types.InlineKeyboardMarkup(
        keyboard=[
            [
                types.InlineKeyboardButton(
                    text=f"Базовый - {TOKEN_PLANS['basic']['price']}₽",
                    callback_data=f"buy_rate_{TOKEN_PLANS['basic']['price']}"
                ),
                types.InlineKeyboardButton(
                    text=f"Расширенный - {TOKEN_PLANS['advanced']['price']}₽",
                    callback_data=f"buy_rate_{TOKEN_PLANS['advanced']['price']}"
                )
            ],
            [
                types.InlineKeyboardButton(
                    text=f"Премиум - {TOKEN_PLANS['premium']['price']}₽",
                    callback_data=f"buy_rate_{TOKEN_PLANS['premium']['price']}"
                ),
                types.InlineKeyboardButton(
                    text=f"Неограниченный - {TOKEN_PLANS['unlimited']['price']}₽",
                    callback_data=f"buy_rate_{TOKEN_PLANS['unlimited']['price']}"
                )
            ],
        ]
    )
    return markup

load_assistants_config()

REQUIRED_CHANNEL_ID = "@GuidingStarVlog"
SUBSCRIPTION_CHECK_CACHE = {}

def check_user_subscription(user_id):
    """Проверяет, подписан ли пользователь на канал"""
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
    """Создаёт клавиатуру для проверки подписки"""
    keyboard = types.InlineKeyboardMarkup()
    url_button = types.InlineKeyboardButton(text="Подписаться на канал", url="https://t.me/GuidingStarVlog")
    check_button = types.InlineKeyboardButton(text="Я подписался", callback_data="check_subscription")
    keyboard.add(url_button)
    keyboard.add(check_button)
    return keyboard

@bot.callback_query_handler(func=lambda call: call.data == "check_subscription")
def subscription_check_callback(call):
    """Обрабатывает нажатие кнопки 'Я подписался'"""
    user_id = call.from_user.id
    
    # Логирование нажатия
    log_command(user_id, "check_subscription")
    
    if user_id in SUBSCRIPTION_CHECK_CACHE:
        del SUBSCRIPTION_CHECK_CACHE[user_id]
    
    if check_user_subscription(user_id):
        # Устанавливаем ассистента по умолчанию
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

def create_main_menu():
    """Создаёт главное меню бота"""
    keyboard = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    profile_btn = types.KeyboardButton("Мой профиль")
    experts_btn = types.KeyboardButton("Эксперты")
    assistants_btn = types.KeyboardButton("Ассистенты")  # Новая кнопка
    sub_btn = types.KeyboardButton("Купить подписку")
    keyboard.add(profile_btn, experts_btn)
    keyboard.add(assistants_btn, sub_btn)
    return keyboard

def create_assistants_menu():
    """Создаёт инлайн-меню с ассистентами"""
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
    """Создаёт меню выбора экспертов"""
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

# Обработчики кнопок и команд
@bot.message_handler(func=lambda message: message.text == "Ассистенты")
def assistants_button_handler(message):
    """Обрабатывает нажатие кнопки 'Ассистенты'"""
    log_command(message.from_user.id, "Ассистенты")
    bot.send_message(
        message.chat.id,
        "Выберите ассистента:",
        reply_markup=create_assistants_menu()
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("select_assistant_"))
def assistant_callback_handler(call):
    """Обрабатывает выбор ассистента из инлайн-кнопок"""
    assistant_id = call.data.split("_")[-1]
    log_command(call.from_user.id, f"select_assistant_{assistant_id}")
    config = load_assistants_config()
    if assistant_id in config['assistants']:
        set_user_assistant(call.from_user.id, assistant_id)
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"Выбран ассистент: {config['assistants'][assistant_id]['name']}"
        )
    else:
        bot.answer_callback_query(call.id, "Ассистент не найден")

@bot.message_handler(func=lambda message: message.text == "Эксперты")
def experts_button_handler(message):
    log_command(message.from_user.id, "Эксперты")
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
@bot.message_handler(func=lambda message: message.text == "Купить подписку")
def get_pay(message):
    log_command(message.from_user.id, "Купить подписку")
    bot.send_message(
        message.chat.id,
        """🎉 Бесплатно - 30 000 в день на каждого пользователя ✨
💼 Базовый: 149 руб. (200 000 токенов)
📝 Всё необходимое для простых задач.
🚀 Расширенный: 349 руб. (500 000 токенов)
🌈 Для тех, кто ценит больше возможностей.
🌟 Премиум-тариф: 649 руб. (1 200 000 токенов)
💪 Все функции для эффективной работы.
🔓 Неограниченный: 1499 руб. (3 000 000 токенов)
🌍 Абсолютная свобода.
🎁 За приглашенного друга — 100 000 токенов в подарок! 🎊""",
        reply_markup=create_price_menu()
    )

@bot.callback_query_handler(func=lambda callback: callback.data.startswith("buy_rate_"))
def buy_rate(callback):
    price = int(callback.data.split("_")[-1])
    bot.send_invoice(
        callback.message.chat.id,
        title=f"Подписка за {price}",
        description="Описание тарифа",
        invoice_payload="month_subscription",
        provider_token=pay_token,
        currency="RUB",
        start_parameter="test_bot",
        prices=[types.LabeledPrice(label="Тариф", amount=price * 100)]
    )

@bot.pre_checkout_query_handler(func=lambda query: True)
def process_pre_checkout_query(pre_checkout_query):
    bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@bot.message_handler(content_types=['successful_payment'])
def successful_pay(message):
    amount = message.successful_payment.total_amount / 100
    selected_plan = None
    for plan_name, plan_info in TOKEN_PLANS.items():
        if plan_info.get('price', 0) == amount:
            selected_plan = plan_name
            break
    if selected_plan:
        conn = connect_to_db()
        cur = conn.cursor()
        # Рассчитываем дату окончания подписки (30 дней от текущего момента)
        end_date = datetime.datetime.now().date() + datetime.timedelta(days=30)
        cur.execute("""
            UPDATE users 
            SET subscription_plan = %s,
                daily_tokens = daily_tokens + %s,
                subscription_end_date = %s
            WHERE user_id = %s
        """, (selected_plan, TOKEN_PLANS[selected_plan]['tokens'], end_date, message.from_user.id))
        conn.commit()
        cur.close()
        conn.close()
        bot.send_message(
            message.chat.id, 
            f'Оплата прошла успешно!\nНачислено токенов: {TOKEN_PLANS[selected_plan]["tokens"]}\nПодписка активна до: {end_date.strftime("%d.%m.%Y")}'
        )

@bot.message_handler(commands=['new'])
def clear_chat_history(message):
    log_command(message.from_user.id, "new")
    chat_id = message.chat.id
    conn = connect_to_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM chat_history WHERE chat_id = %s", (chat_id,))
    conn.commit()
    cur.close()
    conn.close()
    set_user_assistant(message.from_user.id, 'universal_expert')  # Устанавливаем ассистента по умолчанию
    bot.reply_to(message, "История чата очищена! Можете начать новый диалог с универсальным экспертом.")

# Управление токенами
def check_and_update_tokens(user_id):
    """Проверяет и обновляет токены пользователя"""
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

    # Проверяем, истекла ли подписка
    if current_plan != 'free' and subscription_end_date and current_date > subscription_end_date:
        cur.execute(""" 
            UPDATE users 
            SET subscription_plan = 'free', 
                daily_tokens = %s,
                subscription_end_date = NULL
            WHERE user_id = %s 
        """, (FREE_DAILY_TOKENS, user_id))
        try:
            bot.send_message(
                user_id,
                "Ваша подписка истекла. Вы переведены на бесплатный тариф. Пожалуйста, выберите новый тариф: /pay"
            )
        except telebot.apihelper.ApiTelegramException as e:
            if e.error_code == 403:
                print(f"Пользователь {user_id} заблокировал бота.")
            else:
                print(f"Ошибка API для {user_id}: {e}")
        except Exception as e:
            print(f"Ошибка отправки уведомления {user_id}: {e}")

    # Проверяем токены
    if tokens <= MIN_TOKENS_THRESHOLD:
        if current_plan != 'free':
            cur.execute(""" 
                UPDATE users 
                SET subscription_plan = 'free',
                    subscription_end_date = NULL
                WHERE user_id = %s 
            """, (user_id,))
        if current_date > last_update_date:
            cur.execute(""" 
                UPDATE users 
                SET daily_tokens = %s, 
                    last_token_update = %s 
                WHERE user_id = %s 
            """, (FREE_DAILY_TOKENS, current_date, user_id))
    
    # Отправка предупреждения о низком балансе токенов
    if tokens < 15000 and current_plan != 'free':
        if last_warning_time is None or (datetime.datetime.now() - last_warning_time).total_seconds() > 86400:
            try:
                bot.send_message(
                    user_id,
                    """Ваши токены на исходе! ⏳
Осталось меньше 15 000 токенов, и скоро вам может не хватить для дальнейшего использования. В таком случае вы будете автоматически переведены на бесплатный тариф с ограниченными возможностями.
Чтобы избежать этого, пополните баланс и продолжайте пользоваться всеми функциями без ограничений! 🌟
/pay — Пополнить баланс"""
                )
                cur.execute(""" 
                    UPDATE users 
                    SET last_warning_time = %s 
                    WHERE user_id = %s
                """, (datetime.datetime.now(), user_id))
            except telebot.apihelper.ApiTelegramException as e:
                if e.error_code == 403:
                    print(f"Пользователь {user_id} заблокировал бота.")
                else:
                    print(f"Ошибка API для {user_id}: {e}")
            except Exception as e:
                print(f"Ошибка отправки уведомления {user_id}: {e}")
    
    # Перевод на бесплатный тариф, если токены закончились
    if tokens < 3000:
        if current_plan != 'free':
            cur.execute(""" 
                UPDATE users 
                SET subscription_plan = 'free', 
                    daily_tokens = 0,
                    subscription_end_date = NULL
                WHERE user_id = %s 
            """, (user_id,))
            try:
                bot.send_message(
                    user_id,
                    """Подписка завершена! 🚫
Вы не потеряли токены, но для продолжения доступа выберите новый тариф.
Новый тариф откроет вам ещё больше возможностей и токенов.
/pay — Выбрать новый тариф"""
                )
            except telebot.apihelper.ApiTelegramException as e:
                if e.error_code == 403:
                    print(f"Пользователь {user_id} заблокировал бота.")
                else:
                    print(f"Ошибка API для {user_id}: {e}")
            except Exception as e:
                print(f"Ошибка отправки уведомления {user_id}: {e}")
    
    conn.commit()
    cur.close()
    conn.close()

@bot.message_handler(commands=['profile'])
def show_profile(message):
    """Показывает профиль пользователя"""
    log_command(message.from_user.id, "profile")
    user_id = message.from_user.id
    user_data = load_user_data(user_id)
    
    # Получаем данные о подписке
    conn = connect_to_db()
    cur = conn.cursor()
    cur.execute("SELECT subscription_plan, subscription_end_date FROM users WHERE user_id = %s", (user_id,))
    subscription_data = cur.fetchone()
    cur.close()
    conn.close()

    subscription_plan = subscription_data[0] if subscription_data else 'free'
    subscription_end_date = subscription_data[1] if subscription_data and subscription_data[1] else None

    # Рассчитываем оставшиеся дни подписки
    remaining_days = None
    if subscription_plan != 'free' and subscription_end_date:
        today = datetime.datetime.now().date()
        remaining_days = (subscription_end_date - today).days
        if remaining_days < 0:
            remaining_days = 0  # Если подписка истекла

    # Формируем текст профиля
    profile_text = f"""
ID: {user_id}

Ваш текущий тариф: {subscription_plan.capitalize()}
"""
    if subscription_plan != 'free' and remaining_days is not None:
        profile_text += f"Подписка активна еще {remaining_days} дней\n"

    profile_text += f"""
Оставшаяся квота:
GPT-4o: {user_data['daily_tokens']} символов

🏷 Детали расходов:
💰 Общая сумма: ${user_data['total_spent']:.4f}

📝 Входные токены: {user_data['input_tokens']}
📝 Выходные токены: {user_data['output_tokens']}
👥 Реферальная программа:
Количество приглашенных пользователей: {user_data['invited_users']}
{'🙁 Вы пока не пригласили ни одного друга.' if user_data['invited_users'] == 0 else f'🎉 Вы пригласили: {user_data['invited_users']} друзей'}
{'👤 Вы были приглашены пользователем с ID: ' + str(user_data['referrer_id']) if user_data['referrer_id'] else 'Вы не были приглашены никем.'}
Чтобы пригласить пользователя, отправьте ему ссылку: {generate_referral_link(user_id)}
Чтобы добавить подписку нажмите /pay
"""
    bot.send_message(message.chat.id, profile_text)

# Обновляем список администраторов
ADMIN_IDS = [998107476, 741831495]

@bot.message_handler(commands=['statsadmin12'])
def show_stats_admin(message):
    """Показывает статистику использования команд (только для администратора)"""
    if message.from_user.id not in ADMIN_IDS:
        bot.reply_to(message, "У вас нет прав для просмотра статистики.")
        return
    log_command(message.from_user.id, "statsadmin12")
    week_stats = get_command_stats('week')
    month_stats = get_command_stats('month')
    year_stats = get_command_stats('year')
    command_names = {
    'start': 'Запуск бота (/start)',
    'profile': 'Мой профиль',
    'new': 'Очистить историю чата (/new)',
    'Эксперты': 'Эксперты',
    'expert_1': 'Иван Петров - Финансовый эксперт',
    'expert_2': 'Самир - IT-разработчик',
    'Купить подписку': 'Купить подписку',
    'Ассистенты': 'Ассистенты',  # Добавляем новую команду
    'universal': 'Универсальный эксперт',
    'cybersecurity': 'Консультант по кибербезопасности',
    'dig_marketing': 'Консультант по маркетингу',
    'brand_mgmt': 'Консультант по бренд-менеджменту',
    'biz_create': 'Консультант по открытию бизнеса',
    'comm_skills': 'Консультант по навыкам общения',
    'commskills': 'Консультант по навыкам общения',
    'stk_trading': 'Консультант по фондовому рынку',
    'stktrading': 'Консультант по фондовому рынку',
    'crypto': 'Консультант по криптовалютам',
    'real_estate': 'Консультант по недвижимости',
    'realestate': 'Консультант по недвижимости',
    'startups': 'Консультант по стартапам',
    'passive_inv': 'Консультант по пассивным инвестициям',
    'passiveinv': 'Консультант по пассивным инвестициям',
    'esg': 'Консультант по ESG-инвестициям',
    'forex': 'Консультант по валютным рынкам',
    'finance': 'Консультант по международным финансам',
    'fintech': 'Консультант по финтеху',
    'pensions': 'Консультант по пенсиям',
    'insurance': 'Консультант по страхованию',
    'tax_credit': 'Консультант по налогам и кредитам',
    'taxcredit': 'Консультант по налогам и кредитам',
    'personal_fin': 'Консультант по личным финансам',
    'personalfin': 'Консультант по личным финансам',
    'income_edu': 'Консультант по доходам и образованию',
    'incomeedu': 'Консультант по доходам и образованию',
    'prod_mgmt': 'Консультант по продакт-менеджменту',
    'prodmgmt': 'Консультант по продакт-менеджменту',
    'statsadmin12': 'Статистика (админ)',
    'check_subscription': '✅ Нажатие "Я подписался"'
    }
    
    # Красивое оформление статистики без проблемных символов
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

    # Отправляем без parse_mode или используем HTML
    try:
        bot.reply_to(message, stats_text, parse_mode="Markdown")
    except Exception as e:
        # Если Markdown не работает, отправляем без форматирования
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
    
    # Устанавливаем ассистента по умолчанию
    set_user_assistant(user_id, 'universal_expert')
    
    if not check_user_subscription(user_id):
        bot.send_message(
            message.chat.id,
            """👋 Привет! Это быстро и бесплатно.
Чтобы начать пользоваться ботом, подпишись на наш канал Guiding Star — ты получишь доступ к боту и эксклюзивным материалам по финансам и ИИ.""",
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

@bot.message_handler(commands=['cybersecurity', 'tax_payment_consultant', 'consultant_on_benefits_for_large_families',
                              'financial_literacy_assistant', 'business_creation_consultant', 'economics_consultant'])
def set_assistant(message):
    global current_assistant
    command = message.text[1:]
    log_command(message.from_user.id, command)
    config = load_assistants_config()
    assistants = config.get("assistants", {})
    if command in assistants:
        current_assistant = command
        assistant_name = assistants[command]['name']
        bot.reply_to(message, f"Текущий ассистент установлен на: {current_assistant}.")
    else:
        bot.reply_to(message, "Неизвестный ассистент. Попробуйте выбрать ассистента из меню.")

# Рассылка и действия
def typing(chat_id):
    while True:
        bot.send_chat_action(chat_id, "typing")
        time.sleep(5)

def send_broadcast(message_content, photo=None):
    """Рассылает сообщение всем пользователям"""
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

# Обработка сообщений и документов
@bot.message_handler(func=lambda message: True, content_types=["text"])
def echo_message(message):
    if not check_user_subscription(message.from_user.id):
        bot.send_message(
            message.chat.id,
            """👋 Привет! Это быстро и бесплатно.
Чтобы начать пользоваться ботом, подпишись на наш канал Guiding Star — ты получишь доступ к боту и эксклюзивным материалам по финансам и ИИ.""",
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
    file_info = bot.get_file(message.document.file_id)
    downloaded_file = bot.download_file(file_info.file_path)
    file_extension = message.document.file_name.split('.')[-1].lower()
    try:
        if file_extension == 'txt':
            content = downloaded_file.decode('utf-8')
            input_tokens = len(content)
            if not update_user_tokens(message.chat.id, input_tokens, 0):
                bot.reply_to(message, "У вас закончился лимит токенов. Попробуйте завтра или купите подписку.")
                return
            bot.reply_to(message, process_text_message(content, message.chat.id))
        elif file_extension == 'pdf':
            with io.BytesIO(downloaded_file) as pdf_file:
                content = read_pdf(pdf_file)
                input_tokens = len(content)
                if not update_user_tokens(message.chat.id, input_tokens, 0):
                    bot.reply_to(message, "У вас закончился лимит токенов. Попробуйте завтра или купите подписку.")
                    return
                bot.reply_to(message, process_text_message(content, message.chat.id))
        elif file_extension == 'docx':
            with io.BytesIO(downloaded_file) as docx_file:
                content = read_docx(docx_file)
                input_tokens = len(content)
                if not update_user_tokens(message.chat.id, input_tokens, 0):
                    bot.reply_to(message, "У вас закончился лимит токенов. Попробуйте завтра или купите подписку.")
                    return
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
    input_tokens = len(text)
    if not update_user_tokens(chat_id, input_tokens, 0):
        return "У вас закончился лимит токенов. Попробуйте завтра или купите подписку."
    config = load_assistants_config()
    current_assistant = get_user_assistant(chat_id)
    assistant_settings = config["assistants"].get(current_assistant, {})
    prompt = assistant_settings.get("prompt", "Вы просто бот.")
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

# Обработка голосовых сообщений
import tempfile
from pydub import AudioSegment

@bot.message_handler(content_types=["voice"])
def voice(message):
    """Обрабатывает голосовое сообщение"""
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
        input_tokens = len(recognized_text)
        if not update_user_tokens(message.chat.id, input_tokens, 0):
            bot.reply_to(message, "Лимит токенов исчерпан. Попробуйте завтра или купите подписку.")
            return
        ai_response = process_text_message(recognized_text, message.chat.id)
        bot.reply_to(message, ai_response)
    except Exception as e:
        logging.error(f"Ошибка обработки голосового сообщения: {e}")
        bot.reply_to(message, "Произошла ошибка, попробуйте позже!")

# Обработчик событий и запуск
def handler(event, context):
    message = json.loads(event["body"])
    update = telebot.types.Update.de_json(message)
    allowed_updates=["message", "callback_query", "pre_checkout_query", "buy_rate_149"]
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
        setup_assistant_handlers()  # Вызов функции настройки обработчиков ассистентов
        bot.polling()
    finally:
        if conn:
            conn.close()