# -*- coding: utf-8 -*-
"""
Свежие цифры по крипте с сервера — только чтение.

Зачем отдельная команда, а не просто разрешить помощнице ssh: разрешение
`Bash(ssh …)` открывает на сервере ЛЮБУЮ команду, включая разрушительную.
Голосом управляет whisper, который регулярно ослышивается, а режим у оболочки
acceptEdits — то есть подтверждать никто не будет. Поэтому наружу торчит одна
команда с зашитым набором действий, и разрешена в конфиге только она.

Читает три вещи (мастер-состояние живёт на сервере, локальные файлы устарели):
    ~/trader/positions.json   позиции
    ~/trader/cron.log         жив ли крон механики
    ~/trader/trade_log.txt    последние сделки

Ничего не пишет и не отправляет ордера.

    python C:/PixelClaude/tools/crypto_report.py
"""
import subprocess
import sys

HOST = "mrpatruy_ildar@mrpatruy.beget.tech"
# Одна ssh-сессия на всё: каждое соединение с Beget стоит несколько секунд.
REMOTE = (
    "echo '=== positions.json ==='; cat ~/trader/positions.json 2>/dev/null; "
    "echo; echo '=== cron.log, последние 15 строк ==='; "
    "tail -n 15 ~/trader/cron.log 2>/dev/null; "
    "echo; echo '=== trade_log.txt, последние 15 строк ==='; "
    "tail -n 15 ~/trader/trade_log.txt 2>/dev/null; "
    # часы бота бишкекские, сервер Beget живёт по Москве — показываем Бишкек
    "echo; echo '=== время на сервере ==='; TZ=Asia/Bishkek date"
)


def main():
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=25",
           "-o", "StrictHostKeyChecking=accept-new", HOST, REMOTE]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=90)
    except FileNotFoundError:
        print("ssh не найден в PATH")
        return 1
    except subprocess.TimeoutExpired:
        print("сервер не ответил за 90 секунд")
        return 1

    if p.stdout.strip():
        print(p.stdout.strip())
    if p.returncode != 0:
        err = (p.stderr or "").strip().splitlines()
        print("\nssh вернул ошибку:", err[-1] if err else p.returncode)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
