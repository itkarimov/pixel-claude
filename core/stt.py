# -*- coding: utf-8 -*-
"""
Микрофон → распознавание речи.

Поток с микрофона режется на кадры по 30 мс, простой энергетический VAD ловит
начало и конец фразы, готовый кусок уходит в faster-whisper. Пока говорит TTS,
слушатель заглушается, иначе оболочка слышит саму себя.

Про размер модели. Замер на этой машине (4 ядра, без CUDA), фраза 5.6 с:

    tiny   0.75 с   RTF 0.13
    base   1.61 с   RTF 0.29
    small  5.64 с   RTF 1.00   ← стояло раньше

small распознавал ровно тот же текст, что и base, но тратил на это лишние
четыре секунды — то есть просто удерживал человека в тишине. Поэтому по
умолчанию base; на совсем слабой машине есть tiny.

Движков два, переключаются ключом stt_backend:

    faster-whisper   через CTranslate2, ставится одним pip и качает модель сам
    onnx             через sherpa-onnx, модель кладётся руками в models/

Сравнить их на одной и той же записи — tools/bench_stt.py. Числа оттуда,
а не из общих соображений: разница между движками на конкретной машине
предсказанию не поддаётся.
"""
import glob
import os
import queue
import re
import threading

import numpy as np
from PySide6.QtCore import QObject, Signal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE_RATE = 16000
FRAME = 480                      # 30 мс
PREROLL = 10                     # кадров до начала речи, чтобы не срезать первый слог

# Whisper на тишине и шуме любит выдумывать титры из обучающей выборки.
# Свой VAD такое пропускает редко, но метко — поэтому отбраковываем по списку.
JUNK = re.compile(
    r"^(продолжение следует|субтитры[^.]*|редактор субтитров[^.]*|"
    r"спасибо за просмотр|спасибо за внимание|дсл|ася|титры[^.]*)[.!… ]*$",
    re.I)


class Engine:
    """Движок распознавания: загрузиться и разобрать кусок звука в текст."""

    name = "?"

    def __init__(self, cfg):
        self.cfg = cfg
        self.model = None

    def load(self):
        raise NotImplementedError

    def transcribe(self, audio):
        raise NotImplementedError

    def warmup(self):
        """
        Первый разбор всегда медленнее последующих. Тратим на него полсекунды
        тишины при запуске, иначе эту секунду ждал бы человек на первой фразе.
        """
        self.transcribe(np.zeros(SAMPLE_RATE // 2, dtype=np.float32))


class FasterWhisper(Engine):
    """CTranslate2. Модель качается сама при первом запуске."""

    name = "faster-whisper"

    def load(self):
        from faster_whisper import WhisperModel

        self.model = WhisperModel(self.cfg.get("whisper_model", "base"),
                                  device="cpu", compute_type="int8",
                                  cpu_threads=os.cpu_count() or 4)
        self.warmup()

    def transcribe(self, audio):
        segments, _ = self.model.transcribe(
            audio, language=self.cfg.get("whisper_language", "ru"),
            beam_size=1,
            # свой VAD уже отрезал тишину, встроенный только тратит время
            vad_filter=False, without_timestamps=True,
            condition_on_previous_text=False)
        return " ".join(s.text.strip() for s in segments).strip()


class WhisperOnnx(Engine):
    """
    sherpa-onnx: тот же whisper, но через onnxruntime и без torch.

    Модель кладётся руками — tools/get_onnx_model.py. Автоматически ничего не
    качаем: 197 МБ по чужой сети без спроса — это невежливо.
    """

    name = "onnx"

    def load(self):
        import sherpa_onnx

        folder = self.cfg.get("onnx_model_dir") or os.path.join(
            ROOT, "models", "whisper-base")
        encoder, decoder, tokens = find_model(folder)
        self.model = sherpa_onnx.OfflineRecognizer.from_whisper(
            encoder=encoder, decoder=decoder, tokens=tokens,
            language=self.cfg.get("whisper_language", "ru"),
            task="transcribe", num_threads=os.cpu_count() or 4,
            decoding_method="greedy_search")
        self.warmup()

    def transcribe(self, audio):
        stream = self.model.create_stream()
        stream.accept_waveform(SAMPLE_RATE, audio)
        self.model.decode_stream(stream)
        return (stream.result.text or "").strip()


def find_model(folder):
    """
    Тройка encoder / decoder / tokens в папке модели.

    Если рядом лежат обычные и int8 версии — берём int8: она вдвое меньше и
    быстрее на процессоре, а на разборчивость влияет мало.
    """
    if not os.path.isdir(folder):
        raise FileNotFoundError(
            f"нет папки с моделью {folder} — забери её через "
            f"tools/get_onnx_model.py")

    def pick(kind):
        names = sorted(glob.glob(os.path.join(folder, f"*{kind}*.onnx")))
        if not names:
            raise FileNotFoundError(f"в {folder} нет файла *{kind}*.onnx")
        int8 = [n for n in names if "int8" in os.path.basename(n)]
        return (int8 or names)[0]

    tokens = sorted(glob.glob(os.path.join(folder, "*tokens*.txt")))
    if not tokens:
        raise FileNotFoundError(f"в {folder} нет файла *tokens*.txt")
    return pick("encoder"), pick("decoder"), tokens[0]


def make_engine(cfg):
    backend = (cfg.get("stt_backend") or "faster-whisper").lower()
    return WhisperOnnx(cfg) if backend in ("onnx", "sherpa") else FasterWhisper(cfg)


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
        self._engine = None
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

        if self._engine is None:
            engine = make_engine(self.cfg)
            self.status.emit(f"гружу распознавание ({engine.name})…")
            try:
                engine.load()
            except Exception as exc:
                # onnx без положенной руками модели — обычное дело, и оставлять
                # человека с мёртвым микрофоном из-за этого нельзя: откатываемся
                # на faster-whisper, он качает модель сам
                self.status.emit(f"{engine.name} не поднялся ({exc}) — беру whisper")
                engine = FasterWhisper(self.cfg)
                try:
                    engine.load()
                except Exception as exc2:
                    self._run = False
                    self.status.emit(f"распознавание не поднялось: {exc2}")
                    return
            self._engine = engine

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
        # 900 мс паузы — это почти секунда, которую человек просто ждёт. 550 мс
        # хватает, чтобы отличить конец фразы от паузы между словами.
        silence_need = int(self.cfg.get("vad_silence_ms", 550) / 30)
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
            text = self._engine.transcribe(audio)
        except Exception as exc:
            self.status.emit(f"ошибка распознавания: {exc}")
            return

        self.status.emit("слушаю")
        if len(text) > 1 and not JUNK.match(text):
            self.heard.emit(text)
