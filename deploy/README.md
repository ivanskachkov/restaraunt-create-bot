# Запуск на Raspberry Pi

Бот подходит для Pi: он почти всё время ждёт сообщений, а тяжёлую работу (генерацию сайта)
делает Gemini на своей стороне. Хватит Pi 3 и выше, нужен только стабильный интернет.

⚠️ **Бот должен работать в одном месте.** Telegram не отдаёт обновления двум экземплярам сразу:
если бот останется запущенным и на компьютере, оба будут получать ошибку `409 Conflict`.
Перед запуском на Pi останови бота на компьютере.

## 1. Подготовка Pi

```bash
sudo apt update
sudo apt install -y git python3-venv
sudo timedatectl set-timezone Europe/Kyiv   # иначе в /menu будет неверное время создания
```

## 2. Код и зависимости

```bash
git clone https://github.com/ivanskachkov/restaraunt-create-bot.git
cd restaraunt-create-bot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Установка занимает несколько минут: Pillow и cryptography на ARM ставятся из готовых пакетов,
но качаются небыстро.

## 3. Ключи

`.env` не лежит в репозитории — его нужно создать на Pi:

```bash
nano .env
```

```
TELEGRAM_BOT_TOKEN=...
GITHUB_TOKEN=...
LLM_API_KEY=...
GITHUB_REPO=websites-for-bot
TELEGRAM_OWNER_ID=...
SELLER_NAME=...
```

`TELEGRAM_OWNER_ID` обязателен: без него ботом сможет пользоваться кто угодно, кто найдёт его в Telegram.

## 4. Проверка вручную

```bash
.venv/bin/python main.py
```

Должно появиться `🤖 Бот успешно запущен!`. Напиши боту `/demo` — если пришёл сайт, всё работает.
Останови через `Ctrl+C`.

## 5. Автозапуск

```bash
sed -e "s|__USER__|$USER|g" -e "s|__DIR__|$PWD|g" deploy/restaurant-bot.service \
  | sudo tee /etc/systemd/system/restaurant-bot.service > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now restaurant-bot
```

Команду нужно выполнять из папки с ботом — она подставляет в файл службы твоего пользователя
и текущий путь.

Логи и состояние:

```bash
systemctl status restaurant-bot
journalctl -u restaurant-bot -f      # живой лог, выйти — Ctrl+C
```

## 6. Обновление

```bash
cd ~/restaraunt-create-bot
git pull
.venv/bin/pip install -r requirements.txt
sudo systemctl restart restaurant-bot
```

## База данных

`restaurants.db` лежит рядом с кодом и в git не попадает — это вся история лидов и статусов.
Раз в неделю имеет смысл копировать её с Pi:

```bash
scp pi@raspberrypi.local:~/restaraunt-create-bot/restaurants.db ./backup/
```

Если переносишь бота с компьютера на Pi — скопируй туда же старую базу, иначе уже обработанные
заведения снова попадут в лиды.

## Если что-то не так

| Симптом | Причина |
|---|---|
| `409 Conflict` в логе | бот запущен ещё где-то — останови вторую копию |
| `🚨 ОШИБКА: Проверь файл .env` | нет `.env` или в нём не хватает ключа |
| Сайты отдают 404 | выключен GitHub Pages в репозитории с сайтами |
| `Bad credentials` | истёк или отозван `GITHUB_TOKEN` |
| Служба перезапускается по кругу | `journalctl -u restaurant-bot -n 50` покажет причину |
