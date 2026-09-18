"""Демо-заведение: тот же конвейер, что и для настоящих лидов, но данные выдуманы.

Нужно, чтобы можно было посмотреть и потрогать результат (сайт, корзину, правку меню),
не тратя на это живого лида — и чтобы каждый запуск давал другую страну, кухню и язык.
"""
import random
import uuid

# Телефоны выдуманные, но в международном формате — иначе не проверить заказ через WhatsApp.
# Диапазоны, зарезервированные для примеров и вымысла, чтобы не попасть в чужой реальный номер.
VENUES = [
    {"name": "Трапезна Софія", "cuisine": "ukrainian", "city": "Київ", "country": "ua",
     "address": "вул. Ярославів Вал, 15", "phone": "+380 44 555 0110",
     "hours": "Mo-Su 10:00-22:00"},
    {"name": "Osteria del Melograno", "cuisine": "italian", "city": "Bologna", "country": "it",
     "address": "Via Santo Stefano, 34", "phone": "+39 051 555 0142",
     "hours": "Tu-Su 12:00-15:00,19:00-23:30"},
    {"name": "Taberna La Higuera", "cuisine": "spanish;tapas", "city": "Sevilla", "country": "es",
     "address": "Calle Feria, 27", "phone": "+34 954 555 018",
     "hours": "Tu-Sa 13:00-16:00,20:00-00:00"},
    {"name": "Café Lindenblatt", "cuisine": "german;coffee_shop", "city": "Leipzig", "country": "de",
     "address": "Karl-Liebknecht-Straße, 62", "phone": "+49 341 5550 129",
     "hours": "Mo-Fr 08:00-19:00; Sa-Su 09:00-20:00"},
    {"name": "Meze Bahçe", "cuisine": "turkish;seafood", "city": "İzmir", "country": "tr",
     "address": "Alsancak Mahallesi, Kıbrıs Şehitleri Caddesi 41", "phone": "+90 232 555 0173",
     "hours": "Mo-Su 12:00-01:00"},
    {"name": "მარანი ვაზი", "cuisine": "georgian", "city": "თბილისი", "country": "ge",
     "address": "ერეკლე II-ის ქუჩა, 8", "phone": "+995 32 255 0164",
     "hours": "Mo-Su 11:00-23:00"},
    {"name": "Bistrot des Halles", "cuisine": "french", "city": "Lyon", "country": "fr",
     "address": "Rue de la Martinière, 19", "phone": "+33 4 72 55 01 88",
     "hours": "Tu-Sa 12:00-14:30,19:00-22:30"},
    {"name": "小さな出汁屋", "cuisine": "japanese;ramen", "city": "京都", "country": "jp",
     "address": "中京区先斗町通四条上ル 12", "phone": "+81 75 555 0197",
     "hours": "We-Mo 11:30-14:00,17:30-22:00"},
]


def random_venue():
    """Случайное демо-заведение с уникальным id — генерировать их можно сколько угодно."""
    venue = dict(random.choice(VENUES))
    venue["place_id"] = f"demo-{venue['country']}-{uuid.uuid4().hex[:6]}"
    venue["contacts"] = {"phones": [venue["phone"]], "socials": {}, "email": None,
                         "opening_hours": venue["hours"]}
    return venue
