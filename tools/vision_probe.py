# -*- coding: utf-8 -*-
"""
Каким способом отдать кадр модели, чтобы она его увидела.

Три кандидата, от самого быстрого к самому медленному:
  1) картинка блоком base64 прямо в stream-json — без диска, без лишнего хода;
  2) путь к файлу в тексте — модель сама прочитает, но это лишний ход
     с вызовом инструмента, а значит секунды;
  3) то же, но с явной просьбой посмотреть файл.

Каждый вариант с таймаутом: неудачная попытка не падает, а молча висит,
и без таймаута это выглядит как «зависло приложение».
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOWINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
SHOT = os.path.join(ROOT, "bench_tmp", "shot.jpg")
QUESTION = "Что изображено на картинке? Ответь одной короткой фразой."


def spawn():
    claude = shutil.which("claude") or shutil.which("claude.cmd")
    cmd = [claude, "-p", "--input-format", "stream-json",
           "--output-format", "stream-json", "--verbose",
           "--safe-mode", "--permission-mode", "bypassPermissions"]
    return subprocess.Popen(cmd, cwd=ROOT, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace",
                            bufsize=1, creationflags=NOWINDOW)


def attempt(label, content, timeout=90):
    proc = spawn()
    result = {"answer": None, "err": None, "done": False}

    def pump():
        try:
            for raw in proc.stdout:
                try:
                    ev = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") == "assistant":
                    for b in (ev.get("message") or {}).get("content") or []:
                        if b.get("type") == "text" and b.get("text", "").strip():
                            result["answer"] = b["text"].strip()
                        if b.get("type") == "tool_use":
                            result.setdefault("tools", []).append(b.get("name"))
                if ev.get("type") == "result":
                    result["done"] = True
                    if ev.get("is_error"):
                        result["err"] = str(ev.get("result"))[:200]
                    return
        except Exception as exc:
            result["err"] = f"чтение оборвалось: {exc}"

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()

    t0 = time.perf_counter()
    try:
        payload = json.dumps({"type": "user",
                              "message": {"role": "user", "content": content}},
                             ensure_ascii=False)
        proc.stdin.write(payload + "\n")
        proc.stdin.flush()
    except Exception as exc:
        result["err"] = f"не смогла отправить: {exc}"

    reader.join(timeout)
    took = time.perf_counter() - t0
    state = "ЗАВИС" if not result["done"] else ("ОШИБКА" if result["err"] else "OK")
    print(f"\n[{state}] {label} — {took:.2f} с")
    if result.get("tools"):
        print(f"   инструменты: {', '.join(result['tools'])}")
    if result["answer"]:
        print(f"   ответ: {result['answer'][:200]}")
    if result["err"]:
        print(f"   ошибка: {result['err']}")
    if not result["done"]:
        try:
            proc.stdin.close()
        except OSError:
            pass
        proc.terminate()
        time.sleep(0.4)
        try:
            err = (proc.stderr.read() or "").strip()
            if err:
                print(f"   stderr: {err[:300]}")
        except Exception:
            pass
    else:
        try:
            proc.stdin.close()
        except OSError:
            pass
        proc.terminate()
    return result["done"] and not result["err"]


if __name__ == "__main__":
    if not os.path.exists(SHOT):
        print("сначала сними кадр: python tools/vision_test.py --shot")
        sys.exit(1)
    with open(SHOT, "rb") as fh:
        jpeg = fh.read()
    b64 = base64.b64encode(jpeg).decode()
    print(f"кадр {len(jpeg) / 1024:.0f} КБ → base64 {len(b64) / 1024:.0f} КБ")

    attempt("1. блок image/base64 в потоке", [
        {"type": "image", "source": {"type": "base64",
                                     "media_type": "image/jpeg", "data": b64}},
        {"type": "text", "text": QUESTION},
    ])
    attempt("2. путь к файлу в тексте", f"{SHOT}\n\n{QUESTION}")
    attempt("3. явная просьба прочитать файл",
            f"Посмотри изображение {SHOT} инструментом Read. {QUESTION}")
