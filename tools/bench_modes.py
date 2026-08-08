# -*- coding: utf-8 -*-
"""
Сколько стоят хуки и как их убрать, не потеряв авторизацию.

Важное правило замера: проверять is_error. Флаг --bare выглядит мгновенным,
но он просто не логинится и падает за 0.05 с — принять это за ускорение легко,
поэтому здесь каждый ход проверяется на настоящий ответ.

    python tools/bench_modes.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.agent import SYSTEM_APPEND, REMINDER  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOWINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
SENT_END = re.compile(r"[.!?…]")

QUESTIONS = ("Привет, слышишь меня?",
             "Сколько будет два плюс два?",
             "Скажи любое слово")


def run(label, extra, partial=True, model=""):
    claude = shutil.which("claude") or shutil.which("claude.cmd")
    cmd = [claude, "-p", "--input-format", "stream-json",
           "--output-format", "stream-json", "--verbose",
           "--append-system-prompt", SYSTEM_APPEND] + list(extra)
    if partial:
        cmd += ["--include-partial-messages"]
    if model:
        cmd += ["--model", model]

    t_spawn = time.perf_counter()
    proc = subprocess.Popen(cmd, cwd=ROOT, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace",
                            bufsize=1, creationflags=NOWINDOW)
    print(f"\n{label}")
    init, rows, broken = None, [], None
    try:
        for n, question in enumerate(QUESTIONS):
            payload = json.dumps(
                {"type": "user",
                 "message": {"role": "user", "content": question + REMINDER}},
                ensure_ascii=False)
            t0 = time.perf_counter()
            proc.stdin.write(payload + "\n")
            proc.stdin.flush()

            tok = sent = blk = None
            buf = ""
            for raw in proc.stdout:
                try:
                    ev = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                et = ev.get("type")

                if et == "system" and ev.get("subtype") == "init" and init is None:
                    init = time.perf_counter() - t_spawn

                if et == "stream_event":
                    piece = ((ev.get("event") or {}).get("delta") or {}).get("text") or ""
                    if piece:
                        if tok is None:
                            tok = time.perf_counter() - t0
                        buf += piece
                        # тег эмоции '[done] ' в начале за предложение не считаем
                        body = re.sub(r"^\s*\[\w+\]\s*", "", buf)
                        if sent is None and SENT_END.search(body):
                            sent = time.perf_counter() - t0

                if et == "assistant" and blk is None:
                    if any(b.get("type") == "text" and b.get("text", "").strip()
                           for b in (ev.get("message") or {}).get("content") or []):
                        blk = time.perf_counter() - t0

                if et == "result":
                    total = time.perf_counter() - t0
                    if ev.get("is_error"):
                        broken = str(ev.get("result"))[:120]
                        print(f"  ХОД НЕ СОСТОЯЛСЯ: {broken}")
                    else:
                        rows.append((n, tok, sent, blk, total))
                    break
            if broken:
                break
    except Exception as exc:
        print(f"  оборвалось: {exc}")
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass
        proc.terminate()

    if broken:
        return None

    def f(v):
        return f"{v:5.2f}" if v is not None else "  —  "

    print(f"  инициализация процесса: {init:.2f} с" if init else "  инициализация: ?")
    for n, tok, sent, blk, tot in rows:
        print(f"  {n + 1}-й ход: 1-й токен {f(tok)} | 1-я фраза {f(sent)} | "
              f"блок {f(blk)} | конец {f(tot)}")
    warm = [r for r in rows if r[0] > 0]
    if warm:
        best_sent = [r[2] for r in warm if r[2]]
        best_blk = [r[3] for r in warm if r[3]]
        got = min(best_sent) if best_sent else (min(best_blk) if best_blk else None)
        print(f"  → на прогретом до первой фразы: {got:.2f} с" if got else "")
    return rows


if __name__ == "__main__":
    print("=" * 66)
    run("КАК СЕЙЧАС (хуки claude-mem, MCP отключены)",
        ["--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}'])
    run("--setting-sources project,local (без пользовательских настроек)",
        ["--setting-sources", "project,local",
         "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}'])
    run("--safe-mode (без хуков, плагинов, CLAUDE.md)", ["--safe-mode"])
    run("--safe-mode + без скилов", ["--safe-mode", "--disable-slash-commands"])
    print()
