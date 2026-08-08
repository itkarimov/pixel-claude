# -*- coding: utf-8 -*-
"""
Честная проверка --bare: он реально отвечает или мгновенно падает?

Флаг обещает пропустить хуки, но в описании есть оговорка «OAuth и keychain не
читаются». Если авторизация не подхватывается, ход завершается за сотые доли
секунды ошибкой — и это легко принять за фантастическое ускорение.
"""
import json
import shutil
import subprocess
import sys
import time

NOWINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def probe(label, extra):
    claude = shutil.which("claude") or shutil.which("claude.cmd")
    cmd = [claude, "-p", "--input-format", "stream-json",
           "--output-format", "stream-json", "--verbose",
           "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}'] + extra
    proc = subprocess.Popen(cmd, cwd=".", stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace",
                            bufsize=1, creationflags=NOWINDOW)
    payload = json.dumps({"type": "user",
                          "message": {"role": "user",
                                      "content": "Скажи одно слово: огурец"}},
                         ensure_ascii=False)
    t0 = time.perf_counter()
    proc.stdin.write(payload + "\n")
    proc.stdin.flush()

    for raw in proc.stdout:
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "result":
            print(f"[{label}] {time.perf_counter() - t0:.2f} с | "
                  f"ошибка={ev.get('is_error')} | subtype={ev.get('subtype')}")
            print(f"   ответ: {str(ev.get('result'))[:300]}")
            break
    try:
        proc.stdin.close()
        proc.terminate()
        err = (proc.stderr.read() or "").strip()
        if err:
            print(f"   stderr: {err[:300]}")
    except Exception:
        pass


if __name__ == "__main__":
    probe("--bare", ["--bare"])
    probe("обычный", [])
