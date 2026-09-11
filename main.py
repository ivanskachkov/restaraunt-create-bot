import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from telebot.types import BotCommand

from app import config, db, deploy
from app.telegram_bot import bot


def main():
    db.init_db()
    if not deploy.pages_enabled():
        print(f"⚠️ GitHub Pages выключен для '{config.GITHUB_REPO}' — сгенерированные сайты будут отдавать 404.")
        print("   Включить: Settings → Pages → Source: Deploy from a branch → main / (root)")
    if config.OWNER_ID is None:
        print("⚠️ TELEGRAM_OWNER_ID не задан — ботом (и удалением сайтов) может пользоваться кто угодно.")
        print("   Напиши боту /start — он пришлёт твой ID, впиши его в .env.")
    bot.set_my_commands([
        BotCommand("menu", "Сгенерированные сайты и статусы"),
        BotCommand("start", "Помощь"),
    ])
    print("🤖 Бот успешно запущен! Напиши ему в Телеграм.")
    bot.infinity_polling()


if __name__ == "__main__":
    main()
