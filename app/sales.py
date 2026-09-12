import io
from urllib.parse import quote

import qrcode

from . import config, i18n


def whatsapp_number(contacts):
    """Номер для wa.me. Только международный формат: локальный номер без кода страны дал бы чужой контакт."""
    for phone in contacts.get("phones", []):
        if phone.startswith("+"):
            return "".join(filter(str.isdigit, phone))
    return None


def whatsapp_link(number, text=None):
    return f"https://wa.me/{number}" + (f"?text={quote(text, safe='')}" if text else "")


def build_pitch(name, site_url, has_ordering, locale):
    """Предложение владельцу на языке его страны."""
    s = i18n.ui_strings(locale.lang)
    hello = i18n.fill(s["pitch_hello_named"], seller=config.SELLER_NAME) if config.SELLER_NAME else s["pitch_hello"]
    features = [s["feature_menu"]]
    if has_ordering:
        features.append(s["feature_ordering"])
    features.append(s["feature_contacts"])
    features.append(i18n.fill(s["feature_mobile_bilingual"], language=locale.language_name) if locale.bilingual
                    else s["feature_mobile"])
    return "\n".join([
        hello,
        "",
        i18n.fill(s["pitch_noticed"], name=name),
        site_url,
        "",
        s["pitch_features_title"],
        *features,
        "",
        s["pitch_outro"],
    ])


def build_short_pitch(name, site_url, locale):
    """Для ссылки wa.me с готовым текстом: полный текст после URL-кодирования слишком длинный для Telegram."""
    return i18n.fill(i18n.ui_strings(locale.lang)["short_pitch"], name=name, url=site_url)


def make_qr_png(url):
    img = qrcode.make(url, box_size=12, border=3)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf
