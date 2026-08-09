# -*- coding: utf-8 -*-
"""
Глаза: кадр с камеры для модели и живой предпросмотр в окне.

Камера держится открытой, пока зрение включено, и поток кадров крутится в
отдельной нити. Так у нас всегда есть свежий кадр под рукой: открывать камеру
по требованию стоило бы 1.3 с на каждую реплику (замерено), а это ровно та
задержка, с которой мы боремся.

Побочный плюс такого решения — честность: пока зрение включено, на камере
горит лампочка. Видно, что она смотрит.

Второе: один и тот же кадр не уходит дважды. Если человек не двигался, модель
уже видит эту картинку в своём контексте — присылать её снова значит платить
за неё второй раз и раздувать контекст на пустом месте.
"""
import threading
import time

import numpy as np
from PySide6.QtCore import QObject, Signal

FPS = 6                  # предпросмотру хватает, а процессор не греется
IDLE_FPS = 2             # когда окно спрятано в трей — совсем лениво
SIG_SIZE = 32            # к такому квадрату сводим кадр, чтобы сравнивать сцены


class Eyes(QObject):
    """Камера: живой кадр для предпросмотра и снимок для модели."""

    frame = Signal(bytes)        # JPEG для предпросмотра в окне
    status = Signal(str)
    active = Signal(bool)
    skipped = Signal()           # кадр не изменился, отправлять нечего

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self._run = False
        self._thread = None
        self._last = None            # последний кадр как есть (numpy)
        self._lock = threading.Lock()
        self._idle = False
        # подпись последнего отданного кадра — своя на каждого получателя:
        # основная модель и фоновый описатель смотрят в камеру независимо
        self._sent = {}
        self._sent_at = {}           # когда этот кадр ушёл, для отметки давности

    # ── управление ────────────────────────────────────────────────────────
    def available(self):
        try:
            import cv2  # noqa: F401
            return True
        except ImportError:
            return False

    def is_on(self):
        return self._run

    def start(self):
        if self._run:
            return
        if not self.available():
            self.status.emit("нет модуля камеры (pip install opencv-python-headless)")
            return
        self._run = True
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def stop(self):
        self._run = False
        with self._lock:
            self._last = None
        self.forget()
        self.active.emit(False)
        self.status.emit("камера выключена")

    def forget(self):
        """
        Забыть, что уже отправлено. Нужно при смене сессии: в новом контексте
        модель кадра не видела, и пропускать его как «уже показанный» нельзя.
        """
        self._sent.clear()
        self._sent_at.clear()

    def sent_ago(self, who="main"):
        """Сколько секунд назад этот получатель видел настоящий кадр."""
        at = self._sent_at.get(who)
        return None if at is None else time.time() - at

    def toggle(self):
        self.stop() if self._run else self.start()
        return self._run

    def set_idle(self, idle):
        """Окно спрятано — можно реже дёргать камеру."""
        self._idle = bool(idle)

    # ── кадры ─────────────────────────────────────────────────────────────
    def _worker(self):
        """
        Обёртка над съёмкой. Всё тело в try не для красоты: драйвер камеры
        бросает наружу C++-исключение OpenCV (`Unknown C++ exception`), которое
        не ловится проверкой isOpened(). Без обёртки поток умирал молча, camera
        оставалась захваченной, а кнопка ГЛАЗА — включённой навсегда.
        """
        cap = None
        try:
            cap = self._capture()
        except Exception as exc:                     # noqa: BLE001 — драйвер бросает что угодно
            self.status.emit(f"камера не завелась ({type(exc).__name__})")
        if cap is None:
            self._run = False
            self.active.emit(False)
            return

        try:
            self._loop(cap)
        except Exception as exc:                     # noqa: BLE001
            self.status.emit(f"камера отвалилась ({type(exc).__name__})")
        finally:
            self._run = False
            try:
                cap.release()
            except Exception:                        # noqa: BLE001
                pass
            self.active.emit(False)

    def _capture(self):
        """Открывает камеру. None — не получилось, причина уже сказана вслух."""
        import cv2

        backend = getattr(cv2, f"CAP_{self.cfg.get('camera_backend', 'DSHOW')}",
                          cv2.CAP_DSHOW)
        index = int(self.cfg.get("camera_index", 0))
        self.status.emit("включаю камеру…")
        cap = cv2.VideoCapture(index, backend)
        if not cap.isOpened():
            cap.release()
            self.status.emit("камера не открылась")
            return None

        # Запрошенное разрешение — пожелание, а не требование: часть камер
        # на нём падает. Не вышло — снимаем в том, что даёт драйвер.
        for prop, key, default in ((cv2.CAP_PROP_FRAME_WIDTH, "camera_width", 640),
                                   (cv2.CAP_PROP_FRAME_HEIGHT, "camera_height", 480)):
            try:
                cap.set(prop, int(self.cfg.get(key, default)))
            except Exception:                        # noqa: BLE001
                pass
        return cap

    def _loop(self, cap):
        self.active.emit(True)
        self.status.emit("вижу")
        preview_w = int(self.cfg.get("camera_preview_width", 160))
        misses = 0
        while self._run:
            ok, frame = cap.read()
            if not ok or frame is None:
                misses += 1
                if misses > 50:                      # ~10 секунд пустоты — камеру забрали
                    self.status.emit("камера пропала")
                    return
                time.sleep(0.2)
                continue
            misses = 0
            with self._lock:
                self._last = frame
            if not self._idle:
                small = self._encode(frame, preview_w, 60)
                if small:
                    self.frame.emit(small)
            time.sleep(1.0 / (IDLE_FPS if self._idle else FPS))

    @staticmethod
    def _encode(frame, width, quality):
        import cv2

        h, w = frame.shape[:2]
        if w > width:
            frame = cv2.resize(frame, (width, max(1, int(h * width / w))),
                               interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return bytes(buf) if ok else None

    @staticmethod
    def _signature(frame):
        """
        Сжатый до квадратика серый отпечаток кадра — по нему сравниваем сцены.

        Яркость и контраст из отпечатка вычищены намеренно. Замер
        (tools/vision_dedup_probe.py) на неподвижной сцене: сырой серый отпечаток
        за восемь секунд уезжает от первого кадра с 23 до 52 единиц — камера
        плавно подстраивает экспозицию, и это выглядит как смена сцены. После
        приведения к нулевому среднему и единичному разбросу остаётся геометрия:
        кто где стоит. Именно она нам и нужна.
        """
        import cv2

        small = cv2.resize(frame, (SIG_SIZE, SIG_SIZE),
                           interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)
        return (gray - gray.mean()) / (gray.std() + 1e-6)

    def snapshot(self, who="main", force=False):
        """
        Свежий кадр в JPEG для отправки модели, или None если зрение выключено
        либо картинка не изменилась с прошлого раза.

        Ширину держим скромной: 640 точек — это ~400 токенов на кадр, разглядеть
        человека и обстановку хватает, а платить за 4K незачем. И не шлём одно
        и то же дважды: неподвижная сцена уже лежит в контексте модели.

        Порог взят замером (tools/vision_dedup_probe.py):

            тот же кадр                          0.00
            неподвижная сцена, 8 с          до   0.48   ← дыхание экспозиции
            сдвиг кадра на 6% ширины             0.41
            человек закрыл десятую часть кадра   0.76
            закрыта четверть кадра               1.07

        Порог 0.6 стоит между шумом покоя и заметным движением. Мелкий сдвиг
        (0.41) в него не попадает — и это осознанно: он тонет в шуме экспозиции,
        а отличить его от неё нельзя, не гоняя кадр каждую секунду. Сцену это
        не меняет, а на прямую просьбу «посмотри» кадр всё равно берётся свежий
        через force=True.

        Ошибаться тут лучше в меньшую сторону: лишний кадр — это лишние 400
        токенов, а пропущенный — это модель, которая уверенно рассказывает про
        позавчерашнюю картинку.

        force=True — когда человек прямо просит посмотреть. Тут «ничего не
        изменилось» не ответ: нужен настоящий свежий кадр.
        """
        with self._lock:
            frame = None if self._last is None else self._last.copy()
        if frame is None:
            return None

        sig = self._signature(frame)
        if not force:
            was = self._sent.get(who)
            limit = float(self.cfg.get("camera_change_threshold", 0.6))
            if was is not None and float(np.abs(sig - was).mean()) < limit:
                self.skipped.emit()
                return None
        self._sent[who] = sig
        self._sent_at[who] = time.time()

        return self._encode(frame,
                            int(self.cfg.get("camera_send_width", 640)),
                            int(self.cfg.get("camera_quality", 70)))
