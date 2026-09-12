import json
import re
import time
from datetime import date

import requests

from . import config


def _contacts_block(address, contacts):
    lines = [f"- Адрес: {address}"]
    if contacts["phones"]:
        lines.append(f"- Телефон: {', '.join(contacts['phones'])}")
    if contacts["email"]:
        lines.append(f"- Email: {contacts['email']}")
    if contacts["opening_hours"]:
        lines.append(f"- Время работы (формат OpenStreetMap, переведи в понятный вид): {contacts['opening_hours']}")
    for label, url in contacts["socials"].items():
        lines.append(f"- {label}: {url}")
    return "\n".join(lines)


def build_prompt(name, cuisine, city, address, contacts, locale, with_ordering):
    lang = locale.lang
    booking = (f'ссылка tel: на номер {contacts["phones"][0]}' if contacts["phones"]
               else 'якорь на блок "Контакты" (#contacts)')
    # Разметка карточек обязательна: по ней код вынимает меню в отдельный файл menu.json,
    # чтобы владелец мог править блюда и цены без повторной генерации страницы (см. menu.py)
    translated_attrs = (f' data-dish-en="<название блюда по-английски>" '
                        f'data-description-en="<описание блюда по-английски>"' if locale.bilingual else "")
    menu = (
        'Блок "Меню" (id="menu", Grid на 6 карточек блюд). Все карточки размечены строго одинаково:\n'
        f'      * корневой элемент карточки: data-dish="<название блюда на языке {lang}>" '
        f'data-price="<цена числом, без валюты>" data-currency="{locale.currency_symbol}"{translated_attrs};\n'
        '      * внутри карточки ровно по одному элементу с атрибутами data-field="name" (только название блюда), '
        'data-field="description" (только описание) и data-field="price" (цена вместе с символом валюты);\n'
        '      * никакого другого текста в этих трёх элементах быть не должно.'
        + (' Кнопок заказа в карточки не добавляй — их добавит отдельный скрипт.' if with_ordering else "")
    )

    if locale.bilingual:
        languages = (f'Язык сайта — {lang} (основной, по умолчанию) и английский. '
                     f'В шапке кнопка переключения языков {lang.upper()}/EN.')
        logic = (f'Внутри <script> напиши логику переключателя языков: все тексты меняются между {lang} '
                 'и английским. Тексты придумай сам. Переключатель обязан выставлять '
                 f'document.documentElement.lang ("{lang}" или "en"). Блюда из блока "Меню" в переключатель '
                 'не включай: их подставляет и переводит отдельный скрипт по атрибутам data-*-en.')
    else:
        languages = 'Язык сайта — английский, переключателя языков нет.'
        logic = 'Тексты придумай сам.'

    return f"""
    Ты Senior Frontend Developer. Создай премиальный, полностью рабочий Landing Page одним HTML файлом для заведения "{name}".
    Тип/кухня: {cuisine}. Город: {city}.
    {languages} Укажи <html lang="{lang}">.
    Цены — в валюте {locale.currency_code}, на карточках пиши их с символом {locale.currency_symbol}.
    Блюда и тексты — естественные для этой страны и кухни.

    Реальные контактные данные заведения (других нет):
{_contacts_block(address, contacts)}

    Технические требования:
    1. Использовать Tailwind CSS через CDN (<script src="https://cdn.tailwindcss.com"></script>).
    2. Добавить библиотеку FontAwesome для иконок.
    3. Подключить Google Fonts (с поддержкой символов языка {lang}).
    4. Вернуть ТОЛЬКО HTML код, без Markdown-блоков.

    Структура страницы:
    - Навигация (Sticky Header): Логотип ({name}), ссылки на секции{", переключатель языков" if locale.bilingual else ""}.
    - Hero Section (id="hero"): Большой фоновый цвет/градиент, заголовок, подзаголовок, кнопка бронирования — {booking}.
    - Блок "О нас" (id="about"): Приятный текст о концепции и кухне.
    - {menu}
    - Блок "Контакты" (id="contacts"): только реальные данные из списка выше.
    - Footer: Копирайт с текущим годом ({date.today().year}).

    Строгие правила для контактов и ссылок:
    - Используй ТОЛЬКО контактные данные из списка выше. Не придумывай телефоны, email, время работы и адреса.
      Если какого-то пункта в списке нет — не показывай его вообще, без заглушек вроде "уточняйте".
    - Иконки соцсетей — только для соцсетей из списка, с точно этими URL, target="_blank" rel="noopener".
      Если соцсетей в списке нет — никакого блока соцсетей и никаких иконок соцсетей на странице.
    - Все внутренние ссылки (навигация, логотип, кнопки) — только якоря на секции этой страницы
      (#hero, #about, #menu, #contacts). Никаких ссылок на другие страницы и на корень сайта
      ("/", "/home", "index.html") и никаких пустых href="#".

    Логика:
    {logic}
    """


RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}  # временная перегрузка/лимиты — имеет смысл повторить


def _candidate_models():
    seen = set()
    for model in [config.LLM_MODEL, *config.LLM_MODEL_FALLBACKS]:
        if model not in seen:
            seen.add(model)
            yield model


def _strip_fences(text):
    return re.sub(r'^```[a-zA-Z]*\s*|```\s*$', '', text.strip(), flags=re.MULTILINE).strip()


def _generate(prompt, temperature, is_complete, what):
    """Запрос к Gemini с перебором моделей. Возвращает текст ответа без markdown-обёртки или None."""
    headers = {'Content-Type': 'application/json'}
    # 8192 не хватало: у моделей с "мышлением" размышления тратят тот же бюджет,
    # и на сам HTML оставалось ~300 токенов — страница приходила обрезанной.
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": 32768}
    }

    for model in _candidate_models():
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={config.LLM_API_KEY}"

        for attempt, timeout in enumerate(config.LLM_TIMEOUTS, start=1):
            is_last_attempt = attempt == len(config.LLM_TIMEOUTS)
            try:
                print(f"   ⏳ {what} ({model}, попытка {attempt})...")
                response = requests.post(url, headers=headers, json=payload, timeout=timeout)
                response.raise_for_status()

                candidate = response.json().get("candidates", [{}])[0]
                # У моделей с "мышлением" в parts попадают и размышления — берём только ответ
                text = _strip_fences("".join(part.get("text", "")
                                             for part in candidate.get("content", {}).get("parts", [])
                                             if not part.get("thought")))

                finish_reason = candidate.get("finishReason")
                if finish_reason != "STOP" or not is_complete(text):
                    # Обрезанный ответ использовать нельзя — страница откроется битой
                    print(f"⚠️ {model} вернул неполный ответ (finishReason={finish_reason}), пробую другую модель...")
                    break

                return text
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                if is_last_attempt:
                    print(f"⚠️ {model} не отвечает ({e}), пробую другую модель...")
                    break
                time.sleep(5)
            except requests.exceptions.HTTPError as e:
                status_code = e.response.status_code if e.response is not None else None
                if status_code in {429, 503}:
                    # Перегрузка модели и исчерпанная квота за секунды не проходят —
                    # быстрее переключиться на модель с отдельным пулом и лимитом
                    reason = "лимит исчерпан" if status_code == 429 else "перегружен"
                    print(f"⚠️ {model}: {reason}, пробую другую модель...")
                    break
                if status_code not in RETRYABLE_STATUS_CODES:
                    print(f"🚨 Ошибка Gemini ({model}): {e}")
                    return None
                if is_last_attempt:
                    print(f"⚠️ {model} недоступен ({status_code}), пробую другую модель...")
                    break
                time.sleep(5 * attempt)
            except Exception as e:
                print(f"🚨 Ошибка Gemini ({model}): {e}")
                return None

    print("🚨 Все доступные модели Gemini сейчас недоступны.")
    return None


def generate_site(name, cuisine, city, address, contacts, locale, with_ordering):
    prompt = build_prompt(name, cuisine, city, address, contacts, locale, with_ordering)
    html = _generate(prompt, 0.8, lambda text: "</html>" in text, "Генерация сайта")
    if not html:
        return None
    # Переключатель языков вставляет тексты из JS через textContent, где "&copy;" не раскрывается
    html = html.replace("&copy;", "©")
    return html if html.startswith("<!DOCTYPE") else "<!DOCTYPE html>\n" + html


def _is_json_object(text):
    try:
        return isinstance(json.loads(text), dict)
    except ValueError:
        return False


def translate_strings(strings, lang):
    """Переводит значения словаря на язык lang. Возвращает словарь или None."""
    prompt = f"""
    Переведи значения этого JSON на язык с кодом "{lang}". Это тексты для сайта кафе и для делового
    сообщения его владельцу — пиши естественно, вежливо, как носитель языка.
    Правила:
    - Сообщение владельцу пишет мужчина от первого лица — если в языке это видно по формам слов, используй мужской род.
    - Ключи не меняй и не переводи.
    - Плейсхолдеры в фигурных скобках ({{name}}, {{url}}, {{seller}}, {{language}}) оставь как есть.
    - Кавычки « » замени на принятые в этом языке.
    - Верни ТОЛЬКО JSON-объект, без пояснений.

    {json.dumps(strings, ensure_ascii=False, indent=2)}
    """
    text = _generate(prompt, 0.2, _is_json_object, f"Перевод текстов на {lang}")
    return json.loads(text) if text else None
