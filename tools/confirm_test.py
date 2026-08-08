# -*- coding: utf-8 -*-
"""
Проверка эвристики «эту просьбу надо переспросить».

Ошибиться тут можно в обе стороны, и обе неприятны. Пропустишь опасное —
подтверждение бесполезно. Переспросишь про «покажи файл» — человек через день
поставит confirm_mode: off, и подтверждения не станет вовсе.

    python tools/confirm_test.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.confirm import Confirmer  # noqa: E402

# (фраза, надо ли переспрашивать)
CASES = [
    # необратимое или уходящее наружу — переспрашиваем
    ("удали файл config.json", True),
    ("сотри лишние строки в agent.py", True),
    ("снеси папку bench_tmp", True),
    ("закоммить и запушь", True),
    ("сделай git push в main", True),
    ("задеплой на бегет", True),
    ("отправь отчёт в телеграм", True),
    ("перепиши README целиком", True),
    ("переименуй core в engine", True),
    ("установи pandas", True),
    ("откати последний коммит", True),
    ("сбрось изменения в vision.py", True),
    ("очисти crash.log", True),
    ("git reset --hard", True),
    ("выложи это на гитхаб", True),
    ("опубликуй пост", True),

    # чтение, поиск, разговор — идут как шли
    ("посмотри что в agent.py", False),
    ("покажи последние коммиты", False),
    ("что там в логе", False),
    ("найди где используется snapshot", False),
    ("объясни как работает дедуп кадров", False),
    ("сколько строк в vision.py", False),
    ("запусти тесты", False),
    ("проверь статус гита", False),
    ("как дела", False),
    ("что ты видишь", False),
    ("сравни два подхода и скажи какой лучше", False),
    ("прочитай config и скажи какой там порог", False),
]

ANSWERS = [
    ("да", "yes"), ("ага", "yes"), ("давай", "yes"), ("выполняй", "yes"),
    ("нет", "no"), ("отмена", "no"), ("стоп", "no"), ("не надо", "no"),
    ("погоди", "no"),
    ("нет, лучше покажи файл", "no"),        # начинается с отказа — это отказ
    ("удали вообще всё", "other"),           # новая просьба вместо прежней
]


def main():
    conf = Confirmer({"confirm_mode": "voice"})

    print("── что считаем опасным ──")
    bad = 0
    for text, want in CASES:
        got = conf.needs(text, spoken=True)
        if got != want:
            bad += 1
            beda = "ЛОЖНАЯ ТРЕВОГА" if got else "ПРОПУСТИЛА"
            print(f"  {beda:<15} «{text}»")
    print(f"  {len(CASES) - bad} из {len(CASES)} верно")

    print("\n── разбор ответа на «выполнять?» ──")
    wrong = 0
    for text, want in ANSWERS:
        conf.hold("удали файл")
        got = conf.reply(text)
        conf.clear()
        if got != want:
            wrong += 1
            print(f"  «{text}» → {got}, ожидали {want}")
    print(f"  {len(ANSWERS) - wrong} из {len(ANSWERS)} верно")

    print("\n── режимы ──")
    for mode, spoken, want in (("off", True, False), ("all", False, True),
                               ("voice", False, False), ("voice", True, True)):
        conf.cfg["confirm_mode"] = mode
        got = conf.needs("удали всё", spoken=spoken)
        mark = "ок" if got == want else "НЕ ТАК"
        print(f"  {mode:<6} голосом={str(spoken):<5} → переспросить={got}  {mark}")

    return 1 if (bad or wrong) else 0


if __name__ == "__main__":
    sys.exit(main())
