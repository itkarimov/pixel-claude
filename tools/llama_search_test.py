# -*- coding: utf-8 -*-
"""
Ищет ли Meta AI в интернете.

Спрашиваем заведомо свежее — то, чего в памяти модели быть не может. Смотрим,
появился ли в ленте вызов поиска: без него ответ выглядит уверенно, но берётся
из головы, и отличить это на слух невозможно.

Заодно печатаем список моделей, доступных ключу: в config легко вписать модель,
которой у вас нет, и тогда всё падает с невнятной ошибкой.

    python tools/llama_search_test.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QCoreApplication, QTimer                # noqa: E402

from app import load_config                                        # noqa: E402
from core.llama import LlamaRunner, api_key, endpoint              # noqa: E402

QUESTION = "Какая сейчас погода в Бишкеке? Ответь одной фразой."


def models(cfg):
    import requests

    base = endpoint(cfg).rsplit("/responses", 1)[0]
    r = requests.get(base + "/models", timeout=30,
                     headers={"Authorization": f"Bearer {api_key(cfg)}"})
    if r.status_code >= 400:
        return None, f"{r.status_code} {(r.text or '')[:120]}"
    return [m.get("id") for m in (r.json().get("data") or [])], None


def main():
    cfg = load_config()
    if not api_key(cfg):
        print("нет ключа — смотри config.json")
        return 1

    print(f"адрес: {endpoint(cfg)}")
    chosen = cfg.get("llama_model")
    have, err = models(cfg)
    if err:
        print(f"список моделей не отдался: {err}")
    else:
        print(f"доступно ключу: {', '.join(have)}")
        if chosen not in have:
            print(f"\n[ПЛОХО] в config стоит «{chosen}», а такой модели у ключа нет.")
            print("        Поставь одну из перечисленных выше.")
            return 1
    print(f"модель: {chosen}")
    print(f"поиск разрешён: {cfg.get('llama_web_search', True)}\n")

    qt = QCoreApplication(sys.argv)
    brain = LlamaRunner(cfg, remember=False)
    seen = {"said": [], "tools": [], "errors": [], "t0": time.perf_counter()}

    brain.speak.connect(seen["said"].append)
    brain.log.connect(lambda kind, text: (
        seen["tools"].append(text) if kind == "tool" else
        seen["errors"].append(text) if kind == "error" else None))
    brain.busy.connect(lambda on: None if on else QTimer.singleShot(300, qt.quit))

    print(f"спрашиваю: {QUESTION}\n")
    brain.start()
    brain.send(QUESTION)
    QTimer.singleShot(120_000, qt.quit)
    qt.exec()

    took = time.perf_counter() - seen["t0"]
    if seen["errors"]:
        print("ошибка:", seen["errors"][-1])
        return 1

    print("поиск:", seen["tools"] or "НЕ ИСКАЛА")
    print("ответ:", " ".join(seen["said"]) or "(пусто)")
    print(f"ход {took:.1f} с")

    ok = bool(seen["tools"]) and bool(seen["said"])
    print("\n[ок] поиском воспользовалась" if ok else
          "\n[ПЛОХО] в интернет не полезла — проверь llama_web_search и адрес")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
