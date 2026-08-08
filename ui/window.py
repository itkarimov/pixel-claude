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


class Portrait(QWidget):
    """Портрет: спрайт эмоции, моргание, индикатор речи и кнопка микрофона."""

    mic_toggled = Signal(bool)

    def __init__(self, sprites_dir, parent=None):
        super().__init__(parent)
        self.dir = sprites_dir
        self.cache = {}
        self.emotion = "neutral"
        self.talking = False
        self.level = 0.0
        self._phase = 0
        self._breath = 0
        self.canvas = QColor(BG)          # цвет фона берём из самого спрайта

        self.mic = QPushButton("МИК ВЫКЛ", self)
        self.mic.setCheckable(True)
        self.mic.setCursor(Qt.PointingHandCursor)
        self.mic.setFixedSize(132, 40)
        self.mic.toggled.connect(self._on_mic)
        self._style_mic(False)

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

    # ── анимация ──────────────────────────────────────────────────────────
    def _breathe(self):
        self._breath = (self._breath + 1) % 4
        self._breath_timer.start(random.randint(680, 900))
        self.update()

    def _tick(self):
        if self.talking:
            self._phase = (self._phase + 1) % 4
            self.update()

    # ── микрофон ──────────────────────────────────────────────────────────
    def _style_mic(self, on):
        color, border = (GREEN, "#2f7d55") if on else (RED, "#7d2f2f")
        self.mic.setStyleSheet(f"""
            QPushButton {{
                background: {PANEL}; color: {color};
                border: 3px solid {border};
                font-family: Consolas; font-size: 13px; font-weight: bold;
                letter-spacing: 1px;
            }}
            QPushButton:hover {{ background: #292244; }}
        """)

    def _on_mic(self, on):
        self.mic.setText("МИК ВКЛ" if on else "МИК ВЫКЛ")
        self._style_mic(on)
        self.mic_toggled.emit(on)

    def resizeEvent(self, event):
        self.mic.move(self.width() - self.mic.width() - 14,
                      self.height() - self.mic.height() - 14)
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

        if self.mic.isChecked():               # индикатор громкости
            mw = int((w - 20) * min(1.0, self.level))
            p.fillRect(x + 10, y + 10, w - 20, 5, track)
            p.fillRect(x + 10, y + 10, mw, 5, accent)
        p.end()


class Chat(QWidget):
    """Лента сессии: что сказали, что делаем, что ответили."""

    submitted = Signal(str)
    session_picked = Signal(str)      # выбрана сессия из списка
    session_new = Signal()            # начать с чистого листа

    def __init__(self, parent=None):
        super().__init__(parent)
        self.picker = QComboBox()
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

        self.status = QLabel("готова")
        self.status.setStyleSheet(
            f"color:{GREY}; font-family:Consolas; font-size:11px; padding:2px 6px;")

        top = QHBoxLayout()
        top.setSpacing(6)
        top.addWidget(self.picker, 1)
        top.addWidget(self.btn_new, 0)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 4, 10, 10)
        lay.setSpacing(5)
        lay.addLayout(top)
        lay.addWidget(self.view, 1)
        lay.addWidget(self.status, 0, Qt.AlignRight)
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
        self.resize(int(cfg.get("window_width", 460)),
                    int(cfg.get("window_height", 880)))
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
