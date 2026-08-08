# -*- coding: utf-8 -*-
"""
Подбор порога «сцена изменилась»: где проходит граница между шумом и движением.

Кадр к реплике прикладывается, только если картинка поменялась — иначе платим
за одну и ту же по 400 токенов. Порог нельзя брать с потолка: возьмёшь мало —
камера сама себя переоткрывает по дыханию экспозиции; возьмёшь много — модель
уверенно рассказывает про позавчерашнюю сцену.

Меряем на живой камере (шум покоя) и на синтетике (заведомое движение).

    python tools/vision_dedup_probe.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

from core.vision import Eyes  # noqa: E402


class FakeCfg(dict):
    pass


def diff(a, b):
    return float(np.abs(a - b).mean())


def main():
    import cv2

    cfg = FakeCfg({"camera_backend": "DSHOW", "camera_index": 0,
                   "camera_width": 640, "camera_height": 480})
    cap = cv2.VideoCapture(int(cfg["camera_index"]), cv2.CAP_DSHOW)
    if not cap.isOpened():
        print("камера не открылась")
        sys.exit(1)
    for _ in range(5):
        cap.read()

    ok, first = cap.read()
    if not ok:
        print("кадра нет")
        sys.exit(1)
    base = Eyes._signature(first)

    print("── неподвижная сцена: насколько кадр «уплывает» сам по себе ──")
    worst = 0.0
    for i in range(8):
        time.sleep(1.0)
        ok, frame = cap.read()
        if not ok:
            continue
        d = diff(Eyes._signature(frame), base)
        worst = max(worst, d)
        print(f"   +{i + 1} с: {d:.2f}")
    cap.release()
    print(f"   худшее за 8 с покоя: {worst:.2f}")

    print("\n── синтетика: заведомое движение на том же кадре ──")
    h, w = first.shape[:2]
    print(f"   тот же кадр                        {diff(base, base):.2f}")

    shifted = np.roll(first, int(w * 0.06), axis=1)
    print(f"   сдвиг на 6% ширины                 "
          f"{diff(Eyes._signature(shifted), base):.2f}")

    for part, label in ((0.10, "десятую часть"), (0.25, "четверть")):
        blocked = first.copy()
        side = int((h * w * part) ** 0.5)
        blocked[h // 2 - side // 2:h // 2 + side // 2,
                w // 2 - side // 2:w // 2 + side // 2] = 0
        print(f"   закрыли {label} кадра{' ' * (12 - len(label))}"
              f"{diff(Eyes._signature(blocked), base):.2f}")

    print("\nПорог берут между худшим покоем и самым слабым движением.")


if __name__ == "__main__":
    main()
