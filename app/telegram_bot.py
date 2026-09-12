import math
import threading
from html import escape

import telebot
from telebot import types
from telebot.apihelper import ApiTelegramException

from . import config, db, deploy, i18n, leads, sales

bot = telebot.TeleBot(config.BOT_TOKEN)

PAGE_SIZE = 8


def _is_owner(user_id):
    return config.OWNER_ID is None or user_id == config.OWNER_ID


def _status_emoji(status):
    return db.STATUS_LABELS.get(status, "❔").split()[0]


def _sites_page(page):
    places, total = db.list_places(page * PAGE_SIZE, PAGE_SIZE)
    if not total:
        return "📋 Сгенерированных сайтов пока нет. Напиши название города, чтобы начать.", None

    counts = db.status_counts()
    summary = " · ".join(f"{label}: {counts.get(status, 0)}" for status, label in db.STATUS_LABELS.items())
    text = (f"📋 <b>Сгенерированные сайты: {total}</b>\n{summary}\n\n"
            "Нажми на заведение, чтобы открыть карточку и сменить статус.")

    markup = types.InlineKeyboardMarkup()
    for place in places:
        label = f"{_status_emoji(place['status'])} {place['name']}" + (f" · {place['city']}" if place["city"] else "")
        markup.row(types.InlineKeyboardButton(label, callback_data=f"site:{place['place_id']}"))

    pages = math.ceil(total / PAGE_SIZE)
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(types.InlineKeyboardButton("◀️", callback_data=f"page:{page - 1}"))
        nav.append(types.InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="noop"))
        if page < pages - 1:
            nav.append(types.InlineKeyboardButton("▶️", callback_data=f"page:{page + 1}"))
        markup.row(*nav)
    return text, markup


def _site_card(place):
    if place["site_url"]:
        site_line = f'🌐 <a href="{escape(place["site_url"])}">{escape(place["site_url"])}</a>'
    elif place["status"] == "refused":
        site_line = "🌐 Сайт удалён после отказа"
    else:
        site_line = "🌐 Сайт не сохранился (старая версия бота)"

    lines = [
        f"🏷 <b>{escape(place['name'])}</b>",
        f"Статус: {db.STATUS_LABELS.get(place['status'], place['status'])}",
    ]
    if place["city"]:
        lines.append(f"Город: {escape(place['city'])}")
    if place["created_at"]:
        lines.append(f"Создан: {place['created_at']}")
    lines += ["", site_line]
    locale = i18n.site_locale(place["country"])
    lines.append(leads.language_line(locale))
    if place["site_url"]:
        lines.append(leads.menu_line(place["menu"]))
    wa_text = sales.build_short_pitch(place["name"], place["site_url"], locale) if place["site_url"] else None
    if place["contacts"]:
        lines += ["", "📞 <b>Контакты:</b>", *leads.contact_lines(place["contacts"], wa_text)]

    place_id = place["place_id"]
    markup = types.InlineKeyboardMarkup()
    if place["site_url"]:
        markup.row(types.InlineKeyboardButton("📝 Текст предложения", callback_data=f"kit:{place_id}"))
    if place["status"] != "refused":
        markup.row(types.InlineKeyboardButton("🆕 Сгенерирован", callback_data=f"status:{place_id}:done"),
                   types.InlineKeyboardButton("✉️ Написал", callback_data=f"status:{place_id}:contacted"))
        markup.row(types.InlineKeyboardButton("✅ Продан", callback_data=f"status:{place_id}:sold"),
                   types.InlineKeyboardButton("❌ Отказ", callback_data=f"ask_refuse:{place_id}"))
    markup.row(types.InlineKeyboardButton("⬅️ К списку", callback_data="page:0"))
    return "\n".join(lines), markup


def _refuse_confirmation(place):
    text = (f"❌ Отметить отказ от <b>{escape(place['name'])}</b>?\n\n"
            "Демо-сайт будет удалён, а заведение больше никогда не попадёт в лиды.")
    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton("Да, удалить сайт", callback_data=f"refuse:{place['place_id']}"),
               types.InlineKeyboardButton("Отмена", callback_data=f"site:{place['place_id']}"))
    return text, markup


def _show(call, text, markup):
    try:
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode="HTML",
                              reply_markup=markup, disable_web_page_preview=True)
    except ApiTelegramException as e:
        # Повторное нажатие на ту же кнопку — Telegram ругается, что текст не изменился
        if "message is not modified" not in str(e):
            raise


@bot.message_handler(commands=['start'])
def send_welcome(message):
    print(f"👤 /start от пользователя {message.from_user.id} (@{message.from_user.username})")
    if not _is_owner(message.from_user.id):
        bot.reply_to(message, "⛔ Это приватный бот.")
        return
    text = ("👋 Привет! Напиши мне название города, и я сгенерирую Landing Pages для ресторанов без сайтов.\n\n"
            "/menu — список сгенерированных сайтов и их статусы.")
    if config.OWNER_ID is None:
        text += (f"\n\n🔓 Сейчас ботом может пользоваться кто угодно. Твой Telegram ID: {message.from_user.id} — "
                 "впиши его в .env как TELEGRAM_OWNER_ID=... и перезапусти бота.")
    bot.reply_to(message, text)


@bot.message_handler(commands=['menu'])
def send_menu(message):
    if not _is_owner(message.from_user.id):
        return
    text, markup = _sites_page(0)
    bot.send_message(message.chat.id, text, parse_mode="HTML", reply_markup=markup)


@bot.message_handler(content_types=['text'])
def handle_city_search(message):
    if not _is_owner(message.from_user.id):
        return
    if message.text.startswith("/"):
        bot.reply_to(message, "Не знаю такой команды. Есть /menu и /start.")
        return
    city_name = message.text.strip()
    thread = threading.Thread(target=leads.process_city_task, args=(bot, message.chat.id, city_name))
    thread.start()


@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    if not _is_owner(call.from_user.id):
        bot.answer_callback_query(call.id, "⛔ Нет доступа")
        return

    action, _, arg = call.data.partition(":")
    toast = None

    if action == "page":
        _show(call, *_sites_page(int(arg)))
    elif action == "kit":
        place = db.get_place(arg)
        if not place or not place["site_url"]:
            bot.answer_callback_query(call.id, "У этого заведения нет сайта")
            return
        leads.send_sales_kit(bot, call.message.chat.id, place["name"], place["site_url"], place["has_ordering"],
                             i18n.site_locale(place["country"]))
    elif action in ("site", "status", "ask_refuse", "refuse"):
        place_id = arg.rsplit(":", 1)[0] if action == "status" else arg
        place = db.get_place(place_id)
        if not place:
            bot.answer_callback_query(call.id, "Запись не найдена")
            return

        if action == "status":
            status = arg.rsplit(":", 1)[1]
            # Отказ — только через подтверждение, иначе сайт остался бы висеть публично
            if status not in ("done", "contacted", "sold") or place["status"] == "refused":
                bot.answer_callback_query(call.id, "Этот статус здесь недоступен")
                return
            db.set_status(place_id, status)
            place = db.get_place(place_id)
            toast = f"Статус: {db.STATUS_LABELS[status]}"
        elif action == "ask_refuse":
            _show(call, *_refuse_confirmation(place))
            bot.answer_callback_query(call.id)
            return
        elif action == "refuse":
            if not deploy.delete_site(place_id):
                bot.answer_callback_query(call.id, "Не удалось удалить сайт с GitHub, попробуй ещё раз",
                                          show_alert=True)
                return
            db.set_status(place_id, "refused", clear_site=True)
            place = db.get_place(place_id)
            toast = "Сайт удалён, заведение больше не попадёт в лиды"

        _show(call, *_site_card(place))

    bot.answer_callback_query(call.id, toast)
