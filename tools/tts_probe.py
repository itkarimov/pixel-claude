# -*- coding: utf-8 -*-
"""
Где теряется время в озвучке: сеть, запуск ffmpeg или его раскачка.

Поток обещал первый звук за 0.62 с, а на деле выходит 1.7 с — значит, между
приходом mp3 и появлением звука что-то жуёт секунду. Разбираем по частям.
"""
import asyncio
import os
import shutil
import subprocess
import sys
import threading
import time

NOWINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
TEXT = "Готово, посмотрела три файла и поправила два."
VOICE = "ru-RU-SvetlanaNeural"
FFMPEG = shutil.which("ffmpeg")


def spawn(extra):
    return subprocess.Popen(
        [FFMPEG, "-hide_banner", "-loglevel", "error"] + extra +
        ["-f", "mp3", "-i", "pipe:0", "-f", "s16le", "-ar", "24000", "-ac", "1",
         "pipe:1"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, creationflags=NOWINDOW)


def measure(label, extra, read_size):
    t0 = time.perf_counter()
    ff = spawn(extra)
    spawned = time.perf_counter() - t0
    marks = {}

    def feed():
        async def pump():
            import edge_tts
            agen = edge_tts.Communicate(TEXT, VOICE, rate="+8%").stream()
            try:
                async for chunk in agen:
                    if chunk["type"] == "audio" and chunk.get("data"):
                        marks.setdefault("mp3", time.perf_counter() - t0)
                        ff.stdin.write(chunk["data"])
                        ff.stdin.flush()
            finally:
                await agen.aclose()

        try:
            asyncio.run(pump())
        except Exception as exc:
            marks["err"] = exc
        finally:
            try:
                ff.stdin.close()
            except OSError:
                pass

    threading.Thread(target=feed, daemon=True).start()

    total = 0
    while True:
        block = ff.stdout.read(read_size)
        if not block:
            break
        marks.setdefault("pcm", time.perf_counter() - t0)
        total += len(block)
    ff.terminate()

    secs = total / 2 / 24000
    print(f"{label}")
    print(f"   запуск ffmpeg {spawned:.2f} с | первый mp3 "
          f"{marks.get('mp3', -1):.2f} с | ПЕРВЫЙ ЗВУК {marks.get('pcm', -1):.2f} с "
          f"| всего {secs:.1f} с речи")
    if marks.get("err"):
        print(f"   ошибка: {marks['err']}")


if __name__ == "__main__":
    if not FFMPEG:
        print("ffmpeg не найден")
        sys.exit(1)
    measure("как сделано сейчас (блок 50 мс)", [], 4800)
    measure("+ без разведки формата", ["-probesize", "32", "-analyzeduration", "0"],
            4800)
    measure("+ мелкий блок чтения (10 мс)",
            ["-probesize", "32", "-analyzeduration", "0", "-fflags", "nobuffer",
             "-flush_packets", "1"], 960)
