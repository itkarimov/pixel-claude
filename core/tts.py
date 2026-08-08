# -*- coding: utf-8 -*-
"""
Озвучка ответов: edge-tts (нейронный русский голос) → mp3 → ffmpeg → wav → звук.

Если интернета нет, откатывается на офлайновый SAPI-голос Windows,
чтобы оболочка не немела.
"""
import asyncio
import os
import re
import queue
import subprocess
import tempfile
import threading
import wave

import numpy as np
from PySide6.QtCore import QObject, Signal

NOWINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
MAX_CHARS = 400


def clean_for_speech(text):
    """Выкидывает всё, что голосом звучит мусором."""
    text = re.sub(r"```.*?```", " ", text or "", flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[*_#>|]+", " ", text)
    text = re.sub(r"https?://\S+", " ссылка ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_CHARS]


class Speaker(QObject):
    speaking = Signal(bool)
    status = Signal(str)

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self._q = queue.Queue()
        self._tmp = tempfile.mkdtemp(prefix="pixelclaude_")
        self._n = 0
        threading.Thread(target=self._worker, daemon=True).start()

    def say(self, text):
        text = clean_for_speech(text)
        if text:
            self._q.put(text)

    def shutup(self):
        """Сбрасывает очередь и глушит текущую фразу."""
        while not self._q.empty():
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass

    # ── внутреннее ────────────────────────────────────────────────────────
    def _worker(self):
        while True:
            text = self._q.get()
            self.speaking.emit(True)
            try:
                wav = self._synth_edge(text)
                if wav:
                    self._play(wav)
                else:
                    self._synth_sapi(text)
            except Exception as exc:
                self.status.emit(f"озвучка не вышла: {exc}")
            finally:
                self.speaking.emit(False)

    def _synth_edge(self, text):
        """edge-tts → mp3 → wav. None, если не получилось."""
        try:
            import edge_tts
        except ImportError:
            return None

        self._n += 1
        mp3 = os.path.join(self._tmp, f"v{self._n}.mp3")
        wav = os.path.join(self._tmp, f"v{self._n}.wav")

        async def gen():
            comm = edge_tts.Communicate(
                text,
                self.cfg.get("voice", "ru-RU-SvetlanaNeural"),
                rate=self.cfg.get("tts_rate", "+0%"),
            )
            await comm.save(mp3)

        try:
            asyncio.run(gen())
        except Exception:
            return None
        if not os.path.exists(mp3) or os.path.getsize(mp3) < 512:
            return None

        try:
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                            "-i", mp3, "-ar", "24000", "-ac", "1", wav],
                           check=True, creationflags=NOWINDOW,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            return None
        return wav if os.path.exists(wav) else None

    def _play(self, path):
        import sounddevice as sd
        with wave.open(path, "rb") as w:
            rate = w.getframerate()
            data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        sd.play(data, rate)
        sd.wait()

    def _synth_sapi(self, text):
        """Офлайн-запасной вариант — системный голос Windows."""
        if os.name != "nt":
            return
        txt = os.path.join(self._tmp, "sapi.txt")
        with open(txt, "w", encoding="utf-8") as fh:
            fh.write(text)
        ps = (f"$t=[IO.File]::ReadAllText('{txt}',[Text.Encoding]::UTF8);"
              "$v=New-Object -ComObject SAPI.SpVoice;$v.Speak($t)|Out-Null")
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       creationflags=NOWINDOW,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
