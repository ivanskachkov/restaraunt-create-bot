"""Меню сайта хранится отдельным файлом menu.json, а не внутри HTML.

Модель по-прежнему рисует карточки блюд, но код вынимает из них данные, оставляет одну карточку
шаблоном и подставляет блюда на лету. Благодаря этому правка цены — это правка одной строки в JSON
(секунды), а не повторная генерация всей страницы моделью (минуты, и каждый раз другой дизайн).

Разметка, на которую опирается разбор (её требует промпт в llm.py):
    <div data-dish="Борщ" data-price="120" data-currency="грн">
        <h3 data-field="name">Борщ</h3>
        <p data-field="description">...</p>
        <span data-field="price">120 грн</span>
    </div>
"""
import re
from collections import namedtuple
from html import escape, unescape

MENU_FILE = "menu.json"
TEMPLATE_ID = "menu-card-template"
RENDERER_ID = "menu-renderer"
MENU_VERSION = 1

# Карточки подставляются после загрузки страницы, поэтому виджет заказа вешает кнопки по этому событию
RENDERED_EVENT = "menu:rendered"

_RENDERER = r"""
<script id="menu-renderer">
(function () {
  var tpl = document.getElementById("__TEMPLATE_ID__");
  if (!tpl) return;
  var data = null;

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  // Название и описание могут быть переведены: если страницу переключили на английский, берём *_en
  function pick(item, field) {
    var lang = (document.documentElement.getAttribute("lang") || "").toLowerCase().slice(0, 2);
    return (lang === "en" && item[field + "_en"]) ? item[field + "_en"] : item[field];
  }
  function render() {
    if (!data) return;
    var parent = tpl.parentNode;
    Array.prototype.slice.call(parent.querySelectorAll("[data-dish]")).forEach(function (node) {
      node.parentNode.removeChild(node);
    });
    data.items.forEach(function (item) {
      if (item.hidden) return;
      var box = document.createElement("div");
      box.innerHTML = tpl.innerHTML
        .replace(/\{\{name_en\}\}/g, esc(item.name_en || item.name))
        .replace(/\{\{description_en\}\}/g, esc(item.description_en || item.description))
        .replace(/\{\{name\}\}/g, esc(pick(item, "name")))
        .replace(/\{\{description\}\}/g, esc(pick(item, "description")))
        .replace(/\{\{price\}\}/g, esc(item.price));
      while (box.firstChild) parent.insertBefore(box.firstChild, tpl);
    });
    document.dispatchEvent(new CustomEvent("__RENDERED_EVENT__"));
  }

  new MutationObserver(render).observe(document.documentElement, {attributes: true, attributeFilter: ["lang"]});
  // Пока меню не загрузилось (или если файла нет), на странице остаются карточки из HTML
  fetch("__MENU_FILE__", {cache: "no-store"})
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (json) { if (json && json.items && json.items.length) { data = json; render(); } })
    .catch(function () {});
})();
</script>
"""

Element = namedtuple("Element", "start inner_start inner_end end tag attrs")

_TAG_NAME = re.compile(r"<([a-zA-Z][\w-]*)")
_ATTR = re.compile(r"""([\w:.-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
_ATTR_VALUE = r"""\s*=\s*(?:"[^"]*"|'[^']*')"""


def _open_tag_end(html, start):
    """Конец открывающего тега. Посимвольно, потому что '>' бывает и внутри значения атрибута."""
    quote = None
    for i in range(start, len(html)):
        char = html[i]
        if quote:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == ">":
            return i + 1
    return -1


def _element_end(html, start, tag):
    """Конец элемента: считаем вложенные теги с тем же именем."""
    pattern = re.compile(rf"</?{re.escape(tag)}\b", re.I)
    depth = 0
    pos = start
    while True:
        match = pattern.search(html, pos)
        if not match:
            return -1
        if match.group(0).startswith("</"):
            depth -= 1
            if depth <= 0:
                end = html.find(">", match.end())
                return end + 1 if end != -1 else -1
        else:
            depth += 1
        pos = match.end()


def _element_at(html, attr_pos):
    """Элемент, в открывающем теге которого нашёлся атрибут на позиции attr_pos."""
    start = html.rfind("<", 0, attr_pos)
    if start < 0:
        return None
    name = _TAG_NAME.match(html, start)
    if not name:
        return None
    inner_start = _open_tag_end(html, start)
    # Если тег закончился раньше атрибута, значит атрибут не его, а вложенного элемента
    if inner_start < 0 or inner_start <= attr_pos:
        return None
    end = _element_end(html, start, name.group(1))
    if end < 0:
        return None
    inner_end = html.rfind("</", inner_start, end)
    attrs = {m.group(1).lower(): unescape(m.group(2) if m.group(2) is not None else m.group(3))
             for m in _ATTR.finditer(html[start:inner_start])}
    return Element(start, inner_start, inner_end, end, name.group(1), attrs)


def _cards(html):
    cards = []
    for match in re.finditer(r"\bdata-dish\s*=\s*[\"']", html, re.I):
        card = _element_at(html, match.start())
        if card and (not cards or card.start >= cards[-1].end):
            cards.append(card)
    return cards


def _field(card_html, field):
    match = re.search(rf"\bdata-field\s*=\s*[\"']{field}[\"']", card_html, re.I)
    return _element_at(card_html, match.start()) if match else None


def _text(fragment):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def _templatize(card_html, item, currency):
    """Карточка блюда с {{name}}, {{description}}, {{price}} вместо текста. None, если разметки нет."""
    name_el = _field(card_html, "name")
    price_el = _field(card_html, "price")
    if not name_el or not price_el:
        return None

    price_inner = card_html[price_el.inner_start:price_el.inner_end]
    # Формат цены («120 грн», «€12.50») задаёт модель — подменяем в нём только само число
    price_text = (price_inner.replace(item["price"], "{{price}}", 1) if item["price"] in price_inner
                  else "{{price}} " + escape(currency))

    fields = [(name_el, "{{name}}"), (price_el, price_text)]
    description_el = _field(card_html, "description")
    if description_el:
        fields.append((description_el, "{{description}}"))
    # С конца, чтобы замены не сдвигали позиции следующих
    for element, text in sorted(fields, key=lambda f: f[0].inner_start, reverse=True):
        card_html = card_html[:element.inner_start] + text + card_html[element.inner_end:]

    open_end = _open_tag_end(card_html, 0)
    open_tag = card_html[:open_end]
    # Переводы тоже подставляются из JSON: модель часто пишет свой переключатель языка,
    # который читает названия блюд именно из этих атрибутов
    open_tag = re.sub(rf"\bdata-dish-en{_ATTR_VALUE}", 'data-dish-en="{{name_en}}"', open_tag, flags=re.I)
    open_tag = re.sub(rf"\bdata-description-en{_ATTR_VALUE}", 'data-description-en="{{description_en}}"',
                      open_tag, flags=re.I)
    open_tag = re.sub(rf"\bdata-dish{_ATTR_VALUE}", 'data-dish="{{name}}"', open_tag, flags=re.I)
    open_tag = re.sub(rf"\bdata-price{_ATTR_VALUE}", 'data-price="{{price}}"', open_tag, flags=re.I)
    return open_tag + card_html[open_end:]


def extract(html, locale):
    """Вынимает меню из сгенерированной страницы.

    Возвращает (меню, HTML с шаблоном карточки) либо (None, исходный HTML), если модель не разметила
    карточки — тогда сайт работает как раньше, со статичным меню внутри страницы.
    """
    cards = _cards(html)
    if not cards:
        return None, html

    items = []
    for number, card in enumerate(cards, start=1):
        name = card.attrs.get("data-dish", "").strip()
        price = card.attrs.get("data-price", "").strip()
        if not name or not price:
            continue
        card_html = html[card.start:card.end]
        description_el = _field(card_html, "description")
        item = {
            "id": str(number),
            "name": name,
            "description": _text(card_html[description_el.inner_start:description_el.inner_end])
            if description_el else "",
            "price": price,
        }
        for field, key in (("data-dish-en", "name_en"), ("data-description-en", "description_en")):
            if card.attrs.get(field, "").strip():
                item[key] = card.attrs[field].strip()
        items.append((card, item))

    template = next((t for t in (_templatize(html[c.start:c.end], i, locale.currency_symbol) for c, i in items) if t),
                    None)
    if not template:
        return None, html

    end = cards[-1].end
    html = html[:end] + f'\n<template id="{TEMPLATE_ID}">{template}</template>' + html[end:]
    menu = {
        "version": MENU_VERSION,
        "lang": locale.lang,
        "currency": locale.currency_symbol,
        "items": [item for _, item in items],
    }
    return menu, html


def renderer_script():
    return (_RENDERER.replace("__TEMPLATE_ID__", TEMPLATE_ID)
            .replace("__RENDERED_EVENT__", RENDERED_EVENT)
            .replace("__MENU_FILE__", MENU_FILE))
