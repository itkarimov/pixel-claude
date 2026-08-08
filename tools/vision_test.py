# -*- coding: utf-8 -*-
"""
Проверка зрения: снять кадр с камеры и спросить claude, что он видит.

Отвечает на два вопроса сразу:
  1) ловится ли кадр с этой камеры и за сколько;
  2) принимает ли claude CLI картинку прямо в потоке stream-json —
     если да, зрение стоит доли секунды, если нет, кадр пришлось бы класть
     на диск и ждать лишний ход с чтением файла.

    python tools/vision_test.py           # камера + вопрос модели
    python tools/vision_test.py --shot    # только снять кадр, без модели
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


def grab(width=768, quality=70, warmup=3):
    """Кадр с камеры в JPEG-байты. Первые кадры тёмные — камера не успела."""
    import cv2

    t0 = time.perf_counter()
    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    if not cap.isOpened():
        raise RuntimeError("камера не открылась")
    opened = time.perf_counter() - t0

    frame = None
    for _ in range(warmup):
        ok, f = cap.read()
        if ok:
            frame = f
    cap.release()
    if frame is None:
        raise RuntimeError("кадр не пришёл")
    grabbed = time.perf_counter() - t0

    h, w = frame.shape[:2]
    if w > width:
        frame = cv2.resize(frame, (width, int(h * width / w)),
                           interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("не сжалось в jpeg")
    total = time.perf_counter() - t0
    print(f"камера: открытие {opened:.2f} с | кадр {grabbed:.2f} с | "
          f"всего {total:.2f} с | {w}x{h} → {frame.shape[1]}x{frame.shape[0]} | "
          f"{len(buf) / 1024:.0f} КБ")
    return bytes(buf)


def ask(jpeg, question="Что ты видишь на этом снимке? Опиши одной фразой."):
    claude = shutil.which("claude") or shutil.which("claude.cmd")
    if not claude:
        print("claude не найден в PATH")
        return

    cmd = [claude, "-p", "--input-format", "stream-json",
           "--output-format", "stream-json", "--verbose",
           "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']
    proc = subprocess.Popen(cmd, cwd=ROOT, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace",
                            bufsize=1, creationflags=NOWINDOW)

    payload = json.dumps({
        "type": "user",
        "message": {"role": "user", "content": [
            {"type": "image", "source": {"type": "base64",
                                         "media_type": "image/jpeg",
                                         "data": base64.b64encode(jpeg).decode()}},
            {"type": "text", "text": question},
        ]},
    }, ensure_ascii=False)

    t0 = time.perf_counter()
    proc.stdin.write(payload + "\n")
    proc.stdin.flush()

    answer = ""
    for raw in proc.stdout:
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "assistant":
            for b in (ev.get("message") or {}).get("content") or []:
                if b.get("type") == "text" and b.get("text", "").strip():
                    print(f"[{time.perf_counter() - t0:.2f} с] {b['text'].strip()}")
                    answer = b["text"]
        if ev.get("type") == "result":
            if ev.get("is_error"):
                print("ОШИБКА:", str(ev.get("result"))[:400])
            print(f"ход целиком: {time.perf_counter() - t0:.2f} с")
            break

    err = ""
    try:
        proc.stdin.close()
        proc.terminate()
        err = (proc.stderr.read() or "").strip()
    except Exception:
        pass
    if err and not answer:
        print("stderr:", err[:400])
    return answer


if __name__ == "__main__":
    jpeg = grab()
    out = os.path.join(ROOT, "bench_tmp", "shot.jpg")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "wb") as fh:
        fh.write(jpeg)
    print("кадр сохранён:", out)
    if "--shot" not in sys.argv:
        ask(jpeg)
