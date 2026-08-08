# -*- coding: utf-8 -*-
"""
Список сессий — тот же, что в сайдбаре приложения Claude.

Приложение держит свой реестр в IndexedDB, но лезть туда не нужно: заголовок,
который вы задали в приложении, оно дублирует записью `custom-title` прямо в
файл CLI-сессии. Оттуда и берём.

    ~/.claude/projects/<путь проекта с дефисами>/<session_id>.jsonl

Сессии собираются по всем проектам сразу, у каждой своя рабочая папка (поле
`cwd` в записях) — выбрали сессию, оболочка переключилась туда же.
"""
import glob
import json
import os
import re
from collections import deque
from datetime import datetime

REMINDER_RE = re.compile(r"\s*\[оболочка\].*", re.S)
EMOTION_RE = re.compile(r"^\s*\[\w+\]\s*")

HEAD_LINES = 400          # сколько строк с начала читать ради cwd и первой реплики
TAIL_WINDOWS = (262_144, 2_097_152, 16_777_216)   # окна поиска custom-title с конца

SCAN_CAP = 800            # потолок обхода: дальше в прошлое список всё равно не листают
SKIP_DIRS = ("claude-mem-observer-sessions",)     # фоновые агенты памяти, не наши сессии


def projects_root():
    return os.path.join(os.path.expanduser("~"), ".claude", "projects")


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


def list_sessions(limit=40, workdir=None, unnamed=False, unnamed_limit=60):
    """
    Сессии, свежие сверху. Именованные — те же, что в сайдбаре приложения.
    Безымянные (прогоны CLI, голосовые сессии оболочки) приложение прячет, здесь
    их подмешивает unnamed=True, взяв в заголовок первую реплику.

    Лимиты раздельные намеренно: безымянных на диске втрое больше, и на общем
    счётчике они выдавливали бы рабочие сессии из списка.

    Сначала сортируем по времени файла (дёшево, только stat), читаем верхушку и
    останавливаемся, набрав оба лимита — иначе на сотнях сессий запуск бы тормозил.
    """
    pattern = os.path.join(project_dir(workdir) if workdir else projects_root(),
                           "*.jsonl" if workdir else os.path.join("*", "*.jsonl"))
    try:
        paths = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    except OSError:
        return []

    out = []
    n_named = n_unnamed = 0
    for path in paths[:SCAN_CAP]:
        if n_named >= limit and (not unnamed or n_unnamed >= unnamed_limit):
            break
        if any(d in path for d in SKIP_DIRS):
            continue
        title, cwd = _scan_tail(path)
        first = ""
        if title:
            if n_named >= limit:
                continue
        else:
            if not unnamed or n_unnamed >= unnamed_limit:
                continue
            first, head_cwd = _scan_head(path)
            cwd = cwd or head_cwd
            if not first:
                continue
        if title:
            n_named += 1
        else:
            n_unnamed += 1
        name = title or first[:60]
        folder = os.path.basename(cwd.rstrip("\\/")) if cwd else ""
        out.append({
            "id": os.path.splitext(os.path.basename(path))[0],
            "path": path,
            "title": name,
            "cwd": cwd,
            "named": bool(title),
            "label": f"{name} · {folder}" if folder else name,
            "mtime": os.path.getmtime(path),
        })
    return out


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
