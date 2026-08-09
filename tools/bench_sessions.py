# -*- coding: utf-8 -*-
"""
Сколько стоит собрать список сессий.

Раньше список строился обходом файлов проектов с дочитыванием хвостов
(до 16 МБ на файл ради строки custom-title) — секунды при запуске. Теперь
источник — реестр приложения, десятки маленьких json. Если сборка укладывается
в десятки миллисекунд, её можно делать прямо при открытии выпадающего списка,
и тогда удалённая в приложении сессия исчезает здесь сразу, без перезапуска.

    python tools/bench_sessions.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import sessions                              # noqa: E402


def measure(name, fn, runs=5):
    fn()                                               # прогрев дискового кеша
    times = []
    for _ in range(runs):
        t = time.perf_counter()
        out = fn()
        times.append((time.perf_counter() - t) * 1000)
    times.sort()
    print(f"{name:34} {times[len(times) // 2]:7.1f} мс   "
          f"(мин {times[0]:.1f}, макс {times[-1]:.1f})   строк: {len(out)}")
    return out


def main():
    print(f"реестр приложения: {sessions.registry_root()}")
    print(f"есть: {os.path.isdir(sessions.registry_root())}\n")

    items = measure("список из реестра", lambda: sessions.list_sessions(limit=60))
    measure("он же, с расписанием",
            lambda: sessions.list_sessions(limit=60, scheduled=True))
    measure("запасной путь (обход файлов)",
            lambda: sessions._from_files(60, None), runs=3)

    print(f"\nчто попадёт в список ({len(items)}):")
    for it in items:
        print(f"  {it['label']}")


if __name__ == "__main__":
    main()
