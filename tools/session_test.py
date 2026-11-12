# -*- coding: utf-8 -*-
"""
Выбор сессии: только из сайдбара и только через подтверждение.

Проверяем две вещи, из-за которых всё и переделано. Первая: оболочка
больше не заводит сессий сама — заведённая отсюда в сайдбаре приложения
не появляется, и открыть её потом нечем. Вторая: список открывается одним
касанием, и без спроса уехать из своего разговора в чужой нельзя.

    python tools/session_test.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from PySide6.QtWidgets import QApplication            # noqa: E402

from ui.window import Chat                            # noqa: E402

app = QApplication([])
chat = Chat()

picked = []
made = []
chat.session_picked.connect(picked.append)
chat.session_new.connect(lambda: made.append(1))

items = [{"id": "aaa", "label": "Чат с пикселем · PixelClaude", "title": "Чат с пикселем"},
         {"id": "bbb", "label": "Крипта · veles_bybit", "title": "Крипта"}]

bad = []


def check(name, ok, note=""):
    print(("  ok  " if ok else "ПРОВАЛ") + f"  {name}" + (f"  — {note}" if note else ""))
    if not ok:
        bad.append(name)


# 1. список для Клода: новой сессии в нём нет
chat.set_sessions(items, "aaa", allow_new=False)
labels = [chat.picker.itemText(i) for i in range(chat.picker.count())]
check("нет пункта «новая сессия»", not any("новая" in t for t in labels), str(labels))
check("кнопка + спрятана", not chat.btn_new.isVisibleTo(chat))
check("стоит домашняя", chat.picker.currentData() == "aaa")

# 2. выбор другой сессии сам по себе ничего не переключает
chat.picker.setCurrentIndex(1)
chat._picked(1)
check("сразу не переключает", not picked)
check("спрашивает подтверждение", chat.switch.isVisibleTo(chat))
check("выпадашка вернулась на домашнюю", chat.picker.currentData() == "aaa")

# 3. «нет» — остаёмся на месте
chat.switch.rejected.emit()
check("отказ: не переключились", not picked)
check("отказ: полоса спряталась", not chat.switch.isVisibleTo(chat))
check("отказ: сессия прежняя", chat.picker.currentData() == "aaa")

# 4. «да» — переключились
chat._picked(1)
chat.switch.confirmed.emit()
check("согласие: сигнал ушёл", picked == ["bbb"], str(picked))

# 5. своя сессия в списке — повторный выбор не дёргает процесс
chat.set_sessions(items, "bbb", allow_new=False)
chat._picked(chat.picker.findData("bbb"))
check("та же сессия — ничего не делает", picked == ["bbb"] and not chat.switch.isVisibleTo(chat))

# 6. у Meta AI новый разговор заводить можно
chat.set_sessions(items, None, allow_new=True)
labels = [chat.picker.itemText(i) for i in range(chat.picker.count())]
check("у других мозгов «новая» на месте", any("новая" in t for t in labels))

print("\nПРОВАЛОВ: " + str(len(bad)) if bad else "\nвсё сошлось")
sys.exit(1 if bad else 0)
