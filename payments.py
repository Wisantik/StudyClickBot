import datetime
import uuid
from yookassa import Payment

from database import connect_to_db, load_user_data


TRIAL_DAYS = 3
MONTH_PRICE = "399.00"


def process_trial_expiration(user_id: int):
    import datetime
    import uuid
    from yookassa import Payment

    conn = connect_to_db()
    try:
        user = load_user_data(user_id)
        if not user:
            return None

        if user["subscription_plan"] != "plus_trial":
            return None

        start_date = user.get("subscription_start_date")
        if not start_date:
            return None

        # 🔥 ВАЖНО: работаем ТОЛЬКО с date
        today = datetime.date.today()
        trial_end_date = start_date + datetime.timedelta(days=TRIAL_DAYS)

        if today < trial_end_date:
            return None  # trial ещё активен

        payment_method_id = user.get("payment_method_id")
        auto_renewal = user.get("auto_renewal")

        # ❌ Нет возможности автосписания
        if not payment_method_id or not auto_renewal:
            with conn.cursor() as cursor:
                cursor.execute("""
                    UPDATE users
                    SET subscription_plan = 'free',
                        trial_used = TRUE
                    WHERE user_id = %s
                """, (user_id,))
                conn.commit()

            return {
                "event": "trial_no_autopay",
                "user_id": user_id,
                "payment_method_id": payment_method_id,
                "auto_renewal": auto_renewal
            }

        # ✅ Создаём автоплатёж
        payment_params = {
            "amount": {"value": MONTH_PRICE, "currency": "RUB"},
            "capture": True,
            "payment_method_id": payment_method_id,
            "description": f"Автопродление подписки для {user_id}",
            "receipt": {
                "customer": {
                    "email": user.get("email", "unknown@example.com")
                },
                "items": [{
                    "description": "Подписка Plus (месяц)",
                    "quantity": "1.00",
                    "amount": {"value": MONTH_PRICE, "currency": "RUB"},
                    "vat_code": 1
                }]
            },
            "idempotency_key": str(uuid.uuid4())
        }

        payment = Payment.create(payment_params)

        if payment.status != "succeeded":
            return {
                "event": "autopay_failed",
                "user_id": user_id,
                "payment_id": payment.id,
                "status": payment.status
            }

        # ✅ Продлеваем подписку
        new_start = today
        new_end = new_start + datetime.timedelta(days=30)

        with conn.cursor() as cursor:
            cursor.execute("""
                UPDATE users
                SET subscription_plan = 'plus_month',
                    subscription_start_date = %s,
                    subscription_end_date = %s,
                    trial_used = TRUE
                WHERE user_id = %s
            """, (new_start, new_end, user_id))
            conn.commit()

        return {
            "event": "subscription_extended",
            "user_id": user_id,
            "payment_id": payment.id,
            "start_date": new_start,
            "end_date": new_end
        }

    except Exception as e:
        raise Exception(f"process_trial_expiration failed: {e}")

    finally:
        conn.close()



def daily_trial_check():
    """
    Фоновая проверка trial-подписок.
    Возвращает список событий для main.py
    """
    events = []
    conn = connect_to_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT user_id
                FROM users
                WHERE subscription_plan = 'plus_trial'
            """)
            users = cursor.fetchall()

        for (user_id,) in users:
            try:
                result = process_trial_expiration(user_id)
                if result:
                    events.append(result)
            except Exception as e:
                events.append({
                    "event": "critical_error",
                    "user_id": user_id,
                    "error": str(e)
                })

        return events

    finally:
        conn.close()


def run_scheduler():
    while True:
        try:
            schedule.run_pending()
        except Exception as e:
            logger.error(f"Ошибка в scheduler: {e}")
        time.sleep(30)

def set_user_subscription(user_id: int, plan: str, days: int = 30):
    conn = connect_to_db()
    try:
        start = datetime.date.today()
        end = start + datetime.timedelta(days=days)

        with conn.cursor() as cursor:
            cursor.execute("""
                UPDATE users
                SET subscription_plan = %s,
                    subscription_start_date = %s,
                    subscription_end_date = %s
                WHERE user_id = %s
            """, (plan, start, end, user_id))
            conn.commit()

    finally:
        conn.close()
