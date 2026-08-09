# -*- coding: utf-8 -*-
"""
Предполётная проверка перед запуском окна.
Гоняет всё, что можно проверить без человека у микрофона.

    python tools/preflight.py
"""
import io
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OK, BAD = "  [ок]  ", "  [!!]  "
problems = []


def check(name, fn):
    try:
        detail = fn()
        print(f"{OK}{name}" + (f" — {detail}" if detail else ""))
    except Exception as exc:
        print(f"{BAD}{name} — {exc}")
        problems.append(name)


def c_imports():
    from core.agent import AgentRunner          # noqa: F401
    from core.stt import Listener               # noqa: F401
    from core.tts import Speaker                # noqa: F401
    from core import sessions                   # noqa: F401
    from ui.window import MainWindow            # noqa: F401
    return "все модули поднялись"


def c_sprites():
    from core.agent import EMOTIONS
    spr = os.path.join(ROOT, "assets", "sprites")
    missing = [e for e in EMOTIONS if not os.path.exists(os.path.join(spr, f"{e}.png"))]
    if missing:
        raise RuntimeError("нет спрайтов: " + ", ".join(sorted(missing)))
    extra = {os.path.splitext(f)[0] for f in os.listdir(spr)} - EMOTIONS
    tail = f", лишних файлов {len(extra)}" if extra else ""
    return f"{len(EMOTIONS)} эмоций на месте{tail}"


def c_claude():
    from core.agent import AgentRunner
    from app import load_config
    agent = AgentRunner(load_config())
    if not agent.available():
        raise RuntimeError("claude не найден в PATH")
    return os.path.basename(agent.claude)


def c_llama():
    """Второй мозг — проверяем только ключ, запрос стоит денег."""
    from app import load_config
    from core.llama import api_key, DEFAULT_MODEL
    cfg = load_config()
    if not api_key(cfg):
        raise RuntimeError("нет ключа Meta AI — впиши llama_api_key в config.json "
                           "или задай LLAMA_API_KEY. Ключ: "
                           "https://llama.developer.meta.com")
    try:
        import requests                             # noqa: F401
    except ImportError:
        raise RuntimeError("нет requests — pip install requests")
    return f"ключ на месте, модель «{cfg.get('llama_model') or DEFAULT_MODEL}»"


def c_sessions():
    from core import sessions
    t0 = time.time()
    items = sessions.list_sessions()
    if not items:
        raise RuntimeError("список пуст")
    return f"{len(items)} шт за {time.time() - t0:.2f} с, сверху «{items[0]['title']}»"


def c_tts():
    from app import load_config
    from core.tts import Speaker
    wav = Speaker(load_config())._synth_edge("Проверка.")
    if not wav:
        raise RuntimeError("edge-tts молчит, останется системный голос")
    return f"{os.path.getsize(wav) // 1024} КБ wav"


def c_mic():
    """Открываем поток на три секунды и смотрим, идёт ли звук."""
    import numpy as np
    import sounddevice as sd
    frames = []
    with sd.InputStream(samplerate=16000, channels=1, dtype="int16",
                        blocksize=480,
                        callback=lambda d, f, t, s: frames.append(d[:, 0].copy())):
        time.sleep(3.0)
    if not frames:
        raise RuntimeError("с микрофона не пришло ни одного кадра")
    rms = [float(np.sqrt(np.mean(f.astype(np.float32) ** 2))) for f in frames]
    peak, floor = max(rms), sorted(rms)[len(rms) // 2]
    if peak < 30:
        raise RuntimeError(f"тишина в линии (пик {peak:.0f}) — проверь устройство ввода")
    return f"{len(frames)} кадров, фон {floor:.0f}, пик {peak:.0f}"


def c_whisper():
    from app import load_config
    cfg = load_config()
    t0 = time.time()
    from faster_whisper import WhisperModel
    WhisperModel(cfg.get("whisper_model", "small"), device="cpu", compute_type="int8")
    return f"модель «{cfg.get('whisper_model')}» за {time.time() - t0:.1f} с"


print("── предполётная проверка ──")
check("модули", c_imports)
check("спрайты эмоций", c_sprites)
check("claude CLI", c_claude)
check("Meta AI (второй мозг)", c_llama)
check("список сессий", c_sessions)
check("синтез речи", c_tts)
check("микрофон", c_mic)
check("распознавание", c_whisper)

print()
if problems:
    print("не прошло:", ", ".join(problems))
    sys.exit(1)
print("всё готово — можно запускать окно")
