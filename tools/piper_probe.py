# -*- coding: utf-8 -*-
"""
Стоит ли переезжать на локальный синтез.

edge-tts звучит лучше, но каждое соединение с сервисом Microsoft — новое
рукопожатие TLS, и пауза между репликами его обнуляет: 1.7 с до первого звука
вместо обещанных 0.6 с. Piper считает на процессоре, сети не требует вовсе.

Меряем: загрузку модели (разово), время до первого куска звука и до конца.

    python tools/piper_probe.py           # молча
    python tools/piper_probe.py --play    # со звуком, чтобы оценить голос
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL = os.path.join(ROOT, "assets", "voices", "ru_RU-irina-medium.onnx")
PHRASES = ("Готово, посмотрела три файла и поправила два.",
           "Сейчас гляну, что там в проекте.",
           "Не вышло — в конфиге не хватает токена.")


def main():
    play = "--play" in sys.argv
    if not os.path.exists(MODEL):
        print(f"нет модели: {MODEL}")
        sys.exit(1)

    from piper import PiperVoice

    t0 = time.perf_counter()
    voice = PiperVoice.load(MODEL)
    print(f"загрузка модели: {time.perf_counter() - t0:.2f} с (разово, при старте)")

    rate = voice.config.sample_rate
    print(f"частота: {rate} Гц")

    for n, text in enumerate(PHRASES):
        t0 = time.perf_counter()
        first = None
        pcm = []
        for chunk in voice.synthesize(text):
            if first is None:
                first = time.perf_counter() - t0
            pcm.append(np.frombuffer(chunk.audio_int16_bytes, dtype=np.int16))
        total = time.perf_counter() - t0
        audio = np.concatenate(pcm) if pcm else np.zeros(0, dtype=np.int16)
        secs = len(audio) / rate
        print(f"  фраза {n + 1}: первый звук {first:.2f} с | синтез {total:.2f} с "
              f"| речи {secs:.1f} с | скорость x{secs / total:.1f}")

        if play:
            import sounddevice as sd
            sd.play(audio, rate)
            sd.wait()


if __name__ == "__main__":
    main()
