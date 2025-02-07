# Функция для подключения к базе данных PostgreSQL
import psycopg2
import os
from dotenv import load_dotenv


def connect_to_db():
    try:
        load_dotenv()  # Загружаем переменные из .env файла
        
        connection = psycopg2.connect(
            dbname=os.getenv('DB_NAME'),
            user=os.getenv('DB_USER'),
            password=os.getenv('DB_PASSWORD'),
            host=os.getenv('DB_HOST'),
            port=os.getenv('DB_PORT')
        )

        return connection
    except Exception as e:
        print(f"Ошибка подключения к базе данных: {e}")



# Функция для загрузки конфигурации ассистентов из базы данных
def load_assistants_config():
    try:
        # Получаем соединение с базой данных
        connection = connect_to_db()
        cursor = connection.cursor()
        
        # Выполняем запрос для получения всех ассистентов
        cursor.execute("SELECT assistant_key, name, prompt FROM assistants")
        assistants_data = cursor.fetchall()
        
        # Формируем словарь в том же формате, что и раньше
        assistants_config = {"assistants": {}}
        for assistant_key, name, prompt in assistants_data:
            assistants_config["assistants"][assistant_key] = {
                "name": name,
                "prompt": prompt
            }
            
        cursor.close()
        connection.close()
        
        return assistants_config
        
    except Exception as e:
        print(f"Ошибка при загрузке ассистентов: {e}")
        return {"assistants": {}}


        ###############################################
        #ЮЗЕРЫ
        ###############################################
def create_default_user(user_id: int):
    """Создание нового пользователя в базе данных с настройками по умолчанию"""
    try:
        connection = connect_to_db()
        cursor = connection.cursor()
        
        cursor.execute("""
            INSERT INTO users (
                user_id, daily_tokens, last_reset, total_spent,
                referral_count, input_tokens, output_tokens,
                invited_users, referrer_id, subscription_plan
            ) VALUES (
                %s, 20000, CURRENT_DATE, 0.0,
                0, 0, 0, 0, NULL, 'free'
            )
            RETURNING daily_tokens, last_reset, total_spent, 
                      referral_count, input_tokens, output_tokens,
                      invited_users, referrer_id, subscription_plan
        """, (user_id,))
        
        # Получаем созданные данные
        user = cursor.fetchone()
        
        connection.commit()
        cursor.close()
        connection.close()
        
        # Возвращаем в нужном формате
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
            "subscription_plan": user[8]
        }
        
    except Exception as e:
        print(f"Ошибка при создании пользователя: {e}")
        return None

def load_user_data(user_id: int):
    """Загрузка данных пользователя из базы данных в формате для существующих функций"""
    try:
        connection = connect_to_db()
        cursor = connection.cursor()
        
        cursor.execute("""
            SELECT daily_tokens, last_reset, total_spent, 
                   referral_count, input_tokens, output_tokens, 
                   invited_users, referrer_id, subscription_plan
            FROM users 
            WHERE user_id = %s
        """, (user_id,))
        
        user = cursor.fetchone()
        if user:
            # Возвращаем данные в том же формате, что ожидают существующие функции
            return {
                "user_id": user_id,
                "daily_tokens": user[0],
                "last_reset": str(user[1]),  # Преобразуем дату в строку для совместимости
                "total_spent": float(user[2]),  # Преобразуем в float для совместимости
                "referral_count": user[3],
                "input_tokens": user[4],
                "output_tokens": user[5],
                "invited_users": user[6],
                "referrer_id": user[7],
                "subscription_plan": user[8]
            }
        else:
            # Если пользователь не найден, создаем нового
            user_data = create_default_user(user_id)
            
        cursor.close()
        connection.close()
        return None
        
    except Exception as e:
        print(f"Ошибка при загрузке данных пользователя: {e}")
        return None

def save_user_data(user_data: dict):
    """Сохранение данных пользователя в базу данных"""
    try:
        connection = connect_to_db()
        cursor = connection.cursor()
        
        cursor.execute("""
            INSERT INTO users (
                user_id, daily_tokens, last_reset, total_spent,
                referral_count, input_tokens, output_tokens,
                invited_users, referrer_id, subscription_plan
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                daily_tokens = EXCLUDED.daily_tokens,
                last_reset = EXCLUDED.last_reset,
                total_spent = EXCLUDED.total_spent,
                referral_count = EXCLUDED.referral_count,
                input_tokens = EXCLUDED.input_tokens,
                output_tokens = EXCLUDED.output_tokens,
                invited_users = EXCLUDED.invited_users,
                referrer_id = EXCLUDED.referrer_id,
                subscription_plan = EXCLUDED.subscription_plan
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
            user_data["subscription_plan"]
        ))
        
        connection.commit()
        cursor.close()
        connection.close()
        
        return True
        
    except Exception as e:
        print(f"Ошибка при сохранении данных пользователя: {e}")
        return False
    
        ###############################################
        #ИСТОРИЯ
        ###############################################
def store_message_in_db(chat_id, role, content):
    """Сохраняет сообщение в базу данных"""
    conn = connect_to_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO chat_history (chat_id, role, content, timestamp) 
        VALUES (%s, %s, %s, NOW())
    """, (chat_id, role, content))
    conn.commit()
    cur.close()
    conn.close()

def get_chat_history(chat_id, limit=10):
    """Получает историю чата из базы данных"""
    conn = connect_to_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT role, content FROM chat_history 
        WHERE chat_id = %s 
        ORDER BY timestamp DESC 
        LIMIT %s
    """, (chat_id, limit))
    history = [{"role": role, "content": content} for role, content in cur.fetchall()]
    cur.close()
    conn.close()
    return list(reversed(history))  # Возвращаем в хронологическом порядке

def clear_chat_history(chat_id):
    """Очищает историю чата в базе данных"""
    conn = connect_to_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM chat_history WHERE chat_id = %s", (chat_id,))
    conn.commit()
    cur.close()
    conn.close()


