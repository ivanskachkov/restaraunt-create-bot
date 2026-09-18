"""Правка меню сайта из бота: распознавание фото, точечные изменения и публикация menu.json.

Меню правит владелец бота (то есть ты), а не владелец заведения: сопровождение сайтов — часть услуги.
Правки сначала копятся в базе, и только по кнопке уезжают на GitHub, чтобы десяток мелких изменений
не превратился в десяток пересборок Pages.
"""
import json
import re
import time

from . import db, deploy, llm, menu

MAX_ITEMS = 60           # выше этого меню перестаёт быть меню и начинает быть прайс-листом
PUBLISH_WAIT_SECONDS = 5 * 60


def _price(value):
    """Цена как строка: '12', '12.50'. None, если на цену не похоже.

    Модель возвращает распознанное с фото по-разному ('14.0', '6,5') — на сайте цена должна
    выглядеть как в меню, а не как число с плавающей точкой.
    """
    try:
        number = float(re.sub(r"[^\d.]", "", str(value).replace(",", ".")))
    except ValueError:
        return None
    if number <= 0:
        return None
    return str(int(number)) if number == int(number) else f"{number:.2f}"


def plural(count, one, few, many):
    """Русские окончания: 1 блюдо, 2 блюда, 5 блюд."""
    if count % 10 == 1 and count % 100 != 11:
        return one
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return few
    return many


def _renumber(items):
    for number, item in enumerate(items, start=1):
        item["id"] = str(number)
    return items


def normalize_items(raw_items):
    """Приводит распознанное моделью к формату menu.json, отбрасывая мусор."""
    items = []
    for raw in raw_items or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()[:80]
        price = _price(raw.get("price"))
        if not name or not price:
            continue
        item = {"id": "", "name": name, "description": str(raw.get("description") or "").strip()[:300],
                "price": price}
        for key in ("name_en", "description_en"):
            if raw.get(key):
                item[key] = str(raw[key]).strip()[:300]
        items.append(item)
    return _renumber(items[:MAX_ITEMS])


def items_from_photo(image, mime_type, locale):
    return normalize_items(llm.parse_menu_photo(image, mime_type, locale))


def blank_menu(locale):
    return {"version": menu.MENU_VERSION, "lang": locale.lang, "currency": locale.currency_symbol, "items": []}


def find(dishes, dish_id):
    return next((item for item in dishes["items"] if item["id"] == dish_id), None)


def _changed(dishes):
    dishes["dirty"] = True
    return dishes


def set_price(dishes, dish_id, price):
    price = _price(price)
    item = find(dishes, dish_id)
    if not item or not price:
        return None
    item["price"] = price
    return _changed(dishes)


def rename(dishes, dish_id, name):
    item = find(dishes, dish_id)
    if not item or not name.strip():
        return None
    item["name"] = name.strip()[:80]
    item.pop("name_en", None)  # старый перевод относился к другому блюду
    return _changed(dishes)


def describe(dishes, dish_id, description):
    item = find(dishes, dish_id)
    if not item:
        return None
    item["description"] = description.strip()[:300]
    item.pop("description_en", None)
    return _changed(dishes)


def toggle_hidden(dishes, dish_id):
    item = find(dishes, dish_id)
    if not item:
        return None
    if item.pop("hidden", False):
        return _changed(dishes)
    item["hidden"] = True
    return _changed(dishes)


def remove(dishes, dish_id):
    if not find(dishes, dish_id):
        return None
    dishes["items"] = [item for item in dishes["items"] if item["id"] != dish_id]
    _renumber(dishes["items"])
    return _changed(dishes)


def add_items(dishes, new_items, replace=False):
    """Добавляет блюда (или заменяет ими всё меню). Возвращает меню или None, если добавлять нечего."""
    if not new_items:
        return None
    dishes["items"] = new_items if replace else dishes["items"] + new_items
    _renumber(dishes["items"])
    del dishes["items"][MAX_ITEMS:]
    return _changed(dishes)


def parse_dish_line(text):
    """Блюдо одной строкой: 'Борщ; наваристый, со сметаной; 120' (описание можно не писать)."""
    parts = [part.strip() for part in re.split(r"[;\n|]", text) if part.strip()]
    if len(parts) < 2:
        return None
    name, description = (parts[0], " ".join(parts[1:-1]))
    return {"name": name, "description": description, "price": parts[-1]}


def publish(place_id, dishes):
    """Отправляет меню на GitHub. Возвращает True, если сайт уже отдаёт новую версию."""
    payload = {key: value for key, value in dishes.items() if key != "dirty"}
    payload["rev"] = int(time.time())
    if not deploy.commit_files(place_id, {menu.MENU_FILE: json.dumps(payload, ensure_ascii=False, indent=2)},
                               f"Update menu for {place_id}"):
        return False

    dishes.pop("dirty", None)
    dishes["rev"] = payload["rev"]
    db.save_menu(place_id, dishes)
    if not deploy.pages_enabled():
        return False
    # Старый menu.json остаётся в кэше Pages и отвечает 200 — поэтому ждём именно новую версию
    return deploy.wait_until_published(deploy.file_url(place_id, menu.MENU_FILE),
                                       f'"rev": {payload["rev"]}', PUBLISH_WAIT_SECONDS)


def is_dirty(dishes):
    return bool(dishes and dishes.get("dirty"))


def summary(dishes):
    if not dishes:
        return "меню вшито в страницу"
    visible = sum(1 for item in dishes["items"] if not item.get("hidden"))
    hidden = len(dishes["items"]) - visible
    return (f"{visible} {plural(visible, 'блюдо', 'блюда', 'блюд')}"
            + (f" (+{hidden} скрыто)" if hidden else ""))
