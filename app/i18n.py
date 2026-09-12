"""Язык и валюта сайта по стране заведения, плюс тексты, которые код вставляет на страницу и в предложение владельцу."""
import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from babel import Locale
from babel.languages import get_official_languages
from babel.numbers import get_currency_symbol, get_territory_currencies

from . import config, llm

# У записей, созданных до интернационализации, страна не сохранялась — все они из Украины
DEFAULT_COUNTRY = "UA"

EN = {
    "demo_banner": "Demo concept of a website for «{name}». Not the official website of this venue.",
    "og_title": "{name} — website demo",
    "og_description": "Website concept for «{name}»: menu and contacts.",
    "og_description_ordering": "Website concept for «{name}»: menu, contacts and online ordering.",
    "add_to_order": "+ Add to order",
    "cart": "Order",
    "your_order": "Your order",
    "table": "Table",
    "total": "Total",
    "send_whatsapp": "Send order via WhatsApp",
    "order_from_site": "New order from the website",
    "pitch_hello_named": "Hello! My name is {seller}, I make websites for cafes and restaurants.",
    "pitch_hello": "Hello! I make websites for cafes and restaurants.",
    "pitch_noticed": "I noticed that «{name}» doesn't have its own website yet, so I made a demo version for you — "
                     "take a look at how it could look:",
    "pitch_features_title": "What's already there:",
    "feature_menu": "• a menu with prices and dish descriptions",
    "feature_ordering": "• ordering right from the website — orders go straight to your WhatsApp",
    "feature_contacts": "• contacts, opening hours and a call button",
    "feature_mobile_bilingual": "• a mobile-friendly version, in {language} and English",
    "feature_mobile": "• a mobile-friendly version",
    "pitch_outro": "The menu and texts in the demo are just an example — I'll replace them with yours. "
                   "If you're interested, write back and I'll tell you the details and the price.",
    "short_pitch": "Hello! I made a demo website for «{name}» — take a look at how it could look: {url}\n"
                   "If you're interested, I'll tell you the details and the price.",
}

UK = {
    "demo_banner": "Демо-концепт сайту для «{name}». Не є офіційним сайтом закладу.",
    "og_title": "{name} — демо сайту",
    "og_description": "Демо-концепт сайту для «{name}»: меню, контакти.",
    "og_description_ordering": "Демо-концепт сайту для «{name}»: меню, контакти та онлайн-замовлення.",
    "add_to_order": "+ Додати до замовлення",
    "cart": "Замовлення",
    "your_order": "Ваше замовлення",
    "table": "Столик",
    "total": "Разом",
    "send_whatsapp": "Надіслати замовлення в WhatsApp",
    "order_from_site": "Замовлення з сайту",
    "pitch_hello_named": "Вітаю! Мене звати {seller}, я роблю сайти для кафе та ресторанів.",
    "pitch_hello": "Вітаю! Я роблю сайти для кафе та ресторанів.",
    "pitch_noticed": "Помітив, що у «{name}» поки немає власного сайту, тож зробив для вас демо-версію — "
                     "подивіться, як це може виглядати:",
    "pitch_features_title": "Що вже є:",
    "feature_menu": "• меню з цінами та описом страв",
    "feature_ordering": "• замовлення прямо з сайту — воно одразу приходить вам у WhatsApp",
    "feature_contacts": "• контакти, графік роботи та кнопка дзвінка",
    "feature_mobile_bilingual": "• зручна версія для телефону, {language} та англійська мови",
    "feature_mobile": "• зручна версія для телефону",
    "pitch_outro": "Меню й тексти в демо — приклад, я заміню їх вашими. Якщо цікаво — напишіть, "
                   "розповім деталі та вартість.",
    "short_pitch": "Вітаю! Зробив демо-сайт для «{name}» — подивіться, як це може виглядати: {url}\n"
                   "Якщо цікаво, розповім деталі та вартість.",
}

_BUILTIN = {"en": EN, "uk": UK}
_PLACEHOLDER = re.compile(r"\{\w+\}")
_cache_lock = threading.Lock()


@dataclass(frozen=True)
class SiteLocale:
    country: str          # "ES"
    lang: str             # "es"
    currency_code: str    # "EUR"
    currency_symbol: str  # "€"

    @property
    def bilingual(self):
        """Местный язык + английский. Для англоязычных стран сайт одноязычный."""
        return self.lang != "en"

    @property
    def og_locale(self):
        return f"{self.lang.split('_')[0]}_{self.country}"

    @property
    def language_name(self):
        """Название языка на нём самом: español, Deutsch, українська."""
        return Locale.parse(self.lang).get_language_name(self.lang)


def site_locale(country_code):
    country = (country_code or DEFAULT_COUNTRY).upper()
    languages = get_official_languages(country, de_facto=True)
    lang = languages[0] if languages else "en"
    currencies = get_territory_currencies(country)
    currency = currencies[0] if currencies else "USD"
    if currency == "UAH":
        symbol = "грн"  # в украинских меню пишут "грн", а не "₴"
    else:
        try:
            symbol = get_currency_symbol(currency, locale=f"{lang}_{country}")
        except Exception:
            symbol = get_currency_symbol(currency, locale="en")
    return SiteLocale(country, lang, currency, symbol)


def fill(template, **values):
    """Подстановка через replace, а не str.format: в переведённом тексте могут встретиться лишние скобки."""
    for key, value in values.items():
        template = template.replace("{" + key + "}", str(value))
    return template


def _valid_translation(translated):
    return (isinstance(translated, dict) and translated.keys() == EN.keys()
            and all(isinstance(translated[k], str)
                    and sorted(_PLACEHOLDER.findall(translated[k])) == sorted(_PLACEHOLDER.findall(v))
                    for k, v in EN.items()))


def _load_cache():
    try:
        return json.loads(Path(config.TRANSLATIONS_CACHE_PATH).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def ui_strings(lang):
    """Украинский и английский встроены; остальные языки один раз переводятся через Gemini и кэшируются."""
    if lang in _BUILTIN:
        return _BUILTIN[lang]
    with _cache_lock:
        cache = _load_cache()
        if lang not in cache:
            translated = llm.translate_strings(EN, lang)
            if not _valid_translation(translated):
                print(f"⚠️ Не удалось перевести тексты на {lang} — будут на английском.")
                return EN
            cache[lang] = translated
            Path(config.TRANSLATIONS_CACHE_PATH).write_text(json.dumps(cache, ensure_ascii=False, indent=2),
                                                            encoding="utf-8")
        return cache[lang]
