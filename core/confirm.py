# -*- coding: utf-8 -*-
"""
Переспросить, прежде чем ломать.

Оболочка работает в настоящем проекте с permission_mode acceptEdits: сказанное
вслух доходит до файлов без единого клика. И между человеком и этими файлами
стоит распознавание речи, которое иногда слышит своё. «Удали ветку» и «у Даши
ветка» — для whisper это соседние гипотезы.

Поэтому опасные просьбы не уходят сразу: помощница повторяет их вслух и ждёт
«да». Безобидные — «покажи файл», «что там в логе» — идут как шли, иначе
подтверждение из страховки превращается в тик.

Опасным считаем то, что меняет мир необратимо или наружу: удаление, перезапись,
коммит, пуш, деплой, отправка, установка, откат. Чтение и поиск — нет.

По умолчанию переспрашиваем только на голос (confirm_mode: voice): набранный
руками текст человек видел глазами, а расслышать его неправильно невозможно.
"""
import re

from PySide6.QtCore import QObject, QTimer, Signal

# Необратимое или уходящее наружу. Корни, а не слова целиком: русский язык
# приделает к ним что угодно — «удали», «удалить», «удаляй», «поудаляй».
RISKY = re.compile(
    r"(удал|сотри|стере|снес|уничтож|очист|обнул|"
    r"перепиш|перезапиш|затри|замен[ияь]|переимен|"
    # «коммит» само по себе не опасно: «покажи последние коммиты» — это чтение.
    # Опасен глагол, а не существительное.
    r"закоммит|коммитн|(сделай|создай|запили)\s+коммит|запуш|пушни|"
    r"отправ|напиши в чат|напиши ему|напиши ей|разошли|"
    r"залей|заливай|выложи|выклад|опубликуй|публику|"
    r"деплой|выкат|раскат|"
    r"установ|доустанов|переустанов|снеси пакет|"
    r"сбрось|сброс|откат|"
    r"\brm\b|\bdrop\b|--force|--hard|force push|git push|git commit|"
    r"git reset|deploy|publish)", re.I)

# Ответы на «выполнять?». Отказ проверяем первым: «нет, лучше покажи» начинается
# с «нет», и это именно отказ, а не новая просьба.
#
# \w* после корней обязательно. Без него «отмен\b» не ловит «отмена»: между «н»
# и «а» никакой границы слова нет, и отказ уходит в «переформулировал».
NO = re.compile(r"^\s*(нет\w*|не\s+(надо|нужно|делай|стоит)|отмен\w*|стоп|стой|"
                r"погод\w*|подожд\w*|отставить|отбой|забудь|cancel|no)\b", re.I)
YES = re.compile(r"^\s*(да|ага|угу|давай\w*|валяй|поехали|выполняй|делай|жми|"
                 r"подтвержд\w*|верно|точно|именно|ок|окей|yes|ok)\b", re.I)


class Confirmer(QObject):
    """Придерживает опасную просьбу, пока человек не скажет «да»."""

    ask = Signal(str)              # проговорить и показать: подтверди вот это
    accepted = Signal(str)         # подтверждено — вот что выполнять
    dropped = Signal(str, bool)    # отменено: (что отменили, молча ли)

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self._pending = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._expired)

    # ── решение ───────────────────────────────────────────────────────────
    def needs(self, text, spoken):
        """Надо ли переспрашивать про эту просьбу."""
        mode = self.cfg.get("confirm_mode", "voice")
        if mode == "off":
            return False
        if mode == "voice" and not spoken:
            return False
        if mode == "all":
            return True
        return bool(RISKY.search(text or ""))

    def pending(self):
        return self._pending

    # ── ход подтверждения ─────────────────────────────────────────────────
    def hold(self, text):
        """Придержать просьбу и попросить подтверждения."""
        self._pending = text
        seconds = float(self.cfg.get("confirm_timeout_sec", 30))
        if seconds > 0:
            self._timer.start(int(seconds * 1000))
        self.ask.emit(self.phrase(text))

    @staticmethod
    def phrase(text, limit=110):
        """Что произнести вслух. Длинную просьбу режем — её слушать невозможно."""
        body = " ".join((text or "").split())
        if len(body) > limit:
            body = body[:limit - 1].rstrip() + "…"
        return f"Поняла так: {body}. Выполнять?"

    def reply(self, text):
        """
        Разобрать ответ человека, пока висит вопрос.

        Возвращает 'yes', 'no' или 'other'. 'other' — это не отказ и не
        согласие, а новая формулировка: человек услышал, что его не так поняли,
        и переспросил иначе. Такую фразу берём вместо прежней.
        """
        if not self._pending:
            return "other"
        if NO.match(text or ""):
            self.reject()
            return "no"
        if YES.match(text or ""):
            self.accept()
            return "yes"
        return "other"

    def accept(self):
        self._timer.stop()
        text, self._pending = self._pending, None
        if text:
            self.accepted.emit(text)

    def reject(self, quiet=False):
        self._timer.stop()
        text, self._pending = self._pending, None
        if text:
            self.dropped.emit(text, quiet)

    def clear(self):
        """Снять вопрос, ничего не объявляя: человек его уже переформулировал."""
        self._timer.stop()
        self._pending = None

    def _expired(self):
        """
        Молчание — это «нет», но вслух об этом не сообщаем. Человек за полминуты
        занялся другим, и голос из угла, объявляющий про забытую просьбу, пугает
        сильнее, чем молча несделанное дело. В ленте запись остаётся.
        """
        self.reject(quiet=True)
