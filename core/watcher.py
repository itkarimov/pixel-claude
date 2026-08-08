# -*- coding: utf-8 -*-
"""
Фоновый описатель: смотрит в камеру и переводит увиденное в одну фразу.

Зачем он нужен. Кадр к реплике прикладывается только когда сцена изменилась —
иначе мы платим за одну и ту же картинку каждый раз. Но тогда в спокойные
минуты помощница вообще не знает, что перед ней, хотя камера включена. Описатель
закрывает эту дыру: раз в полминуты, и только если картинка поменялась, он
спрашивает у дешёвой модели «что видно» и держит ответ наготове. Дальше строка
«[вижу] …» подмешивается в реплику — это несколько десятков токенов вместо
четырёхсот за кадр.

Процесс у него свой и отдельный: в основную сессию эти вопросы лезть не должны,
там разговор с человеком.

Почему процесс всё-таки перезапускается. Замер (tools/bench_describer.py),
четыре кадра подряд в один и тот же процесс:

    кадр 1   6.76 с   вход 4350 т
    кадр 2   4.07 с   вход 4961 т   (+611)
    кадр 3   4.03 с   вход 5536 т   (+1186)
    кадр 4   4.55 с   вход 6119 т   (+1769)

    свежий процесс на каждый кадр:  5.8–8.0 с, вход стабильно ~4350 т

Долгоживущий процесс отвечает быстрее — прогревается кеш, — но каждый кадр
навсегда оседает в его контексте и стоит ~590 токенов в каждом следующем ходе,
причём линейно: к четвёртому ходу это уже +1769. За час забытой включённой
камеры набегают десятки тысяч токенов на пустом месте. Поэтому берём и то и
другое: процесс живёт (и разгоняется), но после watch_recycle_every описаний
поднимается заново, и рост обнуляется.
"""
import base64
import json
import os
import shutil
import subprocess
import threading
import time

from PySide6.QtCore import QObject, Signal

SYSTEM = (
    "Ты — глаза голосовой помощницы. Тебе дают кадр с веб-камеры. "
    "Ответь ОДНОЙ короткой фразой по-русски: кто в кадре, чем занят, что вокруг. "
    "Без вступлений, без markdown, без оценок внешности. "
    "Если в кадре никого нет — так и скажи."
)
ASK = "Что видно сейчас? Одной фразой."


class Describer(QObject):
    """Держит свежее словесное описание того, что в камере."""

    described = Signal(str)      # появилось новое описание
    status = Signal(str)

    def __init__(self, cfg, eyes, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.eyes = eyes
        self.claude = shutil.which("claude") or shutil.which("claude.cmd")
        self._proc = None
        self._run = False
        self._text = ""
        self._at = 0.0
        self._shots = 0              # кадров скормлено текущему процессу
        self._lock = threading.Lock()

    # ── управление ────────────────────────────────────────────────────────
    def start(self):
        if self._run or not self.claude:
            return
        if not self.cfg.get("watch_enabled", True):
            return
        self._run = True
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self._run = False
        self.forget()
        self._recycle()

    def forget(self):
        with self._lock:
            self._text, self._at = "", 0.0

    # ── что показать модели ───────────────────────────────────────────────
    def note(self):
        """
        Строка «[вижу] …» для подмешивания в реплику, или None если сказать
        нечего. Давность указываем честно: по устаревшей картинке нельзя
        отвечать на вопрос «что у меня в руке прямо сейчас».
        """
        with self._lock:
            text, at = self._text, self._at
        if not text:
            return None
        age = int(time.time() - at)
        stamp = f" (было {age} с назад)" if age > 12 else ""
        return f"[вижу] {text}{stamp}"

    # ── фоновый цикл ──────────────────────────────────────────────────────
    def _loop(self):
        interval = float(self.cfg.get("watch_interval_sec", 30))
        while self._run:
            try:
                if self.eyes.is_on():
                    # своя подпись кадра: описатель и основная модель смотрят
                    # в камеру независимо друг от друга
                    shot = self.eyes.snapshot(who="watch")
                    if shot:
                        self._describe(shot)
            except Exception as exc:
                self.status.emit(f"описатель споткнулся: {exc}")
            for _ in range(int(max(1, interval))):     # чтобы стоп не ждал минуту
                if not self._run:
                    return
                time.sleep(1)

    def _alive(self):
        return self._proc is not None and self._proc.poll() is None

    def _recycle(self):
        """Уронить процесс, чтобы накопленные кадры ушли вместе с его контекстом."""
        proc, self._proc = self._proc, None
        self._shots = 0
        if proc and proc.poll() is None:
            try:
                proc.stdin.close()
            except OSError:
                pass
            proc.terminate()

    def _spawn(self):
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        cmd = [self.claude, "-p",
               "--input-format", "stream-json",
               "--output-format", "stream-json", "--verbose",
               "--safe-mode",                       # без хуков и плагинов
               "--tools", "",                       # смотреть, а не работать
               "--append-system-prompt", SYSTEM,
               "--model", self.cfg.get("watch_model", "haiku")]
        try:
            self._proc = subprocess.Popen(
                cmd, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                errors="replace", bufsize=1, creationflags=flags)
        except Exception as exc:
            self.status.emit(f"описатель не запустился: {exc}")
            self._proc = None

    def _describe(self, jpeg):
        if self._shots >= int(self.cfg.get("watch_recycle_every", 10)):
            self._recycle()
        if not self._alive():
            self._spawn()
        if not self._alive():
            return
        self._shots += 1

        payload = json.dumps({
            "type": "user",
            "message": {"role": "user", "content": [
                {"type": "image",
                 "source": {"type": "base64", "media_type": "image/jpeg",
                            "data": base64.b64encode(jpeg).decode()}},
                {"type": "text", "text": ASK},
            ]},
        }, ensure_ascii=False)

        try:
            self._proc.stdin.write(payload + "\n")
            self._proc.stdin.flush()
        except OSError:
            self._proc = None
            return

        for raw in self._proc.stdout:
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if event.get("type") != "result":
                continue
            if event.get("is_error"):
                return
            text = " ".join((event.get("result") or "").split()).strip()
            if text:
                with self._lock:
                    self._text, self._at = text[:200], time.time()
                self.described.emit(self._text)
            return
