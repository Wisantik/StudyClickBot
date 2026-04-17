import json
import psycopg2
from psycopg2 import OperationalError
import os
from dotenv import load_dotenv
import redis
import time

from yookassa import Configuration, Payment


load_dotenv()

def connect_to_db():
    max_retries = 5
    retry_delay = 5  # секунды
    for attempt in range(max_retries):
        try:
            conn = psycopg2.connect(
                dbname=os.getenv('DB_NAME'),
                user=os.getenv('DB_USER'),
                password=os.getenv('DB_PASSWORD'),
                host=os.getenv('DB_HOST'),
                port=os.getenv('DB_PORT', '5432')
            )
            return conn
        except OperationalError as e:
            print(f"[ERROR] Ошибка подключения к базе данных (попытка {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
            else:
                raise Exception("Не удалось подключиться к базе данных после нескольких попыток")

def create_subscription_tables(connection):
    try:
        with connection.cursor() as cursor:
            # Добавляем поля в таблицу users
            cursor.execute("""
                ALTER TABLE IF EXISTS users
                ADD COLUMN IF NOT EXISTS subscription_plan VARCHAR(50),
                ADD COLUMN IF NOT EXISTS subscription_start_date TIMESTAMP,
                ADD COLUMN IF NOT EXISTS payment_method_id VARCHAR(255);
            """)
            # Таблица для хранения платежей
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS payments (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    payment_id VARCHAR(255) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            connection.commit()
    except Exception as e:
        print(f"Ошибка при создании таблиц подписок: {e}")
        connection.rollback()

def save_payment_id_for_user(user_id, payment_id):
    conn = connect_to_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO payments (user_id, payment_id, created_at) VALUES (%s, %s, NOW())",
                (user_id, payment_id)
            )
            conn.commit()
            print(f"payment_id {payment_id} сохранён для user_id {user_id}")
    except Exception as e:
        print(f"Ошибка при сохранении payment_id: {e}")
    finally:
        conn.close()

def save_payment_method_for_user(user_id, payment_method_id):
    conn = connect_to_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "UPDATE users SET payment_method_id = %s WHERE user_id = %s",
                (payment_method_id, user_id)
            )
            conn.commit()
            print(f"payment_method_id {payment_method_id} сохранён для user_id {user_id}")
    except Exception as e:
        print(f"Ошибка при сохранении payment_method_id: {e}")
    finally:
        conn.close()

def get_payment_method_for_user(user_id):
    conn = connect_to_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT payment_method_id FROM users WHERE user_id = %s", (user_id,))
            result = cursor.fetchone()
            return result[0] if result else None
    except Exception as e:
        print(f"Ошибка при получении payment_method_id: {e}")
        return None
    finally:
        conn.close()


def refresh_assistants_cache(connection):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT assistant_key, name, prompt FROM assistants;")
            rows = cursor.fetchall()
            assistants = {row[0]: {"name": row[1], "prompt": row[2]} for row in rows}

        r.set("assistants_config", json.dumps({"assistants": assistants}, ensure_ascii=False))
        print("[INFO] Конфигурация ассистентов обновлена в Redis.")
    except Exception as e:
        print(f"[ERROR] Ошибка при обновлении кэша ассистентов: {e}")

def set_default_assistant(connection, assistant_key):
    with connection.cursor() as cursor:
        cursor.execute("""
            UPDATE users
            SET current_assistant = %s
            WHERE current_assistant IS NULL OR current_assistant = 'default'
        """, (assistant_key,))
        connection.commit()

r = redis.Redis(
    host='redis',
    port=6379,
    db=0,
    password=os.getenv('REDIS_PASSWORD'),
    decode_responses=True
)

def check_assistants_in_database(connection):
    with connection.cursor() as cursor:
        cursor.execute("SELECT assistant_key, name FROM assistants;")
        assistants = cursor.fetchall()
        print("Ассистенты в базе данных:")
        for assistant in assistants:
            print(f"Ключ: {assistant[0]}, Имя: {assistant[1]}")

def set_user_assistant(user_id: int, assistant_key: str):
    print(f"[INFO] Устанавливаем ассистента для пользователя {user_id}: {assistant_key}")
    r.set(user_id, assistant_key)
    conn = connect_to_db()
    if conn is None:
        print("[ERROR] Не удалось подключиться к базе данных.")
        return
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE users
                SET current_assistant = %s
                WHERE user_id = %s
            """, (assistant_key, user_id))
            conn.commit()
    except Exception as e:
        print(f"[ERROR] Ошибка при обновлении ассистента в базе данных: {e}")
    finally:
        conn.close()

def get_user_assistant(user_id: int, user_text: str | None = None) -> str:

    assistant_key = r.get(user_id)
    if assistant_key:
        return assistant_key

    conn = connect_to_db()
    if conn is None:
        print("[ERROR] Не удалось подключиться к базе данных.")
        return None

    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT current_assistant FROM users WHERE user_id = %s",
                (user_id,)
            )
            result = cur.fetchone()
            if result:
                return result[0]
            else:
                print(
                    f"[WARNING] Ассистент для пользователя {user_id} "
                    f"не найден в базе данных."
                )
                return None
    except Exception as e:
        print(
            f"[ERROR] Ошибка при получении ассистента из базы данных: {e}"
        )
        return None
    finally:
        conn.close()


def create_experts_table(connection):
    with connection.cursor() as cursor:
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS experts (
            expert_id SERIAL PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            specialization VARCHAR(100) NOT NULL,
            description TEXT NOT NULL,
            photo_url VARCHAR(255),
            telegram_username VARCHAR(100),
            contact_info TEXT,
            is_available BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        connection.commit()

def insert_expert(connection, name, specialization, description, photo_url=None, telegram_username=None, contact_info=None):
    with connection.cursor() as cursor:
        cursor.execute("""
        INSERT INTO experts (name, specialization, description, photo_url, telegram_username, contact_info)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING expert_id;
        """, (name, specialization, description, photo_url, telegram_username, contact_info))
        expert_id = cursor.fetchone()[0]
        connection.commit()
        return expert_id
    
def get_all_experts(connection):
    with connection.cursor() as cursor:
        cursor.execute("""
        SELECT expert_id, name, specialization, description, photo_url, telegram_username, contact_info, is_available
        FROM experts
        WHERE is_available = TRUE
        ORDER BY name;
        """)
        return cursor.fetchall()
    
def get_expert_by_id(connection, expert_id):
    with connection.cursor() as cursor:
        cursor.execute("""
        SELECT expert_id, name, specialization, description, photo_url, telegram_username, contact_info, is_available
        FROM experts
        WHERE expert_id = %s;
        """, (expert_id,))
        return cursor.fetchone()

def insert_initial_experts(connection):
    experts = [
        {
            "name": "Иван Петров",
            "specialization": "Финансовый эксперт",
            "description": """Я — аттестованный финансовый эксперт, квалифицированный инвестор, трейдер на фондовом и криптовалютном рынке.

📚 Образование: высшее техническое и юридическое.
💼 В инвестициях — с 2018 года. Несколько раз достигал доходности свыше +100% годовых.

🔧 За время работы:

Провёл аудит более 450 финансовых планов
Обучил свыше 5000 человек
Преподавал на курсах «Метод» и «Инвестиции доступны всем»

💬 Ко мне можно обращаться по вопросам:

📊 Фондовый рынок
инвестиции в акции и облигации
торговля на российских и зарубежных площадках
составление индивидуальных портфелей

🌍 Международные финансы
вложения в зарубежные активы (акции, облигации, недвижимость)
анализ валютных рисков и курсов
выбор стран и рынков для инвестиций
налоговое и правовое сопровождение
трансграничные переводы и структура капитала

₿ Криптовалюты
трейдинг и среднесрочные инвестиции
подбор надёжных проектов и инструментов
управление рисками и безопасностью активов""",
            "photo_url": "https://ltdfoto.ru/images/2025/03/29/image.png",
            "telegram_username": "@rmmusin",
            "contact_info": "@rmmusin"
        },
        {
            "name": "Самир",
            "specialization": "IT-разработчик",
            "description": """Здравствуйте! Меня зовут Самир.

Я занимаюсь IT-разработкой более 15 лет, из них 10 лет специализируюсь на создании сайтов и систем автоматизации бизнеса.

Работаю официально:
— Индивидуальный предприниматель с 2016 года
— Также зарегистрировано ООО с НДС

Услуги:
Разработка сайтов ( Wp, Modx, Bitrix, Lravel) — от визиток до CRM-платформ, с учётом UX и SEO.
Чат-боты — автоматизация общения, интеграция с CRM.
SEO — выведение сайта в ТОП по ключевым запросам.
Контекстная реклама — быстрый трафик с оптимальными бюджетами.""",
            "photo_url": "https://ltdfoto.ru/images/2025/03/29/imagee25b64cc45bb0022.png",
            "telegram_username": "@antartus",
            "contact_info": "@antartus"
        },
    ]
    with connection.cursor() as cursor:
        cursor.execute("TRUNCATE TABLE experts RESTART IDENTITY CASCADE;")
        connection.commit()
    for expert in experts:
        insert_expert(
            connection,
            expert["name"],
            expert["specialization"],
            expert["description"],
            expert["photo_url"],
            expert["telegram_username"],
            expert["contact_info"]
        )

def check_and_create_columns(connection):
    with connection.cursor() as cursor:
        create_assistants_table = """
        CREATE TABLE IF NOT EXISTS public.assistants (
            id SERIAL PRIMARY KEY,
            assistant_key VARCHAR(100) NOT NULL UNIQUE,
            name VARCHAR(200) NOT NULL,
            prompt TEXT NOT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );
        """
        create_chat_history_table = """
        CREATE TABLE IF NOT EXISTS public.chat_history (
            id SERIAL PRIMARY KEY,
            chat_id BIGINT NOT NULL,
            role VARCHAR(50) NOT NULL CHECK (role IN ('user', 'assistant')),
            content TEXT NOT NULL,
            "timestamp" TIMESTAMP WITHOUT TIME ZONE NOT NULL
        );
        """
        create_users_table = """
        CREATE TABLE IF NOT EXISTS public.users (
            user_id BIGINT PRIMARY KEY,
            daily_tokens INTEGER NOT NULL,
            last_reset DATE NOT NULL,
            total_spent NUMERIC(10, 4) DEFAULT 0,
            referral_count INTEGER DEFAULT 0,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            invited_users INTEGER DEFAULT 0,
            referrer_id BIGINT,
            subscription_plan VARCHAR(50) DEFAULT 'free',
            created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            current_assistant VARCHAR(255),
            last_token_update DATE DEFAULT CURRENT_DATE,
            last_warning_time TIMESTAMP WITHOUT TIME ZONE,
            subscription_start_date DATE,
            subscription_end_date DATE,
            trial_used BOOLEAN DEFAULT FALSE,
            auto_renewal BOOLEAN DEFAULT TRUE,
            web_search_enabled BOOLEAN DEFAULT FALSE,
            language VARCHAR(10) DEFAULT 'ru',
            payment_method_id VARCHAR(255)
        );
        """
        create_payments_table = """
        CREATE TABLE IF NOT EXISTS payments (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            payment_id VARCHAR(255) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
        try:
            cursor.execute(create_assistants_table)
            cursor.execute(create_chat_history_table)
            cursor.execute(create_users_table)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_queries_log (
                    id SERIAL PRIMARY KEY,
                    chat_id BIGINT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute(create_payments_table)
            cursor.execute("""
                ALTER TABLE users 
                ADD COLUMN IF NOT EXISTS subscription_start_date DATE,
                ADD COLUMN IF NOT EXISTS subscription_end_date DATE,
                ADD COLUMN IF NOT EXISTS trial_used BOOLEAN DEFAULT FALSE,
                ADD COLUMN IF NOT EXISTS auto_renewal BOOLEAN DEFAULT TRUE,
                ADD COLUMN IF NOT EXISTS web_search_enabled BOOLEAN DEFAULT FALSE,
                ADD COLUMN IF NOT EXISTS language VARCHAR(10) DEFAULT 'ru',
                ADD COLUMN IF NOT EXISTS payment_method_id VARCHAR(255);
            """)
                   
            connection.commit()
        except Exception as e:
            print(f"Ошибка при создании таблиц или добавлении столбцов: {e}")
            connection.rollback()
        create_experts_table(connection)

def load_assistants_config():
    cache_key = 'assistants_config'
    cached_config = r.get(cache_key)
    if cached_config:
        return json.loads(cached_config)
    try:
        connection = connect_to_db()
        cursor = connection.cursor()
        cursor.execute("SELECT assistant_key, name, prompt FROM assistants")
        assistants_data = cursor.fetchall()
        if not assistants_data:
            print("Нет ассистентов в базе данных.")
            return {"assistants": {}}
        assistants_config = {"assistants": {}}
        for assistant_key, name, prompt in assistants_data:
            assistants_config["assistants"][assistant_key] = {
                "name": name,
                "prompt": prompt
            }
        r.set(cache_key, json.dumps(assistants_config))
        print("Кэшированные данные в Redis.")
        return assistants_config
    except Exception as e:
        print(f"Ошибка при загрузке ассистентов: {e}")
        return {"assistants": {}}

def create_default_user(user_id: int, referrer_id: int = None):
    try:
        connection = connect_to_db()
        cursor = connection.cursor()
        cursor.execute("""
            INSERT INTO users (
                user_id, daily_tokens, last_reset, total_spent,
                referral_count, input_tokens, output_tokens,
                invited_users, referrer_id, subscription_plan,
                trial_used, auto_renewal, web_search_enabled, language,
                payment_method_id
            ) VALUES (
                %s, 30000, CURRENT_DATE, 0.0,
                0, 0, 0, 0, %s, 'free', FALSE, TRUE, FALSE, 'ru', NULL
            )
            RETURNING daily_tokens, last_reset, total_spent,
                      referral_count, input_tokens, output_tokens,
                      invited_users, referrer_id, subscription_plan,
                      trial_used, auto_renewal, web_search_enabled, language,
                      payment_method_id
        """, (user_id, referrer_id))
        user = cursor.fetchone()
        connection.commit()
        cursor.close()
        connection.close()
        return {
            "user_id": user_id,
            "daily_tokens": user[0],
            "last_reset": str(user[1]),
            "total_spent": float(user[2]),
            "referral_count": user[3],
            "input_tokens": user[4],
            "output_tokens": user[5],
            "invited_users": user[6],
            "referrer_id": user[7],
            "subscription_plan": user[8],
            "trial_used": user[9],
            "auto_renewal": user[10],
            "web_search_enabled": user[11],
            "language": user[12],
            "payment_method_id": user[13]
        }
    except Exception as e:
        print(f"Ошибка при создании пользователя: {e}")
        return None

def load_user_data(user_id: int):
    connection = None
    try:
        connection = connect_to_db()
        cursor = connection.cursor()
        cursor.execute("""
            SELECT daily_tokens, last_reset, total_spent,
                   referral_count, input_tokens, output_tokens,
                   invited_users, referrer_id, subscription_plan,
                   trial_used, auto_renewal, web_search_enabled, language,
                   subscription_start_date, subscription_end_date, payment_method_id
            FROM users
            WHERE user_id = %s
        """, (user_id,))
        user = cursor.fetchone()
        if user:
            return {
                "user_id": user_id,
                "daily_tokens": user[0],
                "last_reset": str(user[1]),
                "total_spent": float(user[2]),
                "referral_count": user[3],
                "input_tokens": user[4],
                "output_tokens": user[5],
                "invited_users": user[6],
                "referrer_id": user[7],
                "subscription_plan": user[8],
                "trial_used": user[9],
                "auto_renewal": user[10],
                "web_search_enabled": user[11],
                "language": user[12],
                "subscription_start_date": user[13],
                "subscription_end_date": user[14],
                "payment_method_id": user[15]
            }
        return None
    except Exception as e:
        print(f"Ошибка при загрузке данных пользователя: {e}")
        return None
    finally:
        if connection:
            cursor.close()
            connection.close()

def save_user_data(user_data: dict):
    try:
        connection = connect_to_db()
        cursor = connection.cursor()
        cursor.execute("""
            INSERT INTO users (
                user_id, daily_tokens, last_reset, total_spent,
                referral_count, input_tokens, output_tokens,
                invited_users, referrer_id, subscription_plan,
                trial_used, auto_renewal, web_search_enabled, language,
                subscription_start_date, subscription_end_date, payment_method_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                daily_tokens = EXCLUDED.daily_tokens,
                last_reset = EXCLUDED.last_reset,
                total_spent = EXCLUDED.total_spent,
                referral_count = EXCLUDED.referral_count,
                input_tokens = EXCLUDED.input_tokens,
                output_tokens = EXCLUDED.output_tokens,
                invited_users = EXCLUDED.invited_users,
                referrer_id = EXCLUDED.referrer_id,
                subscription_plan = EXCLUDED.subscription_plan,
                trial_used = EXCLUDED.trial_used,
                auto_renewal = EXCLUDED.auto_renewal,
                web_search_enabled = EXCLUDED.web_search_enabled,
                language = EXCLUDED.language,
                subscription_start_date = EXCLUDED.subscription_start_date,
                subscription_end_date = EXCLUDED.subscription_end_date,
                payment_method_id = EXCLUDED.payment_method_id
        """, (
            user_data["user_id"],
            user_data["daily_tokens"],
            user_data["last_reset"],
            user_data["total_spent"],
            user_data["referral_count"],
            user_data["input_tokens"],
            user_data["output_tokens"],
            user_data["invited_users"],
            user_data["referrer_id"],
            user_data["subscription_plan"],
            user_data["trial_used"],
            user_data["auto_renewal"],
            user_data["web_search_enabled"],
            user_data["language"],
            user_data["subscription_start_date"],
            user_data["subscription_end_date"],
            user_data["payment_method_id"]
        ))
        connection.commit()
        cursor.close()
        connection.close()
        return True
    except Exception as e:
        print(f"Ошибка при сохранении данных пользователя: {e}")
        return False

def store_message_in_db(chat_id, role, content):
    conn = connect_to_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO chat_history (chat_id, role, content, timestamp)
                VALUES (%s, %s, %s, NOW())
            """, (chat_id, role, content))
            conn.commit()
            cache_key = f'chat_history_{chat_id}'
            message = json.dumps({"role": role, "content": content})
            r.rpush(cache_key, message)
            if r.llen(cache_key) > 100:
                r.lpop(cache_key)
    except Exception as e:
        print(f"Ошибка при сохранении сообщения: {e}")
    finally:
        conn.close()

def get_chat_history(chat_id, limit=10):
    conn = connect_to_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT role, content FROM chat_history
                WHERE chat_id = %s
                ORDER BY timestamp DESC
                LIMIT %s
            """, (chat_id, limit))
            history = [{"role": role, "content": content} for role, content in cur.fetchall()]
            return history[::-1]
    except Exception as e:
        print(f"Ошибка при получении истории чата: {e}")
        return []
    finally:
        conn.close()

def clear_chat_history(chat_id):
    conn = connect_to_db()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM chat_history WHERE chat_id = %s", (chat_id,))
            conn.commit()
    except Exception as e:
        print(f"Ошибка при очистке истории чата: {e}")
    finally:
        conn.close()

conn = connect_to_db()
set_default_assistant(conn, 'universal_expert')
conn.close()

def log_user_query(chat_id: int, content: str):
    """
    Сохраняет ТОЛЬКО реальные запросы пользователей для статистики.
    Промпты и системный мусор сюда НЕ попадают.
    """
    if not content or len(content.strip()) < 5:
        return

    text = content.strip()
    lower = text.lower()

    # Фильтр промптов
    if (len(text) > 700 or
        "ты — универсальный экспертный" in lower or
        "ты — универсальный, экспертный" in lower or
        "mece" in lower or
        "chain-of-thought" in lower or
        "формат ответа" in lower or
        "правила общения" in lower or
        "настройки генерации" in lower or
        "инструкции для finni" in lower or
        "[файл:" in lower):
        return  # промпт — не сохраняем

    conn = connect_to_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO user_queries_log (chat_id, content, timestamp)
                VALUES (%s, %s, NOW())
            """, (chat_id, text))
            conn.commit()
    except Exception as e:
        print(f"[ERROR] Не удалось сохранить запрос в user_queries_log: {e}")
    finally:
        conn.close()

def get_db_connection():
    return connect_to_db()
