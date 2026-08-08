# -*- coding: utf-8 -*-
"""
Замер задержки по стадиям: распознавание, ход модели, синтез речи.

Без цифр разговор про «ускорить» превращается в гадание, поэтому меряем каждую
стадию отдельно и на этой же машине.

    python tools/bench_latency.py            # всё
    python tools/bench_latency.py stt        # только распознавание
    python tools/bench_latency.py tts agent
"""
import gc
import json
import os
import subprocess
import sys
import time
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = os.path.join(ROOT, "bench_tmp")
NOWINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

PHRASE = "Посмотри пожалуйста, что там в проекте с последними правками, и скажи коротко"


def line(title):
    print(f"\n=== {title} " + "=" * max(0, 58 - len(title)))


def make_sample():
    """Эталонная фраза голосом — на ней и меряем распознавание."""
    os.makedirs(TMP, exist_ok=True)
    wav = os.path.join(TMP, "sample.wav")
    if os.path.exists(wav):
        return wav
    import asyncio
    import edge_tts

    mp3 = os.path.join(TMP, "sample.mp3")

    async def gen():
        await edge_tts.Communicate(PHRASE, "ru-RU-SvetlanaNeural").save(mp3)

    asyncio.run(gen())
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp3,
                    "-ar", "16000", "-ac", "1", wav],
                   check=True, creationflags=NOWINDOW)
    return wav


def load_pcm(path):
    with wave.open(path, "rb") as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        dur = w.getnframes() / w.getframerate()
    return data.astype(np.float32) / 32768.0, dur


def bench_stt():
    line("РАСПОЗНАВАНИЕ (faster-whisper, CPU int8)")
    audio, dur = load_pcm(make_sample())
    print(f"фраза {dur:.1f} с: «{PHRASE[:50]}…»\n")
    from faster_whisper import WhisperModel

    for name in ("tiny", "base", "small"):
        try:
            t0 = time.perf_counter()
            model = WhisperModel(name, device="cpu", compute_type="int8",
                                 cpu_threads=os.cpu_count() or 4)
            load = time.perf_counter() - t0

            # прогрев: первый прогон всегда медленнее, он не показателен
            list(model.transcribe(audio[:16000], language="ru", beam_size=1)[0])

            best, text = None, ""
            for _ in range(3):
                t0 = time.perf_counter()
                segs, _ = model.transcribe(audio, language="ru", beam_size=1,
                                           vad_filter=False,
                                           without_timestamps=True,
                                           condition_on_previous_text=False)
                text = " ".join(s.text.strip() for s in segs)
                took = time.perf_counter() - t0
                best = took if best is None else min(best, took)

            rtf = best / dur
            print(f"{name:>6}: загрузка {load:5.1f} с | распознавание {best:5.2f} с "
                  f"| RTF {rtf:.2f}")
            print(f"        «{text.strip()}»")
        except Exception as exc:
            print(f"{name:>6}: не вышло — {exc}")
        finally:
            model = None
            gc.collect()


def bench_tts():
    line("СИНТЕЗ РЕЧИ")
    text = "Готово, посмотрела три файла и поправила два."
    os.makedirs(TMP, exist_ok=True)

    # 1. как сейчас: edge-tts целиком в mp3, затем ffmpeg
    import asyncio
    import edge_tts

    mp3 = os.path.join(TMP, "t.mp3")
    wav = os.path.join(TMP, "t.wav")

    async def whole():
        await edge_tts.Communicate(text, "ru-RU-SvetlanaNeural", rate="+8%").save(mp3)

    t0 = time.perf_counter()
    asyncio.run(whole())
    got_mp3 = time.perf_counter() - t0
    t0 = time.perf_counter()
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp3,
                    "-ar", "24000", "-ac", "1", wav],
                   check=True, creationflags=NOWINDOW)
    ff = time.perf_counter() - t0
    print(f"edge-tts целиком : {got_mp3:5.2f} с + ffmpeg {ff:.2f} с = "
          f"{got_mp3 + ff:5.2f} с до первого звука")

    # 2. потоком: сколько ждать ПЕРВЫЙ кусок звука
    async def first_chunk():
        comm = edge_tts.Communicate(text, "ru-RU-SvetlanaNeural", rate="+8%")
        t = time.perf_counter()
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                return time.perf_counter() - t
        return None

    try:
        print(f"edge-tts потоком : {asyncio.run(first_chunk()):5.2f} с до первого куска")
    except Exception as exc:
        print(f"edge-tts потоком : не вышло — {exc}")

    # 3. системный голос Windows — офлайн, без сети
    if os.name == "nt":
        ps = ("$v=New-Object -ComObject SAPI.SpVoice;"
              "$s=New-Object -ComObject SAPI.SpFileStream;"
              f"$s.Open('{os.path.join(TMP, 'sapi.wav')}',3);"
              "$v.AudioOutputStream=$s;$v.Speak('Готово, посмотрела три файла.')"
              "|Out-Null;$s.Close()")
        t0 = time.perf_counter()
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       creationflags=NOWINDOW, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
        print(f"SAPI офлайн      : {time.perf_counter() - t0:5.2f} с (вся фраза)")


def bench_agent(models=("", "haiku")):
    line("ХОД МОДЕЛИ (claude CLI, долгоживущий процесс)")
    import shutil

    claude = shutil.which("claude") or shutil.which("claude.cmd")
    if not claude:
        print("claude не найден в PATH")
        return

    from core.agent import SYSTEM_APPEND, REMINDER

    for model in models:
        cmd = [claude, "-p", "--input-format", "stream-json",
               "--output-format", "stream-json", "--verbose",
               "--append-system-prompt", SYSTEM_APPEND,
               "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']
        if model:
            cmd += ["--model", model]

        t_spawn = time.perf_counter()
        proc = subprocess.Popen(cmd, cwd=ROOT, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace",
                                bufsize=1, creationflags=NOWINDOW)

        name = model or "по умолчанию"
        marks = {}
        try:
            for turn, question in enumerate(("Привет, как слышно?",
                                             "Сколько будет два плюс два?")):
                payload = json.dumps(
                    {"type": "user",
                     "message": {"role": "user", "content": question + REMINDER}},
                    ensure_ascii=False)
                t0 = time.perf_counter()
                proc.stdin.write(payload + "\n")
                proc.stdin.flush()

                first_text = None
                for raw in proc.stdout:
                    try:
                        ev = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if ev.get("type") == "system" and ev.get("subtype") == "init":
                        marks["init"] = time.perf_counter() - t_spawn
                    if ev.get("type") == "assistant" and first_text is None:
                        blocks = (ev.get("message") or {}).get("content") or []
                        if any(b.get("type") == "text" and b.get("text", "").strip()
                               for b in blocks):
                            first_text = time.perf_counter() - t0
                    if ev.get("type") == "result":
                        total = time.perf_counter() - t0
                        label = "первый ход" if turn == 0 else "второй ход"
                        print(f"{name:>13} {label}: до текста "
                              f"{first_text if first_text else -1:5.2f} с | "
                              f"до конца {total:5.2f} с")
                        break
        except Exception as exc:
            print(f"{name:>13}: оборвалось — {exc}")
        finally:
            if "init" in marks:
                print(f"{name:>13} инициализация процесса: {marks['init']:.2f} с")
            try:
                proc.stdin.close()
            except OSError:
                pass
            proc.terminate()


if __name__ == "__main__":
    what = sys.argv[1:] or ["stt", "agent", "tts"]
    if "stt" in what:
        bench_stt()
    if "tts" in what:
        bench_tts()
    if "agent" in what:
        bench_agent()
    print()
