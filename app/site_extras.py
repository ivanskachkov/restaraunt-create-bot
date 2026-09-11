"""То, что добавляется в сгенерированную страницу кодом, а не моделью — чтобы работало одинаково на всех сайтах."""
import json
import re
from html import escape

from . import i18n

DEMO_BANNER_ID = "demo-concept-banner"
ORDER_WIDGET_ID = "order-widget"

_ORDER_WIDGET = r"""
<script id="order-widget">
(function () {
  var PHONE = __PHONE__;
  var L = __LABELS__;
  var cards = document.querySelectorAll("[data-dish][data-price]");
  if (!cards.length) return;

  var table = new URLSearchParams(location.search).get("table");
  var cart = [];
  var FONT = "font-family:system-ui,-apple-system,sans-serif;";
  var BTN = FONT + "margin-top:12px;padding:8px 14px;border:0;border-radius:999px;background:#111827;" +
            "color:#fff;font-size:14px;font-weight:600;cursor:pointer";
  var SMALL = FONT + "width:28px;height:28px;border:1px solid #d1d5db;border-radius:8px;background:#fff;" +
              "color:#111827;font-size:16px;cursor:pointer";

  function el(tag, css, text) {
    var node = document.createElement(tag);
    if (css) node.style.cssText = css;
    if (text !== undefined) node.textContent = text;
    if (tag === "button") node.type = "button";
    return node;
  }
  function fmt(n) { return String(Math.round(n * 100) / 100); }
  function count() { return cart.reduce(function (s, it) { return s + it.qty; }, 0); }
  function total() { return cart.reduce(function (s, it) { return s + it.qty * it.price; }, 0); }
  function currency() { return cart.length ? cart[0].currency : ""; }

  var fab = el("button", FONT + "position:fixed;right:16px;bottom:60px;z-index:2147483646;display:none;" +
               "padding:12px 18px;border:0;border-radius:999px;background:#16a34a;color:#fff;font-size:15px;" +
               "font-weight:600;box-shadow:0 6px 20px rgba(0,0,0,.25);cursor:pointer");
  var panel = el("div", FONT + "position:fixed;right:16px;bottom:116px;z-index:2147483646;display:none;" +
                 "width:min(360px,calc(100vw - 32px));max-height:60vh;overflow:auto;background:#fff;" +
                 "color:#111827;border-radius:16px;box-shadow:0 10px 40px rgba(0,0,0,.3);padding:16px;" +
                 "font-size:14px;line-height:1.4");
  document.body.appendChild(panel);
  document.body.appendChild(fab);
  fab.addEventListener("click", function () {
    panel.style.display = panel.style.display === "none" ? "block" : "none";
  });

  function render() {
    var n = count();
    fab.style.display = n ? "block" : "none";
    fab.textContent = "🛒 " + L.cart + " (" + n + ")";
    if (!n) panel.style.display = "none";

    while (panel.firstChild) panel.removeChild(panel.firstChild);
    panel.appendChild(el("div", "font-weight:700;font-size:16px;margin-bottom:8px",
                         table ? L.your_order + " · " + L.table + " " + table : L.your_order));
    cart.forEach(function (it) {
      var row = el("div", "display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid #eee");
      var minus = el("button", SMALL, "−");
      minus.addEventListener("click", function () {
        it.qty--;
        if (it.qty <= 0) cart.splice(cart.indexOf(it), 1);
        render();
      });
      var plus = el("button", SMALL, "+");
      plus.addEventListener("click", function () { it.qty++; render(); });
      row.appendChild(el("div", "flex:1", it.name));
      row.appendChild(minus);
      row.appendChild(el("span", "min-width:20px;text-align:center", String(it.qty)));
      row.appendChild(plus);
      row.appendChild(el("div", "min-width:72px;text-align:right", fmt(it.qty * it.price) + " " + it.currency));
      panel.appendChild(row);
    });
    panel.appendChild(el("div", "font-weight:700;margin:10px 0", L.total + ": " + fmt(total()) + " " + currency()));

    var send = el("button", FONT + "width:100%;padding:12px;border:0;border-radius:12px;background:#16a34a;" +
                  "color:#fff;font-size:15px;font-weight:600;cursor:pointer", L.send_whatsapp);
    send.addEventListener("click", function () {
      var lines = [L.order_from_site];
      if (table) lines.push(L.table + ": " + table);
      lines.push("");
      cart.forEach(function (it) {
        lines.push(it.qty + " × " + it.name + " — " + fmt(it.qty * it.price) + " " + it.currency);
      });
      lines.push("", L.total + ": " + fmt(total()) + " " + currency());
      window.open("https://wa.me/" + PHONE + "?text=" + encodeURIComponent(lines.join("\n")), "_blank");
    });
    panel.appendChild(send);
  }

  cards.forEach(function (card) {
    var add = el("button", BTN, L.add_to_order);
    add.addEventListener("click", function () {
      var name = card.getAttribute("data-dish");
      var item = cart.filter(function (it) { return it.name === name; })[0];
      if (item) {
        item.qty++;
      } else {
        cart.push({name: name, qty: 1, price: parseFloat(card.getAttribute("data-price")) || 0,
                   currency: card.getAttribute("data-currency") || ""});
      }
      render();
    });
    card.appendChild(add);
  });
})();
</script>
"""


def _insert_into_head(html, snippet):
    html, found = re.subn(r'<head[^>]*>', lambda m: f"{m.group(0)}\n{snippet}", html, count=1, flags=re.I)
    return html if found else snippet + html


def _insert_before_body_end(html, snippet):
    if re.search(r'</body>', html, flags=re.I):
        return re.sub(r'</body>', lambda m: f"{snippet}\n{m.group(0)}", html, count=1, flags=re.I)
    return html.replace("</html>", f"{snippet}\n</html>")


def add_demo_markers(html, name, locale):
    """Демо висит публично под чужим названием, поэтому:
    noindex — чтобы его не нашли через поиск и не приняли за официальный сайт,
    плашка — чтобы открывший по ссылке сразу видел, что это концепт."""
    if DEMO_BANNER_ID in html:
        return html

    text = i18n.fill(i18n.ui_strings(locale.lang)["demo_banner"], name=name)
    if locale.bilingual:
        text += " · " + i18n.fill(i18n.EN["demo_banner"], name=name)
    html = _insert_into_head(html, '<meta name="robots" content="noindex, nofollow">\n'
                                   '<style>body{padding-bottom:44px}</style>')
    banner = (f'<div id="{DEMO_BANNER_ID}" style="position:fixed;left:0;right:0;bottom:0;z-index:2147483647;'
              'background:rgba(17,24,39,.94);color:#fff;font:13px/1.4 system-ui,sans-serif;'
              f'text-align:center;padding:10px 12px">{escape(text)}</div>')
    html, found = re.subn(r'<body[^>]*>', lambda m: f"{m.group(0)}\n{banner}", html, count=1, flags=re.I)
    return html if found else _insert_before_body_end(html, banner)


def add_og_tags(html, name, site_url, has_ordering, locale):
    """Превью ссылки в Instagram/Telegram: карточка с названием вместо голого адреса."""
    strings = i18n.ui_strings(locale.lang)
    title = i18n.fill(strings["og_title"], name=name)
    description = i18n.fill(strings["og_description_ordering" if has_ordering else "og_description"], name=name)
    html = re.sub(r'<meta[^>]+property="og:[^"]*"[^>]*>\s*', "", html, flags=re.I)
    return _insert_into_head(html, "\n".join([
        '<meta property="og:type" content="website">',
        f'<meta property="og:title" content="{escape(title)}">',
        f'<meta property="og:description" content="{escape(description)}">',
        f'<meta property="og:url" content="{escape(site_url)}">',
        f'<meta property="og:locale" content="{escape(locale.og_locale)}">',
    ]))


def add_order_widget(html, wa_number, locale):
    if ORDER_WIDGET_ID in html:
        return html
    keys = ("add_to_order", "cart", "your_order", "table", "total", "send_whatsapp", "order_from_site")
    strings = i18n.ui_strings(locale.lang)
    # "</" внутри <script> закрыл бы тег раньше времени, если он встретится в переводе
    labels = json.dumps({k: strings[k] for k in keys}, ensure_ascii=False).replace("</", "<\\/")
    return _insert_before_body_end(html, _ORDER_WIDGET.replace("__PHONE__", json.dumps(wa_number))
                                   .replace("__LABELS__", labels))


def finalize(html, name, site_url, wa_number, locale):
    """Возвращает (готовый HTML, есть ли на сайте заказ)."""
    # Корзина работает только если модель разметила карточки меню и есть куда отправлять заказ
    has_ordering = bool(wa_number) and "data-dish=" in html and "data-price=" in html
    html = add_demo_markers(html, name, locale)
    html = add_og_tags(html, name, site_url, has_ordering, locale)
    if has_ordering:
        html = add_order_widget(html, wa_number, locale)
    return html, has_ordering
