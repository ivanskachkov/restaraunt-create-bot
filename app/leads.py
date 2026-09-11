from html import escape
from urllib.parse import quote

from . import config, db, deploy, i18n, llm, osm, sales, site_extras


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


def format_lead_message(name, address, city, cuisine, contacts, site_url, is_live, has_ordering, locale):
    site_line = (f'🌐 <b>Готовый сайт:</b> <a href="{escape(site_url)}">{escape(site_url)}</a>' if is_live
                 else f"⚠️ <b>Сайт залит, но не открывается:</b> {escape(site_url)}\n"
                      f"(проверь, включён ли GitHub Pages в настройках репозитория)")
    ordering_line = ("🛒 На сайте есть заказ через WhatsApp" if has_ordering
                     else "🛒 Без онлайн-заказа (нет номера в международном формате для WhatsApp)")
    return "\n".join([
        f"🎯 <b>Лид: {escape(name)}</b>",
        f"📍 Адрес: {escape(address)}",
        f'🗺 <a href="{escape(maps_url(name, address, city))}">Открыть карточку в Google Maps</a>',
        f"🍕 Тип: {escape(cuisine)}",
        "",
        site_line,
        language_line(locale),
        ordering_line,
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
        place_id = p.get('place_id')
        name = p.get('name', 'Без названия')
        address = p.get('location', {}).get('formatted_address', osm.UNKNOWN_ADDRESS)
        contacts = p["contacts"]

        categories = p.get('categories', [])
        cuisine = categories[0].get('name', 'Ресторан/Кафе') if categories else 'Ресторан/Кафе'

        bot.send_message(chat_id, f"⚙️ Генерирую лендинг: {name}...")

        wa_number = sales.whatsapp_number(contacts)
        html = llm.generate_site(name, cuisine, city_name, address, contacts, locale, with_ordering=bool(wa_number))

        if html:
            html, has_ordering = site_extras.finalize(html, name, deploy.site_url(place_id), wa_number, locale)
            site_url, is_live = deploy.deploy_to_github(place_id, html)
            if site_url:
                db.save_place(place_id, name, site_url, city_name, contacts, has_ordering, locale.country)
                msg = format_lead_message(name, address, city_name, cuisine, contacts, site_url, is_live,
                                          has_ordering, locale)
                bot.send_message(chat_id, msg, parse_mode="HTML", disable_web_page_preview=True)
                send_sales_kit(bot, chat_id, name, site_url, has_ordering, locale)
                processed_count += 1

    if processed_count:
        bot.send_message(chat_id, "✅ Пакет заведений обработан. Пиши город снова для продолжения.\n"
                                  "Статусы (написал / продан / отказ) — в /menu.")
