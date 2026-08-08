# -*- coding: utf-8 -*-
"""
Какой способ доступа к камере работает на этой машине и за сколько.

Встроенные камеры ноутбуков (особенно ov9734 и родня) часто не видны старому
DirectShow и открываются только через Media Foundation — а неудачная попытка
не падает с ошибкой, а молча висит. Поэтому пробуем по очереди, с таймаутом.

    python tools/camera_probe.py
"""
import os
import sys
import time
import multiprocessing as mp


def _try(backend_name, backend_id, index, q):
    import cv2

    t0 = time.perf_counter()
    cap = cv2.VideoCapture(index, backend_id) if backend_id else cv2.VideoCapture(index)
    if not cap.isOpened():
        cap.release()
        q.put((backend_name, index, None, "не открылась"))
        return
    opened = time.perf_counter() - t0

    frame = None
    for _ in range(4):
        ok, f = cap.read()
        if ok and f is not None:
            frame = f
    cap.release()
    if frame is None:
        q.put((backend_name, index, None, f"открылась за {opened:.1f} с, но кадра нет"))
        return
    q.put((backend_name, index, (frame.shape[1], frame.shape[0]),
           f"открытие {opened:.2f} с | кадр {time.perf_counter() - t0:.2f} с"))


def probe(timeout=25):
    import cv2

    backends = [("MSMF", cv2.CAP_MSMF), ("DSHOW", cv2.CAP_DSHOW), ("ANY", 0)]
    good = []
    for name, bid in backends:
        for index in (0, 1):
            q = mp.Queue()
            p = mp.Process(target=_try, args=(name, bid, index, q), daemon=True)
            p.start()
            p.join(timeout)
            if p.is_alive():
                p.terminate()
                print(f"{name:>6} #{index}: ЗАВИС (>{timeout} с) — не годится")
                continue
            try:
                _, _, size, note = q.get_nowait()
            except Exception:
                print(f"{name:>6} #{index}: тишина")
                continue
            if size:
                print(f"{name:>6} #{index}: OK {size[0]}x{size[1]} | {note}")
                good.append((name, bid, index))
            else:
                print(f"{name:>6} #{index}: {note}")
        if good:
            break            # первый сработавший способ и берём
    return good


if __name__ == "__main__":
    mp.freeze_support()
    found = probe()
    print("\nрабочие способы:", found or "ни одного")
