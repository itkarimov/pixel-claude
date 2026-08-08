# -*- coding: utf-8 -*-
"""
Проверка фонового описателя: берёт живой кадр и показывает, что он про него
скажет и за сколько.

    python tools/describer_test.py
    python tools/describer_test.py --model sonnet

Секунды тут не критичны — описатель работает в фоне, — но если он молчит или
отвечает пустой строкой, зрение в режиме describe окажется слепым, а понять
это по самой оболочке трудно.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.vision import Eyes      # noqa: E402
from core.watcher import Describer  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None, help="чем описывать (по умолчанию из config)")
    ap.add_argument("--shots", type=int, default=1, help="сколько кадров подряд")
    args = ap.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    import json
    with open(os.path.join(root, "config.json"), encoding="utf-8") as fh:
        cfg = json.load(fh)
    if args.model:
        cfg["watch_model"] = args.model

    eyes = Eyes(cfg)
    if not eyes.available():
        print("нет opencv")
        return 1
    eyes.start()
    print("грею камеру…")
    for _ in range(30):
        time.sleep(0.2)
        if eyes.snapshot(who="probe", force=True):
            break

    describer = Describer(cfg, eyes)
    if not describer.claude:
        print("claude CLI не найден")
        eyes.stop()
        return 1
    print(f"модель описателя: {cfg.get('watch_model', 'haiku')}")

    for n in range(args.shots):
        jpeg = eyes.snapshot(who="probe", force=True)
        if not jpeg:
            print("кадра нет")
            break
        t0 = time.perf_counter()
        describer._describe(jpeg)
        dt = time.perf_counter() - t0
        size = len(jpeg) / 1024
        print(f"\n  кадр {n + 1}: {size:.0f} КБ, описание за {dt:.2f} с")
        print(f"  → {describer.note() or '(пусто — описатель не ответил)'}")
        if n + 1 < args.shots:
            time.sleep(2)

    describer.stop()

    eyes.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
