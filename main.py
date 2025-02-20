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

print(f"Connecting to DB: {os.getenv('DB_NAME')}, User: {os.getenv('DB_USER')}, Host: {os.getenv('DB_HOST')} password: {os.getenv('DB_PASSWORD')} ")

connect_to_db()


TOKEN_PLANS = {
    "free": {"tokens": 30000},
    "basic": {"price": 149, "tokens": 200000},  # Изменено на 200,000 токенов
    "advanced": {"price": 349, "tokens": 500000},  # Изменено на 500,000 токенов
    "premium": {"price": 649, "tokens": 1200000},  # Изменено на 1,200,000 токенов
    "unlimited": {"price": 1499, "tokens": 3000000},  # Изменено на 3,000,000 токенов
}

MIN_TOKENS_THRESHOLD = 5000  # Порог для обновления токенов
FREE_DAILY_TOKENS = 30000    # Количество бесплатных токенов


logger = telebot.logger
telebot.logger.setLevel(logging.INFO)
pay_token = os.getenv('PAY_TOKEN')
bot = telebot.TeleBot(os.getenv('BOT_TOKEN'), threaded=False)
client = openai.Client(
    api_key=os.getenv('OPENAI_API_KEY'), 
    base_url=os.getenv('OPENAI_BASE_URL')
)



def setup_bot_commands():
    """Настройка команд бота с учетом ограничений Telegram API"""
    commands = [
        telebot.types.BotCommand("start", "Начать работу с ботом"),
        telebot.types.BotCommand("new", "Очистить историю чата"),
        telebot.types.BotCommand("profile", "Посмотреть профиль"),
        telebot.types.BotCommand("pay", "Купить подписку"),
        # Команды для ассистентов (названия должны быть только в нижнем регистре и без пробелов)
        telebot.types.BotCommand("cyber", "Кибербезопасность"),
        telebot.types.BotCommand("tax", "Налоговый консультант"),
        telebot.types.BotCommand("finance", "Финансовая грамотность"),
        telebot.types.BotCommand("crypto", "Криптовалюты"),
        telebot.types.BotCommand("business", "Создание бизнеса"),
        telebot.types.BotCommand("economics", "Экономика"),
        telebot.types.BotCommand("stocks", "Фондовый рынок"),
        telebot.types.BotCommand("loans", "Кредиты и займы"),
        telebot.types.BotCommand("insurance", "Страхование"),
        telebot.types.BotCommand("realestate", "Инвестиции в недвижимость")
    ]
    
    try:
        bot.set_my_commands(commands)
        print("Команды бота успешно настроены")
    except Exception as e:
        print(f"Ошибка при настройке команд бота: {e}")

def get_full_assistant_key(command: str) -> str:
    """Получение полного ключа ассистента по команде"""
    command_to_key = {
        'cyber': 'cybersecurity',
        'tax': 'Tax Payment Consultant',
        'benefits': 'Consultant on benefits for large families',
        'finance': 'Financial Literacy Assistant',
        'crypto': 'investment_cryptocurrency_con',
        'business': 'business creation consultant',
        'economics': 'Economics consultant',
        'stocks': 'Stock Market Trading Consultant',
        'loans': 'Loan and Loan Consultant',
        'insurance': 'insurance consultant',
        'realestate': 'real_estate_investment_con'
    }
    return command_to_key.get(command)

@bot.message_handler(commands=['cyber', 'tax', 'finance', 'crypto', 'business', 
                             'economics', 'stocks', 'loans', 'insurance', 'realestate'])
def select_assistant(message):
    """Обработчик команд выбора ассистента"""
    command = message.text[1:]  # Убираем /
    full_key = get_full_assistant_key(command)

    print(f"[DEBUG] Полный ключ ассистента: {full_key}")  # Отладочное сообщение

    if full_key:
        config = load_assistants_config()  # Убедитесь, что конфигурация загружается корректно
        print(f"[DEBUG] Конфигурация ассистентов: {config}")  # Проверяем содержимое конфигурации

        if full_key in config['assistants']:
            set_user_assistant(message.from_user.id, full_key)
            bot.reply_to(message, f"Выбран ассистент: {config['assistants'][full_key]['name']}")
        else:
            bot.reply_to(message, "Ассистент не найден в конфигурации.")
    else:
        bot.reply_to(message, "Ассистент не найден")


def create_price_menu() -> types.InlineKeyboardMarkup:
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

# Загрузить конфигурацию ассистентов
load_assistants_config()

# Функции для управления подписками
def load_chat_ids():
    try:
        with open("/function/storage/subscribers/subscribers.txt", "r") as file:
            return {line.strip() for line in file.readlines()}
    except FileNotFoundError:
        return set()


def save_chat_id(chat_id):
    with open("//function/storage/subscribers/subscribers.txt", "a") as file:
        file.write(str(chat_id) + "\n")


current_assistant = None  # Переменная для хранения текущего ассистента

# Функция для инициализации обработчиков ассистентов
def setup_assistant_handlers():
    config = load_assistants_config()
    assistants = config.get("assistants", {})

    for assistant_id, assistant_info in assistants.items():
        # Создаем обработчик для каждого ассистента
        @bot.message_handler(func=lambda message, name=assistant_info['name']: message.text == name)
        def handle_assistant(message, assistant_id=assistant_id):
            global current_assistant
            current_assistant = assistant_id  # Устанавливаем ключ ассистента
            bot.reply_to(message, f"Текущий ассистент установлен на: {message.text}.")


# Добавляем обработчик для кнопки профиля
@bot.message_handler(func=lambda message: message.text == "Мой профиль")
def profile_button_handler(message):
    show_profile(message)


@bot.message_handler(commands=["pay"])
@bot.message_handler(func=lambda message: message.text == "Купить подписку")
def get_pay(message) -> None:
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
def buy_rate(callback) -> None:
    price = int(callback.data.split("_")[-1])
    bot.send_invoice(
        callback.message.chat.id,
        title=f"Подписка за {price}",
        description="Описание тарифа",
        invoice_payload="month_subscription",
        provider_token=pay_token,
        currency="RUB",
        start_parameter="test_bot",
        prices=[
            types.LabeledPrice(label="Тариф", amount=price * 100)
        ]
    )


@bot.pre_checkout_query_handler(func=lambda query: True)
def process_pre_checkout_query(pre_checkout_query) -> None:
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
        
        cur.execute("""
            UPDATE users 
            SET subscription_plan = %s,
                daily_tokens = daily_tokens + %s
            WHERE user_id = %s
        """, (selected_plan, TOKEN_PLANS[selected_plan]['tokens'], message.from_user.id))
        
        conn.commit()
        cur.close()
        conn.close()
        
        bot.send_message(
            message.chat.id, 
            f'Оплата прошла успешно!\nНачислено токенов: {TOKEN_PLANS[selected_plan]["tokens"]}'
        )

@bot.message_handler(commands=['new'])
def clear_chat_history(message):
    chat_id = message.chat.id
    
    # Clear chat history from database
    conn = connect_to_db()
    cur = conn.cursor()
    
    cur.execute("""
        DELETE FROM chat_history 
        WHERE chat_id = %s
    """, (chat_id,))
    
    conn.commit()
    cur.close()
    conn.close()
    
    bot.reply_to(message, "История чата очищена! Можете начать новый диалог.")

def check_and_update_tokens(user_id):
    conn = connect_to_db()
    cur = conn.cursor()
    
    # Получаем данные пользователя
    cur.execute(""" 
        SELECT daily_tokens, subscription_plan, last_token_update, last_warning_time 
        FROM users WHERE user_id = %s 
    """, (user_id,))
    user_data = cur.fetchone()
    
    if not user_data:
        return
        
    tokens, current_plan, last_update, last_warning_time = user_data
    current_date = datetime.datetime.now().date()
    
    # Если last_update уже является объектом date, то просто используем его
    if isinstance(last_update, str):
        last_update_date = datetime.datetime.strptime(last_update, '%Y-%m-%d').date()
    else:
        last_update_date = last_update  # Предполагаем, что last_update уже date

    # Проверяем условия для обновления токенов
    if tokens <= MIN_TOKENS_THRESHOLD:
        if current_plan != 'free':
            # Переводим на бесплатный план
            cur.execute(""" 
                UPDATE users 
                SET subscription_plan = 'free' 
                WHERE user_id = %s 
            """, (user_id,))
            
        # Проверяем, прошел ли день с последнего обновления
        if current_date > last_update_date:
            cur.execute(""" 
                UPDATE users 
                SET daily_tokens = %s, 
                    last_token_update = %s 
                WHERE user_id = %s 
            """, (FREE_DAILY_TOKENS, current_date, user_id))
    
    # Inside check_and_update_tokens function
    if tokens < 15000 and current_plan != 'free':  # Added check for non-free plan
        # Проверяем, прошло ли 24 часа с последнего уведомления
        if last_warning_time is None or (datetime.datetime.now() - last_warning_time).total_seconds() > 86400:
            bot.send_message(
                user_id,
                """Ваши токены на исходе! ⏳
    Осталось меньше 15 000 токенов, и скоро вам может не хватить для дальнейшего использования. В таком случае вы будете автоматически переведены на бесплатный тариф с ограниченными возможностями.
    Чтобы избежать этого, пополните баланс и продолжайте пользоваться всеми функциями без ограничений! 🌟
    [Pay — Пополнить баланс]"""
            )
            # Обновляем время последнего уведомления
            cur.execute("""
                UPDATE users 
                SET last_warning_time = %s 
                WHERE user_id = %s
            """, (datetime.datetime.now(), user_id))
    
    # Проверяем, осталось ли меньше 3,000 токенов
    if tokens < 3000:
        if current_plan != 'free':
            # Переводим на бесплатный план
            cur.execute(""" 
                UPDATE users 
                SET subscription_plan = 'free', 
                    daily_tokens = 0 
                WHERE user_id = %s 
            """, (user_id,))
            bot.send_message(
                user_id,
                """Подписка завершена! 🚫
Вы не потеряли токены, но для продолжения доступа выберите новый тариф.
Новый тариф откроет вам ещё больше возможностей и токенов.
[Pay — Выбрать новый тариф]"""
            )
    
    conn.commit()
    cur.close()
    conn.close()




@bot.message_handler(commands=['profile'])
def show_profile(message):
    user_id = message.from_user.id
    user_data = load_user_data(user_id)

    # Убираем проверки на наличие полей
    invited_users = user_data['invited_users']  # Теперь предполагаем, что поле всегда существует
    referrer_id = user_data['referrer_id']  # То же самое здесь

    profile_text = f"""
ID: {user_id}

Ваш текущий тариф: {user_data['subscription_plan'].capitalize()}

Оставшаяся квота:
GPT-4o mini: {user_data['daily_tokens']} символов

🏷 Детали расходов:
💰 Общая сумма: ${user_data['total_spent']:.4f}

📝 Входные токены: {user_data['input_tokens']}
📝 Выходные токены: {user_data['output_tokens']}
👥 Реферальная программа:
Количество приглашенных пользователей: {invited_users}
{'🙁 Вы пока не пригласили ни одного друга.' if invited_users == 0 else f'🎉 Вы пригласили: {invited_users} друзей'}
{'👤 Вы были приглашены пользователем с ID: ' + str(referrer_id) if referrer_id else 'Вы не были приглашены никем.'}
Чтобы пригласть пользователя, отправьте ему ссылку: {generate_referral_link(user_id)}
Чтобы добавить подписку нажмите /pay
"""
    bot.send_message(message.chat.id, profile_text)


# Настраиваем обработчики ассистентов при запуске бота
setup_assistant_handlers()


@bot.message_handler(func=lambda message: message.text == "Отменить")
def cancel_subscription(message):
    # Отправляем сообщение и убираем клавиатуру
    bot.send_message(message.chat.id, "Вы отменили выбор тарифного плана.", reply_markup=types.ReplyKeyboardRemove())

@bot.message_handler(func=lambda message: message.text == "Купить подписку")
@bot.message_handler(commands=['pay'])
def send_subscription_options(message):
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)

    for plan_name, plan_info in TOKEN_PLANS.items():
        price_label = f"{plan_info['price']} ₽"
        keyboard.add(types.KeyboardButton(text=f"{plan_name.capitalize()} - {price_label}"))

    keyboard.add(types.KeyboardButton(text="Отменить"))
    bot.send_message(message.chat.id, "Выберите тарифный план:", reply_markup=create_price_menu())




@bot.pre_checkout_query_handler(func=lambda query: True)
def pre_checkout_handler(pre_checkout_query):
    bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)


@bot.message_handler(content_types=['successful_payment'])
def successful_pay(message):
    bot.send_message(message.chat.id, 'Оплата прошла успешно! Ваша подписка активирована.')


@bot.message_handler(commands=["start", "help"])  # ANCHOR - start
def send_welcome(message):
    profile_btn = types.KeyboardButton("Мой профиль")
    keyboard = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    keyboard.add(profile_btn)

    sub_btn = types.KeyboardButton("Купить подписку")
    keyboard.add(sub_btn)

    # Получаем реферальный ID, если он есть
    referrer_id = message.text.split()[1] if len(message.text.split()) > 1 else None
    user_id = message.from_user.id

    print(f"Start command received. User ID: {user_id}, Referrer ID: {referrer_id}")

    # Проверяем, существует ли пользователь
    user_data = load_user_data(user_id)

    if user_data:
        if referrer_id:
            bot.reply_to(message, "Вы уже зарегистрированы. Нельзя использовать реферальную ссылку.")
        else:
            # Если пользователь уже есть, просто отправляем приветственное сообщение
            bot.send_message(message.chat.id, "Добро пожаловать обратно!")
    else:
        # Если пользователя не найдено, и есть реферальный ID, то создаем нового пользователя
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

        # Создаем нового пользователя без реферала
        user_data = create_default_user(user_id, referrer_id)

        # Отправляем приветственное сообщение
        bot.send_message(message.chat.id, "Вы успешно зарегистрированы!")
        
    # Создаем и отправляем клавиатуру с опциями
    bot.send_message(message.chat.id, """Привет! Я — Финни
🏆 Я единственный бот в Telegram с полноценной персонализированной поддержкой в мире финансов.
                     
🎯 Моя цель — помочь тебе стать финансово грамотным, независимо от твоего возраста или уровня знаний.
                     
Выберите интересующего вас ассистента из меню команд:
                     
📊 /finance - Финансовая грамотность
💰 /crypto - Инвестиции в криптовалюту
📈 /stocks - Инвестирование на фондовом рынке
🏡 /realestate - Инвестирование в недвижимость
💡 /business - Создание бизнеса
💸 /loans - Кредиты и займы
🔐 /cyber - Кибербезопасность
🏦 /insurance - Страхование
💰 /economics - Экономика и финансы
                     
📚 Персонализированное обучение: Я адаптирую материал в зависимости от твоего уровня знаний. Если ты новичок, не переживай — я объясню все доступно и шаг за шагом.
🔍 Как я работаю? После каждого ответа я предложу тебе 3 возможных опции для дальнейшего изучения. Это поможет двигаться по пути финансовой грамотности, не запутываясь в сложных терминах.
🤝 Твоя помощь в обучении: Если нужно, можете задать дополнительные вопросы, мои контакты в шапке профиля""", reply_markup=keyboard)

@bot.message_handler(commands=['referral'])
def send_referral_link(message):
    user_id = message.from_user.id
    referral_link = generate_referral_link(user_id)  # Генерируем реферальную ссылку
    bot.reply_to(message, f"Ваша реферальная ссылка: {referral_link}")  # Отправляем сообщение с реферальной ссылкой


@bot.message_handler(commands=['cybersecurity', 'tax_payment_consultant', 'consultant_on_benefits_for_large_families',
                               'financial_literacy_assistant', 'business_creation_consultant', 'economics_consultant'])
def set_assistant(message):
    global current_assistant  # Убедитесь, что используете глобальную переменную

    command = message.text[1:]  # Убираем '/'
    config = load_assistants_config()
    assistants = config.get("assistants", {})

    if command in assistants:
        current_assistant = command
        assistant_name = assistants[command]['name']  # Получаем имя ассистента
        bot.reply_to(message, f"Текущий ассистент установлен на: {current_assistant}.")
    else:
        bot.reply_to(message, "Неизвестный ассистент. Попробуйте выбрать ассистента из меню.")


def typing(chat_id):
    while True:
        bot.send_chat_action(chat_id, "typing")
        time.sleep(5)


def send_broadcast(message_content, photo=None):
    conn = connect_to_db()
    cur = conn.cursor()
    
    # Получаем всех пользователей из базы данных
    cur.execute("SELECT user_id FROM users")
    users = cur.fetchall()
    
    for user in users:
        try:
            if photo:
                # Отправляем изображение с подписью
                bot.send_photo(user[0], photo, caption=message_content)
            else:
                # Отправляем только текст
                bot.send_message(user[0], message_content)
        except Exception as e:
            print(f"Не удалось отправить сообщение пользователю {user[0]}: {e}")
            continue
            
    cur.close()
    conn.close()


@bot.message_handler(commands=["broadcast"])
def broadcast(message):
    if message.from_user.id == 998107476:
        msg = bot.reply_to(message, "Отправьте изображение с подписью или просто текст для рассылки:")
        bot.register_next_step_handler(msg, process_broadcast)
    else:
        bot.reply_to(message, "У вас нет прав на отправку рассылки.")


def process_broadcast(message):
    if message.content_type == 'photo':
        # Получаем последнее (самое качественное) изображение
        photo = message.photo[-1].file_id
        # Получаем подпись к изображению
        caption = message.caption if message.caption else ""
        send_broadcast(caption, photo=photo)
    else:
        # Обычная текстовая рассылка
        send_broadcast(message.text)

    bot.reply_to(message, "Рассылка успешно завершена!")


# Регистрируем обработчик для фото
@bot.message_handler(content_types=['photo'])
def handle_broadcast_photo(message):
    if message.from_user.id == 998107476 and message.caption and message.caption.startswith('/broadcast'):
        photo = message.photo[-1].file_id
        caption = message.caption.replace('/broadcast', '').strip()
        send_broadcast(caption, photo=photo)
        bot.reply_to(message, "Рассылка с изображением успешно завершена!")



@bot.message_handler(func=lambda message: True, content_types=["text"])
def echo_message(message):
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
            # Считаем токены из текстового файла
            input_tokens = len(content)
            if not update_user_tokens(message.chat.id, input_tokens, 0):
                bot.reply_to(message, "У вас закончился дневной лимит токенов. Попробуйте завтра или приобретите подписку.")
                return
            bot.reply_to(message, process_text_message(content, message.chat.id))

        elif file_extension == 'pdf':
            # Использование BytesIO для PDF
            with io.BytesIO(downloaded_file) as pdf_file:
                content = read_pdf(pdf_file)
                # Считаем токены из PDF
                input_tokens = len(content)
                if not update_user_tokens(message.chat.id, input_tokens, 0):
                    bot.reply_to(message, "У вас закончился дневной лимит токенов. Попробуйте завтра или приобретите подписку.")
                    return
                bot.reply_to(message, process_text_message(content, message.chat.id))

        elif file_extension == 'docx':
            # Используем BytesIO для DOCX
            with io.BytesIO(downloaded_file) as docx_file:
                content = read_docx(docx_file)
                # Считаем токены из DOCX
                input_tokens = len(content)
                if not update_user_tokens(message.chat.id, input_tokens, 0):
                    bot.reply_to(message, "У вас закончился дневной лимит токенов. Попробуйте завтра или приобретите подписку.")
                    return
                bot.reply_to(message, process_text_message(content, message.chat.id))

        else:
            bot.reply_to(message, "Неверный формат файла. Поддерживаются форматы: .txt, .pdf, .docx.")

    except Exception as e:
        bot.reply_to(message, f"Произошла ошибка при чтении файла: {e}")



def read_pdf(file):
    content = []
    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:  # Добавляем текст только если он не пустой
                content.append(text)
    return "\n".join(content)


def read_docx(file):
    document = docx.Document(file)
    content = []
    for para in document.paragraphs:
        content.append(para.text)
    return "\n".join(content)




def update_user_tokens(user_id, input_tokens, output_tokens):
    check_and_update_tokens(user_id)  # Проверяем и обновляем токены если нужно
    
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
        return "У вас закончился дневной лимит токенов. Попробуйте завтра или приобретите подписку."

    config = load_assistants_config()
    current_assistant = get_user_assistant(chat_id)
    assistant_settings = config["assistants"].get(current_assistant, {})
    prompt = assistant_settings.get("prompt", "Вы просто бот.")
    input_text = f"{prompt}\n\nUser: {text}\nAssistant:"

    # Получаем историю из базы данных
    history = get_chat_history(chat_id)
    history.append({"role": "user", "content": input_text})

    try:
        chat_completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=history
        )

        ai_response = chat_completion.choices[0].message.content
        output_tokens = len(ai_response)

        if not update_user_tokens(chat_id, 0, output_tokens):
            return "Ответ слишком длинный для вашего оставшегося лимита токенов."

        # Обновляем общую сумму расходов
        user_data = load_user_data(chat_id)
        user_data['total_spent'] += (input_tokens + output_tokens) * 0.000001
        save_user_data(user_data)

        # Сохраняем сообщения в базу данных
        store_message_in_db(chat_id, "user", input_text)
        store_message_in_db(chat_id, "assistant", ai_response)

        return ai_response

    except Exception as e:
        return f"Произошла ошибка: {str(e)}"




@bot.message_handler(func=lambda msg: msg.voice.mime_type == "audio/ogg", content_types=["voice"])
def voice(message):
    """Обрабатывает полученное голосовое сообщение."""
    file_info = bot.get_file(message.voice.file_id)
    downloaded_file = bot.download_file(file_info.file_path)

    try:
        # Отправляем файл на распознавание с помощью Whisper
        response = client.audio.transcriptions.create(
            file=("file.ogg", downloaded_file, "audio/ogg"),
            model="whisper-1",
        )

        # Получаем распознанный текст
        recognized_text = response.text.strip()

        # Проверка длины распознанного текста
        if len(recognized_text) > 1000000:
            bot.reply_to(message, "Предупреждение: распознанный текст слишком длинный, попробуйте сократить его.")
            return

        if not recognized_text:
            bot.reply_to(message, "Предупреждение: распознанный текст неразборчив. Пожалуйста, попробуйте снова.")
            return

        # Считаем токены из распознанного текста
        input_tokens = len(recognized_text)
        if not update_user_tokens(message.chat.id, input_tokens, 0):
            bot.reply_to(message, "У вас закончился дневной лимит токенов. Попробуйте завтра или приобретите подписку.")
            return

        # Обрабатываем текстовое сообщение с учётом текущего ассистента
        ai_response = process_text_message(recognized_text, message.chat.id)
        bot.reply_to(message, ai_response)

    except Exception as e:
        logging.error(f"Ошибка при обработке голосового сообщения: {e}")
        bot.reply_to(message, "Произошла ошибка, попробуйте позже!")


def handler(event, context):
    message = json.loads(event["body"])
    update = telebot.types.Update.de_json(message)
    allowed_updates=["message", "callback_query", "pre_checkout_query", "buy_rate_149"]
    # Удаляем условие проверки имени пользователя
    if update.message is not None:
        try:
            bot.process_new_updates([update])
        except Exception as e:
            print(e)

    return {
        "statusCode": 200,
        "body": "ok",
    }


if __name__ == "__main__":
    print("Bot started")
    
    conn = connect_to_db()
    
    try:
        check_and_create_columns(conn)  # Создаем таблицы
        with conn.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM assistants;")
            count = cursor.fetchone()[0]
        
        if count == 0:
            print("Таблица 'assistants' пуста. Вставляем начальные данные.")
            insert_initial_data(conn)

        assistants_config = load_assistants_config()  # Загружаем конфигурацию
        # print(f"Загруженные ассистенты: {assistants_config}")

        # Здесь можно дополнительно проверить кэш в Redis
        cached_config = r.get('assistants_config')
        setup_bot_commands()  # Настраиваем команды бота
        bot.polling()  # Запускаем бота для опроса
    finally:
        if conn:
            conn.close()
