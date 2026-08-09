# -*- coding: utf-8 -*-
"""
Переключение мозга: claude ↔ Meta AI.

Проверяем без сети и без ключа то, что можно: кнопка переключает, сигналы
второго мозга приходят в ту же ленту, разбор потока Meta AI понимает обе формы
ответа, а без ключа оболочка честно говорит об этом и не притворяется рабочей.

    python tools/brain_test.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.llama import LlamaRunner, api_key           # noqa: E402

problems = []


def check(name, fn):
    try:
        detail = fn()
        print(f"  [ок]  {name}" + (f" — {detail}" if detail else ""))
    except Exception as exc:
        print(f"  [!!]  {name} — {exc}")
        problems.append(name)


class FakeResp:
    """Ответ сервера, каким его отдаёт requests в потоковом режиме."""

    def __init__(self, lines):
        self._lines = lines

    def iter_lines(self, decode_unicode=True):
        return iter(self._lines)


def c_stream_openai():
    """Форма openai: choices[].delta.content."""
    lines = ["data: " + json.dumps({"choices": [{"delta": {"content": p}}]})
             for p in ("[done] ", "Привет. ", "Как дела?")]
    lines.append("data: [DONE]")
    got = "".join(LlamaRunner._stream(FakeResp(lines)))
    if got != "[done] Привет. Как дела?":
        raise RuntimeError(f"собралось не то: {got!r}")
    return got


def c_stream_native():
    """Родная форма llama: event.delta.text."""
    lines = [json.dumps({"event": {"delta": {"text": p}}})
             for p in ("[think] ", "Думаю.")]
    got = "".join(LlamaRunner._stream(FakeResp(lines)))
    if got != "[think] Думаю.":
        raise RuntimeError(f"собралось не то: {got!r}")
    return got


def c_flush():
    """Говорит предложениями, эмоцию отдаёт один раз, дважды не повторяет."""
    said_out, emo_out = [], []
    r = LlamaRunner({})
    r.speak.connect(said_out.append)
    r.emotion.connect(emo_out.append)

    buf, said, emo = "", "", None
    for piece in ("[done] ", "Первая фраза. ", "Вторая фраза."):
        buf += piece
        emo, said = r._flush(buf, said, emo, final=False)
    r._flush(buf, said, emo, final=True)

    whole = " ".join(said_out)
    if whole != "Первая фраза. Вторая фраза.":
        raise RuntimeError(f"озвучено не то: {whole!r}")
    if emo_out != ["done"]:
        raise RuntimeError(f"эмоции: {emo_out}")
    return f"{len(said_out)} куска, эмоция {emo_out[0]}"


def c_no_key():
    """Без ключа не притворяется рабочей."""
    r = LlamaRunner({"llama_api_key": ""})
    os.environ.pop("LLAMA_API_KEY", None)
    os.environ.pop("META_API_KEY", None)
    if r.available():
        raise RuntimeError("считает себя готовой без ключа")
    errors = []
    r.log.connect(lambda kind, text: errors.append(text) if kind == "error" else None)
    r.send("привет")
    if not errors:
        raise RuntimeError("промолчала вместо объяснения")
    return errors[-1][:60] + "…"


def c_same_interface():
    """Оба мозга должны отвечать на одни и те же вызовы."""
    from core.agent import AgentRunner
    need = ("available", "alive", "start", "stop", "restart",
            "send", "set_session", "reset")
    missing = [n for n in need if not hasattr(LlamaRunner, n)]
    if missing:
        raise RuntimeError("у Meta AI нет: " + ", ".join(missing))
    signals = ("log", "speak", "emotion", "busy", "session", "ready")
    lost = [s for s in signals
            if not (hasattr(AgentRunner, s) and hasattr(LlamaRunner, s))]
    if lost:
        raise RuntimeError("разошлись сигналы: " + ", ".join(lost))
    return f"{len(need)} методов, {len(signals)} сигналов совпадают"


def c_ui():
    """Кнопка на месте и меняет подпись."""
    from PySide6.QtWidgets import QApplication
    from ui.window import Chat
    app = QApplication.instance() or QApplication([])       # noqa: F841
    chat = Chat()
    seen = []
    chat.brain_switched.connect(seen.append)
    if chat.brain.text() != "МОЗГ: CLAUDE":
        raise RuntimeError(f"подпись при старте: {chat.brain.text()}")
    chat.brain.setChecked(True)
    if chat.brain.text() != "МОЗГ: META AI" or seen != ["llama"]:
        raise RuntimeError(f"после нажатия: {chat.brain.text()}, сигналы {seen}")
    chat.set_brain("claude")
    if chat.brain.isChecked() or seen != ["llama"]:
        raise RuntimeError("откат кнопки поднял лишний сигнал")
    return "переключается и откатывается без лишних сигналов"


print("── переключение мозга ──")
check("поток, форма openai", c_stream_openai)
check("поток, форма llama", c_stream_native)
check("озвучка предложениями", c_flush)
check("без ключа не врёт", c_no_key)
check("интерфейс совпадает с claude", c_same_interface)
check("кнопка в ленте", c_ui)

print()
if problems:
    print("не прошло:", ", ".join(problems))
    sys.exit(1)
print(f"всё хорошо. Ключ сейчас: {'есть' if api_key({}) else 'НЕТ — впиши в config.json'}")
