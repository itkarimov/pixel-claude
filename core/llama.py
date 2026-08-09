# -*- coding: utf-8 -*-
"""
Второй мозг — Meta AI (Llama) вместо claude.

Снаружи выглядит ровно как AgentRunner: те же сигналы и те же методы, поэтому
всё остальное в оболочке — портрет, озвучка, камера, лента — работает с обоими
и ничего про них не знает.

Чем этот мозг отличается по сути, а не по коду:

  * инструментов нет. Он не читает файлы, не правит код, не ищет в интернете,
    не запускает команды. Это собеседник, а не работник. Поэтому и
    переспрашивание (core/confirm.py) при нём не нужно: удалять ему нечего.
  * контекст ведём сами. У claude есть --resume и файл сессии, здесь историю
    держим в памяти и складываем в llama_sessions/<id>.json.
  * платит Meta, а не подписка Anthropic — ради этого всё и затевалось.

Ключ берём из config (`llama_api_key`) или из окружения LLAMA_API_KEY /
META_API_KEY. Получить: https://llama.developer.meta.com

Запросы шлём через requests: у Llama API интерфейс OpenAI-совместимый, и ради
одного POST со потоком тянуть SDK на десятки мегабайт незачем — на этой машине
меньше гигабайта свободной памяти. Разбираем обе формы ответа: и openai-подобную
(choices[].delta.content), и родную llama (event.delta.text).
"""
import base64
import json
import os
import re
import threading
import time
import uuid

from PySide6.QtCore import QObject, Signal

from core.agent import EMOTIONS, split_emotion

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE = os.path.join(ROOT, "llama_sessions")

# Старый Llama API (api.llama.com) Meta закрыла 6 июля 2026 — на любой ключ он
# отвечает 401 «Authentication Error», и это легко принять за плохой ключ.
# Живой адрес — Meta Model API, модели семейства muse-spark.
DEFAULT_URL = "https://api.meta.ai/v1/chat/completions"
DEFAULT_MODEL = "muse-spark-1.2"

# Приписка своя, а не claude-овская: там половина про инструменты и поиск,
# которых здесь нет, и обещать их — значит врать голосом.
SYSTEM = (
    "Ты — девушка, голосовая помощница в пиксельной оболочке, твой ответ "
    "озвучивается вслух женским голосом. "
    "ПРАВИЛА ОТВЕТА: "
    "0) говори о себе В ЖЕНСКОМ РОДЕ — «посмотрела», «нашла», «готова»; "
    "никогда «посмотрел», «готов», «помощник», «ассистент»; "
    "1) по-русски, одна-две фразы, живой речью; "
    "2) никакого markdown — ни списков, ни заголовков, ни обратных кавычек, "
    "ни звёздочек, ни блоков кода; "
    "3) первым символом — тег эмоции в квадратных скобках; "
    "4) [done] — ответила, [think] — размышляю или нужны уточнения, "
    "[unhappy] — не вышло, [angry] — сломалось всерьёз, [puzzled] — не поняла "
    "просьбу, [relief] — обошлось, [excited] — вышло здорово, [sly] — с хитрецой. "
    "Пример правильного ответа: [done] Готово, поняла тебя. "
    "ЕСЛИ К РЕПЛИКЕ ПРИЛОЖЕН КАДР С КАМЕРЫ: это то, что ты видишь прямо сейчас "
    "своими глазами, а не присланный файл — так про него и говори. "
    "ЧЕГО ТЫ НЕ УМЕЕШЬ: у тебя нет доступа к файлам, коду, командам и интернету. "
    "Если просят что-то сделать на компьютере или найти свежее — честно скажи, "
    "что для этого нужно переключить мозг на Клода кнопкой внизу."
)

REMINDER = ("\n\n[оболочка] Ответь одной-двумя фразами живой речью, без markdown. "
            "О себе — в женском роде. Первым символом — тег эмоции, один из: "
            + " ".join(f"[{e}]" for e in sorted(EMOTIONS)))

HISTORY_TURNS = 20          # сколько прошлых реплик тащим в запрос
MAX_TOKENS = 2000           # с запасом: размышления модели идут из этого же бюджета
SENTENCE_END = re.compile(r"[.!?…](?=\s|$)")


def endpoint(cfg):
    """
    Полный адрес запроса. В документации адрес дают то целиком, то базой
    (`https://api.llama.com/compat/v1`) — принимаем оба, иначе POST уходит
    мимо и сервер отвечает не пойми чем.
    """
    url = (cfg.get("llama_api_url") or DEFAULT_URL).strip().rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    return url


def api_key(cfg):
    return (cfg.get("llama_api_key")
            or os.environ.get("LLAMA_API_KEY")
            or os.environ.get("META_API_KEY") or "").strip()


def list_sessions(limit=40):
    """
    Разговоры с Meta AI, свежие сверху — в том же виде, что и сессии claude,
    чтобы выпадающий список ничего не различал.

    Показываем только то, что оболочка вела сама через API. Переписку с сайта
    meta.ai сюда не подтянуть: это другой продукт, и доступа к его истории у
    api.llama.com нет.
    """
    out = []
    try:
        names = os.listdir(STORE)
    except OSError:
        return out

    for name in names:
        if not name.endswith(".json"):
            continue
        path = os.path.join(STORE, name)
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        title = (data.get("title") or _first_words(data) or "без названия")
        out.append({
            "id": data.get("id") or os.path.splitext(name)[0],
            "path": path,
            "title": title,
            "cwd": "",
            "named": True,
            "brain": "llama",
            "label": f"{title} · Meta AI",
            "mtime": data.get("at") or os.path.getmtime(path),
        })
    out.sort(key=lambda it: it["mtime"], reverse=True)
    return out[:limit]


def _first_words(data):
    """Заголовок по первой реплике человека — своих названий у Meta AI нет."""
    for msg in data.get("messages") or []:
        if msg.get("role") != "user":
            continue
        text = msg.get("content")
        if isinstance(text, list):                  # реплика с картинкой
            text = " ".join(b.get("text", "") for b in text
                            if isinstance(b, dict) and b.get("type") == "text")
        text = " ".join(str(text or "").split())
        if text:
            return text[:60]
    return ""


def tail(path, limit=10):
    """Последние реплики — чтобы лента не открывалась пустой."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    rows = []
    for msg in (data.get("messages") or [])[-limit:]:
        text = msg.get("content")
        if isinstance(text, list):
            text = " ".join(b.get("text", "") for b in text
                            if isinstance(b, dict) and b.get("type") == "text")
        text = " ".join(str(text or "").split())
        if text:
            rows.append((msg.get("role") or "user", text[:220]))
    return rows


class LlamaRunner(QObject):
    """Мозг Meta AI. Сигналы и методы — как у AgentRunner."""

    log = Signal(str, str)      # (вид, текст): user | assistant | tool | system | error
    speak = Signal(str)
    emotion = Signal(str)
    busy = Signal(bool)
    session = Signal(str)
    ready = Signal(bool)

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.session_id = None
        self.workdir = cfg.get("workdir") or None
        self.history = []        # [{"role": ..., "content": ...}]
        self._busy = False
        self._stop = threading.Event()

    # ── публичное API ─────────────────────────────────────────────────────
    def available(self):
        return bool(api_key(self.cfg))

    def alive(self):
        return self.available()

    def start(self):
        if not self.available():
            self.log.emit("error", "нет ключа Meta AI — впиши llama_api_key "
                                   "в config.json или задай LLAMA_API_KEY")
            self.ready.emit(False)
            return
        if not self.session_id:
            self.session_id = uuid.uuid4().hex[:16]
            self.session.emit(self.session_id)
        self.ready.emit(True)

    def stop(self):
        self._stop.set()
        self._set_busy(False)
        self.ready.emit(False)

    def restart(self):
        self._stop.clear()
        self.start()

    def set_session(self, session_id, workdir=None):
        self.session_id = session_id or None
        if workdir and os.path.isdir(workdir):
            self.workdir = workdir
        self.history = self._load(self.session_id)
        self.restart()

    def reset(self):
        self.session_id = None
        self.history = []
        self.restart()
        self.log.emit("system", "новая сессия Meta AI — контекст пустой")

    def send(self, text, image=None, display=None):
        text = (text or "").strip()
        if not text:
            return
        self.log.emit("user", (display or text).strip())
        if not self.available():
            self.log.emit("error", "нет ключа Meta AI — получить можно на "
                                   "llama.developer.meta.com")
            self.emotion.emit("unhappy")
            return
        if self._busy:
            self.log.emit("system", "отвечаю на прошлое, подожди секунду")
            return
        if not self.session_id:
            self.start()
        self._stop.clear()
        self._set_busy(True)
        self.emotion.emit("think")
        threading.Thread(target=self._turn, args=(text, image),
                         daemon=True).start()

    # ── ход ───────────────────────────────────────────────────────────────
    def _turn(self, text, image):
        try:
            self._ask(text, image)
        except Exception as exc:                    # noqa: BLE001 — сеть роняет чем угодно
            self.emotion.emit("unhappy")
            self.log.emit("error", f"Meta AI не ответила: {exc}")
        finally:
            self._set_busy(False)

    def _ask(self, text, image):
        import requests

        body = text + REMINDER
        if image:
            content = [
                {"type": "text", "text": body},
                {"type": "image_url", "image_url": {
                    "url": "data:image/jpeg;base64,"
                           + base64.b64encode(image).decode()}},
            ]
        else:
            content = body

        messages = ([{"role": "system", "content": SYSTEM}]
                    + self.history[-HISTORY_TURNS:]
                    + [{"role": "user", "content": content}])

        url = endpoint(self.cfg)
        # Лимит щедрый не от жадности: muse-spark сначала думает, и размышления
        # идут из того же бюджета. Замер на фразе «как дела?» — 405 токенов
        # размышлений и 16 на сам ответ; при лимите 400 текста не остаётся вовсе,
        # приходит пустой content и молчание в ответ.
        payload = {"model": self.cfg.get("llama_model") or DEFAULT_MODEL,
                   "messages": messages, "stream": True,
                   "max_completion_tokens": int(self.cfg.get("llama_max_tokens")
                                                or MAX_TOKENS)}
        headers = {"Authorization": f"Bearer {api_key(self.cfg)}",
                   "Content-Type": "application/json"}

        resp = requests.post(url, headers=headers, json=payload,
                             stream=True, timeout=(10, 180))
        if resp.status_code >= 400:
            raise RuntimeError(self._explain(resp))
        # У потока событий charset в заголовке не приходит, и requests по
        # умолчанию читает его как latin-1 — русский превращается в кашу.
        resp.encoding = "utf-8"

        said, buf, emo = "", "", None
        for piece in self._stream(resp):
            if self._stop.is_set():
                resp.close()
                return
            buf += piece
            emo, said = self._flush(buf, said, emo, final=False)
        self._flush(buf, said, emo, final=True)

        # в историю кладём текст без служебной приписки — она в каждом ходе своя
        self.history.append({"role": "user", "content": text})
        self.history.append({"role": "assistant", "content": buf.strip()})
        self._save()

    @staticmethod
    def _explain(resp):
        """Понятная причина вместо голого кода ответа."""
        detail = ""
        try:
            data = resp.json()
            detail = (data.get("error") or {}).get("message") or ""
        except ValueError:
            detail = (resp.text or "")[:200]
        if resp.status_code in (401, 403):
            return f"ключ не принят ({resp.status_code}). {detail}".strip()
        if resp.status_code == 429:
            return "лимит запросов Meta AI исчерпан"
        return f"ошибка {resp.status_code}. {detail}".strip()

    @staticmethod
    def _stream(resp):
        """
        Куски текста из потока. Разбираем две формы: openai-совместимую
        (choices[].delta.content) и родную llama (event.delta.text) — какая
        придёт, заранее не известно.
        """
        for raw in resp.iter_lines(decode_unicode=True):
            if not raw:
                continue
            line = raw.strip()
            if line.startswith("data:"):
                line = line[5:].strip()
            if not line or line == "[DONE]":
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue

            for choice in ev.get("choices") or []:
                piece = ((choice.get("delta") or {}).get("content")
                         or (choice.get("message") or {}).get("content") or "")
                if isinstance(piece, str) and piece:
                    yield piece

            inner = ev.get("event") or {}
            piece = (inner.get("delta") or {}).get("text") or ""
            if piece:
                yield piece

    def _flush(self, buf, said, emo, final):
        """
        Отдаёт готовое: эмоцию — как только распознан тег, текст — законченными
        предложениями, чтобы озвучка начиналась раньше конца ответа.
        """
        found, body = split_emotion(buf)
        if found and found != emo:
            emo = found
            self.emotion.emit(emo)

        body = body.strip()
        if not body:
            return emo, said

        if final:
            ready = body
            if not emo:
                self.emotion.emit("neutral")
        else:
            ends = list(SENTENCE_END.finditer(body))
            if not ends:
                return emo, said
            ready = body[:ends[-1].end()].strip()

        if len(ready) <= len(said):
            return emo, said
        fresh = ready[len(said):].strip()
        if not fresh or (not final and len(fresh) < 10):
            return emo, said

        self.log.emit("assistant", fresh)
        self.speak.emit(fresh)
        return emo, ready

    def _set_busy(self, value):
        self._busy = value
        self.busy.emit(value)

    # ── свои сессии ───────────────────────────────────────────────────────
    def title(self):
        return _first_words({"messages": self.history})

    def _path(self, session_id):
        return os.path.join(STORE, f"{session_id}.json")

    def _load(self, session_id):
        if not session_id:
            return []
        try:
            with open(self._path(session_id), encoding="utf-8") as fh:
                data = json.load(fh)
            return data.get("messages") or []
        except (OSError, ValueError):
            return []

    def _save(self):
        if not self.session_id:
            return
        try:
            os.makedirs(STORE, exist_ok=True)
            with open(self._path(self.session_id), "w", encoding="utf-8") as fh:
                json.dump({"id": self.session_id, "at": time.time(),
                           "title": self.title(), "messages": self.history},
                          fh, ensure_ascii=False)
        except OSError:
            pass
