# -*- coding: utf-8 -*-
"""
Нарезка листа эмоций (assets/sheet_emotions.png) на спрайты портрета.

Лист нарисован не по линейке: ряды идут в разном масштабе (в нижнем голова на
пятую часть меньше, чем в верхнем), фигуры в клетках стоят вразнобой, а у поз
с поднятыми руками содержимое шире клетки. Поэтому кадр не вырезается по
формальной сетке, а собирается по двум привязкам:

    * верх лица — первая строка, где под чёлкой набирается полоса кожи;
    * середина головы — медиана тёмного (волос) на уровне головы.

Каждая клетка приводится к одному масштабу по высоте лица, поэтому при смене
эмоции голова остаётся на месте и не меняет размер. Всё, что не попало в свою
клетку, добивается цветом фона — он на листе ровный, шва не видно.

Запуск:  python tools/slice_emotions.py
Выход:   assets/sprites/<эмоция>.png
"""
import io
import os
import shutil
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import numpy as np
from PIL import Image

OUT_W, OUT_H = 210, 166      # кадр портрета, пропорция под панель окна
FACE_Y = 40                  # куда ставим верх лица
FACE_H = 101                 # эталонная высота лица (верхний ряд листа)

# Эмоция → (ряд, колонка). Подписи на листе русские, порядок слева направо:
#   0: нейтрально, лёгкая улыбка, широкая улыбка, смеётся, смеётся сильно,
#      удивление, шок, задумчивость
#   1: грусть, разочарование, беспокойство, плачет, рыдает, злость, ярость,
#      раздражение
#   2: смущение, влюблённость, трогательная, стеснение, сонливость, усталость,
#      крутость, восторг
#   3: нервозность, тревога, беспомощность, облегчение, подмигивание, победа,
#      двойная победа, ура
#   4: профили, виды сзади
MAPPING = {
    "neutral":   (0, 0),
    "smile":     (0, 1),
    "happy":     (0, 2),
    "laugh":     (0, 4),
    "surprised": (0, 5),
    "puzzled":   (0, 6),    # знак вопроса над головой
    "think":     (0, 7),
    "sad":       (1, 0),
    "crying":    (1, 3),
    "angry":     (1, 5),
    "unhappy":   (1, 7),
    "shy":       (2, 0),
    "love":      (2, 1),
    "tired":     (2, 5),
    "sly":       (2, 6),    # тёмные очки
    "excited":   (2, 7),
    "relief":    (3, 3),
    "done":      (3, 4),    # подмигивает и показывает большой палец
    "victory":   (3, 6),
    # Кадры простоя: оболочка показывает их, пока разговора нет. Профили из
    # нижнего ряда сюда не берём — там и силуэт другой, и масштаб, подмена
    # читалась бы как рывок, а не как «сидит ждёт».
    "idle_hand": (2, 2),    # подпёрла щёку
    "idle_calm": (2, 4),    # просто сидит, лицо спокойное
    "idle_doze": (2, 3),    # задремала
}


def bands(flags, thr=0):
    out, start = [], None
    for i, v in enumerate(flags):
        if v > thr and start is None:
            start = i
        elif v <= thr and start is not None:
            out.append((start, i - 1))
            start = None
    if start is not None:
        out.append((start, len(flags) - 1))
    return out


def grid(a, bg):
    """Полосы рядов и колонок. Подписи тоньше фигур — отсекаются порогом."""
    dark = a.sum(axis=2) < 300
    cont = np.abs(a - bg).sum(axis=2) > 28
    rows = [b for b in bands(dark.sum(axis=1), 200) if b[1] - b[0] > 40]
    cols = [b for b in bands(cont.any(axis=0).astype(int)) if b[1] - b[0] > 60]
    if len(rows) != 5 or len(cols) != 8:
        raise SystemExit(f"лист читается не как 8x5: {len(cols)}x{len(rows)}")
    return rows, cols


def safe_x(cols):
    """Границы, за которые кадр лезть не должен: середины просветов."""
    out = []
    for i, (x0, x1) in enumerate(cols):
        left = 0 if i == 0 else (cols[i - 1][1] + x0) // 2
        right = 10 ** 6 if i == len(cols) - 1 else (x1 + cols[i + 1][0]) // 2
        out.append((left, right))
    return out


def anchors(a, y0, y1, x0, x1):
    """
    Середина лица, верх лица и его высота внутри клетки.

    Середину берём по коже, а не по тёмной массе: волосы у неё зачёсаны на одну
    сторону и тянут медиану вбок тем сильнее, чем больше их попало в кадр.
    """
    cell = a[y0:y1 + 1, x0:x1 + 1]
    dark = cell.sum(axis=2) < 300
    hair_cx = int(np.median(np.nonzero(dark[:60])[1]))

    r, g, b = cell[:, :, 0], cell[:, :, 1], cell[:, :, 2]
    skin = (r > 170) & (g > 120) & (b > 95) & (r > g + 18) & (g > b + 5)
    band = slice(max(0, hair_cx - 32), hair_cx + 32)
    counts = skin[:, band].sum(axis=1)
    top = int(np.argmax(counts > 18))
    bottom = len(counts) - 1 - int(np.argmax(counts[::-1] > 10))
    brow = skin[top:top + 30]
    cx = int(np.median(np.nonzero(brow)[1])) if brow.any() else hair_cx
    return cx + x0, top + y0, bottom - top


def cut(im, bg, box, cx, top, scale):
    """Кадр вокруг головы: клетка увеличивается до общего масштаба и обрезается."""
    x_lo, x_hi, y_lo, y_hi = box
    src_w, src_h = int(OUT_W / scale), int(OUT_H / scale)
    left = cx - int(OUT_W / 2 / scale)
    upper = top - int(FACE_Y / scale)

    # то, что вышло за свою клетку, не берём — там соседка
    cl, cu = max(left, x_lo), max(upper, y_lo)
    cr, cb = min(left + src_w, x_hi), min(upper + src_h, y_hi)
    piece = im.crop((cl, cu, cr, cb))
    piece = piece.resize((max(1, round(piece.width * scale)),
                          max(1, round(piece.height * scale))), Image.LANCZOS)

    out = Image.new("RGB", (OUT_W, OUT_H), tuple(int(v) for v in bg))
    out.paste(piece, (round((cl - left) * scale), round((cu - upper) * scale)))
    return out


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sheet = os.path.join(root, "assets", "sheet_emotions.png")
    if not os.path.exists(sheet):
        raise SystemExit(f"нет листа {sheet}")

    im = Image.open(sheet).convert("RGB")
    a = np.array(im).astype(int)
    bg = a[2, 2]
    rows, cols = grid(a, bg)
    limits = safe_x(cols)
    print("ряды:", rows)
    print("колонки:", [c for c in cols])

    spr = os.path.join(root, "assets", "sprites")
    old = os.path.join(root, "assets", "sprites_ref")
    if os.path.isdir(spr) and not os.path.isdir(old):
        shutil.move(spr, old)
        print(f"прежние спрайты отставлены в {os.path.basename(old)}")
    os.makedirs(spr, exist_ok=True)

    # масштаб и линия лица — общие на ряд: внутри ряда клетки совпадают, а
    # отдельная клетка может соврать, если руку подняли к лицу
    shape = {}
    for r, (y0, y1) in enumerate(rows):
        tops, heights = [], []
        for x0, x1 in cols:
            _, top, height = anchors(a, y0, y1, x0, x1)
            tops.append(top)
            heights.append(height)
        shape[r] = (int(np.median(tops)), FACE_H / float(np.median(heights)))
        print(f"  ряд {r}: лицо {np.median(heights):.0f} px, масштаб "
              f"{shape[r][1]:.2f}")

    for stale in os.listdir(spr):               # кадры от прошлых раскладок
        if os.path.splitext(stale)[0] not in MAPPING:
            os.remove(os.path.join(spr, stale))
            print(f"  убран лишний кадр: {stale}")

    for name, (r, c) in MAPPING.items():
        y0, y1 = rows[r]
        x0, x1 = cols[c]
        cx, top, _ = anchors(a, y0, y1, x0, x1)
        row_top, scale = shape[r]
        top = min(max(top, row_top - 3), row_top + 3)
        box = (limits[c][0], min(limits[c][1], im.width),
               max(0, y0 - 8), y1 - 1)
        cut(im, bg, box, cx, top, scale).save(
            os.path.join(spr, f"{name}.png"))
    print(f"кадров: {len(MAPPING)} → {spr}")


if __name__ == "__main__":
    main()
