# -*- coding: utf-8 -*-
"""
Сравнение движков распознавания на одной и той же записи.

Какой быстрее на конкретной машине — предсказанию не поддаётся: faster-whisper
идёт через CTranslate2, sherpa-onnx через onnxruntime, и на одном железе быстрее
первый, на другом второй. Поэтому не рассуждаем, а меряем.

Запись берётся из bench_tmp/sample.wav (создаётся tools/bench_latency.py stt).
Движок onnx участвует, только если модель уже положена — сама она не качается.

    python tools/bench_stt.py
    python tools/bench_stt.py --runs 5
"""
import os
import sys
import time
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

from app import load_config                              # noqa: E402
from core.stt import FasterWhisper, WhisperOnnx          # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE = os.path.join(ROOT, "bench_tmp", "sample.wav")


def load_audio():
    if not os.path.exists(SAMPLE):
        print(f"нет записи {SAMPLE}\nсначала: python tools/bench_latency.py stt")
        sys.exit(1)
    with wave.open(SAMPLE, "rb") as w:
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        dur = w.getnframes() / w.getframerate()
    return pcm.astype(np.float32) / 32768.0, dur


def bench(engine, audio, dur, runs, label=None):
    label = label or engine.name
    t0 = time.perf_counter()
    try:
        engine.load()                     # load() уже включает прогрев
    except Exception as exc:
        print(f"{label:>16}: не поднялся — {exc}")
        return
    load = time.perf_counter() - t0

    best, text = None, ""
    for _ in range(runs):
        t0 = time.perf_counter()
        text = engine.transcribe(audio)
        took = time.perf_counter() - t0
        best = took if best is None else min(best, took)

    print(f"{label:>16}: загрузка {load:5.1f} с | разбор {best:5.2f} с "
          f"| RTF {best / dur:.2f}")
    print(f"{'':>16}  «{text}»")


def main():
    runs = 3
    if "--runs" in sys.argv:
        runs = int(sys.argv[sys.argv.index("--runs") + 1])

    cfg = dict(load_config())
    audio, dur = load_audio()
    print(f"запись {dur:.1f} с, по {runs} прогона на движок\n")

    for size in ("tiny", "base", "small"):
        bench(FasterWhisper({**cfg, "whisper_model": size}), audio, dur, runs,
              label=f"whisper {size}")
    bench(WhisperOnnx(cfg), audio, dur, runs, label="sherpa-onnx")

    print("\nRTF 1.00 значит: сколько человек говорил, столько же он потом ждёт.")


if __name__ == "__main__":
    main()
