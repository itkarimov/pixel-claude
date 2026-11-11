# -*- coding: utf-8 -*-
"""
Образцы голосов: одна и та же фраза разными движками, рядом — сколько заняло.

Слушать и выбирать приходится человеку, поэтому инструмент делает две вещи:
кладёт wav-файлы в одну папку и печатает время синтеза. Время меряется до
готового звука целиком, без проигрывания; для озвучки важнее первая секунда,
но для сравнения движков между собой этого хватает.

    python tools/voice_samples.py            все движки
    python tools/voice_samples.py edge       только edge-tts

Выход: bench_tmp/voices/<движок>_<голос>.wav
"""
import io
import os
import sys
import time
import wave

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "bench_tmp", "voices")

# Фраза нарочно обычная, с числом и запинкой: на ровном книжном тексте любой
# движок звучит прилично, разница слышна на живой реплике.
TEXT = ("Готово, посмотрела три файла и поправила два. "
        "Слушай, а давай я ещё и тесты прогоню?")

EDGE_VOICES = [
    "ru-RU-SvetlanaNeural",
    "ru-RU-DmitryNeural",
    # Многоязычные голоса говорят по-русски живее, чем собственно русские:
    # у них интонация тянется из английской модели.
    "en-US-AvaMultilingualNeural",
    "en-US-EmmaMultilingualNeural",
    "de-DE-SeraphinaMultilingualNeural",
    "fr-FR-VivienneMultilingualNeural",
]

SILERO_SPEAKERS = ["kseniya", "baya", "xenia", "aidar", "eugene"]


def save(path, pcm, rate):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)


def seconds(pcm, rate):
    return len(pcm) / 2 / rate


def report(name, took, pcm, rate):
    dur = seconds(pcm, rate)
    print(f"  {name:38s} {took:5.2f} c на {dur:5.2f} c звука  "
          f"(быстрее реального времени в {dur / max(took, 1e-6):4.1f} раза)")


def run_edge(only=None):
    import asyncio
    import subprocess
    import shutil

    import edge_tts

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("edge: нет ffmpeg, mp3 не во что разворачивать")
        return

    for voice in EDGE_VOICES:
        if only and only not in voice.lower():
            continue
        start = time.time()

        async def grab():
            chunks = []
            comm = edge_tts.Communicate(TEXT, voice, rate="+8%")
            async for part in comm.stream():
                if part["type"] == "audio":
                    chunks.append(part["data"])
            return b"".join(chunks)

        try:
            mp3 = asyncio.run(grab())
        except Exception as exc:
            print(f"  {voice:38s} не вышло: {exc}")
            continue
        out = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
             "-f", "s16le", "-ar", "24000", "-ac", "1", "pipe:1"],
            input=mp3, capture_output=True)
        took = time.time() - start
        save(os.path.join(OUT, f"edge_{voice}.wav"), out.stdout, 24000)
        report(voice, took, out.stdout, 24000)


def run_piper():
    from piper import PiperVoice

    name = "ru_RU-irina-medium"
    path = os.path.join(ROOT, "assets", "voices", f"{name}.onnx")
    if not os.path.exists(path):
        print("piper: модели нет")
        return
    start = time.time()
    voice = PiperVoice.load(path)
    load = time.time() - start

    start = time.time()
    pcm = b"".join(c.audio_int16_bytes for c in voice.synthesize(TEXT))
    took = time.time() - start
    save(os.path.join(OUT, f"piper_{name}.wav"), pcm, voice.config.sample_rate)
    report(name, took, pcm, voice.config.sample_rate)
    print(f"  (модель грузится {load:.2f} c, один раз за запуск)")


def run_silero(version="v5_6_ru"):
    import torch

    path = os.path.join(ROOT, "assets", "voices", f"{version}.pt")
    if not os.path.exists(path):
        url = f"https://models.silero.ai/models/tts/ru/{version}.pt"
        print(f"  качаю {url}")
        torch.hub.download_url_to_file(url, path)

    torch.set_num_threads(min(4, os.cpu_count() or 4))
    start = time.time()
    model = torch.package.PackageImporter(path).load_pickle("tts_models", "model")
    model.to(torch.device("cpu"))
    print(f"  (модель грузится {time.time() - start:.2f} c, один раз за запуск)")

    for speaker in SILERO_SPEAKERS:
        start = time.time()
        try:
            audio = model.apply_tts(text=TEXT, speaker=speaker, sample_rate=48000)
        except Exception as exc:
            print(f"  {speaker:38s} не вышло: {exc}")
            continue
        took = time.time() - start
        pcm = (audio.numpy() * 32767).astype("int16").tobytes()
        save(os.path.join(OUT, f"silero_{version}_{speaker}.wav"), pcm, 48000)
        report(f"{version} {speaker}", took, pcm, 48000)


def main():
    os.makedirs(OUT, exist_ok=True)
    which = (sys.argv[1] if len(sys.argv) > 1 else "all").lower()
    print(f"фраза: {TEXT}\nкуда: {OUT}\n")
    if which in ("all", "edge"):
        print("edge-tts (через сеть):")
        run_edge()
    if which in ("all", "piper"):
        print("piper (локально):")
        run_piper()
    if which in ("all", "silero"):
        print("silero (локально):")
        run_silero()


if __name__ == "__main__":
    main()
