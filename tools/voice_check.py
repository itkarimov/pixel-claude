# -*- coding: utf-8 -*-
"""
Какой голос сейчас настроен и работает ли он.

Проверяет ровно то, что стоит в config.json: движок, наличие голоса, время до
первого звука. Без --play ничего не проигрывает — только синтезирует.

    python tools/voice_check.py
    python tools/voice_check.py --play
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import load_config                          # noqa: E402
from core.tts import Speaker                         # noqa: E402

PHRASE = "Готово, посмотрела три файла и поправила два."


def main():
    cfg = load_config()
    speaker = Speaker(cfg)
    print(f"движок: {speaker.engine()}")
    if speaker.engine() == "piper":
        print(f"голос:  {cfg.get('piper_voice')}")
    else:
        print(f"голос:  {cfg.get('voice')} (через сеть)")

    t0 = time.perf_counter()
    if speaker.engine() == "piper":
        voice = speaker._load_piper()
        if voice is None:
            print("модель локального голоса не найдена")
            sys.exit(1)
        chunks = list(voice.synthesize(PHRASE))
        size = sum(len(c.audio_int16_bytes) for c in chunks)
    else:
        wav = speaker._synth_edge(PHRASE)
        if not wav:
            print("синтез не отдал звук — проверь интернет и ffmpeg")
            sys.exit(1)
        size = os.path.getsize(wav)
    print(f"синтез: {time.perf_counter() - t0:.2f} с, {size // 1024} КБ звука")

    if "--play" in sys.argv:
        speaker.say(PHRASE)
        time.sleep(6)
    print("работает")


if __name__ == "__main__":
    main()
