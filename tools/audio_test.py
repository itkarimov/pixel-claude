# -*- coding: utf-8 -*-
"""Проверка звука: устройства ввода, синтез речи, кэш модели распознавания."""
import io
import os
import sys
import wave

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import load_config                      # noqa: E402
from core.tts import Speaker                     # noqa: E402

cfg = load_config()

print("── микрофоны ──")
try:
    import sounddevice as sd
    default_in = sd.default.device[0]
    for i, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0:
            mark = "→" if i == default_in else " "
            print(f" {mark} [{i}] {dev['name']}")
except Exception as exc:
    print(" ошибка:", exc)

print("\n── синтез речи ──")
sp = Speaker(cfg)
wav = sp._synth_edge("Проверка связи. Я тебя слышу.")
if wav and os.path.exists(wav):
    with wave.open(wav, "rb") as w:
        print(f" edge-tts ок: {w.getnframes() / w.getframerate():.1f} с, "
              f"{w.getframerate()} Гц, {os.path.getsize(wav)} байт")
    if "--play" in sys.argv:
        sp._play(wav)
        print(" воспроизведено")
else:
    print(" edge-tts не сработал — будет запасной SAPI")

print("\n── модель распознавания ──")
cache = os.path.expanduser("~/.cache/huggingface/hub")
found = [d for d in os.listdir(cache) if "whisper" in d.lower()] \
    if os.path.isdir(cache) else []
print(" кэш:", ", ".join(found) if found else "пусто — скачается при первом включении")
