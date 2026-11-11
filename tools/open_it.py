# -*- coding: utf-8 -*-
"""
Открыть папку, файл или сайт. Ничего не запускает и ничего не меняет.

Зачем отдельная команда. Claude Code не выполняет новую команду оболочки без
подтверждения человеком, а подтверждать в голосовой оболочке негде: окна с
кнопкой «Разрешить» нет, диалог идёт голосом. Значит, то, что ей можно,
должно быть разрешено заранее и поимённо. Разрешать `explorer.exe` целиком
нельзя: им открывается не только папка, но и любой .exe по пути. Поэтому
наружу торчит одна команда, которая умеет ровно открывать:

    python tools/open_it.py "C:/Users/dakak/Downloads"   папка в проводнике
    python tools/open_it.py "C:/отчёт.pdf"               файл в его программе
    python tools/open_it.py https://bitrix24.kz          сайт в браузере
    python tools/open_it.py битрикс                      закладка по названию
    python tools/open_it.py --browser                    браузер по умолчанию
    python tools/open_it.py edge                         именно Edge

Исполняемое она открывать отказывается: `открой setup.exe` голосом звучит
невинно, а означает «запусти программу». Список расширений ниже, и он
закрытый — что не разрешено, то не открывается.
"""
import io
import os
import re
import subprocess
import sys
import webbrowser

# reconfigure, а не новый TextIOWrapper: обёртка закрывает поток, когда её
# собирает сборщик мусора, и второй такой модуль в одном процессе гасит первый
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Открыть такое — значит запустить. Не наша работа.
RUNNABLE = {".exe", ".com", ".bat", ".cmd", ".ps1", ".psm1", ".vbs", ".vbe",
            ".js", ".jse", ".wsf", ".wsh", ".msi", ".msp", ".scr", ".cpl",
            ".reg", ".hta", ".jar", ".pif", ".lnk", ".url", ".appref-ms",
            ".py", ".pyw", ".sh"}

URLISH = re.compile(r"^(https?://|www\.)|^[\w.-]+\.(ru|com|org|net|kg|kz|io|dev|me)(/|$)",
                    re.I)

# Браузер по имени. Список закрытый: «открой Edge» должно открывать Edge, но
# этой же командой нельзя запустить что-то ещё — имя ищется только здесь.
PROGRAMS = ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")
BROWSERS = {
    "edge": ("Microsoft/Edge/Application/msedge.exe",),
    "chrome": ("Google/Chrome/Application/chrome.exe",),
    "яндекс": ("Yandex/YandexBrowser/Application/browser.exe",),
    "yandex": ("Yandex/YandexBrowser/Application/browser.exe",),
    "firefox": ("Mozilla Firefox/firefox.exe",),
}


def browser_exe(name):
    for tail in BROWSERS.get(name, ()):
        for env in PROGRAMS:
            root = os.environ.get(env)
            if not root:
                continue
            path = os.path.join(root, *tail.split("/"))
            if os.path.isfile(path):
                return path
    return None


def open_browser(name, url=""):
    exe = browser_exe(name)
    if not exe:
        return False
    subprocess.Popen([exe] + ([url] if url else []))
    say(f"открыла {name}" + (f" на {url}" if url else ""))
    return True


def say(text):
    print(text)


def open_url(url):
    if not url.lower().startswith(("http://", "https://", "file:///")):
        url = "https://" + url
    webbrowser.open(url)
    say(f"открыла {url}")


def open_path(path):
    full = os.path.abspath(os.path.expanduser(path))
    if os.path.isdir(full):
        # explorer всегда возвращает код 1, даже когда всё открылось
        subprocess.run(["explorer.exe", full.replace("/", "\\")], check=False)
        say(f"открыла папку {full}")
        return True
    if os.path.isfile(full):
        ext = os.path.splitext(full)[1].lower()
        if ext in RUNNABLE:
            say(f"не открою {os.path.basename(full)}: это запуск программы, "
                f"а не просмотр файла")
            return True
        os.startfile(full)          # noqa: S606 — открыть в программе по умолчанию
        say(f"открыла {full}")
        return True
    return False


def by_bookmark(query):
    try:
        import bookmarks
    except ImportError:
        return False
    found = bookmarks.find(bookmarks.read(), query)
    if not found:
        return False
    best = found[0]
    webbrowser.open(best["url"])
    say(f"открыла закладку «{best['name']}» — {best['url']}")
    if len(found) > 1:
        say("подходило ещё: " + ", ".join(i["name"] for i in found[1:5]))
    return True


def main():
    args = [a for a in sys.argv[1:] if a.strip()]
    if not args or args[0] == "--browser":
        rest = " ".join(args[1:]).strip()
        open_url(rest or "https://www.google.com")
        return
    query = " ".join(args).strip().strip('"')

    first = args[0].lower().strip('"')
    if first in BROWSERS and open_browser(first, " ".join(args[1:]).strip()):
        return
    if open_path(query):
        return
    if URLISH.search(query):
        open_url(query)
        return
    if by_bookmark(query):
        return
    say(f"не нашла, что открыть по запросу «{query}»: такого пути нет, "
        f"на адрес не похоже, в закладках тоже пусто")


if __name__ == "__main__":
    main()
