import os
import sys
import time
import sqlite3
import requests
import threading
import telebot
from github import Github
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# --- ИНИЦИАЛИЗАЦИЯ И НАСТРОЙКИ ---
load_dotenv()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
LLM_API_KEY = os.getenv("LLM_API_KEY")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-2.5-flash")

# Проверка, все ли ключи на месте
if not all([GITHUB_TOKEN, LLM_API_KEY, BOT_TOKEN]):
    raise ValueError("🚨 ОШИБКА: Проверь файл .env, не хватает ключей!")

bot = telebot.TeleBot(BOT_TOKEN)


# --- БАЗА ДАННЫХ ---
def ensure_places_schema(cursor):
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='places'")
    table_exists = cursor.fetchone() is not None

    if not table_exists:
        cursor.execute('''
                       CREATE TABLE places
                       (
                           place_id TEXT PRIMARY KEY,
                           name TEXT,
                           status TEXT,
                           site_url TEXT
                       )
                       ''')
        return

    cursor.execute("PRAGMA table_info(places)")
    columns = {row[1] for row in cursor.fetchall()}
    if "place_id" in columns:
        return

    if "osm_id" in columns:
        cursor.execute("ALTER TABLE places RENAME TO places_old")
        cursor.execute('''
                       CREATE TABLE places
                       (
                           place_id TEXT PRIMARY KEY,
                           name TEXT,
                           status TEXT,
                           site_url TEXT
                       )
                       ''')
        cursor.execute('''
                       INSERT OR IGNORE INTO places (place_id, name, status, site_url)
                       SELECT osm_id, name, status, site_url FROM places_old
                       ''')
        cursor.execute("DROP TABLE places_old")
        return

    cursor.execute("ALTER TABLE places RENAME TO places_backup")
    cursor.execute('''
                   CREATE TABLE places
                   (
                       place_id TEXT PRIMARY KEY,
                       name TEXT,
                       status TEXT,
                       site_url TEXT
                   )
                   ''')


def get_db_connection():
    """Создает безопасное подключение к БД для каждого потока."""
    conn = sqlite3.connect('restaurants.db', check_same_thread=False)
    cursor = conn.cursor()
    ensure_places_schema(cursor)
    conn.commit()
    return conn, cursor


# Инициализируем БД при старте
get_db_connection()


# --- OPENSTREETMAP OVERPASS API ---
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OSM_USER_AGENT = "restaurant-create-bot/1.0"
FOOD_AMENITIES = "restaurant|cafe|bar|fast_food|pub|food_court"
MAX_SITES_PER_REQUEST = 3
LLM_TIMEOUTS = [45, 90, 120]
CITY_ALIASES = {
    "киев": "Kyiv",
    "київ": "Kyiv",
    "kiev": "Kyiv",
    "днепр": "Dnipro",
    "дніпро": "Dnipro",
}


class SiteGenerationConfigError(Exception):
    pass


def get_city_bbox(city_name):
    """Finds a city bounding box using OpenStreetMap Nominatim."""
    search_name = CITY_ALIASES.get(city_name.strip().lower(), city_name.strip())
    headers = {"User-Agent": OSM_USER_AGENT}
    queries = [
        f"{search_name}, Ukraine",
        f"{search_name} city Ukraine",
        search_name,
    ]

    for query in queries:
        params = {
            "q": query,
            "format": "jsonv2",
            "limit": 5,
            "addressdetails": 0,
            "countrycodes": "ua",
        }
        response = requests.get(NOMINATIM_URL, params=params, headers=headers, timeout=15)
        response.raise_for_status()

        for result in response.json():
            result_type = result.get("type")
            if result_type not in {"city", "town", "village", "administrative"}:
                continue

            south, north, west, east = result["boundingbox"]
            return south, west, north, east

    return None


def build_address(tags):
    parts = []
    street = tags.get("addr:street")
    house_number = tags.get("addr:housenumber")

    if street and house_number:
        parts.append(f"{street}, {house_number}")
    elif street:
        parts.append(street)

    city = tags.get("addr:city")
    if city:
        parts.append(city)

    return ", ".join(parts) if parts else "Адрес не указан"


def build_category(tags):
    amenity_names = {
        "restaurant": "Restaurant",
        "cafe": "Cafe",
        "bar": "Bar",
        "fast_food": "Fast food",
        "pub": "Pub",
        "food_court": "Food court",
    }
    amenity = tags.get("amenity")
    cuisine = tags.get("cuisine")

    if cuisine:
        return cuisine.replace(";", ", ")
    return amenity_names.get(amenity, "Restaurant/Cafe")


def parse_number(value):
    if value is None:
        return None

    try:
        return float(str(value).replace(",", "."))
    except ValueError:
        return None


def score_place(tags):
    rating = parse_number(tags.get("rating") or tags.get("stars"))
    reviews = parse_number(tags.get("review_count") or tags.get("reviews") or tags.get("user_ratings_total"))

    if rating and reviews:
        return rating * (reviews ** 0.5)

    score = 0
    if tags.get("phone") or tags.get("contact:phone"):
        score += 30
    if tags.get("addr:street"):
        score += 20
    if tags.get("addr:housenumber"):
        score += 10
    if tags.get("cuisine"):
        score += 10
    if tags.get("opening_hours"):
        score += 10
    if tags.get("instagram") or tags.get("contact:instagram") or tags.get("facebook") or tags.get("contact:facebook"):
        score += 15
    if tags.get("amenity") == "restaurant":
        score += 8
    if tags.get("amenity") in {"cafe", "bar", "pub"}:
        score += 5

    return score


def normalize_osm_place(element):
    tags = element.get("tags", {})
    lat = element.get("lat") or element.get("center", {}).get("lat")
    lon = element.get("lon") or element.get("center", {}).get("lon")

    return {
        "fsq_id": f"osm_{element.get('type')}_{element.get('id')}",
        "name": tags.get("name") or tags.get("name:uk") or tags.get("name:ru") or "Без названия",
        "location": {"formatted_address": build_address(tags)},
        "tel": tags.get("contact:phone") or tags.get("phone") or "",
        "geocodes": {"main": {"latitude": lat, "longitude": lon}},
        "categories": [{"name": build_category(tags)}],
        "lead_score": score_place(tags),
    }


def request_overpass(query):
    last_error = None
    headers = {"User-Agent": OSM_USER_AGENT}

    for url in OVERPASS_URLS:
        for attempt in range(2):
            try:
                response = requests.post(
                    url,
                    data={"data": query},
                    headers=headers,
                    timeout=60,
                )
                response.raise_for_status()
                return response
            except requests.exceptions.RequestException as e:
                last_error = e
                status_code = getattr(getattr(e, "response", None), "status_code", None)
                if status_code not in {429, 502, 503, 504}:
                    raise

                print(f"⚠️ Overpass endpoint недоступен ({status_code}): {url}. Пробую дальше...")
                time.sleep(2 + attempt * 3)

    raise last_error


def fetch_leads(city_name):
    """Finds food places without websites through OpenStreetMap Overpass API."""
    print(f"📡 Отправляем запрос к OpenStreetMap Overpass API для города {city_name}...")

    try:
        bbox = get_city_bbox(city_name)
        if not bbox:
            print("⚠️ OpenStreetMap не нашел город.")
            return []

        south, west, north, east = bbox
        query = f"""
        [out:json][timeout:25][bbox:{south},{west},{north},{east}];
        (
          nwr["amenity"~"^({FOOD_AMENITIES})$"]["name"][!"website"][!"contact:website"][!"url"];
          nwr["amenity"~"^({FOOD_AMENITIES})$"]["name:uk"][!"website"][!"contact:website"][!"url"];
          nwr["amenity"~"^({FOOD_AMENITIES})$"]["name:ru"][!"website"][!"contact:website"][!"url"];
        );
        out center 80;
        """

        response = request_overpass(query)
        places = response.json().get("elements", [])
        if not places:
            print("⚠️ Сервер вернул пустой список.")
            return []

        leads = []
        for place in places:
            tags = place.get("tags", {})
            has_website = tags.get("website") or tags.get("contact:website") or tags.get("url")
            has_name = tags.get("name") or tags.get("name:uk") or tags.get("name:ru")
            if not has_website and has_name:
                leads.append(normalize_osm_place(place))

        leads.sort(key=lambda place: place.get("lead_score", 0), reverse=True)
        print(f"✅ OpenStreetMap вернул {len(places)} заведений. Из них без сайта: {len(leads)}")
        return leads

    except Exception as e:
        print(f"🚨 Ошибка OpenStreetMap Overpass API: {e}")
        return []

# --- ГЕНЕРАЦИЯ И ДЕПЛОЙ ---
def generate_site(name, cuisine, city, street):
    """Прямой REST API запрос к Google Gemini"""
    prompt = f"""
    Создай компактный Landing Page одним HTML файлом для заведения "{name}".
    Тип/кухня: {cuisine}. Адрес: {city}, {street}.
    Используй Tailwind CDN, современный дизайн, блоки hero/menu/about/contact.
    Добавь переключатель языков украинский/английский через JS.
    Верни только валидный HTML без Markdown. HTML должен быть не длиннее 9000 символов.
    """

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{LLM_MODEL}:generateContent?key={LLM_API_KEY}"
    headers = {'Content-Type': 'application/json'}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 4096,
        },
    }

    try:
        for attempt, timeout in enumerate(LLM_TIMEOUTS, start=1):
            try:
                response = requests.post(url, headers=headers, json=payload, timeout=timeout)
                response.raise_for_status()

                text = response.json()['candidates'][0]['content']['parts'][0]['text']
                return text.replace('```html', '').replace('```', '').strip()
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                print(f"⚠️ Gemini timeout/network error, попытка {attempt}/{len(LLM_TIMEOUTS)}: {type(e).__name__}")
                if attempt == len(LLM_TIMEOUTS):
                    return None
                time.sleep(3 * attempt)
    except requests.exceptions.HTTPError as e:
        status_code = e.response.status_code if e.response is not None else None
        response_text = e.response.text[:300] if e.response is not None else ""
        print(f"🚨 Ошибка REST API Gemini: HTTP {status_code}. Ответ: {response_text}")

        if status_code in {400, 401, 403, 404}:
            raise SiteGenerationConfigError(
                f"проверь LLM_API_KEY и доступность модели {LLM_MODEL}. "
                f"Gemini вернул HTTP {status_code}."
            )

        return None
    except Exception as e:
        print(f"🚨 Ошибка REST API Gemini: {type(e).__name__}: {e}")
        return None


def deploy_to_github(place_id, html_content):
    """Создает репозиторий на GitHub и включает Pages"""
    g = Github(GITHUB_TOKEN)
    user = g.get_user()
    repo_name = f"promo-{place_id}"
    pages_url = f"https://{user.login}.github.io/{repo_name}"

    try:
        repo = user.create_repo(repo_name, homepage=pages_url, auto_init=True)
        repo.create_file("index.html", "Init generated site", html_content, branch="main")

        headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
        api_url = f"https://api.github.com/repos/{user.login}/{repo_name}/pages"
        requests.post(api_url, headers=headers, json={"source": {"branch": "main", "path": "/"}})

        return pages_url
    except Exception as e:
        print(f"🚨 Ошибка GitHub: {e}")
        return None


# --- ФОНОВАЯ ОБРАБОТКА ЛИДОВ ---
def process_city_task(chat_id, city_name):
    """Фоновая задача, чтобы бот не зависал при долгой генерации"""
    bot.send_message(chat_id, f"🔍 Ищу рестораны без сайтов в OpenStreetMap: {city_name}...")

    places = fetch_leads(city_name)
    if not places:
        bot.send_message(chat_id, "Ничего не найдено. Попробуй другой город.")
        return

    conn, cursor = get_db_connection()
    processed_count = 0
    generation_stopped = False
    selected_places = []

    for p in places:
        place_id = p.get('fsq_id')
        cursor.execute("SELECT status FROM places WHERE place_id=?", (place_id,))
        if cursor.fetchone():
            continue  # Пропускаем, если уже делали

        selected_places.append(p)
        if len(selected_places) >= MAX_SITES_PER_REQUEST:
            break

    if not selected_places:
        bot.send_message(chat_id, "Все найденные заведения уже обработаны. Попробуй другой город.")
        return

    bot.send_message(
        chat_id,
        f"🎯 Найдено {len(places)} лидов без сайта. Выбрал {len(selected_places)} лучших по доступным данным OSM.",
    )

    for p in selected_places:
        place_id = p.get('fsq_id')
        name = p.get('name', 'Без названия')

        address = p.get('location', {}).get('formatted_address', 'Адрес не указан')
        phone = p.get('tel', '')

        # Получаем тип заведения из нормализованных категорий OpenStreetMap
        categories = p.get('categories', [])
        cuisine = categories[0].get('name', 'Ресторан/Кафе') if categories else 'Ресторан/Кафе'

        # Координаты для Google Maps
        lat = p.get('geocodes', {}).get('main', {}).get('latitude')
        lon = p.get('geocodes', {}).get('main', {}).get('longitude')

        bot.send_message(chat_id, f"⚙️ Генерирую сайт: {name} (Тип: {cuisine})...")
        try:
            html = generate_site(name, cuisine, city_name, address)
        except SiteGenerationConfigError as e:
            bot.send_message(chat_id, f"🚨 Генерация сайтов остановлена: {e}")
            generation_stopped = True
            break

        if html:
            site_url = deploy_to_github(place_id, html)
            if site_url:
                cursor.execute(
                    "INSERT INTO places (place_id, name, status, site_url) VALUES (?, ?, ?, ?)",
                    (place_id, name, 'done', site_url),
                )
                conn.commit()

                contact = f"💬 [WhatsApp](https://wa.me/{''.join(filter(str.isdigit, phone))})" if phone else "⚠️ Номер не указан"
                maps_link = f"[Google Maps](https://www.google.com/maps/search/?api=1&query={lat},{lon})" if lat and lon else "Без координат"

                msg = (
                    f"🎯 **Лид: {name}**\n"
                    f"📍 Адрес: {address}\n"
                    f"🗺 Карта: {maps_link}\n"
                    f"🍕 Тип: {cuisine}\n\n"
                    f"🌐 **Готовый сайт:** {site_url}\n"
                    f"📞 Связь: {contact}\n"
                    f"*(Сайт может выдавать 404 первые пару минут, пока GitHub его собирает)*"
                )

                bot.send_message(chat_id, msg, parse_mode="Markdown", disable_web_page_preview=True)

                processed_count += 1
                time.sleep(25)

    if processed_count:
        bot.send_message(chat_id, "✅ Пакет заведений обработан. Пиши город снова для продолжения.")
    elif not generation_stopped:
        bot.send_message(chat_id, "⚠️ Не удалось сгенерировать сайты для выбранных заведений.")

# --- ИНТЕРФЕЙС БОТА ---


@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message,
                 "👋 Привет! Я бот-архитектор.\nНапиши мне название города, и я найду для тебя горячие лиды (рестораны без сайтов), сгенерирую им лендинги и пришлю контакты.")


@bot.message_handler(content_types=['text'])
def handle_city_search(message):
    city_name = message.text.strip()

    # Запускаем генерацию в фоне сразу с именем города
    thread = threading.Thread(target=process_city_task, args=(message.chat.id, city_name))
    thread.start()


if __name__ == "__main__":
    print("🤖 Бот успешно запущен с OpenStreetMap Overpass API! Напиши ему в Телеграм.")
    bot.infinity_polling()
