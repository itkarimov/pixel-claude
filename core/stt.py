# -*- coding: utf-8 -*-
"""
Микрофон → распознавание речи.

Поток с микрофона режется на кадры по 30 мс, простой энергетический VAD ловит
начало и конец фразы, готовый кусок уходит в faster-whisper. Пока говорит TTS,
слушатель заглушается, иначе оболочка слышит саму себя.
"""
import queue
import threading

import numpy as np
from PySide6.QtCore import QObject, Signal

SAMPLE_RATE = 16000
FRAME = 480                      # 30 мс
PREROLL = 10                     # кадров до начала речи, чтобы не срезать первый слог


class Listener(QObject):
    heard = Signal(str)          # распознанная фраза
    status = Signal(str)         # текст состояния для UI
    level = Signal(float)        # 0..1 — громкость для индикатора
    listening = Signal(bool)     # идёт ли захват фразы прямо сейчас

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self._q = queue.Queue()
        self._stream = None
        self._model = None
        self._run = False
        self._muted = False
        self._noise = 300.0

    # ── управление ────────────────────────────────────────────────────────
    def start(self):
        if self._run:
            return
        self._run = True
        threading.Thread(target=self._worker, daemon=True).start()

    def stop(self):
        self._run = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        self.listening.emit(False)
        self.status.emit("микрофон выключен")

    def set_muted(self, muted):
        """Глушим захват, пока оболочка говорит вслух."""
        self._muted = bool(muted)

    # ── поток ─────────────────────────────────────────────────────────────
    def _callback(self, indata, frames, time_info, status):
        if not self._muted:
            self._q.put(indata[:, 0].copy())

    def _worker(self):
        import sounddevice as sd

        if self._model is None:
            self.status.emit("гружу модель распознавания…")
            try:
                from faster_whisper import WhisperModel
                self._model = WhisperModel(self.cfg.get("whisper_model", "small"),
                                           device="cpu", compute_type="int8")
            except Exception as exc:
                self._run = False
                self.status.emit(f"whisper не поднялся: {exc}")
                return

        try:
            self._stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                                          dtype="int16", blocksize=FRAME,
                                          callback=self._callback)
            self._stream.start()
        except Exception as exc:
            self._run = False
            self.status.emit(f"микрофон недоступен: {exc}")
            return

        self.status.emit("слушаю")
        start_rms = float(self.cfg.get("vad_start_rms", 900))
        silence_need = int(self.cfg.get("vad_silence_ms", 900) / 30)
        min_frames = int(self.cfg.get("vad_min_speech_ms", 400) / 30)

        pre, speech = [], []
        in_speech, silence, loud = False, 0, 0

        while self._run:
            try:
                frame = self._q.get(timeout=0.3)
            except queue.Empty:
                continue

            rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)) + 1e-6)
            self.level.emit(min(1.0, rms / 4000.0))
            thr = max(start_rms, self._noise * 3.0)

            if not in_speech:
                self._noise = self._noise * 0.97 + rms * 0.03      # шумовой порог
                pre.append(frame)
                if len(pre) > PREROLL:
                    pre.pop(0)
                # три кадра подряд, а не два: одиночные щелчки и стуки по столу
                # дают короткий пик выше порога и ложно запускали запись
                loud = loud + 1 if rms > thr else 0
                if loud >= 3:
                    in_speech, silence = True, 0
                    speech = list(pre)
                    pre.clear()
                    self.listening.emit(True)
            else:
                speech.append(frame)
                silence = silence + 1 if rms < thr * 0.6 else 0
                if silence >= silence_need:
                    in_speech = False
                    self.listening.emit(False)
                    if len(speech) >= min_frames:
                        self._transcribe(np.concatenate(speech))
                    speech = []

        self.status.emit("микрофон выключен")

    def _transcribe(self, pcm):
        self.status.emit("разбираю…")
        audio = pcm.astype(np.float32) / 32768.0
        try:
            segments, _ = self._model.transcribe(
                audio, language=self.cfg.get("whisper_language", "ru"),
                beam_size=1, vad_filter=True,
                condition_on_previous_text=False)
            text = " ".join(s.text.strip() for s in segments).strip()
        except Exception as exc:
            self.status.emit(f"ошибка распознавания: {exc}")
            return

        self.status.emit("слушаю")
        if len(text) > 1:
            self.heard.emit(text)
