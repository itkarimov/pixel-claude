# -*- coding: utf-8 -*-
"""
Список сессий — ровно тот, что в сайдбаре приложения Claude.

Приложение ведёт свой список обычными json-файлами, по одному на сессию:

    %APPDATA%/Claude/claude-code-sessions/<аккаунт>/<устройство>/
        local_<uuid>.json     запись о сессии, поле cliSessionId — имя файла
                              транскрипта в ~/.claude/projects/<проект>/
        deleted_<uuid>        метка удаления; <uuid> — тот самый cliSessionId
        scheduled-tasks.json  задачи по расписанию, не сессии

Отсюда два важных следствия, ради которых всё и переписано:

  * Удаляя сессию в приложении, вы **не удаляете файл транскрипта** — уходит
    только запись реестра и появляется метка. Поэтому список нельзя строить по
    файлам в ~/.claude/projects: удалённое оттуда никуда не денется. По реестру —
    денется само, без всякой синхронизации.
  * Заголовок лежит прямо в записи, и хвост многомегабайтного транскрипта ради
    строки `custom-title` вычитывать больше не надо. Раньше на это уходили
    секунды при запуске.

Что приложение в сайдбаре не показывает и мы тоже не показываем: архив
(`isArchived`) и прогоны по расписанию (`scheduledTaskId` — их бывает десяток
с одинаковым названием). Записи без транскрипта на диске пропускаем: открыть
такую всё равно нечем.

Если приложения Claude на машине нет, реестра тоже нет — тогда работает запасной
путь: сканирование файлов проектов, как было раньше.
"""
import glob
import json
import os
import re
import time
from collections import deque
from datetime import datetime

REMINDER_RE = re.compile(r"\s*\[оболочка\].*", re.S)
EMOTION_RE = re.compile(r"^\s*\[\w+\]\s*")

HEAD_LINES = 400          # сколько строк с начала читать ради cwd и первой реплики
TAIL_WINDOWS = (262_144, 2_097_152, 16_777_216)   # окна поиска custom-title с конца

SCAN_CAP = 800            # потолок обхода в запасном пути
SKIP_DIRS = ("claude-mem-observer-sessions",)     # фоновые агенты памяти, не наши сессии

DELETED = "deleted_"


def projects_root():
    return os.path.join(os.path.expanduser("~"), ".claude", "projects")


def registry_root():
    """Реестр сайдбара приложения. Пусто, если приложение не установлено."""
    return os.path.join(os.environ.get("APPDATA", ""), "Claude", "claude-code-sessions")


def project_dir(workdir):
    """
    claude кодирует путь проекта, заменяя дефисом любой не-буквенно-цифровой
    символ — не только разделители, но и точки с подчёркиваниями:
    C:\\Projects\\my_app  →  C--Projects-my-app
    """
    key = re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(workdir))
    return os.path.join(projects_root(), key)


def _text(message):
    content = (message or {}).get("content")
    if isinstance(content, list):
        content = " ".join(b.get("text", "") for b in content
                           if isinstance(b, dict) and b.get("type") == "text")
    return content if isinstance(content, str) else ""


def _clean(text):
    """Убирает служебную приписку оболочки и тег эмоции."""
    text = REMINDER_RE.sub("", text or "")
    return " ".join(EMOTION_RE.sub("", text).split()).strip()


# ── список из реестра приложения ──────────────────────────────────────────────

def _transcripts():
    """{id сессии: путь к .jsonl} по всем проектам. Только имена, файлы не читаем."""
    found = {}
    for path in glob.glob(os.path.join(projects_root(), "*", "*.jsonl")):
        if any(d in path for d in SKIP_DIRS):
            continue
        found.setdefault(os.path.splitext(os.path.basename(path))[0], path)
    return found


def _registry():
    """Записи сайдбара и метки удаления. None, если реестра на машине нет."""
    root = registry_root()
    if not os.path.isdir(root):
        return None, set()

    rows = []
    for path in glob.glob(os.path.join(root, "*", "*", "local_*.json")):
        try:
            with open(path, encoding="utf-8") as fh:
                rec = json.load(fh)
        except (OSError, ValueError):
            continue                      # запись пишется прямо сейчас — пропустим
        if isinstance(rec, dict) and rec.get("cliSessionId"):
            rows.append(rec)

    tombs = {os.path.basename(p)[len(DELETED):]
             for p in glob.glob(os.path.join(root, "*", "*", DELETED + "*"))}
    return rows, tombs


def _from_registry(rows, tombs, limit, workdir, scheduled):
    files = _transcripts()
    out = []
    for rec in rows:
        sid = rec["cliSessionId"]
        path = files.get(sid)
        if not path or sid in tombs:
            continue                      # удалена в приложении или нечего открывать
        if rec.get("isArchived"):
            continue
        if rec.get("scheduledTaskId") and not scheduled:
            continue
        cwd = rec.get("cwd") or rec.get("originCwd") or ""
        if workdir and os.path.abspath(cwd) != os.path.abspath(workdir):
            continue
        title = (rec.get("title") or "без названия").strip()
        folder = os.path.basename(cwd.rstrip("\\/"))
        out.append({
            "id": sid,
            "path": path,
            "title": title,
            "cwd": cwd,
            "named": True,
            "label": f"{title} · {folder}" if folder else title,
            # порядок как в сайдбаре: приложение сортирует по последнему открытию
            "mtime": (rec.get("lastFocusedAt") or rec.get("lastActivityAt")
                      or rec.get("createdAt") or 0) / 1000.0,
        })
    out.sort(key=lambda it: it["mtime"], reverse=True)
    return out[:limit]


# ── запасной путь: сканирование файлов проектов ───────────────────────────────

def _scan_head(path):
    """Первая реплика и рабочая папка — они лежат в начале файла."""
    first, cwd = "", ""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for n, line in enumerate(fh):
                if n > HEAD_LINES:
                    break
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not cwd and ev.get("cwd"):
                    cwd = ev["cwd"]
                if not first and ev.get("type") == "user":
                    first = _clean(_text(ev.get("message")))
                if first and cwd:
                    break
    except OSError:
        pass
    return first, cwd


def _scan_tail(path):
    """
    Заголовок и рабочая папка из хвоста файла. Заголовок пишется многократно —
    берём последний. Читаем только хвост: файл активной сессии бывает на
    мегабайты, а разбирать его целиком ради одной строки незачем.
    """
    title, cwd = "", ""
    try:
        size = os.path.getsize(path)
    except OSError:
        return title, cwd

    # окно наращиваем: в живой сессии на мегабайты заголовок может лежать
    # далеко от конца, и на коротком хвосте она пропадала из списка
    for window in TAIL_WINDOWS:
        try:
            with open(path, "rb") as fh:
                fh.seek(max(0, size - window))
                chunk = fh.read().decode("utf-8", "replace")
        except OSError:
            return title, cwd
        for line in chunk.splitlines():
            if '"cwd"' not in line and '"custom-title"' not in line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "custom-title" and ev.get("customTitle"):
                title = ev["customTitle"]
            elif ev.get("cwd"):
                cwd = ev["cwd"]
        if title or window >= size:
            break
    return title, cwd


def _from_files(limit, workdir):
    """Как было до реестра: именованные сессии, заголовок из хвоста транскрипта."""
    pattern = os.path.join(project_dir(workdir) if workdir else projects_root(),
                           "*.jsonl" if workdir else os.path.join("*", "*.jsonl"))
    try:
        paths = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    except OSError:
        return []

    out = []
    for path in paths[:SCAN_CAP]:
        if len(out) >= limit:
            break
        if any(d in path for d in SKIP_DIRS):
            continue
        title, cwd = _scan_tail(path)
        if not title:
            continue
        folder = os.path.basename(cwd.rstrip("\\/")) if cwd else ""
        out.append({
            "id": os.path.splitext(os.path.basename(path))[0],
            "path": path,
            "title": title,
            "cwd": cwd,
            "named": True,
            "label": f"{title} · {folder}" if folder else title,
            "mtime": os.path.getmtime(path),
        })
    return out


def list_sessions(limit=40, workdir=None, scheduled=False):
    """
    Сессии, свежие сверху — те же и в том же порядке, что в сайдбаре приложения.

    Удалили сессию в приложении — она пропадёт и здесь при следующем чтении:
    список строится по реестру приложения, а не по файлам на диске.

    scheduled=True добавляет прогоны по расписанию (в сайдбаре их нет).
    """
    rows, tombs = _registry()
    if rows is None:
        return _from_files(limit, workdir)
    return _from_registry(rows, tombs, limit, workdir, scheduled)


def tail(path, limit=10):
    """Последние реплики сессии — чтобы лента не открывалась пустой."""
    if not path or not os.path.exists(path):
        return []
    keep = deque(maxlen=limit)
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") not in ("user", "assistant"):
                    continue
                text = _clean(_text(ev.get("message")))
                if text:
                    keep.append((ev["type"], text[:220]))
    except OSError:
        return []
    return list(keep)


def when(mtime):
    return datetime.fromtimestamp(mtime).strftime("%d.%m %H:%M")


# ── уборка за собой ───────────────────────────────────────────────────────────
#
# Сессии, заведённые не приложением, в сайдбаре не появляются: сайдбар — это
# отдельный реестр, и пишет в него только приложение. Такой разговор открыть
# потом нечем, а место он занимает: за один день набралось полтора десятка
# файлов на мегабайт с лишним.
#
# Основной чат их больше не плодит — без выбранной сессии claude не
# запускается вовсе. Но описатель камеры по устройству обязан быть отдельным
# коротким процессом, и за ним транскрипты остаются. Их и подчищаем.
#
# Трогаем только своё, и узнаём его по ПЕРВОЙ реплике: у чата это приписка
# оболочки, у описателя — его единственный вопрос. Сессия, заведённая руками
# из терминала, такой реплики не имеет и остаётся на месте. Чужое не трогаем,
# даже если его нет в сайдбаре.

PIXEL_MARK = "[оболочка]"
WATCH_ASK = "Что видно сейчас? Одной фразой."


def trash_root():
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "sessions_trash")


def _first_user(path, limit=120):
    """Первая реплика человека в транскрипте. Читаем только начало файла."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for _ in range(limit):
                line = fh.readline()
                if not line:
                    break
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") != "user":
                    continue
                text = _text(ev.get("message"))
                if text.strip():
                    return text
    except OSError:
        pass
    return ""


def is_ours(path):
    """Наш ли это транскрипт — чата оболочки или описателя камеры."""
    first = _first_user(path)
    return bool(first) and (PIXEL_MARK in first or first.strip() == WATCH_ASK)


def orphans(keep=()):
    """Наши транскрипты, которых нет в сайдбаре. keep — то, что сейчас открыто."""
    rows, _ = _registry()
    if rows is None:
        return []                      # без реестра сравнивать не с чем
    known = {r["cliSessionId"] for r in rows if r.get("cliSessionId")}
    known.update(keep)
    out = []
    for path in glob.glob(os.path.join(projects_root(), "*", "*.jsonl")):
        if any(d in path for d in SKIP_DIRS):
            continue
        if os.path.splitext(os.path.basename(path))[0] in known:
            continue
        if is_ours(path):
            out.append(path)
    return out


def sweep(keep=(), days=7):
    """
    Убрать наши сессии-сироты в sessions_trash, а старое из корзины — удалить.

    Через корзину, а не сразу насовсем: правило «что не в сайдбаре — наше»
    держится на разборе первой реплики, и ошибиться в нём можно. Неделя даёт
    время заметить пропажу.
    """
    moved = 0
    for path in orphans(keep):
        folder = os.path.join(trash_root(), os.path.basename(os.path.dirname(path)))
        try:
            os.makedirs(folder, exist_ok=True)
            target = os.path.join(folder, os.path.basename(path))
            if os.path.exists(target):
                os.remove(target)
            os.replace(path, target)
            moved += 1
        except OSError:
            continue
    edge = time.time() - days * 86400
    for old in glob.glob(os.path.join(trash_root(), "*", "*.jsonl")):
        try:
            if os.path.getmtime(old) < edge:
                os.remove(old)
        except OSError:
            continue
    return moved
