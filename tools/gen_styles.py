# -*- coding: utf-8 -*-
"""
Превью трёх стилей пиксельного портрета.
Каждый стиль рисуется на холсте 64x64 настоящими пикселями,
потом масштабируется nearest-neighbor — как в 90х.

Запуск:  python tools/gen_styles.py
Выход:   assets/preview_styles.png
"""
import os
from PIL import Image, ImageDraw, ImageFont

W = H = 64
SCALE = 7

# ── палитры ────────────────────────────────────────────────────────────────
CRT = {
    "bezel":   (191, 183, 158),
    "bezel_l": (216, 208, 184),
    "bezel_d": (138, 131, 110),
    "screen":  (11, 20, 16),
    "glow_d":  (34, 96, 62),
    "glow":    (108, 240, 150),
    "glow_hi": (190, 255, 205),
    "led":     (225, 70, 60),
}

JRPG = {
    "bg":      (46, 42, 69),
    "bg2":     (60, 54, 90),
    "frame":   (120, 106, 180),
    "skin":    (233, 181, 140),
    "skin_sh": (198, 140, 100),
    "skin_dk": (160, 105, 74),
    "hair":    (176, 81, 47),
    "hair_hi": (217, 119, 87),
    "hair_dk": (110, 46, 24),
    "eye_w":   (240, 238, 230),
    "eye":     (35, 32, 58),
    "robe":    (62, 90, 140),
    "robe_dk": (40, 60, 100),
    "trim":    (217, 119, 87),
    "mouth":   (140, 66, 58),
}

MASCOT = {
    "bg":      (240, 238, 230),
    "bg_dot":  (222, 218, 205),
    "body":    (217, 119, 87),
    "body_hi": (238, 163, 134),
    "body_dk": (184, 86, 58),
    "line":    (92, 42, 28),
    "eye":     (36, 21, 18),
    "white":   (255, 255, 255),
    "spark":   (245, 197, 66),
    "blush":   (232, 132, 122),
}

EMOTIONS = ["neutral", "think", "laugh"]
RU = {"neutral": "нейтральный", "think": "думаю", "laugh": "смеюсь"}


def canvas(bg):
    img = Image.new("RGB", (W, H), bg)
    return img, ImageDraw.Draw(img)


def rect(d, x1, y1, x2, y2, c):
    """Прямоугольник, которому всё равно, в каком порядке заданы углы."""
    d.rectangle([min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)], fill=c)


# ── СТИЛЬ A: CRT-терминал ──────────────────────────────────────────────────
def style_crt(emo):
    p = CRT
    img, d = canvas(p["bezel"])
    # бевел корпуса
    rect(d, 0, 0, 63, 0, p["bezel_l"]); rect(d, 0, 0, 0, 63, p["bezel_l"])
    rect(d, 0, 63, 63, 63, p["bezel_d"]); rect(d, 63, 0, 63, 63, p["bezel_d"])
    # экран
    rect(d, 4, 4, 59, 46, p["bezel_d"])
    rect(d, 5, 5, 58, 45, p["screen"])
    # нижняя панель: индикатор + вентиляция
    rect(d, 8, 52, 10, 54, p["led"])
    for x in range(16, 52, 3):
        rect(d, x, 52, x + 1, 54, p["bezel_d"])

    cx = 32
    if emo == "neutral":
        for ex in (15, 38):                       # глаза-прямоугольники
            rect(d, ex, 16, ex + 11, 24, p["glow_d"])
            rect(d, ex + 1, 17, ex + 10, 23, p["glow"])
            rect(d, ex + 4, 19, ex + 7, 22, p["screen"])
        rect(d, cx - 11, 33, cx + 10, 35, p["glow"])       # ровный рот
        rect(d, cx - 11, 36, cx + 10, 36, p["glow_d"])
    elif emo == "think":
        for ex in (15, 38):                       # прищур
            rect(d, ex, 18, ex + 11, 22, p["glow_d"])
            rect(d, ex + 1, 19, ex + 10, 21, p["glow"])
            rect(d, ex + 2, 19, ex + 5, 21, p["screen"])   # зрачок влево-вверх
        rect(d, cx - 9, 34, cx + 2, 35, p["glow"])          # рот вбок
        for i, dx in enumerate((0, 5, 10)):                 # «...»
            rect(d, 40 + dx, 9 + (i % 2), 42 + dx, 11 + (i % 2), p["glow_d"])
    else:  # laugh
        for ex in (15, 38):                       # глаза «^ ^»
            for i in range(6):
                rect(d, ex + i, 22 - i, ex + i + 1, 23 - i, p["glow"])
                rect(d, ex + 11 - i, 22 - i, ex + 10 - i, 23 - i, p["glow"])
        d.ellipse([cx - 12, 28, cx + 11, 42], fill=p["glow_d"])   # открытый рот
        d.ellipse([cx - 10, 30, cx + 9, 40], fill=p["screen"])
        rect(d, cx - 10, 30, cx + 9, 32, p["glow"])
        rect(d, cx - 7, 38, cx + 6, 40, p["glow_hi"])              # язык/блик

    # скан-линии
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    for y in range(5, 46, 2):
        od.rectangle([5, y, 58, y], fill=(0, 0, 0, 60))
    return Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")


# ── СТИЛЬ B: 16-битный JRPG-бюст ───────────────────────────────────────────
def style_jrpg(emo):
    p = JRPG
    img, d = canvas(p["bg"])
    d.ellipse([-6, 18, 70, 74], fill=p["bg2"])                 # виньетка-подложка
    for x, y in ((10, 10), (52, 14), (18, 6), (46, 5), (57, 26)):
        rect(d, x, y, x, y, p["frame"])                        # звёзды
    rect(d, 0, 0, 63, 1, p["frame"]); rect(d, 0, 62, 63, 63, p["frame"])
    rect(d, 0, 0, 1, 63, p["frame"]); rect(d, 62, 0, 63, 63, p["frame"])

    # плечи / плащ
    d.ellipse([4, 50, 59, 78], fill=p["robe"])
    d.ellipse([4, 50, 30, 78], fill=p["robe_dk"])
    rect(d, 26, 50, 37, 55, p["trim"])
    # шея
    rect(d, 27, 44, 36, 52, p["skin_sh"])
    rect(d, 27, 44, 31, 52, p["skin_dk"])
    # голова
    d.ellipse([18, 14, 45, 49], fill=p["skin"])
    d.ellipse([32, 14, 45, 49], fill=p["skin_sh"])
    d.ellipse([18, 14, 40, 49], fill=p["skin"])
    rect(d, 17, 30, 18, 35, p["skin_sh"])                      # уши
    rect(d, 45, 30, 46, 35, p["skin_sh"])
    # волосы
    d.ellipse([16, 9, 47, 33], fill=p["hair"])
    d.ellipse([16, 9, 47, 26], fill=p["hair_hi"])
    rect(d, 16, 22, 20, 38, p["hair"])                         # пряди по бокам
    rect(d, 43, 22, 47, 38, p["hair_dk"])
    d.ellipse([22, 20, 42, 30], fill=p["skin"])                # лоб из-под чёлки
    d.ellipse([22, 20, 42, 26], fill=p["hair"])

    def eye(x, top, h, px_off=0):
        rect(d, x, top, x + 6, top + h, p["eye_w"])
        rect(d, x + 2 + px_off, top + 1, x + 4 + px_off, top + h, p["eye"])
        rect(d, x + 2 + px_off, top + 1, x + 2 + px_off, top + 1, p["eye_w"])

    if emo == "neutral":
        rect(d, 23, 29, 28, 29, p["hair_dk"]); rect(d, 35, 29, 40, 29, p["hair_dk"])
        eye(23, 31, 4); eye(35, 31, 4)
        rect(d, 31, 37, 32, 39, p["skin_sh"])
        rect(d, 28, 42, 35, 43, p["mouth"])
    elif emo == "think":
        rect(d, 23, 28, 28, 28, p["hair_dk"]); rect(d, 24, 27, 27, 27, p["hair_dk"])
        rect(d, 35, 30, 40, 30, p["hair_dk"])
        eye(23, 32, 2, -1); eye(35, 32, 2, -1)
        rect(d, 31, 37, 32, 39, p["skin_sh"])
        rect(d, 29, 42, 34, 42, p["mouth"]); rect(d, 34, 41, 35, 41, p["mouth"])
    else:  # laugh
        rect(d, 22, 27, 28, 27, p["hair_dk"]); rect(d, 35, 27, 41, 27, p["hair_dk"])
        for i in range(4):                                     # зажмуренные «^»
            rect(d, 23 + i, 33 - i, 24 + i, 33 - i, p["eye"])
            rect(d, 29 - i, 33 - i, 28 - i, 33 - i, p["eye"])
            rect(d, 35 + i, 33 - i, 36 + i, 33 - i, p["eye"])
            rect(d, 41 - i, 33 - i, 40 - i, 33 - i, p["eye"])
        rect(d, 31, 37, 32, 38, p["skin_sh"])
        d.ellipse([27, 40, 37, 47], fill=p["mouth"])           # смеющийся рот
        rect(d, 28, 40, 36, 41, p["eye_w"])
        rect(d, 20, 36, 22, 38, p["hair_hi"]); rect(d, 42, 36, 44, 38, p["hair_hi"])
    return img


# ── СТИЛЬ C: маскот-искорка ────────────────────────────────────────────────
def style_mascot(emo):
    p = MASCOT
    img, d = canvas(p["bg"])
    for y in range(2, 64, 6):                                   # фоновая сетка
        for x in range(2, 64, 6):
            rect(d, x, y, x, y, p["bg_dot"])
    # антенна
    rect(d, 31, 8, 32, 16, p["line"])
    d.polygon([(31, 2), (35, 6), (31, 10), (27, 6)], fill=p["spark"])
    d.polygon([(31, 4), (33, 6), (31, 8), (29, 6)], fill=(255, 240, 190))
    # тело
    d.ellipse([10, 15, 53, 56], fill=p["line"])
    d.ellipse([11, 16, 52, 55], fill=p["body"])
    d.ellipse([11, 16, 52, 40], fill=p["body_hi"])
    d.ellipse([13, 20, 50, 55], fill=p["body"])
    d.ellipse([16, 44, 47, 55], fill=p["body_dk"])
    rect(d, 5, 34, 10, 41, p["line"]); rect(d, 6, 35, 10, 40, p["body"])   # лапки
    rect(d, 53, 34, 58, 41, p["line"]); rect(d, 53, 35, 57, 40, p["body_dk"])

    def eye(x, y, w, h):
        d.ellipse([x, y, x + w, y + h], fill=p["eye"])
        rect(d, x + 2, y + 2, x + 3, y + 3, p["white"])

    if emo == "neutral":
        eye(18, 26, 10, 12); eye(36, 26, 10, 12)
        rect(d, 29, 45, 34, 46, p["line"])
    elif emo == "think":
        eye(19, 30, 10, 7); eye(37, 30, 10, 7)
        rect(d, 17, 24, 27, 25, p["line"]); rect(d, 18, 23, 24, 23, p["line"])
        rect(d, 37, 26, 47, 27, p["line"])
        rect(d, 27, 46, 33, 47, p["line"]); rect(d, 33, 45, 35, 45, p["line"])
        for i, (x, y) in enumerate(((54, 14), (58, 10), (61, 6))):          # «...»
            rect(d, x, y, x + 1 + i, y + 1 + i, p["line"])
    else:  # laugh
        for i in range(5):                                       # зажмуренные глаза
            rect(d, 19 + i, 34 - i, 20 + i, 34 - i, p["eye"])
            rect(d, 28 - i, 34 - i, 27 - i, 34 - i, p["eye"])
            rect(d, 37 + i, 34 - i, 38 + i, 34 - i, p["eye"])
            rect(d, 46 - i, 34 - i, 45 - i, 34 - i, p["eye"])
        d.ellipse([24, 40, 39, 50], fill=p["line"])
        d.ellipse([26, 41, 37, 47], fill=(200, 90, 90))
        rect(d, 14, 36, 17, 38, p["blush"]); rect(d, 47, 36, 50, 38, p["blush"])
    return img


STYLES = [
    ("A · CRT-терминал", style_crt),
    ("B · 16-бит JRPG", style_jrpg),
    ("C · Маскот-искорка", style_mascot),
]


def font(size):
    for path in (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\arial.ttf"):
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def main():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = os.path.join(here, "assets")
    os.makedirs(out_dir, exist_ok=True)

    cell = W * SCALE
    pad, head, lab = 18, 54, 26
    sheet = Image.new("RGB", (pad + (cell + pad) * 3, head + (cell + lab) * 3 + pad),
                      (24, 24, 26))
    d = ImageDraw.Draw(sheet)
    f_title, f_lab = font(24), font(19)

    for col, (name, fn) in enumerate(STYLES):
        x = pad + col * (cell + pad)
        d.text((x, 16), name, font=f_title, fill=(240, 238, 230))
        for row, emo in enumerate(EMOTIONS):
            y = head + row * (cell + lab)
            sheet.paste(fn(emo).resize((cell, cell), Image.NEAREST), (x, y))
            d.text((x + 2, y + cell + 4), RU[emo], font=f_lab, fill=(150, 150, 155))

    path = os.path.join(out_dir, "preview_styles.png")
    sheet.save(path)
    print(path)


if __name__ == "__main__":
    main()
