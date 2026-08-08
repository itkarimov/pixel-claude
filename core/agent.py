# -*- coding: utf-8 -*-
"""
Мост к claude CLI: один долгоживущий процесс на всю сессию.

Почему не процесс на реплику (как было сначала): замеры показали 15.6 с на ход,
из которых 13 — накладные. Плюс загрузка MCP-серверов раздувала первый ход
до 35 с. Отсюда долгоживущий процесс (`--input-format stream-json`) и
отключённые MCP-серверы.

Но и после этого ход занимал 5.9 с до первой фразы. Причина нашлась замером
(tools/bench_modes.py): хук claude-mem `UserPromptSubmit` запускает node на
КАЖДУЮ реплику и стоит 1.5–2.5 с, а вместе с `Stop` и `PostToolUse` — 3.7 с
на ход. Долгоживущий процесс тут не спасает: хук на то и хук, что срабатывает
каждый раз.

    как было                      5.90 с до первой фразы, старт 9.24 с
    --setting-sources project,local  2.19 с,                старт 2.46 с
    --safe-mode                      2.15 с,                старт 2.33 с

По умолчанию берём первый вариант: он снимает пользовательские настройки
(там и лежат хуки), но оставляет CLAUDE.md и правила проекта — оболочке они
нужны, она же работает в проекте. Флаг --bare, который выглядит ещё быстрее,
не годится: он вообще не логинится и падает за 0.05 с с «Not logged in», что
в замере легко принять за ускорение.
"""
import base64
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
    "Пример правильного ответа: [done] Готово, поправила три файла в core. "
    "ЕСЛИ К РЕПЛИКЕ ПРИЛОЖЕН КАДР С КАМЕРЫ: это то, что ты видишь прямо сейчас "
    "своими глазами, а не присланный файл — так про него и говори. Не описывай "
    "кадр без просьбы, просто учитывай, что видишь. "
    "ЕСЛИ В РЕПЛИКЕ ЕСТЬ СТРОКА «[вижу] …»: это то же самое зрение, только "
    "словами — что сейчас в камере. Кадра при этом нет, но видишь ты именно это. "
    "Если строка помечена «(было N с назад)» — картинка могла устареть, и когда "
    "человек спрашивает про сиюминутное, честнее сказать, что нужен свежий взгляд. "
    "У ТЕБЯ ЕСТЬ ПОИСК В ИНТЕРНЕТЕ (WebSearch) И ЧТЕНИЕ СТРАНИЦ (WebFetch): "
    "если спрашивают про новости, курсы, погоду, цены или что-то свежее — ищи, "
    "а не отвечай «не знаю» и не пересказывай память. Назови источник коротко. "
    "ЕСЛИ ЗАДАЧА ТРЕБУЕТ РАБОТЫ инструментами: СНАЧАЛА скажи одну короткую фразу "
    "с тегом — «[think] Сейчас посмотрю» — и только потом берись за инструменты, "
    "иначе человек сидит в тишине и думает, что оболочка зависла. В конце — "
    "вторая фраза с результатом."
)

EMOTIONS = {"neutral", "smile", "happy", "laugh", "surprised", "puzzled",
            "think", "sad", "crying", "angry", "unhappy", "shy", "love",
            "tired", "sly", "excited", "relief", "victory", "done"}

REMINDER = ("\n\n[оболочка] Ответь одной-двумя фразами живой речью, без markdown, "
            "без обратных кавычек и списков — текст пойдёт в озвучку. "
            "О себе — в женском роде: посмотрела, сделала, готова. "
            "Если собираешься работать инструментами — сперва короткая фраза "
            "вслух, потом работа. "
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
        self._buf = ""           # текст сообщения, копится по кускам
        self._said = ""          # что уже озвучено из текущего сообщения
        self._last_msg = ""      # последнее озвученное сообщение целиком
        self._emo = None         # какая эмоция уже показана в этом сообщении
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

    def send(self, text, image=None, display=None):
        """
        image — кадр с камеры в JPEG, если зрение включено.
        display — что показать в ленте вместо text: в реплику подмешивается
        служебное («[вижу] …»), а человеку в ленте нужны только его слова.
        """
        text = (text or "").strip()
        if not text:
            return
        if not self.claude:
            self.log.emit("error", "claude CLI не найден в PATH")
            return
        self.log.emit("user", (display or text).strip())
        if self._busy:
            # текст озвучивается раньше, чем ход официально закрыт: человек уже
            # услышал ответ и говорит дальше. Не отфутболиваем — придерживаем.
            self._pending = (text, image)
            self.log.emit("system", "приняла, отвечу следом")
            return
        self._write(text, image)

    def _write(self, text, image=None):
        if not self.alive():                      # процесс умер или ещё не поднят
            self.log.emit("system", "поднимаю claude…")
            self._spawn(wait=True)
            if not self.alive():
                return

        self._buf = self._said = self._last_msg = ""
        self._emo = None
        self._set_busy(True)
        self.emotion.emit("think")

        body = text + REMINDER
        if image:
            # кадр уходит блоком прямо в потоке. Через файл на диске тоже
            # работает, но модель тратит лишний ход на чтение — замер показал
            # 8.7 с против 7.0 с, полторы секунды на ровном месте.
            content = [
                {"type": "image",
                 "source": {"type": "base64", "media_type": "image/jpeg",
                            "data": base64.b64encode(image).decode()}},
                {"type": "text", "text": body},
            ]
        else:
            content = body

        payload = json.dumps({"type": "user",
                              "message": {"role": "user", "content": content}},
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

        # хуки — главный пожиратель времени, 3.7 с на ход (tools/bench_modes.py)
        mode = self.cfg.get("hooks_mode", "skip_user")
        if mode == "skip_user":
            # снимаем пользовательские настройки (в них хуки), оставляем проект
            cmd += ["--setting-sources", "project,local"]
        elif mode == "safe":
            # совсем без надстроек: ни хуков, ни плагинов, ни CLAUDE.md
            cmd += ["--safe-mode"]
        # mode == "keep" — ничего не трогаем, хуки работают как обычно

        if self.cfg.get("stream_partial", True):
            # текст по кускам: можно начать говорить с первой готовой фразы,
            # не дожидаясь, пока модель допишет остальное
            cmd += ["--include-partial-messages"]
        if self.cfg.get("disable_mcp", True) and mode != "safe":
            # первый ход с подключёнными MCP-серверами занимал 35 с вместо 12
            cmd += ["--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']
        # Разрешения приходили из пользовательских настроек, а мы их снимаем
        # ради хуков — значит, нужное разрешаем флагом. Поиск в вебе оболочке
        # нужен: спросить голосом «что там с курсом» и услышать «не могу» —
        # это не помощница.
        allowed = self.cfg.get("allowed_tools")
        if allowed:
            cmd += ["--allowedTools"] + list(allowed)
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

        if etype == "stream_event":
            self._partial(event.get("event") or {})
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
                    self._buf = block["text"]
                    self._flush(final=True)
            return

        if etype == "result":
            self._set_busy(False)
            if self._pending:                     # придержанная реплика — вперёд
                queued, self._pending = self._pending, None
                text, image = queued if isinstance(queued, tuple) else (queued, None)
                threading.Timer(0.1, lambda: self._write(text, image)).start()
                return
            if event.get("is_error"):
                self.emotion.emit("unhappy")
                self.log.emit("error", _short(event.get("result"), 200))
                return
            text = (event.get("result") or "").strip()
            _, clean = split_emotion(text)
            if clean and clean != self._last_msg:    # финал отличается — договорим
                # в буфер кладём сырой текст: тег эмоции ещё пригодится
                self._buf, self._said, self._emo = text, "", None
                self._flush(final=True)

    # ── потоковый разбор ──────────────────────────────────────────────────
    def _partial(self, ev):
        """Куски текста по мере поступления — чтобы начать говорить раньше."""
        kind = ev.get("type")
        if kind == "message_start":
            self._buf, self._said, self._emo = "", "", None
            return
        if kind == "content_block_delta":
            piece = (ev.get("delta") or {}).get("text") or ""
            if piece:
                self._buf += piece
                self._flush()

    def _flush(self, final=False):
        """
        Отдаёт наружу то, что уже готово: эмоцию — как только распознан тег,
        текст — законченными предложениями. Дважды одно и то же не произносим:
        ведём учёт сказанного в self._said.

        Один и тот же ответ приходит от CLI трижды — кусками (stream_event),
        целым блоком (assistant) и финальным событием (result). Отсюда правило:
        _last_msg проставляется на КАЖДОМ финальном заходе, даже если говорить
        уже нечего. Иначе получалось так: фраза целиком отзвучала ещё на потоке,
        обработчик блока вышел раньше как «нового нет» и отметку не поставил,
        а result увидел пустую отметку и произнёс всё заново. Ответ из двух
        фраз при этом звучал трижды: каждая фраза отдельно и потом всё вместе.
        """
        emo, body = split_emotion(self._buf)
        if emo and emo != self._emo:
            self._emo = emo
            self.emotion.emit(emo)

        body = body.strip()
        if not body:
            return

        if final:
            ready = body
            self._last_msg = ready          # отметку ставим до всех выходов
            if not self._emo:
                self.emotion.emit("neutral")
        else:
            ends = list(re.finditer(r"[.!?…](?=\s|$)", body))
            if not ends:
                return
            ready = body[:ends[-1].end()].strip()

        if len(ready) <= len(self._said):
            return
        fresh = ready[len(self._said):].strip()
        if not fresh or (not final and len(fresh) < 10):
            return

        self._said = ready
        self.log.emit("assistant", fresh)
        self.speak.emit(fresh)
