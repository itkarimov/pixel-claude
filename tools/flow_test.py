# -*- coding: utf-8 -*-
"""
Куда уходит сказанная фраза: подтверждение, зрение, обычный путь.

Это единственный тест на маршрутизацию реплик в app.py, и проверяет он то, что
глазами не проверишь: опасная просьба не должна доходить до агента, пока человек
не сказал «да», а безобидная не должна ждать ни секунды. Отдельно проверяется
кнопка НЕТ — потому что отменять голосом там, где распознавание уже раз
ослышалось, бессмысленно.

Наружу тест не ходит: процесс claude, синтез и микрофон подменены заглушками,
камера не включается. Окно собирается в offscreen — ничего не мигает.

    python tools/flow_test.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal      # noqa: E402
from PySide6.QtWidgets import QApplication      # noqa: E402

app = QApplication([])

import app as shell_mod                         # noqa: E402

sent = []          # что дошло до агента
spoken = []        # что прозвучало вслух


class Agent(QObject):
    """Вместо процесса claude — список того, что ему отдали."""

    log = Signal(str, str)
    emotion = Signal(str)
    speak = Signal(str)
    busy = Signal(bool)
    session = Signal(str)

    def __init__(self, cfg, parent=None):
        super().__init__()
        self.session_id = None
        self.workdir = cfg.get("workdir")

    def available(self):
        return True

    def send(self, text, image=None, display=None):
        sent.append((text, bool(image), display))

    def start(self):
        pass

    def stop(self):
        pass

    def set_session(self, *a, **k):
        pass

    def reset(self):
        pass


class Speaker(QObject):
    speaking = Signal(bool)
    status = Signal(str)

    def __init__(self, cfg, parent=None):
        super().__init__()

    def say(self, text):
        spoken.append(text)

    def shutup(self):
        pass

    def warmup(self):
        pass


shell_mod.AgentRunner = Agent
shell_mod.Speaker = Speaker

failed = []


def check(what, ok):
    print(f"  [{'ок' if ok else 'НЕ ТАК'}] {what}")
    if not ok:
        failed.append(what)


def main():
    cfg = dict(shell_mod.load_config())
    cfg["confirm_mode"] = "voice"
    cfg["confirm_timeout_sec"] = 0        # таймер тут только мешает
    sh = shell_mod.Shell(cfg)
    bar = sh.win.chat.confirm

    print("── безобидная просьба голосом ──")
    sh.on_heard("покажи последние коммиты")
    check("ушла агенту сразу", bool(sent) and "покажи" in sent[-1][0])
    check("полоса подтверждения не появилась", bar.isHidden())

    print("\n── опасная просьба голосом ──")
    было = len(sent)
    sh.on_heard("удали файл config.json")
    check("агенту не ушла", len(sent) == было)
    check("проговорена вслух",
          bool(spoken) and spoken[-1].startswith("Поняла так:"))
    check("полоса показана", not bar.isHidden())

    print("\n── ответ «да» голосом ──")
    sh.on_heard("да")
    check("ушла агенту", len(sent) == было + 1
          and "удали файл" in sent[-1][0])
    check("полоса убрана", bar.isHidden())

    print("\n── опасная просьба, отказ кнопкой НЕТ ──")
    было = len(sent)
    sh.on_heard("запушь в main")
    bar.no.click()
    check("агенту не ушла", len(sent) == было)
    check("полоса убрана", bar.isHidden())

    print("\n── третий ответ считается новой просьбой ──")
    было = len(sent)
    sh.on_heard("удали ветку")
    sh.on_heard("нет, лучше покажи ветки")      # начинается с «нет» — это отказ
    check("опасная снята", len(sent) == было)
    sh.on_heard("покажи ветки")
    check("следующая безобидная прошла", len(sent) == было + 1)

    print("\n── набранное руками не переспрашивается (confirm_mode: voice) ──")
    было = len(sent)
    sh.on_input("удали файл config.json")
    check("ушло сразу", len(sent) == было + 1)

    print("\n── камера выключена — в реплике нет ни кадра, ни строки «вижу» ──")
    sh.on_input("что там в логе")
    check("кадра нет", sent[-1][1] is False)
    check("строки «[вижу]» нет", "[вижу]" not in sent[-1][0])

    sh.watcher.stop()
    sh.eyes.stop()

    print()
    if failed:
        print(f"провалено: {len(failed)}")
        for what in failed:
            print(f"  · {what}")
        return 1
    print("всё сошлось")
    return 0


if __name__ == "__main__":
    sys.exit(main())
