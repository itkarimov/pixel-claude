# -*- coding: utf-8 -*-
"""Пиксельное окно: сверху портрет с эмоциями, снизу лента сессии."""
import os
import random

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QTextEdit, QVBoxLayout, QWidget)

BG = "#141021"
PANEL = "#1e1930"
EDGE = "#6a5fa0"
EDGE_DARK = "#3b3360"
INK = "#e8e4f0"
GREEN = "#7fe3a2"
CYAN = "#79c6f0"
AMBER = "#f0c463"
RED = "#e86a6a"
GREY = "#8b85a6"

KIND_COLOR = {"user": CYAN, "assistant": INK, "tool": GREEN,
              "system": GREY, "error": RED}
KIND_MARK = {"user": "›", "assistant": "◆", "tool": "·",
             "system": "—", "error": "!"}

EYE_W = 44               # ширина предпросмотра камеры в арт-пикселях
EYE_SCALE_MAX = 2        # крупнее делать незачем: он не должен спорить с лицом


class MicButton(QPushButton):
    """
    Кнопка микрофона с уровнем звука внутри.

    Полоска громкости раньше висела поверх портрета сверху и перечёркивала
    лицо. Место ей внутри самой кнопки: смотришь на «МИК ВКЛ» — там же и
    видишь, что тебя слышно.
    """

    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self._level = 0.0
        self._accent = QColor(GREEN)

    def set_level(self, level):
        level = max(0.0, min(1.0, float(level)))
        # перерисовываем только на заметное изменение: кадры идут 33 раза в
        # секунду, и дёргать окно на каждый мелкий скачок незачем
        if abs(level - self._level) > 0.02:
            self._level = level
            self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.isChecked():
            return
        p = QPainter(self)
        inner = self.rect().adjusted(5, 0, -5, -5)
        bar = 5
        y = inner.bottom() - bar
        p.fillRect(inner.left(), y, inner.width(), bar, QColor(EDGE_DARK))
        width = int(inner.width() * self._level)
        if width:
            p.fillRect(inner.left(), y, width, bar, self._accent)
        p.end()


class Portrait(QWidget):
    """Портрет: спрайт эмоции, индикатор речи, кнопки микрофона и камеры."""

    mic_toggled = Signal(bool)
    cam_toggled = Signal(bool)

    def __init__(self, sprites_dir, parent=None):
        super().__init__(parent)
        self.dir = sprites_dir
        self.cache = {}
        self.emotion = "neutral"
        self.talking = False
        self.level = 0.0
        self._phase = 0
        self._breath = 0
        self._eye = None                  # кадр с камеры для предпросмотра
        self.canvas = QColor(BG)          # цвет фона берём из самого спрайта

        self.mic = MicButton("МИК ВЫКЛ", self)
        self.mic.setCheckable(True)
        self.mic.setCursor(Qt.PointingHandCursor)
        self.mic.setFixedSize(132, 40)
        self.mic.toggled.connect(self._on_mic)
        self._style_mic(False)

        self.cam = QPushButton("ГЛАЗА ВЫКЛ", self)
        self.cam.setCheckable(True)
        self.cam.setCursor(Qt.PointingHandCursor)
        self.cam.setFixedSize(132, 40)
        self.cam.toggled.connect(self._on_cam)
        self._style_btn(self.cam, False)

        # дыхание: спрайт качается на один арт-пиксель, чтобы портрет жил
        self._breath_timer = QTimer(self)
        self._breath_timer.timeout.connect(self._breathe)
        self._breath_timer.start(760)

        self._anim = QTimer(self)
        self._anim.timeout.connect(self._tick)
        self._anim.start(140)

    # ── спрайты ───────────────────────────────────────────────────────────
    def _pix(self, name):
        if name not in self.cache:
            path = os.path.join(self.dir, f"{name}.png")
            if not os.path.exists(path):
                return None
            pix = QPixmap(path)
            self.cache[name] = pix
            if not pix.isNull():
                self.canvas = QColor(pix.toImage().pixel(1, 1))
        return self.cache[name]

    def set_emotion(self, emotion):
        if self._pix(emotion) is not None:
            self.emotion = emotion
            self.update()

    def set_talking(self, talking):
        self.talking = talking
        self.update()

    def set_level(self, level):
        self.level = level
        self.mic.set_level(level)

    # ── анимация ──────────────────────────────────────────────────────────
    def _breathe(self):
        self._breath = (self._breath + 1) % 4
        self._breath_timer.start(random.randint(680, 900))
        self.update()

    def _tick(self):
        if self.talking:
            self._phase = (self._phase + 1) % 4
            self.update()

    # ── микрофон и камера ─────────────────────────────────────────────────
    @staticmethod
    def _style_btn(button, on):
        color, border = (GREEN, "#2f7d55") if on else (RED, "#7d2f2f")
        button.setStyleSheet(f"""
            QPushButton {{
                background: {PANEL}; color: {color};
                border: 3px solid {border};
                font-family: Consolas; font-size: 13px; font-weight: bold;
                letter-spacing: 1px;
            }}
            QPushButton:hover {{ background: #292244; }}
        """)

    def _style_mic(self, on):
        self._style_btn(self.mic, on)

    def _on_mic(self, on):
        self.mic.setText("МИК ВКЛ" if on else "МИК ВЫКЛ")
        self._style_btn(self.mic, on)
        self.mic_toggled.emit(on)

    def _on_cam(self, on):
        self.cam.setText("ГЛАЗА ВКЛ" if on else "ГЛАЗА ВЫКЛ")
        self._style_btn(self.cam, on)
        if not on:
            self._eye = None
        self.cam_toggled.emit(on)

    def set_camera_frame(self, jpeg):
        """Кадр для предпросмотра. Мельчим — иначе видео выбивается из стиля."""
        pix = QPixmap()
        if not pix.loadFromData(jpeg, "JPG") or pix.isNull():
            return
        self._eye = pix.scaledToWidth(EYE_W, Qt.SmoothTransformation)
        self.update()

    def camera_failed(self):
        """Камера не открылась — кнопку отжимаем, чтобы не врала."""
        self.cam.blockSignals(True)
        self.cam.setChecked(False)
        self.cam.setText("ГЛАЗА ВЫКЛ")
        self._style_btn(self.cam, False)
        self.cam.blockSignals(False)
        self._eye = None
        self.update()

    def resizeEvent(self, event):
        y = self.height() - self.mic.height() - 14
        self.mic.move(self.width() - self.mic.width() - 14, y)
        self.cam.move(self.width() - self.mic.width() - self.cam.width() - 22, y)
        super().resizeEvent(event)

    # ── отрисовка ─────────────────────────────────────────────────────────
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform, False)

        pix = self._pix(self.emotion) or self._pix("neutral")
        if pix is None:
            p.fillRect(self.rect(), QColor(BG))
            p.end()
            return
        p.fillRect(self.rect(), self.canvas)

        # целочисленный масштаб — иначе пиксели «плывут»
        scale = max(1, int(min(self.width() / pix.width(),
                               self.height() / pix.height())))
        w, h = pix.width() * scale, pix.height() * scale
        x, y = (self.width() - w) // 2, (self.height() - h) // 2
        breath = (0, 1, 0, -1)[self._breath] * scale
        p.drawPixmap(x, y + breath, w, h, pix)   # качается только сам спрайт

        # индикаторы на светлом фоне зелёным не читаются — берём тёмные чернила
        light = self.canvas.lightness() > 128
        accent = QColor("#2b3550") if light else QColor(GREEN)
        track = QColor("#8f9aa8") if light else QColor(EDGE_DARK)

        p.setPen(track)
        p.drawRect(x, y, w - 1, h - 1)

        if self.talking:                       # столбики «говорю»
            bx, by, bw = x + 10, y + h - 14, 5
            for i in range(4):
                hh = 4 + ((self._phase + i) % 4) * 4
                p.fillRect(bx + i * (bw + 3), by - hh, bw, hh, accent)

        # Уровень громкости рисует сама кнопка микрофона: поверх портрета он
        # перечёркивал лицо. Цвет ей подбирать не надо — фон кнопки всегда
        # тёмный, а этот accent подстроен под светлый фон спрайта и в кнопке
        # сливается со всем подряд.

        if self._eye is not None:              # то, что она сейчас видит
            k = max(1, min(EYE_SCALE_MAX, scale))   # крупные пиксели — так в стиле
            ew, eh = self._eye.width() * k, self._eye.height() * k
            # левый нижний угол, вровень с кнопками: лицо должно оставаться
            # открытым, а глазок — рядом с кнопкой, которая его включает
            ex = x + 10
            # над кнопками, а не вровень с ними: в узком окне кнопка ГЛАЗА
            # доезжает до левого края и предпросмотр оказывался под ней
            ey = max(y + 10, min(self.mic.y() - eh - 8, y + h - eh - 10))
            p.fillRect(ex - 3, ey - 3, ew + 6, eh + 6, QColor(PANEL))
            p.drawPixmap(ex, ey, ew, eh, self._eye)
            p.setPen(accent)
            p.drawRect(ex - 3, ey - 3, ew + 5, eh + 5)
        p.end()


class ConfirmBar(QWidget):
    """
    Полоса «выполнять?»: показывает придержанную просьбу и две кнопки.

    Голосом ответить можно и без неё, но кнопки нужны: если распознавание уже
    один раз ошиблось, повторять «нет» в микрофон — так себе способ отменить.
    """

    confirmed = Signal()
    rejected = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.text = QLabel("")
        self.text.setWordWrap(True)
        self.text.setStyleSheet(
            f"color:{AMBER}; font-family:Consolas; font-size:12px;")

        self.yes = QPushButton("ДА")
        self.no = QPushButton("НЕТ")
        for button, color, border in ((self.yes, GREEN, "#2f7d55"),
                                      (self.no, RED, "#7d2f2f")):
            button.setFixedSize(52, 28)
            button.setCursor(Qt.PointingHandCursor)
            button.setStyleSheet(f"""
                QPushButton {{
                    background:{PANEL}; color:{color}; border:3px solid {border};
                    font-family:Consolas; font-size:12px; font-weight:bold;
                }}
                QPushButton:hover {{ background:#292244; }}
            """)
        self.yes.clicked.connect(self.confirmed.emit)
        self.no.clicked.connect(self.rejected.emit)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(6)
        lay.addWidget(self.text, 1)
        lay.addWidget(self.yes, 0)
        lay.addWidget(self.no, 0)
        self.setStyleSheet(f"background:{PANEL}; border:3px solid {AMBER};")

    def ask(self, text):
        self.text.setText(f"выполнять? {text}")
        self.show()


class SessionPicker(QComboBox):
    """
    Список сессий, который перечитывается в момент открытия.

    Нужно ради удаления: сессию убирают в приложении Claude, и она должна
    пропасть здесь сразу, а не после перезапуска оболочки. Сборка списка стоит
    ~30 мс (реестр приложения — это десятки маленьких json), так что делаем её
    синхронно, до показа выпадашки, и никакого мигания не видно.
    """

    about_to_open = Signal()

    def showPopup(self):
        self.about_to_open.emit()
        super().showPopup()


class Chat(QWidget):
    """Лента сессии: что сказали, что делаем, что ответили."""

    submitted = Signal(str)
    session_picked = Signal(str)      # выбрана сессия из списка
    session_new = Signal()            # начать с чистого листа
    sessions_needed = Signal()        # открывают список — перечитай реестр
    brain_switched = Signal(str)      # "claude" | "llama"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.picker = SessionPicker()
        self.picker.about_to_open.connect(self.sessions_needed.emit)
        self.picker.setCursor(Qt.PointingHandCursor)
        self.picker.setStyleSheet(f"""
            QComboBox {{
                background:{PANEL}; color:{AMBER}; border:3px solid {EDGE_DARK};
                padding:3px 6px; font-family:Consolas; font-size:12px;
            }}
            QComboBox::drop-down {{ border:0; width:18px; }}
            QComboBox QAbstractItemView {{
                background:#0d0a16; color:{INK};
                selection-background-color:{EDGE_DARK};
                border:2px solid {EDGE_DARK};
                font-family:Consolas; font-size:12px;
            }}
        """)
        self.picker.activated.connect(self._picked)

        self.btn_new = QPushButton("+")
        self.btn_new.setFixedSize(32, 28)
        self.btn_new.setCursor(Qt.PointingHandCursor)
        self.btn_new.setToolTip("новая сессия")
        self.btn_new.setStyleSheet(f"""
            QPushButton {{
                background:{PANEL}; color:{GREEN}; border:3px solid {EDGE_DARK};
                font-family:Consolas; font-size:15px; font-weight:bold;
            }}
            QPushButton:hover {{ background:#292244; }}
        """)
        self.btn_new.clicked.connect(self.session_new.emit)

        self.view = QTextEdit(readOnly=True)
        self.view.setStyleSheet(
            f"QTextEdit {{ background:#0d0a16; color:{INK}; "
            f"border:3px solid {EDGE_DARK}; padding:6px; }}")
        self.view.setFont(QFont("Consolas", 10))

        self.input = QLineEdit()
        self.input.setPlaceholderText("…или напиши руками и Enter")
        self.input.setStyleSheet(
            f"QLineEdit {{ background:{PANEL}; color:{INK}; "
            f"border:3px solid {EDGE_DARK}; padding:6px; "
            f"font-family:Consolas; font-size:12px; }}")
        self.input.returnPressed.connect(self._submit)

        # Переключатель мозга. Он же и подпись: видно, кто сейчас отвечает —
        # иначе по ответам не отличишь, а платят они из разных карманов.
        self.brain = QPushButton("МОЗГ: CLAUDE")
        self.brain.setCheckable(True)
        self.brain.setFixedHeight(26)
        self.brain.setCursor(Qt.PointingHandCursor)
        self.brain.setToolTip("переключить на Meta AI (Llama)")
        self._style_brain(False)
        self.brain.toggled.connect(self._brain_switched)

        self.status = QLabel("готова")
        self.status.setStyleSheet(
            f"color:{GREY}; font-family:Consolas; font-size:11px; padding:2px 6px;")

        self.confirm = ConfirmBar()
        self.confirm.hide()

        top = QHBoxLayout()
        top.setSpacing(6)
        top.addWidget(self.picker, 1)
        top.addWidget(self.btn_new, 0)

        bottom = QHBoxLayout()
        bottom.setSpacing(6)
        bottom.addWidget(self.brain, 0)
        bottom.addStretch(1)
        bottom.addWidget(self.status, 0)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 4, 10, 10)
        lay.setSpacing(5)
        lay.addLayout(top)
        lay.addWidget(self.view, 1)
        lay.addLayout(bottom)
        lay.addWidget(self.confirm, 0)
        lay.addWidget(self.input, 0)

    # ── список сессий ─────────────────────────────────────────────────────
    def set_sessions(self, items, current_id=None):
        """items — из core.sessions.list_sessions(). Первый пункт всегда новая."""
        self.picker.blockSignals(True)
        self.picker.clear()
        self.picker.addItem("(новая сессия)", None)
        for it in items:
            self.picker.addItem(it["label"], it["id"])
        idx = self.picker.findData(current_id) if current_id else 0
        if current_id and idx < 0:
            # файл сессии ещё не появился на диске — показываем её отдельной строкой
            self.picker.insertItem(1, "· текущая сессия", current_id)
            idx = 1
        self.picker.setCurrentIndex(idx if idx >= 0 else 0)
        self.picker.blockSignals(False)

    # ── переключатель мозга ───────────────────────────────────────────────
    def _style_brain(self, llama):
        color, border = ("#8ab4ff", "#2f4d7d") if llama else (GREEN, "#2f7d55")
        self.brain.setStyleSheet(f"""
            QPushButton {{
                background:{PANEL}; color:{color}; border:3px solid {border};
                padding:2px 10px; font-family:Consolas; font-size:11px;
                font-weight:bold; letter-spacing:1px;
            }}
            QPushButton:hover {{ background:#292244; }}
        """)

    def _brain_switched(self, llama):
        self.brain.setText("МОЗГ: META AI" if llama else "МОЗГ: CLAUDE")
        self._style_brain(llama)
        self.brain_switched.emit("llama" if llama else "claude")

    def set_brain(self, name):
        """Поставить переключатель без сигнала — при запуске и при откате."""
        llama = name == "llama"
        self.brain.blockSignals(True)
        self.brain.setChecked(llama)
        self.brain.setText("МОЗГ: META AI" if llama else "МОЗГ: CLAUDE")
        self._style_brain(llama)
        self.brain.blockSignals(False)

    def _picked(self, index):
        sid = self.picker.itemData(index)
        if sid:
            self.session_picked.emit(sid)
        else:
            self.session_new.emit()

    def _submit(self):
        text = self.input.text().strip()
        if text:
            self.input.clear()
            self.submitted.emit(text)

    def append(self, kind, text):
        color = KIND_COLOR.get(kind, INK)
        mark = KIND_MARK.get(kind, " ")
        safe = (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        self.view.append(
            f'<span style="color:{color}">{mark}&nbsp;{safe}</span>')
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def clear(self):
        self.view.clear()

    def set_status(self, text):
        self.status.setText(text)


class MainWindow(QWidget):
    """Окно прячется в трей, а не закрывается: процесс claude держит сессию."""

    hidden_to_tray = Signal()

    def __init__(self, cfg, sprites_dir):
        super().__init__()
        self.allow_quit = False
        self.setWindowTitle("Pixel Claude")
        self.resize(int(cfg.get("window_width", 552)),
                    int(cfg.get("window_height", 704)))
        self.setMinimumSize(340, 600)
        self.setStyleSheet(f"background:{BG};")

        self.portrait = Portrait(sprites_dir)
        self.chat = Chat()

        share = float(cfg.get("portrait_share", 0.6))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 0)
        lay.setSpacing(8)
        lay.addWidget(self.portrait, int(share * 100))
        lay.addWidget(self.chat, int((1 - share) * 100))

    def closeEvent(self, event):
        if self.allow_quit:
            event.accept()
            return
        event.ignore()
        self.hide()
        self.hidden_to_tray.emit()

    def show_up(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()
