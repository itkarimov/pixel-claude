# -*- coding: utf-8 -*-
"""
Доходит ли до оболочки поиск в вебе.

Проверка не праздная: оболочка запускается без пользовательских настроек
(вместе с ними снимаются хуки, а они стоят 3.7 с на ход), и разрешения на
WebSearch/WebFetch уезжают туда же. Поэтому они выдаются флагом --allowedTools,
и легко не заметить, что флаг перестал доходить: вместо ошибки помощница просто
скажет «не знаю», и это будет выглядеть как особенность модели.

Спрашиваем заведомо свежее — то, чего в памяти модели быть не может.

    python tools/web_test.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QCoreApplication, QTimer   # noqa: E402

from app import load_config                           # noqa: E402
from core.agent import AgentRunner                    # noqa: E402

QUESTION = ("Найди в интернете, какая сейчас погода в Бишкеке. "
            "Обязательно воспользуйся поиском.")


def main():
    cfg = dict(load_config())
    print(f"разрешено флагом: {cfg.get('allowed_tools')}")

    app = QCoreApplication(sys.argv)
    agent = AgentRunner(cfg)
    seen = {"tools": [], "answer": "", "error": "", "t0": None}

    agent.log.connect(lambda kind, text: (
        seen["tools"].append(text) if kind == "tool" else
        seen.__setitem__("error", text) if kind == "error" else None))
    agent.speak.connect(lambda text: seen.__setitem__(
        "answer", (seen["answer"] + " " + text).strip()))

    state = {"phase": 0}

    def step():
        if state["phase"] == 0 and agent.alive():
            state["phase"] = 1
            seen["t0"] = time.perf_counter()
            print(f"\nспрашиваю: {QUESTION}\n")
            agent.send(QUESTION)
        elif state["phase"] == 1 and not agent._busy and seen["answer"]:
            state["phase"] = 2
            report(seen)
            agent.stop()
            QTimer.singleShot(200, app.quit)

    timer = QTimer()
    timer.timeout.connect(step)
    timer.start(150)
    agent.start()
    QTimer.singleShot(150_000, app.quit)
    app.exec()


def report(seen):
    took = time.perf_counter() - seen["t0"]
    print(f"инструменты: {seen['tools'] or 'НИ ОДНОГО'}")
    print(f"ответ ({took:.1f} с): {seen['answer']}")
    if seen["error"]:
        print(f"ошибка: {seen['error']}")

    searched = any("гуглю" in t or "открываю" in t for t in seen["tools"])
    print("\n[ок] поиском воспользовалась" if searched else
          "\n[ПЛОХО] в интернет не полезла — проверь --allowedTools")


if __name__ == "__main__":
    main()
