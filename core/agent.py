# -*- coding: utf-8 -*-
"""
Мост к claude CLI: один долгоживущий процесс на всю сессию.

Почему не процесс на реплику (как было сначала): замеры показали 15.6 с на ход,
из которых 13 — накладные. Плюс загрузка MCP-серверов раздувала первый ход
до 35 с. Отсюда долгоживущий процесс (`--input-format stream-json`) и
отключённые MCP-серверы.

Но и после этого ход занимал 5.9 с до первой фразы. Причина нашлась замером
(tools/bench_modes.py): хук claude-mem `UserPromptSubmit` запускает node на
КАЖДУЮ реплику и стоит 1.5–2.5 с, а вместе с `Stop` и `PostToolUse` — 3.7 с
на ход. Долгоживущий процесс тут не спасает: хук на то и хук, что срабатывает
каждый раз.

    как было                      5.90 с до первой фразы, старт 9.24 с
    --setting-sources project,local  2.19 с,                старт 2.46 с
    --safe-mode                      2.15 с,                старт 2.33 с

По умолчанию берём первый вариант: он снимает пользовательские настройки
(там и лежат хуки), но оставляет CLAUDE.md и правила проекта — оболочке они
нужны, она же работает в проекте. Флаг --bare, который выглядит ещё быстрее,
не годится: он вообще не логинится и падает за 0.05 с с «Not logged in», что
в замере легко принять за ускорение.
"""
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

from PySide6.QtCore import QObject, Signal

# ВАЖНО: на Windows claude — это .CMD-обёртка, и перенос строки внутри аргумента
# обрывает командную строку, отрезая все последующие флаги. Поэтому системная
# приписка строго однострочная, а реплики уходят в stdin.
SYSTEM_APPEND = (
    "Ты — девушка, голосовая помощница в пиксельной оболочке, твой ответ "
    "озвучивается вслух женским голосом. "
    "ПРАВИЛА ФИНАЛЬНОГО ОТВЕТА: "
    "0) говори о себе В ЖЕНСКОМ РОДЕ — «посмотрела», «нашла», «готова», «я сама», "
    "«помощница»; никогда «посмотрел», «готов», «помощник», «ассистент»; "
    "1) по-русски, одна-две фразы, живой речью; "
    "2) никакого markdown — ни списков, ни заголовков, ни обратных кавычек, "
    "ни звёздочек, ни блоков кода; "
    "3) первым символом — тег эмоции в квадратных скобках; "
    "4) [done] — задача выполнена, [think] — размышляю или нужны уточнения, "
    "[unhappy] — не вышло, [angry] — сломалось всерьёз, [puzzled] — не поняла "
    "просьбу, [relief] — обошлось, [excited] — вышло здорово, [sly] — с хитрецой. "
    "Пример правильного ответа: [done] Готово, поправила три файла в core. "
    "ЕСЛИ К РЕПЛИКЕ ПРИЛОЖЕН КАДР С КАМЕРЫ: это то, что ты видишь прямо сейчас "
    "своими глазами, а не присланный файл — так про него и говори. Не описывай "
    "кадр без просьбы, просто учитывай, что видишь. "
    "ЕСЛИ В РЕПЛИКЕ ЕСТЬ СТРОКА «[вижу] …»: это то же самое зрение, только "
    "словами — что сейчас в камере. Кадра при этом нет, но видишь ты именно это. "
    "Если строка помечена «(было N с назад)» — картинка могла устареть, и когда "
    "человек спрашивает про сиюминутное, честнее сказать, что нужен свежий взгляд. "
    "ЕСЛИ В РЕПЛИКЕ ЕСТЬ СТРОКА «[камера выключена]»: ты СЕЙЧАС НЕ ВИДИШЬ — камеру "
    "выключили. Кадры выше в разговоре устарели, пересказывать их как «вижу прямо "
    "сейчас» нельзя. Спросят, видишь ли ты, — отвечай, что глаза выключены и их "
    "надо включить кнопкой ГЛАЗА. "
    "У ТЕБЯ ЕСТЬ ПОИСК В ИНТЕРНЕТЕ (WebSearch) И ЧТЕНИЕ СТРАНИЦ (WebFetch): "
    "если спрашивают про новости, курсы, погоду, цены или что-то свежее — ищи, "
    "а не отвечай «не знаю» и не пересказывай память. Назови источник коротко. "
    "ЕСЛИ ЗАДАЧА ТРЕБУЕТ РАБОТЫ инструментами: СНАЧАЛА скажи одну короткую фразу "
    "с тегом — «[think] Сейчас посмотрю» — и только потом берись за инструменты, "
    "иначе человек сидит в тишине и думает, что оболочка зависла. В конце — "
    "вторая фраза с результатом."
)

EMOTIONS = {"neutral", "smile", "happy", "laugh", "surprised", "puzzled",
            "think", "sad", "crying", "angry", "unhappy", "shy", "love",
            "tired", "sly", "excited", "relief", "victory", "done"}

REMINDER = ("\n\n[оболочка] Говори живой речью, без markdown, без обратных "
            "кавычек и списков — текст пойдёт в озвучку. "
            "ДЛИНА: по умолчанию коротко, одна-две фразы. Просят объяснить, "
            "рассказать, разобрать, перечислить или спрашивают «подробнее» — "
            "говори столько, сколько нужно, и не комкай: человек может "
            "перебить в любой момент. "
            "О себе — в женском роде: посмотрела, сделала, готова. "
            "Если собираешься работать инструментами — сперва короткая фраза "
            "вслух, потом работа. "
            "Первым символом — тег эмоции, один из: "
            + " ".join(f"[{e}]" for e in sorted(EMOTIONS)))

TOOL_RU = {
    "Read": "смотрю", "Write": "пишу", "Edit": "правлю", "NotebookEdit": "правлю",
    "Bash": "запускаю", "Grep": "ищу", "Glob": "ищу файлы", "WebFetch": "открываю",
    "WebSearch": "гуглю", "TodoWrite": "обновляю план", "Task": "поднимаю агента",
    "TaskCreate": "ставлю задачу", "SlashCommand": "выполняю команду",
}


# Имена, которые в открытой папке читать нельзя. Прочитанное уходит в модель и
# звучит вслух: ключ, один раз попавший в разговор, оттуда уже не убрать.
SECRET_GLOBS = (".env", ".env.*", "*.env", "*key*", "*.pem", "*.p12", "*.pfx",
                ".mcp.json", "credentials*", ".credentials*", "token*.json",
                "*secret*", "*password*", "*passwd*", "id_rsa*", "id_ed25519*",
                "id_ecdsa*", "*.ppk", "*.kdbx", "*.jks", "*.keystore",
                ".npmrc", ".pypirc", ".netrc", ".htpasswd", "*.ovpn")

# Папки, которые не читаем целиком: там живут чужие пароли, куки и сессии.
# Имена внутри них ничего не говорят о содержимом, масками их не поймать —
# закрываем по папке.
SECRET_DIRS = (".ssh", ".claude", ".claude-mem", ".aws", ".gnupg", ".azure",
               ".kube", ".docker", ".android", ".config/gh", ".vscode",
               "AppData", "Cookies", "Local Settings")

# Команды, которыми можно стереть или подвинуть файл мимо инструментов Write и
# Edit. Запрет на них не абсолютен — всё, что запускает код, в принципе может
# что угодно, — но он закрывает главный здешний риск: ослышавшееся
# распознавание речи и режим acceptEdits, который ничего не переспросит.
BASH_DESTRUCTIVE = ("rm", "rmdir", "del", "erase", "unlink", "shred", "truncate",
                    "mv", "move", "ren", "rename", "cp", "copy", "xcopy",
                    "robocopy", "dd", "fsutil", "format", "diskpart", "takeown",
                    "icacls", "attrib", "Remove-Item", "Move-Item",
                    "Rename-Item", "Clear-Content", "Set-Content",
                    "Add-Content", "Out-File", "ri", "rni", "mi", "sc",
                    "ac", "rd")


def _deny_path(path):
    """
    Путь в том виде, в каком его понимают запреты claude.

    Формат выяснен замером (tools/deny_test.py), и он неочевиден:
    относительные маски вида `Read(**/*deploy_key*)` **не работают вовсе** —
    с ними claude спокойно печатает приватный ключ. Срабатывает только
    абсолютный путь в форме `//c/Trading/…`.
    """
    full = os.path.abspath(os.path.expanduser(path))
    drive, rest = os.path.splitdrive(full)
    return ("//" + drive.rstrip(":").lower() + rest.replace("\\", "/")).rstrip("/")


# Команды оболочки, которые ей разрешено запускать. Имя файла → для чего.
HELPERS = {
    "crypto_report.py": "отчёт торгового бота",
}

# Свой MCP-сервер: открыть что-нибудь и найти закладку. Нужен не только ради
# безопасности: путь там едет аргументом в JSON, а не строкой для оболочки.
MCP_TOOLS = ("mcp__pixel__open", "mcp__pixel__bookmarks_find")

# Открыть окно проводника. Разрешено под обоими именами и во всех трёх видах
# записи правила: инструмент оболочки здесь зовётся то Bash, то PowerShell, а
# шаблон аргументов отделяется то пробелом, то двоеточием — какая пара сойдётся,
# заранее не скажешь, а лишнее правило ничего не стоит.
WINDOW = ("explorer.exe", "explorer")

# Этим запускают что угодно, не только окно, — закрыто.
LAUNCHERS = ("start", "Start-Process", "Invoke-Item", "ii", "rundll32",
             "wscript", "cscript", "mshta")

def shell_rule(command):
    """
    Правило на команду оболочки. Решает здесь имя инструмента.

    Замер (tools/tools_check.py --forms, 12.11.2026), команда
    `python C:/PixelClaude/tools/echo_probe.py`:

        Bash(… :*) / Bash(… *) / Bash(…)              отказ
        PowerShell(… :*) / PowerShell(… *) / (…)      прошло

    То есть инструмент оболочки на этой машине — PowerShell, и правило к нему
    прилипает в любой из трёх форм. Двоеточие в пути не мешает: прежний вывод,
    будто оно рвёт разбор, родился из запуска, в котором файла разрешений не
    было вовсе (см. find_claude). Bash оставляем на случай, если инструмент
    всё же окажется им: лишняя строка в сгенерированном файле ничего не стоит,
    а молчаливый отказ стоит дорого.
    """
    return [f"PowerShell({command}:*)", f"PowerShell({command})",
            f"Bash({command}:*)", f"Bash({command})"]


def tool_command(script, workdir):
    """
    Команда запуска утилиты — абсолютным путём.

    Раньше путь считался от рабочей папки, чтобы в нём не было двоеточия: его
    подозревали в том, что оно рвёт правило разрешения. Подозрение не
    подтвердилось (см. shell_rule), а относительный путь ломался, стоило
    рабочей папке оказаться на другом диске.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return "python " + os.path.join(root, "tools", script).replace("\\", "/")


def mcp_config():
    """
    Описание своего сервера для --mcp-config.

    pythonw вместо python намеренно: сервер поднимает сам claude, наши флаги
    «без окна» на него не распространяются, и обычный интерпретатор мигнул бы
    чёрным окном консоли на каждый запуск.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    quiet = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    runner = quiet if os.path.isfile(quiet) else sys.executable
    return {"mcpServers": {"pixel": {
        "command": runner,
        "args": [os.path.join(root, "tools", "mcp_pixel.py")],
    }}}


def tool_rules(workdir):
    """Разрешения на эти утилиты и подсказка про них одним куском."""
    rules, lines = list(MCP_TOOLS), []
    for name in WINDOW:
        rules += shell_rule(name)
    for script, what in HELPERS.items():
        command = tool_command(script, workdir)
        rules += shell_rule(command)
        lines.append(f"Отчёт по крипте — командой {command}.")
    hint = (
        "\n\n[оболочка] ЧЕМ ОТКРЫВАТЬ ПАПКИ, ФАЙЛЫ И САЙТЫ. "
        "Лучше всего — инструментом mcp__pixel__open: он понимает путь, "
        "адрес, название закладки и имя браузера, и ему всё равно, как "
        "записан путь. Закладки ищет mcp__pixel__bookmarks_find. "
        "Из оболочки открывает только explorer.exe, путь в кавычках: "
        "explorer.exe \"C:/Users/dakak/Downloads\". start, Start-Process, "
        "Invoke-Item, rundll32 и webbrowser закрыты наглухо — не пробуй. "
        "Если пришло «requires approval», команду не разрешили: возьми "
        "mcp__pixel__open и не проси человека подтвердить — подтверждать "
        "здесь нечем и некому, окна с кнопкой нет. "
        + " ".join(lines)
    )
    return rules, hint


def deny_rules(dirs):
    """
    Чем закрыта каждая открытая папка: секреты не читаем, файлы не трогаем.

    Папки из extra_dirs открыты только на чтение. Рабочая папка живёт по другим
    правилам — в ней она и пишет, и правит, это её работа.

    Правило пишется и для самой папки, и для вложенных: одно без другого ловит
    половину случаев.
    """
    rules = []
    for path in dirs:
        base = _deny_path(path)
        for glob in SECRET_GLOBS:
            rules.append(f"Read({base}/{glob})")
            rules.append(f"Read({base}/**/{glob})")
        for folder in SECRET_DIRS:
            rules.append(f"Read({base}/{folder})")
            rules.append(f"Read({base}/{folder}/**)")
        for tool in ("Write", "Edit", "NotebookEdit"):
            rules.append(f"{tool}({base})")
            rules.append(f"{tool}({base}/**)")
    for verb in tuple(BASH_DESTRUCTIVE) + LAUNCHERS:
        rules += shell_rule(verb)
    return rules


def _short(value, limit=58):
    text = str(value).replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


# Частые отказы claude CLI приходят по-английски и ничего не говорят о том, что
# делать. Переводим их в одну строку с командой, которую человек может набрать.
TROUBLE = (
    (re.compile(r"oauth|authenticat|unauthorized|\b401\b|/login", re.I),
     "мозг Claude разлогинен — в терминале набери «claude auth login». "
     "Пока можно переключиться кнопкой МОЗГ на Meta AI или Groq"),
    (re.compile(r"rate.?limit|usage limit|\b429\b", re.I),
     "у Claude кончился лимит — переключись кнопкой МОЗГ на Meta AI или Groq"),
    (re.compile(r"ENOTFOUND|ECONNREFUSED|ETIMEDOUT|network", re.I),
     "claude не достучался до сети — проверь интернет"),
    # Подтвердить команду негде: окна с кнопкой «Разрешить» у голосовой
    # оболочки нет. Значит, разрешать надо заранее, в config.json.
    (re.compile(r"requires approval|permission|not allowed|denied", re.I),
     "команда не разрешена — впиши её в allowed_tools в config.json "
     "и перезапусти окно"),
)


def explain(err):
    """Что показать человеку вместо английского отказа CLI."""
    text = str(err or "")
    for pattern, hint in TROUBLE:
        if pattern.search(text):
            return hint
    return _short(text, 200) or "claude завершился молча"


def describe_tool(name, params):
    """Человекочитаемая строка «что я сейчас делаю»."""
    verb = TOOL_RU.get(name, name)
    p = params or {}
    for key in ("file_path", "notebook_path", "path"):
        if p.get(key):
            return f"{verb} {os.path.basename(str(p[key]))}"
    if p.get("command"):
        return f"{verb}: {_short(p['command'])}"
    if p.get("pattern"):
        return f"{verb}: {_short(p['pattern'], 34)}"
    for key in ("url", "query", "prompt", "description"):
        if p.get(key):
            return f"{verb}: {_short(p[key], 44)}"
    return verb


def split_emotion(text):
    """Отрезает ведущий тег эмоции: '[done] Готово' → ('done', 'Готово')."""
    m = re.match(r"\s*\[(\w+)\]\s*", text or "")
    if m and m.group(1).lower() in EMOTIONS:
        return m.group(1).lower(), text[m.end():].strip()
    return None, (text or "").strip()


def kill_tree(proc):
    """
    Прибить процесс вместе с потомками.

    На Windows claude запускается через claude.cmd: Popen держит обёртку
    cmd.exe, а настоящий claude.exe и его node — уже внуки. terminate()
    снимает только обёртку, внуки остаются жить и висят в памяти до
    перезагрузки. Так в диспетчере задач и набрался двадцать один claude по
    60–150 МБ: каждая смена сессии оставляла по копии. taskkill /T убирает
    дерево целиком.
    """
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.stdin.close()
    except (OSError, ValueError, AttributeError):
        pass
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)
            return
        except OSError:
            pass
    try:
        proc.terminate()
    except OSError:
        pass


STRAY_MARK = "помощниц"      # кусок нашей системной приписки, у чата и описателя


def find_claude():
    """
    Путь к claude — именно к .exe, а не к .cmd. Разница тут решает всё.

    npm кладёт в PATH обёртку claude.cmd, и та зовёт настоящий exe строкой
    `"%dp0%\\…\\claude.exe"   %*`. Popen на .cmd поднимает cmd.exe, тот
    раскрывает %*, и ПЕРЕВОД СТРОКИ внутри аргумента обрывает команду ровно на
    этом месте: остаток строки для cmd — отдельная команда, и она молча
    пропадает.

    Системная приписка у нас многострочная. Значит, всё, что идёт после неё, —
    --setting-sources, --mcp-config, --add-dir, --settings, --permission-mode,
    --model и --resume — до claude не доезжало вообще. Снаружи это выглядело
    как три разные поломки: разрешения не срабатывают (файла настроек не
    было), свои инструменты не видны (MCP-сервер не поднимался) и после
    каждого запуска появляется новая сессия (--resume терялся). Замеры по
    синтаксису правил, которыми мы объясняли первое, меряли пустоту.

    Прямой запуск exe обходит cmd.exe целиком: аргументы уходят процессу как
    есть, с переводами строк внутри.
    """
    direct = shutil.which("claude.exe")
    if direct:
        return direct
    wrapper = shutil.which("claude") or shutil.which("claude.cmd")
    if not wrapper:
        return None
    # у обёртки рядом лежит node_modules с настоящим exe — её же путь и подскажет
    exe = os.path.join(os.path.dirname(wrapper), "node_modules",
                       "@anthropic-ai", "claude-code", "bin", "claude.exe")
    return exe if os.path.isfile(exe) else wrapper


def kill_strays():
    """
    Снять claude-процессы, оставшиеся от прошлого запуска. Возвращает сколько.

    Зовётся один раз при старте, до того как поднимется свой. Своих узнаём по
    куску системной приписки в командной строке: у процессов приложения Claude
    её нет вовсе, проверено — из сорока живых под неё попали ровно наши.

    Через PowerShell, потому что командная строка чужого процесса из питона без
    psutil никак не достаётся. Команда уходит в -EncodedCommand (UTF-16), иначе
    русская буква в ней по дороге превращается в вопросительные знаки.
    """
    if os.name != "nt":
        return 0
    script = ("Get-CimInstance Win32_Process -Filter \"Name='claude.exe'\" | "
              "Where-Object { $_.CommandLine -like '*" + STRAY_MARK + "*' } | "
              "ForEach-Object { $_.ProcessId }")
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand",
             base64.b64encode(script.encode("utf-16-le")).decode()],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return 0
    killed = 0
    for line in (out.stdout or "").split():
        if not line.strip().isdigit():
            continue
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", line.strip()],
                           capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)
            killed += 1
        except OSError:
            pass
    return killed


class AgentRunner(QObject):
    """Долгоживущий процесс claude и разбор его потока событий."""

    log = Signal(str, str)      # (вид, текст): user | assistant | tool | system | error
    speak = Signal(str)
    emotion = Signal(str)
    busy = Signal(bool)
    session = Signal(str)
    ready = Signal(bool)        # процесс поднят и готов принимать реплики

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.session_id = None
        self.workdir = cfg.get("workdir") or None
        self.claude = find_claude()
        self._proc = None
        self._starting = False   # процесс уже поднимается — второй не нужен
        self._lock = threading.Lock()
        self._buf = ""           # текст сообщения, копится по кускам
        self._said = ""          # что уже озвучено из текущего сообщения
        self._last_msg = ""      # последнее озвученное сообщение целиком
        self._emo = None         # какая эмоция уже показана в этом сообщении
        self._busy = False
        self._pending = None     # реплика, сказанная до конца хода

    # ── публичное API ─────────────────────────────────────────────────────
    def available(self):
        return bool(self.claude)

    def alive(self):
        return self._proc is not None and self._proc.poll() is None

    def _claim(self):
        """Право поднять процесс. Второму потоку оно не достанется."""
        with self._lock:
            if self._starting or (self._proc is not None
                                  and self._proc.poll() is None):
                return False
            self._starting = True
            return True

    def start(self):
        """Поднять процесс заранее, чтобы первая реплика не ждала инициализации."""
        if not self.claude or not self.session_id:
            return
        if not self._claim():
            return
        threading.Thread(target=self._spawn, daemon=True).start()

    def send(self, text, image=None, display=None):
        """
        image — кадр с камеры в JPEG, если зрение включено.
        display — что показать в ленте вместо text: в реплику подмешивается
        служебное («[вижу] …»), а человеку в ленте нужны только его слова.
        """
        text = (text or "").strip()
        if not text:
            return
        if not self.claude:
            self.log.emit("error", "claude CLI не найден в PATH")
            return
        if not self.session_id:
            self.log.emit("user", (display or text).strip())
            self.log.emit("error", "сессия не выбрана — выбери её в списке сверху")
            return
        self.log.emit("user", (display or text).strip())
        if self._busy:
            # текст озвучивается раньше, чем ход официально закрыт: человек уже
            # услышал ответ и говорит дальше. Не отфутболиваем — придерживаем.
            self._pending = (text, image)
            self.log.emit("system", "приняла, отвечу следом")
            return
        self._write(text, image)

    def _write(self, text, image=None):
        if not self.alive():                      # процесс умер или ещё не поднят
            self.log.emit("system", "поднимаю claude…")
            if self._claim():
                self._spawn(wait=True)
            else:
                # поднимают в другом потоке — ждём его, а не заводим второй
                for _ in range(100):
                    time.sleep(0.05)
                    if self.alive():
                        break
            if not self.alive():
                return

        self._buf = self._said = self._last_msg = ""
        self._emo = None
        self._set_busy(True)
        self.emotion.emit("think")

        body = text + REMINDER
        if image:
            # кадр уходит блоком прямо в потоке. Через файл на диске тоже
            # работает, но модель тратит лишний ход на чтение — замер показал
            # 8.7 с против 7.0 с, полторы секунды на ровном месте.
            content = [
                {"type": "image",
                 "source": {"type": "base64", "media_type": "image/jpeg",
                            "data": base64.b64encode(image).decode()}},
                {"type": "text", "text": body},
            ]
        else:
            content = body

        payload = json.dumps({"type": "user",
                              "message": {"role": "user", "content": content}},
                             ensure_ascii=False)
        try:
            self._proc.stdin.write(payload + "\n")
            self._proc.stdin.flush()
        except OSError as exc:
            self._set_busy(False)
            self.emotion.emit("unhappy")
            self.log.emit("error", f"не смогла передать реплику: {exc}")

    def set_session(self, session_id, workdir=None):
        """Подхватить существующую сессию — процесс перезапускается с --resume."""
        self.session_id = session_id or None
        if workdir and os.path.isdir(workdir):
            self.workdir = workdir
        self.restart()

    def reset(self):
        """
        Новых сессий оболочка не заводит.

        Созданная отсюда сессия в сайдбаре приложения не появляется: сайдбар —
        отдельный реестр, пишет в него только само приложение. Снаружи
        получался разговор, которого в списке нет и открыть его нечем.
        Поэтому работаем только в том, что в сайдбаре уже есть.
        """
        self.log.emit("error", "новую сессию не создаю — выбери из списка")

    def restart(self):
        self.stop()
        self.start()

    def stop(self):
        with self._lock:
            proc, self._proc = self._proc, None
        kill_tree(proc)
        self._set_busy(False)
        self.ready.emit(False)

    # ── процесс ───────────────────────────────────────────────────────────
    def _build_cmd(self):
        cmd = [self.claude, "-p",
               "--input-format", "stream-json",
               "--output-format", "stream-json", "--verbose",
               "--append-system-prompt", SYSTEM_APPEND + tool_rules(self.workdir)[1]]

        # хуки — главный пожиратель времени, 3.7 с на ход (tools/bench_modes.py)
        mode = self.cfg.get("hooks_mode", "skip_user")
        if mode == "skip_user":
            # снимаем пользовательские настройки (в них хуки), оставляем проект
            cmd += ["--setting-sources", "project,local"]
        elif mode == "safe":
            # совсем без надстроек: ни хуков, ни плагинов, ни CLAUDE.md
            cmd += ["--safe-mode"]
        # mode == "keep" — ничего не трогаем, хуки работают как обычно

        if self.cfg.get("stream_partial", True):
            # текст по кускам: можно начать говорить с первой готовой фразы,
            # не дожидаясь, пока модель допишет остальное
            cmd += ["--include-partial-messages"]
        if mode != "safe":
            # Чужие MCP-серверы не поднимаем: первый ход с ними занимал 35 с
            # вместо 12. Свой — поднимаем всегда, через него она открывает
            # папки и ищет закладки, командой оболочки это неразрешимо.
            config = mcp_config() if self.cfg.get("pixel_tools", True) else {
                "mcpServers": {}}
            cmd += ["--strict-mcp-config", "--mcp-config", json.dumps(config)]
        # Разрешения приходили из пользовательских настроек, а мы их снимаем
        # ради хуков — значит, нужное разрешаем флагом. Поиск в вебе оболочке
        # нужен: спросить голосом «что там с курсом» и услышать «не могу» —
        # это не помощница.
        # Папки за пределами рабочей: без них она честно отвечает «доступа нет».
        # Существующие проверяем сами — на несуществующей CLI падает при старте,
        # а выглядит это как «оболочка не поднялась».
        extra = [d for d in (self.cfg.get("extra_dirs") or [])
                 if os.path.isdir(os.path.expanduser(d))]
        if extra:
            cmd += ["--add-dir"] + extra
        # Разрешения и запреты идут файлом, а не флагами: на десяток открытых
        # папок правил набирается под тысячу, а командная строка Windows
        # обрывается на 32 тысячах символов — и обрывается молча.
        cmd += ["--settings", self._write_settings(extra)]
        if self.cfg.get("permission_mode"):
            cmd += ["--permission-mode", self.cfg["permission_mode"]]
        if self.cfg.get("model"):
            cmd += ["--model", self.cfg["model"]]
        if self.session_id:
            cmd += ["--resume", self.session_id]
        return cmd

    def _write_settings(self, extra):
        """
        Файл разрешений для CLI. Пересобирается на каждый запуск процесса.

        Запреты сильнее разрешений, и секреты в открытых папках закрываются
        сами, не полагаясь на конфиг: открыли папку — защита появилась вместе
        с ней. Поэтому список собирается здесь, а не переписывается руками.
        """
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(root, ".pixel_settings.json")
        rules, _ = tool_rules(self.workdir)
        data = {"permissions": {
            "deny": deny_rules(extra) + list(self.cfg.get("denied_tools") or []),
            "allow": rules + list(self.cfg.get("allowed_tools") or []),
        }}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return path

    def _spawn(self, wait=False):
        """
        Поднять процесс. Право на это берётся через _claim() — иначе два
        потока заведут две копии claude, и обе будут жить.

        Без выбранной сессии не запускаемся вовсе: claude без --resume заводит
        новую сессию, в сайдбаре приложения её не видно, и после неё остаётся
        мусорный транскрипт. Оболочка работает только в том разговоре, который
        выбран в списке.
        """
        if not self.session_id:
            with self._lock:
                self._starting = False
            self.log.emit("error", "сессия не выбрана — claude не запускаю")
            return
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            proc = subprocess.Popen(
                self._build_cmd(), cwd=self.workdir,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding="utf-8",
                errors="replace", bufsize=1, creationflags=flags)
        except Exception as exc:
            with self._lock:
                self._starting = False
            self.log.emit("error", f"не смогла запустить claude: {exc}")
            return
        with self._lock:
            self._proc = proc
            self._starting = False
        self.ready.emit(True)
        target = self._read
        if wait:
            threading.Thread(target=target, args=(proc,), daemon=True).start()
        else:
            target(proc)

    def _read(self, proc):
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            self._handle(event)

        err = ""
        try:
            err = (proc.stderr.read() or "").strip()
        except (OSError, ValueError):
            pass
        if self._busy:
            self._set_busy(False)
            self.emotion.emit("unhappy")
            self.log.emit("error", explain(err))
        with self._lock:
            if self._proc is proc:
                self._proc = None
        self.ready.emit(False)

    def _set_busy(self, value):
        self._busy = value
        self.busy.emit(value)

    def _handle(self, event):
        etype = event.get("type")

        if etype == "system" and event.get("subtype") == "init":
            sid = event.get("session_id")
            if sid and sid != self.session_id:
                self.session_id = sid
                self.session.emit(sid)
            return

        if etype == "stream_event":
            self._partial(event.get("event") or {})
            return

        if etype == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                kind = block.get("type")
                if kind == "tool_use":
                    self.log.emit("tool", describe_tool(block.get("name"),
                                                        block.get("input")))
                elif kind == "text" and block.get("text", "").strip():
                    # говорим сразу, не дожидаясь result — это экономит ~3 с,
                    # а промежуточное «сейчас посмотрю» голосом звучит естественно
                    self._buf = block["text"]
                    self._flush(final=True)
            return

        if etype == "user":
            # Отказ инструмента приходит сюда и раньше нигде не показывался:
            # в ленте было пусто, а она пересказывала случившееся своими
            # словами. Теперь причина видна строкой.
            for block in (event.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_result" \
                        and block.get("is_error"):
                    self.log.emit("error", explain(block.get("content")))
            return

        if etype == "result":
            self._set_busy(False)
            if self._pending:                     # придержанная реплика — вперёд
                queued, self._pending = self._pending, None
                text, image = queued if isinstance(queued, tuple) else (queued, None)
                threading.Timer(0.1, lambda: self._write(text, image)).start()
                return
            if event.get("is_error"):
                self.emotion.emit("unhappy")
                self.log.emit("error", explain(event.get("result")))
                return
            text = (event.get("result") or "").strip()
            _, clean = split_emotion(text)
            if clean and clean != self._last_msg:    # финал отличается — договорим
                # в буфер кладём сырой текст: тег эмоции ещё пригодится
                self._buf, self._said, self._emo = text, "", None
                self._flush(final=True)

    # ── потоковый разбор ──────────────────────────────────────────────────
    def _partial(self, ev):
        """Куски текста по мере поступления — чтобы начать говорить раньше."""
        kind = ev.get("type")
        if kind == "message_start":
            self._buf, self._said, self._emo = "", "", None
            return
        if kind == "content_block_delta":
            piece = (ev.get("delta") or {}).get("text") or ""
            if piece:
                self._buf += piece
                self._flush()

    def _flush(self, final=False):
        """
        Отдаёт наружу то, что уже готово: эмоцию — как только распознан тег,
        текст — законченными предложениями. Дважды одно и то же не произносим:
        ведём учёт сказанного в self._said.

        Один и тот же ответ приходит от CLI трижды — кусками (stream_event),
        целым блоком (assistant) и финальным событием (result). Отсюда правило:
        _last_msg проставляется на КАЖДОМ финальном заходе, даже если говорить
        уже нечего. Иначе получалось так: фраза целиком отзвучала ещё на потоке,
        обработчик блока вышел раньше как «нового нет» и отметку не поставил,
        а result увидел пустую отметку и произнёс всё заново. Ответ из двух
        фраз при этом звучал трижды: каждая фраза отдельно и потом всё вместе.
        """
        emo, body = split_emotion(self._buf)
        if emo and emo != self._emo:
            self._emo = emo
            self.emotion.emit(emo)

        body = body.strip()
        if not body:
            return

        if final:
            ready = body
            self._last_msg = ready          # отметку ставим до всех выходов
            if not self._emo:
                self.emotion.emit("neutral")
        else:
            ends = list(re.finditer(r"[.!?…](?=\s|$)", body))
            if not ends:
                return
            ready = body[:ends[-1].end()].strip()

        if len(ready) <= len(self._said):
            return
        fresh = ready[len(self._said):].strip()
        if not fresh or (not final and len(fresh) < 10):
            return

        self._said = ready
        self.log.emit("assistant", fresh)
        self.speak.emit(fresh)
