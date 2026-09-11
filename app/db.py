import json
import sqlite3
from contextlib import closing
from datetime import datetime

from . import config

STATUS_LABELS = {
    "done": "🆕 Сгенерирован",
    "contacted": "✉️ Написал",
    "sold": "✅ Продан",
    "refused": "❌ Отказ",
}


def _connect():
    # timeout=15 спасает от ошибки "database is locked" при многопоточности
    conn = sqlite3.connect(config.DB_PATH, timeout=15.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with closing(_connect()) as conn, conn:
        conn.execute('''
                     CREATE TABLE IF NOT EXISTS places
                     (
                         place_id TEXT PRIMARY KEY,
                         name     TEXT,
                         status   TEXT,
                         site_url TEXT
                     )
                     ''')
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(places)")}
        for column, column_type in (("city", "TEXT"), ("contacts", "TEXT"), ("created_at", "TEXT"),
                                    ("has_ordering", "INTEGER DEFAULT 0"), ("country", "TEXT")):
            if column not in existing:
                conn.execute(f"ALTER TABLE places ADD COLUMN {column} {column_type}")

        # Прошлая версия хранила id как osm_node_123 и публиковала в отдельные репозитории promo-*
        # (их уже нет). Без приведения к текущему формату эти заведения снова попадали бы в лиды.
        conn.execute(r"UPDATE OR IGNORE places SET place_id = replace(place_id, '_', '-'), site_url = NULL "
                     r"WHERE place_id LIKE 'osm\_%' ESCAPE '\'")
        conn.execute(r"DELETE FROM places WHERE place_id LIKE 'osm\_%' ESCAPE '\'")


def processed_ids():
    """Все заведения, которые уже были в работе — в любом статусе, включая отказ."""
    with closing(_connect()) as conn:
        return {row["place_id"] for row in conn.execute("SELECT place_id FROM places")}


def save_place(place_id, name, site_url, city, contacts, has_ordering, country):
    with closing(_connect()) as conn, conn:
        conn.execute("INSERT INTO places (place_id, name, status, site_url, city, contacts, created_at, "
                     "has_ordering, country) VALUES (?, ?, 'done', ?, ?, ?, ?, ?, ?)",
                     (place_id, name, site_url, city, json.dumps(contacts, ensure_ascii=False),
                      datetime.now().isoformat(sep=" ", timespec="minutes"), int(has_ordering), country))


def _row_to_place(row):
    place = dict(row)
    place["contacts"] = json.loads(place["contacts"]) if place["contacts"] else None
    return place


def list_places(offset, limit):
    with closing(_connect()) as conn:
        total = conn.execute("SELECT COUNT(*) FROM places").fetchone()[0]
        rows = conn.execute("SELECT * FROM places ORDER BY created_at IS NULL, created_at DESC LIMIT ? OFFSET ?",
                            (limit, offset)).fetchall()
        return [_row_to_place(r) for r in rows], total


def status_counts():
    with closing(_connect()) as conn:
        return {row["status"]: row["n"] for row in
                conn.execute("SELECT status, COUNT(*) AS n FROM places GROUP BY status")}


def get_place(place_id):
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM places WHERE place_id=?", (place_id,)).fetchone()
        return _row_to_place(row) if row else None


def set_status(place_id, status, clear_site=False):
    with closing(_connect()) as conn, conn:
        if clear_site:
            conn.execute("UPDATE places SET status=?, site_url=NULL WHERE place_id=?", (status, place_id))
        else:
            conn.execute("UPDATE places SET status=? WHERE place_id=?", (status, place_id))
