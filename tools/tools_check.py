# -*- coding: utf-8 -*-
"""
Доезжает ли до claude то, что мы ему передаём: свои инструменты и разрешения.

Проверка появилась после находки, из-за которой переписан раздел README про
разрешения. Обёртка claude.cmd обрывала командную строку на переводе строки
внутри системной приписки, и --add-dir, --mcp-config и --settings до claude не
доходили вовсе. Снаружи это выглядело как три отдельные поломки: нет доступа к
папкам, модель «не хочет» звать свои инструменты, правила не срабатывают.

Поэтому команда собирается НЕ руками, а тем же core/agent.py, что и у
оболочки: смысл в том, чтобы мерить настоящий запуск, а не его описание.

Последняя проверка — та самая, из-за которой городился обходной слой: правило
на команду, где в пути есть двоеточие. Раньше считалось, что двоеточие рвёт
разбор и совпасть не с чем.

Каждая проверка — отдельный короткий запуск, целиком около минуты. Сессию не
продолжаем (--resume снят), чтобы не писать в живой разговор; к просьбе
подмешана приписка оболочки, и такой огрызок потом уберёт уборщик из
core/sessions.py.

    python tools/tools_check.py
"""
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from app import load_config                            # noqa: E402
from core.agent import REMINDER, AgentRunner           # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBE = os.path.join(ROOT, "tools", "echo_probe.py").replace("\\", "/")

# у оболочки разговор потоком, нам нужен один ход
PAIRS = ("--input-format", "--output-format", "--resume")
FLAGS = ("--verbose", "--include-partial-messages", "-p")


def one_shot(agent, prompt, model="haiku"):
    """Команда оболочки, превращённая в один ход. Возвращает (ответ, секунды)."""
    src, cmd, i = agent._build_cmd(), [], 0
    while i < len(src):
        if src[i] in PAIRS:
            i += 2
            continue
        if src[i] in FLAGS:
            i += 1
            continue
        cmd.append(src[i])
        i += 1
    cmd += ["--model", model, "--output-format", "json", "-p", prompt + REMINDER]

    start = time.time()
    try:
        done = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=300,
                              cwd=agent.workdir)
    except subprocess.TimeoutExpired:
        return "(таймаут)", time.time() - start
    took = time.time() - start
    try:
        body = json.loads(done.stdout or "{}")
    except json.JSONDecodeError:
        return (done.stdout or done.stderr or "(пусто)")[:300], took
    # тег эмоции в начале ответа — часть приписки, а не ответ: снимаем, иначе
    # проверка «ответ начинается с да» спотыкается о «[smile] Да»
    answer = re.sub(r"^\s*\[\w+\]\s*", "", (body.get("result") or ""))
    return answer.replace("\n", " ").strip(), took


def cases(cfg):
    """(название, просьба, как понять что сошлось, добавка к разрешениям)."""
    return (
        ("свои инструменты видны",
         "Перечисли названия доступных тебе инструментов, в имени которых есть "
         "слово pixel. Ничего не запускай. Если таких нет — ответь ровно «нет».",
         lambda a: "pixel" in a.lower() and not a.lower().startswith("нет"),
         []),
        ("чужие папки открыты (--add-dir)",
         "Ничего не запускай и не читай. Ответь одним словом «да» или «нет»: "
         "входит ли папка C:/Trading в число доступных тебе.",
         lambda a: a.lower().startswith("да"),
         []),
        ("правило с двоеточием в пути",
         f"Запусти команду: python {PROBE} — и ответь одним словом, что она "
         f"напечатала. Если запустить не дали, ответь ровно «не разрешено».",
         lambda a: "огурец" in a.lower(),
         [f"Bash(python {PROBE}:*)", f"Bash(python {PROBE})",
          f"PowerShell(python {PROBE} *)", f"PowerShell(python {PROBE})"]),
    )


FORMS = ("Bash({cmd}:*)", "Bash({cmd} *)", "Bash({cmd})",
         "PowerShell({cmd}:*)", "PowerShell({cmd} *)", "PowerShell({cmd})")


def measure_forms():
    """
    Какая из шести форм правила на самом деле совпадает с командой.

    Шесть их потому, что раньше никакая не срабатывала и мы писали все разом.
    Причина была в другом (файл разрешений не доезжал), и теперь можно
    спросить прямо: разрешаем ровно одну форму и смотрим, запустилась команда
    или нет.

        python tools/tools_check.py --forms
    """
    cfg = load_config()
    agent = AgentRunner(cfg)
    base = list(cfg.get("allowed_tools") or [])
    command = f"python {PROBE}"
    prompt = (f"Запусти команду: {command} — и ответь одним словом, что она "
              f"напечатала. Если запустить не дали, ответь ровно «не разрешено».")
    print(f"команда: {command}\n")
    for form in FORMS:
        cfg["allowed_tools"] = base + [form.format(cmd=command)]
        answer, took = one_shot(agent, prompt)
        ok = "огурец" in answer.lower()
        print(f"{'  прошло' if ok else '  отказ '}  {form.format(cmd='…'):28}"
              f"  ({took:.1f} с)  {answer[:60]}")
    cfg["allowed_tools"] = base
    return 0


def main():
    if "--forms" in sys.argv:
        return measure_forms()
    cfg = load_config()
    agent = AgentRunner(cfg)
    if not agent.claude:
        print("claude не найден в PATH")
        return 1
    print(f"запускаем: {agent.claude}")
    print(f"папка:     {agent.workdir}\n")

    base = list(cfg.get("allowed_tools") or [])
    bad = []
    for name, prompt, ok, extra_rules in cases(cfg):
        cfg["allowed_tools"] = base + extra_rules
        answer, took = one_shot(agent, prompt)
        good = ok(answer)
        print(f"{'  ok  ' if good else 'ПРОВАЛ'}  {name}  ({took:.1f} с)")
        print(f"        {answer[:220]}")
        if not good:
            bad.append(name)
    cfg["allowed_tools"] = base
    print("\n" + ("всё сошлось" if not bad else f"провалов: {len(bad)}"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
