# -*- coding: utf-8 -*-
"""Проверка связки с claude CLI без окна: одна реплика, печать событий."""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QCoreApplication, QTimer        # noqa: E402

from app import load_config                                # noqa: E402
from core.agent import AgentRunner                         # noqa: E402


def main():
    prompt = " ".join(sys.argv[1:]) or "Скажи одной фразой, что ты меня слышишь."
    app = QCoreApplication(sys.argv)
    agent = AgentRunner(load_config())
    print("claude:", agent.claude)

    agent.log.connect(lambda k, t: print(f"[{k}] {t}"))
    agent.emotion.connect(lambda e: print(f"[эмоция] {e}"))
    agent.speak.connect(lambda t: print(f"[озвучка] {t}"))
    agent.session.connect(lambda s: print(f"[сессия] {s}"))
    agent.busy.connect(lambda b: None if b else QTimer.singleShot(200, app.quit))

    QTimer.singleShot(0, lambda: agent.send(prompt))
    QTimer.singleShot(180000, app.quit)
    app.exec()


if __name__ == "__main__":
    main()
