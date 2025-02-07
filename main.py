import logging
import telebot
import os
import openai
import json
import boto3

import time
import io
from telebot import types
import docx
import PyPDF2
import pdfplumber
import datetime
from database import *
import psycopg2

TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")
PROXY_API_KEY = os.environ.get("PROXY_API_KEY")
YANDEX_KEY_ID = os.environ.get("YANDEX_KEY_ID")
YANDEX_KEY_SECRET = os.environ.get("YANDEX_KEY_SECRET")
YANDEX_BUCKET = os.environ.get("YANDEX_BUCKET")
YOUR_ADMIN_ID = os.environ.get("YOUR_ADMIN_ID")
YOUR_PROVIDER_TOKEN = os.environ.get("YOUR_PROVIDER_TOKEN")


# Функция для подключения к базе данных PostgreSQL
connect_to_db()
insert_initial_data(connect_to_db())

SUBSCRIPTION_PLANS = {
    "free": {"price": 0, "tokens": 20000},  # Бесплатный план
    "basic": {"price": 149, "tokens": 300000},  # Базовый план
    "advanced": {"price": 499, "tokens": 600000},  # Расширенный план
    "premium": {"price": 899, "tokens": 1000000},  # Премиум план
    "unlimited": {"price": 1599, "tokens": 5000000},  # Неограниченный план
}

logger = telebot.logger
telebot.logger.setLevel(logging.INFO)
pay_token = "live_I1XQU2cDLDo9p_QN4nu5LLn1xC0yfZTUByVckEsRjJg"

bot = telebot.TeleBot("7738522562:AAHMDy0fMzWgRIjMgGbPA9-OiKtvnINILOg", threaded=False)
client = openai.Client(api_key="sk-IdmJMXNU1gZPbd1Isu38a1IqFVW3jZQ0", base_url="https://api.proxyapi.ru/openai/v1")


def create_price_menu() -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup(
        keyboard=[
            [
                types.InlineKeyboardButton(
                    text="Базовый - 149p",
                    callback_data="buy_rate_149"
                ),
                types.InlineKeyboardButton(
                    text="Расширенный - 499p",
                    callback_data="buy_rate_499"
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="Премиум - 899p",
                    callback_data="buy_rate_899"
                ),
                types.InlineKeyboardButton(
                    text="Неограниченный - 1599p",
                    callback_data="buy_rate_1599"
                )
            ],
        ]
    )
    return markup


def get_s3_client():
    session = boto3.session.Session(
        aws_access_key_id=YANDEX_KEY_ID, aws_secret_access_key=YANDEX_KEY_SECRET
    )
    return session.client(
        service_name="s3", endpoint_url="https://storage.yandexcloud.net"
    )


CONFIG_FILE = 'assistants_config.json'


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
        "Выберите тарифный план:",
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
        provider_token="381764678:TEST:106386",
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
    # Отправляем сообщение только если это успешный платеж
    bot.send_message(message.chat.id, 'Оплата прошла успешно! Ваша подписка активирована.')



@bot.message_handler(commands=['profile'])
def show_profile(message):
    user_id = message.from_user.id
    user_data = load_user_data(user_id)

    # Убираем проверки на наличие полей
    invited_users = user_data['invited_users']  # Теперь предполагаем, что поле всегда существует
    referrer_id = user_data['referrer_id']  # То же самое здесь

    profile_text = f"""
ID: {user_id}

Ваш текущий тариф: Free

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


@bot.message_handler(commands=['pay'])
def send_subscription_options(message):
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)

    for plan_name, plan_info in SUBSCRIPTION_PLANS.items():
        price_label = f"{plan_info['price']} ₽"
        keyboard.add(types.KeyboardButton(text=f"{plan_name.capitalize()} - {price_label}"))

    keyboard.add(types.KeyboardButton(text="Отменить"))
    bot.send_message(message.chat.id, "Выберите тарифный план:", reply_markup=create_price_menu())


def setup_subscription_handlers():
    for plan_name, plan_info in SUBSCRIPTION_PLANS.items():
        price_label = f"{plan_info['price']} ₽"
        plan_button_text = f"{plan_name.capitalize()} - {price_label}"

        @bot.message_handler(func=lambda message, text=plan_button_text: message.text == text)
        def handle_subscription(message, plan=plan_name):
            price = SUBSCRIPTION_PLANS[plan]['price'] * 100
            user_id = message.from_user.id

            bot.send_invoice(
                chat_id=message.chat.id,
                title=f"Подписка на {plan.capitalize()}",
                description=f"Оплата подписки на тарифный план {plan.capitalize()}.",
                provider_token=YOUR_PROVIDER_TOKEN,
                currency='RUB',
                prices=[types.LabeledPrice(label=plan.capitalize(), amount=price)],
                start_parameter=f'{plan}_subscription',
                invoice_payload=f'Оплатил пользователь {user_id}'
            )


setup_subscription_handlers()


@bot.pre_checkout_query_handler(func=lambda query: True)
def pre_checkout_handler(pre_checkout_query):
    bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)


@bot.message_handler(content_types=['successful_payment'])
def successful_pay(message):
    bot.send_message(message.chat.id, 'Оплата прошла успешно! Ваша подписка активирована.')


@bot.message_handler(commands=["start", "help"])
def send_welcome(message):
    # Получаем реферальный ID, если он есть
    referrer_id = message.text.split()[1] if len(message.text.split()) > 1 else None
    user_id = message.from_user.id

    print(f"Start command received. User ID: {user_id}, Referrer ID: {referrer_id}")

    # Проверяем, существует ли пользователь
    user_data = load_user_data(user_id)

    if referrer_id:
        if user_data:
            bot.reply_to(message, "Вы уже зарегистрированы. Нельзя использовать реферальную ссылку.")
            return  # Если пользователь уже существует, прерываем выполнение

        try:
            referrer_id = int(referrer_id)  # Преобразуем строку в целое число
            referrer_data = load_user_data(referrer_id)

            if referrer_data:
                # Увеличиваем количество приглашенных пользователей у реферера
                referrer_data['invited_users'] = referrer_data.get('invited_users', 0) + 1
                # Повышаем квоту символов реферера
                referrer_data['daily_tokens'] += 100000  # Например, добавляем 100000 символов
                save_user_data(referrer_data)

        except ValueError:
            print("Invalid referrer ID format")

    # # Создаем нового пользователя, если он еще не зарегистрирован
    # if user_data is None:  # Если пользователь не найден
    #     user_data = create_default_user(user_id)

    #     # Обновляем referrer_id для нового пользователя
    #     if referrer_id:
    #         user_data['referrer_id'] = referrer_id  # Устанавливаем referrer_id
    #     save_user_data(user_data)

    config = load_assistants_config()
    assistants = config.get("assistants", {})

    # Создаем клавиатуру с кнопками
    keyboard = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)

    # Добавляем кнопку профиля первой
    profile_btn = types.KeyboardButton("Мой профиль")
    keyboard.add(profile_btn)

    # Создаем клавиатуру с кнопками для ассистентов
    config = load_assistants_config()
    assistants = config.get("assistants", {})

    for assistant_id, assistant_info in assistants.items():
        button = types.KeyboardButton(assistant_info['name'])  # Имя ассистента
        keyboard.add(button)

    # Отправляем приветственное сообщение с клавиатурой
    bot.send_message(message.chat.id, """Привет! Я — Финни

🏆 Я единственный бот в Telegram с полноценной персонализированной поддержкой в мире финансов. 

🎯 Моя цель — помочь тебе стать финансово грамотным, независимо от твоего возраста или уровня знаний.

Вот как мы можем начать:

Выбор направления обучения: Я помогу тебе выбрать тему, которая интересует или нуждается в улучшении. Выбирайте один из вариантов:

📊 Финансовая грамотность
💰Инвестиции в криптовалюту 
📈 Инвестирование на фондовом рынке
🏡 Инвестирование в недвижимость
💡 Создание бизнеса
💸 Кредиты и займы
🔐 Кибербезопасность
🏦 Страхование
💰 Экономика и финансы

📚 Персонализированное обучение: Я адаптирую материал в зависимости от твоего уровня знаний. Если ты новичок, не переживай — я объясню все доступно и шаг за шагом.
🔍 Как я работаю? После каждого ответа я предложу тебе 3 возможных опции для дальнейшего изучения. Это поможет двигаться по пути финансовой грамотности, не запутываясь в сложных терминах.
🤝 Твоя помощь в обучении: Если нужно, можете задать дополнительные вопросы, мои контакты в шапке профиля""",
                     reply_markup=keyboard)


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
    with open("/function/storage/users/users.json", "r") as file:
        users = json.load(file)

    for user in users:
        try:
            if photo:
                # Отправляем изображение с подписью
                bot.send_photo(user['user_id'], photo, caption=message_content)
            else:
                # Отправляем только текст
                bot.send_message(user['user_id'], message_content)
        except Exception as e:
            print(f"Не удалось отправить сообщение пользователю {user['user_id']}: {e}")
            continue


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


@bot.message_handler(commands=["new"])
def clear_history(message):
    clear_history_for_chat(message.chat.id)
    bot.reply_to(message, "История чата очищена!")


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
            bot.reply_to(message, process_text_message(content, message.chat.id))

        elif file_extension == 'pdf':
            # Использование BytesIO для PDF
            with io.BytesIO(downloaded_file) as pdf_file:
                content = read_pdf(pdf_file)
                bot.reply_to(message, process_text_message(content, message.chat.id))

        elif file_extension == 'docx':
            # Используем BytesIO для DOCX
            with io.BytesIO(downloaded_file) as docx_file:
                content = read_docx(docx_file)
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
    user_data = load_user_data(user_id)  # Загружаем данные пользователя

    # Проверяем нужно ли обновить дневной лимит
    last_reset = datetime.datetime.strptime(user_data['last_reset'], '%Y-%m-%d').date()
    if datetime.datetime.now().date() > last_reset:
        user_data['daily_tokens'] = 20000  # Обновляем дневной лимит
        user_data['last_reset'] = str(datetime.datetime.now().date())

    # Вычитаем токены
    new_tokens = user_data['daily_tokens'] - (input_tokens + output_tokens)
    if new_tokens < 0:
        return False

    user_data['daily_tokens'] = new_tokens
    user_data['input_tokens'] += input_tokens  # Увеличиваем входные токены
    user_data['output_tokens'] += output_tokens  # Увеличиваем выходные токены
    save_user_data(user_data)  # Сохраняем обновленные данные пользователя
    return True


def generate_referral_link(user_id):
    return f"https://t.me/filling33_bot?start={user_id}"

def process_text_message(text, chat_id) -> str:
    input_tokens = len(text)

    if not update_user_tokens(chat_id, input_tokens, 0):
        return "У вас закончился дневной лимит токенов. Попробуйте завтра или приобретите подписку."

    global current_assistant
    config = load_assistants_config()
    assistant_settings = config["assistants"].get(current_assistant, {})
    prompt = assistant_settings.get("prompt", "Вы просто бот.")
    input_text = f"{prompt}\n\nUser: {text}\nAssistant:"

    # # Чтение текущей истории чата
    # s3client = get_s3_client()
    history = []
    # try:
    #     history_object_response = s3client.get_object(
    #         Bucket=YANDEX_BUCKET, Key=f"{chat_id}.json"
    #     )
    #     history = json.loads(history_object_response["Body"].read())
    # except:
    #     pass

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
        user_data['total_spent'] += (input_tokens + output_tokens) * 0.000001  # Примерная стоимость за токен
        save_user_data(user_data)

        history.append({"role": "assistant", "content": ai_response})

        # # Сохраняем текущую историю чата
        # s3client.put_object(
        #     Bucket=YANDEX_BUCKET,
        #     Key=f"{chat_id}.json",
        #     Body=json.dumps(history),
        # )

        return ai_response

    except Exception as e:
        return f"Произошла ошибка: {str(e)}"


def clear_history_for_chat(chat_id):
    try:
        s3client = get_s3_client()
        s3client.put_object(
            Bucket=YANDEX_BUCKET,
            Key=f"{chat_id}.json",
            Body=json.dumps([]),
        )
    except:
        pass


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
    print("Bot starte1")
    
    bot.polling()
    conn = connect_to_db()
    # Не забудьте закрыть соединение после использования
    if conn:
        conn.close()



print("Bot started2")      