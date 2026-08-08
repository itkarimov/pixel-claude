# -*- coding: utf-8 -*-
"""
Ответ не должен произноситься дважды.

Один и тот же текст приходит от CLI трижды: кусками (stream_event), целым
блоком (assistant) и финальным событием (result). Оболочка обязана озвучить
его ровно один раз — учёт сказанного ведёт AgentRunner._flush.

Прогоняем настоящую последовательность событий мимо процесса claude и считаем,
сколько раз оболочка собралась говорить.

    python tools/double_test.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QCoreApplication          # noqa: E402

from core.agent import AgentRunner, split_emotion    # noqa: E402

REPLY = "[happy] Привет! Вижу тебя через камеру, готова помогать — что будем делать?"


def deltas(text, size=7):
    """Тот же текст, нарезанный на куски, как его отдаёт модель."""
    return [text[i:i + size] for i in range(0, len(text), size)]


def events(text, with_stream=True):
    """Полная последовательность событий одного хода."""
    out = []
    if with_stream:
        out.append({"type": "stream_event", "event": {"type": "message_start"}})
        for piece in deltas(text):
            out.append({"type": "stream_event",
                        "event": {"type": "content_block_delta",
                                  "delta": {"type": "text_delta", "text": piece}}})
        out.append({"type": "stream_event", "event": {"type": "content_block_stop"}})
    out.append({"type": "assistant",
                "message": {"content": [{"type": "text", "text": text}]}})
    out.append({"type": "result", "subtype": "success", "is_error": False,
                "result": text})
    return out


def run(label, text, with_stream):
    """
    Проверяем не число кусков, а отсутствие повторов. Ответ из двух фраз и
    должен звучать двумя кусками — она начинает говорить, не дожидаясь конца.
    Плохо только одно: услышать одно и то же дважды.
    """
    app = QCoreApplication.instance() or QCoreApplication([])   # noqa: F841
    agent = AgentRunner({"workdir": "."})
    said, shown = [], []
    agent.speak.connect(said.append)
    agent.log.connect(lambda kind, msg: shown.append(msg) if kind == "assistant" else None)

    agent._busy = True                       # как будто ход идёт
    for event in events(text, with_stream):
        agent._handle(event)

    expected = " ".join(split_emotion(text)[1].split())
    got = " ".join(" ".join(said).split())
    ok = got == expected and said == shown
    print(f"  [{'ок' if ok else 'ПЛОХО'}] {label}: кусков {len(said)}")
    for n, phrase in enumerate(said, 1):
        print(f"        {n}) {phrase[:70]}")
    if not ok:
        print(f"        ждали: «{expected[:70]}»")
        print(f"        вышло: «{got[:70]}»")
    return ok


def main():
    print("── один ход: каждое слово ответа звучит ровно один раз ──")
    good = True
    good &= run("поток + блок + финал", REPLY, with_stream=True)
    good &= run("без потока (stream_partial: false)", REPLY, with_stream=False)
    good &= run("длинный ответ в две фразы",
                "[done] Посмотрела логи, там пусто. Похоже, бот не запускался.",
                with_stream=True)
    good &= run("короткий ответ", "[done] Готово.", with_stream=True)
    print("\n" + ("всё хорошо" if good else "ЕСТЬ ПОВТОРЫ"))
    sys.exit(0 if good else 1)


if __name__ == "__main__":
    main()
