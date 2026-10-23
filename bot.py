import os
import time
import random
import logging
from flask import Flask, request
import telebot
from telebot.types import (
    InlineKeyboardMarkup, 
    InlineKeyboardButton, 
    LabeledPrice
)

# На Render токен лучше скрыть в Environment Variables, но можно вписать и сюда
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL")  # Render подставляет этот URL сам

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="Markdown")
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

user_country_pref = {}

COUNTRY_DATA = {
    "UA": {
        "name": "Украина",
        "code": "+380",
        "mask": "+380 ({operator}) ***-**-{last_two}",
        "operators": ["67", "68", "97", "50", "66", "73", "93"],
        "cities": ["Киев, Шевченковский р-н", "Днепр, Соборный р-н", "Одесса, Приморский р-н", "Львов, Галицкий р-н"]
    },
    "US": {
        "name": "США",
        "code": "+1",
        "mask": "+1 ({operator}) ***-**-{last_two}",
        "operators": ["212", "310", "415", "305"],
        "cities": ["New York, NY", "Los Angeles, CA", "Miami, FL", "Chicago, IL"]
    },
    "PL": {
        "name": "Польша",
        "code": "+48",
        "mask": "+48 ({operator}) ***-**-{last_two}",
        "operators": ["50", "51", "60", "79"],
        "cities": ["Warszawa, Śródmieście", "Kraków, Stare Miasto", "Wrocław"]
    }
}

def generate_masked_phone(country_code: str):
    cdata = COUNTRY_DATA.get(country_code, COUNTRY_DATA["UA"])
    op = random.choice(cdata["operators"])
    last_two = f"{random.randint(10, 99)}"
    masked_phone = cdata["mask"].format(operator=op, last_two=last_two)
    city = random.choice(cdata["cities"])
    return masked_phone, city


@bot.message_handler(commands=['start'])
def cmd_start(message):
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton(text="🇺🇦 Украина (+380)", callback_data="select_UA"))
    kb.add(InlineKeyboardButton(text="🇺🇸 США (+1)", callback_data="select_US"))
    kb.add(InlineKeyboardButton(text="🇵🇱 Польша (+48)", callback_data="select_PL"))

    bot.send_message(
        message.chat.id,
        "🕵️ **Telegram OSINT / Spy Intelligence Bot v5.0**\n\n"
        "Система глубокого анализа данных и перехвата сессий.\n"
        "Выберите страну предполагаемого проживания/регистрации цели:",
        reply_markup=kb
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith("select_"))
def process_country_select(call):
    country_code = call.data.split("_")[1]
    user_country_pref[call.from_user.id] = country_code

    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"✅ Страна выбрана: **{COUNTRY_DATA[country_code]['name']}**\n\n"
             f"👇 Отправьте `@username` цели или её **Telegram ID** для начала сканирования:"
    )
    bot.answer_callback_query(call.id)


@bot.message_handler(func=lambda message: True and not message.text.startswith('/'))
def process_target_scan(message):
    target = message.text.strip()
    user_id = message.from_user.id
    country_code = user_country_pref.get(user_id, "UA")

    status_msg = bot.send_message(message.chat.id, "🚀 Инициализация сеанса сканирования...")

    total_steps = 10
    step_delay = 2.0 

    stages = [
        "Анализ открытых узлов Telegram API...",
        "Перехват публичных метаданных...",
        "Запрос к базам геолокации GSM-вышек...",
        "Анализ совпадений списков контактов...",
        "Дешифровка фрагментов профиля...",
        "Определение привязанного номера телефона...",
        "Сбор списка общих чатов и медиа...",
        "Формирование приватного архива...",
        "Сжатие данных в защищенный контейнер...",
        "Завершение формирования отчета..."
    ]

    for i in range(1, total_steps + 1):
        percent = i * 10
        bar_filled = "█" * i
        bar_empty = "░" * (total_steps - i)

        text = (
            f"⚙️ **Сканирование объекта:** `{target}`\n"
            f"[{bar_filled}{bar_empty}] **{percent}%**\n\n"
            f"🔄 *Текущий этап:* {stages[i-1]}"
        )
        try:
            bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=status_msg.message_id,
                text=text
            )
        except Exception:
            pass
        time.sleep(step_delay)

    masked_phone, city = generate_masked_phone(country_code)

    report_text = (
        f"🎯 **Результат сканирования цели:** `{target}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 **Регион / Геопозиция:** {city}\n"
        f"📱 **Маскированный номер:** `{masked_phone}`\n"
        f"💬 **Перехвачено сообщений:** {random.randint(1400, 8900)}\n"
        f"🖼 **Найдено скрытых фото/медиа:** {random.randint(12, 85)}\n"
        f"🔐 **Активные сессии:** 2 устройства (iOS / Web)\n\n"
        f"⚠️ **Внимание:** Полный архив переписок и расшифровка номера заблокированы.\n"
        f"Для снятия шифрования требуется оплата сервисного сбора сети."
    )

    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton(text="⭐ Разблокировать архив (1 Star)", callback_data="pay_stars"))

    bot.delete_message(message.chat.id, status_msg.message_id)
    bot.send_message(message.chat.id, report_text, reply_markup=kb)


@bot.callback_query_handler(func=lambda call: call.data == "pay_stars")
def send_stars_invoice(call):
    prices = [LabeledPrice(label="Архив расшифровки", amount=1)]
    bot.send_invoice(
        chat_id=call.message.chat.id,
        title="🔓 Доступ к архиву переписок",
        description="Разблокировка полного отчета, точности GPS и расшифрованного номера.",
        invoice_payload="spy_archive_payload",
        provider_token="",
        currency="XTR",
        prices=prices,
        start_parameter="spy-archive-unlock"
    )
    bot.answer_callback_query(call.id)


@bot.pre_checkout_query_handler(func=lambda query: True)
def process_pre_checkout(query):
    bot.answer_pre_checkout_query(pre_checkout_query_id=query.id, ok=True)


@bot.message_handler(content_types=['successful_payment'])
def process_successful_payment(message):
    bot.send_message(
        message.chat.id,
        "🛑 **ДЕМОНСТРАЦИОННЫЙ СТЕНД БЕЗОПАСНОСТИ**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🎉 **Вы совершили тестовую оплату Звёздами!**\n\n"
        "👨‍🏫 **Объяснение для презентации (Как работает скам):**\n"
        "1. **Иллюзия работы:** Прогресс-бар с паузами создал ложное ощущение, что бот выполнял «сложные серверные вычисления».\n"
        "2. **Маскировка номера:** Маска `+380 (67) ***-**-89` была сгенерирована случайно на основе шаблонов операторов выбранной страны. Реальный номер бот не знает.\n"
        "3. **Финансовый ущерб:** В реальном мошенническом боте пользователь теряет реальные деньги (или Telegram Stars), но в ответ получает либо ничего, либо пустой файл/шаблоны.\n\n"
        "💡 *Telegram Bot API строго изолирован и не имеет физической возможности читать чужие чаты или определять геолокацию без согласия пользователя.*"
    )


# --- НАСТРОЙКА WEBHOOK ДЛЯ FLASK И RENDER ---

@app.route(f'/{BOT_TOKEN}', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return '', 200
    else:
        return 'Forbidden', 403


@app.route('/')
def index():
    return "Security Demo Bot is running!", 200


if __name__ == "__main__":
    # Устанавливаем вебхук при старте приложения на Render
    if RENDER_EXTERNAL_URL:
        bot.remove_webhook()
        bot.set_webhook(url=f"{RENDER_EXTERNAL_URL}/{BOT_TOKEN}")
    
    # Render автоматически передает переменную PORT
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
