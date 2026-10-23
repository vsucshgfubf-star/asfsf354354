import os
import time
import random
import hmac
import hashlib
import html
import logging
import threading

from flask import Flask, request, abort
import telebot
from telebot.apihelper import ApiTelegramException
from telebot.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    LabeledPrice,
)

# ---------------------------------------------------------------- НАСТРОЙКИ

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Не задана переменная окружения BOT_TOKEN")

# Render сам задаёт RENDER_EXTERNAL_URL. Локально можно задать вручную.
BASE_URL = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")

# Секрет для проверки, что запросы на вебхук приходят именно от Telegram.
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET") or hashlib.sha256(
    ("webhook:" + BOT_TOKEN).encode()
).hexdigest()

# 1 = показывать платёж в 1 Star (с автовозвратом), 0 = выключить оплату
ENABLE_PAYMENT = os.environ.get("ENABLE_PAYMENT", "1") == "1"

PAYLOAD = "demo_payment_v1"
STEP_DELAY = 1.5          # пауза между этапами «сканирования», сек
SCAN_COOLDOWN = 10        # минимальный интервал между сканами одного пользователя, сек
MAX_TARGET_LEN = 64

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("demo-bot")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
app = Flask(__name__)

# --------------------------------------------------------------- СОСТОЯНИЕ
user_country_pref = {}
last_scan_time = {}
active_scans = set()
state_lock = threading.Lock()

# ------------------------------------------------------------------ ДАННЫЕ

COUNTRY_DATA = {
    "UA": {
        "name": "Украина",
        "mask": "+380 ({operator}) ***-**-{last_two}",
        "operators": ["67", "68", "97", "50", "66", "73", "93"],
        "cities": ["Киев, Шевченковский р-н", "Днепр, Соборный р-н",
                   "Одесса, Приморский р-н", "Львов, Галицкий р-н"],
    },
    "US": {
        "name": "США",
        "mask": "+1 ({operator}) ***-**-{last_two}",
        "operators": ["212", "310", "415", "305"],
        "cities": ["New York, NY", "Los Angeles, CA", "Miami, FL", "Chicago, IL"],
    },
    "PL": {
        "name": "Польша",
        "mask": "+48 ({operator}) ***-**-{last_two}",
        "operators": ["50", "51", "60", "79"],
        "cities": ["Warszawa, Śródmieście", "Kraków, Stare Miasto", "Wrocław"],
    },
}

STAGES = [
    "Анализ открытых узлов Telegram API...",
    "Перехват публичных метаданных...",
    "Запрос к базам геолокации GSM-вышек...",
    "Анализ совпадений списков контактов...",
    "Дешифровка фрагментов профиля...",
    "Определение привязанного номера телефона...",
    "Сбор списка общих чатов и медиа...",
    "Формирование приватного архива...",
    "Сжатие данных в защищённый контейнер...",
    "Завершение формирования отчёта...",
]

# ----------------------------------------------------------------- ХЕЛПЕРЫ


def generate_masked_phone(country_code: str):
    cdata = COUNTRY_DATA.get(country_code, COUNTRY_DATA["UA"])
    op = random.choice(cdata["operators"])
    last_two = f"{random.randint(10, 99)}"
    return cdata["mask"].format(operator=op, last_two=last_two), random.choice(cdata["cities"])


def safe_edit(chat_id, message_id, text, reply_markup=None):
    try:
        bot.edit_message_text(text, chat_id, message_id, reply_markup=reply_markup)
    except ApiTelegramException as e:
        if "message is not modified" not in str(e):
            log.warning("edit_message_text failed: %s", e)
    except Exception:
        log.exception("edit_message_text unexpected error")


def country_keyboard():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🇺🇦 Украина (+380)", callback_data="select_UA"))
    kb.add(InlineKeyboardButton("🇺🇸 США (+1)", callback_data="select_US"))
    kb.add(InlineKeyboardButton("🇵🇱 Польша (+48)", callback_data="select_PL"))
    return kb


# ----------------------------------------------------------------- ХЕНДЛЕРЫ


@bot.message_handler(commands=["start"])
def cmd_start(message):
    bot.send_message(
        message.chat.id,
        "🕵️ <b>Telegram Spy & OSINT Intelligence Bot v4.2</b>\n\n"
        "Система глубокого анализа данных, поиска скрытых сессий и геолокации по ID или @username.\n\n"
        "Выберите целевую страну для настройки протокола сканирования:",
        reply_markup=country_keyboard(),
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("select_"))
def cb_country(call):
    code = call.data.split("_", 1)[1]
    if code not in COUNTRY_DATA:
        bot.answer_callback_query(call.id)
        return
    with state_lock:
        user_country_pref[call.from_user.id] = code
    safe_edit(
        call.message.chat.id,
        call.message.message_id,
        f"✅ Выбрана зона поиска: <b>{html.escape(COUNTRY_DATA[code]['name'])}</b>\n\n"
        "👇 Теперь отправьте в чат <code>@username</code> или Telegram ID объекта для инициализации перехвата:",
    )
    bot.answer_callback_query(call.id)


@bot.message_handler(content_types=["text"], func=lambda m: not m.text.startswith("/"))
def on_text(message):
    user_id = message.from_user.id

    with state_lock:
        if user_id in active_scans:
            blocked = "busy"
        elif time.time() - last_scan_time.get(user_id, 0) < SCAN_COOLDOWN:
            blocked = "cooldown"
        else:
            blocked = None
            active_scans.add(user_id)
            last_scan_time[user_id] = time.time()
        country = user_country_pref.get(user_id, "UA")

    if blocked == "busy":
        bot.send_message(message.chat.id, "⏳ Предыдущий сеанс анализа ещё выполняется. Ожидайте завершения.")
        return
    if blocked == "cooldown":
        bot.send_message(message.chat.id, "⏳ Слишком частые запросы к узлам API. Пожалуйста, подождите.")
        return

    target = message.text.strip()[:MAX_TARGET_LEN]
    # Запускаем в отдельном потоке, чтобы не блокировать вебхук Flask.
    threading.Thread(
        target=run_scan, args=(message.chat.id, user_id, target, country), daemon=True
    ).start()


def run_scan(chat_id, user_id, target, country):
    try:
        status = bot.send_message(chat_id, "🚀 Инициализация соединения с серверами API...")
        total = len(STAGES)
        safe_target = html.escape(target)

        for i in range(1, total + 1):
            percent = i * 100 // total
            bar = "█" * i + "░" * (total - i)
            safe_edit(
                chat_id,
                status.message_id,
                f"⚙️ <b>Сканирование цели:</b> <code>{safe_target}</code>\n"
                f"[{bar}] <b>{percent}%</b>\n\n"
                f"🔄 <i>Статус:</i> {STAGES[i - 1]}",
            )
            time.sleep(STEP_DELAY)

        phone, city = generate_masked_phone(country)

        report = (
            f"🎯 <b>Отчёт по объекту:</b> <code>{safe_target}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📍 <b>Приблизительная локация:</b> {html.escape(city)}\n"
            f"📱 <b>Телефон (маска):</b> <code>{html.escape(phone)}</code>\n"
            f"💬 <b>Перехвачено сообщений:</b> {random.randint(1400, 8900)}\n"
            f"🖼 <b>Обнаружено скрытых медиа:</b> {random.randint(12, 85)}\n"
            f"🔐 <b>Активные сессии:</b> 2 устройства (iOS / Web)\n\n"
            f"⚠️ <b>Внимание:</b> Полный архив переписок и деанонимизация номера заблокированы протоколом безопасности цели."
        )

        kb = InlineKeyboardMarkup()
        if ENABLE_PAYMENT:
            kb.add(InlineKeyboardButton(
                "⭐ Разблокировать полный архив (1 Star)", callback_data="pay_stars"))

        try:
            bot.delete_message(chat_id, status.message_id)
        except Exception:
            pass
        bot.send_message(chat_id, report, reply_markup=kb)
    except Exception:
        log.exception("scan failed")
    finally:
        with state_lock:
            active_scans.discard(user_id)


# ------------------------------------------------------------ ОПЛАТА STARS


@bot.callback_query_handler(func=lambda c: c.data == "pay_stars")
def cb_pay(call):
    bot.answer_callback_query(call.id)
    if not ENABLE_PAYMENT:
        return
    bot.send_invoice(
        chat_id=call.message.chat.id,
        title="🔓 Доступ к архиву переписок",
        description="Снятие сетевых ограничений и выгрузка зашифрованного лога сообщений.",
        invoice_payload=PAYLOAD,
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label="Архив сообщений", amount=1)],
    )


@bot.pre_checkout_query_handler(func=lambda q: True)
def on_pre_checkout(query):
    if ENABLE_PAYMENT and query.invoice_payload == PAYLOAD:
        bot.answer_pre_checkout_query(query.id, ok=True)
    else:
        bot.answer_pre_checkout_query(query.id, ok=False, error_message="Ошибка обработки платежа.")


@bot.message_handler(content_types=["successful_payment"])
def on_successful_payment(message):
    charge_id = message.successful_payment.telegram_payment_charge_id
    try:
        # Автоматический возврат звезды, чтобы на демонстрации не тратить реальные средства
        bot.refund_star_payment(message.from_user.id, charge_id)
        refund_note = "💸 <i>(Тестовый режим: ваша звезда была автоматически возвращена на баланс).</i>"
        log.info("Refunded %s to user %s", charge_id, message.from_user.id)
    except Exception:
        log.exception("Refund failed for charge %s", charge_id)
        refund_note = "⚠️ Платёж прошёл успешно."

    # Блок с разоблачением для презентации (показываем после завершения «скама»)
    bot.send_message(
        message.chat.id,
        "🛑 <b>ЭКСПОЗЕ / УЧЕБНЫЙ СТЕНД БЕЗОПАСНОСТИ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🎉 Вы только что прошли полный цикл взаимодействия со скам-ботом!\n\n"
        "👨‍🏫 <b>Как преподавателю показать суть схемы:</b>\n"
        "1. <b>Эффект ожидания:</b> Прогресс-бар с задержкой создаёт иллюзию реального взлома.\n"
        "2. <b>Псевдо-данные:</b> Номера и локации генерируются случайным образом под маску страны.\n"
        "3. <b>Монетизация:</b> Требование оплаты через нативный интерфейс Telegram Stars создаёт ложное чувство безопасности.\n\n"
        + refund_note,
    )


# --------------------------------------------------------------- FLASK / WEBHOOK


@app.route("/webhook", methods=["POST"])
def webhook():
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not hmac.compare_digest(secret, WEBHOOK_SECRET):
        abort(403)
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        abort(403)
    update = telebot.types.Update.de_json(request.get_data(as_text=True))
    bot.process_new_updates([update])
    return "", 200


@app.route("/")
def index():
    return "Security Demo Bot is running!", 200


def setup_webhook():
    if not BASE_URL:
        log.warning("RENDER_EXTERNAL_URL не задан, вебхук не установлен")
        return
    try:
        bot.remove_webhook()
        bot.set_webhook(
            url=f"{BASE_URL}/webhook",
            secret_token=WEBHOOK_SECRET,
            allowed_updates=["message", "callback_query", "pre_checkout_query"],
        )
        log.info("Webhook установлен: %s/webhook", BASE_URL)
    except Exception:
        log.exception("Не удалось установить вебхук")


setup_webhook()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
