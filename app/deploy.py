import functools
import threading
import time

import requests
from github import Github

from . import config

# Глобальная блокировка для защиты репозитория от конфликтов SHA при параллельном деплое
_github_deploy_lock = threading.Lock()


def _get_repo():
    g = Github(config.GITHUB_TOKEN)
    user = g.get_user()
    return user, user.get_repo(config.GITHUB_REPO)


@functools.cache
def _github_login():
    return Github(config.GITHUB_TOKEN).get_user().login


def site_url(place_id):
    """Адрес страницы известен до публикации — он нужен для превью и QR-кода внутри самой страницы."""
    return f"https://{_github_login()}.github.io/{config.GITHUB_REPO}/{place_id}/"


def delete_site(place_id):
    """Удаляет страницу из репозитория. True, если её там больше нет."""
    file_path = f"{place_id}/index.html"
    try:
        _, repo = _get_repo()
        with _github_deploy_lock:
            try:
                contents = repo.get_contents(file_path, ref="main")
            except Exception:
                return True  # уже удалена
            repo.delete_file(contents.path, f"Remove site for {place_id} (refused)", contents.sha, branch="main")
        return True
    except Exception as e:
        print(f"🚨 Не удалось удалить сайт {place_id}: {e}")
        return False


def _wait_until_live(url):
    """Ждёт, пока GitHub Pages соберёт страницу. Возвращает False, если не дождались."""
    deadline = time.time() + config.PAGES_WAIT_SECONDS
    while time.time() < deadline:
        try:
            if requests.get(url, timeout=10).status_code == 200:
                return True
        except requests.exceptions.RequestException:
            pass
        time.sleep(5)
    return False


def pages_enabled():
    """Включён ли GitHub Pages у репозитория. Без него все ссылки отдают 404."""
    try:
        _, repo = _get_repo()
        return repo.has_pages
    except Exception as e:
        print(f"🚨 Не удалось проверить GitHub Pages: {e}")
        return False


def deploy_to_github(place_id, html_content):
    """Публикует сайт. Возвращает (url, доступен_ли_он_уже)."""
    try:
        _, repo = _get_repo()
    except Exception:
        print(f"🚨 Репозиторий '{config.GITHUB_REPO}' не найден.")
        return None, False

    file_path = f"{place_id}/index.html"
    pages_url = site_url(place_id)

    with _github_deploy_lock:
        try:
            try:
                contents = repo.get_contents(file_path, ref="main")
                repo.update_file(contents.path, f"Update site for {place_id}", html_content, contents.sha,
                                 branch="main")
            except Exception:
                repo.create_file(file_path, f"Init site for {place_id}", html_content, branch="main")

            time.sleep(3)  # Краткая пауза для стабильности API
        except Exception as e:
            print(f"🚨 Ошибка GitHub: {e}")
            return None, False

    # Ждать сборки бессмысленно, если Pages вообще выключен — ссылка всё равно даст 404
    if not repo.has_pages:
        print("⚠️ GitHub Pages выключен — файл закоммичен, но страница не откроется.")
        return pages_url, False

    return pages_url, _wait_until_live(pages_url)
