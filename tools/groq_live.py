# -*- coding: utf-8 -*-
"""
Живая проверка мозга Groq: один вопрос, настоящий ключ, настоящий поток.

    python tools/groq_live.py [вопрос]

Разговор не сохраняется (remember=False) — иначе каждая проверка оставляет
в списке сессий мусор, который потом выгребать руками.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QCoreApplication, QTimer      # noqa: E402

from core.groq import GroqRunner                          # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    question = " ".join(sys.argv[1:]) or "Привет! Как дела?"
    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as fh:
        cfg = json.load(fh)

    app = QCoreApplication(sys.argv)
    brain = GroqRunner(cfg, remember=False)
    print("ключ:", "есть" if brain.available() else "НЕТ")
    print("адрес:", brain._endpoint())
    print("модель:", cfg.get("groq_model"))

    started = time.time()
    brain.log.connect(lambda kind, text: print(f"[{kind}] {text}"))
    brain.emotion.connect(lambda e: print(f"[эмоция] {e}"))
    brain.speak.connect(lambda t: print(f"[голос] {t}"))
    brain.busy.connect(lambda b: None if b else finish(app, started))

    brain.start()
    brain.send(question)
    QTimer.singleShot(90_000, app.quit)      # не висеть, если сеть молчит
    app.exec()


def finish(app, started):
    print(f"\n— {time.time() - started:.1f} с")
    QTimer.singleShot(200, app.quit)


if __name__ == "__main__":
    main()
