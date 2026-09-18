import json
import threading
from html import escape
from urllib.parse import quote

from . import config, db, demo, deploy, i18n, llm, menu, menu_admin, osm, sales, site_extras


def maps_url(name, address, city):
    # Поиск по названию и адресу открывает карточку заведения (отзывы, фото, реальный сайт),
    # а поиск по координатам — просто пин на карте
    parts = [name] + ([address] if address != osm.UNKNOWN_ADDRESS else []) + [city]
    return "https://www.google.com/maps/search/?api=1&query=" + quote(", ".join(parts), safe="")


def contact_lines(contacts, wa_text=None):
    """wa_text — если задан, ссылка WhatsApp откроет чат с уже вписанным текстом."""
    lines = [f"☎️ {escape(phone)}" for phone in contacts["phones"]]
    wa_number = sales.whatsapp_number(contacts)
    if wa_number:
        label = "Написать в WhatsApp с готовым текстом" if wa_text else "WhatsApp"
        lines.append(f'💬 <a href="{escape(sales.whatsapp_link(wa_number, wa_text))}">{label}</a>')
    for label, url in contacts["socials"].items():
        lines.append(f'📱 <a href="{escape(url)}">{label}</a>')
    if contacts["email"]:
        lines.append(f"✉️ {escape(contacts['email'])}")
    return lines


def language_line(locale):
    languages = f"{locale.language_name} + English" if locale.bilingual else "English"
    return f"🌍 Язык сайта: {escape(languages)} · валюта {escape(locale.currency_code)}"


def _site_line(site_url, status):
    link = f'<a href="{escape(site_url)}">{escape(site_url)}</a>'
    if status == deploy.SITE_LIVE:
        return f"🌐 <b>Готовый сайт:</b> {link}"
    if status == deploy.SITE_BUILDING:
        return (f"⏳ <b>Сайт собирается на GitHub:</b> {link}\n"
                "Обычно это занимает 1–5 минут. Пришлю сообщение, когда он откроется, — вместе с текстом "
                "предложения, чтобы ты не отправил владельцу нерабочую ссылку.")
    return (f"⚠️ <b>Сайт залит, но GitHub Pages выключен:</b> {link}\n"
            "Включи: Settings → Pages → Source: Deploy from a branch → main / (root)")


def menu_line(dishes):
    if not dishes:
        return "🍽 Меню вшито в страницу — правится только перегенерацией"
    return f"🍽 Меню: {menu_admin.summary(dishes)} — правится из бота, без перегенерации сайта"


def format_lead_message(name, address, city, cuisine, contacts, site_url, status, has_ordering, locale, dishes):
    ordering_line = ("🛒 На сайте есть заказ через WhatsApp" if has_ordering
                     else "🛒 Без онлайн-заказа (нет номера в международном формате для WhatsApp)")
    return "\n".join([
        f"🎯 <b>Лид: {escape(name)}</b>",
        f"📍 Адрес: {escape(address)}",
        f'🗺 <a href="{escape(maps_url(name, address, city))}">Открыть карточку в Google Maps</a>',
        f"🍕 Тип: {escape(cuisine)}",
        f"🕒 График в OSM: {escape(contacts['opening_hours'])}",
        "",
        _site_line(site_url, status),
        language_line(locale),
        ordering_line,
        menu_line(dishes),
        "",
        "📞 <b>Контакты:</b>",
        *contact_lines(contacts, sales.build_short_pitch(name, site_url, locale)),
    ])


def send_sales_kit(bot, chat_id, name, site_url, has_ordering, locale):
    """Текст предложения отдельным сообщением на языке владельца — удобно копировать в Instagram."""
    bot.send_message(chat_id, sales.build_pitch(name, site_url, has_ordering, locale), disable_web_page_preview=True)
    # QR-код временно отключён. Чтобы вернуть — раскомментировать (и вернуть пункт про QR в тексты i18n):
    # caption = f"🔳 QR-код на сайт «{name}»"
    # if has_ordering:
    #     caption += (f"\n\nДля конкретного столика добавь в ссылку номер — тогда он придёт в заказе:\n"
    #                 f"{site_url}?table=5")
    # bot.send_photo(chat_id, sales.make_qr_png(site_url), caption=caption)


def _notify_when_live(bot, chat_id, name, site_url, has_ordering, locale):
    if deploy.wait_until_live(site_url, config.PAGES_FOLLOWUP_SECONDS):
        bot.send_message(chat_id, f'✅ Сайт «{escape(name)}» открылся: <a href="{escape(site_url)}">{escape(site_url)}</a>',
                         parse_mode="HTML", disable_web_page_preview=True)
        send_sales_kit(bot, chat_id, name, site_url, has_ordering, locale)
    else:
        minutes = config.PAGES_FOLLOWUP_SECONDS // 60
        bot.send_message(chat_id, f"⚠️ Сайт «{name}» так и не открылся за {minutes} минут. Проверь вкладку Actions "
                                  f"в репозитории {config.GITHUB_REPO}: там видно, собралась ли страница.")


def generate_and_publish(bot, chat_id, place_id, name, cuisine, city, address, contacts, locale, note=None):
    """Генерирует сайт, публикует его и присылает карточку. Общий путь для лидов и демо."""
    bot.send_message(chat_id, f"⚙️ Генерирую лендинг: {name}...")

    wa_number = sales.whatsapp_number(contacts)
    html = llm.generate_site(name, cuisine, city, address, contacts, locale, with_ordering=bool(wa_number))
    if not html:
        bot.send_message(chat_id, f"⚠️ Не получилось сгенерировать сайт для «{name}». Попробуй ещё раз.")
        return False

    html, has_ordering, dishes = site_extras.finalize(html, name, deploy.site_url(place_id), wa_number, locale)
    files = {"index.html": html}
    if dishes:
        files[menu.MENU_FILE] = json.dumps(dishes, ensure_ascii=False, indent=2)

    site_url, status = deploy.deploy_to_github(place_id, files)
    if not site_url:
        bot.send_message(chat_id, f"⚠️ Не получилось опубликовать сайт «{name}» на GitHub.")
        return False

    db.save_place(place_id, name, site_url, city, contacts, has_ordering, locale.country, dishes)
    message = format_lead_message(name, address, city, cuisine, contacts, site_url, status, has_ordering,
                                  locale, dishes)
    bot.send_message(chat_id, message + (f"\n\n{note}" if note else ""), parse_mode="HTML",
                     disable_web_page_preview=True)

    if status == deploy.SITE_LIVE:
        send_sales_kit(bot, chat_id, name, site_url, has_ordering, locale)
    elif status == deploy.SITE_BUILDING:
        threading.Thread(target=_notify_when_live, daemon=True,
                         args=(bot, chat_id, name, site_url, has_ordering, locale)).start()
    return True


def create_demo_task(bot, chat_id):
    venue = demo.random_venue()
    locale = i18n.site_locale(venue["country"])
    bot.send_message(chat_id, f"🎲 Демо-заведение: <b>{escape(venue['name'])}</b> · {escape(venue['city'])}\n"
                              f"{language_line(locale)}", parse_mode="HTML")
    note = ("🧪 <b>Это демо:</b> заведение и контакты выдуманы, продавать его некому — можно спокойно "
            "всё нажимать.\nПравка меню: /menu → карточка → 🍽 Меню.")
    generate_and_publish(bot, chat_id, venue["place_id"], venue["name"], venue["cuisine"], venue["city"],
                         venue["address"], venue["contacts"], locale, note)


def process_city_task(bot, chat_id, city_name):
    bot.send_message(chat_id, f"🔍 Ищу рестораны без сайтов: {city_name}...")

    try:
        search = osm.fetch_leads(city_name, db.processed_ids(), config.MAX_SITES_PER_REQUEST)
    except osm.CityNotFoundError:
        bot.send_message(chat_id, "⚠️ Не нашёл такой город или регион. Проверь название и попробуй ещё раз.")
        return
    except osm.OverpassUnavailableError:
        bot.send_message(chat_id, "⚠️ OpenStreetMap сейчас недоступен. Попробуй через пару минут.")
        return

    if not search.total_count:
        bot.send_message(chat_id, "Ничего не найдено. Попробуй другой город.")
        return

    if not search.leads:
        bot.send_message(chat_id, f"Все {search.total_count} подходящих заведений в этом регионе уже в работе "
                                  f"— см. /menu.")
        return

    locale = i18n.site_locale(search.country_code)
    bot.send_message(chat_id, f"🎯 Новых лидов: {search.new_count} (всего подходящих: {search.total_count}). "
                              f"Генерирую {len(search.leads)} сайтов...\n{language_line(locale)}",
                     parse_mode="HTML")

    processed_count = 0
    for p in search.leads:
        categories = p.get('categories', [])
        cuisine = categories[0].get('name', 'Ресторан/Кафе') if categories else 'Ресторан/Кафе'
        if generate_and_publish(bot, chat_id, p.get('place_id'), p.get('name', 'Без названия'), cuisine, city_name,
                                p.get('location', {}).get('formatted_address', osm.UNKNOWN_ADDRESS),
                                p["contacts"], locale):
            processed_count += 1

    if processed_count:
        bot.send_message(chat_id, "✅ Пакет заведений обработан. Пиши город снова для продолжения.\n"
                                  "Статусы (написал / продан / отказ) — в /menu.")
