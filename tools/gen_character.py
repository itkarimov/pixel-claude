# -*- coding: utf-8 -*-
"""
Генератор спрайтов персонажа: девушка, длинные волосы, майка, кадр по пупок.
Холст 72x80 настоящих пикселей, потом nearest-neighbor апскейл.

Запуск:  python tools/gen_character.py
Выход:   assets/sprites/<emotion>.png  +  assets/preview_character.png
"""
import os
from PIL import Image, ImageDraw, ImageFont

W, H = 72, 80
SCALE = 6

P = {
    "bg":      (42, 36, 64),
    "bg2":     (58, 50, 88),
    "frame":   (120, 106, 180),
    "star":    (150, 140, 210),

    "skin":    (240, 194, 154),
    "skin_sh": (209, 154, 114),
    "skin_dk": (169, 112, 76),

    "hair":    (122, 62, 40),
    "hair_hi": (168, 90, 56),
    "hair_dk": (74, 35, 22),

    "top":     (237, 231, 220),
    "top_sh":  (201, 193, 180),
    "top_dk":  (160, 152, 140),

    "eye_w":   (245, 242, 234),
    "iris":    (62, 94, 122),
    "pupil":   (26, 21, 38),
    "lash":    (40, 28, 30),
    "lip":     (196, 98, 106),
    "lip_dk":  (150, 66, 76),
    "blush":   (232, 148, 140),
    "line":    (26, 21, 38),
    "spark":   (245, 197, 66),
    "tear":    (140, 200, 235),
}

EMOTIONS = ["neutral", "think", "happy", "laugh", "sly",
            "unhappy", "sad", "surprised", "done"]
RU = {
    "neutral": "нейтральный", "think": "думаю", "happy": "радость",
    "laugh": "смеюсь", "sly": "хитрый", "unhappy": "недовольна",
    "sad": "расстроена", "surprised": "удивлена", "done": "готово!",
}


def rect(d, x1, y1, x2, y2, c):
    d.rectangle([min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)], fill=c)


def ell(d, x1, y1, x2, y2, c):
    d.ellipse([x1, y1, x2, y2], fill=c)


# ── фон ────────────────────────────────────────────────────────────────────
def draw_bg(d):
    rect(d, 0, 0, W - 1, H - 1, P["bg"])
    ell(d, -10, 14, W + 10, H + 20, P["bg2"])
    for x, y in ((8, 8), (60, 12), (16, 20), (55, 5), (66, 30), (5, 34)):
        rect(d, x, y, x, y, P["star"])
    rect(d, 0, 0, W - 1, 1, P["frame"]); rect(d, 0, H - 2, W - 1, H - 1, P["frame"])
    rect(d, 0, 0, 1, H - 1, P["frame"]); rect(d, W - 2, 0, W - 1, H - 1, P["frame"])


# ── фигура ─────────────────────────────────────────────────────────────────
def draw_body(d, arm_up=False):
    # волосы сзади: масса за головой + два длинных локона до пояса
    ell(d, 16, 3, 56, 50, P["hair_dk"])
    ell(d, 14, 18, 27, 72, P["hair_dk"])
    ell(d, 45, 18, 58, 72, P["hair_dk"])
    ell(d, 15, 18, 25, 68, P["hair"])
    ell(d, 47, 18, 57, 68, P["hair"])

    # руки
    if arm_up:
        rect(d, 17, 50, 23, 76, P["skin_sh"])            # левая вдоль тела
        ell(d, 16, 72, 24, 79, P["skin_sh"])
        # предплечье
        d.polygon([(51, 64), (60, 60), (65, 48), (56, 45)], fill=P["skin_dk"])
        d.polygon([(52, 62), (59, 59), (63, 48), (57, 46)], fill=P["skin_sh"])
        # кулак: широкий и приземистый — иначе читается как палец вверх
        ell(d, 50, 33, 68, 50, P["skin_dk"])
        ell(d, 51, 34, 67, 49, P["skin"])
        for ky in (38, 42, 46):                          # согнутые пальцы — горизонтали
            rect(d, 57, ky, 66, ky, P["skin_sh"])
        rect(d, 57, 35, 57, 48, P["skin_sh"])            # ребро ладони
        # большой палец: короткий, толстый, лежит поверх кулака слева
        rect(d, 51, 27, 57, 36, P["skin_dk"])
        ell(d, 51, 24, 57, 31, P["skin_dk"])
        rect(d, 52, 28, 56, 35, P["skin"])
        ell(d, 52, 25, 56, 30, P["skin"])
    else:
        rect(d, 17, 50, 23, 76, P["skin_sh"])
        ell(d, 16, 72, 24, 79, P["skin_sh"])
        rect(d, 49, 50, 55, 76, P["skin_sh"])
        ell(d, 48, 72, 56, 79, P["skin_sh"])

    # торс: плечи → талия → бёдра
    ell(d, 19, 42, 53, 60, P["skin"])
    d.polygon([(21, 50), (51, 50), (48, 66), (47, 80), (25, 80), (24, 66)],
              fill=P["skin"])
    rect(d, 44, 50, 51, 80, P["skin_sh"])                # теневая сторона
    d.polygon([(21, 50), (46, 50), (44, 66), (44, 80), (25, 80), (24, 66)],
              fill=P["skin"])

    # шея + тень от подбородка
    rect(d, 32, 32, 40, 46, P["skin_sh"])
    rect(d, 32, 32, 40, 35, P["skin_dk"])

    # майка: скругленный вырез, бретели, подол чуть выше пупка
    rect(d, 23, 49, 49, 69, P["top"])
    rect(d, 43, 49, 49, 69, P["top_sh"])
    ell(d, 29, 44, 43, 53, P["skin"])                    # вырез
    rect(d, 27, 43, 31, 52, P["top"])                    # бретели
    rect(d, 41, 43, 45, 52, P["top_sh"])
    rect(d, 31, 43, 31, 52, P["top_dk"])
    rect(d, 41, 43, 41, 52, P["top_dk"])
    rect(d, 30, 55, 30, 66, P["top_sh"])                 # вертикальные складки
    rect(d, 42, 55, 42, 66, P["top_dk"])
    rect(d, 23, 68, 49, 69, P["top_dk"])                 # подол
    rect(d, 35, 73, 36, 75, P["skin_dk"])                # пупок
    rect(d, 34, 73, 34, 74, P["skin_sh"])

    # голова
    ell(d, 25, 6, 47, 36, P["skin"])
    ell(d, 27, 18, 45, 39, P["skin"])
    ell(d, 41, 10, 47, 36, P["skin_sh"])                 # мягкая тень справа
    ell(d, 25, 6, 44, 37, P["skin"])
    rect(d, 24, 23, 25, 28, P["skin_sh"])                # уши
    rect(d, 47, 23, 48, 28, P["skin_sh"])

    # волосы спереди: ровная чёлка + пряди у лица
    ell(d, 19, 1, 53, 28, P["hair"])
    ell(d, 25, 3, 45, 14, P["hair_hi"])
    ell(d, 27, 12, 46, 34, P["skin"])                    # открытое лицо
    ell(d, 23, 4, 48, 17, P["hair"])                     # чёлка дугой
    rect(d, 22, 12, 26, 46, P["hair"])                   # пряди у лица
    rect(d, 46, 12, 50, 46, P["hair_dk"])
    rect(d, 23, 7, 25, 13, P["hair_hi"])


# ── лицо ───────────────────────────────────────────────────────────────────
def eye(d, x, y, h=5, px=0, py=0, lash=True):
    """Глаз: белок, радужка, зрачок, блик, ресница."""
    ell(d, x, y, x + 6, y + h, P["eye_w"])
    ell(d, x + 1 + px, y + 1 + py, x + 4 + px, y + h - 1 + py, P["iris"])
    rect(d, x + 2 + px, y + 2 + py, x + 3 + px, y + h - 2 + py, P["pupil"])
    rect(d, x + 2 + px, y + 1 + py, x + 2 + px, y + 1 + py, P["eye_w"])
    if lash:
        rect(d, x, y, x + 6, y, P["lash"])


def closed_eye(d, x, y, up=True):
    """Зажмуренный глаз дугой."""
    for i in range(4):
        dy = -i if up else i
        rect(d, x + i, y + dy, x + i, y + dy, P["lash"])
        rect(d, x + 6 - i, y + dy, x + 6 - i, y + dy, P["lash"])


def brow(d, x, y, tilt=0, c=None):
    """Бровь: tilt<0 — внешний край вниз (грусть), tilt>0 — вверх (злость)."""
    c = c or P["hair_dk"]
    for i in range(6):
        rect(d, x + i, y + round(tilt * (i - 2.5) / 2.5), x + i,
             y + round(tilt * (i - 2.5) / 2.5), c)


def draw_face(d, emo, blink=False):
    LX, RX = 28, 39            # x левого / правого глаза
    EY = 22                    # базовая линия глаз
    BY = 19                    # линия бровей
    MY = 32                    # линия рта

    def E(x, y, h=5, px=0, py=0):
        """Глаз с учётом моргания — в кадре моргания вместо него дуга."""
        if blink:
            closed_eye(d, x, y + h // 2)
        else:
            eye(d, x, y, h, px, py)

    if emo == "neutral":
        brow(d, LX, BY); brow(d, RX, BY)
        E(LX, EY); E(RX, EY)
        rect(d, 35, 28, 36, 30, P["skin_sh"])
        rect(d, 33, MY, 38, MY, P["lip"])
        rect(d, 34, MY + 1, 37, MY + 1, P["lip_dk"])

    elif emo == "think":
        brow(d, LX, BY - 2, 2); brow(d, RX, BY + 1, -1)
        E(LX, EY, 4, px=-1, py=-1); E(RX, EY, 4, px=-1, py=-1)
        rect(d, 35, 28, 36, 30, P["skin_sh"])
        rect(d, 33, MY, 37, MY, P["lip"]); rect(d, 37, MY - 1, 38, MY - 1, P["lip"])
        for i, (x, y) in enumerate(((52, 10), (57, 7), (62, 4))):   # «...»
            rect(d, x, y, x + i, y + i, P["frame"])

    elif emo == "happy":
        brow(d, LX, BY - 1, -1); brow(d, RX, BY - 1, 1)
        E(LX, EY); E(RX, EY)
        rect(d, 35, 28, 36, 30, P["skin_sh"])
        for i in range(6):                                          # улыбка дугой
            rect(d, 33 + i, MY + (1 if 0 < i < 5 else 0), 33 + i,
                 MY + (1 if 0 < i < 5 else 0), P["lip"])
        rect(d, 26, 28, 28, 29, P["blush"]); rect(d, 44, 28, 46, 29, P["blush"])

    elif emo == "laugh":
        brow(d, LX, BY - 2, -1); brow(d, RX, BY - 2, 1)
        closed_eye(d, LX, EY + 3); closed_eye(d, RX, EY + 3)
        rect(d, 35, 28, 36, 29, P["skin_sh"])
        ell(d, 32, MY - 1, 40, MY + 5, P["lip_dk"])                 # открытый рот
        rect(d, 33, MY - 1, 39, MY, P["eye_w"])
        ell(d, 34, MY + 3, 38, MY + 5, P["lip"])
        rect(d, 25, 28, 28, 30, P["blush"]); rect(d, 44, 28, 47, 30, P["blush"])

    elif emo == "sly":
        brow(d, LX, BY - 3, 1); brow(d, RX, BY + 1, 2)
        E(LX, EY + 2, 3, px=1); E(RX, EY + 2, 3, px=1)
        rect(d, 35, 28, 36, 30, P["skin_sh"])
        rect(d, 33, MY + 1, 37, MY + 1, P["lip"])                   # ухмылка углом
        rect(d, 38, MY, 39, MY, P["lip"]); rect(d, 39, MY - 1, 39, MY - 1, P["lip_dk"])

    elif emo == "unhappy":
        brow(d, LX, BY, 3); brow(d, RX, BY, -3)
        E(LX, EY + 1, 4); E(RX, EY + 1, 4)
        rect(d, 35, 28, 36, 30, P["skin_sh"])
        for i in range(6):                                          # рот дугой вниз
            rect(d, 33 + i, MY + 1 - (1 if 0 < i < 5 else 0), 33 + i,
                 MY + 1 - (1 if 0 < i < 5 else 0), P["lip_dk"])

    elif emo == "sad":
        brow(d, LX, BY, -3); brow(d, RX, BY, 3)
        E(LX, EY + 1, 4, py=1); E(RX, EY + 1, 4, py=1)
        rect(d, 35, 28, 36, 30, P["skin_sh"])
        for i in range(5):
            rect(d, 34 + i, MY + 1 - (1 if 0 < i < 4 else 0), 34 + i,
                 MY + 1 - (1 if 0 < i < 4 else 0), P["lip_dk"])
        rect(d, 45, 27, 45, 30, P["tear"]); rect(d, 45, 31, 45, 31, P["tear"])

    elif emo == "surprised":
        brow(d, LX, BY - 3); brow(d, RX, BY - 3)
        E(LX, EY, 7); E(RX, EY, 7)
        rect(d, 35, 28, 36, 30, P["skin_sh"])
        ell(d, 34, MY + 1, 38, MY + 5, P["lip_dk"])
        ell(d, 35, MY + 2, 37, MY + 4, P["lip"])

    elif emo == "done":
        brow(d, LX, BY - 1, -1); brow(d, RX, BY - 2, 1)
        closed_eye(d, LX, EY + 3)                                   # подмигивание
        E(RX, EY)
        rect(d, 35, 28, 36, 30, P["skin_sh"])
        for i in range(6):
            rect(d, 33 + i, MY + (1 if 0 < i < 5 else 0), 33 + i,
                 MY + (1 if 0 < i < 5 else 0), P["lip"])
        rect(d, 34, MY + 2, 37, MY + 2, P["lip_dk"])
        rect(d, 26, 28, 28, 29, P["blush"]); rect(d, 44, 28, 46, 29, P["blush"])
        for cx, cy, s in ((9, 22, 2), (13, 38, 1), (8, 52, 2), (66, 62, 1)):
            rect(d, cx - s, cy, cx + s, cy, P["spark"])             # искры
            rect(d, cx, cy - s, cx, cy + s, P["spark"])


def sprite(emo, blink=False):
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    draw_bg(d)
    draw_body(d, arm_up=(emo == "done"))
    draw_face(d, emo, blink=blink)
    return img


def font(size):
    for p in (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\arial.ttf"):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size)


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spr_dir = os.path.join(root, "assets", "sprites")
    os.makedirs(spr_dir, exist_ok=True)

    for emo in EMOTIONS:
        sprite(emo).save(os.path.join(spr_dir, f"{emo}.png"))
        if emo != "laugh":                       # у смеха глаза и так зажмурены
            sprite(emo, blink=True).save(os.path.join(spr_dir, f"{emo}_blink.png"))

    # иконка для ярлыка на рабочем столе
    icon = sprite("happy").resize((256, 256), Image.NEAREST)
    icon.save(os.path.join(root, "assets", "app.ico"),
              sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

    cw, ch = W * SCALE, H * SCALE
    pad, lab, cols = 14, 26, 3
    rows = (len(EMOTIONS) + cols - 1) // cols
    sheet = Image.new("RGB", (pad + (cw + pad) * cols,
                              pad + (ch + lab) * rows), (24, 24, 26))
    d = ImageDraw.Draw(sheet)
    f = font(19)
    for i, emo in enumerate(EMOTIONS):
        x = pad + (i % cols) * (cw + pad)
        y = pad + (i // cols) * (ch + lab)
        sheet.paste(sprite(emo).resize((cw, ch), Image.NEAREST), (x, y))
        d.text((x + 2, y + ch + 4), RU[emo], font=f, fill=(160, 158, 165))

    out = os.path.join(root, "assets", "preview_character.png")
    sheet.save(out)
    print(out)


if __name__ == "__main__":
    main()
