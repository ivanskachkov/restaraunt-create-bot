import os
from dotenv import load_dotenv

load_dotenv()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
LLM_API_KEY = os.getenv("LLM_API_KEY")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-flash-latest")  # алиас на актуальную flash-модель, не привязан к версии
# Если основная модель перегружена (503 "high demand"), пробуем эти по очереди —
# у разных моделей отдельные пулы мощностей на бесплатном тарифе.
LLM_MODEL_FALLBACKS = ["gemini-2.5-flash", "gemini-flash-lite-latest"]
GITHUB_REPO = os.getenv("GITHUB_REPO", "websites-for-bot")

# Имя в тексте предложения владельцу ("Мене звати ..."). Если не задано — представление без имени.
SELLER_NAME = os.getenv("SELLER_NAME")

# Только этот пользователь Telegram может управлять ботом. Если не задан — бот открыт для всех.
OWNER_ID = int(os.getenv("TELEGRAM_OWNER_ID")) if os.getenv("TELEGRAM_OWNER_ID") else None

if not all([GITHUB_TOKEN, LLM_API_KEY, BOT_TOKEN]):
    raise ValueError("🚨 ОШИБКА: Проверь файл .env, не хватает ключей!")

DB_PATH = "restaurants.db"
# Переводы текстов плашки, корзины и предложения владельцу — можно править руками
TRANSLATIONS_CACHE_PATH = "translations_cache.json"

# --- OPENSTREETMAP ---
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
REVERSE_NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
OSM_USER_AGENT = "restaurant-create-bot/1.2"
# fast_food исключён намеренно: сетям вроде McDonald's/KFC сайт не нужен.
# Сети, помеченные как restaurant/cafe, отсекаются отдельно по тегам brand (см. osm.py).
FOOD_AMENITIES = "restaurant|cafe|bar|pub|food_court"

# --- ЛИДЫ И ГЕНЕРАЦИЯ ---
MAX_SITES_PER_REQUEST = 3
LLM_TIMEOUTS = [90, 120, 180]
PAGES_WAIT_SECONDS = 90  # Сколько ждать, пока GitHub Pages соберёт страницу
