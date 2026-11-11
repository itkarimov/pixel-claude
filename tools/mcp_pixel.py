# -*- coding: utf-8 -*-
"""
Свои инструменты помощницы: открыть что-нибудь и найти закладку.

Почему не команда оболочки. Разрешения claude на Bash разбирают командную
строку PowerShell, и двоеточие в пути — `C:/Users/…` — рвёт её на «несколько
операций», после чего правило не совпадает ни с чем. Проверено: отказ приходит
даже на правило, выписанное под команду слово в слово. Обойти это можно только
полным отсутствием двоеточия в команде, а модель упорно пишет путь с буквой
диска — никакая подсказка её не переучивает.

Инструмент MCP этой беды лишён: путь едет аргументом в JSON, командной строки
нет вовсе, разбирать нечего. Заодно он виден модели как настоящий инструмент со
своим описанием, а не как строчка в приписке, которую легко пролистать.

Сервер поднимает сама оболочка (core/agent.py), руками запускать незачем.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP          # noqa: E402

import bookmarks                                # noqa: E402
import open_it                                  # noqa: E402

app = FastMCP("pixel")


@app.tool()
def open(target: str) -> str:
    """
    ЕДИНСТВЕННЫЙ способ что-либо открыть: папку, файл, сайт, закладку, браузер.

    Просят открыть что угодно — вызывай этот инструмент, и только его. Команды
    оболочки explorer, explorer.exe, start, Start-Process, Invoke-Item,
    webbrowser для этого НЕ РАБОТАЮТ: подтвердить их некому, они повиснут и
    вернут «requires approval». Сколько их ни переписывай, результат тот же.

    target: путь к папке или файлу (можно с буквой диска), адрес сайта,
        название закладки, имя браузера (edge, chrome) или просто «загрузки»,
        «рабочий стол», «документы».

    Ничего не удаляет, не перемещает и не правит. Запускать программы
    отказывается: .exe, .bat, .ps1 и прочее исполняемое не откроет.
    """
    target = (target or "").strip()
    if not target:
        return "нечего открывать: пустой запрос"

    name = target.lower().strip('"')
    if name in open_it.BROWSERS and open_it.open_browser(name):
        return f"открыла {name}"

    path = open_it.resolve(target)
    if path and open_it.open_path(path):
        return f"открыла {path}"
    if open_it.URLISH.search(target):
        open_it.open_url(target)
        return f"открыла {target}"
    if open_it.by_bookmark(target):
        return f"открыла закладку по запросу «{target}»"
    return (f"не нашла, что открыть по запросу «{target}»: такого пути нет, "
            f"на адрес не похоже, в закладках тоже пусто")


@app.tool()
def bookmarks_find(query: str = "") -> str:
    """
    Найти закладки браузера по названию, адресу или папке. Только чтение.

    query: что искать. Пустой запрос — все закладки.

    Возвращает список «название — адрес [папка]». Чтобы открыть найденное,
    вызови open с названием закладки.
    """
    items = bookmarks.read()
    if not items:
        return "закладок не нашлось"
    found = bookmarks.find(items, query)
    if not found:
        return f"по запросу «{query}» закладок нет, всего их {len(items)}"
    head = found[:25]
    lines = [f"{i['name']} — {i['url']} [{i['folder'].strip('/') or i['browser']}]"
             for i in head]
    tail = f"\n…и ещё {len(found) - len(head)}" if len(found) > len(head) else ""
    return f"нашлось {len(found)} из {len(items)}:\n" + "\n".join(lines) + tail


if __name__ == "__main__":
    app.run()
