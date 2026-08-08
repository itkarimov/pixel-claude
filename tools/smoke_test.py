# -*- coding: utf-8 -*-
"""Проверка: окно собирается, спрайты грузятся, снимок в assets/_shot.png."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QTimer                    # noqa: E402
from PySide6.QtWidgets import QApplication           # noqa: E402

from app import Shell, load_config                   # noqa: E402


def main():
    app = QApplication(sys.argv)
    shell = Shell(load_config())
    shell.show()

    shell.win.chat.append("user", "посмотри что в папке finance")
    shell.win.chat.append("tool", "смотрю webhook.php")
    shell.win.chat.append("tool", "запускаю: git status --short")
    shell.win.chat.append("assistant", "Там бот ДДС и вебхук, всё на месте.")
    shell.win.portrait.set_emotion("done")
    shell.win.portrait.set_talking(True)

    def shot():
        path = os.path.join(ROOT, "assets", "_shot.png")
        shell.win.grab().save(path)
        print(path)
        app.quit()

    QTimer.singleShot(1200, shot)
    app.exec()


if __name__ == "__main__":
    main()
