# -*- coding: utf-8 -*-
"""
Нарезка листа эмоций (assets/ref.png) на спрайты.

Лист размечен не идеально ровной сеткой: у клеток с поднятыми руками и
сердечками шире контент. Поэтому клетки режутся не по формальной сетке, а по
двум устойчивым привязкам — центр фигуры по массе тёмного (волосы и кофта)
и верхняя линия волос. Так голова стоит на месте при любой смене эмоции.

Запуск:  python tools/slice_ref.py
Выход:   assets/sprites/<эмоция>.png  +  assets/sheet/r<ряд>c<колонка>.png
"""
import io
import os
import shutil
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import numpy as np
from PIL import Image

CROP_W, CROP_H = 176, 146      # окно кадра в пикселях листа (арт нарисован 2х)
TOP_MARGIN = 6                 # воздух над волосами
# Панель портрета вытянута по вертикали, а клетка листа шире, чем высока —
# при целочисленном масштабе упиралось бы в ширину и половина высоты пустовала.
# Продлеваем торс вниз повтором нижней строки: там ровные вертикальные пряди
# и кофта, шов незаметен, зато пропорции сходятся с панелью.
EXTEND = 50
# Арт на листе нарисован с двукратным апскейлом (проверено длинами серий).
# Возвращаем к родному разрешению: окно масштабируется целым числом, и на
# мелком спрайте шаг получается частым — портрет заполняет панель, а не треть.
DOWNSCALE = 2

# Эмоция → (ряд, колонка). Карта собрана по фактическому рисунку в клетках:
# подписи на листе местами не совпадают с тем, что нарисовано, поэтому сверялся
# с нарезкой (assets/sheet/r<ряд>c<колонка>.png), а не с текстом под фигурой.
MAPPING = {
    "neutral":   (0, 0),   # спокойное лицо
    "smile":     (0, 1),   # лёгкая улыбка
    "happy":     (0, 2),   # широкая улыбка
    "laugh":     (0, 3),   # смеётся, глаза зажмурены
    "surprised": (0, 5),   # округлённый рот
    "puzzled":   (0, 6),   # вопросительный знак над головой
    "think":     (0, 7),   # рука у подбородка
    "sad":       (1, 0),   # грусть
    "crying":    (1, 2),   # слёзы
    "angry":     (1, 4),   # злость, сомкнутый рот
    "unhappy":   (1, 6),   # раздражение
    "shy":       (2, 0),   # смущение, румянец
    "love":      (2, 1),   # сердечки
    "tired":     (2, 5),   # усталость
    "sly":       (2, 6),   # тёмные очки
    "excited":   (2, 7),   # восторг, искры
    "relief":    (3, 3),   # облегчение, выдох
    "victory":   (3, 5),   # два знака мира
    # Клетка (3,4) с V-знаком и подмигиванием отпала: на кулаке прорисовано
    # столько складок, что рука читается как шести-семипалая. Здесь кулак
    # поднят целиком — считать нечего.
    "done":      (3, 6),   # поднятый кулак, широкая улыбка
}


# Точечные правки поверх нарезанного спрайта: оригинал ref.png не трогаем,
# патч воспроизводится при каждой нарезке.
# Формат: эмоция → [(x0, x1, строка_куда, строка_откуда), ...]
PATCHES = {}


def apply_patches(img, name):
    if name not in PATCHES:
        return img
    px = img.load()
    for x0, x1, y_dst, y_src in PATCHES[name]:
        for x in range(x0, x1 + 1):
            px[x, y_dst] = px[x, y_src]
    return img


def bands(flags, min_len=1):
    out, start = [], None
    for i, v in enumerate(flags):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start >= min_len:
                out.append((start, i - 1))
            start = None
    if start is not None and len(flags) - start >= min_len:
        out.append((start, len(flags) - 1))
    return out


def analyse(path):
    """Возвращает (изображение, центры колонок, верхние линии волос по рядам)."""
    im = Image.open(path).convert("RGB")
    a = np.array(im).astype(int)
    bg = a[0, 0]
    content = np.abs(a - bg).sum(axis=2) > 24
    dark = a.sum(axis=2) < 260

    row_bands = [b for b in bands(content.any(axis=1)) if b[1] - b[0] > 40]
    centers, tops = [], []

    for y0, y1 in row_bands:
        band_c, band_d = content[y0:y1 + 1], dark[y0:y1 + 1]
        cells = [b for b in bands(band_c.any(axis=0)) if b[1] - b[0] > 40]
        row_centers, row_tops = [], []
        for cx0, cx1 in cells:
            sub = band_d[:, cx0:cx1 + 1]
            xs = np.nonzero(sub)[1]
            row_centers.append(int(np.median(xs)) + cx0)
            row_tops.append(int(np.argmax(sub.sum(axis=1) > 12)))
        centers.append(row_centers)
        tops.append(y0 + int(np.median(row_tops)))

    ncol = min(len(r) for r in centers)
    col_centers = [int(np.median([r[k] for r in centers])) for k in range(ncol)]
    return im, col_centers, tops


def crop(im, cx, top):
    x0 = max(0, min(im.width - CROP_W, cx - CROP_W // 2))
    y0 = max(0, min(im.height - CROP_H, top - TOP_MARGIN))
    cell = im.crop((x0, y0, x0 + CROP_W, y0 + CROP_H))
    if not EXTEND:
        return cell

    # ищем снизу последнюю строку, где по центру идёт сплошная кофта:
    # в клетках с поднятыми руками низ кадра попадает на фон, и повтор такой
    # строки давал вертикальные полосы
    a = np.array(cell).astype(int)
    xa, xb = int(CROP_W * 0.34), int(CROP_W * 0.66)
    cut = CROP_H - 1
    for y in range(CROP_H - 1, CROP_H // 2, -1):
        if (a[y, xa:xb].sum(axis=1) < 300).mean() > 0.8:
            cut = y
            break

    out = Image.new("RGB", (CROP_W, CROP_H + EXTEND))
    out.paste(cell.crop((0, 0, CROP_W, cut + 1)), (0, 0))
    tail = cell.crop((0, cut, CROP_W, cut + 1))
    out.paste(tail.resize((CROP_W, CROP_H + EXTEND - cut - 1), Image.NEAREST),
              (0, cut + 1))
    if DOWNSCALE > 1:
        out = out.resize((out.width // DOWNSCALE, out.height // DOWNSCALE),
                         Image.NEAREST)
    return out


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ref = os.path.join(root, "assets", "ref.png")
    if not os.path.exists(ref):
        raise SystemExit(f"нет файла {ref}")

    im, cols, tops = analyse(ref)
    print(f"колонок: {len(cols)}, рядов: {len(tops)}")
    print("центры колонок:", cols)
    print("линии волос:   ", tops)

    spr = os.path.join(root, "assets", "sprites")
    old = os.path.join(root, "assets", "sprites_generated")
    if os.path.isdir(spr) and not os.path.isdir(old):
        shutil.move(spr, old)          # свои старые спрайты не удаляю, а отставляю
        print(f"старые спрайты убраны в {os.path.basename(old)}")
    os.makedirs(spr, exist_ok=True)

    sheet = os.path.join(root, "assets", "sheet")
    os.makedirs(sheet, exist_ok=True)
    for r, top in enumerate(tops):
        for c, cx in enumerate(cols):
            crop(im, cx, top).save(os.path.join(sheet, f"r{r}c{c}.png"))

    for stale in os.listdir(spr):               # спрайты от прошлых карт эмоций
        if os.path.splitext(stale)[0] not in MAPPING:
            os.remove(os.path.join(spr, stale))
            print(f"  убран лишний спрайт: {stale}")

    for name, (r, c) in MAPPING.items():
        if r >= len(tops) or c >= len(cols):
            print(f"  пропуск {name}: нет клетки ({r},{c})")
            continue
        apply_patches(crop(im, cols[c], tops[r]), name) \
            .save(os.path.join(spr, f"{name}.png"))
    print(f"сохранено эмоций: {len(MAPPING)} → {spr}")

    # иконка для ярлыка: квадратный кроп по лицу, иначе портрет растянет
    face = Image.open(os.path.join(spr, "happy.png")).crop((10, 2, 78, 70))
    face.resize((256, 256), Image.NEAREST).save(
        os.path.join(root, "assets", "app.ico"),
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("иконка обновлена: assets/app.ico")


if __name__ == "__main__":
    main()
