# -*- coding: utf-8 -*-
"""
Долгоживущий процесс описателя против свежего на каждый кадр.

Вопрос простой: описатель смотрит в камеру раз в полминуты часами. Держать под
это один процесс или поднимать новый каждый раз? У первого варианта разгон за
счёт кеша, но каждый кадр навсегда оседает в контексте и оплачивается снова в
каждом следующем ходе. Смотрим на оба числа — время и входные токены.

    python tools/bench_describer.py
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOWINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
SHOTS = 4


def grab_frames(n):
    """Разные кадры: одинаковые модель могла бы и закешировать."""
    import cv2

    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    if not cap.isOpened():
        raise RuntimeError("камера не открылась")
    out = []
    for _ in range(n):
        frame = None
        for _ in range(3):
            ok, f = cap.read()
            if ok:
                frame = f
        if frame is None:
            break
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        if ok:
            out.append(bytes(buf))
        time.sleep(0.6)
    cap.release()
    return out


def spawn():
    from core.watcher import SYSTEM

    claude = shutil.which("claude") or shutil.which("claude.cmd")
    cmd = [claude, "-p", "--input-format", "stream-json",
           "--output-format", "stream-json", "--verbose",
           "--safe-mode", "--tools", "",
           "--append-system-prompt", SYSTEM, "--model", "haiku"]
    return subprocess.Popen(cmd, cwd=ROOT, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, encoding="utf-8", errors="replace",
                            bufsize=1, creationflags=NOWINDOW)


def ask(proc, jpeg):
    from core.watcher import ASK

    payload = json.dumps({
        "type": "user",
        "message": {"role": "user", "content": [
            {"type": "image", "source": {"type": "base64",
                                         "media_type": "image/jpeg",
                                         "data": base64.b64encode(jpeg).decode()}},
            {"type": "text", "text": ASK},
        ]},
    }, ensure_ascii=False)

    t0 = time.perf_counter()
    proc.stdin.write(payload + "\n")
    proc.stdin.flush()
    for raw in proc.stdout:
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if ev.get("type") != "result":
            continue
        usage = ev.get("usage") or {}
        tokens = (usage.get("input_tokens", 0)
                  + usage.get("cache_read_input_tokens", 0)
                  + usage.get("cache_creation_input_tokens", 0))
        return time.perf_counter() - t0, tokens, str(ev.get("result"))[:70]
    return None, None, None


def kill(proc):
    try:
        proc.stdin.close()
    except OSError:
        pass
    proc.terminate()


def main():
    frames = grab_frames(SHOTS)
    if len(frames) < 2:
        print("не набрала кадров с камеры")
        sys.exit(1)
    print(f"кадров: {len(frames)}, по {len(frames[0]) // 1024} КБ\n")

    print("── один процесс на все кадры ──")
    proc = spawn()
    base = None
    for n, jpeg in enumerate(frames, 1):
        took, tokens, text = ask(proc, jpeg)
        if took is None:
            print(f"  кадр {n}: ответа нет")
            continue
        base = tokens if base is None else base
        print(f"  кадр {n}: {took:5.2f} с | вход {tokens} т (+{tokens - base})")
        print(f"           «{text}»")
    kill(proc)

    print("\n── свежий процесс на каждый кадр ──")
    for n, jpeg in enumerate(frames, 1):
        proc = spawn()
        took, tokens, text = ask(proc, jpeg)
        kill(proc)
        if took is None:
            print(f"  кадр {n}: ответа нет")
            continue
        print(f"  кадр {n}: {took:5.2f} с | вход {tokens} т")

    print("\nРост входа у долгоживущего — это осевшие кадры. Отсюда перезапуск "
          "раз в watch_recycle_every описаний.")


if __name__ == "__main__":
    main()
