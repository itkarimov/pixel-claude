# -*- coding: utf-8 -*-
"""
Мост к claude CLI: один долгоживущий процесс на всю сессию.

Почему не процесс на реплику (как было сначала): замеры показали 15.6 с на ход,
из которых 13 — накладные. Хуки 4.8 с, инициализация 3.2 с, завершение 2.1 с,
и лишь 2.6 с собственно работы модели. Плюс загрузка MCP-серверов раздувала
первый ход до 35 с.

Стало: процесс поднимается один раз (`--input-format stream-json`), хуки
отрабатывают тоже один раз, MCP-серверы отключены, а озвучка идёт по приходу
текста, не дожидаясь финального события — это ещё минус три секунды.
"""
import json
import os
import re
import shutil
import subprocess
import threading

from PySide6.QtCore import QObject, Signal

# ВАЖНО: на Windows claude — это .CMD-обёртка, и перенос строки внутри аргумента
# обрывает командную строку, отрезая все последующие флаги. Поэтому системная
# приписка строго однострочная, а реплики уходят в stdin.
SYSTEM_APPEND = (
    "Ты — девушка, голосовая помощница в пиксельной оболочке, твой ответ "
    "озвучивается вслух женским голосом. "
    "ПРАВИЛА ФИНАЛЬНОГО ОТВЕТА: "
    "0) говори о себе В ЖЕНСКОМ РОДЕ — «посмотрела», «нашла», «готова», «я сама», "
    "«помощница»; никогда «посмотрел», «готов», «помощник», «ассистент»; "
    "1) по-русски, одна-две фразы, живой речью; "
    "2) никакого markdown — ни списков, ни заголовков, ни обратных кавычек, "
    "ни звёздочек, ни блоков кода; "
    "3) первым символом — тег эмоции в квадратных скобках; "
    "4) [done] — задача выполнена, [think] — размышляю или нужны уточнения, "
    "[unhappy] — не вышло, [angry] — сломалось всерьёз, [puzzled] — не поняла "
    "просьбу, [relief] — обошлось, [excited] — вышло здорово, [sly] — с хитрецой. "
    "Пример правильного ответа: [done] Готово, поправила три файла в core."
)

EMOTIONS = {"neutral", "smile", "happy", "laugh", "surprised", "puzzled",
            "think", "sad", "crying", "angry", "unhappy", "shy", "love",
            "tired", "sly", "excited", "relief", "victory", "done"}

REMINDER = ("\n\n[оболочка] Ответь одной-двумя фразами живой речью, без markdown, "
            "без обратных кавычек и списков — текст пойдёт в озвучку. "
            "О себе — в женском роде: посмотрела, сделала, готова. "
            "Первым символом — тег эмоции, один из: "
            + " ".join(f"[{e}]" for e in sorted(EMOTIONS)))

TOOL_RU = {
    "Read": "смотрю", "Write": "пишу", "Edit": "правлю", "NotebookEdit": "правлю",
    "Bash": "запускаю", "Grep": "ищу", "Glob": "ищу файлы", "WebFetch": "открываю",
    "WebSearch": "гуглю", "TodoWrite": "обновляю план", "Task": "поднимаю агента",
    "TaskCreate": "ставлю задачу", "SlashCommand": "выполняю команду",
}


def _short(value, limit=58):
    text = str(value).replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def describe_tool(name, params):
    """Человекочитаемая строка «что я сейчас делаю»."""
    verb = TOOL_RU.get(name, name)
    p = params or {}
    for key in ("file_path", "notebook_path", "path"):
        if p.get(key):
            return f"{verb} {os.path.basename(str(p[key]))}"
    if p.get("command"):
        return f"{verb}: {_short(p['command'])}"
    if p.get("pattern"):
        return f"{verb}: {_short(p['pattern'], 34)}"
    for key in ("url", "query", "prompt", "description"):
        if p.get(key):
            return f"{verb}: {_short(p[key], 44)}"
    return verb


def split_emotion(text):
    """Отрезает ведущий тег эмоции: '[done] Готово' → ('done', 'Готово')."""
    m = re.match(r"\s*\[(\w+)\]\s*", text or "")
    if m and m.group(1).lower() in EMOTIONS:
        return m.group(1).lower(), text[m.end():].strip()
    return None, (text or "").strip()


class AgentRunner(QObject):
    """Долгоживущий процесс claude и разбор его потока событий."""

    log = Signal(str, str)      # (вид, текст): user | assistant | tool | system | error
    speak = Signal(str)
    emotion = Signal(str)
    busy = Signal(bool)
    session = Signal(str)
    ready = Signal(bool)        # процесс поднят и готов принимать реплики

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.session_id = None
        self.workdir = cfg.get("workdir") or None
        self.claude = shutil.which("claude") or shutil.which("claude.cmd")
        self._proc = None
        self._lock = threading.Lock()
        self._said = ""          # что уже озвучено в этом ходу
        self._busy = False
        self._pending = None     # реплика, сказанная до конца хода

    # ── публичное API ─────────────────────────────────────────────────────
    def available(self):
        return bool(self.claude)

    def alive(self):
        return self._proc is not None and self._proc.poll() is None

    def start(self):
        """Поднять процесс заранее, чтобы первая реплика не ждала инициализации."""
        if not self.claude or self.alive():
            return
        threading.Thread(target=self._spawn, daemon=True).start()

    def send(self, text):
        text = (text or "").strip()
        if not text:
            return
        if not self.claude:
            self.log.emit("error", "claude CLI не найден в PATH")
            return
        self.log.emit("user", text)
        if self._busy:
            # текст озвучивается раньше, чем ход официально закрыт: человек уже
            # услышал ответ и говорит дальше. Не отфутболиваем — придерживаем.
            self._pending = text
            self.log.emit("system", "приняла, отвечу следом")
            return
        self._write(text)

    def _write(self, text):
        if not self.alive():                      # процесс умер или ещё не поднят
            self.log.emit("system", "поднимаю claude…")
            self._spawn(wait=True)
            if not self.alive():
                return

        self._said = ""
        self._set_busy(True)
        self.emotion.emit("think")
        payload = json.dumps({"type": "user",
                              "message": {"role": "user", "content": text + REMINDER}},
                             ensure_ascii=False)
        try:
            self._proc.stdin.write(payload + "\n")
            self._proc.stdin.flush()
        except OSError as exc:
            self._set_busy(False)
            self.emotion.emit("unhappy")
            self.log.emit("error", f"не смогла передать реплику: {exc}")

    def set_session(self, session_id, workdir=None):
        """Подхватить существующую сессию — процесс перезапускается с --resume."""
        self.session_id = session_id or None
        if workdir and os.path.isdir(workdir):
            self.workdir = workdir
        self.restart()

    def reset(self):
        self.session_id = None
        self.workdir = self.cfg.get("workdir") or None
        self.restart()
        self.log.emit("system", "новая сессия — контекст пустой")

    def restart(self):
        self.stop()
        self.start()

    def stop(self):
        with self._lock:
            proc, self._proc = self._proc, None
        if proc and proc.poll() is None:
            try:
                proc.stdin.close()
            except OSError:
                pass
            proc.terminate()
        self._set_busy(False)
        self.ready.emit(False)

    # ── процесс ───────────────────────────────────────────────────────────
    def _build_cmd(self):
        cmd = [self.claude, "-p",
               "--input-format", "stream-json",
               "--output-format", "stream-json", "--verbose",
               "--append-system-prompt", SYSTEM_APPEND]
        if self.cfg.get("disable_mcp", True):
            # первый ход с подключёнными MCP-серверами занимал 35 с вместо 12
            cmd += ["--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']
        if self.cfg.get("permission_mode"):
            cmd += ["--permission-mode", self.cfg["permission_mode"]]
        if self.cfg.get("model"):
            cmd += ["--model", self.cfg["model"]]
        if self.session_id:
            cmd += ["--resume", self.session_id]
        return cmd

    def _spawn(self, wait=False):
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            proc = subprocess.Popen(
                self._build_cmd(), cwd=self.workdir,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding="utf-8",
                errors="replace", bufsize=1, creationflags=flags)
        except Exception as exc:
            self.log.emit("error", f"не смогла запустить claude: {exc}")
            return
        with self._lock:
            self._proc = proc
        self.ready.emit(True)
        target = self._read
        if wait:
            threading.Thread(target=target, args=(proc,), daemon=True).start()
        else:
            target(proc)

    def _read(self, proc):
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            self._handle(event)

        err = ""
        try:
            err = (proc.stderr.read() or "").strip()
        except (OSError, ValueError):
            pass
        if self._busy:
            self._set_busy(False)
            self.emotion.emit("unhappy")
            self.log.emit("error", _short(err, 200) or "claude завершился молча")
        with self._lock:
            if self._proc is proc:
                self._proc = None
        self.ready.emit(False)

    def _set_busy(self, value):
        self._busy = value
        self.busy.emit(value)

    def _handle(self, event):
        etype = event.get("type")

        if etype == "system" and event.get("subtype") == "init":
            sid = event.get("session_id")
            if sid and sid != self.session_id:
                self.session_id = sid
                self.session.emit(sid)
            return

        if etype == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                kind = block.get("type")
                if kind == "tool_use":
                    self.log.emit("tool", describe_tool(block.get("name"),
                                                        block.get("input")))
                elif kind == "text" and block.get("text", "").strip():
                    # говорим сразу, не дожидаясь result — это экономит ~3 с,
                    # а промежуточное «сейчас посмотрю» голосом звучит естественно
                    emo, clean = split_emotion(block["text"])
                    if not clean:
                        continue
                    self.emotion.emit(emo or "neutral")
                    self.log.emit("assistant", clean)
                    self.speak.emit(clean)
                    self._said = clean
            return

        if etype == "result":
            self._set_busy(False)
            if self._pending:                     # придержанная реплика — вперёд
                queued, self._pending = self._pending, None
                threading.Timer(0.1, lambda: self._write(queued)).start()
                return
            if event.get("is_error"):
                self.emotion.emit("unhappy")
                self.log.emit("error", _short(event.get("result"), 200))
                return
            text = (event.get("result") or "").strip()
            emo, clean = split_emotion(text)
            if clean and clean != self._said:        # финал отличается — договорим
                self.emotion.emit(emo or "neutral")
                self.log.emit("assistant", clean)
                self.speak.emit(clean)
