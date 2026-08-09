# -*- coding: utf-8 -*-
"""
Живая проверка второго мозга: один короткий запрос в Meta AI.

Тратит токены Meta, поэтому спрашиваем самое дешёвое и просим короткий ответ.
Смотрим три вещи: принят ли ключ, идёт ли текст потоком (от этого зависит,
начнёт ли она говорить до конца ответа) и ставит ли она тег эмоции.

    python tools/llama_live.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QCoreApplication, QTimer     # noqa: E402

from app import load_config                             # noqa: E402
from core.llama import LlamaRunner, api_key, endpoint   # noqa: E402

QUESTION = "Привет! Скажи одной фразой, как ты себя чувствуешь."


def main():
    cfg = load_config()
    key = api_key(cfg)
    print(f"ключ: {'есть, ' + key[:8] + '…' if key else 'НЕТ'}")
    print(f"модель: {cfg.get('llama_model')}")
    print(f"адрес: {endpoint(cfg)}\n")
    if not key:
        print("[ПЛОХО] без ключа проверять нечего")
        return 1

    app = QCoreApplication(sys.argv)
    brain = LlamaRunner(cfg)
    seen = {"said": [], "emotions": [], "errors": [], "first": None,
            "t0": time.perf_counter()}

    brain.speak.connect(lambda t: (
        seen["said"].append(t),
        seen.__setitem__("first", seen["first"]
                         or time.perf_counter() - seen["t0"])))
    brain.emotion.connect(seen["emotions"].append)
    brain.log.connect(lambda kind, text:
                      seen["errors"].append(text) if kind == "error" else None)
    brain.busy.connect(lambda on: None if on else QTimer.singleShot(300, app.quit))

    print(f"спрашиваю: {QUESTION}\n")
    brain.start()
    brain.send(QUESTION)
    QTimer.singleShot(90_000, app.quit)
    app.exec()

    took = time.perf_counter() - seen["t0"]
    if seen["errors"]:
        print("ошибка:", seen["errors"][-1])
        print("\n[ПЛОХО] Meta AI не ответила")
        return 1

    print("ответ:", " ".join(seen["said"]) or "(пусто)")
    print(f"эмоции: {seen['emotions'] or 'НИ ОДНОЙ'}")
    print(f"первый звук через {seen['first']:.1f} с, весь ход {took:.1f} с"
          if seen["first"] else f"звука не было, ход {took:.1f} с")

    ok = bool(seen["said"]) and bool(seen["emotions"])
    print("\n[ок] второй мозг работает" if ok else
          "\n[ПЛОХО] ответ пустой или без тега эмоции")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
