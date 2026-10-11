"""Talking instead of typing: hold Ctrl+Alt+Space (or click the bubble's mic) and speak; the words go in the
bubble's line, to check before sending. Speech becomes text on this computer, with Whisper (faster-whisper's
small model, downloaded the first time: about 480 MB), in whatever language was spoken. Chinese comes out in the
script the system uses: Traditional in Taiwan, Hong Kong and Macau, else Simplified."""
from __future__ import annotations

import logging
import math
import os
import re
import threading
import time

from PySide6.QtCore import QLocale, QObject, QTimer, Signal

from . import paths

log = logging.getLogger("pet.voice")
RATE = 16000              # what Whisper takes
MODEL, MODEL_REPO = "small", "Systran/faster-whisper-small"
MAX_SECONDS = 60          # a recording stops by itself after this
FORGET_AFTER = 10 * 60    # seconds unused before the model is let go (it holds a few hundred MB)
QUIET_DB, LOUD_DB = -55.0, -18.0   # the level's range
PROMPT = {"Hans": "以下是普通话的句子。", "Hant": "以下是普通話的句子。"}  # (Chinese with punctuation, in that script)
CJK = r"\u3000-\u303f\u3400-\u9fff\uf900-\ufaff\uff00-\uffef"  # (CJK punctuation, ideographs, full-width forms)
FULL_WIDTH = {",": "，", "?": "？", "!": "！", ":": "：", ";": "；", ".": "。"}


def chinese_script() -> str:
    """'Hant' where the system uses Traditional Chinese, else 'Hans'."""
    system = QLocale.system()
    if system.script() == QLocale.Script.TraditionalHanScript:
        return "Hant"
    return "Hant" if QLocale.territoryToCode(system.territory()) in ("TW", "HK", "MO") else "Hans"


def languages() -> set[str]:
    """What the user may speak: English, Chinese and the system's language (short recordings otherwise get taken
    for a language nobody here speaks now and then)."""
    return {"en", "zh", QLocale.system().name().split("_")[0].lower()}


def tidy(text: str, language: str, script: str) -> str:
    """Whisper's words as one line: Chinese in the script wanted, without spaces between Chinese characters."""
    text = re.sub(r"\s+([,.!?;:])", r"\1", " ".join(text.split()))
    if language == "zh":
        import opencc
        territory = QLocale.territoryToCode(QLocale.system().territory())
        config = "t2s" if script == "Hans" else {"TW": "s2twp", "HK": "s2hk"}.get(territory, "s2t")
        text = opencc.OpenCC(config).convert(text)
        text = re.sub(rf"(?<=[{CJK}])\s*([,?!:;.])\s*", lambda m: FULL_WIDTH[m.group(1)], text)  # 一点, -> 一点，
        text = re.sub(rf"(?<=[{CJK}])\s+(?=[{CJK}])", "", text)
    return text


def joined(typed: str, heard: str) -> str:
    """What was heard after what was typed: with a space between words, none between Chinese characters."""
    if not typed or typed[-1].isspace() or not heard:
        return typed + heard
    cjk = re.compile(rf"[{CJK}]")
    return typed + ("" if cjk.match(typed[-1]) or cjk.match(heard[0]) else " ") + heard


def _stand_in_for_av():
    """faster-whisper imports PyAV (FFmpeg, 60 MB) to read audio files, which the pet never asks it to (it hands
    it the microphone's samples): the build leaves PyAV out (DeskPet.spec), and an empty module takes its place."""
    import importlib.util
    import sys
    import types
    if "av" not in sys.modules and importlib.util.find_spec("av") is None:
        sys.modules["av"] = types.ModuleType("av")


_stand_in_for_av()


def prepare():
    """Load the microphone's library ahead (half a second, which on first use froze the pet as it jumped out)."""
    try:
        import sounddevice  # noqa: F401
    except Exception:  # (said when the mic is used)
        pass


class Recorder:
    """The microphone, from start() to stop(): mono, at RATE. level(): how loud it is now, 0..1."""

    def __init__(self):
        self._chunks: list = []
        self._level = 0.0
        self._stream = None
        self._rate = RATE

    def start(self):
        import sounddevice as sd  # (PortAudio; on Linux the libportaudio2 package)
        try:
            self._stream = sd.InputStream(samplerate=RATE, channels=1, dtype="float32", callback=self._take)
        except sd.PortAudioError:  # the device doesn't do RATE: its own, resampled at the end
            self._rate = int(sd.query_devices(kind="input")["default_samplerate"])
            self._stream = sd.InputStream(samplerate=self._rate, channels=1, dtype="float32", callback=self._take)
        self._stream.start()

    def _take(self, data, frames, when, status):
        import numpy as np
        self._chunks.append(data[:, 0].copy())
        rms = float(np.sqrt(np.mean(np.square(data))))
        now = min(1.0, max(0.0, (20 * math.log10(rms + 1e-9) - QUIET_DB) / (LOUD_DB - QUIET_DB)))
        self._level = max(now, self._level * 0.8)  # (up at once, down gently)

    def level(self) -> float:
        return self._level

    def stop(self):
        """The recording, as a numpy array at RATE."""
        import numpy as np
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
            stream.close()
        audio = np.concatenate(self._chunks) if self._chunks else np.zeros(0, np.float32)
        self._chunks = []
        if self._rate != RATE and len(audio):
            n = int(len(audio) * RATE / self._rate)
            audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)
        return audio


class Transcriber:
    """Whisper: loaded when first needed (downloaded the first time), let go after a while unused."""

    def __init__(self):
        self._model = None
        self._lock = threading.Lock()
        self.used_at = 0.0

    def downloaded(self) -> bool:
        folder = paths.MODELS / ("models--" + MODEL_REPO.replace("/", "--"))
        return any(folder.glob("snapshots/*/model.bin"))

    def load(self):
        with self._lock:
            if self._model is None:
                os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")  # (copies instead, on Windows: fine)
                from faster_whisper import WhisperModel
                log.info("Loading the speech model%s", "" if self.downloaded() else " (downloading it first)")
                paths.MODELS.mkdir(parents=True, exist_ok=True)
                self._model = WhisperModel(MODEL, device="cpu", compute_type="int8", download_root=str(paths.MODELS),
                                           cpu_threads=min(os.cpu_count() or 4, 16),
                                           local_files_only=self.downloaded())  # (no asking online for updates)
            self.used_at = time.monotonic()
            return self._model

    def forget_if_unused(self):
        with self._lock:
            if self._model is not None and time.monotonic() - self.used_at > FORGET_AFTER:
                self._model = None
                log.info("Let the speech model go")

    def __call__(self, audio) -> str:
        """The words in a recording. Whisper's encoder is the slow part (on a laptop's CPU, 2.5 s for the 30 s it
        normally takes, however short the speech), so it runs once, on the speech plus 3 s (at least 10 s: as
        whisper.cpp's audio_ctx; a sentence then takes about 1.5 s in all), and the language and the words both
        come from that. Recordings over 30 s go the usual way."""
        import numpy as np
        from faster_whisper.audio import pad_or_trim
        from faster_whisper.tokenizer import Tokenizer
        from faster_whisper.vad import collect_chunks, get_speech_timestamps
        model = self.load()
        speech = get_speech_timestamps(audio)  # (silence out, before and after and in between)
        if not speech:
            return ""
        chunks, _ = collect_chunks(audio, speech)
        audio = np.concatenate(chunks)
        features = model.feature_extractor(audio)
        n, window = features.shape[-1], model.feature_extractor.nb_max_frames  # (100 frames a second)
        frames = min(window, max(1000, -(-(n + 300) // 100) * 100))
        encoded = model.encode(pad_or_trim(features[..., :window], frames))
        ranked = [(token[2:-2], p) for token, p in model.model.detect_language(encoded)[0]]
        wanted = languages()
        language = next((code for code, _ in ranked if code in wanted), ranked[0][0])
        script = chinese_script()
        prompt = PROMPT[script] if language == "zh" else None
        if n > window:
            segments, _ = model.transcribe(audio, language=language, without_timestamps=True,
                                           condition_on_previous_text=False, initial_prompt=prompt)
            text = ("" if language in ("zh", "ja") else " ").join(s.text.strip() for s in segments)
        else:
            tokenizer = Tokenizer(model.hf_tokenizer, True, task="transcribe", language=language)
            before = tokenizer.encode(" " + prompt) if prompt else []
            result = model.model.generate(encoded, [model.get_prompt(tokenizer, before, without_timestamps=True)],
                                          beam_size=5, max_length=448, suppress_blank=True)[0]
            text = tokenizer.decode(result.sequences_ids[0]).strip()
        self.used_at = time.monotonic()
        return tidy(text, language, script)


class Voice(QObject):
    """One recording at a time: start(), then stop() to have it written down (heard), or cancel()."""
    heard = Signal(str)       # what was said ("" if nothing was)
    failed = Signal(str)      # what went wrong: a key of the pet's strings, or the error itself
    _written = Signal(str, str)   # (from the worker: the text, or what went wrong)

    def __init__(self, make_recorder=Recorder, transcriber=None):
        super().__init__()
        self._make_recorder = make_recorder
        self.transcriber = transcriber or Transcriber()
        self._recorder = None
        self.writing = False      # turning the last recording into text
        self._written.connect(self._done)
        self.limit = QTimer(self, singleShot=True, interval=MAX_SECONDS * 1000, timeout=self.stop)
        self.forget_timer = QTimer(self, interval=60_000, timeout=self.transcriber.forget_if_unused)
        self.forget_timer.start()

    @property
    def listening(self) -> bool:
        return self._recorder is not None

    def level(self) -> float:
        return self._recorder.level() if self._recorder is not None else 0.0

    def start(self) -> bool:
        if self.listening or self.writing:
            return False
        recorder = self._make_recorder()
        try:
            recorder.start()
        except Exception as e:
            log.warning("Couldn't record", exc_info=True)
            self.failed.emit(_problem(e, "no_mic"))
            return False
        self._recorder = recorder
        self.limit.start()
        threading.Thread(target=self._warm_up, name="voice", daemon=True).start()  # the model, meanwhile
        return True

    def stop(self):
        recorder, self._recorder = self._recorder, None
        if recorder is None:
            return
        self.limit.stop()
        audio = recorder.stop()
        if len(audio) < RATE * 0.3:  # (a click, not a word)
            self.heard.emit("")
            return
        self.writing = True
        threading.Thread(target=self._write, args=(audio,), name="voice", daemon=True).start()

    def cancel(self):
        recorder, self._recorder = self._recorder, None
        self.limit.stop()
        if recorder is not None:
            recorder.stop()

    def _warm_up(self):
        try:
            self.transcriber.load()
        except Exception:  # (said when it's needed: see _write)
            pass

    def _write(self, audio):
        try:
            self._written.emit(self.transcriber(audio), "")
        except Exception as e:
            log.warning("Couldn't write the speech down", exc_info=True)
            self._written.emit("", _problem(e, "e_voice"))

    def _done(self, text: str, problem: str):
        self.writing = False
        if problem:
            self.failed.emit(problem)
        else:
            self.heard.emit(text)


def _problem(e: Exception, otherwise: str) -> str:
    """What went wrong, as one of the pet's strings."""
    text = str(e).lower()
    if isinstance(e, ImportError):
        return "no_voice"
    if "portaudio library not found" in text:
        return "no_portaudio"
    if any(w in text for w in ("connection", "resolve", "timed out", "huggingface", "offline", "network")):
        return "e_model"
    return otherwise
