import functools
import threading
import time

import requests
from github import Github, InputGitTreeElement

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
    """Удаляет страницу и её меню из репозитория. True, если их там больше нет."""
    try:
        _, repo = _get_repo()
        with _github_deploy_lock:
            try:
                contents = repo.get_contents(place_id, ref="main")
            except Exception:
                return True  # уже удалена
            for item in contents if isinstance(contents, list) else [contents]:
                repo.delete_file(item.path, f"Remove site for {place_id} (refused)", item.sha, branch="main")
        return True
    except Exception as e:
        print(f"🚨 Не удалось удалить сайт {place_id}: {e}")
        return False


def file_url(place_id, filename):
    return site_url(place_id) + filename


def wait_until_live(url, seconds):
    """Ждёт, пока GitHub Pages соберёт страницу. Возвращает False, если не дождались."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            if requests.get(url, timeout=10).status_code == 200:
                return True
        except requests.exceptions.RequestException:
            pass
        time.sleep(10)
    return False


def wait_until_published(url, needle, seconds):
    """Ждёт, пока Pages начнёт отдавать обновлённый файл: старый лежит в кэше и отвечает 200,
    поэтому ждём не код ответа, а появление needle в содержимом."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            response = requests.get(url, params={"t": int(time.time())}, timeout=10,
                                    headers={"Cache-Control": "no-cache"})
            if response.status_code == 200 and needle in response.text:
                return True
        except requests.exceptions.RequestException:
            pass
        time.sleep(10)
    return False


def pages_enabled():
    """Включён ли GitHub Pages у репозитория. Без него все ссылки отдают 404."""
    try:
        _, repo = _get_repo()
        return repo.has_pages
    except Exception as e:
        print(f"🚨 Не удалось проверить GitHub Pages: {e}")
        return False


SITE_LIVE = "live"
SITE_BUILDING = "building"
SITE_PAGES_DISABLED = "pages_disabled"


def _commit_together(repo, files, message):
    """Все файлы сайта одним коммитом: каждый коммит запускает сборку Pages и отменяет предыдущую."""
    elements = [InputGitTreeElement(path, "100644", "blob", content) for path, content in files.items()]
    parent = repo.get_git_commit(repo.get_branch("main").commit.sha)
    tree = repo.create_git_tree(elements, parent.tree)
    commit = repo.create_git_commit(message, tree, [parent])
    repo.get_git_ref("heads/main").edit(commit.sha)


def _commit_one_by_one(repo, files, message):
    for path, content in files.items():
        try:
            contents = repo.get_contents(path, ref="main")
            repo.update_file(contents.path, message, content, contents.sha, branch="main")
        except Exception:
            repo.create_file(path, message, content, branch="main")


def commit_files(place_id, files, message=None):
    """Коммитит файлы сайта ({"index.html": ..., "menu.json": ...}). True, если получилось."""
    try:
        _, repo = _get_repo()
    except Exception:
        print(f"🚨 Репозиторий '{config.GITHUB_REPO}' не найден.")
        return False

    files = {f"{place_id}/{filename}": content for filename, content in files.items()}
    message = message or f"Site for {place_id}"

    with _github_deploy_lock:
        try:
            try:
                _commit_together(repo, files, message)
            except Exception as e:
                print(f"⚠️ Не вышло одним коммитом ({e}), публикую файлы по одному...")
                _commit_one_by_one(repo, files, message)

            time.sleep(3)  # Краткая пауза для стабильности API
            return True
        except Exception as e:
            print(f"🚨 Ошибка GitHub: {e}")
            return False


def deploy_to_github(place_id, files):
    """Публикует сайт целиком. Возвращает (url, статус): SITE_LIVE, SITE_BUILDING или SITE_PAGES_DISABLED."""
    if not commit_files(place_id, files):
        return None, None

    pages_url = site_url(place_id)
    # Ждать сборки бессмысленно, если Pages вообще выключен — ссылка всё равно даст 404
    if not pages_enabled():
        print("⚠️ GitHub Pages выключен — файл закоммичен, но страница не откроется.")
        return pages_url, SITE_PAGES_DISABLED

    # Сборка Pages обычно идёт 30–60 сек, но бывает и 5+ минут; к тому же каждый новый коммит
    # отменяет незаконченную сборку — страница появится с последней успешной
    live = wait_until_live(pages_url, config.PAGES_WAIT_SECONDS)
    return pages_url, SITE_LIVE if live else SITE_BUILDING
