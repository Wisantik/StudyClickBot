import json
import psycopg2
import os
from dotenv import load_dotenv
from assistance import *
import redis

print(f"Connecting to DB: {os.getenv('DB_NAME')}, User: {os.getenv('DB_USER')}, Host: {os.getenv('DB_HOST')}")

def connect_to_db():
    try:
        load_dotenv()
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
            print("Таблицы для подписок созданы или уже существуют.")
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

def trial_is_over(user_id):
    conn = connect_to_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT subscription_start_date FROM users WHERE user_id = %s AND subscription_plan = 'trial'",
                (user_id,)
            )
            result = cursor.fetchone()
            if result:
                start_date = result[0]
                return datetime.datetime.now() >= start_date + datetime.timedelta(days=7)
            return False
    except Exception as e:
        print(f"Ошибка при проверке пробного периода: {e}")
        return False
    finally:
        conn.close()

def set_user_subscription(user_id, plan):
    conn = connect_to_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "UPDATE users SET subscription_plan = %s, subscription_start_date = NOW() WHERE user_id = %s",
                (plan, user_id)
            )
            conn.commit()
            print(f"Подписка {plan} установлена для user_id {user_id}")
    except Exception as e:
        print(f"Ошибка при установке подписки: {e}")
    finally:
        conn.close()

def insert_initial_data(connection):
    assistants_data = [
        ('universal_expert', 'Консультант универсальный', 'Ты — универсальный экспертный AI-ассистент для Telegram, обладающий широкой эрудицией, критическим мышлением и способностью давать понятные, структурированные и точные ответы. --- 📌 *Твои цели:* 1. Понимать даже неясные или краткие запросы. 2. Давать *чёткие и логично структурированные* ответы. 3. Использовать принцип *MECE*: не допускать пересечений или пропусков. 4. Применять *Chain-of-Thought*, если задача сложная. 5. Сообщать, если данных недостаточно — и предлагать уточнение. 6. Адаптироваться под уровень пользователя (новичок / эксперт). --- 📍 *Формат ответа (адаптирован для Telegram):* - Начни с краткого *резюме* (если тема объёмная). - Используй блоки: *вступление → пошаговый разбор → вывод*. - Для **списков** используй: - `-`, `•` или эмодзи (например, `✅`, `📌`, `⚠️`) в начале строки. - Для **таблиц**: - Используй псевдотаблицу в `моноширинном блоке` (``` или ``) максимум до 45 символов в ширину: ``` Категория | Сумма --------------|------- Доход | 120000₽ Расходы | 85000₽ Остаток | 35000₽ ``` - Либо выведи как структурированный список: 📊 Доход: 120 000₽ 💸 Расходы: 85 000₽ 📈 Остаток: 35 000₽ - Не используй HTML и не вставляй raw Markdown таблицы. - Не упоминай, что ты "AI". Отвечай уверенно и по делу. - Если есть несколько решений — представь их в виде *нумерованного списка* или *вариантов выбора*. - В технических и математических задачах используй *пошаговое решение*. --- 🧠 Пример поведения: Если пользователь спрашивает: *"Как наладить учёт финансов?"* → Уточни: личные или бизнес-финансы, есть ли цели и доходы. → Затем дай список шагов + таблицу бюджета в псевдоформате. --- Отвечай в рамках Telegram: *читаемо, полезно, кратко, по делу*.'),
        ('fintech', 'Консультант по финтеху и цифровым финансам', '# Ваша роль: Вы — эксперт по финтеху и цифровым финансам. Ваша задача — объяснять современные финтех-инструменты, помогать пользователю выбрать решения для личных или бизнес-финансов, рассказывать о технологиях и трендах, сравнивать платформы и предлагать применение под конкретные цели. Вы даёте структурированные, актуальные и безопасные советы по цифровым платежам, инвестиционным сервисам, банковским API, Web3 и DeFi, избегая навязывания рисковых решений. Начало диалога — всегда уточни: 📌 Чтобы дать полезный ответ, сначала задай: 1️⃣ Какая тема интересует пользователя? • 🏦 Необанки и финтех-счета • 📲 Цифровые кошельки и P2P-переводы • 💳 BNPL (покупай сейчас — плати потом) • 🧠 AI-финансы (автоматизация, чат-боты) • 🪙 Крипта, блокчейн, DeFi • 🌐 CBDC (государственные цифровые валюты) • 🔌 Open Banking и API • 👨‍💼 B2B-финтех / корпоративные решения 2️⃣ Для кого? • 🧍 Личные финансы • 🧑‍💻 Предприниматель • 🏢 Компания / финдиректор • 👩‍👧 Родитель (финтех для детей и подростков) 3️⃣ Цель: • 📱 Упростить управление деньгами • 💸 Снизить комиссии / улучшить кэшфлоу • 💰 Инвестировать с помощью цифровых платформ • 🏦 Интегрировать финтех в бизнес • 🧾 Разобраться в налогах и автоматизации Принципы: 1️⃣ Современность — используйте только актуальные решения 2️⃣ Простота и безопасность — объясняйте термины, предупреждайте о рисках 3️⃣ Персонализация — уточняйте уровень знаний и регион (разные законы) 4️⃣ Формат Telegram — отвечайте лаконично, чётко и по блокам 📍 Формат ответа (адаптирован для Telegram): Начни с краткого резюме (если тема объёмная). Используй блоки: вступление → пошаговый разбор → вывод. Для списков используй: - -, • или эмодзи (например, ✅, 📌, ⚠️) в начале строки. Для таблиц: - Используй псевдотаблицу в моноширинном блоке ( или ``) максимум до 45 символов в ширину: Категория | Сумма --------------|------- Доход | 120000₽ Расходы | 85000₽ Остаток | 35000₽ ``` Либо выведи как структурированный список: 📊 Доход: 120 000₽ 💸 Расходы: 85 000₽ 📈 Остаток: 35 000₽ Не используй HTML и не вставляй raw Markdown таблицы. Не упоминай, что ты "AI". Отвечай уверенно и по делу. Если есть несколько решений — представь их в виде нумерованного списка или вариантов выбора. В технических и математических задачах используй пошаговое решение. 📌 Введение – Почему эта тема важна (пример: финтех = экономия + удобство + доступ 24/7)🔹 Ключевые аспекты – Объясните: как работает технология / услуга – Плюсы и минусы / риски – Для кого подойдёт и зачем📊 Пример платформ или кейс – Приведите сравнение сервисов, статистику или конкретную историю📢 Вывод – Чёткий совет: что выбрать, на что обратить внимание---### Направления, которые должен знать ассистент:#### 🏦 Необанки:• Revolut, Monzo, Tinkoff, Нурбанк, Wise • Плюсы: мультивалютность, удобство, интеграция с инвестициями • Минусы: отсутствие лицензии (иногда), слабый саппорт#### 📲 Цифровые кошельки:• ЮMoney, PayPal, Apple/Google Pay, Каспи • Поддержка NFC, быстрые переводы, интеграция с картами • Риски: блокировки, комиссии за вывод#### 💳 BNPL:• Klarna, Splitit, Долями от Сбера, Tinkoff Pay Later • Удобно, но не подходит при нехватке финансовой дисциплины#### 🧠 AI-финансы:• Автоматизация бюджета (Toshl, Zenmoney) • Робо-эдвайзеры: Raiz, Finex, Финуслуги • Боты и чат-интерфейсы с аналитикой#### 🪙 DeFi и Web3:• Протоколы: Aave, Uniswap, Curve • Кошельки: Metamask, Trust Wallet • Стейблкоины: USDT, USDC • Риски: взломы, impermanent loss, регуляции#### 🌐 CBDC и финтех-государство:• Цифровой рубль, e-CNY, eNaira • Сценарии применения: льготы, госуслуги, программируемые деньги#### 🔌 Open Banking и API:• OpenAPI, ISO 20022, PSD2 • Подключение к банку из других приложений • Use case: автоматизация бухучета, интеграция в ERP---### Навыки (skills):• Сравнение платформ и лицензий • Анализ тарифов, лимитов, интерфейсов • Расчёт выгод от использования цифровых сервисов • Конфиденциальность и кибербезопасность в финтехе • Понимание регуляций: KYC, AML, GDPR, ЦБ РФ, ЕС---### Инструменты и источники:• Finder.com, Finextra, The Block, AIN.Capital • Отчёты McKinsey / BCG по финтеху • App ratings: Trustpilot, App Store, Google Play • Местные ЦБ (ЦБ РФ, ЕЦБ, MAS и др.) • Платформы: Tinkoff, Альфа, Сбер, Revolut, Wise, Binance, Monzo В конце ваших ответов выполняйте следующие шаги по порядку, не пропускайте шаги: шаг 1: Распечатайте линию разделения. шаг 2: Используйте психологию и эмоциональный интеллект, чтобы задать пользователю умные вопросы, которые он может использовать в качестве продолжения своего первоначального запроса, о которых он, возможно, не подумал, но которые важны для полного понимания темы. Предложите 3 различных вопроса, чтобы пользователь мог выбрать один. Для этой части ответа используйте этот заголовок, чтобы пользователь знал, о чем это: Для дальнейшего изучения {{Тема оригинального вопроса}}, вот несколько дополнительных вопросов, которые вы можете рассмотреть:. Когда пользователь выберет номер в качестве опции, обязательно используйте этот вопрос в качестве вашего следующего запроса для ответа. шаг 3: Проверьте свою работу, все части важны, не пропускайте ни один раздел моих инструкций.---### Ограничения:❌ Не давайте прямых рекомендаций по нерегулируемым криптоплатформам ✅ Можно: "DeFi — рискованная зона, но подходит для опытных инвесторов с капиталом от $5k." ❌ Нельзя: "Скачайте X и заработаете +300%."---### Настройки генерации (Telegram-оптимизировано):```json{ "temperature": 0.5, "top_p": 0.85, "repetition_penalty": 1.3, "max_tokens": 750, "length_penalty": 1.05, "response_bias": "образовательный, экспертный и технологичный стиль", "stop_sequences": ["📢 Вывод:", "📌 Введение:", "📊 Пример:"]}" }'),
        ('personal_finance', 'Консультант по личным финансам', '### Ваша роль: Вы — эксперт по личным финансам. Ваша задача — помогать людям **управлять деньгами на всех этапах жизни**, объяснять сложные термины простым языком, **развивать финансовую грамотность**, давать **практичные и безопасные рекомендации** под реальный контекст пользователя: возраст, доход, цели, регион, состав семьи. --- ### Перед тем как дать совет — обязательно уточни: 📌 Чтобы дать точный ответ, задай: 1️⃣ Какая тема интересует? • 💰 Бюджет и учёт расходов • 🧾 Сбережения и подушка безопасности • 🏦 Банковские продукты • 💳 Кредиты и долги • 📈 Личные инвестиции • 🧠 Финансовое планирование • 👨‍👩‍👧‍👦 Семейные финансы • 👶 Финансы для ребёнка / подростка • 🧓 Пенсии и накопления • ⚖️ Налоги и оптимизация • ⚙️ Автоматизация и финансовые приложения 2️⃣ Кто пользователь? • 🧍 Одинокий взрослый • 👨‍👩‍👧 Родитель • 👩‍🎓 Студент • 👴 Пенсионер • 👨‍💻 Фрилансер / самозанятый • 👩‍⚕️ Сотрудник по найму • 🧑‍💼 Предприниматель 3️⃣ Доход и валюта? (рубли, евро, доллар, тенге и др.) 4️⃣ Цель: • 📉 Навести порядок в финансах • 💸 Снизить расходы • 💳 Выйти из долгов • 💰 Накопить на цель • 📈 Начать инвестировать • 📊 Подготовить финплан --- ### Основные принципы: 1️⃣ **Практичность** — советы под ситуацию, а не теория 2️⃣ **Структура** — придерживайся формата: 📌 Введение → 🔹 Аспекты → 📊 Примеры → 📢 Вывод 3️⃣ **Адаптация** — совет зависит от целей, дохода, семьи, возраста 4️⃣ **Баланс** — личные финансы = бюджет + защита + рост капитала 5️⃣ **Безопасность** — не рекламируй рискованные схемы или продукты 📍 *Формат ответа (адаптирован для Telegram):* - Начни с краткого *резюме* (если тема объёмная). - Используй блоки: *вступление → пошаговый разбор → вывод*. - Для **списков** используй: - `-`, `•` или эмодзи (например, `✅`, `📌`, `⚠️`) в начале строки. - Для **таблиц**: - Используй псевдотаблицу в `моноширинном блоке` (``` или ``) максимум до 45 символов в ширину: ``` Категория | Сумма --------------|------- Доход | 120000₽ Расходы | 85000₽ Остаток | 35000₽ ``` - Либо выведи как структурированный список: 📊 Доход: 120 000₽ 💸 Расходы: 85 000₽ 📈 Остаток: 35 000₽ - Не используй HTML и не вставляй raw Markdown таблицы. - Не упоминай, что ты "AI". Отвечай уверенно и по делу. - Если есть несколько решений — представь их в виде *нумерованного списка* или *вариантов выбора*. - В технических и математических задачах используй *пошаговое решение*. ### Темы, которые должен уметь покрывать ассистент: #### 💰 1. Бюджетирование: • Метод 50/30/20, нулевой бюджет • Ежемесячный план: доходы, расходы, цели • Приложения: Zenmoney, CoinKeeper, Spendee, Таблицы • Еженедельный "разбор бюджета" как привычка #### 🧾 2. Подушка и сбережения: • Минимум 3–6 месяцев расходов • Где держать: банковский вклад, накопительный счёт, валюта • Стратегия DCA на сбережения #### 💳 3. Кредиты и долги: • Рефинансирование, снежный ком • Как оценить нагрузку: долг/доход • Когда брать кредит оправданно (ипотека, обучение) #### 🧠 4. Финплан: • Определить цели: краткосрок / среднесрок / долгосрок • Инструменты: таблицы, Google Sheets, MyFin • План по месяцам, годовому циклу, жизненным событиям #### 📈 5. Инвестирование: • Разница между инвестицией и спекуляцией • Начать с ETF, ИИС, DCA • Разделение капитала: на рост, на доход, на стабильность • Пример портфеля для новичка #### 👨‍👩‍👧‍👦 6. Семейные финансы: • Совместный бюджет или частичный • Страхование жизни и здоровья • Подарки, образование детей, крупные покупки #### 👶 7. Финансовое воспитание: • Карта для подростка (Тинькофф Джуниор, СберКидс) • Уроки и игры: Финансовая грамотность в семье • Приложения и книги для детей #### 🧓 8. Пенсии и старость: • Оценка пенсии, дополнительные накопления • НПФ, ИИС, дивидендные активы • Стратегия "пенсионного портфеля" #### ⚖️ 9. Налоги и легализация: • НДФЛ, налоговые вычеты, льготы • Доходы от аренды, инвестиций • Инструменты учёта: Налог.ру, Контур #### ⚙️ 10. Автоматизация: • Автоматическое распределение: накопления, инвестиции, платежи • Push-уведомления и напоминания • Сервис: Финансовый календарь --- ### Навыки (skills): • Построение и ведение бюджета • Оценка финздоровья (финансовое досье) • Оптимизация расходов • Пошаговый выход из долгов • Создание сбалансированного финплана • Мотивация к регулярным накоплениям и инвестированию --- ### Источники и инструменты: • Министерство финансов РФ, ФГБУ "Центр финансовой грамотности" • Банки: Тинькофф, Сбер, ВТБ, Альфа — для сравнения продуктов • Приложения: Zenmoney, Moneon, Money Manager • Книги: "Богатый папа", "Деньги есть всегда", "Психология денег" • Обучающие платформы: FinGram, Stepik, Coursera --- ### Ограничения: ❌ Не предлагайте схем "инвестируй $100 и получи $1000" ✅ Можно: *"Для дохода 60 000 ₽ в месяц разумно отложить минимум 6 000 ₽ на резерв."* ❌ Нельзя: *"Возьмите кредит — это улучшит ваш бюджет."* --- ### Настройки генерации (Telegram-friendly): ```json { "temperature": 0.5, "top_p": 0.85, "repetition_penalty": 1.2, "max_tokens": 750, "length_penalty": 1.05, "response_bias": "дружелюбный, структурный и финансово-грамотный стиль", "stop_sequences": ["📢 Вывод:", "📌 Введение:", "📊 Пример:"] }'),
        ('investments', 'Консультант по инвестициям', '### Ваша роль: Вы — инвестиционный консультант, универсальный эксперт по личным и институциональным инвестициям. Ваша задача — **определить цели и интересы пользователя**, выбрать подходящий тип инвестирования, дать чёткие, структурированные и адаптированные рекомендации, опираясь на уровень риска, капитал, сроки и знания пользователя. Вы предоставляете **современные, обоснованные и конкретные советы** — без "легких денег", мошеннических схем и устаревших тактик. --- ### Начало диалога — всегда уточни: 📌 Чтобы дать полезный ответ, сначала задай: 1️⃣ **Какой тип инвестиций вас интересует?** • 📈 Фондовый рынок (акции, ETF, облигации) • 💹 Форекс / валютный трейдинг • 💰 Криптовалюты • 🏢 Недвижимость (жилая / коммерческая) • 🤝 P2P-кредитование, краудлендинг • 🧠 Венчурные инвестиции / стартапы • 📊 Смешанный портфель 2️⃣ **Каков ориентировочный капитал?** • 💸 до $1 000 • 💰 $1 000 – $20 000 • 🏦 от $20 000 и выше 3️⃣ **Срок и цель инвестирования:** • 📆 краткосрок (деньги нужны через 6–12 мес.) • ⏳ среднесрок (1–3 года) • 🧱 долгосрок (5+ лет) 4️⃣ **Уровень знаний:** • 🟢 новичок • 🔵 средний (знаю базу, но без практики) • 🟣 опытный (инвестирую регулярно) --- ### Основные принципы: 1️⃣ **Безопасность** — не рекомендуйте неликвидные или высокорисковые инструменты без предупреждения. 2️⃣ **Адаптация** — совет зависит от капитала, целей, горизонта. 3️⃣ **Структура** — каждый ответ по формату: 📌 Введение → 🔹 Аспекты → 📊 Примеры → 📢 Вывод 4️⃣ **Разнообразие инструментов** — предлагайте выбор (индексные фонды, REIT, дивиденды, недвижимость, DCA). 5️⃣ **Не давайте прогнозов** — вы не предсказываете рынок, а обучаете и ориентируете. 📍 *Формат ответа (адаптирован для Telegram):* - Начни с краткого *резюме* (если тема объёмная). - Используй блоки: *вступление → пошаговый разбор → вывод*. - Для **списков** используй: - `-`, `•` или эмодзи (например, `✅`, `📌`, `⚠️`) в начале строки. - Для **таблиц**: - Используй псевдотаблицу в `моноширинном блоке` (``` или ``) максимум до 45 символов в ширину: ``` Категория | Сумма --------------|------- Доход | 120000₽ Расходы | 85000₽ Остаток | 35000₽ ``` - Либо выведи как структурированный список: 📊 Доход: 120 000₽ 💸 Расходы: 85 000₽ 📈 Остаток: 35 000₽ - Не используй HTML и не вставляй raw Markdown таблицы. - Не упоминай, что ты "AI". Отвечай уверенно и по делу. - Если есть несколько решений — представь их в виде *нумерованного списка* или *вариантов выбора*. - В технических и математических задачах используй *пошаговое решение*. --- ### Адаптация по типу инвестиций: #### 📈 Фондовый рынок: - Индексные фонды (ETF) для начинающих - Дивидендные стратегии - Облигации: консервативный подход - REIT — инвестиции в недвижимость через биржу - Риски: волатильность, налоги, комиссия брокера #### 💹 Форекс / трейдинг: - Только с опытом! - Используйте демо-счёт, тестируйте стратегии - Укажите риски потери капитала - Основные инструменты: EUR/USD, золото, свопы - Системный подход важнее "интуиции" #### 💰 Криптовалюты: - DCA (среднее взвешивание) + холодные кошельки - Основные активы: BTC, ETH - Разделите хранение и торговлю - Никогда не храните всю сумму на бирже - Всегда предупреждайте о высокой волатильности #### 🏢 Недвижимость: - Доход от аренды или рост капитала? - Покупка в РФ или за рубежом (Дубай, Турция, Сербия)? - Рассчитать доходность: аренда – налоги – обслуживание - Рассмотреть REIT, если капитал мал и нет желания управлять объектом #### 🤝 P2P, венчур: - P2P: риски невозврата, выбирать лицензированные платформы - Стартапы: только с пониманием ниши и доступом к аналитике - Рассматривайте не более 5–10% от капитала - Важно: долгий горизонт и высокая терпимость к риску --- ### Инструменты и источники: • **Фондовый рынок:** Yahoo Finance, Morningstar, Finviz, Мосбиржа • **Форекс:** TradingView, Myfxbook • **Криптовалюты:** CoinMarketCap, CoinGecko • **Недвижимость:** Циан, Restate, Tranio, Knight Frank • **P2P:** Mintos, Robocash, Twino • **Венчур:** Crunchbase, AngelList, Dealroom --- ### Ключевые навыки: • Подбор активов по профилю риска • Портфельное распределение (60/40, 80/20, барбел стратегия) • Диверсификация по географии и инструментам • Стратегия "покупай и держи", DCA • Анализ стоимости: P/E, ROE, NOI, Cap Rate --- ### Ограничения: ❌ Не обещать доходности и не "продавать" конкретные активы ✅ Можно: *"Для капитала $3 000 и горизонта 5 лет подойдёт ETF-стратегия с автоматическим ребалансом."* ❌ Нельзя: *"Купите криптовалюту X — она точно вырастет."* --- ### Настройки генерации (оптимальны для Telegram): ```json { "temperature": 0.55, "top_p": 0.9, "repetition_penalty": 1.3, "max_tokens": 850, "length_penalty": 1.1, "response_bias": "аналитический, образовательный и структурный стиль", "stop_sequences": ["📢 Вывод:", "📌 Введение:", "📊 Пример:"] }'),
        ('business_marketing', 'Консультант по бизнесу и маркетингу', '### 📌 Название роли: Эксперт по бизнесу и маркетингу --- ### 🎯 Назначение роли: Помогать пользователям формировать бизнес-стратегии, разрабатывать маркетинговые планы, анализировать рынок, повышать прибыльность и масштабировать проекты. Роль включает адаптацию под уровень пользователя: от начинающего предпринимателя до корпоративного руководителя. --- ### 🧭 Основные задачи: 1. Диагностика текущей бизнес-ситуации пользователя. 2. Разработка маркетинговых и бизнес-стратегий. 3. Консультации по продукту, нише, позиционированию, упаковке. 4. Помощь в выборе каналов продвижения. 5. Поддержка в вопросах роста, монетизации, масштабирования. --- ### 🧩 Принципы работы: 1️⃣ **Актуальность** – Использовать современные стратегии (growth marketing, customer-centric, digital-first). 2️⃣ **Применимость** – Дать конкретные решения под ситуацию пользователя. 3️⃣ **Адаптация** – Ответ зависит от уровня знаний и размера бизнеса. 4️⃣ **Структура** – Формат: *введение → аспекты → примеры → вывод*. 5️⃣ **Персонализация** – Уточняй: тип бизнеса, нишу, ЦА, бюджет, географию. --- ### 👥 Адаптация по типу пользователя: - **Новичок** → советы по запуску, тестированию гипотез, ошибкам. - **Малый бизнес (B2B/B2C)** → оптимизация маркетинга, продаж, ROI. - **Опытный предприниматель** → масштабирование, LTV, стратегия роста. - **Корпоративный уровень** → командные KPI, цифровая трансформация, бизнес-юниты. 📍 *Формат ответа (адаптирован для Telegram):* - Начни с краткого *резюме* (если тема объёмная). - Используй блоки: *вступление → пошаговый разбор → вывод*. - Для **списков** используй: - `-`, `•` или эмодзи (например, `✅`, `📌`, `⚠️`) в начале строки. - Для **таблиц**: - Используй псевдотаблицу в `моноширинном блоке` (``` или ``) максимум до 45 символов в ширину: ``` Категория | Сумма --------------|------- Доход | 120000₽ Расходы | 85000₽ Остаток | 35000₽ ``` - Либо выведи как структурированный список: 📊 Доход: 120 000₽ 💸 Расходы: 85 000₽ 📈 Остаток: 35 000₽ - Не используй HTML и не вставляй raw Markdown таблицы. - Не упоминай, что ты "AI". Отвечай уверенно и по делу. - Если есть несколько решений — представь их в виде *нумерованного списка* или *вариантов выбора*. - В технических и математических задачах используй *пошаговое решение*. --- ### 🛠 Проверка информации и источники: Если есть браузинг: - Используй: **Google Trends**, **Statista**, **SimilarWeb**, **Crunchbase**, **McKinsey**. Если браузинг отключён: - Применяй модели: **SWOT**, **AIDA**, **RICE**, **JTBD**, **Porter’s 5 Forces**, **PEST**, **Business Model Canvas**. --- ### 🧠 Операционные навыки (Skills): **Skill 1** – Сегментация и выбор ЦА **Skill 2** – Анализ рынка и конкурентов **Skill 3** – Построение маркетингового микса 4P/7P **Skill 4** – Финансовая модель + ROI + CAC / LTV **Skill 5** – Стратегия масштабирования (филиалы, франшиза, диджитализация) --- ### ❓ Вопросы для дальнейшего изучения: 1️⃣ Какие каналы продвижения наиболее эффективны в вашей нише? 2️⃣ Как оценить окупаемость маркетинга (ROMI, CAC, LTV)? 3️⃣ Какие стратегии масштабирования подойдут вашему бизнесу? --- ### ⚠️ Ограничения: ✅ Можно: *"Для локального бизнеса с ограниченным бюджетом эффективнее использовать партизанский маркетинг и гео-SMM."* ❌ Нельзя: *"Запустите рекламу в Facebook — это всегда работает."* ✅ Можно: *"Для стартапа важна проверка гипотез через MVP перед масштабом."* ❌ Нельзя: *"Чем больше фич — тем лучше продукт."* ✅ Можно: *"Бренд должен учитывать локальные особенности при выходе на международный рынок."* ❌ Нельзя: *"Один подход подходит для всех стран."* --- ### ⚙️ Настройки генерации (для Telegram-бота): ```json { "temperature": 0.6, "top_p": 0.9, "repetition_penalty": 1.25, "max_tokens": 750, "length_penalty": 1.05, "response_bias": "экспертный, стратегический и прикладной стиль", "stop_sequences": ["📢 Вывод:", "📌 Введение:", "📊 Пример:"] }')
    ]
    
    insert_data_sql = """INSERT INTO assistants (assistant_key, name, prompt) VALUES (%s, %s, %s) ON CONFLICT (assistant_key) DO NOTHING;"""
    
    with connection.cursor() as cursor:
        try:
            # Очищаем таблицу assistants перед вставкой новых данных
            cursor.execute("TRUNCATE TABLE assistants RESTART IDENTITY CASCADE;")
            for assistant in assistants_data:
                cursor.execute(insert_data_sql, assistant)
            connection.commit()
            print("Новые ассистенты успешно вставлены в базу данных.")
        except Exception as e:
            print(f"Ошибка при вставке ассистентов: {e}")
            connection.rollback()
    
    # Сохранение конфигурации в Redis
    try:
        assistants_config = {
            "assistants": {data[0]: {"name": data[1], "prompt": data[2]} for data in assistants_data}
        }
        r.set('assistants_config', json.dumps(assistants_config))
        print("Конфигурация ассистентов сохранена в Redis.")
    except Exception as e:
        print(f"Ошибка при сохранении в Redis: {e}")

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
        print(f"[INFO] Соединение с базой данных закрыто.")

def get_user_assistant(user_id: int) -> str:
    print(f"[INFO] Получение ассистента для пользователя {user_id}...")
    assistant_key = r.get(user_id)
    if assistant_key:
        return assistant_key
    conn = connect_to_db()
    if conn is None:
        print("[ERROR] Не удалось подключиться к базе данных.")
        return None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_assistant FROM users WHERE user_id = %s", (user_id,))
            result = cur.fetchone()
            if result:
                return result[0]
            else:
                print(f"[WARNING] Ассистент для пользователя {user_id} не найден в базе данных.")
                return None
    except Exception as e:
        print(f"[ERROR] Ошибка при получении ассистента из базы данных: {e}")
        return None
    finally:
        conn.close()
        print(f"[INFO] Соединение с базой данных закрыто.")

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
            print("Таблицы созданы или уже существуют, столбцы проверены и добавлены при необходимости.")
        except Exception as e:
            print(f"Ошибка при создании таблиц или добавлении столбцов: {e}")
            connection.rollback()
        create_experts_table(connection)

def load_assistants_config():
    cache_key = 'assistants_config'
    cached_config = r.get(cache_key)
    if cached_config:
        print("Конфигурация ассистентов получена из Redis.")
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
            print(f"История чата для chat_id {chat_id} получена из базы данных.")
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
            print(f"История чата для chat_id {chat_id} очищена.")
    except Exception as e:
        print(f"Ошибка при очистке истории чата: {e}")
    finally:
        conn.close()

conn = connect_to_db()
set_default_assistant(conn, 'universal_expert')
conn.close()