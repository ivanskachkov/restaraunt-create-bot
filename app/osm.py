import time
from dataclasses import dataclass

import requests

from . import config

# "place" покрывает city/town/village и т.п.; остальные — административные границы.
CITY_RESULT_TYPES = {"city", "town", "administrative", "state", "county", "province", "municipality"}

UNKNOWN_ADDRESS = "Адрес не указан"

PHONE_KEYS = ("contact:phone", "phone", "contact:mobile", "mobile")
EMAIL_KEYS = ("contact:email", "email")
SOCIAL_KEYS = {
    "Instagram": ("contact:instagram", "instagram"),
    "Facebook": ("contact:facebook", "facebook"),
    "Telegram": ("contact:telegram", "telegram"),
    "TikTok": ("contact:tiktok", "tiktok"),
}
# В OSM соцсеть бывает записана не ссылкой, а просто ником
SOCIAL_BASE_URLS = {
    "Instagram": "https://www.instagram.com/",
    "Facebook": "https://www.facebook.com/",
    "Telegram": "https://t.me/",
    "TikTok": "https://www.tiktok.com/@",
}
CONTACT_KEYS = PHONE_KEYS + EMAIL_KEYS + tuple(k for keys in SOCIAL_KEYS.values() for k in keys)


def get_city_bbox(city_name):
    """Поиск координат города, области или региона. Возвращает (bbox, код страны) или None.

    Nominatim индексирует названия из OSM на всех языках (name:ru, name:uk, name:ja и т.д.),
    поэтому структурированный запрос находит город независимо от языка ввода — отдельный
    словарь-переводчик не нужен.
    """
    search_name = city_name.strip()
    headers = {"User-Agent": config.OSM_USER_AGENT}

    # Структурированный запрос (city=) с featuretype=city идёт первым: он не путает
    # город с одноимённым природным объектом (например "Dnipro" на английском — это ещё
    # и название реки Днепр, и без featuretype она обгоняет город по "importance").
    # Области/районы структурный запрос не находит, поэтому для них — запасной q=.
    # addressdetails=1 — ради кода страны: по нему выбирается язык сайта.
    attempts = [
        {"city": search_name, "featuretype": "city", "format": "jsonv2", "limit": 3, "addressdetails": 1},
        {"q": search_name, "format": "jsonv2", "limit": 10, "addressdetails": 1},
    ]

    for i, params in enumerate(attempts):
        if i > 0:
            time.sleep(1)  # Nominatim usage policy: не больше 1 запроса в секунду
        response = requests.get(config.NOMINATIM_URL, params=params, headers=headers, timeout=15)
        if response.status_code != 200:
            continue
        for result in response.json():
            if result.get("type") in CITY_RESULT_TYPES or result.get("category") == "place":
                south, north, west, east = result["boundingbox"]
                country_code = result.get("address", {}).get("country_code")
                return (south, west, north, east), country_code

    return None


def get_address_by_coords(lat, lon):
    """Обратный геокодинг для получения адреса."""
    try:
        time.sleep(1.2)
        params = {"format": "jsonv2", "lat": lat, "lon": lon}
        response = requests.get(config.REVERSE_NOMINATIM_URL, params=params,
                                headers={"User-Agent": config.OSM_USER_AGENT}, timeout=10)
        if response.status_code == 200:
            addr = response.json().get("address", {})
            road = addr.get("road")
            house = addr.get("house_number")
            if road and house:
                return f"{road}, {house}"
            elif road:
                return road
    except Exception:
        pass
    return UNKNOWN_ADDRESS


def _split_values(value):
    """В OSM несколько значений одного тега пишутся через ';'."""
    return [v.strip() for v in value.split(";") if v.strip()]


def extract_contacts(tags):
    phones = []
    for key in PHONE_KEYS:
        for phone in _split_values(tags.get(key, "")):
            if phone not in phones:
                phones.append(phone)

    socials = {}
    for label, keys in SOCIAL_KEYS.items():
        values = next((_split_values(tags[k]) for k in keys if tags.get(k)), [])
        if values:
            value = values[0]
            socials[label] = value if value.startswith("http") else SOCIAL_BASE_URLS[label] + value.lstrip("@")

    emails = next((_split_values(tags[k]) for k in EMAIL_KEYS if tags.get(k)), [])

    return {
        "phones": phones,
        "socials": socials,
        "email": emails[0] if emails else None,
        "opening_hours": tags.get("opening_hours"),
    }


def place_id_of(element):
    return f"osm-{element.get('type')}-{element.get('id')}"


def normalize_osm_place(element):
    tags = element.get("tags", {})
    lat = element.get("lat") or element.get("center", {}).get("lat")
    lon = element.get("lon") or element.get("center", {}).get("lon")

    street = tags.get("addr:street")
    house = tags.get("addr:housenumber")
    if street:
        address = f"{street}, {house}" if house else street
    else:
        address = get_address_by_coords(lat, lon)

    cuisine = tags.get("cuisine") or tags.get("amenity", "Restaurant")

    return {
        "place_id": place_id_of(element),
        "name": tags.get("name") or tags.get("name:uk") or tags.get("name:ru") or "Без названия",
        "location": {"formatted_address": address},
        "contacts": extract_contacts(tags),
        "geocodes": {"main": {"latitude": lat, "longitude": lon}},
        "categories": [{"name": cuisine}],
    }


def request_overpass(query):
    last_error = None
    headers = {"User-Agent": config.OSM_USER_AGENT}

    for url in config.OVERPASS_URLS:
        for attempt in range(2):
            try:
                response = requests.post(url, data={"data": query}, headers=headers, timeout=60)
                response.raise_for_status()
                return response
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                # Таймаут/обрыв соединения — сервер перегружен, есть смысл пробовать дальше
                last_error = e
                print(f"⚠️ Overpass {url} недоступен (таймаут). Пробую дальше...")
                time.sleep(2 + attempt * 3)
            except requests.exceptions.HTTPError as e:
                last_error = e
                status_code = e.response.status_code if e.response is not None else None
                if status_code not in {429, 502, 503, 504}:
                    raise
                print(f"⚠️ Overpass {url} недоступен ({status_code}). Пробую дальше...")
                time.sleep(2 + attempt * 3)
    raise last_error


class CityNotFoundError(Exception):
    """Nominatim не нашёл такой город/регион — проблема в названии, а не в сервисе."""


class OverpassUnavailableError(Exception):
    """Все зеркала Overpass недоступны — временная проблема сервиса, не города."""


@dataclass
class LeadSearch:
    leads: list        # заведения, взятые в работу (не больше limit)
    new_count: int     # сколько всего новых, ещё не обработанных
    total_count: int   # сколько всего подходящих в городе
    country_code: str  # страна города — по ней выбирается язык сайта


def fetch_leads(city_name, exclude_ids, limit):
    print(f"📡 Отправляем запрос к OpenStreetMap для {city_name}...")

    found = get_city_bbox(city_name)
    if not found:
        print("⚠️ OpenStreetMap не нашел локацию.")
        raise CityNotFoundError(city_name)

    (south, west, north, east), country_code = found
    # [!brand][!"brand:wikidata"] отсекает сетевые заведения: у McDonald's, KFC и подобных
    # эти теги есть всегда, у локальных заведений — нет.
    chain_filter = '[!brand][!"brand:wikidata"]'
    no_site_filter = '[!website][!"contact:website"][!url]'
    # Без контактов лид бесполезен — связаться не с кем. Фильтр именно в запросе:
    # у ~90% заведений контактов нет.
    contact_filter = f'[~"^({"|".join(CONTACT_KEYS)})$"~"."]'
    filters = no_site_filter + chain_filter + contact_filter
    # Лимита вроде "out 30" нет намеренно: иначе, когда первые 30 обработаны,
    # до остальных заведений города бот не добрался бы никогда.
    query = f"""
    [out:json][timeout:60][bbox:{south},{west},{north},{east}];
    (
      nwr["amenity"~"^({config.FOOD_AMENITIES})$"]["name"]{filters};
      nwr["amenity"~"^({config.FOOD_AMENITIES})$"]["name:uk"]{filters};
      nwr["amenity"~"^({config.FOOD_AMENITIES})$"]["name:ru"]{filters};
    );
    out center;
    """

    try:
        response = request_overpass(query)
    except requests.exceptions.RequestException as e:
        print(f"🚨 Overpass недоступен: {e}")
        raise OverpassUnavailableError(str(e)) from e

    candidates = []
    for place in response.json().get("elements", []):
        tags = place.get("tags", {})
        has_website = tags.get("website") or tags.get("contact:website") or tags.get("url")
        has_name = tags.get("name") or tags.get("name:uk") or tags.get("name:ru")
        is_chain = tags.get("brand") or tags.get("brand:wikidata")
        has_contact = any(tags.get(k) for k in CONTACT_KEYS)
        if not has_website and has_name and not is_chain and has_contact:
            candidates.append(place)

    new_places = [p for p in candidates if place_id_of(p) not in exclude_ids]
    print(f"✅ Найдено с контактами и без сайта: {len(candidates)}, из них новых: {len(new_places)}")

    # Геокодинг адреса медленный (1+ сек на заведение) — делаем только для тех, кого берём в работу
    return LeadSearch([normalize_osm_place(p) for p in new_places[:limit]], len(new_places), len(candidates),
                      country_code)
