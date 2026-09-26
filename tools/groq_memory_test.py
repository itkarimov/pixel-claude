# -*- coding: utf-8 -*-
"""
Проверка памяти Groq: помнит ли сказанное в прошлом ходе и поднимается ли
разговор с диска.

Три шага: сказать имя → спросить имя в том же разговоре → завести новый
runner на тот же id и спросить снова. Третий шаг и есть настоящая проверка:
он читает историю из groq_sessions, а не из оперативной памяти.

    python tools/groq_memory_test.py

Тестовый разговор в конце удаляется, чтобы не висел в списке сессий.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer   # noqa: E402

from core import groq                                             # noqa: E402
from core.groq import GroqRunner                                  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = "Ильдар"


def ask(brain, text):
    """Один ход, синхронно: ждём, пока мозг отработает."""
    loop = QEventLoop()
    said = []
    brain.log.connect(lambda kind, t: said.append((kind, t)))
    brain.busy.connect(lambda b: loop.quit() if not b else None)
    started = time.time()
    brain.send(text)
    QTimer.singleShot(120_000, loop.quit)
    loop.exec()
    took = time.time() - started
    for kind, t in said:
        if kind in ("assistant", "tool", "error"):
            print(f"    [{kind}] {t}")
    print(f"    — {took:.1f} с")
    return " ".join(t for kind, t in said if kind == "assistant")


def main():
    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as fh:
        cfg = json.load(fh)
    app = QCoreApplication(sys.argv)

    print("1. говорю имя")
    brain = GroqRunner(cfg)
    brain.start()
    sid = brain.session_id
    ask(brain, f"Запомни: меня зовут {NAME}. Просто подтверди.")

    print("2. спрашиваю в том же разговоре (память в оперативке)")
    live = ask(brain, "Как меня зовут?")

    path = os.path.join(groq.STORE, f"{sid}.json")
    print(f"3. файл разговора: {'есть' if os.path.exists(path) else 'НЕТ'} — {path}")

    print("4. новый мозг, тот же id (память с диска)")
    again = GroqRunner(cfg)
    again.set_session(sid)
    print(f"    реплик поднято: {len(again.history)}")
    disk = ask(again, "Как меня зовут?")

    print("5. виден ли разговор в списке сессий")
    rows = [it for it in groq.list_sessions(limit=60) if it["id"] == sid]
    print(f"    {rows[0]['label'] if rows else 'НЕ ВИДЕН'}")

    print("\nитог:")
    print("  память в разговоре:", "да" if NAME.lower() in live.lower() else "НЕТ")
    print("  память с диска:   ", "да" if NAME.lower() in disk.lower() else "НЕТ")

    try:                                   # за собой прибираем
        os.remove(path)
        print("  тестовый разговор удалён")
    except OSError:
        pass
    app.quit()


if __name__ == "__main__":
    main()
