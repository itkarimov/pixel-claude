# -*- coding: utf-8 -*-
"""
Озвучка ответов.

Главное здесь — не ждать. Замеры до первого звука на этой машине, 8 ядер:

    SAPI, системный голос Windows           долго и роботом
    edge-tts потоком, соединение холодное   3.9 с
    edge-tts потоком, прогретое             1.4 с
    piper локально                          0.34 с
    Silero v5 локально                      0.5 с на всю фразу

Ловушка edge-tts: каждая фраза открывает новое соединение с сервисом Microsoft,
и рукопожатие стоит секунды, а пауза между репликами прогрев обнуляет — у
голосовой помощницы паузы как раз и есть норма жизни. Вдобавок Microsoft время
от времени закрывает эндпоинт для старых версий библиотеки: 11 ноября 2026 года
edge-tts 7.2.8 стал получать 403 на все голоса, озвучка молча скатывалась до
SAPI, и голос звучал роботом. Лечится обновлением edge-tts, но полагаться на
это как на основной путь не стоит.

Поэтому синтез по умолчанию локальный. Silero считает фразу целиком, без потока,
зато в двенадцать раз быстрее реального времени — ждать нечего. Звучит он живее
piper, которому русского голоса дали всего час записей.

Модель Silero — архив torch.package: при загрузке исполняется код, упакованный
в него авторами. Поэтому файл кладётся в assets/voices руками, с их сайта
models.silero.ai, и сам движок ничего не качает. Весит 141 МБ, в репозиторий не
попадает — как и голоса piper.

Порядок отступления: silero → piper → edge потоком → edge файлом → SAPI.
"""
import asyncio
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import wave

import numpy as np
from PySide6.QtCore import QObject, Signal

NOWINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
# Сколько текста уходит в движок за один заход. Это не предел реплики: длинное
# делится по границам предложений и читается подряд. Раньше здесь стояло 400 и
# всё лишнее просто отрезалось — развёрнутый ответ обрывался на полуслове.
PIECE = 600
RATE = 24000                 # частота, в которой играем
CHUNK = 2400                 # 50 мс — шаг чтения из декодера

# Точка, вопрос, восклицание или перевод строки, за которыми идёт пробел.
BREAK = re.compile(r"(?<=[.!?…])\s+|\n+")


def clean_for_speech(text):
    """Выкидывает всё, что голосом звучит мусором. Длину не трогает."""
    text = re.sub(r"```.*?```", " ", text or "", flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[*_#>|]+", " ", text)
    text = re.sub(r"https?://\S+", " ссылка ", text)
    return re.sub(r"\s+", " ", text).strip()


def split_for_speech(text, limit=PIECE):
    """
    Длинную реплику — на куски по границам предложений.

    Синтезаторы на длинном тексте либо захлёбываются, либо молча обрезают
    хвост. Режем сами и читаем куски подряд: пауза между ними неотличима от
    паузы между предложениями, зато ничего не теряется.
    """
    parts, piece = [], ""
    for sentence in BREAK.split(text):
        sentence = sentence.strip()
        if not sentence:
            continue
        while len(sentence) > limit:          # одно предложение длиннее куска
            cut = sentence.rfind(" ", 0, limit) or limit
            parts.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if len(piece) + len(sentence) + 1 > limit:
            if piece:
                parts.append(piece)
            piece = sentence
        else:
            piece = f"{piece} {sentence}".strip()
    if piece:
        parts.append(piece)
    return parts


class Speaker(QObject):
    speaking = Signal(bool)
    status = Signal(str)

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self._q = queue.Queue()
        self._tmp = tempfile.mkdtemp(prefix="pixelclaude_")
        self._n = 0
        self._stop = threading.Event()      # «замолчи», взводится из shutup()
        self._ffmpeg = shutil.which("ffmpeg")
        self._sounding = False
        self._piper = None                  # модель локального синтеза
        self._piper_lock = threading.Lock()
        self._silero = None                 # None — не пробовали, False — не вышло
        self._silero_lock = threading.Lock()
        threading.Thread(target=self._worker, daemon=True).start()

    def engine(self):
        """silero и piper — локально, edge — через сеть, system — SAPI."""
        return (self.cfg.get("voice_engine") or "silero").lower()

    def say(self, text):
        for piece in split_for_speech(clean_for_speech(text)):
            self._q.put(piece)

    def warmup(self):
        """
        Разогреть синтез на старте, пока человек только тянется к микрофону.

        Для piper это важнее всего: загрузка модели 4.6 с, а самая первая фраза
        стоит ещё 5.3 с — onnxruntime на ней раскладывает граф. Дальше 0.3 с.
        Для edge — прогрев соединения: первое рукопожатие 2.3 с.
        """
        def work():
            try:
                if self.engine() == "silero":
                    if self._load_silero():
                        self._synth_silero("раз")        # греем граф
                        self.status.emit("готова")
                        return
                if self.engine() in ("silero", "piper"):
                    voice = self._load_piper()
                    if voice is not None:
                        for _ in voice.synthesize("раз"):     # греем граф
                            pass
                        self.status.emit("готова")
                        return
                if self._voice() and self._ffmpeg:
                    wav = self._synth_edge("а")
                    if wav and os.path.exists(wav):
                        os.remove(wav)
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    # ── Silero: лучший из локальных по-русски ─────────────────────────────
    def _silero_path(self):
        name = self.cfg.get("silero_model", "v5_6_ru")
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return self.cfg.get("silero_file") or os.path.join(
            root, "assets", "voices", f"{name}.pt")

    def _load_silero(self):
        """
        Модель Silero — архив torch.package: при загрузке исполняется код,
        упакованный в него авторами. Поэтому файл берётся только с их сайта
        (models.silero.ai) и только тот, что положен в assets/voices руками,
        сам ничего не качаем.
        """
        with self._silero_lock:
            if self._silero is not None:
                return self._silero
            path = self._silero_path()
            if not os.path.exists(path):
                self.status.emit("нет модели Silero — беру запасной голос")
                self._silero = False
                return False
            try:
                import torch

                torch.set_num_threads(int(self.cfg.get("silero_threads", 4)))
                model = torch.package.PackageImporter(path).load_pickle(
                    "tts_models", "model")
                model.to(torch.device("cpu"))
                self._silero = model
            except Exception as exc:
                self.status.emit(f"Silero не поднялся: {exc}")
                self._silero = False
            return self._silero

    def _synth_silero(self, text):
        """Волна int16 или None. Silero считает фразу целиком, зато мгновенно."""
        model = self._load_silero()
        if not model:
            return None, 0
        rate = int(self.cfg.get("silero_rate", 48000))
        speaker = self.cfg.get("silero_speaker", "kseniya")
        try:
            audio = model.apply_tts(text=text, speaker=speaker,
                                    sample_rate=rate, put_accent=True,
                                    put_yo=True)
        except Exception as exc:
            self.status.emit(f"Silero не справился: {exc}")
            return None, 0
        return (np.clip(audio.numpy(), -1.0, 1.0) * 32767).astype(
            np.int16).tobytes(), rate

    def _speak_silero(self, text):
        """True, если отыграли локально. False — пусть пробуют запасные пути."""
        pcm, rate = self._synth_silero(text)
        if not pcm:
            return False
        try:
            import sounddevice as sd
        except ImportError:
            return False
        try:
            with sd.RawOutputStream(samplerate=rate, channels=1,
                                    dtype="int16", blocksize=0) as out:
                self._begin()
                # пишем кусками, иначе «замолчи» не сработает до конца фразы
                for i in range(0, len(pcm), CHUNK * 2):
                    if self._stop.is_set():
                        break
                    out.write(pcm[i:i + CHUNK * 2])
        except Exception as exc:
            self.status.emit(f"локальный голос сорвался: {exc}")
            return False
        return True

    # ── локальный синтез: быстро и без сети ───────────────────────────────
    def _piper_model_path(self):
        name = self.cfg.get("piper_voice", "ru_RU-irina-medium")
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = self.cfg.get("piper_model") or os.path.join(
            root, "assets", "voices", f"{name}.onnx")
        return path if os.path.exists(path) else None

    def _load_piper(self):
        with self._piper_lock:
            if self._piper is not None:
                return self._piper
            path = self._piper_model_path()
            if not path:
                return None
            try:
                from piper import PiperVoice
                self.status.emit("гружу голос…")
                self._piper = PiperVoice.load(path)
            except Exception as exc:
                self.status.emit(f"локальный голос не поднялся: {exc}")
                self._piper = None
            return self._piper

    def _speak_piper(self, text):
        """True, если отыграли локально. False — пусть пробуют запасные пути."""
        voice = self._load_piper()
        if voice is None:
            return False
        try:
            import sounddevice as sd
        except ImportError:
            return False

        rate = getattr(voice.config, "sample_rate", 22050)
        played = 0
        try:
            with sd.RawOutputStream(samplerate=rate, channels=1,
                                    dtype="int16", blocksize=0) as out:
                for chunk in voice.synthesize(text):
                    if self._stop.is_set():
                        break
                    data = chunk.audio_int16_bytes
                    if not data:
                        continue
                    self._begin()
                    out.write(data)
                    played += len(data)
        except Exception as exc:
            self.status.emit(f"локальный голос сорвался: {exc}")
            return played > 0
        return played > 0 or self._stop.is_set()

    def shutup(self):
        """Сбрасывает очередь и глушит текущую фразу."""
        while not self._q.empty():
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
        self._stop.set()
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass

    # ── внутреннее ────────────────────────────────────────────────────────
    def _worker(self):
        while True:
            text = self._q.get()
            self._stop.clear()
            try:
                if self.engine() == "silero" and self._speak_silero(text):
                    pass
                elif self.engine() in ("silero", "piper") and self._speak_piper(text):
                    pass
                elif self.engine() != "system" and self._stream_edge(text):
                    pass
                else:
                    wav = self._synth_edge(text)
                    self._begin()
                    if wav:
                        self._play_file(wav)
                    else:
                        self._synth_sapi(text)
            except Exception as exc:
                self.status.emit(f"озвучка не вышла: {exc}")
            finally:
                # «Замолчала» — только когда очередь пуста. Длинный ответ идёт
                # кусками, и если гасить признак между ними, портрет моргает
                # ртом, а микрофон успевает разглушиться и поймать её же хвост.
                if self._sounding and self._q.empty():
                    self._sounding = False
                    self.speaking.emit(False)

    def _begin(self):
        """
        «Говорю» включаем на первом звуке, а не на старте синтеза: иначе портрет
        шевелит губами в тишине, пока фраза ещё синтезируется.
        """
        if not self._sounding:
            self._sounding = True
            self.speaking.emit(True)

    def _voice(self):
        return self.cfg.get("voice")

    # ── потоковый путь: звук начинается, пока фраза ещё синтезируется ─────
    def _stream_edge(self, text):
        """True, если отыграли потоком. False — пусть пробуют запасные пути."""
        if not self._voice() or not self._ffmpeg:
            return False
        try:
            import edge_tts          # noqa: F401
            import sounddevice as sd
        except ImportError:
            return False

        # ffmpeg декодирует mp3 из трубы в сырой звук, тоже в трубу.
        # -probesize/-analyzeduration обязательны: по умолчанию ffmpeg сначала
        # разведывает формат и придерживает звук лишние 0.23 с, а формат мы и
        # так знаем — это mp3 от edge-tts.
        ff = subprocess.Popen(
            [self._ffmpeg, "-hide_banner", "-loglevel", "error",
             "-probesize", "32", "-analyzeduration", "0", "-fflags", "nobuffer",
             "-f", "mp3", "-i", "pipe:0",
             "-f", "s16le", "-ar", str(RATE), "-ac", "1",
             "-flush_packets", "1", "pipe:1"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, creationflags=NOWINDOW)

        fed = {"bytes": 0, "error": None}

        def feed():
            """Качаем куски из edge-tts и сразу суём их декодеру."""
            async def pump():
                comm = edge_tts.Communicate(text, self._voice(),
                                            rate=self.cfg.get("tts_rate", "+0%"))
                agen = comm.stream()
                try:
                    async for chunk in agen:
                        if self._stop.is_set():
                            break
                        if chunk["type"] == "audio" and chunk.get("data"):
                            ff.stdin.write(chunk["data"])
                            ff.stdin.flush()
                            fed["bytes"] += len(chunk["data"])
                finally:
                    # генератор надо закрыть явно: после break aiohttp остаётся
                    # с открытой сессией и сыплет «Fatal error on SSL transport»
                    await agen.aclose()

            try:
                asyncio.run(pump())
            except Exception as exc:
                fed["error"] = exc
            finally:
                try:
                    ff.stdin.close()
                except OSError:
                    pass

        pumper = threading.Thread(target=feed, daemon=True)
        pumper.start()

        played = 0
        try:
            with sd.RawOutputStream(samplerate=RATE, channels=1,
                                    dtype="int16", blocksize=0) as out:
                while True:
                    block = ff.stdout.read(CHUNK * 2)      # 2 байта на отсчёт
                    if not block:
                        break
                    if self._stop.is_set():
                        break
                    self._begin()
                    out.write(block)
                    played += len(block)
        except Exception as exc:
            self.status.emit(f"поток звука оборвался: {exc}")
        finally:
            pumper.join(timeout=2)
            try:
                ff.stdout.close()
            except OSError:
                pass
            if ff.poll() is None:
                ff.terminate()

        if fed["error"] and not played:
            return False
        return played > 0 or self._stop.is_set()

    # ── запасной путь: как раньше, файлом целиком ─────────────────────────
    def _synth_edge(self, text):
        """edge-tts → mp3 → wav. None, если не получилось."""
        if not self._voice():
            # пустой голос = «наружу ничего не отправлять»: edge-tts шлёт текст
            # ответа на серверы Microsoft, а системный голос работает офлайн
            return None
        try:
            import edge_tts
        except ImportError:
            return None

        self._n += 1
        mp3 = os.path.join(self._tmp, f"v{self._n}.mp3")
        wav = os.path.join(self._tmp, f"v{self._n}.wav")

        async def gen():
            comm = edge_tts.Communicate(text, self._voice(),
                                        rate=self.cfg.get("tts_rate", "+0%"))
            await comm.save(mp3)

        try:
            asyncio.run(gen())
        except Exception:
            return None
        if not os.path.exists(mp3) or os.path.getsize(mp3) < 512:
            return None

        try:
            subprocess.run([self._ffmpeg or "ffmpeg", "-y", "-loglevel", "error",
                            "-i", mp3, "-ar", str(RATE), "-ac", "1", wav],
                           check=True, creationflags=NOWINDOW,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            return None
        return wav if os.path.exists(wav) else None

    def _play_file(self, path):
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
