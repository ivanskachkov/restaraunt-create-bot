import math
import threading
from html import escape

import telebot
from telebot import types
from telebot.apihelper import ApiTelegramException

from . import config, db, deploy, i18n, leads, menu_admin, sales

bot = telebot.TeleBot(config.BOT_TOKEN)

PAGE_SIZE = 8

# Что бот ждёт следующим сообщением: новую цену, название блюда, фото меню.
# Живёт в памяти — состояние короткое, после перезапуска проще начать заново.
_pending = {}
_pending_lock = threading.Lock()

PENDING_PROMPTS = {
    "price": "Пришли новую цену — только число, например 149 или 12.50.",
    "rename": "Пришли новое название блюда.",
    "describe": "Пришли новое описание блюда.",
    "add": "Пришли блюдо одной строкой: <b>название; описание; цена</b>\n"
           "Например: <code>Борщ; наваристый, со сметаной; 149</code>\nОписание можно пропустить.",
    "photo": "Пришли фото меню — можно несколько подряд, я соберу блюда со всех.",
}


def _is_owner(user_id):
    return config.OWNER_ID is None or user_id == config.OWNER_ID


def _set_pending(chat_id, value):
    with _pending_lock:
        if value is None:
            _pending.pop(chat_id, None)
        else:
            _pending[chat_id] = value


def _get_pending(chat_id):
    with _pending_lock:
        return _pending.get(chat_id)


def _status_emoji(status):
    return db.STATUS_LABELS.get(status, "❔").split()[0]


def _sites_page(page):
    places, total = db.list_places(page * PAGE_SIZE, PAGE_SIZE)
    if not total:
        return "📋 Сгенерированных сайтов пока нет. Напиши название города, чтобы начать.", None

    counts = db.status_counts()
    summary = " · ".join(f"{label}: {counts.get(status, 0)}" for status, label in db.STATUS_LABELS.items())
    text = (f"📋 <b>Сгенерированные сайты: {total}</b>\n{summary}\n\n"
            "Нажми на заведение, чтобы открыть карточку, сменить статус или поправить меню.")

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
        buttons = [types.InlineKeyboardButton("📝 Текст предложения", callback_data=f"kit:{place_id}")]
        if place["menu"]:
            buttons.append(types.InlineKeyboardButton("🍽 Меню", callback_data=f"menu:{place_id}"))
        markup.row(*buttons)
    if place["status"] != "refused":
        markup.row(types.InlineKeyboardButton("🆕 Сгенерирован", callback_data=f"status:{place_id}:done"),
                   types.InlineKeyboardButton("✉️ Написал", callback_data=f"status:{place_id}:contacted"))
        markup.row(types.InlineKeyboardButton("✅ Продан", callback_data=f"status:{place_id}:sold"),
                   types.InlineKeyboardButton("❌ Отказ", callback_data=f"ask_refuse:{place_id}"))
    markup.row(types.InlineKeyboardButton("⬅️ К списку", callback_data="page:0"))
    return "\n".join(lines), markup


def _menu_screen(place):
    dishes = place["menu"]
    place_id = place["place_id"]
    markup = types.InlineKeyboardMarkup()

    if not dishes:
        markup.row(types.InlineKeyboardButton("⬅️ К заведению", callback_data=f"site:{place_id}"))
        return ("🍽 У этого сайта меню вшито в страницу — так делали до появления menu.json. "
                "Поправить его можно только новой генерацией.", markup)

    lines = [f"🍽 <b>Меню «{escape(place['name'])}»</b> — {menu_admin.summary(dishes)}"]
    if menu_admin.is_dirty(dishes):
        lines.append("⚠️ Есть неопубликованные правки — нажми «Опубликовать на сайте».")
    elif dishes.get("rev"):
        lines.append("✅ Всё опубликовано.")
    lines.append("\nНажми на блюдо, чтобы поменять цену, название или скрыть его.")

    currency = dishes.get("currency", "")
    for item in dishes["items"]:
        name = item["name"][:30] + ("…" if len(item["name"]) > 30 else "")
        label = f"{'🙈' if item.get('hidden') else '🍽'} {name} · {item['price']} {currency}".strip()
        markup.row(types.InlineKeyboardButton(label, callback_data=f"dish:{place_id}:{item['id']}"))

    markup.row(types.InlineKeyboardButton("📷 Меню с фото", callback_data=f"photo:{place_id}"),
               types.InlineKeyboardButton("➕ Блюдо", callback_data=f"additem:{place_id}"))
    if menu_admin.is_dirty(dishes):
        markup.row(types.InlineKeyboardButton("🚀 Опубликовать на сайте", callback_data=f"pub:{place_id}"))
    markup.row(types.InlineKeyboardButton("⬅️ К заведению", callback_data=f"site:{place_id}"))
    return "\n".join(lines), markup


def _dish_screen(place, item):
    place_id = place["place_id"]
    currency = place["menu"].get("currency", "")
    lines = [f"🍽 <b>{escape(item['name'])}</b>", f"Цена: {escape(item['price'])} {escape(currency)}".strip()]
    if item.get("description"):
        lines.append(f"Описание: {escape(item['description'])}")
    if item.get("name_en"):
        lines.append(f"EN: {escape(item['name_en'])}")
    if item.get("hidden"):
        lines.append("\n🙈 Блюдо скрыто — на сайте его не видно.")

    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton("💰 Цена", callback_data=f"price:{place_id}:{item['id']}"),
               types.InlineKeyboardButton("✏️ Название", callback_data=f"rename:{place_id}:{item['id']}"),
               types.InlineKeyboardButton("📝 Описание", callback_data=f"describe:{place_id}:{item['id']}"))
    markup.row(types.InlineKeyboardButton("👁 Показать" if item.get("hidden") else "🙈 Скрыть",
                                          callback_data=f"hide:{place_id}:{item['id']}"),
               types.InlineKeyboardButton("🗑 Удалить", callback_data=f"del:{place_id}:{item['id']}"))
    markup.row(types.InlineKeyboardButton("⬅️ К меню", callback_data=f"menu:{place_id}"))
    return "\n".join(lines), markup


def _photo_preview(place_id, items):
    lines = [f"📷 Распознал блюд: <b>{len(items)}</b>"]
    for item in items[:12]:
        lines.append(f"• {escape(item['name'])} — {escape(item['price'])}")
    if len(items) > 12:
        lines.append(f"…и ещё {len(items) - 12}")
    lines.append("\nМожно прислать ещё фото — блюда добавятся к этому списку.")

    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton("✅ Заменить меню этим", callback_data=f"usephoto:{place_id}:replace"))
    markup.row(types.InlineKeyboardButton("➕ Добавить к текущему", callback_data=f"usephoto:{place_id}:append"))
    markup.row(types.InlineKeyboardButton("❌ Отмена", callback_data=f"menu:{place_id}"))
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


def _send_menu_screen(chat_id, place_id):
    text, markup = _menu_screen(db.get_place(place_id))
    bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup)


def _ask(call, place_id, action, dish_id=None):
    _set_pending(call.message.chat.id, {"action": action, "place_id": place_id, "dish_id": dish_id})
    bot.send_message(call.message.chat.id, PENDING_PROMPTS[action] + "\n\nОтменить — /cancel", parse_mode="HTML")


def _publish_task(chat_id, place_id):
    place = db.get_place(place_id)
    bot.send_message(chat_id, "🚀 Публикую меню на сайте...")
    if menu_admin.publish(place_id, place["menu"]):
        bot.send_message(chat_id, f'✅ Меню обновлено: <a href="{escape(place["site_url"])}">'
                                  f'{escape(place["site_url"])}</a>', parse_mode="HTML",
                         disable_web_page_preview=True)
    else:
        bot.send_message(chat_id, "⏳ Меню закоммичено, но сайт ещё отдаёт старую версию. GitHub Pages "
                                  "иногда собирается несколько минут — проверь ссылку чуть позже.")


@bot.message_handler(commands=['start'])
def send_welcome(message):
    print(f"👤 /start от пользователя {message.from_user.id} (@{message.from_user.username})")
    if not _is_owner(message.from_user.id):
        bot.reply_to(message, "⛔ Это приватный бот.")
        return
    text = ("👋 Привет! Напиши мне название города, и я сгенерирую Landing Pages для ресторанов без сайтов.\n\n"
            "/menu — список сгенерированных сайтов, статусы и правка меню.\n"
            "/demo — случайное выдуманное заведение, чтобы потрогать результат.")
    if config.OWNER_ID is None:
        text += (f"\n\n🔓 Сейчас ботом может пользоваться кто угодно. Твой Telegram ID: {message.from_user.id} — "
                 "впиши его в .env как TELEGRAM_OWNER_ID=... и перезапусти бота.")
    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton("🎲 Сгенерировать демо-сайт", callback_data="demo"))
    bot.reply_to(message, text, reply_markup=markup)


@bot.message_handler(commands=['menu'])
def send_menu(message):
    if not _is_owner(message.from_user.id):
        return
    text, markup = _sites_page(0)
    bot.send_message(message.chat.id, text, parse_mode="HTML", reply_markup=markup)


@bot.message_handler(commands=['demo'])
def send_demo(message):
    if not _is_owner(message.from_user.id):
        return
    threading.Thread(target=leads.create_demo_task, args=(bot, message.chat.id), daemon=True).start()


@bot.message_handler(commands=['cancel'])
def cancel_pending(message):
    if not _is_owner(message.from_user.id):
        return
    _set_pending(message.chat.id, None)
    bot.reply_to(message, "Отменил. Напиши город или загляни в /menu.")


def _apply_pending(message, pending):
    place = db.get_place(pending["place_id"])
    if not place or not place["menu"]:
        _set_pending(message.chat.id, None)
        bot.reply_to(message, "Меню этого заведения больше нет.")
        return

    dishes, action, dish_id = place["menu"], pending["action"], pending.get("dish_id")
    if action == "price":
        updated = menu_admin.set_price(dishes, dish_id, message.text)
    elif action == "rename":
        updated = menu_admin.rename(dishes, dish_id, message.text)
    elif action == "describe":
        updated = menu_admin.describe(dishes, dish_id, message.text)
    else:
        parsed = menu_admin.parse_dish_line(message.text)
        updated = menu_admin.add_items(dishes, menu_admin.normalize_items([parsed])) if parsed else None

    if not updated:
        bot.reply_to(message, "Не понял. " + PENDING_PROMPTS[action] + "\n\nОтменить — /cancel", parse_mode="HTML")
        return

    db.save_menu(pending["place_id"], updated)
    _set_pending(message.chat.id, None)
    _send_menu_screen(message.chat.id, pending["place_id"])


@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    if not _is_owner(message.from_user.id):
        return
    pending = _get_pending(message.chat.id)
    if not pending or pending["action"] != "photo":
        bot.reply_to(message, "Чтобы обновить меню по фото: /menu → заведение → 🍽 Меню → 📷 Меню с фото.")
        return

    place = db.get_place(pending["place_id"])
    if not place or not place["menu"]:
        _set_pending(message.chat.id, None)
        return

    bot.send_message(message.chat.id, "⏳ Читаю меню с фото...")
    file_info = bot.get_file(message.photo[-1].file_id)  # последнее фото — в максимальном разрешении
    image = bot.download_file(file_info.file_path)
    items = menu_admin.items_from_photo(image, "image/jpeg", i18n.site_locale(place["country"]))
    if not items:
        bot.send_message(message.chat.id, "⚠️ Не нашёл на фото блюд с ценами. Попробуй кадр поближе и поровнее "
                                          "— или добавь блюдо руками кнопкой «➕ Блюдо».")
        return

    pending["items"] = pending.get("items", []) + items
    _set_pending(message.chat.id, pending)
    text, markup = _photo_preview(pending["place_id"], pending["items"])
    bot.send_message(message.chat.id, text, parse_mode="HTML", reply_markup=markup)


@bot.message_handler(content_types=['text'])
def handle_city_search(message):
    if not _is_owner(message.from_user.id):
        return
    if message.text.startswith("/"):
        bot.reply_to(message, "Не знаю такой команды. Есть /menu, /demo и /start.")
        return

    pending = _get_pending(message.chat.id)
    if pending:
        if pending["action"] == "photo":
            bot.reply_to(message, "Жду фото меню. Отменить — /cancel")
        else:
            _apply_pending(message, pending)
        return

    city_name = message.text.strip()
    thread = threading.Thread(target=leads.process_city_task, args=(bot, message.chat.id, city_name))
    thread.start()


def _handle_menu_callback(call, action, arg):
    """Экраны меню и правки. Возвращает текст всплывающей подсказки (ответ на callback — снаружи)."""
    place_id, _, dish_id = arg.partition(":")
    place = db.get_place(place_id)
    if not place or not place["menu"]:
        return "Меню не найдено"

    if action == "menu":
        _show(call, *_menu_screen(place))
        return None
    if action == "dish":
        item = menu_admin.find(place["menu"], dish_id)
        if not item:
            return "Блюдо не найдено"
        _show(call, *_dish_screen(place, item))
        return None
    if action in ("price", "rename", "describe", "add", "photo"):
        _ask(call, place_id, action, dish_id or None)
        return None

    if action == "hide":
        updated = menu_admin.toggle_hidden(place["menu"], dish_id)
    elif action == "del":
        updated = menu_admin.remove(place["menu"], dish_id)
    elif action == "usephoto":
        pending = _get_pending(call.message.chat.id) or {}
        updated = menu_admin.add_items(place["menu"], menu_admin.normalize_items(pending.get("items")),
                                       replace=dish_id == "replace")
        _set_pending(call.message.chat.id, None)
    else:
        return None

    if not updated:
        return "Не получилось — попробуй ещё раз"

    db.save_menu(place_id, updated)
    place = db.get_place(place_id)
    if action in ("del", "usephoto"):
        _show(call, *_menu_screen(place))
    else:
        _show(call, *_dish_screen(place, menu_admin.find(place["menu"], dish_id)))
    return "Готово. Осталось опубликовать"


@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    if not _is_owner(call.from_user.id):
        bot.answer_callback_query(call.id, "⛔ Нет доступа")
        return

    action, _, arg = call.data.partition(":")
    toast = None

    if action == "demo":
        threading.Thread(target=leads.create_demo_task, args=(bot, call.message.chat.id), daemon=True).start()
        toast = "Генерирую демо-сайт"
    elif action == "page":
        _show(call, *_sites_page(int(arg)))
    elif action in ("menu", "dish", "price", "rename", "describe", "additem", "photo", "hide", "del", "usephoto"):
        toast = _handle_menu_callback(call, "add" if action == "additem" else action, arg)
    elif action == "pub":
        place = db.get_place(arg)
        if not place or not place["menu"]:
            bot.answer_callback_query(call.id, "Меню не найдено")
            return
        threading.Thread(target=_publish_task, args=(call.message.chat.id, arg), daemon=True).start()
        toast = "Публикую"
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
