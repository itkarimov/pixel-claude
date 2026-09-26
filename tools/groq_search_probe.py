# -*- coding: utf-8 -*-
"""
Разведка: каким способом у Groq включается поиск в интернете.

Форм несколько и они несовместимы — встроенный инструмент у gpt-oss против
отдельных моделей-агентов groq/compound. Пробуем все и смотрим, какая отвечает
свежими данными, а не отговоркой.

    python tools/groq_search_probe.py
"""
import json
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = "https://api.groq.com/openai/v1/chat/completions"
ASK = "Какая сейчас погода в Бишкеке? Ответь одной фразой с цифрой."

with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as fh:
    KEY = json.load(fh)["groq_api_key"]

CASES = [
    ("gpt-oss-20b + browser_search",
     {"model": "openai/gpt-oss-20b", "tools": [{"type": "browser_search"}]}),
    ("gpt-oss-20b + web_search",
     {"model": "openai/gpt-oss-20b", "tools": [{"type": "web_search"}]}),
    ("groq/compound-mini", {"model": "groq/compound-mini"}),
    ("groq/compound", {"model": "groq/compound"}),
]


def run(name, extra):
    payload = {"messages": [{"role": "user", "content": ASK}],
               "max_tokens": 500, **extra}
    try:
        resp = requests.post(URL, timeout=90, json=payload, headers={
            "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    except Exception as exc:                        # noqa: BLE001
        print(f"  {name}: сеть не дала — {exc}")
        return
    if resp.status_code >= 400:
        detail = ""
        try:
            detail = (resp.json().get("error") or {}).get("message") or ""
        except ValueError:
            detail = resp.text[:200]
        print(f"  {name}: {resp.status_code} — {detail[:180]}")
        return
    data = resp.json()
    msg = (data.get("choices") or [{}])[0].get("message") or {}
    text = " ".join((msg.get("content") or "").split())
    tools = msg.get("executed_tools") or data.get("executed_tools")
    print(f"  {name}: {text[:200]}")
    if tools:
        for t in tools:
            if isinstance(t, dict):
                print(f"      инструмент: {t.get('type')} → "
                      f"{str(t.get('arguments'))[:120]}")
    print(f"      токенов: {(data.get('usage') or {}).get('total_tokens')}")


def models():
    resp = requests.get("https://api.groq.com/openai/v1/models",
                        headers={"Authorization": f"Bearer {KEY}"}, timeout=30)
    if resp.status_code >= 400:
        print("  список моделей не отдали:", resp.status_code)
        return
    names = sorted(m["id"] for m in resp.json().get("data", []))
    print("  всего:", len(names))
    for n in names:
        if "compound" in n or "gpt-oss" in n or "qwen" in n or "llama-4" in n:
            print("   ·", n)


print("== какие модели доступны ==")
models()
print("\n== кто умеет искать ==")
for case_name, case_extra in CASES:
    run(case_name, case_extra)
