# -*- coding: utf-8 -*-
"""
Доходят ли до помощницы отчёты из чужой папки.

Проверяем через настоящий мост (core/agent.py), а не голым вызовом claude:
смысл в том, что флаг --add-dir реально собирается в командную строку и
доезжает до процесса.

Закрытость секретов проверяется отдельно — tools/deny_test.py. Здесь этого
намеренно нет: спрашивать помощницу «покажи ключ» и радоваться ответу «не буду,
это секрет» бессмысленно, она при этом файл всё равно читает. Проверять надо
запрет, а не вежливость.

    python tools/crypto_access_test.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QCoreApplication, QTimer       # noqa: E402

from app import load_config                               # noqa: E402
from core.agent import AgentRunner                        # noqa: E402

problems = []


def ask(cfg, question, seconds=180):
    """Один ход с новым процессом claude. Возвращает (ответ, инструменты)."""
    qt = QCoreApplication.instance() or QCoreApplication([])
    agent = AgentRunner(cfg)
    seen = {"said": [], "tools": [], "errors": []}
    agent.log.connect(lambda kind, text: (
        seen["tools"].append(text) if kind == "tool" else
        seen["errors"].append(text) if kind == "error" else None))
    agent.speak.connect(seen["said"].append)

    state = {"sent": False}

    def tick():
        if not state["sent"] and agent.alive():
            state["sent"] = True
            agent.send(question)
        elif state["sent"] and not agent._busy and seen["said"]:
            agent.stop()
            QTimer.singleShot(200, qt.quit)

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(200)
    agent.start()
    QTimer.singleShot(seconds * 1000, qt.quit)
    qt.exec()
    timer.stop()
    agent.stop()
    return " ".join(seen["said"]), seen["tools"], seen["errors"]


def check(name, fn):
    try:
        detail = fn()
        print(f"  [ок]  {name} — {detail}")
    except Exception as exc:
        print(f"  [!!]  {name} — {exc}")
        problems.append(name)


def main():
    cfg = load_config()
    dirs = cfg.get("extra_dirs") or []
    print(f"рабочая папка : {cfg.get('workdir')}")
    print(f"чужие папки   : {dirs or 'нет'}")
    print(f"запрещено     : {len(cfg.get('denied_tools') or [])} правил\n")

    if not dirs:
        print("extra_dirs пуст — проверять нечего")
        return 1

    target = dirs[0]

    def c_read():
        answer, tools, errors = ask(
            cfg, f"Прочитай файл {target}/positions.json и скажи одной фразой, "
                 f"сколько в нём позиций. Если не можешь — так и скажи.")
        print(f"        инструменты: {tools}")
        print(f"        ответ: {answer}")
        if errors:
            raise RuntimeError(errors[-1])
        if not tools:
            raise RuntimeError("файл даже не пробовала открыть")
        if "нет доступа" in answer.lower() or "не могу" in answer.lower():
            raise RuntimeError("всё ещё говорит, что доступа нет")
        return "отчёт прочитан"

    print("── доступ к крипте ──")
    check("отчёт positions.json", c_read)

    print()
    print("секреты проверяются отдельно: python tools/deny_test.py")
    if problems:
        print("не прошло:", ", ".join(problems))
        return 1
    print("всё хорошо")
    return 0


if __name__ == "__main__":
    sys.exit(main())
