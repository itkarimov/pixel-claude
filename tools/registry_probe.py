# -*- coding: utf-8 -*-
"""
Разведка реестра сессий приложения Claude.

Приложение держит список не в IndexedDB, как мы думали, а простыми файлами:

    %APPDATA%/Claude/claude-code-sessions/<...>/<...>/local_<uuid>.json
    %APPDATA%/Claude/claude-code-sessions/<...>/<...>/deleted_<uuid>

Нас интересуют три вещи:
  1. связывается ли запись реестра с файлом CLI-сессии (поле cliSessionId);
  2. что означает deleted_<uuid> — какой это uuid и остаётся ли рядом local_;
  3. сколько записей реестра осиротело (файла .jsonl уже нет).

    python tools/registry_probe.py
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.sessions import projects_root                 # noqa: E402


def registry_root():
    return os.path.join(os.environ.get("APPDATA", ""), "Claude", "claude-code-sessions")


def main():
    root = registry_root()
    print(f"реестр: {root}")
    print(f"есть: {os.path.isdir(root)}\n")

    live = sorted(glob.glob(os.path.join(root, "*", "*", "local_*.json")))
    tombs = sorted(glob.glob(os.path.join(root, "*", "*", "deleted_*")))
    other = [p for p in glob.glob(os.path.join(root, "*", "*", "*"))
             if p not in live and p not in tombs]

    print(f"записей local_*.json : {len(live)}")
    print(f"меток deleted_*      : {len(tombs)}")
    print(f"прочее               : {len(other)} {[os.path.basename(p) for p in other[:5]]}\n")

    # какие поля вообще есть — чтобы не гадать
    if live:
        with open(live[0], encoding="utf-8") as fh:
            sample = json.load(fh)
        print("поля записи:", ", ".join(sorted(sample)), "\n")

    rows, cli_ids, local_ids = [], set(), set()
    for path in live:
        try:
            with open(path, encoding="utf-8") as fh:
                rec = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"  [!] {os.path.basename(path)}: {exc}")
            continue
        rows.append(rec)
        if rec.get("cliSessionId"):
            cli_ids.add(rec["cliSessionId"])
        local_ids.add(rec.get("sessionId", "").replace("local_", ""))

    # сверка с файлами транскриптов
    on_disk = {os.path.splitext(os.path.basename(p))[0]
               for p in glob.glob(os.path.join(projects_root(), "*", "*.jsonl"))}
    print(f"файлов .jsonl на диске всего: {len(on_disk)}")
    print(f"из них числятся в реестре   : {len(cli_ids & on_disk)}")
    print(f"записей реестра без файла   : {len(cli_ids - on_disk)}\n")

    # чей uuid в метке удаления
    tomb_ids = {os.path.basename(p).replace("deleted_", "") for p in tombs}
    print("метки удаления:")
    print(f"  совпали с local_ id  : {len(tomb_ids & local_ids)}")
    print(f"  совпали с cliSession : {len(tomb_ids & cli_ids)}")
    print(f"  совпали с .jsonh диск: {len(tomb_ids & on_disk)}")
    print(f"  ни с чем             : {len(tomb_ids - local_ids - cli_ids - on_disk)}")
    for t in sorted(tomb_ids):
        where = []
        if t in local_ids:
            where.append("есть живая запись local_!")
        if t in cli_ids:
            where.append("cliSessionId")
        if t in on_disk:
            where.append(".jsonl цел")
        print(f"    {t}  {' / '.join(where) or '— нигде'}")

    print("\nсписок, как его видит приложение (свежие сверху):")
    rows.sort(key=lambda r: r.get("lastFocusedAt") or r.get("lastActivityAt") or 0,
              reverse=True)
    for rec in rows:
        flag = []
        if rec.get("isArchived"):
            flag.append("архив")
        if rec.get("cliSessionId") not in on_disk:
            flag.append("НЕТ ФАЙЛА")
        folder = os.path.basename((rec.get("cwd") or "").rstrip("\\/"))
        print(f"  {rec.get('title', '')[:44]:44} | {rec.get('titleSource', ''):6} "
              f"| {folder:22} | {' '.join(flag)}")


if __name__ == "__main__":
    main()
