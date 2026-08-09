# -*- coding: utf-8 -*-
"""
Во сколько токенов обходится реплика Meta AI.

Считать на глаз нельзя: у моделей muse-spark основной расход — не ответ, а
размышления перед ним, и они идут из того же бюджета. На фразе «как дела?»
намерено 405 токенов размышлений и 16 на сам ответ. Управляется это
параметром reasoning_effort, и разница в разы:

    без настройки   421 токен на выходе
    low             354
    minimal         101      ← стоит в конфиге по умолчанию

Ответ во всех трёх случаях одинаковый, поэтому для голосовой болтовни
minimal — правильный выбор. Значение "none" модель не принимает.

Прогоняйте после смены модели: у каждой свои аппетиты.
Каждый прогон — настоящий запрос, то есть настоящие деньги (копейки).

    python tools/llama_cost.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import load_config                                   # noqa: E402
from core.llama import api_key, endpoint, DEFAULT_MODEL       # noqa: E402

QUESTION = "Ответь одной короткой фразой: как дела?"
EFFORTS = (None, "low", "minimal")


def ask(cfg, model, effort):
    import requests

    body = {"model": model,
            "messages": [{"role": "user", "content": QUESTION}],
            "max_completion_tokens": 2000}
    if effort:
        body["reasoning_effort"] = effort

    t0 = time.perf_counter()
    r = requests.post(endpoint(cfg), timeout=120, json=body,
                      headers={"Authorization": f"Bearer {api_key(cfg)}",
                               "Content-Type": "application/json"})
    took = time.perf_counter() - t0
    if r.status_code >= 400:
        return None, took, (r.text or "")[:120]

    data = r.json()
    usage = data.get("usage") or {}
    text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content")
    return {
        "вход": usage.get("prompt_tokens", 0),
        "выход": usage.get("completion_tokens", 0),
        "размышления": (usage.get("completion_tokens_details")
                        or {}).get("reasoning_tokens", 0),
        "ответ": text,
    }, took, None


def main():
    cfg = load_config()
    if not api_key(cfg):
        print("нет ключа — смотри config.json")
        return 1

    models = sys.argv[1:] or [cfg.get("llama_model") or DEFAULT_MODEL]
    print(f"адрес: {endpoint(cfg)}")
    print(f"вопрос: {QUESTION}\n")
    print(f"{'модель':22} {'усилие':9} {'вход':>5} {'выход':>6} "
          f"{'думала':>7} {'сек':>6}   ответ")

    best = None
    for model in models:
        for effort in EFFORTS:
            got, took, err = ask(cfg, model, effort)
            label = effort or "по умолч."
            if err:
                print(f"{model:22} {label:9} — не вышло: {err}")
                continue
            print(f"{model:22} {label:9} {got['вход']:5} {got['выход']:6} "
                  f"{got['размышления']:7} {took:6.1f}   {str(got['ответ'])[:40]}")
            if got["ответ"] and (best is None or got["выход"] < best[2]):
                best = (model, label, got["выход"])

    if best:
        print(f"\nдешевле всего: {best[0]}, усилие {best[1]} — "
              f"{best[2]} токенов на выходе")
    return 0


if __name__ == "__main__":
    sys.exit(main())
