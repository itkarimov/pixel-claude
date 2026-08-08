# -*- coding: utf-8 -*-
"""
Сквозной замер: от конца фразы человека до первого звука ответа.

Меряем то, что человек реально чувствует, а не отдельные стадии. Фраза берётся
из файла (bench_tmp/sample.wav, создаётся tools/bench_latency.py), дальше всё
по-настоящему: распознавание, ход модели, синтез и вывод в динамики.

    python tools/e2e_test.py            # без звука в колонках
    python tools/e2e_test.py --play     # со звуком
    python tools/e2e_test.py --eyes     # ещё и с кадром с камеры
"""
import os
import sys
import time
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np                                        # noqa: E402
from PySide6.QtCore import QCoreApplication, QTimer       # noqa: E402

from app import load_config                               # noqa: E402
from core.agent import AgentRunner                        # noqa: E402
from core.tts import Speaker                              # noqa: E402
from core.vision import Eyes                              # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE = os.path.join(ROOT, "bench_tmp", "sample.wav")


def transcribe(cfg):
    if not os.path.exists(SAMPLE):
        print(f"нет образца {SAMPLE} — сначала: python tools/bench_latency.py stt")
        sys.exit(1)
    with wave.open(SAMPLE, "rb") as w:
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        dur = w.getnframes() / w.getframerate()
    audio = pcm.astype(np.float32) / 32768.0

    from faster_whisper import WhisperModel
    name = cfg.get("whisper_model", "base")
    model = WhisperModel(name, device="cpu", compute_type="int8",
                         cpu_threads=os.cpu_count() or 4)
    list(model.transcribe(audio[:8000], language="ru", beam_size=1)[0])   # прогрев

    t0 = time.perf_counter()
    segs, _ = model.transcribe(audio, language=cfg.get("whisper_language", "ru"),
                               beam_size=1, vad_filter=False,
                               without_timestamps=True,
                               condition_on_previous_text=False)
    text = " ".join(s.text.strip() for s in segs).strip()
    took = time.perf_counter() - t0
    print(f"распознавание ({name}): {took:.2f} с на фразу {dur:.1f} с")
    print(f"  «{text}»")
    return text, took


def main():
    play = "--play" in sys.argv
    with_eyes = "--eyes" in sys.argv

    cfg = dict(load_config())
    if not play:
        cfg["voice"] = ""          # без сети и без звука — меряем только путь

    app = QCoreApplication(sys.argv)
    heard, t_stt = transcribe(cfg)

    agent = AgentRunner(cfg)
    flags = [a for a in agent._build_cmd()
             if a.startswith("--") or a in ("project,local",)]
    print(f"флаги CLI: {' '.join(flags)}")
    print(f"рабочая папка: {agent.workdir}")
    speaker = Speaker(cfg) if play else None
    if speaker:
        speaker.warmup()          # как в приложении: связь греется на старте
    marks = {}

    def on_speak(text):
        marks.setdefault("text", time.perf_counter())
        if speaker:
            speaker.say(text)

    def on_sound(on):
        # только звук ЭТОГО хода: хвост прогревочной фразы иначе попадает
        # в метку и замер уезжает в минус
        if on and marks.get("text"):
            marks.setdefault("sound", time.perf_counter())

    agent.speak.connect(on_speak)
    talking = {"now": False}
    if speaker:
        speaker.speaking.connect(on_sound)
        speaker.speaking.connect(lambda on: talking.__setitem__("now", on))

    agent.log.connect(lambda kind, text: print(f"   [{kind}] {text[:90]}")
                      if kind in ("tool", "error", "system") else None)

    eyes = None
    if with_eyes:
        eyes = Eyes(cfg)
        eyes.start()

    # два случая, они честно разные: короткий вопрос и настоящая работа
    turns = [("простой вопрос", "Как настроение?"),
             ("рабочая просьба", heard)]
    state = {"phase": 0, "t0": None, "n": 0}

    def idle():
        return agent.alive() and not agent._busy and not talking["now"]

    def step():
        phase = state["phase"]
        if phase == 0:
            if not agent.alive():
                return
            print("\nпрогревочный ход…")
            state["phase"] = 1
            agent.send("Привет")
        elif phase == 1 and idle():
            # ждём и тишины тоже: иначе метка «первый звук» достаётся
            # от прогревочного хода и замер уходит в минус
            if speaker:
                speaker.shutup()          # добиваем очередь, если что-то осталось
            marks.clear()
            shot = eyes.snapshot() if eyes else None
            if with_eyes:
                print(f"кадр с камеры: "
                      f"{'нет' if not shot else str(len(shot) // 1024) + ' КБ'}")
            label, text = turns[state["n"]]
            print(f"\n— {label}: «{text[:60]}»")
            state["phase"] = 2
            state["t0"] = time.perf_counter()
            agent.send(text, shot)
        elif phase == 2 and marks.get("text") and (marks.get("sound") or not play):
            report(turns[state["n"]][0], state["t0"], marks, t_stt, play)
            state["n"] += 1
            if state["n"] >= len(turns):
                state["phase"] = 3
                agent.stop()
                if eyes:
                    eyes.stop()
                QTimer.singleShot(300, app.quit)
            else:
                state["phase"] = 1

    timer = QTimer()
    timer.timeout.connect(step)
    timer.start(120)
    agent.start()
    QTimer.singleShot(90_000, app.quit)          # чтобы не висеть вечно
    app.exec()


def report(label, t0, marks, t_stt, play):
    to_text = marks["text"] - t0
    synth = (marks["sound"] - marks["text"]) if (play and marks.get("sound")) else 0.62
    note = "" if (play and marks.get("sound")) else "  (из bench_latency)"
    print("\n" + "=" * 58)
    print(f"  {label}")
    print(f"  пауза после фразы (VAD)     {0.55:5.2f} с")
    print(f"  распознавание               {t_stt:5.2f} с")
    print(f"  ход модели до первой фразы  {to_text:5.2f} с")
    print(f"  синтез до первого звука     {synth:5.2f} с{note}")
    print(f"  ИТОГО от конца фразы        {0.55 + t_stt + to_text + synth:5.2f} с")
    print("=" * 58)


if __name__ == "__main__":
    main()
