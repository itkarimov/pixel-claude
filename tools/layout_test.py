# -*- coding: utf-8 -*-
"""
Как выглядит окно с включёнными камерой и микрофоном.

Обычный smoke_test снимает пустое окно: камера выключена, уровень нулевой — и
ровно то, что нужно проверить глазами, на снимок не попадает. Здесь кадр
подставной, уровень выставлен руками, живая камера не нужна.

    python tools/layout_test.py
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication          # noqa: E402

from app import load_config                         # noqa: E402
from ui.window import MainWindow                    # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "assets", "_layout.png")


def fake_frame():
    """Кадр 4:3 в клетку — по ней сразу видно масштаб и пропорции."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (320, 240), (54, 62, 78))
    d = ImageDraw.Draw(img)
    for i in range(0, 320, 40):
        d.line([(i, 0), (i, 240)], fill=(96, 110, 132))
    for i in range(0, 240, 40):
        d.line([(0, i), (320, i)], fill=(96, 110, 132))
    d.ellipse([120, 70, 200, 150], fill=(214, 178, 148))      # «лицо»
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return buf.getvalue()


def main():
    app = QApplication(sys.argv)
    cfg = load_config()
    win = MainWindow(cfg, os.path.join(ROOT, "assets", "sprites"))
    win.resize(int(cfg.get("window_width", 460)), int(cfg.get("window_height", 880)))
    win.show()
    app.processEvents()

    win.portrait.set_emotion("happy")
    win.portrait.mic.setChecked(True)
    win.portrait.cam.setChecked(True)
    win.portrait.set_level(0.62)
    win.portrait.set_camera_frame(fake_frame())
    win.chat.append("system", "проверка расположения")
    win.chat.append("user", "Следишь?")
    win.chat.append("assistant", "Слежу, вижу тебя в кадре прямо сейчас.")
    app.processEvents()

    win.grab().save(OUT)
    print(OUT)


if __name__ == "__main__":
    main()
