# -*- coding: utf-8 -*-
"""
Pixel Claude — голосовая пиксельная оболочка над claude CLI.

Микрофон → faster-whisper → claude -p (stream-json) → портрет + лента + голос.
Запуск: python app.py   (или ярлык PixelClaude.bat)
"""
import json
import os
import shutil
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QObject, QTimer, Signal      # noqa: E402
from PySide6.QtGui import QAction, QIcon                # noqa: E402
from PySide6.QtWidgets import (QApplication, QMenu,     # noqa: E402
                               QSystemTrayIcon)

from core import sessions                               # noqa: E402
from core.agent import AgentRunner                      # noqa: E402
from core.stt import Listener                           # noqa: E402
from core.tts import Speaker                            # noqa: E402
from ui.window import MainWindow                        # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(ROOT, "state.json")
CRASH = os.path.join(ROOT, "crash.log")
# Свой реестр сессий, заведённых из оболочки. Приложение Claude держит сайдбар
# по собственной базе (IndexedDB) со своими id — сессию, созданную снаружи, оно
# не увидит. Чтобы такие разговоры не пропадали хотя бы здесь, ведём их сами.
MINE = os.path.join(ROOT, "mine.json")


def install_crash_log():
    """
    Под pythonw stderr уходит в никуда, и падение выглядит как «окно исчезло».
    Пишем всё в crash.log, иначе разбираться не с чем.
    """
    import datetime
    import traceback

    def dump(kind, exc_type, exc, tb):
        try:
            with open(CRASH, "a", encoding="utf-8") as fh:
                fh.write(f"\n=== {kind} {datetime.datetime.now():%Y-%m-%d %H:%M:%S} ===\n")
                traceback.print_exception(exc_type, exc, tb, file=fh)
        except OSError:
            pass

    sys.excepthook = lambda t, e, tb: dump("main", t, e, tb)
    threading.excepthook = lambda a: dump(f"thread {a.thread.name}",
                                          a.exc_type, a.exc_value, a.exc_traceback)
    try:                                  # сам stderr тоже в файл
        sys.stderr = open(CRASH, "a", encoding="utf-8", buffering=1)
    except OSError:
        pass


def load_config():
    """
    Свой config.json в гит не попадает: в нём рабочая папка и прочее локальное,
    а репозиторий публичный. При первом запуске создаётся из config.example.json.
    """
    path = os.path.join(ROOT, "config.json")
    if not os.path.exists(path):
        shutil.copyfile(os.path.join(ROOT, "config.example.json"), path)
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    if not cfg.get("workdir") or not os.path.isdir(cfg["workdir"]):
        cfg["workdir"] = ROOT              # чужой путь из примера не должен ронять
    return cfg


def load_state():
    try:
        with open(STATE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_state(data):
    _write_json(STATE, data)


def _write_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass


def load_mine():
    try:
        with open(MINE, encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def remember_mine(item):
    """Дописывает сессию оболочки в свой реестр, без дублей."""
    items = [x for x in load_mine() if x.get("id") != item["id"]]
    items.insert(0, item)
    _write_json(MINE, items[:60])


def merge_sessions(app_items):
    """Список приложения плюс свои сессии, свежие сверху."""
    known = {it["id"] for it in app_items}
    out = list(app_items)
    for mine in load_mine():
        if mine.get("id") in known or not os.path.exists(mine.get("path") or ""):
            continue
        folder = os.path.basename((mine.get("cwd") or "").rstrip("\\/"))
        title = mine.get("title") or "без названия"
        out.append({**mine, "named": False, "mine": True,
                    "label": f"▸ {title}" + (f" · {folder}" if folder else ""),
                    "mtime": os.path.getmtime(mine["path"])})
    return sorted(out, key=lambda it: it["mtime"], reverse=True)


class Shell(QObject):
    tail_loaded = Signal(str, list)      # (session_id, [(вид, текст), ...])
    list_loaded = Signal(list)           # список сессий, собранный в фоне

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.win = MainWindow(cfg, os.path.join(ROOT, "assets", "sprites"))
        self.agent = AgentRunner(cfg)
        self.speaker = Speaker(cfg)
        self.listener = Listener(cfg)
        self.busy = False
        self.tray = None
        self.app_items = []          # список из приложения, без своих сессий
        self.last_prompt = ""        # им озаглавим сессию, если её заведут сейчас

        self._wire()
        self._greet()
        self._tray()
        # поднимаем claude сразу: инициализация и хуки отработают, пока человек
        # только тянется к микрофону, и первая реплика не ждёт лишних секунд
        self.agent.start()

    # ── трей ──────────────────────────────────────────────────────────────
    def _tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        icon = QIcon(os.path.join(ROOT, "assets", "app.ico"))
        self.tray = QSystemTrayIcon(icon, self.win)
        self.tray.setToolTip("Pixel Claude")

        # ССЫЛКУ НА МЕНЮ ДЕРЖИМ В self: setContextMenu не забирает владение,
        # локальную переменную сборщик мусора убьёт, а значок в трее останется
        # с висячим указателем — и приложение падает молча.
        self.menu = menu = QMenu()
        act_show = QAction("Показать", menu)
        act_show.triggered.connect(self.win.show_up)
        self.act_mic = QAction("Включить микрофон", menu, checkable=True)
        self.act_mic.triggered.connect(self.win.portrait.mic.setChecked)
        act_quit = QAction("Выход", menu)
        act_quit.triggered.connect(self.quit)
        menu.addAction(act_show)
        menu.addAction(self.act_mic)
        menu.addSeparator()
        menu.addAction(act_quit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda r: self.win.show_up()
            if r == QSystemTrayIcon.Trigger else None)
        self.tray.show()

        self.win.hidden_to_tray.connect(
            lambda: self.tray.showMessage(
                "Pixel Claude", "Свернулась в трей — сессия жива, микрофон работает",
                QSystemTrayIcon.Information, 4000))

    def quit(self):
        self.win.allow_quit = True
        self.listener.stop()
        self.agent.stop()
        if self.tray:
            self.tray.hide()
        QApplication.quit()

    # ── связи ─────────────────────────────────────────────────────────────
    def _wire(self):
        w = self.win
        w.portrait.mic_toggled.connect(self.on_mic)
        w.chat.submitted.connect(self.on_input)
        w.chat.session_picked.connect(self.on_session_picked)
        w.chat.session_new.connect(self.on_session_new)
        self.tail_loaded.connect(self.on_tail)
        self.list_loaded.connect(self.on_list)

        self.agent.log.connect(w.chat.append)
        self.agent.log.connect(self._note_prompt)
        self.agent.emotion.connect(w.portrait.set_emotion)
        self.agent.speak.connect(self.speaker.say)
        self.agent.busy.connect(self.on_busy)
        self.agent.session.connect(self.on_session_created)

        self.listener.heard.connect(self.on_heard)
        self.listener.status.connect(w.chat.set_status)
        self.listener.level.connect(w.portrait.set_level)
        self.listener.listening.connect(
            lambda on: w.chat.set_status("слышу тебя…" if on else "слушаю"))

        self.speaker.speaking.connect(self.on_speaking)
        self.speaker.status.connect(w.chat.set_status)

    def _greet(self):
        chat = self.win.chat
        if not self.agent.available():
            chat.append("error", "claude CLI не найден — проверь PATH")
            return

        # Прошлую сессию поднимаем из state.json мгновенно, а список — в фоне:
        # сбор занимает секунды, и окно на это время просто висело бы.
        state = load_state()
        self.items = []
        if state.get("session_id"):
            self.agent.set_session(state["session_id"], state.get("cwd"))
            chat.append("system", f"продолжаю: {state.get('title') or 'прошлая сессия'}")
            if state.get("path"):
                self._load_tail({"id": state["session_id"], "path": state["path"]})
        else:
            chat.append("system", "включи микрофон и говори")

        chat.set_status("собираю список сессий…")
        threading.Thread(target=lambda: self.list_loaded.emit(
            sessions.list_sessions()), daemon=True).start()

    def _note_prompt(self, kind, text):
        if kind == "user":
            self.last_prompt = text

    # ── сессии ────────────────────────────────────────────────────────────
    def on_list(self, items):
        self.app_items = items
        self.items = merge_sessions(items)
        self.win.chat.set_sessions(self.items, self.agent.session_id)
        self.win.chat.set_status("готова")

    def _find(self, session_id):
        return next((it for it in self.items if it["id"] == session_id), None)

    def _remember(self, item):
        save_state({"session_id": item["id"], "cwd": item.get("cwd"),
                    "path": item.get("path"), "title": item.get("title")})

    def _load_tail(self, item):
        """Хвост читаем в потоке: файл активной сессии бывает на мегабайты."""
        def work():
            self.tail_loaded.emit(item["id"], sessions.tail(item["path"]))
        threading.Thread(target=work, daemon=True).start()

    def on_tail(self, session_id, rows):
        if session_id != self.agent.session_id:
            return                               # пока читали, сессию переключили
        for kind, text in rows:
            self.win.chat.append(kind, text)

    def on_session_picked(self, session_id):
        item = self._find(session_id)
        if not item:
            return
        self.speaker.shutup()
        self.agent.set_session(item["id"], item["cwd"])
        self.win.chat.clear()
        folder = os.path.basename((item["cwd"] or "").rstrip("\\/"))
        self.win.chat.append("system", f"{item['title']} · папка {folder}")
        self.win.portrait.set_emotion("neutral")
        self._remember(item)
        self._load_tail(item)

    def on_session_new(self):
        self.speaker.shutup()
        self.agent.reset()
        self.agent.workdir = self.cfg["workdir"]     # новая — в папке из конфига
        self.win.chat.clear()
        self.win.chat.append("system", "новая сессия — говори")
        self.win.portrait.set_emotion("neutral")
        save_state({})
        self.win.chat.set_sessions(self.items, None)

    def on_session_created(self, session_id):
        """claude завёл новую сессию — заносим в свой реестр и в список."""
        item = {"id": session_id, "cwd": self.agent.workdir,
                "path": os.path.join(sessions.project_dir(self.agent.workdir),
                                     f"{session_id}.jsonl"),
                "title": (self.last_prompt or "новый разговор")[:60]}
        save_state({**item, "session_id": session_id})
        remember_mine(item)
        self.items = merge_sessions(self.app_items)
        self.win.chat.set_sessions(self.items, session_id)

    # ── события ───────────────────────────────────────────────────────────
    def on_mic(self, on):
        if on:
            self.listener.start()
            self.win.portrait.set_emotion("happy")
        else:
            self.listener.stop()
        if self.tray:
            self.act_mic.setChecked(on)
            self.act_mic.setText("Выключить микрофон" if on else "Включить микрофон")

    def on_heard(self, text):
        if self.busy:
            self.win.chat.append("system", f"(пропустила: {text})")
            return
        self.speaker.shutup()
        self.agent.send(text)

    def on_input(self, text):
        self.speaker.shutup()
        self.agent.send(text)

    def on_busy(self, busy):
        self.busy = busy
        self.win.chat.set_status("думаю…" if busy else "готова")

    def on_speaking(self, speaking):
        self.win.portrait.set_talking(speaking)
        if speaking:
            self.listener.set_muted(True)
        else:                       # пауза, чтобы не поймать собственный хвост
            QTimer.singleShot(400, lambda: self.listener.set_muted(False))

    def show(self):
        self.win.show()


def main():
    install_crash_log()
    cfg = load_config()
    app = QApplication(sys.argv)
    app.setApplicationName("Pixel Claude")
    app.setQuitOnLastWindowClosed(False)     # крестик прячет в трей, не убивает
    icon = os.path.join(ROOT, "assets", "app.ico")
    if os.path.exists(icon):
        app.setWindowIcon(QIcon(icon))

    shell = Shell(cfg)
    shell.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
