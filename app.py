# -*- coding: utf-8 -*-
"""
Pixel Claude — голосовая пиксельная оболочка над claude CLI.

Микрофон → faster-whisper → claude -p (stream-json) → портрет + лента + голос.
Запуск: python app.py   (или ярлык PixelClaude.bat)
"""
import json
import os
import re
import shutil
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def claim_single_instance():
    """
    Пускает только одну копию. True — мы первые, False — надо уйти.

    Держим именованный мьютекс Windows: он живёт вместе с процессом и исчезает
    сам, даже если приложение убили, — в отличие от файла-замка, после которого
    пришлось бы разбираться с чужим PID.

    Проверка стоит здесь, до импорта PySide6 и остального: они грузятся
    несколько секунд, и если брать мьютекс в main(), двойной клик по ярлыку
    успевает проскочить — обе копии считают себя первыми.
    """
    if os.name != "nt":
        return True
    import ctypes

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW(None, False, "PixelClaude.single.instance")
    if kernel32.GetLastError() != 183:          # ERROR_ALREADY_EXISTS
        return True                             # мьютекс нарочно не закрываем

    # Копия уже работает — поднимаем её окно, иначе человек решит, что ярлык
    # не сработал, и будет жать снова.
    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW(None, "Pixel Claude")
    if hwnd:
        user32.ShowWindow(hwnd, 9)              # SW_RESTORE — достать из трея
        user32.SetForegroundWindow(hwnd)
    return False


if __name__ == "__main__" and not claim_single_instance():
    sys.exit(0)

from PySide6.QtCore import QObject, QTimer, Signal      # noqa: E402
from PySide6.QtGui import QAction, QIcon                # noqa: E402
from PySide6.QtWidgets import (QApplication, QMenu,     # noqa: E402
                               QSystemTrayIcon)

from core import sessions                               # noqa: E402
from core.agent import AgentRunner                      # noqa: E402
from core.confirm import Confirmer                      # noqa: E402
from core import groq                                   # noqa: E402
from core import llama                                  # noqa: E402
from core.groq import GroqRunner                        # noqa: E402
from core.llama import LlamaRunner                      # noqa: E402
from core.stt import Listener                           # noqa: E402
from core.tts import Speaker                            # noqa: E402
from core.vision import Eyes                            # noqa: E402
from core.watcher import Describer                      # noqa: E402
from ui.window import MainWindow                        # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(ROOT, "state.json")
CRASH = os.path.join(ROOT, "crash.log")

# Просьбы, на которые словесное описание не годится: если человек просит
# посмотреть прямо сейчас, фраза двадцатисекундной давности — это враньё,
# нужен настоящий свежий кадр.
BLIND = "[камера выключена]"     # ставим в реплику, когда зрение выключено

LOOK_NOW = re.compile(
    r"(посмотр|смотр|погляд|глян|видишь|что вид|как я выгляж|что у меня|"
    r"что я держ|узна[её]шь|на камер|в камер|кадр)", re.I)


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


class Shell(QObject):
    tail_loaded = Signal(str, list)      # (session_id, [(вид, текст), ...])
    list_loaded = Signal(list)           # список сессий, собранный в фоне

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.win = MainWindow(cfg, os.path.join(ROOT, "assets", "sprites"))
        # Все мозги живут рядом. Claude — процесс, его держим поднятым;
        # Meta AI и Groq — просто http, ничего не занимают. Переключение
        # кнопкой, без перезапуска.
        self.agent = AgentRunner(cfg)
        self.llama = LlamaRunner(cfg)
        self.groq = GroqRunner(cfg)
        self.brains = {"claude": self.agent, "llama": self.llama,
                       "groq": self.groq}
        self.backend = "claude"
        self.speaker = Speaker(cfg)
        self.listener = Listener(cfg)
        self.eyes = Eyes(cfg)
        self.watcher = Describer(cfg, self.eyes)
        self.confirmer = Confirmer(cfg)
        self.busy = False
        self.saved_frames = 0        # сколько кадров не отправили как повтор
        self.tray = None
        self.items = []              # сессии из сайдбара приложения
        self.last_prompt = ""        # им озаглавим сессию, если её заведут сейчас

        self._wire()
        self._greet()
        self._tray()
        # поднимаем claude сразу: инициализация отработает, пока человек только
        # тянется к микрофону, и первая реплика не ждёт лишних секунд
        self.agent.start()
        # то же и для голоса: первое соединение с синтезом стоит лишние 1.7 с
        self.speaker.warmup()

    # ── мозг ──────────────────────────────────────────────────────────────
    @property
    def brain(self):
        """Тот мозг, что сейчас отвечает."""
        return self.brains.get(self.backend, self.agent)

    def on_brain_switched(self, name):
        if name == self.backend:
            return
        brain = self.brains.get(name)
        if brain is None:
            return
        if not brain.available():
            # молча оставить кнопку переключённой — обман: ответов не будет
            # (у Клода brand нет: он не по ключу живёт, а по наличию CLI)
            self.win.chat.append(
                "error", f"{getattr(brain, 'brand', 'claude')} недоступен. "
                         + getattr(brain, "key_hint", "проверь PATH"))
            self.win.chat.set_brain(self.backend)
            return
        self.speaker.shutup()
        self.backend = name
        self.win.chat.set_brain(name)
        if name == "claude":
            self.win.chat.append("system", "мозг: Claude")
            if not self.agent.alive():
                self.agent.start()
        else:
            brain.start()
            model = self.cfg.get(f"{name}_model") or brain.brand
            self.win.chat.append("system", f"мозг: {brain.brand} · {model}")
            self.win.chat.append("system", "инструментов нет — файлы и команды "
                                           "только у Клода")
            if name == "groq" and not brain.searches():
                self.win.chat.append("system", "и без интернета: поиск "
                                               "выключен в config.json")
        self.on_list(self._collect_sessions())      # у каждого мозга свой список

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
        act_show.triggered.connect(self._show_window)
        self.act_mic = QAction("Включить микрофон", menu, checkable=True)
        self.act_mic.triggered.connect(self.win.portrait.mic.setChecked)
        self.act_cam = QAction("Включить камеру", menu, checkable=True)
        self.act_cam.triggered.connect(self.win.portrait.cam.setChecked)
        act_quit = QAction("Выход", menu)
        act_quit.triggered.connect(self.quit)
        menu.addAction(act_show)
        menu.addAction(self.act_mic)
        menu.addAction(self.act_cam)
        menu.addSeparator()
        menu.addAction(act_quit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda r: self._show_window()
            if r == QSystemTrayIcon.Trigger else None)
        self.tray.show()

        self.win.hidden_to_tray.connect(
            lambda: self.tray.showMessage(
                "Pixel Claude", "Свернулась в трей — сессия жива, микрофон работает",
                QSystemTrayIcon.Information, 4000))

    def _show_window(self):
        self.eyes.set_idle(False)
        self.win.show_up()

    def quit(self):
        self.win.allow_quit = True
        self.listener.stop()
        self.watcher.stop()
        self.eyes.stop()
        for brain in self.brains.values():
            brain.stop()
        if self.tray:
            self.tray.hide()
        QApplication.quit()

    # ── связи ─────────────────────────────────────────────────────────────
    def _wire(self):
        w = self.win
        w.portrait.mic_toggled.connect(self.on_mic)
        w.portrait.cam_toggled.connect(self.on_cam)
        w.chat.submitted.connect(self.on_input)
        w.chat.session_picked.connect(self.on_session_picked)
        w.chat.session_new.connect(self.on_session_new)
        w.chat.sessions_needed.connect(self.on_sessions_needed)
        w.chat.brain_switched.connect(self.on_brain_switched)
        self.tail_loaded.connect(self.on_tail)
        self.list_loaded.connect(self.on_list)

        # все мозги говорят в одни и те же уши: лента, портрет, голос
        for brain in self.brains.values():
            brain.log.connect(w.chat.append)
            brain.log.connect(self._note_prompt)
            brain.emotion.connect(w.portrait.set_emotion)
            brain.speak.connect(self.speaker.say)
            brain.busy.connect(self.on_busy)
        self.agent.session.connect(self.on_session_created)
        for name in ("llama", "groq"):
            self.brains[name].session.connect(
                lambda sid, n=name: self.on_api_session(sid, n))

        self.listener.heard.connect(self.on_heard)
        self.listener.status.connect(w.chat.set_status)
        self.listener.level.connect(w.portrait.set_level)
        self.listener.listening.connect(
            lambda on: w.chat.set_status("слышу тебя…" if on else "слушаю"))

        self.speaker.speaking.connect(self.on_speaking)
        self.speaker.status.connect(w.chat.set_status)

        self.eyes.frame.connect(w.portrait.set_camera_frame)
        self.eyes.status.connect(w.chat.set_status)
        self.eyes.active.connect(self.on_eyes)
        self.eyes.skipped.connect(self.on_frame_skipped)
        self.watcher.status.connect(w.chat.set_status)
        self.watcher.described.connect(self.on_described)

        self.confirmer.ask.connect(self.on_confirm_ask)
        self.confirmer.accepted.connect(self.on_confirm_accepted)
        self.confirmer.dropped.connect(self.on_confirm_dropped)
        w.chat.confirm.confirmed.connect(self.confirmer.accept)
        w.chat.confirm.rejected.connect(self.confirmer.reject)
        # свёрнутое окно предпросмотр не показывает — незачем греть камеру
        w.hidden_to_tray.connect(lambda: self.eyes.set_idle(True))

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
            # мозг запоминаем вместе с сессией: id от Meta AI для claude
            # бессмыслен, --resume на него не встанет
            self.backend = state.get("brain") or "claude"
            self.win.chat.set_brain(self.backend)
            self.brain.set_session(state["session_id"], state.get("cwd"))
            chat.append("system", f"продолжаю: {state.get('title') or 'прошлая сессия'}")
            if state.get("path"):
                self._load_tail({"id": state["session_id"], "path": state["path"],
                                 "brain": self.backend})
        else:
            chat.append("system", "включи микрофон и говори")

        chat.set_status("собираю список сессий…")
        self._reload_sessions()

    def _note_prompt(self, kind, text):
        if kind == "user":
            self.last_prompt = text

    # ── сессии ────────────────────────────────────────────────────────────
    def _collect_sessions(self):
        """
        Список для того мозга, что сейчас отвечает. У каждого он свой и
        смешивать их нельзя: у claude это сайдбар приложения, у Meta AI и
        Groq — наши собственные разговоры в llama_sessions и groq_sessions.
        """
        if self.backend == "llama":
            return llama.list_sessions(limit=60)
        if self.backend == "groq":
            return groq.list_sessions(limit=60)
        return sessions.list_sessions(limit=60)

    def _reload_sessions(self):
        """Перечитывает список в потоке — окно на это не замирает."""
        threading.Thread(target=lambda: self.list_loaded.emit(
            self._collect_sessions()), daemon=True).start()

    def on_sessions_needed(self):
        """Открывают список — перечитываем (~30 мс), чтобы не отставал."""
        self.on_list(self._collect_sessions())

    def on_list(self, items):
        self.items = items
        self.win.chat.set_sessions(self.items, self.brain.session_id)
        self.win.chat.set_status("готова")

    def _find(self, session_id):
        return next((it for it in self.items if it["id"] == session_id), None)

    def _remember(self, item):
        save_state({"session_id": item["id"], "cwd": item.get("cwd"),
                    "path": item.get("path"), "title": item.get("title"),
                    "brain": item.get("brain") or "claude"})

    def _load_tail(self, item):
        """Хвост читаем в потоке: файл активной сессии бывает на мегабайты."""
        read = llama.tail if item.get("brain") in ("llama", "groq") else sessions.tail

        def work():
            self.tail_loaded.emit(item["id"], read(item["path"]))
        threading.Thread(target=work, daemon=True).start()

    def on_tail(self, session_id, rows):
        if session_id != self.brain.session_id:
            return                               # пока читали, сессию переключили
        for kind, text in rows:
            self.win.chat.append(kind, text)

    def on_session_picked(self, session_id):
        item = self._find(session_id)
        if not item:
            return
        self.speaker.shutup()
        self.eyes.forget()
        self.watcher.forget()          # в чужом контексте кадра ещё не видели
        # сессия сама знает, чей она мозг — выбор из списка его и включает
        want = item.get("brain") or "claude"
        if want != self.backend:
            self.backend = want
            self.win.chat.set_brain(want)
        self.brain.set_session(item["id"], item.get("cwd"))
        self.win.chat.clear()
        folder = os.path.basename((item["cwd"] or "").rstrip("\\/"))
        self.win.chat.append("system", f"{item['title']} · папка {folder}")
        self.win.portrait.set_emotion("neutral")
        self._remember(item)
        self._load_tail(item)

    def on_session_new(self):
        self.speaker.shutup()
        self.eyes.forget()
        self.watcher.forget()
        self.brain.reset()
        self.brain.workdir = self.cfg["workdir"]     # новая — в папке из конфига
        self.win.chat.clear()
        self.win.chat.append("system", "новая сессия — говори")
        self.win.portrait.set_emotion("neutral")
        save_state({})
        self.win.chat.set_sessions(self.items, None)

    def on_session_created(self, session_id):
        """
        claude завёл новую сессию. В списке её не будет: список — это сайдбар
        приложения, а туда сессия снаружи не попадает. Показываем её отдельной
        строкой «текущая» и запоминаем в state.json, чтобы поднять при запуске.
        """
        item = {"id": session_id, "cwd": self.agent.workdir,
                "path": os.path.join(sessions.project_dir(self.agent.workdir),
                                     f"{session_id}.jsonl"),
                "title": (self.last_prompt or "новый разговор")[:60]}
        save_state({**item, "session_id": session_id})
        self.win.chat.set_sessions(self.items, session_id)

    def on_api_session(self, session_id, name):
        """Meta AI или Groq завели разговор — запоминаем, чтобы поднять при запуске."""
        brain = self.brains[name]
        save_state({"session_id": session_id, "cwd": "", "brain": name,
                    "path": os.path.join(brain.store, f"{session_id}.json"),
                    "title": (self.last_prompt
                              or f"разговор с {brain.brand}")[:60]})
        if self.backend == name:
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

    def on_cam(self, on):
        if on:
            self.eyes.set_idle(not self.win.isVisible())
            self.eyes.start()
            self.watcher.start()
        else:
            self.watcher.stop()
            self.eyes.stop()
            if self.saved_frames:
                # видно, ради чего всё затевалось: кадр ~400 токенов
                self.win.chat.append(
                    "system", f"кадров не отправлено как повтор: {self.saved_frames} "
                              f"(~{self.saved_frames * 400} токенов)")
            self.saved_frames = 0
        if self.tray:
            self.act_cam.setChecked(on)
            self.act_cam.setText("Выключить камеру" if on else "Включить камеру")

    def on_eyes(self, active):
        """Камера отвалилась сама — отжимаем кнопку, чтобы она не врала."""
        if not active and self.win.portrait.cam.isChecked():
            self.win.portrait.camera_failed()
            self.watcher.stop()
            if self.tray:
                self.act_cam.setChecked(False)
                self.act_cam.setText("Включить камеру")

    def on_frame_skipped(self):
        self.saved_frames += 1

    def on_described(self, text):
        """Показываем в ленте, что именно она видит — иначе камера как чёрный ящик."""
        self.win.chat.append("system", f"вижу: {text}")

    def _compose(self, text):
        """
        Собирает реплику вместе со зрением: свежий кадр, если сцена изменилась
        или человек прямо просит посмотреть; иначе — словесное описание, оно
        на порядок дешевле кадра.
        """
        if not self.eyes.is_on():
            # Молчание тут читается как «всё по-прежнему»: в истории разговора
            # остались кадры, и модель уверенно отвечает, что видит человека,
            # хотя камера уже выключена. Состояние надо называть каждый раз.
            return f"{BLIND}\n{text}", None
        shot = self.eyes.snapshot(force=bool(LOOK_NOW.search(text)))
        if shot:
            return text, shot
        note = self.watcher.note()
        return (f"{note}\n{text}" if note else text), None

    # ── просьбы и подтверждение ───────────────────────────────────────────
    def _run(self, text):
        """Отдать просьбу активному мозгу как есть."""
        self.speaker.shutup()
        body, shot = self._compose(text)
        self.brain.send(body, shot, display=text)

    def _offer(self, text, spoken):
        """Опасную просьбу придерживаем и переспрашиваем, остальные — сразу."""
        if self.backend != "claude":
            # переспрашивать не о чем: у мозгов по API нет инструментов,
            # удалять и коммитить им нечем — любая просьба остаётся разговором
            self._run(text)
        elif self.confirmer.needs(text, spoken):
            self.confirmer.hold(text)
        else:
            self._run(text)

    def _answer(self, text):
        """Пока висит «выполнять?», сказанное идёт сюда, а не агенту."""
        if self.confirmer.reply(text) != "other":
            return
        # ни да, ни нет — значит человек услышал, что его не так поняли, и
        # сказал иначе. Прежнюю просьбу снимаем молча и разбираем новую.
        self.confirmer.clear()
        self.win.chat.confirm.hide()
        self._offer(text, spoken=True)

    def on_confirm_ask(self, phrase):
        self.win.chat.confirm.ask(self.confirmer.pending())
        self.win.chat.append("system", phrase)
        self.win.portrait.set_emotion("puzzled")
        self.speaker.shutup()
        self.speaker.say(phrase)

    def on_confirm_accepted(self, text):
        self.win.chat.confirm.hide()
        self._run(text)

    def on_confirm_dropped(self, text, quiet):
        self.win.chat.confirm.hide()
        self.win.portrait.set_emotion("relief")
        if quiet:
            self.win.chat.append("system", f"не дождалась ответа, отменила: {text}")
        else:
            self.win.chat.append("system", f"отменила: {text}")
            self.speaker.say("Отменила.")

    def on_heard(self, text):
        if self.confirmer.pending():
            self._answer(text)
            return
        if self.busy:
            self.win.chat.append("system", f"(пропустила: {text})")
            return
        self._offer(text, spoken=True)

    def on_input(self, text):
        if self.confirmer.pending():
            self._answer(text)
            return
        self._offer(text, spoken=False)

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
