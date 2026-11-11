# -*- coding: utf-8 -*-
"""
Закладки браузера: найти и открыть. Только чтение.

Зачем отдельная команда, а не просто доступ к папке. Закладки лежат в профиле
браузера, а там же, в соседних файлах, — сохранённые пароли (`Login Data`),
куки, история и ключ, которым всё это зашифровано (`Local State`). Открыть
помощнице папку профиля ради закладок — значит открыть и это. Поэтому наружу
торчит одна команда с зашитым набором действий: она разбирает файл `Bookmarks`,
достаёт оттуда только имя и адрес и ничего не пишет.

По той же причине здесь нет ни добавления, ни удаления, ни переименования
закладок: правка профиля на ходу ломает его в открытом браузере, а ослышавшееся
распознавание речи делает такую правку в любой момент.

    python tools/bookmarks.py                  все закладки
    python tools/bookmarks.py почта            найти по имени или адресу
    python tools/bookmarks.py --open почта     открыть первую подходящую
    python tools/bookmarks.py --open vk.com    открыть адрес напрямую
    python tools/bookmarks.py --browser chrome искать в другом браузере

Открывается всё в браузере по умолчанию.
"""
import io
import json
import os
import re
import sys
import webbrowser

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

LOCAL = os.environ.get("LOCALAPPDATA", os.path.expanduser("~/AppData/Local"))

BROWSERS = {
    "edge": os.path.join(LOCAL, "Microsoft", "Edge", "User Data"),
    "chrome": os.path.join(LOCAL, "Google", "Chrome", "User Data"),
    "yandex": os.path.join(LOCAL, "Yandex", "YandexBrowser", "User Data"),
}

LIMIT = 40          # сколько строк печатать: длинный список вслух не зачитать


def files(browser=None):
    """Файлы закладок всех профилей: (браузер, профиль, путь)."""
    out = []
    for name, root in BROWSERS.items():
        if browser and name != browser:
            continue
        if not os.path.isdir(root):
            continue
        for profile in sorted(os.listdir(root)):
            path = os.path.join(root, profile, "Bookmarks")
            if os.path.isfile(path):
                out.append((name, profile, path))
    return out


def walk(node, folder=""):
    """Дерево закладок в плоский список. Берём только имя, адрес и папку."""
    if not isinstance(node, dict):
        return
    if node.get("type") == "url" and node.get("url"):
        yield {"name": node.get("name") or node["url"],
               "url": node["url"], "folder": folder}
        return
    here = f"{folder}/{node.get('name')}" if node.get("name") else folder
    for child in node.get("children") or []:
        yield from walk(child, here)


def read(browser=None):
    items = []
    for name, profile, path in files(browser):
        try:
            with io.open(path, encoding="utf-8") as f:
                roots = (json.load(f).get("roots") or {})
        except (OSError, ValueError) as exc:
            print(f"не прочитать закладки {name}/{profile}: {exc}")
            continue
        for root in roots.values():
            for item in walk(root):
                item["browser"] = name
                items.append(item)
    return items


def find(items, query):
    """Поиск по имени, адресу и папке. Пустой запрос — всё подряд."""
    if not query:
        return items
    words = [w for w in re.split(r"\s+", query.lower().strip()) if w]
    out = []
    for item in items:
        hay = f"{item['name']} {item['url']} {item['folder']}".lower()
        if all(w in hay for w in words):
            out.append(item)
    # точное совпадение имени — вперёд: «почта» должно открыть почту,
    # а не первую попавшуюся страницу, где это слово встретилось в адресе
    out.sort(key=lambda i: (query.lower() not in i["name"].lower(), len(i["name"])))
    return out


def main():
    args = sys.argv[1:]
    browser = None
    if "--browser" in args:
        i = args.index("--browser")
        browser = args[i + 1].lower() if i + 1 < len(args) else None
        del args[i:i + 2]
    opening = "--open" in args
    if opening:
        args.remove("--open")
    query = " ".join(args).strip()

    if opening and re.match(r"^(https?://|www\.)|\.[a-z]{2,}(/|$)", query, re.I):
        url = query if query.startswith("http") else "https://" + query
        webbrowser.open(url)
        print(f"открыла {url}")
        return

    items = read(browser)
    if not items:
        print("закладок не нашлось — браузер не установлен или профиль пуст")
        return
    found = find(items, query)
    if not found:
        print(f"закладок по запросу «{query}» нет, всего их {len(items)}")
        return

    if opening:
        best = found[0]
        webbrowser.open(best["url"])
        print(f"открыла «{best['name']}» — {best['url']}")
        if len(found) > 1:
            print(f"подходило ещё {len(found) - 1}: "
                  + ", ".join(i["name"] for i in found[1:6]))
        return

    print(f"нашлось {len(found)}" + (f" из {len(items)}" if query else ""))
    for item in found[:LIMIT]:
        where = item["folder"].strip("/") or item["browser"]
        print(f"  {item['name']}  —  {item['url']}  [{where}]")
    if len(found) > LIMIT:
        print(f"  …и ещё {len(found) - LIMIT}")


if __name__ == "__main__":
    main()
